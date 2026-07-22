"""Agent pipeline orchestrator.

Runs the four agents end to end and returns one consolidated read-out:

    Interpreter -> Control Mapper -> Gap Analysis -> Remediation (+ scoring)

Two entry points:
  * `run` (existing change)  — analyse a change already in the estate.
  * `run_text` (new regulation) — interpret raw regulation text, inject it as a
    new change into the digital twin, then analyse it.
"""
from __future__ import annotations

import logging
import time
from datetime import date
from typing import Any

from ..models import (
    ComplianceScore,
    ComplianceStatus,
    Criticality,
    Edge,
    Estate,
    Gap,
    GapSeverity,
    MaturityLevel,
    Obligation,
    RegulatoryChange,
    Regulation,
    RelType,
    RemediationAction,
    Scenario,
)
from ..contracts import (
    ControlMappingRequest,
    GapAnalysisRequest,
    InterpretRequest,
    RemediationRequest,
    ScoreNarrationRequest,
)
from ..scoring import score_change as _score_change_local
from ..settings import settings as _settings
from .events import EventCallback, PipelineEvent
from .fabric_control_mapper import FabricControlMapperAgent
from .fabric_gap_analyst import FabricGapAnalystAgent
from .fabric_remediation_planner import FabricRemediationPlannerAgent
from .fabric_score_narrator import FabricScoreNarratorAgent
from .foundry_client import FabricDataAgentError
from .interpreter import InterpreterAgent

logger = logging.getLogger(__name__)

_CRIT = {
    "Low": Criticality.LOW,
    "Medium": Criticality.MEDIUM,
    "High": Criticality.HIGH,
    "Critical": Criticality.CRITICAL,
}

_SEVERITY_MAP = {
    "None": GapSeverity.NONE,
    "Low": GapSeverity.LOW,
    "Medium": GapSeverity.MEDIUM,
    "High": GapSeverity.HIGH,
    "Critical": GapSeverity.CRITICAL,
}


def _severity_from_str(value: str) -> GapSeverity:
    return _SEVERITY_MAP.get(value, GapSeverity.MEDIUM)


def _criticality_from_str(value: str) -> Criticality:
    return _CRIT.get(value, Criticality.MEDIUM)


def _compliance_status_from_score(score: float) -> ComplianceStatus:
    if score >= 80:
        return ComplianceStatus.COMPLIANT
    if score >= 50:
        return ComplianceStatus.PARTIAL
    return ComplianceStatus.NONCOMPLIANT



class FabricPipelineError(FabricDataAgentError):
    """Raised when the Fabric-backed pipeline cannot run due to missing configuration."""


# Obligation theme -> candidate capability IDs. Used to pre-filter the control
# estate before calling the Fabric Control Mapper agent so it does not scan
# every control in the lakehouse. Keeps the prompt small and cuts latency by
# 3-5x on estates with many controls. Themes not listed here fall back to
# considering all controls (safe default).
_THEME_TO_CAPABILITY_IDS: dict[str, tuple[str, ...]] = {
    "AI_GOVERNANCE": ("CAP-AIG", "CAP-TRACE", "CAP-AUD"),
    "MODEL_RISK": ("CAP-MRM", "CAP-AIG"),
    "TRAINING_DATA": ("CAP-TDQ", "CAP-DQ"),
    "TRACEABILITY": ("CAP-TRACE", "CAP-AUD"),
    "AUDITABILITY": ("CAP-AUD",),
    "DATA_LINEAGE": ("CAP-LIN", "CAP-MDM"),
    "DATA_QUALITY": ("CAP-DQ", "CAP-DG"),
    "METADATA": ("CAP-MDM", "CAP-DG"),
    "PRIVACY": ("CAP-PII", "CAP-DG"),
    "RETENTION": ("CAP-RET", "CAP-DG"),
    "ACCESS_CONTROL": ("CAP-AC", "CAP-AUTH"),
    "SCA": ("CAP-AUTH", "CAP-AC"),
    "CYBER": ("CAP-CYBER", "CAP-AC"),
    "ICT_SECURITY": ("CAP-CYBER", "CAP-RES"),
    "ICT_RESILIENCE": ("CAP-RES", "CAP-INC"),
    "INCIDENT_MGMT": ("CAP-INC", "CAP-RES"),
    "THIRD_PARTY_RISK": ("CAP-TPR", "CAP-RES"),
    "KYC_CDD": ("CAP-FC",),
    "TXN_MONITORING": ("CAP-FC",),
    "SAR_REPORTING": ("CAP-FC",),
    "SANCTIONS": ("CAP-SANCT", "CAP-FC"),
    "REG_REPORTING": ("CAP-REP", "CAP-AUD"),
    "CAPITAL_ADEQUACY": ("CAP-CAPITAL", "CAP-REP"),
    "CONDUCT": ("CAP-COND",),
    "LOGGING_MONITORING": ("CAP-AUD", "CAP-TRACE", "CAP-INC"),
}


def _truncate(text: str | None, limit: int) -> str:
    """Trim long text fields to keep Fabric agent prompts small."""
    if not text:
        return ""
    text = str(text)
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


class AgentPipeline:
    def __init__(
        self,
        estate: Estate,
        *,
        on_event: EventCallback | None = None,
    ):
        self.est = estate
        self._on_event = on_event

    # ------------------------------------------------------------------ #
    # Observability — synchronous event emission. Callers pass ``on_event``
    # to render live progress (see ``regimpact.cli._make_streaming_callback``)
    # or capture events in tests. Default ``None`` keeps the hot path a
    # single ``is None`` check per call site.
    # ------------------------------------------------------------------ #
    def _emit(
        self,
        kind: str,
        stage: str,
        *,
        message: str = "",
        tool_name: str | None = None,
        data_source: str | None = None,
        duration_ms: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Emit a :class:`PipelineEvent`. No-op when ``on_event`` is None.

        A subscriber that raises must NEVER crash the pipeline — the whole
        point of observability is to be invisible when it fails. We swallow
        the exception, log it at WARNING, and continue. Renderers that keep
        raising will flood the log, which is the correct signal.
        """
        if self._on_event is None:
            return
        try:
            event = PipelineEvent(
                kind=kind,  # type: ignore[arg-type]
                stage=stage,
                message=message,
                tool_name=tool_name,
                data_source=data_source,
                duration_ms=duration_ms,
                details=details or {},
            )
            self._on_event(event)
        except Exception as exc:  # noqa: BLE001 — observability must not raise
            logger.warning(
                "PipelineEvent callback failed stage=%s kind=%s error=%s",
                stage,
                kind,
                exc,
            )

    # ------------------------------------------------------------------ #
    def run(self, change_id: str) -> dict:
        """Analyse a change using the Fabric-backed agent pipeline.

        Requires Foundry/Fabric configuration: FOUNDRY_PROJECT_ENDPOINT,
        FOUNDRY_EXECUTIVE_QA_AGENT_NAME, FABRIC_WORKSPACE_ID, and
        FABRIC_DATA_AGENT_ID must all be set. Raises FabricPipelineError
        explicitly if any configuration is missing.
        """
        if not _settings.foundry_fabric_enabled:
            raise FabricPipelineError(
                "Foundry/Fabric configuration is required for agent pipeline "
                "execution. Ensure FOUNDRY_PROJECT_ENDPOINT, "
                "FOUNDRY_EXECUTIVE_QA_AGENT_NAME, FABRIC_WORKSPACE_ID, and "
                "FABRIC_DATA_AGENT_ID are set."
            )
        return self._run_fabric(change_id)

    def run_text(
        self,
        text: str,
        *,
        regulation_id: str,
        regulation_name: str,
        change_title: str,
        effective_date: date | None = None,
    ) -> dict:
        """Interpret raw regulation text and inject + analyse it as a change."""
        change_id = f"CHG-{regulation_id.replace('REG-', '')}-UPLOAD"
        # Bracket the InterpreterAgent call with stage_start/stage_end so
        # renderers can show it as the first row. On failure we emit
        # stage_error and re-raise — hard-fail, no partial pipeline.
        self._emit(
            "stage_start",
            "interpreter",
            message="Interpreting regulation text",
        )
        _t0 = time.perf_counter()
        try:
            interpretation = InterpreterAgent().interpret(
                InterpretRequest(
                    regulation_id=regulation_id,
                    change_id=change_id,
                    name=regulation_name,
                    title=change_title,
                    source_text=text,
                )
            )
        except Exception as exc:
            self._emit(
                "stage_error",
                "interpreter",
                message=str(exc),
                duration_ms=int((time.perf_counter() - _t0) * 1000),
            )
            raise
        self._emit(
            "stage_end",
            "interpreter",
            duration_ms=int((time.perf_counter() - _t0) * 1000),
            details={
                "obligations": len(interpretation.obligations),
                "mode": interpretation.mode,
            },
        )
        self._inject(
            interpretation.obligations,
            regulation_id=regulation_id,
            regulation_name=regulation_name,
            change_title=change_title,
            effective_date=effective_date or date(2026, 12, 31),
        )
        report = self.run(change_id)
        report["llm_mode"] = interpretation.mode
        report["interpreted_obligations"] = len(interpretation.obligations)
        return report

    # ------------------------------------------------------------------ #
    def _inject(self, obligations: list[dict], *, regulation_id: str, regulation_name: str,
                change_title: str, effective_date: date) -> str:
        if not any(r.id == regulation_id for r in self.est.regulations):
            self.est.regulations.append(Regulation(
                id=regulation_id, name=regulation_name, short_code=regulation_id.replace("REG-", ""),
                regulator="(uploaded)", jurisdiction="(uploaded)", domain="Regulatory Change",
                description=f"Injected via Regulation Interpreter agent: {regulation_name}.",
            ))

        change_id = f"CHG-{regulation_id.replace('REG-', '')}-UPLOAD"
        self.est.changes = [c for c in self.est.changes if c.id != change_id]
        self.est.obligations = [o for o in self.est.obligations if o.change_id != change_id]
        self.est.changes.append(RegulatoryChange(
            id=change_id, regulation_id=regulation_id, title=change_title,
            reference=f"{regulation_name} (uploaded)",
            summary=f"Interpreted from uploaded text: {len(obligations)} obligations.",
            change_type="New", published_date=date(2026, 6, 25),
            effective_date=effective_date, criticality=Criticality.HIGH,
        ))
        self.est.edges.append(Edge(
            source_id=change_id, source_type="RegulatoryChange", target_id=regulation_id,
            target_type="Regulation", rel_type=RelType.CHANGE_OF_REGULATION))

        for i, ob in enumerate(obligations, start=1):
            ob_id = f"OBL-{change_id}-{i:02d}"
            self.est.obligations.append(Obligation(
                id=ob_id, change_id=change_id, regulation_id=regulation_id,
                statement=ob.summary, article=", ".join(ob.source_refs), theme=ob.theme,
                criticality=_CRIT.get(ob.criticality, Criticality.HIGH),
                target_maturity=MaturityLevel(ob.target_maturity),
            ))
            self.est.edges.append(Edge(
                source_id=change_id, source_type="RegulatoryChange", target_id=ob_id,
                target_type="Obligation", rel_type=RelType.INTRODUCES_OBLIGATION))
        return change_id

    def _run_fabric(self, change_id: str) -> dict:
        """Execute the four-stage Fabric-backed analysis pipeline."""
        change_obligations = [
            ob for ob in self.est.obligations if ob.change_id == change_id
        ]
        obligation_ids = [ob.id for ob in change_obligations]
        # Pre-filter controls by theme so the Fabric agent picks from a small
        # shortlist instead of scanning every control in the lakehouse.
        # Also build a per-obligation candidate control map so each obligation
        # in the request payload carries an explicit list of control IDs that
        # match its theme. Without this, the agent has to infer the mapping
        # from a global candidate list against a global obligation list and
        # (as observed with ControlMapper v4) sometimes returns empty
        # mappings when the inference doesn't lock in. Per-obligation hints
        # remove the ambiguity — the agent still validates against inline
        # facts, but the "which controls apply to which obligation" signal
        # is now explicit rather than derived.
        obligation_candidates: dict[str, list[str]] = {}
        unmapped_themes: set[str] = set()
        for ob in change_obligations:
            cap_ids = _THEME_TO_CAPABILITY_IDS.get(ob.theme, ())
            if not cap_ids:
                unmapped_themes.add(ob.theme)
            matching_ids = [
                c.id for c in self.est.controls
                if c.capability_id in cap_ids
            ]
            obligation_candidates[ob.id] = matching_ids
        # Global capability filter — union across obligations. Kept because
        # the harness prompt still emits a top-level 'candidate_controls'
        # shortlist that the agent uses as the enumerable universe.
        candidate_capability_ids: set[str] = set()
        for cap_ids in (
            _THEME_TO_CAPABILITY_IDS.get(ob.theme, ()) for ob in change_obligations
        ):
            candidate_capability_ids.update(cap_ids)
        if candidate_capability_ids:
            candidate_controls_source = [
                c for c in self.est.controls
                if c.capability_id in candidate_capability_ids
            ]
        else:
            # Every obligation had an unrecognised theme -> fall back to
            # sending all controls so the agent has *something* to match
            # against. This is a last resort; the WARNING below tells
            # operators to add the missing theme(s) to _THEME_TO_CAPABILITY_IDS.
            candidate_controls_source = list(self.est.controls)
            for ob in change_obligations:
                # Every obligation now sees the full estate as its shortlist.
                obligation_candidates[ob.id] = [c.id for c in candidate_controls_source]
        candidate_control_facts = [
            {
                "id": c.id,
                "name": c.name,
                "capability_id": c.capability_id,
                "status": c.status.value if hasattr(c.status, "value") else str(c.status),
                "current_maturity": int(c.maturity),
                "description": _truncate(c.description, 160),
            }
            for c in candidate_controls_source
        ]
        # Build in-context obligation facts so Fabric agents can reason about
        # freshly-interpreted obligations that don't yet exist in the lakehouse.
        # summary is truncated to keep the prompt small (agent matches by theme,
        # not by full statement text). candidate_control_ids carries the
        # per-obligation shortlist derived from the theme -> capability map.
        obligation_facts = [
            {
                "id": ob.id,
                "theme": ob.theme,
                "summary": _truncate(ob.statement, 200),
                "criticality": ob.criticality.value if hasattr(ob.criticality, "value") else str(ob.criticality),
                "target_maturity": int(ob.target_maturity),
                "candidate_control_ids": obligation_candidates.get(ob.id, []),
                "affected_data_domain_ids": [
                    e.target_id for e in self.est.edges
                    if e.source_id == ob.id and e.rel_type == RelType.OBLIGATION_CONCERNS_DATA_DOMAIN
                ],
            }
            for ob in change_obligations
        ]
        # Evidence cardinality — the primary signal for diagnosing empty-
        # mapping failures. Bishop's harness logs the RESPONSE side
        # (mappings/reason after the Fabric call); we log the REQUEST side
        # (what the agent was given to reason about) so the two together
        # answer "was the agent starved of context, or did it ignore what
        # it had?". INFO-level because operators need this on the happy path.
        candidates_per_obligation = [
            len(obligation_candidates.get(ob.id, [])) for ob in change_obligations
        ]
        min_candidates = min(candidates_per_obligation) if candidates_per_obligation else 0
        avg_candidates = (
            sum(candidates_per_obligation) / len(candidates_per_obligation)
            if candidates_per_obligation
            else 0.0
        )
        obligations_with_zero_candidates = [
            ob.id
            for ob, count in zip(change_obligations, candidates_per_obligation)
            if count == 0
        ]
        logger.info(
            "Fabric control_mapper request evidence change_id=%s obligations=%d "
            "candidate_controls=%d candidates_per_obligation_min=%d "
            "candidates_per_obligation_avg=%.1f obligations_with_zero_candidates=%d "
            "unmapped_themes=%s",
            change_id,
            len(obligation_ids),
            len(candidate_control_facts),
            min_candidates,
            avg_candidates,
            len(obligations_with_zero_candidates),
            sorted(unmapped_themes) if unmapped_themes else "[]",
        )
        if obligations_with_zero_candidates:
            logger.warning(
                "Fabric control_mapper obligations with zero candidate controls "
                "change_id=%s count=%d ids=%s unmapped_themes=%s — "
                "add missing themes to _THEME_TO_CAPABILITY_IDS to give the "
                "agent a per-obligation shortlist",
                change_id,
                len(obligations_with_zero_candidates),
                obligations_with_zero_candidates[:10],
                sorted(unmapped_themes) if unmapped_themes else "[]",
            )

        # Stage 1: Control Mapper — ground obligation-to-control mappings in Fabric
        self._emit(
            "stage_start",
            "control_mapper",
            message="Mapping obligations → controls",
            details={
                "obligations": len(obligation_ids),
                "candidate_controls": len(candidate_control_facts),
            },
        )
        _cm_t0 = time.perf_counter()
        try:
            cm_response = FabricControlMapperAgent().map(
                ControlMappingRequest(
                    obligation_ids=obligation_ids,
                    obligations=obligation_facts,
                    candidate_controls=candidate_control_facts,
                    fabric_context_question=(
                        f"Map all obligations for regulatory change {change_id} "
                        "to controls from the provided 'candidate_controls' "
                        "shortlist. Use inline 'obligations' facts as authoritative. "
                        "Each obligation carries a 'candidate_control_ids' array "
                        "listing the shortlist entries whose capability_id matches "
                        "its theme — prefer those IDs first, and fall back to the "
                        "wider 'candidate_controls' list only if none of the "
                        "per-obligation candidates fit. Every obligation MUST "
                        "receive either at least one mapping or a documented "
                        "'reason' entry explaining why no mapping was possible."
                    ),
                )
            )
        except FabricDataAgentError as exc:
            logger.error(
                "Fabric stage failed stage=control_mapper change_id=%s obligations=%d error=%s",
                change_id,
                len(obligation_ids),
                exc,
            )
            self._emit(
                "stage_error",
                "control_mapper",
                message=str(exc),
                duration_ms=int((time.perf_counter() - _cm_t0) * 1000),
            )
            raise FabricPipelineError(
                f"Fabric stage 'control_mapper' failed for {change_id}: {exc}"
            ) from exc
        # One tool_call event per unique tool_evidence entry — surfaces to
        # renderers as "↳ tool: ... · data_source: ..." rows under the stage.
        for _ev in cm_response.tool_evidence:
            self._emit(
                "tool_call",
                "control_mapper",
                tool_name=_ev.tool_name,
                data_source=_ev.data_source,
                message=_truncate(_ev.query, 80),
            )
        # Documented empty-with-reason: control_mapper returned no mappings
        # but supplied a reason (e.g. shortlist exhausted). This is a valid
        # outcome per the ControlMappingResponse contract — log at WARNING
        # and let the pipeline continue. Downstream stages will see empty
        # inputs; that surfaces naturally rather than being masked here.
        if not cm_response.mappings and cm_response.reason:
            logger.warning(
                "Fabric control_mapper returned empty mappings with reason "
                "change_id=%s obligations=%d reason=%s",
                change_id,
                len(obligation_ids),
                cm_response.reason,
            )
        control_ids = list({m.control_id for m in cm_response.mappings})
        logger.debug(
            "Fabric stage complete stage=control_mapper change_id=%s mappings=%d controls=%d",
            change_id,
            len(cm_response.mappings),
            len(control_ids),
        )
        # INFO-level response evidence pairs with the pre-call request-side
        # log above. Together they answer "did the agent honour the inputs
        # it was given?" — a mappings=0 with candidates_per_obligation_avg
        # > 0 in the request log means the agent ignored the shortlist.
        mapped_obligations = {m.obligation_id for m in cm_response.mappings}
        logger.info(
            "Fabric control_mapper response evidence change_id=%s "
            "mappings=%d unique_obligations_mapped=%d/%d "
            "unique_controls=%d tool_evidence=%d reason_present=%s",
            change_id,
            len(cm_response.mappings),
            len(mapped_obligations),
            len(obligation_ids),
            len(control_ids),
            len(cm_response.tool_evidence),
            cm_response.reason is not None,
        )
        # Persist Fabric-authoritative control mappings into the estate
        edges_added = self._persist_control_mappings(cm_response.mappings)
        logger.debug(
            "Fabric writeback stage=control_mapper change_id=%s edges_added=%d",
            change_id,
            edges_added,
        )
        self._emit(
            "stage_end",
            "control_mapper",
            duration_ms=int((time.perf_counter() - _cm_t0) * 1000),
            details={
                "mappings": len(cm_response.mappings),
                "controls": len(control_ids),
                "edges_added": edges_added,
                "tool_calls": len(cm_response.tool_evidence),
                "empty_with_reason": bool(
                    not cm_response.mappings and cm_response.reason
                ),
            },
        )

        # Stage 2: Gap Analyst — identify maturity/evidence gaps via Fabric views
        # Build inline control facts so agent can reason even if lakehouse view
        # doesn't have the freshly-derived mappings. description is truncated
        # to keep the prompt small.
        control_lookup = {c.id: c for c in self.est.controls}
        obligation_lookup = {ob.id: ob for ob in change_obligations}
        control_facts = [
            {
                "id": c.id,
                "name": c.name,
                "capability_id": c.capability_id,
                "status": c.status.value if hasattr(c.status, "value") else str(c.status),
                "current_maturity": int(c.maturity),
                "description": _truncate(c.description, 160),
            }
            for cid in control_ids
            for c in [control_lookup.get(cid)] if c is not None
        ]
        # Authoritative obligation→control pairs from stage 1 with the exact
        # target/current maturity numbers side-by-side. This lets the Gap
        # Analyst compute maturity_shortfall directly without re-joining or
        # querying the lakehouse (fresh mappings aren't there yet).
        mapping_facts = []
        for mapping in cm_response.mappings:
            obligation = obligation_lookup.get(mapping.obligation_id)
            control = control_lookup.get(mapping.control_id)
            if obligation is None or control is None:
                continue
            target = int(obligation.target_maturity)
            current = int(control.maturity)
            mapping_facts.append(
                {
                    "obligation_id": mapping.obligation_id,
                    "control_id": mapping.control_id,
                    "target_maturity": target,
                    "current_maturity": current,
                    "maturity_shortfall": max(0, target - current),
                    "control_status": control.status.value
                    if hasattr(control.status, "value")
                    else str(control.status),
                }
            )
        # Propagate empty-with-reason from control_mapper to the next stage
        # rather than crashing GapAnalysisRequest.validate(). The request
        # will carry the ControlMapper's reason forward so the Gap Analyst
        # can still be invoked with grounded context (obligation facts) and
        # emit findings for uncovered obligations.
        if not cm_response.mappings and cm_response.reason:
            logger.warning(
                "Fabric stage propagating empty control_ids "
                "stage=gap_analyst change_id=%s reason=%s",
                change_id,
                cm_response.reason,
            )
        ga_response = None
        self._emit(
            "stage_start",
            "gap_analyst",
            message="Analysing maturity + evidence gaps",
            details={
                "obligations": len(obligation_ids),
                "controls": len(control_ids),
                "mappings": len(mapping_facts),
            },
        )
        _ga_t0 = time.perf_counter()
        try:
            ga_response = FabricGapAnalystAgent().analyze(
                GapAnalysisRequest(
                    change_id=change_id,
                    obligation_ids=obligation_ids,
                    control_ids=control_ids,
                    obligations=obligation_facts,
                    controls=control_facts,
                    mappings=mapping_facts,
                    # Forward the ControlMapper's documented empty-with-reason
                    # so GapAnalysisRequest.validate() accepts an empty
                    # control_ids list (mirrors ControlMappingResponse's
                    # empty-with-reason contract). If control_mapper returned
                    # normal results, cm_response.reason is None and this is
                    # a no-op on the happy path.
                    reason=cm_response.reason if not cm_response.mappings else None,
                )
            )
        except FabricDataAgentError as exc:
            # Gap Analyst soft-fail: transient Foundry glitches (InternalServerError
            # bursts, retry-loop exhaustion) or malformed agent JSON must not
            # tank the whole pipeline. Downgrade to WARNING and continue with
            # zero findings. Downstream `if gap_ids:` gate skips
            # remediation_planner automatically. score_narrator uses locally
            # pre-computed floats so it still runs. Mirrors the
            # remediation_planner soft-fail pattern.
            # Safety: We deliberately DO NOT call `_persist_gaps([], change_id)`
            # on soft-fail — that would wipe legitimate prior gap data for this
            # change_id. Instead we preserve on-disk state so a rerun can
            # recover cleanly.
            logger.warning(
                "Fabric stage soft-fail stage=gap_analyst change_id=%s "
                "obligations=%d controls=%d error=%s — continuing pipeline "
                "with zero findings (prior gap state preserved)",
                change_id,
                len(obligation_ids),
                len(control_ids),
                exc,
            )
            self._emit(
                "stage_error",
                "gap_analyst",
                message=str(exc),
                duration_ms=int((time.perf_counter() - _ga_t0) * 1000),
                details={"soft_fail": True},
            )
        if ga_response is not None:
            for _ev in ga_response.tool_evidence:
                self._emit(
                    "tool_call",
                    "gap_analyst",
                    tool_name=_ev.tool_name,
                    data_source=_ev.data_source,
                    message=_truncate(_ev.query, 80),
                )
            gap_ids = [f.gap_id for f in ga_response.findings]
            logger.debug(
                "Fabric stage complete stage=gap_analyst change_id=%s findings=%d",
                change_id,
                len(ga_response.findings),
            )
            # If the Analyst returned no gaps, log the mapping facts that justify
            # the "no gap" verdict so an operator can audit the decision without
            # digging into the raw agent response.
            if not ga_response.findings and mapping_facts:
                shortfall_summary = ", ".join(
                    f"{m['obligation_id']}->{m['control_id']}"
                    f"(target={m['target_maturity']},current={m['current_maturity']},"
                    f"shortfall={m['maturity_shortfall']},status={m['control_status']})"
                    for m in mapping_facts
                )
                logger.info(
                    "Fabric gap_analyst returned no findings change_id=%s pairs=%d justification=%s",
                    change_id,
                    len(mapping_facts),
                    shortfall_summary,
                )
            # Persist Fabric-authoritative gaps (replace any prior gaps for this change)
            persisted_gaps = self._persist_gaps(ga_response.findings, change_id)
            logger.debug(
                "Fabric writeback stage=gap_analyst change_id=%s gaps_persisted=%d",
                change_id,
                len(persisted_gaps),
            )
            self._emit(
                "stage_end",
                "gap_analyst",
                duration_ms=int((time.perf_counter() - _ga_t0) * 1000),
                details={
                    "findings": len(ga_response.findings),
                    "persisted_gaps": len(persisted_gaps),
                    "tool_calls": len(ga_response.tool_evidence),
                },
            )
        else:
            gap_ids = []
            persisted_gaps = []

        # Stage 3: Remediation Planner — prioritised owner-assigned actions from Fabric
        rp_actions: list = []
        total_effort: int = 0
        rp_response = None
        if gap_ids:
            self._emit(
                "stage_start",
                "remediation_planner",
                message="Planning remediation actions",
                details={"gaps": len(gap_ids)},
            )
            _rp_t0 = time.perf_counter()
            # Inline gap facts for freshly-derived gaps not yet in the lakehouse.
            # rationale is truncated to keep the prompt small.
            gap_facts = [
                {
                    "id": g.id,
                    "obligation_id": g.obligation_id,
                    "control_id": g.control_id or "",
                    "severity": g.severity.value if hasattr(g.severity, "value") else str(g.severity),
                    "maturity_shortfall": int(g.maturity_shortfall),
                    "rationale": _truncate(g.rationale, 240),
                }
                for g in persisted_gaps
            ]
            try:
                rp_response = FabricRemediationPlannerAgent().plan(
                    RemediationRequest(gap_ids=gap_ids, gaps=gap_facts)
                )
            except FabricDataAgentError as exc:
                # Remediation planner is a "nice to have" narrative stage — a
                # failure here (empty actions without reason, transient
                # Foundry glitch, malformed JSON) must NOT tank the entire
                # pipeline. Downgrade to WARNING + continue with zero
                # actions. The interpret/control/gap stages already produced
                # authoritative artefacts; score_narrator uses locally
                # pre-computed floats and is independent. See
                # decisions.md §2026-07-17 (empty-with-reason contract) —
                # this is the pipeline-level partner to that harness-level
                # tolerance.
                logger.warning(
                    "Fabric stage soft-fail stage=remediation_planner "
                    "change_id=%s gaps=%d error=%s — continuing pipeline "
                    "with zero remediation actions",
                    change_id,
                    len(gap_ids),
                    exc,
                )
                self._emit(
                    "stage_error",
                    "remediation_planner",
                    message=str(exc),
                    duration_ms=int((time.perf_counter() - _rp_t0) * 1000),
                    details={"soft_fail": True},
                )
                rp_response = None
            if rp_response is not None:
                for _ev in rp_response.tool_evidence:
                    self._emit(
                        "tool_call",
                        "remediation_planner",
                        tool_name=_ev.tool_name,
                        data_source=_ev.data_source,
                        message=_truncate(_ev.query, 80),
                    )
                rp_actions = list(rp_response.actions)
                total_effort = sum(a.estimated_effort_days for a in rp_actions)
                if not rp_actions and rp_response.reason:
                    logger.info(
                        "Fabric stage complete stage=remediation_planner "
                        "change_id=%s actions=0 reason=%s",
                        change_id,
                        rp_response.reason,
                    )
                else:
                    logger.debug(
                        "Fabric stage complete stage=remediation_planner change_id=%s actions=%d total_effort_days=%d",
                        change_id,
                        len(rp_actions),
                        total_effort,
                    )
            # Persist Fabric-authoritative remediation actions (replace prior for this change).
            # When rp_actions is empty (either soft-fail or empty-with-reason),
            # this clears any stale remediations for this change's gaps.
            persisted_actions = self._persist_remediations(rp_actions, persisted_gaps)
            logger.debug(
                "Fabric writeback stage=remediation_planner change_id=%s actions_persisted=%d",
                change_id,
                len(persisted_actions),
            )
            # Only emit stage_end on the happy path. stage_error already
            # closed the row on soft-fail (rp_response is None then).
            if rp_response is not None:
                self._emit(
                    "stage_end",
                    "remediation_planner",
                    duration_ms=int((time.perf_counter() - _rp_t0) * 1000),
                    details={
                        "actions": len(rp_actions),
                        "total_effort_days": total_effort,
                        "persisted_actions": len(persisted_actions),
                        "tool_calls": len(rp_response.tool_evidence),
                    },
                )
        else:
            logger.debug(
                "Fabric stage skipped stage=remediation_planner change_id=%s reason=no_gap_ids",
                change_id,
            )
            # Clear any prior remediations for this change's gaps
            self._persist_remediations([], persisted_gaps)
            # Emit skipped stage bracket so the renderer can show a row
            # rather than silently omit the stage. Start + end back-to-back
            # keeps the event stream honest for capturing callbacks.
            self._emit(
                "stage_start",
                "remediation_planner",
                message="Skipped — no gaps to remediate",
            )
            self._emit(
                "stage_end",
                "remediation_planner",
                duration_ms=0,
                details={"skipped": True, "reason": "no_gap_ids"},
            )

        # Stage 4: Score Narrator — Fabric-grounded score movement explanation.
        # Compute local scores from the freshly-persisted gaps/remediations
        # FIRST, then hand them to the Narrator as authoritative input. The
        # Fabric compliance_scores table has no rows for a change that was
        # uploaded seconds ago — asking the agent to derive scores from an
        # empty table returns 0.0/0.0/0.0. Feeding pre-computed facts keeps
        # the agent in its true role (narrator, not calculator) while the
        # scoring math stays deterministic and repo-owned.
        precomputed = self._compute_local_score_facts(change_id)
        self._emit(
            "stage_start",
            "score_narrator",
            message="Narrating compliance score movement",
            details={
                "as_is": precomputed["as_is"],
                "post_change": precomputed["post_change"],
                "post_remediation": precomputed["post_remediation"],
            },
        )
        _sn_t0 = time.perf_counter()
        try:
            sn_response = FabricScoreNarratorAgent().narrate(
                ScoreNarrationRequest(
                    change_id=change_id,
                    as_is=precomputed["as_is"],
                    post_change=precomputed["post_change"],
                    post_remediation=precomputed["post_remediation"],
                )
            )
        except FabricDataAgentError as exc:
            logger.error(
                "Fabric stage failed stage=score_narrator change_id=%s error=%s",
                change_id,
                exc,
            )
            self._emit(
                "stage_error",
                "score_narrator",
                message=str(exc),
                duration_ms=int((time.perf_counter() - _sn_t0) * 1000),
            )
            raise FabricPipelineError(
                f"Fabric stage 'score_narrator' failed for {change_id}: {exc}"
            ) from exc
        for _ev in sn_response.tool_evidence:
            self._emit(
                "tool_call",
                "score_narrator",
                tool_name=_ev.tool_name,
                data_source=_ev.data_source,
                message=_truncate(_ev.query, 80),
            )
        logger.debug(
            "Fabric stage complete stage=score_narrator change_id=%s "
            "as_is=%.2f post_change=%.2f post_remediation=%.2f "
            "(precomputed as_is=%.2f post_change=%.2f post_remediation=%.2f)",
            change_id,
            sn_response.as_is,
            sn_response.post_change,
            sn_response.post_remediation,
            precomputed["as_is"],
            precomputed["post_change"],
            precomputed["post_remediation"],
        )
        # Detect if the narrator ignored our pre-computed facts (drift > 0.5%).
        # Non-fatal — we still trust the agent — but worth logging so we can
        # audit whether the SCORE_NARRATOR_SPEC discipline is holding.
        for name, agent_val, pre_val in (
            ("as_is", sn_response.as_is, precomputed["as_is"]),
            ("post_change", sn_response.post_change, precomputed["post_change"]),
            ("post_remediation", sn_response.post_remediation, precomputed["post_remediation"]),
        ):
            if abs(agent_val - pre_val) > 0.5:
                logger.warning(
                    "Score Narrator drift stage=score_narrator change_id=%s "
                    "score=%s agent=%.2f precomputed=%.2f",
                    change_id,
                    name,
                    agent_val,
                    pre_val,
                )

        # Persist Fabric-authoritative Overall/BANK scores (replace prior for this change)
        self._persist_fabric_scores(sn_response, change_id)

        # Local scoring supplies ONLY derived fields Fabric doesn't return
        # (regulation_compliance snapshot, weakest_capabilities ranking).
        # We compute these on a shallow copy so it does not overwrite the
        # authoritative Fabric scores we just persisted.
        derived = self._derived_local_fields(change_id)

        self._emit(
            "stage_end",
            "score_narrator",
            duration_ms=int((time.perf_counter() - _sn_t0) * 1000),
            details={
                "as_is": sn_response.as_is,
                "post_change": sn_response.post_change,
                "post_remediation": sn_response.post_remediation,
                "tool_calls": len(sn_response.tool_evidence),
            },
        )

        return self._fabric_report(
            change_id,
            obligation_count=len(obligation_ids),
            cm_response=cm_response,
            gap_count=len(ga_response.findings) if ga_response is not None else 0,
            rp_actions=rp_actions,
            total_effort=total_effort,
            sn_response=sn_response,
            derived=derived,
        )

    def _fabric_report(
        self,
        change_id: str,
        *,
        obligation_count: int,
        cm_response,
        gap_count: int,
        rp_actions: list,
        total_effort: int,
        sn_response,
        derived: dict,
    ) -> dict:
        actions = [
            {
                "action": a.action,
                "type": "remediation",
                "priority": a.priority,
                "effort_days": a.estimated_effort_days,
                "owner": a.owner_unit_id,
            }
            for a in rp_actions
        ]
        scores = {
            "change_id": change_id,
            "as_is": sn_response.as_is,
            "post_change": sn_response.post_change,
            "post_remediation": sn_response.post_remediation,
            "score_drop": round(sn_response.as_is - sn_response.post_change, 1),
            "score_recovered": round(
                sn_response.post_remediation - sn_response.post_change, 1
            ),
            "regulation_compliance": derived["regulation_compliance"],
            "weakest_capabilities": derived["weakest_capabilities"],
        }
        return {
            "change_id": change_id,
            "llm_mode": "fabric-agentic",
            "agents": [
                {
                    "agent": "Regulation Interpreter",
                    "result": f"{obligation_count} obligations",
                },
                {
                    "agent": "Control Mapper",
                    "result": f"{len(cm_response.mappings)} mappings",
                },
                {
                    "agent": "Gap Analysis",
                    "result": f"{gap_count} gaps, {total_effort} days",
                },
                {
                    "agent": "Remediation",
                    "result": f"{len(actions)} actions",
                },
            ],
            "gaps": {
                "change_id": change_id,
                "obligations": obligation_count,
                "gaps": gap_count,
                "total_effort_days": total_effort,
            },
            "remediation": {
                "change_id": change_id,
                "actions": actions,
                "total_effort_days": total_effort,
                "narrative": sn_response.narrative,
            },
            "scores": scores,
        }

    def _report(self, change_id, mapping, gaps, remediation, scores) -> dict:
        return {
            "change_id": change_id,
            "llm_mode": "validated-assessment",
            "agents": [
                {"agent": "Regulation Interpreter", "result": f"{gaps['obligations']} obligations"},
                {"agent": "Control Mapper", "result": f"{mapping['edges_added']} new mappings"},
                {"agent": "Gap Analysis", "result": f"{gaps['gaps']} gaps, {gaps['total_effort_days']} days"},
                {"agent": "Remediation", "result": f"{len(remediation['actions'])} actions"},
            ],
            "gaps": gaps,
            "remediation": remediation,
            "scores": scores,
        }

    # ------------------------------------------------------------------ #
    # Fabric writeback helpers — persist agent outputs into the Estate so
    # downstream exports (CSV, GraphML, markdown) reflect Fabric's judgment
    # rather than re-derived local Python analysis.
    # ------------------------------------------------------------------ #
    def _persist_control_mappings(self, mappings: list) -> int:
        """Add OBLIGATION_REQUIRES_CONTROL edges from Fabric mappings.

        Dedupes against edges already in the estate. Returns count added.
        """
        existing = {
            (e.source_id, e.target_id, e.rel_type)
            for e in self.est.edges
        }
        added = 0
        for m in mappings:
            key = (m.obligation_id, m.control_id, RelType.OBLIGATION_REQUIRES_CONTROL)
            if key in existing:
                continue
            self.est.edges.append(
                Edge(
                    source_id=m.obligation_id,
                    source_type="Obligation",
                    target_id=m.control_id,
                    target_type="Control",
                    rel_type=RelType.OBLIGATION_REQUIRES_CONTROL,
                )
            )
            existing.add(key)
            added += 1
        return added

    def _persist_gaps(self, findings: list, change_id: str) -> list[Gap]:
        """Replace prior gaps for this change with Fabric-derived findings.

        Derives blast-radius (systems/processes/products/data_domains) from
        structural edges in the estate using Fabric's chosen control_id.
        """
        # Pre-index edges by source control
        ctl_systems: dict[str, list[str]] = {}
        ctl_processes: dict[str, list[str]] = {}
        proc_products: dict[str, list[str]] = {}
        obl_data: dict[str, list[str]] = {}
        for e in self.est.edges:
            if e.rel_type == RelType.CONTROL_IMPLEMENTED_IN_SYSTEM:
                ctl_systems.setdefault(e.source_id, []).append(e.target_id)
            elif e.rel_type == RelType.CONTROL_OPERATES_IN_PROCESS:
                ctl_processes.setdefault(e.source_id, []).append(e.target_id)
            elif e.rel_type == RelType.PROCESS_SUPPORTS_PRODUCT:
                proc_products.setdefault(e.source_id, []).append(e.target_id)
            elif e.rel_type == RelType.OBLIGATION_CONCERNS_DATA_DOMAIN:
                obl_data.setdefault(e.source_id, []).append(e.target_id)

        new_gaps: list[Gap] = []
        for f in findings:
            cid = f.control_id or None
            systems = ctl_systems.get(cid, []) if cid else []
            processes = ctl_processes.get(cid, []) if cid else []
            products = sorted({
                p for pr in processes for p in proc_products.get(pr, [])
            })
            new_gaps.append(
                Gap(
                    id=f.gap_id,
                    obligation_id=f.obligation_id,
                    change_id=change_id,
                    control_id=cid,
                    severity=_severity_from_str(f.severity),
                    maturity_shortfall=int(f.maturity_shortfall),
                    rationale=f.rationale,
                    affected_system_ids=systems,
                    affected_process_ids=processes,
                    affected_product_ids=products,
                    affected_data_domain_ids=obl_data.get(f.obligation_id, []),
                )
            )

        # Replace this change's gaps in the estate
        self.est.gaps = [g for g in self.est.gaps if g.change_id != change_id] + new_gaps

        # Drop stale Gap-related edges whose source gap no longer exists,
        # then re-emit fresh edges for the new gaps.
        valid_gap_ids = {g.id for g in self.est.gaps}
        self.est.edges = [
            e for e in self.est.edges
            if e.rel_type not in (RelType.GAP_FOR_OBLIGATION, RelType.GAP_AGAINST_CONTROL)
            or e.source_id in valid_gap_ids
        ]
        # Also drop the edges for THIS change's new gaps in case of re-run
        new_ids = {g.id for g in new_gaps}
        self.est.edges = [
            e for e in self.est.edges
            if not (
                e.rel_type in (RelType.GAP_FOR_OBLIGATION, RelType.GAP_AGAINST_CONTROL)
                and e.source_id in new_ids
            )
        ]
        for g in new_gaps:
            self.est.edges.append(
                Edge(
                    source_id=g.id, source_type="Gap",
                    target_id=g.obligation_id, target_type="Obligation",
                    rel_type=RelType.GAP_FOR_OBLIGATION,
                )
            )
            if g.control_id:
                self.est.edges.append(
                    Edge(
                        source_id=g.id, source_type="Gap",
                        target_id=g.control_id, target_type="Control",
                        rel_type=RelType.GAP_AGAINST_CONTROL,
                    )
                )
        return new_gaps

    def _persist_remediations(
        self,
        actions: list,
        gaps_for_change: list[Gap],
    ) -> list[RemediationAction]:
        """Replace remediations for the given change's gaps with Fabric actions.

        Fabric doesn't return action_type; default to 'Enhance'.
        """
        gap_ids_for_change = {g.id for g in gaps_for_change}
        new_actions: list[RemediationAction] = [
            RemediationAction(
                id=a.remediation_id,
                gap_id=a.gap_id,
                action=a.action,
                action_type="Enhance",
                estimated_effort_days=int(a.estimated_effort_days),
                priority=_criticality_from_str(a.priority),
                target_unit_id=a.owner_unit_id,
            )
            for a in actions
        ]

        # Replace remediations tied to this change's gaps
        self.est.remediations = [
            r for r in self.est.remediations if r.gap_id not in gap_ids_for_change
        ] + new_actions

        # Reset REMEDIATION_RESOLVES_GAP edges for this change's gaps
        self.est.edges = [
            e for e in self.est.edges
            if not (
                e.rel_type == RelType.REMEDIATION_RESOLVES_GAP
                and e.target_id in gap_ids_for_change
            )
        ]
        for r in new_actions:
            self.est.edges.append(
                Edge(
                    source_id=r.id, source_type="RemediationAction",
                    target_id=r.gap_id, target_type="Gap",
                    rel_type=RelType.REMEDIATION_RESOLVES_GAP,
                )
            )
        return new_actions

    def _persist_fabric_scores(self, sn_response, change_id: str) -> None:
        """Replace Overall/BANK compliance scores for this change with Fabric values."""
        # Drop only the Overall rows for this change that we're about to replace
        self.est.scores = [
            s for s in self.est.scores
            if not (s.change_id == change_id and s.scope_type == "Overall")
        ]
        for scenario, score in (
            (Scenario.AS_IS, sn_response.as_is),
            (Scenario.POST_CHANGE, sn_response.post_change),
            (Scenario.POST_REMEDIATION, sn_response.post_remediation),
        ):
            self.est.scores.append(
                ComplianceScore(
                    scope_type="Overall",
                    scope_id="BANK",
                    scope_name="Enterprise",
                    scenario=scenario,
                    score=float(score),
                    status=_compliance_status_from_score(float(score)),
                    change_id=change_id,
                )
            )

    def _derived_local_fields(self, change_id: str) -> dict:
        """Compute derived score fields Fabric doesn't return.

        Runs local scoring on a snapshot of the estate so it cannot overwrite
        Fabric-persisted scores. Returns only regulation_compliance and
        weakest_capabilities.
        """
        scores_snapshot = list(self.est.scores)
        try:
            local = _score_change_local(self.est, change_id)
        finally:
            # Always restore the Fabric-authoritative scores
            self.est.scores = scores_snapshot
        return {
            "regulation_compliance": local["regulation_compliance"],
            "weakest_capabilities": local["weakest_capabilities"],
        }

    def _compute_local_score_facts(self, change_id: str) -> dict:
        """Compute AS-IS / POST_CHANGE / POST_REMEDIATION score facts for the Narrator.

        The Fabric ``compliance_scores`` table has no rows for a change_id
        that was uploaded seconds ago. Rather than let the Narrator query
        an empty table and return zeroes, we compute the three headline
        scores locally from the freshly-persisted obligations, controls,
        gaps, and remediations, then feed them to the agent as input.

        Runs on a snapshot so it cannot mutate Fabric-persisted scores.
        """
        scores_snapshot = list(self.est.scores)
        try:
            local = _score_change_local(self.est, change_id)
        finally:
            self.est.scores = scores_snapshot
        return {
            "as_is": float(local["as_is"]),
            "post_change": float(local["post_change"]),
            "post_remediation": float(local["post_remediation"]),
        }

    def build_engine_summary(self, change_id: str) -> dict:
        """Build the summary dict expected by export_report from the Fabric-populated estate.

        Reads Fabric-persisted gaps/remediations directly — does NOT recompute.
        Mirrors the shape of ImpactEngine._summary().
        """
        change = next(c for c in self.est.changes if c.id == change_id)
        gaps = [g for g in self.est.gaps if g.change_id == change_id]
        gap_ids = {g.id for g in gaps}
        actions = [r for r in self.est.remediations if r.gap_id in gap_ids]
        sev_counts: dict[str, int] = {}
        for g in gaps:
            sev_counts[g.severity.value] = sev_counts.get(g.severity.value, 0) + 1
        return {
            "change_id": change_id,
            "change_title": change.title,
            "regulation_id": change.regulation_id,
            "effective_date": change.effective_date.isoformat(),
            "criticality": change.criticality.value,
            "obligations": len(
                [o for o in self.est.obligations if o.change_id == change_id]
            ),
            "gaps": len(gaps),
            "gaps_by_severity": sev_counts,
            "total_effort_days": sum(a.estimated_effort_days for a in actions),
            "affected_products": sorted({p for g in gaps for p in g.affected_product_ids}),
            "affected_systems": sorted({s for g in gaps for s in g.affected_system_ids}),
            "affected_processes": sorted({pr for g in gaps for pr in g.affected_process_ids}),
        }


def run_pipeline(estate: Estate, change_id: str) -> dict:
    return AgentPipeline(estate).run(change_id)
