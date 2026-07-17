"""Entra-authenticated Foundry agent clients for Fabric-grounded workflows."""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import inspect
import json
import logging
import re
import time
from dataclasses import dataclass
from importlib import import_module
from typing import Any, Literal

from ..contracts import (
    FabricQuestionRequest,
    FabricQuestionResponse,
    SourceReference,
    ToolEvidence,
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


# Regex matching the Foundry Fabric Data Agent "active run on thread" server-side
# race. Multiple purpose-built agents (ControlMapper, GapAnalyst, ...) that share
# the same underlying data agent can hit this when the previous stage's run
# hasn't been fully released yet. The condition clears in a few seconds.
# See https://aka.ms/foundryfabrictroubleshooting
_ACTIVE_RUN_RE = re.compile(
    r"Can't add messages to thread_\w+ while a run run_\w+ is active",
    re.IGNORECASE,
)
# Message fragments the OpenAI SDK raises for transient network/service issues
# that are safe to retry (connection reset, DNS blip, service cold start on a
# freshly deployed model, request timeout, 5xx from the gateway).
_TRANSIENT_MESSAGE_FRAGMENTS = (
    "connection error",
    "connection aborted",
    "connection reset",
    "connection refused",
    "read timed out",
    "request timed out",
    "temporarily unavailable",
    "service unavailable",
    "bad gateway",
    "gateway timeout",
    "internal server error",
    # Fabric-specific: the Foundry gateway surfaces intermittent Fabric Data
    # Agent execution failures (session churn, query engine warmup, transient
    # lakehouse throttling) as a 400 BadRequestError with code
    # `tool_user_error` and message "Fabric run failed during execution".
    # Ping succeeds against the same agent moments earlier — the failure is
    # not deterministic. Retrying with backoff usually clears it.
    "fabric run failed during execution",
    "tool_user_error",
)
_RETRY_BACKOFF_SECONDS = (3.0, 6.0, 12.0, 24.0)


def _openai_transient_error_types() -> tuple[type[BaseException], ...]:
    """Return openai SDK transient exception types when the package is installed.

    Only genuinely retryable failures are included. 4xx errors
    (BadRequestError, AuthenticationError, PermissionDeniedError, NotFoundError,
    UnprocessableEntityError) are deterministic parameter faults and MUST NOT
    be retried — retrying them wastes ~45s and hides the real error.
    """
    try:
        openai_module = import_module("openai")
    except ImportError:
        return ()
    candidate_names = (
        "APIConnectionError",
        "APITimeoutError",
        "InternalServerError",  # 500
        "RateLimitError",       # 429 — retryable with backoff
    )
    resolved: list[type[BaseException]] = []
    for name in candidate_names:
        cls = getattr(openai_module, name, None)
        if isinstance(cls, type) and issubclass(cls, BaseException):
            resolved.append(cls)
    return tuple(resolved)


def _openai_deterministic_error_types() -> tuple[type[BaseException], ...]:
    """Return openai SDK 4xx exception types that MUST NOT be retried."""
    try:
        openai_module = import_module("openai")
    except ImportError:
        return ()
    candidate_names = (
        "BadRequestError",           # 400
        "AuthenticationError",       # 401
        "PermissionDeniedError",     # 403
        "NotFoundError",             # 404
        "UnprocessableEntityError",  # 422
    )
    resolved: list[type[BaseException]] = []
    for name in candidate_names:
        cls = getattr(openai_module, name, None)
        if isinstance(cls, type) and issubclass(cls, BaseException):
            resolved.append(cls)
    return tuple(resolved)


def _is_retryable_transient(exc: BaseException) -> bool:
    """Return True when ``exc`` matches a known transient Foundry/OpenAI failure."""
    # Deterministic parameter faults surface via the openai SDK as
    # APIConnectionError but retrying them is pointless — the cause is our
    # config, not the network. Walk the __cause__ / __context__ chain and
    # abort early if we see one.
    node: BaseException | None = exc
    while node is not None:
        if isinstance(node, (OverflowError, ValueError, TypeError)):
            return False
        node = node.__cause__ or node.__context__
    message = str(exc).lower()
    # Foundry Fabric emits transient failures dressed up as 400 BadRequestError
    # (code=tool_user_error). Match the message BEFORE the 4xx short-circuit
    # so these specific Fabric-side flakes retry instead of failing fast.
    if _ACTIVE_RUN_RE.search(message):
        return True
    if any(fragment in message for fragment in _TRANSIENT_MESSAGE_FRAGMENTS):
        return True
    # 4xx status errors from the OpenAI SDK are otherwise deterministic —
    # retrying them just wastes ~45s and buries the real error message.
    if isinstance(exc, _openai_deterministic_error_types()):
        return False
    if isinstance(exc, _openai_transient_error_types()):
        return True
    if isinstance(exc, TimeoutError):
        return True
    return False


@dataclass(frozen=True)
class FoundryAgentConfig:
    """Configuration for invoking a named Foundry agent."""

    project_endpoint: str
    model_deployment_name: str
    agent_name: str
    agent_version: str
    api_version: str = "2025-05-01-preview"
    timeout_seconds: float = 120
    max_output_tokens: int = 8000

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
            max_output_tokens=config.foundry_agent_max_output_tokens,
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
            max_output_tokens=config.foundry_agent_max_output_tokens,
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

        # Log the raw input sent to the Foundry gateway. INFO carries the
        # size fingerprint (easy to correlate with logs); DEBUG carries the
        # full text so operators can reproduce a failing call exactly.
        logger.info(
            "[FOUNDRY DEBUG] invoke agent=%s v%s input_bytes=%d input_head=%r",
            self.config.agent_name,
            self.config.agent_version,
            len(input_text),
            input_text[:240],
        )
        logger.debug(
            "[FOUNDRY DEBUG] invoke agent=%s v%s full_input=%s",
            self.config.agent_name,
            self.config.agent_version,
            input_text,
        )

        try:
            agent = self._agent or self._build_agent()
        except foundry_runtime_error_types() as exc:
            raise FoundryAgentError(f"Foundry agent setup failed: {exc}") from exc

        for method_name in ("run", "invoke", "chat", "complete"):
            method = getattr(agent, method_name, None)
            if callable(method):
                raw = self._invoke_with_retry(
                    method, method_name, input_text
                )
                return FoundryAgentRawResponse(
                    text=_extract_text(raw),
                    agent_name=self.config.agent_name,
                    agent_version=self.config.agent_version,
                    metadata=_extract_metadata(raw),
                )

        raise FoundryAgentError("Foundry agent has no supported call method")

    def _invoke_with_retry(
        self,
        method: Any,
        method_name: str,
        input_text: str,
    ) -> Any:
        """Call the agent method with backoff on known transient failures.

        Handles the Foundry Fabric Data Agent "active run on thread" race
        (see https://aka.ms/foundryfabrictroubleshooting) as well as generic
        OpenAI SDK connection/timeout/5xx errors that surface as
        ``APIConnectionError``, ``APITimeoutError``, or the string
        "Connection error." from the transport layer. All other errors are
        raised immediately.
        """
        attempts = len(_RETRY_BACKOFF_SECONDS) + 1
        last_exc: BaseException | None = None
        for attempt in range(1, attempts + 1):
            try:
                return _resolve_response(method(input_text))
            except foundry_agent_error_types() as exc:
                last_exc = exc
                if _is_retryable_transient(exc) and attempt <= len(_RETRY_BACKOFF_SECONDS):
                    backoff = _RETRY_BACKOFF_SECONDS[attempt - 1]
                    logger.warning(
                        "[FOUNDRY DEBUG] transient failure for agent %s v%s "
                        "(attempt %d/%d, %s); retrying in %.1fs",
                        self.config.agent_name,
                        self.config.agent_version,
                        attempt,
                        attempts,
                        type(exc).__name__,
                        backoff,
                    )
                    time.sleep(backoff)
                    continue
                logger.error(
                    "[FOUNDRY DEBUG] invoke failed for agent %s v%s via %s: %s (body=%s)",
                    self.config.agent_name,
                    self.config.agent_version,
                    method_name,
                    exc,
                    _extract_error_body(exc),
                )
                raise FoundryAgentError(
                    f"Foundry agent invocation failed: {exc}"
                ) from exc
        # Exhausted retries on transient failures.
        logger.error(
            "[FOUNDRY DEBUG] invoke exhausted %d retries for agent %s v%s: %s",
            attempts,
            self.config.agent_name,
            self.config.agent_version,
            last_exc,
        )
        raise FoundryAgentError(
            f"Foundry agent invocation failed after {attempts} attempts: {last_exc}"
        ) from last_exc

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
        """Invoke a Foundry prompt/hosted agent through Responses API.

        NOTE: ``max_output_tokens`` is intentionally NOT passed at the top
        level. When paired with ``agent_reference`` the Foundry gateway
        rejects it with a 400 BadRequestError — the deployed agent's own
        model settings govern the token ceiling. We steer response length
        via the agent's prompt instructions instead (see
        ``fabric_workflow.py`` OUTPUT DISCIPLINE clauses).
        """
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
            timeout=_build_httpx_timeout(self.config.timeout_seconds),
        )


def _build_httpx_timeout(total_seconds: float) -> Any:
    """Build a finite, bounded ``httpx.Timeout`` for Foundry Responses calls.

    A plain ``float`` timeout in ``responses.create(...)`` is expanded by
    httpx into ``Timeout(connect=x, read=x, write=x, pool=x)``. On Windows
    with Python 3.14 the socket layer rejects timeouts that do not fit into
    a C ``timeval`` — a mis-typed ``FOUNDRY_AGENT_TIMEOUT_SECONDS`` (``inf``,
    ``3e10``, etc.) then surfaces as ``OverflowError`` wrapped in
    ``APIConnectionError`` ("Connection error."). We build the Timeout
    explicitly with a short connect phase and bounded read/write/pool phases
    so a bad config value cannot reach the socket layer.
    """
    try:
        httpx_module = import_module("httpx")
    except ImportError:
        # Fall back to a plain float if httpx is not directly importable;
        # the openai SDK will still normalize it.
        return max(30.0, min(float(total_seconds), 900.0))
    read_seconds = max(30.0, min(float(total_seconds), 900.0))
    return httpx_module.Timeout(
        connect=30.0,
        read=read_seconds,
        write=read_seconds,
        pool=read_seconds,
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
        """Ask a Fabric-grounded question through Foundry and validate the answer.

        Single-attempt: on any semantic failure (malformed JSON, missing
        ``answer`` field, truncated inner payload) the error propagates
        immediately. Semantic retry was removed intentionally — each
        additional attempt was doubling / tripling wall-clock time on the
        happy path when the model got noisy, and the transport-level retry
        inside :meth:`FoundryAgentClient._invoke_with_retry` still handles
        network / 5xx / active-run races.

        Lenient PARSING (metadata defaults, inner-payload recovery,
        markdown-fence extraction) is preserved — those never cost extra
        round-trips. Constitutional constraint remains: no hardcoded /
        offline agent-behavior fallback.
        """
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
        _validate_inner_answer(response)
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


def _extract_error_body(exc: BaseException) -> str:
    """Extract the raw response body from an OpenAI SDK error for logging.

    OpenAI SDK exceptions carry the server-side error payload on ``.body``
    (parsed dict) or ``.response`` (httpx.Response). str(exc) usually only
    shows the summary — the actionable detail (invalid param name, missing
    field, etc.) lives in the body.
    """
    body = getattr(exc, "body", None)
    if body is not None:
        try:
            return json.dumps(body)[:600]
        except (TypeError, ValueError):
            return str(body)[:600]
    response = getattr(exc, "response", None)
    if response is not None:
        text = getattr(response, "text", None)
        if text:
            return str(text)[:600]
    return "<none>"


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


# Inner-payload keys that indicate a Fabric agent returned the inner
# answer directly (envelope stripped by the model). When we see one of
# these at the top level and no ``answer`` key, we recover by treating
# the whole payload as the inner answer.
_RECOVERABLE_INNER_KEYS = frozenset(
    {
        "mappings",
        "findings",
        "actions",
        "narrative",
        "change_id",
        "impacted_entities",
        "hops",
    }
)

# Matches a markdown fenced code block, optionally tagged ``json``.
# Non-greedy body capture; anchored to a fence on both sides.
_FENCE_RE = re.compile(
    r"```(?:json)?\s*(?P<body>\{.*?\})\s*```",
    re.IGNORECASE | re.DOTALL,
)


def _extract_json_block(text: str) -> str:
    """Return the JSON-object substring embedded in ``text``.

    Handles three shapes:
    1. Markdown fences: ``` ```json {...} ``` `` or ``` ``` {...} ``` ``
    2. Bare JSON with leading/trailing whitespace.
    3. Prose containing a JSON object (balanced-brace scan from first ``{``).

    Raises ``FabricDataAgentError`` if no JSON object can be located.
    """
    stripped = text.strip()
    match = _FENCE_RE.search(stripped)
    if match:
        return match.group("body").strip()
    start = stripped.find("{")
    if start == -1:
        raise FabricDataAgentError("Fabric Data Agent returned malformed JSON")
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(stripped)):
        ch = stripped[i]
        if escape:
            escape = False
            continue
        if in_string:
            if ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return stripped[start : i + 1]
    raise FabricDataAgentError(
        f"malformed JSON: unmatched opening brace at pos {start} "
        "(likely truncated)"
    )


def _parse_json_payload(text: str) -> dict[str, Any]:
    """Decode a JSON-only Fabric agent response with lenient extraction.

    First tries a strict ``json.loads`` on the trimmed text. If that fails,
    delegates to :func:`_extract_json_block` to peel off markdown fences or
    surrounding prose, then decodes the extracted block. Raises the existing
    ``FabricDataAgentError("Fabric Data Agent returned malformed JSON")`` if
    both attempts fail.
    """
    stripped = text.strip()
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        extracted = _extract_json_block(stripped)
        try:
            payload = json.loads(extracted)
        except json.JSONDecodeError as exc:
            raise FabricDataAgentError(
                "Fabric Data Agent returned malformed JSON"
            ) from exc
    if not isinstance(payload, dict):
        raise FabricDataAgentError(
            "Fabric Data Agent response JSON must be an object"
        )
    return payload


def _fabric_response_from_payload(
    payload: dict[str, Any],
    request: FabricQuestionRequest,
) -> FabricQuestionResponse:
    """Convert decoded JSON into a ``FabricQuestionResponse``.

    Only ``answer`` is truly required for downstream processing. Metadata
    (``citations``, ``tool_evidence``, ``confidence``) is defaulted with a
    WARNING when missing so operators still see the misbehavior.

    Recovery: when ``answer`` is missing but the top-level payload contains a
    key from :data:`_RECOVERABLE_INNER_KEYS`, treat the whole payload as the
    inner answer (agents sometimes drop the envelope entirely). Also handles
    ``answer`` values that arrive as ``dict``/``list`` by JSON-encoding them.
    """
    top_keys = sorted(payload.keys())
    if "answer" not in payload:
        if any(key in payload for key in _RECOVERABLE_INNER_KEYS):
            logger.warning(
                "Fabric response envelope missing 'answer'; recovered by "
                "treating top-level JSON as inner payload "
                "agent=%s v%s top_keys=%s",
                request.agent_name,
                request.agent_version,
                top_keys,
            )
            recovered = json.dumps(payload)
            payload = {
                "answer": recovered,
                **{
                    k: v
                    for k, v in payload.items()
                    if k in ("citations", "tool_evidence", "confidence", "question")
                },
            }
        else:
            raise FabricDataAgentError(
                "Fabric Data Agent response missing required field(s): answer"
            )

    defaulted: list[str] = []
    citations_payload = payload.get("citations")
    if citations_payload is None:
        citations_payload = []
        defaulted.append("citations")
    tool_evidence_payload = payload.get("tool_evidence")
    if tool_evidence_payload is None:
        tool_evidence_payload = []
        defaulted.append("tool_evidence")
    confidence_value = payload.get("confidence")
    if confidence_value is None:
        confidence_value = "low"
        defaulted.append("confidence")

    if defaulted:
        logger.warning(
            "Fabric Data Agent response missing metadata field(s) %s; "
            "using defaults agent=%s v%s top_keys=%s",
            defaulted,
            request.agent_name,
            request.agent_version,
            top_keys,
        )

    citations = [_source_ref_from_payload(item) for item in citations_payload]
    tool_evidence = [
        _tool_evidence_from_payload(item) for item in tool_evidence_payload
    ]

    raw_answer = payload["answer"]
    if isinstance(raw_answer, (dict, list)):
        answer = json.dumps(raw_answer)
    else:
        answer = str(raw_answer)

    return FabricQuestionResponse(
        question=str(payload.get("question", request.question)),
        answer=answer,
        agent_name=request.agent_name,
        agent_version=request.agent_version,
        citations=citations,
        tool_evidence=tool_evidence,
        confidence=str(confidence_value),
    )


def _looks_truncated(text: str) -> bool:
    """Heuristic: does ``text`` look cut off mid-generation?

    True when the first non-whitespace char is ``{`` or ``[`` and the
    last char is NOT the matching close bracket. Complete-but-malformed
    JSON (balanced but invalid contents) returns False so the retry
    loop can pick the correct nudge (envelope reminder vs "be concise").
    """
    if not text:
        return False
    first = text[0]
    last = text[-1]
    if first == "{" and last != "}":
        return True
    if first == "[" and last != "]":
        return True
    return False


def _validate_inner_answer(response: FabricQuestionResponse) -> None:
    """Ensure ``response.answer`` parses as JSON when it's a string.

    The envelope layer only guarantees the OUTER JSON parses. When the
    Fabric agent returns an ``answer`` string that is itself truncated
    or malformed, downstream ``_json_answer`` calls in
    ``fabric_workflow`` would raise ``FabricAgentHarnessError`` outside
    the semantic retry loop — one bad response would abort the entire
    pipeline. Lifting the check into the client turns a truncated /
    malformed inner answer into a retryable semantic failure that the
    loop can course-correct with a tailored prompt.

    Constitutional constraint: this NEVER attempts to close braces or
    salvage a truncated response — silent completion would fabricate
    findings / mappings / actions. It only raises so the retry loop can
    re-ask the model with better instructions.
    """
    answer = response.answer
    if isinstance(answer, dict):
        return
    if not isinstance(answer, str):
        return
    stripped = answer.strip()
    if not stripped:
        return
    # Only agents that return structured JSON (control_mapper, gap_analyst,
    # remediation_planner, lineage) start their answer with ``{`` or ``[``.
    # Prose answers (executive_qa, score_narrator) MUST pass through
    # untouched — they legitimately are not JSON.
    if stripped[0] not in "{[":
        return
    strict_pos: int | None = None
    strict_msg: str | None = None
    try:
        json.loads(stripped)
        return
    except json.JSONDecodeError as exc:
        strict_pos = exc.pos
        strict_msg = exc.msg
    try:
        extracted = _extract_json_block(stripped)
        json.loads(extracted)
        return
    except (FabricDataAgentError, json.JSONDecodeError):
        pass
    truncated = _looks_truncated(stripped)
    raise FabricDataAgentError(
        f"inner answer JSON invalid at pos {strict_pos} "
        f"(answer_bytes={len(answer)}, "
        f"truncated={'true' if truncated else 'false'}): {strict_msg}"
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
