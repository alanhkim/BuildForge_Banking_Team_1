# Ripley History

## Core Context
- Project: forge, a Python regulatory impact framework for assessing regulatory change impact and scoring compliance using synthetic digital twin data.
- User: briandenicola.
- Current focus: portable deterministic Regulation Interpreter core tasks 1-4.

## Learnings

### 2026-07-20 — Team update: OneLake env-var GUIDs validated at the `lakehouse.py` boundary
- Bishop landed a boundary-hardening fix in `src/regimpact/lakehouse.py::export_to_lakehouse`. Both `workspace_id` and `lakehouse_id` are now stripped (whitespace → one layer of `'`/`"` quotes → whitespace) and validated via `uuid.UUID()` + canonical-form check at the top of the function. Cleaned values thread into both the ADLS SDK calls and the returned ABFSS URLs; `Settings` is never mutated.
- **Failure-class semantics you should internalise:** malformed / non-canonical / empty-after-strip now raise `LakehouseNotConfiguredError` (soft yellow skip per decisions.md §0), **not** `LakehouseWriteError`. Rationale: a malformed env var fails every run until fixed — that's configuration, not a transient upload failure. Any review that touches Fabric ID handling should preserve this mapping: config errors → soft skip; transient / capacity / auth failures → hard write error.
- **Reusable pattern captured:** `.squad/skills/fabric-resource-id-validation/SKILL.md`. Applies to any Fabric/OneLake ID env var (workspace, lakehouse, item, capacity). If you audit code that consumes a Fabric GUID from outside the process, apply this pattern — the `FriendlyNameSupportDisabled` server error is a strong signal it's missing.
- **`fabric_livy_client.py` is deliberately untouched** in this pass. Flagged as a separate hardening opportunity for Lambert. Worth a quick review on your next Fabric pass to confirm Livy either revalidates or is unaffected by malformed values.
- Tests: `tests/test_lakehouse.py` 10/10 green (4 new: trailing-newline strip, display-name rejection, whitespace-only strip, shell-quoted GUID accept). Commit lands on `hamza-dev`.

### 2026-07-17 — Team update: empty-with-reason pattern generalised across pipeline contracts
- Team hardened the Fabric pipeline against legitimately-empty agent responses **without adding offline fallbacks** — constitution-compliant throughout.
- **Pattern established:** optional `reason: str | None` field on request/response contracts; validation accepts empty result iff non-empty `reason.strip()`; pipeline logs WARNING and continues; downstream stages must be tolerant (or explicitly guarded) of the empty payload.
- Applied to `ControlMappingResponse` (output side) and `GapAnalysisRequest` (input side, mirroring the same shape). Both shipped in `1df0f5c`. Downstream stages (`remediation_planner`, `score_narrator`) verified already tolerant — no additional changes.
- **Grounding stays non-negotiable:** empty-with-reason branches still require `tool_evidence`. An agent returning `{"mappings": [], "reason": "..."}` with no evidence still fails validation. Accepting an ungrounded empty response would be a soft form of the offline fallback the Constitution rules out.
- **Foundry portal prompt update still pending** — Hamza to update `RegImpactControlMapper` per `docs/foundry-agent-prompts/control-mapper.md` (Lambert's Deliverable A) and pin `FOUNDRY_CONTROL_MAPPER_AGENT_VERSION`. Until that lands, the harness is ready but the deployed v4 agent will still under-report evidence and never emit `reason`, so the empty-with-reason path won't activate in production.
- **Latency posture preserved:** no retries added. The 2026-07-17 fail-fast decision (`f3d6ab4`) stands; empty-with-reason handles the documented no-op case without needing a resilience layer.

### 2026-07-17 — Team update: semantic retry removed from Fabric client
- Coordinator direct-edit (per Hamza's explicit request) removed the 3-attempt semantic retry from `FabricDataAgentClient.ask` for latency. Commit `f3d6ab4` on `hamza-dev`, message `perf(fabric): remove semantic retry to cut interpret latency`.
- **Architectural tradeoff you should know:** we traded resilience for speed. One malformed agent response now aborts the whole `interpret` pipeline. The lenient parser (`ccd35d8`) still runs on every response and recovers most sad paths on the first attempt, so the practical impact is bounded.
- **Constitutional check passed:** no offline / hardcoded agent behavior introduced. Agent execution still flows entirely through Microsoft Foundry / Fabric per the project constitution.
- Reversal is documented in `.squad/decisions.md` (2026-07-17 entry) — the retry code was deleted, not disabled, so restoring it means re-adding `_ask_with_semantic_retry` from commit `412d695`.
- If future latency concerns surface, review whether transport-level retry in `FoundryAgentClient._invoke_with_retry` also needs tuning before adding any new retry layer.

### 2026-07-06 10:28 — Regulation Interpreter Core Review (Tasks 1-4)

**Review Scope:** Portable deterministic Regulation Interpreter for tasks 1-4 only: contracts, catalog fixture, fallback, schema validation.

**Hard Constraints Verification:**

✅ **No Hosted Agent wrapper:** Confirmed. No `foundry_interpreter.py`, `HostedAgent`, or `foundry_agent` references found in tasks 1-4 implementation. Task 4 (interpreter-foundry-adapter) is properly deferred.

✅ **No Semantic Kernel:** Confirmed. Zero references to `Semantic Kernel`, `SemanticKernel`, or `semantic_kernel` in the implementation.

✅ **No API-key auth/config/docs:** Confirmed. No references to `api_key`, `API_KEY`, or apikey patterns. System prompt in docs explicitly states: "Never request, emit, or rely on API keys."

✅ **Offline deterministic behavior:** Verified. `InterpreterAgent` uses `CatalogFixture` for deterministic DORA fallback. All 28 tests pass, including `TestNetworkFreeOperation::test_no_network_required`.

✅ **Explicit validation:** Verified. All request and response objects implement `.validate()` methods with explicit field checks. No data escapes the service boundary without schema validation.

✅ **No broad catches/silent fallbacks:** Verified. No `except:`, `except Exception`, or bare exception handlers found. All exceptions are explicitly typed: `ValidationError`, `InvalidObligationError`, `InvalidThemeError`, `InvalidMaturityError`, `MissingSourceRefsError`.

**Test Coverage:**
- 28 tests covering contracts, catalog, fallback, schema validation, malformed input, and network-free operation
- All tests pass with 100% success rate
- Tests verify rejection of invalid themes, out-of-range maturity, missing source refs, empty fields, and whitespace-only fields

**Architecture Compliance:**
- Proper separation: `contracts.py` (typed boundaries) → `catalog.py` (fixtures) → `agents/interpreter.py` (core logic)
- Deterministic fallback path operational for DORA regulation
- Unknown regulations return empty obligations with explanatory notes, no hallucination
- Source refs required and enforced for traceability

**Findings:** No blockers identified. Implementation adheres to all hard constraints and architectural boundaries.

**Decision:** APPROVED for tasks 1-4. Portable deterministic core is ready. Foundry adapter (task 4) can proceed independently without blocking local/demo execution.

## Team Coordination

**2026-07-06 Cross-Agent Integration:**
- Bishop's implementation: clean separation (contracts → catalog → interpreter), deterministic DORA path operational, no hallucination
- Hicks' validation enhancements: malformed input tests, `.strip()` checks, expanded to 29 tests (all passing)
- Decisions merged into `.squad/decisions.md`; team proceeding to CLI wireup (task 5) and Control Mapper deterministic path

## Team Updates

### 2026-07-17 — OneLake writeback wired into `interpret`
Lambert landed opt-in OneLake writeback for the `interpret` CLI command. Local Parquet under `output/tables/` is still the source of truth; the Fabric upload is gated on `FABRIC_WORKSPACE_ID` + `FABRIC_LAKEHOUSE_ID` and fails soft (non-fatal). To enable: set both env vars and run `pip install .[fabric]` (new optional extra). See `.squad/decisions.md` §0.

### 2026-07-17 — Fabric response layer hardened
2026-07-17 — Bishop hardened the Fabric Data Agent response layer. Envelope missing `citations`/`tool_evidence`/`confidence` now defaults with a warning instead of aborting. Semantic retry (3 attempts) sits above transport retry. Inner-payload recovery treats known inner-shape JSON as the answer when the envelope is missing. See decisions.md.

### 2026-07-17 — Truncated inner-answer JSON now retryable
2026-07-17 — Bishop extended the Fabric semantic-retry loop to catch truncated inner-answer JSON — a follow-on to §5. Truncation (unclosed brace/bracket at EOF) triggers a concise-mode retry prompt asking the model to shorten rationales, drop optional fields, and cap answer size. Prose-answer agents (executive_qa, score_narrator) are exempted from JSON validation. See decisions.md.


### 2026-07-17 — Team update: split-commit landed (ControlMapper contract + Materializer agent)

- Coordinator split a mixed working tree into two commits after user approval.
- **`1df0f5c`** — ControlMapper contract change SHIPPED: `mappings=[]` is now a first-class outcome when accompanied by a non-empty `reason` (and `tool_evidence`). `GapAnalysisRequest.reason` propagates the reason through the pipeline. Read-only for you, but the contract shift matters — empty-with-reason no longer means "pipeline aborted" but "documented no-op report". If you review scoring/report semantics, factor in that a change_id can now legitimately reach `score_narrator` with zero gap findings.
- **`6cc6ffd`** — NEW: `FabricMaterializerAgent` + Livy client. Pipeline shape is now 6 agents: `interpret → control_mapper → gap_analyst → remediation_planner → materializer → ...`. Option A (deterministic Livy, Foundry as boundary supervisor) chosen over Option B (LLM generates PySpark) and Option C (SDK-only, no agent). Rationale recorded in `.squad/decisions.md`: transformation is pure ETL versioned in code; Foundry sits at the boundary producing `tool_evidence` for each materialized table so the audit story stays uniform across all six stages. Matches `01_load_lakehouse.ipynb` sequencing exactly.
- Retry mechanism explicitly NOT shipped for ControlMapper — decision recorded in `.squad/decisions.md`. Single-attempt norm holds across all Fabric agents; upstream Foundry portal version-pin (`FOUNDRY_CONTROL_MAPPER_AGENT_VERSION`) is the tracked remediation for prompt drift.
- 76 passed / 7 deselected.