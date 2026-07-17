import json

import pytest

from regimpact.agents.fabric_workflow import (
    CONTROL_MAPPER_SPEC,
    FabricAgentHarness,
    FabricAgentHarnessError,
    _json_answer,
)
from regimpact.agents.fabric_control_mapper import FabricControlMapperAgent
from regimpact.agents.fabric_executive_qa import FabricExecutiveQAAgent
from regimpact.agents.fabric_gap_analyst import FabricGapAnalystAgent
from regimpact.agents.fabric_lineage import FabricLineageAgent
from regimpact.agents.fabric_remediation_planner import FabricRemediationPlannerAgent
from regimpact.agents.fabric_score_narrator import FabricScoreNarratorAgent
from regimpact.agents.foundry_client import (
    FabricDataAgentClient,
    FabricDataAgentConfig,
    FabricDataAgentError,
    FoundryAgentClient,
    FoundryAgentConfig,
    _extract_json_block,
    _fabric_response_from_payload,
    _validate_inner_answer,
)
from regimpact.contracts import (
    ControlMappingRequest,
    FabricQuestionRequest,
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


# ---------------------------------------------------------------------------
# Fabric response hardening: lenient parsing, recovery, and semantic retry.
# ---------------------------------------------------------------------------


def _fabric_request(question: str = "How compliant is DORA?"):
    return FabricQuestionRequest(
        question=question,
        agent_name="RegImpactQA",
        agent_version="1",
        workspace_id="ws-1",
        data_agent_id="da-1",
        allowed_sources=["RegImpactLH"],
    )


def test_fabric_response_defaults_missing_metadata_fields(caplog):
    request = _fabric_request()
    payload = {"answer": "DORA moves from 54.8 to 59.6."}

    with caplog.at_level("WARNING"):
        response = _fabric_response_from_payload(payload, request)

    assert response.answer.startswith("DORA moves")
    assert response.citations == []
    assert response.tool_evidence == []
    assert response.confidence == "low"
    assert any(
        "missing metadata field(s)" in rec.message and "citations" in rec.message
        for rec in caplog.records
    )


def test_fabric_response_recovers_inner_payload_when_answer_missing(caplog):
    request = _fabric_request()
    inner = {
        "mappings": [
            {
                "obligation_id": "OBL-DORA-01",
                "control_id": "CTL-OR-3",
                "capability_id": "CAP-RES",
                "rationale": "ICT resilience obligation.",
                "confidence": "high",
                "source_refs": [source_ref_payload("v_obligation_control_map")],
            }
        ]
    }

    with caplog.at_level("WARNING"):
        response = _fabric_response_from_payload(inner, request)

    assert any(
        "recovered by treating top-level JSON as inner payload" in rec.message
        for rec in caplog.records
    )
    parsed = json.loads(response.answer)
    assert parsed == inner


def test_fabric_response_handles_answer_as_dict():
    request = _fabric_request()
    inner = {"findings": [{"obligation_id": "OBL-DORA-01"}]}
    payload = {"answer": inner, "citations": [], "tool_evidence": [], "confidence": "medium"}

    response = _fabric_response_from_payload(payload, request)

    assert isinstance(response.answer, str)
    assert json.loads(response.answer) == inner
    assert response.confidence == "medium"


def test_fabric_response_still_raises_when_answer_truly_missing():
    request = _fabric_request()
    payload = {"citations": [], "tool_evidence": [], "confidence": "low"}

    with pytest.raises(FabricDataAgentError, match="missing required field"):
        _fabric_response_from_payload(payload, request)


class _StubAgent:
    """Minimal Foundry runtime stub cycling through canned responses."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.prompts = []

    def run(self, prompt):
        self.prompts.append(prompt)
        if not self._responses:
            raise AssertionError("No more canned responses")
        return self._responses.pop(0)


def _fabric_client(agent):
    return FabricDataAgentClient(
        foundry_client=FoundryAgentClient(
            config=FoundryAgentConfig(
                project_endpoint="https://example.services.ai.azure.com/api/projects/demo",
                agent_name="RegImpactQA",
                agent_version="1",
                model_deployment_name="gpt-4o-mini",
            ),
            agent=agent,
        ),
        config=FabricDataAgentConfig(
            workspace_id="ws-1",
            data_agent_id="da-1",
        ),
    )


def test_ask_fails_fast_on_malformed_json():
    # Semantic retry was removed for latency. Any semantic failure surfaces
    # immediately after the first attempt.
    stub = _StubAgent(["definitely not json"])
    client = _fabric_client(stub)

    with pytest.raises(FabricDataAgentError, match="malformed JSON"):
        client.ask("How compliant is DORA?")

    assert len(stub.prompts) == 1


def test_extract_json_block_from_prose():
    text = 'Sure, here is the answer:\n{"findings": []}\n\nHope that helps!'
    assert _extract_json_block(text) == '{"findings": []}'


def test_extract_json_block_from_markdown_fence():
    text = '```json\n{"findings": [{"id": "F-1"}]}\n```'
    assert _extract_json_block(text) == '{"findings": [{"id": "F-1"}]}'


def test_extract_json_block_handles_nested_braces_and_strings():
    text = 'noise {"a": {"b": "text with } inside"}, "c": 1} trailing'
    extracted = _extract_json_block(text)
    assert json.loads(extracted) == {"a": {"b": "text with } inside"}, "c": 1}


def test_extract_json_block_raises_on_no_json():
    with pytest.raises(FabricDataAgentError, match="malformed JSON"):
        _extract_json_block("no json here at all")


def test_json_answer_accepts_dict_directly():
    inner = {"findings": [{"obligation_id": "OBL-1"}]}
    response = FabricQuestionResponse(
        question="Q",
        answer=inner,  # type: ignore[arg-type]
        agent_name="RegImpactQA",
        agent_version="1",
        citations=[source_ref("gaps")],
        tool_evidence=[tool_evidence()],
        confidence="low",
    )

    assert _json_answer(response) is inner


def test_json_answer_extracts_json_from_markdown_fence():
    inner = {"actions": [{"id": "A-1"}]}
    fenced = f"```json\n{json.dumps(inner)}\n```"
    response = FabricQuestionResponse(
        question="Q",
        answer=fenced,
        agent_name="RegImpactQA",
        agent_version="1",
        citations=[source_ref("remediation_actions")],
        tool_evidence=[tool_evidence()],
        confidence="low",
    )

    assert _json_answer(response) == inner


# ---------------------------------------------------------------------------
# Truncation retry: inner-answer JSON validated inside the semantic-retry
# loop so a cut-off response becomes a retryable failure with a tailored
# "be concise" nudge, instead of aborting the pipeline downstream.
# ---------------------------------------------------------------------------


def _tool_evidence_payload():
    return {
        "tool_name": "fabric_dataagent_preview",
        "data_source": "RegImpactLH",
        "query": "SELECT 1",
        "source_refs": [source_ref_payload("gaps")],
    }


def _envelope(answer_str, confidence="medium"):
    return json.dumps(
        {
            "answer": answer_str,
            "citations": [source_ref_payload("gaps")],
            "tool_evidence": [_tool_evidence_payload()],
            "confidence": confidence,
        }
    )


def _good_envelope():
    inner = {"findings": [{"gap_id": "g1", "control_id": "CTL-DQ-1"}]}
    return _envelope(json.dumps(inner), confidence="high")


def test_validate_inner_answer_accepts_dict():
    response = FabricQuestionResponse(
        question="Q",
        answer={"findings": [{"gap_id": "g1"}]},  # type: ignore[arg-type]
        agent_name="RegImpactQA",
        agent_version="1",
        citations=[source_ref("gaps")],
        tool_evidence=[tool_evidence()],
        confidence="low",
    )

    assert _validate_inner_answer(response) is None


def test_validate_inner_answer_accepts_prose_wrapped_json():
    response = FabricQuestionResponse(
        question="Q",
        answer='Sure: {"findings": []}',
        agent_name="RegImpactQA",
        agent_version="1",
        citations=[source_ref("gaps")],
        tool_evidence=[tool_evidence()],
        confidence="low",
    )

    assert _validate_inner_answer(response) is None


def test_ask_fails_fast_on_truncated_inner_answer():
    # Envelope parses, but the inner answer string is cut off mid-object.
    # Semantic retry was removed for latency — surfaces immediately with a
    # clear reason including truncated=true so operators can act.
    truncated_inner = '{"findings":[{"gap_id":"g1","control_id":"CTL-DQ-1"'
    stub = _StubAgent([_envelope(truncated_inner)])
    client = _fabric_client(stub)

    with pytest.raises(FabricDataAgentError) as exc:
        client.ask("How compliant is DORA?")

    message = str(exc.value)
    assert "truncated=true" in message
    assert len(stub.prompts) == 1


def test_ask_fails_fast_on_malformed_inner_answer_not_truncated():
    # Balanced braces but invalid JSON inside -> truncated=false.
    stub = _StubAgent([_envelope('{"findings": [WHOOPS]}')])
    client = _fabric_client(stub)

    with pytest.raises(FabricDataAgentError) as exc:
        client.ask("How compliant is DORA?")

    message = str(exc.value)
    assert "truncated=false" in message
    assert len(stub.prompts) == 1

