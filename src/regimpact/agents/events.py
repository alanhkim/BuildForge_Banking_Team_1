"""Event bus for :class:`AgentPipeline` observability.

Emitted synchronously by :class:`~regimpact.agents.pipeline.AgentPipeline`
at stage boundaries and tool-call sites so callers (CLI, tests) can render
live progress or capture the sequence without changing pipeline behaviour.

Design constraints
------------------
* No callback attached → no cost, no behaviour change. The default
  ``on_event=None`` path is a bare ``is None`` check per emission site.
* Frozen dataclass — events are immutable value objects safe to pass to
  arbitrary subscribers (renderers, test capture lists, log adapters).
* No async, no threads — the pipeline stays sequential. Streaming here
  means "surface what is already happening", not "parallelise the run".
* No external dependencies. This module is import-safe even when the
  ``fabric`` extra is not installed.

Event kinds
-----------
``stage_start`` / ``stage_end``
    Bracket each pipeline stage (interpreter, control_mapper,
    gap_analyst, remediation_planner, score_narrator, and CLI-level
    post-pipeline stages like onelake_files / purview / report).
    ``stage_end`` carries ``duration_ms`` and stage-specific counters
    (obligation count, mapping count, gap count, score triple) in
    ``details``.

``stage_error``
    A stage failed. For hard-fail stages (interpreter, control_mapper,
    score_narrator) the pipeline re-raises after emitting. For soft-fail
    stages (gap_analyst, remediation_planner) the pipeline continues and
    the renderer should show the row as errored.

``tool_call``
    A Foundry/Fabric tool invocation reported via
    :class:`~regimpact.contracts.ToolEvidence`. One event per unique
    tool_evidence entry. ``message`` carries the query text (truncated by
    the emitter).

``writeback`` / ``info``
    Reserved for future use (Delta table appends, generic status). Not
    yet emitted by the pipeline but callers may consume them.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Literal, Optional

EventKind = Literal[
    "stage_start",
    "stage_end",
    "stage_error",
    "tool_call",
    "writeback",
    "info",
]


@dataclass(frozen=True)
class PipelineEvent:
    """A single observability event emitted by the pipeline.

    ``details`` is a plain ``dict`` (not frozen) because the field itself
    is bound at construction time — swapping the reference would require
    a new event. Renderers should treat it as read-only.
    """

    kind: EventKind
    stage: str
    message: str = ""
    tool_name: Optional[str] = None
    data_source: Optional[str] = None
    duration_ms: Optional[int] = None
    details: dict[str, Any] = field(default_factory=dict)


EventCallback = Callable[[PipelineEvent], None]
"""Callable subscribers pass to ``AgentPipeline(on_event=...)``.

Callbacks are invoked synchronously on the pipeline's thread. Long-running
work in a callback will block the pipeline, so keep renderers cheap
(mutate state + refresh a Live view — do not do I/O).
"""
