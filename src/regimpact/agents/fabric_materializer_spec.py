"""PySpark script the FabricMaterializerAgent submits to the Fabric Livy Batch API.

We keep the raw and gold table lists AND the SQL for the three published views
here so they live in one deterministic place — the CLI, the agent, and tests
all reference the same source of truth.

The PySpark source itself is a normal Python string built at module import
using :meth:`str.replace` on unambiguous ``__PLACEHOLDER__`` tokens — no
``%``-formatting and no ``.format()``, so the embedded braces (``f`` strings,
SQL, dict literals) survive unmodified when Livy evaluates them server-side.
"""
from __future__ import annotations

from ..lakehouse import GOLD_SUBPATH, RAW_SUBPATH

# ---------------------------------------------------------------- table lists

# Raw parquet uploaded by ``regimpact interpret`` — one per table.
RAW_TABLES: tuple[str, ...] = (
    "regulations",
    "regulatory_changes",
    "obligations",
    "controls",
    "capabilities",
    "technologies",
    "evidence",
    "systems",
    "business_processes",
    "products",
    "data_domains",
    "business_units",
    "risks",
    "gaps",
    "remediation_actions",
    "compliance_scores",
    "relationships",
)

# Gold-layer star schema uploaded alongside the raw parquet.
GOLD_TABLES: tuple[str, ...] = (
    "dim_regulation",
    "dim_change",
    "dim_obligation",
    "dim_control",
    "dim_capability",
    "dim_technology",
    "dim_evidence",
    "dim_system",
    "dim_process",
    "dim_product",
    "dim_data_domain",
    "dim_unit",
    "dim_risk",
    "fact_compliance_score",
    "fact_gap",
    "fact_remediation",
    "bridge_gap_entity",
)

# Views built on top of the raw tables (see docs/agent-plan.md).
VIEW_NAMES: tuple[str, ...] = (
    "v_impact",
    "v_compliance",
    "v_capability_health",
)


# --------------------------------------------------------- PySpark script
_PYSPARK_TEMPLATE = """\
# regimpact FabricMaterializerAgent — Livy batch payload.
# Loads uploaded parquet into managed Delta tables and publishes views.

RAW_FOLDER = 'Files/__RAW_SUBPATH__'
GOLD_FOLDER = 'Files/__GOLD_SUBPATH__'

RAW_TABLES = __RAW_TABLES__
GOLD_TABLES = __GOLD_TABLES__

loaded = []
skipped = []

for name in RAW_TABLES:
    path = f'{RAW_FOLDER}/{name}.parquet'
    try:
        df = spark.read.parquet(path)
        (
            df.write
            .mode('overwrite')
            .option('overwriteSchema', 'true')
            .format('delta')
            .saveAsTable(name)
        )
        loaded.append(name)
        print(f'[raw] loaded {name}')
    except Exception as exc:  # noqa: BLE001
        skipped.append((name, str(exc)))
        print(f'[raw] skipped {name}: {exc}')

for name in GOLD_TABLES:
    path = f'{GOLD_FOLDER}/{name}.parquet'
    try:
        df = spark.read.parquet(path)
        (
            df.write
            .mode('overwrite')
            .option('overwriteSchema', 'true')
            .format('delta')
            .saveAsTable(name)
        )
        loaded.append(name)
        print(f'[gold] loaded {name}')
    except Exception as exc:  # noqa: BLE001
        skipped.append((name, str(exc)))
        print(f'[gold] skipped {name}: {exc}')

# --- Published views -------------------------------------------------------

spark.sql('''
CREATE OR REPLACE VIEW v_impact AS
SELECT
    c.id                    AS change_id,
    c.title                 AS change_title,
    c.regulation_id         AS regulation_id,
    c.criticality           AS change_criticality,
    c.effective_date        AS effective_date,
    o.id                    AS obligation_id,
    o.statement             AS obligation,
    o.theme                 AS theme,
    o.target_maturity       AS target_maturity,
    g.id                    AS gap_id,
    g.severity              AS gap_severity,
    g.maturity_shortfall    AS maturity_shortfall,
    g.control_id            AS control_id,
    ctl.capability_id       AS capability_id,
    cap.name                AS capability,
    g.rationale             AS rationale,
    r.action                AS remediation,
    r.action_type           AS action_type,
    r.estimated_effort_days AS estimated_effort_days,
    r.priority              AS remediation_priority,
    r.target_unit_id        AS target_unit_id
FROM regulatory_changes c
JOIN obligations o           ON o.change_id       = c.id
LEFT JOIN gaps g             ON g.obligation_id   = o.id
LEFT JOIN remediation_actions r ON r.gap_id       = g.id
LEFT JOIN controls ctl       ON ctl.id            = g.control_id
LEFT JOIN capabilities cap   ON cap.id            = ctl.capability_id
''')
print('[view] created v_impact')

spark.sql('''
CREATE OR REPLACE VIEW v_compliance AS
SELECT
    s.change_id,
    s.scope_type,
    s.scope_id,
    s.scope_name,
    s.scenario,
    s.score,
    s.status
FROM compliance_scores s
''')
print('[view] created v_compliance')

spark.sql('''
CREATE OR REPLACE VIEW v_capability_health AS
SELECT
    cap.id       AS capability_id,
    cap.name     AS capability,
    cap.domain   AS domain,
    ctl.id       AS control_id,
    ctl.name     AS control,
    ctl.maturity AS maturity,
    ctl.status   AS control_status,
    ev.evidence_type AS evidence_type,
    ev.status    AS evidence_status,
    tec.name     AS technology,
    tec.is_microsoft AS is_microsoft
FROM capabilities cap
LEFT JOIN controls ctl   ON ctl.capability_id = cap.id
LEFT JOIN evidence ev    ON ev.control_id     = ctl.id
LEFT JOIN technologies tec ON tec.id          = ev.technology_id
''')
print('[view] created v_capability_health')

print(f'[summary] loaded={len(loaded)} skipped={len(skipped)}')
if skipped:
    for name, reason in skipped:
        print(f'[summary] skipped {name}: {reason}')
"""


def _build_script() -> str:
    return (
        _PYSPARK_TEMPLATE.replace("__RAW_SUBPATH__", RAW_SUBPATH)
        .replace("__GOLD_SUBPATH__", GOLD_SUBPATH)
        .replace("__RAW_TABLES__", repr(list(RAW_TABLES)))
        .replace("__GOLD_TABLES__", repr(list(GOLD_TABLES)))
    )


#: Final PySpark payload (already interpolated with subpaths + table lists).
MATERIALIZE_PYSPARK_TEMPLATE: str = _build_script()


__all__ = [
    "RAW_TABLES",
    "GOLD_TABLES",
    "VIEW_NAMES",
    "MATERIALIZE_PYSPARK_TEMPLATE",
]
