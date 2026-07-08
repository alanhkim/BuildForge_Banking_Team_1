# Constitution

## Mission
A generic Python-based framework to assess regulatory change impact and score compliance using a synthetic 5-layer digital twin.

## Scope & Boundaries
- **In-Scope:** Foundry/Fabric-first agentic execution, Fabric-compatible numbered notebooks, synthetic data generation, Microsoft Agent Framework integration, and Fabric Data Agent integration.
- **Out-of-Scope:** Cloud infrastructure provisioning (IaC/Bicep/Terraform handled by another team). Real banking data ingestion.

## Realism Requirements
- Fabric Lakehouse Delta tables
- Purview lineage/glossary exports
- Fabric Data Agent readiness

## Engineering Guardrails
- **Quality Gates:** `ruff` for linting, `pytest` for unit testing, and secret scanning (TruffleHog/GHAS).
- **Execution:** Must be runnable via CLI (`regimpact`) and Fabric notebooks.
