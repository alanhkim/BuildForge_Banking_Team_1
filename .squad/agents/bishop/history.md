# Bishop History

## Core Context
- Project: forge, a Python regulatory impact framework for assessing regulatory change impact and scoring compliance using synthetic digital twin data.
- User: briandenicola.
- Current focus: portable deterministic Regulation Interpreter core tasks 1-4: contracts, DORA catalog fixture, deterministic fallback, schema validation.

## Learnings

### 2026-07-06: Implemented Regulation Interpreter Core (Tasks 1-4)

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

**Boundaries Preserved:**
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

## Team Coordination

**2026-07-06 Cross-Agent Integration:**
- Hicks validated malformed input handling and enhanced field validation with `.strip()` checks, expanding test coverage from 22 to 29 tests (all passing)
- Ripley approved core architecture, verified all hard constraints (no Foundry wrapper, no Semantic Kernel, no API-key auth, offline-deterministic behavior confirmed)
- Coordinator verified ruff and pytest pass; team proceeding to CLI wireup (task 5)

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

