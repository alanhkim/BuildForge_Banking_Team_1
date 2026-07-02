from regimpact.generator import build_estate


def test_build_estate_creates_correlated_dora_example():
    estate = build_estate()

    assert estate.regulations[0].id == "REG-DORA"
    assert estate.changes[0].regulation_id == "REG-DORA"
    assert estate.obligations[0].change_id == "CHG-DORA"
    assert estate.controls[0].capability_id == "CAP-RES"
    assert estate.controls[0].evidence_ids == ["EV-BCP"]
