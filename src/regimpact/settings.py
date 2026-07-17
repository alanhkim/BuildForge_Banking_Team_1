"""Runtime settings for local and Fabric-friendly demo runs."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:
    from dotenv import load_dotenv as _load_dotenv
    _load_dotenv(override=False)  # .env values never override already-set env vars
except ImportError:
    pass  # python-dotenv is optional; env vars must be set manually


# Windows socket.settimeout on Python 3.14 rejects timeout values that do not
# fit into a C ``timeval`` struct. httpx/httpcore forwards our value straight
# down to the socket layer, so we clamp aggressively here to prevent a
# mis-typed ``.env`` (e.g. ``3e10`` or ``inf``) from surfacing as an opaque
# ``OverflowError: timeout doesn't fit into C timeval`` wrapped by
# ``openai.APIConnectionError``.
_TIMEOUT_DEFAULT_SECONDS = 120.0
_TIMEOUT_MIN_SECONDS = 30.0
_TIMEOUT_MAX_SECONDS = 900.0  # 15 minutes; longer requests should be async

# Foundry Responses API max_output_tokens ceiling. gpt-5.4-mini defaults to a
# fairly small ceiling that truncates long JSON payloads mid-string. We clamp
# to a generous range so a mis-typed .env can't produce an unusable value.
_MAX_OUTPUT_TOKENS_DEFAULT = 8000
_MAX_OUTPUT_TOKENS_MIN = 512
_MAX_OUTPUT_TOKENS_MAX = 32000


def _parse_timeout_seconds(raw: str | None) -> float:
    """Parse ``FOUNDRY_AGENT_TIMEOUT_SECONDS`` with clamping and safe fallback."""
    if not raw:
        return _TIMEOUT_DEFAULT_SECONDS
    try:
        value = float(raw)
    except ValueError:
        return _TIMEOUT_DEFAULT_SECONDS
    if not (value == value) or value in (float("inf"), float("-inf")):  # NaN / inf
        return _TIMEOUT_DEFAULT_SECONDS
    if value < _TIMEOUT_MIN_SECONDS:
        return _TIMEOUT_MIN_SECONDS
    if value > _TIMEOUT_MAX_SECONDS:
        return _TIMEOUT_MAX_SECONDS
    return value


def _parse_max_output_tokens(raw: str | None) -> int:
    """Parse ``FOUNDRY_AGENT_MAX_OUTPUT_TOKENS`` with clamping and safe fallback."""
    if not raw:
        return _MAX_OUTPUT_TOKENS_DEFAULT
    try:
        value = int(float(raw))
    except ValueError:
        return _MAX_OUTPUT_TOKENS_DEFAULT
    if value < _MAX_OUTPUT_TOKENS_MIN:
        return _MAX_OUTPUT_TOKENS_MIN
    if value > _MAX_OUTPUT_TOKENS_MAX:
        return _MAX_OUTPUT_TOKENS_MAX
    return value


@dataclass(frozen=True)
class Settings:
    """Environment-driven settings — all values must be supplied via .env or the shell."""

    seed: int = int(os.getenv("REGIMPACT_SEED") or "42")
    as_of: str = os.getenv("REGIMPACT_AS_OF") or ""
    output_dir: Path = field(
        default_factory=lambda: Path(os.getenv("REGIMPACT_OUTPUT_DIR") or "output")
    )
    azure_openai_endpoint: str = os.getenv("AZURE_OPENAI_ENDPOINT") or ""
    azure_openai_deployment: str = os.getenv("AZURE_OPENAI_DEPLOYMENT") or ""
    azure_openai_api_version: str = os.getenv("AZURE_OPENAI_API_VERSION") or ""
    azure_openai_token_scope: str = os.getenv("AZURE_OPENAI_TOKEN_SCOPE") or ""
    fabric_workspace_id: str = os.getenv("FABRIC_WORKSPACE_ID") or ""
    fabric_lakehouse_id: str = os.getenv("FABRIC_LAKEHOUSE_ID") or ""
    fabric_data_agent_id: str = os.getenv("FABRIC_DATA_AGENT_ID") or ""
    purview_account: str = os.getenv("PURVIEW_ACCOUNT_NAME") or ""
    regimpact_foundry_enabled: bool = (
        os.getenv("REGIMPACT_FOUNDRY_ENABLED") or ""
    ).lower() in {"1", "true", "yes", "on"}
    foundry_project_endpoint: str = os.getenv("FOUNDRY_PROJECT_ENDPOINT") or ""
    foundry_model_deployment_name: str = os.getenv("FOUNDRY_MODEL_DEPLOYMENT_NAME") or ""
    foundry_api_version: str = os.getenv("FOUNDRY_API_VERSION") or ""
    foundry_fabric_agent_name: str = os.getenv("FOUNDRY_FABRIC_AGENT_NAME") or ""
    foundry_fabric_agent_version: str = os.getenv("FOUNDRY_FABRIC_AGENT_VERSION") or ""
    foundry_control_mapper_agent_name: str = os.getenv("FOUNDRY_CONTROL_MAPPER_AGENT_NAME") or ""
    foundry_control_mapper_agent_version: str = os.getenv("FOUNDRY_CONTROL_MAPPER_AGENT_VERSION") or ""
    foundry_gap_analyst_agent_name: str = os.getenv("FOUNDRY_GAP_ANALYST_AGENT_NAME") or ""
    foundry_gap_analyst_agent_version: str = os.getenv("FOUNDRY_GAP_ANALYST_AGENT_VERSION") or ""
    foundry_remediation_planner_agent_name: str = os.getenv("FOUNDRY_REMEDIATION_PLANNER_AGENT_NAME") or ""
    foundry_remediation_planner_agent_version: str = os.getenv("FOUNDRY_REMEDIATION_PLANNER_AGENT_VERSION") or ""
    foundry_score_narrator_agent_name: str = os.getenv("FOUNDRY_SCORE_NARRATOR_AGENT_NAME") or ""
    foundry_score_narrator_agent_version: str = os.getenv("FOUNDRY_SCORE_NARRATOR_AGENT_VERSION") or ""
    foundry_lineage_agent_name: str = os.getenv("FOUNDRY_LINEAGE_AGENT_NAME") or ""
    foundry_lineage_agent_version: str = os.getenv("FOUNDRY_LINEAGE_AGENT_VERSION") or ""
    foundry_executive_qa_agent_name: str = os.getenv("FOUNDRY_EXECUTIVE_QA_AGENT_NAME") or ""
    foundry_executive_qa_agent_version: str = os.getenv("FOUNDRY_EXECUTIVE_QA_AGENT_VERSION") or ""
    foundry_agent_timeout_seconds: float = field(
        default_factory=lambda: _parse_timeout_seconds(
            os.getenv("FOUNDRY_AGENT_TIMEOUT_SECONDS")
        )
    )
    foundry_agent_max_output_tokens: int = field(
        default_factory=lambda: _parse_max_output_tokens(
            os.getenv("FOUNDRY_AGENT_MAX_OUTPUT_TOKENS")
        )
    )
    fabric_materialize_timeout_seconds: int = int(
        os.getenv("FABRIC_MATERIALIZE_TIMEOUT_SECONDS") or "600"
    )

    @property
    def foundry_enabled(self) -> bool:
        """Whether live Foundry/Azure OpenAI integration is configured."""
        return (
            self.regimpact_foundry_enabled
            and bool(self.foundry_project_endpoint)
            and bool(self.foundry_model_deployment_name)
        )

    @property
    def foundry_fabric_enabled(self) -> bool:
        """Whether a Foundry agent backed by Fabric Data Agent is configured."""
        return (
            bool(self.foundry_project_endpoint)
            and bool(self.foundry_executive_qa_agent_name)
            and bool(self.foundry_executive_qa_agent_version)
            and bool(self.fabric_workspace_id)
            and bool(self.fabric_data_agent_id)
        )

    @property
    def tables_dir(self) -> Path:
        return self.output_dir / "tables"

    @property
    def gold_dir(self) -> Path:
        return self.output_dir / "gold"

    @property
    def graph_dir(self) -> Path:
        return self.output_dir / "graph"

    @property
    def reports_dir(self) -> Path:
        return self.output_dir / "reports"

    @property
    def purview_dir(self) -> Path:
        return self.output_dir / "purview"


settings = Settings()
