"""Agent 2 — Control Mapper.

Maps each obligation to the controls (and concerned data domains) that satisfy
it, using the capability-theme ontology. Idempotent: only adds edges that do
not already exist, so it works for both catalog obligations and newly
interpreted ones.
"""
from __future__ import annotations

from ..catalog import load_catalog
from ..models import Edge, Estate, RelType


class ControlMapperAgent:
    name = "Control Mapper"

    def __init__(self, estate: Estate):
        self.est = estate
        cat = load_catalog()
        fam_controls: dict[str, list[str]] = {}
        for fam in cat["control_families"]:
            fam_controls[fam["id"]] = [c["id"] for c in fam["controls"]]
        self.theme_controls = {
            theme: [cid for fam in spec["control_families"] for cid in fam_controls.get(fam, [])]
            for theme, spec in cat["themes"].items()
        }
        self.theme_data = {theme: spec["data_domains"] for theme, spec in cat["themes"].items()}
        self._existing = {
            (e.source_id, e.target_id, e.rel_type)
            for e in estate.edges
        }

    def map_all(self) -> dict:
        mapped = 0
        for ob in self.est.obligations:
            mapped += self._map_obligation(ob)
        return {"obligations_mapped": len(self.est.obligations), "edges_added": mapped}

    def _add(self, src, src_t, tgt, tgt_t, rel) -> int:
        key = (src, tgt, rel)
        if key in self._existing:
            return 0
        self.est.edges.append(Edge(source_id=src, source_type=src_t, target_id=tgt,
                                   target_type=tgt_t, rel_type=rel))
        self._existing.add(key)
        return 1

    def _map_obligation(self, ob) -> int:
        added = 0
        for cid in self.theme_controls.get(ob.theme, []):
            added += self._add(ob.id, "Obligation", cid, "Control", RelType.OBLIGATION_REQUIRES_CONTROL)
        for dd in self.theme_data.get(ob.theme, []):
            added += self._add(ob.id, "Obligation", dd, "DataDomain", RelType.OBLIGATION_CONCERNS_DATA_DOMAIN)
        return added
