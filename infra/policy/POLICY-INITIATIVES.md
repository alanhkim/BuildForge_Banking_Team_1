# Governance & Compliance Initiatives — Banking BuildForge

> See the [top-level README](../README.md) for repo structure, prerequisites, and deployment commands.
> This document tracks initiative-level detail only.

This document tracks the Microsoft Sentinel / centralized logging deployment and all Azure
Policy built-in initiatives assigned as part of this environment's governance baseline.

Status legend: **Deployed** | **Staged (not yet deployed)** | **Modified in place**

---

## 1. Microsoft Sentinel & Centralized Logging Workspace

| Field | Value |
|---|---|
| Resource group | `rg-operations-shared` (northcentralus) |
| Log Analytics workspace | `la-centralized-sentinel` |
| SKU / Retention | PerGB2018 / 90 days |
| Sentinel onboarding | Enabled (`Microsoft.SecurityInsights/onboardingStates/default`) |
| Bicep files | `main.bicep`, `modules\sentinel-workspace.bicep` |
| Status | **Deployed** |

---

## 2. Policy Initiatives — Management Group scope

Root file: `policy\main.bicep` (targetScope = `managementGroup`)
Module: `policy\modules\policy-assignment.bicep`

### FedRAMP High
| Field | Value |
|---|---|
| Built-in initiative ID | `d5264498-16f4-418a-b659-fa7ef418175f` |
| Purpose / driver | US federal regulatory compliance baseline (FedRAMP High) |
| Scope | Tenant Root Group (`38b8b03b-4c63-41b4-810f-2b02d862b33a`) |
| Assignment name | `5f01d22c57ca480bb6afaf5b` (existing — reused intentionally to avoid a duplicate assignment) |
| Managed identity / roles | SystemAssigned; no additional role assignment needed (already provisioned) |
| Remediation behavior | Mixed Audit/DeployIfNotExists per member policy |
| Status | **Modified in place** — this deployment only updates the existing assignment (drops a pinned `definitionVersion`); it does not create a new assignment |

### EU AI Act 2024 (1689)
| Field | Value |
|---|---|
| Built-in initiative ID | `1308bccf-446a-4283-a4e0-0c983fe7a572` |
| Purpose / driver | EU AI Act 2024/1689 regulatory compliance mapping |
| Scope | `BuildForge_Root_Group` management group (one level below Tenant Root Group; cascades to subscriptions beneath it) |
| Assignment name | `eu-ai-act-2024-1689` |
| Managed identity / roles | SystemAssigned; no additional role assignment configured |
| Remediation behavior | Mixed Audit/DeployIfNotExists per member policy |
| Status | **Deployed** |

### EU 2022/2555 (NIS2) 2022
| Field | Value |
|---|---|
| Built-in initiative ID | `42346945-b531-41d8-9e46-f95057672e88` |
| Purpose / driver | EU NIS2 Directive — cybersecurity & incident reporting for critical/important sector entities (banking is an explicitly named "essential entity" sector) |
| Scope | `BuildForge_Root_Group` management group |
| Assignment name | `eu-nis2-2022-2555` |
| Managed identity / roles | SystemAssigned; no additional role assignment configured |
| Remediation behavior | 181 member policies, mixed Audit/DeployIfNotExists, all parameters have defaults |
| Status | **Deployed** |

### Configure Microsoft Defender for Cloud plans
| Field | Value |
|---|---|
| Built-in initiative ID | `f08c57cd-dbd6-49a4-a85e-9ae77ac959b0` |
| Purpose / driver | Enables Defender CSPM plus all 11 workload protection plans (Containers, AI, Storage, Servers, SQL on VMs, App Services, SQL, Key Vault, ARM, Open-Source Relational DBs, Cosmos DB) |
| Scope | `BuildForge_Root_Group` management group |
| Assignment name | `defender-for-cloud-plans` |
| Managed identity / roles | SystemAssigned; **Owner** role (`8e3af657-a8ff-443c-a75c-2fe8c4bcb635`) granted at scope — required because member policies are DeployIfNotExists and Owner is the superset role needed across all 12 member policies (some need Owner, others Contributor) |
| Remediation behavior | 12 member policies, all DeployIfNotExists |
| Status | **Deployed** |

### CIS Azure Foundations v3.0.0
| Field | Value |
|---|---|
| Built-in initiative ID | `470a962c-86a0-433b-803a-3c176b5ce79c` |
| Purpose / driver | Latest CIS Azure hardening benchmark — configuration best practices (identity, networking, storage, logging/monitoring, Key Vault) |
| Scope | `BuildForge_Root_Group` management group |
| Assignment name | `cis-azure-foundations-v3` |
| Managed identity / roles | None — confirmed all 53 member policies default to Audit/AuditIfNotExists effects, so no remediation identity is required |
| Remediation behavior | Audit-only (no DeployIfNotExists/Modify/Deny effects present) |
| Status | **Deployed** |

### CIS Controls v8.1
| Field | Value |
|---|---|
| Built-in initiative ID | `046796ef-e8a7-4398-bbe9-cce970b1a3ae` |
| Purpose / driver | General CIS Controls framework (broader than Azure Foundations — organizational/process controls: asset inventory, data protection, secure configuration, account management, audit logging, incident response, etc., mapped onto Azure resources) |
| Scope | `BuildForge_Root_Group` management group |
| Assignment name | `cis-controls-v8-1` |
| Managed identity / roles | None — confirmed across all 167 member policies: 164 use parameterized effects defaulting to Audit/AuditIfNotExists/Disabled, and the remaining 3 are hardcoded to `audit`/`auditIfNotExists`. No Deny/DeployIfNotExists/Modify effects present, so no remediation identity is required |
| Remediation behavior | Audit-only |
| Status | **Deployed** |

### Audit Public Network Access
| Field | Value |
|---|---|
| Built-in initiative ID | `f1535064-3294-48fa-94e2-6e83095a5c08` |
| Purpose / driver | ALZ security hardening best practice — flags resources across supported services that still allow public network access (precursor/safer alternative to the Preview "Restrict Public Network Access" Deny-capable initiative) |
| Scope | `BuildForge_Root_Group` management group |
| Assignment name | `audit-public-net-access` (shortened from `audit-public-network-access` — policy assignment names have a hard 24-character limit) |
| Managed identity / roles | None — all effects default to Audit/Disabled |
| Remediation behavior | Audit-only |
| Status | **Deployed** |

### Enforce Encryption-in-Transit — HTTPS
| Field | Value |
|---|---|
| Built-in initiative ID | `c7c0ab87-63da-4706-ba95-ff564e38402b` (Preview) |
| Purpose / driver | ALZ security hardening — requires HTTPS-only across supported services |
| Scope | `BuildForge_Root_Group` management group |
| Assignment name | `encrypt-in-transit-https` (shortened from `enforce-encryption-in-transit-https` — policy assignment names have a hard 24-character limit) |
| Effect configuration | **Audit-safe override applied**: `effect_deny` set to `Audit` (default ships as `Deny`); `effect_modify` set to `Disabled` (no audit-only alternative exists for this sub-policy) |
| Managed identity / roles | **SystemAssigned, no role granted.** ARM requires an identity to be present on the assignment whenever the initiative *contains* any Modify-capable policy, even though `effect_modify` is set to `Disabled` here — the identity will never actually attempt remediation, so no role assignment was added |
| Remediation behavior | Audit-only after override (would otherwise Deny/Modify by default) |
| Status | **Deployed** |

### Enforce Encryption-in-Transit — TLS Version
| Field | Value |
|---|---|
| Built-in initiative ID | `f1fe6a81-eee9-47b8-9f7f-80685141209e` (Preview) |
| Purpose / driver | ALZ security hardening — requires minimum TLS 1.2 across supported services |
| Scope | `BuildForge_Root_Group` management group |
| Assignment name | `encrypt-in-transit-tls` (shortened from `enforce-encryption-in-transit-tls` — policy assignment names have a hard 24-character limit) |
| Effect configuration | **Audit-safe override applied**: `effect_deny` set to `Audit` (default ships as `Deny`); `effect_deployIfNotExists` and `effect_modify` set to `Disabled` (no audit-only alternative exists for either) |
| Managed identity / roles | **SystemAssigned, no role granted** — same ARM requirement as the HTTPS initiative above |
| Remediation behavior | Audit-only after override (would otherwise Deny/DeployIfNotExists/Modify by default) |
| Status | **Deployed** |

### Configure Azure Activity logs to stream to specified Log Analytics workspace
| Field | Value |
|---|---|
| Built-in policy ID | `2465583e-4e78-4c15-b6be-a36cbc7c8b0f` (single policy, not an initiative) |
| Purpose / driver | Streams the subscription-level **Activity Log** (management-plane audit trail — resource creation/deletion/modification, policy evaluations, service health, alerts) to the centralized workspace. Distinct from the "allLogs" resource diagnostic initiative in Section 3, which only covers per-resource diagnostic logs, not the Activity Log itself. Explicitly called out in FedRAMP AU (audit) controls |
| Scope | `BuildForge_Root_Group` management group — applies to every subscription under this MG, including any added in the future |
| Assignment name | `activity-log-to-law` |
| Target workspace | `la-centralized-sentinel` (`rg-operations-shared`) |
| Managed identity / roles | SystemAssigned; **Contributor** (`749f88d5-cbae-40b8-bcfc-e573ddc772fa`, required to create the diagnostic setting on each subscription) + **Log Analytics Contributor** (`92aaf0da-9dab-42b6-94a3-d43ce8d16293`, required to write into the target workspace), both granted at `BuildForge_Root_Group` scope |
| Remediation behavior | DeployIfNotExists |
| Status | **Deployed** |

### DORA 2022/2554
| Field | Value |
|---|---|
| Built-in initiative ID | `f9c0485f-da8e-43b5-961e-58ebd54b907c` |
| Purpose / driver | EU Digital Operational Resilience Act (DORA) 2022/2554 regulatory compliance mapping — ICT risk management for financial entities (banking is in scope) |
| Scope | `BuildForge_Root_Group` management group |
| Assignment name | `51b4336e9402450eb3c0b7b8` (originally created out-of-band via the Azure portal on 2026-07-10; reused intentionally so this deployment updates the existing assignment in place instead of creating a duplicate) |
| Managed identity / roles | SystemAssigned; no additional role assignment configured (matches the identity/role state of the original out-of-band assignment) |
| Remediation behavior | Mixed Audit/DeployIfNotExists per member policy (includes `IncludeArcMachines` parameters for two member policies) |
| Status | **Deployed** — brought under IaC management; assignment name/scope/parameters preserved from the original manual assignment |

---

## 3. Policy Initiatives — Subscription scope

Root file: `policy\diagnostics-main.bicep` (targetScope = `subscription`)
Module: `policy\modules\policy-assignment-subscription.bicep`

### Centralized Diagnostic Logging (allLogs → Sentinel LAW)
| Field | Value |
|---|---|
| Built-in initiative ID | `0884adba-2312-4468-abeb-5422caed1038` ("Enable allLogs category group resource logging for supported resources to Log Analytics") |
| Purpose / driver | Automatically deploys diagnostic settings (allLogs category group) routing supported Azure resources' logs to the centralized Sentinel Log Analytics workspace — covers existing resources and auto-remediates newly created resources of the same types going forward |
| Scope | Subscription (`Banking BuildForge Team 1`, `4a23dcea-b762-4569-b255-3c45517c941b`) |
| Assignment name | `centralized-diag-logging` (shortened from `centralized-diagnostic-logging` — policy assignment names have a hard 24-character limit) |
| Target workspace | `la-centralized-sentinel` (`rg-operations-shared`) |
| Managed identity / roles | SystemAssigned; **Log Analytics Contributor** (`92aaf0da-9dab-42b6-94a3-d43ce8d16293`) granted at subscription scope — required by all 140 member policies |
| Resource type / location coverage | Defaults: all 140 supported resource types, all regions (`*`) |
| Remediation behavior | 140 member policies, all DeployIfNotExists |
| Status | **Deployed** |

---

## 4. Open Decisions / Not Yet Added

- Older CIS Azure Foundations versions (v1.1.0, v1.3.0, v1.4.0, v2.0.0, v2.1.0) were reviewed and intentionally not selected to avoid redundant/conflicting compliance reporting — only one CIS Azure Foundations version (v3.0.0, above) should ever be assigned at a time.
- **CIS Kubernetes benchmark** (preview) — out of scope unless AKS workloads are introduced.
- **Recommended for a FedRAMP-compliant enterprise banking landing zone, not yet added:**
  - **PCI DSS v4.0.1** (`a06d5deb-24aa-4991-9d58-fa7563154e31`) — if card payment processing is in scope.
  - **NIST SP 800-53 Rev. 5** (`179d1daa-458f-4e47-8086-2a68d0d6c38f`) — extends the control mapping FedRAMP High already provides; often required as a distinct audit artifact.
  - **SOC 2 Type 2** (`4054785f-702b-4a98-9215-009cbd58b141`) / **ISO/IEC 27001:2022** (`5e4ff661-23bf-42fa-8e3a-309a55091cc7`) — typically driven by specific customer/contractual requirements rather than technical need; confirm before adding.
  - **Restrict Public Network Access across Azure Services** (`a3d07baa-2640-4810-9814-c8c4bbfc21a6`, Preview) — Deny/Modify-capable enforcement version of the audit-only initiative already added; consider only after validating the audit findings from `audit-public-network-access` don't flag legitimate exceptions.
  - **Enforce Encryption-at-Rest with Customer Managed Keys (CMK)** (`f15f4d95-c59c-4395-9317-be6978d0743f`, Preview) and the HSM-backed variant (`7a00a7fc-fdf4-4ad8-8fa2-a94acc223e8e`, Preview) — high-value for banking data-at-rest control requirements, but requires Key Vault CMK infrastructure to already be in place before enabling any Deny effect.
  - **Enforce Encryption-in-Use** (`7a76da03-ec94-45ea-a4fd-496c350c2a63`, Preview) — confidential computing enforcement; not all services support it yet.
- **Not policy-driven, but core to a secure banking landing zone** (flagged for awareness, no Bicep module applies):
  - Management group hierarchy hardening (platform/landing-zones/corp/online subscription split) — currently a flat `BuildForge_Root_Group`.
  - Hub-spoke network topology with mandatory Azure Firewall egress inspection.
  - Privileged Identity Management (PIM) for just-in-time elevated access.
  - Customer Lockbox and Azure Dedicated/Managed HSM for key custody.
  - Azure Backup Center coverage — no strong generic built-in initiative exists for "require backup on all VMs" as a single bundle; typically requires a custom policy.

---

## Repository

Deployment target (once validated and approved): `https://github.com/alanhkim/BuildForge_Banking_Team_1/tree/brdenico-ai-design/infra`
