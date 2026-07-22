targetScope = 'subscription'

@description('Resource ID of the centralized Log Analytics workspace that all resource diagnostic logs should be routed to.')
param logAnalyticsWorkspaceId string = '/subscriptions/4a23dcea-b762-4569-b255-3c45517c941b/resourceGroups/rg-operations-shared/providers/Microsoft.OperationalInsights/workspaces/la-centralized-sentinel'

@description('Region for the assignment resource.')
param location string = 'eastus'

// Enable allLogs category group resource logging for supported resources to Log Analytics —
// built-in initiative (140 member policies, one per supported resource type) that deploys a
// diagnostic setting routing allLogs to the centralized workspace for every existing resource,
// and automatically catches newly created resources of the same types going forward since the
// underlying policies are DeployIfNotExists and evaluated on every resource write.
module centralizedLogging 'modules/policy-assignment-subscription.bicep' = {
  params: {
    assignmentName: 'centralized-diag-logging'
    displayName: 'Enable allLogs category group resource logging for supported resources to Log Analytics'
    policyDefinitionId: '/providers/Microsoft.Authorization/policySetDefinitions/0884adba-2312-4468-abeb-5422caed1038'
    location: location
    identityType: 'SystemAssigned'
    parameters: {
      logAnalytics: {
        value: logAnalyticsWorkspaceId
      }
    }
    roleDefinitionIds: [
      '92aaf0da-9dab-42b6-94a3-d43ce8d16293' // Log Analytics Contributor, required by all 140 member policies
    ]
  }
}

output centralizedLoggingAssignmentId string = centralizedLogging.outputs.assignmentId
