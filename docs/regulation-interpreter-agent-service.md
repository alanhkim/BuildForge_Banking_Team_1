# Regulation Interpreter Hosted Agent Service Plan

## Decision

The Regulation Interpreter should exist as a Microsoft Foundry **Hosted Agent** built with **Microsoft Agent Framework**, backed by a portable core interpreter service in this repository.

The Hosted Agent is the deployment target, but the implementation must not be Hosted-only. The same interpreter core must run:

- locally through `python -m regimpact interpret`
- in tests with no network access
- inside Foundry as a Hosted Agent
- with deterministic fallback whenever live model execution is unavailable

Authentication must use **Microsoft Entra only**. Do not implement API-key authentication.

## Service Shape

```text
src/regimpact/
  agents/
    interpreter.py              # portable core interpreter service
    foundry_interpreter.py      # Agent Framework Hosted Agent adapter
  contracts.py                  # typed request/response contracts
  catalog.py                    # deterministic catalog fixtures
```

## Runtime Flow

```text
Caller / CLI / Hosted endpoint
        |
        v
Agent Framework wrapper
        |
        v
RegulationInterpreterService
        |
        +--> validate input
        +--> use Foundry model path only when enabled and Entra auth is available
        +--> validate model JSON
        +--> fall back to deterministic catalog/rule interpreter when needed
        +--> return structured obligations
```

## Agent Goal

Convert raw regulation text or catalog change entries into structured, traceable obligations that downstream agents can map to bank controls, evidence, gaps, remediations, and scores.

## Agent Responsibilities

- Read regulation change text, uploaded files, or deterministic catalog entries.
- Extract obligation IDs, themes, summaries, target maturity, criticality, affected data domains, and source references.
- Preserve traceability to source text or catalog metadata.
- Return schema-valid JSON every time.
- Avoid hallucinating regulations, controls, systems, technologies, products, or evidence.
- Use deterministic fallback for offline demos and failure recovery.
- Use Entra authentication only for Foundry calls.

## Non-Goals

- Do not calculate gaps.
- Do not score compliance.
- Do not generate remediation actions.
- Do not export Parquet.
- Do not provision Azure resources.
- Do not support API keys.

## System Prompt

```text
You are the Regulation Interpreter for a banking regulatory impact assessment system.

Your job is to convert regulatory change text into structured obligations. Extract only obligations supported by the supplied source text or catalog metadata. Do not invent regulations, controls, systems, products, technologies, or evidence.

Return only JSON matching the required schema. Each obligation must include: id, change_id, theme, summary, target_maturity, criticality, affected_data_domain_ids, and source_refs.

If the source text is incomplete, mark uncertainty explicitly in the `notes` field rather than guessing. Prefer deterministic catalog identifiers when present.

Authentication and service configuration are handled by the host. Never request, emit, or rely on API keys.
```

## Request Contract

```json
{
  "regulation_id": "REG-DORA",
  "change_id": "CHG-DORA",
  "name": "DORA",
  "title": "Critical ICT Resilience Update",
  "source_text": "optional regulation text",
  "source_path": "optional local path",
  "offline_mode": true
}
```

## Response Contract

```json
{
  "regulation_id": "REG-DORA",
  "change_id": "CHG-DORA",
  "obligations": [
    {
      "id": "OBL-DORA-01",
      "change_id": "CHG-DORA",
      "theme": "ICT_RESILIENCE",
      "summary": "Maintain mature ICT continuity and recovery controls.",
      "target_maturity": 4,
      "criticality": "Critical",
      "affected_data_domain_ids": ["DD-PII"],
      "source_refs": ["catalog:REG-DORA:OBL-DORA-01"],
      "notes": []
    }
  ],
  "mode": "deterministic-fallback"
}
```

## Implementation Tasks

| Order | Task ID | Task | Output |
| --- | --- | --- | --- |
| 1 | `interpreter-contracts` | Define typed request/response contracts and validation errors. | Stable interpreter contract module. |
| 2 | `interpreter-catalog-fixture` | Add deterministic DORA fixture data. | Stable `REG-DORA`, `CHG-DORA`, `OBL-DORA-01` seed. |
| 3 | `interpreter-fallback` | Implement offline deterministic interpretation. | Network-free interpreter path. |
| 4 | `interpreter-foundry-adapter` | Add Microsoft Agent Framework / Foundry Hosted Agent adapter. | Hosted Agent boundary using Entra auth only. |
| 5 | `interpreter-schema-validation` | Validate all outputs before returning. | Rejection of invalid IDs, themes, maturity, and missing source refs. |
| 6 | `interpreter-cli-wireup` | Wire `python -m regimpact interpret`. | CLI path using the same core service. |
| 7 | `interpreter-tests` | Add regression tests. | Tests for DORA fixture, malformed input, validation failures, and offline fallback. |

## Dependency Plan

```text
demo-cli-contract
        |
demo-catalog-seed
        |
interpreter-contracts + interpreter-catalog-fixture
        |
interpreter-fallback
        |
interpreter-schema-validation
        |
interpreter-cli-wireup
        |
interpreter-tests
        |
agent-regulation-interpreter complete
```

`interpreter-foundry-adapter` can proceed after `interpreter-contracts`, but it must not block local deterministic CLI/demo execution.

## Acceptance Criteria

- `python -m regimpact interpret --file ... --regulation REG-DORA --name DORA --title "Critical ICT Resilience Update"` returns structured obligations.
- Local execution works without network access and without Foundry configuration.
- Foundry integration uses Entra authentication only.
- Invalid model JSON never escapes the service boundary.
- Every returned obligation includes source references.
- Ruff and pytest pass.

## Deployment Path

1. Build and test the portable Python core.
2. Add the Agent Framework adapter.
3. Package as a Foundry Hosted Agent when a managed endpoint is needed.
4. Keep deterministic fallback available in both local and Hosted Agent runtimes.
5. Ground downstream demos on the validated JSON contract, not free-form model output.
