"""Microsoft Purview assets generator.

Turns the correlated estate into governance artefacts that can be imported into
Microsoft Purview (Unified Catalog):

  * glossary_terms.json  — business glossary terms (regulations, themes,
                           data domains, controls) with definitions
  * lineage.csv          — asset-to-asset lineage rows
                           (system -> data domain -> obligation -> regulation)

In a live environment these feed Purview so that data stewards can see *why* a
data asset matters (which regulation depends on it) and trace impact through
lineage. For the POC they demonstrate the governance layer without needing the
Purview APIs.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

from .models import Estate, RelType


def _glossary_terms(estate: Estate) -> list[dict]:
    terms: list[dict] = []

    for reg in estate.regulations:
        terms.append({
            "name": reg.short_code,
            "longDescription": reg.description,
            "status": "Approved",
            "termType": "Regulation",
            "attributes": {"regulator": reg.regulator, "jurisdiction": reg.jurisdiction, "domain": reg.domain},
        })

    for dd in estate.data_domains:
        terms.append({
            "name": dd.name,
            "longDescription": f"{dd.classification} data domain. Contains PII: {dd.contains_pii}.",
            "status": "Approved",
            "termType": "DataDomain",
            "attributes": {"classification": dd.classification, "contains_pii": dd.contains_pii},
        })

    # control families become governance "capabilities"
    seen_families: set[str] = set()
    for ctl in estate.controls:
        if ctl.control_family in seen_families:
            continue
        seen_families.add(ctl.control_family)
        terms.append({
            "name": ctl.control_family,
            "longDescription": f"Control capability: {ctl.control_family}.",
            "status": "Approved",
            "termType": "ControlCapability",
        })

    for cap in estate.capabilities:
        terms.append({
            "name": cap.name,
            "longDescription": f"Compliance capability in the {cap.domain} domain.",
            "status": "Approved",
            "termType": "Capability",
        })

    for tec in estate.technologies:
        terms.append({
            "name": tec.name,
            "longDescription": f"{tec.category} technology provided by {tec.vendor}.",
            "status": "Approved",
            "termType": "Technology",
            "attributes": {"vendor": tec.vendor, "category": tec.category, "is_microsoft": tec.is_microsoft},
        })

    return terms


def _lineage_rows(estate: Estate) -> list[dict]:
    """Build a readable lineage chain: System -> DataDomain -> Obligation -> Regulation."""
    name = {x.id: getattr(x, "name", None) or getattr(x, "short_code", x.id)
            for grp in ("systems", "data_domains", "obligations", "regulations", "controls",
                        "capabilities", "technologies", "evidence")
            for x in getattr(estate, grp)}

    rows: list[dict] = []
    for e in estate.edges:
        if e.rel_type in (
            RelType.SYSTEM_STORES_DATA_DOMAIN,
            RelType.OBLIGATION_CONCERNS_DATA_DOMAIN,
            RelType.CHANGE_OF_REGULATION,
            RelType.OBLIGATION_REQUIRES_CONTROL,
            RelType.CONTROL_IMPLEMENTED_IN_SYSTEM,
            RelType.CONTROL_REALIZES_CAPABILITY,
            RelType.CONTROL_EVIDENCED_BY,
            RelType.CAPABILITY_ENABLED_BY_TECHNOLOGY,
            RelType.EVIDENCE_PRODUCED_BY_TECHNOLOGY,
        ):
            rows.append({
                "source_qualified_name": e.source_id,
                "source_name": name.get(e.source_id, e.source_id),
                "source_type": e.source_type,
                "relationship": e.rel_type.value,
                "target_qualified_name": e.target_id,
                "target_name": name.get(e.target_id, e.target_id),
                "target_type": e.target_type,
            })
    return rows


def export_purview(estate: Estate, purview_dir: Path) -> list[Path]:
    purview_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    glossary_path = purview_dir / "glossary_terms.json"
    glossary_path.write_text(
        json.dumps({"name": "Regulatory Glossary", "terms": _glossary_terms(estate)}, indent=2),
        encoding="utf-8",
    )
    written.append(glossary_path)

    lineage_path = purview_dir / "lineage.csv"
    rows = _lineage_rows(estate)
    with lineage_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    written.append(lineage_path)

    return written
