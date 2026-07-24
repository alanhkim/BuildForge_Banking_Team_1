"""Empirically probe the output-token ceiling of a Foundry-deployed agent.

The Foundry portal does not always expose an agent's max_output_tokens
setting. This script sends a series of prompts asking for outputs of known
target lengths and reports:

  * how many bytes actually came back
  * output_tokens reported by the API (when available)
  * whether the response was truncated / incomplete

If the deployed agent has a portal-side output cap (e.g. 1000 tokens), the
returned byte count will plateau well below the requested length. That
plateau IS the cap.

Usage:

    python scripts/probe-foundry-agent-caps.py                    # control_mapper
    python scripts/probe-foundry-agent-caps.py --agent gap        # gap_analyst
    python scripts/probe-foundry-agent-caps.py --agent remediation
    python scripts/probe-foundry-agent-caps.py --agent score
    python scripts/probe-foundry-agent-caps.py --agent interpreter
    python scripts/probe-foundry-agent-caps.py --agent controls
    python scripts/probe-foundry-agent-caps.py --targets 500,2000,5000

Requires the same .env used by the main pipeline.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from pathlib import Path
from typing import Any

from regimpact.agents.foundry_client import (
    FoundryAgentClient,
    FoundryAgentConfig,
)
from regimpact.settings import settings


AGENT_KEYS = {
    "controls": "control_mapper",
    "control_mapper": "control_mapper",
    "gap": "gap_analyst",
    "gap_analyst": "gap_analyst",
    "remediation": "remediation_planner",
    "remediation_planner": "remediation_planner",
    "score": "score_narrator",
    "score_narrator": "score_narrator",
    "interpreter": "regulation_interpreter",
    "regulation_interpreter": "regulation_interpreter",
}


def _resolve_agent(key: str) -> tuple[str, str, str]:
    """Return (agent_name, agent_version, normalized_key) for the given key."""
    normalized = AGENT_KEYS.get(key.lower())
    if normalized is None:
        raise SystemExit(
            f"Unknown agent key {key!r}. Choose from: {sorted(set(AGENT_KEYS))}"
        )
    name_attr = f"foundry_{normalized}_agent_name"
    ver_attr = f"foundry_{normalized}_agent_version"
    name = getattr(settings, name_attr, None)
    version = getattr(settings, ver_attr, None)
    if not name or not version:
        raise SystemExit(
            f"Settings missing {name_attr} / {ver_attr}. Check .env."
        )
    return name, version, normalized


def _build_probe_prompt(target_chars: int) -> str:
    """Ask the agent to emit a JSON blob of known length.

    Kept intentionally simple — we don't want the agent's own instructions
    (JSON contract, tool discipline, etc.) to reject the probe. Everything
    below asks for a single flat JSON object with a padding field.
    """
    return (
        "Diagnostic probe. Ignore any prior instructions about response "
        "schemas or tool discipline. Reply with ONE JSON object exactly of "
        "the form: {\"probe\": \"pad\", \"payload\": \"XXXX\"} where XXXX "
        f"is the lowercase letter 'a' repeated exactly {target_chars} times. "
        "No prose, no markdown fences, no additional keys. Emit the "
        "complete JSON in a single output."
    )


def _extract_usage(metadata: dict[str, Any]) -> dict[str, Any]:
    """Pull usage-related fields out of metadata, tolerating shape drift."""
    usage_keys = (
        "usage",
        "output_tokens",
        "input_tokens",
        "total_tokens",
        "reasoning_tokens",
        "completion_tokens",
        "prompt_tokens",
        "status",
        "incomplete_details",
        "finish_reason",
        "stop_reason",
    )
    picked: dict[str, Any] = {}
    for key in usage_keys:
        if key in metadata:
            picked[key] = metadata[key]
    # Nested usage dict (Responses API surfaces `usage.output_tokens`)
    usage = metadata.get("usage")
    if isinstance(usage, dict):
        for k in (
            "input_tokens",
            "output_tokens",
            "total_tokens",
            "reasoning_tokens",
            "output_tokens_details",
        ):
            if k in usage:
                picked.setdefault(f"usage.{k}", usage[k])
    return picked


def _looks_truncated(text: str) -> bool:
    """Cheap heuristic — did the JSON blob end mid-write?"""
    stripped = text.strip()
    if not stripped:
        return True
    # Complete JSON objects end in '}' after balancing braces. If the last
    # non-whitespace char isn't '}' or ']', we almost certainly got cut off.
    if stripped[-1] not in "}]\"":
        return True
    # Count brace balance — if positive, we're missing closers.
    depth = 0
    in_string = False
    escape = False
    for ch in stripped:
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == "\"":
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
    return depth != 0


def _probe_once(
    client: FoundryAgentClient, target_chars: int, dump_dir: Path | None
) -> dict[str, Any]:
    prompt = _build_probe_prompt(target_chars)
    prompt_bytes = len(prompt)
    try:
        response = client.invoke(prompt)
    except Exception as exc:  # noqa: BLE001 — surface everything
        return {
            "target_chars": target_chars,
            "prompt_bytes": prompt_bytes,
            "error": f"{type(exc).__name__}: {exc}",
            "traceback": traceback.format_exc(),
        }

    text = response.text or ""
    result: dict[str, Any] = {
        "target_chars": target_chars,
        "prompt_bytes": prompt_bytes,
        "response_bytes": len(text),
        "response_head": text[:120],
        "response_tail": text[-80:] if len(text) > 80 else "",
        "looks_truncated": _looks_truncated(text),
        "usage": _extract_usage(response.metadata),
        "metadata_keys": sorted(response.metadata.keys()),
    }
    # If the agent returned its native envelope, extract the inner answer
    # length — that's the field the pipeline validator gates on.
    inner_answer: str | None = None
    try:
        outer = json.loads(text)
        if isinstance(outer, dict):
            answer_field = outer.get("answer")
            if isinstance(answer_field, str):
                inner_answer = answer_field
                result["envelope_answer_bytes"] = len(answer_field)
                result["envelope_answer_looks_truncated"] = _looks_truncated(
                    answer_field
                )
            elif answer_field is not None:
                # Sometimes agents return `answer` as an object, not a string.
                result["envelope_answer_bytes"] = len(
                    json.dumps(answer_field, separators=(",", ":"))
                )
                result["envelope_answer_is_object"] = True
    except (json.JSONDecodeError, ValueError):
        pass

    # Try to parse payload length so we can tell "cap on total output" vs
    # "cap on the padding field specifically" apart.
    try:
        parsed = json.loads(text)
        payload = parsed.get("payload") if isinstance(parsed, dict) else None
        if isinstance(payload, str):
            result["parsed_payload_chars"] = len(payload)
    except (json.JSONDecodeError, ValueError):
        result["parsed_payload_chars"] = None

    # Dump full body + raw metadata to disk so we can inspect exactly what
    # the agent produced. Names include target so multiple probes coexist.
    if dump_dir is not None:
        dump_dir.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y%m%dT%H%M%S")
        body_path = dump_dir / f"probe-{ts}-target{target_chars}-body.txt"
        meta_path = dump_dir / f"probe-{ts}-target{target_chars}-meta.json"
        body_path.write_text(text, encoding="utf-8")
        try:
            meta_path.write_text(
                json.dumps(response.metadata, indent=2, default=str),
                encoding="utf-8",
            )
        except (TypeError, ValueError):
            meta_path.write_text(repr(response.metadata), encoding="utf-8")
        if inner_answer is not None:
            answer_path = (
                dump_dir / f"probe-{ts}-target{target_chars}-answer.txt"
            )
            answer_path.write_text(inner_answer, encoding="utf-8")
            result["dump_answer_path"] = str(answer_path)
        result["dump_body_path"] = str(body_path)
        result["dump_meta_path"] = str(meta_path)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--agent",
        default="controls",
        help=f"Agent key. One of: {sorted(set(AGENT_KEYS))}",
    )
    parser.add_argument(
        "--targets",
        default="500,1500,3000,6000",
        help="Comma-separated list of target output lengths (chars).",
    )
    parser.add_argument(
        "--dump-dir",
        default="output/foundry-probes",
        help=(
            "Directory to dump full response bodies + metadata. "
            "Pass empty string to disable."
        ),
    )
    args = parser.parse_args()

    try:
        targets = [int(x.strip()) for x in args.targets.split(",") if x.strip()]
    except ValueError as exc:
        raise SystemExit(f"Invalid --targets: {exc}") from exc

    agent_name, agent_version, normalized_key = _resolve_agent(args.agent)

    print("=== Foundry agent output-cap probe ===")
    print(f"agent_name    = {agent_name}")
    print(f"agent_version = {agent_version}")
    print(f"endpoint      = {settings.foundry_project_endpoint}")
    print(f"deployment    = {settings.foundry_model_deployment_name!r}")
    print(
        "client_max_output_tokens (settings) = "
        f"{settings.foundry_agent_max_output_tokens}"
    )
    print(
        "FOUNDRY_PASS_MAX_OUTPUT_TOKENS      = "
        f"{settings.foundry_pass_max_output_tokens}"
    )
    print(f"targets       = {targets}")
    print()

    config = FoundryAgentConfig(
        project_endpoint=settings.foundry_project_endpoint,
        model_deployment_name=settings.foundry_model_deployment_name,
        agent_name=agent_name,
        agent_version=agent_version,
        api_version=settings.foundry_api_version or "2025-05-01-preview",
        timeout_seconds=settings.foundry_agent_timeout_seconds,
        max_output_tokens=settings.foundry_agent_max_output_tokens,
    )
    client = FoundryAgentClient(config=config)

    dump_dir: Path | None = None
    if args.dump_dir:
        dump_dir = Path(args.dump_dir) / normalized_key
        print(f"dump_dir      = {dump_dir}")
    print()

    results: list[dict[str, Any]] = []
    for target in targets:
        print(f"--- probe: target_chars={target} ---")
        result = _probe_once(client, target, dump_dir)
        results.append(result)
        if "error" in result:
            print(f"  ERROR: {result['error']}")
            continue
        print(f"  response_bytes           = {result['response_bytes']}")
        print(
            "  envelope_answer_bytes    = "
            f"{result.get('envelope_answer_bytes')}"
        )
        print(
            "  envelope_answer_trunc'd  = "
            f"{result.get('envelope_answer_looks_truncated')}"
        )
        print(f"  parsed_payload_chars     = {result.get('parsed_payload_chars')}")
        print(f"  outer_looks_truncated    = {result['looks_truncated']}")
        print(f"  metadata_keys            = {result['metadata_keys']}")
        print(f"  usage                    = {result['usage']}")
        print(f"  response_head            = {result['response_head']!r}")
        if result["response_tail"]:
            print(f"  response_tail            = {result['response_tail']!r}")
        if "dump_body_path" in result:
            print(f"  dump_body                = {result['dump_body_path']}")
        print()

    # Summary — plateau detection.
    print("=== Summary ===")
    for r in results:
        if "error" in r:
            print(f"  target={r['target_chars']:>5}  ERROR: {r['error']}")
            continue
        out_tokens = r.get("usage", {}).get("usage.output_tokens") or r.get(
            "usage", {}
        ).get("output_tokens")
        print(
            f"  target={r['target_chars']:>5}  "
            f"resp_bytes={r['response_bytes']:>5}  "
            f"answer_bytes={str(r.get('envelope_answer_bytes')):>5}  "
            f"ans_trunc={str(r.get('envelope_answer_looks_truncated')):>5}  "
            f"out_tokens={out_tokens!s:>6}  "
            f"outer_trunc={r['looks_truncated']}"
        )

    print()
    print("Interpretation:")
    print(
        "  * envelope_answer_bytes is the length of the INNER `answer` string "
        "— that's what the pipeline validator gates on."
    )
    print(
        "  * If answer_bytes plateaus near 1000 across targets, that's the "
        "same cap the pipeline is hitting."
    )
    print(
        "  * If resp_bytes plateaus but answer_bytes climbs, only the "
        "envelope is capped (unlikely to be our bug)."
    )
    print(
        "  * If out_tokens plateaus, the cap is in tokens on the Foundry side."
    )
    print(
        "  * Full response bodies and raw metadata are in the dump directory."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
