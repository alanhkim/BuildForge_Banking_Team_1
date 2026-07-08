"""Runtime settings for local and Fabric-friendly demo runs."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    """Environment-overridable settings with deterministic local defaults."""

    seed: int = int(os.getenv("REGIMPACT_SEED", "42"))
    as_of: str = os.getenv("REGIMPACT_AS_OF", "2026-06-25")
    output_dir: Path = field(
        default_factory=lambda: Path(os.getenv("REGIMPACT_OUTPUT_DIR", "output"))
    )
    azure_openai_endpoint: str = os.getenv("AZURE_OPENAI_ENDPOINT", "")
    azure_openai_deployment: str = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4o")
    azure_openai_api_version: str = os.getenv(
        "AZURE_OPENAI_API_VERSION",
        "2024-08-01-preview",
    )
    azure_openai_token_scope: str = os.getenv(
        "AZURE_OPENAI_TOKEN_SCOPE",
        "https://cognitiveservices.azure.com/.default",
    )
    fabric_workspace_id: str = os.getenv("FABRIC_WORKSPACE_ID", "")
    fabric_lakehouse_id: str = os.getenv("FABRIC_LAKEHOUSE_ID", "")
    purview_account: str = os.getenv("PURVIEW_ACCOUNT_NAME", "")
    regimpact_foundry_enabled: bool = os.getenv(
        "REGIMPACT_FOUNDRY_ENABLED",
        "",
    ).lower() in {"1", "true", "yes", "on"}
    foundry_project_endpoint: str = os.getenv("FOUNDRY_PROJECT_ENDPOINT", "")
    foundry_model_deployment_name: str = os.getenv(
        "FOUNDRY_MODEL_DEPLOYMENT_NAME",
        "",
    )
    foundry_api_version: str = os.getenv(
        "FOUNDRY_API_VERSION",
        "2025-05-01-preview",
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
