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


def test_export_ignores_non_parquet_non_csv_files(tmp_path, monkeypatch):
    _write_parquet_stub(tmp_path / "regulations.parquet", b"real-parquet")
    (tmp_path / "notes.txt").write_text("ignore me", encoding="utf-8")
    (tmp_path / "manifest.json").write_text("{}", encoding="utf-8")
    (tmp_path / "README.md").write_text("# hi", encoding="utf-8")

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


# --- CSV alongside Parquet (parity with local export_tables / export_gold) ---
#
# ``export_tables`` and ``export_gold`` write both ``*.parquet`` and ``*.csv``
# for every entity/star-schema table. The OneLake writeback must upload both
# so users see the same set of files in Fabric that they have on disk.
# The glob stays restricted to those two extensions on purpose — see the
# comment in ``export_to_lakehouse`` for why.


def _write_csv_stub(path: Path, payload: str = "id,name\n1,foo\n") -> None:
    # Write bytes to preserve LF exactly (``write_text`` will translate to
    # CRLF on Windows, which breaks byte-level upload assertions below).
    path.write_bytes(payload.encode("utf-8"))


def test_export_to_lakehouse_uploads_csv_alongside_parquet(tmp_path, monkeypatch):
    """Both ``foo.parquet`` and ``foo.csv`` must be uploaded in the same call."""
    _write_parquet_stub(tmp_path / "foo.parquet", b"parquet-bytes")
    _write_csv_stub(tmp_path / "foo.csv", "id,name\n1,foo\n")

    _, _, file_clients = _wire_mock_datalake(monkeypatch)

    urls = export_to_lakehouse(
        tmp_path,
        workspace_id=_WS_GUID,
        lakehouse_id=_LH_GUID,
        credential=object(),
    )

    assert set(file_clients) == {"foo.parquet", "foo.csv"}
    file_clients["foo.parquet"].upload_data.assert_called_once_with(
        b"parquet-bytes", overwrite=True
    )
    file_clients["foo.csv"].upload_data.assert_called_once_with(
        b"id,name\n1,foo\n", overwrite=True
    )
    assert len(urls) == 2


def test_export_to_lakehouse_uploads_csv_when_no_parquet_present(
    tmp_path, monkeypatch
):
    """CSV upload is not gated on Parquet presence — CSV-only dirs still upload."""
    _write_csv_stub(tmp_path / "foo.csv", "id,name\n1,foo\n")

    _, _, file_clients = _wire_mock_datalake(monkeypatch)

    urls = export_to_lakehouse(
        tmp_path,
        workspace_id=_WS_GUID,
        lakehouse_id=_LH_GUID,
        credential=object(),
    )

    assert list(file_clients) == ["foo.csv"]
    file_clients["foo.csv"].upload_data.assert_called_once_with(
        b"id,name\n1,foo\n", overwrite=True
    )
    assert len(urls) == 1
    assert urls[0].endswith("/foo.csv")


def test_export_to_lakehouse_ignores_other_extensions(tmp_path, monkeypatch):
    """Regression guard — glob must NOT widen to arbitrary files."""
    _write_parquet_stub(tmp_path / "foo.parquet", b"parquet-bytes")
    _write_csv_stub(tmp_path / "foo.csv", "id,name\n1,foo\n")
    (tmp_path / "foo.json").write_text("{}", encoding="utf-8")
    (tmp_path / "foo.txt").write_text("noise", encoding="utf-8")
    (tmp_path / "README.md").write_text("# hi", encoding="utf-8")

    _, _, file_clients = _wire_mock_datalake(monkeypatch)

    urls = export_to_lakehouse(
        tmp_path,
        workspace_id=_WS_GUID,
        lakehouse_id=_LH_GUID,
        credential=object(),
    )

    assert set(file_clients) == {"foo.parquet", "foo.csv"}
    assert len(urls) == 2


def test_export_to_lakehouse_returns_urls_for_both_formats(tmp_path, monkeypatch):
    """Returned ABFSS URL list must contain both the .parquet and .csv URLs."""
    _write_parquet_stub(tmp_path / "regulations.parquet", b"parquet-bytes")
    _write_csv_stub(tmp_path / "regulations.csv", "id,name\n1,foo\n")

    _wire_mock_datalake(monkeypatch)

    urls = export_to_lakehouse(
        tmp_path,
        workspace_id=_WS_GUID,
        lakehouse_id=_LH_GUID,
        credential=object(),
    )

    assert sorted(urls) == sorted(
        [
            f"abfss://{_WS_GUID}@onelake.dfs.fabric.microsoft.com/"
            f"{_LH_GUID}/Files/tables/regulations.parquet",
            f"abfss://{_WS_GUID}@onelake.dfs.fabric.microsoft.com/"
            f"{_LH_GUID}/Files/tables/regulations.csv",
        ]
    )


# ---------------------------------------------------------------------------
# Delta table writeback tests (``export_regimpact_tables`` / ``write_delta_table``)
# ---------------------------------------------------------------------------
#
# These mirror the shape of the Files/ tests above but target the delta-rs
# writeback path. Key differences:
#
# * ``deltalake.write_deltalake`` is the boundary we mock. A fake
#   ``deltalake`` module is installed in ``sys.modules`` before the lazy
#   import fires inside ``write_delta_table``.
# * ``pyarrow`` is a core (non-optional) dependency, so tests write real
#   Parquet files instead of stubs — the mock captures the ``pa.Table``
#   argument and we assert on its shape.
# * Credentials are ``MagicMock``s that respond to ``get_token(scope)``
#   with a mock whose ``.token`` attribute is a canned string.

from regimpact.lakehouse import (  # noqa: E402 — grouped with Delta tests
    export_regimpact_tables,
    write_delta_table,
)


def _install_fake_deltalake_module(monkeypatch, write_fn) -> MagicMock:
    """Register a fake ``deltalake`` module exposing ``write_deltalake``.

    Returns the ``write_deltalake`` mock so tests can assert on call args.
    Mirrors :func:`_install_fake_datalake_module` but for delta-rs.
    """
    write_mock = MagicMock(name="write_deltalake", side_effect=write_fn)
    deltalake_mod = types.ModuleType("deltalake")
    deltalake_mod.write_deltalake = write_mock
    monkeypatch.setitem(sys.modules, "deltalake", deltalake_mod)
    return write_mock


def _write_real_parquet(path: Path, *, rows: int = 2) -> None:
    """Write a small but valid Parquet file so pyarrow.parquet.read_table works."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    table = pa.table(
        {
            "id": list(range(rows)),
            "name": [f"row-{i}" for i in range(rows)],
        }
    )
    pq.write_table(table, path)


def _mock_credential(token_value: str = "fake-bearer-token") -> MagicMock:
    cred = MagicMock(name="credential")
    token = MagicMock(name="token")
    token.token = token_value
    cred.get_token.return_value = token
    return cred


def test_write_delta_table_happy_path_appends_and_returns_url(tmp_path, monkeypatch):
    parquet = tmp_path / "dim_control.parquet"
    _write_real_parquet(parquet, rows=3)

    captured: dict = {}

    def fake_write(url, data, **kwargs):
        captured["url"] = url
        captured["data"] = data
        captured["kwargs"] = kwargs

    write_mock = _install_fake_deltalake_module(monkeypatch, fake_write)
    cred = _mock_credential()

    url = write_delta_table(
        parquet,
        workspace_id=_WS_GUID,
        lakehouse_id=_LH_GUID,
        table_name="dim_control",
        credential=cred,
    )

    # URL uses bare-GUID canonical form, no ``.Lakehouse`` suffix.
    assert url == (
        f"abfss://{_WS_GUID}@onelake.dfs.fabric.microsoft.com/"
        f"{_LH_GUID}/Tables/dim_control"
    )
    write_mock.assert_called_once()
    # Contract: always append, always merge schema.
    assert captured["kwargs"]["mode"] == "append"
    assert captured["kwargs"]["schema_mode"] == "merge"
    # Storage options carry the bearer token and the Fabric flag.
    storage_options = captured["kwargs"]["storage_options"]
    assert storage_options["bearer_token"] == "fake-bearer-token"
    assert storage_options["use_fabric_endpoint"] == "true"
    # Credential was asked for a Storage token.
    cred.get_token.assert_called_once_with("https://storage.azure.com/.default")


def test_write_delta_table_honours_regional_endpoint_override(tmp_path, monkeypatch):
    parquet = tmp_path / "controls.parquet"
    _write_real_parquet(parquet)

    captured: dict = {}

    def fake_write(url, data, **kwargs):
        captured["url"] = url

    _install_fake_deltalake_module(monkeypatch, fake_write)

    regional = "https://northcentralus-onelake.dfs.fabric.microsoft.com"
    url = write_delta_table(
        parquet,
        workspace_id=_WS_GUID,
        lakehouse_id=_LH_GUID,
        table_name="controls",
        credential=_mock_credential(),
        onelake_endpoint=regional,
    )

    # Unlike the Files/ path (where the returned URL keeps the canonical
    # host because Spark shortcuts parse it), the Delta URL uses the
    # actual host delta-rs will hit — regional when overridden.
    expected_host = "northcentralus-onelake.dfs.fabric.microsoft.com"
    assert url == (
        f"abfss://{_WS_GUID}@{expected_host}/{_LH_GUID}/Tables/controls"
    )
    assert captured["url"] == url


def test_write_delta_table_rejects_malformed_workspace_id(tmp_path, monkeypatch):
    parquet = tmp_path / "controls.parquet"
    _write_real_parquet(parquet)
    _install_fake_deltalake_module(monkeypatch, lambda *a, **k: None)

    with pytest.raises(LakehouseNotConfiguredError):
        write_delta_table(
            parquet,
            workspace_id="not-a-guid",
            lakehouse_id=_LH_GUID,
            table_name="controls",
            credential=_mock_credential(),
        )


def test_write_delta_table_missing_deltalake_package_raises_configured_error(
    tmp_path, monkeypatch
):
    parquet = tmp_path / "controls.parquet"
    _write_real_parquet(parquet)
    # Simulate deltalake not installed: ensure any real one is masked out,
    # and register a sentinel that will raise ImportError on ``from``-import.
    monkeypatch.setitem(sys.modules, "deltalake", None)

    with pytest.raises(LakehouseNotConfiguredError) as excinfo:
        write_delta_table(
            parquet,
            workspace_id=_WS_GUID,
            lakehouse_id=_LH_GUID,
            table_name="controls",
            credential=_mock_credential(),
        )

    # Message must point users at the extras install so this is actionable.
    assert "regimpact[fabric]" in str(excinfo.value)


def test_write_delta_table_wraps_deltalake_errors_with_table_name(
    tmp_path, monkeypatch
):
    parquet = tmp_path / "dim_control.parquet"
    _write_real_parquet(parquet)

    def fake_write(url, data, **kwargs):
        # Simulate delta-rs schema-mismatch or transient IO failure.
        raise RuntimeError("Schema mismatch: column 'x' type Int64 != Utf8")

    _install_fake_deltalake_module(monkeypatch, fake_write)

    with pytest.raises(LakehouseWriteError) as excinfo:
        write_delta_table(
            parquet,
            workspace_id=_WS_GUID,
            lakehouse_id=_LH_GUID,
            table_name="dim_control",
            credential=_mock_credential(),
        )

    # Table name must appear in the message so operators can pinpoint
    # which of the 34 tables in a batch failed.
    assert "dim_control" in str(excinfo.value)
    assert isinstance(excinfo.value.__cause__, RuntimeError)


def test_write_delta_table_wraps_credential_errors(tmp_path, monkeypatch):
    parquet = tmp_path / "controls.parquet"
    _write_real_parquet(parquet)
    _install_fake_deltalake_module(monkeypatch, lambda *a, **k: None)

    cred = MagicMock(name="credential")
    cred.get_token.side_effect = RuntimeError("MSAL cache corrupted")

    with pytest.raises(LakehouseWriteError) as excinfo:
        write_delta_table(
            parquet,
            workspace_id=_WS_GUID,
            lakehouse_id=_LH_GUID,
            table_name="controls",
            credential=cred,
        )

    assert "bearer token" in str(excinfo.value).lower()


def test_export_regimpact_tables_writes_raw_and_gold_with_flat_names(
    tmp_path, monkeypatch
):
    raw_dir = tmp_path / "raw"
    gold_dir = tmp_path / "gold"
    raw_dir.mkdir()
    gold_dir.mkdir()

    # Real Fabric shape: raw ``controls`` vs gold ``dim_control`` — verified
    # no name collision, so flat namespace under Tables/ is safe.
    _write_real_parquet(raw_dir / "controls.parquet")
    _write_real_parquet(raw_dir / "regulations.parquet")
    _write_real_parquet(gold_dir / "dim_control.parquet")
    _write_real_parquet(gold_dir / "fact_gap.parquet")

    called_urls: list[str] = []

    def fake_write(url, data, **kwargs):
        called_urls.append(url)

    _install_fake_deltalake_module(monkeypatch, fake_write)

    result = export_regimpact_tables(
        raw_dir,
        gold_dir,
        workspace_id=_WS_GUID,
        lakehouse_id=_LH_GUID,
        credential=_mock_credential(),
    )

    assert set(result) == {"raw", "gold"}
    assert len(result["raw"]) == 2
    assert len(result["gold"]) == 2

    # Names come from file stems; no ``regimpact_raw`` / ``regimpact_gold``
    # prefix — flat under Tables/.
    all_urls = result["raw"] + result["gold"]
    assert any(u.endswith("/Tables/controls") for u in all_urls)
    assert any(u.endswith("/Tables/regulations") for u in all_urls)
    assert any(u.endswith("/Tables/dim_control") for u in all_urls)
    assert any(u.endswith("/Tables/fact_gap") for u in all_urls)
    # No .Lakehouse suffix anywhere — same invariant as the Files path.
    assert not any(".Lakehouse" in u for u in all_urls)


def test_export_regimpact_tables_ignores_csv_siblings(tmp_path, monkeypatch):
    raw_dir = tmp_path / "raw"
    gold_dir = tmp_path / "gold"
    raw_dir.mkdir()
    gold_dir.mkdir()

    _write_real_parquet(raw_dir / "controls.parquet")
    # CSV sibling must be ignored — Parquet is source of truth for Delta.
    (raw_dir / "controls.csv").write_bytes(b"id,name\n1,x\n")
    (raw_dir / "notes.txt").write_bytes(b"skip me")

    call_count = {"n": 0}

    def fake_write(url, data, **kwargs):
        call_count["n"] += 1

    _install_fake_deltalake_module(monkeypatch, fake_write)

    result = export_regimpact_tables(
        raw_dir,
        gold_dir,
        workspace_id=_WS_GUID,
        lakehouse_id=_LH_GUID,
        credential=_mock_credential(),
    )

    # Exactly one write — just the .parquet, never the .csv or .txt.
    assert call_count["n"] == 1
    assert len(result["raw"]) == 1
    assert result["gold"] == []


def test_export_regimpact_tables_missing_dirs_return_empty_lists(
    tmp_path, monkeypatch
):
    # Neither dir exists — this happens on a fresh checkout before demo
    # has produced gold outputs. Should return empty, not raise.
    _install_fake_deltalake_module(monkeypatch, lambda *a, **k: None)

    result = export_regimpact_tables(
        tmp_path / "missing-raw",
        tmp_path / "missing-gold",
        workspace_id=_WS_GUID,
        lakehouse_id=_LH_GUID,
        credential=_mock_credential(),
    )

    assert result == {"raw": [], "gold": []}


def test_export_regimpact_tables_propagates_write_failure_with_table_name(
    tmp_path, monkeypatch
):
    raw_dir = tmp_path / "raw"
    gold_dir = tmp_path / "gold"
    raw_dir.mkdir()
    gold_dir.mkdir()
    _write_real_parquet(raw_dir / "regulations.parquet")

    def fake_write(url, data, **kwargs):
        raise RuntimeError("delta-rs: transient IO error")

    _install_fake_deltalake_module(monkeypatch, fake_write)

    with pytest.raises(LakehouseWriteError) as excinfo:
        export_regimpact_tables(
            raw_dir,
            gold_dir,
            workspace_id=_WS_GUID,
            lakehouse_id=_LH_GUID,
            credential=_mock_credential(),
        )

    # Table name (file stem) must appear in the message.
    assert "regulations" in str(excinfo.value)


def test_export_regimpact_tables_shares_single_credential_across_writes(
    tmp_path, monkeypatch
):
    raw_dir = tmp_path / "raw"
    gold_dir = tmp_path / "gold"
    raw_dir.mkdir()
    gold_dir.mkdir()
    _write_real_parquet(raw_dir / "controls.parquet")
    _write_real_parquet(raw_dir / "regulations.parquet")
    _write_real_parquet(gold_dir / "dim_control.parquet")

    _install_fake_deltalake_module(monkeypatch, lambda *a, **k: None)
    cred = _mock_credential()

    export_regimpact_tables(
        raw_dir,
        gold_dir,
        workspace_id=_WS_GUID,
        lakehouse_id=_LH_GUID,
        credential=cred,
    )

    # 3 writes → 3 token fetches on the SAME credential object. This
    # confirms the credential is reused across writes (no per-table
    # DefaultAzureCredential churn) and each write gets a fresh token
    # to survive a mid-batch expiry.
    assert cred.get_token.call_count == 3
    for call in cred.get_token.call_args_list:
        assert call.args == ("https://storage.azure.com/.default",)
