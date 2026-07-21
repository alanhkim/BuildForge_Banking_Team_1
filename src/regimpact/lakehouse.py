"""OneLake writer — uploads Parquet tables into a Fabric lakehouse Files/ path.

The core CLI does not depend on the Azure Data Lake SDK. This module lazily
imports ``azure.storage.filedatalake`` (and ``azure.identity`` for the default
credential) only when :func:`export_to_lakehouse` is actually invoked, so
installations without the ``fabric`` extra can still import ``regimpact``.

OneLake exposes each Fabric lakehouse as an ADLS Gen2 filesystem under the
account ``https://onelake.dfs.fabric.microsoft.com``. The filesystem is the
workspace ID and the top-level directory is ``<lakehouse_id>.Lakehouse``.
Parquet files land under ``Files/<subpath>/`` and can then be shortcut'd
into a Delta table from the Fabric UI.
"""
from __future__ import annotations

import logging
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)

_ONELAKE_ACCOUNT_URL = "https://onelake.dfs.fabric.microsoft.com"

# Folder layout expected by ``notebooks/08_fabric/01_load_lakehouse.ipynb``.
# The PySpark loader reads Parquet from these two Files/ subfolders and
# materialises them as managed Delta tables plus the ``v_impact``,
# ``v_compliance`` and ``v_capability_health`` views. Keep these constants
# in sync with ``RAW_FOLDER`` / ``GOLD_FOLDER`` in that notebook.
RAW_SUBPATH = "regimpact_raw"
GOLD_SUBPATH = "regimpact_gold"


class LakehouseError(Exception):
    """Base class for OneLake writeback errors."""


class LakehouseNotConfiguredError(LakehouseError):
    """Raised when FABRIC_WORKSPACE_ID or FABRIC_LAKEHOUSE_ID is not set."""


class LakehouseWriteError(LakehouseError):
    """Raised when uploading a Parquet file to OneLake fails."""


def _normalize_fabric_id(raw: str | None, env_var: str) -> str:
    """Strip whitespace + surrounding quotes and validate as a Fabric GUID.

    OneLake's ADLS Gen2 endpoint rejects malformed filesystem/directory names
    with an opaque ``FriendlyNameSupportDisabled`` server error at upload time.
    Common causes are copy/paste artefacts on the env var: trailing newlines
    from ``export FOO="…"``, surrounding quote characters, whitespace, or
    passing a workspace *display name* while friendly-name resolution is
    disabled on the tenant.

    We surface that as a clear configuration failure at the boundary instead
    of letting it turn into a red ``LakehouseWriteError`` on every run.

    Parameters
    ----------
    raw:
        The value read from settings/env (may be ``None`` or empty).
    env_var:
        The env var name (e.g. ``"FABRIC_WORKSPACE_ID"``) — used only in the
        error message so the operator knows what to fix.

    Returns
    -------
    str
        The cleaned, GUID-validated value.

    Raises
    ------
    LakehouseNotConfiguredError
        If the value is empty after stripping, or if it is not a valid GUID.
        Malformed values fail every run until fixed, so per decision §0 they
        are treated as "not configured" (yellow skip) rather than a transient
        write error (red hard-failure).
    """
    if raw is None:
        raise LakehouseNotConfiguredError(
            f"{env_var} is not set; cannot write to OneLake."
        )
    # Strip whitespace, then strip a single layer of surrounding quotes.
    cleaned = raw.strip().strip("'").strip('"').strip()
    if not cleaned:
        raise LakehouseNotConfiguredError(
            f"{env_var} is not set; cannot write to OneLake."
        )
    try:
        # ``uuid.UUID`` accepts canonical 8-4-4-4-12 hex (case-insensitive)
        # and also braced/urn forms; reject the latter by re-checking the
        # canonical string form matches the input casefolded.
        parsed = uuid.UUID(cleaned)
    except (ValueError, AttributeError, TypeError) as exc:
        raise LakehouseNotConfiguredError(
            f"{env_var} must be a Fabric workspace/lakehouse GUID "
            f"(got '{cleaned}'). Copy it from the Fabric portal → "
            f"Workspace settings → About."
        ) from exc
    if str(parsed) != cleaned.lower():
        raise LakehouseNotConfiguredError(
            f"{env_var} must be a canonical GUID in 8-4-4-4-12 hex form "
            f"(got '{cleaned}'). Copy it from the Fabric portal → "
            f"Workspace settings → About."
        )
    return cleaned


def _default_credential():
    """Lazy-import ``DefaultAzureCredential`` so azure-identity stays optional."""
    from azure.identity import DefaultAzureCredential

    return DefaultAzureCredential()


def export_to_lakehouse(
    tables_dir: Path,
    workspace_id: str,
    lakehouse_id: str,
    files_subpath: str = "tables",
    credential=None,
) -> list[str]:
    """Upload every ``*.parquet`` file in ``tables_dir`` to a Fabric lakehouse.

    Parameters
    ----------
    tables_dir:
        Local directory containing Parquet files to upload.
    workspace_id:
        Fabric workspace ID (GUID). Used as the ADLS filesystem name.
    lakehouse_id:
        Fabric lakehouse ID (GUID). Used as the top-level directory
        (``<lakehouse_id>.Lakehouse``).
    files_subpath:
        Subfolder under ``Files/`` where Parquet files land. Defaults to
        ``"tables"``.
    credential:
        Optional Azure credential. Defaults to
        :class:`azure.identity.DefaultAzureCredential`.

    Returns
    -------
    list[str]
        ABFSS URLs of every uploaded file.

    Raises
    ------
    LakehouseNotConfiguredError
        If ``workspace_id`` or ``lakehouse_id`` is empty, or is not a valid
        Fabric GUID (after stripping surrounding whitespace/quotes).
    LakehouseWriteError
        If any Parquet upload fails.
    """
    workspace_id = _normalize_fabric_id(workspace_id, "FABRIC_WORKSPACE_ID")
    lakehouse_id = _normalize_fabric_id(lakehouse_id, "FABRIC_LAKEHOUSE_ID")

    from azure.storage.filedatalake import DataLakeServiceClient

    cred = credential if credential is not None else _default_credential()
    service = DataLakeServiceClient(_ONELAKE_ACCOUNT_URL, credential=cred)
    file_system = service.get_file_system_client(workspace_id)
    target_dir = f"{lakehouse_id}.Lakehouse/Files/{files_subpath}"
    directory_client = file_system.get_directory_client(target_dir)

    uploaded: list[str] = []
    parquet_files = sorted(tables_dir.glob("*.parquet"))
    for local_path in parquet_files:
        try:
            file_client = directory_client.get_file_client(local_path.name)
            data = local_path.read_bytes()
            file_client.upload_data(data, overwrite=True)
        except Exception as exc:  # noqa: BLE001 — wrap all Azure SDK errors
            raise LakehouseWriteError(
                f"Failed to upload {local_path} to OneLake: {exc}"
            ) from exc

        abfss_url = (
            f"abfss://{workspace_id}@onelake.dfs.fabric.microsoft.com/"
            f"{lakehouse_id}.Lakehouse/Files/{files_subpath}/{local_path.name}"
        )
        uploaded.append(abfss_url)
        logger.info(
            "Uploaded %s (%d bytes) to %s", local_path.name, len(data), abfss_url
        )

    logger.info(
        "OneLake upload complete: %d Parquet file(s) written to %s",
        len(uploaded),
        target_dir,
    )
    return uploaded


def export_regimpact_lakehouse(
    tables_dir: Path,
    gold_dir: Path,
    workspace_id: str,
    lakehouse_id: str,
    credential=None,
) -> dict[str, list[str]]:
    """Upload the raw entity + gold star-schema Parquet sets to a Fabric lakehouse.

    This is the high-level entry point the ``interpret`` CLI uses. It matches
    the folder layout expected by the companion PySpark notebook
    (``notebooks/08_fabric/01_load_lakehouse.ipynb``), which reads:

    * ``Files/regimpact_raw/*.parquet``  — 17 entity tables
    * ``Files/regimpact_gold/*.parquet`` — dim_* / fact_* / bridge_* tables

    ...and then materialises them as managed Delta tables plus the
    ``v_impact``, ``v_compliance`` and ``v_capability_health`` views inside
    Fabric. Delta conversion and view creation require the Fabric-provided
    Spark session, so they stay in the notebook — this function only lands
    the Parquet in the right place.

    Both directories are optional at runtime: if either is empty or missing,
    that half of the upload is skipped and reported as an empty list. This
    lets ``interpret`` run before ``demo`` has produced gold outputs without
    failing the pipeline.

    Parameters
    ----------
    tables_dir:
        Local directory holding the raw entity Parquet files (from
        :func:`regimpact.exporter.export_tables`).
    gold_dir:
        Local directory holding the star-schema Parquet files (from
        :func:`regimpact.gold.export_gold`).
    workspace_id:
        Fabric workspace ID (GUID).
    lakehouse_id:
        Fabric lakehouse ID (GUID).
    credential:
        Optional Azure credential. Defaults to
        :class:`azure.identity.DefaultAzureCredential`.

    Returns
    -------
    dict[str, list[str]]
        ``{"raw": [...abfss urls...], "gold": [...abfss urls...]}``.

    Raises
    ------
    LakehouseNotConfiguredError
        If ``workspace_id`` or ``lakehouse_id`` is empty, or is not a valid
        Fabric GUID (after stripping surrounding whitespace/quotes).
    LakehouseWriteError
        If any Parquet upload fails.
    """
    cred = credential if credential is not None else _default_credential()

    raw_uploaded: list[str] = []
    if tables_dir.exists() and any(tables_dir.glob("*.parquet")):
        raw_uploaded = export_to_lakehouse(
            tables_dir,
            workspace_id=workspace_id,
            lakehouse_id=lakehouse_id,
            files_subpath=RAW_SUBPATH,
            credential=cred,
        )
    else:
        logger.warning(
            "OneLake raw upload skipped: %s has no Parquet files.", tables_dir
        )

    gold_uploaded: list[str] = []
    if gold_dir.exists() and any(gold_dir.glob("*.parquet")):
        gold_uploaded = export_to_lakehouse(
            gold_dir,
            workspace_id=workspace_id,
            lakehouse_id=lakehouse_id,
            files_subpath=GOLD_SUBPATH,
            credential=cred,
        )
    else:
        logger.warning(
            "OneLake gold upload skipped: %s has no Parquet files.", gold_dir
        )

    return {"raw": raw_uploaded, "gold": gold_uploaded}
