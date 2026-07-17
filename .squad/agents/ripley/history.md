# Ripley History

## Core Context
- Project: forge, a Python regulatory impact framework for assessing regulatory change impact and scoring compliance using synthetic digital twin data.
- User: briandenicola.
- Current focus: portable deterministic Regulation Interpreter core tasks 1-4.

## Learnings

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
