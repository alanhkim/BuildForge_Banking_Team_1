"""Synthetic data generator.

Builds a fully *correlated* bank estate from the reference catalog:

  * static estate  : units, data domains, risks, systems, processes,
                     products and controls (with seeded as-is maturity)
  * regulatory layer: one regulatory change per regulation, with obligations
                     instantiated from templates
  * relationships  : every link is emitted as a typed edge so the dataset is
                     traversable as a graph and joinable as tables

Generation is deterministic for a given seed, so demos are reproducible.
"""
from __future__ import annotations

import random
from datetime import date, timedelta

from .catalog import load_catalog
from .models import (
    BusinessProcess,
    BusinessUnit,
    Capability,
    Control,
    ControlStatus,
    Criticality,
    DataDomain,
    Edge,
    Estate,
    Evidence,
    EvidenceStatus,
    MaturityLevel,
    Obligation,
    Product,
    Regulation,
    RegulatoryChange,
    RelType,
    Risk,
    System,
    Technology,
)

# Anchor date for the POC ("today" in the demo narrative).
TODAY = date(2026, 6, 25)


def _maturity(rng: random.Random, bias: int) -> MaturityLevel:
    """Seeded as-is maturity, nudged by a control-family bias."""
    base = rng.choices(
        population=[0, 1, 2, 3, 4, 5],
        weights=[6, 14, 24, 28, 18, 10],
        k=1,
    )[0]
    value = max(0, min(5, base + bias - 1))
    return MaturityLevel(value)


def _status(maturity: MaturityLevel, rng: random.Random) -> ControlStatus:
    if maturity == MaturityLevel.NONE:
        return rng.choice([ControlStatus.MISSING, ControlStatus.PLANNED])
    if maturity <= MaturityLevel.REPEATABLE:
        return ControlStatus.PARTIAL
    return ControlStatus.IMPLEMENTED


def _evidence_status(rng: random.Random, maturity: MaturityLevel) -> EvidenceStatus:
    """Evidence availability correlates with control maturity."""
    if maturity == MaturityLevel.NONE:
        return rng.choices(
            [EvidenceStatus.MISSING, EvidenceStatus.PARTIAL],
            weights=[80, 20], k=1)[0]
    if maturity <= MaturityLevel.REPEATABLE:
        return rng.choices(
            [EvidenceStatus.MISSING, EvidenceStatus.PARTIAL, EvidenceStatus.STALE],
            weights=[35, 40, 25], k=1)[0]
    if maturity == MaturityLevel.DEFINED:
        return rng.choices(
            [EvidenceStatus.PARTIAL, EvidenceStatus.STALE, EvidenceStatus.PRESENT],
            weights=[25, 30, 45], k=1)[0]
    return rng.choices(
        [EvidenceStatus.STALE, EvidenceStatus.PRESENT],
        weights=[20, 80], k=1)[0]


def _assert_referential_integrity(est: Estate) -> None:
    """Fail loud (Rule 4 + Rule 12) if the catalog produced a broken graph.

    Guards two things at *build time* (rather than relying only on the
    downstream 07_audit gate): every edge must reference nodes that exist, and
    node ids must be unique. A typo in catalog.yaml surfaces here as an error,
    not as a silent orphan edge downstream.
    """
    node_attrs = (
        "units", "data_domains", "risks", "technologies", "capabilities",
        "systems", "products", "processes", "controls", "evidence",
        "regulations", "changes", "obligations",
    )
    node_ids: set[str] = set()
    duplicates: list[str] = []
    for attr in node_attrs:
        for node in getattr(est, attr):
            if node.id in node_ids:
                duplicates.append(node.id)
            node_ids.add(node.id)

    dangling = [
        (e.rel_type.value if hasattr(e.rel_type, "value") else e.rel_type,
         e.source_id, e.target_id)
        for e in est.edges
        if e.source_id not in node_ids or e.target_id not in node_ids
    ]

    problems: list[str] = []
    if duplicates:
        problems.append(f"{len(duplicates)} duplicate node id(s): {sorted(set(duplicates))[:10]}")
    if dangling:
        preview = "; ".join(f"{rt}: {s} -> {t}" for rt, s, t in dangling[:10])
        problems.append(f"{len(dangling)} edge(s) reference undefined nodes. First: {preview}")
    if problems:
        raise ValueError(
            "Referential integrity violation (Rule 4) in catalog.yaml — " + " | ".join(problems)
        )


def generate_estate(seed: int = 42, as_of: str | None = None) -> Estate:
    """Generate the full correlated estate.

    ``as_of`` stamps every exported row's ``As_Of`` column. When ``None`` (or
    empty), today's date is used so each run is dated to when it actually ran.
    Callers that need a pinned value (backfills, tests) may pass an ISO date.
    """
    rng = random.Random(seed)
    cat = load_catalog()
    est = Estate()
    est.as_of = as_of or date.today().isoformat()

    # Curated demo cohort (Rule 14): pinned, narratable baseline values that
    # override the seeded RNG for named cohort members only.
    cohort = cat.get("demo_cohort", {}) or {}
    cohort_controls = cohort.get("controls", {}) or {}
    cohort_evidence = cohort.get("evidence", {}) or {}

    # -- simple node tables ------------------------------------------------- #
    est.units = [BusinessUnit(**u) for u in cat["business_units"]]
    est.data_domains = [DataDomain(**d) for d in cat["data_domains"]]
    est.risks = [
        Risk(inherent_rating=Criticality(r["inherent_rating"]), **{k: v for k, v in r.items() if k != "inherent_rating"})
        for r in cat["risks"]
    ]

    # -- Layer 4: technologies --------------------------------------------- #
    for t in cat["technologies"]:
        est.technologies.append(Technology(**t))

    # -- Layer 3: capabilities + (capability)-[enabled by]->(technology) ---- #
    for cap in cat["capabilities"]:
        est.capabilities.append(Capability(id=cap["id"], name=cap["name"], domain=cap["domain"]))
        for tech in cap.get("technologies", []):
            est.edges.append(
                Edge(source_id=cap["id"], source_type="Capability", target_id=tech,
                     target_type="Technology", rel_type=RelType.CAPABILITY_ENABLED_BY_TECHNOLOGY)
            )
    evidence_tech = {ev["id"]: ev for ev in cat["evidence_types"]}

    # -- systems + (system)-[stores]->(data domain) ------------------------- #
    for s in cat["systems"]:
        est.systems.append(
            System(
                id=s["id"],
                name=s["name"],
                category=s["category"],
                vendor=s["vendor"],
                criticality=Criticality(s["criticality"]),
            )
        )
        for dd in s.get("data_domains", []):
            est.edges.append(
                Edge(
                    source_id=s["id"],
                    source_type="System",
                    target_id=dd,
                    target_type="DataDomain",
                    rel_type=RelType.SYSTEM_STORES_DATA_DOMAIN,
                )
            )

    # -- products + (product)-[owned by]->(unit) ---------------------------- #
    for p in cat["products"]:
        est.products.append(
            Product(id=p["id"], name=p["name"], product_line=p["product_line"], owner_unit_id=p["owner_unit_id"])
        )
        est.edges.append(
            Edge(
                source_id=p["id"],
                source_type="Product",
                target_id=p["owner_unit_id"],
                target_type="BusinessUnit",
                rel_type=RelType.PRODUCT_OWNED_BY_UNIT,
            )
        )

    # -- processes + (process)-[uses]->(system) / [supports]->(product) ----- #
    process_systems: dict[str, list[str]] = {}
    for pr in cat["processes"]:
        est.processes.append(
            BusinessProcess(
                id=pr["id"], name=pr["name"], value_chain=pr["value_chain"], owner_unit_id=pr["owner_unit_id"]
            )
        )
        process_systems[pr["id"]] = pr.get("systems", [])
        for sysid in pr.get("systems", []):
            est.edges.append(
                Edge(source_id=pr["id"], source_type="BusinessProcess", target_id=sysid,
                     target_type="System", rel_type=RelType.PROCESS_USES_SYSTEM)
            )
        for prodid in pr.get("products", []):
            est.edges.append(
                Edge(source_id=pr["id"], source_type="BusinessProcess", target_id=prodid,
                     target_type="Product", rel_type=RelType.PROCESS_SUPPORTS_PRODUCT)
            )

    # -- controls + structural edges ---------------------------------------- #
    # theme -> [control_id] map is derived from the families behind each theme.
    family_controls: dict[str, list[str]] = {}
    for fam in cat["control_families"]:
        bias = int(fam.get("maturity_bias", 1))
        ids: list[str] = []
        for c in fam["controls"]:
            pinned = cohort_controls.get(c["id"])
            maturity = (
                MaturityLevel(int(pinned["maturity"]))
                if pinned and "maturity" in pinned
                else _maturity(rng, bias)
            )
            ctl = Control(
                id=c["id"],
                name=c["name"],
                control_family=fam["name"],
                capability_id=fam.get("capability", ""),
                description=f"{c['name']} control within the {fam['name']} family.",
                status=_status(maturity, rng),
                maturity=maturity,
                owner_unit_id=fam["owner_unit_id"],
            )
            est.controls.append(ctl)
            ids.append(ctl.id)

            # control -> systems
            for sysid in c.get("systems", []):
                est.edges.append(
                    Edge(source_id=ctl.id, source_type="Control", target_id=sysid,
                         target_type="System", rel_type=RelType.CONTROL_IMPLEMENTED_IN_SYSTEM)
                )
            # control -> risk it mitigates
            est.edges.append(
                Edge(source_id=ctl.id, source_type="Control", target_id=fam["risk_id"],
                     target_type="Risk", rel_type=RelType.CONTROL_MITIGATES_RISK)
            )
            # control -> capability it realises (Layer 2 -> Layer 3)
            if fam.get("capability"):
                est.edges.append(
                    Edge(source_id=ctl.id, source_type="Control", target_id=fam["capability"],
                         target_type="Capability", rel_type=RelType.CONTROL_REALIZES_CAPABILITY)
                )
            # control -> evidence artefacts (Layer 5), status correlated to maturity
            for ev_id in fam.get("evidence", []):
                ev_def = evidence_tech.get(ev_id, {})
                pinned_ev = cohort_evidence.get(ctl.id, {})
                ev_status = (
                    EvidenceStatus(pinned_ev[ev_id])
                    if ev_id in pinned_ev
                    else _evidence_status(rng, maturity)
                )
                ev_inst_id = f"EVD-{ctl.id}-{ev_id}"
                est.evidence.append(
                    Evidence(
                        id=ev_inst_id,
                        control_id=ctl.id,
                        evidence_type=ev_id,
                        name=ev_def.get("name", ev_id),
                        status=ev_status,
                        technology_id=ev_def.get("technology_id", ""),
                    )
                )
                est.edges.append(
                    Edge(source_id=ctl.id, source_type="Control", target_id=ev_inst_id,
                         target_type="Evidence", rel_type=RelType.CONTROL_EVIDENCED_BY)
                )
                if ev_def.get("technology_id"):
                    est.edges.append(
                        Edge(source_id=ev_inst_id, source_type="Evidence",
                             target_id=ev_def["technology_id"], target_type="Technology",
                             rel_type=RelType.EVIDENCE_PRODUCED_BY_TECHNOLOGY)
                    )
            # control -> processes (those that use the control's systems)
            ctl_systems = set(c.get("systems", []))
            for prid, sysids in process_systems.items():
                if ctl_systems & set(sysids):
                    est.edges.append(
                        Edge(source_id=ctl.id, source_type="Control", target_id=prid,
                             target_type="BusinessProcess", rel_type=RelType.CONTROL_OPERATES_IN_PROCESS)
                    )
        family_controls[fam["id"]] = ids

    theme_controls = {
        theme: [cid for fam in spec["control_families"] for cid in family_controls.get(fam, [])]
        for theme, spec in cat["themes"].items()
    }
    theme_data = {theme: spec["data_domains"] for theme, spec in cat["themes"].items()}

    # -- regulations, changes, obligations ---------------------------------- #
    for idx, reg in enumerate(cat["regulations"]):
        est.regulations.append(
            Regulation(
                id=reg["id"], name=reg["name"], short_code=reg["short_code"],
                regulator=reg["regulator"], jurisdiction=reg["jurisdiction"],
                domain=reg["domain"], description=reg["description"],
            )
        )

        change_id = f"CHG-{reg['short_code']}"
        published = TODAY - timedelta(days=rng.randint(20, 120))
        effective = TODAY + timedelta(days=rng.randint(90, 540))
        templates = reg["obligation_templates"]
        change_crit = max(
            (Criticality(t["criticality"]) for t in templates),
            key=lambda c: ["Low", "Medium", "High", "Critical"].index(c.value),
        )
        est.changes.append(
            RegulatoryChange(
                id=change_id,
                regulation_id=reg["id"],
                title=f"{reg['short_code']} regulatory update",
                reference=f"{reg['short_code']} 2026 amendment",
                summary=f"Incoming update to {reg['name']} affecting {reg['domain'].lower()} obligations.",
                change_type=rng.choice(["New", "Amendment", "Clarification"]),
                published_date=published,
                effective_date=effective,
                criticality=change_crit,
            )
        )
        est.edges.append(
            Edge(source_id=change_id, source_type="RegulatoryChange", target_id=reg["id"],
                 target_type="Regulation", rel_type=RelType.CHANGE_OF_REGULATION)
        )

        for oi, tmpl in enumerate(templates, start=1):
            ob_id = f"OBL-{reg['short_code']}-{oi:02d}"
            est.obligations.append(
                Obligation(
                    id=ob_id,
                    change_id=change_id,
                    regulation_id=reg["id"],
                    statement=tmpl["statement"],
                    article=tmpl.get("article", ""),
                    theme=tmpl["theme"],
                    criticality=Criticality(tmpl["criticality"]),
                    target_maturity=MaturityLevel(int(tmpl["target_maturity"])),
                )
            )
            est.edges.append(
                Edge(source_id=change_id, source_type="RegulatoryChange", target_id=ob_id,
                     target_type="Obligation", rel_type=RelType.INTRODUCES_OBLIGATION)
            )
            # obligation -> required controls
            for cid in theme_controls.get(tmpl["theme"], []):
                est.edges.append(
                    Edge(source_id=ob_id, source_type="Obligation", target_id=cid,
                         target_type="Control", rel_type=RelType.OBLIGATION_REQUIRES_CONTROL)
                )
            # obligation -> concerned data domains
            for dd in theme_data.get(tmpl["theme"], []):
                est.edges.append(
                    Edge(source_id=ob_id, source_type="Obligation", target_id=dd,
                         target_type="DataDomain", rel_type=RelType.OBLIGATION_CONCERNS_DATA_DOMAIN)
                )

    _assert_referential_integrity(est)
    return est


def build_estate() -> Estate:
    """Build the deterministic default demo estate."""
    return generate_estate()
