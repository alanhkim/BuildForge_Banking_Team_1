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

import pyarrow as pa
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


def _install_fake_deltalake_module(
    monkeypatch, write_fn, delta_table=None
) -> MagicMock:
    """Register a fake ``deltalake`` module exposing ``write_deltalake``.

    Also plants a ``DeltaTable`` symbol so :func:`_load_delta_table` in
    the production code can import it. When ``delta_table`` is ``None``
    (the default), the planted ``DeltaTable`` raises a fake
    ``TableNotFoundError`` on construction — this forces the "first
    write" fallback path (``write_deltalake(mode="append",
    schema_mode="merge")``), which matches what all pre-MERGE tests were
    asserting on. Pass a custom callable / class when a test needs the
    MERGE-upsert path.

    Returns the ``write_deltalake`` mock so tests can assert on call args.
    Mirrors :func:`_install_fake_datalake_module` but for delta-rs.
    """
    write_mock = MagicMock(name="write_deltalake", side_effect=write_fn)
    deltalake_mod = types.ModuleType("deltalake")
    deltalake_mod.write_deltalake = write_mock
    if delta_table is None:
        def _default_dt_ctor(*args, **kwargs):
            raise _FakeTableNotFoundError("Delta table does not exist")
        deltalake_mod.DeltaTable = _default_dt_ctor
    else:
        deltalake_mod.DeltaTable = delta_table
    monkeypatch.setitem(sys.modules, "deltalake", deltalake_mod)
    return write_mock


class _FakeTableNotFoundError(Exception):
    """Stand-in for ``deltalake.exceptions.TableNotFoundError``.

    Production code matches by class name (see ``_is_table_not_found``
    in ``regimpact/lakehouse.py``) so we only need the class name to be
    ``TableNotFoundError`` — the class body itself can stay trivial.
    """
    pass


# The production ``_is_table_not_found`` check keys on ``type(exc).__name__``,
# so we forcibly rename this fake to match the real delta-rs class name.
_FakeTableNotFoundError.__name__ = "TableNotFoundError"


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


# ---------------------------------------------------------------------------
# MERGE-upsert path tests (2026-07-21 follow-up on the initial Delta append
# landing). These exercise the branch of ``write_delta_table`` that fires
# when ``DeltaTable(...)`` opens successfully — the "table already exists"
# case that must dedup / upsert rather than blindly append.
# ---------------------------------------------------------------------------


def _build_merge_recording_dt(
    capture: dict,
    *,
    target_schema=None,
) -> MagicMock:
    """Build a DeltaTable-shaped MagicMock whose .merge() chain is captured.

    The production MERGE path calls, in order:
        target_arrow = pa.schema(dt.schema().to_arrow())
        target_cols  = [f.name for f in dt.schema().fields]
        dt.merge(source, predicate, source_alias, target_alias)
          .when_matched_update_all(predicate=...)   # only if compare cols exist
          .when_not_matched_insert_all()
          .execute()

    This helper returns a MagicMock configured so the chained builder
    calls return the same builder instance (records ALL invocations) and
    ``capture`` receives the ``merge()`` call kwargs plus a handle to the
    builder for post-hoc assertions.

    ``target_schema`` may be:

    * ``pa.Schema`` — names AND types are wired verbatim. Use this in
      tests that exercise type coercion (LargeUtf8 vs Utf8, int32 vs
      int64, refusal on string↔numeric, etc.). ``dt.schema().to_arrow()``
      returns exactly this pa.Schema.
    * ``list[str]`` — names only; types default to ``pa.string()`` for
      every field. Backward-compat form for tests that only exercise
      case alignment / predicate wiring and whose source data is
      predominantly strings. Since the production cast helper skips
      cast when source_type == target_type, all-string source data
      round-trips untouched.
    * ``None`` — no schema wired (used for tests that never enter the
      MERGE branch, or first-write path tests).

    Notes
    -----
    MagicMock's ``name=`` constructor kwarg controls repr, NOT the
    ``.name`` attribute, so we set ``.name`` explicitly on the field
    mocks so production's ``[f.name for f in fields]`` returns strings
    (not more MagicMock instances).
    """
    import pyarrow as pa  # lazy — keeps import cost off the module load

    builder = MagicMock(name="merge_builder")
    builder.when_matched_update_all.return_value = builder
    builder.when_not_matched_insert_all.return_value = builder

    def _merge(*, source, predicate, source_alias, target_alias):
        capture["source"] = source
        capture["predicate"] = predicate
        capture["source_alias"] = source_alias
        capture["target_alias"] = target_alias
        capture["builder"] = builder
        return builder

    dt = MagicMock(name="DeltaTable_instance")
    dt.merge = MagicMock(side_effect=_merge)

    # Normalise target_schema → (name list for .fields, pa.Schema for .to_arrow).
    if target_schema is None:
        target_names: list[str] = []
        target_pa_schema = pa.schema([])
    elif isinstance(target_schema, pa.Schema):
        target_names = list(target_schema.names)
        target_pa_schema = target_schema
    elif isinstance(target_schema, list):
        target_names = list(target_schema)
        target_pa_schema = pa.schema([(n, pa.string()) for n in target_names])
    else:
        raise TypeError(
            f"_build_merge_recording_dt: target_schema must be pa.Schema, "
            f"list[str], or None (got {type(target_schema).__name__})"
        )

    fake_fields = []
    for col_name in target_names:
        f = MagicMock()
        f.name = col_name
        fake_fields.append(f)
    fake_schema = MagicMock(name="DeltaSchema")
    fake_schema.fields = fake_fields
    # Production wraps this in ``pa.schema(...)`` — passing a pa.Schema
    # through pa.schema() is idempotent and returns the same-shaped
    # schema, so this correctly stands in for arro3.core → pyarrow.
    fake_schema.to_arrow = MagicMock(return_value=target_pa_schema)
    dt.schema.return_value = fake_schema
    return dt


def _write_parquet_with_columns(path: Path, columns: dict) -> None:
    """Write a Parquet file with the exact column dict supplied."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    pq.write_table(pa.table(columns), path)


def test_write_delta_table_merges_when_table_exists(tmp_path, monkeypatch):
    """When DeltaTable opens successfully we drive .merge(...) and NEVER
    call write_deltalake (the create-path fallback)."""
    parquet = tmp_path / "dim_control.parquet"
    _write_real_parquet(parquet)

    capture: dict = {}
    # _write_real_parquet emits {id: int64, name: string} — target
    # agrees on both case AND types, so both alignment steps are no-ops.
    dt_instance = _build_merge_recording_dt(
        capture,
        target_schema=pa.schema({"id": pa.int64(), "name": pa.string()}),
    )

    def _dt_ctor(url, storage_options=None):
        capture["ctor_url"] = url
        capture["ctor_storage_options"] = storage_options
        return dt_instance

    write_mock = _install_fake_deltalake_module(
        monkeypatch, lambda *a, **k: None, delta_table=_dt_ctor
    )

    url = write_delta_table(
        parquet,
        workspace_id=_WS_GUID,
        lakehouse_id=_LH_GUID,
        table_name="dim_control",
        credential=_mock_credential(),
    )

    # Create-path fallback must NOT be exercised when the table exists.
    write_mock.assert_not_called()
    # The MERGE chain was driven end-to-end.
    dt_instance.merge.assert_called_once()
    capture["builder"].when_matched_update_all.assert_called_once()
    capture["builder"].when_not_matched_insert_all.assert_called_once()
    capture["builder"].execute.assert_called_once()
    # URL still has the canonical bare-GUID shape.
    assert url.endswith("/Tables/dim_control")
    # Storage options carried through to the DeltaTable constructor.
    assert capture["ctor_storage_options"]["use_fabric_endpoint"] == "true"
    assert capture["ctor_storage_options"]["bearer_token"] == "fake-bearer-token"


def test_write_delta_table_creates_when_table_does_not_exist(tmp_path, monkeypatch):
    """When DeltaTable raises TableNotFoundError, we bootstrap via
    write_deltalake(mode=append, schema_mode=merge)."""
    parquet = tmp_path / "dim_control.parquet"
    _write_real_parquet(parquet)

    captured: dict = {}

    def fake_write(url, data, **kwargs):
        captured["url"] = url
        captured["kwargs"] = kwargs

    # Default DeltaTable in the helper raises _FakeTableNotFoundError,
    # which _is_table_not_found matches by class name → create path.
    write_mock = _install_fake_deltalake_module(monkeypatch, fake_write)

    write_delta_table(
        parquet,
        workspace_id=_WS_GUID,
        lakehouse_id=_LH_GUID,
        table_name="dim_control",
        credential=_mock_credential(),
    )

    write_mock.assert_called_once()
    assert captured["kwargs"]["mode"] == "append"
    assert captured["kwargs"]["schema_mode"] == "merge"


def test_write_delta_table_merge_predicate_uses_only_primary_keys(
    tmp_path, monkeypatch
):
    """The join predicate must reference PK columns ONLY — never as_of."""
    parquet = tmp_path / "controls.parquet"
    _write_parquet_with_columns(
        parquet,
        {"id": [1], "name": ["x"], "as_of": ["2026-07-21"]},
    )

    capture: dict = {}
    dt_instance = _build_merge_recording_dt(
        capture,
        target_schema=pa.schema(
            {"id": pa.int64(), "name": pa.string(), "as_of": pa.string()}
        ),
    )
    _install_fake_deltalake_module(
        monkeypatch,
        lambda *a, **k: None,
        delta_table=lambda url, storage_options=None: dt_instance,
    )

    write_delta_table(
        parquet,
        workspace_id=_WS_GUID,
        lakehouse_id=_LH_GUID,
        table_name="controls",
        credential=_mock_credential(),
    )

    # controls PK = ("id",) → predicate is exactly the id join, no as_of.
    # Identifiers are double-quoted so PascalCase / spaces / reserved
    # words survive the SQL parse (see schema-tolerance in module docstring).
    assert capture["predicate"] == 'target."id" = source."id"'
    assert "as_of" not in capture["predicate"]


def test_write_delta_table_update_predicate_excludes_as_of_and_pk(
    tmp_path, monkeypatch
):
    """The update predicate must compare only non-PK, non-``as_of`` cols
    with null-safe ``IS DISTINCT FROM``."""
    parquet = tmp_path / "dim_control.parquet"
    _write_parquet_with_columns(
        parquet,
        {
            "id": [1, 2],
            "name": ["a", "b"],
            "description": ["d1", "d2"],
            "as_of": ["2026-07-21", "2026-07-21"],
        },
    )

    capture: dict = {}
    dt_instance = _build_merge_recording_dt(
        capture,
        target_schema=pa.schema(
            {
                "id": pa.int64(),
                "name": pa.string(),
                "description": pa.string(),
                "as_of": pa.string(),
            }
        ),
    )
    _install_fake_deltalake_module(
        monkeypatch,
        lambda *a, **k: None,
        delta_table=lambda url, storage_options=None: dt_instance,
    )

    write_delta_table(
        parquet,
        workspace_id=_WS_GUID,
        lakehouse_id=_LH_GUID,
        table_name="dim_control",
        credential=_mock_credential(),
    )

    update_kwargs = capture["builder"].when_matched_update_all.call_args.kwargs
    update_predicate = update_kwargs["predicate"]

    # Both non-PK, non-as_of columns must appear on both sides, with
    # double-quoted identifiers (schema-tolerance invariant).
    assert 'target."name" IS DISTINCT FROM source."name"' in update_predicate
    assert (
        'target."description" IS DISTINCT FROM source."description"'
        in update_predicate
    )
    # ORed together, not ANDed — any single column differing triggers update.
    assert " OR " in update_predicate
    # PK column and as_of must NOT appear in the update predicate at all
    # (in ANY case — check both quoted and unquoted spellings).
    assert 'target."id"' not in update_predicate
    assert 'source."id"' not in update_predicate
    assert "target.id" not in update_predicate
    assert "source.id" not in update_predicate
    assert "as_of" not in update_predicate


def test_write_delta_table_composite_pk_bridge_gap_entity(tmp_path, monkeypatch):
    """bridge_gap_entity: all 3 columns are in the composite PK, so
    compare_columns is empty → we skip when_matched_update_all entirely
    and only wire up when_not_matched_insert_all."""
    parquet = tmp_path / "bridge_gap_entity.parquet"
    _write_parquet_with_columns(
        parquet,
        {
            "gap_id": ["g1", "g2"],
            "entity_type": ["control", "control"],
            "entity_id": ["c1", "c2"],
        },
    )

    capture: dict = {}
    dt_instance = _build_merge_recording_dt(
        capture, target_schema=["gap_id", "entity_type", "entity_id"]
    )
    _install_fake_deltalake_module(
        monkeypatch,
        lambda *a, **k: None,
        delta_table=lambda url, storage_options=None: dt_instance,
    )

    write_delta_table(
        parquet,
        workspace_id=_WS_GUID,
        lakehouse_id=_LH_GUID,
        table_name="bridge_gap_entity",
        credential=_mock_credential(),
    )

    predicate = capture["predicate"]
    # Composite 3-column ANDed predicate, double-quoted identifiers.
    assert 'target."gap_id" = source."gap_id"' in predicate
    assert 'target."entity_type" = source."entity_type"' in predicate
    assert 'target."entity_id" = source."entity_id"' in predicate
    assert predicate.count(" AND ") == 2

    # All cols are PK → no non-PK, non-as_of cols → update must be SKIPPED.
    capture["builder"].when_matched_update_all.assert_not_called()
    # But new rows still land.
    capture["builder"].when_not_matched_insert_all.assert_called_once()
    capture["builder"].execute.assert_called_once()


def test_write_delta_table_composite_pk_relationships(tmp_path, monkeypatch):
    """relationships: 3-column composite PK + one non-PK column
    (``weight``) → both branches of the MERGE fire."""
    parquet = tmp_path / "relationships.parquet"
    _write_parquet_with_columns(
        parquet,
        {
            "source_id": ["s1"],
            "target_id": ["t1"],
            "rel_type": ["depends_on"],
            "weight": [0.5],
        },
    )

    capture: dict = {}
    dt_instance = _build_merge_recording_dt(
        capture,
        target_schema=pa.schema(
            {
                "source_id": pa.string(),
                "target_id": pa.string(),
                "rel_type": pa.string(),
                "weight": pa.float64(),
            }
        ),
    )
    _install_fake_deltalake_module(
        monkeypatch,
        lambda *a, **k: None,
        delta_table=lambda url, storage_options=None: dt_instance,
    )

    write_delta_table(
        parquet,
        workspace_id=_WS_GUID,
        lakehouse_id=_LH_GUID,
        table_name="relationships",
        credential=_mock_credential(),
    )

    predicate = capture["predicate"]
    for pk in ("source_id", "target_id", "rel_type"):
        assert f'target."{pk}" = source."{pk}"' in predicate
    assert predicate.count(" AND ") == 2

    # weight is a non-PK, non-as_of column → update branch IS wired.
    update_kwargs = capture["builder"].when_matched_update_all.call_args.kwargs
    assert (
        'target."weight" IS DISTINCT FROM source."weight"'
        in update_kwargs["predicate"]
    )


# ---------------------------------------------------------------------------
# Schema-tolerance tests (2026-07-21 follow-up).
#
# The Fabric ``01_load_lakehouse`` notebook creates Delta tables with
# PascalCase columns (``ID``, ``Name``, ``As_Of``, ``Value_Chain``, ...)
# per Spark convention, while our local Parquet writer emits lowercase.
# DataFusion (used by delta-rs for MERGE predicates) rejects
# ``target.id = source.id`` when the target column is literally ``ID``.
# ``write_delta_table`` translates at the MERGE seam via
# ``_align_arrow_to_target_schema``; the internal
# ``_TABLE_PRIMARY_KEYS`` contract stays lowercase.
# ---------------------------------------------------------------------------


def test_write_delta_table_merges_when_target_has_pascalcase_columns(
    tmp_path, monkeypatch
):
    """Fabric notebook created ``controls`` with PascalCase columns
    (``ID``, ``Name``, ``As_Of``). Our lowercase Parquet must still
    MERGE cleanly — predicate identifiers use the TARGET's case,
    double-quoted, and the source Arrow table handed to
    ``.merge()`` has been renamed to match."""
    parquet = tmp_path / "controls.parquet"
    _write_parquet_with_columns(
        parquet,
        {"id": [1, 2], "name": ["a", "b"], "as_of": ["2026-07-21", "2026-07-21"]},
    )

    capture: dict = {}
    dt_instance = _build_merge_recording_dt(
        capture,
        target_schema=pa.schema(
            {"ID": pa.int64(), "Name": pa.string(), "As_Of": pa.string()}
        ),
    )
    _install_fake_deltalake_module(
        monkeypatch,
        lambda *a, **k: None,
        delta_table=lambda url, storage_options=None: dt_instance,
    )

    write_delta_table(
        parquet,
        workspace_id=_WS_GUID,
        lakehouse_id=_LH_GUID,
        table_name="controls",
        credential=_mock_credential(),
    )

    # Predicate uses target case (PascalCase) with double-quoted identifiers.
    assert capture["predicate"] == 'target."ID" = source."ID"'
    # Update predicate compares Name (not name / NAME) — ``As_Of`` is
    # excluded (case-insensitive ``as_of`` match) and ``ID`` is the PK.
    update_kwargs = capture["builder"].when_matched_update_all.call_args.kwargs
    update_predicate = update_kwargs["predicate"]
    assert 'target."Name" IS DISTINCT FROM source."Name"' in update_predicate
    assert "As_Of" not in update_predicate
    assert "as_of" not in update_predicate
    assert '"ID"' not in update_predicate

    # The Arrow table actually handed to delta-rs has target-case names.
    aligned = capture["source"]
    assert list(aligned.column_names) == ["ID", "Name", "As_Of"]


def test_write_delta_table_merges_when_target_has_mixed_case_columns(
    tmp_path, monkeypatch
):
    """Each column renamed independently — the alignment is per-column,
    not all-or-nothing. Target here mixes lowercase ``id``, TitleCase
    ``Name``, and SHOUT ``AS_OF``."""
    parquet = tmp_path / "controls.parquet"
    _write_parquet_with_columns(
        parquet,
        {"id": [7], "name": ["mixed"], "as_of": ["2026-07-21"]},
    )

    capture: dict = {}
    dt_instance = _build_merge_recording_dt(
        capture,
        target_schema=pa.schema(
            {"id": pa.int64(), "Name": pa.string(), "AS_OF": pa.string()}
        ),
    )
    _install_fake_deltalake_module(
        monkeypatch,
        lambda *a, **k: None,
        delta_table=lambda url, storage_options=None: dt_instance,
    )

    write_delta_table(
        parquet,
        workspace_id=_WS_GUID,
        lakehouse_id=_LH_GUID,
        table_name="controls",
        credential=_mock_credential(),
    )

    # PK ``id`` stays lowercase (matches target); update compares ``Name``;
    # ``AS_OF`` is skipped (case-insensitive as_of).
    assert capture["predicate"] == 'target."id" = source."id"'
    aligned = capture["source"]
    assert list(aligned.column_names) == ["id", "Name", "AS_OF"]

    update_kwargs = capture["builder"].when_matched_update_all.call_args.kwargs
    update_predicate = update_kwargs["predicate"]
    assert 'target."Name" IS DISTINCT FROM source."Name"' in update_predicate
    assert "AS_OF" not in update_predicate


def test_write_delta_table_raises_when_source_column_missing_from_target(
    tmp_path, monkeypatch
):
    """Source Parquet emits a column the target does not have. That's
    structural drift (not just case) — we refuse rather than silently
    drop the column."""
    parquet = tmp_path / "controls.parquet"
    _write_parquet_with_columns(
        parquet,
        {"id": [1], "name": ["x"], "ghost": ["nope"]},
    )

    capture: dict = {}
    dt_instance = _build_merge_recording_dt(
        capture, target_schema=["ID", "Name"]  # no "ghost"
    )
    _install_fake_deltalake_module(
        monkeypatch,
        lambda *a, **k: None,
        delta_table=lambda url, storage_options=None: dt_instance,
    )

    with pytest.raises(LakehouseWriteError) as excinfo:
        write_delta_table(
            parquet,
            workspace_id=_WS_GUID,
            lakehouse_id=_LH_GUID,
            table_name="controls",
            credential=_mock_credential(),
        )

    msg = str(excinfo.value)
    assert "controls" in msg
    assert "'ghost'" in msg
    assert "schema drift" in msg
    # Merge was refused BEFORE any SQL predicate was built.
    assert "predicate" not in capture


def test_write_delta_table_raises_when_target_pk_missing_from_source(
    tmp_path, monkeypatch
):
    """Target exists but has no column matching our lowercase PK
    contract — target table is fundamentally incompatible. Refuse."""
    parquet = tmp_path / "controls.parquet"
    # Source has NO id column; target also lacks id. All source cols
    # find a target match (name, as_of) — so the source-col check
    # passes and we hit the PK check next.
    _write_parquet_with_columns(
        parquet,
        {"name": ["x"], "as_of": ["2026-07-21"]},
    )

    capture: dict = {}
    dt_instance = _build_merge_recording_dt(
        capture, target_schema=["Name", "As_Of"]  # no id-like column
    )
    _install_fake_deltalake_module(
        monkeypatch,
        lambda *a, **k: None,
        delta_table=lambda url, storage_options=None: dt_instance,
    )

    with pytest.raises(LakehouseWriteError) as excinfo:
        write_delta_table(
            parquet,
            workspace_id=_WS_GUID,
            lakehouse_id=_LH_GUID,
            table_name="controls",
            credential=_mock_credential(),
        )

    msg = str(excinfo.value)
    assert "controls" in msg
    assert "primary-key" in msg
    assert "'id'" in msg  # the missing PK
    assert "predicate" not in capture


def test_write_delta_table_does_not_mutate_input_arrow_table(
    tmp_path, monkeypatch
):
    """``_align_arrow_to_target_schema`` uses ``rename_columns`` which
    returns a NEW pyarrow.Table. Verify the arrow table read from
    Parquet is not mutated in place even when the target uses a
    completely different case."""
    parquet = tmp_path / "controls.parquet"
    _write_parquet_with_columns(
        parquet,
        {"id": [1], "name": ["x"], "as_of": ["2026-07-21"]},
    )

    # Intercept what ``_read_parquet_table`` returns so we can hold a
    # reference to the pre-alignment Arrow table and inspect it after.
    import pyarrow.parquet as pq

    real_read = pq.read_table
    captured_source: dict = {}

    def _intercepting_read(*args, **kwargs):
        table = real_read(*args, **kwargs)
        captured_source["table"] = table
        captured_source["names_before"] = list(table.column_names)
        return table

    monkeypatch.setattr(
        "regimpact.lakehouse._read_parquet_table",
        lambda p: _intercepting_read(p),
    )

    capture: dict = {}
    dt_instance = _build_merge_recording_dt(
        capture,
        target_schema=pa.schema(
            {"ID": pa.int64(), "Name": pa.string(), "As_Of": pa.string()}
        ),
    )
    _install_fake_deltalake_module(
        monkeypatch,
        lambda *a, **k: None,
        delta_table=lambda url, storage_options=None: dt_instance,
    )

    write_delta_table(
        parquet,
        workspace_id=_WS_GUID,
        lakehouse_id=_LH_GUID,
        table_name="controls",
        credential=_mock_credential(),
    )

    # Original Arrow table is unchanged; the renamed COPY reached delta-rs.
    assert captured_source["names_before"] == ["id", "name", "as_of"]
    assert list(captured_source["table"].column_names) == ["id", "name", "as_of"]
    assert list(capture["source"].column_names) == ["ID", "Name", "As_Of"]
    # And the two arrow tables are distinct instances (rename_columns
    # never returns self).
    assert capture["source"] is not captured_source["table"]


def test_write_delta_table_first_write_path_unchanged(tmp_path, monkeypatch):
    """When the target doesn't exist yet, TableNotFoundError fires and
    we fall through to ``write_deltalake`` — schema alignment MUST be
    skipped (no target to read from). Lowercase source columns land in
    the freshly created table exactly as-is."""
    parquet = tmp_path / "controls.parquet"
    _write_parquet_with_columns(
        parquet,
        {"id": [1], "name": ["first"], "as_of": ["2026-07-21"]},
    )

    write_calls: list[dict] = []

    def _fake_write(*args, **kwargs):
        # write_deltalake is called positionally in production:
        #     write_deltalake(url, data, storage_options=..., mode="append")
        # Capture whichever form arrives.
        record: dict = {"args": args, "kwargs": kwargs}
        # The Arrow table is either the 2nd positional or "data" kwarg.
        if len(args) >= 2:
            record["data"] = args[1]
        elif "data" in kwargs:
            record["data"] = kwargs["data"]
        write_calls.append(record)

    # Default fake DeltaTable raises TableNotFoundError → first-write path.
    _install_fake_deltalake_module(monkeypatch, _fake_write)

    write_delta_table(
        parquet,
        workspace_id=_WS_GUID,
        lakehouse_id=_LH_GUID,
        table_name="controls",
        credential=_mock_credential(),
    )

    assert len(write_calls) == 1
    data = write_calls[0]["data"]
    # Column case is preserved verbatim from the Parquet — no alignment
    # happened because there was no target schema to align against.
    assert list(data.column_names) == ["id", "name", "as_of"]



    """A Parquet file whose stem is not in _TABLE_PRIMARY_KEYS must
    raise LakehouseWriteError with the table name in the message — NEVER
    silently default to ("id",)."""
    parquet = tmp_path / "mystery_table.parquet"
    _write_real_parquet(parquet)
    _install_fake_deltalake_module(monkeypatch, lambda *a, **k: None)

    with pytest.raises(LakehouseWriteError) as excinfo:
        write_delta_table(
            parquet,
            workspace_id=_WS_GUID,
            lakehouse_id=_LH_GUID,
            table_name="mystery_table",
            credential=_mock_credential(),
        )

    msg = str(excinfo.value)
    assert "mystery_table" in msg
    assert "_TABLE_PRIMARY_KEYS" in msg


# ---------------------------------------------------------------------------
# Type-coercion tests (2026-07-21 follow-up #2 on the MERGE landing).
# These exercise ``_cast_arrow_to_target_types``: source Arrow types get
# cast to the target Delta table's types before the MERGE fires. Silent
# coercions include LargeUtf8 ↔ Utf8 and safe integer widen/narrow; loud
# refusals include string↔numeric mismatch and overflow-on-narrow.
# ---------------------------------------------------------------------------


def test_write_delta_table_casts_large_utf8_source_to_utf8_target(
    tmp_path, monkeypatch
):
    """LargeUtf8 source → Utf8 target: source column type must be
    ``pa.string()`` in the Arrow table handed to ``.merge()``, not
    ``pa.large_string()``. This is the concrete failure that motivated
    the type-coercion seam — ``LargeUtf8 OR Boolean`` broke DataFusion's
    ``IS DISTINCT FROM`` reduction on ``business_processes``."""
    parquet = tmp_path / "controls.parquet"
    # Write Arrow directly (not via the {col: [...]} shortcut) so we can
    # pin the column type to large_string.
    import pyarrow.parquet as pq

    source_table = pa.table(
        {
            "id": pa.array([1, 2], type=pa.int64()),
            "name": pa.array(["a", "b"], type=pa.large_string()),
        }
    )
    pq.write_table(source_table, parquet)

    capture: dict = {}
    dt_instance = _build_merge_recording_dt(
        capture,
        target_schema=pa.schema({"id": pa.int64(), "name": pa.string()}),
    )
    _install_fake_deltalake_module(
        monkeypatch,
        lambda *a, **k: None,
        delta_table=lambda url, storage_options=None: dt_instance,
    )

    write_delta_table(
        parquet,
        workspace_id=_WS_GUID,
        lakehouse_id=_LH_GUID,
        table_name="controls",
        credential=_mock_credential(),
    )

    merged = capture["source"]
    assert merged.schema.field("name").type == pa.string()
    assert merged.schema.field("name").type != pa.large_string()


def test_write_delta_table_casts_utf8_source_to_large_utf8_target(
    tmp_path, monkeypatch
):
    """Reverse direction: Utf8 source → LargeUtf8 target. Same seam,
    same policy — silent, transparent cast."""
    parquet = tmp_path / "controls.parquet"
    import pyarrow.parquet as pq

    source_table = pa.table(
        {
            "id": pa.array([1, 2], type=pa.int64()),
            "name": pa.array(["a", "b"], type=pa.string()),
        }
    )
    pq.write_table(source_table, parquet)

    capture: dict = {}
    dt_instance = _build_merge_recording_dt(
        capture,
        target_schema=pa.schema({"id": pa.int64(), "name": pa.large_string()}),
    )
    _install_fake_deltalake_module(
        monkeypatch,
        lambda *a, **k: None,
        delta_table=lambda url, storage_options=None: dt_instance,
    )

    write_delta_table(
        parquet,
        workspace_id=_WS_GUID,
        lakehouse_id=_LH_GUID,
        table_name="controls",
        credential=_mock_credential(),
    )

    merged = capture["source"]
    assert merged.schema.field("name").type == pa.large_string()
    assert merged.schema.field("name").type != pa.string()


def test_write_delta_table_casts_int64_to_int32_when_target_is_int32(
    tmp_path, monkeypatch
):
    """Safe narrowing: int64 source with in-range values → int32 target
    is silently coerced by pyarrow.compute.cast(safe=True)."""
    parquet = tmp_path / "controls.parquet"
    import pyarrow.parquet as pq

    source_table = pa.table(
        {
            "id": pa.array([1, 2, 3], type=pa.int64()),
            "name": pa.array(["a", "b", "c"], type=pa.string()),
        }
    )
    pq.write_table(source_table, parquet)

    capture: dict = {}
    dt_instance = _build_merge_recording_dt(
        capture,
        target_schema=pa.schema({"id": pa.int32(), "name": pa.string()}),
    )
    _install_fake_deltalake_module(
        monkeypatch,
        lambda *a, **k: None,
        delta_table=lambda url, storage_options=None: dt_instance,
    )

    write_delta_table(
        parquet,
        workspace_id=_WS_GUID,
        lakehouse_id=_LH_GUID,
        table_name="controls",
        credential=_mock_credential(),
    )

    merged = capture["source"]
    assert merged.schema.field("id").type == pa.int32()


def test_write_delta_table_casts_int32_to_int64_when_target_is_int64(
    tmp_path, monkeypatch
):
    """Always-safe widening: int32 source → int64 target."""
    parquet = tmp_path / "controls.parquet"
    import pyarrow.parquet as pq

    source_table = pa.table(
        {
            "id": pa.array([1, 2, 3], type=pa.int32()),
            "name": pa.array(["a", "b", "c"], type=pa.string()),
        }
    )
    pq.write_table(source_table, parquet)

    capture: dict = {}
    dt_instance = _build_merge_recording_dt(
        capture,
        target_schema=pa.schema({"id": pa.int64(), "name": pa.string()}),
    )
    _install_fake_deltalake_module(
        monkeypatch,
        lambda *a, **k: None,
        delta_table=lambda url, storage_options=None: dt_instance,
    )

    write_delta_table(
        parquet,
        workspace_id=_WS_GUID,
        lakehouse_id=_LH_GUID,
        table_name="controls",
        credential=_mock_credential(),
    )

    merged = capture["source"]
    assert merged.schema.field("id").type == pa.int64()


def test_write_delta_table_raises_on_string_to_int_coercion(
    tmp_path, monkeypatch
):
    """String source → integer target is a real schema mismatch, never a
    case we silently coerce. LakehouseWriteError must name the column
    and both types so operators can pinpoint the drift."""
    parquet = tmp_path / "controls.parquet"
    import pyarrow.parquet as pq

    # ``name`` is the offending non-PK column: source string, target int64.
    # Using a non-PK column keeps the alignment step happy (PK ``id`` has
    # matching int64 type on both sides).
    source_table = pa.table(
        {
            "id": pa.array([1, 2], type=pa.int64()),
            "name": pa.array(["not-an-int", "also-not"], type=pa.string()),
        }
    )
    pq.write_table(source_table, parquet)

    dt_instance = _build_merge_recording_dt(
        {},
        target_schema=pa.schema({"id": pa.int64(), "name": pa.int64()}),
    )
    _install_fake_deltalake_module(
        monkeypatch,
        lambda *a, **k: None,
        delta_table=lambda url, storage_options=None: dt_instance,
    )

    with pytest.raises(LakehouseWriteError) as excinfo:
        write_delta_table(
            parquet,
            workspace_id=_WS_GUID,
            lakehouse_id=_LH_GUID,
            table_name="controls",
            credential=_mock_credential(),
        )

    msg = str(excinfo.value)
    # Column name + both types must all appear so operators can pinpoint
    # the drift without digging into logs.
    assert "name" in msg
    assert "string" in msg.lower()
    assert "int64" in msg.lower()


def test_write_delta_table_raises_on_int_overflow_when_narrowing(
    tmp_path, monkeypatch
):
    """Narrowing int64 → int32 is safe *only* when every value fits.
    A value of 2**40 overflows int32 → pyarrow.compute.cast(safe=True)
    raises ArrowInvalid, which the helper wraps as LakehouseWriteError."""
    parquet = tmp_path / "controls.parquet"
    import pyarrow.parquet as pq

    overflow_value = 2 ** 40  # 1_099_511_627_776 — way past int32 max (~2.1e9)
    source_table = pa.table(
        {
            "id": pa.array([1, 2], type=pa.int64()),
            "count": pa.array([overflow_value, 1], type=pa.int64()),
        }
    )
    pq.write_table(source_table, parquet)

    dt_instance = _build_merge_recording_dt(
        {},
        target_schema=pa.schema({"id": pa.int64(), "count": pa.int32()}),
    )
    _install_fake_deltalake_module(
        monkeypatch,
        lambda *a, **k: None,
        delta_table=lambda url, storage_options=None: dt_instance,
    )

    with pytest.raises(LakehouseWriteError) as excinfo:
        write_delta_table(
            parquet,
            workspace_id=_WS_GUID,
            lakehouse_id=_LH_GUID,
            table_name="controls",
            credential=_mock_credential(),
        )

    msg = str(excinfo.value)
    assert "count" in msg
    # The wrapped ArrowInvalid message references the overflow / cast.
    # We don't over-pin the pyarrow phrasing but do require both types
    # to be named (so drift is diagnosable from the error alone).
    assert "int64" in msg.lower()
    assert "int32" in msg.lower()


def test_write_delta_table_first_write_path_no_type_coercion(
    tmp_path, monkeypatch
):
    """First-write path (TableNotFoundError branch): no target schema
    exists to align against, so type coercion MUST be skipped and
    source types land in ``write_deltalake`` verbatim. Complementary to
    ``test_write_delta_table_first_write_path_unchanged`` which pins
    column *names*; this one pins *types*."""
    parquet = tmp_path / "controls.parquet"
    import pyarrow.parquet as pq

    # LargeUtf8 source — if type coercion incorrectly ran on the
    # first-write path, this would be silently cast to Utf8 (or fail).
    # It must reach write_deltalake as LargeUtf8.
    source_table = pa.table(
        {
            "id": pa.array([1], type=pa.int64()),
            "name": pa.array(["first"], type=pa.large_string()),
        }
    )
    pq.write_table(source_table, parquet)

    write_calls: list[dict] = []

    def _fake_write(*args, **kwargs):
        record: dict = {"args": args, "kwargs": kwargs}
        if len(args) >= 2:
            record["data"] = args[1]
        elif "data" in kwargs:
            record["data"] = kwargs["data"]
        write_calls.append(record)

    # Default fake DeltaTable raises TableNotFoundError → first-write.
    _install_fake_deltalake_module(monkeypatch, _fake_write)

    write_delta_table(
        parquet,
        workspace_id=_WS_GUID,
        lakehouse_id=_LH_GUID,
        table_name="controls",
        credential=_mock_credential(),
    )

    assert len(write_calls) == 1
    data = write_calls[0]["data"]
    # Types are untouched — the caller's Parquet types define the fresh
    # table's schema via write_deltalake(schema_mode="merge").
    assert data.schema.field("name").type == pa.large_string()
    assert data.schema.field("id").type == pa.int64()


def test_write_delta_table_type_alignment_does_not_mutate_input_arrow(
    tmp_path, monkeypatch
):
    """``_cast_arrow_to_target_types`` uses ``pyarrow.Table.set_column``
    which returns a NEW Table each call. Verify the Arrow table read
    from Parquet is not mutated even when every column is cast."""
    parquet = tmp_path / "controls.parquet"
    import pyarrow.parquet as pq

    source_table = pa.table(
        {
            "id": pa.array([1, 2], type=pa.int64()),
            "name": pa.array(["a", "b"], type=pa.large_string()),
        }
    )
    pq.write_table(source_table, parquet)

    # Intercept the Arrow table returned by ``_read_parquet_table`` so
    # we can hold a reference to it and inspect after the write returns.
    real_read = pq.read_table
    captured_source: dict = {}

    def _intercepting_read(*args, **kwargs):
        table = real_read(*args, **kwargs)
        captured_source["table"] = table
        captured_source["types_before"] = {
            f.name: f.type for f in table.schema
        }
        return table

    monkeypatch.setattr(
        "regimpact.lakehouse._read_parquet_table",
        lambda p: _intercepting_read(p),
    )

    capture: dict = {}
    dt_instance = _build_merge_recording_dt(
        capture,
        # Target types differ from source on BOTH columns → cast helper
        # rebuilds every column, so if any mutation crept in we'd see it.
        target_schema=pa.schema({"id": pa.int32(), "name": pa.string()}),
    )
    _install_fake_deltalake_module(
        monkeypatch,
        lambda *a, **k: None,
        delta_table=lambda url, storage_options=None: dt_instance,
    )

    write_delta_table(
        parquet,
        workspace_id=_WS_GUID,
        lakehouse_id=_LH_GUID,
        table_name="controls",
        credential=_mock_credential(),
    )

    # Original Arrow table: types unchanged from what Parquet returned.
    assert captured_source["types_before"] == {
        "id": pa.int64(),
        "name": pa.large_string(),
    }
    original = captured_source["table"]
    assert original.schema.field("id").type == pa.int64()
    assert original.schema.field("name").type == pa.large_string()

    # The Arrow table that reached .merge() has the target's types.
    merged = capture["source"]
    assert merged.schema.field("id").type == pa.int32()
    assert merged.schema.field("name").type == pa.string()

    # And they are distinct instances (set_column never returns self).
    assert merged is not original


def test_write_delta_table_composes_case_and_type_alignment(
    tmp_path, monkeypatch
):
    """Case-alignment and type-coercion compose in that order: source
    ``id`` (large_string) + ``as_of`` (large_string) is renamed to
    target case ``ID`` / ``As_Of`` and then cast to target types
    (string). The Arrow table handed to ``.merge()`` must have BOTH
    the target's names AND the target's types."""
    parquet = tmp_path / "controls.parquet"
    import pyarrow.parquet as pq

    # Source: lowercase names, large_string types on both columns.
    # Note: PK for controls is "id" — using string PK here is a
    # deliberate mismatch scenario (target chose string PKs), covered
    # by the case + type alignment seams.
    source_table = pa.table(
        {
            "id": pa.array(["a1", "a2"], type=pa.large_string()),
            "as_of": pa.array(
                ["2026-07-21", "2026-07-21"], type=pa.large_string()
            ),
        }
    )
    pq.write_table(source_table, parquet)

    capture: dict = {}
    # Target: PascalCase names, plain string() types.
    dt_instance = _build_merge_recording_dt(
        capture,
        target_schema=pa.schema(
            {"ID": pa.string(), "As_Of": pa.string()}
        ),
    )
    _install_fake_deltalake_module(
        monkeypatch,
        lambda *a, **k: None,
        delta_table=lambda url, storage_options=None: dt_instance,
    )

    write_delta_table(
        parquet,
        workspace_id=_WS_GUID,
        lakehouse_id=_LH_GUID,
        table_name="controls",
        credential=_mock_credential(),
    )

    merged = capture["source"]
    # Names: PascalCase (case alignment ran).
    assert list(merged.column_names) == ["ID", "As_Of"]
    # Types: plain string (type coercion ran after case alignment).
    assert merged.schema.field("ID").type == pa.string()
    assert merged.schema.field("As_Of").type == pa.string()
    # And large_string is definitively gone from both columns.
    assert merged.schema.field("ID").type != pa.large_string()
    assert merged.schema.field("As_Of").type != pa.large_string()
