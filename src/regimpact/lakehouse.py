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
from pathlib import Path

logger = logging.getLogger(__name__)

_ONELAKE_ACCOUNT_URL = "https://onelake.dfs.fabric.microsoft.com"


class LakehouseError(Exception):
    """Base class for OneLake writeback errors."""


class LakehouseNotConfiguredError(LakehouseError):
    """Raised when FABRIC_WORKSPACE_ID or FABRIC_LAKEHOUSE_ID is not set."""


class LakehouseWriteError(LakehouseError):
    """Raised when uploading a Parquet file to OneLake fails."""


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
        If ``workspace_id`` or ``lakehouse_id`` is empty.
    LakehouseWriteError
        If any Parquet upload fails.
    """
    if not workspace_id:
        raise LakehouseNotConfiguredError(
            "FABRIC_WORKSPACE_ID is not set; cannot write to OneLake."
        )
    if not lakehouse_id:
        raise LakehouseNotConfiguredError(
            "FABRIC_LAKEHOUSE_ID is not set; cannot write to OneLake."
        )

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
