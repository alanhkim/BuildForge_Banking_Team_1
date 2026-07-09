# Fabric Data Agent Validation

## Current live state

| Surface | Value |
| --- | --- |
| Fabric workspace | `wsRegChgImpactdev1` (`bf949f4b-ca7a-4095-9596-1a3c8e4959e5`) |
| Fabric Data Agent | `Regulatory Impact Data Agent` (`804f0dd2-227c-4edf-bb7a-3b6f83aafb1e`) |
| Foundry project endpoint | `https://muthukr-3312-resource.services.ai.azure.com/api/projects/muthukr-3312` |
| Foundry agent | `FabricTest` |
| Foundry agent version | `2` |
| Foundry agent kind | `prompt` |
| Foundry model | `gpt-5` |
| Foundry protocols | `responses` |
| Fabric tool type | `fabric_dataagent_preview` |
| Fabric tool connection | `/subscriptions/4a23dcea-b762-4569-b255-3c45517c941b/resourceGroups/Default-ActivityLogAlerts/providers/Microsoft.CognitiveServices/accounts/muthukr-3312-resource/projects/muthukr-3312/connections/fabric_dataagent_preview_941627` |
| Agent instance identity | `765f9096-e6d5-476f-8f26-69f13891005b` |
| Agent blueprint identity | `37393fec-98c0-4a74-bdeb-6537e30c78be` |

Plain Foundry agent invocation is available, but forced Fabric Data Agent tool
invocation currently fails with `504 Gateway Time-out`. Application code must not
depend on the Fabric tool path until this smoke set succeeds through Foundry.

Fabric workspace role assignments now show both Foundry identities as
`Contributor`. The timeout still reproduces after the grant, so the blocker is
now narrowed to the Foundry Fabric tool bridge, the project connection, item/data
source permissions below the workspace, or a preview service issue.

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
   Foundry `responses` protocol.
5. Treat any `504`, authorization error, empty answer, or unsupported citation as
   a blocker for repo client integration.

## Current blocker evidence

The score-story smoke prompt was sent to `FabricTest` version `2` with this
instruction:

```text
Use the Fabric Data Agent tool, not web search. In the regulatory impact data,
summarize the DORA compliance score movement from AsIs to PostChange to
PostRemediation and cite the score values or table fields you used.
```

The live Foundry invocation returned:

```text
504 Gateway Time-out
```

Until that is resolved, continue implementing contracts and validation scaffolding,
but do not wire production agent behavior to the Fabric tool as if it were healthy.

## Permission remediation status

The visible Foundry principals have Fabric workspace access:

| Principal | Principal ID | Fabric role |
| --- | --- | --- |
| Foundry agent instance identity | `765f9096-e6d5-476f-8f26-69f13891005b` | Contributor |
| Foundry managed-agent blueprint identity | `c18c4261-7519-419d-b575-1e055d23ca14` | Contributor |

After granting those roles, the smoke prompt still returned `504 Gateway
Time-out`, so the next remediation candidate is to recreate or refresh the
Foundry `fabric_dataagent_preview_941627` tool connection, then republish or
reversion `FabricTest` and rerun the smoke prompt set.
