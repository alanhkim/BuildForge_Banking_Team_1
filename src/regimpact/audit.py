"""Data-quality audit for the Regulatory Change Impact estate.

Two independent audits, both required by the project's governance rules:

* **Referential integrity** (Rule 4) — every foreign key (including list-valued
  keys and every typed edge) must point at an existing primary key. No stale or
  orphaned references between joinable tables, so the demo actually joins.

* **Data-type uniformity** (Rule 3) — a logical column must have the *same*
  physical dtype everywhere it appears (e.g. every ``*_id`` is a string, every
  maturity is an integer). This is what lets a semantic model / Data Agent treat
  the column consistently across all layers.

Referential integrity runs against the in-memory :class:`Estate` (the source of
truth). Data-type uniformity runs against the exported Parquet files (what
actually lands in the Lakehouse and is read by the semantic model / agent).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from .models import Estate


# --------------------------------------------------------------------------- #
# Findings
# --------------------------------------------------------------------------- #
@dataclass
class Finding:
    check: str          # short check id, e.g. "ref:gaps.obligation_id"
    status: str         # "PASS" | "FAIL"
    detail: str         # human-readable detail
    offenders: list[str] = field(default_factory=list)


@dataclass
class AuditReport:
    findings: list[Finding] = field(default_factory=list)

    def add(self, check: str, ok: bool, detail: str, offenders: list[str] | None = None) -> None:
        self.findings.append(
            Finding(check, "PASS" if ok else "FAIL", detail, offenders or [])
        )

    @property
    def failures(self) -> list[Finding]:
        return [f for f in self.findings if f.status == "FAIL"]

    @property
    def ok(self) -> bool:
        return not self.failures


# --------------------------------------------------------------------------- #
# Referential integrity (against the in-memory Estate)
# --------------------------------------------------------------------------- #

# Foreign keys: (estate_attr, column, target_estate_attr, is_list, nullable)
_FOREIGN_KEYS: list[tuple[str, str, str, bool, bool]] = [
    ("changes", "regulation_id", "regulations", False, False),
    ("obligations", "change_id", "changes", False, False),
    ("obligations", "regulation_id", "regulations", False, False),
    ("controls", "capability_id", "capabilities", False, True),
    ("controls", "owner_unit_id", "units", False, False),
    ("evidence", "control_id", "controls", False, False),
    ("evidence", "technology_id", "technologies", False, False),
    ("processes", "owner_unit_id", "units", False, False),
    ("products", "owner_unit_id", "units", False, False),
    ("gaps", "obligation_id", "obligations", False, False),
    ("gaps", "change_id", "changes", False, False),
    ("gaps", "control_id", "controls", False, True),
    ("gaps", "affected_system_ids", "systems", True, False),
    ("gaps", "affected_process_ids", "processes", True, False),
    ("gaps", "affected_product_ids", "products", True, False),
    ("gaps", "affected_data_domain_ids", "data_domains", True, False),
    ("remediations", "gap_id", "gaps", False, False),
    ("remediations", "target_unit_id", "units", False, False),
    ("scores", "change_id", "changes", False, True),
]

# Edge source_type / target_type label -> estate attribute holding those nodes.
_EDGE_TYPE_TO_ATTR = {
    "Regulation": "regulations",
    "RegulatoryChange": "changes",
    "Obligation": "obligations",
    "Control": "controls",
    "Capability": "capabilities",
    "Technology": "technologies",
    "Evidence": "evidence",
    "System": "systems",
    "BusinessProcess": "processes",
    "Product": "products",
    "DataDomain": "data_domains",
    "BusinessUnit": "units",
    "Risk": "risks",
    "Gap": "gaps",
    "RemediationAction": "remediations",
}


def _id_set(estate: Estate, attr: str) -> set[str]:
    return {getattr(x, "id") for x in getattr(estate, attr)}


def _is_blank(value) -> bool:
    return value is None or value == ""


def audit_referential_integrity(estate: Estate) -> AuditReport:
    report = AuditReport()
    id_sets: dict[str, set[str]] = {
        attr: _id_set(estate, attr)
        for attr in {fk[2] for fk in _FOREIGN_KEYS} | set(_EDGE_TYPE_TO_ATTR.values())
    }

    # Primary-key uniqueness per entity table.
    for attr in sorted(set(_EDGE_TYPE_TO_ATTR.values())):
        records = getattr(estate, attr)
        ids = [getattr(x, "id") for x in records]
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        report.add(
            f"pk-unique:{attr}",
            not dupes,
            f"{attr}: {len(ids)} rows, {len(set(ids))} unique ids",
            dupes,
        )

    # Foreign-key coverage.
    for attr, col, target, is_list, nullable in _FOREIGN_KEYS:
        targets = id_sets[target]
        offenders: list[str] = []
        for rec in getattr(estate, attr):
            owner = getattr(rec, "id", "?")
            val = getattr(rec, col)
            if is_list:
                for item in (val or []):
                    if not _is_blank(item) and item not in targets:
                        offenders.append(f"{owner}.{col}->{item}")
            else:
                if _is_blank(val):
                    if not nullable:
                        offenders.append(f"{owner}.{col}=<blank>")
                elif val not in targets:
                    offenders.append(f"{owner}.{col}->{val}")
        report.add(
            f"ref:{attr}.{col}",
            not offenders,
            f"{attr}.{col} -> {target} ({len(getattr(estate, attr))} rows checked)",
            offenders,
        )

    # Edge endpoints.
    edge_offenders: list[str] = []
    unknown_types: set[str] = set()
    for e in estate.edges:
        for node_id, node_type in ((e.source_id, e.source_type), (e.target_id, e.target_type)):
            attr = _EDGE_TYPE_TO_ATTR.get(node_type)
            if attr is None:
                unknown_types.add(node_type)
                continue
            if node_id not in id_sets[attr]:
                edge_offenders.append(f"{e.rel_type.value}:{node_type}({node_id})")
    report.add(
        "ref:relationships.endpoints",
        not edge_offenders,
        f"{len(estate.edges)} edges checked",
        edge_offenders,
    )
    if unknown_types:
        report.add(
            "ref:relationships.types",
            False,
            "edge source/target types with no known entity table",
            sorted(unknown_types),
        )
    return report


# --------------------------------------------------------------------------- #
# Data-type uniformity (against exported Parquet)
# --------------------------------------------------------------------------- #

# Expected logical dtype family for columns, by naming convention or exact name.
# "string" = object/string, "int" = any integer, "float" = floating, "bool".
_EXACT_COLUMN_FAMILY = {
    "maturity": "int",
    "target_maturity": "int",
    "maturity_shortfall": "int",
    "estimated_effort_days": "int",
    "score": "float",
    "weight": "float",
    "contains_pii": "bool",
    "is_microsoft": "bool",
}


def _family_of(dtype: str) -> str:
    if dtype.startswith(("int", "uint")):
        return "int"
    if dtype.startswith("float"):
        return "float"
    if dtype == "bool":
        return "bool"
    return "string"


def _expected_family(column: str) -> str | None:
    # Exported columns are Pascal_Snake (Rule 10); lower-case to match the
    # snake-cased expectation keys / patterns below.
    column = column.lower()
    if column in _EXACT_COLUMN_FAMILY:
        return _EXACT_COLUMN_FAMILY[column]
    if column == "id" or column.endswith("_id") or column.endswith("_ids"):
        return "string"
    return None


def audit_dtype_uniformity(tables_dir: Path) -> AuditReport:
    report = AuditReport()
    parquet_files = sorted(tables_dir.glob("*.parquet"))
    if not parquet_files:
        report.add("dtype:source", False,
                   f"no Parquet files found in {tables_dir}; run `demo` first")
        return report

    # column -> {table: family}
    seen: dict[str, dict[str, str]] = {}
    for pq in parquet_files:
        df = pd.read_parquet(pq)
        for col in df.columns:
            fam = _family_of(str(df[col].dtype))
            seen.setdefault(col, {})[pq.stem] = fam

    # 1) cross-table consistency: same column, same family everywhere.
    for col, by_table in sorted(seen.items()):
        families = set(by_table.values())
        report.add(
            f"dtype-consistent:{col}",
            len(families) == 1,
            f"{col} appears in {len(by_table)} table(s) as {sorted(families)}",
            [f"{t}:{f}" for t, f in sorted(by_table.items())] if len(families) > 1 else [],
        )

    # 2) convention conformance: key/numeric/bool columns match expectation.
    for col, by_table in sorted(seen.items()):
        expected = _expected_family(col)
        if expected is None:
            continue
        bad = sorted(f"{t}:{f}" for t, f in by_table.items() if f != expected)
        report.add(
            f"dtype-expected:{col}",
            not bad,
            f"{col} expected '{expected}'",
            bad,
        )
    return report


# --------------------------------------------------------------------------- #
# Combined
# --------------------------------------------------------------------------- #
def run_audit(estate: Estate, tables_dir: Path) -> dict[str, AuditReport]:
    return {
        "referential_integrity": audit_referential_integrity(estate),
        "dtype_uniformity": audit_dtype_uniformity(tables_dir),
    }
