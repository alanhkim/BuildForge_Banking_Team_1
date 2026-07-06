"""
Tests for the Regulation Interpreter contracts, catalog, and validation.

Tests tasks 1-4:
- interpreter-contracts
- interpreter-catalog-fixture
- interpreter-fallback
- interpreter-schema-validation
"""
import pytest
from regimpact.contracts import (
    InterpretRequest,
    InterpretResponse,
    Obligation,
    ValidationError,
    InvalidObligationError,
    InvalidThemeError,
    InvalidMaturityError,
    MissingSourceRefsError,
    KNOWN_THEMES,
)
from regimpact.catalog import CatalogFixture
from regimpact.agents.interpreter import InterpreterAgent


class TestContracts:
    """Test task 1: interpreter-contracts."""

    def test_interpret_request_validation_success(self):
        """Valid request passes validation."""
        request = InterpretRequest(
            regulation_id="REG-DORA",
            change_id="CHG-DORA",
            name="DORA",
            title="Critical ICT Resilience Update",
            offline_mode=True
        )
        request.validate()  # Should not raise

    def test_interpret_request_validation_missing_fields(self):
        """Request missing required fields fails validation."""
        request = InterpretRequest(
            regulation_id="",
            change_id="CHG-DORA",
            name="DORA",
            title="Test"
        )
        with pytest.raises(ValidationError, match="regulation_id is required"):
            request.validate()

    def test_obligation_validation_success(self):
        """Valid obligation passes validation."""
        obl = Obligation(
            id="OBL-DORA-01",
            change_id="CHG-DORA",
            theme="ICT_RESILIENCE",
            summary="Maintain mature ICT continuity.",
            target_maturity=4,
            criticality="Critical",
            affected_data_domain_ids=["DD-PII"],
            source_refs=["catalog:REG-DORA:OBL-DORA-01"]
        )
        obl.validate()  # Should not raise

    def test_obligation_validation_invalid_theme(self):
        """Obligation with unknown theme fails validation."""
        obl = Obligation(
            id="OBL-TEST",
            change_id="CHG-TEST",
            theme="INVALID_THEME",
            summary="Test",
            target_maturity=3,
            criticality="High",
            affected_data_domain_ids=[],
            source_refs=["test"]
        )
        with pytest.raises(InvalidThemeError, match="Unknown theme"):
            obl.validate()

    def test_obligation_validation_maturity_out_of_range(self):
        """Obligation with out-of-range maturity fails validation."""
        obl = Obligation(
            id="OBL-TEST",
            change_id="CHG-TEST",
            theme="ICT_RESILIENCE",
            summary="Test",
            target_maturity=6,  # Invalid: max is 5
            criticality="High",
            affected_data_domain_ids=[],
            source_refs=["test"]
        )
        with pytest.raises(InvalidMaturityError, match="must be between 1 and 5"):
            obl.validate()

    def test_obligation_validation_missing_source_refs(self):
        """Obligation without source_refs fails validation."""
        obl = Obligation(
            id="OBL-TEST",
            change_id="CHG-TEST",
            theme="ICT_RESILIENCE",
            summary="Test",
            target_maturity=3,
            criticality="High",
            affected_data_domain_ids=[],
            source_refs=[]  # Missing source refs
        )
        with pytest.raises(MissingSourceRefsError, match="must include source_refs"):
            obl.validate()

    def test_obligation_validation_multiple_errors(self):
        """Obligation with multiple errors reports them."""
        obl = Obligation(
            id="",  # Missing ID
            change_id="",  # Missing change_id
            theme="",  # Missing theme
            summary="",  # Missing summary
            target_maturity=3,
            criticality="",  # Missing criticality
            affected_data_domain_ids=[],
            source_refs=[]  # Missing source refs
        )
        with pytest.raises((InvalidObligationError, MissingSourceRefsError)):
            obl.validate()

    def test_interpret_response_validation(self):
        """Valid response passes validation."""
        obl = Obligation(
            id="OBL-DORA-01",
            change_id="CHG-DORA",
            theme="ICT_RESILIENCE",
            summary="Test",
            target_maturity=4,
            criticality="Critical",
            affected_data_domain_ids=["DD-PII"],
            source_refs=["catalog:REG-DORA:OBL-DORA-01"]
        )
        response = InterpretResponse(
            regulation_id="REG-DORA",
            change_id="CHG-DORA",
            obligations=[obl],
            mode="deterministic-fallback"
        )
        response.validate()  # Should not raise


class TestCatalog:
    """Test task 2: interpreter-catalog-fixture."""

    def test_catalog_has_dora_entry(self):
        """Catalog contains DORA fixture data."""
        catalog = CatalogFixture()
        assert catalog.has_entry("REG-DORA", "CHG-DORA")

    def test_catalog_get_dora_obligations(self):
        """Catalog returns DORA obligations with correct structure."""
        catalog = CatalogFixture()
        obligations = catalog.get_obligations("REG-DORA", "CHG-DORA")

        assert obligations is not None
        assert len(obligations) == 1

        obl = obligations[0]
        assert obl.id == "OBL-DORA-01"
        assert obl.change_id == "CHG-DORA"
        assert obl.theme == "ICT_RESILIENCE"
        assert obl.target_maturity == 4
        assert obl.criticality == "Critical"
        assert "DD-PII" in obl.affected_data_domain_ids
        assert obl.source_refs == ["catalog:REG-DORA:OBL-DORA-01"]

    def test_catalog_unknown_regulation(self):
        """Catalog returns None for unknown regulations."""
        catalog = CatalogFixture()
        obligations = catalog.get_obligations("REG-UNKNOWN", "CHG-UNKNOWN")
        assert obligations is None

    def test_catalog_list_regulations(self):
        """Catalog lists known regulations."""
        catalog = CatalogFixture()
        regulations = catalog.list_regulations()
        assert "REG-DORA" in regulations


class TestInterpreterFallback:
    """Test task 3: interpreter-fallback."""

    def test_deterministic_dora_interpretation(self):
        """Known DORA input returns structured obligation."""
        agent = InterpreterAgent(use_offline_fallback=True)
        request = InterpretRequest(
            regulation_id="REG-DORA",
            change_id="CHG-DORA",
            name="DORA",
            title="Critical ICT Resilience Update",
            offline_mode=True
        )

        response = agent.interpret(request)

        assert response.regulation_id == "REG-DORA"
        assert response.change_id == "CHG-DORA"
        assert response.mode == "deterministic-fallback"
        assert len(response.obligations) == 1

        obl = response.obligations[0]
        assert obl.id == "OBL-DORA-01"
        assert obl.theme == "ICT_RESILIENCE"
        assert obl.target_maturity == 4

    def test_unknown_regulation_no_hallucination(self):
        """Unknown regulation returns empty obligations with notes."""
        agent = InterpreterAgent(use_offline_fallback=True)
        request = InterpretRequest(
            regulation_id="REG-UNKNOWN",
            change_id="CHG-UNKNOWN",
            name="Unknown",
            title="Unknown regulation",
            offline_mode=True
        )

        response = agent.interpret(request)

        assert response.regulation_id == "REG-UNKNOWN"
        assert response.change_id == "CHG-UNKNOWN"
        assert response.mode == "deterministic-fallback"
        assert len(response.obligations) == 0
        assert any("No catalog entry found" in note for note in response.notes)

    def test_offline_mode_forces_deterministic(self):
        """Offline mode always uses deterministic interpretation."""
        agent = InterpreterAgent(use_offline_fallback=False)
        request = InterpretRequest(
            regulation_id="REG-DORA",
            change_id="CHG-DORA",
            name="DORA",
            title="Critical ICT Resilience Update",
            offline_mode=True  # Force offline
        )

        response = agent.interpret(request)
        assert response.mode == "deterministic-fallback"
        assert len(response.obligations) == 1


class TestSchemaValidation:
    """Test task 4: interpreter-schema-validation."""

    def test_valid_obligation_passes_schema_validation(self):
        """Valid DORA obligation passes all schema checks."""
        agent = InterpreterAgent(use_offline_fallback=True)
        request = InterpretRequest(
            regulation_id="REG-DORA",
            change_id="CHG-DORA",
            name="DORA",
            title="Critical ICT Resilience Update",
            offline_mode=True
        )

        response = agent.interpret(request)
        # If validation failed, interpret() would have raised
        assert len(response.obligations) == 1

    def test_invalid_obligation_rejected(self):
        """Invalid obligations cannot be returned."""
        # Create a bad obligation and validate it directly
        bad_obl = Obligation(
            id="OBL-BAD",
            change_id="CHG-BAD",
            theme="INVALID_THEME",
            summary="Test",
            target_maturity=3,
            criticality="High",
            affected_data_domain_ids=[],
            source_refs=["test"]
        )

        with pytest.raises(InvalidThemeError):
            bad_obl.validate()

    def test_maturity_range_validation(self):
        """Maturity must be in range 1-5."""
        for invalid_maturity in [0, 6, -1, 10]:
            obl = Obligation(
                id="OBL-TEST",
                change_id="CHG-TEST",
                theme="ICT_RESILIENCE",
                summary="Test",
                target_maturity=invalid_maturity,
                criticality="High",
                affected_data_domain_ids=[],
                source_refs=["test"]
            )
            with pytest.raises(InvalidMaturityError):
                obl.validate()

    def test_source_refs_required(self):
        """Source refs are required for traceability."""
        obl = Obligation(
            id="OBL-TEST",
            change_id="CHG-TEST",
            theme="ICT_RESILIENCE",
            summary="Test",
            target_maturity=3,
            criticality="High",
            affected_data_domain_ids=[],
            source_refs=[]
        )
        with pytest.raises(MissingSourceRefsError):
            obl.validate()

    def test_known_themes_validation(self):
        """Only known themes are accepted."""
        for theme in KNOWN_THEMES:
            obl = Obligation(
                id="OBL-TEST",
                change_id="CHG-TEST",
                theme=theme,
                summary="Test",
                target_maturity=3,
                criticality="High",
                affected_data_domain_ids=[],
                source_refs=["test"]
            )
            obl.validate()  # Should not raise

    def test_criticality_validation(self):
        """Criticality must be from valid set."""
        valid_criticalities = ["Critical", "High", "Medium", "Low"]
        for crit in valid_criticalities:
            obl = Obligation(
                id="OBL-TEST",
                change_id="CHG-TEST",
                theme="ICT_RESILIENCE",
                summary="Test",
                target_maturity=3,
                criticality=crit,
                affected_data_domain_ids=[],
                source_refs=["test"]
            )
            obl.validate()  # Should not raise

        # Invalid criticality
        bad_obl = Obligation(
            id="OBL-TEST",
            change_id="CHG-TEST",
            theme="ICT_RESILIENCE",
            summary="Test",
            target_maturity=3,
            criticality="INVALID",
            affected_data_domain_ids=[],
            source_refs=["test"]
        )
        with pytest.raises(InvalidObligationError):
            bad_obl.validate()


class TestMalformedInput:
    """Test handling of malformed and empty input."""

    def test_empty_regulation_id_rejected(self):
        """Empty regulation_id is rejected at request validation."""
        agent = InterpreterAgent(use_offline_fallback=True)
        request = InterpretRequest(
            regulation_id="",
            change_id="CHG-DORA",
            name="DORA",
            title="Test",
            offline_mode=True
        )
        with pytest.raises(ValidationError, match="regulation_id is required"):
            agent.interpret(request)

    def test_empty_change_id_rejected(self):
        """Empty change_id is rejected at request validation."""
        agent = InterpreterAgent(use_offline_fallback=True)
        request = InterpretRequest(
            regulation_id="REG-DORA",
            change_id="",
            name="DORA",
            title="Test",
            offline_mode=True
        )
        with pytest.raises(ValidationError, match="change_id is required"):
            agent.interpret(request)

    def test_empty_name_rejected(self):
        """Empty name is rejected at request validation."""
        agent = InterpreterAgent(use_offline_fallback=True)
        request = InterpretRequest(
            regulation_id="REG-DORA",
            change_id="CHG-DORA",
            name="",
            title="Test",
            offline_mode=True
        )
        with pytest.raises(ValidationError, match="name is required"):
            agent.interpret(request)

    def test_empty_title_rejected(self):
        """Empty title is rejected at request validation."""
        agent = InterpreterAgent(use_offline_fallback=True)
        request = InterpretRequest(
            regulation_id="REG-DORA",
            change_id="CHG-DORA",
            name="DORA",
            title="",
            offline_mode=True
        )
        with pytest.raises(ValidationError, match="title is required"):
            agent.interpret(request)

    def test_whitespace_only_fields_rejected(self):
        """Whitespace-only fields are rejected as empty."""
        request = InterpretRequest(
            regulation_id="   ",
            change_id="CHG-DORA",
            name="DORA",
            title="Test",
            offline_mode=True
        )
        # Whitespace-only fields should be rejected as empty
        with pytest.raises(ValidationError, match="regulation_id is required"):
            request.validate()

    def test_malformed_regulation_id_handled_gracefully(self):
        """Non-existent regulation_id returns empty obligations with notes."""
        agent = InterpreterAgent(use_offline_fallback=True)
        request = InterpretRequest(
            regulation_id="REG-MALFORMED-123!@#",
            change_id="CHG-UNKNOWN",
            name="Malformed",
            title="Malformed Input Test",
            offline_mode=True
        )

        response = agent.interpret(request)

        # Should not crash, but return empty obligations with explanation
        assert response.regulation_id == "REG-MALFORMED-123!@#"
        assert response.mode == "deterministic-fallback"
        assert len(response.obligations) == 0
        assert any("No catalog entry found" in note for note in response.notes)


class TestNetworkFreeOperation:
    """Verify interpreter works with no network access."""

    def test_no_network_required(self):
        """Interpreter works completely offline."""
        agent = InterpreterAgent(use_offline_fallback=True)
        request = InterpretRequest(
            regulation_id="REG-DORA",
            change_id="CHG-DORA",
            name="DORA",
            title="Critical ICT Resilience Update",
            offline_mode=True
        )

        # This should succeed without any network calls
        response = agent.interpret(request)
        assert response.mode == "deterministic-fallback"
        assert len(response.obligations) == 1
