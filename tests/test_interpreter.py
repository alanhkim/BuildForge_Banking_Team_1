"""
Tests for the Regulation Interpreter contracts, catalog, and validation.

Tests tasks 1-4:
- interpreter-contracts
- interpreter-catalog-fixture
- interpreter-fallback
- interpreter-schema-validation
"""
import asyncio
from dataclasses import dataclass
import sys
import types

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
from regimpact.agents.foundry_interpreter import (
    FoundryInterpreterAdapter,
    FoundryInterpreterConfig,
    FoundryInterpreterError,
)


@dataclass(frozen=True)
class FoundryTestSettings:
    foundry_enabled: bool


class StubFoundryAdapter:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.called = False

    def interpret(self, request):
        self.called = True
        if self.error:
            raise self.error
        return self.response


class AsyncStubAgent:
    def __init__(self, response):
        self.response = response

    async def run(self, prompt):
        await asyncio.sleep(0)
        return self.response


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
        assert len(obligations) >= 1

        obl = obligations[0]
        assert obl.id == "OBL-DORA-01"
        assert obl.change_id == "CHG-DORA"
        assert obl.theme == "ICT_RESILIENCE"
        assert obl.target_maturity == 4
        assert obl.criticality == "Critical"
        assert "DD-REF" in obl.affected_data_domain_ids
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
        assert len(response.obligations) >= 1

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
        assert len(response.obligations) >= 1


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
        assert len(response.obligations) == 4

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
        assert len(response.obligations) == 4


class TestFoundryAdapterFallbacks:
    """Focused tests for the optional Foundry interpreter boundary."""

    def _request(self, offline_mode=False):
        return InterpretRequest(
            regulation_id="REG-DORA",
            change_id="CHG-DORA",
            name="DORA",
            title="Critical ICT Resilience Update",
            source_text="ICT resilience requirements",
            offline_mode=offline_mode,
        )

    def _foundry_response(self):
        return InterpretResponse(
            regulation_id="REG-DORA",
            change_id="CHG-DORA",
            obligations=[
                Obligation(
                    id="OBL-LIVE-01",
                    change_id="CHG-DORA",
                    theme="ICT_RESILIENCE",
                    summary="Maintain resilient ICT services.",
                    target_maturity=4,
                    criticality="High",
                    affected_data_domain_ids=["DD-REF"],
                    source_refs=["source:live:1"],
                )
            ],
            mode="foundry-model",
            notes=["live"],
        )

    def test_disabled_foundry_falls_back(self):
        """Foundry disabled by settings uses deterministic catalog fallback."""
        adapter = StubFoundryAdapter(response=self._foundry_response())
        agent = InterpreterAgent(
            use_offline_fallback=False,
            foundry_adapter=adapter,
            app_settings=FoundryTestSettings(foundry_enabled=False),
        )

        response = agent.interpret(self._request())

        assert response.mode == "deterministic-fallback"
        assert not adapter.called

    def test_offline_mode_falls_back_even_when_foundry_enabled(self):
        """Request offline_mode prevents live adapter usage."""
        adapter = StubFoundryAdapter(response=self._foundry_response())
        agent = InterpreterAgent(
            use_offline_fallback=False,
            foundry_adapter=adapter,
            app_settings=FoundryTestSettings(foundry_enabled=True),
        )

        response = agent.interpret(self._request(offline_mode=True))

        assert response.mode == "deterministic-fallback"
        assert not adapter.called

    def test_missing_optional_dependencies_fall_back(self):
        """Import/setup failures in the optional adapter do not escape."""
        adapter = StubFoundryAdapter(
            error=FoundryInterpreterError("Optional Foundry dependencies are not installed")
        )
        agent = InterpreterAgent(
            use_offline_fallback=False,
            foundry_adapter=adapter,
            app_settings=FoundryTestSettings(foundry_enabled=True),
        )

        response = agent.interpret(self._request())

        assert response.mode == "deterministic-fallback"
        assert adapter.called
        assert any("Foundry unavailable" in note for note in response.notes)

    def test_runtime_adapter_error_falls_back(self):
        """Auth/client/model runtime failures are treated as adapter failures."""
        adapter = StubFoundryAdapter(error=RuntimeError("DefaultAzureCredential failed"))
        agent = InterpreterAgent(
            use_offline_fallback=False,
            foundry_adapter=adapter,
            app_settings=FoundryTestSettings(foundry_enabled=True),
        )

        response = agent.interpret(self._request())

        assert response.mode == "deterministic-fallback"
        assert len(response.obligations) == 4

    def test_azure_sdk_error_falls_back(self, monkeypatch):
        """AzureError subclasses from optional SDKs trigger deterministic fallback."""
        class FakeAzureError(Exception):
            pass

        azure_module = types.ModuleType("azure")
        azure_core_module = types.ModuleType("azure.core")
        azure_exceptions_module = types.ModuleType("azure.core.exceptions")
        azure_exceptions_module.AzureError = FakeAzureError
        monkeypatch.setitem(sys.modules, "azure", azure_module)
        monkeypatch.setitem(sys.modules, "azure.core", azure_core_module)
        monkeypatch.setitem(
            sys.modules,
            "azure.core.exceptions",
            azure_exceptions_module,
        )
        adapter = StubFoundryAdapter(error=FakeAzureError("auth failed"))
        agent = InterpreterAgent(
            use_offline_fallback=False,
            foundry_adapter=adapter,
            app_settings=FoundryTestSettings(foundry_enabled=True),
        )

        response = agent.interpret(self._request())

        assert response.mode == "deterministic-fallback"
        assert len(response.obligations) == 4

    def test_malformed_response_falls_back(self):
        """Malformed model JSON falls back to deterministic interpretation."""
        adapter = FoundryInterpreterAdapter(agent=StubFoundryAdapter(response="not json"))
        agent = InterpreterAgent(
            use_offline_fallback=False,
            foundry_adapter=adapter,
            app_settings=FoundryTestSettings(foundry_enabled=True),
        )

        response = agent.interpret(self._request())

        assert response.mode == "deterministic-fallback"
        assert len(response.obligations) == 4

    def test_invalid_obligation_falls_back(self):
        """Contract-invalid model obligations fall back to catalog output."""
        payload = {
            "regulation_id": "REG-DORA",
            "change_id": "CHG-DORA",
            "obligations": [
                {
                    "id": "OBL-BAD",
                    "change_id": "CHG-DORA",
                    "theme": "ICT_RESILIENCE",
                    "summary": "Missing source refs.",
                    "target_maturity": 4,
                    "criticality": "High",
                    "affected_data_domain_ids": ["DD-REF"],
                    "source_refs": [],
                }
            ],
        }
        adapter = FoundryInterpreterAdapter(agent=StubFoundryAdapter(response=payload))
        agent = InterpreterAgent(
            use_offline_fallback=False,
            foundry_adapter=adapter,
            app_settings=FoundryTestSettings(foundry_enabled=True),
        )

        response = agent.interpret(self._request())

        assert response.mode == "deterministic-fallback"
        assert response.obligations[0].source_refs == ["catalog:REG-DORA:OBL-DORA-01"]

    def test_string_list_fields_fall_back(self):
        """Model list fields must be real lists, not strings."""
        payload = {
            "regulation_id": "REG-DORA",
            "change_id": "CHG-DORA",
            "obligations": [
                {
                    "id": "OBL-LIVE-01",
                    "change_id": "CHG-DORA",
                    "theme": "ICT_RESILIENCE",
                    "summary": "Maintain resilient ICT services.",
                    "target_maturity": 4,
                    "criticality": "High",
                    "affected_data_domain_ids": "DD-REF",
                    "source_refs": "source:live:1",
                }
            ],
        }
        adapter = FoundryInterpreterAdapter(agent=StubFoundryAdapter(response=payload))
        agent = InterpreterAgent(
            use_offline_fallback=False,
            foundry_adapter=adapter,
            app_settings=FoundryTestSettings(foundry_enabled=True),
        )

        response = agent.interpret(self._request())

        assert response.mode == "deterministic-fallback"
        assert response.obligations[0].source_refs == ["catalog:REG-DORA:OBL-DORA-01"]

    def test_boolean_target_maturity_falls_back(self):
        """Boolean maturity values are not valid model integers."""
        payload = {
            "regulation_id": "REG-DORA",
            "change_id": "CHG-DORA",
            "obligations": [
                {
                    "id": "OBL-LIVE-01",
                    "change_id": "CHG-DORA",
                    "theme": "ICT_RESILIENCE",
                    "summary": "Maintain resilient ICT services.",
                    "target_maturity": True,
                    "criticality": "High",
                    "affected_data_domain_ids": ["DD-REF"],
                    "source_refs": ["source:live:1"],
                }
            ],
        }
        adapter = FoundryInterpreterAdapter(agent=StubFoundryAdapter(response=payload))
        agent = InterpreterAgent(
            use_offline_fallback=False,
            foundry_adapter=adapter,
            app_settings=FoundryTestSettings(foundry_enabled=True),
        )

        response = agent.interpret(self._request())

        assert response.mode == "deterministic-fallback"
        assert response.obligations[0].target_maturity == 4

    def test_missing_obligations_field_falls_back(self):
        """Model payloads must include all required top-level fields."""
        payload = {
            "regulation_id": "REG-DORA",
            "change_id": "CHG-DORA",
        }
        adapter = FoundryInterpreterAdapter(agent=StubFoundryAdapter(response=payload))
        agent = InterpreterAgent(
            use_offline_fallback=False,
            foundry_adapter=adapter,
            app_settings=FoundryTestSettings(foundry_enabled=True),
        )

        response = agent.interpret(self._request())

        assert response.mode == "deterministic-fallback"
        assert len(response.obligations) == 4

    def test_missing_obligation_required_field_falls_back(self):
        """Each Foundry obligation must include all contract-required fields."""
        payload = {
            "regulation_id": "REG-DORA",
            "change_id": "CHG-DORA",
            "obligations": [
                {
                    "id": "OBL-LIVE-01",
                    "theme": "ICT_RESILIENCE",
                    "summary": "Maintain resilient ICT services.",
                    "target_maturity": 4,
                    "criticality": "High",
                    "source_refs": ["source:live:1"],
                }
            ],
        }
        adapter = FoundryInterpreterAdapter(agent=StubFoundryAdapter(response=payload))
        agent = InterpreterAgent(
            use_offline_fallback=False,
            foundry_adapter=adapter,
            app_settings=FoundryTestSettings(foundry_enabled=True),
        )

        response = agent.interpret(self._request())

        assert response.mode == "deterministic-fallback"
        assert len(response.obligations) == 4

    def test_response_identity_mismatch_falls_back(self):
        """Foundry cannot change the requested regulation or change identity."""
        payload = {
            "regulation_id": "REG-OTHER",
            "change_id": "CHG-OTHER",
            "obligations": [
                {
                    "id": "OBL-OTHER-01",
                    "change_id": "CHG-OTHER",
                    "theme": "ICT_RESILIENCE",
                    "summary": "Maintain resilient ICT services.",
                    "target_maturity": 4,
                    "criticality": "High",
                    "affected_data_domain_ids": ["DD-REF"],
                    "source_refs": ["source:other:1"],
                }
            ],
        }
        adapter = FoundryInterpreterAdapter(agent=StubFoundryAdapter(response=payload))
        agent = InterpreterAgent(
            use_offline_fallback=False,
            foundry_adapter=adapter,
            app_settings=FoundryTestSettings(foundry_enabled=True),
        )

        response = agent.interpret(self._request())

        assert response.regulation_id == "REG-DORA"
        assert response.change_id == "CHG-DORA"
        assert response.mode == "deterministic-fallback"

    def test_foundry_agent_constructor_uses_entra_client_shape(self, monkeypatch):
        """The live path uses Agent Framework + Entra credential constructor args."""
        calls = {}

        class FakeCredential:
            pass

        class FakeDefaultAzureCredential:
            def __new__(cls):
                calls["credential_created"] = True
                return FakeCredential()

        class FakeFoundryChatClient:
            def __init__(self, **kwargs):
                calls["client_kwargs"] = kwargs

        class FakeAgent:
            def __init__(self, **kwargs):
                calls["agent_kwargs"] = kwargs

        agent_framework_module = types.ModuleType("agent_framework")
        agent_framework_module.Agent = FakeAgent
        foundry_module = types.ModuleType("agent_framework.foundry")
        foundry_module.FoundryChatClient = FakeFoundryChatClient
        azure_identity_module = types.ModuleType("azure.identity")
        azure_identity_module.DefaultAzureCredential = FakeDefaultAzureCredential
        monkeypatch.setitem(sys.modules, "agent_framework", agent_framework_module)
        monkeypatch.setitem(sys.modules, "agent_framework.foundry", foundry_module)
        monkeypatch.setitem(sys.modules, "azure.identity", azure_identity_module)

        adapter = FoundryInterpreterAdapter(
            config=FoundryInterpreterConfig(
                project_endpoint="https://example.services.ai.azure.com/api/projects/demo",
                model_deployment_name="gpt-4o",
                api_version="2025-05-01-preview",
            )
        )

        adapter._build_agent()

        assert calls["credential_created"]
        assert calls["client_kwargs"] == {
            "project_endpoint": "https://example.services.ai.azure.com/api/projects/demo",
            "credential": calls["client_kwargs"]["credential"],
            "model": "gpt-4o",
        }
        assert isinstance(calls["client_kwargs"]["credential"], FakeCredential)
        assert set(calls["agent_kwargs"]) == {"client", "instructions"}

    def test_async_foundry_response_mode(self):
        """Awaitable Agent Framework results are resolved before parsing."""
        payload = {
            "regulation_id": "REG-DORA",
            "change_id": "CHG-DORA",
            "obligations": [
                {
                    "id": "OBL-LIVE-ASYNC-01",
                    "change_id": "CHG-DORA",
                    "theme": "ICT_RESILIENCE",
                    "summary": "Maintain resilient ICT services.",
                    "target_maturity": 4,
                    "criticality": "High",
                    "affected_data_domain_ids": ["DD-REF"],
                    "source_refs": ["source:live:async:1"],
                }
            ],
            "notes": ["async"],
        }
        adapter = FoundryInterpreterAdapter(agent=AsyncStubAgent(response=payload))
        agent = InterpreterAgent(
            use_offline_fallback=False,
            foundry_adapter=adapter,
            app_settings=FoundryTestSettings(foundry_enabled=True),
        )

        response = agent.interpret(self._request())

        assert response.mode == "foundry-model"
        assert response.obligations[0].id == "OBL-LIVE-ASYNC-01"

    @pytest.mark.asyncio
    async def test_async_foundry_response_mode_inside_running_loop(self):
        """Sync interpreter calls can resolve awaitables even under an async host."""
        payload = {
            "regulation_id": "REG-DORA",
            "change_id": "CHG-DORA",
            "obligations": [
                {
                    "id": "OBL-LIVE-LOOP-01",
                    "change_id": "CHG-DORA",
                    "theme": "ICT_RESILIENCE",
                    "summary": "Maintain resilient ICT services.",
                    "target_maturity": 4,
                    "criticality": "High",
                    "affected_data_domain_ids": ["DD-REF"],
                    "source_refs": ["source:live:loop:1"],
                }
            ],
            "notes": ["loop"],
        }
        adapter = FoundryInterpreterAdapter(agent=AsyncStubAgent(response=payload))
        agent = InterpreterAgent(
            use_offline_fallback=False,
            foundry_adapter=adapter,
            app_settings=FoundryTestSettings(foundry_enabled=True),
        )

        response = agent.interpret(self._request())

        assert response.mode == "foundry-model"
        assert response.obligations[0].id == "OBL-LIVE-LOOP-01"

    def test_successful_foundry_response_mode(self):
        """Valid adapter output is returned as foundry-model."""
        agent = InterpreterAgent(
            use_offline_fallback=False,
            foundry_adapter=StubFoundryAdapter(response=self._foundry_response()),
            app_settings=FoundryTestSettings(foundry_enabled=True),
        )

        response = agent.interpret(self._request())

        assert response.mode == "foundry-model"
        assert response.obligations[0].id == "OBL-LIVE-01"
