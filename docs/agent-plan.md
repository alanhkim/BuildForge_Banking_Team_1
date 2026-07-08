# Agent Plan of Action

## Recommendation

Build the regulatory impact agents with Microsoft Agent Framework for both Python and C#, with Microsoft Foundry Agent Service as the primary execution surface and Fabric Data Agent as the governed analytics surface.

Prompt-only agents are useful for fast interpretation and narrative work, but this use case needs traceability, auditability, and Fabric-ready Parquet output contracts. Agent outputs must be schema-validated and must surface Foundry/Fabric configuration failures instead of masking them.

## Architecture Decision

| Layer | Recommendation | Rationale |
| --- | --- | --- |
| Core implementation | Microsoft Agent Framework for Python and C# | Keeps the application aligned to the Foundry/Fabric agentic architecture while allowing selected C# agents or hosts where useful. |
| Live LLM calls | Foundry Responses API with Entra authentication only | Uses Foundry models without API keys. |
| Deployment option | Foundry Hosted Agent | Best fit for custom code, orchestration, identity, and observability. |
| Prompt-only agents | Use selectively | Good for interpretation prototypes and narration when governed by schema contracts. |
| Fabric Data Agent | Governed analytics agent | Queries exported Lakehouse/Semantic Model data and can be exposed as a tool to the orchestrator. |

## Phase 3: Core Agent Chain

### Regulation Interpreter Agent

**Goal:** Convert raw regulation text or catalog change entries into structured obligations.

**Tasks:**
- Read a regulation change document or catalog entry.
- Extract obligations, themes, effective dates, criticality, target maturity, and affected domains.
- Preserve traceability back to source text or seed catalog IDs.
- Avoid hallucinating obligations.
- Return schema-valid output every time.
- Surface Foundry or Entra configuration failures explicitly; do not hide them behind local fallback logic.

**System prompt:**

```text
You are the Regulation Interpreter for a banking regulatory impact assessment system.

Your job is to convert regulatory change text into structured obligations. Extract only obligations supported by the supplied source text or catalog metadata. Do not invent regulations, controls, systems, products, technologies, or evidence.

Return only JSON matching the required schema. Each obligation must include: id, change_id, theme, summary, target_maturity, criticality, affected_data_domain_ids, and source_refs.

If the source text is incomplete, mark uncertainty explicitly in the `notes` field rather than guessing. Prefer supplied catalog identifiers when present.
```

**Plan:**
1. Create a typed obligation contract.
2. Implement the Foundry-backed interpreter path behind Entra authentication; do not support API-key authentication.
3. Keep catalog fixtures as test/demo inputs, not as agent fallback behavior.
4. Validate model output against schema before returning.
5. Add tests for DORA interpretation, malformed model output, and missing Foundry/Fabric configuration errors.

### Control Mapper Agent

**Goal:** Map structured obligations to required bank controls, capabilities, technologies, and evidence expectations.

**Tasks:**
- Match obligation themes to control families.
- Identify controls required for compliance.
- Attach capabilities and evidence expectations.
- Explain why each control is relevant.
- Constrain mapping to generated estate/catalog IDs.
- Return confidence and reason codes without inventing estate entities.

**System prompt:**

```text
You are the Control Mapper for a banking regulatory impact assessment system.

Map each obligation to existing controls, capabilities, and evidence in the supplied estate. Use only provided entity IDs. Never create new controls or capabilities unless explicitly instructed by the caller.

Prefer exact theme-to-control-family mappings. If multiple controls are plausible, return ranked candidates with reasons. If no mapping exists, return an unmapped result with a clear reason.

Return only schema-valid JSON.
```

**Plan:**
1. Implement the Foundry-backed mapping path with catalog/context grounding.
2. Add a catalog-driven context table for grounding.
3. Add LLM-assisted ranking through Microsoft Agent Framework.
4. Test exact match, no-match, and multiple-candidate behavior.

### Gap Analysis Agent

**Goal:** Determine whether mapped controls satisfy obligations based on maturity and evidence.

This should be agentic but constrained by auditable contracts, supplied estate context, and validation.

**Tasks:**
- Compare `target_maturity` to control `maturity`.
- Check evidence status: Missing, Partial, Stale, or Current.
- Compute maturity shortfall.
- Assign severity.
- Build blast radius across systems, processes, products, data domains, and risks.
- Produce traceable gap records.

**Prompt usage:** Use Foundry for assessment and explanation while validating that every returned gap references supplied obligations, controls, evidence, and estate IDs.

```text
You are the Gap Analysis explainer for a regulatory impact engine.

Assess and explain the supplied gap context in business-readable language. Do not invent severity, shortfall, evidence status, affected entities, or IDs outside the provided context.

If a gap has missing evidence or maturity shortfall, explain both factors. Return concise JSON with `gap_id` and `explanation`.
```

**Plan:**
1. Build the Foundry-backed `GapAnalysisAgent` with explicit input/output contracts.
2. Validate all IDs and evidence references against the supplied estate.
3. Use Fabric Data Agent where analytical context is needed from Fabric.
4. Test maturity gap, missing evidence gap, no gap, and blast-radius traceability.

### Remediation Agent

**Goal:** Convert gaps into prioritized, owner-assigned, costed remediation actions.

**Tasks:**
- Generate a remediation action per gap.
- Assign owner from control/business unit.
- Estimate effort using severity, maturity shortfall, and evidence state.
- Rank priority.
- Produce board-ready remediation narrative.
- Return cost and duration estimates with rationale and confidence, grounded in supplied gap context.

**System prompt:**

```text
You are the Remediation Planner for a banking regulatory impact assessment system.

Create practical remediation actions from supplied gap records. Use only supplied gap, control, owner, system, process, product, and evidence data. Do not invent owners or affected systems.

Each remediation must include: id, gap_id, owner, priority, estimated_effort_days, action, dependencies, and expected outcome. Keep recommendations concise, auditable, and suitable for risk/compliance stakeholders.

If effort estimates are supplied, treat them as grounding context and explain any recommended changes.
```

**Plan:**
1. Build a Foundry-backed remediation planner.
2. Ground effort, owner, dependency, and priority recommendations in supplied gap context.
3. Validate returned remediation records before export.
4. Test effort estimates, owner propagation, and priority ranking.

## Phase 4: Scoring Engine

### Compliance Scoring Engine

**Goal:** Produce the board-level score story: As-is -> Post-change dip -> Post-remediation recovery.

This should be part of the governed agentic workflow with auditable scoring contracts.

**Tasks:**
- Compute baseline score from current controls and evidence.
- Apply incoming obligations and gaps to produce post-change score.
- Apply remediation actions to produce post-remediation score.
- Preserve explainability for every score movement.
- Make score behavior testable and stable.

**Optional narrator prompt:**

```text
You are the Compliance Score Narrator.

Explain the supplied score movement for executive risk stakeholders. Do not recalculate scores. Do not alter numeric values. Explain why post-change decreases and why post-remediation recovers.

Return concise Markdown with three sections: As-is, Post-change, Post-remediation.
```

**Plan:**
1. Add `scoring.py`.
2. Define a scoring contract that captures rationale, confidence, and traceability.
3. Validate DORA demo values or tolerances against the agent output contract.
4. Add invariant tests:
   - `post_change < as_is`
   - `post_remediation > post_change`
   - score drivers reference valid gaps and remediations.

## Build Order

| Order | Work item | Owner profile | Output |
| --- | --- | --- | --- |
| 1 | CLI contract + catalog seed prerequisite | Python engineer + domain analyst | Stable DORA input and command surface |
| 2 | Regulation Interpreter | Domain analyst + Python engineer | Structured obligations |
| 3 | Control Mapper | Python engineer | Obligation-control mappings |
| 4 | Gap Analysis | Python engineer + QA | Validated gaps |
| 5 | Remediation | Python engineer + domain analyst | Costed actions |
| 6 | Scoring Engine | Python engineer + QA | Before/after score movement |
| 7 | Foundry adapter | Data/Fabric engineer + Python/C# engineer | Foundry agent calls using Entra auth only |
| 8 | Regression suite | QA | Ruff/pytest-backed confidence |

## Deployment Path

1. Build Foundry-backed Python agents using Microsoft Agent Framework and Entra auth only.
2. Package the orchestrator as a Foundry Hosted Agent.
3. Integrate Fabric Data Agent as the governed analytics/query surface over Fabric data.
4. Keep all outputs audited through schema validation, source references, and Fabric-ready export contracts.

## Next Engineering Step

Implement the Foundry-backed Regulation Interpreter path, fail explicitly when Foundry/Fabric configuration is missing, and add schema/error-path tests.
