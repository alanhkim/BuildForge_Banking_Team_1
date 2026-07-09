"""Fabric-backed Executive Q&A agent."""
from __future__ import annotations

from ..contracts import FabricQuestionResponse
from .fabric_workflow import EXECUTIVE_QA_SPEC, FabricAgentSpec
from .foundry_client import FabricDataAgentClient


class FabricExecutiveQAAgent:
    """Answer executive regulatory-impact questions through Fabric grounding."""

    name = "Executive Q&A Agent"
    spec: FabricAgentSpec = EXECUTIVE_QA_SPEC

    def __init__(self, fabric_client: FabricDataAgentClient | None = None):
        fabric_client = fabric_client or FabricDataAgentClient.for_application_agent(
            "executive_qa"
        )
        self.fabric_client = fabric_client

    def ask(self, question: str) -> FabricQuestionResponse:
        """Return a grounded executive answer with citations and tool evidence."""
        prompt = self.spec.build_prompt({"question": question})
        return self.fabric_client.ask(prompt)
