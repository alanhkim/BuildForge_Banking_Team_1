# Squad Decisions

## Active Decisions

### 2026-07-21 (evening): OneLake writeback uploads CSV alongside Parquet

**Author:** Bishop (Python Core Dev)
**Requested by:** briandenicola
**Status:** Implemented; 23/23 tests green in `tests/test_lakehouse.py`
**Relates to:** §0 (OneLake Writeback Scope & Failure Semantics); 2026-07-21 (afternoon) `.Lakehouse` suffix entry (immediately below — closes the FriendlyNameSupportDisabled trilogy this decision sits on top of)
**Nature:** additive extension of a working boundary — NOT another root-cause fix in the trilogy.

**What:**
Extended `export_to_lakehouse()` and `export_regimpact_lakehouse()` in `src/regimpact/lakehouse.py` to upload both `*.parquet` **and** `*.csv` files from `tables_dir` into the target Fabric lakehouse `Files/<subpath>/` location.

- Glob widened from `*.parquet` to sorted `*.parquet + *.csv`.
- `export_regimpact_lakehouse` presence check widened to `.parquet OR .csv` so a CSV-only directory triggers the upload path instead of being silently skipped as empty.
- CLI (`cli.py`) console message wording: "N gold Parquet file(s)" → "N gold file(s)".
- Same target subpaths (`regimpact_raw`, `regimpact_gold`), same auth, same failure semantics, same ABFSS URL shape — just now `.ext` may be `.parquet` or `.csv`.

**Why (parity):**
`export_tables()` and `export_gold()` already write both formats to disk. Users expect parity between local `output/tables/` and what shows up in the Fabric lakehouse:
- Parquet for the PySpark notebook (`01_load_lakehouse.ipynb`), which reads by exact `{name}.parquet` filename.
- CSV for direct download, Excel eyeballing, non-Spark ingestion, and audit review.

Before this change, users saw a CSV on their laptop, uploaded to Fabric, and could not find that CSV in the lakehouse. That's a broken mental model. This fix removes the surprise.

**Why (restriction — glob stays narrow, NOT `*`):**
`output/tables/` is a working directory. Over time it may accumulate editor swap files (`.swp`, `~$foo.csv`), stray log/debug dumps, manifests, JSON/Markdown scratch, or half-written files if a run was killed mid-export. Pushing all of those to a shared Fabric lakehouse would pollute the workspace, risk name collisions with legitimate assets, and give downstream Spark shortcuts unexpected content to reason about. The two-extension allowlist is a **safety property** — it matches exactly what the local exporters produce, so anything else in the directory is by definition not part of the intended dataset.

Regression guard test `test_export_to_lakehouse_ignores_other_extensions` stages `.parquet + .csv + .json + .txt + .md` and asserts exactly 2 uploads. Anyone widening this in the future will trip that test and land here.

**Position vs FriendlyNameSupportDisabled trilogy:**
This is an EXTENSION on top of the trilogy, not a fourth root-cause fix. The trilogy closed ~10 minutes earlier with the user's "YES THAT WORKED!" confirmation on the `.Lakehouse` suffix drop. No diagnostic funnel work here — the boundary is working end-to-end; this widens what it uploads.

**Backward compatibility:**
Public API signatures unchanged — `export_to_lakehouse()` and `export_regimpact_lakehouse()` still return `list[str]` of ABFSS URLs (the list is just longer now). Existing callers (only in-repo caller is `cli.py::interpret`, updated in the same change) work unchanged.

**Tests:**
- `tests/test_lakehouse.py` — **23/23 green** (17 pre-existing + 4 new CSV-specific + 2 pre-existing parametrized cases).
- Renamed `test_export_ignores_non_parquet_files` → `test_export_ignores_non_parquet_non_csv_files`; CSV reclassified from "expected-ignored" to "expected-uploaded" in its fixture.
- New: `test_export_to_lakehouse_uploads_csv_alongside_parquet` — both formats uploaded in the same call.
- New: `test_export_to_lakehouse_uploads_csv_when_no_parquet_present` — CSV upload not gated on Parquet.
- New: `test_export_to_lakehouse_ignores_other_extensions` — regression guard against future "just glob everything" refactors.
- New: `test_export_to_lakehouse_returns_urls_for_both_formats` — ABFSS URL list contains both file types.

**Files touched:**
- `src/regimpact/lakehouse.py` — glob widened, loop variable renamed `parquet_files` → `upload_files`, docstrings + module-level comment updated to say "Parquet and CSV", presence check in `export_regimpact_lakehouse` widened.
- `src/regimpact/cli.py` — 1-line console message wording.
- `tests/test_lakehouse.py` — 4 new tests + 1 renamed test.

**Downstream impact:**
- Next `interpret` run uploads ~34 files per subpath (17 Parquet + 17 CSV) instead of 17. Object count doubles; storage cost negligible; wall-clock stays sub-second at test scales.
- Notebook unaffected — reads by exact `{name}.parquet` filename; CSVs sitting alongside are inert to the Spark loader.

---

### 2026-07-21 (afternoon): Drop `.Lakehouse` suffix from OneLake ADLS paths

**Author:** Bishop (Python Core Dev)
**Requested by:** briandenicola
**Status:** Implemented; end-to-end verified by user ("YES THAT WORKED!")
**Relates to:** §0 (OneLake Writeback Scope & Failure Semantics); 2026-07-20 GUID validation entry; 2026-07-21 regional endpoint entry (immediately below)
**Completes:** the FriendlyNameSupportDisabled trilogy (GUID validation → regional endpoint → bare GUID path)

**What:**
Removed the `.Lakehouse` suffix from OneLake ADLS Gen2 paths built in `src/regimpact/lakehouse.py::export_to_lakehouse`. The canonical Fabric ADLS Gen2 path prefix — as returned by the Fabric REST API — is the **bare lakehouse GUID**:

- Before: `{workspace_id}/{lakehouse_id}.Lakehouse/Files/{subpath}/…`
- After:  `{workspace_id}/{lakehouse_id}/Files/{subpath}/…`

Both the ADLS SDK call (`get_directory_client(...)`) and the returned ABFSS URLs use the same bare-GUID form. `cli.py` display string that advertises the upload location updated to match.

**Why:**
The `.Lakehouse` suffix is a Fabric **UI / Spark-shortcut** convention — correct for mounting lakehouses in Spark notebooks and for creating shortcuts in the Fabric portal. Direct ADLS Gen2 access via `azure.storage.filedatalake.DataLakeServiceClient` uses the **name-plane**, which rejects `<guid>.Lakehouse` as unresolvable with the opaque `FriendlyNameSupportDisabled` error. Verified against Fabric REST — the source of truth for what the path actually is:

```
GET /v1/workspaces/{workspace_id}/lakehouses/{lakehouse_id}
→ properties.oneLakeFilesPath  = "https://onelake.dfs.fabric.microsoft.com/{workspace_id}/{lakehouse_id}/Files"
→ properties.oneLakeTablesPath = "https://onelake.dfs.fabric.microsoft.com/{workspace_id}/{lakehouse_id}/Tables"
```

No `.Lakehouse` in Fabric's own answer. Blogs and even parts of Microsoft's own docs showing `.Lakehouse` in ADLS URLs are stale or context-mismatched (Spark mount ≠ ADLS Gen2 name-plane).

**Failure-mode context — the FriendlyNameSupportDisabled trilogy:**
`FriendlyNameSupportDisabled` is Fabric's generic "identifier could not be resolved" error. This week the same error string covered three distinct root causes for the same operator, on the same workspace, in ~24 hours:

1. **2026-07-20** — Malformed GUID (trailing `\n` from `export FOO="…"`). Fixed via `_normalize_fabric_id` (soft-skip class per §0).
2. **2026-07-21 (am)** — Wrong endpoint region. Fixed via `_resolve_onelake_endpoint` + `FABRIC_ONELAKE_DFS_ENDPOINT` env override (soft-skip class per §0).
3. **2026-07-21 (pm)** — Malformed path prefix (`.Lakehouse` suffix). Fixed by this decision.

Because the SDK short-circuited at the earliest failure each time, each fix only made the next layer visible. The canonical diagnostic funnel for future occurrences (documented in Bishop history + `.squad/skills/fabric-resource-id-validation/SKILL.md`) is:

1. Validate GUID at the boundary (strip → `uuid.UUID()` → canonical-form check).
2. Validate / override endpoint via `GET /v1/workspaces/{ws}` → `oneLakeEndpoints.dfsEndpoint`.
3. Verify path prefix against `GET /v1/workspaces/{ws}/lakehouses/{lh}` → `properties.oneLakeFilesPath`.

**Reverses / supersedes:**
- Nothing at decision level. Neither the previous OneLake decisions (2026-07-20 GUID validation, 2026-07-21 regional endpoint) nor decisions.md §0 spoke to the path prefix — it was baked into the `lakehouse.py` docstring as folklore only. This decision supersedes that docstring statement.
- Both prior OneLake fixes (GUID normalization, regional endpoint) remain in force and untouched.

**Backward compatibility:**
Any external caller relying on the returned ABFSS URLs will see the URL format change (no more `.Lakehouse`). This is intentional and correct — the previous form was structurally invalid, so nobody could have been using it successfully; every upload was failing before returning anything. Public function signatures unchanged.

**Tests:**
- Updated 6 expected values in `tests/test_lakehouse.py` that asserted the target directory string or the returned ABFSS URLs.
- Added `test_export_to_lakehouse_uses_bare_guid_directory_no_lakehouse_suffix` — asserts `get_directory_client("{lh_guid}/Files/tables")` and that the passed string contains no `.Lakehouse` anywhere.
- Added `test_export_returned_abfss_urls_never_contain_lakehouse_suffix` — asserts no returned ABFSS URL contains `.Lakehouse`.
- **19/19 tests green** in `tests/test_lakehouse.py`.
- End-to-end verified: `python -m regimpact interpret ...` uploads Parquet successfully to `Files/regimpact_raw/` and `Files/regimpact_gold/` in the `wsRegChgImpactdev1` lakehouse.

**Files touched:**
- `src/regimpact/lakehouse.py` — module docstring, `lakehouse_id:` param docstring, `target_dir` construction, `abfss_url` construction + inline comment.
- `src/regimpact/cli.py` — one display-string kwarg to keep the console message in sync with the SDK path.
- `tests/test_lakehouse.py` — 6 expected-value updates + 2 new regression tests.
- `.squad/skills/fabric-resource-id-validation/SKILL.md` — new section documenting the REST-API cross-check pattern.

**Not touched:**
- `_normalize_fabric_id` (GUID validation stands as-is).
- `_resolve_onelake_endpoint` (regional endpoint work stands as-is).
- `pipeline.py`, `foundry_client.py`, `agents/` (out of scope).
- `src/regimpact/01_load_lakehouse.ipynb` — uses lakehouse-relative `Files/regimpact_raw` paths that never spelled out `.Lakehouse`.

**Constitutional check:** Compliant. Pure I/O plumbing on the OneLake writeback path — no agent behavior added, removed, or altered. Rule #3 (no deterministic/offline fallback for agent behavior) is unaffected.

**Reversal:** Restore the `.Lakehouse` suffix in both `target_dir` and `abfss_url` construction in `export_to_lakehouse`, revert the `cli.py` display string, revert the 6 test expected-value updates, and remove the 2 regression guards. Not recommended — the current form matches Fabric's REST-advertised canonical path.

---

### 2026-07-21 (morning): OneLake DFS endpoint override (regional capacities)

**Author:** Bishop (Python Core Dev)
**Requested by:** briandenicola
**Status:** Implemented on current branch
**Relates to:** §0 (OneLake Writeback Scope & Failure Semantics); 2026-07-20 OneLake ID validation entry
**Part of:** the FriendlyNameSupportDisabled trilogy (fix #2 of 3 — see 2026-07-21 afternoon entry above for the completing fix)

**What:**
Added `FABRIC_ONELAKE_DFS_ENDPOINT` env var + optional `onelake_endpoint: str | None` parameter on `export_to_lakehouse` and `export_regimpact_lakehouse` in `src/regimpact/lakehouse.py`. Defaults to the global `https://onelake.dfs.fabric.microsoft.com` (backward-compatible — anyone already relying on the module default sees no change). Malformed endpoint values raise `LakehouseNotConfiguredError` (soft yellow skip per §0), not `LakehouseWriteError`. **ABFSS URLs returned by these functions are unchanged** — they still use the canonical (non-regional) `onelake.dfs.fabric.microsoft.com` host.

Concretely:

- `src/regimpact/settings.py`: one new field, `fabric_onelake_dfs_endpoint: str = os.getenv("FABRIC_ONELAKE_DFS_ENDPOINT") or ""`. Pure env passthrough; no default baked in.
- `src/regimpact/lakehouse.py`:
  - Kept `_ONELAKE_ACCOUNT_URL = "https://onelake.dfs.fabric.microsoft.com"` as the module-level fallback.
  - Added `_ONELAKE_ENDPOINT_MARKER = "onelake.dfs.fabric.microsoft.com"` and `_resolve_onelake_endpoint(raw)` — strip → validate (`https://` prefix + marker substring, case-insensitive) → return; empty/None → return default.
  - Added `onelake_endpoint: str | None = None` to both public functions. Threaded through to `DataLakeServiceClient(account_url, ...)`.
- `src/regimpact/cli.py`: one-line change — `onelake_endpoint=settings.fabric_onelake_dfs_endpoint` on the existing `export_regimpact_lakehouse` call.
- `tests/test_lakehouse.py`: +5 tests (one parametrized across `None` / `""` / `"   "`).

Public signatures are additive — the new parameter has a `None` default. `Settings` is not mutated.

**Why:**
Workspace `bf949f4b-ca7a-4095-9596-1a3c8e4959e5` advertises a **regional** OneLake endpoint via `GET /v1/workspaces/{id}` → `oneLakeEndpoints.dfsEndpoint = https://northcentralus-onelake.dfs.fabric.microsoft.com`. The hardcoded global endpoint hit that capacity's routing layer, which returned the exact same opaque `FriendlyNameSupportDisabled` error that yesterday's malformed-GUID case returned — so the fix looked complete but the next user run was going to hit the second root cause. Two distinct causes, one server-side error string.

Explicit config over auto-retry:

- **No "try global, fall back to regional" magic.** One deterministic round-trip per run. Silent fallback would hide capacity misconfiguration and add latency on regional capacities (the common case going forward — Fabric is rolling out regional endpoints broadly).
- **No auto-detect from the Fabric metadata API.** Would add a synchronous Fabric REST hop per `interpret` run, need a `api.fabric.microsoft.com` token (different scope than storage), and have an unclear failure mode if the metadata call itself fails.
- **ABFSS URLs stay on the canonical host.** Downstream tools (Spark shortcuts, `01_load_lakehouse.ipynb`, Purview lineage) parse those URLs and expect the non-regional form. Regional prefix is a data-plane routing detail applied transparently by OneLake — leaking it into the returned identifier would fragment lineage by region and break shortcut resolution. Test-guarded.

Failure-class semantics per §0 are preserved: malformed endpoint = soft yellow skip (same rationale as malformed GUID).

**Consequences:**
- `interpret` runs against regional Fabric capacities no longer crash with the second `FriendlyNameSupportDisabled` root cause. Operator adds one env var to `.env` and it works.
- Callers already using the global endpoint see zero behavior change (backward-compatible defaults).
- New failure mode: setting `FABRIC_ONELAKE_DFS_ENDPOINT` to garbage now yields a clear soft-skip message naming the env var and the offending value.
- No new dependencies. `_resolve_onelake_endpoint` is pure stdlib string handling.
- The `_resolve_onelake_endpoint` shape is the same shape as `_normalize_fabric_id`. Reusable for future Fabric/OneLake config surfaces (e.g., blob endpoint, capacity ID).

**Reversal:** Delete `_resolve_onelake_endpoint`, remove `_ONELAKE_ENDPOINT_MARKER`, remove the `onelake_endpoint` parameter from both public functions, pass `_ONELAKE_ACCOUNT_URL` directly to `DataLakeServiceClient(...)`, remove the field from `Settings`, remove the kwarg from `cli.py`. No callers depend on the new parameter (default is `None`).

**Constitutional check:** Compliant. Pure I/O plumbing on the OneLake writeback path — no agent behavior added, removed, or altered. Rule #3 unaffected.

**Operator handoff:** Add to `.env`:
```
FABRIC_ONELAKE_DFS_ENDPOINT=https://northcentralus-onelake.dfs.fabric.microsoft.com
```
Then re-run `python -m regimpact interpret ...`.

**Tests:** `python -m pytest tests/test_lakehouse.py -q` → **17 passed** (10 existing + 7 new). (Note: subsequent 2026-07-21 afternoon fix brought the count to 19/19.)

---

### 2026-07-21: Drop the FabricMaterializer + Livy layer — Python only uploads Parquet
**By:** briandenicola (via Copilot)
**What:** Remove `FabricMaterializerAgent`, `fabric_materializer_spec`, and `fabric_livy_client` from the pipeline. The `interpret` CLI keeps `export_regimpact_lakehouse` (Parquet → OneLake `Files/regimpact_raw/`, `Files/regimpact_gold/`) and stops there. Delta table materialization + `v_impact` / `v_compliance` / `v_capability_health` view creation stay inside Fabric, driven by `01_load_lakehouse.ipynb` (Fabric-provided `spark` session). Reverses the earlier decision that put a Livy-based materializer in the Python pipeline.
**Why:** The Livy + Foundry-supervisor stack was too much surface area for the value delivered. The notebook already does the Delta conversion natively inside Fabric — Python re-implementing the same steps through Livy added moving parts (async batch polling, PySpark statement templating, Foundry evidence wrapping) without changing the final Fabric state. Simpler contract: Python owns Parquet-on-disk + Parquet-in-Files/, Fabric owns Delta + views. Copilot Instructions rule #3 (no deterministic/offline fallback for agent behavior) is unaffected — the notebook is the agent's data-prep step, not a fallback.

**Files removed:** `src/regimpact/agents/fabric_materializer.py`, `src/regimpact/agents/fabric_materializer_spec.py`, `src/regimpact/agents/fabric_livy_client.py`, `tests/test_fabric_materializer.py`, `tests/test_fabric_livy_client.py`.
**Files edited:** `src/regimpact/agents/__init__.py` (removed `FabricMaterializerAgent` / `FabricMaterializerError` exports); `src/regimpact/cli.py` (removed materializer import + `if upload_succeeded:` materialize block + `upload_succeeded` flag; corrected notebook path comment).
**Files untouched (per spec):** `src/regimpact/lakehouse.py`, `src/regimpact/01_load_lakehouse.ipynb`, `src/regimpact/agents/pipeline.py`, remaining Foundry/Fabric agent stack.

**Tests:** `tests/test_lakehouse.py` 10/10 green. Full suite: 103 passed / 23 pre-existing baseline failures (v3/v4 env-drift + asyncio config gap + one interpreter contract-error drift — zero failures touch removed code). Grep-verified zero remaining `FabricMaterializer` / `FabricLivyClient` references in `src/`, `tests/`, `docs/`, `scripts/`, `infra/`.

**New pipeline shape:** `interpret → control_mapper → gap_analyst → remediation_planner → OneLake upload → done`. Fabric-side Delta + views happen inside the notebook, not inside the Python process.

**Supersedes:** 2026-07-17 "FabricMaterializerAgent — Option A (deterministic Livy) chosen" (marked SUPERSEDED below; historical record preserved).

**Reversal:** Restore the deleted files from git history (`6cc6ffd` and follow-ups), re-add exports in `agents/__init__.py`, and re-wire the `if upload_succeeded:` block in `cli.py`. Reversal is straightforward — no schema or contract changes shipped with the removal.

**Constitutional check:** Compliant. No agent behavior added, removed, or synthesized. The notebook is Fabric-native data prep, not a client-side fallback for a Foundry agent. Rule #3 unaffected.

---

### 2026-07-20: OneLake ID validation at the `lakehouse.py` boundary

**Author:** Bishop (Python Core Dev)
**Requested by:** briandenicola
**Status:** Implemented on `hamza-dev`
**Relates to:** §0 (OneLake Writeback Scope & Failure Semantics)

**What:**
`src/regimpact/lakehouse.py::export_to_lakehouse` now validates `workspace_id` and `lakehouse_id` at entry via a new `_normalize_fabric_id(raw, env_var)` helper:

1. **Strip** whitespace → strip one layer of surrounding `'` / `"` → strip whitespace again. Local to the function; never mutates the caller's `Settings`.
2. **Validate** via `uuid.UUID(cleaned)` in a `try/except`, then confirm `str(parsed) == cleaned.lower()` to reject braced (`{guid}`), urn (`urn:uuid:guid`), and no-dash (32-hex) variants.
3. **Thread the cleaned value** into the ADLS SDK calls AND the returned ABFSS URLs, so downstream materialization sees clean IDs.
4. On any validation failure — empty after stripping, non-GUID, or non-canonical form — raise `LakehouseNotConfiguredError` (soft yellow skip) with a message naming the env var and the offending value.

Public function signatures unchanged. `Settings` unchanged. `fabric_livy_client.py` untouched.

**Trigger:**
`interpret` failed with `OneLake upload failed: (FriendlyNameSupportDisabled) Request Failed with WorkspaceId and ArtifactId should be either valid Guids or valid Names`. `FriendlyNameSupportDisabled` is a Fabric server-side error that fires when the ADLS filesystem name (workspace_id) or the top-level directory prefix (lakehouse_id) is neither a valid GUID nor a valid friendly name. Root causes are almost always copy/paste artefacts on the env var: trailing newline from `export FABRIC_WORKSPACE_ID="…"`, surrounding quote characters copied verbatim, whitespace, partial paste, or a workspace display name while friendly-name resolution is disabled on the tenant.

**Why `LakehouseNotConfiguredError`, not `LakehouseWriteError`:**
Per §0, `LakehouseNotConfiguredError` = soft skip (yellow, CLI continues, local Parquet remains source of truth); `LakehouseWriteError` = hard failure (red, non-fatal in `interpret` but marks upload as failed). A malformed env var is not a transient issue — it fails EVERY run until the operator fixes it. That is definitionally "not configured", the same category as an empty value. Treating it as `LakehouseWriteError` would (a) print red every run and drown the signal, and (b) blow past the semantic contract §0 established for that error class (transient / retryable / operational). The error message distinguishes the malformed case from the empty case, so operators still know exactly what to fix.

**Consequences:**
- `interpret` runs no longer crash with `FriendlyNameSupportDisabled` at upload time when the env var is malformed — they skip cleanly with a message like `FABRIC_WORKSPACE_ID must be a Fabric workspace/lakehouse GUID (got 'my-workspace-name'). Copy it from the Fabric portal → Workspace settings → About.`
- Shell-quoted values (`FABRIC_LAKEHOUSE_ID="'guid'"`) and trailing-newline values now work transparently — common CLI ergonomic win.
- No new dependencies (`uuid` is stdlib).
- Not applied to `fabric_livy_client.py`. If Livy also consumes `FABRIC_WORKSPACE_ID` raw, that's a separate hardening pass owned by Lambert.

**Reusable pattern:** captured as `.squad/skills/fabric-resource-id-validation/SKILL.md` — strip → `uuid.UUID()` → canonical-form check → map to the "not configured" error class. Applies to any Fabric/OneLake ID env var (workspace, lakehouse, item, capacity).

**Tests:**
`tests/test_lakehouse.py`: 10/10 green. New coverage: trailing-newline workspace_id (strips, SDK receives clean GUID, ABFSS URL uses clean GUID); `"my-workspace-name"` → `LakehouseNotConfiguredError` naming the env var and the offending value; `"   "` (whitespace only) → strips to empty → `LakehouseNotConfiguredError` on the "not set" branch; `"'11111111-1111-1111-1111-111111111111'"` (shell-quoted GUID) → strips quotes, succeeds. Existing tests updated to use canonical GUID fixture constants (`_WS_GUID`, `_LH_GUID`).

**Reversal:**
Delete `_normalize_fabric_id` and the two calls at the top of `export_to_lakehouse`. No callers depend on the validation.

**Constitutional check:** Compliant. Pure I/O plumbing; no agent behavior touched. Per `copilot-instructions.md` rule 3, the constraint applies to agent behavior only, not to data-export paths.

---

### 2026-07-17: Gap Analyst pipeline soft-fail (mirrors remediation_planner pattern)

**Author:** Coordinator (direct edit, Standard Mode)
**Requested by:** Hamza
**Status:** Implemented on `hamza-dev`
**Related:** 2026-07-17T19:53:02Z remediation_planner soft-fail entry (precedent)

**What:**
`gap_analyst` stage in `src/regimpact/agents/pipeline.py` now soft-fails on any `FabricDataAgentError`. The `try/except` was downgraded from `raise FabricPipelineError` to `logger.warning + ga_response = None + continue`. Downstream code guarded with `if ga_response is not None` for persistence; `gap_ids = []` and `persisted_gaps = []` on soft-fail. `_fabric_report(gap_count=...)` uses `len(ga_response.findings) if ga_response is not None else 0`. This matches the `remediation_planner` soft-fail pattern established earlier the same day.

**Trigger:**
User hit `[FOUNDRY DEBUG] transient failure for agent RegImpactGapAnalyst v4 (attempt 1-4/5, InternalServerError)` during `regimpact interpret`. Retry loop exhausted at the transport layer and the previous session's soft-fail only covered `remediation_planner`, so the pipeline still aborted at `gap_analyst`.

**Rationale for preserving prior on-disk gap state:**
On soft-fail we deliberately do NOT call `_persist_gaps([], change_id)`. Persisting an empty finding set would wipe legitimate prior gap data for this `change_id` — including gaps that were correctly identified on the last successful run. Preserving state gives the operator a clean rerun path: transient Foundry error clears, next `interpret` overwrites with fresh authoritative findings. Destructive-on-failure is worse than stale-until-rerun.

**Explicit non-change — score_narrator stays hard-fail:**
An initial draft synthesized a `ScoreNarrationResponse` fallback so the whole pipeline could finish even when the narrator agent was down. It was reverted. Injecting synthesized narrative text — even a bland "score computed successfully" boilerplate — would be a deterministic/offline fallback for **agent behavior**, which `copilot-instructions.md` rule 3 forbids. Score narrative must come from Foundry or the pipeline must fail loudly. Numeric score facts are already locally-computed (`_compute_local_score_facts`) and are not affected; only the narrative wrapper stays gated on Foundry availability.

**Where the resilience boundary now sits:**
- `interpreter` — hard-fail (retry + alias/keyword normalization, but ultimately must produce structured input).
- `control_mapper` — hard-fail (empty mappings without downstream inputs = nothing to plan against).
- `gap_analyst` — **soft-fail** (this decision). Skips remediation, emits `gap_count=0` in the report.
- `remediation_planner` — soft-fail (2026-07-17T19:53:02Z decision).
- `materializer` — hard-fail (Delta writes are the audit artefact; a silent skip would corrupt the compliance narrative).
- `score_narrator` — hard-fail (this decision confirms).

**Constitutional check:**
Compliant. `gap_count=0` when Fabric is unavailable is honest state (we have no data), not fabricated data. Downstream reporting reflects reality. The harness contract remains strict — soft-failing is orchestration-layer only, no client-side response synthesis, no offline behavior for the agent itself.

**Files touched:**
- `src/regimpact/agents/pipeline.py` — `gap_analyst` try/except downgrade, `if ga_response is not None` guards, `_fabric_report` gap_count expression.

**Verification:**
`pytest tests/test_fabric_workflow.py tests/test_impact_scoring.py tests/test_export_audit.py tests/test_lakehouse.py tests/test_fabric_materializer.py tests/test_fabric_livy_client.py -q` → 82 passed / 6 failed. The 6 failures are the pre-existing env-drift baseline (hardcoded `agent_version="3"` vs env's `"4"`/`"5"`) documented at `.squad/decisions.md:152`. Unrelated.

**Reversal:**
To restore hard-fail behavior, replace the `except FabricDataAgentError` warn-and-continue block with `raise FabricPipelineError(...) from exc`, and remove the `if ga_response is not None` guards (persist path becomes unconditional again). No test scaffolding to unwind.

---

### 2026-07-17: Remove semantic retry from Fabric client (fail-fast)

**Author:** Coordinator (direct edit, per user directive)
**Requested by:** Hamza
**Status:** Implemented (`f3d6ab4` on `hamza-dev`)

**What:**
Semantic retry loop removed from `FabricDataAgentClient.ask` in `src/regimpact/agents/foundry_client.py`. Production is now single-attempt: one call to the transport layer, one pass of lenient parsing, immediate raise on failure. `_ask_with_semantic_retry` helper deleted.

**Why:**
User reported `interpret` command too slow. The 3-attempt retry (added in `412d695`) doubled or tripled round-trip latency on any sad-path response. Latency win outweighs the incremental resilience the retry provided, given that lenient parsing already recovers most malformed responses on the first attempt.

**Tradeoff:**
- **Gained:** ~2–3× faster failure path; simpler code; single obvious error surface.
- **Lost:** One bad agent response now aborts the whole `interpret` pipeline. Operator must re-run. Diagnostic hint (`truncated=true/false`) still surfaces from the lenient parser so the failure is legible.

**Preserved:**
- Lenient parsing: metadata defaults, inner-payload recovery, markdown-fence stripping, prose-embedded JSON extraction (from `ccd35d8`).
- Transport-level retry inside `FoundryAgentClient._invoke_with_retry` — network, 429, and 5xx retries are unchanged.
- Constitutional constraint: no offline/deterministic fallback for agent behavior. This decision does not introduce any hardcoded agent responses.

**Reversal:**
Retry loop code was **deleted, not disabled**. To restore:
1. Re-add `_ask_with_semantic_retry` to `FabricDataAgentClient` (see commit `412d695` for prior implementation).
2. Change `ask()` to delegate to it instead of calling the transport directly.
3. Restore the 5 retry-behavior tests in `tests/test_fabric_workflow.py` (see commit `412d695`).

**Constitutional check:** Compliant. No offline path introduced; agent behavior still flows entirely through Microsoft Foundry / Fabric.

**Tests:**
- `tests/test_fabric_workflow.py`: 5 retry tests replaced with 3 fail-fast tests.
- 43/43 pass across fabric_workflow, impact_scoring, export_audit, smoke, lakehouse.

---

### 0. OneLake Writeback Scope & Failure Semantics

**Date:** 2026-07-17
**Author:** Lambert (Integration Engineer)
**Requested by:** hamzamahmood
**Status:** Implemented

**Decision:**
1. **OneLake writeback is opt-in via env vars.** `FABRIC_WORKSPACE_ID` and `FABRIC_LAKEHOUSE_ID` gate the upload. When either is empty, `export_to_lakehouse()` raises `LakehouseNotConfiguredError` and the CLI prints a yellow "skipped" hint — no crash.
2. **Upload failures are non-fatal.** Local Parquet under `output/tables/` remains the source of truth. `LakehouseWriteError` is caught in `interpret` and rendered as a red warning; the command still exits 0 as long as local exports and downstream steps succeed. Rationale: a Fabric capacity outage, expired token, or transient network issue must never break a local pipeline run.
3. **Only `interpret` gets writeback in phase 1.** `demo`, `analyze`, `score`, `audit`, and `generate` are untouched. If a follow-up wants OneLake on `demo`, that is a separate decision — keeping the blast radius small while we validate the ABFSS URL pattern and credential flow against a real Fabric workspace.
4. **Optional extra `[fabric]`** added to `pyproject.toml` — separate from `[foundry]` — so users who only need Fabric writeback do not pull in `agent-framework` / `azure-ai-projects`, and users who only need Foundry do not pull in `azure-storage-file-datalake`. Both share `azure-identity`.
5. **Lazy import** of `azure.storage.filedatalake` and `azure.identity.DefaultAzureCredential` inside `export_to_lakehouse()` — the core CLI installs and imports cleanly without the `fabric` extra.

**Rationale:** Land Parquet in the Fabric lakehouse so agents can query the same data the CLI just produced, without turning OneLake into a hard dependency of the local dev loop.

**Consequences:**
- Set `FABRIC_WORKSPACE_ID` + `FABRIC_LAKEHOUSE_ID` and `pip install .[fabric]` to enable writeback.
- Local runs remain fully functional with neither env vars nor the extra installed.
- Extending writeback to other CLI commands requires a new decision.

---

### 1. Regulation Interpreter Core Implementation

**Date:** 2026-07-06  
**Author:** Bishop (Python Core Dev)  
**Status:** Superseded by Foundry/Fabric-first architecture direction on 2026-07-08

**Architecture:** Previously documented a portable, network-free Regulation Interpreter core with typed contracts, catalog fixture, offline fallback, and schema validation. This is no longer the active direction for agent behavior.

- **Typed Contracts** (`src/regimpact/contracts.py`): InterpretRequest/Response dataclasses with validation methods, explicit exception hierarchy, known themes validation set, maturity range (1-5), source refs enforcement.
- **Catalog Fixture** (`src/regimpact/catalog.py`): DORA fixture data with CatalogFixture class (REG-DORA, CHG-DORA, OBL-DORA-01).
- **Interpreter Implementation** (`src/regimpact/agents/interpreter.py`): Existing fallback behavior is superseded and should be replaced by Foundry/Fabric-first agent execution.
- **Schema Validation**: Required IDs, change_id, theme, summary, criticality, source_refs; theme must be in KNOWN_THEMES; maturity 1-5; criticality Critical/High/Medium/Low.

**Rationale:** Superseded. The active direction is Foundry/Fabric-first agent execution with explicit failures for missing configuration, no API-key authentication, no Semantic Kernel, schema validation, and traceability.

**Consequences:** Existing fallback-first behavior must not guide new work; agent behavior should be implemented through Foundry/Fabric and tested with mocked service boundaries where needed.

---

### 2. Interpreter Validation Enhancement

**Date:** 2026-07-06  
**Author:** Hicks (Tester)  
**Status:** Implemented  

**Decision:** Enhanced input validation in `InterpretRequest` to explicitly reject whitespace-only fields using `.strip()` checks.

**Rationale:** Whitespace-only strings are truthy in Python but semantically empty. Traceability requires meaningful regulation_id, change_id, name, title, and missing or invalid values should fail explicitly.

**Implementation:** Added `.strip()` checks to all required fields in InterpretRequest validation.

**Impact:** 29 tests (expanded from 23), 100% pass; whitespace-only fields now raise ValidationError immediately.

---

### 3. Regulation Interpreter Core Approved (Tasks 1-4)

**Date:** 2026-07-06  
**Reviewer:** Ripley (Lead / Architect)  
**Status:** Superseded by Foundry/Fabric-first architecture direction on 2026-07-08

**Decision:** The previous Regulation Interpreter core approval is superseded for agent behavior because fallback-first implementation masks Foundry/Fabric integration issues.

**Hard Constraints Verified:**
- ✓ No Hosted Agent wrapper (task 4 deferred)
- ✓ No Semantic Kernel
- ✓ No API-key auth/config/docs
- ✗ Offline fallback behavior via CatalogFixture is no longer an approved agent behavior
- ✓ Explicit validation with typed exceptions
- ✓ No broad catches or silent fallbacks

**Test Quality:** Existing tests cover the superseded fallback behavior and should be revised around Foundry boundaries, schema validation, malformed input, and explicit configuration/auth failures.

**Implications:** Downstream agents should depend on validated contracts, but agent behavior should route through Foundry/Fabric rather than local fallback logic.

**Next Steps:** Wire Foundry/Fabric-first execution, remove fallback masking from agent behavior, and use Entra auth only.

---

### 4. Fabric Data Agent Response Hardening

**Date:** 2026-07-17
**Author:** Bishop (Python Core Dev)
**Requested by:** hamzamahmood
**Status:** Implemented

**Decision:** Loosened Fabric Data Agent response validation and added semantic-retry with inner-payload recovery so single-response glitches no longer abort the entire `interpret` pipeline.

**Rules established:**
1. **`answer` is the only truly required envelope field.** Missing `citations` / `tool_evidence` / `confidence` are defaulted to `[]`, `[]`, `"low"` respectively with a WARNING log — no more hard fail on metadata-only omissions.
2. **Semantic retry sits ABOVE transport retry.** `FabricDataAgentClient._ask_with_semantic_retry` retries up to 3 times when JSON is malformed or `answer` is missing, augmenting the prompt with corrective feedback each attempt. Transport-level retry (network / 5xx / active-run races) remains inside `FoundryAgentClient._invoke_with_retry`.
3. **Inner-payload recovery.** When the envelope lacks `answer` but the top-level JSON contains any of `{mappings, findings, actions, narrative, change_id, impacted_entities}` (frozenset `_RECOVERABLE_INNER_KEYS`), the whole payload is treated as the inner answer. Logs a WARNING so operators see the misbehavior.
4. **Constitutional constraint respected.** No offline / deterministic agent-behavior fallback added. We only tolerate parsing variance and retry — never substitute hardcoded findings / mappings / actions.

**Rationale:** Real Fabric agents (esp. `gap_analyst`, `remediation_planner`) intermittently drop metadata fields or return raw inner payloads. Prior strict validation turned every such glitch into a full pipeline abort; users saw `Fabric stage 'gap_analyst' failed for CHG-EUAIACT-UPLOAD: Fabric Data Agent response missing required field(s): answer` and had to re-run manually.

**Files changed:**
- `src/regimpact/agents/foundry_client.py` — lenient `_fabric_response_from_payload`, `_extract_json_block` scanner, `_ask_with_semantic_retry`.
- `src/regimpact/agents/fabric_workflow.py` — `_json_answer` accepts dict / str / prose-embedded JSON; removed stray debug `logger.error`.
- `tests/test_fabric_workflow.py` — 8 new tests, all passing (26/26 total).

**Verification:** 26/26 fabric_workflow tests pass. No regressions in lakehouse / export / impact / audit tests. 17 pre-existing failures in `test_fabric_agents.py` / `test_interpreter.py` verified unchanged via baseline stash comparison — unrelated to this work.

**Consequences:**
- `interpret` runs against real Fabric agents survive single-response glitches automatically.
- New WARNING logs will appear when agents return partial envelopes or raw inner payloads — signal for prompt-quality drift, not silent success.
- Semantic-retry adds up to 3x latency on the affected stage in the worst case; acceptable given the alternative is a full pipeline abort and manual re-run.

---

### 5. Truncation-Aware Retry for Fabric Data Agent Inner Answer JSON

**Date:** 2026-07-17
**Author:** Bishop (Python Core Dev)
**Requested by:** hamzamahmood
**Status:** Implemented — extends §4 (Fabric Data Agent Response Hardening)

**Decision:** Lift inner-answer JSON parseability into the client-side semantic-retry loop, and re-ask the model with a *concise-mode* prompt when the failure is truncation (unclosed brace/bracket at EOF) rather than restructuring.

**Rules established:**
1. **Inner-JSON validity is checked at the client level.** `_validate_inner_answer(response)` runs inside `_ask_with_semantic_retry` in `foundry_client.py`, right after `_fabric_response_from_payload`. Previously the check happened in `fabric_workflow._json_answer` and raised `FabricAgentHarnessError` — a different exception type from a different module — which escaped the retry loop entirely. Lifting the check into the client turns a truncated or malformed inner answer into a retryable semantic failure.
2. **Truncation triggers a concise-mode retry prompt, not the generic envelope reminder.** When the failure reason contains `truncated=true` (from `_validate_inner_answer`) or `likely truncated` (from `_extract_json_block`'s unmatched-brace-at-EOF message), the retry prompt appends a `CRITICAL: TRUNCATED mid-generation` block: keep `rationale` ≤ 200 chars, cap `source_refs` at 1 per item, drop optional prose fields, target ≤ 3500 total chars. The envelope reminder would ask the model to restructure — wrong lever. Concise-mode asks it to shrink.
3. **Prose-answer agents are exempted.** `_validate_inner_answer` gates on `stripped[0] in "{["`, so `executive_qa` and `score_narrator` (which legitimately return prose) pass through untouched.
4. **Constitutional constraint respected — no silent brace-closing, no data fabrication.** `_validate_inner_answer` NEVER attempts to complete a truncated response. Silent completion would fabricate findings / mappings / actions and violate the "no deterministic/offline fallback for agent behavior" rule. The helper only raises so the retry loop can re-ask the model and get a legitimate, complete answer.
5. **`max_output_tokens` is intentionally NOT bumped.** The Foundry gateway rejects `max_output_tokens` at the top level for `agent_reference` invocations (see the existing comment in `_OpenAIResponsesAgent.run`). Prompt discipline is the only real lever over response length for Fabric-grounded agents.

**Rationale:** Fabric Data Agent on `gap_analyst` returned an envelope where `answer` was a JSON string truncated at ~5018 bytes (15 obligations × 14 controls hit the model's output-token ceiling). §4's envelope-hardening covered outer shape only; inner-string parseability was a separate axis that needed a separate fix on the same retry loop.

**Files changed:**
- `src/regimpact/agents/foundry_client.py` — `_looks_truncated` helper, `_validate_inner_answer` gate, wiring into `_ask_with_semantic_retry`, concise-mode retry prompt block when reason contains `truncated=true` / `likely truncated`.
- `tests/test_fabric_workflow.py` — 5 new tests: truncated retry (retries then succeeds), malformed-but-complete retry, dict-answer bypass, prose-wrapped JSON bypass, exhaustion when always truncated.

**Verification:** 45/45 tests pass across `test_fabric_workflow.py`, `test_impact_scoring.py`, `test_export_audit.py`, `test_smoke.py`, `test_lakehouse.py` (excluding pre-existing `defaults_to_deployed` failures that assert hardcoded `agent_version=3` vs env's 4/5 — unrelated).

**Consequences:**
- Structured-JSON Fabric agents (`control_mapper`, `gap_analyst`, `remediation_planner`, `lineage`) survive mid-generation truncation without aborting `interpret`.
- New retry attempts appear in orchestration logs with a `truncated=true` marker — signal for prompts whose expected output structurally exceeds the model's output budget (candidates for pagination / batching at the prompt level).
- Prose-answer agents (`executive_qa`, `score_narrator`) behavior unchanged.

---

### 2026-07-17: ControlMapper contract accepts documented empty mappings

**Status:** ACCEPTED (`1df0f5c` on `hamza-dev`) — flipped from PROPOSED after implementation + test verification
**Author:** Bishop (Python Core Dev)
**Verified by:** Hicks (13 contract/harness/retry/log tests, all green)
**Requested by:** Hamza
**Supersedes:** the "PROPOSED" version of this entry from earlier the same day.

**What shipped:**

1. **`ControlMappingResponse.validate`** accepts `mappings=[]` only when accompanied by a non-empty `reason: str`. Empty-without-reason is still a hard `ValidationError`. `tool_evidence` remains required for BOTH branches (non-empty mappings and empty-with-reason) — losing grounding is worse than losing mappings, and accepting an ungrounded empty response would be a soft form of the offline fallback the Constitution rules out.
2. **`GapAnalysisRequest`** gained a `reason: str | None` field. `validate()` now accepts empty `control_ids` when `reason` is a non-empty string; otherwise still raises `ValidationError("control_ids is required (or provide non-empty reason)")`. `obligation_ids` remains unconditionally required — obligations are pipeline INPUT, not a downstream artefact, so emptiness there is a genuine bug.
3. **Pipeline propagation** (`agents/pipeline.py`) forwards `cm_response.reason` into `GapAnalysisRequest` when mappings is empty, and emits a WARNING `Fabric stage propagating empty control_ids stage=gap_analyst change_id=... reason=...` so the propagation is visible in log tails.
4. **Error unwrap** — `fabric_workflow.map_controls` surfaces the underlying `ValidationError.message` in the harness error (previously buried behind the generic `"Fabric agent response failed validation"` prefix, though that prefix is preserved).
5. **Request/response diagnostics** — inline `candidate_control_ids` per obligation in the request payload; INFO/WARNING evidence logging in `pipeline.py` around the control_mapper → gap_analyst boundary; `tool_evidence_count` surfaced per stage.

**Deliberately NOT shipped:**

- **No retry wrapper on `analyze_gaps` or any other Fabric stage.** The ControlMapper retry that would have been the template was removed in `f3d6ab4` (2026-07-17 fail-fast decision, latency parity). See separate decision below.
- **No response-side change to `GapAnalysisResponse`.** Already accepts empty `findings` as valid — documented in its docstring. Only `tool_evidence` remains required.
- **No further downstream chain fix.** `remediation_planner` already guards with `if gap_ids:` (pipeline.py). `score_narrator` uses locally-precomputed floats (`_compute_local_score_facts`), independent of gap_analyst output. Chain is intact after this one-hop fix.

**Files touched:**
- `src/regimpact/contracts.py`
- `src/regimpact/agents/fabric_workflow.py`
- `src/regimpact/agents/pipeline.py`
- `tests/test_fabric_workflow.py` (+613 lines)

**Verification:** 76 passed / 7 deselected (`pytest -q`). Deselected = 6 pre-existing v3/v4 Foundry drift tests + 1 network-dependent pipeline stage test.

**Constitutional check:** Compliant. Empty-with-reason requires `tool_evidence`, so we never accept ungrounded no-ops. No offline / deterministic path introduced.

---

### 2026-07-17: ControlMapper retry mechanism NOT included

**Status:** Decided
**Author:** Coordinator (from Bishop's implementation deviation notes + user approval of split-commit)
**Requested by:** Hamza

**What:** The `map_controls` retry loop and `_map_controls_attempt` helper that had been added in a prior working-tree state were reverted before the empty-with-reason contract shipped. Production `map_controls` is single-attempt.

**Why:**
1. **Latency parity.** The 2026-07-17 semantic-retry-removal decision (`f3d6ab4`) established single-attempt as the norm for Fabric agents specifically to fix the user-reported slow `interpret` path. Adding retry back to just one agent creates the exact asymmetry that decision moved us away from — one agent retries, four don't. Users would see wildly inconsistent stage latencies.
2. **The empty-with-reason contract handles the documented no-op case.** ControlMapper legitimately returning `mappings=[]` + `reason="Shortlist exhausted for CHG-…"` is a first-class outcome now, not a failure. The pipeline continues (via `GapAnalysisRequest.reason` propagation), producing a documented no-op report. Nothing to retry.
3. **Upstream Foundry prompt fix is the correct remediation for empty-without-reason drift.** Per Lambert's 2026-07-17 investigation, the observed `tool_evidence_count=1` + empty mappings failure mode is a portal-side agent version drift issue — the deployed `RegImpactControlMapper` v4 collapsing a 15-obligation batch into a single stub evidence entry. Client-side retry would paper over that; the fix is the version-pin recommendation (`FOUNDRY_CONTROL_MAPPER_AGENT_VERSION`) plus the prompt directive requiring one `tool_evidence` entry per obligation batch. Both landed in Lambert's Deliverable A prompt update.

**Reversal:** If retry is ever needed, do it at the harness level for ALL Fabric agents at once (avoids the asymmetry problem) and treat it as an override of the fail-fast decision — requiring a fresh decision with fresh latency data.

**Constitutional check:** Compliant. No behavior change to what the agent produces — only removes a resilience layer whose cost outweighed its benefit given the new contract.

---

### 2026-07-17: FabricMaterializerAgent — Option A (deterministic Livy) chosen

**SUPERSEDED 2026-07-21 by "Drop the FabricMaterializer + Livy layer" — see above.**

**Status:** Implemented (`6cc6ffd` on `hamza-dev`)
**Author:** Coordinator (option selected + committed with user approval)
**Requested by:** Hamza

**What:** After OneLake upload lands Parquet in `Files/regimpact/...`, `FabricMaterializerAgent` runs a Foundry-wrapped Livy session that executes a fixed sequence of PySpark statements to promote each Parquet file into a versioned Delta table in `Tables/regimpact/...`. The transformation logic is code (in `fabric_materializer.py` / `fabric_livy_client.py`) — not authored by the LLM. Foundry sits at the boundary as the evidence-producing supervisor: it plans (selects the target table set), invokes the Livy client, and witnesses success/failure. No PySpark is generated by a model at runtime.

**Options considered:**

- **Option A (chosen): deterministic Livy, Foundry as boundary supervisor.** Transformation is code-versioned; Foundry produces `tool_evidence` for each materialized table; single agent per pipeline run.
- **Option B: LLM chooses/generates PySpark.** Rejected. Delta materialization is pure ETL with a known schema — there is no reasoning task for a model here. Handing it PySpark generation introduces prompt-drift risk on a code path with no operator observability (Livy statements would appear in evidence but not in the repo). Fails the same "grounding matters" test that gates ControlMapper empty-with-reason.
- **Option C: SDK-only, no agent involved.** Rejected. Would break the pipeline shape — every other stage is a Foundry agent producing `tool_evidence`. A silent Livy call would leave no audit surface for the materialization step, which is exactly the boundary the compliance narrative needs to cover ("how did the gold Parquet become a Delta table Fabric agents can query?"). Requires a separate parallel evidence path.

**Why Option A:**
1. Transformation is pure ETL and versioned in code — behavior is reviewable in `fabric_materializer.py` and `fabric_livy_client.py`, not in a prompt.
2. Foundry sits at the boundary producing evidence, so the audit story stays uniform across all six pipeline stages (`interpret → control_mapper → gap_analyst → remediation_planner → materializer → ...`).
3. Keeps the 6-agent pipeline coherent — no special-case "this stage isn't an agent" branch.
4. Matches the sequencing already proven in `01_load_lakehouse.ipynb`. The notebook is now the reference implementation the agent replays.

**Files shipped:**
- **New:** `src/regimpact/agents/fabric_livy_client.py` (313 lines) — Livy session lifecycle + statement execution.
- **New:** `src/regimpact/agents/fabric_materializer.py` (150 lines) — Foundry agent that plans and witnesses Delta writes.
- **New:** `src/regimpact/agents/fabric_materializer_spec.py` (211 lines) — spec + prompt for the Materializer.
- **New:** `tests/test_fabric_livy_client.py` (225 lines), `tests/test_fabric_materializer.py` (163 lines).
- `src/regimpact/agents/__init__.py`, `src/regimpact/cli.py`, `src/regimpact/lakehouse.py`, `src/regimpact/settings.py` — wiring + env vars.

**Constitutional check:** Compliant. LLM does no code generation at runtime. Foundry is a real path (no offline fallback for the evidence-producing boundary). Delta output is versioned artefact ingestable by Fabric Data Agents downstream.

---

### 2026-07-17: Bishop's ControlMapper hardening — implementation deviations (superseded)

**Status:** Superseded — the retry-split described here was reverted; see "ControlMapper retry mechanism NOT included" above.
**Author:** Bishop (Python Core Dev)
**Requested by:** Hamza

**Preserved for historical reference:** Bishop originally implemented Fixes 1/2/3/6, which included splitting `map_controls` into `_map_controls_attempt` (parse) + `map_controls` (validate + retry-then-validate). The parse/validate split existed specifically to let the retry loop inspect empty-shape responses before the contract check fired. When the retry loop was reverted for latency parity, the split was collapsed back into a single-attempt `map_controls`. The empty-with-reason contract shape and the `tool_evidence` requirement for both branches survived and shipped in `1df0f5c`.

**Key surviving invariant from Bishop's plan:** empty-with-reason still requires `tool_evidence`. An agent returning `{"mappings": [], "reason": "shortlist exhausted"}` with no evidence fails validation. This is what preserves the constitutional posture.

---

### 2026-07-17: Lambert — evidence-starvation is a Foundry prompt/version drift, not a harness bug

**Status:** ACCEPTED — fix is prompt-side (in Foundry portal, not this repo); repo-side deliverable (paste-ready spec + version-pin recommendation) landed in `docs/foundry-agent-prompts/control-mapper.md` and `docs/foundry-fabric-agents.md`.
**Author:** Lambert (Integration Engineer)
**Requested by:** Hamza

**Finding:** The observed `tool_evidence_count=1` for 15 obligations followed by `mappings: []` without a `reason` is caused by the deployed `RegImpactControlMapper` v4 collapsing an entire batch review into a single stub `tool_evidence` entry. There is no harness-side trim, no `LIMIT 1`, no `_gather_evidence` helper that could account for this. The `candidate_controls` shortlist + per-obligation `candidate_control_ids` reach the agent intact.

**Fix (prompt-side, in Foundry portal):** Directive added to `RegImpactControlMapper` — "Record one `tool_evidence` entry per obligation batch you evaluated (do not collapse a 15-obligation review into a single stub entry). The `query` field should describe the actual inspection you performed — including the case where you reasoned off the inline `candidate_controls` shortlist supplied in the request payload."

**Recommendation (repeated here as a durable decision):** Pin `FOUNDRY_CONTROL_MAPPER_AGENT_VERSION` (and the equivalent for every other Foundry agent) so silent portal drift becomes an explicit env change.

**Rejected code-side fixes:**
- Harness synthesizing a `ToolEvidence(tool_name="inline_shortlist", ...)` entry when the agent returns 1. Rejected — violates "no offline fallback" by fabricating provenance the agent didn't produce.
- Lowering the contract to accept 0 or 1 evidence entries. Rejected — weakens the audit narrative.
- Duplicating the prompt fix in `CONTROL_MAPPER_SPEC.instructions`. Rejected — the portal is the single source of truth for prompt content; duplication invites drift.

**Trigger for a code follow-up:** if after the portal fix operators still see `tool_evidence_count=1` combined with **non-empty** mappings, that's a different bug (agent producing valid mappings but under-documenting). At that point revisit whether the non-empty evidence requirement is achievable without harness-side augmentation.

---

### 2026-07-17: GapAnalyst empty-tolerance — close the cross-stage break

**Status:** ACCEPTED (`1df0f5c` on `hamza-dev`)
**Author:** Bishop (Python Core Dev)
**Verified by:** Hicks (1 rewritten pipeline test + 4 new `GapAnalysisRequest` contract tests; 62 passed / 6 deselected)
**Requested by:** Hamza
**Closes:** the "Known downstream caveat" flagged as out-of-scope in Bishop's ControlMapper hardening implementation notes.

**What shipped:**

1. **`GapAnalysisRequest`** gained `reason: str | None = None`. `validate()` now accepts empty `control_ids` iff `reason.strip()` is non-empty; otherwise raises `ValidationError("control_ids is required (or provide non-empty reason)")`. **`obligation_ids` remains unconditionally required** — obligations are pipeline INPUT (from the Regulation Interpreter), not a downstream artefact. An empty obligation set is a genuine bug, never a legitimate agent outcome, even with a valid `reason` + populated `control_ids`.
2. **Pipeline propagation** (`agents/pipeline.py`): the gap_analyst stage forwards `cm_response.reason` into `GapAnalysisRequest(reason=...)` when `cm_response.mappings` is empty. On the happy path (non-empty mappings), `reason` stays `None` — zero behaviour change.
3. **New WARNING log** right before the gap_analyst call: `Fabric stage propagating empty control_ids stage=gap_analyst change_id=... reason=...`. Complements the existing ControlMapper-stage WARNING so an operator can trace the reason forward through the pipeline in a single log tail.

**Contract shape consistency:** `reason: str | None` is now the standard shape for "empty output/input is legitimate" across the pipeline. `ControlMappingResponse.reason` (output) → `GapAnalysisRequest.reason` (input). Any future agent needing the pattern should mirror the same field name and the same `reason.strip()` validation shape.

**Deliberately NOT shipped:**

- **No response-side change to `GapAnalysisResponse`.** Already accepts empty `findings` as valid per its docstring ("every obligation→control pair meets its target maturity"). Only `tool_evidence` remains required.
- **No retry wrapper on `analyze_gaps`.** ControlMapper retry template was reverted in `f3d6ab4` for latency parity; adding it back for one agent alone would recreate the exact asymmetry that decision moved us away from.
- **No further downstream chain fix.** Audited to one hop: `remediation_planner` already guards with `if gap_ids:` at `pipeline.py:522` (empty findings skip the stage). `score_narrator` uses locally-precomputed floats from `_compute_local_score_facts`, independent of gap_analyst output. Chain intact.

**Files touched:**
- `src/regimpact/contracts.py`
- `src/regimpact/agents/pipeline.py`
- `tests/test_fabric_workflow.py` (1 rewrite + 4 new contract tests, parametrised 3× = 6 net invocations)

**Verification:** 62 passed / 6 deselected (`pytest tests/test_fabric_workflow.py tests/test_lakehouse.py tests/test_export_audit.py tests/test_impact_scoring.py tests/test_smoke.py -q -k "not defaults_to_deployed"`). Zero regressions. Deselected = pre-existing `defaults_to_deployed` agent_version drift (3→4/5), unrelated.

**Constitutional check:** Compliant. Empty-with-reason path still requires `tool_evidence` transitively via the ControlMapper contract that feeds it. No offline / deterministic fallback added. All behaviour flows through real Foundry/Fabric.

---

### 2026-07-17: Remediation Planner empty-with-reason + theme normalization hardening

**Status:** Implemented
**Author:** Coordinator (direct, Standard Mode — scoped hardening applying existing conventions)
**Requested by:** hamzamahmood
**Extends:** §4 (Fabric Data Agent Response Hardening) and the 2026-07-17 ControlMapper empty-with-reason entry.

**Trigger:** Two production failures observed in a single `python -m regimpact interpret --file data/regulations/eu_ai_act_high_risk.txt` run:
1. Foundry Interpreter returned theme `"RISK_MANAGEMENT"` (not in `KNOWN_THEMES`) — pipeline aborted after retry exhausted.
2. Fabric Remediation Planner (`RegImpactRemediationPlanner` v4) returned `{"actions": []}` — `RemediationResponse.validate()` raised `actions is required`, aborting the pipeline at the CHG-EUAIACT-UPLOAD stage.

**Decision:**

1. **Remediation empty-with-reason contract extension (mirrors ControlMapper §2026-07-17).** `RemediationResponse` gained `reason: str | None = None`. `validate()` accepts empty `actions` iff `reason.strip()` is non-empty. **`tool_evidence` remains required in both branches** (parity with `ControlMappingResponse`) — an ungrounded empty response would be a soft form of the offline fallback the Constitution rules out. `REMEDIATION_PLANNER_SPEC.output_contract` gained `"reason": string?`; instructions now direct the model to emit `{"actions": [], "reason": "..."}` when it legitimately has nothing to plan (e.g., every gap already has an active remediation). `plan_remediation()` extracts and forwards `reason`. INFO log emits `actions=N reason_present=bool` per stage call.

2. **Theme alias table + keyword rule expansion.** `_THEME_ALIASES` in `foundry_interpreter.py` grew by 25 entries covering the observable failure surface: Risk Management → MODEL_RISK, Cybersecurity → CYBER, Transparency / Explainability / Human Oversight → AI_GOVERNANCE, Third-Party / Vendor → THIRD_PARTY_RISK, Business Continuity → ICT_RESILIENCE, plus regulatory-vernacular variants. `_THEME_KEYWORD_RULES` grew by 18 entries, notably `("MODEL_RISK", ("risk","management"))`. The retry prompt now embeds the full `KNOWN_THEMES` enum with explicit anti-patterns so the model cannot invent an out-of-band label a second time.

3. **Pipeline-level soft-fail for `remediation_planner`.** The stage's `try/except FabricDataAgentError` in `agents/pipeline.py` downgraded from `raise FabricPipelineError` to `logger.warning + rp_response=None + continue`. Empty-with-reason gets an INFO log with the reason; hard failure gets WARNING. Either way, `_persist_remediations([])` clears stale rows so downstream reports reflect the current state.

**What did NOT change (contract stays strict at the harness):**

- `RemediationResponse.validate()` still rejects empty `actions` without `reason` — same shape as `ControlMappingResponse`.
- `tool_evidence` still required in every branch. No ungrounded no-ops accepted.
- No retry loop added anywhere (latency parity per §f3d6ab4).
- No offline / deterministic fallback for agent behavior. The pipeline degrades gracefully; the agent contract does not.

**Constitutional check:** Compliant. Empty-with-reason still requires `tool_evidence`. Pipeline resilience is orchestration-layer only — the harness contract is unchanged in strictness, only in shape. No hardcoded findings / mappings / actions substituted for real agent output.

**Files touched:**
- `src/regimpact/agents/foundry_interpreter.py`
- `src/regimpact/contracts.py`
- `src/regimpact/agents/fabric_workflow.py`
- `src/regimpact/agents/pipeline.py`

**Verification:** `pytest` across 9 test modules with the standard baseline-exclusion filter → **122 passed / 17 failed**. All 17 failures are the pre-existing baseline documented above at §Truncation-Aware Retry (`test_fabric_agents.py` env-drift + `test_cli.py` missing-config) — unrelated to this change. Four direct smoke cases of `RemediationResponse.validate` (empty-no-reason reject, empty-with-reason accept, empty-with-reason-no-evidence reject, non-empty happy path) all pass.

**Consequences:**
- `remediation_planner` returning `{"actions": []}` with a documented reason is now a first-class outcome, not a crash.
- Any Fabric error in the remediation stage soft-fails: report still generates, just without new remediations for that change.
- Unknown themes are dramatically less likely to abort `interpret` — 25 new aliases + retry-prompt enumeration.
- New INFO / WARNING signals in log tails when either branch fires; use them to spot prompt drift.

## Governance

- All meaningful changes require team consensus
- Document architectural decisions here
- Keep history focused on work, decisions focused on direction
