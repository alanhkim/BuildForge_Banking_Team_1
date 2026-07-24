"""Command-line interface for the Regulatory Change Impact POC.

Usage (from project root, after `pip install -r requirements.txt`):

    python -m regimpact generate
    python -m regimpact analyze --change CHG-DORA
    python -m regimpact score --change CHG-DORA
    python -m regimpact interpret --file data/regulations/eu_ai_act_high_risk.txt \
        --regulation REG-AIACT --name "EU AI Act" --title "High-risk AI update"
    python -m regimpact ask-fabric "How compliant is DORA after remediation?"
    python -m regimpact demo
    python -m regimpact list-changes
"""
from __future__ import annotations

import sys
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

import typer
from rich.console import Console
from rich.live import Live
from rich.spinner import Spinner
from rich.table import Table

from .agents import AgentPipeline
from .agents.events import PipelineEvent
from .agents.exports import run_post_pipeline_exports
from .agents.foundry_client import (
    FabricDataAgentClient,
    FabricDataAgentError,
)
from .agents.foundry_interpreter import FoundryInterpreterError
from .audit import run_audit
from .export import export_graph, export_report, export_tables
from .generator import generate_estate
from .gold import export_gold
from .impact import ImpactEngine
from .purview import export_purview
from .scoring import score_change as score_change_fn
from .settings import settings

app = typer.Typer(add_completion=False, help="Regulatory Change Impact POC")
console = Console()


def _build() -> "object":
    return generate_estate(seed=settings.seed, as_of=settings.as_of)


def _fabric_data_agent_client() -> FabricDataAgentClient:
    """Create the Fabric Data Agent client from current CLI settings."""
    return FabricDataAgentClient.for_application_agent("executive_qa")


@app.command()
def generate() -> None:
    """Generate the synthetic estate and export tables + graph."""
    est = _build()
    tables = export_tables(est, settings.tables_dir, clean=True)
    graphs = export_graph(est, settings.graph_dir)
    console.print(f"[green]Generated estate[/]: {len(est.controls)} controls, "
                  f"{len(est.obligations)} obligations, {len(est.edges)} relationships.")
    console.print(f"Wrote {len(tables)} table files to [cyan]{settings.tables_dir}[/]")
    console.print(f"Wrote {len(graphs)} graph files to [cyan]{settings.graph_dir}[/]")


@app.command("list-changes")
def list_changes() -> None:
    """List the regulatory changes available to analyse."""
    est = _build()
    table = Table(title="Incoming Regulatory Changes")
    for col in ("Change ID", "Title", "Regulation", "Criticality", "Effective"):
        table.add_column(col)
    for c in est.changes:
        table.add_row(c.id, c.title, c.regulation_id, c.criticality.value, c.effective_date.isoformat())
    console.print(table)


@app.command()
def analyze(change: str = typer.Option(..., help="Change ID, e.g. CHG-DORA")) -> None:
    """Run impact analysis for one regulatory change."""
    est = _build()
    engine = ImpactEngine(est)
    summary = engine.analyze_change(change)
    export_tables(est, settings.tables_dir)
    report = export_report(est, summary, settings.reports_dir)
    _print_summary(summary)
    console.print(f"\nReport: [cyan]{report}[/]")


@app.command()
def score(change: str = typer.Option(..., help="Change ID, e.g. CHG-DORA")) -> None:
    """Compliance scorecard with the before / after-change / after-remediation story."""
    est = _build()
    ImpactEngine(est).analyze_change(change)
    result = score_change_fn(est, change)
    _print_scores(result)


@app.command("ask-fabric")
def ask_fabric(
    question: str = typer.Argument(..., help="Fabric-grounded question to ask."),
) -> None:
    """Ask a Foundry/Fabric-grounded compliance question."""
    try:
        response = _fabric_data_agent_client().ask(question)
    except FabricDataAgentError as exc:
        console.print(f"[red]Fabric Data Agent failed:[/] {exc}")
        raise typer.Exit(code=1) from exc

    console.print("\n[bold]Fabric-grounded answer[/]")
    console.print(response.answer)
    console.print(f"\nConfidence: [cyan]{response.confidence}[/]")
    _print_source_refs("Citations", response.citations)
    _print_tool_evidence(response.tool_evidence)


# ---------------------------------------------------------------------------
# Live streaming renderer for ``interpret`` — subscribes to PipelineEvent and
# refreshes a Rich table so the user sees each stage as it runs. Keeps the
# pipeline itself dependency-free (rich imports live here in the CLI layer).
# ---------------------------------------------------------------------------
_STREAM_STAGES: tuple[tuple[str, str], ...] = (
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


@dataclass
class _StageState:
    label: str
    status: str = "pending"  # pending | running | done | error | skipped
    detail: str = ""
    tool_count: int = 0
    tool_lines: list[str] = field(default_factory=list)
    duration_ms: int | None = None


def _format_stage_detail(stage_key: str, details: dict, fallback: str) -> str:
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
    return fallback


@contextmanager
def _streaming_renderer(verbose: bool):
    """Enter a ``rich.live.Live`` context and yield an event callback.

    Rendering is driven by a mutable ``dict[stage_key, _StageState]`` that
    the callback updates in-place on every event. ``verbose=True`` expands
    each stage row to show one ``↳`` line per tool_call; otherwise a
    collapsed ``· N tool call(s)`` suffix is shown.

    The renderer is idempotent to unknown ``stage`` values (silently
    ignored) so a future pipeline stage can emit events without breaking
    existing CLI builds.
    """
    state: dict[str, _StageState] = {
        key: _StageState(label=lbl) for key, lbl in _STREAM_STAGES
    }
    order = [key for key, _ in _STREAM_STAGES]

    def _render() -> Table:
        table = Table(
            title="regimpact interpret — live progress",
            show_lines=False,
        )
        table.add_column("Stage", no_wrap=True)
        table.add_column("Status", no_wrap=True, width=14)
        table.add_column("Detail", overflow="fold")
        for key in order:
            s = state[key]
            if s.status == "running":
                status_cell = Spinner("dots", text="running")
            elif s.status == "done":
                dur = f" {s.duration_ms/1000:.1f}s" if s.duration_ms else ""
                status_cell = f"[green]✓[/]{dur}"
            elif s.status == "error":
                status_cell = "[red]✗ error[/]"
            elif s.status == "skipped":
                status_cell = "[dim]skipped[/]"
            else:
                status_cell = "[dim]…[/]"
            detail = s.detail
            if s.tool_count and not verbose:
                suffix = f"  [dim]· {s.tool_count} tool call(s)[/]"
                detail = (detail + suffix) if detail else suffix.strip()
            if verbose and s.tool_lines:
                joined = "\n".join(f"  [dim]↳ {ln}[/]" for ln in s.tool_lines)
                detail = (detail + "\n" if detail else "") + joined
            table.add_row(s.label, status_cell, detail)
        return table

    live = Live(
        _render(),
        console=console,
        refresh_per_second=8,
        transient=False,
    )

    def _callback(ev: PipelineEvent) -> None:
        s = state.get(ev.stage)
        if s is None:
            return
        if ev.kind == "stage_start":
            s.status = "running"
            if ev.message:
                s.detail = ev.message
        elif ev.kind == "stage_end":
            if ev.details.get("skipped"):
                s.status = "skipped"
                s.detail = ev.details.get("reason") or s.detail
            else:
                s.status = "done"
                s.duration_ms = ev.duration_ms
                s.detail = _format_stage_detail(ev.stage, ev.details, s.detail)
        elif ev.kind == "stage_error":
            s.status = "error"
            s.detail = (ev.message or "unknown error")[:200]
        elif ev.kind == "tool_call":
            s.tool_count += 1
            if ev.tool_name:
                line = ev.tool_name
                if ev.data_source:
                    line += f" · {ev.data_source}"
                if ev.message:
                    line += f" — {ev.message[:60]}"
                s.tool_lines.append(line)
        live.update(_render())

    with live:
        yield _callback


@app.command()
def interpret(
    file: Path = typer.Option(..., help="Path to a regulation text file"),
    regulation: str = typer.Option(..., "--regulation", help="Regulation ID, e.g. REG-AIACT"),
    name: str = typer.Option(..., help="Regulation name, e.g. 'EU AI Act'"),
    title: str = typer.Option("Uploaded regulatory change", help="Change title"),
    stream: bool = typer.Option(
        True,
        "--stream/--no-stream",
        help="Render live per-stage progress (auto-off on non-TTY).",
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Expand each stage with per-tool-call detail lines.",
    ),
) -> None:
    """Run the Foundry-backed four-agent pipeline on a regulation document."""
    if not file.exists():
        console.print(f"[red]File not found:[/] {file}")
        raise typer.Exit(code=1)
    text = file.read_text(encoding="utf-8")
    est = _build()

    # Auto-disable streaming when the CLI is not attached to a TTY
    # (piped, redirected, CI). rich.live degrades to a garbled log
    # otherwise, and log-parsing consumers hate ANSI escape codes.
    use_stream = stream and sys.stdout.isatty()

    if use_stream:
        with _streaming_renderer(verbose=verbose) as on_event:
            _run_interpret(
                est,
                text=text,
                regulation=regulation,
                name=name,
                title=title,
                on_event=on_event,
            )
    else:
        _run_interpret(
            est,
            text=text,
            regulation=regulation,
            name=name,
            title=title,
            on_event=None,
        )


def _run_interpret(
    est,
    *,
    text: str,
    regulation: str,
    name: str,
    title: str,
    on_event,
) -> None:
    """Execute the interpret pipeline and post-pipeline exports.

    Split out from ``interpret`` so the streaming and non-streaming code
    paths share a single implementation. ``on_event`` is either a live
    renderer callback or ``None`` (silent mode — legacy console.print
    output is restored so CI logs remain useful).
    """
    silent = on_event is not None  # True when Live is driving the display

    pipeline = AgentPipeline(est, on_event=on_event)
    try:
        report = pipeline.run_text(
            text,
            regulation_id=regulation,
            regulation_name=name,
            change_title=title,
        )
    except FoundryInterpreterError as exc:
        if not silent:
            console.print(f"[red]Foundry interpreter failed:[/] {exc}")
        raise typer.Exit(code=1) from exc
    except FabricDataAgentError as exc:
        if not silent:
            console.print(f"[red]Fabric pipeline failed:[/] {exc}")
        raise typer.Exit(code=1) from exc

    # Post-pipeline exports (local Parquet + OneLake writeback + Purview +
    # report). Shared with the Streamlit UI worker via
    # ``regimpact.agents.exports.run_post_pipeline_exports`` so both surfaces
    # emit the same four tail-stage events (onelake_files, onelake_tables,
    # purview, report) and honour the same failure-class contract:
    # ``LakehouseNotConfiguredError`` = soft skip, ``LakehouseWriteError`` =
    # non-fatal error, everything else propagates.
    run_post_pipeline_exports(
        estate=est,
        pipeline=pipeline,
        report=report,
        settings=settings,
        on_event=on_event,
        silent=silent,
    )

    if not silent:
        # Legacy read-out — only shown when the live renderer is off, to
        # avoid duplicating the streamed table. Under streaming, the Live
        # table already shows all agent outcomes.
        console.print(
            f"\n[bold]Agent pipeline[/] — mode: [yellow]{report['llm_mode']}[/]"
        )
        agent_table = Table(title="Agent read-out")
        agent_table.add_column("Agent")
        agent_table.add_column("Result")
        for a in report["agents"]:
            agent_table.add_row(a["agent"], a["result"])
        console.print(agent_table)

    # Post-run summary — printed in BOTH modes. Under streaming this
    # renders AFTER the Live context has exited (see ``interpret``), so
    # the final scores + narrative land cleanly under the finished table.
    console.print(
        f"\nInterpreted obligations: [cyan]{report.get('interpreted_obligations', 0)}[/]"
    )
    console.print(f"Remediation narrative: {report['remediation']['narrative']}")
    _print_scores(report["scores"])


@app.command()
def demo() -> None:
    """Full end-to-end run: generate, analyse every change, score, export everything."""
    est = _build()
    engine = ImpactEngine(est)
    summaries = engine.analyze_all()
    for change_id in summaries:
        score_change_fn(est, change_id)
    export_tables(est, settings.tables_dir)
    export_gold(est, settings.gold_dir)
    export_graph(est, settings.graph_dir)
    export_purview(est, settings.purview_dir)
    for change_id, summary in summaries.items():
        export_report(est, summary, settings.reports_dir)
    table = Table(title="Regulatory Change Impact — Portfolio View")
    for col in ("Change", "Crit", "Obligations", "Gaps", "Effort (days)", "Products hit"):
        table.add_column(col)
    for s in summaries.values():
        table.add_row(
            s["change_id"], s["criticality"], str(s["obligations"]), str(s["gaps"]),
            str(s["total_effort_days"]), str(len(s["affected_products"])),
        )
    console.print(table)
    console.print("\nLLM agents: [yellow]Foundry/Fabric-first[/]")
    console.print(f"Tables : [cyan]{settings.tables_dir}[/]")
    console.print(f"Gold   : [cyan]{settings.gold_dir}[/]")
    console.print(f"Graph  : [cyan]{settings.graph_dir}[/]")
    console.print(f"Reports: [cyan]{settings.reports_dir}[/]")
    console.print(f"Purview: [cyan]{settings.purview_dir}[/]")


@app.command()
def audit(
    fresh: bool = typer.Option(
        True, help="Regenerate + analyse + export before auditing (recommended)."
    ),
) -> None:
    """Audit data-type uniformity (Rule 3) and referential integrity (Rule 4)."""
    est = _build()
    if fresh:
        engine = ImpactEngine(est)
        summaries = engine.analyze_all()
        for change_id in summaries:
            score_change_fn(est, change_id)
        export_tables(est, settings.tables_dir)

    reports = run_audit(est, settings.tables_dir)
    total_fail = 0
    for name, rep in reports.items():
        title = name.replace("_", " ").title()
        table = Table(title=f"Audit — {title}")
        for col in ("Check", "Status", "Detail"):
            table.add_column(col, overflow="fold")
        for f in rep.findings:
            colour = "green" if f.status == "PASS" else "red"
            detail = f.detail
            if f.offenders:
                shown = ", ".join(f.offenders[:5])
                more = f" (+{len(f.offenders) - 5} more)" if len(f.offenders) > 5 else ""
                detail = f"{detail}\n  offenders: {shown}{more}"
            table.add_row(f.check, f"[{colour}]{f.status}[/]", detail)
        console.print(table)
        fails = len(rep.failures)
        total_fail += fails
        verdict = "[green]PASS[/]" if fails == 0 else f"[red]{fails} FAIL[/]"
        console.print(f"  {title}: {verdict}  ({len(rep.findings)} checks)\n")

    if total_fail:
        console.print(f"[red bold]Audit failed:[/] {total_fail} issue(s) found.")
        raise typer.Exit(code=1)
    console.print("[green bold]Audit passed:[/] data types uniform and references intact.")


@app.command()
def gold(
    fresh: bool = typer.Option(
        True, help="Regenerate + analyse + score before projecting the star schema."
    ),
) -> None:
    """Project the estate into a Gold star schema (dims + facts) for the semantic model."""
    est = _build()
    if fresh:
        engine = ImpactEngine(est)
        summaries = engine.analyze_all()
        for change_id in summaries:
            score_change_fn(est, change_id)
    written, errors = export_gold(est, settings.gold_dir)
    tables = sorted({p.stem for p in written})
    console.print(f"[green]Gold star schema[/]: {len(tables)} tables -> [cyan]{settings.gold_dir}[/]")
    console.print("  " + ", ".join(tables))
    if errors:
        console.print("[red bold]Referential errors in Gold:[/]")
        for e in errors:
            console.print(f"  [red]{e}[/]")
        raise typer.Exit(code=1)
    console.print("[green]Star schema referential integrity: PASS[/] (fact -> dimension keys intact)")


def _print_summary(s: dict) -> None:
    console.print(f"\n[bold]{s['change_title']}[/] ([cyan]{s['change_id']}[/])")
    console.print(f"  Criticality      : {s['criticality']}")
    console.print(f"  Effective date   : {s['effective_date']}")
    console.print(f"  Obligations      : {s['obligations']}")
    console.print(f"  Gaps             : {s['gaps']}  {s['gaps_by_severity']}")
    console.print(f"  Remediation cost : {s['total_effort_days']} person-days")
    console.print(f"  Products affected: {', '.join(s['affected_products']) or 'None'}")
    console.print(f"  Systems affected : {', '.join(s['affected_systems']) or 'None'}")


def _print_scores(r: dict) -> None:
    console.print(f"\n[bold]Compliance score — before / after[/] ([cyan]{r['change_id']}[/])")
    table = Table()
    for col in ("Scenario", "Score"):
        table.add_column(col)
    table.add_row("As-is (baseline)", f"{r['as_is']}%")
    table.add_row("Post-change (gap exposed)", f"{r['post_change']}%  ([red]-{r['score_drop']}[/])")
    table.add_row("Post-remediation", f"{r['post_remediation']}%  ([green]+{r['score_recovered']}[/])")
    console.print(table)
    console.print(f"  Regulation compliance (this change): [cyan]{r['regulation_compliance']}%[/]")
    weakest = ", ".join(f"{w['capability']} ({w['score']}%)" for w in r["weakest_capabilities"])
    console.print(f"  Weakest capabilities: {weakest or 'None'}")


def _print_source_refs(title: str, source_refs: list) -> None:
    """Print Fabric citations or source references."""
    table = Table(title=title)
    for col in ("Source", "Type", "Name", "Value"):
        table.add_column(col, overflow="fold")
    for source_ref in source_refs:
        table.add_row(
            source_ref.source,
            source_ref.reference_type,
            source_ref.name,
            source_ref.value or "-",
        )
    console.print(table)


def _print_tool_evidence(tool_evidence: list) -> None:
    """Print tool-call provenance for the answer."""
    table = Table(title="Tool evidence")
    for col in ("Tool", "Data source", "Query"):
        table.add_column(col, overflow="fold")
    for evidence in tool_evidence:
        table.add_row(
            evidence.tool_name,
            evidence.data_source,
            evidence.query or "-",
        )
    console.print(table)


if __name__ == "__main__":
    app()
