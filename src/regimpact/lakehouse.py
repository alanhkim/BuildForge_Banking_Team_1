"""OneLake writer — uploads Parquet tables into a Fabric lakehouse Files/ path.

The core CLI does not depend on the Azure Data Lake SDK. This module lazily
imports ``azure.storage.filedatalake`` (and ``azure.identity`` for the default
credential) only when :func:`export_to_lakehouse` is actually invoked, so
installations without the ``fabric`` extra can still import ``regimpact``.

OneLake exposes each Fabric lakehouse as an ADLS Gen2 filesystem under the
account ``https://onelake.dfs.fabric.microsoft.com``. The filesystem is the
workspace ID and the top-level directory is the bare lakehouse ID (Fabric's
ADLS Gen2 canonical form — the ``.Lakehouse`` suffix is a shortcut/UI
convention only and is rejected here with ``FriendlyNameSupportDisabled``).
Confirmed against ``GET /v1/workspaces/{ws}/lakehouses/{lh}`` →
``properties.oneLakeFilesPath`` / ``oneLakeTablesPath``, which both return
``https://onelake.dfs.fabric.microsoft.com/{workspace_id}/{lakehouse_id}/…``
with no suffix. Parquet and CSV files land under ``Files/<subpath>/`` and
can then be shortcut'd into a Delta table from the Fabric UI.
"""
from __future__ import annotations

import logging
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)

# Default (global) OneLake ADLS Gen2 endpoint. Some Fabric capacity SKUs
# advertise a *regional* endpoint (e.g. ``northcentralus-onelake.dfs.
# fabric.microsoft.com``) via the workspace's ``oneLakeEndpoints`` metadata;
# on those capacities the global endpoint's routing layer returns
# ``FriendlyNameSupportDisabled`` instead of forwarding, so callers must
# override this via ``FABRIC_ONELAKE_DFS_ENDPOINT``. Kept as the module
# default so that pre-existing callers see no behavior change.
_ONELAKE_ACCOUNT_URL = "https://onelake.dfs.fabric.microsoft.com"

# Substring every valid OneLake DFS endpoint must contain, whether global
# (``onelake.dfs.fabric.microsoft.com``) or regional
# (``<region>-onelake.dfs.fabric.microsoft.com``). Guards against typos and
# accidentally pointing at a non-OneLake ADLS Gen2 host.
_ONELAKE_ENDPOINT_MARKER = "onelake.dfs.fabric.microsoft.com"

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


def _resolve_onelake_endpoint(raw: str | None) -> str:
    """Return the DFS endpoint URL to hit, falling back to the global default.

    Some Fabric capacities advertise a *regional* OneLake endpoint (e.g.
    ``https://northcentralus-onelake.dfs.fabric.microsoft.com``) via
    ``GET /v1/workspaces/{id}`` → ``oneLakeEndpoints.dfsEndpoint``. On those
    capacities the global endpoint's routing layer returns
    ``FriendlyNameSupportDisabled`` instead of forwarding, so callers must
    point the SDK at the regional host directly. When ``raw`` is ``None`` or
    empty we return :data:`_ONELAKE_ACCOUNT_URL` (backward-compatible global
    default). When ``raw`` is provided we validate the shape at the boundary
    for the same reason we validate GUIDs: a malformed value fails every run
    with an opaque SDK error, so surface it as configuration up front and map
    it to the soft-skip class per decision §0.

    Raises
    ------
    LakehouseNotConfiguredError
        If ``raw`` is provided but does not start with ``https://`` or does
        not contain ``onelake.dfs.fabric.microsoft.com``.
    """
    if raw is None:
        return _ONELAKE_ACCOUNT_URL
    cleaned = raw.strip().strip("'").strip('"').strip()
    if not cleaned:
        return _ONELAKE_ACCOUNT_URL
    lowered = cleaned.lower()
    if not lowered.startswith("https://") or _ONELAKE_ENDPOINT_MARKER not in lowered:
        raise LakehouseNotConfiguredError(
            "FABRIC_ONELAKE_DFS_ENDPOINT must be an https:// URL containing "
            f"'{_ONELAKE_ENDPOINT_MARKER}' (got '{cleaned}'). Copy it from "
            "GET /v1/workspaces/{id} → oneLakeEndpoints.dfsEndpoint, or "
            f"leave the env var unset to use the global default "
            f"'{_ONELAKE_ACCOUNT_URL}'."
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
    onelake_endpoint: str | None = None,
) -> list[str]:
    """Upload every ``*.parquet`` and ``*.csv`` file in ``tables_dir`` to a Fabric lakehouse.

    Parameters
    ----------
    tables_dir:
        Local directory containing Parquet and/or CSV files to upload.
    workspace_id:
        Fabric workspace ID (GUID). Used as the ADLS filesystem name.
    lakehouse_id:
        Fabric lakehouse ID (GUID). Used verbatim as the top-level
        directory under the workspace filesystem (no ``.Lakehouse``
        suffix — that is a Fabric UI / Spark-shortcut convention that the
        ADLS Gen2 name-plane rejects with ``FriendlyNameSupportDisabled``).
    files_subpath:
        Subfolder under ``Files/`` where the files land. Defaults to
        ``"tables"``.
    credential:
        Optional Azure credential. Defaults to
        :class:`azure.identity.DefaultAzureCredential`.
    onelake_endpoint:
        Optional OneLake DFS endpoint URL. When ``None`` or empty, uses the
        global default ``https://onelake.dfs.fabric.microsoft.com``. Pass a
        regional URL (e.g. ``https://northcentralus-onelake.dfs.fabric.
        microsoft.com``) when the target workspace lives on a capacity that
        advertises a regional endpoint via ``oneLakeEndpoints.dfsEndpoint``.
        The returned ABFSS URLs are unaffected — they always use the
        canonical, non-regional host, because regional routing is a
        data-plane detail applied by OneLake, not a name-plane one.

    Returns
    -------
    list[str]
        ABFSS URLs of every uploaded file.

    Raises
    ------
    LakehouseNotConfiguredError
        If ``workspace_id`` or ``lakehouse_id`` is empty, or is not a valid
        Fabric GUID (after stripping surrounding whitespace/quotes); or if
        ``onelake_endpoint`` is provided but not a valid OneLake DFS URL.
    LakehouseWriteError
        If any upload fails.
    """
    workspace_id = _normalize_fabric_id(workspace_id, "FABRIC_WORKSPACE_ID")
    lakehouse_id = _normalize_fabric_id(lakehouse_id, "FABRIC_LAKEHOUSE_ID")
    account_url = _resolve_onelake_endpoint(onelake_endpoint)

    from azure.storage.filedatalake import DataLakeServiceClient

    cred = credential if credential is not None else _default_credential()
    service = DataLakeServiceClient(account_url, credential=cred)
    file_system = service.get_file_system_client(workspace_id)
    # Bare lakehouse GUID — Fabric's canonical ADLS Gen2 path. The
    # ``.Lakehouse`` suffix seen in Spark shortcuts / UI mount paths is
    # rejected here with ``FriendlyNameSupportDisabled``. Verified against
    # ``GET /v1/workspaces/{ws}/lakehouses/{lh}`` → ``oneLakeFilesPath``.
    target_dir = f"{lakehouse_id}/Files/{files_subpath}"
    directory_client = file_system.get_directory_client(target_dir)

    uploaded: list[str] = []
    # Restricted to Parquet + CSV on purpose — those are the two formats
    # ``export_tables`` / ``export_gold`` produce. Anything else in
    # ``tables_dir`` is almost certainly accidental (stray logs, editor
    # backups, README) and shouldn't be pushed to Fabric.
    upload_files = sorted(
        list(tables_dir.glob("*.parquet")) + list(tables_dir.glob("*.csv"))
    )
    for local_path in upload_files:
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
            f"{lakehouse_id}/Files/{files_subpath}/{local_path.name}"
        )
        uploaded.append(abfss_url)
        logger.info(
            "Uploaded %s (%d bytes) to %s", local_path.name, len(data), abfss_url
        )

    logger.info(
        "OneLake upload complete: %d file(s) written to %s",
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
    onelake_endpoint: str | None = None,
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
    the files in the right place.

    Parquet is the primary format consumed by the PySpark notebook; CSVs
    land alongside for direct download and non-Spark consumers (Excel,
    quick eyeballing, external ingestion). Both formats are uploaded
    whenever they exist locally.

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
    onelake_endpoint:
        Optional OneLake DFS endpoint URL. When ``None`` or empty, uses the
        global default. Pass a regional URL when the target workspace lives
        on a capacity that advertises a regional
        ``oneLakeEndpoints.dfsEndpoint``. See
        :func:`export_to_lakehouse` for the full contract.

    Returns
    -------
    dict[str, list[str]]
        ``{"raw": [...abfss urls...], "gold": [...abfss urls...]}``.

    Raises
    ------
    LakehouseNotConfiguredError
        If ``workspace_id`` or ``lakehouse_id`` is empty, or is not a valid
        Fabric GUID (after stripping surrounding whitespace/quotes); or if
        ``onelake_endpoint`` is provided but not a valid OneLake DFS URL.
    LakehouseWriteError
        If any Parquet upload fails.
    """
    cred = credential if credential is not None else _default_credential()

    def _has_uploadable(d: Path) -> bool:
        return d.exists() and (any(d.glob("*.parquet")) or any(d.glob("*.csv")))

    raw_uploaded: list[str] = []
    if _has_uploadable(tables_dir):
        raw_uploaded = export_to_lakehouse(
            tables_dir,
            workspace_id=workspace_id,
            lakehouse_id=lakehouse_id,
            files_subpath=RAW_SUBPATH,
            credential=cred,
            onelake_endpoint=onelake_endpoint,
        )
    else:
        logger.warning(
            "OneLake raw upload skipped: %s has no Parquet or CSV files.",
            tables_dir,
        )

    gold_uploaded: list[str] = []
    if _has_uploadable(gold_dir):
        gold_uploaded = export_to_lakehouse(
            gold_dir,
            workspace_id=workspace_id,
            lakehouse_id=lakehouse_id,
            files_subpath=GOLD_SUBPATH,
            credential=cred,
            onelake_endpoint=onelake_endpoint,
        )
    else:
        logger.warning(
            "OneLake gold upload skipped: %s has no Parquet or CSV files.",
            gold_dir,
        )

    return {"raw": raw_uploaded, "gold": gold_uploaded}


# ---------------------------------------------------------------------------
# Delta table writeback (OneLake ``Tables/`` via delta-rs)
# ---------------------------------------------------------------------------
#
# The Files/ upload above lands raw Parquet + CSV in the lakehouse and stops
# there. Historically the ``01_load_lakehouse.ipynb`` Fabric notebook then
# had to be opened and run manually to promote those files into managed
# Delta tables (and to create the ``v_impact`` / ``v_compliance`` /
# ``v_capability_health`` views).
#
# The functions below eliminate the notebook step for table materialisation
# by writing Delta tables directly to OneLake ``Tables/`` using the
# ``deltalake`` (delta-rs) Python library. Views still require SQL, so
# they stay in the notebook as a one-time setup — but the *tables* they
# read from now materialise automatically at the end of every ``interpret``
# run.
#
# Design decisions (see ``.squad/decisions/inbox/bishop-onelake-delta-tables.md``):
#
# 1. **Append mode, never overwrite.** Rows accumulate across ``interpret``
#    runs — every run adds new obligation/control/gap rows and preserves
#    prior state. Overwriting would silently lose history and defeat the
#    audit purpose of the lakehouse.
# 2. **Flat namespace under ``Tables/``.** No ``regimpact_raw`` /
#    ``regimpact_gold`` subfolders. The raw entity names
#    (``controls``, ``obligations``, ``regulations``) and the gold names
#    (``dim_control``, ``fact_gap``, ``bridge_gap_entity``) don't collide,
#    so Fabric UI shows a clean flat list of ~34 tables. The Fabric SQL
#    endpoint surfaces flat table names most cleanly; nested "table
#    schemas" under ``Tables/`` are supported but add UI clutter with no
#    upside here.
# 3. **Schema evolution: ``schema_mode="merge"``.** Additive column
#    changes (a new column landing in an upstream regenerator) are
#    absorbed automatically. Incompatible type drift still fails loudly,
#    surfaced as ``LakehouseWriteError`` with the table name in the
#    message. NEVER silently drop columns.
# 4. **``deltalake`` import is lazy.** Same pattern as ``DataLakeServiceClient``
#    above — missing package maps to ``LakehouseNotConfiguredError``
#    pointing at ``pip install regimpact[fabric]``.

# Format of a Delta table URL in OneLake — mirrors the Files pattern
# (bare-GUID canonical form, no ``.Lakehouse`` suffix). Confirmed against
# ``GET /v1/workspaces/{ws}/lakehouses/{lh}`` → ``properties.oneLakeTablesPath``.
_DELTA_URL_TEMPLATE = "abfss://{workspace_id}@{host}/{lakehouse_id}/Tables/{table_name}"

# Azure Storage token audience — same scope the Files upload path uses
# implicitly through ``DefaultAzureCredential``. delta-rs takes the token
# explicitly via ``storage_options["bearer_token"]``.
_STORAGE_SCOPE = "https://storage.azure.com/.default"


def _host_from_endpoint(endpoint_url: str) -> str:
    """Strip the ``https://`` scheme from a resolved OneLake endpoint URL.

    delta-rs consumes the ABFSS URL as its write target, so the host
    embedded in the URL is what it will hit — unlike the Files SDK where
    the account URL is a separate parameter. Passing the resolved endpoint
    host (regional if ``FABRIC_ONELAKE_DFS_ENDPOINT`` is set) keeps
    behaviour parity with the Files upload path.
    """
    return endpoint_url.split("://", 1)[-1].rstrip("/")


def _load_write_deltalake():
    """Lazy-import ``deltalake.write_deltalake``.

    Kept out of module top-level so the core CLI can import ``regimpact``
    without the ``fabric`` extra installed. Missing package is treated as
    a configuration issue (soft-skip class per decision §0), not a
    transient write error.
    """
    try:
        from deltalake import write_deltalake
    except ImportError as exc:  # pragma: no cover — exercised via patch
        raise LakehouseNotConfiguredError(
            "The 'deltalake' package is required for OneLake Tables/ "
            "writeback. Install it with: pip install 'regimpact[fabric]'."
        ) from exc
    return write_deltalake


def _read_parquet_table(parquet_path: Path):
    """Read a Parquet file into a ``pyarrow.Table``.

    ``pyarrow`` is a core (non-optional) dependency, so this needs no
    ``ImportError`` guard. Reads happen against files the local pipeline
    just wrote, so the OS-level file itself is trusted; any failure here
    (corrupted file, disk error) is genuinely a write-side problem and
    maps to :class:`LakehouseWriteError`.
    """
    import pyarrow.parquet as pq

    return pq.read_table(parquet_path)


def _bearer_token(credential) -> str:
    """Fetch a fresh bearer token for the Azure Storage audience.

    ``DefaultAzureCredential`` caches internally, so calling this once per
    Delta write is cheap in the common case. Refetching per-table also
    avoids a mid-batch expiry on a very slow run.
    """
    try:
        token = credential.get_token(_STORAGE_SCOPE)
    except Exception as exc:  # noqa: BLE001 — wrap all credential errors
        raise LakehouseWriteError(
            f"Failed to acquire Azure Storage bearer token for OneLake "
            f"Delta write: {exc}"
        ) from exc
    return token.token


def write_delta_table(
    parquet_path: Path,
    *,
    workspace_id: str,
    lakehouse_id: str,
    table_name: str,
    credential=None,
    onelake_endpoint: str | None = None,
) -> str:
    """Append the contents of ``parquet_path`` to a Delta table in OneLake ``Tables/``.

    The table is created on the first call and appended to on every
    subsequent call. Schema evolution is opt-in via delta-rs's
    ``schema_mode="merge"`` — additive column changes are absorbed
    automatically; incompatible type drift raises
    :class:`LakehouseWriteError` naming the offending table.

    Parameters
    ----------
    parquet_path:
        Local Parquet file to append. Table rows are read via ``pyarrow``.
    workspace_id:
        Fabric workspace ID (GUID). Same validation as :func:`export_to_lakehouse`.
    lakehouse_id:
        Fabric lakehouse ID (GUID). Same validation as :func:`export_to_lakehouse`.
    table_name:
        Name of the target Delta table under ``Tables/``. Should be a
        Fabric-friendly identifier (letters, digits, underscores). File
        stem of ``parquet_path`` is the typical choice.
    credential:
        Optional Azure credential. Defaults to
        :class:`azure.identity.DefaultAzureCredential`.
    onelake_endpoint:
        Optional OneLake DFS endpoint URL. Regional endpoint override,
        honoured exactly like :func:`export_to_lakehouse`.

    Returns
    -------
    str
        The ABFSS URL of the Delta table (``abfss://<ws>@<host>/<lh>/Tables/<name>``).

    Raises
    ------
    LakehouseNotConfiguredError
        If ``workspace_id`` / ``lakehouse_id`` are unset or malformed, if
        ``onelake_endpoint`` is malformed, or if the ``deltalake`` package
        is not installed.
    LakehouseWriteError
        If token acquisition, Parquet read, or the Delta append itself
        fails. The table name appears in the message so operators can
        pinpoint schema drift or a single bad table in a batch run.
    """
    workspace_id = _normalize_fabric_id(workspace_id, "FABRIC_WORKSPACE_ID")
    lakehouse_id = _normalize_fabric_id(lakehouse_id, "FABRIC_LAKEHOUSE_ID")
    account_url = _resolve_onelake_endpoint(onelake_endpoint)
    host = _host_from_endpoint(account_url)

    # Fail fast on missing library BEFORE touching credentials / disk.
    write_deltalake = _load_write_deltalake()

    cred = credential if credential is not None else _default_credential()
    token = _bearer_token(cred)

    try:
        arrow_table = _read_parquet_table(parquet_path)
    except Exception as exc:  # noqa: BLE001
        raise LakehouseWriteError(
            f"Failed to read Parquet file for Delta table '{table_name}' "
            f"(path={parquet_path}): {exc}"
        ) from exc

    table_url = _DELTA_URL_TEMPLATE.format(
        workspace_id=workspace_id,
        host=host,
        lakehouse_id=lakehouse_id,
        table_name=table_name,
    )
    storage_options = {
        "bearer_token": token,
        # Tells the underlying object_store layer to treat this URL as a
        # Fabric OneLake endpoint. Without this flag delta-rs may attempt
        # generic ADLS Gen2 discovery and misroute the request.
        "use_fabric_endpoint": "true",
    }

    try:
        # ``schema_mode="merge"`` absorbs additive schema drift (new
        # columns in an upstream regenerator). Incompatible drift still
        # fails and is re-raised as ``LakehouseWriteError`` below with
        # the table name in the message — never silently drop columns.
        write_deltalake(
            table_url,
            arrow_table,
            mode="append",
            schema_mode="merge",
            storage_options=storage_options,
        )
    except Exception as exc:  # noqa: BLE001 — wrap all delta-rs errors
        raise LakehouseWriteError(
            f"Failed to append Parquet '{parquet_path.name}' to Delta "
            f"table '{table_name}' in OneLake: {exc}"
        ) from exc

    logger.info(
        "Appended %s to Delta table %s (%s)",
        parquet_path.name,
        table_name,
        table_url,
    )
    return table_url


def export_regimpact_tables(
    tables_dir: Path,
    gold_dir: Path,
    *,
    workspace_id: str,
    lakehouse_id: str,
    credential=None,
    onelake_endpoint: str | None = None,
) -> dict[str, list[str]]:
    """Materialise every Parquet file in ``tables_dir`` and ``gold_dir`` as
    a Delta table under OneLake ``Tables/`` (append mode).

    Parquet is the source of truth (typed, larger, exactly what the
    ``export_tables`` / ``export_gold`` local pipeline just produced) —
    CSV siblings are ignored here. Table name for each file is the file
    stem (``dim_control.parquet`` → Delta table ``dim_control``).

    Namespacing is deliberately flat: raw entity table names
    (``controls``, ``obligations``) and gold names
    (``dim_control``, ``fact_gap``) don't collide, so both sets land at
    the top of ``Tables/`` for a clean Fabric UI view.

    Both directories are optional at runtime: missing / empty →
    that half is skipped and reported as an empty list. Mirrors the
    ``export_regimpact_lakehouse`` shape for the Files/ path.

    Parameters
    ----------
    tables_dir:
        Local directory holding raw entity Parquet files.
    gold_dir:
        Local directory holding star-schema Parquet files.
    workspace_id:
        Fabric workspace ID (GUID).
    lakehouse_id:
        Fabric lakehouse ID (GUID).
    credential:
        Optional Azure credential. Defaults to
        :class:`azure.identity.DefaultAzureCredential`.
    onelake_endpoint:
        Optional OneLake DFS endpoint URL. Regional endpoint override,
        honoured exactly like :func:`export_regimpact_lakehouse`.

    Returns
    -------
    dict[str, list[str]]
        ``{"raw": [...table urls...], "gold": [...table urls...]}``.

    Raises
    ------
    LakehouseNotConfiguredError
        If ``workspace_id`` / ``lakehouse_id`` are unset or malformed, if
        ``onelake_endpoint`` is malformed, or if the ``deltalake`` package
        is not installed.
    LakehouseWriteError
        If any Delta append fails. The failing table name appears in the
        message.
    """
    # Resolve credential once so all writes reuse the same identity.
    cred = credential if credential is not None else _default_credential()

    def _write_dir(source: Path) -> list[str]:
        if not source.exists():
            logger.warning(
                "OneLake Delta writeback skipped: %s does not exist.", source
            )
            return []
        parquet_files = sorted(source.glob("*.parquet"))
        if not parquet_files:
            logger.warning(
                "OneLake Delta writeback skipped: %s has no Parquet files.",
                source,
            )
            return []
        urls: list[str] = []
        for parquet_path in parquet_files:
            url = write_delta_table(
                parquet_path,
                workspace_id=workspace_id,
                lakehouse_id=lakehouse_id,
                table_name=parquet_path.stem,
                credential=cred,
                onelake_endpoint=onelake_endpoint,
            )
            urls.append(url)
        return urls

    raw_urls = _write_dir(tables_dir)
    gold_urls = _write_dir(gold_dir)

    logger.info(
        "OneLake Delta writeback complete: %d raw + %d gold table(s) appended.",
        len(raw_urls),
        len(gold_urls),
    )
    return {"raw": raw_urls, "gold": gold_urls}
