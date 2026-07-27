"""Measurement script: real rendered sizes of agent observation envelopes
against a Beijing-sized network, to ground (and verify) the planner.py
observation-rendering fix (docs/phase2/DECISIONS.md, live-demo truncation
bug).

Not a test — a one-off probe, run manually:
    .venv\\Scripts\\python.exe tools\\agent_obs_size_probe.py

Points ``services.agent.tools_network`` at
``runtime/com-smoke/Urban Intersection Beijing.CN_31360/Intersection Beijing.inpx``
(a real Vissim 2026 example network already checked into runtime/ from an
earlier live-COM probe — no COM/Vissim needed here, this only exercises the
pure-Python XML parsing + read-tool dispatch path), dispatches the three
read tools named in the bug report through the REAL ``toolkit.dispatch`` path
(so the measured size is the exact envelope shape the orchestrator appends to
``state.history``), and reports, for each:

  - the full envelope's ``json.dumps`` byte length
  - the OLD behavior: what the flat 300-char raw-slice truncator used to
    produce (kept here as a fixed local re-implementation of the pre-fix
    code, since the fix replaced it in planner.py — this is a comparison
    baseline, not a live import)
  - the NEW behavior: ``_render_observation`` at both the compact
    (``_OBSERVATION_SNIPPET_CHARS``) and generous
    (``_recent_observation_budget()``) budgets, and whether each output
    parses as valid JSON
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# Windows console default codepage (cp1252) can't encode the Korean/em-dash
# text in the truncation markers -- reconfigure stdout to UTF-8 so this probe
# runs the same way in any terminal (mirrors the app's own ko-primary,
# ensure_ascii=False convention).
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import services.agent.tools_network as tools_network  # noqa: E402
import services.agent.tools_edit  # noqa: F401,E402  (registers write tools -- harmless, unused here)
from services.agent.toolkit import dispatch  # noqa: E402
from services.agent.planner import (  # noqa: E402
    _OBSERVATION_SNIPPET_CHARS,
    _recent_observation_budget,
    _render_observation,
)

BEIJING_INPX = (
    Path(__file__).resolve().parent.parent.parent
    / "runtime"
    / "com-smoke"
    / "Urban Intersection Beijing.CN_31360"
    / "Intersection Beijing.inpx"
)


def _old_snippet(observation: object, cap: int = 300) -> str:
    """Fixed re-implementation of the OLD (pre-fix) `_observation_snippet` --
    a flat raw-character slice, unconditionally, regardless of recency."""
    text = json.dumps(observation, ensure_ascii=False, separators=(",", ":"))
    if len(text) > cap:
        text = text[: cap - 3] + "..."
    return text


def _parses(text: str) -> bool:
    try:
        json.loads(text)
        return True
    except json.JSONDecodeError:
        return False


def measure(tool_name: str, params: dict, language: str = "ko") -> None:
    envelope = dispatch(tool_name, params)
    full_json = json.dumps(envelope, ensure_ascii=False, separators=(",", ":"))
    print(f"\n=== {tool_name} ===")
    print(f"status: {envelope['status']}")
    if envelope.get("data"):
        for key, value in envelope["data"].items():
            if isinstance(value, list):
                print(f"  data[{key!r}]: list, len={len(value)}")
            elif isinstance(value, (int, float, str)):
                print(f"  data[{key!r}]: {value!r}")
    print(f"full envelope json.dumps length: {len(full_json)} chars")

    old = _old_snippet(envelope)
    print(f"\n  OLD _observation_snippet (flat 300-char raw slice, used for EVERY history entry):")
    print(f"    output length: {len(old)} chars | parses as JSON: {_parses(old)}")
    print(f"    output (first 200 chars): {old[:200]!r}")

    compact = _render_observation(envelope, _OBSERVATION_SNIPPET_CHARS, language)
    print(f"\n  NEW _render_observation @ compact budget ({_OBSERVATION_SNIPPET_CHARS} chars, older history entries):")
    print(f"    output length: {len(compact)} chars | parses as JSON: {_parses(compact)}")
    print(f"    output (first 250 chars): {compact[:250]!r}")

    recent_budget = _recent_observation_budget()
    recent = _render_observation(envelope, recent_budget, language)
    print(f"\n  NEW _render_observation @ recent budget ({recent_budget} chars, MOST RECENT history entry):")
    print(f"    output length: {len(recent)} chars | parses as JSON: {_parses(recent)}")
    is_whole = recent == full_json
    print(f"    renders COMPLETE (byte-identical to full envelope): {is_whole}")
    if not is_whole:
        print(f"    output (first 250 chars): {recent[:250]!r}")
        print(f"    output (last 250 chars): ...{recent[-250:]!r}")


def main() -> None:
    if not BEIJING_INPX.exists():
        print(f"Beijing inpx not found at {BEIJING_INPX}", file=sys.stderr)
        sys.exit(1)
    print(f"Using: {BEIJING_INPX}")
    tools_network.set_active_inpx(BEIJING_INPX)
    try:
        measure("get_network_overview", {})
        measure("get_vehicle_inputs", {})
        measure("get_conflict_areas", {})
    finally:
        tools_network.set_active_inpx(None)


if __name__ == "__main__":
    main()
