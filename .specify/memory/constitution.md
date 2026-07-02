# Constitution

## Mission
A generic Python-based framework to assess regulatory change impact and score compliance using a synthetic 5-layer digital twin.

## Scope & Boundaries
- **In-Scope:** Core Python engine, Fabric-compatible numbered notebooks, synthetic data generation, local offline execution with Azure OpenAI fallback.
- **Out-of-Scope:** Cloud infrastructure provisioning (IaC/Bicep/Terraform handled by another team). Real banking data ingestion.

## Realism Requirements
- Fabric Lakehouse Delta tables
- Purview lineage/glossary exports
- Fabric Data Agent readiness

## Engineering Guardrails
- **Quality Gates:** `ruff` for linting, `pytest` for unit testing, and secret scanning (TruffleHog/GHAS).
- **Execution:** Must be runnable via CLI (`regimpact`) and Fabric notebooks.
