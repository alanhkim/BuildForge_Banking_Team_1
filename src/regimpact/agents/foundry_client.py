"""Entra-authenticated Foundry agent clients for Fabric-grounded workflows."""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import inspect
import json
import logging
from dataclasses import dataclass
from importlib import import_module
from typing import Any, Literal

from ..contracts import (
    FabricQuestionRequest,
    FabricQuestionResponse,
    SourceReference,
    ToolEvidence,
    ValidationError,
)
from ..settings import Settings, settings
from .foundry_interpreter import foundry_runtime_error_types

logger = logging.getLogger(__name__)


class FoundryAgentError(Exception):
    """Raised when a Foundry agent boundary fails explicitly."""


class FabricDataAgentError(FoundryAgentError):
    """Raised when the Fabric Data Agent route cannot return a valid answer."""


FabricApplicationAgent = Literal[
    "control_mapper",
    "gap_analyst",
    "remediation_planner",
    "score_narrator",
    "lineage",
    "executive_qa",
]


def foundry_agent_error_types() -> tuple[type[BaseException], ...]:
    """Return explicit SDK exception types for Foundry agent invocation."""
    error_types = foundry_runtime_error_types() + (TimeoutError,)
    try:
        openai_module = import_module("openai")
    except ImportError:
        return error_types
    openai_error = getattr(openai_module, "OpenAIError", None)
    if isinstance(openai_error, type):
        return error_types + (openai_error,)
    return error_types


@dataclass(frozen=True)
class FoundryAgentConfig:
    """Configuration for invoking a named Foundry agent."""

    project_endpoint: str
    model_deployment_name: str
    agent_name: str
    agent_version: str
    api_version: str = "2025-05-01-preview"
    timeout_seconds: float = 120

    @classmethod
    def for_fabric_agent(cls, config: Settings = settings) -> "FoundryAgentConfig":
        """Build Fabric-backed Foundry agent config from runtime settings."""
        return cls(
            project_endpoint=config.foundry_project_endpoint,
            model_deployment_name=config.foundry_model_deployment_name,
            agent_name=(
                config.foundry_fabric_agent_name
                or config.foundry_executive_qa_agent_name
            ),
            agent_version=(
                config.foundry_fabric_agent_version
                or config.foundry_executive_qa_agent_version
            ),
            api_version=config.foundry_api_version,
            timeout_seconds=config.foundry_agent_timeout_seconds,
        )

    @classmethod
    def for_application_agent(
        cls,
        agent: FabricApplicationAgent,
        config: Settings = settings,
    ) -> "FoundryAgentConfig":
        """Build config for one purpose-built Fabric application agent."""
        name, version = _application_agent_name_version(agent, config)
        return cls(
            project_endpoint=config.foundry_project_endpoint,
            model_deployment_name=config.foundry_model_deployment_name,
            agent_name=name,
            agent_version=version,
            api_version=config.foundry_api_version,
            timeout_seconds=config.foundry_agent_timeout_seconds,
        )

    def validate(self) -> None:
        """Validate required Foundry agent settings."""
        missing = [
            name
            for name, value in (
                ("FOUNDRY_PROJECT_ENDPOINT", self.project_endpoint),
                ("FOUNDRY_MODEL_DEPLOYMENT_NAME", self.model_deployment_name),
                ("FOUNDRY_FABRIC_AGENT_NAME", self.agent_name),
                ("FOUNDRY_FABRIC_AGENT_VERSION", self.agent_version),
            )
            if not value
        ]
        if missing:
            raise FoundryAgentError(
                f"Missing Foundry agent configuration: {', '.join(missing)}"
            )


@dataclass(frozen=True)
class FoundryAgentRawResponse:
    """Raw response from a Foundry agent invocation."""

    text: str
    agent_name: str
    agent_version: str
    metadata: dict[str, Any]


class FoundryAgentClient:
    """Foundry agent-reference client using Entra auth and injectable runtime."""

    def __init__(
        self,
        config: FoundryAgentConfig | None = None,
        agent: Any | None = None,
        credential: Any | None = None,
    ):
        self.config = config or FoundryAgentConfig.for_fabric_agent()
        self._agent = agent
        self._credential = credential

    def invoke(self, input_text: str) -> FoundryAgentRawResponse:
        """Invoke a Foundry agent and normalize the response text."""
        if not input_text.strip():
            raise FoundryAgentError("input_text is required")

        try:
            agent = self._agent or self._build_agent()
        except foundry_runtime_error_types() as exc:
            raise FoundryAgentError(f"Foundry agent setup failed: {exc}") from exc

        for method_name in ("run", "invoke", "chat", "complete"):
            method = getattr(agent, method_name, None)
            if callable(method):
                try:
                    raw = _resolve_response(method(input_text))
                except foundry_agent_error_types() as exc:
                    logger.error(
                        "[FOUNDRY DEBUG] invoke failed for agent %s v%s via %s: %s",
                        self.config.agent_name,
                        self.config.agent_version,
                        method_name,
                        exc,
                    )
                    raise FoundryAgentError(
                        f"Foundry agent invocation failed: {exc}"
                    ) from exc
                return FoundryAgentRawResponse(
                    text=_extract_text(raw),
                    agent_name=self.config.agent_name,
                    agent_version=self.config.agent_version,
                    metadata=_extract_metadata(raw),
                )

        raise FoundryAgentError("Foundry agent has no supported call method")

    def _build_agent(self) -> Any:
        """Build an Azure AI Projects agent-reference wrapper."""
        self.config.validate()
        try:
            azure_identity = import_module("azure.identity")
            projects_module = import_module("azure.ai.projects")
        except ImportError as exc:
            raise FoundryAgentError("Optional Foundry dependencies are not installed") from exc

        credential = self._credential or azure_identity.DefaultAzureCredential()
        project_client_cls = getattr(projects_module, "AIProjectClient")
        project_client = project_client_cls(
            endpoint=self.config.project_endpoint,
            credential=credential,
        )
        return _OpenAIResponsesAgent(
            openai_client=project_client.get_openai_client(),
            config=self.config,
        )


@dataclass(frozen=True)
class _OpenAIResponsesAgent:
    """Adapter exposing run() over Foundry OpenAI responses agent references."""

    openai_client: Any
    config: FoundryAgentConfig

    def run(self, input_text: str) -> Any:
        """Invoke a Foundry prompt/hosted agent through Responses API."""
        return self.openai_client.responses.create(
            model=self.config.model_deployment_name,
            input=[{"role": "user", "content": input_text}],
            extra_body={
                "agent_reference": {
                    "name": self.config.agent_name,
                    "version": self.config.agent_version,
                    "type": "agent_reference",
                }
            },
            timeout=self.config.timeout_seconds,
        )


@dataclass(frozen=True)
class FabricDataAgentConfig:
    """Configuration for Fabric Data Agent invocation through Foundry."""

    workspace_id: str
    data_agent_id: str
    allowed_sources: tuple[str, ...] = (
        "RegImpactLH",
        "RegImpactSM_V1",
        "RegImpact_Ontology",
    )

    @classmethod
    def from_settings(cls, config: Settings = settings) -> "FabricDataAgentConfig":
        """Build Fabric Data Agent config from settings."""
        return cls(
            workspace_id=config.fabric_workspace_id,
            data_agent_id=config.fabric_data_agent_id,
        )

    def validate(self) -> None:
        """Validate required Fabric Data Agent settings."""
        missing = [
            name
            for name, value in (
                ("FABRIC_WORKSPACE_ID", self.workspace_id),
                ("FABRIC_DATA_AGENT_ID", self.data_agent_id),
            )
            if not value
        ]
        if missing:
            raise FabricDataAgentError(
                f"Missing Fabric Data Agent configuration: {', '.join(missing)}"
            )


class FabricDataAgentClient:
    """Narrow wrapper over the Foundry Fabric Data Agent tool path."""

    def __init__(
        self,
        foundry_client: FoundryAgentClient | None = None,
        config: FabricDataAgentConfig | None = None,
    ):
        self.config = config or FabricDataAgentConfig.from_settings()
        self.foundry_client = foundry_client or FoundryAgentClient()

    @classmethod
    def for_application_agent(
        cls,
        agent: FabricApplicationAgent,
        config: Settings = settings,
        credential: Any | None = None,
    ) -> "FabricDataAgentClient":
        """Build a Fabric client routed to one purpose-built Foundry agent."""
        return cls(
            foundry_client=FoundryAgentClient(
                config=FoundryAgentConfig.for_application_agent(agent, config),
                credential=credential,
            ),
            config=FabricDataAgentConfig.from_settings(config),
        )

    def ask(self, question: str) -> FabricQuestionResponse:
        """Ask a Fabric-grounded question through Foundry and validate the answer."""
        self.config.validate()
        foundry_config = self.foundry_client.config
        request = FabricQuestionRequest(
            question=question,
            agent_name=foundry_config.agent_name,
            agent_version=foundry_config.agent_version,
            workspace_id=self.config.workspace_id,
            data_agent_id=self.config.data_agent_id,
            allowed_sources=list(self.config.allowed_sources),
        )
        request.validate()

        prompt = _fabric_prompt(request)
        try:
            raw_response = self.foundry_client.invoke(prompt)
        except FoundryAgentError as exc:
            raise FabricDataAgentError(
                f"Foundry Fabric Data Agent invocation failed: {exc}"
            ) from exc

        payload = _parse_json_payload(raw_response.text)
        response = _fabric_response_from_payload(payload, request)
        try:
            response.validate()
        except ValidationError as exc:
            raise FabricDataAgentError(
                "Fabric Data Agent response failed contract validation"
            ) from exc
        return response


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


def _extract_text(raw_response: Any) -> str:
    """Extract text from common Agent Framework response shapes."""
    content = raw_response
    for attr in ("output_text", "content", "text", "message", "output"):
        if hasattr(content, attr):
            content = getattr(content, attr)
    if isinstance(content, dict):
        return json.dumps(content)
    if isinstance(content, list) and content:
        return _extract_text(content[0])
    return str(content)


def _extract_metadata(raw_response: Any) -> dict[str, Any]:
    """Extract best-effort response metadata without affecting success."""
    metadata = getattr(raw_response, "metadata", None)
    if isinstance(metadata, dict):
        return metadata
    return {}


def _fabric_prompt(request: FabricQuestionRequest) -> str:
    """Build a JSON-only prompt for Fabric Data Agent responses."""
    return (
        "Use the connected Fabric Data Agent tool. Do not use web search. "
        "Return only JSON with keys: question, answer, citations, tool_evidence, "
        "confidence. Citations must reference Fabric table, view, field, measure, "
        "relationship, or entity IDs. Request: "
        f"{json.dumps(request.__dict__, sort_keys=True)}"
    )


def _application_agent_name_version(
    agent: FabricApplicationAgent,
    config: Settings,
) -> tuple[str, str]:
    agent_settings = {
        "control_mapper": (
            config.foundry_control_mapper_agent_name,
            config.foundry_control_mapper_agent_version,
        ),
        "gap_analyst": (
            config.foundry_gap_analyst_agent_name,
            config.foundry_gap_analyst_agent_version,
        ),
        "remediation_planner": (
            config.foundry_remediation_planner_agent_name,
            config.foundry_remediation_planner_agent_version,
        ),
        "score_narrator": (
            config.foundry_score_narrator_agent_name,
            config.foundry_score_narrator_agent_version,
        ),
        "lineage": (
            config.foundry_lineage_agent_name,
            config.foundry_lineage_agent_version,
        ),
        "executive_qa": (
            config.foundry_executive_qa_agent_name,
            config.foundry_executive_qa_agent_version,
        ),
    }
    return agent_settings[agent]


def _parse_json_payload(text: str) -> dict[str, Any]:
    """Decode a JSON-only Fabric agent response."""
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        if stripped.lower().startswith("json"):
            stripped = stripped[4:].strip()
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise FabricDataAgentError("Fabric Data Agent returned malformed JSON") from exc
    if not isinstance(payload, dict):
        raise FabricDataAgentError("Fabric Data Agent response JSON must be an object")
    return payload


def _fabric_response_from_payload(
    payload: dict[str, Any],
    request: FabricQuestionRequest,
) -> FabricQuestionResponse:
    """Convert decoded JSON into a validated FabricQuestionResponse."""
    missing = {
        key
        for key in ("answer", "citations", "tool_evidence", "confidence")
        if key not in payload
    }
    if missing:
        raise FabricDataAgentError(
            f"Fabric Data Agent response missing required field(s): "
            f"{', '.join(sorted(missing))}"
        )

    citations = [_source_ref_from_payload(item) for item in payload["citations"]]
    tool_evidence = [_tool_evidence_from_payload(item) for item in payload["tool_evidence"]]
    return FabricQuestionResponse(
        question=str(payload.get("question", request.question)),
        answer=str(payload["answer"]),
        agent_name=request.agent_name,
        agent_version=request.agent_version,
        citations=citations,
        tool_evidence=tool_evidence,
        confidence=str(payload["confidence"]),  # type: ignore[arg-type]
    )


def _source_ref_from_payload(payload: Any) -> SourceReference:
    """Build a SourceReference from a model payload."""
    if not isinstance(payload, dict):
        raise FabricDataAgentError("citation must be an object")
    return SourceReference(
        source=str(payload.get("source", "")),
        reference_type=str(payload.get("reference_type", "table")),  # type: ignore[arg-type]
        name=str(payload.get("name", "")),
        value=str(payload.get("value", "")),
    )


def _tool_evidence_from_payload(payload: Any) -> ToolEvidence:
    """Build ToolEvidence from a model payload."""
    if not isinstance(payload, dict):
        raise FabricDataAgentError("tool_evidence entry must be an object")
    refs = [_source_ref_from_payload(item) for item in payload.get("source_refs", [])]
    return ToolEvidence(
        tool_name=str(payload.get("tool_name", "")),
        data_source=str(payload.get("data_source", "")),
        query=str(payload.get("query", "")),
        source_refs=refs,
    )
