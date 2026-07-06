# Squad Decisions

## Active Decisions

### 1. Regulation Interpreter Core Implementation

**Date:** 2026-07-06  
**Author:** Bishop (Python Core Dev)  
**Status:** Implemented  

**Architecture:** Portable, deterministic, network-free Regulation Interpreter core with typed contracts, catalog fixture, offline fallback, and schema validation.

- **Typed Contracts** (`src/regimpact/contracts.py`): InterpretRequest/Response dataclasses with validation methods, explicit exception hierarchy, known themes validation set, maturity range (1-5), source refs enforcement.
- **Catalog Fixture** (`src/regimpact/catalog.py`): Deterministic DORA fixture data with CatalogFixture class for offline fallback (REG-DORA, CHG-DORA, OBL-DORA-01).
- **Deterministic Interpretation** (`src/regimpact/agents/interpreter.py`): InterpreterAgent.interpret() method using contracts, catalog-based fallback for known regulations, empty obligations for unknowns (no hallucination).
- **Schema Validation**: Required IDs, change_id, theme, summary, criticality, source_refs; theme must be in KNOWN_THEMES; maturity 1-5; criticality Critical/High/Medium/Low.

**Rationale:** Explicit validation (no silent failures), deterministic first (network-free default), no hallucination, traceability enforced, fully testable without mocking.

**Consequences:** ✓ Deterministic repeatable behavior, ✓ Works with no network, ✓ 22 tests 100% pass, ✓ Ruff clean, ✓ Clean boundaries for Foundry integration; ✗ No Foundry Hosted Agent wrapper (future), ✗ Static catalog (extensible).

---

### 2. Interpreter Validation Enhancement

**Date:** 2026-07-06  
**Author:** Hicks (Tester)  
**Status:** Implemented  

**Decision:** Enhanced input validation in `InterpretRequest` to explicitly reject whitespace-only fields using `.strip()` checks.

**Rationale:** Whitespace-only strings are truthy in Python but semantically empty. Constitution requires explicit validation failures, not silent fallbacks. Traceability requires meaningful regulation_id, change_id, name, title.

**Implementation:** Added `.strip()` checks to all required fields in InterpretRequest validation.

**Impact:** 29 tests (expanded from 23), 100% pass; whitespace-only fields now raise ValidationError immediately.

---

### 3. Regulation Interpreter Core Approved (Tasks 1-4)

**Date:** 2026-07-06  
**Reviewer:** Ripley (Lead / Architect)  
**Status:** APPROVED  

**Decision:** Regulation Interpreter core implementation for tasks 1-4 adheres to all architectural boundaries and hard constraints.

**Hard Constraints Verified:**
- ✓ No Hosted Agent wrapper (task 4 deferred)
- ✓ No Semantic Kernel
- ✓ No API-key auth/config/docs
- ✓ Offline deterministic behavior via CatalogFixture
- ✓ Explicit validation with typed exceptions
- ✓ No broad catches or silent fallbacks

**Test Quality:** 28 tests, 100% pass; coverage includes contracts, catalog, deterministic fallback, schema validation, malformed input, network-free operation.

**Implications:** Portable core ready for CLI integration, Fabric notebooks, and future Foundry adapter. Downstream agents (Control Mapper, Gap Analysis, Remediation) can safely depend on validated contracts. Deterministic fallback ensures repeatable demos without network/Foundry dependencies.

**Next Steps:** Wire CLI, add Control Mapper deterministic path, defer Foundry Responses API integration until Entra auth adapter ready.

## Governance

- All meaningful changes require team consensus
- Document architectural decisions here
- Keep history focused on work, decisions focused on direction
