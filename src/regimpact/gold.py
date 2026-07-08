"""Gold layer: a star-schema projection of the estate for the semantic model.

The Silver/entity tables (`output/tables/`) are graph-shaped and normalised. A
Power BI semantic model and most BI tooling prefer a **dimensional star**: a few
*fact* tables (the measurable events) surrounded by *dimension* tables (the
descriptive context), joined on single-column keys.

This module projects the in-memory :class:`Estate` into that shape and writes it
to ``output/gold/`` as Parquet + CSV. Because it is a deterministic projection of
the already-audited estate, the same data-type uniformity (Rule 3) and
referential integrity (Rule 4) guarantees carry through — `validate_gold()`
re-checks the fact->dimension keys so the star is provably joinable.

Date columns are emitted as real ``datetime64`` (not strings) so the semantic
model types them as ``dateTime``.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from .models import Estate, to_column_name

# --------------------------------------------------------------------------- #
# Column dtype spec per gold table: column -> family
# families: "string" | "int" | "float" | "bool" | "date"
# --------------------------------------------------------------------------- #
GOLD_SCHEMA: dict[str, dict[str, str]] = {
    # dimensions
    "dim_regulation": {
        "id": "string", "name": "string", "short_code": "string",
        "regulator": "string", "jurisdiction": "string", "domain": "string",
        "description": "string",
    },
    "dim_change": {
        "id": "string", "regulation_id": "string", "title": "string",
        "reference": "string", "summary": "string", "change_type": "string",
        "published_date": "date", "effective_date": "date", "criticality": "string",
    },
    "dim_obligation": {
        "id": "string", "change_id": "string", "regulation_id": "string",
        "statement": "string", "article": "string", "theme": "string",
        "criticality": "string", "target_maturity": "int",
    },
    "dim_control": {
        "id": "string", "name": "string", "control_family": "string",
        "capability_id": "string", "description": "string", "status": "string",
        "maturity": "int", "owner_unit_id": "string",
    },
    "dim_capability": {"id": "string", "name": "string", "domain": "string"},
    "dim_technology": {
        "id": "string", "name": "string", "vendor": "string",
        "category": "string", "is_microsoft": "bool",
    },
    "dim_evidence": {
        "id": "string", "control_id": "string", "evidence_type": "string",
        "name": "string", "status": "string", "technology_id": "string",
    },
    "dim_system": {
        "id": "string", "name": "string", "category": "string",
        "vendor": "string", "criticality": "string",
    },
    "dim_process": {
        "id": "string", "name": "string", "value_chain": "string",
        "owner_unit_id": "string",
    },
    "dim_product": {
        "id": "string", "name": "string", "product_line": "string",
        "owner_unit_id": "string",
    },
    "dim_data_domain": {
        "id": "string", "name": "string", "classification": "string",
        "contains_pii": "bool",
    },
    "dim_unit": {"id": "string", "name": "string", "division": "string"},
    "dim_risk": {
        "id": "string", "name": "string", "category": "string",
        "inherent_rating": "string",
    },
    # facts
    "fact_compliance_score": {
        "score_key": "string", "change_id": "string", "scope_type": "string",
        "scope_id": "string", "scope_name": "string", "scenario": "string",
        "score": "float", "status": "string",
    },
    "fact_gap": {
        "gap_id": "string", "change_id": "string", "obligation_id": "string",
        "control_id": "string", "capability_id": "string", "severity": "string",
        "maturity_shortfall": "int", "is_evidence_gap": "bool",
        "affected_systems": "int", "affected_processes": "int",
        "affected_products": "int", "affected_data_domains": "int",
    },
    "fact_remediation": {
        "remediation_id": "string", "gap_id": "string", "change_id": "string",
        "capability_id": "string", "action_type": "string", "priority": "string",
        "estimated_effort_days": "int", "target_unit_id": "string",
    },
    # bridge (multi-valued gap -> affected entities)
    "bridge_gap_entity": {
        "gap_id": "string", "entity_type": "string", "entity_id": "string",
    },
}

# fact column -> (dimension table, dimension key) for the star relationships.
GOLD_RELATIONSHIPS: list[tuple[str, str, str, str]] = [
    ("fact_compliance_score", "change_id", "dim_change", "id"),
    ("fact_gap", "change_id", "dim_change", "id"),
    ("fact_gap", "obligation_id", "dim_obligation", "id"),
    ("fact_gap", "control_id", "dim_control", "id"),
    ("fact_remediation", "change_id", "dim_change", "id"),
    ("fact_remediation", "target_unit_id", "dim_unit", "id"),
    ("dim_change", "regulation_id", "dim_regulation", "id"),
    ("dim_control", "capability_id", "dim_capability", "id"),
    ("dim_control", "owner_unit_id", "dim_unit", "id"),
]

_PANDAS_DTYPE = {
    "string": "string",
    "int": "int64",
    "float": "float64",
    "bool": "bool",
    "date": "datetime64[ns]",
}


def _cast(df: pd.DataFrame, table: str) -> pd.DataFrame:
    """Enforce the declared dtype for every column (Rule 3)."""
    spec = GOLD_SCHEMA[table]
    df = df.reindex(columns=list(spec.keys()))
    for col, fam in spec.items():
        if fam == "date":
            df[col] = pd.to_datetime(df[col], errors="coerce")
        else:
            df[col] = df[col].astype(_PANDAS_DTYPE[fam])
    return df


# --------------------------------------------------------------------------- #
# Builders
# --------------------------------------------------------------------------- #
def build_gold(estate: Estate) -> dict[str, pd.DataFrame]:
    control_cap = {c.id: c.capability_id for c in estate.controls}
    gap_by_id = {g.id: g for g in estate.gaps}

    frames: dict[str, list[dict]] = {t: [] for t in GOLD_SCHEMA}

    for r in estate.regulations:
        frames["dim_regulation"].append(r.model_dump())
    for c in estate.changes:
        d = c.model_dump()
        d["criticality"] = c.criticality.value
        frames["dim_change"].append(d)
    for o in estate.obligations:
        d = o.model_dump()
        d["criticality"] = o.criticality.value
        d["target_maturity"] = int(o.target_maturity)
        frames["dim_obligation"].append(d)
    for c in estate.controls:
        d = c.model_dump()
        d["status"] = c.status.value
        d["maturity"] = int(c.maturity)
        frames["dim_control"].append(d)
    for c in estate.capabilities:
        frames["dim_capability"].append(c.model_dump())
    for t in estate.technologies:
        frames["dim_technology"].append(t.model_dump())
    for ev in estate.evidence:
        d = ev.model_dump()
        d["status"] = ev.status.value
        frames["dim_evidence"].append(d)
    for s in estate.systems:
        d = s.model_dump()
        d["criticality"] = s.criticality.value
        frames["dim_system"].append(d)
    for p in estate.processes:
        frames["dim_process"].append(p.model_dump())
    for p in estate.products:
        frames["dim_product"].append(p.model_dump())
    for dd in estate.data_domains:
        frames["dim_data_domain"].append(dd.model_dump())
    for u in estate.units:
        frames["dim_unit"].append(u.model_dump())
    for rk in estate.risks:
        d = rk.model_dump()
        d["inherent_rating"] = rk.inherent_rating.value
        frames["dim_risk"].append(d)

    for s in estate.scores:
        frames["fact_compliance_score"].append({
            "score_key": f"{s.scope_type}|{s.scope_id}|{s.scenario.value}|{s.change_id}|{estate.as_of}",
            "change_id": s.change_id,
            "scope_type": s.scope_type,
            "scope_id": s.scope_id,
            "scope_name": s.scope_name,
            "scenario": s.scenario.value,
            "score": s.score,
            "status": s.status.value,
        })

    for g in estate.gaps:
        frames["fact_gap"].append({
            "gap_id": g.id,
            "change_id": g.change_id,
            "obligation_id": g.obligation_id,
            "control_id": g.control_id or "",
            "capability_id": control_cap.get(g.control_id or "", ""),
            "severity": g.severity.value,
            "maturity_shortfall": g.maturity_shortfall,
            "is_evidence_gap": g.id.endswith("-EV"),
            "affected_systems": len(g.affected_system_ids),
            "affected_processes": len(g.affected_process_ids),
            "affected_products": len(g.affected_product_ids),
            "affected_data_domains": len(g.affected_data_domain_ids),
        })
        for etype, ids in (
            ("System", g.affected_system_ids),
            ("BusinessProcess", g.affected_process_ids),
            ("Product", g.affected_product_ids),
            ("DataDomain", g.affected_data_domain_ids),
        ):
            for eid in ids:
                frames["bridge_gap_entity"].append(
                    {"gap_id": g.id, "entity_type": etype, "entity_id": eid}
                )

    for r in estate.remediations:
        gap = gap_by_id.get(r.gap_id)
        frames["fact_remediation"].append({
            "remediation_id": r.id,
            "gap_id": r.gap_id,
            "change_id": gap.change_id if gap else "",
            "capability_id": control_cap.get(gap.control_id or "", "") if gap else "",
            "action_type": r.action_type,
            "priority": r.priority.value,
            "estimated_effort_days": r.estimated_effort_days,
            "target_unit_id": r.target_unit_id,
        })

    return {t: _cast(pd.DataFrame(rows), t) for t, rows in frames.items()}


def validate_gold(frames: dict[str, pd.DataFrame]) -> list[str]:
    """Return a list of referential-integrity errors (empty == clean)."""
    errors: list[str] = []
    for ft, fc, dt, dc in GOLD_RELATIONSHIPS:
        keys = set(frames[ft][fc].dropna().tolist()) - {""}
        valid = set(frames[dt][dc].dropna().tolist())
        orphans = sorted(keys - valid)
        if orphans:
            errors.append(f"{ft}.{fc} -> {dt}.{dc}: {len(orphans)} orphan(s) e.g. {orphans[:3]}")
    return errors


def export_gold(estate: Estate, gold_dir: Path) -> tuple[list[Path], list[str]]:
    gold_dir.mkdir(parents=True, exist_ok=True)
    frames = build_gold(estate)
    errors = validate_gold(frames)
    if errors:
        raise ValueError(
            "Gold star-schema referential integrity failed (Rule 4): " + " | ".join(errors)
        )
    written: list[Path] = []
    for name, df in frames.items():
        df["as_of"] = estate.as_of
        df = df.rename(columns={c: to_column_name(c) for c in df.columns})
        csv_path = gold_dir / f"{name}.csv"
        df.to_csv(csv_path, index=False)
        written.append(csv_path)
        pq_path = gold_dir / f"{name}.parquet"
        df.to_parquet(pq_path, index=False)
        written.append(pq_path)
    return written, errors
