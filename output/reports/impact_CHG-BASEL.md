# Regulatory Change Impact Assessment — BASEL regulatory update

- **Change ID:** CHG-BASEL
- **Regulation:** REG-BASEL
- **Criticality:** Critical
- **Effective date:** 2026-10-02
- **Obligations:** 3
- **Gaps identified:** 4  ({'Medium': 1, 'Low': 3})
- **Estimated remediation effort:** 145 person-days

## Blast radius
- **Products affected:** Derivatives, FX & Spot, Mortgages, Personal Loans
- **Processes affected:** AI Model Lifecycle, Data Governance & Quality, Regulatory Reporting, Trading & Settlement
- **Systems affected:** General Ledger / Finance, Data Lakehouse (Fabric), Regulatory Reporting

## Gaps & recommended remediation

| Severity | Shortfall | Gap rationale | Recommended action | Effort (days) | Owner |
|---|---|---|---|---|---|
| Medium | 2 | Control 'Data Quality Rules & Monitoring' is at maturity 1 (Partially Implemented) vs target 3 required by the obligation. Supporting evidence is also Partial, Stale. | Remediate data for Data Quality Rules & Monitoring to close a maturity shortfall of 2. | 70 | BU-DATA |
| Low | 1 | Control 'Critical Data Element Catalog' is at maturity 2 (Partially Implemented) vs target 3 required by the obligation. Supporting evidence is also Partial, Stale. | Remediate data for Critical Data Element Catalog to close a maturity shortfall of 1. | 45 | BU-DATA |
| Low | 0 | Control 'Regulatory Report Production' meets target maturity 3, but 1 evidence artefact(s) are Partial; compliance cannot be demonstrated to the regulator. | Produce and automate compliance evidence for Regulatory Report Production (lineage, logs or attestations). | 15 | BU-FIN |
| Low | 0 | Control 'Report Review & Sign-off' meets target maturity 3, but 1 evidence artefact(s) are Stale; compliance cannot be demonstrated to the regulator. | Produce and automate compliance evidence for Report Review & Sign-off (lineage, logs or attestations). | 15 | BU-FIN |