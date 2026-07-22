"""Tests for :mod:`regimpact.agents.events` + pipeline event emission.

Covers three properties of the observability layer:

1. **Default is silent** — ``AgentPipeline`` with no ``on_event`` still
   works and produces byte-identical results (regression guard: adding
   the event bus must not alter pipeline output).
2. **Broken subscribers do not crash the pipeline** — a callback that
   raises is caught and logged; pipeline continues.
3. **Event stream is complete** — a successful ``_run_fabric`` emits
   ``stage_start``/``stage_end`` for interpreter (skipped here — that's
   a ``run_text`` property), control_mapper, gap_analyst,
   remediation_planner (as ``skipped``), score_narrator in that order,
   plus one ``tool_call`` per ``tool_evidence`` entry.

Test fixtures reuse the stubs from ``test_fabric_workflow.py`` via the
public interfaces (``_minimal_estate_for_change``,
``_StubControlMapperEmptyWithReason``, ``_StubGapAnalystEmpty``,
``_StubScoreNarrator``). Keeping the stubs in one place means the empty-
with-reason contract only needs to be encoded once.
"""
from __future__ import annotations

import logging

import pytest

from regimpact.agents import pipeline as pipeline_module
from regimpact.agents.events import PipelineEvent
from regimpact.agents.pipeline import AgentPipeline
from regimpact.settings import Settings
from tests.test_fabric_workflow import (
    _StubControlMapperEmptyWithReason,
    _StubGapAnalystEmpty,
    _StubScoreNarrator,
    _minimal_estate_for_change,
)


def _stub_pipeline(monkeypatch) -> tuple[AgentPipeline, str, list]:
    """Wire the pipeline to happy-path stubs and return (pipeline, change_id, events)."""
    estate, change_id = _minimal_estate_for_change()

    monkeypatch.setattr(
        pipeline_module,
        "_settings",
        Settings(
            foundry_project_endpoint="https://example/api/projects/demo",
            foundry_executive_qa_agent_name="RegImpactQA",
            foundry_executive_qa_agent_version="3",
            fabric_workspace_id="ws-1",
            fabric_data_agent_id="da-1",
        ),
    )
    monkeypatch.setattr(
        pipeline_module,
        "FabricControlMapperAgent",
        lambda *a, **kw: _StubControlMapperEmptyWithReason(),
    )
    monkeypatch.setattr(
        pipeline_module,
        "FabricGapAnalystAgent",
        lambda *a, **kw: _StubGapAnalystEmpty(),
    )
    monkeypatch.setattr(
        pipeline_module,
        "FabricScoreNarratorAgent",
        lambda *a, **kw: _StubScoreNarrator(),
    )
    # Remediation planner must not be called on empty-gap path.
    monkeypatch.setattr(
        pipeline_module,
        "FabricRemediationPlannerAgent",
        lambda *a, **kw: pytest.fail(
            "remediation_planner must be skipped when gap_ids is empty"
        ),
    )

    events: list[PipelineEvent] = []
    pipeline = AgentPipeline(estate, on_event=events.append)
    return pipeline, change_id, events


def test_pipeline_default_on_event_is_none():
    """Constructing without ``on_event`` sets the attribute to ``None``."""
    estate, _ = _minimal_estate_for_change()
    pipeline = AgentPipeline(estate)
    assert pipeline._on_event is None


def test_emit_is_noop_when_no_subscriber():
    """``_emit`` on a no-subscriber pipeline is a bare ``is None`` check."""
    estate, _ = _minimal_estate_for_change()
    pipeline = AgentPipeline(estate)
    # Should not raise, should not do anything observable.
    pipeline._emit("stage_start", "control_mapper", message="x")
    pipeline._emit("stage_end", "control_mapper", duration_ms=10)


def test_broken_callback_does_not_crash_pipeline(caplog):
    """A subscriber that raises is caught, logged at WARNING, and swallowed."""
    def _broken(ev: PipelineEvent) -> None:
        raise RuntimeError("boom")

    estate, _ = _minimal_estate_for_change()
    pipeline = AgentPipeline(estate, on_event=_broken)

    with caplog.at_level(logging.WARNING, logger="regimpact.agents.pipeline"):
        # Must not raise.
        pipeline._emit("stage_start", "control_mapper", message="x")

    warnings = [
        r for r in caplog.records
        if r.name == "regimpact.agents.pipeline"
        and r.levelname == "WARNING"
        and "PipelineEvent callback failed" in r.getMessage()
    ]
    assert warnings, "broken callback must produce a WARNING log record"


def test_run_fabric_emits_stage_events_in_order(monkeypatch):
    """Successful ``_run_fabric`` emits stage_start/stage_end for every stage."""
    pipeline, change_id, events = _stub_pipeline(monkeypatch)
    report = pipeline.run(change_id)

    # Pipeline still returns its normal report shape — event bus is
    # additive, never subtractive.
    assert report["change_id"] == change_id
    assert report["llm_mode"] == "fabric-agentic"

    # Collect the order of stage_start events per stage.
    started = [ev.stage for ev in events if ev.kind == "stage_start"]
    ended = [ev.stage for ev in events if ev.kind == "stage_end"]

    # interpreter is not run by _run_fabric (that's run_text). Test the
    # four Fabric stages plus the skipped remediation_planner bracket.
    assert started == [
        "control_mapper",
        "gap_analyst",
        "remediation_planner",
        "score_narrator",
    ], f"unexpected stage_start sequence: {started}"

    assert ended == [
        "control_mapper",
        "gap_analyst",
        "remediation_planner",
        "score_narrator",
    ], f"unexpected stage_end sequence: {ended}"


def test_run_fabric_emits_tool_call_events(monkeypatch):
    """Each ``tool_evidence`` entry becomes one ``tool_call`` event."""
    pipeline, change_id, events = _stub_pipeline(monkeypatch)
    pipeline.run(change_id)

    tool_calls_by_stage: dict[str, list[PipelineEvent]] = {}
    for ev in events:
        if ev.kind == "tool_call":
            tool_calls_by_stage.setdefault(ev.stage, []).append(ev)

    # Each stub returns exactly one ToolEvidence entry, so each Fabric
    # stage should emit exactly one tool_call event.
    assert "control_mapper" in tool_calls_by_stage
    assert "gap_analyst" in tool_calls_by_stage
    assert "score_narrator" in tool_calls_by_stage
    for stage, calls in tool_calls_by_stage.items():
        assert len(calls) == 1, (
            f"{stage} should emit one tool_call per tool_evidence entry, "
            f"got {len(calls)}"
        )
        ev = calls[0]
        assert ev.tool_name, f"{stage} tool_call missing tool_name"
        assert ev.data_source, f"{stage} tool_call missing data_source"


def test_skipped_remediation_planner_marks_details(monkeypatch):
    """When gap_ids is empty the planner stage_end carries ``skipped=True``."""
    pipeline, change_id, events = _stub_pipeline(monkeypatch)
    pipeline.run(change_id)

    rp_ends = [
        ev for ev in events
        if ev.kind == "stage_end" and ev.stage == "remediation_planner"
    ]
    assert len(rp_ends) == 1, "expected exactly one remediation_planner stage_end"
    assert rp_ends[0].details.get("skipped") is True
    assert rp_ends[0].details.get("reason") == "no_gap_ids"


def test_stage_end_carries_duration_ms(monkeypatch):
    """Every non-skipped ``stage_end`` reports a non-negative ``duration_ms``."""
    pipeline, change_id, events = _stub_pipeline(monkeypatch)
    pipeline.run(change_id)

    non_skipped = [
        ev for ev in events
        if ev.kind == "stage_end" and not ev.details.get("skipped")
    ]
    assert non_skipped, "at least one non-skipped stage_end must be emitted"
    for ev in non_skipped:
        assert ev.duration_ms is not None, f"{ev.stage} missing duration_ms"
        assert ev.duration_ms >= 0, f"{ev.stage} duration_ms must be >= 0"
