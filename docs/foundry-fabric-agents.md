# Foundry Fabric Application Agents

## Purpose

This document records the purpose-built Foundry prompt agents that back the
regulatory impact application workflow. Each agent is Entra-authenticated,
uses the existing Fabric Data Agent preview tool connection, and is instructed
to return the JSON envelope expected by the application contracts.

## Shared Foundry configuration

| Surface | Value |
| --- | --- |
| Foundry project endpoint | `https://muthukr-3312-resource.services.ai.azure.com/api/projects/muthukr-3312` |
| Model deployment | `gpt-5` |
| Protocol | `responses` |
| Tool type | `fabric_dataagent_preview` |
| Tool connection | `/subscriptions/4a23dcea-b762-4569-b255-3c45517c941b/resourceGroups/Default-ActivityLogAlerts/providers/Microsoft.CognitiveServices/accounts/muthukr-3312-resource/projects/muthukr-3312/connections/fabric_dataagent_preview_941627` |
| Fabric workspace | `wsRegChgImpactdev1` (`bf949f4b-ca7a-4095-9596-1a3c8e4959e5`) |
| Fabric Data Agent | `Regulatory Impact Data Agent` (`804f0dd2-227c-4edf-bb7a-3b6f83aafb1e`) |

## Deployed agents

| Application agent | Foundry agent | Active version | Python module |
| --- | --- | --- | --- |
| Control Mapper | `RegImpactControlMapper` | `3` | `src/regimpact/agents/fabric_control_mapper.py` |
| Gap Analyst | `RegImpactGapAnalyst` | `3` | `src/regimpact/agents/fabric_gap_analyst.py` |
| Remediation Planner | `RegImpactRemediationPlanner` | `3` | `src/regimpact/agents/fabric_remediation_planner.py` |
| Compliance Score Narrator | `RegImpactScoreNarrator` | `3` | `src/regimpact/agents/fabric_score_narrator.py` |
| Audit & Lineage Agent | `RegImpactAuditLineage` | `3` | `src/regimpact/agents/fabric_lineage.py` |
| Executive Q&A Agent | `RegImpactExecutiveQA` | `3` | `src/regimpact/agents/fabric_executive_qa.py` |

## Identity access

Each prompt agent has an agent instance identity and a managed-agent blueprint
identity. Both identity types must keep Fabric workspace `Contributor` access
and semantic model `ReadWriteExplore` access for `RegImpactSM_V1`.

| Foundry agent | Instance principal | Blueprint principal |
| --- | --- | --- |
| `RegImpactControlMapper` | `3f08489a-a4f1-40ff-b6bd-0680ec895495` | `0096edb8-5602-4ee0-8fab-df4e58299164` |
| `RegImpactGapAnalyst` | `2a226ed0-2ad9-41fe-96e2-8b99ef677322` | `30b0738b-7fbb-4d1b-95ad-8c944643bc1f` |
| `RegImpactRemediationPlanner` | `e54cd2a9-e2bf-4a0c-b8bc-7f75235403d9` | `9467b08f-cf96-4930-8928-d14ee16482ff` |
| `RegImpactScoreNarrator` | `731aa74f-b77d-4379-8da5-e0f6b7582a22` | `95af1125-cafd-4cd3-b249-44dc84404100` |
| `RegImpactAuditLineage` | `345ca9c4-ea12-4235-b3f9-b42a282dd6b7` | `ced811fc-6c0a-4a54-bc04-29f62785a3de` |
| `RegImpactExecutiveQA` | `546a61ef-163f-47de-929c-2deeaf2e7bc6` | `d9248fbe-8d0b-4d1c-9e8f-7ca58ea5db64` |

## Output contract

All agents return the shared Fabric Q&A envelope:

```json
{
  "question": "string",
  "answer": "string",
  "citations": [
    {
      "source": "string",
      "reference_type": "table|view|field|measure|relationship|entity",
      "name": "string",
      "value": "string"
    }
  ],
  "tool_evidence": [
    {
      "tool_name": "string",
      "data_source": "string",
      "query": "string",
      "source_refs": [
        {
          "source": "string",
          "reference_type": "table|view|field|measure|relationship|entity",
          "name": "string",
          "value": "string"
        }
      ]
    }
  ],
  "confidence": "low|medium|high"
}
```

For Control Mapper, Gap Analyst, Remediation Planner, Score Narrator, and Audit
& Lineage, the top-level `answer` is a JSON-encoded string matching that agent's
typed inner contract. Executive Q&A uses `answer` for concise natural language.

## Validation status

Azure AI Projects SDK `agent_reference` validation succeeded for:

| Agent | Version | Live validation |
| --- | --- | --- |
| `RegImpactExecutiveQA` | `3` | Returned CHG-DORA scores with object-shaped citations and tool evidence. |
| `RegImpactScoreNarrator` | `3` | Returned CHG-DORA score narration with inner JSON scores: `54.0`, `52.2`, `58.9`. |

The Foundry MCP `agent_invoke` path can time out on Fabric tool calls; the
application integration path remains Azure AI Projects SDK `agent_reference`.
