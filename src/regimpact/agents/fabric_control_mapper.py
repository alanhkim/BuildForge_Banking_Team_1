"""Fabric-backed Control Mapper agent."""
from __future__ import annotations

from ..contracts import ControlMappingRequest, ControlMappingResponse
from .fabric_workflow import CONTROL_MAPPER_SPEC, FabricAgentHarness, FabricAgentSpec
from .foundry_client import FabricDataAgentClient


class FabricControlMapperAgent:
    """Map obligations to existing controls using Fabric-grounded context."""

    name = "Control Mapper"
    spec: FabricAgentSpec = CONTROL_MAPPER_SPEC

    def __init__(self, fabric_client: FabricDataAgentClient):
        self.harness = FabricAgentHarness(fabric_client)

    def map(self, request: ControlMappingRequest) -> ControlMappingResponse:
        """Return grounded obligation-control mappings."""
        return self.harness.map_controls(request)
