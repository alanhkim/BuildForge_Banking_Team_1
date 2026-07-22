# Pipeline Event Bus — Additive Observability Layer

**Confidence:** low (first application 2026-07-23)
**When to apply:** Any long-running sequential Python pipeline where users need to see progress but the operation is I/O-bound and hard to reason about.
**Where applied so far:** `src/regimpact/agents/pipeline.py` + `src/regimpact/cli.py` (regimpact `interpret` command)

## Problem

`python -m regimpact interpret` runs 5+ minutes end-to-end: Foundry Interpreter → Fabric Control Mapper → Gap Analyst → Remediation Planner → Score Narrator → local exports → OneLake Files → OneLake Tables → Purview → report. Users saw a blank terminal for minutes and could not tell:
- Whether the CLI was hung or working
- Which stage was slow
- Which tools each agent was calling

Adding progress output naively couples the CLI to the pipeline internals, or requires the pipeline module to know about `rich`.

## Solution — sync event bus with three roles

1. **Producer** (pipeline module): emits `PipelineEvent(kind, stage, ...)` at stage boundaries and tool-call sites.
2. **Bus** (`events.py`): frozen dataclass + `EventCallback = Callable[[PipelineEvent], None]` type alias. That's the whole contract.
3. **Consumer** (CLI): renders a live table via `rich.live.Live` + `rich.spinner.Spinner`, mutating per-stage state on each event.

Producers know nothing about renderers. Renderers know nothing about pipeline internals beyond the stage keys they care about (unknown stages are silently ignored — forward compatible).

## Recipe

### 1. Define the event contract

```python
# events.py — kept dependency-free
from dataclasses import dataclass, field
from typing import Callable, Literal

EventKind = Literal[
    "stage_start", "stage_end", "stage_error",
    "tool_call", "writeback", "info",
]

@dataclass(frozen=True)
class PipelineEvent:
    kind: EventKind
    stage: str
    message: str = ""
    tool_name: str | None = None
    data_source: str | None = None
    duration_ms: int | None = None
    details: dict = field(default_factory=dict)

EventCallback = Callable[[PipelineEvent], None]
```

### 2. Instrument the pipeline (additive)

Add `on_event: EventCallback | None = None` as a keyword-only ctor param. Provide a `_emit` helper that is a **no-op when subscriber is None** and **swallows subscriber exceptions**:

```python
def _emit(self, kind, stage, **kw):
    if self._on_event is None:
        return
    try:
        self._on_event(PipelineEvent(kind=kind, stage=stage, **kw))
    except Exception:
        logger.warning("PipelineEvent callback failed", exc_info=True)
```

Then bracket every stage:

```python
t0 = time.perf_counter()
self._emit("stage_start", "control_mapper", message="Mapping obligations")
try:
    result = agent.map(...)
except AgentError as exc:
    self._emit("stage_error", "control_mapper",
               message=str(exc),
               duration_ms=int((time.perf_counter() - t0) * 1000))
    raise
for evidence in result.tool_evidence:
    self._emit("tool_call", "control_mapper",
               tool_name=evidence.tool_name,
               data_source=evidence.data_source,
               message=_truncate(evidence.query, 80))
self._emit("stage_end", "control_mapper",
           duration_ms=int((time.perf_counter() - t0) * 1000),
           details={"mappings": len(result.mappings), ...})
```

### 3. Emit skipped stages explicitly

If a stage is conditionally guarded off, emit back-to-back `stage_start` + `stage_end` with `duration_ms=0` and `details={"skipped": True, "reason": "<why>"}`. Renderers need the row to exist so users see the deliberate skip. Do not silently omit.

### 4. Post-pipeline stages emit from the CLI, not the pipeline

Downstream steps (uploads, exports, reports) belong in the CLI. They emit their own events on the same bus. Same event shape, different producer:

```python
def _emit_cli_stage(callback, stage, message):
    if callback is None: return 0.0
    callback(PipelineEvent(kind="stage_start", stage=stage, message=message))
    return time.perf_counter()

def _emit_cli_stage_end(callback, stage, started, *, details=None):
    if callback is None: return
    callback(PipelineEvent(
        kind="stage_end", stage=stage,
        duration_ms=int((time.perf_counter() - started) * 1000),
        details=details or {},
    ))
```

### 5. Consumer: `rich.live.Live` + per-stage state

```python
@contextmanager
def _streaming_renderer(verbose: bool):
    state = {key: _StageState(label=lbl) for key, lbl in _STREAM_STAGES}

    def _render() -> Table: ...  # build fresh Table from state dict

    live = Live(_render(), console=console, refresh_per_second=8)

    def _callback(ev: PipelineEvent) -> None:
        s = state.get(ev.stage)
        if s is None: return  # forward-compat: ignore unknown stages
        # mutate s based on ev.kind
        live.update(_render())

    with live:
        yield _callback
```

### 6. CLI flags

- `--stream/--no-stream` (default on)
- Auto-disable when `not sys.stdout.isatty()` — piped output and CI get non-streaming automatically
- `--verbose/-v` — expand each stage to show per-tool-call lines instead of collapsed count

Both modes share a single `_run_impl(...)` helper. Toggle whether legacy `console.print` calls fire alongside the Live table with `silent = on_event is not None`.

## Rules

1. **The producer NEVER depends on the renderer.** No `rich` imports in the pipeline module. The event bus contract is a callable and a frozen dataclass.
2. **Broken subscribers cannot crash the producer.** `_emit` wraps in `try/except → logger.warning → continue`.
3. **Return shapes never change.** Existing consumers who don't care about events see byte-identical output.
4. **All existing `logger.info/warning/debug` calls preserved.** Additive, not substitutive.
5. **Unknown stages ignored.** The renderer's `state.get(ev.stage) is None → return` idiom means adding a new stage to the pipeline never breaks the renderer.
6. **Skipped stages emit both start and end.** Never silently omit a stage that was conditionally guarded off.

## Anti-patterns

1. **Threading a `console.print` through the pipeline.** Couples the pipeline to `rich`, breaks non-CLI callers, makes testing require captured stdout.
2. **`async` event bus.** Adds no throughput for I/O-bound sequential pipelines, forces every subscriber to be an async callable, harder to reason about ordering.
3. **Silent stage-skipping.** A missing row in the progress table is indistinguishable from a broken renderer. Emit the skip.
4. **Post-pipeline stages inside the pipeline module.** OneLake upload / report generation / Purview export are output-shape concerns. Keep them in the CLI and emit their own events.
5. **Requiring subscribers.** `on_event=None` MUST be a fully-working code path. Never make observability mandatory.

## Testing pattern

Reuse existing stubs; add tests that:
1. Assert `on_event=None` doesn't crash and pipeline returns unchanged result.
2. Assert `_emit` with no subscriber is a bare `is None` check (no exception, no side effect).
3. Assert a callback that raises → warning logged → pipeline continues.
4. Assert `stage_start` / `stage_end` events appear in expected order.
5. Assert one `tool_call` per `tool_evidence` entry with `tool_name`/`data_source` populated.
6. Assert skipped stages carry `details["skipped"] is True` and a reason.
7. Assert `duration_ms >= 0` on every non-skipped `stage_end`.

## Reuse opportunities in this repo

- `regimpact score` command — currently silent while it runs the impact engine.
- `regimpact demo` — runs every change; would benefit from a per-change progress table.
- `regimpact analyze` / `regimpact export-audit` — long export operations.
- Any future batch or backfill command.

The renderer template in `_streaming_renderer` + `_StageState` is directly reusable — just change the `_STREAM_STAGES` tuple and the stage-detail formatter.
