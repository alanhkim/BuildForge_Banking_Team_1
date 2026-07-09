# Fabric Agent Data Specification

## Purpose

This spec defines the Fabric data contract needed by the application agents. It
is written for data engineering so the Lakehouse, semantic model, ontology, and
Fabric Data Agent can be aligned to the agent workflow code.

The application assumes Fabric is available and that agents can query governed
data through the Fabric Data Agent path. Agent behavior must remain
Foundry/Fabric-first, Entra-authenticated, citation-backed, and explicit on
configuration, permission, timeout, or missing-data failures.

## Shared grounding requirements

All agent answers must include:

- Fabric source name: `RegImpactLH`, `RegImpactSM_V1`, or `RegImpact_Ontology`.
- Table, view, measure, field, relationship, or entity names used.
- Entity IDs used in decisions, for example `CHG-DORA`, `OBL-DORA-01`,
  `CTL-OR-3`, `GAP-OBL-DORA-01-CTL-OR-3`, `BU-OPS`.
- Tool evidence: tool name, data source, generated SQL/DAX/graph query if
  available, and source references.
- Confidence: `low`, `medium`, or `high`.

Do not provide inferred mappings, gaps, owners, score values, or lineage hops
without a supporting Fabric source reference.

## Canonical source items

| Fabric item | Intended use |
| --- | --- |
| `RegImpactLH` Lakehouse | Detail/naming queries over base tables and relationship rows. |
| `RegImpactSM_V1` Semantic model | Score and aggregate metrics using governed measures. |
| `RegImpact_Ontology` Ontology | Multi-hop graph traversal and blast-radius paths. |
| `Regulatory Impact Data Agent` | Natural-language query surface across the sources above. |

## Required Lakehouse tables

| Table | Required columns | Used by |
| --- | --- | --- |
| `regulations` | `ID`, `Name`, `Short_Code`, `Regulator`, `Jurisdiction`, `Domain`, `Description`, `As_Of` | Executive Q&A, Audit/Lineage |
| `regulatory_changes` | `ID`, `Regulation_ID`, `Title`, `Reference`, `Summary`, `Change_Type`, `Published_Date`, `Effective_Date`, `Criticality`, `As_Of` | All agents |
| `obligations` | `ID`, `Change_ID`, `Regulation_ID`, `Statement`, `Article`, `Theme`, `Criticality`, `Target_Maturity`, `As_Of` | Control Mapper, Gap Analyst, Lineage |
| `controls` | `ID`, `Name`, `Control_Family`, `Capability_ID`, `Description`, `Status`, `Maturity`, `Owner_Unit_ID`, `As_Of` | Control Mapper, Gap Analyst, Remediation |
| `capabilities` | `ID`, `Name`, `Domain`, `As_Of` | Control Mapper, Gap Analyst, Score Narrator |
| `technologies` | `ID`, `Name`, `Vendor`, `Category`, `Is_Microsoft`, `As_Of` | Control Mapper, Gap Analyst, Lineage |
| `evidence` | `ID`, `Control_ID`, `Evidence_Type`, `Name`, `Status`, `Technology_ID`, `As_Of` | Gap Analyst, Remediation, Evidence Health |
| `systems` | `ID`, `Name`, `Category`, `Vendor`, `Criticality`, `As_Of` | Gap Analyst, Lineage |
| `business_processes` | `ID`, `Name`, `Value_Chain`, `Owner_Unit_ID`, `As_Of` | Gap Analyst, Remediation, Lineage |
| `products` | `ID`, `Name`, `Product_Line`, `Owner_Unit_ID`, `As_Of` | Gap Analyst, Executive Q&A |
| `data_domains` | `ID`, `Name`, `Classification`, `Contains_PII`, `As_Of` | Gap Analyst, Audit/Lineage |
| `business_units` | `ID`, `Name`, `Division`, `As_Of` | Remediation, Executive Q&A |
| `risks` | `ID`, `Name`, `Category`, `Inherent_Rating`, `As_Of` | Gap Analyst, Executive Q&A |
| `gaps` | `ID`, `Obligation_ID`, `Change_ID`, `Control_ID`, `Severity`, `Maturity_Shortfall`, `Rationale`, `Affected_System_IDs`, `Affected_Process_IDs`, `Affected_Product_IDs`, `Affected_Data_Domain_IDs`, `As_Of` | Gap Analyst, Remediation, Score Narrator |
| `remediation_actions` | `ID`, `Gap_ID`, `Action`, `Action_Type`, `Estimated_Effort_Days`, `Priority`, `Target_Unit_ID`, `As_Of` | Remediation, Score Narrator |
| `compliance_scores` | `Change_ID`, `Scope_Type`, `Scope_ID`, `Scope_Name`, `Scenario`, `Score`, `Status`, `As_Of` | Score Narrator, Executive Q&A |
| `relationships` | `Source_ID`, `Source_Type`, `Target_ID`, `Target_Type`, `Rel_Type`, `As_Of` | Control Mapper, Lineage, blast radius |

## Recommended curated views

These views reduce prompt complexity and make the agent behavior easier to test.

| View | Columns | Primary agents |
| --- | --- | --- |
| `v_obligation_control_map` | `Change_ID`, `Obligation_ID`, `Obligation_Statement`, `Theme`, `Target_Maturity`, `Control_ID`, `Control_Name`, `Control_Family`, `Control_Maturity`, `Capability_ID`, `Capability_Name`, `Evidence_ID`, `Evidence_Status`, `Source_Relationship_ID` | Control Mapper |
| `v_gap_blast_radius` | `Change_ID`, `Gap_ID`, `Obligation_ID`, `Control_ID`, `Severity`, `Maturity_Shortfall`, `Affected_Entity_Type`, `Affected_Entity_ID`, `Affected_Entity_Name`, `Owner_Unit_ID`, `Owner_Unit_Name` | Gap Analyst |
| `v_remediation_priority` | `Change_ID`, `Gap_ID`, `Severity`, `Priority`, `Estimated_Effort_Days`, `Target_Unit_ID`, `Target_Unit_Name`, `Action`, `Action_Type`, `Maturity_Shortfall`, `Evidence_Status` | Remediation Planner |
| `v_compliance_score_story` | `Change_ID`, `Scenario`, `Score`, `Status`, `Score_Drop`, `Score_Recovered`, `Gap_Count`, `Critical_Gap_Count`, `Total_Remediation_Effort_Days` | Score Narrator, Executive Q&A |
| `v_evidence_health` | `Change_ID`, `Control_ID`, `Control_Name`, `Evidence_ID`, `Evidence_Name`, `Evidence_Status`, `Technology_ID`, `Technology_Name`, `Capability_ID`, `Capability_Name` | Gap Analyst, Executive Q&A |
| `v_product_regulatory_exposure` | `Change_ID`, `Regulation_ID`, `Product_ID`, `Product_Name`, `Process_ID`, `Process_Name`, `System_ID`, `System_Name`, `Data_Domain_ID`, `Data_Domain_Name`, `Gap_ID`, `Severity` | Executive Q&A, Audit/Lineage |

If the views are not available, the Fabric Data Agent must be able to build the
equivalent joins from base tables and cite those base tables.

## Semantic model measures

| Measure | Required behavior |
| --- | --- |
| `Score (As-is)` | Overall score for `Scenario = AsIs`. |
| `Score (Post-change)` | Overall score for `Scenario = PostChange`. |
| `Score (Post-remediation)` | Overall score for `Scenario = PostRemediation`. |
| `Score Drop` | Difference between As-is and Post-change. |
| `Score Recovered` | Difference between Post-remediation and Post-change. |
| `Gap Count` | Count of distinct gaps in current filter context. |
| `Evidence Gaps` | Count of gaps driven by missing, stale, or partial evidence. |
| `High or Critical Gaps` | Count of high/critical gaps. |
| `Total Maturity Shortfall` | Sum of maturity shortfall across gaps. |
| `Total Remediation Effort (days)` | Sum of estimated remediation effort days. |
| `Remediation Action Count` | Count of remediation actions. |

## Agent-specific contracts

The application code separates the Fabric-backed agents into individual modules:

| Agent | Module |
| --- | --- |
| Control Mapper | `src/regimpact/agents/fabric_control_mapper.py` |
| Gap Analyst | `src/regimpact/agents/fabric_gap_analyst.py` |
| Remediation Planner | `src/regimpact/agents/fabric_remediation_planner.py` |
| Compliance Score Narrator | `src/regimpact/agents/fabric_score_narrator.py` |
| Executive Q&A Agent | `src/regimpact/agents/fabric_executive_qa.py` |
| Audit & Lineage Agent | `src/regimpact/agents/fabric_lineage.py` |

`src/regimpact/agents/fabric_workflow.py` is the shared harness/parser utility,
not the ownership boundary for the individual agents.

### Control Mapper

**Goal:** Map obligations to existing controls, capabilities, technologies, and
evidence expectations.

**Needs:**

- Obligations for the requested change.
- Existing controls and their maturity, status, owner, and capability.
- Relationships from obligations to controls and data domains.
- Evidence and technology linked to controls.
- Valid IDs for every returned mapping.

**Preferred sources:** `v_obligation_control_map`, `relationships`,
`obligations`, `controls`, `capabilities`, `evidence`, `technologies`.

**Must return:** `obligation_id`, `control_id`, `capability_id`, rationale,
confidence, source references.

### Gap Analyst

**Goal:** Identify maturity/evidence gaps and blast radius.

**Needs:**

- Obligation target maturity and criticality.
- Control maturity/status and evidence status.
- Existing computed gaps when available.
- Affected systems, processes, products, data domains, risks, and owners.
- Relationship or ontology paths supporting the blast radius.

**Preferred sources:** `v_gap_blast_radius`, `v_evidence_health`, `gaps`,
`relationships`, `RegImpact_Ontology`.

**Must return:** `gap_id`, `obligation_id`, `control_id`, severity, maturity
shortfall, rationale, source references.

### Remediation Planner

**Goal:** Produce prioritized owner-assigned remediation actions.

**Needs:**

- Gap severity, maturity shortfall, evidence status, and affected entities.
- Existing remediation actions and estimated effort.
- Target/owner business units.
- Dependencies implied by affected processes/systems/technologies.

**Preferred sources:** `v_remediation_priority`, `remediation_actions`, `gaps`,
`business_units`, `controls`, `evidence`.

**Must return:** `remediation_id`, `gap_id`, owner unit, priority, estimated
effort days, action, source references.

### Compliance Score Narrator

**Goal:** Explain as-is, post-change, and post-remediation score movement without
changing numeric score facts.

**Needs:**

- Scenario score rows for each change.
- Gap counts, severity mix, evidence gaps, and remediation effort.
- Weakest capabilities or score drivers.

**Preferred sources:** `v_compliance_score_story`, `compliance_scores`,
`RegImpactSM_V1` measures, `fact_compliance_score`.

**Must return:** narrative, `as_is`, `post_change`, `post_remediation`, source
references, tool evidence.

### Executive Q&A Agent

**Goal:** Answer business questions across impact, score, remediation, and
exposure.

**Needs:**

- All curated views above.
- Semantic model measures for scores and aggregate metrics.
- Glossary of entity synonyms and business-friendly names.

**Preferred sources:** Fabric Data Agent over `RegImpactLH`, `RegImpactSM_V1`,
and `RegImpact_Ontology`.

**Must return:** direct answer, citations, tool evidence, confidence. Unsupported
questions must be refused explicitly.

### Audit & Lineage Agent

**Goal:** Explain source references, lineage, and traceability.

**Needs:**

- `relationships` table.
- Purview lineage export or equivalent lineage table.
- Ontology paths for multi-hop traversal.
- Entity names and source references for every hop.

**Preferred sources:** `relationships`, `RegImpact_Ontology`,
`v_product_regulatory_exposure`, Purview lineage assets.

**Must return:** lineage hops with source ID, relationship, target ID, source
references, and tool evidence.
