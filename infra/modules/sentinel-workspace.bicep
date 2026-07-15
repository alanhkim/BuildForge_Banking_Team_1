@description('Azure region for the workspace.')
param location string

@description('Name of the Log Analytics workspace.')
param workspaceName string

@description('Log Analytics SKU.')
param workspaceSku string = 'PerGB2018'

@description('Data retention in days.')
param retentionInDays int = 90

@description('Tags to apply to resources.')
param tags object = {}

resource workspace 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: workspaceName
  location: location
  tags: tags
  properties: {
    sku: {
      name: workspaceSku
    }
    retentionInDays: retentionInDays
    features: {
      enableLogAccessUsingOnlyResourcePermissions: true
    }
  }
}

// Solution resource that surfaces Sentinel in the portal against this workspace.
resource sentinelSolution 'Microsoft.OperationsManagement/solutions@2015-11-01-preview' = {
  name: 'SecurityInsights(${workspace.name})'
  location: location
  tags: tags
  plan: {
    name: 'SecurityInsights(${workspace.name})'
    publisher: 'Microsoft'
    product: 'OMSGallery/SecurityInsights'
    promotionCode: ''
  }
  properties: {
    workspaceResourceId: workspace.id
  }
}

// Formal Sentinel onboarding state for the workspace.
resource sentinelOnboarding 'Microsoft.SecurityInsights/onboardingStates@2023-11-01' = {
  scope: workspace
  name: 'default'
  properties: {
    customerManagedKey: false
  }
  dependsOn: [
    sentinelSolution
  ]
}

output workspaceId string = workspace.id
output workspaceName string = workspace.name
