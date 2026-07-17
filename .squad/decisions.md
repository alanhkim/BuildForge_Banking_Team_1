# Squad Decisions

## Active Decisions

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

### 2026-07-17: PROPOSED — ControlMapper contract accepts documented empty mappings

**Status:** Proposed — awaiting user approval
**By:** Squad Coordinator (from Lambert + Bishop diagnosis)

**What:** `ControlMappingResponse` will accept `mappings: []` only when accompanied by a non-empty `reason: str` explaining why nothing matched. Empty mappings with no reason remains a hard failure. Harness must surface the underlying validation error to the CLI (unwrap `"Fabric agent response failed validation"` into `"Fabric agent response failed validation: {reason}"`).

**Why:** Current unconditional rejection of empty mappings blocks the pipeline whenever the Fabric agent legitimately finds no matches or is under-informed by tool evidence. Opaque error message prevents user diagnosis. Per project constitution, no offline fallback — we harden the real Fabric path.

**Scope of change:** `src/regimpact/contracts.py` (schema), `src/regimpact/agents/fabric_workflow.py` (error unwrap + logging + bounded retry), Foundry portal `RegImpactControlMapper` prompt, `tests/test_fabric_workflow.py`.

## Governance

- All meaningful changes require team consensus
- Document architectural decisions here
- Keep history focused on work, decisions focused on direction
