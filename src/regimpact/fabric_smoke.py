"""Fabric Data Agent smoke prompt definitions."""
from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

SmokeCategory = Literal[
    "evidence_health",
    "gap_blast_radius",
    "lineage",
    "obligation_control_map",
    "remediation",
    "score_story",
]


class FabricSmokePrompt(BaseModel):
    """One Fabric Data Agent validation prompt and its expected answer shape."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=1)
    category: SmokeCategory
    prompt: str = Field(min_length=20)
    expected_characteristics: tuple[str, ...] = Field(min_length=3)

    @field_validator("expected_characteristics", mode="before")
    @classmethod
    def _coerce_characteristics(cls, value: object) -> tuple[str, ...]:
        if not isinstance(value, list):
            raise TypeError("expected_characteristics must be a list")
        return tuple(str(item) for item in value)


class FabricSmokePromptSet(BaseModel):
    """Versioned Fabric Data Agent smoke prompt set."""

    model_config = ConfigDict(frozen=True)

    version: int = Field(ge=1)
    fabric_workspace_id: str = Field(min_length=1)
    fabric_data_agent_id: str = Field(min_length=1)
    foundry_project_endpoint: str = Field(min_length=1)
    foundry_agent_name: str = Field(min_length=1)
    foundry_agent_version: str = Field(min_length=1)
    prompts: tuple[FabricSmokePrompt, ...] = Field(min_length=1)

    @field_validator("prompts", mode="before")
    @classmethod
    def _coerce_prompts(cls, value: object) -> tuple[object, ...]:
        if not isinstance(value, list):
            raise TypeError("prompts must be a list")
        return tuple(value)

    @field_validator("prompts")
    @classmethod
    def _require_unique_prompt_ids(
        cls,
        value: tuple[FabricSmokePrompt, ...],
    ) -> tuple[FabricSmokePrompt, ...]:
        ids = [prompt.id for prompt in value]
        duplicates = sorted({prompt_id for prompt_id in ids if ids.count(prompt_id) > 1})
        if duplicates:
            raise ValueError(f"duplicate smoke prompt IDs: {', '.join(duplicates)}")
        return value


def load_fabric_smoke_prompts(path: str | Path) -> FabricSmokePromptSet:
    """Load and validate a Fabric Data Agent smoke prompt file."""
    prompt_path = Path(path)
    with prompt_path.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle)
    return FabricSmokePromptSet.model_validate(payload)
