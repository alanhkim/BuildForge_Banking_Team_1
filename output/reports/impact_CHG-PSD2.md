# Regulatory Change Impact Assessment — PSD2 regulatory update

- **Change ID:** CHG-PSD2
- **Regulation:** REG-PSD2
- **Criticality:** Critical
- **Effective date:** 2026-11-02
- **Obligations:** 3
- **Gaps identified:** 5  ({'Critical': 1, 'High': 2, 'Low': 2})
- **Estimated remediation effort:** 385 person-days

## Blast radius
- **Products affected:** Credit Cards, Current Accounts
- **Processes affected:** Fraud & Financial Crime, Payments Processing
- **Systems affected:** Payment Gateway, Transaction Monitoring

## Gaps & recommended remediation

| Severity | Shortfall | Gap rationale | Recommended action | Effort (days) | Owner |
|---|---|---|---|---|---|
| Critical | 4 | Control 'Transaction Risk Analysis (TRA)' is at maturity 0 (Planned) vs target 4 required by the obligation. Supporting evidence is also Missing. | Implement Transaction Risk Analysis (TRA) to close a maturity shortfall of 4. | 135 | BU-TECH |
| High | 3 | Control 'Transaction Risk Analysis (TRA)' is at maturity 0 (Planned) vs target 3 required by the obligation. Supporting evidence is also Missing. | Implement Transaction Risk Analysis (TRA) to close a maturity shortfall of 3. | 95 | BU-TECH |
| High | 3 | Control 'Behavioural Monitoring Scenarios' is at maturity 0 (Planned) vs target 3 required by the obligation. Supporting evidence is also Missing. | Implement Behavioural Monitoring Scenarios to close a maturity shortfall of 3. | 95 | BU-RISK |
| Low | 1 | Control 'Suspicious Activity Reporting (SAR)' is at maturity 2 (Partially Implemented) vs target 3 required by the obligation. Supporting evidence is also Missing. | Uplift Suspicious Activity Reporting (SAR) to close a maturity shortfall of 1. | 45 | BU-RISK |
| Low | 0 | Control 'Alert Triage & Investigation' meets target maturity 3, but 1 evidence artefact(s) are Partial; compliance cannot be demonstrated to the regulator. | Produce and automate compliance evidence for Alert Triage & Investigation (lineage, logs or attestations). | 15 | BU-RISK |