# Lambert History

## Core Context
- Project: forge, a Python regulatory impact framework for assessing regulatory change impact and scoring compliance using synthetic digital twin data.
- User: briandenicola.
- Current focus: maintain Microsoft Agent Framework / Foundry Hosted Agent readiness while implementing deterministic offline interpreter core only.

## Learnings

### 2026-07-17 — Foundry prompt spec + evidence-starvation triage
- Deliverable A: shipped `docs/foundry-agent-prompts/control-mapper.md` — the paste-ready Foundry portal prompt spec Hamza needs for `RegImpactControlMapper`. Covers the response contract (per-mapping + top-level + tool_evidence fields), two full JSON examples (populated mappings AND empty-with-reason), the paste-ready system-prompt directive block, the harness retry contract, numbered deployment steps, and the version-pin recommendation. Reconciled the v3/v4 drift in `docs/foundry-fabric-agents.md` with a note pointing at the new spec.
- Deliverable B (evidence starvation): investigated and confirmed **no code fix available within the excluded surfaces**. Wrote up in `.squad/decisions/inbox/lambert-evidence-and-prompt.md`. Root cause = deployed `RegImpactControlMapper` v4 self-reports a single stub `tool_evidence` entry for the whole batch. Fix is prompt-side (directive 3 in the spec).
- **Key architecture insight**: `tool_evidence` on `FabricQuestionResponse` is a **passthrough** from `foundry_client._fabric_response_from_payload` — it's not gathered by the harness. `lakehouse.py` is writeback-only, not a query path. `pipeline.py` already sends `candidate_controls` shortlist + per-obligation `candidate_control_ids` inline, so the agent isn't starved of *data*; it's starved of *reporting discipline*.
- **Guardrails observed**: did NOT touch `contracts.py`, `_validated` / `_ask` / `map_controls` in `fabric_workflow.py`, or the control_mapper stage in `pipeline.py`. Considered and rejected post-hoc synthesizing `tool_evidence` (Constitutional "no offline fallback" violation) and touching `CONTROL_MAPPER_SPEC.instructions` (would duplicate the prompt fix and invite drift with the portal).

### 2026-07-17 — Team update: semantic retry removed from Fabric client
- The semantic retry loop that wrapped `FabricDataAgentClient.ask` (commit `412d695`) has been removed for latency (commit `f3d6ab4`, hamza-dev). `ask` is now single-attempt.
- Transport-level retry inside `FoundryAgentClient._invoke_with_retry` is untouched — network / 429 / 5xx retries still work. The change only removed the *semantic* retry that was re-issuing calls when the parsed payload looked wrong.
- OneLake writeback path is unaffected — `export_to_lakehouse()` and the `[fabric]` extra pattern are unchanged.

### 2026-07-17 — OneLake writeback for `interpret`
- Built `src/regimpact/lakehouse.py` — uploads every `*.parquet` from `settings.tables_dir` into a Fabric lakehouse under `Files/tables/`. Wired into `interpret` only (not `demo`, `analyze`, `score`, `audit`) — that's phase 1 scope.
- **ABFSS URL pattern** for OneLake-hosted lakehouses:
  `abfss://{workspace_id}@onelake.dfs.fabric.microsoft.com/{lakehouse_id}.Lakehouse/Files/{subpath}/{filename}`
  The ADLS account is always `https://onelake.dfs.fabric.microsoft.com`; the *filesystem* is the workspace ID, and the top-level directory is `{lakehouse_id}.Lakehouse`. Files land under `Files/…`, Delta tables land under `Tables/…`.
- **Lazy-import pattern for optional cloud SDKs:** `from azure.storage.filedatalake import DataLakeServiceClient` lives INSIDE `export_to_lakehouse()`, not at module top. Same for `DefaultAzureCredential`. This lets `from regimpact.lakehouse import ...` succeed even without the `fabric` extra installed, and lets tests plant a fake `azure.storage.filedatalake` in `sys.modules` before the import runs. Added a `fabric` optional extra to `pyproject.toml` (kept separate from `foundry`).
- **Why upload is best-effort, not fatal:** local Parquet in `output/tables/` is the source of truth. The `interpret` command wraps the OneLake call in a try/except that catches `LakehouseNotConfiguredError` (skip silently with a yellow hint) and `LakehouseWriteError` (red warning, but DO NOT re-raise). Rationale: a Fabric outage, expired token, or capacity-off state should never break a local pipeline run — the user still has their Parquet on disk and can re-upload manually.
- **Constitution alignment:** this is I/O plumbing, not agent behavior, so the "no deterministic offline fallback for agent behavior" rule doesn't apply — best-effort writeback for a data-export path is fine.
- **Testing:** all 5 new tests in `tests/test_lakehouse.py` pass; `tests/test_export_audit.py` unchanged (4/4 pass). Pre-existing failures in `test_cli.py::test_ask_fabric_cli_surfaces_missing_configuration` and `test_interpret_cli_surfaces_missing_foundry_configuration` are environmental (real `.env` credentials override `Settings()` defaults) and predate this change.

## Team Updates

### 2026-07-17 — Fabric response layer hardened
2026-07-17 — Bishop hardened the Fabric Data Agent response layer. Envelope missing `citations`/`tool_evidence`/`confidence` now defaults with a warning instead of aborting. Semantic retry (3 attempts) sits above transport retry. Inner-payload recovery treats known inner-shape JSON as the answer when the envelope is missing. See decisions.md.

### 2026-07-17 — Truncated inner-answer JSON now retryable
2026-07-17 — Bishop extended the Fabric semantic-retry loop to catch truncated inner-answer JSON — a follow-on to §5. Truncation (unclosed brace/bracket at EOF) triggers a concise-mode retry prompt asking the model to shorten rationales, drop optional fields, and cap answer size. Prose-answer agents (executive_qa, score_narrator) are exempted from JSON validation. See decisions.md.


### 2026-07-17 — Team update: request/response diagnostics landed

- Coordinator split a mixed working tree into two commits after user approval.
- **`1df0f5c`** — the diagnostics work you flagged as needed for the empty-mappings failure mode SHIPPED:
  - Per-obligation `candidate_control_ids` inline in the request payload (was already there for the shortlist; now surfaced per-obligation too — makes prompt-side reasoning about which controls were considered per obligation legible).
  - INFO/WARNING evidence logging in `pipeline.py` around the control_mapper → gap_analyst boundary. New WARNING `Fabric stage propagating empty control_ids stage=gap_analyst change_id=... reason=...` fires when the empty-with-reason path activates.
  - `tool_evidence_count` surfaced per stage in the pipeline logs.
- Your version-pin recommendation (`FOUNDRY_CONTROL_MAPPER_AGENT_VERSION`) remains the primary durable remediation for the v4 evidence-collapse drift — recorded as a standalone decision in `.squad/decisions.md`. Portal-side prompt directive (one `tool_evidence` per obligation batch) is the paired fix and lives in your Deliverable A spec.
- **`6cc6ffd`** — new `FabricMaterializerAgent` (Option A: deterministic Livy, Foundry as boundary supervisor). Adds a 6th stage between OneLake upload and downstream Fabric queries. If you author the Materializer prompt spec next, note the boundary posture: LLM does no PySpark generation at runtime; the agent plans + witnesses code-versioned statements from `fabric_livy_client.py`.
- 76 passed / 7 deselected. Pre-existing v3/v4 drift tests still deselected — waiting on your portal version-pin to land in env.