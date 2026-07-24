"""Streamlit rendering helpers for the operator UI.

Kept separate from :mod:`streamlit_app` so the view functions can be
iterated on without touching the app wiring. All functions accept a
``st`` module handle rather than importing streamlit at module load —
this keeps the module import-safe when the ``[ui]`` extra is missing,
mirroring the pattern used by :mod:`event_bridge`.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from regimpact.ui.event_bridge import RunState, StageState

_STATUS_ICON = {
    "pending":  "⋯",
    "running":  "⏳",
    "done":     "✅",
    "error":    "❌",
    "skipped":  "⊘",
}


def _status_cell(stage: StageState) -> str:
    icon = _STATUS_ICON.get(stage.status, "⋯")
    if stage.status == "done" and stage.duration_ms is not None:
        return f"{icon} {stage.duration_ms/1000:.1f}s"
    if stage.status == "running":
        return f"{icon} running…"
    if stage.status == "error":
        return f"{icon} error"
    if stage.status == "skipped":
        return f"{icon} skipped"
    return icon


def render_stage_table(st, run: RunState, *, verbose: bool = False) -> None:
    """Render the live stage progress table.

    Uses a plain Streamlit dataframe so it updates cleanly on rerun.
    When ``verbose`` is set, appends tool-call lines under each stage
    as a caption block below the table.

    For any stage in the ``error`` state the full ``stage.detail`` is
    also rendered below the table inside an ``st.expander`` (auto-opened)
    with a monospace ``st.code`` block. Dataframe cells truncate long
    strings with an ellipsis, so this is the only place operators can
    reliably read (and copy) a full Foundry / Fabric error body.
    """
    import pandas as pd

    rows = []
    for stage in run.stages.values():
        detail = stage.detail
        if stage.tool_count and not verbose:
            suffix = f"  · {stage.tool_count} tool call(s)"
            detail = (detail + suffix) if detail else suffix.strip()
        rows.append(
            {
                "Stage": stage.label,
                "Status": _status_cell(stage),
                "Detail": detail,
            }
        )
    df = pd.DataFrame(rows)
    st.dataframe(
        df,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Stage": st.column_config.TextColumn("Stage", width="small"),
            "Status": st.column_config.TextColumn("Status", width="small"),
            # ``width="large"`` widens the cell; hovering shows the full
            # text in a tooltip. For errors we still fall back to the
            # expander below because dataframe tooltips are single-line.
            "Detail": st.column_config.TextColumn("Detail", width="large"),
        },
    )

    # Full error bodies — one expander per errored stage, auto-opened so
    # the operator sees the full 400 payload without extra clicks.
    for stage in run.stages.values():
        if stage.status == "error" and stage.detail:
            with st.expander(
                f"❌ {stage.label} — full error", expanded=True
            ):
                st.code(stage.detail, language="text")

    if verbose:
        for stage in run.stages.values():
            if not stage.tool_lines:
                continue
            with st.expander(f"↳ {stage.label} — {stage.tool_count} tool call(s)"):
                for line in stage.tool_lines:
                    st.caption(f"• {line}")


def render_scores(st, scores: dict[str, Any]) -> None:
    """Render the before/after compliance score panel."""
    col1, col2, col3 = st.columns(3)
    col1.metric("As-is (baseline)", f"{scores['as_is']}%")
    col2.metric(
        "Post-change",
        f"{scores['post_change']}%",
        delta=f"-{scores['score_drop']}",
        delta_color="inverse",
    )
    col3.metric(
        "Post-remediation",
        f"{scores['post_remediation']}%",
        delta=f"+{scores['score_recovered']}",
    )
    st.caption(
        f"Regulation compliance for this change: **{scores['regulation_compliance']}%**"
    )
    if scores.get("weakest_capabilities"):
        st.markdown("**Weakest capabilities**")
        for cap in scores["weakest_capabilities"]:
            st.markdown(f"- {cap['capability']} — {cap['score']}%")


def render_agent_readout(st, report: dict[str, Any]) -> None:
    """Render the per-agent summary + narrative."""
    import pandas as pd

    st.markdown(f"**Pipeline mode:** `{report.get('llm_mode', '?')}`")
    if "interpreted_obligations" in report:
        st.markdown(
            f"**Interpreted obligations:** {report['interpreted_obligations']}"
        )
    agents_df = pd.DataFrame(report.get("agents", []))
    if not agents_df.empty:
        st.dataframe(agents_df, use_container_width=True, hide_index=True)
    narrative = report.get("remediation", {}).get("narrative")
    if narrative:
        st.markdown("**Remediation narrative**")
        st.info(narrative)


def render_filtered_csv(
    st,
    csv_path: Path,
    *,
    change_id: str,
    change_col: str = "change_id",
    empty_message: str = "No rows for this change.",
) -> None:
    """Load a CSV, filter by change_id, and render it.

    Silently handles the two failure modes we care about for the POC:
    (a) file missing (pipeline was interrupted before this stage);
    (b) column missing (schema drift). Neither should crash the UI.
    """
    import pandas as pd

    if not csv_path.exists():
        st.warning(f"Not produced yet: `{csv_path.name}`")
        return
    df = pd.read_csv(csv_path)
    if change_col in df.columns:
        df = df[df[change_col] == change_id]
    if df.empty:
        st.info(empty_message)
        return
    st.dataframe(df, use_container_width=True, hide_index=True)
    st.caption(f"{len(df)} row(s) from `{csv_path.name}`")


def render_report_markdown(st, report_path: Path) -> None:
    """Render the generated Markdown impact report inline."""
    if not report_path.exists():
        st.warning(f"Report not produced: `{report_path.name}`")
        return
    st.markdown(report_path.read_text(encoding="utf-8"))


def render_downloads(st, files: list[Path]) -> None:
    """Render one download button per file that exists."""
    shown = 0
    for path in files:
        if not path.exists():
            continue
        with open(path, "rb") as handle:
            st.download_button(
                label=f"⬇  {path.name}",
                data=handle.read(),
                file_name=path.name,
                mime="application/octet-stream",
                key=f"dl_{path.name}",
            )
        shown += 1
    if shown == 0:
        st.info("No downloadable artifacts found yet.")
