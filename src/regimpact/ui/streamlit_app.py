"""Streamlit operator UI for the regimpact pipeline.

Run locally after ``pip install -e ".[ui,foundry,fabric]"``::

    streamlit run src/regimpact/ui/streamlit_app.py

The app is a thin viewer over the existing ``AgentPipeline`` — no
pipeline changes, no new persistence. It:

1. Accepts a regulation upload (or a catalog pick) + change metadata.
2. Runs :meth:`AgentPipeline.run_text` in a background thread while
   subscribing to :class:`PipelineEvent` via
   :class:`regimpact.ui.event_bridge.RunState`.
3. Streams the same 9-stage progress table the CLI renders.
4. On completion, shows tabs for Obligations, Gaps, Remediation,
   Scores, Agent read-out, Report, and Downloads — sourced from the
   files the pipeline already writes to ``output/``.

Design notes
------------
* Streamlit re-runs the script on every interaction. Long-lived state
  (the background run thread + its event queue + accumulated
  :class:`RunState`) lives in ``st.session_state``.
* The event loop polls the queue with a short sleep to keep the UI
  responsive without pinning a CPU. This is fine for a single-user
  POC; for multi-user hosting the app would move to Container Apps
  with each session getting its own process.
* No Foundry/Fabric fallback masking — a stage error surfaces as a
  red banner with the raw exception message, matching the CLI's
  "surface configuration failures explicitly" contract.
"""
from __future__ import annotations

import json
import queue
import re
import threading
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import streamlit as st

from regimpact.agents.exports import run_post_pipeline_exports
from regimpact.agents.pipeline import AgentPipeline
from regimpact.generator import generate_estate
from regimpact.settings import settings
from regimpact.ui.event_bridge import RunState
from regimpact.ui.renderers import (
    render_agent_readout,
    render_downloads,
    render_filtered_csv,
    render_report_markdown,
    render_scores,
    render_stage_table,
)

# ---------------------------------------------------------------------- #
# Page config
# ---------------------------------------------------------------------- #
st.set_page_config(
    page_title="Regulatory Impact Analyzer",
    page_icon="🏛",
    layout="wide",
)

# ---------------------------------------------------------------------- #
# Session-state bootstrap
# ---------------------------------------------------------------------- #
# ``current_run``  — active RunState the main pane is rendering.
# ``history``      — list[RunState] of finished runs (in-session only).
# ``event_queue``  — queue.Queue shared with the background worker; the
#                    worker writes PipelineEvents, the main loop drains.
# ``worker``       — the Thread running AgentPipeline.run_text.
# ``worker_error`` — captured exception + traceback if the thread died.
_SS_DEFAULTS: dict[str, Any] = {
    "current_run": None,
    "history": [],
    "event_queue": None,
    "worker": None,
    "worker_error": None,
    "run_result": None,
    "verbose": False,
    "form_change_id": "",  # derived once the pipeline builds it
}
for _k, _v in _SS_DEFAULTS.items():
    st.session_state.setdefault(_k, _v)


# ---------------------------------------------------------------------- #
# Filename → pipeline metadata
# ---------------------------------------------------------------------- #
# The pipeline requires ``regulation_id``, ``regulation_name`` and
# ``change_title`` on every run (they flow into the injected
# :class:`RegulatoryChange`, the ``CHG-*-UPLOAD`` change_id, and the
# generated impact report filename). Rather than making the analyst
# type them, derive them from the uploaded filename so a single
# file-picker click is enough to kick off a run.
_SLUG_RE = re.compile(r"[^A-Za-z0-9]+")


def _derive_metadata(filename: str) -> tuple[str, str, str]:
    """Turn ``eu_ai_act_high_risk.txt`` into
    (``REG-EU-AI-ACT-HIGH-RISK``, ``Eu Ai Act High Risk``,
    ``Uploaded: eu_ai_act_high_risk.txt``).

    The slug is uppercase, dash-separated, and stripped of any file
    extension. Digits are preserved. Empty/degenerate stems fall back
    to a timestamp-based ID so the pipeline never sees a blank value.
    """
    stem = filename.rsplit(".", 1)[0] if "." in filename else filename
    slug = _SLUG_RE.sub("-", stem).strip("-").upper()
    if not slug:
        slug = "UPLOAD-" + datetime.now(tz=timezone.utc).strftime("%Y%m%d%H%M%S")
    regulation_id = f"REG-{slug}"
    # Human name: replace separators with spaces, title-case each word.
    pretty = _SLUG_RE.sub(" ", stem).strip()
    regulation_name = pretty.title() if pretty else slug
    change_title = f"Uploaded: {filename}"
    return regulation_id, regulation_name, change_title


# ---------------------------------------------------------------------- #
# Disk persistence — survive page refresh
# ---------------------------------------------------------------------- #
# Streamlit's ``st.session_state`` is tied to the browser tab. A hard
# refresh, a file-watcher-triggered restart, or opening the app in a
# new tab all wipe it. To keep the analyst's live view resilient, the
# current run's ``RunState`` is mirrored to a JSON snapshot on disk
# after every event drain. On startup, if session_state has no
# ``current_run`` we rehydrate from disk. The worker thread itself
# cannot survive a process restart — but a *completed* run's results
# survive, and an *in-progress* snapshot at least shows the last known
# stage table until the worker eventually writes its output artifacts.
_STATE_DIR = Path(".streamlit-runs")
_CURRENT_STATE_FILE = _STATE_DIR / "current.json"


def _run_to_dict(run: RunState) -> dict[str, Any]:
    return {
        "label": run.label,
        "completed": run.completed,
        "error": run.error,
        "report": run.report,
        "stages": [
            {
                "key": s.key,
                "label": s.label,
                "status": s.status,
                "detail": s.detail,
                "tool_count": s.tool_count,
                "tool_lines": list(s.tool_lines),
                "duration_ms": s.duration_ms,
            }
            for s in run.stages.values()
        ],
    }


def _dict_to_run(data: dict[str, Any]) -> RunState:
    run = RunState(label=data.get("label", ""))
    run.completed = bool(data.get("completed", False))
    run.error = data.get("error")
    run.report = data.get("report")
    for saved in data.get("stages", []):
        key = saved.get("key")
        if key in run.stages:
            stage = run.stages[key]
            stage.status = saved.get("status", "pending")
            stage.detail = saved.get("detail", "")
            stage.tool_count = int(saved.get("tool_count", 0))
            stage.tool_lines = list(saved.get("tool_lines", []))
            stage.duration_ms = saved.get("duration_ms")
    return run


def _persist_run(run: RunState) -> None:
    """Snapshot ``run`` to disk. Best-effort — failures are swallowed
    because persistence is a resilience aid, not a correctness gate."""
    try:
        _STATE_DIR.mkdir(exist_ok=True)
        _CURRENT_STATE_FILE.write_text(
            json.dumps(_run_to_dict(run), indent=2, default=str),
            encoding="utf-8",
        )
    except Exception:  # pragma: no cover — persistence is best-effort
        pass


def _load_persisted_run() -> RunState | None:
    if not _CURRENT_STATE_FILE.exists():
        return None
    try:
        data = json.loads(_CURRENT_STATE_FILE.read_text(encoding="utf-8"))
        return _dict_to_run(data)
    except Exception:  # pragma: no cover — corrupt snapshot ignored
        return None


# Rehydrate from disk on the first rerun of a fresh session (e.g.
# after F5 or a Streamlit auto-restart). Only touch session_state if
# it is truly empty for this key — never overwrite a live run.
#
# Errored runs are NOT rehydrated: a page refresh should give the
# analyst a clean slate rather than resurrecting the last failure's
# traceback at the bottom of the page. The stale snapshot is also
# deleted so subsequent reloads stay clean.
if st.session_state.get("current_run") is None:
    _restored = _load_persisted_run()
    if _restored is not None and _restored.error:
        try:
            _CURRENT_STATE_FILE.unlink(missing_ok=True)
        except Exception:  # pragma: no cover — best-effort cleanup
            pass
        _restored = None
    if _restored is not None:
        st.session_state["current_run"] = _restored
        if _restored.completed and _restored.report:
            st.session_state["run_result"] = _restored.report
            if _restored.report.get("change_id"):
                st.session_state["form_change_id"] = _restored.report["change_id"]



# ---------------------------------------------------------------------- #
# Background pipeline runner
# ---------------------------------------------------------------------- #
def _run_pipeline_in_thread(
    text: str,
    regulation_id: str,
    regulation_name: str,
    change_title: str,
    event_q: "queue.Queue[Any]",
) -> None:
    """Worker target — pushes events + terminal ``('done', report)`` /
    ``('error', exc_str)`` sentinels into ``event_q``.

    Runs on its own thread so the Streamlit main loop stays responsive.
    All exceptions are captured — the Foundry / Fabric error contract
    forbids swallowing them silently, so the error message is pushed
    verbatim into the queue for the UI to render.
    """
    try:
        estate = generate_estate(seed=settings.seed, as_of=settings.as_of)
        pipeline = AgentPipeline(
            estate,
            on_event=lambda ev: event_q.put(("event", ev)),
        )
        report = pipeline.run_text(
            text,
            regulation_id=regulation_id,
            regulation_name=regulation_name,
            change_title=change_title,
        )
        # Post-pipeline exports (local Parquet + OneLake writeback + Purview
        # + report). Shared with the CLI's ``interpret`` command via
        # ``run_post_pipeline_exports`` so the UI's four tail-stage rows
        # (onelake_files, onelake_tables, purview, report) receive their
        # stage events instead of sitting forever as "pending". The helper
        # honours the same failure-class contract as the CLI —
        # ``LakehouseNotConfiguredError`` = soft skip,
        # ``LakehouseWriteError`` = non-fatal error, anything else
        # propagates to the ``except Exception`` below.
        run_post_pipeline_exports(
            estate=estate,
            pipeline=pipeline,
            report=report,
            settings=settings,
            on_event=lambda ev: event_q.put(("event", ev)),
            silent=True,
        )
        event_q.put(("done", report))
    except Exception as exc:  # pragma: no cover — surfaced to UI verbatim
        event_q.put(("error", f"{type(exc).__name__}: {exc}\n\n{traceback.format_exc()}"))


def _drain_queue_into_state(run: RunState, event_q: "queue.Queue[Any]") -> bool:
    """Drain all pending items from the queue into ``run``.

    Returns True if a terminal sentinel (``done`` / ``error``) was seen,
    signalling the caller to stop polling.
    """
    terminated = False
    while True:
        try:
            kind, payload = event_q.get_nowait()
        except queue.Empty:
            break
        if kind == "event":
            run.apply(payload)
        elif kind == "done":
            run.completed = True
            run.report = payload
            st.session_state["run_result"] = payload
            terminated = True
        elif kind == "error":
            run.completed = True
            run.error = payload
            terminated = True
    return terminated


# ---------------------------------------------------------------------- #
# Sidebar — input form
# ---------------------------------------------------------------------- #
with st.sidebar:
    st.markdown("### 🏛  Regulatory Impact")
    st.caption("Upload a regulation → watch the pipeline → review outputs.")
    st.divider()

    # --- Upload ------------------------------------------------------- #
    st.markdown("**📤 Upload regulation text**")
    uploaded = st.file_uploader(
        "Regulation document",
        type=["txt", "md"],
        help=(
            "Plain-text or Markdown regulation excerpt. "
            "Metadata (regulation ID, name, change title) is derived "
            "from the filename."
        ),
        key="uploaded_file",
    )
    if uploaded is not None:
        preview_id, preview_name, preview_title = _derive_metadata(uploaded.name)
        st.caption(
            f"→ **{preview_id}** · {preview_name}\n\n_{preview_title}_"
        )

    verbose = st.checkbox(
        "Verbose (show tool calls)",
        value=st.session_state["verbose"],
        key="input_verbose",
    )
    st.session_state["verbose"] = verbose

    running = (
        st.session_state.get("worker") is not None
        and st.session_state["worker"].is_alive()
    )
    disabled = running or uploaded is None

    if st.button(
        "▶  Run pipeline",
        type="primary",
        disabled=disabled,
        use_container_width=True,
    ):
        # Start a new run.
        text_bytes = uploaded.read()
        try:
            text = text_bytes.decode("utf-8")
        except UnicodeDecodeError:
            st.error("Uploaded file is not valid UTF-8 text.")
            st.stop()

        regulation_id, regulation_name, change_title = _derive_metadata(
            uploaded.name
        )

        new_run = RunState(
            label=(
                f"{regulation_id} · "
                f"{datetime.now(tz=timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
            ),
        )
        event_q: "queue.Queue[Any]" = queue.Queue()
        worker = threading.Thread(
            target=_run_pipeline_in_thread,
            kwargs={
                "text": text,
                "regulation_id": regulation_id,
                "regulation_name": regulation_name,
                "change_title": change_title,
                "event_q": event_q,
            },
            daemon=True,
        )
        worker.start()

        st.session_state["current_run"] = new_run
        st.session_state["event_queue"] = event_q
        st.session_state["worker"] = worker
        st.session_state["worker_error"] = None
        st.session_state["run_result"] = None
        st.session_state["form_change_id"] = (
            f"CHG-{regulation_id.replace('REG-', '')}-UPLOAD"
        )
        st.rerun()

    if running:
        st.caption("⏳ Pipeline is running…")

    # --- Session history ---------------------------------------------- #
    if st.session_state["history"]:
        st.divider()
        st.markdown("**📜 This session's runs**")
        for idx, past in enumerate(reversed(st.session_state["history"])):
            marker = "❌" if past.error else "✅"
            if st.button(
                f"{marker}  {past.label}",
                key=f"hist_{idx}",
                use_container_width=True,
            ):
                st.session_state["current_run"] = past
                st.session_state["run_result"] = past.report
                st.rerun()


# ---------------------------------------------------------------------- #
# Main pane
# ---------------------------------------------------------------------- #
st.title("Regulatory Impact Analyzer")
st.caption(
    "Foundry + Fabric agentic pipeline: Interpreter → Control Mapper → "
    "Gap Analyst → Remediation → Score Narrator."
)

run: RunState | None = st.session_state.get("current_run")

if run is None:
    st.info(
        "Upload a regulation and click **Run pipeline** to begin. "
        "Progress auto-refreshes every 5 seconds; results persist "
        "across page reloads."
    )
    st.stop()

# --- Stage progress ---------------------------------------------------- #
st.subheader("Pipeline progress")
stage_slot = st.empty()

# Pipeline is either running or has recently finished. We handle both
# in one path so the terminal ``('done', report)`` sentinel is never
# lost — the previous version gated queue draining on
# ``worker.is_alive()``, which meant a worker that exited between
# reruns left its final events undrained and the UI froze on
# "running" forever.
#
# Cadence: exactly one poll per ``_POLL_INTERVAL_S`` seconds. Each
# rerun drains everything currently in the queue (which may span
# multiple stages if the worker sprinted), snapshots to disk, then
# either exits (run complete) or sleeps + reruns (still running).
_POLL_INTERVAL_S = 5.0

worker = st.session_state.get("worker")
event_q = st.session_state.get("event_queue")

# Drain unconditionally when a queue exists. This catches the final
# events even when the worker thread has already exited.
if event_q is not None:
    _drain_queue_into_state(run, event_q)
    _persist_run(run)

# Render whatever we have now (fresh after any drain above).
with stage_slot.container():
    render_stage_table(st, run, verbose=st.session_state["verbose"])

# Decide what to do next based on RUN state, not worker state.
if worker is not None:
    if run.completed:
        # Terminal sentinel drained. Retire worker + queue and flip
        # to results view on the next rerun.
        st.session_state["worker"] = None
        st.session_state["event_queue"] = None
        if run not in st.session_state["history"]:
            st.session_state["history"].append(run)
        st.rerun()
    elif not worker.is_alive():
        # Thread died without emitting ``('done', ...)`` or
        # ``('error', ...)``. Surface as an error rather than
        # hanging forever.
        run.completed = True
        run.error = (
            "Pipeline worker exited unexpectedly with no terminal "
            "event. Check the terminal running Streamlit for the "
            "underlying traceback."
        )
        _persist_run(run)
        st.session_state["worker"] = None
        st.session_state["event_queue"] = None
        st.rerun()
    else:
        # Still running — wait one poll interval, then rerun.
        # ``time.sleep`` blocks this rerun's Python execution, but
        # Streamlit queues user clicks so they'll be picked up on the
        # next rerun (worst-case latency = _POLL_INTERVAL_S).
        time.sleep(_POLL_INTERVAL_S)
        st.rerun()

# --- Error banner ------------------------------------------------------ #
if run.error:
    st.error("Pipeline failed — see stack trace below.")
    with st.expander("Traceback", expanded=True):
        st.code(run.error, language="text")
    st.stop()

if not run.completed:
    st.stop()

# --- Results tabs ------------------------------------------------------ #
report = run.report or st.session_state.get("run_result") or {}
change_id = report.get("change_id") or st.session_state.get("form_change_id") or ""

if not change_id:
    st.warning("Pipeline completed but no change_id was returned.")
    st.stop()

st.divider()
st.subheader(f"Results — `{change_id}`")

tab_scores, tab_agents, tab_obl, tab_gaps, tab_rem, tab_report, tab_dl = st.tabs(
    [
        "📊 Scores",
        "🧠 Agent read-out",
        "📋 Obligations",
        "🚨 Gaps",
        "🛠 Remediation",
        "📄 Report",
        "⬇  Downloads",
    ]
)

with tab_scores:
    scores = report.get("scores")
    if scores:
        render_scores(st, scores)
    else:
        st.info("No scores in pipeline report.")

with tab_agents:
    render_agent_readout(st, report)

with tab_obl:
    render_filtered_csv(
        st,
        settings.tables_dir / "obligations.csv",
        change_id=change_id,
        empty_message="No obligations recorded for this change.",
    )

with tab_gaps:
    render_filtered_csv(
        st,
        settings.tables_dir / "gaps.csv",
        change_id=change_id,
        empty_message="No gaps recorded for this change.",
    )

with tab_rem:
    render_filtered_csv(
        st,
        settings.tables_dir / "remediation_actions.csv",
        change_id=change_id,
        empty_message="No remediation actions recorded for this change.",
    )

with tab_report:
    render_report_markdown(
        st,
        settings.reports_dir / f"impact_{change_id}.md",
    )

with tab_dl:
    st.caption("Key artifacts produced by this run.")
    render_downloads(
        st,
        [
            settings.reports_dir / f"impact_{change_id}.md",
            settings.tables_dir / "obligations.parquet",
            settings.tables_dir / "gaps.parquet",
            settings.tables_dir / "remediation_actions.parquet",
            settings.tables_dir / "compliance_scores.parquet",
            settings.gold_dir / "fact_compliance_score.parquet",
            settings.gold_dir / "fact_gap.parquet",
            settings.gold_dir / "fact_remediation.parquet",
        ],
    )
