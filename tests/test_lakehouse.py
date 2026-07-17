"""Tests for the OneLake writeback module.

These tests never contact Azure. ``azure.storage.filedatalake`` is imported
lazily inside :func:`regimpact.lakehouse.export_to_lakehouse`, so we patch the
symbol on the ``regimpact.lakehouse`` module before it is dereferenced.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from regimpact.lakehouse import (
    LakehouseNotConfiguredError,
    LakehouseWriteError,
    export_to_lakehouse,
)


def _install_fake_datalake_module(monkeypatch, service_client_cls) -> None:
    """Register a fake ``azure.storage.filedatalake`` module.

    The real dependency ships with the optional ``fabric`` extra and may be
    absent in CI. The lazy import inside ``export_to_lakehouse`` performs
    ``from azure.storage.filedatalake import DataLakeServiceClient``, so we
    plant a lightweight module tree exposing that symbol.
    """
    azure_mod = types.ModuleType("azure")
    storage_mod = types.ModuleType("azure.storage")
    filedatalake_mod = types.ModuleType("azure.storage.filedatalake")
    filedatalake_mod.DataLakeServiceClient = service_client_cls
    azure_mod.storage = storage_mod
    storage_mod.filedatalake = filedatalake_mod
    monkeypatch.setitem(sys.modules, "azure", azure_mod)
    monkeypatch.setitem(sys.modules, "azure.storage", storage_mod)
    monkeypatch.setitem(sys.modules, "azure.storage.filedatalake", filedatalake_mod)


def _write_parquet_stub(path: Path, payload: bytes = b"PAR1-stub") -> None:
    path.write_bytes(payload)


def test_export_raises_when_workspace_unset(tmp_path):
    with pytest.raises(LakehouseNotConfiguredError):
        export_to_lakehouse(
            tmp_path,
            workspace_id="",
            lakehouse_id="lake-1",
            credential=object(),
        )


def test_export_raises_when_lakehouse_unset(tmp_path):
    with pytest.raises(LakehouseNotConfiguredError):
        export_to_lakehouse(
            tmp_path,
            workspace_id="ws-1",
            lakehouse_id="",
            credential=object(),
        )


def test_export_uploads_all_parquet_files(tmp_path, monkeypatch):
    _write_parquet_stub(tmp_path / "regulations.parquet", b"data-1")
    _write_parquet_stub(tmp_path / "controls.parquet", b"data-2")

    file_clients: dict[str, MagicMock] = {}

    def make_file_client(name):
        client = MagicMock(name=f"file_client[{name}]")
        file_clients[name] = client
        return client

    directory_client = MagicMock(name="directory_client")
    directory_client.get_file_client.side_effect = make_file_client

    file_system = MagicMock(name="file_system")
    file_system.get_directory_client.return_value = directory_client

    service_instance = MagicMock(name="service")
    service_instance.get_file_system_client.return_value = file_system

    service_cls = MagicMock(name="DataLakeServiceClient", return_value=service_instance)
    _install_fake_datalake_module(monkeypatch, service_cls)

    urls = export_to_lakehouse(
        tmp_path,
        workspace_id="ws-1",
        lakehouse_id="lake-1",
        credential=object(),
    )

    service_cls.assert_called_once()
    account_url, kwargs = service_cls.call_args.args, service_cls.call_args.kwargs
    assert account_url[0] == "https://onelake.dfs.fabric.microsoft.com"
    assert "credential" in kwargs

    service_instance.get_file_system_client.assert_called_once_with("ws-1")
    file_system.get_directory_client.assert_called_once_with(
        "lake-1.Lakehouse/Files/tables"
    )

    assert set(file_clients) == {"regulations.parquet", "controls.parquet"}
    file_clients["regulations.parquet"].upload_data.assert_called_once_with(
        b"data-1", overwrite=True
    )
    file_clients["controls.parquet"].upload_data.assert_called_once_with(
        b"data-2", overwrite=True
    )

    assert sorted(urls) == sorted(
        [
            "abfss://ws-1@onelake.dfs.fabric.microsoft.com/"
            "lake-1.Lakehouse/Files/tables/regulations.parquet",
            "abfss://ws-1@onelake.dfs.fabric.microsoft.com/"
            "lake-1.Lakehouse/Files/tables/controls.parquet",
        ]
    )


def test_export_wraps_azure_errors(tmp_path, monkeypatch):
    _write_parquet_stub(tmp_path / "regulations.parquet")

    failing_file_client = MagicMock(name="file_client")
    failing_file_client.upload_data.side_effect = RuntimeError("network gone")

    directory_client = MagicMock(name="directory_client")
    directory_client.get_file_client.return_value = failing_file_client

    file_system = MagicMock(name="file_system")
    file_system.get_directory_client.return_value = directory_client

    service_instance = MagicMock(name="service")
    service_instance.get_file_system_client.return_value = file_system

    service_cls = MagicMock(name="DataLakeServiceClient", return_value=service_instance)
    _install_fake_datalake_module(monkeypatch, service_cls)

    with pytest.raises(LakehouseWriteError) as excinfo:
        export_to_lakehouse(
            tmp_path,
            workspace_id="ws-1",
            lakehouse_id="lake-1",
            credential=object(),
        )

    assert "regulations.parquet" in str(excinfo.value)
    assert isinstance(excinfo.value.__cause__, RuntimeError)


def test_export_ignores_non_parquet_files(tmp_path, monkeypatch):
    _write_parquet_stub(tmp_path / "regulations.parquet", b"real-parquet")
    (tmp_path / "regulations.csv").write_text("id,name\n1,foo\n", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("ignore me", encoding="utf-8")

    file_clients: dict[str, MagicMock] = {}

    def make_file_client(name):
        client = MagicMock(name=f"file_client[{name}]")
        file_clients[name] = client
        return client

    directory_client = MagicMock(name="directory_client")
    directory_client.get_file_client.side_effect = make_file_client

    file_system = MagicMock(name="file_system")
    file_system.get_directory_client.return_value = directory_client

    service_instance = MagicMock(name="service")
    service_instance.get_file_system_client.return_value = file_system

    service_cls = MagicMock(name="DataLakeServiceClient", return_value=service_instance)
    _install_fake_datalake_module(monkeypatch, service_cls)

    urls = export_to_lakehouse(
        tmp_path,
        workspace_id="ws-1",
        lakehouse_id="lake-1",
        credential=object(),
    )

    assert list(file_clients) == ["regulations.parquet"]
    assert len(urls) == 1
    assert urls[0].endswith("/regulations.parquet")
