"""Agent 3 — Gap Analysis. Wraps the deterministic impact engine."""
from __future__ import annotations

from ..impact import ImpactEngine
from ..models import Estate


class GapAnalysisAgent:
    name = "Gap Analysis"

    def __init__(self, estate: Estate):
        self.est = estate

    def run(self, change_id: str) -> dict:
        engine = ImpactEngine(self.est)
        return engine.analyze_change(change_id)
