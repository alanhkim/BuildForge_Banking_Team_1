# Lambert History

## Core Context
- Project: forge, a Python regulatory impact framework for assessing regulatory change impact and scoring compliance using synthetic digital twin data.
- User: briandenicola.
- Current focus: maintain Microsoft Agent Framework / Foundry Hosted Agent readiness while implementing deterministic offline interpreter core only.

## Learnings

### 2026-07-17 — OneLake writeback for `interpret`
- Built `src/regimpact/lakehouse.py` — uploads every `*.parquet` from `settings.tables_dir` into a Fabric lakehouse under `Files/tables/`. Wired into `interpret` only (not `demo`, `analyze`, `score`, `audit`) — that's phase 1 scope.
- **ABFSS URL pattern** for OneLake-hosted lakehouses:
  `abfss://{workspace_id}@onelake.dfs.fabric.microsoft.com/{lakehouse_id}.Lakehouse/Files/{subpath}/{filename}`
  The ADLS account is always `https://onelake.dfs.fabric.microsoft.com`; the *filesystem* is the workspace ID, and the top-level directory is `{lakehouse_id}.Lakehouse`. Files land under `Files/…`, Delta tables land under `Tables/…`.
- **Lazy-import pattern for optional cloud SDKs:** `from azure.storage.filedatalake import DataLakeServiceClient` lives INSIDE `export_to_lakehouse()`, not at module top. Same for `DefaultAzureCredential`. This lets `from regimpact.lakehouse import ...` succeed even without the `fabric` extra installed, and lets tests plant a fake `azure.storage.filedatalake` in `sys.modules` before the import runs. Added a `fabric` optional extra to `pyproject.toml` (kept separate from `foundry`).
- **Why upload is best-effort, not fatal:** local Parquet in `output/tables/` is the source of truth. The `interpret` command wraps the OneLake call in a try/except that catches `LakehouseNotConfiguredError` (skip silently with a yellow hint) and `LakehouseWriteError` (red warning, but DO NOT re-raise). Rationale: a Fabric outage, expired token, or capacity-off state should never break a local pipeline run — the user still has their Parquet on disk and can re-upload manually.
- **Constitution alignment:** this is I/O plumbing, not agent behavior, so the "no deterministic offline fallback for agent behavior" rule doesn't apply — best-effort writeback for a data-export path is fine.
- **Testing:** all 5 new tests in `tests/test_lakehouse.py` pass; `tests/test_export_audit.py` unchanged (4/4 pass). Pre-existing failures in `test_cli.py::test_ask_fabric_cli_surfaces_missing_configuration` and `test_interpret_cli_surfaces_missing_foundry_configuration` are environmental (real `.env` credentials override `Settings()` defaults) and predate this change.
