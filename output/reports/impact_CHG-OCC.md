# Regulatory Change Impact Assessment — OCC regulatory update

- **Change ID:** CHG-OCC
- **Regulation:** REG-OCC
- **Criticality:** High
- **Effective date:** 2026-12-16
- **Obligations:** 3
- **Gaps identified:** 5  ({'Medium': 2, 'Low': 2, 'High': 1})
- **Estimated remediation effort:** 325 person-days

## Blast radius
- **Products affected:** Business Loans, Mortgages, Personal Loans
- **Processes affected:** AI Model Lifecycle, Data Governance & Quality, Lending & Credit Decision, Regulatory Reporting
- **Systems affected:** AI / ML Platform, Credit Risk Engine, Data Lakehouse (Fabric)

## Gaps & recommended remediation

| Severity | Shortfall | Gap rationale | Recommended action | Effort (days) | Owner |
|---|---|---|---|---|---|
| High | 3 | Control 'Model Performance Monitoring' is at maturity 1 (Partially Implemented) vs target 4 required by the obligation. Supporting evidence is also Partial. | Uplift Model Performance Monitoring to close a maturity shortfall of 3. | 95 | BU-RISK |
| Medium | 2 | Control 'Model Inventory & Versioning' is at maturity 2 (Partially Implemented) vs target 4 required by the obligation. Supporting evidence is also Missing. | Uplift Model Inventory & Versioning to close a maturity shortfall of 2. | 70 | BU-RISK |
| Medium | 2 | Control 'Data Quality Rules & Monitoring' is at maturity 1 (Partially Implemented) vs target 3 required by the obligation. Supporting evidence is also Partial, Stale. | Remediate data for Data Quality Rules & Monitoring to close a maturity shortfall of 2. | 70 | BU-DATA |
| Low | 1 | Control 'Model Validation & Backtesting' is at maturity 3 (Implemented) vs target 4 required by the obligation. Supporting evidence is also Stale. | Uplift Model Validation & Backtesting to close a maturity shortfall of 1. | 45 | BU-RISK |
| Low | 1 | Control 'Critical Data Element Catalog' is at maturity 2 (Partially Implemented) vs target 3 required by the obligation. Supporting evidence is also Partial, Stale. | Remediate data for Critical Data Element Catalog to close a maturity shortfall of 1. | 45 | BU-DATA |