from regimpact.agents.control_mapper import ControlMapperAgent
from regimpact.agents.gap_analysis import GapAnalysisAgent
from regimpact.agents.remediation import RemediationAgent
from regimpact.generator import build_estate
from regimpact.impact import analyze_change
from regimpact.models import RelType
from regimpact.scoring import score_change


def test_control_mapper_is_idempotent_for_catalog_estate():
    estate = build_estate()

    result = ControlMapperAgent(estate).map_all()

    assert result == {"obligations_mapped": 42, "edges_added": 0}


def test_dora_gap_analysis_writes_gaps_remediations_and_edges():
    estate = build_estate()

    summary = analyze_change(estate, "CHG-DORA")

    assert summary["obligations"] == 4
    assert summary["gaps"] == 13
    assert summary["total_effort_days"] == 990
    assert summary["gaps_by_severity"]["Critical"] == 2
    assert len(estate.gaps) == 13
    assert len(estate.remediations) == 13
    assert any(
        edge.rel_type == RelType.GAP_FOR_OBLIGATION
        for edge in estate.edges
    )
    assert any(
        edge.rel_type == RelType.REMEDIATION_RESOLVES_GAP
        for edge in estate.edges
    )


def test_scoring_shows_post_change_drop_and_remediation_recovery():
    estate = build_estate()
    analyze_change(estate, "CHG-DORA")

    scores = score_change(estate, "CHG-DORA")

    assert scores["as_is"] > scores["post_change"]
    assert scores["post_remediation"] > scores["post_change"]
    assert scores["score_drop"] == 1.8
    assert scores["score_recovered"] == 6.7
    assert len(estate.scores) >= 5


def test_downstream_agent_wrappers_run_offline():
    estate = build_estate()

    gaps = GapAnalysisAgent(estate).run("CHG-DORA")
    remediation = RemediationAgent(estate).run("CHG-DORA")

    assert gaps["gaps"] == 13
    assert remediation["total_effort_days"] == 990
    assert remediation["narrative"].startswith("13 remediation actions identified")
