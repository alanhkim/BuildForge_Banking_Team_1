# Regulatory Change Impact Assessment — EUAIACT regulatory update

- **Change ID:** CHG-EUAIACT
- **Regulation:** REG-AIACT
- **Criticality:** Critical
- **Effective date:** 2027-10-18
- **Obligations:** 5
- **Gaps identified:** 12  ({'High': 5, 'Medium': 4, 'Critical': 2, 'Low': 1})
- **Estimated remediation effort:** 940 person-days

## Blast radius
- **Products affected:** Business Loans, Derivatives, FX & Spot, Mortgages, Personal Loans
- **Processes affected:** AI Model Lifecycle, Data Governance & Quality, Lending & Credit Decision, Regulatory Reporting, Trading & Settlement
- **Systems affected:** AI / ML Platform, Credit Risk Engine, General Ledger / Finance, Data Lakehouse (Fabric), Regulatory Reporting

## Gaps & recommended remediation

| Severity | Shortfall | Gap rationale | Recommended action | Effort (days) | Owner |
|---|---|---|---|---|---|
| Critical | 4 | Control 'Decision Logging & Traceability' is at maturity 0 (Planned) vs target 4 required by the obligation. Supporting evidence is also Missing. | Implement Decision Logging & Traceability to close a maturity shortfall of 4. | 135 | BU-DATA |
| Critical | 4 | Control 'Bias & Fairness Testing' is at maturity 0 (Planned) vs target 4 required by the obligation. Supporting evidence is also Missing. | Strengthen governance of Bias & Fairness Testing to close a maturity shortfall of 4. | 135 | BU-DATA |
| High | 3 | Control 'Reconciliation & Aggregation Controls' is at maturity 1 (Partially Implemented) vs target 4 required by the obligation. Supporting evidence is also Missing. | Remediate data for Reconciliation & Aggregation Controls to close a maturity shortfall of 3. | 95 | BU-DATA |
| High | 3 | Control 'Model Explainability Reporting' is at maturity 1 (Partially Implemented) vs target 4 required by the obligation. Supporting evidence is also Missing. | Uplift Model Explainability Reporting to close a maturity shortfall of 3. | 95 | BU-DATA |
| High | 3 | Control 'Model Performance Monitoring' is at maturity 1 (Partially Implemented) vs target 4 required by the obligation. Supporting evidence is also Partial. | Uplift Model Performance Monitoring to close a maturity shortfall of 3. | 95 | BU-RISK |
| High | 2 | Control 'Training Data Governance' is at maturity 2 (Partially Implemented) vs target 4 required by the obligation. Supporting evidence is also Missing, Partial. | Remediate data for Training Data Governance to close a maturity shortfall of 2. | 70 | BU-DATA |
| High | 2 | Control 'Dataset Documentation & Provenance' is at maturity 2 (Partially Implemented) vs target 4 required by the obligation. Supporting evidence is also Partial, Stale. | Remediate data for Dataset Documentation & Provenance to close a maturity shortfall of 2. | 70 | BU-DATA |
| Medium | 2 | Control 'End-to-End Data Lineage' is at maturity 2 (Partially Implemented) vs target 4 required by the obligation. Supporting evidence is also Missing. | Remediate data for End-to-End Data Lineage to close a maturity shortfall of 2. | 70 | BU-DATA |
| Medium | 2 | Control 'Model Inventory & Versioning' is at maturity 2 (Partially Implemented) vs target 4 required by the obligation. Supporting evidence is also Missing. | Uplift Model Inventory & Versioning to close a maturity shortfall of 2. | 70 | BU-RISK |
| Medium | 1 | Control 'Human Oversight & Approval' is at maturity 3 (Implemented) vs target 4 required by the obligation. Supporting evidence is also Partial. | Strengthen governance of Human Oversight & Approval to close a maturity shortfall of 1. | 45 | BU-DATA |
| Low | 1 | Control 'Model Validation & Backtesting' is at maturity 3 (Implemented) vs target 4 required by the obligation. Supporting evidence is also Stale. | Uplift Model Validation & Backtesting to close a maturity shortfall of 1. | 45 | BU-RISK |
| Medium | 0 | Control 'AI System Risk Classification' meets target maturity 4, but 1 evidence artefact(s) are Stale; compliance cannot be demonstrated to the regulator. | Produce and automate compliance evidence for AI System Risk Classification (lineage, logs or attestations). | 15 | BU-DATA |