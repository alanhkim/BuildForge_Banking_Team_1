targetScope = 'managementGroup'

@description('Tenant Root Group ID. FedRAMP High is assigned here (existing assignment, kept as single source of truth).')
param tenantRootGroupId string = '38b8b03b-4c63-41b4-810f-2b02d862b33a'

@description('Management group directly under the tenant root where EU AI Act 2024 will be assigned.')
param buildForgeRootGroupId string = 'BuildForge_Root_Group'

@description('Region for policy assignment resources that require an identity.')
param location string = 'eastus'

@description('Resource ID of the centralized Log Analytics workspace for Activity Log streaming.')
param logAnalyticsWorkspaceId string = '/subscriptions/4a23dcea-b762-4569-b255-3c45517c941b/resourceGroups/rg-operations-shared/providers/Microsoft.OperationalInsights/workspaces/la-centralized-sentinel'

// FedRAMP High — reuses the existing assignment name/scope/parameters so this deployment
// updates the current assignment in place rather than creating a duplicate.
module fedRampHigh 'modules/policy-assignment.bicep' = {
  scope: managementGroup(tenantRootGroupId)
  params: {
    assignmentName: '5f01d22c57ca480bb6afaf5b'
    displayName: 'FedRAMP High'
    policyDefinitionId: '/providers/Microsoft.Authorization/policySetDefinitions/d5264498-16f4-418a-b659-fa7ef418175f'
    location: location
    identityType: 'SystemAssigned'
    parameters: {
      IncludeArcMachines: {
        value: 'true'
      }
    }
  }
}

// EU AI Act 2024 (1689) — new assignment at the BuildForge_Root_Group management group,
// one level below the tenant root, cascading to the subscription(s) beneath it.
module euAiAct2024 'modules/policy-assignment.bicep' = {
  scope: managementGroup(buildForgeRootGroupId)
  params: {
    assignmentName: 'eu-ai-act-2024-1689'
    displayName: 'EU AI Act 2024 1689'
    policyDefinitionId: '/providers/Microsoft.Authorization/policySetDefinitions/1308bccf-446a-4283-a4e0-0c983fe7a572'
    location: location
    identityType: 'SystemAssigned'
  }
}

// EU 2022/2555 (NIS2) 2022 — new assignment at the BuildForge_Root_Group management group,
// same scope as the EU AI Act assignment.
module nis2 'modules/policy-assignment.bicep' = {
  scope: managementGroup(buildForgeRootGroupId)
  params: {
    assignmentName: 'eu-nis2-2022-2555'
    displayName: 'EU 2022/2555 (NIS2) 2022'
    policyDefinitionId: '/providers/Microsoft.Authorization/policySetDefinitions/42346945-b531-41d8-9e46-f95057672e88'
    location: location
    identityType: 'SystemAssigned'
  }
}

// Configure Microsoft Defender for Cloud plans — enables CSPM plus all 11 workload protection
// plans (Containers, AI, Storage, Servers, SQL on VMs, App Services, SQL, Key Vault, ARM,
// open-source relational DBs, Cosmos DB). These are DeployIfNotExists policies, so the
// assignment's identity needs Owner at this scope to actually remediate (turn plans on).
module defenderForCloudPlans 'modules/policy-assignment.bicep' = {
  scope: managementGroup(buildForgeRootGroupId)
  params: {
    assignmentName: 'defender-for-cloud-plans'
    displayName: 'Configure Microsoft Defender for Cloud plans'
    policyDefinitionId: '/providers/Microsoft.Authorization/policySetDefinitions/f08c57cd-dbd6-49a4-a85e-9ae77ac959b0'
    location: location
    identityType: 'SystemAssigned'
    roleDefinitionIds: [
      '8e3af657-a8ff-443c-a75c-2fe8c4bcb635' // Owner (superset of Contributor, required by all 12 member policies)
    ]
  }
}

// CIS Azure Foundations v3.0.0 — latest CIS Azure hardening benchmark. All 53 member policies
// default to Audit/AuditIfNotExists effects, so no managed identity or role assignment is
// required (audit-only, no remediation).
module cisAzureFoundations 'modules/policy-assignment.bicep' = {
  scope: managementGroup(buildForgeRootGroupId)
  params: {
    assignmentName: 'cis-azure-foundations-v3'
    displayName: 'CIS Azure Foundations v3.0.0'
    policyDefinitionId: '/providers/Microsoft.Authorization/policySetDefinitions/470a962c-86a0-433b-803a-3c176b5ce79c'
    location: location
    identityType: 'None'
  }
}

// CIS Controls v8.1 — general CIS Controls framework (broader than Azure Foundations, covers
// organizational/process controls mapped onto Azure resources). Confirmed audit-only: all
// effects are Audit/AuditIfNotExists/Disabled by default, no Deny/DeployIfNotExists/Modify,
// so no managed identity or role assignment is required.
module cisControls 'modules/policy-assignment.bicep' = {
  scope: managementGroup(buildForgeRootGroupId)
  params: {
    assignmentName: 'cis-controls-v8-1'
    displayName: 'CIS Controls v8.1'
    policyDefinitionId: '/providers/Microsoft.Authorization/policySetDefinitions/046796ef-e8a7-4398-bbe9-cce970b1a3ae'
    location: location
    identityType: 'None'
  }
}

output fedRampHighAssignmentId string = fedRampHigh.outputs.assignmentId
output euAiActAssignmentId string = euAiAct2024.outputs.assignmentId
output nis2AssignmentId string = nis2.outputs.assignmentId
output defenderForCloudPlansAssignmentId string = defenderForCloudPlans.outputs.assignmentId
output cisAzureFoundationsAssignmentId string = cisAzureFoundations.outputs.assignmentId
output cisControlsAssignmentId string = cisControls.outputs.assignmentId

// Audit Public Network Access — audit-only by default (no Deny/Modify effects present),
// flags resources across supported services that still allow public network access.
module auditPublicNetworkAccess 'modules/policy-assignment.bicep' = {
  scope: managementGroup(buildForgeRootGroupId)
  params: {
    assignmentName: 'audit-public-net-access'
    displayName: 'Audit Public Network Access'
    policyDefinitionId: '/providers/Microsoft.Authorization/policySetDefinitions/f1535064-3294-48fa-94e2-6e83095a5c08'
    location: location
    identityType: 'None'
  }
}

// Enforce Encryption-in-Transit — HTTPS. Configured audit-safe: the deny-capable sub-policies
// are overridden to Audit instead of Deny, and the Modify-only sub-policy (no audit alternative
// exists for it) is disabled to avoid any risk of blocking or changing resources.
// ARM requires an identity on the assignment because the initiative *contains* Modify-capable
// policies, even though effect_modify is set to Disabled here — no role assignment is granted
// since the identity will never actually attempt remediation.
module encryptionInTransitHttps 'modules/policy-assignment.bicep' = {
  scope: managementGroup(buildForgeRootGroupId)
  params: {
    assignmentName: 'encrypt-in-transit-https'
    displayName: 'Enforce Encryption-in-Transit across Azure Services - HTTPS'
    policyDefinitionId: '/providers/Microsoft.Authorization/policySetDefinitions/c7c0ab87-63da-4706-ba95-ff564e38402b'
    location: location
    identityType: 'SystemAssigned'
    parameters: {
      effect_deny: {
        value: 'Audit'
      }
      effect_modify: {
        value: 'Disabled'
      }
    }
  }
}

// Enforce Encryption-in-Transit — TLS Version (minimum TLS 1.2). Configured audit-safe: the
// deny-capable sub-policies are overridden to Audit, and the Modify/DeployIfNotExists-only
// sub-policies (no audit alternative exists for either) are disabled.
// ARM requires an identity on the assignment because the initiative *contains* Modify/DeployIfNotExists-
// capable policies, even though both effects are set to Disabled here — no role assignment is
// granted since the identity will never actually attempt remediation.
module encryptionInTransitTls 'modules/policy-assignment.bicep' = {
  scope: managementGroup(buildForgeRootGroupId)
  params: {
    assignmentName: 'encrypt-in-transit-tls'
    displayName: 'Enforce Encryption-in-Transit across Azure Services - TLS Version'
    policyDefinitionId: '/providers/Microsoft.Authorization/policySetDefinitions/f1fe6a81-eee9-47b8-9f7f-80685141209e'
    location: location
    identityType: 'SystemAssigned'
    parameters: {
      effect_deny: {
        value: 'Audit'
      }
      effect_deployIfNotExists: {
        value: 'Disabled'
      }
      effect_modify: {
        value: 'Disabled'
      }
    }
  }
}

output auditPublicNetworkAccessAssignmentId string = auditPublicNetworkAccess.outputs.assignmentId
output encryptionInTransitHttpsAssignmentId string = encryptionInTransitHttps.outputs.assignmentId
output encryptionInTransitTlsAssignmentId string = encryptionInTransitTls.outputs.assignmentId

// Configure Azure Activity logs to stream to specified Log Analytics workspace — single built-in
// DeployIfNotExists policy (not an initiative) that routes the subscription-level Activity Log
// (management-plane audit trail: resource creation/deletion/modification, policy evaluations,
// service health, etc.) to the centralized workspace. This is distinct from the allLogs resource
// diagnostic initiative in diagnostics-main.bicep, which only covers per-resource diagnostic logs.
// Assigned at BuildForge_Root_Group so any future subscriptions added under this management group
// are automatically covered without a new assignment.
module activityLogToLaw 'modules/policy-assignment.bicep' = {
  scope: managementGroup(buildForgeRootGroupId)
  params: {
    assignmentName: 'activity-log-to-law'
    displayName: 'Configure Azure Activity logs to stream to specified Log Analytics workspace'
    policyDefinitionId: '/providers/Microsoft.Authorization/policyDefinitions/2465583e-4e78-4c15-b6be-a36cbc7c8b0f'
    location: location
    identityType: 'SystemAssigned'
    parameters: {
      logAnalytics: {
        value: logAnalyticsWorkspaceId
      }
    }
    roleDefinitionIds: [
      '749f88d5-cbae-40b8-bcfc-e573ddc772fa' // Contributor — required to create the diagnostic setting on each subscription
      '92aaf0da-9dab-42b6-94a3-d43ce8d16293' // Log Analytics Contributor — required to write into the target workspace
    ]
  }
}

output activityLogToLawAssignmentId string = activityLogToLaw.outputs.assignmentId

// DORA 2022/2554 — reuses the existing assignment name/scope/parameters (originally created
// out-of-band via the portal on 2026-07-10) so this deployment updates it in place rather than
// creating a duplicate, bringing it under IaC management going forward.
module dora2022 'modules/policy-assignment.bicep' = {
  scope: managementGroup(buildForgeRootGroupId)
  params: {
    assignmentName: '51b4336e9402450eb3c0b7b8'
    displayName: 'DORA 2022 2554'
    policyDefinitionId: '/providers/Microsoft.Authorization/policySetDefinitions/f9c0485f-da8e-43b5-961e-58ebd54b907c'
    location: location
    identityType: 'SystemAssigned'
    parameters: {
      'IncludeArcMachines-f71be03e-e25b-4d0f-b8bc-9b3e309b66c0': {
        value: 'true'
      }
      'IncludeArcMachines-bed48b13-6647-468e-aa2f-1af1d3f4dd40': {
        value: 'true'
      }
    }
  }
}

output dora2022AssignmentId string = dora2022.outputs.assignmentId
