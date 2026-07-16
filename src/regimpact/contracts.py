"""Typed contracts for Foundry/Fabric-backed regulatory impact agents."""
from dataclasses import dataclass, field
from typing import Any, List, Optional, Literal


# Validation exceptions
class ValidationError(Exception):
    """Base exception for contract validation failures."""
    pass


class InvalidObligationError(ValidationError):
    """Raised when obligation data fails schema validation."""
    pass


class InvalidThemeError(ValidationError):
    """Raised when an obligation theme is not recognized."""
    pass


class InvalidMaturityError(ValidationError):
    """Raised when target_maturity is out of valid range."""
    pass


class MissingSourceRefsError(ValidationError):
    """Raised when source_refs are missing from an obligation."""
    pass


class MissingCitationError(ValidationError):
    """Raised when an agent response is not grounded by citations."""
    pass


# Known themes for validation
KNOWN_THEMES = {
    "ACCESS_CONTROL",
    "AI_GOVERNANCE",
    "AUDITABILITY",
    "CAPITAL_ADEQUACY",
    "CONDUCT",
    "CYBER",
    "ICT_RESILIENCE",
    "ICT_SECURITY",
    "INCIDENT_MGMT",
    "KYC_CDD",
    "METADATA",
    "MODEL_RISK",
    "PRIVACY",
    "REG_REPORTING",
    "RETENTION",
    "SANCTIONS",
    "SAR_REPORTING",
    "SCA",
    "THIRD_PARTY_RISK",
    "TRACEABILITY",
    "TRAINING_DATA",
    "TXN_MONITORING",
    "DATA_LINEAGE",
    "DATA_QUALITY",
    "LOGGING_MONITORING",
}

# Valid maturity range
MIN_MATURITY = 1
MAX_MATURITY = 5

# Valid criticality levels
VALID_CRITICALITY = {"Critical", "High", "Medium", "Low"}


@dataclass
class InterpretRequest:
    """Request contract for regulation interpretation."""
    regulation_id: str
    change_id: str
    name: str
    title: str
    source_text: Optional[str] = None
    source_path: Optional[str] = None

    def validate(self) -> None:
        """Validate required fields."""
        if not self.regulation_id or not self.regulation_id.strip():
            raise ValidationError("regulation_id is required")
        if not self.change_id or not self.change_id.strip():
            raise ValidationError("change_id is required")
        if not self.name or not self.name.strip():
            raise ValidationError("name is required")
        if not self.title or not self.title.strip():
            raise ValidationError("title is required")


@dataclass
class Obligation:
    """Structured obligation extracted from a regulatory change."""
    id: str
    change_id: str
    theme: str
    summary: str
    target_maturity: int
    criticality: str
    affected_data_domain_ids: List[str]
    source_refs: List[str]
    notes: List[str] = field(default_factory=list)

    def validate(self) -> None:
        """Validate obligation fields against schema rules."""
        errors = []

        # Required ID
        if not self.id:
            errors.append("Obligation id is required")

        # Required change_id
        if not self.change_id:
            errors.append("Obligation change_id is required")

        # Theme validation
        if not self.theme:
            errors.append("Obligation theme is required")
        elif self.theme not in KNOWN_THEMES:
            raise InvalidThemeError(
                f"Unknown theme '{self.theme}'. "
                f"Known themes: {', '.join(sorted(KNOWN_THEMES))}"
            )

        # Summary validation
        if not self.summary:
            errors.append("Obligation summary is required")

        # Maturity range validation
        if type(self.target_maturity) is not int:
            errors.append("target_maturity must be an integer")
        elif not (MIN_MATURITY <= self.target_maturity <= MAX_MATURITY):
            raise InvalidMaturityError(
                f"target_maturity must be between {MIN_MATURITY} and {MAX_MATURITY}, "
                f"got {self.target_maturity}"
            )

        # Criticality validation
        if not self.criticality:
            errors.append("Obligation criticality is required")
        elif self.criticality not in VALID_CRITICALITY:
            errors.append(
                f"Criticality must be one of {VALID_CRITICALITY}, got '{self.criticality}'"
            )

        # Source refs validation
        if not self.source_refs:
            raise MissingSourceRefsError(
                f"Obligation {self.id} must include source_refs for traceability"
            )

        # affected_data_domain_ids can be empty but must be a list
        if not isinstance(self.affected_data_domain_ids, list):
            errors.append("affected_data_domain_ids must be a list")

        if errors:
            raise InvalidObligationError("; ".join(errors))


@dataclass
class InterpretResponse:
    """Response contract for regulation interpretation."""
    regulation_id: str
    change_id: str
    obligations: List[Obligation]
    mode: Literal["foundry-model"] = "foundry-model"
    notes: List[str] = field(default_factory=list)

    def validate(self) -> None:
        """Validate response and all obligations."""
        if not self.regulation_id:
            raise ValidationError("regulation_id is required in response")
        if not self.change_id:
            raise ValidationError("change_id is required in response")

        # Validate each obligation
        for obligation in self.obligations:
            obligation.validate()


AgentName = Literal[
    "audit-lineage",
    "compliance-score-narrator",
    "control-mapper",
    "executive-qa",
    "fabric-data-agent",
    "gap-analyst",
    "regulation-interpreter",
    "remediation-planner",
]

AgentErrorKind = Literal[
    "auth",
    "configuration",
    "malformed_response",
    "missing_data",
    "permission",
    "service",
    "timeout",
    "unsupported_question",
]

ConfidenceLevel = Literal["low", "medium", "high"]


@dataclass(frozen=True)
class SourceReference:
    """Pointer to a real Fabric table, view, measure, field, or entity."""

    source: str
    reference_type: Literal["entity", "field", "measure", "relationship", "table", "view"]
    name: str
    value: str = ""

    def validate(self) -> None:
        """Require enough detail to trace the source in Fabric."""
        if not self.source.strip():
            raise ValidationError("source reference source is required")
        if not self.name.strip():
            raise ValidationError("source reference name is required")


@dataclass(frozen=True)
class ToolEvidence:
    """Evidence that a Foundry/Fabric tool call was used."""

    tool_name: str
    data_source: str
    query: str = ""
    source_refs: list[SourceReference] = field(default_factory=list)

    def validate(self) -> None:
        """Validate tool provenance."""
        if not self.tool_name.strip():
            raise ValidationError("tool_name is required")
        if not self.data_source.strip():
            raise ValidationError("data_source is required")
        for source_ref in self.source_refs:
            source_ref.validate()


@dataclass(frozen=True)
class AgentError:
    """Explicit external-boundary error; never a success-shaped fallback."""

    kind: AgentErrorKind
    message: str
    retryable: bool = False
    details: dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        """Validate error payload."""
        if not self.message.strip():
            raise ValidationError("agent error message is required")


@dataclass(frozen=True)
class FabricQuestionRequest:
    """Question to answer through the governed Fabric Data Agent path."""

    question: str
    agent_name: str
    agent_version: str
    workspace_id: str
    data_agent_id: str
    allowed_sources: list[str] = field(default_factory=list)

    def validate(self) -> None:
        """Validate request configuration and prompt."""
        for field_name, value in (
            ("question", self.question),
            ("agent_name", self.agent_name),
            ("agent_version", self.agent_version),
            ("workspace_id", self.workspace_id),
            ("data_agent_id", self.data_agent_id),
        ):
            if not value.strip():
                raise ValidationError(f"{field_name} is required")


@dataclass(frozen=True)
class FabricQuestionResponse:
    """Grounded natural-language answer from the Fabric Data Agent route."""

    question: str
    answer: str
    agent_name: str
    agent_version: str
    citations: list[SourceReference]
    tool_evidence: list[ToolEvidence]
    confidence: ConfidenceLevel
    error: AgentError | None = None

    def validate(self) -> None:
        """Require citations/tool provenance for successful Fabric answers."""
        if self.error is not None:
            self.error.validate()
            return
        if not self.answer.strip():
            raise ValidationError("answer is required")
        if not self.citations:
            raise MissingCitationError("Fabric answer must include citations")
        if not self.tool_evidence:
            raise MissingCitationError("Fabric answer must include tool evidence")
        for citation in self.citations:
            citation.validate()
        for evidence in self.tool_evidence:
            evidence.validate()


@dataclass(frozen=True)
class ControlMappingRequest:
    """Request to map obligations to existing control estate entities.

    ``obligations`` carries in-context obligation facts (id/theme/summary/
    criticality). This is critical for freshly-interpreted regulations whose
    obligations may not yet exist in the Fabric lakehouse. Optional to preserve
    backward compatibility with callers that only supply IDs for already-
    materialised changes.
    """

    obligation_ids: list[str]
    fabric_context_question: str
    obligations: list[dict] = field(default_factory=list)
    candidate_controls: list[dict] = field(default_factory=list)

    def validate(self) -> None:
        """Validate control mapping input."""
        if not self.obligation_ids:
            raise ValidationError("obligation_ids is required")
        if any(not obligation_id.strip() for obligation_id in self.obligation_ids):
            raise ValidationError("obligation_ids cannot contain empty values")
        if not self.fabric_context_question.strip():
            raise ValidationError("fabric_context_question is required")


@dataclass(frozen=True)
class ControlMapping:
    """One obligation-to-control mapping result."""

    obligation_id: str
    control_id: str
    capability_id: str
    rationale: str
    confidence: ConfidenceLevel
    source_refs: list[SourceReference]

    def validate(self) -> None:
        """Validate mapping is grounded to existing IDs."""
        for field_name, value in (
            ("obligation_id", self.obligation_id),
            ("control_id", self.control_id),
            ("capability_id", self.capability_id),
            ("rationale", self.rationale),
        ):
            if not value.strip():
                raise ValidationError(f"{field_name} is required")
        if not self.source_refs:
            raise MissingCitationError("control mapping must include source_refs")
        for source_ref in self.source_refs:
            source_ref.validate()


@dataclass(frozen=True)
class ControlMappingResponse:
    """Control Mapper agent response."""

    mappings: list[ControlMapping]
    tool_evidence: list[ToolEvidence]
    error: AgentError | None = None

    def validate(self) -> None:
        """Validate mapped controls and Fabric evidence."""
        if self.error is not None:
            self.error.validate()
            return
        if not self.mappings:
            raise ValidationError("mappings is required")
        if not self.tool_evidence:
            raise MissingCitationError("control mapping requires tool_evidence")
        for mapping in self.mappings:
            mapping.validate()
        for evidence in self.tool_evidence:
            evidence.validate()


@dataclass(frozen=True)
class GapAnalysisRequest:
    """Request to analyze gaps for mapped obligations and controls.

    ``obligations`` and ``controls`` carry in-context facts (id/theme/summary/
    target_maturity/current_maturity/etc.). ``mappings`` carries authoritative
    obligation→control pairs produced by stage 1 so the Gap Analyst does not
    have to re-derive them (fresh mappings won't exist in the lakehouse yet).
    All three are optional to preserve backward compatibility.
    """

    change_id: str
    obligation_ids: list[str]
    control_ids: list[str]
    obligations: list[dict] = field(default_factory=list)
    controls: list[dict] = field(default_factory=list)
    mappings: list[dict] = field(default_factory=list)

    def validate(self) -> None:
        """Validate gap analysis request."""
        if not self.change_id.strip():
            raise ValidationError("change_id is required")
        if not self.obligation_ids:
            raise ValidationError("obligation_ids is required")
        if not self.control_ids:
            raise ValidationError("control_ids is required")


@dataclass(frozen=True)
class GapAnalysisFinding:
    """One validated gap finding."""

    gap_id: str
    obligation_id: str
    control_id: str
    severity: Literal["Critical", "High", "Medium", "Low", "None"]
    maturity_shortfall: int
    rationale: str
    source_refs: list[SourceReference]

    def validate(self) -> None:
        """Validate gap finding provenance."""
        for field_name, value in (
            ("gap_id", self.gap_id),
            ("obligation_id", self.obligation_id),
            ("control_id", self.control_id),
            ("rationale", self.rationale),
        ):
            if not value.strip():
                raise ValidationError(f"{field_name} is required")
        if self.maturity_shortfall < 0:
            raise ValidationError("maturity_shortfall cannot be negative")
        if not self.source_refs:
            raise MissingCitationError("gap finding must include source_refs")
        for source_ref in self.source_refs:
            source_ref.validate()


@dataclass(frozen=True)
class GapAnalysisResponse:
    """Gap Analyst agent response."""

    findings: list[GapAnalysisFinding]
    tool_evidence: list[ToolEvidence]
    error: AgentError | None = None

    def validate(self) -> None:
        """Validate gap findings and tool evidence.

        An empty ``findings`` list is a valid outcome: it means the Gap
        Analyst determined every obligation→control pair meets its target
        maturity with an active control. Callers should still log the
        justification (mapping shortfalls) so an operator can audit the
        "no gaps" conclusion. ``tool_evidence`` remains required — the
        agent must show it grounded its decision in real data.
        """
        if self.error is not None:
            self.error.validate()
            return
        if not self.tool_evidence:
            raise MissingCitationError("gap analysis requires tool_evidence")
        for finding in self.findings:
            finding.validate()
        for evidence in self.tool_evidence:
            evidence.validate()


@dataclass(frozen=True)
class RemediationRequest:
    """Request to plan remediation for known gaps.

    ``gaps`` carries in-context gap facts (id/obligation_id/control_id/
    severity/rationale). Freshly-derived gaps may not exist in the Fabric
    lakehouse yet. Optional for backward compatibility.
    """

    gap_ids: list[str]
    gaps: list[dict] = field(default_factory=list)

    def validate(self) -> None:
        """Validate remediation request."""
        if not self.gap_ids:
            raise ValidationError("gap_ids is required")
        if any(not gap_id.strip() for gap_id in self.gap_ids):
            raise ValidationError("gap_ids cannot contain empty values")


@dataclass(frozen=True)
class RemediationPlanItem:
    """One grounded remediation action."""

    remediation_id: str
    gap_id: str
    owner_unit_id: str
    priority: Literal["Critical", "High", "Medium", "Low"]
    estimated_effort_days: int
    action: str
    source_refs: list[SourceReference]

    def validate(self) -> None:
        """Validate remediation action grounding."""
        for field_name, value in (
            ("remediation_id", self.remediation_id),
            ("gap_id", self.gap_id),
            ("owner_unit_id", self.owner_unit_id),
            ("action", self.action),
        ):
            if not value.strip():
                raise ValidationError(f"{field_name} is required")
        if self.estimated_effort_days < 0:
            raise ValidationError("estimated_effort_days cannot be negative")
        if not self.source_refs:
            raise MissingCitationError("remediation item must include source_refs")
        for source_ref in self.source_refs:
            source_ref.validate()


@dataclass(frozen=True)
class RemediationResponse:
    """Remediation Planner agent response."""

    actions: list[RemediationPlanItem]
    tool_evidence: list[ToolEvidence]
    error: AgentError | None = None

    def validate(self) -> None:
        """Validate remediation actions and tool evidence."""
        if self.error is not None:
            self.error.validate()
            return
        if not self.actions:
            raise ValidationError("actions is required")
        if not self.tool_evidence:
            raise MissingCitationError("remediation response requires tool_evidence")
        for action in self.actions:
            action.validate()
        for evidence in self.tool_evidence:
            evidence.validate()


@dataclass(frozen=True)
class ScoreNarrationRequest:
    """Request to narrate score movement without recalculating scores.

    ``as_is`` / ``post_change`` / ``post_remediation`` carry the
    pre-computed score facts for THIS change. Freshly uploaded changes
    have no rows in the Fabric ``compliance_scores`` table yet, so the
    Narrator must be handed the numbers as authoritative input rather
    than expected to look them up. Defaults preserve backward-compat
    for callers that still want the agent to derive scores from Fabric.
    """

    change_id: str
    as_is: float = 0.0
    post_change: float = 0.0
    post_remediation: float = 0.0

    def validate(self) -> None:
        """Validate score narration request."""
        if not self.change_id.strip():
            raise ValidationError("change_id is required")
        for score_name, score in (
            ("as_is", self.as_is),
            ("post_change", self.post_change),
            ("post_remediation", self.post_remediation),
        ):
            if not 0 <= score <= 100:
                raise ValidationError(f"{score_name} must be between 0 and 100")


@dataclass(frozen=True)
class ScoreNarrationResponse:
    """Compliance Score Narrator response."""

    change_id: str
    narrative: str
    as_is: float
    post_change: float
    post_remediation: float
    source_refs: list[SourceReference]
    tool_evidence: list[ToolEvidence]
    error: AgentError | None = None

    def validate(self) -> None:
        """Validate score story preserves grounded numeric scores."""
        if self.error is not None:
            self.error.validate()
            return
        if not self.change_id.strip():
            raise ValidationError("change_id is required")
        if not self.narrative.strip():
            raise ValidationError("narrative is required")
        for score_name, score in (
            ("as_is", self.as_is),
            ("post_change", self.post_change),
            ("post_remediation", self.post_remediation),
        ):
            if not 0 <= score <= 100:
                raise ValidationError(f"{score_name} must be between 0 and 100")
        if not self.source_refs:
            raise MissingCitationError("score narration must include source_refs")
        if not self.tool_evidence:
            raise MissingCitationError("score narration requires tool_evidence")
        for source_ref in self.source_refs:
            source_ref.validate()
        for evidence in self.tool_evidence:
            evidence.validate()


@dataclass(frozen=True)
class LineageRequest:
    """Request to trace regulatory impact lineage."""

    entity_id: str

    def validate(self) -> None:
        """Validate lineage request."""
        if not self.entity_id.strip():
            raise ValidationError("entity_id is required")


@dataclass(frozen=True)
class LineageHop:
    """One source-to-target lineage hop."""

    source_id: str
    relationship: str
    target_id: str
    source_refs: list[SourceReference]

    def validate(self) -> None:
        """Validate one lineage hop."""
        for field_name, value in (
            ("source_id", self.source_id),
            ("relationship", self.relationship),
            ("target_id", self.target_id),
        ):
            if not value.strip():
                raise ValidationError(f"{field_name} is required")
        if not self.source_refs:
            raise MissingCitationError("lineage hop must include source_refs")
        for source_ref in self.source_refs:
            source_ref.validate()


@dataclass(frozen=True)
class LineageResponse:
    """Audit & Lineage agent response."""

    entity_id: str
    hops: list[LineageHop]
    tool_evidence: list[ToolEvidence]
    error: AgentError | None = None

    def validate(self) -> None:
        """Validate lineage traceability."""
        if self.error is not None:
            self.error.validate()
            return
        if not self.entity_id.strip():
            raise ValidationError("entity_id is required")
        if not self.hops:
            raise ValidationError("hops is required")
        if not self.tool_evidence:
            raise MissingCitationError("lineage response requires tool_evidence")
        for hop in self.hops:
            hop.validate()
        for evidence in self.tool_evidence:
            evidence.validate()
