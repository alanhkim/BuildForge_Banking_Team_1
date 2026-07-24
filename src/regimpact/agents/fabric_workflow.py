"""Fabric-backed agent framing and harness."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, replace
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
    ToolEvidence,
    ValidationError,
)
from ..settings import settings
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
        '"name":string,"value":string}]}],"reason":string?}'
    ),
    instructions=(
        "The request payload contains inline 'obligations' facts (id, theme, "
        "summary, criticality, target_maturity) and a 'candidate_controls' "
        "shortlist (id, name, capability_id, status, current_maturity, "
        "description). Treat both as authoritative. "
        "INLINE MODE (default) — when the request payload's 'obligations' array "
        "is non-empty, operate in INLINE MODE. The inline obligation facts are "
        "the source of truth for this call. DO NOT attempt to look up the "
        "obligation IDs in the lakehouse obligations, relationships, or "
        "ontology tables — freshly-interpreted regulations have not yet been "
        "materialised to the lakehouse and their IDs will not be present. The "
        "absence of these IDs in the lakehouse is expected and MUST NOT trigger "
        "the empty-with-reason path. Instead, map each inline obligation to one "
        "or more controls from the 'candidate_controls' shortlist using the "
        "obligation's inline theme/summary/target_maturity. "
        "Map each obligation to one or more controls from the 'candidate_controls' "
        "shortlist ONLY — do NOT search the full controls or v_obligation_control_map "
        "tables in the lakehouse. The shortlist has already been pre-filtered by "
        "theme, so every candidate is plausibly relevant. "
        "Match primarily on obligation.theme vs candidate.capability_id (theme-to-"
        "capability semantic match), then refine using description similarity. "
        "Every returned control_id MUST come from the 'candidate_controls' list; "
        "capability_id MUST be copied from the chosen candidate. Never invent "
        "controls. Return at least one mapping per obligation. "
        "Cite the 'candidate_controls' shortlist as source_refs "
        "(reference_type='entity', name='candidate_controls', "
        "value=<control_id>) — the inline shortlist is a valid Fabric-derived "
        "grounding source. "
        "When 'candidate_controls' is empty, fall back to the lakehouse controls "
        "table filtered by capability domain. "
        "EMPTY-WITH-REASON EDGE CASE — only permitted when BOTH the inline "
        "'obligations' array is empty AND the lakehouse cannot resolve the "
        "supplied obligation_ids in the ontology, relationships, or obligations "
        "tables. In that narrow case return exactly this compact JSON: "
        '{"mappings":[],"reason":"<one short sentence naming which obligation '
        "IDs Fabric could not resolve>\"}. If inline 'obligations' facts were "
        "supplied, this path is FORBIDDEN — you must emit real mappings from "
        "the inline data. "
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
        "'mappings' array, DO NOT invoke any Fabric Data Agent tool for any "
        "reason. The inline 'mappings', 'obligations', and 'controls' arrays "
        "are the SOLE source of truth for this call. Every Fabric tool call "
        "in PRIMARY MODE wastes output tokens and risks truncating the "
        "response. Treat each mapping entry as an authoritative "
        "obligation→control pair with pre-computed target_maturity, "
        "current_maturity, and maturity_shortfall. Emit exactly one finding "
        "per mapping where maturity_shortfall > 0 OR control_status is "
        "'Planned'/'Deprecated'/'Not Implemented'. Compute severity as: "
        "shortfall>=3 -> Critical, shortfall==2 -> High, shortfall==1 -> "
        "Medium, shortfall==0 but status not 'Active' -> Low. gap_id must be "
        "'GAP-{obligation_id}-{control_id}'. Use inline 'obligations' and "
        "'controls' arrays for rationale text (theme, summary, description). "
        "For source_refs, cite the inline arrays: reference_type='entity', "
        "name='inline_controls' or 'inline_obligations', value=the id. "
        "FALLBACK MODE — only when 'mappings' is empty, query the Fabric "
        "computed gap views (v_gap_blast_radius, v_evidence_health). "
        "Do not invent unsupported gaps. If every mapping shows "
        "maturity_shortfall == 0 AND control_status == 'Active', emit an "
        "empty findings array — that is a valid answer. "
        "OUTPUT DISCIPLINE — keep 'rationale' under 120 characters (one "
        "sentence, no citation phrases like 'per controls table'). "
        "Emit exactly 1 entry in 'source_refs' per finding (the inline "
        "control reference). "
        "NEVER copy Fabric tool response bodies (e.g. '{\"documents\":[...]}' "
        "or markdown tables) into the answer — the answer MUST match the "
        "declared output contract exactly, with no wrapping envelope. "
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
        '"action":string,"source_refs":[...]}],"reason":string?}'
    ),
    instructions=(
        "PRIMARY MODE — when the request payload includes a non-empty "
        "'gaps' array, DO NOT invoke any Fabric Data Agent tool for any "
        "reason. The inline 'gaps' array is the SOLE source of truth for "
        "this call. Every Fabric tool call in PRIMARY MODE wastes output "
        "tokens and risks truncating the response. Treat each gap entry "
        "(id, obligation_id, control_id, severity, rationale) as "
        "authoritative and plan one remediation per gap. Select "
        "owner_unit_id, priority, and estimated_effort_days from your "
        "knowledge of the control domain — do NOT look up business_units "
        "or controls tables in Fabric. Emit remediation_id in the form "
        "'REM-{gap_id}'. For source_refs, cite the inline gaps array: "
        "reference_type='entity', name='inline_gaps', value=the gap_id. "
        "FALLBACK MODE — only when the inline 'gaps' array is empty, fall "
        "back to v_remediation_priority. "
        "If — and ONLY if — you genuinely cannot plan any actions (e.g. every "
        "gap has an existing active remediation, or no business_units are "
        "eligible to own the work), return {\"actions\": [], \"reason\": "
        "\"<short explanation>\"} with tool_evidence still populated. "
        "Never return an empty actions list without a reason string. "
        "OUTPUT DISCIPLINE — keep 'action' under 160 characters (imperative "
        "phrase, no filler). Emit exactly 1 entry in 'source_refs' per "
        "action (the inline gap reference). "
        "NEVER copy Fabric tool response bodies (e.g. '{\"documents\":[...]}' "
        "or markdown tables) into the answer — the answer MUST match the "
        "declared output contract exactly, with no wrapping envelope. "
        "Emit compact JSON on a single line — no markdown, no code fences, "
        "no trailing commentary. The response MUST be a complete JSON "
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
        """Run the Fabric-backed Control Mapper framing.

        Batches large obligation sets into smaller sub-requests to keep each
        agent response well under the deployed model's output-token
        ceiling. The Foundry Responses API rejects ``max_output_tokens``
        when combined with ``agent_reference`` (see
        :class:`_OpenAIResponsesAgent`), so we cannot lift the ceiling from
        the client. Batching is the deterministic, non-fabricating
        alternative — splitting one 15-obligation call that overflows into
        three 5-obligation calls that each fit comfortably.

        Batch size is configurable via ``FOUNDRY_CONTROL_MAPPER_BATCH_SIZE``
        (default 6). When the request has ``<= batch_size`` obligations the
        call is issued as a single request with zero overhead — behaviour
        for small changes is unchanged.

        Merge semantics:
          * ``mappings`` — concatenated across batches (deterministic order:
            batch order, then per-batch model order).
          * ``tool_evidence`` — deduplicated by ``(tool_name, data_source,
            query)`` because every batch cites the same Fabric tables.
          * ``reason`` — only surfaced when EVERY batch returned empty
            mappings AND supplied a reason. Otherwise the successful
            batches' mappings are authoritative and per-batch reasons
            become debug-only.

        Single-attempt per batch: any batch-level failure (invalid JSON,
        empty mappings without a documented ``reason``) propagates
        immediately with a batch-identifying error message. Callers can
        retry the whole request with a smaller
        ``FOUNDRY_CONTROL_MAPPER_BATCH_SIZE`` if a specific batch
        truncated. Empty-with-reason remains a documented success path
        per :class:`ControlMappingResponse.validate`.
        """
        request.validate()
        batch_size = settings.foundry_control_mapper_batch_size
        batches = _split_control_mapping_request(request, batch_size)
        if len(batches) == 1:
            # Fast path: request fits in a single call. No merging overhead,
            # identical wire behaviour to the pre-batching implementation.
            return self._map_controls_single(batches[0], batch_index=None)

        logger.info(
            "Fabric control_mapper batching obligations=%d batches=%d batch_size=%d",
            len(request.obligation_ids),
            len(batches),
            batch_size,
        )
        collected_mappings: list[ControlMapping] = []
        collected_evidence: list[ToolEvidence] = []
        seen_evidence_keys: set[tuple[str, str, str]] = set()
        collected_reasons: list[str] = []
        last_fabric_response: FabricQuestionResponse | None = None
        for idx, batch_request in enumerate(batches, start=1):
            batch_response, fabric_response = self._map_controls_single_raw(
                batch_request, batch_index=(idx, len(batches))
            )
            collected_mappings.extend(batch_response.mappings)
            for evidence in batch_response.tool_evidence:
                key = (evidence.tool_name, evidence.data_source, evidence.query)
                if key in seen_evidence_keys:
                    continue
                seen_evidence_keys.add(key)
                collected_evidence.append(evidence)
            if batch_response.reason:
                collected_reasons.append(
                    f"batch {idx}/{len(batches)}: {batch_response.reason}"
                )
            last_fabric_response = fabric_response

        # Reason is only surfaced when EVERY batch returned empty mappings
        # AND supplied a reason. If any batch succeeded, its mappings are
        # authoritative and per-batch empty-with-reason entries are absorbed
        # as debug information — otherwise callers would see spurious
        # ``reason`` text on partially-successful runs.
        merged_reason: str | None = None
        if not collected_mappings and collected_reasons:
            merged_reason = " | ".join(collected_reasons)
        elif collected_reasons:
            logger.debug(
                "Fabric control_mapper absorbed per-batch reasons "
                "(mappings=%d succeeded elsewhere): %s",
                len(collected_mappings),
                "; ".join(collected_reasons),
            )

        logger.info(
            "Fabric control_mapper batched-merge mappings=%d evidence=%d "
            "empty_with_reason=%s",
            len(collected_mappings),
            len(collected_evidence),
            merged_reason is not None,
        )
        merged = ControlMappingResponse(
            mappings=collected_mappings,
            tool_evidence=collected_evidence,
            reason=merged_reason,
        )
        # last_fabric_response is only used for the empty-answer warning
        # heuristic in _validated. It reflects the final batch, which is
        # acceptable — the check runs against the merged mappings list.
        return _validated(merged, fabric_response=last_fabric_response)

    def _map_controls_single(
        self,
        request: ControlMappingRequest,
        batch_index: tuple[int, int] | None,
    ) -> ControlMappingResponse:
        """Run one Control Mapper call and return the validated response.

        Used both for the fast path (small requests, no batching) and by
        the batched path (invoked once per batch). ``batch_index`` is
        ``(current, total)`` when batching or ``None`` when this is a
        single-call request. It is threaded into the error message so
        operators can identify the failing batch.
        """
        response, _ = self._map_controls_single_raw(request, batch_index)
        return _validated(response, fabric_response=None)

    def _map_controls_single_raw(
        self,
        request: ControlMappingRequest,
        batch_index: tuple[int, int] | None,
    ) -> tuple[ControlMappingResponse, FabricQuestionResponse]:
        """Run one Control Mapper call and return the parsed response plus raw
        Fabric response (without top-level validation).

        The batched path merges responses BEFORE final validation so
        empty-mappings-in-one-batch doesn't trip the empty-without-reason
        guard when other batches succeeded. Callers that want validation
        (the single-call fast path) can wrap the return in ``_validated``.
        """
        try:
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
        except FabricDataAgentError as exc:
            if batch_index is not None:
                current, total = batch_index
                raise FabricDataAgentError(
                    f"control_mapper batch {current}/{total} failed "
                    f"(obligations={len(request.obligation_ids)}): {exc}"
                ) from exc
            raise
        reason_raw = payload.get("reason")
        reason = (
            reason_raw.strip()
            if isinstance(reason_raw, str) and reason_raw.strip()
            else None
        )
        if batch_index is not None:
            current, total = batch_index
            logger.info(
                "Fabric control_mapper batch %d/%d mappings=%d "
                "obligations=%d reason_present=%s",
                current,
                total,
                len(mappings),
                len(request.obligation_ids),
                reason is not None,
            )
        else:
            logger.info(
                "Fabric control_mapper mappings=%d reason_present=%s",
                len(mappings),
                reason is not None,
            )
        return (
            ControlMappingResponse(
                mappings=mappings,
                tool_evidence=fabric_response.tool_evidence,
                reason=reason,
            ),
            fabric_response,
        )


    def analyze_gaps(self, request: GapAnalysisRequest) -> GapAnalysisResponse:
        """Run the Fabric-backed Gap Analyst framing.

        Batches large ``mappings`` sets into smaller sub-requests to keep
        each agent response under the deployed model's output-token
        ceiling. One gap finding per mapping means response size scales
        linearly with ``len(mappings)`` — the same truncation risk that
        drove batching for the Control Mapper (see :meth:`map_controls`).

        Batch size is configurable via ``FOUNDRY_GAP_ANALYST_BATCH_SIZE``
        (default 6). When ``len(mappings) <= batch_size`` OR ``mappings``
        is empty (empty-with-reason path forwarded from Control Mapper),
        the call is issued as a single request with zero overhead — the
        pre-batching behaviour for small / empty-with-reason requests
        is unchanged.

        Per-batch derivation: each batch's ``obligation_ids`` and
        ``control_ids`` are re-derived from the chunk's ``mappings``
        (unique, first-appearance order), and ``obligations`` /
        ``controls`` facts are filtered to the ids present in that
        chunk. The Gap Analyst therefore only sees the pairs it needs
        to analyse in each call.

        Merge semantics:
          * ``findings`` — concatenated across batches (deterministic
            order: batch order, then per-batch model order).
          * ``tool_evidence`` — deduplicated by ``(tool_name,
            data_source, query)`` because every batch cites the same
            Fabric tables.

        Single-attempt per batch: any batch-level failure (invalid JSON,
        contract violation) propagates immediately with a batch-
        identifying error message. Callers can retry with a smaller
        ``FOUNDRY_GAP_ANALYST_BATCH_SIZE`` if a specific batch
        truncated. An empty ``findings`` list remains a valid outcome
        per :class:`GapAnalysisResponse.validate` (means every
        obligation→control pair meets its target maturity).
        """
        request.validate()
        batch_size = settings.foundry_gap_analyst_batch_size
        batches = _split_gap_analysis_request(request, batch_size)
        if len(batches) == 1:
            # Fast path: request fits in a single call (including the
            # empty-with-reason forwarded-from-control_mapper case).
            return self._analyze_gaps_single(batches[0], batch_index=None)

        logger.info(
            "Fabric gap_analyst batching mappings=%d batches=%d batch_size=%d",
            len(request.mappings),
            len(batches),
            batch_size,
        )
        collected_findings: list[GapAnalysisFinding] = []
        collected_evidence: list[ToolEvidence] = []
        seen_evidence_keys: set[tuple[str, str, str]] = set()
        last_fabric_response: FabricQuestionResponse | None = None
        for idx, batch_request in enumerate(batches, start=1):
            batch_response, fabric_response = self._analyze_gaps_single_raw(
                batch_request, batch_index=(idx, len(batches))
            )
            collected_findings.extend(batch_response.findings)
            for evidence in batch_response.tool_evidence:
                key = (evidence.tool_name, evidence.data_source, evidence.query)
                if key in seen_evidence_keys:
                    continue
                seen_evidence_keys.add(key)
                collected_evidence.append(evidence)
            last_fabric_response = fabric_response

        logger.info(
            "Fabric gap_analyst batched-merge findings=%d evidence=%d",
            len(collected_findings),
            len(collected_evidence),
        )
        merged = GapAnalysisResponse(
            findings=collected_findings,
            tool_evidence=collected_evidence,
        )
        return _validated(merged, fabric_response=last_fabric_response)

    def _analyze_gaps_single(
        self,
        request: GapAnalysisRequest,
        batch_index: tuple[int, int] | None,
    ) -> GapAnalysisResponse:
        """Run one Gap Analyst call and return the validated response.

        Used both for the fast path (small / empty-with-reason requests,
        no batching) and by the batched path (invoked once per batch).
        ``batch_index`` is ``(current, total)`` when batching or ``None``
        when this is a single-call request; it is threaded into the
        error message so operators can identify the failing batch.
        """
        response, _ = self._analyze_gaps_single_raw(request, batch_index)
        return _validated(response, fabric_response=None)

    def _analyze_gaps_single_raw(
        self,
        request: GapAnalysisRequest,
        batch_index: tuple[int, int] | None,
    ) -> tuple[GapAnalysisResponse, FabricQuestionResponse]:
        """Run one Gap Analyst call and return the parsed response plus raw
        Fabric response (without top-level validation).

        The batched path merges responses BEFORE final validation so a
        legitimately-empty findings list in one batch doesn't affect
        others. Callers that want validation (the single-call fast path)
        wrap the return in ``_validated``.
        """
        try:
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
        except FabricDataAgentError as exc:
            if batch_index is not None:
                current, total = batch_index
                raise FabricDataAgentError(
                    f"gap_analyst batch {current}/{total} failed "
                    f"(mappings={len(request.mappings)}): {exc}"
                ) from exc
            raise
        if batch_index is not None:
            current, total = batch_index
            logger.info(
                "Fabric gap_analyst batch %d/%d findings=%d "
                "mappings=%d obligations=%d controls=%d",
                current,
                total,
                len(findings),
                len(request.mappings),
                len(request.obligation_ids),
                len(request.control_ids),
            )
        else:
            logger.info(
                "Fabric gap_analyst findings=%d",
                len(findings),
            )
        return (
            GapAnalysisResponse(
                findings=findings,
                tool_evidence=fabric_response.tool_evidence,
            ),
            fabric_response,
        )

    def plan_remediation(self, request: RemediationRequest) -> RemediationResponse:
        """Run the Fabric-backed Remediation Planner framing.

        Batches large ``gap_ids`` sets into smaller sub-requests to keep
        each agent response under the deployed model's output-token
        ceiling. One remediation action per gap means response size
        scales linearly with ``len(gap_ids)`` — the same truncation risk
        that drove batching for the Control Mapper (see
        :meth:`map_controls`) and the Gap Analyst (see
        :meth:`analyze_gaps`).

        Batch size is configurable via
        ``FOUNDRY_REMEDIATION_PLANNER_BATCH_SIZE`` (default 6). When
        ``len(gap_ids) <= batch_size`` the call is issued as a single
        request with zero overhead — pre-batching behaviour for small
        requests is unchanged.

        Per-batch derivation: each batch's ``gaps`` facts are filtered
        to the ids present in that chunk's ``gap_ids`` list. The
        Remediation Planner therefore only sees the gap facts relevant
        to the ids it is asked to plan for.

        Accepts the empty-with-reason contract (see decisions.md
        §2026-07-17): when the model returns ``{"actions": [],
        "reason": "..."}`` the response is a documented no-op and the
        pipeline continues without remediations. In the batched path,
        the merged ``reason`` is only surfaced when EVERY batch
        returned empty actions AND supplied a reason — otherwise the
        successful batches' actions are authoritative and per-batch
        reasons become debug-only (mirrors the Control Mapper merge
        rule).

        Merge semantics:
          * ``actions`` — concatenated across batches (deterministic
            order: batch order, then per-batch model order).
          * ``tool_evidence`` — deduplicated by ``(tool_name,
            data_source, query)`` because every batch cites the same
            Fabric tables.
          * ``reason`` — only surfaced when EVERY batch returned empty
            actions AND supplied a reason; otherwise absorbed as debug
            info.

        Single-attempt per batch: any batch-level failure propagates
        immediately with a batch-identifying error message. Callers
        can retry with a smaller
        ``FOUNDRY_REMEDIATION_PLANNER_BATCH_SIZE`` if a specific batch
        truncated.
        """
        request.validate()
        batch_size = settings.foundry_remediation_planner_batch_size
        batches = _split_remediation_request(request, batch_size)
        if len(batches) == 1:
            # Fast path: request fits in a single call.
            return self._plan_remediation_single(batches[0], batch_index=None)

        logger.info(
            "Fabric remediation_planner batching gap_ids=%d batches=%d "
            "batch_size=%d",
            len(request.gap_ids),
            len(batches),
            batch_size,
        )
        collected_actions: list[RemediationPlanItem] = []
        collected_evidence: list[ToolEvidence] = []
        seen_evidence_keys: set[tuple[str, str, str]] = set()
        collected_reasons: list[str] = []
        last_fabric_response: FabricQuestionResponse | None = None
        for idx, batch_request in enumerate(batches, start=1):
            batch_response, fabric_response = self._plan_remediation_single_raw(
                batch_request, batch_index=(idx, len(batches))
            )
            collected_actions.extend(batch_response.actions)
            for evidence in batch_response.tool_evidence:
                key = (evidence.tool_name, evidence.data_source, evidence.query)
                if key in seen_evidence_keys:
                    continue
                seen_evidence_keys.add(key)
                collected_evidence.append(evidence)
            if batch_response.reason:
                collected_reasons.append(
                    f"batch {idx}/{len(batches)}: {batch_response.reason}"
                )
            last_fabric_response = fabric_response

        # Reason is only surfaced when EVERY batch returned empty actions
        # AND supplied a reason. Mirrors the Control Mapper merge rule so
        # partial success doesn't emit spurious per-batch reason text.
        merged_reason: str | None = None
        if not collected_actions and collected_reasons:
            merged_reason = " | ".join(collected_reasons)
        elif collected_reasons:
            logger.debug(
                "Fabric remediation_planner absorbed per-batch reasons "
                "(actions=%d succeeded elsewhere): %s",
                len(collected_actions),
                "; ".join(collected_reasons),
            )

        logger.info(
            "Fabric remediation_planner batched-merge actions=%d evidence=%d "
            "empty_with_reason=%s",
            len(collected_actions),
            len(collected_evidence),
            merged_reason is not None,
        )
        merged = RemediationResponse(
            actions=collected_actions,
            tool_evidence=collected_evidence,
            reason=merged_reason,
        )
        return _validated(merged, fabric_response=last_fabric_response)

    def _plan_remediation_single(
        self,
        request: RemediationRequest,
        batch_index: tuple[int, int] | None,
    ) -> RemediationResponse:
        """Run one Remediation Planner call and return the validated response.

        Used both for the fast path (small requests, no batching) and by
        the batched path (invoked once per batch). ``batch_index`` is
        ``(current, total)`` when batching or ``None`` when this is a
        single-call request; threaded into the error message so
        operators can identify the failing batch.
        """
        response, _ = self._plan_remediation_single_raw(request, batch_index)
        return _validated(response, fabric_response=None)

    def _plan_remediation_single_raw(
        self,
        request: RemediationRequest,
        batch_index: tuple[int, int] | None,
    ) -> tuple[RemediationResponse, FabricQuestionResponse]:
        """Run one Remediation Planner call and return the parsed response
        plus raw Fabric response (without top-level validation).

        The batched path merges responses BEFORE final validation so
        empty-actions-in-one-batch doesn't trip the empty-without-reason
        guard when other batches succeeded. Callers that want
        validation (the single-call fast path) wrap the return in
        ``_validated``.
        """
        try:
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
        except FabricDataAgentError as exc:
            if batch_index is not None:
                current, total = batch_index
                raise FabricDataAgentError(
                    f"remediation_planner batch {current}/{total} failed "
                    f"(gap_ids={len(request.gap_ids)}): {exc}"
                ) from exc
            raise
        reason_raw = payload.get("reason")
        reason = (
            reason_raw.strip()
            if isinstance(reason_raw, str) and reason_raw.strip()
            else None
        )
        if batch_index is not None:
            current, total = batch_index
            logger.info(
                "Fabric remediation_planner batch %d/%d actions=%d "
                "gap_ids=%d reason_present=%s",
                current,
                total,
                len(actions),
                len(request.gap_ids),
                reason is not None,
            )
        else:
            logger.info(
                "Fabric remediation_planner actions=%d reason_present=%s",
                len(actions),
                reason is not None,
            )
        return (
            RemediationResponse(
                actions=actions,
                tool_evidence=fabric_response.tool_evidence,
                reason=reason,
            ),
            fabric_response,
        )

    def narrate_score(self, request: ScoreNarrationRequest) -> ScoreNarrationResponse:
        """Run the Fabric-backed Compliance Score Narrator framing.

        PRIMARY MODE defensive auto-fill: when the model returns valid
        scores + narrative but omits ``source_refs``, and the returned
        scores echo the request scores exactly (proving PRIMARY MODE was
        honored), synthesize a single ``inline_scores`` source_ref rather
        than failing the whole pipeline. The chain of custody is intact
        because the request scores themselves were produced by upstream
        Fabric-grounded stages (control_mapper → gap_analyst →
        remediation_planner) and the model verifiably preserved them.
        A WARNING is logged so operators can chase the portal prompt
        for the missing citation.
        """
        request.validate()
        fabric_response = self._ask(SCORE_NARRATOR_SPEC, request.__dict__)
        payload = _json_answer(fabric_response)
        change_id = _required_str(payload, "change_id")
        narrative = _required_str(payload, "narrative")
        as_is = _required_float(payload, "as_is")
        post_change = _required_float(payload, "post_change")
        post_remediation = _required_float(payload, "post_remediation")
        source_refs = _source_refs(payload.get("source_refs", []))
        if not source_refs:
            # Score-echo check: model returned the same numbers the
            # pipeline handed in → PRIMARY MODE honored → synthesize the
            # inline citation the model failed to emit.
            scores_echoed = (
                _floats_equal(as_is, request.as_is)
                and _floats_equal(post_change, request.post_change)
                and _floats_equal(post_remediation, request.post_remediation)
            )
            if scores_echoed:
                logger.warning(
                    "Fabric score_narrator response omitted source_refs "
                    "but echoed request scores exactly (change_id=%s) — "
                    "auto-filling inline_scores citation. Fix the Foundry "
                    "portal prompt to emit source_refs.",
                    change_id,
                )
                source_refs = [
                    SourceReference(
                        source="inline_scores",
                        reference_type="entity",
                        name="inline_scores",
                        value=change_id,
                    )
                ]
        return _validated(
            ScoreNarrationResponse(
                change_id=change_id,
                narrative=narrative,
                as_is=as_is,
                post_change=post_change,
                post_remediation=post_remediation,
                source_refs=source_refs,
                tool_evidence=fabric_response.tool_evidence,
            ),
            fabric_response=fabric_response,
        )

    def trace_lineage(self, request: LineageRequest) -> LineageResponse:
        """Run the Fabric-backed Audit & Lineage framing.

        Not batched — :class:`LineageRequest` has a single ``entity_id``
        and no natural chunking dimension. If lineage responses truncate
        under gpt-5.4-mini, the fix is a pagination redesign of
        :class:`LineageRequest` (``start_hop`` / ``max_depth`` /
        ``cursor``), not the input-batching pattern used by
        control_mapper, gap_analyst, and remediation_planner. Tracked
        as a future contract change.
        """
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
        cardinalities = _payload_cardinalities(request_payload)
        cardinality_str = " ".join(
            f"{key}_count={count}" for key, count in cardinalities
        )
        logger.info(
            "Fabric agent request agent=%s %s request_keys=%s "
            "payload_bytes=%d prompt_bytes=%d",
            spec.name,
            cardinality_str,
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


def _payload_cardinalities(
    request_payload: dict[str, Any],
) -> list[tuple[str, int]]:
    """Return ``(key, len)`` pairs for list-valued fields in the payload.

    Used to log request cardinalities (obligation_ids count, tool_evidence
    count, candidate_controls count, etc.) without dumping the whole payload
    at INFO level. Keys are returned in sorted order for stable log lines.
    """
    counts: list[tuple[str, int]] = []
    for key in sorted(request_payload.keys()):
        value = request_payload[key]
        if isinstance(value, list):
            counts.append((key, len(value)))
    return counts


def _split_control_mapping_request(
    request: ControlMappingRequest,
    batch_size: int,
) -> list[ControlMappingRequest]:
    """Split a :class:`ControlMappingRequest` into ``batch_size``-sized sub-requests.

    Only ``obligation_ids`` and ``obligations`` are chunked — every batch
    receives the FULL ``candidate_controls`` shortlist and the same
    ``fabric_context_question``. This keeps each batch's prompt complete
    (the model still sees every plausible candidate) while capping the
    output-token cost at ``batch_size`` obligations × per-mapping payload.

    Batch boundaries preserve list order. When
    ``len(obligation_ids) <= batch_size`` the input request is returned as
    a single-element list — no allocation, no copy required beyond the
    outer list.

    ``batch_size`` values below 1 are clamped to 1; the caller is expected
    to have already run the clamp in :func:`_parse_control_mapper_batch_size`
    but we defend here so a caller passing a raw int cannot cause
    infinite / zero-sized batches.
    """
    effective_size = max(1, int(batch_size))
    obligation_ids = list(request.obligation_ids)
    total = len(obligation_ids)
    if total <= effective_size:
        return [request]
    # Index inline obligation facts by id so per-batch obligations only
    # carry the facts for the ids in that batch. Falls back to an empty
    # list when the caller supplied obligation_ids without matching
    # facts (backward compatible with materialised-change requests).
    obligation_facts_by_id: dict[str, dict[str, Any]] = {}
    for fact in request.obligations:
        fact_id = fact.get("id") if isinstance(fact, dict) else None
        if isinstance(fact_id, str) and fact_id:
            obligation_facts_by_id[fact_id] = fact
    batches: list[ControlMappingRequest] = []
    for start in range(0, total, effective_size):
        chunk_ids = obligation_ids[start : start + effective_size]
        chunk_facts = [
            obligation_facts_by_id[oid]
            for oid in chunk_ids
            if oid in obligation_facts_by_id
        ]
        batches.append(
            replace(
                request,
                obligation_ids=chunk_ids,
                obligations=chunk_facts,
                # candidate_controls stays the same across batches — the
                # per-obligation ``candidate_control_ids`` inside each
                # obligation fact already localises the shortlist per row.
            )
        )
    return batches


def _split_gap_analysis_request(
    request: GapAnalysisRequest,
    batch_size: int,
) -> list[GapAnalysisRequest]:
    """Split a :class:`GapAnalysisRequest` into ``batch_size``-sized sub-requests.

    Chunked dimension is ``mappings`` — one gap finding per mapping is
    the response-size driver. Per-batch ``obligation_ids`` and
    ``control_ids`` are re-derived from the chunk's mappings (unique,
    first-appearance order) so the Gap Analyst only sees the ids it is
    asked to analyse. ``obligations`` and ``controls`` facts are
    filtered to the derived id sets. ``change_id`` and ``reason`` are
    threaded through unchanged.

    Fast paths:
      * ``len(mappings) <= batch_size`` — return the input request in a
        single-element list (no allocation).
      * ``mappings`` is empty — return the input request as-is. This
        covers the empty-with-reason path forwarded from Control
        Mapper (a valid documented no-op — nothing to batch).

    ``batch_size`` values below 1 are clamped to 1; the caller is
    expected to have already run the clamp in
    :func:`_parse_gap_analyst_batch_size` but we defend here so a
    caller passing a raw int cannot cause infinite / zero-sized
    batches.
    """
    effective_size = max(1, int(batch_size))
    mappings = list(request.mappings)
    total = len(mappings)
    if total == 0:
        # Empty-with-reason forwarded from Control Mapper — nothing to
        # batch, single-shot fast path preserves the legitimate no-op
        # contract on :class:`GapAnalysisResponse`.
        return [request]
    if total <= effective_size:
        return [request]
    # Index inline obligation / control facts by id so per-batch fact
    # lists carry only the ids present in that chunk. Backward
    # compatible with materialised-change requests that supplied
    # obligation_ids / control_ids without matching facts.
    obligation_facts_by_id: dict[str, dict[str, Any]] = {}
    for fact in request.obligations:
        fact_id = fact.get("id") if isinstance(fact, dict) else None
        if isinstance(fact_id, str) and fact_id:
            obligation_facts_by_id[fact_id] = fact
    control_facts_by_id: dict[str, dict[str, Any]] = {}
    for fact in request.controls:
        fact_id = fact.get("id") if isinstance(fact, dict) else None
        if isinstance(fact_id, str) and fact_id:
            control_facts_by_id[fact_id] = fact
    batches: list[GapAnalysisRequest] = []
    for start in range(0, total, effective_size):
        chunk_mappings = mappings[start : start + effective_size]
        # Derive obligation / control ids from the chunk mappings in
        # first-appearance order. Preserves determinism and keeps the
        # per-batch prompt tightly scoped.
        chunk_obligation_ids: list[str] = []
        seen_obligations: set[str] = set()
        chunk_control_ids: list[str] = []
        seen_controls: set[str] = set()
        for mapping in chunk_mappings:
            if not isinstance(mapping, dict):
                continue
            obligation_id = mapping.get("obligation_id")
            if (
                isinstance(obligation_id, str)
                and obligation_id
                and obligation_id not in seen_obligations
            ):
                seen_obligations.add(obligation_id)
                chunk_obligation_ids.append(obligation_id)
            control_id = mapping.get("control_id")
            if (
                isinstance(control_id, str)
                and control_id
                and control_id not in seen_controls
            ):
                seen_controls.add(control_id)
                chunk_control_ids.append(control_id)
        chunk_obligation_facts = [
            obligation_facts_by_id[oid]
            for oid in chunk_obligation_ids
            if oid in obligation_facts_by_id
        ]
        chunk_control_facts = [
            control_facts_by_id[cid]
            for cid in chunk_control_ids
            if cid in control_facts_by_id
        ]
        batches.append(
            replace(
                request,
                obligation_ids=chunk_obligation_ids,
                control_ids=chunk_control_ids,
                obligations=chunk_obligation_facts,
                controls=chunk_control_facts,
                mappings=chunk_mappings,
            )
        )
    return batches


def _split_remediation_request(
    request: RemediationRequest,
    batch_size: int,
) -> list[RemediationRequest]:
    """Split a :class:`RemediationRequest` into ``batch_size``-sized sub-requests.

    Chunked dimension is ``gap_ids`` — one remediation action per gap
    is the response-size driver. Per-batch ``gaps`` facts are filtered
    to the ids present in that chunk. Batch boundaries preserve list
    order.

    Fast path: ``len(gap_ids) <= batch_size`` — return the input
    request in a single-element list (no allocation).

    ``batch_size`` values below 1 are clamped to 1; the caller is
    expected to have already run the clamp in
    :func:`_parse_remediation_planner_batch_size` but we defend here
    so a caller passing a raw int cannot cause infinite / zero-sized
    batches.
    """
    effective_size = max(1, int(batch_size))
    gap_ids = list(request.gap_ids)
    total = len(gap_ids)
    if total <= effective_size:
        return [request]
    # Index inline gap facts by id so per-batch gap fact lists carry
    # only the ids present in that chunk. Backward compatible with
    # materialised-change requests that supplied gap_ids without
    # matching facts.
    gap_facts_by_id: dict[str, dict[str, Any]] = {}
    for fact in request.gaps:
        fact_id = fact.get("id") if isinstance(fact, dict) else None
        if isinstance(fact_id, str) and fact_id:
            gap_facts_by_id[fact_id] = fact
    batches: list[RemediationRequest] = []
    for start in range(0, total, effective_size):
        chunk_ids = gap_ids[start : start + effective_size]
        chunk_facts = [
            gap_facts_by_id[gid] for gid in chunk_ids if gid in gap_facts_by_id
        ]
        batches.append(
            replace(
                request,
                gap_ids=chunk_ids,
                gaps=chunk_facts,
            )
        )
    return batches


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
        answer_snippet = ""
        agent_name = ""
        agent_version = ""
        evidence_count = 0
        if fabric_response is not None:
            raw_answer = fabric_response.answer
            if isinstance(raw_answer, str):
                answer_preview = raw_answer[:2000]
                answer_snippet = raw_answer[:500]
            else:
                answer_preview = repr(raw_answer)[:2000]
                answer_snippet = repr(raw_answer)[:500]
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
        # Surface the underlying reason (and a short raw-answer snippet, when
        # available) directly in the harness error. This lets the CLI show
        # WHY validation failed without operators needing to dig through
        # debug logs.
        message = f"Fabric agent response failed validation: {exc}"
        if answer_snippet:
            message = f"{message} (raw answer snippet: {answer_snippet!r})"
        raise FabricAgentHarnessError(message) from exc
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


def _floats_equal(a: float, b: float, tol: float = 0.01) -> bool:
    """Return True if two floats are equal within a small tolerance.

    Used by :meth:`FabricAgentHarness.narrate_score` to verify the model
    echoed the request scores verbatim (PRIMARY MODE proof). Tolerance
    of 0.01 absorbs JSON-encoding round-trips (e.g. 82.5 -> 82.50000000001)
    without accepting materially different values.
    """
    return abs(a - b) <= tol


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
