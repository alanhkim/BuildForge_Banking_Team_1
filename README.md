# Regulatory Change Impact Intelligence Framework

BuildForge Banking Team 1 is building a reusable financial-services accelerator for regulatory change impact analysis. The current focus is a Python-based framework that turns an incoming regulation or regulatory update into structured obligations, maps those obligations to a synthetic bank estate, identifies compliance gaps, scores the impact, and exports Fabric/Purview-ready data products.

The project is a Foundry/Fabric-first agentic application built on Microsoft Agent Framework, Azure AI Foundry, Microsoft Entra ID, Microsoft Fabric, Fabric Data Agent, and Microsoft Purview.

## Overall goals

- Convert regulatory change text or catalog entries into traceable obligations.
- Map obligations to bank controls, capabilities, systems, processes, products, data domains, technologies, and evidence.
- Detect maturity and evidence gaps through the agentic assessment workflow.
- Produce prioritized remediation actions and before/after compliance scores.
- Export explicit Parquet and Gold star-schema data suitable for Microsoft Fabric Delta Lake ingestion.
- Support Fabric Data Agent and Purview governance scenarios downstream.
- Run Azure AI Foundry Hosted Agent execution with Entra-only authentication.
- Surface Foundry/Fabric configuration or availability failures explicitly instead of masking them with fallback behavior.

## Current design

The framework is organized around a Foundry/Fabric-first agentic workflow with explicit data contracts for Fabric ingestion.

| Layer | Current design |
| --- | --- |
| Input | Catalog changes from `src/regimpact/catalog.yaml` plus uploaded regulation text fixtures under `data/regulations/`. |
| Interpreter | `InterpreterAgent` validates `InterpretRequest` and uses Microsoft Agent Framework / Foundry as the agent execution path. |
| Foundry adapter | `src/regimpact/agents/foundry_interpreter.py` uses Microsoft Agent Framework + Entra credential setup. API keys and Semantic Kernel are not supported. |
| Core engine | Synthetic estate generation, control mapping, gap analysis, remediation, and scoring. |
| Outputs | CSV/Parquet entity tables, relationships, Gold star schema, graph artifacts, Markdown reports, and Purview glossary/lineage assets. |
| Consumers | Microsoft Fabric Lakehouse, Power BI semantic model, Fabric Data Agent, Microsoft Purview, and local CLI demos. |
| Quality gates | `python -m ruff check .` and `python -m pytest`. |

Architecture diagrams:

- [`docs/agent-workflow-architecture.excalidraw`](docs/agent-workflow-architecture.excalidraw)
- [`docs/agent-workflow-architecture.svg`](docs/agent-workflow-architecture.svg)
- [`docs/azure-architecture.excalidraw`](docs/azure-architecture.excalidraw)
- [`docs/azure-architecture.svg`](docs/azure-architecture.svg)

## What has been built so far

- Python package scaffold under `src/regimpact`.
- Typed interpreter contracts in `src/regimpact/contracts.py`.
- Regulation catalog in `src/regimpact/catalog.yaml`.
- Synthetic bank estate generator in `src/regimpact/generator.py`.
- Regulation Interpreter target architecture using Microsoft Agent Framework / Foundry with Entra-only configuration.
- Control Mapper, Gap Analysis, Remediation, and Scoring engines.
- CLI entry point:

  ```bash
  python -m regimpact list-changes
  python -m regimpact generate
  python -m regimpact interpret --file data/regulations/eu_ai_act_high_risk.txt --regulation REG-AIACT --name "EU AI Act" --title "High-risk AI update"
  python -m regimpact analyze --change CHG-DORA
  python -m regimpact score --change CHG-DORA
  python -m regimpact demo
  python -m regimpact audit
  python -m regimpact gold
  ```

- Fabric/Purview-oriented exports:
  - entity tables and relationships as CSV and Parquet
  - Gold star schema as CSV and Parquet
  - graph JSON and GraphML
  - impact reports
  - Purview glossary and lineage assets
- Uploaded-regulation demo fixture:
  - `data/regulations/eu_ai_act_high_risk.txt`
- Regression coverage for:
  - interpreter contract behavior
  - schema validation
  - Foundry integration boundaries
  - CLI commands
  - impact/scoring invariants
  - export/audit behavior

## Local development

Install the package in editable mode:

```bash
python -m pip install -e .
```

Install Foundry dependencies when working on agent integration:

```bash
python -m pip install -e ".[foundry]"
```

Run quality gates:

```bash
python -m ruff check .
python -m pytest
```

## Streamlit UI (local development)

The Streamlit UI wraps the same pipeline the CLI runs (`AgentPipeline.run_text` + `run_post_pipeline_exports`), so both surfaces produce identical outputs and emit the same per-stage events.

Install the UI extra (adds `streamlit>=1.36`):

```bash
python -m pip install -e ".[ui]"
```

Start the local UI server:

```bash
python -m streamlit run src/regimpact/ui/streamlit_app.py
```

The app opens at http://localhost:8501. The same Foundry / Fabric / Purview environment variables the CLI reads from `.env` (`FOUNDRY_PROJECT_ENDPOINT`, `FABRIC_WORKSPACE_ID`, `FABRIC_LAKEHOUSE_ID`, `FABRIC_ONELAKE_DFS_ENDPOINT`, etc.) apply — no separate UI config. Per-session run snapshots are cached under `.streamlit-runs/` (git-ignored) so a page refresh rehydrates without re-running the pipeline.



- Use Microsoft Entra authentication only for Azure AI Foundry integration.
- Do not add API-key authentication, API-key settings, or API-key documentation.
- Do not use Semantic Kernel.
- Do not mask Foundry/Fabric failures with deterministic or offline fallback behavior.
- Validate model output before returning it to downstream engines.
- Export data explicitly in Parquet for Fabric ingestion.

## Backlog required for a functional application

| Area | Required work |
| --- | --- |
| Demo input hardening | Add more uploaded-regulation fixtures and expected outputs for non-DORA examples. |
| CLI contract | Finalize stdout formats, error messages, and exit-code behavior for all commands. |
| Foundry live validation | Test the adapter against a real Azure AI Foundry project using Entra auth. |
| Hosted Agent packaging | Package the interpreter or orchestrator as a Foundry Hosted Agent. |
| Application API | Add an application-facing API layer for submitting regulation text and retrieving results. |
| User interface | Build a UI for uploads, analysis runs, reports, and score exploration. |
| Persistence | Decide how application runs, uploaded documents, outputs, and audit history are stored. |
| Fabric deployment | Automate upload/load of Parquet outputs into Fabric Lakehouse Delta tables. |
| Semantic model | Define and publish the Power BI semantic model over Gold tables. |
| Fabric Data Agent | Ground the Data Agent over Lakehouse/Semantic Model outputs and add curated questions. |
| Purview integration | Convert generated glossary/lineage artifacts into an import or API workflow. |
| Observability | Add tracing, metrics, and application logging for hosted runs. |
| CI/CD | Extend quality gates to include package build, optional dependency checks, diagram validation, and deployment checks. |
| Notebook review | Review coworker notebook assets separately and import only source notebooks, not generated outputs. |

## Deployment details placeholder

Deployment has not been finalized. The intended target architecture is:

1. Azure AI Foundry integration through Microsoft Agent Framework.
2. Foundry Hosted Agent wrapper using Entra authentication.
3. Fabric-backed local/developer workflow that surfaces missing cloud configuration explicitly.
4. Fabric Lakehouse ingestion for generated Parquet outputs.
5. Power BI semantic model and Fabric Data Agent over curated Gold outputs.
6. Purview glossary and lineage publishing.

Future deployment documentation should include:

- Azure resource list and naming conventions.
- Identity model and RBAC assignments.
- Foundry project/model deployment setup.
- Hosted Agent packaging steps.
- Fabric workspace/lakehouse setup.
- Purview import or publication process.
- CI/CD workflow and environment configuration.

## Azure infrastructure placeholder

Infrastructure-as-code assets will live under [`infra/`](infra/). The folder currently contains a placeholder so future Azure deployment work has an agreed location.

Expected future contents include:

- Azure resource group and naming definitions.
- Microsoft Entra app registration, managed identity, and RBAC assignments.
- Azure AI Foundry project/model/Hosted Agent deployment assets.
- Microsoft Fabric workspace, Lakehouse, and data ingestion deployment assets.
- Microsoft Purview account, glossary, and lineage publication assets.
- Observability resources such as Application Insights or Log Analytics.
- Environment parameter files for local, demo, and customer-ready deployments.

## CI/CD and GitHub Actions design placeholder

The repository has quality gates, but the full CI/CD design still needs to be defined. Required GitHub Actions work includes:

- **Pull request quality gate:** run `python -m ruff check .`, `python -m pytest`, dependency installation, and artifact-free working-tree checks.
- **Package validation:** verify editable install, optional `.[foundry]` dependency install, and `python -m regimpact` CLI entry points.
- **Demo smoke workflow:** run `generate`, `interpret`, `analyze`, `score`, `audit`, and `gold` commands against temporary output directories.
- **Security checks:** scan for secrets, API-key references, disallowed Semantic Kernel dependencies, and unsafe generated artifacts.
- **Diagram validation:** parse Excalidraw JSON and SVG XML for committed architecture diagrams.
- **Fabric artifact validation:** verify Parquet files can be generated and read before publishing to Fabric.
- **Foundry integration workflow:** define a manually triggered or environment-gated workflow for live Entra-based Foundry smoke tests.
- **Release workflow:** package the Python engine, publish build artifacts, and attach generated docs/diagrams if needed.
- **Deployment workflow placeholders:** add future jobs for Fabric Lakehouse publishing, Power BI semantic model deployment, Purview import, and Hosted Agent deployment.

Open CI/CD design questions:

- Which workflows run on every PR versus manual dispatch?
- Which Azure/Fabric/Foundry checks require protected environments?
- What branch, tag, or release process promotes demo assets?
- Where should generated Parquet and report artifacts be retained, if at all?
- What status checks should be required before merge?

## Application development placeholder

The current project is an engine and CLI, not yet a full application. A functional application still needs:

- API endpoints for creating an analysis run.
- Document upload and catalog-selection workflow.
- Run status and audit history.
- Report and scorecard views.
- Evidence, control, product, and data-domain drilldowns.
- Authentication and authorization for business users.
- Storage for uploaded source documents and generated outputs.
- Deployment packaging for web/API components.

## Reference documentation

- [`docs/README.md`](docs/README.md): detailed business and architecture overview.
- [`docs/demodocbuild.md`](docs/demodocbuild.md): demo runbook and technical build notes.
- [`docs/agent-plan.md`](docs/agent-plan.md): agent and scoring implementation plan.
- [`docs/regulation-interpreter-agent-service.md`](docs/regulation-interpreter-agent-service.md): Hosted Agent service plan.
- [`.specify/memory/constitution.md`](.specify/memory/constitution.md): project constitution and engineering guardrails.
