from typing import List
from .models import (
    Regulation,
    Change,
    Obligation,
    Technology,
    Evidence,
    Capability,
    Control,
    BusinessUnit,
    Product,
    DataDomain,
    System,
    BusinessProcess,
    Risk
)

class EstateGenerator:
    def __init__(self):
        self.regulations: List[Regulation] = []
        self.changes: List[Change] = []
        self.obligations: List[Obligation] = []
        self.technologies: List[Technology] = []
        self.evidences: List[Evidence] = []
        self.capabilities: List[Capability] = []
        self.controls: List[Control] = []
        self.business_units: List[BusinessUnit] = []
        self.products: List[Product] = []
        self.data_domains: List[DataDomain] = []
        self.systems: List[System] = []
        self.business_processes: List[BusinessProcess] = []
        self.risks: List[Risk] = []

    def generate(self):
        # Business Units
        bu_ops = BusinessUnit(id="BU-OPS", name="Operations")
        self.business_units.append(bu_ops)

        # Products
        prd_payments = Product(id="PRD-PAY", name="Payments", business_unit_id="BU-OPS")
        self.products.append(prd_payments)

        # Data Domains
        dd_pii = DataDomain(id="DD-PII", name="Customer PII")
        self.data_domains.append(dd_pii)

        # Systems
        sys_core = System(id="SYS-CORE", name="Core Banking", data_domain_ids=["DD-PII"])
        self.systems.append(sys_core)

        # Business Processes
        prc_settlement = BusinessProcess(
            id="PRC-SET", 
            name="Payment Settlement", 
            system_ids=["SYS-CORE"], 
            product_ids=["PRD-PAY"]
        )
        self.business_processes.append(prc_settlement)

        # Risks
        rsk_outage = Risk(id="RSK-OUT", name="Service Outage")
        self.risks.append(rsk_outage)

        # Technologies
        tech_servicenow = Technology(id="TEC-SN", name="ServiceNow")
        tech_azure = Technology(id="TEC-AZ", name="Azure Cloud")
        self.technologies.extend([tech_servicenow, tech_azure])

        # Capabilities
        cap_resilience = Capability(
            id="CAP-RES", 
            name="Operational Resilience", 
            technology_ids=["TEC-SN", "TEC-AZ"]
        )
        self.capabilities.append(cap_resilience)

        # Evidence
        ev_bcp = Evidence(
            id="EV-BCP", 
            name="BCP Document", 
            status="Missing", 
            technology_id="TEC-SN"
        )
        self.evidences.append(ev_bcp)

        # Controls
        ctl_or_3 = Control(
            id="CTL-OR-3",
            name="ICT Continuity & Recovery",
            family="Operational Resilience",
            owner="BU-OPS",
            maturity=1,
            capability_id="CAP-RES",
            evidence_ids=["EV-BCP"],
            system_ids=["SYS-CORE"],
            process_ids=["PRC-SET"],
            risk_ids=["RSK-OUT"]
        )
        self.controls.append(ctl_or_3)

        # Regulation
        reg_dora = Regulation(
            id="REG-DORA", 
            name="DORA", 
            title="Digital Operational Resilience Act"
        )
        self.regulations.append(reg_dora)

        # Regulatory Change
        chg_dora = Change(
            id="CHG-DORA",
            regulation_id="REG-DORA",
            title="Critical ICT Resilience Update",
            effective_date="2027-04-15",
            criticality="Critical"
        )
        self.changes.append(chg_dora)

        # Obligation
        obl_dora_01 = Obligation(
            id="OBL-DORA-01",
            change_id="CHG-DORA",
            theme="ICT_RESILIENCE",
            target_maturity=4,
            data_domain_ids=["DD-PII"],
            summary="Maintain mature ICT continuity and recovery controls.",
            criticality="Critical",
            source_refs=["catalog:REG-DORA:OBL-DORA-01"],
            notes=[]
        )
        self.obligations.append(obl_dora_01)
        
        return self

def build_estate() -> EstateGenerator:
    generator = EstateGenerator()
    return generator.generate()
