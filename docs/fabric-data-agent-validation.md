# Fabric Data Agent Validation

## Current live state

| Surface | Value |
| --- | --- |
| Fabric workspace | `wsRegChgImpactdev1` (`bf949f4b-ca7a-4095-9596-1a3c8e4959e5`) |
| Fabric Data Agent | `Regulatory Impact Data Agent` (`804f0dd2-227c-4edf-bb7a-3b6f83aafb1e`) |
| Foundry project endpoint | `https://muthukr-3312-resource.services.ai.azure.com/api/projects/muthukr-3312` |
| Foundry agent | `FabricTest` |
| Foundry agent version | `4` |
| Foundry agent kind | `prompt` |
| Foundry model | `gpt-5` |
| Foundry protocols | `responses` |
| Fabric tool type | `fabric_dataagent_preview` |
| Fabric tool connection | `/subscriptions/4a23dcea-b762-4569-b255-3c45517c941b/resourceGroups/Default-ActivityLogAlerts/providers/Microsoft.CognitiveServices/accounts/muthukr-3312-resource/projects/muthukr-3312/connections/fabric_dataagent_preview_941627` |
| Agent instance identity | `765f9096-e6d5-476f-8f26-69f13891005b` |
| Agent blueprint identity | `37393fec-98c0-4a74-bdeb-6537e30c78be` |

Plain Foundry agent invocation and Fabric Data Agent tool invocation are both
available through the Azure AI Projects SDK using `agent_reference` after the
Fabric Data Agent is published.

Fabric workspace role assignments show both Foundry identities as `Contributor`.
The semantic model also grants both identities `ReadWriteExplore` access as
apps. Direct Fabric portal Data Agent chat works, and Foundry `FabricTest`
version `4` now reaches Fabric and returns a grounded answer.

Direct Data Agent invocation through the published Fabric REST URL still returns
`404 EntityNotFound` for the base OpenAI path, `/models`, `/chat/completions`,
and `/responses`. Use Foundry SDK `agent_reference` as the application
integration path.

The purpose-built application agents are tracked in
`docs/foundry-fabric-agents.md`. Their current active versions use the same
Fabric Data Agent preview tool connection and are validated through Azure AI
Projects SDK `agent_reference`.

## Smoke prompt set

The versioned smoke prompt file is:

```text
data/fabric_data_agent_smoke_prompts.yaml
```

It covers:

| Category | Intent |
| --- | --- |
| `score_story` | Validate score movement and score citations. |
| `obligation_control_map` | Validate obligation-to-control/capability/evidence joins. |
| `gap_blast_radius` | Validate affected products, systems, processes, and data domains. |
| `remediation` | Validate owner, effort, and priority grounding. |
| `evidence_health` | Validate non-present evidence status and linked controls. |
| `lineage` | Validate traceability from regulation through relationship/lineage data. |

Each answer must be grounded in Fabric data and cite tables, curated views, fields,
or entity IDs. Unsupported links must be called out as missing rather than inferred.

## Validation sequence

1. Confirm the Foundry `FabricTest` agent is active and still has the
   `fabric_dataagent_preview` tool attached.
2. Confirm the Fabric Data Agent item is visible in the workspace and is grounded
   over the Lakehouse or semantic model that contains the exported Parquet tables.
3. Confirm the Foundry agent identity or Fabric tool connection principal has the
   required Fabric workspace, item, and data-source permissions.
4. Run each prompt in `data/fabric_data_agent_smoke_prompts.yaml` through the
   Foundry `responses` protocol using Azure AI Projects `agent_reference`.
5. Treat any authorization error, empty answer, unsupported citation, malformed
   JSON, or Fabric tool error as a blocker for repo client integration.

## Working bridge evidence

After publishing the Fabric Data Agent, this prompt was sent to `FabricTest`
version `4` through Azure AI Projects `AIProjectClient.get_openai_client()` and
OpenAI `responses.create()` with `agent_reference`:

```text
How compliant are we for the DORA change today versus after remediation?
```

The live Foundry invocation returned a grounded Fabric answer:

```text
Today/as-is: 54%
Immediate post-change dip: 52.2%
After planned remediation: 58.9%
```

This confirms the supported application path is:

```text
Application -> Azure AI Projects SDK -> Foundry agent_reference -> Fabric Data Agent tool -> Fabric sources
```

## Permission remediation status

The visible Foundry principals have Fabric workspace access:

| Principal | Principal ID | Fabric role |
| --- | --- | --- |
| Foundry agent instance identity | `765f9096-e6d5-476f-8f26-69f13891005b` | Contributor |
| Foundry managed-agent blueprint identity | `c18c4261-7519-419d-b575-1e055d23ca14` | Contributor |

After granting those roles and publishing the Fabric Data Agent, the smoke prompt
succeeded through `FabricTest` version `4`.

## Application agent validation

The following purpose-built Foundry agents were created as prompt agents using
`gpt-5`, the `responses` protocol, Entra authorization, and the
`fabric_dataagent_preview_941627` connection:

| Agent | Version | Validation |
| --- | --- | --- |
| `RegImpactExecutiveQA` | `3` | SDK `agent_reference` returned CHG-DORA score values with object-shaped citations and tool evidence. |
| `RegImpactScoreNarrator` | `3` | SDK `agent_reference` returned a score narration with inner JSON values `54.0`, `52.2`, and `58.9`. |

All six application agents and their identities are listed in
`docs/foundry-fabric-agents.md`.
