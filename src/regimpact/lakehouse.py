"""OneLake writer — uploads Parquet tables into a Fabric lakehouse Files/ path.

The core CLI does not depend on the Azure Data Lake SDK. This module lazily
imports ``azure.storage.filedatalake`` (and ``azure.identity`` for the default
credential) only when :func:`export_to_lakehouse` is actually invoked, so
installations without the ``fabric`` extra can still import ``regimpact``.

OneLake exposes each Fabric lakehouse as an ADLS Gen2 filesystem under the
account ``https://onelake.dfs.fabric.microsoft.com``. The filesystem is the
workspace ID and the top-level directory is the bare lakehouse ID (Fabric's
ADLS Gen2 canonical form — the ``.Lakehouse`` suffix is a shortcut/UI
convention only and is rejected here with ``FriendlyNameSupportDisabled``).
Confirmed against ``GET /v1/workspaces/{ws}/lakehouses/{lh}`` →
``properties.oneLakeFilesPath`` / ``oneLakeTablesPath``, which both return
``https://onelake.dfs.fabric.microsoft.com/{workspace_id}/{lakehouse_id}/…``
with no suffix. Parquet and CSV files land under ``Files/<subpath>/`` and
can then be shortcut'd into a Delta table from the Fabric UI.

Delta writeback semantics (deliberate behavioural change — 2026-07-21):
    The Delta writer at the bottom of this module (:func:`write_delta_table`
    and :func:`export_regimpact_tables`) uses **MERGE upsert**, not blind
    append. Each Delta table under ``Tables/`` now holds **one row per
    primary key** (the latest observed state), NOT one row per
    ``(primary_key, as_of)`` snapshot. Rows whose non-key columns are
    identical to what already exists — differing only in ``as_of`` — are
    skipped without a write. New primary keys are inserted; changed rows
    have every column (including ``as_of``) updated in place. This is a
    deliberate departure from the earlier append-only behaviour: if
    slowly-changing-dimension (SCD Type 2) history is needed later, that
    is a separate design. See :data:`_TABLE_PRIMARY_KEYS` for the
    per-table PK map and ``.squad/decisions/inbox/bishop-onelake-delta-
    merge.md`` for the full rationale.

Schema-tolerance on the MERGE path (2026-07-21 follow-up):
    Tables can be created outside our writer — most commonly by the
    Fabric ``01_load_lakehouse`` notebook, which follows Spark PascalCase
    conventions (``ID``, ``Name``, ``As_Of``, ``Value_Chain``, ...) while
    our local Parquet writer emits lowercase (``id``, ``name``,
    ``as_of``, ``value_chain``, ...). DataFusion's SQL parser rejects a
    predicate of ``target.id = source.id`` when the target column is
    literally named ``ID``, which used to break MERGE. The MERGE branch
    of :func:`write_delta_table` now introspects the target's schema
    (``DeltaTable.schema().fields``), builds a case-insensitive map, and
    renames a copy of the source Arrow table to match the target's case
    before building the predicates. All identifiers in the predicates
    are double-quoted so PascalCase (or future names with spaces /
    reserved words) survive the SQL parse. The internal contract
    (:data:`_TABLE_PRIMARY_KEYS` stays lowercase) is preserved — case
    translation happens only at this single boundary seam. Structural
    drift (a source column with no case-insensitive match in the target,
    or a PK column absent from the target) still raises
    :class:`LakehouseWriteError` — we accommodate case, never silently
    drop or invent columns. First-write path is unchanged: fresh tables
    are still created with our lowercase names via ``write_deltalake``.
    See ``.squad/decisions/inbox/bishop-onelake-schema-alignment.md`` for
    the full rationale.
"""
from __future__ import annotations

import logging
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)

# Default (global) OneLake ADLS Gen2 endpoint. Some Fabric capacity SKUs
# advertise a *regional* endpoint (e.g. ``northcentralus-onelake.dfs.
# fabric.microsoft.com``) via the workspace's ``oneLakeEndpoints`` metadata;
# on those capacities the global endpoint's routing layer returns
# ``FriendlyNameSupportDisabled`` instead of forwarding, so callers must
# override this via ``FABRIC_ONELAKE_DFS_ENDPOINT``. Kept as the module
# default so that pre-existing callers see no behavior change.
_ONELAKE_ACCOUNT_URL = "https://onelake.dfs.fabric.microsoft.com"

# Substring every valid OneLake DFS endpoint must contain, whether global
# (``onelake.dfs.fabric.microsoft.com``) or regional
# (``<region>-onelake.dfs.fabric.microsoft.com``). Guards against typos and
# accidentally pointing at a non-OneLake ADLS Gen2 host.
_ONELAKE_ENDPOINT_MARKER = "onelake.dfs.fabric.microsoft.com"

# Folder layout expected by ``notebooks/08_fabric/01_load_lakehouse.ipynb``.
# The PySpark loader reads Parquet from these two Files/ subfolders and
# materialises them as managed Delta tables plus the ``v_impact``,
# ``v_compliance`` and ``v_capability_health`` views. Keep these constants
# in sync with ``RAW_FOLDER`` / ``GOLD_FOLDER`` in that notebook.
RAW_SUBPATH = "regimpact_raw"
GOLD_SUBPATH = "regimpact_gold"


class LakehouseError(Exception):
    """Base class for OneLake writeback errors."""


class LakehouseNotConfiguredError(LakehouseError):
    """Raised when FABRIC_WORKSPACE_ID or FABRIC_LAKEHOUSE_ID is not set."""


class LakehouseWriteError(LakehouseError):
    """Raised when uploading a Parquet file to OneLake fails."""


def _normalize_fabric_id(raw: str | None, env_var: str) -> str:
    """Strip whitespace + surrounding quotes and validate as a Fabric GUID.

    OneLake's ADLS Gen2 endpoint rejects malformed filesystem/directory names
    with an opaque ``FriendlyNameSupportDisabled`` server error at upload time.
    Common causes are copy/paste artefacts on the env var: trailing newlines
    from ``export FOO="…"``, surrounding quote characters, whitespace, or
    passing a workspace *display name* while friendly-name resolution is
    disabled on the tenant.

    We surface that as a clear configuration failure at the boundary instead
    of letting it turn into a red ``LakehouseWriteError`` on every run.

    Parameters
    ----------
    raw:
        The value read from settings/env (may be ``None`` or empty).
    env_var:
        The env var name (e.g. ``"FABRIC_WORKSPACE_ID"``) — used only in the
        error message so the operator knows what to fix.

    Returns
    -------
    str
        The cleaned, GUID-validated value.

    Raises
    ------
    LakehouseNotConfiguredError
        If the value is empty after stripping, or if it is not a valid GUID.
        Malformed values fail every run until fixed, so per decision §0 they
        are treated as "not configured" (yellow skip) rather than a transient
        write error (red hard-failure).
    """
    if raw is None:
        raise LakehouseNotConfiguredError(
            f"{env_var} is not set; cannot write to OneLake."
        )
    # Strip whitespace, then strip a single layer of surrounding quotes.
    cleaned = raw.strip().strip("'").strip('"').strip()
    if not cleaned:
        raise LakehouseNotConfiguredError(
            f"{env_var} is not set; cannot write to OneLake."
        )
    try:
        # ``uuid.UUID`` accepts canonical 8-4-4-4-12 hex (case-insensitive)
        # and also braced/urn forms; reject the latter by re-checking the
        # canonical string form matches the input casefolded.
        parsed = uuid.UUID(cleaned)
    except (ValueError, AttributeError, TypeError) as exc:
        raise LakehouseNotConfiguredError(
            f"{env_var} must be a Fabric workspace/lakehouse GUID "
            f"(got '{cleaned}'). Copy it from the Fabric portal → "
            f"Workspace settings → About."
        ) from exc
    if str(parsed) != cleaned.lower():
        raise LakehouseNotConfiguredError(
            f"{env_var} must be a canonical GUID in 8-4-4-4-12 hex form "
            f"(got '{cleaned}'). Copy it from the Fabric portal → "
            f"Workspace settings → About."
        )
    return cleaned


def _resolve_onelake_endpoint(raw: str | None) -> str:
    """Return the DFS endpoint URL to hit, falling back to the global default.

    Some Fabric capacities advertise a *regional* OneLake endpoint (e.g.
    ``https://northcentralus-onelake.dfs.fabric.microsoft.com``) via
    ``GET /v1/workspaces/{id}`` → ``oneLakeEndpoints.dfsEndpoint``. On those
    capacities the global endpoint's routing layer returns
    ``FriendlyNameSupportDisabled`` instead of forwarding, so callers must
    point the SDK at the regional host directly. When ``raw`` is ``None`` or
    empty we return :data:`_ONELAKE_ACCOUNT_URL` (backward-compatible global
    default). When ``raw`` is provided we validate the shape at the boundary
    for the same reason we validate GUIDs: a malformed value fails every run
    with an opaque SDK error, so surface it as configuration up front and map
    it to the soft-skip class per decision §0.

    Raises
    ------
    LakehouseNotConfiguredError
        If ``raw`` is provided but does not start with ``https://`` or does
        not contain ``onelake.dfs.fabric.microsoft.com``.
    """
    if raw is None:
        return _ONELAKE_ACCOUNT_URL
    cleaned = raw.strip().strip("'").strip('"').strip()
    if not cleaned:
        return _ONELAKE_ACCOUNT_URL
    lowered = cleaned.lower()
    if not lowered.startswith("https://") or _ONELAKE_ENDPOINT_MARKER not in lowered:
        raise LakehouseNotConfiguredError(
            "FABRIC_ONELAKE_DFS_ENDPOINT must be an https:// URL containing "
            f"'{_ONELAKE_ENDPOINT_MARKER}' (got '{cleaned}'). Copy it from "
            "GET /v1/workspaces/{id} → oneLakeEndpoints.dfsEndpoint, or "
            f"leave the env var unset to use the global default "
            f"'{_ONELAKE_ACCOUNT_URL}'."
        )
    return cleaned


def _default_credential():
    """Lazy-import ``DefaultAzureCredential`` so azure-identity stays optional."""
    from azure.identity import DefaultAzureCredential

    return DefaultAzureCredential()


def export_to_lakehouse(
    tables_dir: Path,
    workspace_id: str,
    lakehouse_id: str,
    files_subpath: str = "tables",
    credential=None,
    onelake_endpoint: str | None = None,
) -> list[str]:
    """Upload every ``*.parquet`` and ``*.csv`` file in ``tables_dir`` to a Fabric lakehouse.

    Parameters
    ----------
    tables_dir:
        Local directory containing Parquet and/or CSV files to upload.
    workspace_id:
        Fabric workspace ID (GUID). Used as the ADLS filesystem name.
    lakehouse_id:
        Fabric lakehouse ID (GUID). Used verbatim as the top-level
        directory under the workspace filesystem (no ``.Lakehouse``
        suffix — that is a Fabric UI / Spark-shortcut convention that the
        ADLS Gen2 name-plane rejects with ``FriendlyNameSupportDisabled``).
    files_subpath:
        Subfolder under ``Files/`` where the files land. Defaults to
        ``"tables"``.
    credential:
        Optional Azure credential. Defaults to
        :class:`azure.identity.DefaultAzureCredential`.
    onelake_endpoint:
        Optional OneLake DFS endpoint URL. When ``None`` or empty, uses the
        global default ``https://onelake.dfs.fabric.microsoft.com``. Pass a
        regional URL (e.g. ``https://northcentralus-onelake.dfs.fabric.
        microsoft.com``) when the target workspace lives on a capacity that
        advertises a regional endpoint via ``oneLakeEndpoints.dfsEndpoint``.
        The returned ABFSS URLs are unaffected — they always use the
        canonical, non-regional host, because regional routing is a
        data-plane detail applied by OneLake, not a name-plane one.

    Returns
    -------
    list[str]
        ABFSS URLs of every uploaded file.

    Raises
    ------
    LakehouseNotConfiguredError
        If ``workspace_id`` or ``lakehouse_id`` is empty, or is not a valid
        Fabric GUID (after stripping surrounding whitespace/quotes); or if
        ``onelake_endpoint`` is provided but not a valid OneLake DFS URL.
    LakehouseWriteError
        If any upload fails.
    """
    workspace_id = _normalize_fabric_id(workspace_id, "FABRIC_WORKSPACE_ID")
    lakehouse_id = _normalize_fabric_id(lakehouse_id, "FABRIC_LAKEHOUSE_ID")
    account_url = _resolve_onelake_endpoint(onelake_endpoint)

    from azure.storage.filedatalake import DataLakeServiceClient

    cred = credential if credential is not None else _default_credential()
    service = DataLakeServiceClient(account_url, credential=cred)
    file_system = service.get_file_system_client(workspace_id)
    # Bare lakehouse GUID — Fabric's canonical ADLS Gen2 path. The
    # ``.Lakehouse`` suffix seen in Spark shortcuts / UI mount paths is
    # rejected here with ``FriendlyNameSupportDisabled``. Verified against
    # ``GET /v1/workspaces/{ws}/lakehouses/{lh}`` → ``oneLakeFilesPath``.
    target_dir = f"{lakehouse_id}/Files/{files_subpath}"
    directory_client = file_system.get_directory_client(target_dir)

    uploaded: list[str] = []
    # Restricted to Parquet + CSV on purpose — those are the two formats
    # ``export_tables`` / ``export_gold`` produce. Anything else in
    # ``tables_dir`` is almost certainly accidental (stray logs, editor
    # backups, README) and shouldn't be pushed to Fabric.
    upload_files = sorted(
        list(tables_dir.glob("*.parquet")) + list(tables_dir.glob("*.csv"))
    )
    for local_path in upload_files:
        try:
            file_client = directory_client.get_file_client(local_path.name)
            data = local_path.read_bytes()
            file_client.upload_data(data, overwrite=True)
        except Exception as exc:  # noqa: BLE001 — wrap all Azure SDK errors
            raise LakehouseWriteError(
                f"Failed to upload {local_path} to OneLake: {exc}"
            ) from exc

        abfss_url = (
            f"abfss://{workspace_id}@onelake.dfs.fabric.microsoft.com/"
            f"{lakehouse_id}/Files/{files_subpath}/{local_path.name}"
        )
        uploaded.append(abfss_url)
        logger.info(
            "Uploaded %s (%d bytes) to %s", local_path.name, len(data), abfss_url
        )

    logger.info(
        "OneLake upload complete: %d file(s) written to %s",
        len(uploaded),
        target_dir,
    )
    return uploaded


def export_regimpact_lakehouse(
    tables_dir: Path,
    gold_dir: Path,
    workspace_id: str,
    lakehouse_id: str,
    credential=None,
    onelake_endpoint: str | None = None,
) -> dict[str, list[str]]:
    """Upload the raw entity + gold star-schema Parquet sets to a Fabric lakehouse.

    This is the high-level entry point the ``interpret`` CLI uses. It matches
    the folder layout expected by the companion PySpark notebook
    (``notebooks/08_fabric/01_load_lakehouse.ipynb``), which reads:

    * ``Files/regimpact_raw/*.parquet``  — 17 entity tables
    * ``Files/regimpact_gold/*.parquet`` — dim_* / fact_* / bridge_* tables

    ...and then materialises them as managed Delta tables plus the
    ``v_impact``, ``v_compliance`` and ``v_capability_health`` views inside
    Fabric. Delta conversion and view creation require the Fabric-provided
    Spark session, so they stay in the notebook — this function only lands
    the files in the right place.

    Parquet is the primary format consumed by the PySpark notebook; CSVs
    land alongside for direct download and non-Spark consumers (Excel,
    quick eyeballing, external ingestion). Both formats are uploaded
    whenever they exist locally.

    Both directories are optional at runtime: if either is empty or missing,
    that half of the upload is skipped and reported as an empty list. This
    lets ``interpret`` run before ``demo`` has produced gold outputs without
    failing the pipeline.

    Parameters
    ----------
    tables_dir:
        Local directory holding the raw entity Parquet files (from
        :func:`regimpact.exporter.export_tables`).
    gold_dir:
        Local directory holding the star-schema Parquet files (from
        :func:`regimpact.gold.export_gold`).
    workspace_id:
        Fabric workspace ID (GUID).
    lakehouse_id:
        Fabric lakehouse ID (GUID).
    credential:
        Optional Azure credential. Defaults to
        :class:`azure.identity.DefaultAzureCredential`.
    onelake_endpoint:
        Optional OneLake DFS endpoint URL. When ``None`` or empty, uses the
        global default. Pass a regional URL when the target workspace lives
        on a capacity that advertises a regional
        ``oneLakeEndpoints.dfsEndpoint``. See
        :func:`export_to_lakehouse` for the full contract.

    Returns
    -------
    dict[str, list[str]]
        ``{"raw": [...abfss urls...], "gold": [...abfss urls...]}``.

    Raises
    ------
    LakehouseNotConfiguredError
        If ``workspace_id`` or ``lakehouse_id`` is empty, or is not a valid
        Fabric GUID (after stripping surrounding whitespace/quotes); or if
        ``onelake_endpoint`` is provided but not a valid OneLake DFS URL.
    LakehouseWriteError
        If any Parquet upload fails.
    """
    cred = credential if credential is not None else _default_credential()

    def _has_uploadable(d: Path) -> bool:
        return d.exists() and (any(d.glob("*.parquet")) or any(d.glob("*.csv")))

    raw_uploaded: list[str] = []
    if _has_uploadable(tables_dir):
        raw_uploaded = export_to_lakehouse(
            tables_dir,
            workspace_id=workspace_id,
            lakehouse_id=lakehouse_id,
            files_subpath=RAW_SUBPATH,
            credential=cred,
            onelake_endpoint=onelake_endpoint,
        )
    else:
        logger.warning(
            "OneLake raw upload skipped: %s has no Parquet or CSV files.",
            tables_dir,
        )

    gold_uploaded: list[str] = []
    if _has_uploadable(gold_dir):
        gold_uploaded = export_to_lakehouse(
            gold_dir,
            workspace_id=workspace_id,
            lakehouse_id=lakehouse_id,
            files_subpath=GOLD_SUBPATH,
            credential=cred,
            onelake_endpoint=onelake_endpoint,
        )
    else:
        logger.warning(
            "OneLake gold upload skipped: %s has no Parquet or CSV files.",
            gold_dir,
        )

    return {"raw": raw_uploaded, "gold": gold_uploaded}


# ---------------------------------------------------------------------------
# Delta table writeback (OneLake ``Tables/`` via delta-rs)
# ---------------------------------------------------------------------------
#
# The Files/ upload above lands raw Parquet + CSV in the lakehouse and stops
# there. Historically the ``01_load_lakehouse.ipynb`` Fabric notebook then
# had to be opened and run manually to promote those files into managed
# Delta tables (and to create the ``v_impact`` / ``v_compliance`` /
# ``v_capability_health`` views).
#
# The functions below eliminate the notebook step for table materialisation
# by writing Delta tables directly to OneLake ``Tables/`` using the
# ``deltalake`` (delta-rs) Python library. Views still require SQL, so
# they stay in the notebook as a one-time setup — but the *tables* they
# read from now materialise automatically at the end of every ``interpret``
# run.
#
# Design decisions (see ``.squad/decisions/inbox/bishop-onelake-delta-tables.md``
# for the original append-mode landing, and
# ``.squad/decisions/inbox/bishop-onelake-delta-merge.md`` for the switch
# to MERGE upsert documented below).
#
# 1. **MERGE upsert, NOT blind append (2026-07-21 follow-up).**
#    The initial implementation used ``mode="append"``, which duplicated
#    every row on every ``interpret`` run. It now MERGEs on the
#    per-table primary key (see :data:`_TABLE_PRIMARY_KEYS`):
#      * match on PK columns ONLY (never on ``as_of``);
#      * when matched AND any non-PK, non-``as_of`` column differs →
#        update every column (including ``as_of`` so the latest state
#        carries the latest stamp);
#      * when matched AND equal-except-``as_of`` → skip (the "no
#        bandwidth" win — no write, no version bump);
#      * when not matched → insert with the new ``as_of``.
#    **Behavioural consequence:** tables now hold one row per PK
#    (latest state only), NOT one row per ``(PK, as_of)`` snapshot.
#    Deliberate change from the earlier append behaviour. If SCD Type 2
#    history is later required, that is a separate design.
# 2. **First-write fallback to append + ``schema_mode="merge"``.**
#    Detected via ``DeltaTable(...)`` raising a "not found"-class
#    exception. Caught by class name so we tolerate delta-rs moving the
#    error class between ``deltalake`` and ``deltalake.exceptions``
#    across versions. Existence probes via ``list`` would be a network
#    round-trip and racy — this exception-driven flow is idiomatic.
# 3. **Flat namespace under ``Tables/``.** No ``regimpact_raw`` /
#    ``regimpact_gold`` subfolders. The raw entity names
#    (``controls``, ``obligations``, ``regulations``) and the gold names
#    (``dim_control``, ``fact_gap``, ``bridge_gap_entity``) don't collide,
#    so Fabric UI shows a clean flat list of ~34 tables. The Fabric SQL
#    endpoint surfaces flat table names most cleanly; nested "table
#    schemas" under ``Tables/`` are supported but add UI clutter with no
#    upside here.
# 4. **Schema evolution on first write only: ``schema_mode="merge"``.**
#    Additive column changes (a new column landing in an upstream
#    regenerator) are absorbed at create time. Once the table exists,
#    the MERGE path assumes the schemas already align — incompatible
#    drift will surface as a ``LakehouseWriteError`` naming the table.
#    NEVER silently drop columns.
# 5. **``deltalake`` import is lazy.** Same pattern as ``DataLakeServiceClient``
#    above — missing package maps to ``LakehouseNotConfiguredError``
#    pointing at ``pip install regimpact[fabric]``.
# 6. **Unknown table name → hard failure.** :func:`_get_primary_keys`
#    raises ``LakehouseWriteError`` (not a silent default of ``("id",)``)
#    so a mis-named Parquet file lands as a loud, pinpointable error
#    instead of quietly corrupting a Delta table's identity semantics.


# --------------------------------------------------------------------------
# Per-table primary key map (verified against ``src/regimpact/models.py``
# and ``src/regimpact/gold.py``). This map is the single source of truth
# for the MERGE predicate used in :func:`write_delta_table`. If a Parquet
# file lands with a stem not in this map, the write raises
# ``LakehouseWriteError`` — do NOT default to ``("id",)`` (silent
# misconfiguration on a Delta table's identity would be worse than a
# clear error).
#
# Shape summary:
#   * 28 tables have a single-column ``id`` PK (15 raw entities + 13 gold dims).
#   *  3 tables have a single-column non-``id`` PK (fact_gap, fact_remediation,
#     fact_compliance_score — the last one built as a flattened composite
#     string ``score_key`` in gold.py).
#   *  3 tables have a composite PK (compliance_scores, relationships,
#     bridge_gap_entity — these entities have no single-column ``id``
#     because they are join tables / value tuples).
# --------------------------------------------------------------------------
_TABLE_PRIMARY_KEYS: dict[str, tuple[str, ...]] = {
    # Raw entities (single-column ``id`` PK) — mirrors ``_TABLES`` in export.py.
    "regulations": ("id",),
    "regulatory_changes": ("id",),
    "obligations": ("id",),
    "controls": ("id",),
    "capabilities": ("id",),
    "technologies": ("id",),
    "evidence": ("id",),
    "systems": ("id",),
    "business_processes": ("id",),
    "products": ("id",),
    "data_domains": ("id",),
    "business_units": ("id",),
    "risks": ("id",),
    "gaps": ("id",),
    "remediation_actions": ("id",),
    # Gold dims (single-column ``id`` PK) — mirrors ``GOLD_SCHEMA`` in gold.py.
    "dim_regulation": ("id",),
    "dim_change": ("id",),
    "dim_obligation": ("id",),
    "dim_control": ("id",),
    "dim_capability": ("id",),
    "dim_technology": ("id",),
    "dim_evidence": ("id",),
    "dim_system": ("id",),
    "dim_process": ("id",),
    "dim_product": ("id",),
    "dim_data_domain": ("id",),
    "dim_unit": ("id",),
    "dim_risk": ("id",),
    # Gold facts (single-column non-``id`` PK).
    "fact_gap": ("gap_id",),
    "fact_remediation": ("remediation_id",),
    # ``score_key`` is an already-flattened composite string built in
    # gold.py (~L191) — treat as a single-column surrogate PK.
    "fact_compliance_score": ("score_key",),
    # Composite PK — entities with no single-column identity.
    #   compliance_scores: ``ComplianceScore`` has no ``id``; each row is
    #     a (scope, scenario, change) tuple.
    #   relationships: ``Edge`` has no ``id``; each row is a directed
    #     edge triple.
    #   bridge_gap_entity: gold bridge table, no single-column PK.
    "compliance_scores": (
        "scope_type",
        "scope_id",
        "scenario",
        "change_id",
    ),
    "relationships": ("source_id", "target_id", "rel_type"),
    "bridge_gap_entity": ("gap_id", "entity_type", "entity_id"),
}


def _get_primary_keys(table_name: str) -> tuple[str, ...]:
    """Return the primary-key columns for ``table_name`` or raise.

    Raises
    ------
    LakehouseWriteError
        If ``table_name`` has no entry in :data:`_TABLE_PRIMARY_KEYS`.
        Silent default (``("id",)``) is deliberately NOT used — a Parquet
        file with an unexpected stem is almost certainly a bug (renamed
        upstream, typo, stray file) and should fail loudly before it
        clobbers a Delta table's identity semantics.
    """
    try:
        return _TABLE_PRIMARY_KEYS[table_name]
    except KeyError as exc:
        raise LakehouseWriteError(
            f"Unknown Delta table '{table_name}' — no primary-key "
            f"mapping registered in _TABLE_PRIMARY_KEYS. Either add an "
            f"entry to that map in regimpact/lakehouse.py or fix the "
            f"Parquet file name so its stem matches an existing table."
        ) from exc

# Format of a Delta table URL in OneLake — mirrors the Files pattern
# (bare-GUID canonical form, no ``.Lakehouse`` suffix). Confirmed against
# ``GET /v1/workspaces/{ws}/lakehouses/{lh}`` → ``properties.oneLakeTablesPath``.
_DELTA_URL_TEMPLATE = "abfss://{workspace_id}@{host}/{lakehouse_id}/Tables/{table_name}"

# Azure Storage token audience — same scope the Files upload path uses
# implicitly through ``DefaultAzureCredential``. delta-rs takes the token
# explicitly via ``storage_options["bearer_token"]``.
_STORAGE_SCOPE = "https://storage.azure.com/.default"


def _host_from_endpoint(endpoint_url: str) -> str:
    """Strip the ``https://`` scheme from a resolved OneLake endpoint URL.

    delta-rs consumes the ABFSS URL as its write target, so the host
    embedded in the URL is what it will hit — unlike the Files SDK where
    the account URL is a separate parameter. Passing the resolved endpoint
    host (regional if ``FABRIC_ONELAKE_DFS_ENDPOINT`` is set) keeps
    behaviour parity with the Files upload path.
    """
    return endpoint_url.split("://", 1)[-1].rstrip("/")


def _load_write_deltalake():
    """Lazy-import ``deltalake.write_deltalake``.

    Kept out of module top-level so the core CLI can import ``regimpact``
    without the ``fabric`` extra installed. Missing package is treated as
    a configuration issue (soft-skip class per decision §0), not a
    transient write error.
    """
    try:
        from deltalake import write_deltalake
    except ImportError as exc:  # pragma: no cover — exercised via patch
        raise LakehouseNotConfiguredError(
            "The 'deltalake' package is required for OneLake Tables/ "
            "writeback. Install it with: pip install 'regimpact[fabric]'."
        ) from exc
    return write_deltalake


def _load_delta_table():
    """Lazy-import ``deltalake.DeltaTable``.

    Used for MERGE-upsert path — opening an existing Delta table so
    :meth:`DeltaTable.merge` can be driven. Same lazy-import + soft-skip
    contract as :func:`_load_write_deltalake` — missing package maps to
    :class:`LakehouseNotConfiguredError` (yellow) rather than
    :class:`LakehouseWriteError` (red) per decision §0.
    """
    try:
        from deltalake import DeltaTable
    except ImportError as exc:  # pragma: no cover — exercised via patch
        raise LakehouseNotConfiguredError(
            "The 'deltalake' package is required for OneLake Tables/ "
            "writeback. Install it with: pip install 'regimpact[fabric]'."
        ) from exc
    return DeltaTable


def _is_table_not_found(exc: BaseException) -> bool:
    """Return True when ``exc`` looks like delta-rs's "table does not exist" error.

    Match by class name so we tolerate delta-rs shuffling the class
    location between ``deltalake`` and ``deltalake.exceptions`` across
    versions (verified against 1.6.2 which surfaces it as
    ``deltalake.exceptions.TableNotFoundError``). A class-name match is
    also easy to fake in tests without depending on the real delta-rs
    exception module tree.
    """
    return type(exc).__name__ == "TableNotFoundError"


def _read_parquet_table(parquet_path: Path):
    """Read a Parquet file into a ``pyarrow.Table``.

    ``pyarrow`` is a core (non-optional) dependency, so this needs no
    ``ImportError`` guard. Reads happen against files the local pipeline
    just wrote, so the OS-level file itself is trusted; any failure here
    (corrupted file, disk error) is genuinely a write-side problem and
    maps to :class:`LakehouseWriteError`.
    """
    import pyarrow.parquet as pq

    return pq.read_table(parquet_path)


def _bearer_token(credential) -> str:
    """Fetch a fresh bearer token for the Azure Storage audience.

    ``DefaultAzureCredential`` caches internally, so calling this once per
    Delta write is cheap in the common case. Refetching per-table also
    avoids a mid-batch expiry on a very slow run.
    """
    try:
        token = credential.get_token(_STORAGE_SCOPE)
    except Exception as exc:  # noqa: BLE001 — wrap all credential errors
        raise LakehouseWriteError(
            f"Failed to acquire Azure Storage bearer token for OneLake "
            f"Delta write: {exc}"
        ) from exc
    return token.token


def _align_arrow_to_target_schema(
    arrow_table,
    target_columns: list[str],
    pk_columns: tuple[str, ...],
    table_name: str,
):
    """Return ``(renamed_arrow_table, target_case_pk_columns)`` after
    case-insensitive column alignment against an existing Delta table.

    Boundary-layer accommodation: tables created outside our writer
    (specifically the Fabric ``01_load_lakehouse`` notebook, which uses
    Spark PascalCase — ``ID``, ``Name``, ``As_Of``, ``Value_Chain``) hold
    columns whose case does not match what our local Parquet writer
    emits (``id``, ``name``, ``as_of``, ``value_chain``). DataFusion's
    SQL parser (used by delta-rs for MERGE predicates) rejects
    ``target.id = source.id`` when the target column is literally named
    ``ID``, so we translate at this single seam instead of forcing every
    upstream / downstream writer to agree on a casing convention.

    The internal contract (:data:`_TABLE_PRIMARY_KEYS` stays lowercase)
    is deliberately preserved — this helper is the ONLY place case
    translation happens. Callers keep passing lowercase PKs; the
    returned ``target_case_pk_columns`` are the target's actual names
    for those PKs and are what the MERGE predicate should reference.

    ``pyarrow.Table.rename_columns`` returns a new Table (it does not
    mutate in place), so the caller's Arrow table is safe to reuse.
    When the source and target already agree on case, this is a no-op
    rename — still a copy, but semantically identical.

    Parameters
    ----------
    arrow_table:
        The source ``pyarrow.Table`` about to be MERGEd. Not mutated.
    target_columns:
        Column names read from the existing Delta table's schema
        (``[f.name for f in DeltaTable.schema().fields]``). Case-sensitive
        — these are the names the MERGE predicate must reference.
    pk_columns:
        The lowercase primary-key columns from
        :data:`_TABLE_PRIMARY_KEYS` for this table.
    table_name:
        Used only in error messages so operators can pinpoint which of
        the ~34 tables drifted.

    Returns
    -------
    tuple[pyarrow.Table, tuple[str, ...]]
        A renamed copy of ``arrow_table`` whose column names match the
        target's case, plus the PK columns rewritten to the target's
        case (ready to plug straight into a MERGE predicate).

    Raises
    ------
    LakehouseWriteError
        If any source column has no case-insensitive match in the target
        (structural drift — a column we emit does not exist in the
        target). NEVER silently drop it — that would corrupt the Delta
        table's shape.

        If any PK column from :data:`_TABLE_PRIMARY_KEYS` has no
        case-insensitive match in the target (the target table is
        fundamentally incompatible with our PK contract — likely built
        against an older schema or a different entity entirely).
    """
    target_by_lower: dict[str, str] = {c.lower(): c for c in target_columns}
    source_columns = list(arrow_table.column_names)

    # Every source column must exist in the target under some casing.
    # Missing = structural drift; we refuse rather than silently drop.
    new_names: list[str] = []
    for src in source_columns:
        target_name = target_by_lower.get(src.lower())
        if target_name is None:
            raise LakehouseWriteError(
                f"Table '{table_name}' schema drift: source column "
                f"'{src}' not present in target (target has: "
                f"{sorted(target_columns)}). The Fabric table was "
                f"likely created with a different schema. Recreate "
                f"the table or align schemas."
            )
        new_names.append(target_name)

    # Every PK from our lowercase contract must resolve to a target
    # column too — otherwise the MERGE predicate would reference a
    # nonexistent column. This is a stricter check than the source-
    # column check above: it catches the case where the target table
    # exists but was created from a completely different entity.
    target_case_pk: list[str] = []
    for pk in pk_columns:
        target_pk_name = target_by_lower.get(pk.lower())
        if target_pk_name is None:
            raise LakehouseWriteError(
                f"Table '{table_name}' schema drift: primary-key "
                f"column '{pk}' not present in target (target has: "
                f"{sorted(target_columns)}). The target table is "
                f"fundamentally incompatible with the expected schema "
                f"— cannot MERGE. Recreate the table."
            )
        target_case_pk.append(target_pk_name)

    # ``rename_columns`` returns a new pyarrow.Table — the caller's
    # arrow_table is left untouched even when the rename is a no-op.
    return arrow_table.rename_columns(new_names), tuple(target_case_pk)


def write_delta_table(
    parquet_path: Path,
    *,
    workspace_id: str,
    lakehouse_id: str,
    table_name: str,
    credential=None,
    onelake_endpoint: str | None = None,
) -> str:
    """MERGE-upsert ``parquet_path`` into a Delta table in OneLake ``Tables/``.

    Behaviour (deliberate change from the earlier append-only path — see
    module docstring):

    * **First write** (table does not exist yet) — falls back to
      ``write_deltalake(mode="append", schema_mode="merge")`` so the
      table is created with schema evolution enabled.
    * **Subsequent writes** — MERGE upsert keyed on the table's primary
      key columns (see :data:`_TABLE_PRIMARY_KEYS`):

        - **Match** on PK columns ONLY (never on ``as_of``).
        - When matched AND at least one non-PK, non-``as_of`` column
          differs → update every column (including ``as_of``, so the
          latest state carries the latest stamp).
        - When matched AND equal-except-``as_of`` → skip (the "no
          bandwidth" win: no version bump, no write).
        - When not matched → insert with the new ``as_of``.

    **Consequence:** after each run the table holds one row per PK — the
    latest observed state — NOT one row per ``(PK, as_of)`` snapshot.
    This is a deliberate departure from the earlier append-only
    behaviour. SCD Type 2 history would be a separate design.

    NULL semantics are handled with SQL ``IS DISTINCT FROM`` (null-safe:
    ``(NULL, NULL)`` → not distinct; ``(NULL, 'x')`` → distinct). Naïve
    ``target.col <> source.col`` returns NULL when either side is NULL,
    which SQL treats as false — bad for change detection.

    Parameters
    ----------
    parquet_path:
        Local Parquet file to upsert. Rows are read via ``pyarrow``.
    workspace_id:
        Fabric workspace ID (GUID). Same validation as :func:`export_to_lakehouse`.
    lakehouse_id:
        Fabric lakehouse ID (GUID). Same validation as :func:`export_to_lakehouse`.
    table_name:
        Name of the target Delta table under ``Tables/``. MUST be a key
        in :data:`_TABLE_PRIMARY_KEYS` — unknown names raise
        :class:`LakehouseWriteError` (silent default of ``("id",)`` is
        deliberately not used; see :func:`_get_primary_keys`).
    credential:
        Optional Azure credential. Defaults to
        :class:`azure.identity.DefaultAzureCredential`.
    onelake_endpoint:
        Optional OneLake DFS endpoint URL. Regional endpoint override,
        honoured exactly like :func:`export_to_lakehouse`.

    Returns
    -------
    str
        The ABFSS URL of the Delta table (``abfss://<ws>@<host>/<lh>/Tables/<name>``).

    Raises
    ------
    LakehouseNotConfiguredError
        If ``workspace_id`` / ``lakehouse_id`` are unset or malformed, if
        ``onelake_endpoint`` is malformed, or if the ``deltalake`` package
        is not installed.
    LakehouseWriteError
        If ``table_name`` is not registered in :data:`_TABLE_PRIMARY_KEYS`,
        if token acquisition fails, if the Parquet read fails, if the
        create-path ``write_deltalake`` call fails, if the MERGE call
        fails, or if opening the existing Delta table fails for any
        reason other than "table not found". The table name appears in
        the message so operators can pinpoint schema drift or a single
        bad table in a batch run.
    """
    workspace_id = _normalize_fabric_id(workspace_id, "FABRIC_WORKSPACE_ID")
    lakehouse_id = _normalize_fabric_id(lakehouse_id, "FABRIC_LAKEHOUSE_ID")
    account_url = _resolve_onelake_endpoint(onelake_endpoint)
    host = _host_from_endpoint(account_url)

    # Look up PK columns BEFORE touching credentials / disk. A mis-named
    # Parquet file is a config bug (config-shaped, not I/O-shaped) so we
    # want it to fail fast without spending a token round-trip.
    pk_columns = _get_primary_keys(table_name)

    # Fail fast on missing library BEFORE touching credentials / disk.
    DeltaTable = _load_delta_table()

    cred = credential if credential is not None else _default_credential()
    token = _bearer_token(cred)

    try:
        arrow_table = _read_parquet_table(parquet_path)
    except Exception as exc:  # noqa: BLE001
        raise LakehouseWriteError(
            f"Failed to read Parquet file for Delta table '{table_name}' "
            f"(path={parquet_path}): {exc}"
        ) from exc

    table_url = _DELTA_URL_TEMPLATE.format(
        workspace_id=workspace_id,
        host=host,
        lakehouse_id=lakehouse_id,
        table_name=table_name,
    )
    storage_options = {
        "bearer_token": token,
        # Tells the underlying object_store layer to treat this URL as a
        # Fabric OneLake endpoint. Without this flag delta-rs may attempt
        # generic ADLS Gen2 discovery and misroute the request.
        "use_fabric_endpoint": "true",
    }

    # ---- Open existing Delta table, or fall back to the create path. ----
    #
    # Existence probe via listing would be a network round-trip and racy.
    # We drive it off the DeltaTable constructor: on first write it
    # raises a "not found"-class exception (see :func:`_is_table_not_found`)
    # and we bootstrap the table with append + schema_mode=merge.
    try:
        delta_table = DeltaTable(table_url, storage_options=storage_options)
    except Exception as exc:  # noqa: BLE001
        if _is_table_not_found(exc):
            logger.info(
                "Delta table '%s' does not exist yet — bootstrapping via "
                "first-write append (schema_mode=merge). URL=%s",
                table_name,
                table_url,
            )
            write_deltalake = _load_write_deltalake()
            try:
                write_deltalake(
                    table_url,
                    arrow_table,
                    mode="append",
                    schema_mode="merge",
                    storage_options=storage_options,
                )
            except Exception as create_exc:  # noqa: BLE001
                raise LakehouseWriteError(
                    f"Failed to create Delta table '{table_name}' in "
                    f"OneLake (first-write path, "
                    f"file={parquet_path.name}): {create_exc}"
                ) from create_exc
            logger.info(
                "Created Delta table %s from %s (%s)",
                table_name,
                parquet_path.name,
                table_url,
            )
            return table_url
        # Anything other than "not found" is a genuine open failure —
        # bad credentials, wrong URL, transient network. Surface it with
        # the table name so operators can pinpoint the culprit.
        raise LakehouseWriteError(
            f"Failed to open Delta table '{table_name}' for MERGE "
            f"upsert in OneLake: {exc}"
        ) from exc

    # ---- MERGE upsert path (existing table). ----
    #
    # Boundary-layer accommodation: tables created by the Fabric
    # ``01_load_lakehouse`` notebook spell columns in PascalCase
    # (``ID``, ``Name``, ``As_Of``, ``Value_Chain``) while our local
    # writer emits lowercase. Read the target's schema first, then
    # translate our source's column case to match — see
    # :func:`_align_arrow_to_target_schema` for the full rationale.
    # Structural drift (missing columns / missing PKs) raises HERE,
    # before we build any SQL predicate.
    try:
        target_columns = [f.name for f in delta_table.schema().fields]
    except Exception as exc:  # noqa: BLE001 — wrap schema-read failures
        raise LakehouseWriteError(
            f"Failed to read schema of Delta table '{table_name}' for "
            f"MERGE upsert alignment: {exc}"
        ) from exc

    aligned_arrow, target_pk_columns = _align_arrow_to_target_schema(
        arrow_table, target_columns, pk_columns, table_name
    )

    # After alignment, aligned_arrow.column_names ARE the target's case.
    # Build predicates from that. All identifiers are double-quoted so
    # PascalCase (and future names with spaces / reserved words) survive
    # the DataFusion SQL parse — ``target."ID" = source."ID"`` works
    # where the unquoted ``target.ID`` may be case-folded to ``id``.
    aligned_columns = list(aligned_arrow.column_names)
    aligned_pk_set = set(target_pk_columns)

    pk_predicate = " AND ".join(
        f'target."{col}" = source."{col}"' for col in target_pk_columns
    )
    # Non-PK, non-``as_of`` columns are the "did anything actually change?"
    # comparison set. ``as_of`` is compared case-insensitively (a target
    # spelled ``As_Of`` still means "the timestamp column") — a column
    # literally named ``asof`` with no underscore is NOT the same column
    # and would be treated as a normal comparison column.
    compare_columns = [
        c for c in aligned_columns
        if c not in aligned_pk_set and c.lower() != "as_of"
    ]
    # ``IS DISTINCT FROM`` is null-safe under the DataFusion SQL dialect
    # delta-rs uses. Verified against delta-rs 1.6.2.
    update_predicate = (
        " OR ".join(
            f'target."{c}" IS DISTINCT FROM source."{c}"' for c in compare_columns
        )
        if compare_columns
        else None
    )

    try:
        merge_builder = delta_table.merge(
            source=aligned_arrow,
            predicate=pk_predicate,
            source_alias="source",
            target_alias="target",
        )
        # Edge case: bridge tables like ``bridge_gap_entity`` have every
        # column in the composite PK, so ``compare_columns`` is empty.
        # Any matched row is definitionally unchanged (no non-PK column
        # exists) — skip the update entirely. New rows still land via
        # ``when_not_matched_insert_all`` below.
        if update_predicate is not None:
            merge_builder = merge_builder.when_matched_update_all(
                predicate=update_predicate
            )
        merge_builder = merge_builder.when_not_matched_insert_all()
        merge_builder.execute()
    except Exception as exc:  # noqa: BLE001 — wrap all delta-rs errors
        raise LakehouseWriteError(
            f"Failed to MERGE-upsert Parquet '{parquet_path.name}' into "
            f"Delta table '{table_name}' in OneLake: {exc}"
        ) from exc

    logger.info(
        "MERGE-upserted %s into Delta table %s (%s)",
        parquet_path.name,
        table_name,
        table_url,
    )
    return table_url


def export_regimpact_tables(
    tables_dir: Path,
    gold_dir: Path,
    *,
    workspace_id: str,
    lakehouse_id: str,
    credential=None,
    onelake_endpoint: str | None = None,
) -> dict[str, list[str]]:
    """MERGE-upsert every Parquet file in ``tables_dir`` and ``gold_dir``
    into its Delta table under OneLake ``Tables/``.

    Each table's primary key is looked up in :data:`_TABLE_PRIMARY_KEYS`
    (keyed by Parquet file stem). Rows accumulate as latest-state, not
    snapshot history — see :func:`write_delta_table` for the MERGE
    semantics (match on PK only, update-if-changed, insert-if-new, skip
    equal-except-``as_of``). This is a deliberate change from the earlier
    append-only behaviour.

    Parquet is the source of truth (typed, larger, exactly what the
    ``export_tables`` / ``export_gold`` local pipeline just produced) —
    CSV siblings are ignored here. Table name for each file is the file
    stem (``dim_control.parquet`` → Delta table ``dim_control``).

    Namespacing is deliberately flat: raw entity table names
    (``controls``, ``obligations``) and gold names
    (``dim_control``, ``fact_gap``) don't collide, so both sets land at
    the top of ``Tables/`` for a clean Fabric UI view.

    Both directories are optional at runtime: missing / empty →
    that half is skipped and reported as an empty list. Mirrors the
    ``export_regimpact_lakehouse`` shape for the Files/ path.

    Parameters
    ----------
    tables_dir:
        Local directory holding raw entity Parquet files.
    gold_dir:
        Local directory holding star-schema Parquet files.
    workspace_id:
        Fabric workspace ID (GUID).
    lakehouse_id:
        Fabric lakehouse ID (GUID).
    credential:
        Optional Azure credential. Defaults to
        :class:`azure.identity.DefaultAzureCredential`.
    onelake_endpoint:
        Optional OneLake DFS endpoint URL. Regional endpoint override,
        honoured exactly like :func:`export_regimpact_lakehouse`.

    Returns
    -------
    dict[str, list[str]]
        ``{"raw": [...table urls...], "gold": [...table urls...]}``.

    Raises
    ------
    LakehouseNotConfiguredError
        If ``workspace_id`` / ``lakehouse_id`` are unset or malformed, if
        ``onelake_endpoint`` is malformed, or if the ``deltalake`` package
        is not installed.
    LakehouseWriteError
        If any Delta append fails. The failing table name appears in the
        message.
    """
    # Resolve credential once so all writes reuse the same identity.
    cred = credential if credential is not None else _default_credential()

    def _write_dir(source: Path) -> list[str]:
        if not source.exists():
            logger.warning(
                "OneLake Delta writeback skipped: %s does not exist.", source
            )
            return []
        parquet_files = sorted(source.glob("*.parquet"))
        if not parquet_files:
            logger.warning(
                "OneLake Delta writeback skipped: %s has no Parquet files.",
                source,
            )
            return []
        urls: list[str] = []
        for parquet_path in parquet_files:
            url = write_delta_table(
                parquet_path,
                workspace_id=workspace_id,
                lakehouse_id=lakehouse_id,
                table_name=parquet_path.stem,
                credential=cred,
                onelake_endpoint=onelake_endpoint,
            )
            urls.append(url)
        return urls

    raw_urls = _write_dir(tables_dir)
    gold_urls = _write_dir(gold_dir)

    logger.info(
        "OneLake Delta writeback complete: %d raw + %d gold table(s) upserted.",
        len(raw_urls),
        len(gold_urls),
    )
    return {"raw": raw_urls, "gold": gold_urls}
