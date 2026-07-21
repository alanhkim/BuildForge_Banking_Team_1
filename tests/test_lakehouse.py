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

# Canonical GUIDs used as valid Fabric workspace/lakehouse IDs in tests.
# The real values are opaque GUIDs from the Fabric portal; using distinct
# all-ones and all-twos patterns keeps the ABFSS URL assertions readable.
_WS_GUID = "11111111-1111-1111-1111-111111111111"
_LH_GUID = "22222222-2222-2222-2222-222222222222"


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
            lakehouse_id=_LH_GUID,
            credential=object(),
        )


def test_export_raises_when_lakehouse_unset(tmp_path):
    with pytest.raises(LakehouseNotConfiguredError):
        export_to_lakehouse(
            tmp_path,
            workspace_id=_WS_GUID,
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
        workspace_id=_WS_GUID,
        lakehouse_id=_LH_GUID,
        credential=object(),
    )

    service_cls.assert_called_once()
    account_url, kwargs = service_cls.call_args.args, service_cls.call_args.kwargs
    assert account_url[0] == "https://onelake.dfs.fabric.microsoft.com"
    assert "credential" in kwargs

    service_instance.get_file_system_client.assert_called_once_with(_WS_GUID)
    file_system.get_directory_client.assert_called_once_with(
        f"{_LH_GUID}/Files/tables"
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
            f"abfss://{_WS_GUID}@onelake.dfs.fabric.microsoft.com/"
            f"{_LH_GUID}/Files/tables/regulations.parquet",
            f"abfss://{_WS_GUID}@onelake.dfs.fabric.microsoft.com/"
            f"{_LH_GUID}/Files/tables/controls.parquet",
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
            workspace_id=_WS_GUID,
            lakehouse_id=_LH_GUID,
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
        workspace_id=_WS_GUID,
        lakehouse_id=_LH_GUID,
        credential=object(),
    )

    assert list(file_clients) == ["regulations.parquet"]
    assert len(urls) == 1
    assert urls[0].endswith("/regulations.parquet")


# --- GUID normalization / validation (FriendlyNameSupportDisabled hardening) ---


def _wire_mock_datalake(monkeypatch) -> tuple[MagicMock, MagicMock, dict[str, MagicMock]]:
    """Wire up a minimal fake DataLakeServiceClient and return (service_cls, file_system, file_clients)."""
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
    return service_cls, file_system, file_clients


def test_export_strips_trailing_newline_from_workspace_id(tmp_path, monkeypatch):
    """`export FABRIC_WORKSPACE_ID="…"` in a shell often leaves a trailing \\n."""
    _write_parquet_stub(tmp_path / "regulations.parquet")
    _wire_mock_datalake(monkeypatch)

    urls = export_to_lakehouse(
        tmp_path,
        workspace_id=f"{_WS_GUID}\n",
        lakehouse_id=_LH_GUID,
        credential=object(),
    )

    # The ABFSS URL must contain the stripped GUID, not the raw newline value.
    assert urls == [
        f"abfss://{_WS_GUID}@onelake.dfs.fabric.microsoft.com/"
        f"{_LH_GUID}/Files/tables/regulations.parquet"
    ]


def test_export_strips_newline_and_passes_clean_guid_to_sdk(tmp_path, monkeypatch):
    _write_parquet_stub(tmp_path / "regulations.parquet")
    service_cls, file_system, _ = _wire_mock_datalake(monkeypatch)

    export_to_lakehouse(
        tmp_path,
        workspace_id=f"  {_WS_GUID}\n",
        lakehouse_id=_LH_GUID,
        credential=object(),
    )

    service_instance = service_cls.return_value
    # Filesystem name (workspace) must be the stripped GUID, not the raw input.
    service_instance.get_file_system_client.assert_called_once_with(_WS_GUID)
    file_system.get_directory_client.assert_called_once_with(
        f"{_LH_GUID}/Files/tables"
    )


def test_export_rejects_non_guid_workspace_id(tmp_path):
    with pytest.raises(LakehouseNotConfiguredError) as excinfo:
        export_to_lakehouse(
            tmp_path,
            workspace_id="my-workspace-name",
            lakehouse_id=_LH_GUID,
            credential=object(),
        )

    msg = str(excinfo.value)
    assert "FABRIC_WORKSPACE_ID" in msg
    assert "my-workspace-name" in msg


def test_export_rejects_whitespace_only_lakehouse_id(tmp_path):
    with pytest.raises(LakehouseNotConfiguredError) as excinfo:
        export_to_lakehouse(
            tmp_path,
            workspace_id=_WS_GUID,
            lakehouse_id="   ",
            credential=object(),
        )

    # Strip runs before the empty check, so this maps to the "not set" branch.
    assert "FABRIC_LAKEHOUSE_ID" in str(excinfo.value)


def test_export_strips_surrounding_quotes_from_lakehouse_id(tmp_path, monkeypatch):
    """A shell-quoted value like `'11111111-…'` should have its quotes stripped."""
    _write_parquet_stub(tmp_path / "regulations.parquet")
    service_cls, file_system, _ = _wire_mock_datalake(monkeypatch)

    urls = export_to_lakehouse(
        tmp_path,
        workspace_id=_WS_GUID,
        lakehouse_id=f"'{_LH_GUID}'",
        credential=object(),
    )

    service_instance = service_cls.return_value
    service_instance.get_file_system_client.assert_called_once_with(_WS_GUID)
    file_system.get_directory_client.assert_called_once_with(
        f"{_LH_GUID}/Files/tables"
    )
    assert urls == [
        f"abfss://{_WS_GUID}@onelake.dfs.fabric.microsoft.com/"
        f"{_LH_GUID}/Files/tables/regulations.parquet"
    ]


# --- OneLake DFS endpoint override (FriendlyNameSupportDisabled hardening) ---
#
# Some Fabric capacities advertise a *regional* OneLake endpoint via
# ``GET /v1/workspaces/{id}`` → ``oneLakeEndpoints.dfsEndpoint``. On those
# capacities the global endpoint returns ``FriendlyNameSupportDisabled``
# instead of forwarding, so callers must point the SDK at the regional host
# directly. The ABFSS URL format is unchanged — regional routing is a
# data-plane detail, not a name-plane one.

_REGIONAL_ENDPOINT = "https://northcentralus-onelake.dfs.fabric.microsoft.com"


@pytest.mark.parametrize("endpoint_arg", [None, "", "   "])
def test_export_uses_global_endpoint_when_override_unset(
    tmp_path, monkeypatch, endpoint_arg
):
    """``None`` / empty / whitespace-only → falls back to the global default."""
    _write_parquet_stub(tmp_path / "regulations.parquet")
    service_cls, _, _ = _wire_mock_datalake(monkeypatch)

    export_to_lakehouse(
        tmp_path,
        workspace_id=_WS_GUID,
        lakehouse_id=_LH_GUID,
        credential=object(),
        onelake_endpoint=endpoint_arg,
    )

    account_url = service_cls.call_args.args[0]
    assert account_url == "https://onelake.dfs.fabric.microsoft.com"


def test_export_uses_regional_endpoint_when_override_set(tmp_path, monkeypatch):
    """Explicit regional endpoint → passed straight through to the SDK."""
    _write_parquet_stub(tmp_path / "regulations.parquet")
    service_cls, _, _ = _wire_mock_datalake(monkeypatch)

    export_to_lakehouse(
        tmp_path,
        workspace_id=_WS_GUID,
        lakehouse_id=_LH_GUID,
        credential=object(),
        onelake_endpoint=_REGIONAL_ENDPOINT,
    )

    account_url = service_cls.call_args.args[0]
    assert account_url == _REGIONAL_ENDPOINT


def test_export_abfss_url_uses_canonical_host_even_with_regional_endpoint(
    tmp_path, monkeypatch
):
    """Regional routing is a data-plane detail — ABFSS URLs stay canonical.

    Downstream tools (Spark shortcuts, notebook loaders) parse these URLs
    and expect the non-regional ``onelake.dfs.fabric.microsoft.com`` host.
    Accidentally region-tainting the URL would break them.
    """
    _write_parquet_stub(tmp_path / "regulations.parquet")
    _wire_mock_datalake(monkeypatch)

    urls = export_to_lakehouse(
        tmp_path,
        workspace_id=_WS_GUID,
        lakehouse_id=_LH_GUID,
        credential=object(),
        onelake_endpoint=_REGIONAL_ENDPOINT,
    )

    assert urls == [
        f"abfss://{_WS_GUID}@onelake.dfs.fabric.microsoft.com/"
        f"{_LH_GUID}/Files/tables/regulations.parquet"
    ]
    # Belt-and-braces: no regional prefix leaked into the returned URL.
    assert "northcentralus-onelake" not in urls[0]


def test_export_rejects_endpoint_missing_scheme(tmp_path):
    """Missing ``https://`` → soft-skip via LakehouseNotConfiguredError."""
    with pytest.raises(LakehouseNotConfiguredError) as excinfo:
        export_to_lakehouse(
            tmp_path,
            workspace_id=_WS_GUID,
            lakehouse_id=_LH_GUID,
            credential=object(),
            onelake_endpoint="onelake.dfs.fabric.microsoft.com",
        )

    msg = str(excinfo.value)
    assert "FABRIC_ONELAKE_DFS_ENDPOINT" in msg
    assert "onelake.dfs.fabric.microsoft.com" in msg


def test_export_rejects_endpoint_pointing_at_non_onelake_host(tmp_path):
    """https:// but not a OneLake host → soft-skip via LakehouseNotConfiguredError."""
    with pytest.raises(LakehouseNotConfiguredError) as excinfo:
        export_to_lakehouse(
            tmp_path,
            workspace_id=_WS_GUID,
            lakehouse_id=_LH_GUID,
            credential=object(),
            onelake_endpoint="https://example.com",
        )

    msg = str(excinfo.value)
    assert "FABRIC_ONELAKE_DFS_ENDPOINT" in msg
    assert "example.com" in msg


# --- Regression guards: bare-GUID canonical ADLS path (no `.Lakehouse` suffix) ---
#
# Confirmed against Fabric REST: ``GET /v1/workspaces/{ws}/lakehouses/{lh}`` →
# ``properties.oneLakeFilesPath`` returns
# ``https://onelake.dfs.fabric.microsoft.com/{workspace_id}/{lakehouse_id}/Files``
# — no ``.Lakehouse`` suffix. The suffix is a Fabric UI / Spark-shortcut
# convention that the ADLS Gen2 name-plane rejects with
# ``FriendlyNameSupportDisabled``. These guards ensure nobody re-adds it
# from a stale blog post or Spark-mount doc.


def test_export_to_lakehouse_uses_bare_guid_directory_no_lakehouse_suffix(
    tmp_path, monkeypatch
):
    """The directory client MUST be created with the bare lakehouse GUID."""
    _write_parquet_stub(tmp_path / "regulations.parquet")
    _, file_system, _ = _wire_mock_datalake(monkeypatch)

    export_to_lakehouse(
        tmp_path,
        workspace_id=_WS_GUID,
        lakehouse_id=_LH_GUID,
        credential=object(),
    )

    file_system.get_directory_client.assert_called_once_with(
        f"{_LH_GUID}/Files/tables"
    )
    # The passed directory string must not contain the stale `.Lakehouse`
    # suffix anywhere — not as a prefix, not as a nested segment.
    (target_dir,) = file_system.get_directory_client.call_args.args
    assert ".Lakehouse" not in target_dir


def test_export_returned_abfss_urls_never_contain_lakehouse_suffix(
    tmp_path, monkeypatch
):
    """Returned ABFSS URLs must use the bare lakehouse GUID as the top-level dir."""
    _write_parquet_stub(tmp_path / "regulations.parquet")
    _write_parquet_stub(tmp_path / "controls.parquet")
    _wire_mock_datalake(monkeypatch)

    urls = export_to_lakehouse(
        tmp_path,
        workspace_id=_WS_GUID,
        lakehouse_id=_LH_GUID,
        credential=object(),
    )

    assert urls, "expected at least one uploaded URL"
    for url in urls:
        assert ".Lakehouse" not in url, (
            f"stale `.Lakehouse` suffix leaked into ABFSS URL: {url}"
        )
