from regimpact.audit import audit_referential_integrity, run_audit
from regimpact.export import export_report, export_tables
from regimpact.generator import build_estate
from regimpact.gold import export_gold
from regimpact.impact import analyze_change
from regimpact.purview import export_purview
from regimpact.scoring import score_change


def _analyzed_estate():
    estate = build_estate()
    summary = analyze_change(estate, "CHG-DORA")
    score_change(estate, "CHG-DORA")
    return estate, summary


def test_export_tables_writes_csv_and_parquet(tmp_path):
    estate, _ = _analyzed_estate()

    written = export_tables(estate, tmp_path / "tables", clean=True)
    names = {path.name for path in written}

    assert "regulations.csv" in names
    assert "regulations.parquet" in names
    assert "relationships.csv" in names
    assert "relationships.parquet" in names
    assert (tmp_path / "tables" / "compliance_scores.parquet").exists()


def test_export_report_and_purview_assets(tmp_path):
    estate, summary = _analyzed_estate()

    report_path = export_report(estate, summary, tmp_path / "reports")
    purview_paths = export_purview(estate, tmp_path / "purview")

    assert report_path.name == "impact_CHG-DORA.md"
    assert "Regulatory Change Impact Assessment" in report_path.read_text()
    assert {path.name for path in purview_paths} == {
        "glossary_terms.json",
        "lineage.csv",
    }


def test_gold_export_writes_star_schema_with_parquet(tmp_path):
    estate, _ = _analyzed_estate()

    written, errors = export_gold(estate, tmp_path / "gold")
    names = {path.name for path in written}

    assert errors == []
    assert "dim_regulation.parquet" in names
    assert "fact_gap.parquet" in names
    assert "fact_remediation.parquet" in names


def test_audit_reports_clean_referential_integrity_and_parquet_types(tmp_path):
    estate, _ = _analyzed_estate()
    tables_dir = tmp_path / "tables"
    export_tables(estate, tables_dir, clean=True)

    ref_report = audit_referential_integrity(estate)
    reports = run_audit(estate, tables_dir)

    assert ref_report.ok
    assert reports["referential_integrity"].ok
    assert reports["dtype_uniformity"].ok
