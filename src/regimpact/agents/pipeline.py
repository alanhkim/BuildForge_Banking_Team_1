"""Agent pipeline orchestrator.

Runs the four agents end to end and returns one consolidated read-out:

    Interpreter -> Control Mapper -> Gap Analysis -> Remediation (+ scoring)

Two entry points:
  * `run` (existing change)  — analyse a change already in the estate.
  * `run_text` (new regulation) — interpret raw regulation text, inject it as a
    new change into the digital twin, then analyse it.
"""
from __future__ import annotations

from datetime import date

from ..models import (
    Criticality,
    Edge,
    Estate,
    MaturityLevel,
    Obligation,
    RegulatoryChange,
    Regulation,
    RelType,
)
from ..contracts import InterpretRequest
from ..scoring import score_change
from .control_mapper import ControlMapperAgent
from .gap_analysis import GapAnalysisAgent
from .interpreter import InterpreterAgent
from .remediation import RemediationAgent

_CRIT = {
    "Low": Criticality.LOW,
    "Medium": Criticality.MEDIUM,
    "High": Criticality.HIGH,
    "Critical": Criticality.CRITICAL,
}


class AgentPipeline:
    def __init__(self, estate: Estate):
        self.est = estate

    # ------------------------------------------------------------------ #
    def run(self, change_id: str) -> dict:
        """Analyse a change that already exists in the estate."""
        mapping = ControlMapperAgent(self.est).map_all()
        gaps = GapAnalysisAgent(self.est).run(change_id)
        remediation = RemediationAgent(self.est).run(change_id)
        scores = score_change(self.est, change_id)
        return self._report(change_id, mapping, gaps, remediation, scores)

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
        interpretation = InterpreterAgent().interpret(
            InterpretRequest(
                regulation_id=regulation_id,
                change_id=change_id,
                name=regulation_name,
                title=change_title,
                source_text=text,
            )
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


def run_pipeline(estate: Estate, change_id: str) -> dict:
    return AgentPipeline(estate).run(change_id)
