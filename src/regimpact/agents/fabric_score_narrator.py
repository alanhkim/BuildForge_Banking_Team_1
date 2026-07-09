"""Fabric-backed Compliance Score Narrator agent."""
from __future__ import annotations

from ..contracts import ScoreNarrationRequest, ScoreNarrationResponse
from .fabric_workflow import SCORE_NARRATOR_SPEC, FabricAgentHarness, FabricAgentSpec
from .foundry_client import FabricDataAgentClient


class FabricScoreNarratorAgent:
    """Explain compliance score movement without changing Fabric score facts."""

    name = "Compliance Score Narrator"
    spec: FabricAgentSpec = SCORE_NARRATOR_SPEC

    def __init__(self, fabric_client: FabricDataAgentClient | None = None):
        fabric_client = fabric_client or FabricDataAgentClient.for_application_agent(
            "score_narrator"
        )
        self.harness = FabricAgentHarness(fabric_client)

    def narrate(self, request: ScoreNarrationRequest) -> ScoreNarrationResponse:
        """Return a grounded score movement narrative."""
        return self.harness.narrate_score(request)
