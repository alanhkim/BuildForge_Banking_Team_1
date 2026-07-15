# Regulatory Change Impact Assessment — BCBS239 regulatory update

- **Change ID:** CHG-BCBS239
- **Regulation:** REG-BCBS239
- **Criticality:** High
- **Effective date:** 2027-12-16
- **Obligations:** 3
- **Gaps identified:** 6  ({'High': 4, 'Medium': 2})
- **Estimated remediation effort:** 520 person-days

## Blast radius
- **Products affected:** Derivatives, FX & Spot, Mortgages, Personal Loans
- **Processes affected:** AI Model Lifecycle, Data Governance & Quality, Regulatory Reporting, Trading & Settlement
- **Systems affected:** General Ledger / Finance, Data Lakehouse (Fabric), Regulatory Reporting

## Gaps & recommended remediation

| Severity | Shortfall | Gap rationale | Recommended action | Effort (days) | Owner |
|---|---|---|---|---|---|
| High | 3 | Control 'Data Quality Rules & Monitoring' is at maturity 1 (Partially Implemented) vs target 4 required by the obligation. Supporting evidence is also Partial, Stale. | Remediate data for Data Quality Rules & Monitoring to close a maturity shortfall of 3. | 95 | BU-DATA |
| High | 3 | Control 'Reconciliation & Aggregation Controls' is at maturity 1 (Partially Implemented) vs target 4 required by the obligation. Supporting evidence is also Missing. | Remediate data for Reconciliation & Aggregation Controls to close a maturity shortfall of 3. | 95 | BU-DATA |
| High | 3 | Control 'Enterprise Data Catalog' is at maturity 0 (Planned) vs target 3 required by the obligation. Supporting evidence is also Missing, Partial. | Remediate data for Enterprise Data Catalog to close a maturity shortfall of 3. | 95 | BU-DATA |
| High | 3 | Control 'Business Glossary' is at maturity 0 (Planned) vs target 3 required by the obligation. Supporting evidence is also Missing. | Remediate data for Business Glossary to close a maturity shortfall of 3. | 95 | BU-DATA |
| Medium | 2 | Control 'Critical Data Element Catalog' is at maturity 2 (Partially Implemented) vs target 4 required by the obligation. Supporting evidence is also Partial, Stale. | Remediate data for Critical Data Element Catalog to close a maturity shortfall of 2. | 70 | BU-DATA |
| Medium | 2 | Control 'End-to-End Data Lineage' is at maturity 2 (Partially Implemented) vs target 4 required by the obligation. Supporting evidence is also Missing. | Remediate data for End-to-End Data Lineage to close a maturity shortfall of 2. | 70 | BU-DATA |