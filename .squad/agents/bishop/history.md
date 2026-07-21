# Bishop History

## Core Context
- Project: BuildForge_Banking_Team_1 (forge) — Python regulatory impact framework, Foundry/Fabric-first agent pipeline over synthetic digital-twin banking data.
- User: Hamza Mahmood.
- Current focus: hardening the 6-agent Fabric pipeline (`interpret → control_mapper → gap_analyst → remediation_planner → materializer → ...`). Recent themes: fail-fast latency posture (no client-side semantic retry), empty-with-reason contracts for legitimate no-op outcomes, request/response diagnostics for prompt-drift triage.
- Older 2026-07-06 Regulation Interpreter core work (deterministic offline fallback) is SUPERSEDED — archived in `history-archive.md`. Its contract shape still lives on in `contracts.py` but the offline behavior is no longer active per the 2026-07-08 Foundry/Fabric-first pivot.

## Learnings

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