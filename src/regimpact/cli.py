"""Command-line interface for the Regulatory Change Impact POC.

Usage (from project root, after `pip install -r requirements.txt`):

    python -m regimpact generate
    python -m regimpact analyze --change CHG-DORA
    python -m regimpact score --change CHG-DORA
    python -m regimpact interpret --file data/regulations/eu_ai_act_high_risk.txt \
        --regulation REG-AIACT --name "EU AI Act" --title "High-risk AI update"
    python -m regimpact demo
    python -m regimpact list-changes
"""
from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from .agents import AgentPipeline
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


@app.command()
def interpret(
    file: Path = typer.Option(..., help="Path to a regulation text file"),
    regulation: str = typer.Option(..., "--regulation", help="Regulation ID, e.g. REG-AIACT"),
    name: str = typer.Option(..., help="Regulation name, e.g. 'EU AI Act'"),
    title: str = typer.Option("Uploaded regulatory change", help="Change title"),
) -> None:
    """Run the Foundry-backed four-agent pipeline on a regulation document."""
    if not file.exists():
        console.print(f"[red]File not found:[/] {file}")
        raise typer.Exit(code=1)
    text = file.read_text(encoding="utf-8")
    est = _build()
    pipeline = AgentPipeline(est)
    try:
        report = pipeline.run_text(
            text,
            regulation_id=regulation,
            regulation_name=name,
            change_title=title,
        )
    except FoundryInterpreterError as exc:
        console.print(f"[red]Foundry interpreter failed:[/] {exc}")
        raise typer.Exit(code=1) from exc

    export_tables(est, settings.tables_dir)
    export_graph(est, settings.graph_dir)
    export_purview(est, settings.purview_dir)
    engine_summary = ImpactEngine(est).analyze_change(report["change_id"])
    export_report(est, engine_summary, settings.reports_dir)

    console.print(f"\n[bold]Agent pipeline[/] — mode: [yellow]{report['llm_mode']}[/]")
    agent_table = Table(title="Agent read-out")
    agent_table.add_column("Agent")
    agent_table.add_column("Result")
    for a in report["agents"]:
        agent_table.add_row(a["agent"], a["result"])
    console.print(agent_table)
    console.print(f"\nInterpreted obligations: [cyan]{report.get('interpreted_obligations', 0)}[/]")
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
    console.print("\nLLM agents: [yellow]Deterministic (offline)[/]")
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


if __name__ == "__main__":
    app()
