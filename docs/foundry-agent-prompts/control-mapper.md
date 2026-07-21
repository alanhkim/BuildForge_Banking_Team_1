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
| `tool_evidence` | array of `ToolEvidence` | **always** | Grounding is non-negotiable. Every response must list the queries/rows you inspected, even when `mappings` is empty. |
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

## System prompt directive block (paste this into the portal)

Add the following block to the RegImpactControlMapper system prompt.
It is designed to layer on top of the existing shortlist-only guidance
(currently in `CONTROL_MAPPER_SPEC.instructions`) — do not remove the
shortlist rules; the block below only adds the empty-response contract.

```text
EMPTY-RESPONSE CONTRACT (mandatory)

You must return one of two response shapes for every request:

1. NORMAL RESPONSE — one or more mappings.
   For every obligation supplied in the request's `obligation_ids`, return
   at least one entry in `mappings` whose `obligation_id` matches. Every
   `control_id` must come from the request's `candidate_controls` shortlist.
   `reason` may be omitted.

2. DOCUMENTED-EMPTY RESPONSE — no mappings, with explanation.
   If — and only if — no obligation in the request can be mapped to any
   control in `candidate_controls`, return `mappings: []` AND set `reason`
   to a non-empty string. The `reason` must:
   - Name the specific obligations you could not map (by `obligation_id`).
   - Summarise what you found in the shortlist (which candidate control IDs
     were considered).
   - Explain what disqualified each candidate for those obligations
     (e.g. wrong capability domain, insufficient coverage, deprecated).

NEVER return `mappings: []` with `reason` missing, `reason: null`,
`reason: ""`, or a whitespace-only `reason`. The harness rejects that
response as a validation failure and the pipeline aborts.

ALWAYS populate `tool_evidence` with the queries and rows you actually
inspected, even when `mappings` is empty. Grounding is required in every
response. An empty `tool_evidence` array is a validation failure.

Prefer NORMAL RESPONSE. Use DOCUMENTED-EMPTY RESPONSE only when the
shortlist genuinely does not contain a plausible match — not to avoid work.
If a candidate is a partial match, map it and lower `confidence` to `low`
rather than returning empty.
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
- Empty-without-reason (or empty `tool_evidence`) is a hard validation
  failure. The `FabricAgentHarnessError` now carries the underlying
  `ValidationError` reason plus a 500-char snippet of the raw agent answer
  for diagnosis — no more opaque errors.
- Payload cardinalities (`obligations_count`, `candidates_count`, etc.) are
  logged at INFO before every call so operators can see what the agent was
  handed.
- The Gap Analyst stage now accepts empty `control_ids` when accompanied by
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
4. Append the "System prompt directive block" above verbatim to the end of
   the existing prompt. Do not remove existing shortlist-only or output-
   discipline rules — they still apply.
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
