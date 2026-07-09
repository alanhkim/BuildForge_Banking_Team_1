# Foundry/Fabric Agent Backlog

## Purpose

Create a Foundry/Fabric-first agentic workflow that uses Microsoft Fabric as the governed analytics surface for the regulatory impact data products produced by this repository. The agents must align with the project constitution: Microsoft Agent Framework, Azure AI Foundry, Fabric Data Agent integration, Entra authentication only, explicit Parquet exports, and no deterministic/offline fallback paths for agent behavior.

## Current Verified State

- Fabric workspace access is working for `wsRegChgImpactdev1`.
- Fabric Data Agent item is visible as `Regulatory Impact Data Agent`.
- Foundry resource `muthukr-3312-resource` and project `muthukr-3312` are visible.
- Foundry agent `FabricTest` exists and includes a `fabric_dataagent_preview` tool connection.
- Plain Foundry agent invocation works.
- Forced Fabric Data Agent tool invocation through Foundry currently returns a gateway timeout and must be fixed before application integration.
- Live `FabricTest` metadata confirms an active prompt agent at version `2` using `gpt-5`, the `responses` protocol, Entra authorization, and the `fabric_dataagent_preview` tool connection `fabric_dataagent_preview_941627`.
- The Fabric Data Agent smoke prompt set is versioned in `data/fabric_data_agent_smoke_prompts.yaml`; validation details and current `504 Gateway Time-out` evidence are in `docs/fabric-data-agent-validation.md`.
- Fabric workspace roles now include the Foundry agent instance identity and blueprint identity as `Contributor`; the `504 Gateway Time-out` still reproduces, so the next hypothesis is a stale/broken Foundry Fabric tool connection or preview service bridge issue.
- Initial application-side contracts and client boundaries are in place for Fabric Q&A, control mapping, gap analysis, remediation, score narration, lineage, Foundry agent invocation, and Fabric Data Agent invocation through Foundry. These surfaces validate citations/tool evidence and surface bridge failures explicitly.

## Target Agent Architecture

| Agent | Responsibility | Fabric Data Agent usage |
| --- | --- | --- |
| Regulation Interpreter | Convert regulation text into structured obligations. | No; uses Foundry model over source text and supplied catalog context. |
| Control Mapper | Map obligations to controls, capabilities, technologies, and evidence expectations. | Yes; queries current estate/control/evidence context from Fabric. |
| Gap Analyst | Identify maturity/evidence gaps and blast radius. | Yes; queries Fabric views over controls, evidence, systems, products, processes, risks, and data domains. |
| Remediation Planner | Generate prioritized remediation actions. | Yes; queries owners, affected assets, evidence state, and prior gap context. |
| Compliance Score Narrator | Explain as-is, post-change, and post-remediation score movement. | Yes; queries score facts, dimensions, and supporting drivers. |
| Executive Q&A Agent | Answer board, risk, compliance, and governance questions. | Yes; primary Fabric Data Agent consumer. |
| Audit & Lineage Agent | Explain source references, lineage, glossary, and evidence traceability. | Yes; queries Fabric/Purview-oriented export tables and views. |

## Epic 1: Fabric Data Foundation

**Goal:** Make the repository outputs queryable by Fabric Data Agent through stable, documented tables and views.

| Task ID | Task | Dependencies | Acceptance criteria |
| --- | --- | --- | --- |
| FAB-001 | Define Fabric table inventory for all exported Parquet entities, relationships, Gold dimensions, and facts. | None | A table inventory lists source file, Fabric table name, primary key, and purpose. |
| FAB-002 | Create or update Fabric Lakehouse load notebook/script for raw entity and relationship Parquet outputs. | FAB-001 | Tables load into Fabric Lakehouse without manual schema edits. |
| FAB-003 | Create curated view definitions: `v_obligation_control_map`, `v_gap_blast_radius`, `v_remediation_priority`, `v_compliance_score_story`, `v_evidence_health`, and `v_product_regulatory_exposure`. | FAB-002 | Each view has documented columns and returns rows against demo data. |
| FAB-004 | Add Fabric Data Agent grounding instructions for the five-layer model, score story, and allowed answer boundaries. | FAB-003 | Data Agent instructions reference curated views and prohibit unsupported claims. |
| FAB-005 | Add smoke prompts and expected answer characteristics for Fabric Data Agent validation. | FAB-004 | Prompt set covers obligations, gaps, remediation, evidence health, score story, and lineage. |

## Epic 2: Foundry Tool Validation

**Goal:** Prove Foundry can invoke the Fabric Data Agent tool reliably before the app depends on it.

| Task ID | Task | Dependencies | Acceptance criteria |
| --- | --- | --- | --- |
| FND-001 | Document current Foundry project, agent, version, Fabric tool connection, and managed identities. | None | The documented state includes project endpoint, agent name/version, tool type, and connection ID. |
| FND-002 | Diagnose the current Fabric tool `504 Gateway Time-out` from `FabricTest`. | FND-001, FAB-005 | Root cause is identified as permissions, tool connection, Data Agent config, timeout, or service issue. |
| FND-003 | Grant required Fabric workspace/item/data-source permissions to the Foundry agent identity or connection principal. | FND-002 | Foundry tool invocation no longer fails due to authorization. |
| FND-004 | Validate `FabricTest` can answer the smoke prompts through the Fabric Data Agent tool. | FND-003, FAB-005 | Foundry invocation returns Fabric-grounded answers for all smoke prompts. |
| FND-005 | Capture invocation request/response examples for application integration. | FND-004 | Examples include success, missing permission, missing data, and timeout/error cases. |

## Epic 3: Agent Contracts

**Goal:** Define typed request/response contracts so every agent is traceable, testable, and validation-first.

| Task ID | Task | Dependencies | Acceptance criteria |
| --- | --- | --- | --- |
| CNT-001 | Define `FabricQuestionRequest` and `FabricQuestionResponse`. | FND-005 | Contract captures prompt, agent name/version, answer, citations/tool evidence, confidence, and errors. |
| CNT-002 | Define `ControlMappingRequest` and `ControlMappingResponse`. | FAB-003 | Response requires obligation IDs, mapped entity IDs, rationale, confidence, and source refs. |
| CNT-003 | Define `GapAnalysisRequest` and `GapAnalysisResponse`. | FAB-003 | Response requires gap IDs, severity, evidence/maturity drivers, blast-radius refs, and rationale. |
| CNT-004 | Define `RemediationRequest` and `RemediationResponse`. | CNT-003 | Response requires owner, priority, effort estimate, dependency refs, expected outcome, and confidence. |
| CNT-005 | Define score narration contract for score facts and executive explanation. | FAB-003 | Response cannot alter numeric scores and must cite score drivers. |
| CNT-006 | Add contract validation tests for malformed IDs, missing citations, unsupported entities, and invalid enum values. | CNT-001 through CNT-005 | Tests fail invalid agent responses before downstream export. |

## Epic 4: Repo Client Integration

**Goal:** Add reusable Entra-authenticated clients for Foundry agent invocation and Fabric Data Agent access through Foundry.

| Task ID | Task | Dependencies | Acceptance criteria |
| --- | --- | --- | --- |
| CLI-001 | Add settings for `FOUNDRY_PROJECT_ENDPOINT`, `FOUNDRY_FABRIC_AGENT_NAME`, `FOUNDRY_FABRIC_AGENT_VERSION`, `FABRIC_WORKSPACE_ID`, and `FABRIC_DATA_AGENT_ID`. | FND-005 | Missing settings produce explicit configuration errors. |
| CLI-002 | Implement `FoundryAgentClient` using Entra authentication only. | CLI-001 | Client invokes a configured Foundry agent and returns typed raw response metadata. |
| CLI-003 | Implement `FabricDataAgentClient` as a narrow wrapper over the Foundry Fabric-tool agent path. | CLI-002, CNT-001 | Client returns `FabricQuestionResponse` with answer and tool evidence. |
| CLI-004 | Add `regimpact ask-fabric "<question>"` CLI command. | CLI-003 | Command returns Fabric-grounded answer or explicit Foundry/Fabric error. |
| CLI-005 | Add mocked tests for success, missing configuration, auth failure, tool timeout, and malformed response. | CLI-004 | Tests assert no fallback behavior and no API-key settings. |

## Epic 5: Agent Workflow Implementation

**Goal:** Replace wrapper-style local agent behavior with Foundry/Fabric-backed agent workflow steps.

| Task ID | Task | Dependencies | Acceptance criteria |
| --- | --- | --- | --- |
| AGT-001 | Implement Foundry-backed Regulation Interpreter contract path for uploaded/new regulation text. | CNT-006 | Interpreter returns schema-valid obligations or explicit model/config error. |
| AGT-002 | Implement Fabric-backed Control Mapper. | CLI-003, CNT-002 | Mapper queries Fabric context and returns validated mappings only to known entities. |
| AGT-003 | Implement Fabric-backed Gap Analyst. | AGT-002, CNT-003 | Gap Analyst returns validated gaps with evidence and maturity drivers. |
| AGT-004 | Implement Fabric-backed Remediation Planner. | AGT-003, CNT-004 | Planner returns validated actions grounded in gap/control/owner context. |
| AGT-005 | Implement Compliance Score Narrator. | AGT-004, CNT-005 | Narrator explains score movement without modifying score facts. |
| AGT-006 | Implement Executive Q&A Agent command or service endpoint. | CLI-004 | Users can ask cross-cutting compliance questions grounded by Fabric. |
| AGT-007 | Implement Audit & Lineage Agent. | FAB-003, CLI-003 | Agent explains lineage/source refs for obligations, gaps, evidence, and exports. |

## Epic 6: Orchestration and Runtime

**Goal:** Provide a coherent end-to-end workflow that coordinates all agents and exports results.

| Task ID | Task | Dependencies | Acceptance criteria |
| --- | --- | --- | --- |
| ORC-001 | Define orchestrator state model for regulation submissions, agent outputs, Fabric evidence, and run status. | AGT-001 through AGT-005 | State model is serializable and includes correlation IDs. |
| ORC-002 | Implement `regimpact run-agent-chain --change <id>` and uploaded-text equivalent. | ORC-001 | Command runs all configured agents or fails explicitly at the blocked step. |
| ORC-003 | Persist agent outputs to Parquet/Fabric-ready tables. | ORC-002 | Outputs include source refs, tool evidence, confidence, and timestamps. |
| ORC-004 | Add Application Insights/OpenTelemetry spans for each agent/tool call. | ORC-002 | Traces include agent name, operation, duration, correlation ID, and error classification. |
| ORC-005 | Package orchestrator path for Foundry Hosted Agent or Container Apps runtime. | ORC-004 | Deployment target can run with managed identity and Entra auth only. |

## Epic 7: Quality, Governance, and Demo Readiness

**Goal:** Make the agent workflow trustworthy, demonstrable, and aligned with governance expectations.

| Task ID | Task | Dependencies | Acceptance criteria |
| --- | --- | --- | --- |
| QLT-001 | Add end-to-end mocked agent-chain tests. | ORC-002 | Tests cover successful run and explicit failures for each external boundary. |
| QLT-002 | Add live validation script gated by environment variables. | FND-004, CLI-004 | Script validates Foundry/Fabric connectivity without secrets or API keys. |
| QLT-003 | Add prompt-injection and unsupported-question test cases for Fabric Q&A. | FAB-005, CLI-004 | Agent refuses unsupported claims and cites available data. |
| QLT-004 | Update demo docs with Fabric Agent Q&A flow and screenshots/placeholders. | AGT-006 | Demo path shows Foundry agent using Fabric Data Agent over exported data. |
| QLT-005 | Update PR/CI quality gates for contract tests and optional live validation. | QLT-001, QLT-002 | CI runs mocked tests by default; live checks are manual/environment-gated. |

## Build Order

1. FAB-001 through FAB-005
2. FND-001 through FND-005
3. CNT-001 through CNT-006
4. CLI-001 through CLI-005
5. AGT-001 through AGT-007
6. ORC-001 through ORC-005
7. QLT-001 through QLT-005

## Immediate Next Task

Start with **FND-002** and **FAB-005** together: prove the Foundry `FabricTest` agent can invoke the Fabric Data Agent tool against a small smoke prompt set. This de-risks the integration before code depends on the tool path.
