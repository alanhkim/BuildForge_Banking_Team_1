"""Fabric-backed agent framing and harness."""
from __future__ import annotations

import json
import logging
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
from .foundry_client import (
    FabricDataAgentClient,
    FabricDataAgentError,
    _extract_json_block,
)

logger = logging.getLogger(__name__)


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
        "The request payload contains inline 'obligations' facts (id, theme, "
        "summary, criticality, target_maturity) and a 'candidate_controls' "
        "shortlist (id, name, capability_id, status, current_maturity, "
        "description). Treat both as authoritative. "
        "Map each obligation to one or more controls from the 'candidate_controls' "
        "shortlist ONLY — do NOT search the full controls or v_obligation_control_map "
        "tables in the lakehouse. The shortlist has already been pre-filtered by "
        "theme, so every candidate is plausibly relevant. "
        "Match primarily on obligation.theme vs candidate.capability_id (theme-to-"
        "capability semantic match), then refine using description similarity. "
        "Every returned control_id MUST come from the 'candidate_controls' list; "
        "capability_id MUST be copied from the chosen candidate. Never invent "
        "controls. Return at least one mapping per obligation. "
        "When 'candidate_controls' is empty, fall back to the lakehouse controls "
        "table filtered by capability domain. "
        "OUTPUT DISCIPLINE — keep 'rationale' under 240 characters (one sentence, "
        "no citations, no restatement of the obligation). Emit compact JSON on a "
        "single line — no markdown, no code fences, no trailing commentary. The "
        "response MUST be a complete JSON object; do not stop mid-string."
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
        "PRIMARY MODE — when the request payload includes a non-empty "
        "'mappings' array, treat each entry as an authoritative "
        "obligation→control pair with pre-computed target_maturity, "
        "current_maturity, and maturity_shortfall. Emit exactly one finding "
        "per mapping where maturity_shortfall > 0 OR control_status is "
        "'Planned'/'Deprecated'/'Not Implemented'. Compute severity as: "
        "shortfall>=3 -> Critical, shortfall==2 -> High, shortfall==1 -> "
        "Medium, shortfall==0 but status not 'Active' -> Low. gap_id must be "
        "'GAP-{obligation_id}-{control_id}'. Use inline 'obligations' and "
        "'controls' arrays for rationale text (theme, summary, description). "
        "Do NOT require these ids to be resolvable in v_gap_blast_radius. "
        "Cite the controls table and evidence sources for grounding. "
        "FALLBACK MODE — only when 'mappings' is empty, query the Fabric "
        "computed gap views (v_gap_blast_radius, v_evidence_health). "
        "Do not invent unsupported gaps. If every mapping shows "
        "maturity_shortfall == 0 AND control_status == 'Active', emit an "
        "empty findings array — that is a valid answer. "
        "OUTPUT DISCIPLINE — keep 'rationale' under 160 characters (one "
        "sentence, no citation phrases like 'per controls table'). "
        "Cap 'source_refs' at 2 entries per finding — one control-table "
        "reference and one evidence reference is sufficient. "
        "Emit compact JSON on a single line — no markdown, no code fences, "
        "no trailing commentary. The response MUST be a complete JSON "
        "object; if you are approaching the output limit, drop the lowest-"
        "severity findings rather than truncate mid-string."
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
        "When the request payload includes inline 'gaps' facts (id, obligation_id, "
        "control_id, severity, rationale), treat those as authoritative and plan "
        "one remediation per gap. Do NOT require the gap_ids to already exist in "
        "the gaps table. Use the controls and business_units tables to select "
        "realistic owner_unit_id, priority, and estimated_effort_days. Cite the "
        "controls/business_units/evidence tables you used. When inline gaps are "
        "absent, fall back to v_remediation_priority. Never invent owners; owner_unit_id "
        "must exist in business_units. Emit remediation_id in the form 'REM-{gap_id}'. "
        "OUTPUT DISCIPLINE — keep 'action' under 200 characters (imperative phrase, "
        "no filler). Emit compact JSON on a single line — no markdown, no code "
        "fences, no trailing commentary. The response MUST be a complete JSON "
        "object; do not stop mid-string."
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
        "PRIMARY MODE — when the request payload includes non-zero "
        "'as_is', 'post_change', or 'post_remediation' values, treat those "
        "as authoritative pre-computed score facts for THIS change and "
        "echo them back verbatim in the response. Do NOT query the "
        "compliance_scores table for scores when the request already "
        "supplies them — a freshly uploaded change has no rows yet and "
        "the lookup would return zero. Use the Fabric gap/remediation "
        "views ONLY to source narrative drivers (which gaps caused the "
        "drop, which remediations recovered it). "
        "FALLBACK MODE — only when all three request scores are 0, query "
        "compliance_scores by change_id for the numeric facts. "
        "Explain why PostChange drops and PostRemediation recovers using "
        "cited gap/remediation drivers. Never invent scores that differ "
        "from the provided facts. "
        "OUTPUT DISCIPLINE — keep 'narrative' under 400 characters. Emit "
        "compact JSON on a single line — no markdown, no code fences."
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
        raw_mappings = _required_list(payload, "mappings")
        _warn_if_empty(fabric_response, "mappings", raw_mappings)
        mappings = [
            ControlMapping(
                obligation_id=_required_str(item, "obligation_id"),
                control_id=_required_str(item, "control_id"),
                capability_id=_required_str(item, "capability_id"),
                rationale=_required_str(item, "rationale"),
                confidence=_required_confidence(item),
                source_refs=_source_refs(item.get("source_refs", [])),
            )
            for item in raw_mappings
        ]
        return _validated(
            ControlMappingResponse(
                mappings=mappings,
                tool_evidence=fabric_response.tool_evidence,
            ),
            fabric_response=fabric_response,
        )

    def analyze_gaps(self, request: GapAnalysisRequest) -> GapAnalysisResponse:
        """Run the Fabric-backed Gap Analyst framing."""
        request.validate()
        fabric_response = self._ask(GAP_ANALYST_SPEC, request.__dict__)
        payload = _json_answer(fabric_response)
        raw_findings = _required_list(payload, "findings")
        _warn_if_empty(fabric_response, "findings", raw_findings)
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
            for item in raw_findings
        ]
        return _validated(
            GapAnalysisResponse(
                findings=findings,
                tool_evidence=fabric_response.tool_evidence,
            ),
            fabric_response=fabric_response,
        )

    def plan_remediation(self, request: RemediationRequest) -> RemediationResponse:
        """Run the Fabric-backed Remediation Planner framing."""
        request.validate()
        fabric_response = self._ask(REMEDIATION_PLANNER_SPEC, request.__dict__)
        payload = _json_answer(fabric_response)
        raw_actions = _required_list(payload, "actions")
        _warn_if_empty(fabric_response, "actions", raw_actions)
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
            for item in raw_actions
        ]
        return _validated(
            RemediationResponse(
                actions=actions,
                tool_evidence=fabric_response.tool_evidence,
            ),
            fabric_response=fabric_response,
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
            ),
            fabric_response=fabric_response,
        )

    def trace_lineage(self, request: LineageRequest) -> LineageResponse:
        """Run the Fabric-backed Audit & Lineage framing."""
        request.validate()
        fabric_response = self._ask(LINEAGE_AGENT_SPEC, request.__dict__)
        payload = _json_answer(fabric_response)
        raw_hops = _required_list(payload, "hops")
        _warn_if_empty(fabric_response, "hops", raw_hops)
        hops = [
            LineageHop(
                source_id=_required_str(item, "source_id"),
                relationship=_required_str(item, "relationship"),
                target_id=_required_str(item, "target_id"),
                source_refs=_source_refs(item.get("source_refs", [])),
            )
            for item in raw_hops
        ]
        return _validated(
            LineageResponse(
                entity_id=_required_str(payload, "entity_id"),
                hops=hops,
                tool_evidence=fabric_response.tool_evidence,
            ),
            fabric_response=fabric_response,
        )

    def _ask(
        self,
        spec: FabricAgentSpec,
        request_payload: dict[str, Any],
    ) -> FabricQuestionResponse:
        prompt = spec.build_prompt(request_payload)
        # Emit the exact agent input so operators can reproduce/inspect the
        # request that produced any given response. INFO level so it appears
        # in normal runs; truncated preview at WARNING level would hide the
        # facts we actually need to audit.
        try:
            payload_json = json.dumps(request_payload, default=str, sort_keys=True)
        except (TypeError, ValueError):
            payload_json = repr(request_payload)
        logger.info(
            "Fabric agent request agent=%s request_keys=%s payload_bytes=%d prompt_bytes=%d",
            spec.name,
            sorted(request_payload.keys()),
            len(payload_json),
            len(prompt),
        )
        logger.debug(
            "Fabric agent request payload agent=%s payload=%s",
            spec.name,
            payload_json,
        )
        logger.debug(
            "Fabric agent request prompt agent=%s prompt=%s",
            spec.name,
            prompt,
        )
        return self.fabric_client.ask(prompt)


def _json_answer(fabric_response: FabricQuestionResponse) -> dict[str, Any]:
    """Parse the Fabric answer field as the agent-specific JSON payload.

    Accepts three answer shapes so this stays tolerant of Fabric agents that
    occasionally strip the envelope or wrap JSON in markdown / prose:

    1. ``dict`` — kept by :func:`_fabric_response_from_payload` when the
       upstream envelope was recovered from an inner-shape payload.
    2. Plain JSON string.
    3. JSON embedded in markdown fences or prose (extracted via
       :func:`_extract_json_block`).
    """
    answer = fabric_response.answer
    if isinstance(answer, dict):
        logger.debug(
            "Fabric answer arrived as dict agent=%s version=%s keys=%s",
            fabric_response.agent_name,
            fabric_response.agent_version,
            sorted(answer.keys()),
        )
        return answer
    if not isinstance(answer, str):
        logger.error(
            "Fabric answer wrong type agent=%s version=%s type=%s",
            fabric_response.agent_name,
            fabric_response.agent_version,
            type(answer).__name__,
        )
        raise FabricAgentHarnessError(
            "Fabric agent answer must be a string or dict"
        )

    text = answer.strip()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as first_exc:
        try:
            extracted = _extract_json_block(text)
            payload = json.loads(extracted)
        except (FabricDataAgentError, json.JSONDecodeError) as exc:
            # Log both ends of the answer so we can distinguish a truncated
            # response (tail cut mid-token) from a well-formed response with
            # trailing garbage. Include the exact decoder position from the
            # first attempt so operators can jump straight to the fault.
            logger.error(
                "Fabric JSON parse failed for agent=%s version=%s "
                "answer_bytes=%d decode_error=%s decode_pos=%d "
                "extract_error=%s answer_head=%r answer_tail=%r",
                fabric_response.agent_name,
                fabric_response.agent_version,
                len(answer),
                first_exc.msg,
                first_exc.pos,
                exc,
                answer[:400],
                answer[-400:] if len(answer) > 400 else "",
            )
            raise FabricAgentHarnessError(
                "Fabric agent answer was not valid JSON"
            ) from exc
    if not isinstance(payload, dict):
        logger.error(
            "Fabric JSON root type invalid for agent=%s version=%s type=%s",
            fabric_response.agent_name,
            fabric_response.agent_version,
            type(payload).__name__,
        )
        raise FabricAgentHarnessError("Fabric agent answer JSON must be an object")
    logger.debug(
        "Fabric JSON parsed for agent=%s version=%s keys=%s",
        fabric_response.agent_name,
        fabric_response.agent_version,
        sorted(payload.keys()),
    )
    return payload


def _validated(response, fabric_response: FabricQuestionResponse | None = None):
    """Validate a typed response and return it. Logs raw Fabric answer on failure."""
    try:
        response.validate()
    except ValidationError as exc:
        answer_preview = ""
        agent_name = ""
        agent_version = ""
        evidence_count = 0
        if fabric_response is not None:
            answer_preview = fabric_response.answer[:2000]
            agent_name = fabric_response.agent_name
            agent_version = fabric_response.agent_version
            evidence_count = len(fabric_response.tool_evidence)
        logger.error(
            "Fabric response validation failed type=%s detail=%s agent=%s version=%s "
            "tool_evidence_count=%d raw_answer=%s",
            type(response).__name__,
            exc,
            agent_name,
            agent_version,
            evidence_count,
            answer_preview,
        )
        raise FabricAgentHarnessError("Fabric agent response failed validation") from exc
    return response


def _warn_if_empty(
    fabric_response: FabricQuestionResponse,
    key: str,
    items: list[Any],
) -> None:
    """Log the raw Fabric answer when the agent returned an empty result list."""
    if items:
        return
    logger.error(
        "Fabric agent returned empty %s list agent=%s version=%s "
        "tool_evidence_count=%d raw_answer=%s",
        key,
        fabric_response.agent_name,
        fabric_response.agent_version,
        len(fabric_response.tool_evidence),
        fabric_response.answer[:2000],
    )


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
