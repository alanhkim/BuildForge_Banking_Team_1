"""Shared post-pipeline export helper.

Extracted from ``regimpact.cli._run_interpret`` so that the Streamlit UI
worker (``regimpact.ui.streamlit_app._run_pipeline_in_thread``) can run
the *same* post-pipeline export sequence the CLI runs. Without this the
UI's 4 tail stages (``onelake_files``, ``onelake_tables``, ``purview``,
``report``) would sit as perpetual "pending" rows because
:meth:`AgentPipeline.run_text` legitimately terminates at
``score_narrator`` — the Foundry/Fabric agent pipeline ends at the agent
boundary; exports are I/O plumbing that lives outside it.

Failure-class contract (mirrors ``decisions.md`` §0 verbatim)
-------------------------------------------------------------
* :class:`~regimpact.lakehouse.LakehouseNotConfiguredError` — SOFT SKIP.
  Emitted as ``stage_end`` with ``skipped=True``. In non-silent mode a
  yellow console note is printed. The local Parquet under ``output/``
  remains the source of truth; the operator can rerun once configured.
* :class:`~regimpact.lakehouse.LakehouseWriteError` — NON-FATAL ERROR.
  Emitted as ``stage_error``. In non-silent mode a red console note is
  printed. The pipeline continues to the next stage because local
  Parquet already succeeded — this is best-effort writeback.
* Anything else propagates — no broader ``except`` here.

The four ``_emit_stage*`` helpers live in this module (not the CLI)
because they belong next to their only caller.
"""
from __future__ import annotations

import time
from typing import Any, Callable, Optional

from rich.console import Console

from ..export import export_graph, export_report, export_tables
from ..gold import export_gold
from ..lakehouse import (
    LakehouseNotConfiguredError,
    LakehouseWriteError,
    export_regimpact_lakehouse,
    export_regimpact_tables,
)
from ..purview import export_purview
from .events import PipelineEvent

EventCallback = Optional[Callable[[PipelineEvent], None]]


# ---------------------------------------------------------------------- #
# Stage emission helpers (moved verbatim from cli.py — same behaviour)
# ---------------------------------------------------------------------- #
def _emit_stage(callback: EventCallback, stage: str, message: str) -> float:
    """Emit ``stage_start`` for a post-pipeline stage and return the start time."""
    if callback is None:
        return 0.0
    callback(PipelineEvent(kind="stage_start", stage=stage, message=message))
    return time.perf_counter()


def _emit_stage_end(
    callback: EventCallback,
    stage: str,
    started: float,
    *,
    details: dict | None = None,
) -> None:
    if callback is None:
        return
    callback(
        PipelineEvent(
            kind="stage_end",
            stage=stage,
            duration_ms=int((time.perf_counter() - started) * 1000),
            details=details or {},
        )
    )


def _emit_stage_skipped(callback: EventCallback, stage: str, reason: str) -> None:
    """Emit ONLY the terminal ``stage_end`` for a soft-skip.

    ``_emit_stage`` has already fired ``stage_start`` at the top of every
    export block, so this helper must NOT re-emit it — that would produce
    two starts for one logical stage and confuse renderers that expect
    the standard start → end pair.
    """
    if callback is None:
        return
    callback(
        PipelineEvent(
            kind="stage_end",
            stage=stage,
            duration_ms=0,
            details={"skipped": True, "reason": reason},
        )
    )


def _emit_stage_error(
    callback: EventCallback, stage: str, started: float, message: str
) -> None:
    if callback is None:
        return
    callback(
        PipelineEvent(
            kind="stage_error",
            stage=stage,
            message=message,
            duration_ms=int((time.perf_counter() - started) * 1000),
        )
    )


# ---------------------------------------------------------------------- #
# Public entrypoint
# ---------------------------------------------------------------------- #
def run_post_pipeline_exports(
    *,
    estate: Any,
    pipeline: Any,
    report: dict,
    settings: Any,
    on_event: EventCallback = None,
    silent: bool = True,
) -> None:
    """Run every export the CLI's ``interpret`` command runs after ``run_text``.

    Parameters
    ----------
    estate
        The estate object produced by :func:`regimpact.generator.generate_estate`
        and mutated by the pipeline's ``_inject`` + Fabric-writeback stages.
    pipeline
        The :class:`~regimpact.agents.pipeline.AgentPipeline` instance that just
        finished ``run_text``. Only used to call
        :meth:`AgentPipeline.build_engine_summary` for the report stage — the
        engine summary is intentionally re-derived from the estate rather than
        by re-running ``ImpactEngine.analyze_change()``, which would overwrite
        Fabric's authoritative gaps/remediations with local Python analysis.
    report
        The dict returned by :meth:`AgentPipeline.run_text`. We read
        ``report["change_id"]`` to build the engine summary.
    settings
        A :class:`regimpact.settings.Settings` instance (or duck-typed stand-in
        for tests). We read the output directories and Fabric config off it.
    on_event
        Optional :class:`PipelineEvent` callback. When ``None`` the stage
        helpers are no-ops. Same contract as
        :attr:`AgentPipeline._on_event` — broken subscribers do NOT crash the
        exports because emission funnels through the pipeline's ``_emit``…
        actually no: these are CLI-level stages emitted DIRECTLY into
        ``on_event`` (there is no shared safety wrapper here). Callers must
        pass a callback that does not raise, matching the CLI's existing
        contract. The Streamlit worker pushes into a ``queue.Queue`` which
        cannot fail under normal operation.
    silent
        When ``True`` (default), no ``console.print`` calls fire — appropriate
        for the streaming CLI path (Live renderer drives the display) and for
        the Streamlit worker (events flow through ``on_event``). When
        ``False`` the legacy green/yellow/red console messages are restored so
        the non-streaming CLI path keeps its CI-log-friendly output.

    Contract
    --------
    * Runs the three unconditional local exports (tables, gold, graph)
      inside a tracked ``local_exports`` stage so failures surface in the
      UI rather than crashing invisibly before downstream stages emit.
    * Runs four bracketed stages: ``onelake_files``, ``onelake_tables``,
      ``purview``, ``report``. Each emits ``stage_start`` + a terminal event
      (``stage_end``, skipped ``stage_end``, or ``stage_error``).
    * The two OneLake stages catch :class:`LakehouseNotConfiguredError` and
      :class:`LakehouseWriteError` and continue. Nothing else is caught.
    * ``local_exports`` re-raises after emitting ``stage_error`` — a bad
      gold star-schema means the operator sees the crash immediately and
      downstream stages stay pending (visually indicating they never ran).
    * Purview + report stages have no swallow list — any failure propagates
      to the caller (which is the desired behaviour: bad glossary export or a
      missing report template means the operator sees a red banner, not a
      "success" run with silently missing outputs).
    """
    console = Console() if not silent else None

    # -- Stage: local_exports ---------------------------------------------- #
    # The three unconditional local exports (tables, gold, graph) run in a
    # tracked stage so failures are visible in the streaming renderer / UI
    # stage table. Prior to this bracket, an ``export_gold`` referential-
    # integrity failure would crash BEFORE any downstream stage emitted a
    # ``stage_start``, leaving ``onelake_files`` / ``onelake_tables`` /
    # ``purview`` / ``report`` visually stuck at "pending" forever. Wrapping
    # them makes the crash surface as a ``stage_error`` row and preserves
    # the visible fact that downstream stages never ran.
    _t = _emit_stage(on_event, "local_exports", "Writing tables + gold + graph")
    try:
        export_tables(estate, settings.tables_dir)
        export_gold(estate, settings.gold_dir)
        export_graph(estate, settings.graph_dir)
        _emit_stage_end(on_event, "local_exports", _t)
    except Exception as exc:
        _emit_stage_error(on_event, "local_exports", _t, str(exc))
        if console is not None:
            console.print(f"[red]Local export failed:[/] {exc}")
        raise

    # -- Stage: onelake_files ---------------------------------------------- #
    # Push Parquet tables to the configured Fabric lakehouse (best-effort).
    # Uploads raw entity tables to Files/regimpact_raw/ and the gold star
    # schema to Files/regimpact_gold/, matching the layout the PySpark loader
    # notebook (src/regimpact/01_load_lakehouse.ipynb) reads. Delta table +
    # view materialization is owned by that notebook inside Fabric; Python's
    # responsibility ends at Parquet-in-Files/.
    _t = _emit_stage(on_event, "onelake_files", "Uploading Parquet to Files/")
    try:
        uploaded = export_regimpact_lakehouse(
            settings.tables_dir,
            settings.gold_dir,
            workspace_id=settings.fabric_workspace_id,
            lakehouse_id=settings.fabric_lakehouse_id,
            onelake_endpoint=settings.fabric_onelake_dfs_endpoint,
        )
        _emit_stage_end(
            on_event,
            "onelake_files",
            _t,
            details={
                "raw": len(uploaded["raw"]),
                "gold": len(uploaded["gold"]),
                "message": f"{len(uploaded['raw'])} raw + "
                f"{len(uploaded['gold'])} gold file(s)",
            },
        )
        if not silent:
            console.print(
                f"[green]Uploaded to OneLake:[/] "
                f"[cyan]{len(uploaded['raw'])}[/] raw + "
                f"[cyan]{len(uploaded['gold'])}[/] gold file(s) into "
                f"[cyan]{settings.fabric_lakehouse_id}/Files/"
                f"{{regimpact_raw,regimpact_gold}}[/]"
            )
    except LakehouseNotConfiguredError:
        _emit_stage_skipped(on_event, "onelake_files", "FABRIC_* not configured")
        if not silent:
            console.print(
                "[yellow]OneLake upload skipped:[/] "
                "set FABRIC_WORKSPACE_ID and FABRIC_LAKEHOUSE_ID to enable."
            )
    except LakehouseWriteError as exc:
        _emit_stage_error(on_event, "onelake_files", _t, str(exc))
        if not silent:
            console.print(f"[red]OneLake upload failed:[/] {exc}")
        # Do NOT raise — local export succeeded, this is best-effort writeback.

    # -- Stage: onelake_tables --------------------------------------------- #
    # Materialise Parquet files as Delta tables directly in the lakehouse
    # ``Tables/`` area via delta-rs. Append mode — rows accumulate across
    # ``interpret`` runs. Views (v_impact / v_compliance / v_capability_health)
    # still live in the notebook because they need SQL, but the tables they
    # read from now materialise without any notebook click.
    _t = _emit_stage(on_event, "onelake_tables", "Appending Delta tables")
    try:
        delta_uploaded = export_regimpact_tables(
            settings.tables_dir,
            settings.gold_dir,
            workspace_id=settings.fabric_workspace_id,
            lakehouse_id=settings.fabric_lakehouse_id,
            onelake_endpoint=settings.fabric_onelake_dfs_endpoint,
        )
        _emit_stage_end(
            on_event,
            "onelake_tables",
            _t,
            details={
                "raw": len(delta_uploaded["raw"]),
                "gold": len(delta_uploaded["gold"]),
                "message": f"{len(delta_uploaded['raw'])} raw + "
                f"{len(delta_uploaded['gold'])} gold table(s)",
            },
        )
        if not silent:
            console.print(
                f"[green]📊 Wrote {len(delta_uploaded['raw'])} raw + "
                f"{len(delta_uploaded['gold'])} gold Delta table(s) to "
                f"lakehouse Tables/[/] (append)"
            )
    except LakehouseNotConfiguredError as exc:
        _emit_stage_skipped(on_event, "onelake_tables", str(exc)[:80])
        if not silent:
            console.print(f"[yellow]OneLake Delta writeback skipped:[/] {exc}")
    except LakehouseWriteError as exc:
        _emit_stage_error(on_event, "onelake_tables", _t, str(exc))
        if not silent:
            console.print(f"[red]OneLake Delta writeback failed:[/] {exc}")
        # Do NOT raise — Files/ upload above already succeeded, this is
        # a best-effort additional writeback for automatic table
        # materialisation.

    # -- Stage: purview ---------------------------------------------------- #
    _t = _emit_stage(on_event, "purview", "Exporting Purview glossary + lineage")
    export_purview(estate, settings.purview_dir)
    _emit_stage_end(on_event, "purview", _t)

    # -- Stage: report ----------------------------------------------------- #
    # Report is built from Fabric-persisted gaps/remediations in the estate.
    # We deliberately do NOT re-run ImpactEngine.analyze_change() here — that
    # would overwrite Fabric's authoritative outputs with local Python analysis.
    _t = _emit_stage(on_event, "report", "Building impact report")
    engine_summary = pipeline.build_engine_summary(report["change_id"])
    export_report(estate, engine_summary, settings.reports_dir)
    _emit_stage_end(on_event, "report", _t)
