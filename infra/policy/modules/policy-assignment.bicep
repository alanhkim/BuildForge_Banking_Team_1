targetScope = 'managementGroup'

@description('Name of the policy assignment. Reuse an existing name to update in place instead of creating a duplicate assignment.')
param assignmentName string

@description('Display name shown in the Azure portal for this assignment.')
param displayName string

@description('Resource ID of the built-in policy or initiative (policySet) definition to assign.')
param policyDefinitionId string

@description('Region for the assignment resource (required when identity type is not None).')
param location string = 'eastus'

@description('Managed identity type. Use SystemAssigned when the initiative contains DeployIfNotExists/Modify policies.')
@allowed([
  'None'
  'SystemAssigned'
])
param identityType string = 'None'

@description('Policy/initiative parameter values, keyed by parameter name, e.g. { IncludeArcMachines: { value: \'true\' } }.')
param parameters object = {}

@description('Enforcement mode for the assignment.')
@allowed([
  'Default'
  'DoNotEnforce'
])
param enforcementMode string = 'Default'

@description('Role definition IDs (GUIDs, not full resource IDs) to grant to the assignment\'s system-assigned identity at this scope. Required for DeployIfNotExists/Modify initiatives to actually remediate.')
param roleDefinitionIds array = []

resource assignment 'Microsoft.Authorization/policyAssignments@2024-04-01' = {
  name: assignmentName
  location: location
  identity: identityType == 'None' ? null : {
    type: identityType
  }
  properties: {
    displayName: displayName
    policyDefinitionId: policyDefinitionId
    enforcementMode: enforcementMode
    parameters: parameters
  }
}

resource roleAssignments 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for roleId in roleDefinitionIds: if (identityType != 'None') {
  name: guid(managementGroup().id, assignmentName, roleId)
  properties: {
    principalId: assignment.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: tenantResourceId('Microsoft.Authorization/roleDefinitions', roleId)
  }
}]

output assignmentId string = assignment.id
output principalId string = identityType == 'None' ? '' : assignment.identity.principalId
