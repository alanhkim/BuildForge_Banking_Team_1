# Regulatory Change Impact Assessment — Uploaded: eu_ai_act_high_risk.txt

- **Change ID:** CHG-EU-AI-ACT-HIGH-RISK-UPLOAD
- **Regulation:** REG-EU-AI-ACT-HIGH-RISK
- **Criticality:** High
- **Effective date:** 2026-12-31
- **Obligations:** 5
- **Gaps identified:** 8  ({'Critical': 4, 'High': 3, 'Medium': 1})
- **Estimated remediation effort:** 190 person-days

## Blast radius
- **Products affected:** Business Loans, Derivatives, FX & Spot, Mortgages, Personal Loans
- **Processes affected:** AI Model Lifecycle, Data Governance & Quality, Lending & Credit Decision, Regulatory Reporting, Trading & Settlement
- **Systems affected:** AI / ML Platform, Credit Risk Engine, General Ledger / Finance, Data Lakehouse (Fabric), Regulatory Reporting

## Gaps & recommended remediation

| Severity | Shortfall | Gap rationale | Recommended action | Effort (days) | Owner |
|---|---|---|---|---|---|
| Critical | 4 | Planned traceability logging leaves high-risk AI lifecycle events and interventions insufficiently traceable. | Implement end-to-end lifecycle and intervention logging with immutable audit trails for high-risk AI operations. | 20 | BU-RISK |
| Critical | 3 | Data quality monitoring is immature for accurate, complete high-risk AI datasets. | Implement automated data quality monitoring for high-risk AI datasets with completeness, accuracy, and exception thresholds. | 30 | BU-RISK |
| Critical | 3 | Aggregation controls are immature for documented lineage, quality checks, approvals, and evidence. | Automate lineage aggregation controls with quality checks, approval workflow, and auditable evidence collection. | 30 | BU-RISK |
| Critical | 3 | Performance monitoring is only partially implemented for critical high-risk AI model risk duties. | Deploy continuous performance monitoring with alerting and escalation for critical high-risk AI model risk controls. | 35 | BU-RISK |
| High | 2 | Critical data element catalog is below target for representative, complete AI datasets. | Document and complete a critical data element catalog for representative, complete high-risk AI datasets. | 20 | BU-RISK |
| High | 2 | Data lineage is only partially implemented and falls short for high-risk AI provenance evidence. | Implement end-to-end data lineage capture and retention for high-risk AI provenance evidence. | 20 | BU-RISK |
| High | 2 | Explainability reporting is too immature to support human oversight and model limitation awareness. | Document and deploy explainability reports that surface model limits, key factors, and human oversight guidance. | 15 | BU-RISK |
| Medium | 1 | Validation and backtesting need stronger maturity to evidence ongoing performance and remediation. | Implement periodic validation and backtesting with remediation tracking for high-risk AI performance evidence. | 20 | BU-RISK |