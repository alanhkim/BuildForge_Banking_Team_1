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
