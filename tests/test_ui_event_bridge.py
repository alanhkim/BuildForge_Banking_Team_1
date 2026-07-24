"""Unit tests for ``regimpact.ui.event_bridge``.

Pure-logic tests — no Streamlit runtime needed. Verifies that
:class:`RunState` correctly folds :class:`PipelineEvent` sequences that
the pipeline actually emits.
"""
from __future__ import annotations

from regimpact.agents.events import PipelineEvent
from regimpact.ui.event_bridge import (
    STAGE_KEYS,
    RunState,
    format_stage_detail,
)


def _mk(kind, stage, **kwargs) -> PipelineEvent:
    return PipelineEvent(kind=kind, stage=stage, **kwargs)


def test_runstate_initialises_all_stages_pending() -> None:
    run = RunState()
    assert set(run.stages.keys()) == set(STAGE_KEYS)
    for key in STAGE_KEYS:
        assert run.stages[key].status == "pending"
        assert run.stages[key].duration_ms is None
        assert run.stages[key].tool_count == 0


def test_stage_start_marks_running_and_stores_message() -> None:
    run = RunState()
    run.apply(_mk("stage_start", "interpreter", message="Interpreting text"))
    assert run.stages["interpreter"].status == "running"
    assert run.stages["interpreter"].detail == "Interpreting text"


def test_stage_end_marks_done_with_duration_and_detail() -> None:
    run = RunState()
    run.apply(_mk("stage_start", "interpreter"))
    run.apply(
        _mk(
            "stage_end",
            "interpreter",
            duration_ms=12345,
            details={"obligations": 7, "mode": "fabric-agentic"},
        )
    )
    stage = run.stages["interpreter"]
    assert stage.status == "done"
    assert stage.duration_ms == 12345
    assert "7 obligations" in stage.detail
    assert "fabric-agentic" in stage.detail


def test_stage_end_with_skipped_flag_becomes_skipped() -> None:
    run = RunState()
    run.apply(
        _mk(
            "stage_end",
            "onelake_files",
            details={"skipped": True, "reason": "FABRIC_* not configured"},
        )
    )
    stage = run.stages["onelake_files"]
    assert stage.status == "skipped"
    assert stage.detail == "FABRIC_* not configured"


def test_stage_error_captures_message_and_marks_error() -> None:
    run = RunState()
    run.apply(_mk("stage_start", "control_mapper"))
    run.apply(
        _mk(
            "stage_error",
            "control_mapper",
            message="Foundry endpoint unreachable",
            duration_ms=800,
        )
    )
    stage = run.stages["control_mapper"]
    assert stage.status == "error"
    assert "Foundry endpoint unreachable" in stage.detail


def test_tool_call_increments_counter_and_records_line() -> None:
    run = RunState()
    run.apply(_mk("stage_start", "gap_analyst"))
    run.apply(
        _mk(
            "tool_call",
            "gap_analyst",
            tool_name="fabric_query",
            data_source="controls_lakehouse",
            message="SELECT * FROM controls WHERE id IN (...)",
        )
    )
    run.apply(
        _mk(
            "tool_call",
            "gap_analyst",
            tool_name="fabric_query",
            data_source="evidence_lakehouse",
        )
    )
    stage = run.stages["gap_analyst"]
    assert stage.tool_count == 2
    assert len(stage.tool_lines) == 2
    assert "controls_lakehouse" in stage.tool_lines[0]
    assert "evidence_lakehouse" in stage.tool_lines[1]


def test_unknown_stage_is_silently_ignored() -> None:
    run = RunState()
    # Should not raise, should not mutate state.
    run.apply(_mk("stage_start", "future_stage_not_yet_wired"))
    for key in STAGE_KEYS:
        assert run.stages[key].status == "pending"


def test_apply_all_folds_a_full_pipeline_sequence() -> None:
    run = RunState()
    run.apply_all(
        [
            _mk("stage_start", "interpreter"),
            _mk(
                "stage_end",
                "interpreter",
                duration_ms=5000,
                details={"obligations": 4, "mode": "fabric-agentic"},
            ),
            _mk("stage_start", "control_mapper"),
            _mk(
                "stage_end",
                "control_mapper",
                duration_ms=8000,
                details={"mappings": 12, "controls": 20},
            ),
            _mk("stage_start", "gap_analyst"),
            _mk(
                "stage_end",
                "gap_analyst",
                duration_ms=3000,
                details={"findings": 6},
            ),
            _mk("stage_start", "remediation_planner"),
            _mk(
                "stage_end",
                "remediation_planner",
                duration_ms=4000,
                details={"actions": 5, "total_effort_days": 120},
            ),
            _mk("stage_start", "score_narrator"),
            _mk(
                "stage_end",
                "score_narrator",
                duration_ms=2000,
                details={
                    "as_is": 82.0,
                    "post_change": 61.0,
                    "post_remediation": 78.5,
                },
            ),
        ]
    )
    assert run.stages["interpreter"].status == "done"
    assert run.stages["control_mapper"].status == "done"
    assert run.stages["gap_analyst"].status == "done"
    assert run.stages["remediation_planner"].status == "done"
    assert run.stages["score_narrator"].status == "done"
    assert "12 mappings" in run.stages["control_mapper"].detail
    assert "6 gaps" in run.stages["gap_analyst"].detail
    assert "5 actions" in run.stages["remediation_planner"].detail
    assert "120d effort" in run.stages["remediation_planner"].detail
    assert "82.0" in run.stages["score_narrator"].detail
    assert "78.5" in run.stages["score_narrator"].detail


# ---------------------------------------------------------------------- #
# format_stage_detail — direct coverage of the formatter branches so
# any drift from the CLI version surfaces quickly.
# ---------------------------------------------------------------------- #
def test_format_stage_detail_interpreter() -> None:
    out = format_stage_detail(
        "interpreter", {"obligations": 3, "mode": "fabric-agentic"}, "fallback"
    )
    assert out == "3 obligations · mode=fabric-agentic"


def test_format_stage_detail_control_mapper_with_empty_reason() -> None:
    out = format_stage_detail(
        "control_mapper",
        {"mappings": 0, "controls": 10, "empty_with_reason": True},
        "fallback",
    )
    assert "0 mappings" in out
    assert "empty-with-reason" in out


def test_format_stage_detail_remediation_skipped() -> None:
    out = format_stage_detail(
        "remediation_planner",
        {"skipped": True, "reason": "no gaps to plan"},
        "fallback",
    )
    assert out == "no gaps to plan"


def test_format_stage_detail_unknown_stage_uses_fallback() -> None:
    assert (
        format_stage_detail("some_new_stage", {}, "fallback text")
        == "fallback text"
    )


def test_format_stage_detail_message_passthrough() -> None:
    """Stages that only carry a ``message`` field (OneLake, Purview,
    Report) render that string as the detail."""
    out = format_stage_detail(
        "onelake_files", {"message": "3 raw + 8 gold file(s)"}, "fallback"
    )
    assert out == "3 raw + 8 gold file(s)"
