"""Export layer: turns an Estate into Lakehouse-ready tables, a graph and a report.

Outputs (under the configured output dir):
  tables/*.parquet + *.csv   -> one file per entity, plus edges.csv
  graph/estate.graphml       -> for Gephi / yEd / NetworkX
  graph/estate.json          -> nodes+edges for D3 / Cytoscape web viz
  reports/<change>.md        -> human-readable impact assessment
"""
from __future__ import annotations

import json
from pathlib import Path

import networkx as nx
import pandas as pd

from .graph import build_graph
from .models import Estate, to_column_name

# entity attribute -> output table name
_TABLES = {
    "regulations": "regulations",
    "changes": "regulatory_changes",
    "obligations": "obligations",
    "controls": "controls",
    "capabilities": "capabilities",
    "technologies": "technologies",
    "evidence": "evidence",
    "systems": "systems",
    "processes": "business_processes",
    "products": "products",
    "data_domains": "data_domains",
    "units": "business_units",
    "risks": "risks",
    "gaps": "gaps",
    "remediations": "remediation_actions",
    "scores": "compliance_scores",
}


def _to_frame(records: list) -> pd.DataFrame:
    rows = []
    for r in records:
        d = r.model_dump()
        for k, v in list(d.items()):
            if isinstance(v, list):
                d[k] = ";".join(str(x) for x in v)
            elif hasattr(v, "value"):  # Enum
                d[k] = v.value
        rows.append(d)
    return pd.DataFrame(rows)


def export_tables(estate: Estate, tables_dir: Path, clean: bool = False) -> list[Path]:
    tables_dir.mkdir(parents=True, exist_ok=True)
    if clean:
        # Rule 12: start from a clean slate so a renamed/removed table cannot
        # leave a stale file behind. Only touches this dedicated output dir.
        for stale in (*tables_dir.glob("*.csv"), *tables_dir.glob("*.parquet")):
            stale.unlink()
    written: list[Path] = []
    for attr, name in _TABLES.items():
        df = _to_frame(getattr(estate, attr))
        if df.empty:
            continue
        df["as_of"] = estate.as_of
        df = df.rename(columns={c: to_column_name(c) for c in df.columns})
        csv_path = tables_dir / f"{name}.csv"
        df.to_csv(csv_path, index=False)
        written.append(csv_path)
        pq_path = tables_dir / f"{name}.parquet"
        df.to_parquet(pq_path, index=False)
        written.append(pq_path)

    # edges table (the relationship backbone)
    edges = pd.DataFrame([e.model_dump() for e in estate.edges])
    if not edges.empty:
        edges["rel_type"] = edges["rel_type"].map(lambda x: x.value if hasattr(x, "value") else x)
        edges["as_of"] = estate.as_of
        edges = edges.rename(columns={c: to_column_name(c) for c in edges.columns})
        csv_path = tables_dir / "relationships.csv"
        edges.to_csv(csv_path, index=False)
        written.append(csv_path)
        pq_path = tables_dir / "relationships.parquet"
        edges.to_parquet(pq_path, index=False)
        written.append(pq_path)
    return written


def export_graph(estate: Estate, graph_dir: Path) -> list[Path]:
    graph_dir.mkdir(parents=True, exist_ok=True)
    g = build_graph(estate)
    written: list[Path] = []

    graphml_path = graph_dir / "estate.graphml"
    nx.write_graphml(g, graphml_path)
    written.append(graphml_path)

    json_path = graph_dir / "estate.json"
    payload = {
        "nodes": [{"id": n, **g.nodes[n]} for n in g.nodes],
        "edges": [
            {"source": u, "target": v, "rel_type": d.get("rel_type")}
            for u, v, d in g.edges(data=True)
        ],
    }
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    written.append(json_path)
    return written


def export_report(estate: Estate, summary: dict, reports_dir: Path) -> Path:
    reports_dir.mkdir(parents=True, exist_ok=True)
    change_id = summary["change_id"]
    name_by_id = {x.id: getattr(x, "name", x.id) for grp in
                  ("systems", "processes", "products") for x in getattr(estate, grp)}
    gaps = [g for g in estate.gaps if g.change_id == change_id]
    rem_by_gap = {r.gap_id: r for r in estate.remediations}

    lines = [
        f"# Regulatory Change Impact Assessment — {summary['change_title']}",
        "",
        f"- **Change ID:** {change_id}",
        f"- **Regulation:** {summary['regulation_id']}",
        f"- **Criticality:** {summary['criticality']}",
        f"- **Effective date:** {summary['effective_date']}",
        f"- **Obligations:** {summary['obligations']}",
        f"- **Gaps identified:** {summary['gaps']}  ({summary['gaps_by_severity']})",
        f"- **Estimated remediation effort:** {summary['total_effort_days']} person-days",
        "",
        "## Blast radius",
        f"- **Products affected:** {', '.join(name_by_id.get(p, p) for p in summary['affected_products']) or 'None'}",
        f"- **Processes affected:** {', '.join(name_by_id.get(p, p) for p in summary['affected_processes']) or 'None'}",
        f"- **Systems affected:** {', '.join(name_by_id.get(s, s) for s in summary['affected_systems']) or 'None'}",
        "",
        "## Gaps & recommended remediation",
        "",
        "| Severity | Shortfall | Gap rationale | Recommended action | Effort (days) | Owner |",
        "|---|---|---|---|---|---|",
    ]
    for g in sorted(gaps, key=lambda x: x.maturity_shortfall, reverse=True):
        rem = rem_by_gap.get(g.id)
        lines.append(
            f"| {g.severity.value} | {g.maturity_shortfall} | {g.rationale} | "
            f"{rem.action if rem else '-'} | {rem.estimated_effort_days if rem else '-'} | "
            f"{rem.target_unit_id if rem else '-'} |"
        )

    report_path = reports_dir / f"impact_{change_id}.md"
    report_path.write_text("\n".join(lines), encoding="utf-8")
    return report_path
