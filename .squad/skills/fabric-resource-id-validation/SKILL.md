# Fabric Resource ID Validation

## What

A boundary-hardening pattern for any Python function that consumes a Fabric/OneLake resource ID (workspace, lakehouse, item, capacity) from env vars or user config.

Strip → validate → use cleaned value everywhere. Malformed IDs map to the "not configured" error class, never to a "write failed" error class.

## When to reach for it

Any time you receive a Fabric GUID from outside your process (env var, config file, CLI arg, YAML, agent tool call) and forward it into an SDK call whose server-side error message is opaque. Signals:

- The SDK error surfaces late (at upload/query time), not at construction time.
- The server error is `FriendlyNameSupportDisabled`, `InvalidRequestBody`, `BadRequest` with vague ID text, or a raw ADLS filesystem-name rejection.
- The value could plausibly be pasted by a human into a shell (trailing newline, surrounding quotes, whitespace all common).

## The pattern

```python
import uuid


class MyServiceNotConfiguredError(Exception):
    """Soft-skip error: fix the config, then retry."""


def _normalize_fabric_id(raw: str | None, env_var: str) -> str:
    """Strip whitespace + surrounding quotes and validate as a canonical GUID."""
    if raw is None:
        raise MyServiceNotConfiguredError(
            f"{env_var} is not set; cannot proceed."
        )
    cleaned = raw.strip().strip("'").strip('"').strip()
    if not cleaned:
        raise MyServiceNotConfiguredError(
            f"{env_var} is not set; cannot proceed."
        )
    try:
        parsed = uuid.UUID(cleaned)
    except (ValueError, AttributeError, TypeError) as exc:
        raise MyServiceNotConfiguredError(
            f"{env_var} must be a Fabric GUID (got '{cleaned}'). "
            f"Copy it from the Fabric portal → Workspace settings → About."
        ) from exc
    if str(parsed) != cleaned.lower():
        # Rejects braced ({…}), urn (urn:uuid:…), and no-dash (32-hex) forms.
        raise MyServiceNotConfiguredError(
            f"{env_var} must be a canonical GUID in 8-4-4-4-12 hex form "
            f"(got '{cleaned}')."
        )
    return cleaned
```

Then at the entry of the function that talks to Fabric:

```python
def upload(workspace_id: str, lakehouse_id: str, ...) -> ...:
    workspace_id = _normalize_fabric_id(workspace_id, "FABRIC_WORKSPACE_ID")
    lakehouse_id = _normalize_fabric_id(lakehouse_id, "FABRIC_LAKEHOUSE_ID")
    # ...use the cleaned values in SDK calls AND in returned URLs.
```

## Rules

1. **Never mutate the caller's config object.** Clean the value into a local, use the local everywhere downstream. This keeps `Settings` (or equivalent) as pure I/O binding.
2. **Strip in this order:** whitespace → quotes → whitespace. The final whitespace strip catches spaces that were *inside* the quotes.
3. **Use the cleaned value both in the SDK call AND in any URL you return.** Downstream stages (materializer, logging, telemetry) must see the same clean GUID.
4. **Map malformed IDs to the "not configured" error class, not the "write failed" class.** Rationale: malformed config fails every run until fixed — semantically identical to unset. If your project has decisions.md-level semantics for these classes, follow them.
5. **Signature-stable.** Do not change the public function signature to add a "validated" wrapper. Do it at the top of the existing function.
6. **`uuid` is stdlib.** No new dependencies.
7. **Canonical-form check is optional but recommended.** `uuid.UUID()` alone accepts `{guid}`, `urn:uuid:guid`, and no-dash 32-hex. If the downstream SDK is strict about the canonical 8-4-4-4-12 form (Fabric usually is), reject those variants explicitly with `str(parsed) == cleaned.lower()`.

## Anti-patterns

- Validating inside a `Settings` `__post_init__` — forces every caller through validation even when they never touch Fabric. Keep it at the Fabric boundary.
- Regex-based GUID validation. `uuid.UUID()` is the standard, handles case, and gives you the canonical string back for free.
- Catching `Exception` broadly around the parse. `ValueError` is what `uuid.UUID` raises on bad input; `AttributeError` / `TypeError` cover the "someone passed a non-string" edge case. Everything else should propagate — you don't want to silently swallow a memory error.
- Storing the cleaned value back into `os.environ` or `Settings`. Locality wins — other consumers may have their own validation needs.

## Origin

Extracted from the 2026-07-20 fix in `src/regimpact/lakehouse.py::export_to_lakehouse` after a user hit the opaque `FriendlyNameSupportDisabled` Fabric error caused by a trailing `\n` in `FABRIC_WORKSPACE_ID`. See `.squad/decisions/inbox/bishop-onelake-guid-validation.md` for the full rationale, and `.squad/agents/bishop/history.md` for the story.

## Related pattern — env-var override of hardcoded regional URLs

The same shape (strip → validate at boundary → soft-skip class on malformed → fall back to a module-level default) also works for **regional endpoint overrides** — cases where a hardcoded global URL is fine for most workspaces but fails on capacities that advertise a regional endpoint via `GET /v1/workspaces/{id}` → `oneLakeEndpoints.dfsEndpoint`. The failure mode is the same opaque `FriendlyNameSupportDisabled` server error, so the same validate-at-boundary + soft-skip pattern applies.

```python
_ONELAKE_ACCOUNT_URL = "https://onelake.dfs.fabric.microsoft.com"
_ONELAKE_ENDPOINT_MARKER = "onelake.dfs.fabric.microsoft.com"


def _resolve_onelake_endpoint(raw: str | None) -> str:
    """None/empty → global default; else validate shape and pass through."""
    if raw is None:
        return _ONELAKE_ACCOUNT_URL
    cleaned = raw.strip().strip("'").strip('"').strip()
    if not cleaned:
        return _ONELAKE_ACCOUNT_URL
    lowered = cleaned.lower()
    if not lowered.startswith("https://") or _ONELAKE_ENDPOINT_MARKER not in lowered:
        raise LakehouseNotConfiguredError(
            f"FABRIC_ONELAKE_DFS_ENDPOINT must be an https:// URL containing "
            f"'{_ONELAKE_ENDPOINT_MARKER}' (got '{cleaned}')."
        )
    return cleaned
```

Then:

```python
def export_to_lakehouse(..., onelake_endpoint: str | None = None) -> ...:
    account_url = _resolve_onelake_endpoint(onelake_endpoint)
    service = DataLakeServiceClient(account_url, credential=cred)
    # ...but do NOT taint returned identifiers (e.g. ABFSS URLs) with the
    # regional prefix — those are name-plane, regional routing is data-plane.
```

### Additional rules for endpoint overrides

8. **Backward-compat by construction.** New parameter defaults to `None` and resolves to the pre-existing hardcoded URL when unset. Callers on the old signature see zero behavior change.
9. **Env var lives in `Settings` as a pure passthrough** (`os.getenv(...) or ""`). The *default* URL lives in the domain module, not in `Settings`. Keeps `Settings` a dumb env binding and puts the operational knowledge next to the SDK call.
10. **Do NOT taint name-plane identifiers with regional prefixes.** ABFSS URLs (`abfss://{ws}@onelake.dfs.fabric.microsoft.com/...`), shortcut paths, Purview lineage records, and anything else parsed by downstream tools must stay on the canonical (non-regional) host. Regional routing is a data-plane concern applied transparently by OneLake based on the caller's location and the workspace's capacity region. Guard this invariant with a test.
11. **Do NOT auto-fallback ("try global, retry regional").** Silent fallback (a) hides capacity misconfiguration, (b) adds a wasted round-trip on the common case, (c) makes the failure mode ambiguous. Explicit config beats magic.
12. **Do NOT auto-detect from the Fabric metadata API.** Adds a synchronous REST hop per run, needs a different token scope (`api.fabric.microsoft.com`), and has an ambiguous failure mode if the metadata call itself fails. An env var is one line in `.env` and zero runtime cost.

### Related origin

Extracted from the 2026-07-21 fix in `src/regimpact/lakehouse.py` after the same operator hit `FriendlyNameSupportDisabled` a second time — this time with canonical GUIDs, because the workspace lives on a regional (North Central US) capacity and the hardcoded global endpoint's routing layer refused to forward. See `.squad/decisions/inbox/bishop-onelake-regional-endpoint.md`.

## Canonical OneLake ADLS path is bare GUID (NOT `{id}.Lakehouse`)

**Trap.** When you construct an ADLS Gen2 path to a Fabric lakehouse's `Files/` or `Tables/` area, the top-level directory under the workspace filesystem is the **bare lakehouse GUID**:

```
https://onelake.dfs.fabric.microsoft.com/{workspace_id}/{lakehouse_id}/Files/{subpath}/…
abfss://{workspace_id}@onelake.dfs.fabric.microsoft.com/{lakehouse_id}/Files/{subpath}/…
```

**NOT** `{lakehouse_id}.Lakehouse/Files/…`. The `.Lakehouse` suffix is a Fabric **UI / Spark-shortcut** convention — it is correct when mounting a lakehouse into a Spark notebook or creating a Fabric shortcut via the portal. It is **wrong** for direct ADLS Gen2 access via `azure.storage.filedatalake.DataLakeServiceClient`, which rejects the suffixed form with the opaque server-side error `FriendlyNameSupportDisabled` (Fabric's generic "identifier could not be resolved").

Microsoft's own docs use the `.Lakehouse` suffix in Spark-mount examples, so it's easy to copy the wrong pattern into an SDK code path and think you're following the docs. You're not — you're following a doc for a different code path.

### The definitive check

Do not trust blogs, tutorials, or even Microsoft docs about the path prefix. Ask Fabric directly:

```
GET https://api.fabric.microsoft.com/v1/workspaces/{workspace_id}/lakehouses/{lakehouse_id}
```

The response includes:

```json
"properties": {
  "oneLakeFilesPath":  "https://onelake.dfs.fabric.microsoft.com/{workspace_id}/{lakehouse_id}/Files",
  "oneLakeTablesPath": "https://onelake.dfs.fabric.microsoft.com/{workspace_id}/{lakehouse_id}/Tables"
}
```

**That response is the authoritative canonical form.** If the URL you are building in code does not match it byte-for-byte (after substituting your workspace_id and lakehouse_id), your URL is wrong regardless of what any documentation says. Note: no `.Lakehouse`.

### Diagnostic funnel for `FriendlyNameSupportDisabled`

The server-side error string `FriendlyNameSupportDisabled` covers at least three distinct root causes. Run these checks in order — cheapest first, and each check has to pass before the next failure mode becomes visible:

1. **GUID validation at the boundary.** Strip whitespace + quotes, `uuid.UUID()`, canonical-form check. Covers env-var copy/paste artefacts. Fix: `_normalize_fabric_id` pattern above.
2. **Regional endpoint routing.** `GET /v1/workspaces/{ws}` → `oneLakeEndpoints.dfsEndpoint`. If it's a regional URL (e.g. `https://northcentralus-onelake.dfs.fabric.microsoft.com`), the hardcoded global endpoint's routing layer may refuse to forward. Fix: env-var override + `_resolve_onelake_endpoint` pattern above.
3. **Canonical path prefix.** `GET /v1/workspaces/{ws}/lakehouses/{lh}` → `properties.oneLakeFilesPath`. Confirms the bare-GUID form; catches stale `.Lakehouse` suffix copy-paste from Spark-mount docs.

The generic error message will not tell you which layer is failing — the SDK short-circuits at the earliest failure. Fix each layer, then re-run to see the next.

### Additional rules (Fabric ADLS path construction)

13. **Do not use `.Lakehouse` in any string passed to an ADLS Gen2 SDK call or in any returned ABFSS URL.** It only belongs in Spark shortcut / UI mount paths, and those are constructed by Fabric, not by us.
14. **Byte-for-byte cross-check.** Any hand-constructed OneLake URL should match `properties.oneLakeFilesPath` / `oneLakeTablesPath` from `GET /v1/workspaces/{ws}/lakehouses/{lh}` when you substitute in your GUIDs. If it doesn't match, the URL is wrong — no exceptions, no "but the docs say".
15. **Guard against re-introduction with a regression test.** `assert ".Lakehouse" not in <constructed_path>` catches anyone (including future you) who pastes an example back in from a stale doc. Cheap, fast, worth it.
16. **Notebook / Spark-mount code CAN use `.Lakehouse`** in mount paths (e.g. `/lakehouse/default/Files/...` or `<lh>.Lakehouse/Tables/...` inside a Spark session). Do not sweep those away when fixing an ADLS Gen2 code path — different code path, different convention, both correct in their own context.

### Related origin (bare-GUID ADLS path)

Extracted from the 2026-07-21 (afternoon) fix in `src/regimpact/lakehouse.py::export_to_lakehouse` after the same operator hit `FriendlyNameSupportDisabled` a **third** time this week — GUIDs and endpoint were both correct by then; the failing layer was the `.Lakehouse` suffix that had lived in the code (and its docstring) since day one, copied from a Spark-mount doc. See `.squad/decisions/inbox/bishop-onelake-drop-lakehouse-suffix.md` for the full rationale and the diagnostic funnel writeup in `.squad/agents/bishop/history.md` under the same date.

### Extension: delta-rs writes against OneLake `Tables/`

Applies when materialising Delta tables directly from Python (no Fabric notebook, no Spark), using the `deltalake` (delta-rs) library. Adds three rules on top of rules 1-16 above:

17. **URL host = the resolved endpoint host, NOT canonical.** Unlike the Files upload path — where the returned ABFSS URL keeps `onelake.dfs.fabric.microsoft.com` because Spark shortcuts and OneLake lineage parse it downstream — delta-rs uses the URL host as the actual endpoint it will hit. There is no separate `endpoint` key in `storage_options` for OneLake; the URL host IS the endpoint. Passing canonical host with `FABRIC_ONELAKE_DFS_ENDPOINT` set silently reproduces the pre-trilogy regional-capacity misroute.

    ```python
    # CORRECT for delta-rs writes
    host = _resolve_onelake_endpoint(endpoint).split("://", 1)[-1].rstrip("/")
    url = f"abfss://{workspace_id}@{host}/{lakehouse_id}/Tables/{table_name}"
    ```

    All other trilogy invariants still apply: bare-GUID form, no `.Lakehouse` suffix, GUIDs pre-validated via `_normalize_fabric_id`.

18. **`storage_options` shape is fixed.** For OneLake specifically:

    ```python
    storage_options = {
        "bearer_token": credential.get_token("https://storage.azure.com/.default").token,
        "use_fabric_endpoint": "true",   # required — object_store special-cases OneLake
    }
    ```

    - Without `use_fabric_endpoint`, delta-rs's underlying `object_store` layer attempts generic ADLS Gen2 discovery and may misroute.
    - Bearer-token audience is `https://storage.azure.com/.default` — same audience the Files SDK uses implicitly via `DefaultAzureCredential`.
    - Refetch the token per write inside a batch. `DefaultAzureCredential` caches internally so this is cheap, and it immunises long batches against mid-run token expiry.

19. **Missing `deltalake` package = `LakehouseNotConfiguredError`, not `LakehouseWriteError`.** Lazy-import behind a helper; catch `ImportError` and raise the config-class error with a message pointing at `pip install 'regimpact[fabric]'`. Matches the decision-§0 taxonomy: config gap = yellow skip, transient/auth/write failure = red warn.

    ```python
    def _load_write_deltalake():
        try:
            from deltalake import write_deltalake
        except ImportError as exc:
            raise LakehouseNotConfiguredError(
                "The 'deltalake' package is required for OneLake Tables/ "
                "writeback. Install it with: pip install 'regimpact[fabric]'."
            ) from exc
        return write_deltalake
    ```

### Related origin (delta-rs OneLake extension)

Added 2026-07-21 (late evening) while landing `write_delta_table` and `export_regimpact_tables` in `src/regimpact/lakehouse.py`. Full rationale for rules 17-19 lives in `.squad/decisions/inbox/bishop-onelake-delta-tables.md` and the corresponding writeup in `.squad/agents/bishop/history.md` under the same date. Test coverage: `tests/test_lakehouse.py` grew 21 → 34 tests; the 13 new tests mock at the `deltalake.write_deltalake` boundary via a `sys.modules` fake so no live Fabric writes fire during CI.



