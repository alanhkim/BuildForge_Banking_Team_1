import json

import pytest

from regimpact.agents.fabric_workflow import (
    CONTROL_MAPPER_SPEC,
    FabricAgentHarness,
    FabricAgentHarnessError,
)
from regimpact.agents.fabric_control_mapper import FabricControlMapperAgent
from regimpact.agents.fabric_executive_qa import FabricExecutiveQAAgent
from regimpact.agents.fabric_gap_analyst import FabricGapAnalystAgent
from regimpact.agents.fabric_lineage import FabricLineageAgent
from regimpact.agents.fabric_remediation_planner import FabricRemediationPlannerAgent
from regimpact.agents.fabric_score_narrator import FabricScoreNarratorAgent
from regimpact.contracts import (
    ControlMappingRequest,
    FabricQuestionResponse,
    GapAnalysisRequest,
    LineageRequest,
    RemediationRequest,
    ScoreNarrationRequest,
    SourceReference,
    ToolEvidence,
)


class StubFabricClient:
    def __init__(self, answer_payload):
        self.answer_payload = answer_payload
        self.questions = []

    def ask(self, question):
        self.questions.append(question)
        return FabricQuestionResponse(
            question=question,
            answer=json.dumps(self.answer_payload),
            agent_name="RegImpactQA",
            agent_version="1",
            citations=[source_ref("compliance_scores")],
            tool_evidence=[tool_evidence()],
            confidence="high",
        )


class EchoFabricClient:
    def __init__(self):
        self.questions = []

    def ask(self, question):
        self.questions.append(question)
        return FabricQuestionResponse(
            question=question,
            answer="Executive answer.",
            agent_name="RegImpactQA",
            agent_version="1",
            citations=[source_ref("v_compliance_score_story")],
            tool_evidence=[tool_evidence()],
            confidence="high",
        )


class MalformedFabricClient:
    def ask(self, question):
        return FabricQuestionResponse(
            question=question,
            answer="not json",
            agent_name="RegImpactQA",
            agent_version="1",
            citations=[source_ref("gaps")],
            tool_evidence=[tool_evidence()],
            confidence="high",
        )


def source_ref(name):
    return SourceReference(
        source="RegImpactLH",
        reference_type="table",
        name=name,
        value="CHG-DORA",
    )


def source_ref_payload(name):
    return {
        "source": "RegImpactLH",
        "reference_type": "table",
        "name": name,
        "value": "CHG-DORA",
    }


def tool_evidence():
    return ToolEvidence(
        tool_name="fabric_dataagent_preview",
        data_source="RegImpactLH",
        query="SELECT * FROM compliance_scores",
        source_refs=[source_ref("compliance_scores")],
    )


def test_agent_spec_prompt_frames_required_sources_and_contract():
    prompt = CONTROL_MAPPER_SPEC.build_prompt(
        {
            "obligation_ids": ["OBL-DORA-01"],
            "fabric_context_question": "Map the obligation.",
        }
    )

    assert "Agent: Control Mapper" in prompt
    assert "Use Fabric Data Agent grounding only" in prompt
    assert "v_obligation_control_map" in prompt
    assert "Return only JSON" in prompt
    assert "OBL-DORA-01" in prompt


def test_control_mapper_harness_returns_typed_response():
    client = StubFabricClient(
        {
            "mappings": [
                {
                    "obligation_id": "OBL-DORA-01",
                    "control_id": "CTL-OR-3",
                    "capability_id": "CAP-RES",
                    "rationale": "ICT resilience obligation maps to ICT continuity.",
                    "confidence": "high",
                    "source_refs": [source_ref_payload("v_obligation_control_map")],
                }
            ]
        }
    )
    harness = FabricAgentHarness(client)

    response = harness.map_controls(
        ControlMappingRequest(
            obligation_ids=["OBL-DORA-01"],
            fabric_context_question="Map DORA obligations.",
        )
    )

    assert response.mappings[0].control_id == "CTL-OR-3"
    assert response.tool_evidence[0].tool_name == "fabric_dataagent_preview"
    assert "Control Mapper" in client.questions[0]


def test_split_control_mapper_agent_uses_own_class_and_spec():
    client = StubFabricClient(
        {
            "mappings": [
                {
                    "obligation_id": "OBL-DORA-01",
                    "control_id": "CTL-OR-3",
                    "capability_id": "CAP-RES",
                    "rationale": "ICT resilience obligation maps to ICT continuity.",
                    "confidence": "high",
                    "source_refs": [source_ref_payload("v_obligation_control_map")],
                }
            ]
        }
    )
    agent = FabricControlMapperAgent(client)

    response = agent.map(
        ControlMappingRequest(
            obligation_ids=["OBL-DORA-01"],
            fabric_context_question="Map DORA obligations.",
        )
    )

    assert agent.name == "Control Mapper"
    assert agent.spec.name == "Control Mapper"
    assert response.mappings[0].control_id == "CTL-OR-3"
    assert "Agent: Control Mapper" in client.questions[0]


def test_split_control_mapper_defaults_to_deployed_foundry_agent():
    agent = FabricControlMapperAgent()

    assert agent.harness.fabric_client.foundry_client.config.agent_name == (
        "RegImpactControlMapper"
    )
    assert agent.harness.fabric_client.foundry_client.config.agent_version == "3"


def test_gap_analyst_harness_returns_typed_response():
    harness = FabricAgentHarness(
        StubFabricClient(
            {
                "findings": [
                    {
                        "gap_id": "GAP-OBL-DORA-01-CTL-OR-3",
                        "obligation_id": "OBL-DORA-01",
                        "control_id": "CTL-OR-3",
                        "severity": "Critical",
                        "maturity_shortfall": 3,
                        "rationale": "Control maturity is below target.",
                        "source_refs": [source_ref_payload("gaps")],
                    }
                ]
            }
        )
    )

    response = harness.analyze_gaps(
        GapAnalysisRequest(
            change_id="CHG-DORA",
            obligation_ids=["OBL-DORA-01"],
            control_ids=["CTL-OR-3"],
        )
    )

    assert response.findings[0].severity == "Critical"


def test_split_gap_analyst_agent_uses_own_class_and_spec():
    client = StubFabricClient(
        {
            "findings": [
                {
                    "gap_id": "GAP-OBL-DORA-01-CTL-OR-3",
                    "obligation_id": "OBL-DORA-01",
                    "control_id": "CTL-OR-3",
                    "severity": "Critical",
                    "maturity_shortfall": 3,
                    "rationale": "Control maturity is below target.",
                    "source_refs": [source_ref_payload("gaps")],
                }
            ]
        }
    )
    agent = FabricGapAnalystAgent(client)

    response = agent.analyze(
        GapAnalysisRequest(
            change_id="CHG-DORA",
            obligation_ids=["OBL-DORA-01"],
            control_ids=["CTL-OR-3"],
        )
    )

    assert agent.spec.name == "Gap Analyst"
    assert response.findings[0].gap_id == "GAP-OBL-DORA-01-CTL-OR-3"
    assert "Agent: Gap Analyst" in client.questions[0]


def test_split_gap_analyst_defaults_to_deployed_foundry_agent():
    agent = FabricGapAnalystAgent()

    assert agent.harness.fabric_client.foundry_client.config.agent_name == (
        "RegImpactGapAnalyst"
    )
    assert agent.harness.fabric_client.foundry_client.config.agent_version == "3"


def test_remediation_harness_returns_typed_response():
    harness = FabricAgentHarness(
        StubFabricClient(
            {
                "actions": [
                    {
                        "remediation_id": "REM-DORA-01",
                        "gap_id": "GAP-OBL-DORA-01-CTL-OR-3",
                        "owner_unit_id": "BU-OPS",
                        "priority": "Critical",
                        "estimated_effort_days": 110,
                        "action": "Uplift ICT continuity and recovery.",
                        "source_refs": [source_ref_payload("remediation_actions")],
                    }
                ]
            }
        )
    )

    response = harness.plan_remediation(
        RemediationRequest(gap_ids=["GAP-OBL-DORA-01-CTL-OR-3"])
    )

    assert response.actions[0].owner_unit_id == "BU-OPS"
    assert response.actions[0].estimated_effort_days == 110


def test_split_remediation_planner_agent_uses_own_class_and_spec():
    client = StubFabricClient(
        {
            "actions": [
                {
                    "remediation_id": "REM-DORA-01",
                    "gap_id": "GAP-OBL-DORA-01-CTL-OR-3",
                    "owner_unit_id": "BU-OPS",
                    "priority": "Critical",
                    "estimated_effort_days": 110,
                    "action": "Uplift ICT continuity and recovery.",
                    "source_refs": [source_ref_payload("remediation_actions")],
                }
            ]
        }
    )
    agent = FabricRemediationPlannerAgent(client)

    response = agent.plan(RemediationRequest(gap_ids=["GAP-OBL-DORA-01-CTL-OR-3"]))

    assert agent.spec.name == "Remediation Planner"
    assert response.actions[0].priority == "Critical"
    assert "Agent: Remediation Planner" in client.questions[0]


def test_split_remediation_planner_defaults_to_deployed_foundry_agent():
    agent = FabricRemediationPlannerAgent()

    assert agent.harness.fabric_client.foundry_client.config.agent_name == (
        "RegImpactRemediationPlanner"
    )
    assert agent.harness.fabric_client.foundry_client.config.agent_version == "3"


def test_score_narrator_harness_preserves_scores():
    harness = FabricAgentHarness(
        StubFabricClient(
            {
                "change_id": "CHG-DORA",
                "narrative": "Post-change drops, then remediation recovers.",
                "as_is": 54.8,
                "post_change": 53.3,
                "post_remediation": 59.6,
                "source_refs": [source_ref_payload("compliance_scores")],
            }
        )
    )

    response = harness.narrate_score(ScoreNarrationRequest(change_id="CHG-DORA"))

    assert response.as_is == 54.8
    assert response.post_change == 53.3
    assert response.post_remediation == 59.6


def test_split_score_narrator_agent_uses_own_class_and_spec():
    client = StubFabricClient(
        {
            "change_id": "CHG-DORA",
            "narrative": "Post-change drops, then remediation recovers.",
            "as_is": 54.8,
            "post_change": 53.3,
            "post_remediation": 59.6,
            "source_refs": [source_ref_payload("compliance_scores")],
        }
    )
    agent = FabricScoreNarratorAgent(client)

    response = agent.narrate(ScoreNarrationRequest(change_id="CHG-DORA"))

    assert agent.spec.name == "Compliance Score Narrator"
    assert response.post_remediation == 59.6
    assert "Agent: Compliance Score Narrator" in client.questions[0]


def test_split_score_narrator_defaults_to_deployed_foundry_agent():
    agent = FabricScoreNarratorAgent()

    assert agent.harness.fabric_client.foundry_client.config.agent_name == (
        "RegImpactScoreNarrator"
    )
    assert agent.harness.fabric_client.foundry_client.config.agent_version == "3"


def test_lineage_harness_returns_hops():
    harness = FabricAgentHarness(
        StubFabricClient(
            {
                "entity_id": "CHG-DORA",
                "hops": [
                    {
                        "source_id": "CHG-DORA",
                        "relationship": "INTRODUCES_OBLIGATION",
                        "target_id": "OBL-DORA-01",
                        "source_refs": [source_ref_payload("relationships")],
                    }
                ],
            }
        )
    )

    response = harness.trace_lineage(LineageRequest(entity_id="CHG-DORA"))

    assert response.hops[0].target_id == "OBL-DORA-01"


def test_split_lineage_agent_uses_own_class_and_spec():
    client = StubFabricClient(
        {
            "entity_id": "CHG-DORA",
            "hops": [
                {
                    "source_id": "CHG-DORA",
                    "relationship": "INTRODUCES_OBLIGATION",
                    "target_id": "OBL-DORA-01",
                    "source_refs": [source_ref_payload("relationships")],
                }
            ],
        }
    )
    agent = FabricLineageAgent(client)

    response = agent.trace(LineageRequest(entity_id="CHG-DORA"))

    assert agent.spec.name == "Audit & Lineage Agent"
    assert response.hops[0].relationship == "INTRODUCES_OBLIGATION"
    assert "Agent: Audit & Lineage Agent" in client.questions[0]


def test_split_lineage_defaults_to_deployed_foundry_agent():
    agent = FabricLineageAgent()

    assert agent.harness.fabric_client.foundry_client.config.agent_name == (
        "RegImpactAuditLineage"
    )
    assert agent.harness.fabric_client.foundry_client.config.agent_version == "3"


def test_split_executive_qa_agent_uses_own_prompt_framing():
    client = EchoFabricClient()
    agent = FabricExecutiveQAAgent(client)

    response = agent.ask("What is the DORA remediation story?")

    assert agent.spec.name == "Executive Q&A Agent"
    assert response.answer == "Executive answer."
    assert "Agent: Executive Q&A Agent" in client.questions[0]
    assert "What is the DORA remediation story?" in client.questions[0]


def test_split_executive_qa_defaults_to_deployed_foundry_agent():
    agent = FabricExecutiveQAAgent()

    assert agent.fabric_client.foundry_client.config.agent_name == (
        "RegImpactExecutiveQA"
    )
    assert agent.fabric_client.foundry_client.config.agent_version == "3"


def test_harness_surfaces_malformed_fabric_answer():
    harness = FabricAgentHarness(MalformedFabricClient())

    with pytest.raises(FabricAgentHarnessError, match="not valid JSON"):
        harness.narrate_score(ScoreNarrationRequest(change_id="CHG-DORA"))


def test_harness_rejects_missing_source_refs():
    harness = FabricAgentHarness(
        StubFabricClient(
            {
                "mappings": [
                    {
                        "obligation_id": "OBL-DORA-01",
                        "control_id": "CTL-OR-3",
                        "capability_id": "CAP-RES",
                        "rationale": "Missing citations.",
                        "confidence": "medium",
                        "source_refs": [],
                    }
                ]
            }
        )
    )

    with pytest.raises(FabricAgentHarnessError, match="failed validation"):
        harness.map_controls(
            ControlMappingRequest(
                obligation_ids=["OBL-DORA-01"],
                fabric_context_question="Map DORA obligations.",
            )
        )
