# Bishop History

## Core Context
- Project: BuildForge_Banking_Team_1 (forge) — Python regulatory impact framework, Foundry/Fabric-first agent pipeline over synthetic digital-twin banking data.
- User: Hamza Mahmood.
- Current focus: hardening the 6-agent Fabric pipeline (`interpret → control_mapper → gap_analyst → remediation_planner → materializer → ...`). Recent themes: fail-fast latency posture (no client-side semantic retry), empty-with-reason contracts for legitimate no-op outcomes, request/response diagnostics for prompt-drift triage.
- Older 2026-07-06 Regulation Interpreter core work (deterministic offline fallback) is SUPERSEDED — archived in `history-archive.md`. Its contract shape still lives on in `contracts.py` but the offline behavior is no longer active per the 2026-07-08 Foundry/Fabric-first pivot.

## Learnings

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

### 2026-07-17 — Control Mapper hardening: unwrap validation, request context logging, empty-with-reason contract, bounded retry

**Delivered fixes 1/2/3/6 from Hamza's approved plan:**

- **Fix 1 (`fabric_workflow.py::_validated`)**: `FabricAgentHarnessError` now embeds the underlying `ValidationError` detail plus a 500-char raw-answer snippet in the message. CLI operators no longer need debug logs to see WHY validation failed.
- **Fix 2 (`fabric_workflow.py::_ask` + new `_payload_cardinalities` helper)**: Every Fabric agent request now logs list-field cardinalities (obligation_ids_count, tool_evidence_count, candidate_controls_count, etc.) at INFO. Payload body stays at DEBUG. Same log line covers all agents — no per-agent bespoke fields.
- **Fix 3 (`contracts.py::ControlMappingResponse`)**: Added `reason: str | None = None` field. `validate()` accepts empty mappings ONLY when `reason.strip()` is non-empty; otherwise raises `ValidationError("mappings is required (or provide non-empty reason)")`. Both branches still require `tool_evidence` (no unfounded empty responses).
- **Fix 6 (`fabric_workflow.py::map_controls`)**: Refactored into `map_controls` + private `_map_controls_attempt`. On empty-without-reason, retries once with `retry_attempt=2` added to the payload. Both attempts log at INFO with `attempt=`, `mappings=`, `reason_present=`. Other failure modes (invalid JSON, wrong types) still bubble up on first attempt — no retry.
- **Pipeline empty-with-reason handling (`pipeline.py`)**: After control_mapper stage, if `mappings == []` and `reason` is present, log at WARNING with the reason and continue. Downstream stages will see empty inputs.

**Gotchas / decisions on ambiguous spots:**

1. **Retry needed to split `map_controls`** into a "parse but don't validate" helper (`_map_controls_attempt`) + a validate-once caller. Reason: with Fix 3 in place, `_validated` would fail-fast on empty-without-reason before the retry could fire. Splitting lets us inspect the parsed shape and decide whether to retry BEFORE contract validation. Kept scoped to control_mapper as the plan requested.
2. **Kept `tool_evidence` required in the empty-with-reason branch of `ControlMappingResponse.validate()`.** The plan didn't specify — I chose to preserve grounding so an agent can't return `{"mappings": [], "reason": "..."}` with zero evidence. Flagged for Hicks.
3. **Pipeline `continue vs raise` ambiguity**: Plan literally says "continue the pipeline (not raise)". I do exactly that — log WARNING with reason at the CM stage and let control flow proceed. Note: `GapAnalysisRequest.validate()` requires non-empty `control_ids`, so an empty-with-reason CM result will fail at the gap_analyst stage. That's out of scope for this fix; documented in the decisions inbox.
4. **No `to_schema()` / `model_json_schema()` in the repo** — grep returned no matches, so no JSON Schema export to update.
5. **`_payload_cardinalities`** is a module-level helper (not a method) so it can be reused by any future agent adapter without needing a harness instance.

**Test result:** 43 passed, 6 deselected (the requested `not defaults_to_deployed` filter). Ran with `pytest tests/test_fabric_workflow.py tests/test_lakehouse.py tests/test_export_audit.py tests/test_impact_scoring.py tests/test_smoke.py -q -k "not defaults_to_deployed" --tb=short`.

**Files touched:**
- `src/regimpact/contracts.py` — `ControlMappingResponse` gets `reason`, validation loosened for documented empty case.
- `src/regimpact/agents/fabric_workflow.py` — Fix 1 (`_validated`), Fix 2 (`_ask` + `_payload_cardinalities`), Fix 6 (`map_controls` + `_map_controls_attempt`).
- `src/regimpact/agents/pipeline.py` — empty-with-reason WARNING log in control_mapper stage.
- `.squad/decisions/inbox/bishop-control-mapper-implementation.md` — decision note on the split validation flow + downstream gap_analyst caveat.


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

**Problem:** `interpret` was aborting mid-pipeline with `Fabric Data Agent response missing required field(s): answer` — a single misbehaved response from Fabric killed the whole run, forcing operators to re-execute manually.

**Solution — three-layer resilience, no offline agent-behavior fallback (constitution respected):**

1. **Lenient envelope defaults** (`_fabric_response_from_payload` in `foundry_client.py`): only `answer` is truly required. Missing `citations`/`tool_evidence`/`confidence` default to `[]`/`[]`/`"low"` with a WARNING log listing what was defaulted, agent name/version, and observed keys. Also handles `answer` being a dict/list instead of a string (JSON-encodes it).
2. **Inner-payload recovery** (`_RECOVERABLE_INNER_KEYS` frozenset): when the envelope lacks `answer` but the top-level JSON has any of `{mappings, findings, actions, narrative, change_id, impacted_entities}`, treat the whole payload as the inner answer and synthesize the envelope. WARNING logged — silent recovery would hide real agent misbehavior.
3. **Semantic retry** (`_ask_with_semantic_retry`, max 3 attempts, 0s backoff): distinct from the transport retry inside `FoundryAgentClient._invoke_with_retry`. This handles model-level glitches (bad JSON, missing `answer` after recovery attempt). On retry, prompt is augmented with corrective feedback: `"IMPORTANT: Your previous response was rejected because: {reason}. Return a single JSON object with keys: answer, citations, tool_evidence, confidence..."`. On exhaustion, raises with comma-joined reasons from all attempts.

**Also hardened:**
- `_parse_json_payload`: replaced `.strip("`")` (fragile — strips backticks anywhere) with regex-matched markdown-fence handling. Added `_extract_json_block` fallback that walks brace depth (string-aware) to pull the first JSON object out of prose.
- `_json_answer` (fabric_workflow.py): now accepts dict-typed `answer` directly (paired with the envelope change), plus prose-embedded JSON via `_extract_json_block`.
- Removed noise/PII risk: leftover `logger.error(fabric_response.answer)  # testing` was dumping the entire raw answer at ERROR on every call.

**Constitutional guardrail:** The whole design retries and parses more forgivingly, but NEVER substitutes hardcoded findings/mappings/actions for what the agent should have said. If the agent truly cannot produce a valid response after 3 tries, the pipeline still fails — loudly, with the full reason chain.

**Verification:**
- 8 new tests in `tests/test_fabric_workflow.py`: defaults on missing metadata, inner-payload recovery, dict-typed answer, still-raises when truly empty, retry succeeds on 2nd attempt, retry exhaustion, JSON extraction from prose, dict-through-`_json_answer`.
- 26/26 fabric_workflow tests pass. Existing lakehouse/export/audit/impact tests still pass.
- 17 pre-existing failures in `test_fabric_agents.py` + `test_interpreter.py` are unchanged (verified by baseline stash comparison — they fail identically without Bishop's changes; unrelated to this work).

**Files:** `src/regimpact/agents/foundry_client.py` (+~220 lines net), `src/regimpact/agents/fabric_workflow.py` (+~20 lines net), `tests/test_fabric_workflow.py` (+219 lines).

### 2026-07-17 (extension) — Truncation-aware retry for inner answer JSON

**Follow-up problem:** The `interpret` pipeline aborted again — this time on `gap_analyst` (15 obligations × 14 controls). The Fabric agent returned a well-formed OUTER envelope, but the `answer` FIELD was a JSON string truncated at ~5018 bytes (model hit its output-token ceiling). Downstream `_json_answer` in `fabric_workflow` raised `FabricAgentHarnessError` — a DIFFERENT exception type from a DIFFERENT module — so it escaped the semantic-retry loop entirely. Yesterday's envelope hardening didn't cover this axis because it only validated envelope shape, never inner-string parseability.

**Solution — extend, don't replace, the previous decision:**

1. **New `_validate_inner_answer(response)` helper** (`foundry_client.py`): runs INSIDE `_ask_with_semantic_retry` right after `_fabric_response_from_payload`. Only validates when the stripped `answer` starts with `{` or `[` — prose answers from `executive_qa` / `score_narrator` legitimately are not JSON and MUST pass through untouched (learned this the hard way — first pass broke `test_ask_retries_on_validation_failure` because it accepted `answer="recovered"` prose). For JSON-shaped answers: strict `json.loads` first, then `_extract_json_block` + `json.loads` fallback for prose-wrapped JSON. On failure, raises `FabricDataAgentError` with a structured message: `"inner answer JSON invalid at pos {pos} (answer_bytes={n}, truncated=true|false): {msg}"`.

2. **Truncation heuristic (`_looks_truncated`)**: first non-whitespace char is `{` or `[` AND last char is NOT the matching close bracket → truncated. Complete-but-malformed JSON (balanced braces, invalid contents) is NOT flagged as truncated so the retry loop picks the right nudge. Also, `_extract_json_block`'s unmatched-brace-at-EOF error now includes `"likely truncated"` so both raise sites can trigger the same code path.

3. **Truncation-specific prompt augmentation** (`_ask_with_semantic_retry`): when the failure reason contains `"truncated=true"` or `"likely truncated"`, append a `CRITICAL: TRUNCATED mid-generation...` block on top of the standard envelope reminder. It tells the model to keep `rationale` ≤ 200 chars, cap `source_refs` at 1 per item, drop optional prose fields, and target ≤ 3500 char total. Envelope reminder alone would ask the model to restructure — wrong lever. Concise-mode nudge asks it to shrink.

**Why prompt discipline and not `max_output_tokens`:** The Foundry gateway rejects `max_output_tokens` at the top level for `agent_reference` invocations (see comment in `_OpenAIResponsesAgent.run`). Prompt-side conciseness is the only real control we have over response length.

**Constitutional guardrail still respected:** never closes braces, never fabricates the missing findings/mappings/actions. Truncation-detection is heuristic-only; the fix is always to re-ask the model with better instructions and let it produce a complete, well-formed answer of its own.

**Verification:**
- 5 new tests in `tests/test_fabric_workflow.py`: `_validate_inner_answer` accepts dict / prose-wrapped JSON, `ask` retries on truncated inner answer (asserts concise nudge fires), `ask` retries on malformed-but-complete inner answer (asserts concise nudge does NOT fire), `ask` exhausts retries and message contains `truncated=true` 3×.
- 31/31 fabric_workflow tests pass (excluding 6 pre-existing `defaults_to_deployed` failures that assert hardcoded `agent_version == "3"` while the env now reports "4"/"5" — unrelated to this work; excluded by the same `-k "not defaults_to_deployed"` filter yesterday used).
- Broader smoke: 45/45 in fabric_workflow + impact_scoring + export_audit + smoke + lakehouse.

**Files:** `src/regimpact/agents/foundry_client.py` (+~90 lines: 2 new helpers, extended retry loop, updated `_extract_json_block` error message), `tests/test_fabric_workflow.py` (+~110 lines: 5 new tests + shared payload helpers).

**Relationship to prior decision:** This EXTENDS the 2026-07-17 Fabric response hardening — envelope layer still owns outer-shape validation and recovery; inner-JSON parseability is a new axis that lives beside it in the same retry loop. The previous work made single-response bugs recoverable; this makes single-response truncation recoverable too.



### 2026-07-17 — Team update: split-commit landed (retry loop NOT shipped, Materializer separate)

- Coordinator split a mixed working tree into two commits after user approval ("yes i agree").
- **`1df0f5c`** feat(fabric): ControlMapper empty-with-reason contract + request/response diagnostics — your empty-with-reason contract shape SHIPPED as designed. `tool_evidence` still required for both branches (non-empty mappings AND empty-with-reason). Error unwrap landed. Per-obligation `candidate_control_ids` in request payload landed.
- **Retry loop REVERTED before commit.** Single-attempt `map_controls` is now the norm — the `_map_controls_attempt` parse/validate split you introduced was collapsed back into a single-attempt validate-in-place flow. Decision recorded as "ControlMapper retry mechanism NOT included" in `.squad/decisions.md`. Rationale: latency parity with the 2026-07-17 semantic-retry-removal decision — one agent retrying while four don't creates asymmetric stage latencies. Your gap_analyst empty-tolerance fix (the follow-up to the cross-stage break you flagged) also shipped in the same commit.
- **`6cc6ffd`** feat(fabric): FabricMaterializerAgent + Livy client — brand-new 6th agent in the pipeline (Option A: deterministic Livy, Foundry as boundary supervisor). Not yours, but changes the pipeline shape: `interpret → control_mapper → gap_analyst → remediation_planner → materializer → ...`. Read-only from your perspective right now, but if you touch pipeline glue expect a new stage between OneLake upload and any downstream Fabric queries.
- 76 passed / 7 deselected. The 6 v3/v4 drift tests + 1 network test remain deselected — tracked separately.
- Your gap_analyst empty-tolerance rewrite note for Hicks (test needing `FabricGapAnalystAgent` stub) is still open — landed contract, test rewrite not yet done. Not blocking.