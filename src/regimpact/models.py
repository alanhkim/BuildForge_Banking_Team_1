from dataclasses import dataclass, field
from typing import List, Optional

@dataclass
class Regulation:
    id: str
    name: str
    title: str

@dataclass
class Change:
    id: str
    regulation_id: str
    title: str
    effective_date: str
    criticality: str

@dataclass
class BusinessUnit:
    id: str
    name: str

@dataclass
class Product:
    id: str
    name: str
    business_unit_id: str

@dataclass
class DataDomain:
    id: str
    name: str

@dataclass
class System:
    id: str
    name: str
    data_domain_ids: List[str] = field(default_factory=list)

@dataclass
class BusinessProcess:
    id: str
    name: str
    system_ids: List[str] = field(default_factory=list)
    product_ids: List[str] = field(default_factory=list)

@dataclass
class Risk:
    id: str
    name: str

@dataclass
class Obligation:
    id: str
    change_id: str
    theme: str
    target_maturity: int
    data_domain_ids: List[str] = field(default_factory=list)

@dataclass
class Technology:
    id: str
    name: str

@dataclass
class Evidence:
    id: str
    name: str
    status: str
    technology_id: Optional[str] = None

@dataclass
class Capability:
    id: str
    name: str
    technology_ids: List[str] = field(default_factory=list)

@dataclass
class Control:
    id: str
    name: str
    family: str
    owner: str
    maturity: int
    capability_id: str
    evidence_ids: List[str] = field(default_factory=list)
    system_ids: List[str] = field(default_factory=list)
    process_ids: List[str] = field(default_factory=list)
    risk_ids: List[str] = field(default_factory=list)

@dataclass
class Gap:
    id: str
    obligation_id: str
    control_id: str
    severity: str
    shortfall: int
    blast_radius: List[str] = field(default_factory=list)

@dataclass
class Remediation:
    id: str
    gap_id: str
    priority: str
    owner: str
    costed_days: int
