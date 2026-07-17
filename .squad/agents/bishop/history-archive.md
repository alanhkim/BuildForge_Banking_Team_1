# Bishop History Archive

Older `history.md` entries archived by Scribe on 2026-07-17 to keep the active history under the 12KB threshold. Preserved verbatim for reference.

---

### 2026-07-06: Implemented Regulation Interpreter Core (Tasks 1-4)

**Note (2026-07-17):** This work is SUPERSEDED by the Foundry/Fabric-first architecture direction adopted 2026-07-08 (see `.squad/decisions.md` §1, §3). Deterministic offline fallback described below is no longer the active agent behavior. Preserved here for historical context on the original contracts and validation surface, which still inform current `contracts.py` structure.

**Architecture:**
- Created `src/regimpact/contracts.py` with typed request/response contracts using dataclasses
- Created `src/regimpact/catalog.py` with deterministic DORA fixture (REG-DORA, CHG-DORA, OBL-DORA-01)
- Updated `src/regimpact/agents/interpreter.py` to implement deterministic interpretation with catalog fallback
- Updated `src/regimpact/models.py` to expand Obligation dataclass with all required fields

**Key Patterns:**
- Explicit validation exceptions (InvalidThemeError, InvalidMaturityError, MissingSourceRefsError, InvalidObligationError)
- Known themes validation set: ICT_RESILIENCE, DATA_PROTECTION, OPERATIONAL_RESILIENCE, CYBER_SECURITY, THIRD_PARTY_RISK, INCIDENT_REPORTING
- Maturity range validation (1-5)
- Required source_refs for traceability
- Criticality validation (Critical, High, Medium, Low)
- No hallucination: unknown regulations return empty obligations with notes, not invented data

**Test Coverage:**
- 22 tests covering contracts, catalog, fallback, schema validation, and network-free operation
- Tests verify: valid/invalid obligations, theme validation, maturity range, source refs, criticality, DORA fixture, unknown regulation handling
- All tests pass with ruff linting clean

**API Contract:**
- `InterpretRequest`: regulation_id, change_id, name, title, optional source_text/source_path, offline_mode flag
- `InterpretResponse`: regulation_id, change_id, obligations list, mode (deterministic-fallback), notes
- `Obligation`: id, change_id, theme, summary, target_maturity, criticality, affected_data_domain_ids, source_refs, notes

**Boundaries Preserved (at time of work):**
- No Foundry Hosted Agent wrapper implemented (ready for future wrapper, not blocking)
- No API-key authentication
- No Semantic Kernel usage
- Deterministic, network-free operation by default
- Microsoft Agent Framework direction preserved for eventual wrapping

**File Paths:**
- `src/regimpact/contracts.py` - typed contracts and validation
- `src/regimpact/catalog.py` - deterministic catalog fixtures
- `src/regimpact/agents/interpreter.py` - interpreter agent with offline fallback
- `tests/test_interpreter.py` - comprehensive test suite

## Team Coordination (2026-07-06)

- Hicks validated malformed input handling and enhanced field validation with `.strip()` checks, expanding test coverage from 22 to 29 tests (all passing)
- Ripley approved core architecture, verified all hard constraints (no Foundry wrapper, no Semantic Kernel, no API-key auth, offline-deterministic behavior confirmed)
- Coordinator verified ruff and pytest pass; team proceeding to CLI wireup (task 5)


---

## 2026-07-17 (archived by Scribe 2026-07-17T19:09:49Z) — Fabric Data Agent response layer hardened

Full detail preserved verbatim; summarised entry retained in active history. Corresponding decision: `.squad/decisions.md` §4.

**Problem:** `interpret` was aborting mid-pipeline with `Fabric Data Agent response missing required field(s): answer` — a single misbehaved response from Fabric killed the whole run, forcing operators to re-execute manually.

**Solution — three-layer resilience, no offline agent-behavior fallback (constitution respected):**

1. **Lenient envelope defaults** (`_fabric_response_from_payload` in `foundry_client.py`): only `answer` is truly required. Missing `citations`/`tool_evidence`/`confidence` default to `[]`/`[]`/`"low"` with a WARNING log listing what was defaulted, agent name/version, and observed keys. Also handles `answer` being a dict/list instead of a string (JSON-encodes it).
2. **Inner-payload recovery** (`_RECOVERABLE_INNER_KEYS` frozenset): when the envelope lacks `answer` but the top-level JSON has any of `{mappings, findings, actions, narrative, change_id, impacted_entities}`, treat the whole payload as the inner answer and synthesize the envelope. WARNING logged — silent recovery would hide real agent misbehavior.
3. **Semantic retry** (`_ask_with_semantic_retry`, max 3 attempts, 0s backoff): distinct from the transport retry inside `FoundryAgentClient._invoke_with_retry`. This handles model-level glitches (bad JSON, missing `answer` after recovery attempt). On retry, prompt is augmented with corrective feedback. On exhaustion, raises with comma-joined reasons from all attempts.

**Also hardened:** `_parse_json_payload` regex-based markdown-fence handling + `_extract_json_block` fallback walking brace depth. `_json_answer` (fabric_workflow.py) accepts dict-typed `answer` directly plus prose-embedded JSON.

**Constitutional guardrail:** retries and parses more forgivingly, NEVER substitutes hardcoded findings/mappings/actions.

**Verification:** 8 new tests; 26/26 fabric_workflow tests pass. **Note:** the 3-attempt semantic retry loop was later REMOVED for latency in commit `f3d6ab4` (2026-07-17 fail-fast decision). The lenient parser stays; only the outer retry was removed.

**Files:** `src/regimpact/agents/foundry_client.py` (+~220 lines net), `src/regimpact/agents/fabric_workflow.py` (+~20 lines net), `tests/test_fabric_workflow.py` (+219 lines).

## 2026-07-17 (extension, archived by Scribe 2026-07-17T19:09:49Z) — Truncation-aware retry for inner answer JSON

Full detail preserved verbatim; corresponding decision: `.squad/decisions.md` §5. **Note:** superseded by the fail-fast decision (`f3d6ab4`) — the semantic retry loop this extension enhanced was later removed. The `_validate_inner_answer` helper and `_looks_truncated` heuristic still exist for legibility of failure messages (`truncated=true/false` diagnostic remains in operator output).

**Follow-up problem:** `interpret` aborted on `gap_analyst` (15 obligations × 14 controls). The Fabric agent returned a well-formed OUTER envelope, but the `answer` FIELD was a JSON string truncated at ~5018 bytes (model hit its output-token ceiling). Downstream `_json_answer` in `fabric_workflow` raised `FabricAgentHarnessError` — a DIFFERENT exception type from a DIFFERENT module — so it escaped the semantic-retry loop entirely.

**Solution — extend, don't replace:**

1. **New `_validate_inner_answer(response)` helper** (`foundry_client.py`): runs INSIDE `_ask_with_semantic_retry`. Only validates when the stripped `answer` starts with `{` or `[` — prose answers from `executive_qa` / `score_narrator` legitimately are not JSON and MUST pass through untouched. For JSON-shaped answers: strict `json.loads` first, then `_extract_json_block` fallback. On failure, raises `FabricDataAgentError` with structured message: `"inner answer JSON invalid at pos {pos} (answer_bytes={n}, truncated=true|false): {msg}"`.
2. **Truncation heuristic (`_looks_truncated`)**: first non-whitespace char is `{` or `[` AND last char is NOT the matching close bracket → truncated. Complete-but-malformed JSON is NOT flagged as truncated so the retry loop picks the right nudge.
3. **Truncation-specific prompt augmentation**: `CRITICAL: TRUNCATED mid-generation...` block asks the model to keep `rationale` ≤ 200 chars, cap `source_refs` at 1 per item, drop optional prose fields, target ≤ 3500 char total. Envelope reminder would ask restructure — wrong lever. Concise-mode asks it to shrink.

**Why prompt discipline and not `max_output_tokens`:** Foundry gateway rejects `max_output_tokens` at the top level for `agent_reference` invocations.

**Constitutional guardrail:** never closes braces, never fabricates missing findings/mappings/actions. Truncation-detection is heuristic-only.

**Verification:** 5 new tests. 31/31 fabric_workflow tests pass. Broader smoke: 45/45.

**Files:** `src/regimpact/agents/foundry_client.py` (+~90 lines), `tests/test_fabric_workflow.py` (+~110 lines).

## 2026-07-17 (archived by Scribe 2026-07-17T19:09:49Z) — Control Mapper hardening: Fixes 1/2/3/6

Full detail preserved verbatim. Corresponding decision: `.squad/decisions.md` §"ControlMapper contract accepts documented empty mappings". **Note:** Fix 6 (retry wrapper) was REVERTED before commit for latency parity — see `.squad/decisions.md` §"ControlMapper retry mechanism NOT included". Fixes 1/2/3 shipped in `1df0f5c`. The `_map_controls_attempt` parse/validate split was collapsed back into a single-attempt `map_controls`. Empty-with-reason contract shape + `tool_evidence` requirement for both branches survived and shipped.

**Delivered fixes 1/2/3/6 from Hamza''s approved plan (as originally implemented):**

- **Fix 1 (`fabric_workflow.py::_validated`)**: `FabricAgentHarnessError` now embeds the underlying `ValidationError` detail plus a 500-char raw-answer snippet in the message. CLI operators no longer need debug logs to see WHY validation failed.
- **Fix 2 (`fabric_workflow.py::_ask` + new `_payload_cardinalities` helper)**: Every Fabric agent request now logs list-field cardinalities (obligation_ids_count, tool_evidence_count, candidate_controls_count, etc.) at INFO. Payload body stays at DEBUG. Same log line covers all agents — no per-agent bespoke fields.
- **Fix 3 (`contracts.py::ControlMappingResponse`)**: Added `reason: str | None = None` field. `validate()` accepts empty mappings ONLY when `reason.strip()` is non-empty; otherwise raises `ValidationError("mappings is required (or provide non-empty reason)")`. Both branches still require `tool_evidence` (no unfounded empty responses).
- **Fix 6 (`fabric_workflow.py::map_controls`) — LATER REVERTED**: Refactored into `map_controls` + private `_map_controls_attempt`. On empty-without-reason, retries once with `retry_attempt=2` added to the payload. Both attempts log at INFO with `attempt=`, `mappings=`, `reason_present=`. Reverted for latency parity — see decisions.md.
- **Pipeline empty-with-reason handling (`pipeline.py`)**: After control_mapper stage, if `mappings == []` and `reason` is present, log at WARNING with the reason and continue. Downstream stages will see empty inputs.

**Gotchas / decisions on ambiguous spots:**

1. **Retry needed to split `map_controls`** into a "parse but don''t validate" helper (`_map_controls_attempt`) + a validate-once caller. Reason: with Fix 3 in place, `_validated` would fail-fast on empty-without-reason before the retry could fire. Splitting lets us inspect the parsed shape and decide whether to retry BEFORE contract validation. Kept scoped to control_mapper as the plan requested.
2. **Kept `tool_evidence` required in the empty-with-reason branch of `ControlMappingResponse.validate()`.** The plan didn''t specify — chose to preserve grounding so an agent can''t return `{"mappings": [], "reason": "..."}` with zero evidence.
3. **Pipeline `continue vs raise` ambiguity**: Plan literally says "continue the pipeline (not raise)". Log WARNING with reason at the CM stage and let control flow proceed. Note: `GapAnalysisRequest.validate()` required non-empty `control_ids` at the time — cross-stage break flagged (later closed by GapAnalyst empty-tolerance in same-day follow-up).
4. **No `to_schema()` / `model_json_schema()` in the repo** — grep returned no matches, so no JSON Schema export to update.
5. **`_payload_cardinalities`** is a module-level helper (not a method) so it can be reused by any future agent adapter without needing a harness instance.

**Test result:** 43 passed, 6 deselected (the requested `not defaults_to_deployed` filter). Ran with `pytest tests/test_fabric_workflow.py tests/test_lakehouse.py tests/test_export_audit.py tests/test_impact_scoring.py tests/test_smoke.py -q -k "not defaults_to_deployed" --tb=short`.

**Files touched:**
- `src/regimpact/contracts.py` — `ControlMappingResponse` gets `reason`, validation loosened for documented empty case.
- `src/regimpact/agents/fabric_workflow.py` — Fix 1 (`_validated`), Fix 2 (`_ask` + `_payload_cardinalities`), Fix 6 (`map_controls` + `_map_controls_attempt` — later reverted).
- `src/regimpact/agents/pipeline.py` — empty-with-reason WARNING log in control_mapper stage.