# Agent Plan of Action

## Recommendation

Build the regulatory impact agents as Python, Agent Framework-compatible code first, with deterministic offline fallbacks. Use Microsoft Foundry Agent Service later as a deployment surface, preferably through a Hosted Agent for the orchestrator.

Prompt Agents are useful for fast prompt-only interpretation and narrative work, but this use case needs deterministic scoring, traceability, auditability, and Fabric-ready Parquet output contracts. Gap analysis and scoring should remain source-of-truth Python engines, not prompt-only agents.

## Architecture Decision

| Layer | Recommendation | Rationale |
| --- | --- | --- |
| Core implementation | Python classes with Agent Framework-style contracts | Keeps local/offline demo testable and repeatable. |
| Live LLM calls | Foundry Responses API from Python | Adds Foundry models only when configured. |
| Deployment option | Foundry Hosted Agent | Best fit for custom code, orchestration, identity, and observability. |
| Prompt-only agents | Use selectively | Good for interpretation prototypes and narration, not deterministic truth. |
| Fabric Data Agent | Separate downstream consumer | Queries exported Lakehouse/Semantic Model data after the pipeline runs. |

## Phase 3: Core Agent Chain

### Regulation Interpreter Agent

**Goal:** Convert raw regulation text or catalog change entries into structured obligations.

**Tasks:**
- Read a regulation change document or catalog entry.
- Extract obligations, themes, effective dates, criticality, target maturity, and affected domains.
- Preserve traceability back to source text or seed catalog IDs.
- Avoid hallucinating obligations.
- Return schema-valid output every time.
- Fall back to deterministic catalog/rule logic when Foundry is unavailable.

**System prompt:**

```text
You are the Regulation Interpreter for a banking regulatory impact assessment system.

Your job is to convert regulatory change text into structured obligations. Extract only obligations supported by the supplied source text or catalog metadata. Do not invent regulations, controls, systems, products, technologies, or evidence.

Return only JSON matching the required schema. Each obligation must include: id, change_id, theme, summary, target_maturity, criticality, affected_data_domain_ids, and source_refs.

If the source text is incomplete, mark uncertainty explicitly in the `notes` field rather than guessing. Prefer deterministic catalog identifiers when present.
```

**Plan:**
1. Create a typed obligation contract.
2. Implement deterministic DORA catalog fallback first.
3. Add optional Foundry call behind configuration.
4. Validate model output against schema before returning.
5. Add tests for DORA interpretation and malformed text fallback.

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
1. Implement deterministic theme mapping, starting with `ICT_RESILIENCE -> Operational Resilience -> CTL-OR-3`.
2. Add a catalog-driven mapping table.
3. Add optional LLM-assisted ranking later.
4. Test exact match, no-match, and multiple-candidate behavior.

### Gap Analysis Agent

**Goal:** Determine whether mapped controls satisfy obligations based on maturity and evidence.

This should be mostly deterministic code, not prompt-led.

**Tasks:**
- Compare `target_maturity` to control `maturity`.
- Check evidence status: Missing, Partial, Stale, or Current.
- Compute maturity shortfall.
- Assign severity.
- Build blast radius across systems, processes, products, data domains, and risks.
- Produce traceable gap records.

**Prompt usage:** Use a prompt only for explanation, never for source-of-truth gap calculation.

```text
You are the Gap Analysis explainer for a regulatory impact engine.

The gap records have already been calculated deterministically. Your job is only to explain them in business-readable language. Do not change severity, shortfall, evidence status, affected entities, or IDs.

If a gap has missing evidence or maturity shortfall, explain both factors. Return concise JSON with `gap_id` and `explanation`.
```

**Plan:**
1. Build deterministic `impact.py`.
2. Keep `GapAnalysisAgent` as a wrapper over the deterministic engine.
3. Use LLM only for explanations.
4. Test maturity gap, missing evidence gap, no gap, and blast-radius traceability.

### Remediation Agent

**Goal:** Convert gaps into prioritized, owner-assigned, costed remediation actions.

**Tasks:**
- Generate a remediation action per gap.
- Assign owner from control/business unit.
- Estimate effort using severity, maturity shortfall, and evidence state.
- Rank priority.
- Produce board-ready remediation narrative.
- Keep cost and duration deterministic for repeatable demos.

**System prompt:**

```text
You are the Remediation Planner for a banking regulatory impact assessment system.

Create practical remediation actions from supplied gap records. Use only supplied gap, control, owner, system, process, product, and evidence data. Do not invent owners or affected systems.

Each remediation must include: id, gap_id, owner, priority, estimated_effort_days, action, dependencies, and expected outcome. Keep recommendations concise, auditable, and suitable for risk/compliance stakeholders.

If deterministic effort estimates are supplied, preserve them exactly.
```

**Plan:**
1. Build deterministic remediation rules.
2. Preserve known demo behavior, such as Critical + shortfall 3 + Missing evidence = 110 days.
3. Add LLM narrative only after deterministic action exists.
4. Test effort estimates, owner propagation, and priority ranking.

## Phase 4: Scoring Engine

### Compliance Scoring Engine

**Goal:** Produce the board-level score story: As-is -> Post-change dip -> Post-remediation recovery.

This should be pure deterministic Python, not a Foundry agent.

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
2. Define a deterministic scoring formula.
3. Lock DORA demo values or tolerances.
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
| 4 | Gap Analysis | Python engineer + QA | Deterministic gaps |
| 5 | Remediation | Python engineer + domain analyst | Costed actions |
| 6 | Scoring Engine | Python engineer + QA | Before/after score movement |
| 7 | Foundry adapter | Data/Fabric engineer + Python engineer | Optional live LLM calls |
| 8 | Regression suite | QA | Ruff/pytest-backed confidence |

## Deployment Path

1. Build local Python agents and deterministic engines first.
2. Add optional Foundry Responses API adapter for Interpreter and Remediation narration.
3. Package the orchestrator as a Foundry Hosted Agent only when a managed endpoint is needed.
4. Keep Gap Analysis and Scoring deterministic and audited.
5. Use Fabric Data Agent separately after exports exist and are loaded into Fabric.

## Next Engineering Step

Implement the prerequisite CLI contract and deterministic catalog seed, then start the Regulation Interpreter with offline fallback and schema tests.
