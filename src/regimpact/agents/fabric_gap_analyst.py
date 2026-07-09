"""Fabric-backed Gap Analyst agent."""
from __future__ import annotations

from ..contracts import GapAnalysisRequest, GapAnalysisResponse
from .fabric_workflow import GAP_ANALYST_SPEC, FabricAgentHarness, FabricAgentSpec
from .foundry_client import FabricDataAgentClient


class FabricGapAnalystAgent:
    """Identify maturity/evidence gaps and blast radius from Fabric."""

    name = "Gap Analyst"
    spec: FabricAgentSpec = GAP_ANALYST_SPEC

    def __init__(self, fabric_client: FabricDataAgentClient):
        self.harness = FabricAgentHarness(fabric_client)

    def analyze(self, request: GapAnalysisRequest) -> GapAnalysisResponse:
        """Return grounded gap findings."""
        return self.harness.analyze_gaps(request)
