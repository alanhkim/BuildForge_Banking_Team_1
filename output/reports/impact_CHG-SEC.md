# Regulatory Change Impact Assessment — SEC regulatory update

- **Change ID:** CHG-SEC
- **Regulation:** REG-SEC
- **Criticality:** Critical
- **Effective date:** 2027-08-26
- **Obligations:** 3
- **Gaps identified:** 5  ({'High': 2, 'Critical': 1, 'Low': 2})
- **Estimated remediation effort:** 340 person-days

## Blast radius
- **Products affected:** Credit Cards, Current Accounts, Mortgages, Personal Loans, Savings Accounts
- **Processes affected:** AI Model Lifecycle, Data Governance & Quality, Customer Onboarding, Regulatory Reporting, Customer Servicing
- **Systems affected:** Identity & Access Mgmt, Data Lakehouse (Fabric)

## Gaps & recommended remediation

| Severity | Shortfall | Gap rationale | Recommended action | Effort (days) | Owner |
|---|---|---|---|---|---|
| Critical | 3 | Control 'Major Incident Regulatory Notification' is at maturity 1 (Partially Implemented) vs target 4 required by the obligation. Supporting evidence is also Missing. | Uplift Major Incident Regulatory Notification to close a maturity shortfall of 3. | 110 | BU-TECH |
| High | 2 | Control 'ICT Incident Detection & Response' is at maturity 2 (Partially Implemented) vs target 4 required by the obligation. Supporting evidence is also Missing, Partial. | Uplift ICT Incident Detection & Response to close a maturity shortfall of 2. | 70 | BU-TECH |
| High | 2 | Control 'Threat-Led Penetration Testing' is at maturity 2 (Partially Implemented) vs target 4 required by the obligation. Supporting evidence is also Partial, Stale. | Uplift Threat-Led Penetration Testing to close a maturity shortfall of 2. | 70 | BU-TECH |
| Low | 1 | Control 'Security Monitoring & SIEM' is at maturity 2 (Partially Implemented) vs target 3 required by the obligation. Supporting evidence is also Missing, Partial. | Uplift Security Monitoring & SIEM to close a maturity shortfall of 1. | 45 | BU-TECH |
| Low | 1 | Control 'Vulnerability Management' is at maturity 2 (Partially Implemented) vs target 3 required by the obligation. Supporting evidence is also Missing, Stale. | Uplift Vulnerability Management to close a maturity shortfall of 1. | 45 | BU-TECH |