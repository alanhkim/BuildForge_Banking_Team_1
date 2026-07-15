# Regulatory Change Impact Assessment — High-Risk AI Systems - EU AI Act Requirements

- **Change ID:** CHG-EUAIACT-UPLOAD
- **Regulation:** REG-EUAIACT
- **Criticality:** High
- **Effective date:** 2026-12-31
- **Obligations:** 5
- **Gaps identified:** 12  ({'Medium': 4, 'Low': 6, 'High': 2})
- **Estimated remediation effort:** 650 person-days

## Blast radius
- **Products affected:** Business Loans, Derivatives, FX & Spot, Mortgages, Personal Loans
- **Processes affected:** AI Model Lifecycle, Data Governance & Quality, Lending & Credit Decision, Regulatory Reporting, Trading & Settlement
- **Systems affected:** AI / ML Platform, Credit Risk Engine, General Ledger / Finance, Data Lakehouse (Fabric), Regulatory Reporting

## Gaps & recommended remediation

| Severity | Shortfall | Gap rationale | Recommended action | Effort (days) | Owner |
|---|---|---|---|---|---|
| High | 3 | Control 'Decision Logging & Traceability' is at maturity 0 (Planned) vs target 3 required by the obligation. Supporting evidence is also Missing. | Implement Decision Logging & Traceability to close a maturity shortfall of 3. | 95 | BU-DATA |
| High | 3 | Control 'Bias & Fairness Testing' is at maturity 0 (Planned) vs target 3 required by the obligation. Supporting evidence is also Missing. | Strengthen governance of Bias & Fairness Testing to close a maturity shortfall of 3. | 95 | BU-DATA |
| Medium | 2 | Control 'Data Quality Rules & Monitoring' is at maturity 1 (Partially Implemented) vs target 3 required by the obligation. Supporting evidence is also Partial, Stale. | Remediate data for Data Quality Rules & Monitoring to close a maturity shortfall of 2. | 70 | BU-DATA |
| Medium | 2 | Control 'Reconciliation & Aggregation Controls' is at maturity 1 (Partially Implemented) vs target 3 required by the obligation. Supporting evidence is also Missing. | Remediate data for Reconciliation & Aggregation Controls to close a maturity shortfall of 2. | 70 | BU-DATA |
| Medium | 2 | Control 'Model Explainability Reporting' is at maturity 1 (Partially Implemented) vs target 3 required by the obligation. Supporting evidence is also Missing. | Uplift Model Explainability Reporting to close a maturity shortfall of 2. | 70 | BU-DATA |
| Medium | 2 | Control 'Model Performance Monitoring' is at maturity 1 (Partially Implemented) vs target 3 required by the obligation. Supporting evidence is also Partial. | Uplift Model Performance Monitoring to close a maturity shortfall of 2. | 70 | BU-RISK |
| Low | 1 | Control 'Critical Data Element Catalog' is at maturity 2 (Partially Implemented) vs target 3 required by the obligation. Supporting evidence is also Partial, Stale. | Remediate data for Critical Data Element Catalog to close a maturity shortfall of 1. | 45 | BU-DATA |
| Low | 1 | Control 'End-to-End Data Lineage' is at maturity 2 (Partially Implemented) vs target 3 required by the obligation. Supporting evidence is also Missing. | Remediate data for End-to-End Data Lineage to close a maturity shortfall of 1. | 45 | BU-DATA |
| Low | 1 | Control 'Model Inventory & Versioning' is at maturity 2 (Partially Implemented) vs target 3 required by the obligation. Supporting evidence is also Missing. | Uplift Model Inventory & Versioning to close a maturity shortfall of 1. | 45 | BU-RISK |
| Low | 0 | Control 'AI System Risk Classification' meets target maturity 3, but 1 evidence artefact(s) are Stale; compliance cannot be demonstrated to the regulator. | Produce and automate compliance evidence for AI System Risk Classification (lineage, logs or attestations). | 15 | BU-DATA |
| Low | 0 | Control 'Human Oversight & Approval' meets target maturity 3, but 1 evidence artefact(s) are Partial; compliance cannot be demonstrated to the regulator. | Produce and automate compliance evidence for Human Oversight & Approval (lineage, logs or attestations). | 15 | BU-DATA |
| Low | 0 | Control 'Model Validation & Backtesting' meets target maturity 3, but 1 evidence artefact(s) are Stale; compliance cannot be demonstrated to the regulator. | Produce and automate compliance evidence for Model Validation & Backtesting (lineage, logs or attestations). | 15 | BU-RISK |