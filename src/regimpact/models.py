"""Domain model for the Regulatory Change Impact framework.

The model is intentionally graph-shaped: entities are nodes, and every
relationship is captured as an explicit, typed edge. This is what lets the
data stay *correlated* and lets a Fabric Data Agent (or a graph viz, or
Purview lineage) answer questions like "which products are affected if
control X is immature?".

Node tables (entities) + edge tables (relationships) are both exported, so
the same model powers tabular analytics and graph traversal.
"""
from __future__ import annotations

from datetime import date
from enum import Enum

from pydantic import BaseModel, Field


# --------------------------------------------------------------------------- #
# Output column naming (Rule 10 — Pascal_Snake set at the source)
# --------------------------------------------------------------------------- #
_COLUMN_ACRONYMS = {"id": "ID", "ids": "IDs", "pii": "PII"}


def to_column_name(snake: str) -> str:
    """Convert an internal ``snake_case`` field name to the ``Pascal_Snake``
    column name used in every exported artefact (Rule 10). Acronyms are
    upper-cased (``id`` -> ``ID``, ``ids`` -> ``IDs``, ``pii`` -> ``PII``), e.g.
    ``owner_unit_id`` -> ``Owner_Unit_ID``, ``contains_pii`` -> ``Contains_PII``.
    Apply once, at the output boundary; internal attribute names stay snake_case.
    """
    return "_".join(_COLUMN_ACRONYMS.get(p, p.capitalize()) for p in snake.split("_"))


# --------------------------------------------------------------------------- #
# Enumerations
# --------------------------------------------------------------------------- #
class MaturityLevel(int, Enum):
    """CMMI-style control maturity (0 = absent, 5 = optimised)."""

    NONE = 0
    INITIAL = 1
    REPEATABLE = 2
    DEFINED = 3
    MANAGED = 4
    OPTIMISED = 5


class ControlStatus(str, Enum):
    IMPLEMENTED = "Implemented"
    PARTIAL = "Partially Implemented"
    PLANNED = "Planned"
    MISSING = "Missing"


class Criticality(str, Enum):
    LOW = "Low"
    MEDIUM = "Medium"
    HIGH = "High"
    CRITICAL = "Critical"


class GapSeverity(str, Enum):
    NONE = "None"
    LOW = "Low"
    MEDIUM = "Medium"
    HIGH = "High"
    CRITICAL = "Critical"


class EvidenceStatus(str, Enum):
    """Whether the artefact that proves a control is operating exists."""

    PRESENT = "Present"
    STALE = "Stale"
    PARTIAL = "Partial"
    MISSING = "Missing"


class ComplianceStatus(str, Enum):
    COMPLIANT = "Compliant"
    PARTIAL = "Partially Compliant"
    NONCOMPLIANT = "Non-Compliant"


class Scenario(str, Enum):
    """Compliance scoring scenarios for the before/after narrative."""

    AS_IS = "AsIs"                    # baseline, before the change lands
    POST_CHANGE = "PostChange"        # after the change introduces new targets
    POST_REMEDIATION = "PostRemediation"  # after recommended actions are done


class RelType(str, Enum):
    """Typed relationships between entities (edge labels)."""

    CHANGE_OF_REGULATION = "CHANGE_OF_REGULATION"
    INTRODUCES_OBLIGATION = "INTRODUCES_OBLIGATION"
    OBLIGATION_REQUIRES_CONTROL = "OBLIGATION_REQUIRES_CONTROL"
    CONTROL_IMPLEMENTED_IN_SYSTEM = "CONTROL_IMPLEMENTED_IN_SYSTEM"
    CONTROL_OPERATES_IN_PROCESS = "CONTROL_OPERATES_IN_PROCESS"
    CONTROL_MITIGATES_RISK = "CONTROL_MITIGATES_RISK"
    CONTROL_REALIZES_CAPABILITY = "CONTROL_REALIZES_CAPABILITY"
    CONTROL_EVIDENCED_BY = "CONTROL_EVIDENCED_BY"
    CAPABILITY_ENABLED_BY_TECHNOLOGY = "CAPABILITY_ENABLED_BY_TECHNOLOGY"
    EVIDENCE_PRODUCED_BY_TECHNOLOGY = "EVIDENCE_PRODUCED_BY_TECHNOLOGY"
    PROCESS_USES_SYSTEM = "PROCESS_USES_SYSTEM"
    PROCESS_SUPPORTS_PRODUCT = "PROCESS_SUPPORTS_PRODUCT"
    SYSTEM_STORES_DATA_DOMAIN = "SYSTEM_STORES_DATA_DOMAIN"
    OBLIGATION_CONCERNS_DATA_DOMAIN = "OBLIGATION_CONCERNS_DATA_DOMAIN"
    PRODUCT_OWNED_BY_UNIT = "PRODUCT_OWNED_BY_UNIT"
    GAP_FOR_OBLIGATION = "GAP_FOR_OBLIGATION"
    GAP_AGAINST_CONTROL = "GAP_AGAINST_CONTROL"
    REMEDIATION_RESOLVES_GAP = "REMEDIATION_RESOLVES_GAP"


# --------------------------------------------------------------------------- #
# Entity (node) models
# --------------------------------------------------------------------------- #
class Regulation(BaseModel):
    id: str
    name: str
    short_code: str
    regulator: str
    jurisdiction: str
    domain: str  # e.g. Financial Crime, Operational Resilience, Prudential
    description: str


class RegulatoryChange(BaseModel):
    id: str
    regulation_id: str
    title: str
    reference: str  # e.g. "DORA Art. 11 amendment"
    summary: str
    change_type: str  # New / Amendment / Clarification
    published_date: date
    effective_date: date
    criticality: Criticality


class Obligation(BaseModel):
    id: str
    change_id: str
    regulation_id: str
    statement: str
    article: str = ""  # source clause / article reference
    theme: str  # capability theme the obligation maps to
    criticality: Criticality
    target_maturity: MaturityLevel  # maturity the bank must reach


class Control(BaseModel):
    id: str
    name: str
    control_family: str
    capability_id: str = ""  # Layer-3 capability this control realises
    description: str
    status: ControlStatus
    maturity: MaturityLevel  # current/as-is maturity
    owner_unit_id: str


class Capability(BaseModel):
    """Layer 3 — a compliance capability realised by controls."""

    id: str
    name: str
    domain: str


class Technology(BaseModel):
    """Layer 4 — an enabling platform / tool."""

    id: str
    name: str
    vendor: str
    category: str
    is_microsoft: bool = False


class Evidence(BaseModel):
    """Layer 5 — an artefact that proves a control is operating."""

    id: str
    control_id: str
    evidence_type: str
    name: str
    status: EvidenceStatus
    technology_id: str


class System(BaseModel):
    id: str
    name: str
    category: str  # Core, Channel, Risk, Data, Reporting...
    vendor: str
    criticality: Criticality


class BusinessProcess(BaseModel):
    id: str
    name: str
    value_chain: str
    owner_unit_id: str


class Product(BaseModel):
    id: str
    name: str
    product_line: str
    owner_unit_id: str


class DataDomain(BaseModel):
    id: str
    name: str
    classification: str  # Public / Internal / Confidential / Restricted (PII)
    contains_pii: bool


class BusinessUnit(BaseModel):
    id: str
    name: str
    division: str


class Risk(BaseModel):
    id: str
    name: str
    category: str
    inherent_rating: Criticality


class Gap(BaseModel):
    """Computed: an obligation whose supporting controls fall short."""

    id: str
    obligation_id: str
    change_id: str
    control_id: str | None
    severity: GapSeverity
    maturity_shortfall: int  # target - actual (clamped >= 0)
    rationale: str
    affected_system_ids: list[str] = Field(default_factory=list)
    affected_process_ids: list[str] = Field(default_factory=list)
    affected_product_ids: list[str] = Field(default_factory=list)
    affected_data_domain_ids: list[str] = Field(default_factory=list)


class RemediationAction(BaseModel):
    """Computed: what must be done to close a gap."""

    id: str
    gap_id: str
    action: str
    action_type: str  # Build / Enhance / Policy / Data / Assurance
    estimated_effort_days: int
    priority: Criticality
    target_unit_id: str


class ComplianceScore(BaseModel):
    """Computed: a compliance score for a scope under a given scenario."""

    scope_type: str  # Overall / Regulation / Capability
    scope_id: str
    scope_name: str
    scenario: Scenario
    score: float  # 0-100
    status: ComplianceStatus
    change_id: str | None = None


class Edge(BaseModel):
    """A typed relationship between two entities."""

    source_id: str
    source_type: str
    target_id: str
    target_type: str
    rel_type: RelType
    weight: float = 1.0


class Estate(BaseModel):
    """The full generated dataset: nodes + edges in one cohesive object."""

    regulations: list[Regulation] = Field(default_factory=list)
    changes: list[RegulatoryChange] = Field(default_factory=list)
    obligations: list[Obligation] = Field(default_factory=list)
    controls: list[Control] = Field(default_factory=list)
    capabilities: list[Capability] = Field(default_factory=list)
    technologies: list[Technology] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    systems: list[System] = Field(default_factory=list)
    processes: list[BusinessProcess] = Field(default_factory=list)
    products: list[Product] = Field(default_factory=list)
    data_domains: list[DataDomain] = Field(default_factory=list)
    units: list[BusinessUnit] = Field(default_factory=list)
    risks: list[Risk] = Field(default_factory=list)
    gaps: list[Gap] = Field(default_factory=list)
    remediations: list[RemediationAction] = Field(default_factory=list)
    scores: list[ComplianceScore] = Field(default_factory=list)
    edges: list[Edge] = Field(default_factory=list)
    # Version stamp written onto every exported row so amendment runs are
    # comparable (see settings.as_of / REGIMPACT_AS_OF).
    as_of: str = ""
