import json

import pytest

from regimpact.agents.fabric_workflow import (
    CONTROL_MAPPER_SPEC,
    FabricAgentHarness,
    FabricAgentHarnessError,
    _json_answer,
    _validated,
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
    ControlMapping,
    ControlMappingRequest,
    ControlMappingResponse,
    FabricQuestionRequest,
    FabricQuestionResponse,
    GapAnalysisRequest,
    GapAnalysisResponse,
    LineageRequest,
    RemediationRequest,
    ScoreNarrationRequest,
    ScoreNarrationResponse,
    SourceReference,
    ToolEvidence,
    ValidationError,
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


# ---------------------------------------------------------------------------
# ControlMappingResponse contract — empty-with-reason branch (Bishop's Fix 3).
# ---------------------------------------------------------------------------


def _valid_te():
    """Fresh ToolEvidence for contract-level tests (frozen dataclass reused)."""
    return ToolEvidence(
        tool_name="fabric_dataagent_preview",
        data_source="RegImpactLH",
        query="SELECT 1",
        source_refs=[source_ref("controls")],
    )


def _valid_mapping():
    return ControlMapping(
        obligation_id="OBL-DORA-01",
        control_id="CTL-OR-3",
        capability_id="CAP-RES",
        rationale="ICT resilience obligation maps to ICT continuity.",
        confidence="high",
        source_refs=[source_ref("controls")],
    )


def test_control_mapping_response_empty_with_reason_and_evidence_accepted():
    response = ControlMappingResponse(
        mappings=[],
        tool_evidence=[_valid_te()],
        reason="Shortlist exhausted — no candidate matched the obligation themes.",
    )

    # Should not raise — empty is valid iff reason + tool_evidence supplied.
    response.validate()


@pytest.mark.parametrize("reason", [None, "", "   "])
def test_control_mapping_response_empty_without_reason_rejected(reason):
    response = ControlMappingResponse(
        mappings=[],
        tool_evidence=[_valid_te()],
        reason=reason,
    )

    with pytest.raises(ValidationError, match="mappings is required"):
        response.validate()


def test_control_mapping_response_empty_with_reason_but_no_evidence_rejected():
    # Regression: tool_evidence stays required even when reason justifies
    # an empty mappings list. Grounding is non-negotiable.
    response = ControlMappingResponse(
        mappings=[],
        tool_evidence=[],
        reason="Shortlist exhausted.",
    )

    # validate() raises MissingCitationError (a subclass of ValidationError).
    with pytest.raises(ValidationError, match="tool_evidence"):
        response.validate()


def test_control_mapping_response_nonempty_no_reason_still_valid():
    # Normal path: populated mappings, no reason field. Must still validate.
    response = ControlMappingResponse(
        mappings=[_valid_mapping()],
        tool_evidence=[_valid_te()],
        reason=None,
    )

    response.validate()


# ---------------------------------------------------------------------------
# GapAnalysisRequest contract — empty-with-reason branch (Bishop's Gap
# Analyst empty-tolerance fix, mirrors ControlMappingResponse contract).
# See .squad/decisions/inbox/bishop-gap-analyst-empty-tolerance.md.
# ---------------------------------------------------------------------------


def test_gap_analysis_request_empty_control_ids_with_reason_accepted():
    # Empty control_ids paired with a non-empty reason is legitimate:
    # forwards a ControlMapper "shortlist exhausted" state to Gap Analyst
    # so it can still emit findings for uncovered obligations.
    request = GapAnalysisRequest(
        change_id="CHG-CM-STAGE-TEST",
        obligation_ids=["OBL-CM-01"],
        control_ids=[],
        reason="Shortlist exhausted for CHG-CM-STAGE-TEST.",
    )

    # Must not raise.
    request.validate()


@pytest.mark.parametrize("reason", [None, "", "   "])
def test_gap_analysis_request_empty_control_ids_without_reason_rejected(reason):
    # No reason (or whitespace-only) means the empty control_ids list has
    # no documented justification — treat it as a validation failure so
    # the pipeline surfaces the omission rather than masking it.
    request = GapAnalysisRequest(
        change_id="CHG-CM-STAGE-TEST",
        obligation_ids=["OBL-CM-01"],
        control_ids=[],
        reason=reason,
    )

    with pytest.raises(ValidationError, match="control_ids is required"):
        request.validate()


def test_gap_analysis_request_nonempty_control_ids_no_reason_still_valid():
    # Regression: normal happy path — populated control_ids, reason=None.
    # Must still validate after the empty-tolerance branch was added.
    request = GapAnalysisRequest(
        change_id="CHG-DORA",
        obligation_ids=["OBL-DORA-01"],
        control_ids=["CTL-OR-3"],
        reason=None,
    )

    request.validate()


def test_gap_analysis_request_empty_obligation_ids_always_rejected():
    # obligation_ids is unconditionally required — even a valid reason and
    # a populated control_ids list can't compensate. Confirms Bishop's
    # decision that obligations aren't a legit-empty artefact (an analysis
    # with nothing to analyse against has nothing to say).
    request = GapAnalysisRequest(
        change_id="CHG-DORA",
        obligation_ids=[],
        control_ids=["CTL-OR-3"],
        reason="This reason should not rescue an empty obligation list.",
    )

    with pytest.raises(ValidationError, match="obligation_ids is required"):
        request.validate()


# ---------------------------------------------------------------------------
# _validated wrapping — surface underlying reason + raw-answer snippet
# (Bishop's Fix 1).
# ---------------------------------------------------------------------------


def _fabric_response_with_answer(answer: str):
    return FabricQuestionResponse(
        question="q",
        answer=answer,
        agent_name="RegImpactControlMapper",
        agent_version="3",
        citations=[source_ref("controls")],
        tool_evidence=[_valid_te()],
        confidence="low",
    )


def test_fabric_harness_error_surfaces_validation_reason():
    # Empty-without-reason response — validate() will raise
    # "mappings is required (or provide non-empty reason)". _validated
    # must preserve the prefix AND surface that underlying detail.
    bad_response = ControlMappingResponse(
        mappings=[],
        tool_evidence=[_valid_te()],
        reason=None,
    )

    with pytest.raises(FabricAgentHarnessError) as exc_info:
        _validated(bad_response)

    message = exc_info.value.args[0]
    assert message.startswith("Fabric agent response failed validation")
    # Must contain something identifying the underlying failure (partial
    # match — the exact validator wording is Bishop's, not ours to pin).
    assert "mappings" in message or "reason" in message


def test_fabric_harness_error_includes_raw_answer_snippet():
    raw_answer = (
        '{"mappings": [], "tool_evidence": [{"tool_name": "x"}]}'
        # padding so the answer is >500 chars — verifies truncation still
        # yields a snippet, not an empty string.
        + " " * 600
    )
    fabric_response = _fabric_response_with_answer(raw_answer)
    bad_response = ControlMappingResponse(
        mappings=[],
        tool_evidence=[_valid_te()],
        reason=None,
    )

    with pytest.raises(FabricAgentHarnessError) as exc_info:
        _validated(bad_response, fabric_response=fabric_response)

    message = exc_info.value.args[0]
    assert "raw answer snippet" in message
    # First 20 chars of the answer must appear (snippet is repr'd, so
    # match against the un-quoted characters).
    assert '"mappings": []' in message


# ---------------------------------------------------------------------------
# _ask INFO log payload cardinalities (Bishop's Fix 2).
# ---------------------------------------------------------------------------


def test_fabric_harness_logs_payload_cardinalities(caplog):
    # Payload with two list-valued fields; _payload_cardinalities emits
    # sorted keys, so we can assert exact substrings.
    payload = {
        "obligation_ids": [f"OBL-{i:02d}" for i in range(15)],
        "tool_evidence": [{"tool_name": "fabric_dataagent_preview"}],
        "fabric_context_question": "Map DORA obligations.",
    }

    # Fabric client is only invoked to satisfy _ask's return path; we
    # care about the log record emitted BEFORE the client call.
    client = StubFabricClient({"mappings": []})
    harness = FabricAgentHarness(client)

    with caplog.at_level("INFO", logger="regimpact.agents.fabric_workflow"):
        harness._ask(CONTROL_MAPPER_SPEC, payload)

    request_records = [
        r for r in caplog.records
        if r.name == "regimpact.agents.fabric_workflow"
        and "Fabric agent request" in r.getMessage()
    ]
    assert request_records, "expected an INFO 'Fabric agent request' log"
    formatted = request_records[0].getMessage()
    assert "obligation_ids_count=15" in formatted
    assert "tool_evidence_count=1" in formatted
    # fabric_context_question is a string, not a list -> no count emitted.
    assert "fabric_context_question_count" not in formatted


# ---------------------------------------------------------------------------
# map_controls fail-fast semantics. Retry-on-empty-without-reason was
# removed 2026-07-17 for latency parity with the other Fabric agents
# after the semantic-retry removal in foundry_client.py. Empty-with-reason
# remains a documented success path per ControlMappingResponse.validate.
# ---------------------------------------------------------------------------


class ScriptedFabricClient:
    """Fabric client stub that cycles through canned inner-answer payloads.

    Each call returns the next payload in the queue wrapped in a full
    FabricQuestionResponse envelope (fresh tool_evidence per call).
    """

    def __init__(self, answer_payloads):
        self._answer_payloads = list(answer_payloads)
        self.questions = []

    def ask(self, question):
        self.questions.append(question)
        payload = self._answer_payloads.pop(0)
        return FabricQuestionResponse(
            question=question,
            answer=json.dumps(payload),
            agent_name="RegImpactControlMapper",
            agent_version="3",
            citations=[source_ref("controls")],
            tool_evidence=[tool_evidence()],
            confidence="high",
        )


def _valid_mapping_payload():
    return {
        "obligation_id": "OBL-DORA-01",
        "control_id": "CTL-OR-3",
        "capability_id": "CAP-RES",
        "rationale": "ICT resilience obligation maps to ICT continuity.",
        "confidence": "high",
        "source_refs": [source_ref_payload("v_obligation_control_map")],
    }


def test_map_controls_fails_fast_on_empty_without_reason():
    # Single attempt only — no retry. Empty mappings without a documented
    # reason fail contract validation and propagate immediately.
    client = ScriptedFabricClient([{"mappings": []}])
    harness = FabricAgentHarness(client)

    with pytest.raises(FabricAgentHarnessError) as exc_info:
        harness.map_controls(
            ControlMappingRequest(
                obligation_ids=["OBL-DORA-01"],
                fabric_context_question="Map DORA obligations.",
            )
        )

    assert len(client.questions) == 1, "must not retry"
    message = exc_info.value.args[0]
    assert message.startswith("Fabric agent response failed validation")
    assert "mappings" in message or "reason" in message


def test_map_controls_accepts_empty_with_reason():
    # Empty-with-reason is a documented success — no retry, no exception.
    client = ScriptedFabricClient(
        [
            {
                "mappings": [],
                "reason": "Shortlist exhausted — no candidate matched.",
            }
        ]
    )
    harness = FabricAgentHarness(client)

    response = harness.map_controls(
        ControlMappingRequest(
            obligation_ids=["OBL-DORA-01"],
            fabric_context_question="Map DORA obligations.",
        )
    )

    assert len(client.questions) == 1
    assert response.mappings == []
    assert response.reason == "Shortlist exhausted — no candidate matched."
    assert response.tool_evidence


def test_map_controls_returns_populated_mappings():
    client = ScriptedFabricClient(
        [{"mappings": [_valid_mapping_payload()]}]
    )
    harness = FabricAgentHarness(client)

    response = harness.map_controls(
        ControlMappingRequest(
            obligation_ids=["OBL-DORA-01"],
            fabric_context_question="Map DORA obligations.",
        )
    )

    assert len(client.questions) == 1
    assert len(response.mappings) == 1
    assert response.mappings[0].control_id == "CTL-OR-3"
    assert response.reason is None


# ---------------------------------------------------------------------------
# Pipeline stage: control_mapper WARNING on empty-with-reason (Bishop's
# pipeline continuation logic). After Bishop's Gap Analyst empty-tolerance
# fix (see .squad/decisions/inbox/bishop-gap-analyst-empty-tolerance.md),
# GapAnalysisRequest accepts empty ``control_ids`` when accompanied by a
# non-empty ``reason`` forwarded from the ControlMapper. The pipeline now
# runs end-to-end on the empty-with-reason path — we assert both stages'
# WARNING logs fire AND that the reason string is propagated verbatim
# into the Gap Analyst request.
# ---------------------------------------------------------------------------


def _minimal_estate_for_change():
    """Build the smallest Estate that lets AgentPipeline reach control_mapper."""
    from datetime import date

    from regimpact.models import (
        Control,
        ControlStatus,
        Criticality,
        Estate,
        MaturityLevel,
        Obligation as EstateObligation,
        Regulation as EstateRegulation,
        RegulatoryChange,
    )

    change_id = "CHG-CM-STAGE-TEST"
    reg_id = "REG-CM-STAGE-TEST"
    estate = Estate(
        regulations=[
            EstateRegulation(
                id=reg_id,
                name="Test Reg",
                short_code="TR",
                regulator="TEST",
                jurisdiction="EU",
                domain="Test",
                description="fixture",
            )
        ],
        changes=[
            RegulatoryChange(
                id=change_id,
                regulation_id=reg_id,
                title="Test change",
                reference="TR-1",
                summary="fixture",
                change_type="New",
                published_date=date(2026, 1, 1),
                effective_date=date(2026, 12, 31),
                criticality=Criticality.HIGH,
            )
        ],
        obligations=[
            EstateObligation(
                id="OBL-CM-01",
                change_id=change_id,
                regulation_id=reg_id,
                statement="Maintain ICT resilience.",
                article="Art. 1",
                theme="ICT_RESILIENCE",
                criticality=Criticality.HIGH,
                target_maturity=MaturityLevel.MANAGED,
            )
        ],
        controls=[
            Control(
                id="CTL-RES-1",
                name="ICT continuity control",
                control_family="Resilience",
                capability_id="CAP-RES",
                description="fixture",
                status=ControlStatus.IMPLEMENTED,
                maturity=MaturityLevel.REPEATABLE,
                owner_unit_id="BU-OPS",
            )
        ],
    )
    return estate, change_id


class _StubControlMapperEmptyWithReason:
    """Drop-in for FabricControlMapperAgent that returns empty-with-reason."""

    def __init__(self, *args, **kwargs):  # accept default-ctor call from pipeline
        self.calls = []

    def map(self, request):
        self.calls.append(request)
        return ControlMappingResponse(
            mappings=[],
            tool_evidence=[_valid_te()],
            reason="Shortlist exhausted for CHG-CM-STAGE-TEST.",
        )


class _StubGapAnalystEmpty:
    """Drop-in for FabricGapAnalystAgent that returns zero findings.

    Captures the request in ``calls`` so tests can assert the reason
    string forwarded from control_mapper reached the Gap Analyst
    unchanged. Zero findings + non-empty tool_evidence is a documented
    valid outcome per GapAnalysisResponse.validate().
    """

    def __init__(self, *args, **kwargs):
        self.calls: list = []

    def analyze(self, request):
        self.calls.append(request)
        return GapAnalysisResponse(
            findings=[],
            tool_evidence=[_valid_te()],
        )


class _StubScoreNarrator:
    """Drop-in for FabricScoreNarratorAgent that returns a valid narration.

    The pipeline always reaches score_narrator (even when gap_ids is
    empty — only remediation_planner is guarded). Returns pre-populated
    numeric scores + narrative so ScoreNarrationResponse.validate()
    passes.
    """

    def __init__(self, *args, **kwargs):
        self.calls: list = []

    def narrate(self, request):
        self.calls.append(request)
        return ScoreNarrationResponse(
            change_id=request.change_id,
            narrative="No mappings were derived; scores held at baseline.",
            as_is=request.as_is,
            post_change=request.post_change,
            post_remediation=request.post_remediation,
            source_refs=[source_ref("v_compliance_score_story")],
            tool_evidence=[_valid_te()],
        )


def test_control_mapper_pipeline_stage_logs_warning_on_empty_with_reason(
    monkeypatch, caplog
):
    """Empty-with-reason flows end-to-end: control_mapper -> gap_analyst.

    Bishop's Gap Analyst empty-tolerance fix (2026-07-17) means the
    pipeline no longer raises when control_mapper returns
    ``mappings=[]`` with a reason. The reason is forwarded verbatim
    into ``GapAnalysisRequest(control_ids=[], reason=<same>)`` and both
    stages log WARNING records for observability. remediation_planner
    is skipped because ``gap_ids`` is empty (guarded by ``if gap_ids:``),
    but score_narrator still runs.
    """
    from regimpact.agents import pipeline as pipeline_module
    from regimpact.settings import Settings

    estate, change_id = _minimal_estate_for_change()

    # Force foundry_fabric_enabled=True without hitting real Foundry/Fabric.
    monkeypatch.setattr(
        pipeline_module,
        "_settings",
        Settings(
            foundry_project_endpoint="https://example/api/projects/demo",
            foundry_executive_qa_agent_name="RegImpactQA",
            foundry_executive_qa_agent_version="3",
            fabric_workspace_id="ws-1",
            fabric_data_agent_id="da-1",
        ),
    )
    cm_stub = _StubControlMapperEmptyWithReason()
    ga_stub = _StubGapAnalystEmpty()
    sn_stub = _StubScoreNarrator()
    # Return the SAME stub instance on every construction so we can inspect
    # captured calls after pipeline.run(). The pipeline default-constructs
    # each agent class inside _run_fabric().
    monkeypatch.setattr(
        pipeline_module,
        "FabricControlMapperAgent",
        lambda *a, **kw: cm_stub,
    )
    monkeypatch.setattr(
        pipeline_module,
        "FabricGapAnalystAgent",
        lambda *a, **kw: ga_stub,
    )
    monkeypatch.setattr(
        pipeline_module,
        "FabricScoreNarratorAgent",
        lambda *a, **kw: sn_stub,
    )
    # FabricRemediationPlannerAgent must NOT be reached on the empty path
    # (guarded by `if gap_ids:`). Stub with a sentinel that fails loudly
    # if the guard ever regresses.
    def _remediation_should_not_be_called(*args, **kwargs):
        raise AssertionError(
            "remediation_planner must be skipped when gap_ids is empty"
        )

    monkeypatch.setattr(
        pipeline_module,
        "FabricRemediationPlannerAgent",
        _remediation_should_not_be_called,
    )

    pipeline = pipeline_module.AgentPipeline(estate)

    with caplog.at_level("WARNING", logger="regimpact.agents.pipeline"):
        # Must NOT raise — Bishop's fix means the empty-with-reason state
        # is a documented valid outcome that flows through the pipeline.
        report = pipeline.run(change_id)

    # Pipeline completed and produced a report.
    assert report["change_id"] == change_id
    assert report["llm_mode"] == "fabric-agentic"

    # ---- WARNING 1: control_mapper empty-with-reason ----
    cm_warnings = [
        r for r in caplog.records
        if r.name == "regimpact.agents.pipeline"
        and r.levelname == "WARNING"
        and "control_mapper returned empty mappings with reason" in r.getMessage()
    ]
    assert cm_warnings, (
        "expected a WARNING record for control_mapper empty-with-reason"
    )
    assert "Shortlist exhausted for CHG-CM-STAGE-TEST." in cm_warnings[0].getMessage()

    # ---- WARNING 2: gap_analyst propagating empty control_ids ----
    ga_warnings = [
        r for r in caplog.records
        if r.name == "regimpact.agents.pipeline"
        and r.levelname == "WARNING"
        and "Fabric stage propagating empty control_ids" in r.getMessage()
        and "stage=gap_analyst" in r.getMessage()
    ]
    assert ga_warnings, (
        "expected a WARNING record for gap_analyst empty-control_ids propagation"
    )
    assert "Shortlist exhausted for CHG-CM-STAGE-TEST." in ga_warnings[0].getMessage()

    # ---- Contract: reason propagates from ControlMapper into GapAnalyst ----
    # Bishop's Test 5 requirement folded in here rather than duplicated as a
    # separate test: an integration assertion that the request-building
    # helper inside pipeline._run_fabric forwards cm_response.reason
    # verbatim to GapAnalysisRequest.reason when mappings is empty.
    assert len(ga_stub.calls) == 1, "Gap Analyst must be invoked exactly once"
    ga_request = ga_stub.calls[0]
    assert isinstance(ga_request, GapAnalysisRequest)
    assert ga_request.change_id == change_id
    assert ga_request.control_ids == []
    assert ga_request.reason == "Shortlist exhausted for CHG-CM-STAGE-TEST."
    # obligation_ids is still forwarded — empty control_ids doesn't
    # imply empty obligations.
    assert ga_request.obligation_ids == ["OBL-CM-01"]



