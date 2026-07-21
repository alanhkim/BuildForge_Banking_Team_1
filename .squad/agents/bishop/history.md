# Bishop History

## Core Context
- Project: BuildForge_Banking_Team_1 (forge) — Python regulatory impact framework, Foundry/Fabric-first agent pipeline over synthetic digital-twin banking data.
- User: Hamza Mahmood.
- Current focus: hardening the 6-agent Fabric pipeline (`interpret → control_mapper → gap_analyst → remediation_planner → materializer → ...`). Recent themes: fail-fast latency posture (no client-side semantic retry), empty-with-reason contracts for legitimate no-op outcomes, request/response diagnostics for prompt-drift triage.
- Older 2026-07-06 Regulation Interpreter core work (deterministic offline fallback) is SUPERSEDED — archived in `history-archive.md`. Its contract shape still lives on in `contracts.py` but the offline behavior is no longer active per the 2026-07-08 Foundry/Fabric-first pivot.

## Learnings

### 2026-07-21 (evening) — OneLake writeback extended to CSV alongside Parquet (glob stays restricted)

**Ask.** User: "nice it worked — wrote to the lakehouse. however, i only see parquet written — can we also write the equivalent csv files please..." Additive extension to the boundary hardened earlier today.

**Root cause of the gap.** `export_tables()` (in `export.py`) and `export_gold()` (in `gold.py`) write BOTH `.parquet` and `.csv` for each entity/star-schema table on disk. `export_to_lakehouse()` globbed only `*.parquet`. Net effect: user had CSVs on disk that never made it to OneLake.

**Fix (`src/regimpact/lakehouse.py`).** Extended the glob to pick up both extensions in one sorted list:

```python
upload_files = sorted(
    list(tables_dir.glob("*.parquet")) + list(tables_dir.glob("*.csv"))
)
```

Loop variable renamed `parquet_files` → `upload_files`, log line dropped the format qualifier ("Parquet file(s)" → "file(s)"). Docstrings + module-level comment updated to say "Parquet and CSV". The ADLS SDK doesn't care about extension — it's all bytes over `upload_data`.

Also updated `export_regimpact_lakehouse`'s presence check to gate on `.parquet OR .csv` (was `.parquet` only), so a CSV-only directory still triggers the upload path instead of being silently skipped as "empty". Belt-and-braces: proves CSV isn't parasitic on Parquet.

CLI (`cli.py`): 1-line console message change — "N gold Parquet file(s)" → "N gold file(s)" — to reflect the new reality (~34 files per subpath now, not 17).

**Deliberate constraint — the glob stays restricted to `.parquet + .csv`, NOT `*`.** Anything else that lands in `output/tables/` (stray `.txt` logs, editor backups, `.json` scratch, `README.md`) is almost certainly accidental. Widening the glob to "everything" would push unrelated files into a Fabric lakehouse where they don't belong and where naming collisions could clobber legitimate assets. Restricting to the two formats the local exporters actually produce is a safety property, not a limitation.

**Notebook is unaffected.** `notebooks/08_fabric/01_load_lakehouse.ipynb` reads by exact filename `{name}.parquet`, so CSVs sitting alongside are inert to it. Parquet remains the primary format for Spark ingestion; CSVs are for direct download / Excel / non-Spark consumers. Verified — did not touch the notebook.

**Tests (`tests/test_lakehouse.py`):** 23/23 green (17 existing + 4 new CSV-specific + 2 pre-existing parametrized cases that expanded).
- Renamed `test_export_ignores_non_parquet_files` → `test_export_ignores_non_parquet_non_csv_files` and dropped the CSV from its "should be ignored" fixture (CSVs are now uploaded).
- Added `test_export_to_lakehouse_uploads_csv_alongside_parquet` — both formats uploaded in same call.
- Added `test_export_to_lakehouse_uploads_csv_when_no_parquet_present` — CSV upload not gated on Parquet.
- Added `test_export_to_lakehouse_ignores_other_extensions` — regression guard: `.parquet + .csv + .json + .txt + .md` → exactly 2 uploads. This is the guard against future well-meaning "just glob everything" refactors.
- Added `test_export_to_lakehouse_returns_urls_for_both_formats` — ABFSS URL list contains both file types.

**Windows CRLF gotcha (test-only, one-time).** `Path.write_text(payload)` on Windows silently translates `\n` → `\r\n`, which then broke byte-level `assert_called_once_with(b"id,name\n1,foo\n", ...)` assertions on the mock. Fix: `_write_csv_stub` writes bytes directly via `path.write_bytes(payload.encode("utf-8"))`. Production code is unaffected because `export_tables` writes CSV via `pandas.DataFrame.to_csv(path)`, which the SDK reads back verbatim — the CRLF wrinkle only bit the test fixture. Worth remembering: any test that mocks byte-level upload and stages fixtures via `write_text` needs `write_bytes` on Windows.

**Deliberately NOT done:**
- Did not touch `_normalize_fabric_id`, `_resolve_onelake_endpoint`, or any of the FriendlyNameSupportDisabled trilogy boundary hardening — orthogonal.
- Did not change any public function signature.
- Did not widen the glob beyond Parquet + CSV. See above.
- Did not touch the Fabric loader notebook.

### 2026-07-21 (afternoon) — OneLake canonical ADLS path is the bare lakehouse GUID (no `.Lakehouse` suffix)

**Bug (third `FriendlyNameSupportDisabled` incident this week, same operator, same workspace).** GUIDs are canonical (yesterday's `_normalize_fabric_id` fix). Endpoint is regional and correctly overridden (this morning's `_resolve_onelake_endpoint` fix). Uploads *still* fail with `FriendlyNameSupportDisabled`. Same generic error string — third root cause.

**Definitive diagnosis (via Fabric REST, not blogs).** `GET /v1/workspaces/{ws}/lakehouses/{lh}` returns:

```json
"oneLakeFilesPath":  "https://onelake.dfs.fabric.microsoft.com/{workspace_id}/{lakehouse_id}/Files"
"oneLakeTablesPath": "https://onelake.dfs.fabric.microsoft.com/{workspace_id}/{lakehouse_id}/Tables"
```

Fabric's canonical ADLS Gen2 path uses the **bare lakehouse GUID** as the top-level directory. Our code has always built `{lakehouse_id}.Lakehouse/Files/{subpath}` — copied from Microsoft docs about mounting lakehouses in **Spark shortcuts**, where the `.Lakehouse` suffix is a valid UI/notebook mount convention. That convention does NOT apply to direct ADLS Gen2 access via `DataLakeServiceClient`. The name-plane rejects `<guid>.Lakehouse` as unresolvable → `FriendlyNameSupportDisabled`.

**Fix (`src/regimpact/lakehouse.py`, one-liner in each of two spots inside `export_to_lakehouse`):**
- `target_dir = f"{lakehouse_id}/Files/{files_subpath}"` (was `{lakehouse_id}.Lakehouse/Files/…`).
- `abfss_url = f"abfss://{workspace_id}@onelake.dfs.fabric.microsoft.com/{lakehouse_id}/Files/{files_subpath}/{local_path.name}"` (was `{lakehouse_id}.Lakehouse/Files/…`).
- Module docstring + `lakehouse_id:` param docstring + inline comment updated to state the canonical form is the bare GUID and call out that `.Lakehouse` is a shortcut/UI convention only.
- Also updated `cli.py` display string (`f"{settings.fabric_lakehouse_id}/Files/…"`) — it advertises where the Python upload landed, so it must match the SDK path.

**Why this hid for so long.** Two reasons converged:
1. The two prior `FriendlyNameSupportDisabled` fixes (GUID normalization → regional endpoint override) were each real bugs *and* were each necessary preconditions before this third layer became visible. With malformed GUIDs the SDK never got as far as looking up the directory; with wrong regional routing it never got past the account-URL resolution. Each fix unblocked the next failure mode.
2. Microsoft's own docs use the `.Lakehouse` suffix in Spark/shortcut examples — which is correct for those contexts. The doc set doesn't clearly distinguish "mount path for a notebook" from "direct ADLS Gen2 name-plane identifier"; a reader porting one example to the other silently gets it wrong.

**Diagnostic funnel for future `FriendlyNameSupportDisabled` occurrences (canonical sequence — run in order, cheapest first):**
1. **GUID validation at the boundary.** Strip + `uuid.UUID()` + canonical-form check. Covers env-var copy/paste artefacts. → `_normalize_fabric_id`.
2. **Regional endpoint check.** Hit `GET /v1/workspaces/{ws}` and read `oneLakeEndpoints.dfsEndpoint`. If it's regional, the hardcoded global endpoint's routing layer will refuse to forward. → `_resolve_onelake_endpoint` + `FABRIC_ONELAKE_DFS_ENDPOINT`.
3. **Canonical path verification.** Hit `GET /v1/workspaces/{ws}/lakehouses/{lh}` and read `properties.oneLakeFilesPath` / `oneLakeTablesPath`. Fabric will tell you *exactly* what the ADLS Gen2 path prefix is. Do not trust blogs or Spark examples for the direct-ADLS path — trust the metadata API.

`FriendlyNameSupportDisabled` is Fabric's generic "identifier could not be resolved" error and covers all three: bad GUID, wrong region routing, or malformed path prefix. The funnel above is the right sequence for triage.

**Rule that falls out of this pattern.** Anytime you build a Fabric ADLS Gen2 URL from documented conventions, cross-check it against `GET /v1/workspaces/{ws}/lakehouses/{lh}` → `properties.oneLakeFilesPath`. That REST response is the authoritative canonical form. If the string you're constructing does not match it byte-for-byte (after substituting your workspace_id and lakehouse_id), your URL is wrong regardless of what any doc/blog/tutorial says.

**Tests (`tests/test_lakehouse.py`):** 19/19 green (17 existing, all updated to drop `.Lakehouse` in expected values, + 2 new regression guards).
- `test_export_to_lakehouse_uses_bare_guid_directory_no_lakehouse_suffix` — asserts `get_directory_client("{lh_guid}/Files/tables")` and that `.Lakehouse` appears nowhere in the passed string.
- `test_export_returned_abfss_urls_never_contain_lakehouse_suffix` — asserts `.Lakehouse` appears nowhere in any returned ABFSS URL.

**Backward-compat call-out.** ABFSS URLs returned by `export_to_lakehouse` and `export_regimpact_lakehouse` change format (drop `.Lakehouse`). That is intentional and correct — the old URLs were structurally invalid and nobody could have been using them successfully, because every upload was failing before returning anything.

**Deliberately NOT done:**
- Did not touch `_normalize_fabric_id`. GUID validation itself was and is fine — the previous fixes were correct, just insufficient.
- Did not touch `_resolve_onelake_endpoint`. Regional endpoint work stands.
- Did not change any public function signature.
- Did not touch `pipeline.py`, `foundry_client.py`, `agents/`, or `01_load_lakehouse.ipynb`. The notebook uses paths *relative* to the attached lakehouse's `Files/` root (`Files/regimpact_raw`, `Files/regimpact_gold`), so it never spells out the `.Lakehouse` prefix in the first place — it was never part of the bug surface.
- Did not re-open the FabricMaterializer discussion. Pure path-string fix.

**Grep sweep results.** After the fix, all remaining `.Lakehouse` occurrences in the tree are either (a) explanatory prose in `lakehouse.py` docstrings / `test_lakehouse.py` comments explaining why we don't use it, (b) the two regression-guard assertions themselves (`assert ".Lakehouse" not in target_dir`), (c) the `regimpact.lakehouse` Python module name (unrelated), or (d) `.squad/agents/lambert/history.md` (historical record of the pre-fix state — left unchanged per user instruction). `docs/` had zero hits. `01_load_lakehouse.ipynb` had zero hits. `pyproject.toml` had zero hits. Nothing else needed updating.

**Files touched:**
- `src/regimpact/lakehouse.py` — module docstring, `lakehouse_id:` param docstring, `target_dir` line, `abfss_url` line + inline comment.
- `src/regimpact/cli.py` — one display-string kwarg (`{settings.fabric_lakehouse_id}/Files/…`).
- `tests/test_lakehouse.py` — 6 expected-value updates + 2 new regression tests.
- `.squad/decisions/inbox/bishop-onelake-drop-lakehouse-suffix.md` — decision drop.
- `.squad/skills/fabric-resource-id-validation/SKILL.md` — new section on canonical ADLS path verification.

**For the operator:** no `.env` change needed. Just re-run `python -m regimpact interpret ...` after `pip install -e .` picks up the code change. This should be the last `FriendlyNameSupportDisabled` — GUIDs, endpoint, and path prefix are all now correct simultaneously for the first time.

### 2026-07-21 — OneLake regional endpoint: env-var override, ABFSS stays canonical

**Bug (follow-on to yesterday's GUID hardening).** Same operator, same workspace, canonical GUIDs — and `interpret` still failed with `(FriendlyNameSupportDisabled) Request Failed with WorkspaceId and ArtifactId should be either valid Guids or valid Names`. The GUIDs pass `_normalize_fabric_id`, so `FriendlyNameSupportDisabled` was coming from a *different* boundary this time.

**Diagnosis.** Hit `GET https://api.fabric.microsoft.com/v1/workspaces/{id}` directly and the workspace advertises a **regional** OneLake endpoint via `oneLakeEndpoints.dfsEndpoint`: `https://northcentralus-onelake.dfs.fabric.microsoft.com`. Our `lakehouse.py` hardcoded the global endpoint (`https://onelake.dfs.fabric.microsoft.com`) as the ADLS account URL. On some capacity SKUs the global endpoint's routing layer does not forward transparently — it returns the same opaque `FriendlyNameSupportDisabled` error that a malformed GUID does. Hitting the regional endpoint directly works. So the same server-side error string covers two entirely different root causes (bad ID *or* wrong endpoint for this capacity), which is why yesterday's fix looked like it solved the problem but the user was actually going to hit the second cause on their next run.

**Fix (`src/regimpact/lakehouse.py`, `src/regimpact/settings.py`, `src/regimpact/cli.py`):**
- `_ONELAKE_ACCOUNT_URL = "https://onelake.dfs.fabric.microsoft.com"` stays as the module-level *fallback default* (backward-compat — anyone already relying on it sees no change).
- New `_resolve_onelake_endpoint(raw)` helper: `None` / empty / whitespace → returns the default; otherwise strip whitespace + surrounding quotes, then validate the shape at the boundary (must start with `https://` and contain `onelake.dfs.fabric.microsoft.com`). Same failure-class reasoning as yesterday's GUID validator: malformed config fails every run until fixed → `LakehouseNotConfiguredError` (soft yellow skip per §0), never `LakehouseWriteError` (transient/red).
- New optional parameter `onelake_endpoint: str | None = None` on both `export_to_lakehouse` and `export_regimpact_lakehouse`. Signature-additive, default-preserving.
- `DataLakeServiceClient(account_url, credential=...)` uses the resolved endpoint.
- **ABFSS URLs are unchanged** — always `abfss://{workspace_id}@onelake.dfs.fabric.microsoft.com/…`. This is deliberate: ABFSS is a *name-plane* identifier that downstream Spark shortcuts, `01_load_lakehouse.ipynb`, and lineage tools parse; the regional prefix is a *data-plane* routing detail OneLake applies for you. Region-tainting the URL would break those consumers. Added an explicit test guarding that invariant.
- `settings.py`: `fabric_onelake_dfs_endpoint: str = os.getenv("FABRIC_ONELAKE_DFS_ENDPOINT") or ""` — pure env passthrough, no default baked into `Settings`. The default lives in the domain module where the SDK call happens. Keeps the "settings are dumb bindings" pattern intact.
- `cli.py`: one-line threading — `onelake_endpoint=settings.fabric_onelake_dfs_endpoint` on the existing `export_regimpact_lakehouse` call. Everything else unchanged.

**Fix pattern (generalizable).** Env-var override → optional parameter (defaults to `None`) → resolver helper that falls back to a module-level default when the override is empty/unset → validate at the boundary and map malformed values to the "not configured" soft-skip class. Same shape as the GUID validator; same §0 failure semantics. Backward-compatible by construction (empty env var → same behavior as before). Explicit config beats magic auto-retry (do NOT "try global first, fall back to regional" — one round-trip per run, silent regional fallback hides capacity misconfiguration).

**Why NOT auto-detect the endpoint from `GET /v1/workspaces/{id}`?** Considered and rejected. Pros: no operator config needed. Cons: (a) adds a synchronous Fabric REST hop to every `interpret` run, (b) needs a token for `api.fabric.microsoft.com` (different scope than `storage.azure.com`), (c) failure mode when the metadata call fails is unclear — silently fall back to global? Fail hard? Neither is obviously right. An env var is one line in `.env` and zero runtime cost. Explicit > magic.

**Why NOT change ABFSS URL format.** Even on a regional capacity, the workspace advertises the canonical `onelake.dfs.fabric.microsoft.com` host as its `oneLakeEndpoints.blobEndpoint` for ABFSS-style access — regional routing is a *data-plane* concern, applied transparently by OneLake based on the caller's location and the workspace's capacity region. Downstream tools (Spark shortcuts, `01_load_lakehouse.ipynb`, Purview lineage) expect the canonical form. If we started emitting `abfss://{ws}@northcentralus-onelake.dfs.fabric.microsoft.com/...` we would (a) break those consumers, (b) fragment lineage records by region, (c) leak an operational detail into a logical identifier. Test `test_export_abfss_url_uses_canonical_host_even_with_regional_endpoint` guards this invariant.

**Failure-class semantics (unchanged, still per §0).** `LakehouseNotConfiguredError` = soft yellow skip: unset workspace/lakehouse GUID, malformed GUID, *and now malformed endpoint*. `LakehouseWriteError` = hard red: transient network / auth / capacity failures at upload time. Malformed `FABRIC_ONELAKE_DFS_ENDPOINT` slots into the soft-skip bucket for the same reason a malformed GUID does — it fails every run until the operator fixes it, so it is definitionally "not configured".

**Tests (`tests/test_lakehouse.py`):** 17/17 green (10 existing + 7 new). New coverage:
- `None` / `""` / `"   "` for `onelake_endpoint` → SDK receives the global default (parametrized).
- Explicit regional endpoint → SDK receives the regional URL verbatim.
- Regional endpoint + ABFSS URL invariant → returned URLs still use the canonical `onelake.dfs.fabric.microsoft.com` host; belt-and-braces `assert "northcentralus-onelake" not in urls[0]`.
- `"onelake.dfs.fabric.microsoft.com"` (no scheme) → `LakehouseNotConfiguredError`, message names the env var and the offending value.
- `"https://example.com"` (wrong host) → `LakehouseNotConfiguredError`, message names the env var and the offending value.

**Deliberately NOT done:**
- Did not touch `pipeline.py`, `foundry_client.py`, or the agent stack (out of scope; only OneLake writeback consumes the DFS endpoint).
- Did not re-validate GUIDs differently — `_normalize_fabric_id` stays as-is.
- Did not add auto-detect from `oneLakeEndpoints.dfsEndpoint`. Reasoning above.
- Did not change the ABFSS URL format. Reasoning above.

**Files touched:**
- `src/regimpact/lakehouse.py` — module comment + `_ONELAKE_ENDPOINT_MARKER` constant + `_resolve_onelake_endpoint` helper + new `onelake_endpoint` parameter on both public functions + SDK call uses resolved endpoint.
- `src/regimpact/settings.py` — one field: `fabric_onelake_dfs_endpoint`.
- `src/regimpact/cli.py` — one kwarg on the existing `export_regimpact_lakehouse` call.
- `tests/test_lakehouse.py` — 5 new test functions (one parametrized across 3 values).
- `.squad/decisions/inbox/bishop-onelake-regional-endpoint.md` — decision drop.
- `.squad/skills/fabric-resource-id-validation/SKILL.md` — related-pattern section appended.

**For the operator:** add `FABRIC_ONELAKE_DFS_ENDPOINT=https://northcentralus-onelake.dfs.fabric.microsoft.com` to `.env`, then re-run `python -m regimpact interpret ...`.

### 2026-07-21 — Team update: your GUID validation is now the ENTIRE OneLake boundary — Livy client is gone
- Lambert removed the FabricMaterializer + Livy layer today (reverses the 2026-07-17 Option-A decision). Files deleted: `fabric_materializer.py`, `fabric_materializer_spec.py`, `fabric_livy_client.py`, plus both test modules. See top of `.squad/decisions.md` for the governing reversal.
- **Direct impact on your work:** the `fabric_livy_client.py` GUID revalidation follow-up you flagged in your 2026-07-20 "Deliberately NOT done" list is now MOOT — the file is gone. Your `_normalize_fabric_id` in `lakehouse.py::export_to_lakehouse` is the ENTIRE OneLake ID validation surface for the Python pipeline. There is no longer a second consumer of `FABRIC_WORKSPACE_ID` / `FABRIC_LAKEHOUSE_ID` that needs the pattern re-applied. Cross that off your follow-up list.
- **Reusable pattern in `.squad/skills/fabric-resource-id-validation/SKILL.md` stays useful** — anything future that consumes a Fabric/OneLake GUID env var (workspace, lakehouse, item, capacity) should still follow strip → `uuid.UUID()` → canonical-form check → soft-skip error class. Just no immediate second application to make in this codebase right now.
- **Tests:** `tests/test_lakehouse.py` 10/10 green (your GUID coverage untouched). Full suite: 103 passed / 23 pre-existing baseline failures (env-drift + asyncio config + one interpreter contract drift — none touch your code). The 2 test files removed with the drop (`test_fabric_livy_client.py`, `test_fabric_materializer.py`) never asserted anything about your validation path; nothing lost from your coverage story.
- **Failure-class semantics you established survive intact:** `LakehouseNotConfiguredError` = soft yellow skip for config issues (empty / malformed / non-canonical GUID); `LakehouseWriteError` = hard red for transient / capacity / auth failures. Nothing in the drop touched decisions.md §0. If you own future Fabric writeback hardening, that mapping is still authoritative.

### 2026-07-20 — OneLake `FriendlyNameSupportDisabled`: strip + `uuid.UUID()` at the boundary

**Bug:** `interpret` failed with `OneLake upload failed: Failed to upload output\tables\business_processes.parquet to OneLake: (FriendlyNameSupportDisabled) Request Failed with WorkspaceId and ArtifactId should be either valid Guids or valid Names`. Opaque server-side error surfaced at upload time as a red `LakehouseWriteError`.

**Diagnosis:** `FriendlyNameSupportDisabled` is Fabric ADLS Gen2 rejecting the filesystem name (`workspace_id`) and/or the top-level directory prefix (`lakehouse_id`) because neither was a valid GUID nor a valid friendly name on a tenant where friendly-name resolution is disabled. The usual copy/paste artefacts do this every time: trailing `\n` from `export FOO="…"`, surrounding `'…'` / `"…"` from the shell, whitespace, partial paste, or accidentally using the workspace *display name*.

**Fix (`src/regimpact/lakehouse.py`):**
- New `_normalize_fabric_id(raw, env_var)` helper — strips whitespace, then strips a single layer of surrounding single/double quotes, then re-strips whitespace, then validates via `uuid.UUID(cleaned)`. Rejects braced/urn/no-dash forms by asserting `str(parsed) == cleaned.lower()` (canonical 8-4-4-4-12 hex).
- `export_to_lakehouse()` now normalizes both `workspace_id` and `lakehouse_id` at entry BEFORE any SDK call, and passes the cleaned values into both the ADLS SDK and the returned ABFSS URLs. `Settings` is never mutated (values are cleaned locally). Signature unchanged.
- Empty check is preserved by the strip-then-empty ordering: `"   "` → `""` → `LakehouseNotConfiguredError` with the "not set" message, matching prior behavior.

**Why `LakehouseNotConfiguredError`, not `LakehouseWriteError`:** Per decision §0, `LakehouseNotConfiguredError` is the soft skip (yellow, CLI continues); `LakehouseWriteError` is the hard fail (red, best-effort). A malformed env var fails EVERY run until fixed — that is a configuration bug, not a transient network/capacity issue. Mapping it to the soft-skip branch keeps `interpret` finishing successfully (local Parquet is still the source of truth) while giving the operator a specific message naming the env var and the offending value.

**Reusable pattern captured** in `.squad/skills/fabric-resource-id-validation/SKILL.md` — strip whitespace, strip surrounding quotes, `uuid.UUID()` validate, canonical-form check, map to the "not configured" error class. Applies to any Fabric/OneLake ID env var (workspace, lakehouse, item, capacity).

**Tests (`tests/test_lakehouse.py`):** 10/10 green. Added coverage for trailing-newline strip, non-GUID display-name rejection, whitespace-only stripping to empty, and quoted-GUID acceptance. Existing tests updated to use canonical GUIDs (`_WS_GUID` = `11111111-…`, `_LH_GUID` = `22222222-…`) as fixture constants.

**Deliberately NOT done:**
- Did not touch `fabric_livy_client.py` (out of scope). If Livy also reads `FABRIC_WORKSPACE_ID` raw, that's a follow-up for Lambert.
- Did not validate in `export_regimpact_lakehouse` directly — it delegates to `export_to_lakehouse`, which now validates. Skipping validation when both dirs are empty is fine: nothing gets uploaded so there's nothing to break.
- Did not add a Settings-level validator. Keeping the boundary at `lakehouse.py` matches the pattern of "clean at the edge, not in the config layer" and avoids forcing every consumer of Settings through GUID validation.

**Files touched:**
- `src/regimpact/lakehouse.py` — `_normalize_fabric_id` + normalized entry.
- `tests/test_lakehouse.py` — GUID fixture constants + 4 new tests.
- `.squad/decisions/inbox/bishop-onelake-guid-validation.md` — decision drop.
- `.squad/skills/fabric-resource-id-validation/SKILL.md` — reusable pattern.

### 2026-07-17 — Gap Analyst empty-tolerance: close the cross-stage break I flagged

**Followup to the Control Mapper hardening below.** That pass added a documented empty-with-reason exit for `ControlMappingResponse`, but `GapAnalysisRequest.validate()` still raised `"control_ids is required"` the moment control_mapper legitimately returned empty. `interpret` would fail one hop later. This pass closes that loop with the same shape.

**Delivered:**

- **`contracts.py::GapAnalysisRequest`** — added `reason: str | None = None`. `validate()` now accepts empty `control_ids` when `reason.strip()` is non-empty; otherwise still raises `ValidationError("control_ids is required (or provide non-empty reason)")`. `obligation_ids` still required unconditionally (obligations are pipeline input, not a downstream artefact — an empty obligation set is a genuine bug).
- **`pipeline.py`** — the gap_analyst stage now forwards `cm_response.reason` into `GapAnalysisRequest(reason=...)` when `cm_response.mappings` is empty. On the happy path (non-empty mappings), `reason` stays `None` — zero behaviour change.
- **`pipeline.py` WARNING log** — new log line right before the gap_analyst call: `Fabric stage propagating empty control_ids stage=gap_analyst change_id=... reason=...`. Complements the existing WARNING at the control_mapper stage so an operator can trace the reason forward through the pipeline in a single log tail.

**Deliberately NOT done, with justification:**

1. **No response-side change** to `GapAnalysisResponse`. It already accepts empty `findings` as a valid outcome ("every obligation→control pair meets its target maturity" — documented in the class docstring). Only `tool_evidence` is required, and that stays required. No mirror of the empty-with-reason pattern needed on the response.
2. **No retry wrapper on `analyze_gaps`.** Per Hamza's rule 4, only add retry if the ControlMapper pattern makes it cheap. That pattern was REMOVED from `map_controls` in commit `f3d6ab4` (2026-07-17 fail-fast decision). Adding it to `analyze_gaps` alone would create an asymmetry — one agent retries, four don't. Flagged as a follow-up if empty-with-reason turns out to be common enough to justify a general harness-level retry.
3. **No downstream chain fix beyond one hop.** Per Hamza's rule 6. I audited the immediate next stage (remediation_planner) — the pipeline already guards it with `if gap_ids:` at line ~522, so empty findings skip that stage cleanly. Score_narrator receives locally-precomputed floats, not agent output, so it's independent. Chain intact after this fix.

**Test result:** 55 passed, 7 deselected. One test (`test_control_mapper_pipeline_stage_logs_warning_on_empty_with_reason`) hangs because it asserts the OLD behaviour — `pytest.raises(ValidationError, match="control_ids")` — and only stubs `FabricControlMapperAgent`, so once the pipeline no longer raises there it tries to instantiate a real `FabricGapAnalystAgent` and hangs on the Foundry client. Flagged for Hicks to rewrite (see summary).

**Files touched:**
- `src/regimpact/contracts.py` — `GapAnalysisRequest` gets `reason`, validation loosened symmetrically with `ControlMappingResponse`.
- `src/regimpact/agents/pipeline.py` — forward `cm_response.reason` into the request; new WARNING log for the gap_analyst propagation path.
- `.squad/decisions/inbox/bishop-gap-analyst-empty-tolerance.md` — decision note documenting the contract extension and the remaining "one hop at a time" audit.

**Consistency win:** `reason: str | None` is now the standard shape for "empty output is legitimate" across the pipeline. `ControlMappingResponse.reason` (output) → `GapAnalysisRequest.reason` (input). Any future agent that needs this pattern should follow the same naming.

### 2026-07-17 — Control Mapper hardening: unwrap validation, request context logging, empty-with-reason contract

**SUMMARISED — full details archived in `history-archive.md`.** Delivered Fixes 1/2/3 from Hamza's approved plan:

- **Fix 1 (`fabric_workflow.py::_validated`)**: `FabricAgentHarnessError` embeds `ValidationError` detail + 500-char raw-answer snippet.
- **Fix 2 (`_ask` + `_payload_cardinalities`)**: Every Fabric agent request logs list-field cardinalities at INFO; payload body at DEBUG.
- **Fix 3 (`contracts.py::ControlMappingResponse`)**: `reason: str | None = None`; `validate()` accepts empty mappings iff `reason.strip()` non-empty; `tool_evidence` still required in both branches.
- **Pipeline** (`pipeline.py`): control_mapper stage logs WARNING and continues when `mappings == []` and `reason` present.

**Fix 6 (retry wrapper) was REVERTED before commit** for latency parity with 2026-07-17 fail-fast decision (`f3d6ab4`). See `.squad/decisions.md` §"ControlMapper retry mechanism NOT included". Fixes 1/2/3 shipped in `1df0f5c`. 43 passed / 6 deselected at the time; verified again by Hicks post-GapAnalyst work: 62 passed / 6 deselected.

**Files:** `src/regimpact/contracts.py`, `src/regimpact/agents/fabric_workflow.py`, `src/regimpact/agents/pipeline.py`.


- The 3-attempt semantic retry loop in `FabricDataAgentClient.ask` (which layered on top of the lenient parser you built in `ccd35d8`) has been removed for latency. Production is now single-attempt / fail-fast (commit `f3d6ab4`, hamza-dev).
- **Your lenient parser stays.** Metadata defaults, inner-payload recovery, markdown-fence stripping, and prose-embedded JSON extraction all still run — just once per call now instead of up to three times.
- Practical impact: any malformed agent response that the lenient parser cannot recover now aborts the `interpret` pipeline immediately. Operator sees the `truncated=true/false` diagnostic and re-runs.
- If future work re-introduces retries, do it at the transport layer inside `FoundryAgentClient._invoke_with_retry`, not around `ask`.

### 2026-07-06 — Regulation Interpreter core (Tasks 1-4)

**SUPERSEDED — full details archived in `history-archive.md`.** Delivered typed contracts (`contracts.py`), deterministic DORA catalog fixture, offline interpreter, and 22→29 tests (with Hicks). Contract shape still informs current code; offline agent behavior is no longer active per 2026-07-08 Foundry/Fabric-first pivot.

## Team Updates

### 2026-07-17 — OneLake writeback wired into `interpret`
Lambert landed opt-in OneLake writeback for the `interpret` CLI command. Local Parquet under `output/tables/` is still the source of truth; the Fabric upload is gated on `FABRIC_WORKSPACE_ID` + `FABRIC_LAKEHOUSE_ID` and fails soft (non-fatal). To enable: set both env vars and run `pip install .[fabric]` (new optional extra). See `.squad/decisions.md` §0.

### 2026-07-17 — Fabric Data Agent response layer hardened

**SUMMARISED — full details archived in `history-archive.md`; corresponding decision `.squad/decisions.md` §4.** Three-layer resilience for misbehaved Fabric responses: (1) lenient envelope defaults in `_fabric_response_from_payload`; (2) inner-payload recovery via `_RECOVERABLE_INNER_KEYS`; (3) semantic retry loop (`_ask_with_semantic_retry`, 3 attempts). Markdown-fence handling + `_extract_json_block` brace-walker added. `_json_answer` accepts dict-typed answers + prose-embedded JSON. Constitutional: NEVER substitutes hardcoded content; failures still surface. 8 new tests, 26/26 fabric_workflow pass. **Note:** the outer 3-attempt semantic retry was later removed for latency (`f3d6ab4`); the lenient parser stays.

### 2026-07-17 (extension) — Truncation-aware retry for inner answer JSON

**SUMMARISED — full details archived in `history-archive.md`; corresponding decision `.squad/decisions.md` §5.** Extended the semantic retry loop with `_validate_inner_answer` (JSON-shape-only) + `_looks_truncated` heuristic (open-bracket without matching close) + truncation-specific prompt augmentation (concise-mode nudge: ≤200 char rationale, 1 source_ref, ≤3500 chars total). Prompt discipline chosen because Foundry rejects `max_output_tokens` at top level for `agent_reference`. 5 new tests, 31/31 fabric_workflow pass. **Note:** the retry loop this extended was later removed (`f3d6ab4`); `_validate_inner_answer` + `_looks_truncated` still live in the codebase so operator errors carry the `truncated=true/false` diagnostic.



### 2026-07-17 — Team update: split-commit landed (retry loop NOT shipped, Materializer separate)

- Coordinator split a mixed working tree into two commits after user approval ("yes i agree").
- **`1df0f5c`** feat(fabric): ControlMapper empty-with-reason contract + request/response diagnostics — your empty-with-reason contract shape SHIPPED as designed. `tool_evidence` still required for both branches (non-empty mappings AND empty-with-reason). Error unwrap landed. Per-obligation `candidate_control_ids` in request payload landed.
- **Retry loop REVERTED before commit.** Single-attempt `map_controls` is now the norm — the `_map_controls_attempt` parse/validate split you introduced was collapsed back into a single-attempt validate-in-place flow. Decision recorded as "ControlMapper retry mechanism NOT included" in `.squad/decisions.md`. Rationale: latency parity with the 2026-07-17 semantic-retry-removal decision — one agent retrying while four don't creates asymmetric stage latencies. Your gap_analyst empty-tolerance fix (the follow-up to the cross-stage break you flagged) also shipped in the same commit.
- **`6cc6ffd`** feat(fabric): FabricMaterializerAgent + Livy client — brand-new 6th agent in the pipeline (Option A: deterministic Livy, Foundry as boundary supervisor). Not yours, but changes the pipeline shape: `interpret → control_mapper → gap_analyst → remediation_planner → materializer → ...`. Read-only from your perspective right now, but if you touch pipeline glue expect a new stage between OneLake upload and any downstream Fabric queries.
- 76 passed / 7 deselected. The 6 v3/v4 drift tests + 1 network test remain deselected — tracked separately.
- Your gap_analyst empty-tolerance rewrite note for Hicks (test needing `FabricGapAnalystAgent` stub) is still open — landed contract, test rewrite not yet done. Not blocking.