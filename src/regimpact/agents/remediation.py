"""Agent 4 — Remediation.

The deterministic remediation actions are produced by the impact engine
(Agent 3). This wrapper assembles a prioritised remediation plan with a
network-free executive narrative.
"""
from __future__ import annotations

from ..models import Estate


class RemediationAgent:
    name = "Remediation"

    def __init__(self, estate: Estate):
        self.est = estate

    def run(self, change_id: str) -> dict:
        gap_ids = {g.id for g in self.est.gaps if g.change_id == change_id}
        actions = [r for r in self.est.remediations if r.gap_id in gap_ids]
        priority_order = ["Critical", "High", "Medium", "Low"]
        actions.sort(key=lambda a: (priority_order.index(a.priority.value), -a.estimated_effort_days))
        plan = [
            {
                "action": a.action,
                "type": a.action_type,
                "priority": a.priority.value,
                "effort_days": a.estimated_effort_days,
                "owner": a.target_unit_id,
            }
            for a in actions
        ]
        return {
            "change_id": change_id,
            "actions": plan,
            "total_effort_days": sum(a.estimated_effort_days for a in actions),
            "narrative": self._narrative(change_id, plan),
        }

    def _narrative(self, change_id: str, plan: list[dict]) -> str:
        if not plan:
            return "No remediation required."
        top = plan[:3]
        bullets = "; ".join(f"{p['action']} ({p['priority']})" for p in top)
        return (
            f"{len(plan)} remediation actions identified for {change_id}. "
            f"Immediate priorities: {bullets}."
        )
