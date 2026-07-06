"""
Deterministic catalog fixtures for known regulatory changes.

Provides offline fallback data for DORA and other regulations
to enable network-free demos and testing.
"""
from typing import Dict, List, Optional
from .contracts import Obligation


# Deterministic DORA catalog entry
DORA_OBLIGATIONS = [
    Obligation(
        id="OBL-DORA-01",
        change_id="CHG-DORA",
        theme="ICT_RESILIENCE",
        summary="Maintain mature ICT continuity and recovery controls.",
        target_maturity=4,
        criticality="Critical",
        affected_data_domain_ids=["DD-PII"],
        source_refs=["catalog:REG-DORA:OBL-DORA-01"],
        notes=[]
    )
]


class CatalogFixture:
    """Deterministic catalog for known regulations."""

    def __init__(self):
        self._catalog: Dict[str, List[Obligation]] = {
            "REG-DORA": DORA_OBLIGATIONS,
        }

    def get_obligations(self, regulation_id: str, change_id: str) -> Optional[List[Obligation]]:
        """
        Retrieve obligations for a known regulation change.

        Args:
            regulation_id: Regulation identifier (e.g., "REG-DORA")
            change_id: Change identifier (e.g., "CHG-DORA")

        Returns:
            List of obligations if found, None otherwise.
        """
        obligations = self._catalog.get(regulation_id)
        if obligations is None:
            return None

        # Filter by change_id to support multiple changes per regulation
        matching = [obl for obl in obligations if obl.change_id == change_id]
        return matching if matching else None

    def has_entry(self, regulation_id: str, change_id: str) -> bool:
        """Check if catalog has an entry for the given regulation and change."""
        return self.get_obligations(regulation_id, change_id) is not None

    def list_regulations(self) -> List[str]:
        """List all regulation IDs in the catalog."""
        return list(self._catalog.keys())
