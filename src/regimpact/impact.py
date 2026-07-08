"""Impact & gap analysis engine.

Given a regulatory change, this engine traverses the correlated estate to:

  1. find the obligations the change introduces
  2. compare required controls' as-is maturity against the obligation's target
  3. raise a typed Gap wherever there is a shortfall (or a missing control)
  4. trace the blast radius (systems, processes, products, data domains)
  5. propose a RemediationAction to close each gap

Results are written back onto the Estate (gaps, remediations and their edges)
so they flow through the same export + graph + Data Agent surfaces.
"""
from __future__ import annotations

from .models import (
    Control,
    Criticality,
    Edge,
    Estate,
    Evidence,
    EvidenceStatus,
    Gap,
    GapSeverity,
    Obligation,
    RelType,
    RemediationAction,
)

_WEAK_EVIDENCE = {EvidenceStatus.MISSING, EvidenceStatus.PARTIAL, EvidenceStatus.STALE}

_CRIT_ORDER = ["Low", "Medium", "High", "Critical"]
_SEV_BY_SHORTFALL = {0: GapSeverity.NONE, 1: GapSeverity.LOW, 2: GapSeverity.MEDIUM, 3: GapSeverity.HIGH}


def _escalate(sev: GapSeverity, criticality: Criticality) -> GapSeverity:
    """Bump severity up when the underlying obligation is highly critical."""
    order = [GapSeverity.NONE, GapSeverity.LOW, GapSeverity.MEDIUM, GapSeverity.HIGH, GapSeverity.CRITICAL]
    idx = order.index(sev)
    if criticality == Criticality.CRITICAL and sev not in (GapSeverity.NONE,):
        idx = min(len(order) - 1, idx + 1)
    return order[idx]


def _severity(shortfall: int, criticality: Criticality) -> GapSeverity:
    base = _SEV_BY_SHORTFALL.get(shortfall, GapSeverity.CRITICAL)
    return _escalate(base, criticality)


def _gap_priority(sev: GapSeverity) -> Criticality:
    return {
        GapSeverity.LOW: Criticality.LOW,
        GapSeverity.MEDIUM: Criticality.MEDIUM,
        GapSeverity.HIGH: Criticality.HIGH,
        GapSeverity.CRITICAL: Criticality.CRITICAL,
    }.get(sev, Criticality.LOW)


def _action_type(control_family: str, missing: bool) -> str:
    fam = control_family.lower()
    if "data" in fam or "lineage" in fam or "aggregation" in fam:
        return "Data"
    if "privacy" in fam or "conduct" in fam or "governance" in fam:
        return "Policy"
    return "Build" if missing else "Enhance"


class ImpactEngine:
    def __init__(self, estate: Estate):
        self.est = estate
        self._controls: dict[str, Control] = {c.id: c for c in estate.controls}
        self._evidence: dict[str, list[Evidence]] = {}
        for ev in estate.evidence:
            self._evidence.setdefault(ev.control_id, []).append(ev)
        self._index_edges()

    def _index_edges(self) -> None:
        self.obl_controls: dict[str, list[str]] = {}
        self.obl_data: dict[str, list[str]] = {}
        self.ctl_systems: dict[str, list[str]] = {}
        self.ctl_processes: dict[str, list[str]] = {}
        self.proc_products: dict[str, list[str]] = {}
        for e in self.est.edges:
            if e.rel_type == RelType.OBLIGATION_REQUIRES_CONTROL:
                self.obl_controls.setdefault(e.source_id, []).append(e.target_id)
            elif e.rel_type == RelType.OBLIGATION_CONCERNS_DATA_DOMAIN:
                self.obl_data.setdefault(e.source_id, []).append(e.target_id)
            elif e.rel_type == RelType.CONTROL_IMPLEMENTED_IN_SYSTEM:
                self.ctl_systems.setdefault(e.source_id, []).append(e.target_id)
            elif e.rel_type == RelType.CONTROL_OPERATES_IN_PROCESS:
                self.ctl_processes.setdefault(e.source_id, []).append(e.target_id)
            elif e.rel_type == RelType.PROCESS_SUPPORTS_PRODUCT:
                self.proc_products.setdefault(e.source_id, []).append(e.target_id)

    # ------------------------------------------------------------------ #
    def analyze_change(self, change_id: str) -> dict:
        obligations = [o for o in self.est.obligations if o.change_id == change_id]
        if not obligations:
            raise ValueError(f"No obligations found for change '{change_id}'.")

        new_gaps: list[Gap] = []
        new_actions: list[RemediationAction] = []
        for ob in obligations:
            new_gaps.extend(self._gaps_for_obligation(ob))

        for gap in new_gaps:
            new_actions.append(self._remediation_for_gap(gap))

        # write back onto the estate (replace any prior run for this change)
        self.est.gaps = [g for g in self.est.gaps if g.change_id != change_id] + new_gaps
        keep_actions = {g.id for g in self.est.gaps if g.change_id != change_id}
        self.est.remediations = [r for r in self.est.remediations if r.gap_id in keep_actions] + new_actions
        self._sync_edges()
        return self._summary(change_id, new_gaps, new_actions)

    def analyze_all(self) -> dict:
        summaries = {c.id: self.analyze_change(c.id) for c in self.est.changes}
        return summaries

    # ------------------------------------------------------------------ #
    def _gaps_for_obligation(self, ob: Obligation) -> list[Gap]:
        target = int(ob.target_maturity)
        required = self.obl_controls.get(ob.id, [])
        data_domains = self.obl_data.get(ob.id, [])
        gaps: list[Gap] = []

        if not required:
            sev = _severity(target, ob.criticality)
            gaps.append(
                Gap(
                    id=f"GAP-{ob.id}-NONE",
                    obligation_id=ob.id,
                    change_id=ob.change_id,
                    control_id=None,
                    severity=GapSeverity.CRITICAL,
                    maturity_shortfall=target,
                    rationale=f"No control exists to satisfy obligation '{ob.id}'.",
                    affected_data_domain_ids=data_domains,
                )
            )
            return gaps

        for cid in required:
            ctl = self._controls[cid]
            shortfall = max(0, target - int(ctl.maturity))
            weak_evidence = [e for e in self._evidence.get(cid, []) if e.status in _WEAK_EVIDENCE]
            if shortfall == 0:
                # Control is mature enough, but if it can't be evidenced the
                # obligation still isn't demonstrably met (Evidence layer).
                if weak_evidence:
                    sev = _escalate(GapSeverity.LOW, ob.criticality)
                    statuses = ", ".join(sorted({e.status.value for e in weak_evidence}))
                    gaps.append(
                        Gap(
                            id=f"GAP-{ob.id}-{cid}-EV",
                            obligation_id=ob.id,
                            change_id=ob.change_id,
                            control_id=cid,
                            severity=sev,
                            maturity_shortfall=0,
                            rationale=(
                                f"Control '{ctl.name}' meets target maturity {target}, but "
                                f"{len(weak_evidence)} evidence artefact(s) are {statuses}; "
                                f"compliance cannot be demonstrated to the regulator."
                            ),
                            affected_system_ids=self.ctl_systems.get(cid, []),
                            affected_process_ids=self.ctl_processes.get(cid, []),
                            affected_product_ids=sorted({
                                p for pr in self.ctl_processes.get(cid, [])
                                for p in self.proc_products.get(pr, [])
                            }),
                            affected_data_domain_ids=data_domains,
                        )
                    )
                continue
            sev = _severity(shortfall, ob.criticality)
            systems = self.ctl_systems.get(cid, [])
            processes = self.ctl_processes.get(cid, [])
            products = sorted({p for pr in processes for p in self.proc_products.get(pr, [])})
            evidence_note = ""
            if weak_evidence:
                statuses = ", ".join(sorted({e.status.value for e in weak_evidence}))
                evidence_note = f" Supporting evidence is also {statuses}."
            gaps.append(
                Gap(
                    id=f"GAP-{ob.id}-{cid}",
                    obligation_id=ob.id,
                    change_id=ob.change_id,
                    control_id=cid,
                    severity=sev,
                    maturity_shortfall=shortfall,
                    rationale=(
                        f"Control '{ctl.name}' is at maturity {int(ctl.maturity)} "
                        f"({ctl.status.value}) vs target {target} required by the obligation."
                        + evidence_note
                    ),
                    affected_system_ids=systems,
                    affected_process_ids=processes,
                    affected_product_ids=products,
                    affected_data_domain_ids=data_domains,
                )
            )
        return gaps

    def _remediation_for_gap(self, gap: Gap) -> RemediationAction:
        missing = gap.control_id is None
        evidence_gap = gap.id.endswith("-EV")
        if missing:
            family = "new control"
            name = "the missing capability"
            owner = "BU-RISK"
        else:
            ctl = self._controls[gap.control_id]
            family = ctl.control_family
            name = ctl.name
            owner = ctl.owner_unit_id
            # a control that exists only on paper (maturity 0) still needs building
            missing = int(ctl.maturity) == 0
        if evidence_gap:
            effort = 15 + (10 if gap.severity in (GapSeverity.HIGH, GapSeverity.CRITICAL) else 0)
            return RemediationAction(
                id=f"REM-{gap.id}",
                gap_id=gap.id,
                action=f"Produce and automate compliance evidence for {name} (lineage, logs or attestations).",
                action_type="Assurance",
                estimated_effort_days=effort,
                priority=_gap_priority(gap.severity),
                target_unit_id=owner,
            )
        atype = _action_type(family, missing)
        verb = {"Build": "Implement", "Enhance": "Uplift", "Data": "Remediate data for",
                "Policy": "Strengthen governance of"}.get(atype, "Address")
        effort = 20 + gap.maturity_shortfall * 25 + (15 if gap.severity == GapSeverity.CRITICAL else 0)
        return RemediationAction(
            id=f"REM-{gap.id}",
            gap_id=gap.id,
            action=f"{verb} {name} to close a maturity shortfall of {gap.maturity_shortfall}.",
            action_type=atype,
            estimated_effort_days=effort,
            priority=_gap_priority(gap.severity),
            target_unit_id=owner,
        )

    def _sync_edges(self) -> None:
        # drop previously-emitted gap/remediation edges, then re-add
        self.est.edges = [
            e for e in self.est.edges
            if e.rel_type not in (RelType.GAP_FOR_OBLIGATION, RelType.GAP_AGAINST_CONTROL, RelType.REMEDIATION_RESOLVES_GAP)
        ]
        for g in self.est.gaps:
            self.est.edges.append(
                Edge(source_id=g.id, source_type="Gap", target_id=g.obligation_id,
                     target_type="Obligation", rel_type=RelType.GAP_FOR_OBLIGATION)
            )
            if g.control_id:
                self.est.edges.append(
                    Edge(source_id=g.id, source_type="Gap", target_id=g.control_id,
                         target_type="Control", rel_type=RelType.GAP_AGAINST_CONTROL)
                )
        for r in self.est.remediations:
            self.est.edges.append(
                Edge(source_id=r.id, source_type="RemediationAction", target_id=r.gap_id,
                     target_type="Gap", rel_type=RelType.REMEDIATION_RESOLVES_GAP)
            )

    def _summary(self, change_id: str, gaps: list[Gap], actions: list[RemediationAction]) -> dict:
        change = next(c for c in self.est.changes if c.id == change_id)
        sev_counts: dict[str, int] = {}
        for g in gaps:
            sev_counts[g.severity.value] = sev_counts.get(g.severity.value, 0) + 1
        affected_products = sorted({p for g in gaps for p in g.affected_product_ids})
        affected_systems = sorted({s for g in gaps for s in g.affected_system_ids})
        affected_processes = sorted({pr for g in gaps for pr in g.affected_process_ids})
        return {
            "change_id": change_id,
            "change_title": change.title,
            "regulation_id": change.regulation_id,
            "effective_date": change.effective_date.isoformat(),
            "criticality": change.criticality.value,
            "obligations": len([o for o in self.est.obligations if o.change_id == change_id]),
            "gaps": len(gaps),
            "gaps_by_severity": sev_counts,
            "total_effort_days": sum(a.estimated_effort_days for a in actions),
            "affected_products": affected_products,
            "affected_systems": affected_systems,
            "affected_processes": affected_processes,
        }


def analyze_change(estate: Estate, change_id: str) -> dict:
    return ImpactEngine(estate).analyze_change(change_id)


def analyze_all(estate: Estate) -> dict:
    return ImpactEngine(estate).analyze_all()
