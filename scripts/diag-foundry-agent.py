"""One-shot diagnostic: verify Foundry agent + model deployment are reachable.

Usage:
    python scripts/diag-foundry-agent.py

Prints the resolved settings and attempts a minimal invocation of the
Control Mapper agent. On failure, prints the full exception chain so it
is obvious whether the fault is deployment-name, credentials, or network.
"""
from __future__ import annotations

import sys
import traceback

from regimpact.agents.foundry_client import (
    FoundryAgentClient,
    FoundryAgentConfig,
)
from regimpact.settings import settings


def _mask(value: str, keep: int = 4) -> str:
    if not value:
        return "<empty>"
    if len(value) <= keep * 2:
        return "*" * len(value)
    return f"{value[:keep]}...{value[-keep:]}"


def main() -> int:
    print("=== Resolved settings ===")
    print(f"FOUNDRY_PROJECT_ENDPOINT        = {settings.foundry_project_endpoint}")
    print(f"FOUNDRY_MODEL_DEPLOYMENT_NAME   = {settings.foundry_model_deployment_name!r}")
    print(f"FOUNDRY_API_VERSION             = {settings.foundry_api_version}")
    print(f"FOUNDRY_CONTROL_MAPPER_AGENT    = "
          f"{settings.foundry_control_mapper_agent_name} v"
          f"{settings.foundry_control_mapper_agent_version}")
    print(f"FOUNDRY_AGENT_TIMEOUT_SECONDS   = {settings.foundry_agent_timeout_seconds}")
    print()

    config = FoundryAgentConfig(
        project_endpoint=settings.foundry_project_endpoint,
        model_deployment_name=settings.foundry_model_deployment_name,
        agent_name=settings.foundry_control_mapper_agent_name,
        agent_version=settings.foundry_control_mapper_agent_version,
        api_version=settings.foundry_api_version or "2025-05-01-preview",
        timeout_seconds=settings.foundry_agent_timeout_seconds,
    )
    client = FoundryAgentClient(config=config)

    print("=== Attempting minimal invocation ===")
    try:
        response = client.invoke("ping")
    except Exception as exc:
        print(f"FAILED with {type(exc).__name__}: {exc}")
        print()
        print("=== Full traceback ===")
        traceback.print_exc()
        return 1

    print("SUCCESS")
    print(f"  agent_name    = {response.agent_name}")
    print(f"  agent_version = {response.agent_version}")
    print(f"  text (first 200 chars): {response.text[:200]!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
