# Banking BuildForge — Infra (Bicep)

Infrastructure-as-code for the Banking BuildForge environment: a centralized Microsoft Sentinel /
Log Analytics workspace, and a set of built-in Azure Policy initiative assignments forming the
FedRAMP High / ALZ security governance baseline.

- **Tenant**: `38b8b03b-4c63-41b4-810f-2b02d862b33a`
- **Management group**: `BuildForge_Root_Group` (directly under the Tenant Root Group)
- **Subscription**: `Banking BuildForge Team 1` (`4a23dcea-b762-4569-b255-3c45517c941b`)

## Repository layout

```
infra/
├── main.bicep                     # Subscription scope — Sentinel + centralized LAW
├── main.parameters.json           # Parameters matching the deployed Sentinel config
├── modules/
│   └── sentinel-workspace.bicep   # LAW + SecurityInsights solution + Sentinel onboarding
└── policy/
    ├── main.bicep                 # Management group scope — all policy initiative assignments
    ├── diagnostics-main.bicep     # Subscription scope — centralized diagnostic logging initiative
    ├── POLICY-INITIATIVES.md      # Full compliance/governance tracking doc (per-initiative detail)
    └── modules/
        ├── policy-assignment.bicep              # Reusable managementGroup-scoped assignment module
        └── policy-assignment-subscription.bicep # Reusable subscription-scoped assignment module
```

Each root template has its own `targetScope` (`subscription` or `managementGroup`) and is deployed
independently — Bicep does not allow mixing scopes in a single file, so these are intentionally
separate deployments rather than one combined template.

## Prerequisites

- Azure CLI (`az`), logged in: `az login`
- Access to the target subscription and management group (Owner or equivalent for policy
  assignment + role assignment operations)
- Bicep tooling — either `az bicep` or a standalone `bicep.exe` for local `bicep build` validation

## Deploying

Always validate before deploying: compile check, then `what-if` against live Azure, then create.

### 1. Sentinel + centralized Log Analytics workspace (subscription scope)

```powershell
cd infra
bicep build main.bicep --stdout                      # compile check
az deployment sub what-if `
  --location northcentralus `
  --template-file main.bicep `
  --parameters main.parameters.json                  # live validation
az deployment sub create `
  --location northcentralus `
  --template-file main.bicep `
  --parameters main.parameters.json                  # deploy
```

### 2. Policy initiatives (management group scope)

```powershell
cd infra\policy
bicep build main.bicep --stdout
az deployment mg what-if `
  --management-group-id "38b8b03b-4c63-41b4-810f-2b02d862b33a" `
  --location eastus `
  --template-file main.bicep
az deployment mg create `
  --management-group-id "38b8b03b-4c63-41b4-810f-2b02d862b33a" `
  --location eastus `
  --template-file main.bicep
```

### 3. Centralized diagnostic logging initiative (subscription scope)

```powershell
cd infra\policy
bicep build diagnostics-main.bicep --stdout
az deployment sub what-if `
  --location eastus `
  --template-file diagnostics-main.bicep `
  --subscription "4a23dcea-b762-4569-b255-3c45517c941b"
az deployment sub create `
  --location eastus `
  --template-file diagnostics-main.bicep `
  --subscription "4a23dcea-b762-4569-b255-3c45517c941b"
```

## Conventions used in this repo

- **Policy assignment names must be ≤ 24 characters** — this is an Azure Policy hard limit, not a
  Bicep/ARM limit. Several assignment names in `policy/main.bicep` and `diagnostics-main.bicep`
  are intentionally abbreviated for this reason (see `POLICY-INITIATIVES.md` for the full-length
  display names).
- **Managed identity is required whenever an initiative *contains* a Modify/DeployIfNotExists
  policy**, even if that specific effect parameter is set to `Disabled` for this assignment. ARM
  validates against the initiative definition, not the resolved effect value.
- **No role assignment is granted to an identity that will never remediate** (e.g., audit-safe
  Encryption-in-Transit assignments) — only initiatives that actually deploy/modify resources have
  a `roleDefinitionIds` entry in their module call.
- Every new module addition follows this cycle: `bicep build --stdout` (compile) → `az deployment
  ... what-if` (live validation) → update `POLICY-INITIATIVES.md` → get sign-off → `az deployment
  ... create` (deploy).

## Governance documentation

See [`policy/POLICY-INITIATIVES.md`](policy/POLICY-INITIATIVES.md) for the full list of assigned
initiatives, their scopes, built-in IDs, managed identity/role configuration, remediation behavior,
deployment status, and open decisions on additional initiatives under consideration.

## Target repository

Once fully validated, these files are intended to be pushed to:
`https://github.com/alanhkim/BuildForge_Banking_Team_1/tree/brdenico-ai-design/infra`
