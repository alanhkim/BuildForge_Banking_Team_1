# Regulatory Change Impact Assessment — NISTAIRMF regulatory update

- **Change ID:** CHG-NISTAIRMF
- **Regulation:** REG-NIST
- **Criticality:** High
- **Effective date:** 2027-10-26
- **Obligations:** 3
- **Gaps identified:** 8  ({'Low': 3, 'Critical': 1, 'Medium': 2, 'High': 2})
- **Estimated remediation effort:** 570 person-days

## Blast radius
- **Products affected:** Business Loans, Mortgages, Personal Loans
- **Processes affected:** AI Model Lifecycle, Data Governance & Quality, Lending & Credit Decision, Regulatory Reporting
- **Systems affected:** AI / ML Platform, Credit Risk Engine, Data Lakehouse (Fabric)

## Gaps & recommended remediation

| Severity | Shortfall | Gap rationale | Recommended action | Effort (days) | Owner |
|---|---|---|---|---|---|
| Critical | 4 | Control 'Bias & Fairness Testing' is at maturity 0 (Planned) vs target 4 required by the obligation. Supporting evidence is also Missing. | Strengthen governance of Bias & Fairness Testing to close a maturity shortfall of 4. | 135 | BU-DATA |
| High | 3 | Control 'Model Performance Monitoring' is at maturity 1 (Partially Implemented) vs target 4 required by the obligation. Supporting evidence is also Partial. | Uplift Model Performance Monitoring to close a maturity shortfall of 3. | 95 | BU-RISK |
| High | 3 | Control 'Decision Logging & Traceability' is at maturity 0 (Planned) vs target 3 required by the obligation. Supporting evidence is also Missing. | Implement Decision Logging & Traceability to close a maturity shortfall of 3. | 95 | BU-DATA |
| Medium | 2 | Control 'Model Inventory & Versioning' is at maturity 2 (Partially Implemented) vs target 4 required by the obligation. Supporting evidence is also Missing. | Uplift Model Inventory & Versioning to close a maturity shortfall of 2. | 70 | BU-RISK |
| Medium | 2 | Control 'Model Explainability Reporting' is at maturity 1 (Partially Implemented) vs target 3 required by the obligation. Supporting evidence is also Missing. | Uplift Model Explainability Reporting to close a maturity shortfall of 2. | 70 | BU-DATA |
| Low | 1 | Control 'Human Oversight & Approval' is at maturity 3 (Implemented) vs target 4 required by the obligation. Supporting evidence is also Partial. | Strengthen governance of Human Oversight & Approval to close a maturity shortfall of 1. | 45 | BU-DATA |
| Low | 1 | Control 'Model Validation & Backtesting' is at maturity 3 (Implemented) vs target 4 required by the obligation. Supporting evidence is also Stale. | Uplift Model Validation & Backtesting to close a maturity shortfall of 1. | 45 | BU-RISK |
| Low | 0 | Control 'AI System Risk Classification' meets target maturity 4, but 1 evidence artefact(s) are Stale; compliance cannot be demonstrated to the regulator. | Produce and automate compliance evidence for AI System Risk Classification (lineage, logs or attestations). | 15 | BU-DATA |