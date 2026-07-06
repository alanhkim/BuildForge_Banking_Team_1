"""Deterministic free-text regulation interpreter."""
from __future__ import annotations

import re

from ..catalog import load_catalog

_CRITICALITIES = ["Low", "Medium", "High", "Critical"]


def _allowed_themes() -> dict[str, list[str]]:
    """Return theme names grounded in the control-family catalog."""
    catalog = load_catalog()
    family_names = {family["id"]: family["name"] for family in catalog["control_families"]}
    return {
        theme: [
            family_names.get(family_id, family_id)
            for family_id in spec["control_families"]
        ]
        for theme, spec in catalog["themes"].items()
    }


class TextRegulationInterpreterAgent:
    """Keyword-grounded text interpreter for offline demos."""

    name = "Regulation Interpreter"

    def interpret(
        self,
        text: str,
        *,
        regulation_name: str = "Uploaded Regulation",
    ) -> list[dict]:
        del regulation_name
        return self._deterministic(text, _allowed_themes())

    def _deterministic(self, text: str, themes: dict[str, list[str]]) -> list[dict]:
        keywords = {
            "DATA_LINEAGE": ["lineage", "provenance", "traceab"],
            "DATA_QUALITY": ["data quality", "accuracy", "completeness"],
            "TRAINING_DATA": ["training data", "dataset", "representative"],
            "AI_GOVERNANCE": [
                "human oversight",
                "ai governance",
                "high-risk ai",
                "oversight",
            ],
            "MODEL_RISK": ["model", "robustness", "validation", "backtest"],
            "TRACEABILITY": ["record", "logs", "logging", "audit trail"],
            "PRIVACY": ["personal data", "consent", "data subject", "breach"],
            "RETENTION": ["retention", "retain", "no longer than"],
            "SANCTIONS": ["sanction", "watchlist"],
            "KYC_CDD": ["due diligence", "beneficial owner", "know your customer", "kyc"],
            "TXN_MONITORING": ["transaction", "monitoring", "suspicious"],
            "SAR_REPORTING": ["suspicious activity", "sar", "report suspicious"],
            "ICT_RESILIENCE": ["resilience", "impact tolerance", "business service"],
            "INCIDENT_MGMT": ["incident", "disclose", "notification"],
            "CYBER": ["cyber", "security control", "vulnerab"],
            "ACCESS_CONTROL": ["access control", "privileged", "least privilege"],
            "CAPITAL_ADEQUACY": ["capital", "risk-weighted", "rwa"],
            "SCA": ["authentication", "strong customer"],
            "CONDUCT": ["fair value", "customer outcome", "good outcomes"],
            "REG_REPORTING": ["regulatory report", "prudential report"],
            "AUDITABILITY": ["audit", "assurance", "evidence"],
            "METADATA": ["catalog", "glossary", "metadata"],
        }
        obligations = []
        for sentence in re.split(r"(?<=[.;])\s+", text):
            lowered = sentence.lower().strip()
            if len(lowered) < 25:
                continue
            for theme, terms in keywords.items():
                if theme in themes and any(term in lowered for term in terms):
                    criticality = (
                        "Critical"
                        if any(word in lowered for word in ["must", "shall", "required"])
                        else "High"
                    )
                    obligations.append(
                        {
                            "statement": sentence.strip(),
                            "theme": theme,
                            "article": "",
                            "criticality": (
                                criticality
                                if criticality in _CRITICALITIES
                                else "High"
                            ),
                            "target_maturity": 4,
                        }
                    )
                    break
        return obligations
