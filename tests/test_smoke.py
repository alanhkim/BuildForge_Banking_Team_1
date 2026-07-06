from regimpact.generator import build_estate
from regimpact.models import RelType


def test_build_estate_creates_correlated_dora_example():
    estate = build_estate()

    assert any(regulation.id == "REG-DORA" for regulation in estate.regulations)
    assert any(change.regulation_id == "REG-DORA" for change in estate.changes)
    assert any(obligation.change_id == "CHG-DORA" for obligation in estate.obligations)
    assert any(
        edge.source_id == "CTL-OR-3"
        and edge.target_id == "CAP-RES"
        and edge.rel_type == RelType.CONTROL_REALIZES_CAPABILITY
        for edge in estate.edges
    )
    assert any(
        evidence.control_id == "CTL-OR-3"
        and evidence.evidence_type == "EV-BCP"
        for evidence in estate.evidence
    )
