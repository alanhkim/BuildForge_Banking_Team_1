# Hicks History

## Core Context
- Project: forge, a Python regulatory impact framework for assessing regulatory change impact and scoring compliance using synthetic digital twin data.
- User: briandenicola.
- Current focus: focused tests and quality gates for portable deterministic Regulation Interpreter core tasks 1-4.

## Learnings

### 2026-07-20 — Team update: OneLake env-var GUID validation — 4 new tests in `test_lakehouse.py`
- Bishop landed a boundary-hardening fix in `src/regimpact/lakehouse.py::export_to_lakehouse` — both `workspace_id` and `lakehouse_id` are stripped and `uuid.UUID()`-validated at entry, with canonical-form check to reject braced / urn / no-dash variants. Malformed values now raise `LakehouseNotConfiguredError` (soft yellow skip) instead of the opaque `LakehouseWriteError` that used to surface `FriendlyNameSupportDisabled` at upload time.
- **Test coverage you should know:** `tests/test_lakehouse.py` 10/10 green. Added 4 tests: (1) trailing-newline workspace_id → strips and SDK/ABFSS URL see clean GUID; (2) `"my-workspace-name"` (display name) → `LakehouseNotConfiguredError` naming the env var and the offending value; (3) `"   "` (whitespace only) → strips to empty → `LakehouseNotConfiguredError` on the "not set" branch; (4) `"'11111111-1111-1111-1111-111111111111'"` (shell-quoted GUID) → strips quotes, succeeds. Existing tests refactored to use `_WS_GUID` / `_LH_GUID` canonical fixture constants — semantic coverage unchanged.
- **If you add new coverage for Fabric ID handling:** follow the same fixture-constants + boundary-error-class pattern. `LakehouseNotConfiguredError` = config problems (assert message names env var); `LakehouseWriteError` = transient / SDK failures (assert on wrapped cause). Do not conflate the two error classes in a single test — the semantic contract in `decisions.md` §0 / 2026-07-20 is explicit about them.
- **Reusable pattern captured** in `.squad/skills/fabric-resource-id-validation/SKILL.md` for future Fabric GUID env vars (workspace, lakehouse, item, capacity).

### 2026-07-17 — Team update: retry tests replaced with fail-fast tests
- The 5 retry-behavior tests in `tests/test_fabric_workflow.py` (added when the semantic retry landed in `412d695`) were replaced with 3 fail-fast equivalents in commit `f3d6ab4` on hamza-dev.
- The lenient-parsing tests (metadata defaults, inner-payload recovery, markdown fence, prose-embedded JSON) are unchanged — those still assert single-pass parsing behavior.
- 43/43 pass across `test_fabric_workflow`, `test_impact_scoring`, `test_export_audit`, `test_smoke`, `test_lakehouse`. Six pre-existing `defaults_to_deployed` failures are unrelated and were excluded via `-k`.
- If you add new fail-fast coverage: assert that a single bad response raises immediately (no re-invocation of the transport mock).

### Interpreter Validation (2026-07-06)
**Test Coverage:**
- Validated core tasks 1-4: interpreter-contracts, interpreter-catalog-fixture, interpreter-fallback, interpreter-schema-validation
- Added 6 focused tests for malformed/empty input handling (TestMalformedInput class)
- Enhanced contracts.py to reject whitespace-only fields with `.strip()` validation
- 29 tests total, all passing with 100% success rate

**Quality Gates:**
- ✓ `python -m ruff check .` - All checks passed
- ✓ `python -m pytest` - 29 passed in 0.05s

**Key Test Coverage Areas:**
- DORA fixture interpretation: catalog lookup, obligation structure validation
- Malformed input: empty/whitespace regulation_id, change_id, name, title
- Schema validation: invalid themes, out-of-range maturity (1-5), missing source_refs, invalid criticality
- Network-free operation: fully offline deterministic interpretation

**Key Files:**
- `tests/test_interpreter.py` (expanded from 22 to 29 tests)
- `src/regimpact/contracts.py` (enhanced field validation)
- `src/regimpact/catalog.py` (DORA fixture)
- `src/regimpact/agents/interpreter.py` (deterministic fallback)

**Validation Behavior:**
- Empty and whitespace-only fields are explicitly rejected at request validation
- Unknown regulations return empty obligations with helpful notes, no hallucination
- All obligations validate theme, maturity range, criticality, and source_refs before returning
- Explicit ValidationError exceptions with actionable messages, no silent fallbacks

## Team Coordination

**2026-07-06 Cross-Agent Integration:**
- Bishop implemented core Regulation Interpreter with contracts, catalog fixture, and deterministic fallback (22 tests)
- Enhanced validation via malformed input tests and `.strip()` checks, approved by Ripley for all hard constraints compliance
- All 29 tests passing, ruff clean; team proceeding to CLI wireup

## Team Updates

### 2026-07-17 — OneLake writeback wired into `interpret`
Lambert landed opt-in OneLake writeback for the `interpret` CLI command. Local Parquet under `output/tables/` is still the source of truth; the Fabric upload is gated on `FABRIC_WORKSPACE_ID` + `FABRIC_LAKEHOUSE_ID` and fails soft (non-fatal). 5 new tests in `tests/test_lakehouse.py` (all green); `tests/test_export_audit.py` still passes. To enable: set both env vars and run `pip install .[fabric]` (new optional extra). See `.squad/decisions.md` §0.

### 2026-07-17 — Fabric response layer hardened
2026-07-17 — Bishop hardened the Fabric Data Agent response layer. Envelope missing `citations`/`tool_evidence`/`confidence` now defaults with a warning instead of aborting. Semantic retry (3 attempts) sits above transport retry. Inner-payload recovery treats known inner-shape JSON as the answer when the envelope is missing. See decisions.md.

### 2026-07-17 — Truncated inner-answer JSON now retryable
2026-07-17 — Bishop extended the Fabric semantic-retry loop to catch truncated inner-answer JSON — a follow-on to §5. Truncation (unclosed brace/bracket at EOF) triggers a concise-mode retry prompt asking the model to shorten rationales, drop optional fields, and cap answer size. Prose-answer agents (executive_qa, score_narrator) are exempted from JSON validation. See decisions.md.

### 2026-07-17 — ControlMapper validation contract change coming (heads-up)
ControlMapper validation contract change coming (empty mappings + non-empty reason accepted; empty + no reason still rejected). New tests needed: empty+reason accepted, empty+no-reason rejected, retry fires exactly once on the failing path, error message surfaces underlying `ValidationError` reason, request payload logged with obligation/evidence counts. Wait for Bishop + Lambert to finish implementation before writing. See `.squad/log/2026-07-17T16-52-25Z-control-mapper-diagnosis.md` and the PROPOSED decision at the bottom of `.squad/decisions.md`.


### 2026-07-17 — ControlMapper hardening tests landed (13 new tests, all green)
Verified Bishop's Fixes 1/2/3/6 in `tests/test_fabric_workflow.py`. 11 new test functions (13 test cases with parametrization), all passing; no production code touched.

**Contract tests (`ControlMappingResponse.validate()`):**
- empty mappings + non-empty reason + tool_evidence → accepted
- empty mappings + missing/empty/whitespace reason → `MissingCitationError` (parametrized × 3)
- empty mappings + reason present but tool_evidence empty → `MissingCitationError` (grounding stays non-negotiable)
- non-empty mappings with no reason → accepted (reason optional when mappings present)

**Harness error wrapping (`_validated`):**
- `ValidationError` re-raised as `FabricAgentHarnessError` with the original reason in the message
- error message includes an `answer snippet` slice for diagnosis

**INFO log cardinality (`_ask`):**
- `obligations_count=N controls_count=M` (list-valued keys only, sorted) present in the INFO record; scalar fields excluded; `change_id` NOT logged (secret discipline)

**Retry behaviour (`map_controls`):**
- empty + no reason → retries once with `retry_attempt: 2` and succeeds on second attempt
- empty + reason → does NOT retry (single call, empty result returned upstream)
- retry exhausted (both attempts empty w/o reason) → `FabricAgentHarnessError`

**Pipeline stage warning:**
- `FabricControlMapperAgent` stage emits WARNING with `change_id`, `obligations`, `reason` when downstream sees empty-with-reason (verified via caplog on `regimpact.agents.pipeline` at WARNING level; downstream `gap_analyst` raises `ValidationError("control_ids is required")` — documented cross-stage break, out of scope for control_mapper hardening)

**Run**: 42 passed / 6 deselected (`defaults_to_deployed` — pre-existing agent_version drift 3→4/5, unrelated) in `tests/test_fabric_workflow.py`; broader Bishop-verification set (fabric_workflow + lakehouse + export_audit + impact_scoring + smoke) 56 passed / 6 deselected. Zero regressions.

**No real bugs uncovered** — the hardening holds under all specified adversarial inputs. Documented cross-stage break at `pipeline.py:468` (`FabricGapAnalystAgent` rejects empty `control_ids`) is Bishop's known follow-up, not a regression.

---

## 2026-07-17 — Gap Analyst empty-tolerance test rewrite + contract tests

Requested by Hamza, following Bishop's `bishop-gap-analyst-empty-tolerance.md` decision (GapAnalysisRequest now accepts empty control_ids when paired with a non-empty 
eason, mirroring the ControlMappingResponse empty-with-reason contract; pipeline forwards cm_response.reason and emits a second WARNING log at `pipeline.py:474`).

### Rewrote (1)
- `test_control_mapper_pipeline_stage_logs_warning_on_empty_with_reason` (`tests/test_fabric_workflow.py`): removed the old `pytest.raises(ValidationError, match=""control_ids"")` expectation. Added `_StubGapAnalystEmpty` and `_StubScoreNarrator` (drop-ins for `FabricGapAnalystAgent` / `FabricScoreNarratorAgent`). Wrapped monkeypatched agent classes as `lambda: shared_stub` so captured calls survive pipeline default-construction. Added `_remediation_should_not_be_called` sentinel to lock in the `if gap_ids:` guard for `FabricRemediationPlannerAgent`. Now asserts:
  - Pipeline returns a report without raising.
  - Two WARNING records fire: `control_mapper returned empty mappings with reason` AND `Fabric stage propagating empty control_ids stage=gap_analyst`.
  - Both records contain `Shortlist exhausted for CHG-CM-STAGE-TEST.`.
  - Captured `GapAnalysisRequest` has `control_ids == []` and `reason == "Shortlist exhausted for CHG-CM-STAGE-TEST."` — folded in Bishop's Test 5 (reason-propagation integration assertion) rather than duplicating.

### Added (4 new tests, 3 parametrizations → 6 net invocations)
Grouped as `GapAnalysisRequest contract` block, sibling to the existing `ControlMappingResponse contract` block:
1. `test_gap_analysis_request_empty_control_ids_with_reason_accepted` — happy path for the new branch.
2. `test_gap_analysis_request_empty_control_ids_without_reason_rejected` — parametrised over `[None, "", "   "]` (matches Bishop's whitespace-reason pattern in `ControlMappingResponse`). Asserts `ValidationError` matching `"control_ids is required"`.
3. `test_gap_analysis_request_nonempty_control_ids_no_reason_still_valid` — regression on normal happy path.
4. `test_gap_analysis_request_empty_obligation_ids_always_rejected` — locks in Bishop's decision that `obligation_ids` is not a legit-empty artefact even with a valid reason + populated control_ids.

Imports updated: added `GapAnalysisResponse` and `ScoreNarrationResponse` (previously unused in this file).

### Runs
- `pytest tests/test_fabric_workflow.py -q --tb=short` → **48 passed / 6 failed (pre-existing `defaults_to_deployed` — agent_version drift 3→4/5, unrelated to this work)**.
- `pytest tests/test_fabric_workflow.py -q -k "not defaults_to_deployed"` → **48 passed / 6 deselected**.
- Broader regression (`tests/test_fabric_workflow.py tests/test_lakehouse.py tests/test_export_audit.py tests/test_impact_scoring.py tests/test_smoke.py -k "not defaults_to_deployed"`) → **62 passed / 6 deselected**. Zero regressions.

### Learnings
- **`monkeypatch.setattr` with `lambda: instance`** is the clean way to force the pipeline's `FabricXxxAgent()` default-construct call to return a shared stub. Assigning the class directly (as the pre-existing `_StubControlMapperEmptyWithReason` did) means every construction creates a fresh instance — fine when you only need behaviour, but useless if the test wants to inspect `calls` after the run. Merged both patterns in the rewritten test: control_mapper uses a shared instance, gap_analyst uses a shared instance, score_narrator uses a shared instance.
- **Test 5 as folded assertion, not standalone test**: Bishop's spec offered a choice between a light unit test on a request-building helper or an assertion on the stub call from Task 1. The pipeline builds `GapAnalysisRequest` inline (no extractable helper), so folding into Task 1 was correct — a separate test would have needed identical scaffolding for a single extra assertion.
- **Remediation stage sentinel** (`_remediation_should_not_be_called`): asserts the `if gap_ids:` guard at `pipeline.py:522` holds. Without it, a regression that dropped the guard would silently invoke the real `FabricRemediationPlannerAgent` and try to hit Foundry — the test would fail with a confusing config/network error instead of a clear "guard regressed" signal.
- **No real bugs uncovered.** Bishop's fix is symmetric with the `ControlMappingResponse` pattern and the pipeline propagation logic is clean. The cross-stage break flagged in the previous session is now fully closed.
