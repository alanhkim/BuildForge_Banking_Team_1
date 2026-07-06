"""Compliance scoring engine.

Produces a board-level compliance score (0-100) for the bank, driven by two
signals that the framework already models:

  * control maturity   (are the controls strong enough?)
  * evidence status    (can we prove they operate? — the Evidence layer)

It then tells the before/after story for an incoming change:

  AsIs            baseline posture (excludes the new change's obligations)
  PostChange      posture once the change's obligations land (score dips)
  PostRemediation posture once the recommended actions are done (score recovers)

Scores are written back onto the Estate (`estate.scores`) so they export and
surface through the Data Agent like everything else.
"""
from __future__ import annotations

from .models import (
    ComplianceScore,
    ComplianceStatus,
    Criticality,
    Estate,
    Evidence,
    EvidenceStatus,
    Obligation,
    RelType,
    Scenario,
)

_CRIT_WEIGHT = {Criticality.LOW: 1, Criticality.MEDIUM: 2, Criticality.HIGH: 3, Criticality.CRITICAL: 4}
_EVIDENCE_WEIGHT = {
    EvidenceStatus.PRESENT: 1.0,
    EvidenceStatus.STALE: 0.6,
    EvidenceStatus.PARTIAL: 0.4,
    EvidenceStatus.MISSING: 0.0,
}


def _status(score: float) -> ComplianceStatus:
    if score >= 80:
        return ComplianceStatus.COMPLIANT
    if score >= 50:
        return ComplianceStatus.PARTIAL
    return ComplianceStatus.NONCOMPLIANT


class ComplianceScorer:
    def __init__(self, estate: Estate):
        self.est = estate
        self.controls = {c.id: c for c in estate.controls}
        self._evidence_by_control: dict[str, list[Evidence]] = {}
        for ev in estate.evidence:
            self._evidence_by_control.setdefault(ev.control_id, []).append(ev)
        self.obl_controls: dict[str, list[str]] = {}
        for e in estate.edges:
            if e.rel_type == RelType.OBLIGATION_REQUIRES_CONTROL:
                self.obl_controls.setdefault(e.source_id, []).append(e.target_id)

    # ------------------------------------------------------------------ #
    def _evidence_factor(self, control_id: str, override_present: bool = False) -> float:
        if override_present:
            return 1.0
        items = self._evidence_by_control.get(control_id, [])
        if not items:
            return 0.6  # neutral when no evidence type is defined
        return sum(_EVIDENCE_WEIGHT[i.status] for i in items) / len(items)

    def _control_compliance(self, control_id: str, target: int, remediated: bool) -> float:
        ctl = self.controls[control_id]
        maturity = target if remediated else int(ctl.maturity)
        maturity_ratio = min(1.0, maturity / target) if target else 1.0
        evidence = self._evidence_factor(control_id, override_present=remediated)
        return 0.7 * maturity_ratio + 0.3 * evidence

    def _obligation_score(self, ob: Obligation, remediated: bool) -> float:
        controls = self.obl_controls.get(ob.id, [])
        if not controls:
            return 0.0
        target = int(ob.target_maturity)
        vals = [self._control_compliance(cid, target, remediated) for cid in controls]
        return 100.0 * sum(vals) / len(vals)

    def _weighted(self, obligations: list[Obligation], remediated_change: str | None = None) -> float:
        if not obligations:
            return 0.0
        num = den = 0.0
        for ob in obligations:
            w = _CRIT_WEIGHT[ob.criticality]
            remediated = remediated_change is not None and ob.change_id == remediated_change
            num += w * self._obligation_score(ob, remediated)
            den += w
        return round(num / den, 1) if den else 0.0

    # ------------------------------------------------------------------ #
    def score_change(self, change_id: str) -> dict:
        change = next(c for c in self.est.changes if c.id == change_id)
        all_obl = self.est.obligations
        change_obl = [o for o in all_obl if o.change_id == change_id]
        baseline_obl = [o for o in all_obl if o.change_id != change_id]

        as_is = self._weighted(baseline_obl)
        post_change = self._weighted(baseline_obl + change_obl)
        post_rem = self._weighted(baseline_obl + change_obl, remediated_change=change_id)
        reg_change_only = self._weighted(change_obl)
        reg_change_rem = self._weighted(change_obl, remediated_change=change_id)

        rows: list[ComplianceScore] = [
            ComplianceScore(scope_type="Overall", scope_id="BANK", scope_name="Enterprise",
                            scenario=Scenario.AS_IS, score=as_is, status=_status(as_is), change_id=change_id),
            ComplianceScore(scope_type="Overall", scope_id="BANK", scope_name="Enterprise",
                            scenario=Scenario.POST_CHANGE, score=post_change, status=_status(post_change), change_id=change_id),
            ComplianceScore(scope_type="Overall", scope_id="BANK", scope_name="Enterprise",
                            scenario=Scenario.POST_REMEDIATION, score=post_rem, status=_status(post_rem), change_id=change_id),
            ComplianceScore(scope_type="Regulation", scope_id=change.regulation_id, scope_name=change.regulation_id,
                            scenario=Scenario.POST_CHANGE, score=reg_change_only, status=_status(reg_change_only), change_id=change_id),
            ComplianceScore(scope_type="Regulation", scope_id=change.regulation_id, scope_name=change.regulation_id,
                            scenario=Scenario.POST_REMEDIATION, score=reg_change_rem, status=_status(reg_change_rem), change_id=change_id),
        ]
        rows.extend(self._capability_rows(change_id))

        # write back (replace prior rows for this change)
        self.est.scores = [s for s in self.est.scores if s.change_id != change_id] + rows
        return {
            "change_id": change_id,
            "as_is": as_is,
            "post_change": post_change,
            "post_remediation": post_rem,
            "score_drop": round(as_is - post_change, 1),
            "score_recovered": round(post_rem - post_change, 1),
            "regulation_compliance": reg_change_only,
            "weakest_capabilities": self._weakest_capabilities(3),
        }

    def _capability_scores(self) -> dict[str, float]:
        cap_scores: dict[str, list[float]] = {}
        for ctl in self.est.controls:
            if not ctl.capability_id:
                continue
            target = 4  # standing maturity bar for capability health
            cap_scores.setdefault(ctl.capability_id, []).append(
                100.0 * self._control_compliance(ctl.id, target, remediated=False)
            )
        return {cap: round(sum(v) / len(v), 1) for cap, v in cap_scores.items() if v}

    def _capability_rows(self, change_id: str) -> list[ComplianceScore]:
        names = {c.id: c.name for c in self.est.capabilities}
        rows = []
        for cap_id, score in self._capability_scores().items():
            rows.append(ComplianceScore(
                scope_type="Capability", scope_id=cap_id, scope_name=names.get(cap_id, cap_id),
                scenario=Scenario.AS_IS, score=score, status=_status(score), change_id=change_id))
        return rows

    def _weakest_capabilities(self, n: int) -> list[dict]:
        names = {c.id: c.name for c in self.est.capabilities}
        ranked = sorted(self._capability_scores().items(), key=lambda kv: kv[1])[:n]
        return [{"capability": names.get(cid, cid), "score": sc} for cid, sc in ranked]


def score_change(estate: Estate, change_id: str) -> dict:
    return ComplianceScorer(estate).score_change(change_id)
