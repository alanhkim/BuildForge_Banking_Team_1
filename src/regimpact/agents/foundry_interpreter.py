"""Microsoft Agent Framework / Foundry boundary for interpretation."""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import inspect
import json
from dataclasses import dataclass
from importlib import import_module
from typing import Any

from ..contracts import InterpretRequest, InterpretResponse, Obligation, ValidationError
from ..settings import Settings, settings

_BASE_FOUNDRY_RUNTIME_ERRORS = (
    AttributeError,
    ImportError,
    RuntimeError,
    TypeError,
    ValueError,
)


def foundry_runtime_error_types() -> tuple[type[BaseException], ...]:
    """Return explicit exception types from optional Foundry dependencies."""
    try:
        azure_core_exceptions = import_module("azure.core.exceptions")
    except ImportError:
        return _BASE_FOUNDRY_RUNTIME_ERRORS
    azure_error = getattr(azure_core_exceptions, "AzureError", None)
    if isinstance(azure_error, type):
        return _BASE_FOUNDRY_RUNTIME_ERRORS + (azure_error,)
    return _BASE_FOUNDRY_RUNTIME_ERRORS


SYSTEM_PROMPT = """You are the Regulation Interpreter for a banking regulatory impact assessment system.

Convert regulatory change text into structured obligations. Extract only obligations supported by the supplied source text or catalog metadata. Do not invent regulations, controls, systems, products, technologies, or evidence.

Return only JSON matching this schema: {"regulation_id": string, "change_id": string, "obligations": [{"id": string, "change_id": string, "theme": string, "summary": string, "target_maturity": integer, "criticality": "Critical|High|Medium|Low", "affected_data_domain_ids": [string], "source_refs": [string], "notes": [string]}], "notes": [string]}.

If the source text is incomplete, mark uncertainty in notes rather than guessing. Authentication is handled by the host. Never request, emit, or rely on API keys."""


class FoundryInterpreterError(Exception):
    """Raised when the Foundry path cannot produce a valid response."""


@dataclass(frozen=True)
class FoundryInterpreterConfig:
    """Configuration needed for Entra-authenticated Foundry model calls."""

    project_endpoint: str
    model_deployment_name: str
    api_version: str

    @classmethod
    def from_settings(cls, config: Settings = settings) -> "FoundryInterpreterConfig":
        """Build adapter config from runtime settings."""
        return cls(
            project_endpoint=config.foundry_project_endpoint,
            model_deployment_name=config.foundry_model_deployment_name,
            api_version=config.foundry_api_version,
        )

    def validate(self) -> None:
        """Validate required Foundry settings."""
        missing = [
            name
            for name, value in (
                ("FOUNDRY_PROJECT_ENDPOINT", self.project_endpoint),
                ("FOUNDRY_MODEL_DEPLOYMENT_NAME", self.model_deployment_name),
            )
            if not value
        ]
        if missing:
            raise FoundryInterpreterError(
                f"Missing Foundry configuration: {', '.join(missing)}"
            )


class FoundryInterpreterAdapter:
    """Live interpreter adapter using Agent Framework with Entra auth only."""

    def __init__(
        self,
        config: FoundryInterpreterConfig | None = None,
        credential: Any | None = None,
        client: Any | None = None,
        agent: Any | None = None,
    ):
        self.config = config or FoundryInterpreterConfig.from_settings()
        self._credential = credential
        self._client = client
        self._agent = agent

    def interpret(self, request: InterpretRequest) -> InterpretResponse:
        """Call Foundry and return a schema-valid interpreter response."""
        raw_response = self._invoke_model(request)
        payload = _parse_json_payload(raw_response)
        response = _response_from_payload(payload, request)
        try:
            response.validate()
        except ValidationError as exc:
            raise FoundryInterpreterError("Foundry response failed contract validation") from exc
        return response

    def _invoke_model(self, request: InterpretRequest) -> Any:
        """Invoke the optional Agent Framework client."""
        try:
            agent = self._agent or self._build_agent()
        except foundry_runtime_error_types() as exc:
            raise FoundryInterpreterError("Foundry agent setup failed") from exc

        prompt = _build_user_prompt(request)

        for method_name in ("run", "invoke", "chat", "complete"):
            method = getattr(agent, method_name, None)
            if callable(method):
                try:
                    return _resolve_response(method(prompt))
                except foundry_runtime_error_types() as exc:
                    raise FoundryInterpreterError("Foundry model invocation failed") from exc

        raise FoundryInterpreterError("Agent Framework agent has no supported call method")

    def _build_agent(self) -> Any:
        """Create an Agent Framework agent backed by a Foundry chat client."""
        self.config.validate()
        try:
            agent_framework = import_module("agent_framework")
            foundry_module = import_module("agent_framework.foundry")
            azure_identity = import_module("azure.identity")
        except ImportError as exc:
            raise FoundryInterpreterError(
                "Optional Foundry dependencies are not installed"
            ) from exc

        agent_cls = getattr(agent_framework, "Agent")
        client_cls = getattr(foundry_module, "FoundryChatClient")
        credential = self._credential or azure_identity.DefaultAzureCredential()
        client = self._client or client_cls(
            project_endpoint=self.config.project_endpoint,
            credential=credential,
            model=self.config.model_deployment_name,
        )
        return agent_cls(
            client=client,
            instructions=SYSTEM_PROMPT,
        )


def _resolve_response(response: Any) -> Any:
    """Resolve sync or async Agent Framework responses."""
    if not inspect.isawaitable(response):
        return response
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(response)
    with ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(lambda: asyncio.run(response)).result()


def _build_user_prompt(request: InterpretRequest) -> str:
    """Build a constrained JSON-only model prompt."""
    payload = {
        "regulation_id": request.regulation_id,
        "change_id": request.change_id,
        "name": request.name,
        "title": request.title,
        "source_text": request.source_text or "",
        "source_path": request.source_path or "",
    }
    return (
        "Interpret this regulatory change and return only the response JSON:\n"
        f"{json.dumps(payload, sort_keys=True)}"
    )


def _parse_json_payload(raw_response: Any) -> dict[str, Any]:
    """Extract and decode JSON from common Agent Framework response shapes."""
    content = raw_response
    for attr in ("content", "text", "message", "output"):
        if hasattr(content, attr):
            content = getattr(content, attr)

    if isinstance(content, dict):
        return content

    if isinstance(content, list) and content:
        return _parse_json_payload(content[0])

    if not isinstance(content, str):
        content = str(content)

    text = content.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:].strip()

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise FoundryInterpreterError("Foundry returned malformed JSON") from exc

    if not isinstance(parsed, dict):
        raise FoundryInterpreterError("Foundry response JSON must be an object")
    return parsed


def _response_from_payload(
    payload: dict[str, Any],
    request: InterpretRequest,
) -> InterpretResponse:
    """Convert decoded JSON into typed contracts."""
    missing = {
        key
        for key in ("regulation_id", "change_id", "obligations")
        if key not in payload
    }
    if missing:
        raise FoundryInterpreterError(
            f"Foundry response missing required field(s): {', '.join(sorted(missing))}"
        )
    if not isinstance(payload["obligations"], list):
        raise FoundryInterpreterError("Foundry response obligations must be a list")
    if not isinstance(payload["regulation_id"], str):
        raise FoundryInterpreterError("Foundry response regulation_id must be a string")
    if not isinstance(payload["change_id"], str):
        raise FoundryInterpreterError("Foundry response change_id must be a string")
    if payload["regulation_id"] != request.regulation_id:
        raise FoundryInterpreterError("Foundry response regulation_id does not match request")
    if payload["change_id"] != request.change_id:
        raise FoundryInterpreterError("Foundry response change_id does not match request")
    notes = payload.get("notes", [])
    if not isinstance(notes, list) or not all(isinstance(note, str) for note in notes):
        raise FoundryInterpreterError("Foundry response notes must be a list of strings")

    try:
        obligations = [
            _obligation_from_payload_item(item, request)
            for item in payload.get("obligations", [])
        ]
    except (KeyError, TypeError) as exc:
        raise FoundryInterpreterError("Foundry response does not match contract") from exc

    return InterpretResponse(
        regulation_id=str(payload.get("regulation_id", request.regulation_id)),
        change_id=str(payload.get("change_id", request.change_id)),
        obligations=obligations,
        mode="foundry-model",
        notes=notes,
    )


def _obligation_from_payload_item(
    item: Any,
    request: InterpretRequest,
) -> Obligation:
    """Convert one model obligation after validating primitive field types."""
    if not isinstance(item, dict):
        raise FoundryInterpreterError("Foundry obligation must be an object")

    required_fields = {
        "id",
        "change_id",
        "theme",
        "summary",
        "target_maturity",
        "criticality",
        "affected_data_domain_ids",
        "source_refs",
    }
    missing = required_fields - set(item)
    if missing:
        raise FoundryInterpreterError(
            "Foundry obligation missing required field(s): "
            f"{', '.join(sorted(missing))}"
        )

    required_string_fields = ("id", "theme", "summary", "criticality")
    for field_name in required_string_fields:
        if not isinstance(item.get(field_name), str):
            raise FoundryInterpreterError(
                f"Foundry obligation {field_name} must be a string"
            )

    target_maturity = item["target_maturity"]
    if type(target_maturity) is not int or not 1 <= target_maturity <= 5:
        raise FoundryInterpreterError(
            "Foundry obligation target_maturity must be an integer from 1 to 5"
        )

    change_id = item.get("change_id", request.change_id)
    if not isinstance(change_id, str):
        raise FoundryInterpreterError("Foundry obligation change_id must be a string")
    if change_id != request.change_id:
        raise FoundryInterpreterError(
            "Foundry obligation change_id does not match request"
        )

    affected_data_domain_ids = item.get("affected_data_domain_ids", [])
    source_refs = item.get("source_refs", [])
    notes = item.get("notes", [])
    for field_name, value in (
        ("affected_data_domain_ids", affected_data_domain_ids),
        ("source_refs", source_refs),
        ("notes", notes),
    ):
        if not isinstance(value, list) or not all(isinstance(entry, str) for entry in value):
            raise FoundryInterpreterError(
                f"Foundry obligation {field_name} must be a list of strings"
            )

    return Obligation(
        id=item["id"],
        change_id=change_id,
        theme=item["theme"],
        summary=item["summary"],
        target_maturity=target_maturity,
        criticality=item["criticality"],
        affected_data_domain_ids=affected_data_domain_ids,
        source_refs=source_refs,
        notes=notes,
    )
