# Squad Decisions

## Active Decisions

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

## Governance

- All meaningful changes require team consensus
- Document architectural decisions here
- Keep history focused on work, decisions focused on direction
