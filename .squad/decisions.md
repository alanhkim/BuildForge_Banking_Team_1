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

## Governance

- All meaningful changes require team consensus
- Document architectural decisions here
- Keep history focused on work, decisions focused on direction
