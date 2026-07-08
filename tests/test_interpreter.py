"""Tests for Regulation Interpreter contracts and Foundry boundaries."""
import asyncio
from dataclasses import dataclass
import sys
import types

import pytest

from regimpact.agents.foundry_interpreter import (
    FoundryInterpreterAdapter,
    FoundryInterpreterConfig,
    FoundryInterpreterError,
)
from regimpact.agents.interpreter import InterpreterAgent
from regimpact.catalog import CatalogFixture
from regimpact.contracts import (
    KNOWN_THEMES,
    InterpretRequest,
    InterpretResponse,
    InvalidMaturityError,
    InvalidObligationError,
    InvalidThemeError,
    MissingSourceRefsError,
    Obligation,
    ValidationError,
)


@dataclass(frozen=True)
class FoundryTestSettings:
    foundry_project_endpoint: str = "https://example.services.ai.azure.com/api/projects/demo"
    foundry_model_deployment_name: str = "gpt-4o"
    foundry_api_version: str = "2025-05-01-preview"


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

    def run(self, prompt):
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


def request() -> InterpretRequest:
    return InterpretRequest(
        regulation_id="REG-DORA",
        change_id="CHG-DORA",
        name="DORA",
        title="Critical ICT Resilience Update",
        source_text="ICT resilience requirements",
    )


def foundry_response() -> InterpretResponse:
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
        notes=["live"],
    )


class TestContracts:
    """Interpreter request/response contract validation."""

    def test_interpret_request_validation_success(self):
        request().validate()

    def test_interpret_request_validation_missing_fields(self):
        bad_request = InterpretRequest(
            regulation_id="",
            change_id="CHG-DORA",
            name="DORA",
            title="Test",
        )
        with pytest.raises(ValidationError, match="regulation_id is required"):
            bad_request.validate()

    def test_interpret_request_rejects_whitespace_only_fields(self):
        bad_request = InterpretRequest(
            regulation_id="   ",
            change_id="CHG-DORA",
            name="DORA",
            title="Test",
        )
        with pytest.raises(ValidationError, match="regulation_id is required"):
            bad_request.validate()

    def test_obligation_validation_success(self):
        obligation = Obligation(
            id="OBL-DORA-01",
            change_id="CHG-DORA",
            theme="ICT_RESILIENCE",
            summary="Maintain mature ICT continuity.",
            target_maturity=4,
            criticality="Critical",
            affected_data_domain_ids=["DD-PII"],
            source_refs=["catalog:REG-DORA:OBL-DORA-01"],
        )
        obligation.validate()

    def test_obligation_validation_invalid_theme(self):
        obligation = Obligation(
            id="OBL-TEST",
            change_id="CHG-TEST",
            theme="INVALID_THEME",
            summary="Test",
            target_maturity=3,
            criticality="High",
            affected_data_domain_ids=[],
            source_refs=["test"],
        )
        with pytest.raises(InvalidThemeError, match="Unknown theme"):
            obligation.validate()

    def test_obligation_validation_maturity_out_of_range(self):
        obligation = Obligation(
            id="OBL-TEST",
            change_id="CHG-TEST",
            theme="ICT_RESILIENCE",
            summary="Test",
            target_maturity=6,
            criticality="High",
            affected_data_domain_ids=[],
            source_refs=["test"],
        )
        with pytest.raises(InvalidMaturityError, match="must be between 1 and 5"):
            obligation.validate()

    def test_obligation_validation_missing_source_refs(self):
        obligation = Obligation(
            id="OBL-TEST",
            change_id="CHG-TEST",
            theme="ICT_RESILIENCE",
            summary="Test",
            target_maturity=3,
            criticality="High",
            affected_data_domain_ids=[],
            source_refs=[],
        )
        with pytest.raises(MissingSourceRefsError, match="must include source_refs"):
            obligation.validate()

    def test_obligation_validation_multiple_errors(self):
        obligation = Obligation(
            id="",
            change_id="",
            theme="",
            summary="",
            target_maturity=3,
            criticality="",
            affected_data_domain_ids=[],
            source_refs=[],
        )
        with pytest.raises((InvalidObligationError, MissingSourceRefsError)):
            obligation.validate()

    def test_interpret_response_validation(self):
        response = foundry_response()
        response.validate()
        assert response.mode == "foundry-model"


class TestCatalog:
    """Catalog remains available as grounding/test data, not as fallback behavior."""

    def test_catalog_has_dora_entry(self):
        catalog = CatalogFixture()
        assert catalog.has_entry("REG-DORA", "CHG-DORA")

    def test_catalog_get_dora_obligations(self):
        catalog = CatalogFixture()
        obligations = catalog.get_obligations("REG-DORA", "CHG-DORA")

        assert obligations is not None
        assert len(obligations) >= 1

        obligation = obligations[0]
        assert obligation.id == "OBL-DORA-01"
        assert obligation.change_id == "CHG-DORA"
        assert obligation.theme == "ICT_RESILIENCE"
        assert obligation.target_maturity == 4
        assert obligation.criticality == "Critical"
        assert "DD-REF" in obligation.affected_data_domain_ids
        assert obligation.source_refs == ["catalog:REG-DORA:OBL-DORA-01"]

    def test_catalog_unknown_regulation(self):
        catalog = CatalogFixture()
        assert catalog.get_obligations("REG-UNKNOWN", "CHG-UNKNOWN") is None

    def test_catalog_list_regulations(self):
        catalog = CatalogFixture()
        assert "REG-DORA" in catalog.list_regulations()


class TestSchemaValidation:
    """Schema validation rejects malformed obligations and model output."""

    def test_maturity_range_validation(self):
        for invalid_maturity in [0, 6, -1, 10]:
            obligation = Obligation(
                id="OBL-TEST",
                change_id="CHG-TEST",
                theme="ICT_RESILIENCE",
                summary="Test",
                target_maturity=invalid_maturity,
                criticality="High",
                affected_data_domain_ids=[],
                source_refs=["test"],
            )
            with pytest.raises(InvalidMaturityError):
                obligation.validate()

    def test_source_refs_required(self):
        obligation = Obligation(
            id="OBL-TEST",
            change_id="CHG-TEST",
            theme="ICT_RESILIENCE",
            summary="Test",
            target_maturity=3,
            criticality="High",
            affected_data_domain_ids=[],
            source_refs=[],
        )
        with pytest.raises(MissingSourceRefsError):
            obligation.validate()

    def test_known_themes_validation(self):
        for theme in KNOWN_THEMES:
            obligation = Obligation(
                id="OBL-TEST",
                change_id="CHG-TEST",
                theme=theme,
                summary="Test",
                target_maturity=3,
                criticality="High",
                affected_data_domain_ids=[],
                source_refs=["test"],
            )
            obligation.validate()

    def test_criticality_validation(self):
        for criticality in ["Critical", "High", "Medium", "Low"]:
            obligation = Obligation(
                id="OBL-TEST",
                change_id="CHG-TEST",
                theme="ICT_RESILIENCE",
                summary="Test",
                target_maturity=3,
                criticality=criticality,
                affected_data_domain_ids=[],
                source_refs=["test"],
            )
            obligation.validate()

        bad_obligation = Obligation(
            id="OBL-TEST",
            change_id="CHG-TEST",
            theme="ICT_RESILIENCE",
            summary="Test",
            target_maturity=3,
            criticality="INVALID",
            affected_data_domain_ids=[],
            source_refs=["test"],
        )
        with pytest.raises(InvalidObligationError):
            bad_obligation.validate()


class TestInterpreterFoundryBoundary:
    """Interpreter uses Foundry and surfaces failures explicitly."""

    def test_successful_foundry_response_mode(self):
        agent = InterpreterAgent(foundry_adapter=StubFoundryAdapter(response=foundry_response()))

        response = agent.interpret(request())

        assert response.mode == "foundry-model"
        assert response.obligations[0].id == "OBL-LIVE-01"

    def test_foundry_disabled_does_not_fall_back(self):
        adapter = StubFoundryAdapter(
            error=FoundryInterpreterError("Missing Foundry configuration")
        )
        agent = InterpreterAgent(foundry_adapter=adapter)

        with pytest.raises(FoundryInterpreterError, match="Missing Foundry configuration"):
            agent.interpret(request())

        assert adapter.called

    def test_runtime_adapter_error_is_raised(self):
        agent = InterpreterAgent(
            foundry_adapter=StubFoundryAdapter(
                error=RuntimeError("DefaultAzureCredential failed")
            )
        )

        with pytest.raises(
            FoundryInterpreterError,
            match="Foundry interpreter invocation failed",
        ):
            agent.interpret(request())

    def test_azure_sdk_error_is_raised(self, monkeypatch):
        class FakeAzureError(Exception):
            pass

        azure_module = types.ModuleType("azure")
        azure_core_module = types.ModuleType("azure.core")
        azure_exceptions_module = types.ModuleType("azure.core.exceptions")
        azure_exceptions_module.AzureError = FakeAzureError
        monkeypatch.setitem(sys.modules, "azure", azure_module)
        monkeypatch.setitem(sys.modules, "azure.core", azure_core_module)
        monkeypatch.setitem(sys.modules, "azure.core.exceptions", azure_exceptions_module)
        agent = InterpreterAgent(
            foundry_adapter=StubFoundryAdapter(error=FakeAzureError("auth failed"))
        )

        with pytest.raises(
            FoundryInterpreterError,
            match="Foundry interpreter invocation failed",
        ):
            agent.interpret(request())

    def test_missing_optional_dependencies_are_raised(self):
        agent = InterpreterAgent(
            foundry_adapter=StubFoundryAdapter(
                error=FoundryInterpreterError(
                    "Optional Foundry dependencies are not installed"
                )
            )
        )

        with pytest.raises(
            FoundryInterpreterError,
            match="Optional Foundry dependencies are not installed",
        ):
            agent.interpret(request())

    def test_malformed_response_is_raised(self):
        adapter = FoundryInterpreterAdapter(agent=StubFoundryAdapter(response="not json"))
        agent = InterpreterAgent(foundry_adapter=adapter)

        with pytest.raises(FoundryInterpreterError, match="malformed JSON"):
            agent.interpret(request())

    def test_invalid_obligation_is_raised(self):
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
        agent = InterpreterAgent(foundry_adapter=adapter)

        with pytest.raises(
            FoundryInterpreterError,
            match="failed contract validation",
        ):
            agent.interpret(request())

    def test_string_list_fields_are_raised(self):
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
        agent = InterpreterAgent(foundry_adapter=adapter)

        with pytest.raises(FoundryInterpreterError, match="must be a list of strings"):
            agent.interpret(request())

    def test_boolean_target_maturity_is_raised(self):
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
        agent = InterpreterAgent(foundry_adapter=adapter)

        with pytest.raises(FoundryInterpreterError, match="target_maturity"):
            agent.interpret(request())

    def test_missing_obligations_field_is_raised(self):
        adapter = FoundryInterpreterAdapter(
            agent=StubFoundryAdapter(
                response={"regulation_id": "REG-DORA", "change_id": "CHG-DORA"}
            )
        )
        agent = InterpreterAgent(foundry_adapter=adapter)

        with pytest.raises(FoundryInterpreterError, match="missing required field"):
            agent.interpret(request())

    def test_missing_obligation_required_field_is_raised(self):
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
        agent = InterpreterAgent(foundry_adapter=adapter)

        with pytest.raises(FoundryInterpreterError, match="missing required field"):
            agent.interpret(request())

    def test_response_identity_mismatch_is_raised(self):
        payload = {
            "regulation_id": "REG-OTHER",
            "change_id": "CHG-OTHER",
            "obligations": [],
        }
        adapter = FoundryInterpreterAdapter(agent=StubFoundryAdapter(response=payload))
        agent = InterpreterAgent(foundry_adapter=adapter)

        with pytest.raises(FoundryInterpreterError, match="does not match request"):
            agent.interpret(request())

    def test_foundry_agent_constructor_uses_entra_client_shape(self, monkeypatch):
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
        agent = InterpreterAgent(foundry_adapter=adapter)

        response = agent.interpret(request())

        assert response.mode == "foundry-model"
        assert response.obligations[0].id == "OBL-LIVE-ASYNC-01"

    @pytest.mark.asyncio
    async def test_async_foundry_response_mode_inside_running_loop(self):
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
        agent = InterpreterAgent(foundry_adapter=adapter)

        response = agent.interpret(request())

        assert response.mode == "foundry-model"
        assert response.obligations[0].id == "OBL-LIVE-LOOP-01"
