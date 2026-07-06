"""
Typed contracts for the Regulation Interpreter agent.

Defines request/response contracts with validation for interpreting
regulatory changes into structured obligations.
"""
from dataclasses import dataclass, field
from typing import List, Optional, Literal


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
    offline_mode: bool = True

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
    mode: Literal["deterministic-fallback", "foundry-model"] = "deterministic-fallback"
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
