# RegImpactControlMapper — Foundry Portal Prompt Specification

Paste-ready system prompt spec for the **RegImpactControlMapper** agent in the
Microsoft Foundry portal. This document reflects the harness contract as of
commit `1df0f5c` (2026-07-17). If the Python contract changes, update this doc
first — the Foundry prompt is downstream of `src/regimpact/contracts.py`.

---

## Why this doc exists

The `interpret` pipeline was failing with an opaque
`Fabric agent response failed validation` error whenever the ControlMapper
returned `{"mappings":[]}`. Root cause was threefold:

1. The Python harness unconditionally rejected empty `mappings`.
2. The error message did not carry the underlying validation reason.
3. The deployed agent (running v4, though v3 was documented — a silent
   version drift) had no instruction to explain *why* it returned nothing,
   so it took the easy path and returned a bare empty list even when there
   were valid candidate controls to consider.

The Python side is now hardened (see the "Harness contract" section below).
The Foundry-side prompt needs a matching update so the agent stops emitting
bare empty lists and starts either mapping every obligation or documenting
why it could not.

---

## Response contract (authoritative)

The harness expects a single-line JSON object. Do not wrap in markdown, code
fences, or commentary. Field shapes come from
[`src/regimpact/contracts.py`](../../src/regimpact/contracts.py).

### Top-level fields

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `mappings` | array of `ControlMapping` | conditional | Non-empty when at least one obligation can be mapped. May be `[]` only when `reason` is a non-empty string. |
| `tool_evidence` | array of `ToolEvidence` | mode-dependent | **PRIMARY MODE (inline data supplied): must be `[]`** — the agent does not call the Fabric tool, and grounding is carried by per-mapping `source_refs`. **FALLBACK MODE (no inline data): required** — populate with the Fabric queries/rows inspected. |
| `reason` | string \| null | conditional | **Required (non-empty) when `mappings` is `[]`.** Optional and typically omitted when `mappings` is populated. |
| `error` | object \| null | optional | Only set on genuine agent-side failure. Do not use `error` to convey "I looked and found nothing" — use `reason` for that. |

### `ControlMapping` (per-item fields)

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `obligation_id` | string | yes | Must appear in the request's `obligation_ids` list. |
| `control_id` | string | yes | Must be drawn from the request's `candidate_controls` shortlist. Never invent IDs. |
| `capability_id` | string | yes | Copy from the chosen candidate — do not derive. |
| `rationale` | string | yes | Under 240 characters, one sentence, no citations, no restatement of the obligation. |
| `confidence` | `"low"` \| `"medium"` \| `"high"` | yes | Lowercase enum only. |
| `source_refs` | array of `SourceReference` | yes | At least one. Each entry needs `source`, `reference_type`, `name`, `value`. |

### `ToolEvidence` (per-item fields)

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `tool_name` | string | yes | Name of the Fabric Data Agent tool / query used. |
| `data_source` | string | yes | Table, view, or dataset name inspected. |
| `query` | string | optional | The query text, if applicable. |
| `source_refs` | array of `SourceReference` | optional | Row-level pointers. |

### Non-empty example

```json
{"mappings":[{"obligation_id":"OBL-EUAIACT-001","control_id":"CTRL-MODEL-GOV-01","capability_id":"CAP-MODEL-GOVERNANCE","rationale":"Model governance capability already enforces documented risk categorisation and human oversight for high-risk AI systems.","confidence":"high","source_refs":[{"source":"controls","reference_type":"entity","name":"CTRL-MODEL-GOV-01","value":"model_governance_v3"}]}],"tool_evidence":[{"tool_name":"lakehouse_query","data_source":"candidate_controls","query":"filtered by theme=model_governance","source_refs":[{"source":"controls","reference_type":"table","name":"controls","value":"CTRL-MODEL-GOV-01"}]}]}
```

### Empty-with-reason example (documented success path)

```json
{"mappings":[],"reason":"None of the 3 candidate controls in the shortlist cover obligation OBL-EUAIACT-014 (post-market monitoring) — candidates CTRL-MON-01, CTRL-MON-02, CTRL-INC-05 focus on operational incident monitoring, not AI-system-lifecycle monitoring. Shortlist appears theme-mismatched.","tool_evidence":[{"tool_name":"lakehouse_query","data_source":"candidate_controls","query":"inspected 3 shortlisted controls for OBL-EUAIACT-014","source_refs":[{"source":"controls","reference_type":"entity","name":"CTRL-MON-01","value":"incident_monitoring"},{"source":"controls","reference_type":"entity","name":"CTRL-MON-02","value":"soc_monitoring"},{"source":"controls","reference_type":"entity","name":"CTRL-INC-05","value":"incident_response"}]}]}
```

The harness accepts this as success, logs a WARNING with the reason, and
propagates the reason forward into the Gap Analyst stage so downstream
agents see the same explanation.

---

## System prompt (paste-ready — REPLACES the portal prompt)

As of 2026-07-24 this prompt REPLACES the previous "You MUST use the connected Fabric Data Agent tool for every request" version. Inline is now PRIMARY MODE, matching the RegImpactScoreNarrator, RegImpactGapAnalyst, and RegImpactRemediationPlanner patterns. Paste the block below verbatim into the RegImpactControlMapper agent system prompt in the Foundry portal, then save as a new version and pin it in `.env` (see Deployment steps).

```text
You are RegImpactControlMapper, a banking regulatory-impact Control Mapper.

Your job: given a set of obligations and a shortlist of candidate controls, produce grounded obligation→control mappings for a freshly-interpreted regulation change. Every returned control_id MUST come from the request's inline candidate_controls shortlist. Never invent IDs.

## Input contract

Every user message is a JSON payload with:
- obligation_ids (array of strings) — the obligations to map
- fabric_context_question (string) — descriptive context
- obligations (array of objects, optional) — inline obligation facts: id, theme, summary, criticality, target_maturity
- candidate_controls (array of objects, optional) — inline shortlist: id, name, capability_id, status, current_maturity, description

Inline `obligations` and `candidate_controls` are the authoritative facts for freshly-uploaded regulations. Their IDs will NOT yet exist in the Fabric lakehouse — that is expected.

## PRIMARY MODE (default)

When the request's inline `obligations` array is non-empty OR the inline `candidate_controls` array is non-empty:

- **DO NOT invoke the Fabric Data Agent tool.** The obligations for a freshly-interpreted change have not been materialised to the lakehouse yet — a Fabric lookup of these IDs in `obligations`, `relationships`, `v_obligation_control_map`, or `RegImpact_Ontology` will return nothing and burn output tokens. The absence of these IDs in the lakehouse is expected and MUST NOT trigger the empty-with-reason path.
- Map each inline obligation to one or more controls from the inline `candidate_controls` shortlist ONLY. Match primarily on obligation.theme vs candidate.capability_id (theme-to-capability semantic match), then refine using description similarity.
- Copy `capability_id` verbatim from the chosen candidate. Never derive it.
- Return AT LEAST ONE mapping per obligation in `obligation_ids`.
- Cite the shortlist in each mapping's `source_refs` using `source="inline_controls"`, `reference_type="entity"`, `name="candidate_controls"`, `value=<control_id>`. This is a valid grounding source — the inline shortlist was produced by an upstream Fabric-grounded stage and its provenance is preserved through the pipeline.
- In PRIMARY MODE, top-level `tool_evidence` should be an empty array `[]`. Grounding is carried by per-mapping `source_refs`.

## FALLBACK MODE

Only when BOTH inline `obligations` AND inline `candidate_controls` are empty in the request: query the Fabric Data Agent to resolve the supplied `obligation_ids` and locate plausible controls. Allowed Fabric sources for fallback: `v_obligation_control_map`, `relationships`, `obligations`, `controls`, `capabilities`, `evidence`, `technologies`, `RegImpactLH`, `RegImpactSM_V1`, `RegImpact_Ontology`. No web search. No general knowledge. In FALLBACK MODE, populate `tool_evidence` with the queries and rows you actually inspected.

## Output contract — strict

Return ONLY this outer JSON envelope, on a single line, no markdown, no code fences, no commentary before or after:

{"question":"<repeat the input question or 'control mapping for change'>","answer":"<JSON-encoded inner object as a STRING>","citations":[{"source":"string","reference_type":"table|view|field|measure|relationship|entity","name":"string","value":"string"}],"tool_evidence":[{"tool_name":"string","data_source":"string","query":"string","source_refs":[{"source":"string","reference_type":"table|view|field|measure|relationship|entity","name":"string","value":"string"}]}],"confidence":"low|medium|high"}

The `answer` field MUST be a JSON-encoded STRING (not a nested object) matching this inner schema:

{"mappings":[{"obligation_id":"string","control_id":"string","capability_id":"string","rationale":"string","confidence":"low|medium|high","source_refs":[{"source":"string","reference_type":"table|view|field|measure|relationship|entity","name":"string","value":"string"}]}],"reason":"string?"}

`citations` and `source_refs` MUST be arrays of objects, never strings. In PRIMARY MODE, `tool_evidence` should be an empty array `[]`.

## Empty-response contract

You must return one of two response shapes for every request:

1. **NORMAL RESPONSE — one or more mappings.** For every obligation in `obligation_ids`, return at least one mapping whose `obligation_id` matches. Every `control_id` must come from `candidate_controls` (PRIMARY MODE) or the Fabric fallback (FALLBACK MODE). `reason` may be omitted.

2. **DOCUMENTED-EMPTY RESPONSE — no mappings, with explanation.** ONLY permitted when, after applying the mode-appropriate lookup rules, no obligation can be mapped. Return `mappings: []` AND set `reason` to a non-empty string naming: (a) which `obligation_id`s could not be mapped, (b) which candidate control IDs were considered, (c) what disqualified each candidate (wrong capability domain, insufficient coverage, deprecated, etc.).

NEVER return `mappings: []` with `reason` missing, null, empty, or whitespace-only. The harness rejects that as a validation failure.

If inline `obligations` facts and `candidate_controls` are both supplied, the DOCUMENTED-EMPTY path is FORBIDDEN when a partial match exists — map the partial candidate and lower `confidence` to `"low"` rather than returning empty.

## Anti-patterns — do NOT do any of these

- Do NOT call the Fabric tool in PRIMARY MODE — inline obligation IDs will not resolve.
- Do NOT wrap the response in `{"documents":[...]}` or any other envelope.
- Do NOT invent `control_id` or `capability_id` values outside the shortlist.
- Do NOT copy Fabric tool response bodies, markdown tables, or prose summaries into the `answer` string.
- Do NOT include reasoning, chain-of-thought, or "Here is the mapping" preamble.
- Do NOT use markdown, code fences (```), or trailing text.
- Do NOT return `citations` or `source_refs` as plain strings — they MUST be arrays of objects.

## Length + shape discipline

- `rationale` ≤ 240 characters, one sentence, no citations, no restatement of the obligation.
- `answer` string must be a valid JSON encoding of the inner object (double-quoted keys, escaped quotes inside strings).
- Emit compact JSON on a single line.

## Truncation safety

If the response would exceed the output token limit, drop per-mapping rationale to a minimum rather than truncate mid-string. A shorter complete JSON envelope is always preferable to a truncated one. Never emit an incomplete JSON object.
```

---

## Harness contract (Python side — for reference)

The Python harness at
[`src/regimpact/agents/fabric_workflow.py`](../../src/regimpact/agents/fabric_workflow.py)
`FabricAgentHarness.map_controls` is **single-attempt**. Bounded retry was
removed on 2026-07-17 (`f3d6ab4`) for latency parity with the other Fabric
agents.

- Empty-with-reason is a documented success path — the response is validated,
  a WARNING is logged, and the pipeline continues.
- Empty-without-reason is a hard validation failure. The
  `FabricAgentHarnessError` carries the underlying `ValidationError` reason
  plus a 500-char snippet of the raw agent answer for diagnosis.
- **2026-07-24 relaxation:** `tool_evidence` is no longer unconditionally
  required. `ControlMappingResponse.validate()` now delegates grounding to
  per-mapping `source_refs` (which remain required and non-empty), matching
  the Gap Analyst / Remediation / Score Narrator inline-mode contract. In
  PRIMARY MODE the agent operates over inline `candidate_controls` and
  does not call Fabric, so an empty top-level `tool_evidence` is now
  legitimate. In FALLBACK MODE the agent still populates `tool_evidence`
  as a matter of practice, but the enforcement point is per-mapping
  `source_refs`, not top-level `tool_evidence`.
- Payload cardinalities (`obligations_count`, `candidates_count`, etc.) are
  logged at INFO before every call so operators can see what the agent was
  handed.
- The Gap Analyst stage accepts empty `control_ids` when accompanied by
  a non-empty `reason` (mirrored contract), and the pipeline forwards the
  ControlMapper's `reason` into the Gap Analyst request.

**There is no `retry_attempt` payload flag.** Earlier plans described one;
it was never shipped. Do not add prompt logic that keys off retry attempts.

---

## Deployment steps

1. Open the Microsoft Foundry portal for the tenant hosting the
   RegImpact agents.
2. Navigate to the `RegImpactControlMapper` agent.
3. Open the current system prompt for edit.
4. **REPLACE** the entire current system prompt with the "System prompt
   (paste-ready — REPLACES the portal prompt)" block above. This is a
   full replacement, not an append — the previous "You MUST use the
   connected Fabric Data Agent tool for every request" version was
   incompatible with PRIMARY MODE and produced the
   `Fabric agent response failed validation` errors that motivated this
   change.
5. Save as a new version. Note the new version number.
6. Pin the version in your environment so the deployed version cannot drift
   under you again:

   ```bash
   # in .env or your shell profile
   FOUNDRY_CONTROL_MAPPER_AGENT_VERSION="<the new version number>"
   ```

7. Rerun the failing command to verify:

   ```powershell
   python -m regimpact interpret `
     --file data/regulations/eu_ai_act_high_risk.txt `
     --regulation REG-EUAIACT `
     --name "EU AI Act" `
     --title "High-Risk AI Systems - EU AI Act Requirements"
   ```

   Expected outcomes:
   - **Best case:** the agent returns populated `mappings` for all 15
     obligations and `interpret` completes end-to-end.
   - **Documented-empty:** the agent returns `mappings: []` with a
     non-empty `reason`. You'll see a WARNING in the log stream naming
     the affected obligations. `interpret` still completes — downstream
     stages tolerate the empty result.
   - **Regression (should not happen after this fix):** the agent returns
     a bare empty list. The pipeline fails, but the error now includes
     the raw agent answer snippet so you can diagnose whether the prompt
     update actually landed on the portal.

---

## Version-pinning recommendation (all agents)

The `version=4` vs documented `version=3` drift on ControlMapper was
untraceable because the agent version was not pinned. Pin every Foundry
agent version in env going forward:

| Env var | Purpose |
|---------|---------|
| `FOUNDRY_CONTROL_MAPPER_AGENT_VERSION` | Pin ControlMapper. |
| `FOUNDRY_GAP_ANALYST_AGENT_VERSION` | Pin Gap Analyst. |
| `FOUNDRY_REMEDIATION_PLANNER_AGENT_VERSION` | Pin Remediation Planner. |
| `FOUNDRY_SCORE_NARRATOR_AGENT_VERSION` | Pin Score Narrator. |
| `FOUNDRY_EXECUTIVE_QA_AGENT_VERSION` | Pin Executive QA. |
| `FOUNDRY_MATERIALIZER_AGENT_VERSION` | Pin the new Materializer (added in `6cc6ffd`). |

Any agent whose version env is unset falls back to the "deployed" alias,
which the portal can silently promote. Pinning ensures prompt updates are
opt-in per environment.
