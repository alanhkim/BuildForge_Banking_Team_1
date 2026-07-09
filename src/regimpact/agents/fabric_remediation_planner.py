"""Fabric-backed Remediation Planner agent."""
from __future__ import annotations

from ..contracts import RemediationRequest, RemediationResponse
from .fabric_workflow import REMEDIATION_PLANNER_SPEC, FabricAgentHarness, FabricAgentSpec
from .foundry_client import FabricDataAgentClient


class FabricRemediationPlannerAgent:
    """Plan prioritized remediation actions using Fabric-grounded gaps."""

    name = "Remediation Planner"
    spec: FabricAgentSpec = REMEDIATION_PLANNER_SPEC

    def __init__(self, fabric_client: FabricDataAgentClient | None = None):
        fabric_client = fabric_client or FabricDataAgentClient.for_application_agent(
            "remediation_planner"
        )
        self.harness = FabricAgentHarness(fabric_client)

    def plan(self, request: RemediationRequest) -> RemediationResponse:
        """Return grounded remediation actions."""
        return self.harness.plan_remediation(request)
