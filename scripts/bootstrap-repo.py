#!/usr/bin/env python3

import sys
from pathlib import Path

is_dry_run = '--dry-run' in sys.argv
is_force = '--force' in sys.argv
repo_root = Path.cwd().resolve()

files = {
    '.specify/memory/constitution.md': """# Constitution\n\n## Mission\nA generic Python-based framework to assess regulatory change impact and score compliance using a synthetic 5-layer digital twin.\n\n## Scope & Boundaries\n- **In-Scope:** Core Python engine, Fabric-compatible numbered notebooks, synthetic data generation, local offline execution with Azure OpenAI fallback.\n- **Out-of-Scope:** Cloud infrastructure provisioning (IaC/Bicep/Terraform handled by another team). Real banking data ingestion.\n\n## Realism Requirements\n- Fabric Lakehouse Delta tables\n- Purview lineage/glossary exports\n- Fabric Data Agent readiness\n\n## Engineering Guardrails\n- **Quality Gates:** `ruff` for linting, `pytest` for unit testing, and secret scanning (TruffleHog/GHAS).\n- **Execution:** Must be runnable via CLI (`regimpact`) and Fabric notebooks.\n""",
    '.github/copilot-instructions.md': """# Copilot Instructions\n\n1. Adhere strictly to the project Constitution (`.specify/memory/constitution.md`).\n2. Use Python and `ruff` for all engine logic.\n3. Ensure offline-fallback works for any Azure OpenAI agent calls.\n4. Export data explicitly in Parquet format suitable for Fabric Delta Lake ingestion.\n""",
    '.github/prompts/load-context.prompt.md': "Read the constitution from `.specify/memory/constitution.md` and review the data model from the `docs/` folder before suggesting architectural changes.",
    '.github/prompts/checkpoint.prompt.md': "Please summarize the work completed in this session, map it against the acceptance criteria in the constitution, and list the pending incremental tasks.",
    '.github/prompts/handoff.prompt.md': "Generate a HANDOFF.md summarizing current state, open PRs, failing tests, and instructions for the next agent or engineer picking up this feature.",
    '.github/prompts/audit.prompt.md': "Audit the codebase to ensure all exported Parquet schemas pass Rule 3 (Data type uniformity) and Rule 4 (Referential integrity) as specified in the docs.",
    '.github/workflows/quality-gate.yml': """name: Quality Gate\non: [push, pull_request]\njobs:\n  validate:\n    runs-on: ubuntu-latest\n    steps:\n      - uses: actions/checkout@v4\n      - name: Set up Python\n        uses: actions/setup-python@v4\n        with:\n          python-version: '3.11'\n      - name: Install dependencies\n        run: pip install ruff pytest\n      - name: Run Ruff\n        run: ruff check .\n      - name: Run Pytest\n        run: pytest\n      - name: Secret Scan\n        uses: trufflesecurity/trufflehog@main\n        with:\n          path: ./\n          base: "${{ github.event.repository.default_branch }}"\n          head: HEAD\n""",
    'SECURITY.md': """# Security Policy\n\n## Supported Versions\nOnly the latest main branch is currently supported for security updates.\n\n## Reporting a Vulnerability\nPlease do not open a public issue. Email security@bank-example.com to report vulnerabilities.\n""",
    'CODEOWNERS': "* @briandenicola\n",
    'dependabot.yml': """version: 2\nupdates:\n  - package-ecosystem: "pip"\n    directory: "/"\n    schedule:\n      interval: "weekly"\n""",
    'docs/architecture.md': """# Architecture\n\nThe framework operates a 5-layer typed-edge graph:\n1. Regulation\n2. Control\n3. Capability\n4. Technology\n5. Evidence\n\nA 4-agent pipeline interprets changes, maps controls, performs gap analysis, and generates remediations. Exports feed Microsoft Fabric (Delta tables, Power BI semantic models) and Purview.\n""",
    'docs/adr/0000-adr-template.md': """# ADR 0000: [Title]\n\n**Date:** YYYY-MM-DD\n**Status:** Draft | Proposed | Accepted | Rejected\n\n## Context\n[What is the problem or architectural decision?]\n\n## Decision\n[What is the decision?]\n\n## Consequences\n[What becomes easier or harder?]\n""",
    'docs/threat-model.md': """# Threat Model\n\n- **Risk:** Exposure of synthetic PII data during Fabric ingestion.\n  - **Mitigation:** Microsoft Purview DLP and access restrictions.\n- **Risk:** Prompt injection via regulatory change text.\n  - **Mitigation:** Strict system prompts for the Interpretation agent and fallback deterministic checks.\n- **Risk:** Unintended live system modifications.\n  - **Mitigation:** Output is exclusively exported to Parquet/JSON files for isolated offline/read-only consumption.\n"""
}

print("Starting repository bootstrap...")

created_count = 0
skipped_count = 0
overwritten_count = 0

for file_path, content in files.items():
    full_path = (repo_root / file_path).resolve()
    if repo_root not in [full_path, *full_path.parents]:
        raise ValueError(f"Refusing to write outside repository root: {file_path}")

    directory = full_path.parent

    if not is_dry_run:
        directory.mkdir(parents=True, exist_ok=True)

    exists = full_path.exists()
    
    if exists and not is_force:
        print(f"[SKIPPED] {file_path} (already exists, use --force to overwrite)")
        skipped_count += 1
        continue

    if not is_dry_run:
        full_path.write_text(content, encoding='utf-8')

    if exists:
        print(f"[OVERWRITTEN] {file_path}")
        overwritten_count += 1
    else:
        print(f"[CREATED] {file_path}")
        created_count += 1

print("\\n--- Summary ---")
if is_dry_run:
    print("(DRY RUN - no files were written)")
print(f"Created: {created_count}")
print(f"Skipped: {skipped_count}")
print(f"Overwritten: {overwritten_count}")
