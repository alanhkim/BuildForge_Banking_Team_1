"""Fabric-backed Audit & Lineage agent."""
from __future__ import annotations

from ..contracts import LineageRequest, LineageResponse
from .fabric_workflow import LINEAGE_AGENT_SPEC, FabricAgentHarness, FabricAgentSpec
from .foundry_client import FabricDataAgentClient


class FabricLineageAgent:
    """Trace regulatory impact lineage using Fabric relationships and ontology."""

    name = "Audit & Lineage Agent"
    spec: FabricAgentSpec = LINEAGE_AGENT_SPEC

    def __init__(self, fabric_client: FabricDataAgentClient):
        self.harness = FabricAgentHarness(fabric_client)

    def trace(self, request: LineageRequest) -> LineageResponse:
        """Return grounded lineage hops."""
        return self.harness.trace_lineage(request)
