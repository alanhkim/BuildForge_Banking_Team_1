import json
import sys
import types

import pytest

from regimpact.agents.foundry_client import (
    FabricDataAgentClient,
    FabricDataAgentConfig,
    FabricDataAgentError,
    FoundryAgentClient,
    FoundryAgentConfig,
    FoundryAgentError,
)
from regimpact.contracts import (
    ControlMapping,
    ControlMappingResponse,
    FabricQuestionRequest,
    FabricQuestionResponse,
    GapAnalysisFinding,
    GapAnalysisResponse,
    LineageHop,
    LineageResponse,
    MissingCitationError,
    RemediationPlanItem,
    RemediationResponse,
    ScoreNarrationResponse,
    SourceReference,
    ToolEvidence,
    ValidationError,
)
from regimpact.settings import Settings


class StubAgent:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.prompts = []

    def run(self, prompt):
        self.prompts.append(prompt)
        if self.error:
            raise self.error
        return self.response


def source_ref(name="compliance_scores"):
    return SourceReference(source="RegImpactLH", reference_type="table", name=name)


def evidence():
    return ToolEvidence(
        tool_name="fabric_dataagent_preview",
        data_source="RegImpactLH",
        query="SELECT Scenario, Score FROM compliance_scores",
        source_refs=[source_ref()],
    )


def test_fabric_question_contract_requires_citations_and_tool_evidence():
    response = FabricQuestionResponse(
        question="How compliant is DORA?",
        answer="DORA moves from 54.8 to 59.6 after remediation.",
        agent_name="RegImpactQA",
        agent_version="1",
        citations=[source_ref()],
        tool_evidence=[evidence()],
        confidence="high",
    )

    response.validate()


def test_fabric_question_contract_rejects_ungrounded_success():
    response = FabricQuestionResponse(
        question="How compliant is DORA?",
        answer="Looks good.",
        agent_name="RegImpactQA",
        agent_version="1",
        citations=[],
        tool_evidence=[],
        confidence="low",
    )

    with pytest.raises(MissingCitationError):
        response.validate()


def test_fabric_question_request_requires_configuration():
    request = FabricQuestionRequest(
        question=" ",
        agent_name="RegImpactQA",
        agent_version="1",
        workspace_id="workspace",
        data_agent_id="agent",
    )

    with pytest.raises(ValidationError, match="question is required"):
        request.validate()


def test_agent_workflow_contracts_validate_grounded_outputs():
    mapping = ControlMappingResponse(
        mappings=[
            ControlMapping(
                obligation_id="OBL-DORA-01",
                control_id="CTL-OR-3",
                capability_id="CAP-RES",
                rationale="The control realizes ICT resilience.",
                confidence="high",
                source_refs=[source_ref("relationships")],
            )
        ],
        tool_evidence=[evidence()],
    )
    gap = GapAnalysisResponse(
        findings=[
            GapAnalysisFinding(
                gap_id="GAP-OBL-DORA-01-CTL-OR-3",
                obligation_id="OBL-DORA-01",
                control_id="CTL-OR-3",
                severity="Critical",
                maturity_shortfall=3,
                rationale="Target maturity exceeds current maturity.",
                source_refs=[source_ref("gaps")],
            )
        ],
        tool_evidence=[evidence()],
    )
    remediation = RemediationResponse(
        actions=[
            RemediationPlanItem(
                remediation_id="REM-GAP-001",
                gap_id="GAP-OBL-DORA-01-CTL-OR-3",
                owner_unit_id="BU-OPS",
                priority="Critical",
                estimated_effort_days=110,
                action="Uplift ICT continuity and recovery control.",
                source_refs=[source_ref("remediation_actions")],
            )
        ],
        tool_evidence=[evidence()],
    )
    score = ScoreNarrationResponse(
        change_id="CHG-DORA",
        narrative="Post-change drops and post-remediation recovers.",
        as_is=54.8,
        post_change=53.3,
        post_remediation=59.6,
        source_refs=[source_ref("compliance_scores")],
        tool_evidence=[evidence()],
    )
    lineage = LineageResponse(
        entity_id="REG-DORA",
        hops=[
            LineageHop(
                source_id="CHG-DORA",
                relationship="INTRODUCES_OBLIGATION",
                target_id="OBL-DORA-01",
                source_refs=[source_ref("relationships")],
            )
        ],
        tool_evidence=[evidence()],
    )

    for response in (mapping, gap, remediation, score, lineage):
        response.validate()


def test_settings_capture_foundry_fabric_configuration():
    config = Settings(
        foundry_project_endpoint="https://example.services.ai.azure.com/api/projects/demo",
        foundry_fabric_agent_name="RegImpactQA",
        foundry_fabric_agent_version="1",
        fabric_workspace_id="workspace",
        fabric_data_agent_id="data-agent",
    )

    assert config.foundry_fabric_enabled


def test_foundry_agent_client_surfaces_missing_configuration():
    client = FoundryAgentClient(
        config=FoundryAgentConfig(
            project_endpoint="",
            agent_name="",
            agent_version="",
        )
    )

    with pytest.raises(FoundryAgentError, match="Missing Foundry agent configuration"):
        client.invoke("hello")


def test_foundry_agent_client_invokes_injected_agent():
    client = FoundryAgentClient(
        config=FoundryAgentConfig(
            project_endpoint="https://example.services.ai.azure.com/api/projects/demo",
            agent_name="RegImpactQA",
            agent_version="1",
        ),
        agent=StubAgent(response="hello"),
    )

    response = client.invoke("hello")

    assert response.text == "hello"
    assert response.agent_name == "RegImpactQA"
    assert response.agent_version == "1"


def test_foundry_agent_client_uses_ai_projects_agent_reference(monkeypatch):
    calls = {}

    class FakeCredential:
        pass

    class FakeDefaultAzureCredential:
        def __new__(cls):
            calls["credential_created"] = True
            return FakeCredential()

    class FakeResponse:
        output_text = "hello from Fabric"
        metadata = {"response_id": "resp-1"}

    class FakeResponses:
        def create(self, **kwargs):
            calls["responses_create"] = kwargs
            return FakeResponse()

    class FakeOpenAIClient:
        responses = FakeResponses()

    class FakeAIProjectClient:
        def __init__(self, **kwargs):
            calls["project_client"] = kwargs

        def get_openai_client(self):
            calls["get_openai_client"] = True
            return FakeOpenAIClient()

    azure_identity_module = types.ModuleType("azure.identity")
    azure_identity_module.DefaultAzureCredential = FakeDefaultAzureCredential
    azure_ai_projects_module = types.ModuleType("azure.ai.projects")
    azure_ai_projects_module.AIProjectClient = FakeAIProjectClient
    monkeypatch.setitem(sys.modules, "azure.identity", azure_identity_module)
    monkeypatch.setitem(sys.modules, "azure.ai.projects", azure_ai_projects_module)

    client = FoundryAgentClient(
        config=FoundryAgentConfig(
            project_endpoint="https://example.services.ai.azure.com/api/projects/demo",
            agent_name="RegImpactQA",
            agent_version="4",
        )
    )

    response = client.invoke("How compliant is DORA?")

    assert response.text == "hello from Fabric"
    assert calls["credential_created"]
    assert calls["project_client"]["endpoint"] == (
        "https://example.services.ai.azure.com/api/projects/demo"
    )
    assert calls["project_client"]["credential"].__class__ is FakeCredential
    assert calls["get_openai_client"]
    assert calls["responses_create"] == {
        "input": [{"role": "user", "content": "How compliant is DORA?"}],
        "extra_body": {
            "agent_reference": {
                "name": "RegImpactQA",
                "version": "4",
                "type": "agent_reference",
            }
        },
        "timeout": 120,
    }


def test_foundry_agent_client_wraps_openai_errors(monkeypatch):
    class FakeOpenAIError(Exception):
        pass

    class FakeCredential:
        pass

    class FakeDefaultAzureCredential:
        def __new__(cls):
            return FakeCredential()

    class FakeResponses:
        def create(self, **kwargs):
            raise FakeOpenAIError("tool_user_error")

    class FakeOpenAIClient:
        responses = FakeResponses()

    class FakeAIProjectClient:
        def __init__(self, **kwargs):
            pass

        def get_openai_client(self):
            return FakeOpenAIClient()

    openai_module = types.ModuleType("openai")
    openai_module.OpenAIError = FakeOpenAIError
    azure_identity_module = types.ModuleType("azure.identity")
    azure_identity_module.DefaultAzureCredential = FakeDefaultAzureCredential
    azure_ai_projects_module = types.ModuleType("azure.ai.projects")
    azure_ai_projects_module.AIProjectClient = FakeAIProjectClient
    monkeypatch.setitem(sys.modules, "openai", openai_module)
    monkeypatch.setitem(sys.modules, "azure.identity", azure_identity_module)
    monkeypatch.setitem(sys.modules, "azure.ai.projects", azure_ai_projects_module)

    client = FoundryAgentClient(
        config=FoundryAgentConfig(
            project_endpoint="https://example.services.ai.azure.com/api/projects/demo",
            agent_name="RegImpactQA",
            agent_version="4",
        )
    )

    with pytest.raises(FoundryAgentError, match="Foundry agent invocation failed"):
        client.invoke("How compliant is DORA?")


def test_fabric_data_agent_client_returns_validated_response():
    payload = {
        "question": "How compliant is DORA?",
        "answer": "DORA moves from 54.8 AsIs to 53.3 PostChange and 59.6 PostRemediation.",
        "confidence": "high",
        "citations": [
            {
                "source": "RegImpactLH",
                "reference_type": "table",
                "name": "compliance_scores",
                "value": "CHG-DORA",
            }
        ],
        "tool_evidence": [
            {
                "tool_name": "fabric_dataagent_preview",
                "data_source": "RegImpactLH",
                "query": "SELECT Scenario, Score FROM compliance_scores",
                "source_refs": [
                    {
                        "source": "RegImpactLH",
                        "reference_type": "field",
                        "name": "Score",
                    }
                ],
            }
        ],
    }
    foundry = FoundryAgentClient(
        config=FoundryAgentConfig(
            project_endpoint="https://example.services.ai.azure.com/api/projects/demo",
            agent_name="RegImpactQA",
            agent_version="1",
        ),
        agent=StubAgent(response=json.dumps(payload)),
    )
    client = FabricDataAgentClient(
        foundry_client=foundry,
        config=FabricDataAgentConfig(
            workspace_id="workspace",
            data_agent_id="data-agent",
        ),
    )

    response = client.ask("How compliant is DORA?")

    assert response.answer.startswith("DORA moves")
    assert response.citations[0].name == "compliance_scores"


def test_fabric_data_agent_client_rejects_malformed_response():
    foundry = FoundryAgentClient(
        config=FoundryAgentConfig(
            project_endpoint="https://example.services.ai.azure.com/api/projects/demo",
            agent_name="RegImpactQA",
            agent_version="1",
        ),
        agent=StubAgent(response="not json"),
    )
    client = FabricDataAgentClient(
        foundry_client=foundry,
        config=FabricDataAgentConfig(
            workspace_id="workspace",
            data_agent_id="data-agent",
        ),
    )

    with pytest.raises(FabricDataAgentError, match="malformed JSON"):
        client.ask("How compliant is DORA?")


def test_fabric_data_agent_client_surfaces_foundry_timeout():
    foundry = FoundryAgentClient(
        config=FoundryAgentConfig(
            project_endpoint="https://example.services.ai.azure.com/api/projects/demo",
            agent_name="RegImpactQA",
            agent_version="1",
        ),
        agent=StubAgent(error=TimeoutError("504 Gateway Time-out")),
    )
    client = FabricDataAgentClient(
        foundry_client=foundry,
        config=FabricDataAgentConfig(
            workspace_id="workspace",
            data_agent_id="data-agent",
        ),
    )

    with pytest.raises(FabricDataAgentError, match="invocation failed"):
        client.ask("How compliant is DORA?")
