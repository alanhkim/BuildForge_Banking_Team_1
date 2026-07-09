"""Fabric-backed agent framing and harness."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from ..contracts import (
    ControlMapping,
    ControlMappingRequest,
    ControlMappingResponse,
    FabricQuestionResponse,
    GapAnalysisFinding,
    GapAnalysisRequest,
    GapAnalysisResponse,
    LineageHop,
    LineageRequest,
    LineageResponse,
    RemediationPlanItem,
    RemediationRequest,
    RemediationResponse,
    ScoreNarrationRequest,
    ScoreNarrationResponse,
    SourceReference,
    ValidationError,
)
from .foundry_client import FabricDataAgentClient, FabricDataAgentError


class FabricAgentHarnessError(FabricDataAgentError):
    """Raised when Fabric-backed agent framing or response validation fails."""


@dataclass(frozen=True)
class FabricAgentSpec:
    """Prompt and data framing for one Fabric-backed application agent."""

    name: str
    goal: str
    required_sources: tuple[str, ...]
    output_contract: str
    instructions: str

    def build_prompt(self, request_payload: dict[str, Any]) -> str:
        """Build a constrained Fabric Data Agent prompt for this application agent."""
        return "\n".join(
            [
                f"Agent: {self.name}",
                f"Goal: {self.goal}",
                "Use Fabric Data Agent grounding only. Do not use web search.",
                "Use only the required Fabric sources listed below.",
                f"Required sources: {', '.join(self.required_sources)}",
                self.instructions,
                f"Return only JSON matching this contract: {self.output_contract}",
                f"Request: {json.dumps(request_payload, sort_keys=True)}",
            ]
        )


CONTROL_MAPPER_SPEC = FabricAgentSpec(
    name="Control Mapper",
    goal="Map obligations to existing controls, capabilities, and evidence.",
    required_sources=(
        "v_obligation_control_map",
        "relationships",
        "obligations",
        "controls",
        "capabilities",
        "evidence",
        "technologies",
    ),
    output_contract=(
        '{"mappings":[{"obligation_id":string,"control_id":string,'
        '"capability_id":string,"rationale":string,"confidence":"low|medium|high",'
        '"source_refs":[{"source":string,"reference_type":"table|view|field|measure|relationship|entity",'
        '"name":string,"value":string}]}]}'
    ),
    instructions=(
        "Constrain every mapping to existing Fabric IDs. If no mapping exists, "
        "return an empty mappings array and explain missing data through the Fabric "
        "answer error path rather than inventing controls."
    ),
)

GAP_ANALYST_SPEC = FabricAgentSpec(
    name="Gap Analyst",
    goal="Identify maturity/evidence gaps and explain blast-radius drivers.",
    required_sources=(
        "v_gap_blast_radius",
        "v_evidence_health",
        "gaps",
        "controls",
        "evidence",
        "relationships",
        "RegImpact_Ontology",
    ),
    output_contract=(
        '{"findings":[{"gap_id":string,"obligation_id":string,"control_id":string,'
        '"severity":"Critical|High|Medium|Low|None","maturity_shortfall":integer,'
        '"rationale":string,"source_refs":[...]}]}'
    ),
    instructions=(
        "Use computed Fabric gap data when available. Cite maturity, evidence, and "
        "blast-radius sources. Do not calculate unsupported gaps from assumptions."
    ),
)

REMEDIATION_PLANNER_SPEC = FabricAgentSpec(
    name="Remediation Planner",
    goal="Create prioritized owner-assigned remediation actions for known gaps.",
    required_sources=(
        "v_remediation_priority",
        "remediation_actions",
        "gaps",
        "business_units",
        "controls",
        "evidence",
    ),
    output_contract=(
        '{"actions":[{"remediation_id":string,"gap_id":string,"owner_unit_id":string,'
        '"priority":"Critical|High|Medium|Low","estimated_effort_days":integer,'
        '"action":string,"source_refs":[...]}]}'
    ),
    instructions=(
        "Use owner and effort data from Fabric. Do not invent owners or estimates. "
        "Prioritize by Fabric severity, priority, maturity shortfall, and evidence status."
    ),
)

SCORE_NARRATOR_SPEC = FabricAgentSpec(
    name="Compliance Score Narrator",
    goal="Explain score movement without changing numeric score facts.",
    required_sources=(
        "v_compliance_score_story",
        "compliance_scores",
        "RegImpactSM_V1",
        "fact_compliance_score",
    ),
    output_contract=(
        '{"change_id":string,"narrative":string,"as_is":number,'
        '"post_change":number,"post_remediation":number,"source_refs":[...]}'
    ),
    instructions=(
        "Preserve score values exactly as returned from Fabric. Explain why "
        "PostChange drops and PostRemediation recovers using cited gap/remediation drivers."
    ),
)

LINEAGE_AGENT_SPEC = FabricAgentSpec(
    name="Audit & Lineage Agent",
    goal="Trace source-to-target lineage and explain relationship evidence.",
    required_sources=(
        "relationships",
        "RegImpact_Ontology",
        "v_product_regulatory_exposure",
        "purview lineage",
    ),
    output_contract=(
        '{"entity_id":string,"hops":[{"source_id":string,"relationship":string,'
        '"target_id":string,"source_refs":[...]}]}'
    ),
    instructions=(
        "Return only hops supported by relationship, ontology, or Purview lineage data. "
        "Call out missing lineage rather than completing paths from assumptions."
    ),
)

EXECUTIVE_QA_SPEC = FabricAgentSpec(
    name="Executive Q&A Agent",
    goal="Answer business questions across regulatory impact, score, remediation, exposure, evidence, and lineage.",
    required_sources=(
        "RegImpactLH",
        "RegImpactSM_V1",
        "RegImpact_Ontology",
        "v_compliance_score_story",
        "v_product_regulatory_exposure",
        "v_gap_blast_radius",
        "v_remediation_priority",
    ),
    output_contract=(
        '{"question":string,"answer":string,"citations":[{"source":string,'
        '"reference_type":"table|view|field|measure|relationship|entity",'
        '"name":string,"value":string}],"tool_evidence":[...],'
        '"confidence":"low|medium|high"}'
    ),
    instructions=(
        "Answer directly for risk, compliance, CDO, and executive stakeholders. "
        "Use score measures for aggregate score questions, Lakehouse detail for names, "
        "and ontology/relationships for multi-hop lineage or blast radius. Refuse unsupported claims."
    ),
)


class FabricAgentHarness:
    """Harness for Fabric-backed application agents."""

    def __init__(self, fabric_client: FabricDataAgentClient):
        self.fabric_client = fabric_client

    def map_controls(self, request: ControlMappingRequest) -> ControlMappingResponse:
        """Run the Fabric-backed Control Mapper framing."""
        request.validate()
        fabric_response = self._ask(CONTROL_MAPPER_SPEC, request.__dict__)
        payload = _json_answer(fabric_response)
        mappings = [
            ControlMapping(
                obligation_id=_required_str(item, "obligation_id"),
                control_id=_required_str(item, "control_id"),
                capability_id=_required_str(item, "capability_id"),
                rationale=_required_str(item, "rationale"),
                confidence=_required_confidence(item),
                source_refs=_source_refs(item.get("source_refs", [])),
            )
            for item in _required_list(payload, "mappings")
        ]
        return _validated(
            ControlMappingResponse(
                mappings=mappings,
                tool_evidence=fabric_response.tool_evidence,
            )
        )

    def analyze_gaps(self, request: GapAnalysisRequest) -> GapAnalysisResponse:
        """Run the Fabric-backed Gap Analyst framing."""
        request.validate()
        fabric_response = self._ask(GAP_ANALYST_SPEC, request.__dict__)
        payload = _json_answer(fabric_response)
        findings = [
            GapAnalysisFinding(
                gap_id=_required_str(item, "gap_id"),
                obligation_id=_required_str(item, "obligation_id"),
                control_id=_required_str(item, "control_id"),
                severity=_required_severity(item),
                maturity_shortfall=_required_int(item, "maturity_shortfall"),
                rationale=_required_str(item, "rationale"),
                source_refs=_source_refs(item.get("source_refs", [])),
            )
            for item in _required_list(payload, "findings")
        ]
        return _validated(
            GapAnalysisResponse(
                findings=findings,
                tool_evidence=fabric_response.tool_evidence,
            )
        )

    def plan_remediation(self, request: RemediationRequest) -> RemediationResponse:
        """Run the Fabric-backed Remediation Planner framing."""
        request.validate()
        fabric_response = self._ask(REMEDIATION_PLANNER_SPEC, request.__dict__)
        payload = _json_answer(fabric_response)
        actions = [
            RemediationPlanItem(
                remediation_id=_required_str(item, "remediation_id"),
                gap_id=_required_str(item, "gap_id"),
                owner_unit_id=_required_str(item, "owner_unit_id"),
                priority=_required_priority(item),
                estimated_effort_days=_required_int(item, "estimated_effort_days"),
                action=_required_str(item, "action"),
                source_refs=_source_refs(item.get("source_refs", [])),
            )
            for item in _required_list(payload, "actions")
        ]
        return _validated(
            RemediationResponse(
                actions=actions,
                tool_evidence=fabric_response.tool_evidence,
            )
        )

    def narrate_score(self, request: ScoreNarrationRequest) -> ScoreNarrationResponse:
        """Run the Fabric-backed Compliance Score Narrator framing."""
        request.validate()
        fabric_response = self._ask(SCORE_NARRATOR_SPEC, request.__dict__)
        payload = _json_answer(fabric_response)
        return _validated(
            ScoreNarrationResponse(
                change_id=_required_str(payload, "change_id"),
                narrative=_required_str(payload, "narrative"),
                as_is=_required_float(payload, "as_is"),
                post_change=_required_float(payload, "post_change"),
                post_remediation=_required_float(payload, "post_remediation"),
                source_refs=_source_refs(payload.get("source_refs", [])),
                tool_evidence=fabric_response.tool_evidence,
            )
        )

    def trace_lineage(self, request: LineageRequest) -> LineageResponse:
        """Run the Fabric-backed Audit & Lineage framing."""
        request.validate()
        fabric_response = self._ask(LINEAGE_AGENT_SPEC, request.__dict__)
        payload = _json_answer(fabric_response)
        hops = [
            LineageHop(
                source_id=_required_str(item, "source_id"),
                relationship=_required_str(item, "relationship"),
                target_id=_required_str(item, "target_id"),
                source_refs=_source_refs(item.get("source_refs", [])),
            )
            for item in _required_list(payload, "hops")
        ]
        return _validated(
            LineageResponse(
                entity_id=_required_str(payload, "entity_id"),
                hops=hops,
                tool_evidence=fabric_response.tool_evidence,
            )
        )

    def _ask(
        self,
        spec: FabricAgentSpec,
        request_payload: dict[str, Any],
    ) -> FabricQuestionResponse:
        prompt = spec.build_prompt(request_payload)
        return self.fabric_client.ask(prompt)


def _json_answer(fabric_response: FabricQuestionResponse) -> dict[str, Any]:
    """Parse the Fabric answer field as the agent-specific JSON payload."""
    try:
        payload = json.loads(fabric_response.answer)
    except json.JSONDecodeError as exc:
        raise FabricAgentHarnessError("Fabric agent answer was not valid JSON") from exc
    if not isinstance(payload, dict):
        raise FabricAgentHarnessError("Fabric agent answer JSON must be an object")
    return payload


def _validated(response):
    """Validate a typed response and return it."""
    try:
        response.validate()
    except ValidationError as exc:
        raise FabricAgentHarnessError("Fabric agent response failed validation") from exc
    return response


def _required_list(payload: dict[str, Any], key: str) -> list[Any]:
    value = payload.get(key)
    if not isinstance(value, list):
        raise FabricAgentHarnessError(f"{key} must be a list")
    return value


def _required_str(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise FabricAgentHarnessError(f"{key} is required")
    return value


def _required_int(payload: dict[str, Any], key: str) -> int:
    value = payload.get(key)
    if type(value) is not int:
        raise FabricAgentHarnessError(f"{key} must be an integer")
    return value


def _required_float(payload: dict[str, Any], key: str) -> float:
    value = payload.get(key)
    if type(value) not in {int, float}:
        raise FabricAgentHarnessError(f"{key} must be numeric")
    return float(value)


def _required_confidence(payload: dict[str, Any]):
    value = _required_str(payload, "confidence")
    if value not in {"low", "medium", "high"}:
        raise FabricAgentHarnessError("confidence must be low, medium, or high")
    return value


def _required_severity(payload: dict[str, Any]):
    value = _required_str(payload, "severity")
    if value not in {"Critical", "High", "Medium", "Low", "None"}:
        raise FabricAgentHarnessError("severity is invalid")
    return value


def _required_priority(payload: dict[str, Any]):
    value = _required_str(payload, "priority")
    if value not in {"Critical", "High", "Medium", "Low"}:
        raise FabricAgentHarnessError("priority is invalid")
    return value


def _source_refs(payload: Any) -> list[SourceReference]:
    if not isinstance(payload, list):
        raise FabricAgentHarnessError("source_refs must be a list")
    return [_source_ref(item) for item in payload]


def _source_ref(payload: Any) -> SourceReference:
    if not isinstance(payload, dict):
        raise FabricAgentHarnessError("source_ref must be an object")
    reference_type = _required_str(payload, "reference_type")
    if reference_type not in {"entity", "field", "measure", "relationship", "table", "view"}:
        raise FabricAgentHarnessError("source_ref reference_type is invalid")
    return SourceReference(
        source=_required_str(payload, "source"),
        reference_type=reference_type,
        name=_required_str(payload, "name"),
        value=str(payload.get("value", "")),
    )
