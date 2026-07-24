"""PipelineEvent → UI-state adapter.

Pure Python, no Streamlit dependency. This module is unit-tested in
:mod:`tests.test_ui_event_bridge` and imported by
:mod:`regimpact.ui.streamlit_app` at runtime.

The bridge maintains a mutable :class:`RunState` that mirrors the CLI's
live table (``src/regimpact/cli.py::_STREAM_STAGES`` + ``_StageState``)
one-to-one so both surfaces render the same nine stages in the same
order with the same status/detail semantics.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from ..agents.events import PipelineEvent

# Same stage keys + labels as the CLI live renderer. Kept in sync
# manually — if a new stage is added to the pipeline, both this list
# and ``cli.py::_STREAM_STAGES`` need the new row.
STAGES: tuple[tuple[str, str], ...] = (
    ("interpreter",         "Regulation Interpreter"),
    ("control_mapper",      "Control Mapper"),
    ("gap_analyst",         "Gap Analyst"),
    ("remediation_planner", "Remediation Planner"),
    ("score_narrator",      "Score Narrator"),
    ("local_exports",       "Local exports (tables/gold/graph)"),
    ("onelake_files",       "OneLake Files/"),
    ("onelake_tables",      "OneLake Tables/"),
    ("purview",             "Purview export"),
    ("report",              "Report + gold export"),
)

STAGE_KEYS: tuple[str, ...] = tuple(k for k, _ in STAGES)


@dataclass
class StageState:
    """Per-stage rendering state — matches CLI ``_StageState`` fields."""

    key: str
    label: str
    status: str = "pending"  # pending | running | done | error | skipped
    detail: str = ""
    tool_count: int = 0
    tool_lines: list[str] = field(default_factory=list)
    duration_ms: int | None = None


@dataclass
class RunState:
    """Full state for a single pipeline run.

    Consumed by the Streamlit renderer. Safe to serialise into
    ``st.session_state`` between reruns.
    """

    stages: dict[str, StageState] = field(default_factory=dict)
    completed: bool = False
    error: str | None = None
    # Populated by the UI layer after the pipeline returns. Kept here
    # (not in a separate object) so the run history sidebar can show
    # one entry per RunState.
    report: dict | None = None
    label: str = ""  # user-facing name, e.g. "REG-EUAIACT · 2026-07-23 14:12"

    def __post_init__(self) -> None:
        if not self.stages:
            self.stages = {
                key: StageState(key=key, label=label) for key, label in STAGES
            }

    # ------------------------------------------------------------------ #
    # Event application
    # ------------------------------------------------------------------ #
    def apply(self, ev: PipelineEvent) -> None:
        """Fold one :class:`PipelineEvent` into this state.

        Unknown stages are silently ignored so a future pipeline stage
        can emit events without breaking existing UI builds (same
        contract as the CLI renderer).
        """
        stage = self.stages.get(ev.stage)
        if stage is None:
            return
        if ev.kind == "stage_start":
            stage.status = "running"
            if ev.message:
                stage.detail = ev.message
        elif ev.kind == "stage_end":
            if ev.details.get("skipped"):
                stage.status = "skipped"
                stage.detail = ev.details.get("reason") or stage.detail
            else:
                stage.status = "done"
                stage.duration_ms = ev.duration_ms
                stage.detail = format_stage_detail(
                    ev.stage, ev.details, stage.detail
                )
        elif ev.kind == "stage_error":
            stage.status = "error"
            # Preserve the full error message — the renderer surfaces the
            # long form in an expander below the stage table so operators
            # can see the complete Foundry / Fabric 400 payload without
            # having to dig into logs.
            stage.detail = ev.message or "unknown error"
        elif ev.kind == "tool_call":
            stage.tool_count += 1
            if ev.tool_name:
                line = ev.tool_name
                if ev.data_source:
                    line += f" · {ev.data_source}"
                if ev.message:
                    line += f" — {ev.message[:60]}"
                stage.tool_lines.append(line)

    def apply_all(self, events: Iterable[PipelineEvent]) -> None:
        for ev in events:
            self.apply(ev)


# ---------------------------------------------------------------------- #
# Stage detail formatter — duplicated (intentionally, small surface)
# from ``cli.py::_format_stage_detail`` to keep this module import-free
# of ``rich``. If the CLI formatter grows, keep the two in sync.
# ---------------------------------------------------------------------- #
def format_stage_detail(stage_key: str, details: dict, fallback: str) -> str:
    """Produce a short human-readable stage summary from event details."""
    d = details or {}
    if stage_key == "interpreter" and "obligations" in d:
        return f"{d['obligations']} obligations · mode={d.get('mode', '?')}"
    if stage_key == "control_mapper" and "mappings" in d:
        extra = " · empty-with-reason" if d.get("empty_with_reason") else ""
        return f"{d['mappings']} mappings · {d.get('controls', 0)} controls{extra}"
    if stage_key == "gap_analyst" and "findings" in d:
        return f"{d['findings']} gaps"
    if stage_key == "remediation_planner":
        if d.get("skipped"):
            return d.get("reason", "skipped")
        if "actions" in d:
            return f"{d['actions']} actions · {d.get('total_effort_days', 0)}d effort"
    if stage_key == "score_narrator" and "as_is" in d:
        return (
            f"scores {d['as_is']:.1f} → {d['post_change']:.1f} "
            f"→ {d['post_remediation']:.1f}"
        )
    # OneLake / Purview / Report stages carry a plain ``message`` field.
    if "message" in d:
        return str(d["message"])
    return fallback
