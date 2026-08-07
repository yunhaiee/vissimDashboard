"""Roadmap 3.1 GENESIS **SIGNALS** LIVE CHECK: does a ``services.genesis``
SIGNALISED draft open in a real PTV Vissim, and what does Vissim itself say
about the three things ``services/genesis/signals.py`` could not settle offline?

``signals.py`` is the SCATS half of roadmap 3.1 Genesis (b). Its module
docstring declares, in writing, an assumption it could NOT measure and the
emitter carries two more choices no offline reader can confirm. This script is
the named acceptance follow-up of ``4f38e39`` and settles exactly three
questions. **It fixes nothing** — if the emission is wrong, the finding names
the fix and stops there.

  S6  Does a Vissim detector's ``pos`` anchor the UPSTREAM or the DOWNSTREAM
      end of its 4.5 m body? ``signals.py`` ASSUMPTION S6 chose UPSTREAM, so it
      writes ``pos = stop line − 2.0 − 4.5``; the other reading would be
      ``pos = stop line − 2.0``, a 4.5 m difference. Three independent
      measurements, in increasing order of authority:

        S6-a (offline census) every PTV-authored detector in the Examples Demo
             library, checked against its own link's polyline length: how many
             have ``pos + length > link length`` (upstream anchoring would be
             geometrically impossible) versus ``pos − length < 0`` (downstream
             anchoring impossible)?
        S6-b (saved file)     the emitted detectors, round-tripped through
             Vissim's own ``SaveNetAs``: is ``pos`` rewritten, and does Vissim
             add any end-anchor attribute of its own?
        S6-c (live COM)       detectors ADDED via COM on the loaded network at
             deliberately boundary-breaking positions — one that only fits
             under the upstream reading, one that only fits under the
             downstream reading — with ``Pos``/``Length`` read straight back,
             plus a dump of the detector's whole COM attribute table so any
             ``PosEnd``-shaped attribute Vissim exposes is found rather than
             assumed absent.

  Q2  Is ``begin="0"`` accepted at the cycle boundary? Signal group 1 of the
      A.2 composite starts green at offset 0, so the emitted Vissig command is
      ``<cmd display="3" begin="0" />``. Does Vissim load it, does COM read the
      green window back as 0 → 38 s, and does its own save keep the ``0`` or
      rewrite it (to the cycle time, to 1, or by dropping the command)?

  Q3  Does ``redAmber="0"`` render as NO red/amber stage? ASSUMPTION S2 writes
      a zero red/amber because Australian practice has no such interval, while
      Vissim's own left-hand-traffic reference writes 2 s. Does Vissim keep the
      zero on the ``<signalGroup>`` and in the blob's ``<fixedstate
      display="2" duration="0" />``, or substitute a default?

Everything else recorded is in service of those three: the Vissim version, the
``.err`` Vissim wrote (or did not), a COM census of controllers / signal groups
/ heads / detectors with ONE group's red/green/amber values read back and
compared against the plan's own numbers, and a container-level round-trip table
(emitted vs saved) so any attribute Vissim added, dropped or rewrote inside
``signalControllers`` / ``signalHeads`` / ``detectors`` is visible.

THE NETWORK UNDER TEST
----------------------
The synthetic 4-way crossroads, regenerated end to end through the real
pipeline — never hand-assembled:

    crossroads .osm  (reproduced inline; compared against the suite's own
                      ``test_genesis_osm_draft.crossroads_osm`` when that module
                      is importable, so a fixture drift cannot make this probe
                      measure a different network than the suite does)
      -> services.genesis.osm.parse_osm_file
      -> services.genesis.draft.build_draft            8 links / 12 connectors
      -> services.dataimport.fixed_time.convert_to_fixed_time  on 1.6's own A.2
         composite history (cycle 78 s, greens 35/21/6, offsets 0/41/67 — the
         same PhaseData the suite's ``a2_plan`` fixture builds)
      -> services.genesis.signals.generate_signals     4 groups / 6 heads / 6
         detectors, with the explicit approach->phase mapping below
      -> services.genesis.emit.emit_inpx(draft, path, signals)

The approach->phase mapping is the ONE fact ``signals.py`` refuses to guess, so
this probe supplies it explicitly and by a stated rule: the two Main St
approaches (both directions of OSM way 101) run together on phase A, and the
two First Ave approaches take B and C — the module docstring's own worked
example ``{1: A, 3: A, 5: B, 7: C}`` transposed onto this draft's real link ids
``{1: A, 4: A, 5: B, 8: C}``. It is a placeholder layout, exactly as
``signals_group_layout_is_per_approach`` says on every run; nothing here claims
it is §5.4.2.1 compliance.

The UNSIGNALISED emission of the same draft is written alongside it as the
additivity control — that file is the one ``genesis_open_live_check.py``
already proved loads (``small_v2``, 2026-08-06), so a refusal here is
attributable to the signal containers and nothing else.

WHAT THIS SCRIPT TOUCHES — and what it refuses to do
----------------------------------------------------
Identical envelope to ``genesis_open_live_check.py``, which is the point:

* ``connect(..., attach_to_running=False)`` ALWAYS (00-EXECUTION-GUIDE rule:
  GetActiveObject attaches to a human's open GUI and LoadNet would destroy
  their work).
* NO real dataset is opened, in place or otherwise. Every network loaded is a
  file this script wrote into ``runtime/genesis-signals-live-check/nets/``.
* LoadNet -> ``.err`` capture -> COM census -> SaveNetAs (NEW name, same work
  dir) -> S6 COM experiment -> SaveNetAs (second NEW name) -> disconnect.
  **NO simulation**: no ``RunContinuous``, no ``RunSingleStep``, ever. The only
  COM writes are the S6 probe detectors, they land on a throwaway clone, and
  they happen strictly AFTER the round-trip artefact has been saved so the
  comparison file is unperturbed.
* BOUNDED. All COM work runs in a CHILD process under a wall clock. On timeout
  the child is killed and every ``vissim.exe`` that did not exist before the
  child started is killed too — PIDs present beforehand (a human's session) are
  never touched, and the leftovers are re-counted and logged after cleanup.

IF LoadNet REFUSES
------------------
A refusal is a FINDING, not a probe failure, and it gets bisected exactly the
way the emitter-v2 probe bisected its own: signal containers are grafted onto
the KNOWN-LOADABLE unsignalised skeleton one at a time, then attribute sets are
reduced, and each variant is ``LoadNet``-ed in one session (loads only —
nothing saved, nothing edited). The ladder doubles as a direct answer to Q2 and
Q3, because two of its rungs are the signalised file with ONLY ``begin="0"``
changed and with ONLY ``redAmber="0"`` changed.

Usage (from ``backend/``, with the venv python)::

    python tools/genesis_signals_live_check.py                 # build + live
    python tools/genesis_signals_live_check.py --only build    # offline only
    python tools/genesis_signals_live_check.py --only census   # S6-a only
    python tools/genesis_signals_live_check.py --only live
    python tools/genesis_signals_live_check.py --only bisect   # if live refused
    python tools/genesis_signals_live_check.py --only all

Re-runnable and idempotent: each run recreates its own ``nets/<label>/`` from
scratch and merges results into ``state.json`` under that label. ``log.txt`` is
appended with a run header (the house tee rule: live-check evidence lands in
``runtime/``, not only on a console).
"""
from __future__ import annotations

import argparse
import base64
import json
import math
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_ROOT.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # pragma: no cover - detached stream
    pass

WORK_ROOT = REPO_ROOT / "runtime" / "genesis-signals-live-check"
NETS_ROOT = WORK_ROOT / "nets"
STATE_PATH = WORK_ROOT / "state.json"
LOG_PATH = WORK_ROOT / "log.txt"

EXAMPLES_ROOT = Path(r"C:\Users\Public\Documents\PTV Vision\PTV Vissim 2026\Examples Demo")

DEFAULT_LIVE_TIMEOUT_S = 420.0
DEFAULT_BISECT_TIMEOUT_S = 600.0

_MAX_ERR_CHARS = 20000
_MAX_FRAGMENT_CHARS = 6000

#: Vissim-object-level ids that might carry the version/product string.
_VERSION_CANDIDATES = (
    "Version",
    "VersionNumber",
    "ProductName",
    "LicenseInfo",
    "LicenceInfo",
    "ServicePack",
    "Build",
)

#: Signal-group attribute ids to read back off the live COM object. Probed
#: defensively one at a time — an id Vissim does not expose is recorded as
#: unavailable, never inferred.
_SG_ATTR_CANDIDATES = (
    "No",
    "Name",
    "Type",
    "Amber",
    "RedAmber",
    "MinGreen",
    "MaxGreen",
    "EndGreen",
    "EndRed",
    "ClearTm",
    "MinRed",
    "GreenFlsh",
    "ActuationType",
    "SigSeq",
    "DefaultSigSeq",
)

#: Detector attribute ids. The four after ``Length`` are the S6 question asked
#: directly of Vissim: does it expose the OTHER end of the body at all?
_DET_ATTR_CANDIDATES = (
    "No",
    "Name",
    "Lane",
    "Pos",
    "Length",
    "PosEnd",
    "EndPos",
    "PosTo",
    "PosFrom",
    "StartPos",
    "Pos2",
    "SC",
    "PortNo",
    "Type",
)

_LINK_LENGTH_CANDIDATES = ("Length2D", "Length3D", "Length")


# ---------------------------------------------------------------------------
# Logging / state (tee — the house rule: evidence lands in runtime/)
# ---------------------------------------------------------------------------


def _log(msg: str = "") -> None:
    print(msg, flush=True)
    try:
        WORK_ROOT.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(msg + "\n")
    except OSError:
        pass


_STATE: dict[str, Any] = {}


def _load_state() -> dict[str, Any]:
    global _STATE
    if STATE_PATH.exists():
        try:
            _STATE = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except ValueError:
            _STATE = {}
    return _STATE


def _save_state() -> None:
    WORK_ROOT.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(
        json.dumps(_STATE, ensure_ascii=False, indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# The crossroads .osm — reproduced inline, compared against the suite's own
# ---------------------------------------------------------------------------

LAT0 = -32.0700
LON0 = 115.8360

_CROSS_NODES = [
    (1, LAT0, LON0 - 0.003),
    (2, LAT0, LON0),
    (3, LAT0, LON0 + 0.003),
    (4, LAT0 - 0.002, LON0),
    (5, LAT0 + 0.002, LON0),
]


def _osm_text(
    nodes: list[tuple[int, float, float]], ways: list[tuple[int, list[int], dict]]
) -> str:
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<osm version="0.6" generator="prism-test">',
    ]
    for node_id, lat, lon in nodes:
        parts.append(f'  <node id="{node_id}" lat="{lat:.6f}" lon="{lon:.6f}"/>')
    for way_id, refs, tags in ways:
        parts.append(f'  <way id="{way_id}">')
        parts.extend(f'    <nd ref="{ref}"/>' for ref in refs)
        parts.extend(f'    <tag k="{k}" v="{v}"/>' for k, v in tags.items())
        parts.append("  </way>")
    parts.append("</osm>")
    return "\n".join(parts) + "\n"


def crossroads_osm() -> str:
    """``test_genesis_osm_draft.crossroads_osm`` — way 101 Main St (2 lanes per
    direction, tagged), way 102 First Ave (class default), way 103 footway
    (skipped). 8 links / 12 connectors / 1 node."""
    return _osm_text(
        _CROSS_NODES + [(9, LAT0 - 0.005, LON0)],
        [
            (101, [1, 2, 3], {"highway": "secondary", "name": "Main St", "lanes": "4"}),
            (102, [4, 2, 5], {"highway": "residential", "name": "First Ave"}),
            (103, [9, 4], {"highway": "footway"}),
        ],
    )


def _fixture_matches_suite() -> dict[str, Any]:
    """Best-effort: import the suite's fixture module BY PATH and compare its
    ``.osm`` text with the inline copy above."""
    test_path = BACKEND_ROOT / "tests" / "test_genesis_osm_draft.py"
    try:
        import importlib.util

        spec = importlib.util.spec_from_file_location("_genesis_signals_fixture", test_path)
        if spec is None or spec.loader is None:
            return {"compared": False, "reason": "spec_from_file_location returned None"}
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return {
            "compared": True,
            "identical": module.crossroads_osm() == crossroads_osm(),
            "source": str(test_path),
        }
    except Exception as exc:  # pragma: no cover - import-environment dependent
        return {"compared": False, "reason": f"{type(exc).__name__}: {exc}"}


# ---------------------------------------------------------------------------
# The A.2 composite plan — built by unit 1.6, never hand-written here
# ---------------------------------------------------------------------------

#: 1.6's own published composite. Asserted against the CONVERTER's output before
#: any signal is laid out, so a drift in 1.6 fails HERE rather than silently
#: changing what the live check is proving.
A2_CYCLE_S = 78.0
A2_GREENS_S = [35.0, 21.0, 6.0]
A2_OFFSETS_S = [0.0, 41.0, 67.0]
A2_INTERGREENS_S = [6.0, 5.0, 5.0]


def build_a2_plan() -> Any:
    """1.6's A.2 composite, produced by ``convert_to_fixed_time`` from the same
    10-cycle history the suite's ``a2_plan`` fixture uses.

    The totals 410/260/110 s over 10 cycles give phase averages of 41/26/11 s
    with NO rounding residual, which is what makes cycle 78 / greens 35-21-6 /
    offsets 0-41-67 come out of the converter rather than out of this file.
    """
    from services.dataimport.fixed_time import convert_to_fixed_time
    from services.dataimport.schema import (
        PhaseData,
        ProgramSourceKind,
        Provenance,
        SignalControllerData,
        SignalProgramData,
    )

    prov = Provenance(source_file="scats-history.csv", locator="row 1")

    def phase(phase_id, total_s, seq, min_green_s, all_red_s, stretch=False):
        return PhaseData(
            provenance=prov,
            phase_id=phase_id,
            sequence_index=seq,
            amber_s=3.0,
            all_red_s=all_red_s,
            is_stretch_phase=stretch,
            observation_count=10,
            observed_total_duration_s=total_s,
            min_green_s=min_green_s,
        )

    program = SignalProgramData(
        provenance=prov,
        program_id="P1",
        source_kind=ProgramSourceKind.OBSERVED_HISTORY,
        observed_cycle_count=10,
        phases=[
            phase("A", 410.0, 0, 10.0, 3.0, stretch=True),
            phase("B", 260.0, 1, 8.0, 2.0),
            phase("C", 110.0, 2, 5.0, 2.0),
        ],
    )
    return convert_to_fixed_time(
        SignalControllerData(provenance=prov, controller_id="TCS999", programs=[program])
    )


def _approach_phase_mapping(approaches: list[Any]) -> tuple[dict[int, str], dict[str, Any]]:
    """approach link id -> plan phase id, by a STATED rule rather than a guess.

    The rule: group the approaches by the OSM way they came from, take the ways
    in ascending id, and give the FIRST way's approaches phase A together (they
    are the two directions of one street, so they oppose each other and share
    the main phase — the module docstring's own "a standard crossroads: two
    opposing approaches share the main phase"); the remaining approaches take
    the remaining phases in order. This reproduces the docstring's worked
    example ``{1: A, 3: A, 5: B, 7: C}`` on this draft's real link ids.

    It is a PLACEHOLDER, exactly as ``signals_group_layout_is_per_approach``
    declares on every run; §5.4.2.1 wants the site LM plan and SCATS numbering,
    which a generated draft does not have.
    """
    by_way: dict[int, list[Any]] = {}
    for link in approaches:
        by_way.setdefault(link.osm_way_id, []).append(link)
    ways = sorted(by_way)
    mapping: dict[int, str] = {}
    phases = ["A", "B", "C"]
    if ways:
        for link in by_way[ways[0]]:
            mapping[link.id] = "A"
    remaining = [link for way in ways[1:] for link in by_way[way]]
    for index, link in enumerate(remaining):
        mapping[link.id] = phases[min(index + 1, len(phases) - 1)]
    explain = {
        "rule": (
            "approaches grouped by OSM way; the lowest-numbered way's two "
            "directions share phase A, the rest take B, C in link-id order"
        ),
        "ways": {str(way): [link.id for link in by_way[way]] for way in ways},
        "mapping": {str(k): v for k, v in sorted(mapping.items())},
    }
    return mapping, explain


# ---------------------------------------------------------------------------
# Offline build of the network under test
# ---------------------------------------------------------------------------


def build_signalised_network(target_dir: Path) -> dict[str, Any]:
    """Regenerate the crossroads draft + its signals through
    ``services.genesis`` into ``target_dir``.

    Writes THREE artefacts: the ``.osm``, the SIGNALISED ``.inpx`` (the network
    under test) and the UNSIGNALISED ``.inpx`` of the identical draft (the
    additivity control, and the file already proven to load).
    """
    from services.genesis import signals as signals_module
    from services.genesis.draft import build_draft
    from services.genesis.emit import emit_inpx
    from services.genesis.osm import parse_osm_file

    osm_path = target_dir / "cross.osm"
    osm_path.write_text(crossroads_osm(), encoding="utf-8", newline="")
    draft = build_draft(parse_osm_file(osm_path), name="cross")
    if draft.refused:
        raise RuntimeError(f"crossroads draft was refused: {draft.refusals}")

    plan = build_a2_plan()
    plan_check = {
        "ok": bool(plan.ok),
        "cycle_s": plan.plan_cycle_s,
        "greens_s": [row.green_s for row in plan.rows],
        "offsets_s": [row.start_offset_s for row in plan.rows],
        "intergreens_s": [row.intergreen_s for row in plan.rows],
        "round_up_residual_s": plan.round_up_residual_s,
    }
    plan_check["is_the_published_a2_composite"] = (
        plan_check["ok"]
        and plan_check["cycle_s"] == A2_CYCLE_S
        and plan_check["greens_s"] == A2_GREENS_S
        and plan_check["offsets_s"] == A2_OFFSETS_S
        and plan_check["intergreens_s"] == A2_INTERGREENS_S
    )

    node = draft.nodes[0]
    approaches = signals_module.node_approaches(draft, node)
    mapping, mapping_explain = _approach_phase_mapping(approaches)
    signals = signals_module.generate_signals(draft, {node.id: (plan, mapping)})

    signalised = emit_inpx(draft, target_dir / "cross_signalised.inpx", signals)
    unsignalised = emit_inpx(draft, target_dir / "cross_unsignalised.inpx")

    node_signals = signals.nodes[0] if signals.nodes else None
    expected: dict[str, Any] = {
        "controller_no": node_signals.controller_no if node_signals else None,
        "cycle_s": node_signals.cycle_s if node_signals else None,
        "groups": [
            {
                "no": g.no,
                "approach_link_id": g.approach_link_id,
                "phase_id": g.phase_id,
                "green_start_s": g.green_start_s,
                "green_s": g.green_s,
                "green_end_s": g.green_end_s,
                "red_start_s": g.red_start_s,
                "amber_s": g.amber_s,
                "all_red_s": g.all_red_s,
                "red_amber_s": g.red_amber_s,
                "min_green_s": g.min_green_s,
            }
            for g in (node_signals.groups if node_signals else ())
        ],
        "heads": [
            {"no": h.no, "link": h.link_id, "lane": h.lane, "pos_m": h.pos_m, "sg": h.group_no}
            for h in (node_signals.heads if node_signals else ())
        ],
        "detectors": [
            {
                "no": d.no,
                "link": d.link_id,
                "lane": d.lane,
                "pos_m": d.pos_m,
                "length_m": d.length_m,
                "port_no": d.port_no,
                "head_no": d.head_no,
            }
            for d in (node_signals.detectors if node_signals else ())
        ],
    }
    (target_dir / "expected.json").write_text(
        json.dumps(expected, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
    )

    approach_rows = [
        {
            "link_id": link.id,
            "name": link.name,
            "osm_way_id": link.osm_way_id,
            "lanes": link.lanes,
            "length_m": round(link.length_m, 3),
            "phase_id": mapping.get(link.id),
        }
        for link in approaches
    ]

    return {
        "osm": str(osm_path),
        "signalised_inpx": str(signalised),
        "unsignalised_inpx": str(unsignalised),
        "signalised_bytes": signalised.stat().st_size,
        "unsignalised_bytes": unsignalised.stat().st_size,
        "draft": {
            "links": len(draft.links),
            "connectors": len(draft.connectors),
            "nodes": len(draft.nodes),
        },
        "fixture_check": _fixture_matches_suite(),
        "plan_check": plan_check,
        "approaches": approach_rows,
        "approach_phase_mapping": mapping_explain,
        "signals_ok": bool(signals.ok),
        "signals_issue_codes": sorted({issue.code for issue in signals.issues}),
        "signals_refusals": [issue.code for issue in signals.issues if issue.severity.name == "REFUSE"],
        "expected": expected,
        "counts": {
            "groups": len(expected["groups"]),
            "heads": len(expected["heads"]),
            "detectors": len(expected["detectors"]),
        },
    }


# ---------------------------------------------------------------------------
# XML helpers (offline analysis; no COM)
# ---------------------------------------------------------------------------


def _strip_ns(root: ET.Element) -> ET.Element:
    for el in root.iter():
        if isinstance(el.tag, str) and "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]
        for key in list(el.attrib):
            if "}" in key:
                el.attrib[key.split("}", 1)[1]] = el.attrib.pop(key)
    return root


def _parse(path: Path) -> ET.Element:
    return _strip_ns(ET.parse(path).getroot())


def _decode_blob(b64: str | None) -> dict[str, Any]:
    """Decode a controller's ``intSupplyData`` into the Vissig document, and
    read the ONE program's commands and fixed states back out of it.

    This is the same shape ``services.signal_parser`` reads, deliberately
    re-read here from the FILE's own bytes: what the probe is measuring is what
    Vissim wrote, so going through a reader of ours would be evidence about us.
    """
    if not b64:
        return {"present": False}
    out: dict[str, Any] = {"present": True, "b64_len": len(b64)}
    try:
        text = base64.b64decode(b64).decode("utf-8")
    except Exception as exc:
        out["error"] = f"{type(exc).__name__}: {exc}"
        return out
    out["text"] = text if len(text) <= _MAX_FRAGMENT_CHARS else text[:_MAX_FRAGMENT_CHARS] + "\n... [truncated]"
    out["text_len"] = len(text)
    out["has_crlf"] = "\r\n" in text
    try:
        inner = ET.fromstring(text)
    except Exception as exc:
        out["parse_error"] = f"{type(exc).__name__}: {exc}"
        return out
    out["sc_attrs"] = dict(inner.attrib)
    out["child_tags"] = [c.tag for c in inner]
    decl: dict[str, Any] = {}
    for sg in inner.findall("./sgs/sg"):
        decl[sg.get("id") or "?"] = {
            "attrs": dict(sg.attrib),
            "default_durations": [dict(d.attrib) for d in sg.findall("./defaultDurations/defaultDuration")],
        }
    out["sg_declarations"] = decl
    progs = []
    for prog in inner.findall("./progs/prog"):
        groups: dict[str, Any] = {}
        for sg in prog.findall("./sgs/sg"):
            groups[sg.get("sg_id") or "?"] = {
                "attrs": dict(sg.attrib),
                "cmds": [dict(c.attrib) for c in sg.findall("./cmds/cmd")],
                "fixedstates": [dict(f.attrib) for f in sg.findall("./fixedstates/fixedstate")],
            }
        progs.append({"attrs": dict(prog.attrib), "groups": groups})
    out["progs"] = progs
    return out


def signal_index(root: ET.Element) -> dict[str, Any]:
    """Everything the three signal containers hold, keyed by ``no``."""
    controllers: dict[str, Any] = {}
    for sc in root.findall("./signalControllers/signalController"):
        no = sc.get("no") or "?"
        controllers[no] = {
            "attrs": {k: v for k, v in sc.attrib.items() if k != "intSupplyData"},
            "child_tags": [c.tag for c in sc if isinstance(c.tag, str)],
            "groups": {
                (sg.get("no") or "?"): dict(sg.attrib) for sg in sc.findall("./sgs/signalGroup")
            },
            "sig_tms_tab_rows": [
                dict(e.attrib)
                for e in sc.findall("./sigTmsTabConfig/signalOutputConfigurationElement")
            ],
            "ramp_meter_para": [dict(e.attrib) for e in sc.findall("./rampMeterPara")],
            "blob": _decode_blob(sc.get("intSupplyData")),
        }
    heads = {(h.get("no") or "?"): dict(h.attrib) for h in root.findall("./signalHeads/signalHead")}
    detectors = {(d.get("no") or "?"): dict(d.attrib) for d in root.findall("./detectors/detector")}
    return {"controllers": controllers, "heads": heads, "detectors": detectors}


def _diff_attrs(emitted: dict[str, str], saved: dict[str, str]) -> dict[str, Any]:
    added = sorted(set(saved) - set(emitted))
    dropped = sorted(set(emitted) - set(saved))
    changed = {
        key: {"emitted": emitted[key], "saved": saved[key]}
        for key in sorted(set(emitted) & set(saved))
        if emitted[key] != saved[key]
    }
    return {"added": added, "dropped": dropped, "changed": changed}


def _numeric_equal(a: str | None, b: str | None) -> bool:
    """``"93.5"`` and ``"93.500"`` are the same position. A change of FORMAT is
    not a change of VALUE, and the round-trip table must not report one as the
    other."""
    if a is None or b is None:
        return a == b
    if a == b:
        return True
    try:
        return math.isclose(float(a), float(b), rel_tol=0.0, abs_tol=1e-6)
    except (TypeError, ValueError):
        return False


def _container_table(emitted: dict[str, Any], saved: dict[str, Any]) -> dict[str, Any]:
    """The round-trip container table: what survived, what Vissim rewrote."""
    rows: dict[str, Any] = {}
    for family in ("controllers", "heads", "detectors"):
        em = emitted[family]
        sv = saved[family]
        row: dict[str, Any] = {
            "emitted_count": len(em),
            "saved_count": len(sv),
            "ids_missing_in_saved": sorted(set(em) - set(sv)),
            "ids_added_in_saved": sorted(set(sv) - set(em)),
        }
        per_object: dict[str, Any] = {}
        for key in sorted(set(em) & set(sv)):
            if family == "controllers":
                diff = _diff_attrs(em[key]["attrs"], sv[key]["attrs"])
            else:
                diff = _diff_attrs(em[key], sv[key])
            # A pure re-format ("93.500" -> "93.5") is separated from a real
            # value change, because only the second is a finding.
            reformat = {
                k: v for k, v in diff["changed"].items() if _numeric_equal(v["emitted"], v["saved"])
            }
            real = {k: v for k, v in diff["changed"].items() if k not in reformat}
            entry = {
                "attrs_added_by_vissim": diff["added"],
                "attrs_dropped_by_vissim": diff["dropped"],
                "values_rewritten": real,
                "reformatted_only": reformat,
            }
            if family == "controllers":
                entry["group_ids_emitted"] = sorted(em[key]["groups"])
                entry["group_ids_saved"] = sorted(sv[key]["groups"])
                entry["group_attr_diffs"] = {
                    gid: _diff_attrs(em[key]["groups"][gid], sv[key]["groups"][gid])
                    for gid in sorted(set(em[key]["groups"]) & set(sv[key]["groups"]))
                }
                entry["child_tags_emitted"] = em[key]["child_tags"]
                entry["child_tags_saved"] = sv[key]["child_tags"]
            per_object[key] = entry
        row["per_object"] = per_object
        rows[family] = row
    return rows


# ---------------------------------------------------------------------------
# The three verdicts, read off the emitted/saved pair
# ---------------------------------------------------------------------------


def _blob_program_groups(index_entry: dict[str, Any]) -> dict[str, Any]:
    progs = (index_entry.get("blob") or {}).get("progs") or []
    return progs[0]["groups"] if progs else {}


def verdict_begin_zero(emitted: dict[str, Any], saved: dict[str, Any]) -> dict[str, Any]:
    """Q2: is ``begin="0"`` accepted at the cycle boundary?"""
    out: dict[str, Any] = {"question": 'Is begin="0" accepted at the cycle boundary?'}
    findings = []
    for sc_no in sorted(set(emitted["controllers"]) & set(saved["controllers"])):
        em_groups = _blob_program_groups(emitted["controllers"][sc_no])
        sv_groups = _blob_program_groups(saved["controllers"][sc_no])
        for sg_id in sorted(set(em_groups)):
            em_cmds = em_groups[sg_id]["cmds"]
            zero_greens = [
                c for c in em_cmds if c.get("display") == "3" and c.get("begin") in ("0", "0.0")
            ]
            if not zero_greens:
                continue
            sv = sv_groups.get(sg_id)
            findings.append(
                {
                    "controller": sc_no,
                    "signal_group": sg_id,
                    "emitted_cmds": em_cmds,
                    "saved_cmds": sv["cmds"] if sv else None,
                    "survived_verbatim": bool(sv and sv["cmds"] == em_cmds),
                    "green_begin_saved": (
                        next(
                            (c.get("begin") for c in sv["cmds"] if c.get("display") == "3"), None
                        )
                        if sv
                        else None
                    ),
                }
            )
    out["groups_with_a_zero_green_begin"] = findings
    if not findings:
        out["verdict"] = "NOT EXERCISED: no emitted group starts green at begin=0"
    elif all(f["green_begin_saved"] in ("0", "0.0") for f in findings):
        out["verdict"] = (
            'ACCEPTED: every begin="0" green command came back out of Vissim\'s own save '
            "as 0 — unchanged, not dropped, not shifted to the cycle time"
        )
    else:
        out["verdict"] = (
            'REWRITTEN: Vissim did not keep begin="0" — see green_begin_saved per group'
        )
    return out


def verdict_red_amber_zero(emitted: dict[str, Any], saved: dict[str, Any]) -> dict[str, Any]:
    """Q3: does ``redAmber="0"`` render as no red/amber stage?"""
    out: dict[str, Any] = {"question": 'Does redAmber="0" render as no red/amber stage?'}
    rows = []
    for sc_no in sorted(set(emitted["controllers"]) & set(saved["controllers"])):
        em_sc = emitted["controllers"][sc_no]
        sv_sc = saved["controllers"][sc_no]
        em_groups = _blob_program_groups(em_sc)
        sv_groups = _blob_program_groups(sv_sc)
        for sg_id in sorted(set(em_sc["groups"]) & set(sv_sc["groups"])):
            em_fixed = em_groups.get(sg_id, {}).get("fixedstates", [])
            sv_fixed = sv_groups.get(sg_id, {}).get("fixedstates", [])
            rows.append(
                {
                    "controller": sc_no,
                    "signal_group": sg_id,
                    "emitted_redAmber_attr": em_sc["groups"][sg_id].get("redAmber"),
                    "saved_redAmber_attr": sv_sc["groups"][sg_id].get("redAmber"),
                    "emitted_amber_attr": em_sc["groups"][sg_id].get("amber"),
                    "saved_amber_attr": sv_sc["groups"][sg_id].get("amber"),
                    "emitted_fixedstates": em_fixed,
                    "saved_fixedstates": sv_fixed,
                    "emitted_default_durations": (em_sc["blob"].get("sg_declarations") or {})
                    .get(sg_id, {})
                    .get("default_durations"),
                    "saved_default_durations": (sv_sc["blob"].get("sg_declarations") or {})
                    .get(sg_id, {})
                    .get("default_durations"),
                }
            )
    out["groups"] = rows
    kept_attr = all(_numeric_equal(r["emitted_redAmber_attr"], r["saved_redAmber_attr"]) for r in rows)
    kept_state = all(r["emitted_fixedstates"] == r["saved_fixedstates"] for r in rows)
    if not rows:
        out["verdict"] = "NOT EXERCISED: no signal group survived to compare"
    elif kept_attr and kept_state:
        out["verdict"] = (
            'KEPT AS ZERO: Vissim preserved redAmber="0" on every signalGroup and kept the '
            'blob\'s <fixedstate display="2" duration="0"/> — no default was substituted'
        )
    else:
        out["verdict"] = (
            "SUBSTITUTED: Vissim changed the red/amber value or its fixed state — see the "
            "per-group rows"
        )
    return out


def verdict_detector_anchor_from_save(
    emitted: dict[str, Any], saved: dict[str, Any]
) -> dict[str, Any]:
    """S6-b: what the saved file alone can say about the detector anchor."""
    out: dict[str, Any] = {
        "question": "S6: does a detector's pos anchor the UPSTREAM or DOWNSTREAM end?",
        "sub_question": "S6-b — what Vissim's own SaveNetAs did to the emitted detectors",
    }
    rows = []
    for no in sorted(set(emitted["detectors"]) & set(saved["detectors"])):
        em = emitted["detectors"][no]
        sv = saved["detectors"][no]
        rows.append(
            {
                "no": no,
                "emitted_pos": em.get("pos"),
                "saved_pos": sv.get("pos"),
                "emitted_length": em.get("length"),
                "saved_length": sv.get("length"),
                "pos_unchanged": _numeric_equal(em.get("pos"), sv.get("pos")),
                "length_unchanged": _numeric_equal(em.get("length"), sv.get("length")),
                "attrs_added_by_vissim": sorted(set(sv) - set(em)),
            }
        )
    out["detectors"] = rows
    end_anchor_attrs = sorted(
        {
            key
            for sv in saved["detectors"].values()
            for key in sv
            if key.lower() in {"posend", "endpos", "posto", "posfrom", "startpos", "pos2"}
        }
    )
    out["end_anchor_attributes_in_saved_file"] = end_anchor_attrs
    if not rows:
        out["verdict"] = "NOT EXERCISED: no detector survived to compare"
    elif end_anchor_attrs:
        out["verdict"] = (
            f"SAVE IS DECISIVE: Vissim wrote {end_anchor_attrs} alongside pos/length"
        )
    elif all(r["pos_unchanged"] and r["length_unchanged"] for r in rows):
        out["verdict"] = (
            "SAVE IS AMBIGUOUS (as the module predicted): Vissim stores exactly pos + length "
            "and adds no end-anchor attribute, so the file round-trips both readings "
            "identically — S6 must be settled by S6-a and S6-c"
        )
    else:
        out["verdict"] = "Vissim REWROTE a detector pos or length — see the rows"
    return out


# ---------------------------------------------------------------------------
# S6-a: the offline census over PTV's own authored networks
# ---------------------------------------------------------------------------


def _link_polyline_length_m(link_el: ET.Element) -> float | None:
    geometry = link_el.find("geometry")
    if geometry is None:
        return None
    pts_el = geometry.find("linkPolyPts")
    if pts_el is None:
        return None
    coords: list[tuple[float, float]] = []
    for point in pts_el:
        try:
            coords.append((float(point.get("x")), float(point.get("y"))))
        except (TypeError, ValueError):
            return None
    if len(coords) < 2:
        return None
    return sum(math.dist(a, b) for a, b in zip(coords, coords[1:]))


def detector_anchor_census(examples_root: Path = EXAMPLES_ROOT) -> dict[str, Any]:
    """S6-a. Every PTV-AUTHORED detector in the Examples Demo library, checked
    against the length of the link its lane sits on.

    The logic is a pair of impossibility tests, and only one of them can hold
    for a library that PTV's own engineers built and Vissim itself renders:

      * ``pos + length > link length`` -> the body would run off the END of the
        link, which is impossible if ``pos`` anchors the UPSTREAM end.
      * ``pos - length < 0``           -> the body would start BEFORE the link,
        which is impossible if ``pos`` anchors the DOWNSTREAM end.

    Offline, and evidence about VISSIM's own data rather than about this repo —
    which is exactly why it is worth having next to the live COM answer.
    """
    out: dict[str, Any] = {
        "examples_root": str(examples_root),
        "networks": [],
        "totals": {"detectors": 0, "upstream_impossible": 0, "downstream_impossible": 0},
    }
    if not examples_root.exists():
        out["error"] = f"Examples Demo library not found at {examples_root}"
        return out
    for folder in sorted(p for p in examples_root.iterdir() if p.is_dir()):
        candidates = sorted(folder.glob("*.inpx"))
        if not candidates:
            continue
        path = candidates[0]
        try:
            root = _parse(path)
        except Exception as exc:
            out["networks"].append({"network": folder.name, "error": f"{type(exc).__name__}: {exc}"})
            continue
        lengths = {
            (le.get("no") or "?"): _link_polyline_length_m(le) for le in root.iter("link")
        }
        up_impossible: list[dict[str, Any]] = []
        down_impossible: list[dict[str, Any]] = []
        resolved = 0
        for det in root.iter("detector"):
            link_no = (det.get("lane") or "").split(" ")[0]
            try:
                pos = float(det.get("pos"))
                length = float(det.get("length"))
            except (TypeError, ValueError):
                continue
            link_len = lengths.get(link_no)
            if link_len is None:
                continue
            resolved += 1
            row = {
                "detector": det.get("no"),
                "link": link_no,
                "pos": pos,
                "length": length,
                "link_length_m": round(link_len, 3),
            }
            if pos + length > link_len + 1e-6:
                up_impossible.append(row)
            if pos - length < -1e-6:
                down_impossible.append(row)
        if not resolved:
            continue
        out["networks"].append(
            {
                "network": folder.name,
                "file": path.name,
                "detectors_resolved": resolved,
                "upstream_anchor_impossible": len(up_impossible),
                "downstream_anchor_impossible": len(down_impossible),
                "upstream_examples": up_impossible[:3],
                "downstream_examples": down_impossible[:3],
            }
        )
        out["totals"]["detectors"] += resolved
        out["totals"]["upstream_impossible"] += len(up_impossible)
        out["totals"]["downstream_impossible"] += len(down_impossible)

    up = out["totals"]["upstream_impossible"]
    down = out["totals"]["downstream_impossible"]
    if out["totals"]["detectors"] == 0:
        out["verdict"] = "NO DATA: no PTV detector could be resolved onto a link"
    elif up == 0 and down > 0:
        out["verdict"] = (
            f"UPSTREAM. Of {out['totals']['detectors']} PTV-authored detectors, {down} would "
            "run off the START of their link under the DOWNSTREAM reading and ZERO would run "
            "off the END under the UPSTREAM reading — only the upstream anchor is consistent "
            "with PTV's own library, which is what ASSUMPTION S6 chose"
        )
    elif down == 0 and up > 0:
        out["verdict"] = (
            f"DOWNSTREAM. {up} of {out['totals']['detectors']} detectors would run off the END "
            "of their link under the UPSTREAM reading and none off the start — ASSUMPTION S6 "
            "is WRONG and pos = head - setback is the correct emission"
        )
    else:
        out["verdict"] = (
            f"INCONCLUSIVE: {up} upstream-impossible and {down} downstream-impossible "
            "detectors — neither reading is excluded by the library"
        )
    return out


# ---------------------------------------------------------------------------
# CHILD process: the only code in this file that touches COM
# ---------------------------------------------------------------------------


def _snapshot_err_paths(directory: Path) -> set[str]:
    try:
        return {p.name for p in directory.glob("*.err")}
    except OSError:
        return set()


def _read_err(directory: Path, pre_existing: set[str]) -> dict[str, Any]:
    """Every ``.err`` in ``directory``, flagged new-since-snapshot, verbatim."""
    out: dict[str, Any] = {"files": [], "new_files": []}
    try:
        paths = sorted(directory.glob("*.err"))
    except OSError as exc:
        out["error"] = str(exc)
        return out
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            text = f"[could not read: {exc}]"
        if len(text) > _MAX_ERR_CHARS:
            text = text[:_MAX_ERR_CHARS] + "\n... [truncated]"
        out["files"].append({"name": path.name, "bytes": path.stat().st_size, "text": text})
        if path.name not in pre_existing:
            out["new_files"].append(path.name)
    return out


def _read_version(vissim: Any) -> dict[str, Any]:
    info: dict[str, Any] = {"att_values": {}, "python_attrs": {}}
    for att in _VERSION_CANDIDATES:
        try:
            info["att_values"][att] = str(vissim.AttValue(att))
        except Exception as exc:
            info["att_values"][att] = f"<unavailable: {type(exc).__name__}>"
    for name in ("Version", "VersionNumber", "ProductName"):
        try:
            info["python_attrs"][name] = str(getattr(vissim, name))
        except Exception as exc:
            info["python_attrs"][name] = f"<unavailable: {type(exc).__name__}>"
    return info


def _attribute_table(obj: Any, collection: Any = None, cap: int = 400) -> dict[str, Any]:
    """The REAL attribute table via the COM ``Attributes`` collection — ids from
    Vissim itself, never guessed. This is how the S6 question "does Vissim
    expose an end-anchor attribute at all?" gets an answer that is not an
    inference from a candidate list.

    MEASURED 2026-08-07: an ITEM wrapper (``IDetector``, ``ISignalGroup``) has
    no ``Attributes`` property at all — the table hangs off the CONTAINER
    (``Net.Detectors.Attributes``). Both are tried, in that order, and which one
    answered is recorded rather than assumed."""
    out: dict[str, Any] = {}
    attrs = None
    tried: list[str] = []
    for source, name in ((obj, "item"), (collection, "collection")):
        if source is None:
            continue
        try:
            attrs = source.Attributes
            out["read_via"] = name
            break
        except Exception as exc:
            tried.append(f"{name}: {type(exc).__name__}: {exc}")
    if attrs is None:
        out["error"] = "no Attributes collection — " + " | ".join(tried)
        return out
    items = None
    for use_getall in (True, False):
        try:
            items = list(attrs.GetAll()) if use_getall else list(attrs)
            break
        except Exception:
            continue
    if items is None:
        out["error"] = "Attributes collection is not iterable"
        return out
    codes: list[str] = []
    rows: list[dict[str, Any]] = []
    for item in items[:cap]:
        row: dict[str, Any] = {}
        for prop in ("Code", "Name", "Editable"):
            try:
                row[prop] = str(getattr(item, prop))
            except Exception:
                row[prop] = "?"
        codes.append(row["Code"])
        rows.append(row)
    out["count"] = len(items)
    out["codes"] = codes
    out["rows"] = rows
    return out


def _probe_att_values(obj: Any, candidates: tuple[str, ...]) -> dict[str, Any]:
    found: dict[str, Any] = {}
    for attr_id in candidates:
        try:
            found[attr_id] = repr(obj.AttValue(attr_id))
        except Exception as exc:
            found[attr_id] = f"<unavailable: {type(exc).__name__}>"
    return found


def _signal_census(controller: Any) -> dict[str, Any]:
    """Counts, plus every signal group's red/green/amber read back off COM.

    The green window is read the way the repo's own reader reads it — through
    ``VissimController.get_signal_green_split``, which decodes the controller's
    live ``IntSupplyData`` blob — so the number compared against the plan is the
    number the product would use, not one this probe computed.
    """
    out: dict[str, Any] = {}
    vissim = controller.vissim
    for name, accessor in (
        ("signal_controllers", "SignalControllers"),
        ("signal_heads", "SignalHeads"),
        ("detectors", "Detectors"),
        ("links", "Links"),
        ("nodes", "Nodes"),
    ):
        try:
            out[f"{name}_count"] = int(getattr(vissim.Net, accessor).Count)
        except Exception as exc:
            out[f"{name}_count"] = f"<unavailable: {type(exc).__name__}>"

    controllers: list[dict[str, Any]] = []
    try:
        sc_nos = controller.available_signal_controller_nos()
    except Exception as exc:
        out["error"] = f"could not list signal controllers: {type(exc).__name__}: {exc}"
        return out
    out["signal_controller_nos"] = sc_nos

    attr_table_done = False
    for sc_no in sc_nos:
        entry: dict[str, Any] = {"no": sc_no}
        try:
            entry["header"] = controller.get_signal_controller(sc_no)
        except Exception as exc:
            entry["header_error"] = f"{type(exc).__name__}: {exc}"
        try:
            sc_obj = controller.vissim.Net.SignalControllers.ItemByKey(sc_no)
        except Exception as exc:
            sc_obj = None
            entry["object_error"] = f"{type(exc).__name__}: {exc}"
        groups: list[dict[str, Any]] = []
        for sg_no in [int(n) for n in (entry.get("header", {}).get("signal_group_nos") or [])]:
            row: dict[str, Any] = {"sg": sg_no}
            try:
                row["green_split"] = controller.get_signal_green_split(sc_no, sg_no)
            except Exception as exc:
                row["green_split_error"] = f"{type(exc).__name__}: {exc}"
            if sc_obj is not None:
                try:
                    sg_obj = controller._signal_group_by_key(sc_obj, sc_no, sg_no)
                    row["att_values"] = _probe_att_values(sg_obj, _SG_ATTR_CANDIDATES)
                    if not attr_table_done:
                        out["signal_group_attribute_table"] = _attribute_table(
                            sg_obj, getattr(sc_obj, "SGs", None)
                        )
                        attr_table_done = True
                except Exception as exc:
                    row["sg_object_error"] = f"{type(exc).__name__}: {exc}"
            groups.append(row)
        entry["groups"] = groups
        controllers.append(entry)
    out["controllers"] = controllers

    heads: list[dict[str, Any]] = []
    try:
        for no in controller.available_signal_head_nos():
            try:
                heads.append(controller.get_signal_head(no))
            except Exception as exc:
                heads.append({"no": no, "error": f"{type(exc).__name__}: {exc}"})
    except Exception as exc:
        out["signal_head_error"] = f"{type(exc).__name__}: {exc}"
    out["signal_heads"] = heads

    detectors: list[dict[str, Any]] = []
    try:
        det_nos = controller.available_detector_nos()
        for no in det_nos:
            try:
                detectors.append(controller.get_detector(no))
            except Exception as exc:
                detectors.append({"no": no, "error": f"{type(exc).__name__}: {exc}"})
        if det_nos:
            try:
                det_obj = controller.vissim.Net.Detectors.ItemByKey(det_nos[0])
                out["detector_attribute_table"] = _attribute_table(
                    det_obj, controller.vissim.Net.Detectors
                )
                out["detector_att_values"] = _probe_att_values(det_obj, _DET_ATTR_CANDIDATES)
            except Exception as exc:
                out["detector_attribute_table"] = {"error": f"{type(exc).__name__}: {exc}"}
    except Exception as exc:
        out["detector_error"] = f"{type(exc).__name__}: {exc}"
    out["detectors"] = detectors
    return out


def _s6_experiment(controller: Any, plan: dict[str, Any]) -> dict[str, Any]:
    """S6-c. Add detectors at boundary-breaking positions and read them back.

    The design is a pair of impossibility probes on ONE link of known length
    ``L``, plus a mid-link control:

      control  pos = L/2,   length 4.5 — fits under BOTH readings. Proves the
               add/read path itself round-trips a position unchanged, so a
               clamp in either probe below is attributable to the geometry.
      upstream-stress  pos = L - 1.0 — the body would run 3.5 m PAST the link
               end if ``pos`` anchors the upstream end.
      downstream-stress  pos = 1.0 — the body would start 3.5 m BEFORE the link
               if ``pos`` anchors the downstream end.

    Whichever probe Vissim clamps, shortens or refuses names the anchor. If it
    silently accepts both, that is itself the finding ("Vissim does not
    validate detector extent"), the house thesis restated, and the census
    (S6-a) carries the verdict.
    """
    out: dict[str, Any] = {"plan": plan, "probes": []}
    link_no = int(plan["link_no"])
    lane = int(plan["lane"])
    length_m = float(plan["length_m"])

    try:
        link_obj = controller.vissim.Net.Links.ItemByKey(link_no)
        out["link_att_values"] = _probe_att_values(link_obj, _LINK_LENGTH_CANDIDATES)
    except Exception as exc:
        out["link_att_error"] = f"{type(exc).__name__}: {exc}"

    live_length: float | None = None
    for key in _LINK_LENGTH_CANDIDATES:
        raw = (out.get("link_att_values") or {}).get(key, "")
        try:
            live_length = float(str(raw).strip("'\""))
            out["link_length_read_via"] = key
            break
        except (TypeError, ValueError):
            continue
    link_length = live_length if live_length is not None else float(plan["link_length_m"])
    out["link_length_m_used"] = link_length
    out["link_length_m_offline"] = plan["link_length_m"]

    probes = [
        ("control_mid_link", round(link_length / 2.0, 3)),
        ("upstream_stress_pos_is_L_minus_1", round(link_length - 1.0, 3)),
        ("downstream_stress_pos_is_1", 1.0),
    ]
    for label, pos in probes:
        entry: dict[str, Any] = {
            "label": label,
            "requested_pos_m": pos,
            "requested_length_m": length_m,
            "upstream_reading_end_m": round(pos + length_m, 3),
            "downstream_reading_start_m": round(pos - length_m, 3),
            "upstream_reading_fits": pos + length_m <= link_length + 1e-6,
            "downstream_reading_fits": pos - length_m >= -1e-6,
        }
        try:
            entry["readback"] = controller.add_detector(link_no, lane, pos, length_m)
            entry["added"] = True
            entry["pos_kept"] = math.isclose(
                float(entry["readback"]["pos"]), pos, rel_tol=0.0, abs_tol=1e-3
            )
            entry["length_kept"] = math.isclose(
                float(entry["readback"]["length"]), length_m, rel_tol=0.0, abs_tol=1e-3
            )
            try:
                det_obj = controller.vissim.Net.Detectors.ItemByKey(int(entry["readback"]["no"]))
                entry["att_values"] = _probe_att_values(det_obj, _DET_ATTR_CANDIDATES)
            except Exception as exc:
                entry["att_values_error"] = f"{type(exc).__name__}: {exc}"
        except Exception as exc:
            entry["added"] = False
            entry["error"] = f"{type(exc).__name__}: {exc}"
        out["probes"].append(entry)
    return out


def _com_phase(net_path: Path, label: str, out_path: Path, s6_plan_path: Path | None) -> int:
    """LoadNet -> .err -> census -> SaveNetAs -> S6 COM experiment -> SaveNetAs
    -> disconnect. Nothing else, and NO simulation. Child process only, so the
    parent can bound it with a wall clock."""
    import pythoncom

    result: dict[str, Any] = {
        "label": label,
        "network": str(net_path),
        "started_utc": _now(),
        "connected": False,
        "load_ok": None,
        "load_error": None,
        "save_ok": None,
        "save_error": None,
    }

    def flush() -> None:
        out_path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True, default=str),
            encoding="utf-8",
        )

    flush()
    pythoncom.CoInitialize()
    controller = None
    try:
        from services.vissim_com import VissimController

        controller = VissimController()
        t0 = time.time()
        # attach_to_running=False, ALWAYS: GetActiveObject would grab a human's
        # interactive session and LoadNet into it.
        controller.connect(visible=False, launch_if_missing=True, attach_to_running=False)
        result["connected"] = True
        result["prog_id"] = controller.prog_id
        result["connect_seconds"] = round(time.time() - t0, 2)
        result["version"] = _read_version(controller.vissim)
        flush()

        pre_err = _snapshot_err_paths(net_path.parent)
        result["err_files_before_load"] = sorted(pre_err)

        t0 = time.time()
        try:
            controller.load_network(str(net_path))
            result["load_ok"] = True
        except Exception as exc:
            result["load_ok"] = False
            result["load_error"] = f"{type(exc).__name__}: {exc}"
        result["load_seconds"] = round(time.time() - t0, 2)
        result["err_after_load"] = _read_err(net_path.parent, pre_err)
        flush()

        if result["load_ok"]:
            try:
                result["census"] = _signal_census(controller)
            except Exception as exc:
                result["census"] = {"error": f"{type(exc).__name__}: {exc}"}
            flush()

            saved = net_path.with_name(f"{net_path.stem}_saved.inpx")
            t0 = time.time()
            try:
                controller.save_net_as(saved)
                result["save_ok"] = True
                result["saved_path"] = str(saved)
            except Exception as exc:
                result["save_ok"] = False
                result["save_error"] = f"{type(exc).__name__}: {exc}"
            result["save_seconds"] = round(time.time() - t0, 2)
            result["err_after_save"] = _read_err(net_path.parent, pre_err)
            flush()

            # STRICTLY AFTER the round-trip artefact is on disk: the only COM
            # writes this probe makes, on a throwaway clone.
            if s6_plan_path is not None and s6_plan_path.exists() and result["save_ok"]:
                try:
                    plan = json.loads(s6_plan_path.read_text(encoding="utf-8"))
                    result["s6_experiment"] = _s6_experiment(controller, plan)
                except Exception as exc:
                    result["s6_experiment"] = {"error": f"{type(exc).__name__}: {exc}"}
                flush()
                probe_saved = net_path.with_name(f"{net_path.stem}_s6probe.inpx")
                try:
                    controller.save_net_as(probe_saved)
                    result["s6_saved_path"] = str(probe_saved)
                    result["s6_save_ok"] = True
                except Exception as exc:
                    result["s6_save_ok"] = False
                    result["s6_save_error"] = f"{type(exc).__name__}: {exc}"
                result["err_after_s6_save"] = _read_err(net_path.parent, pre_err)
        result["finished_utc"] = _now()
        flush()
        return 0
    except Exception as exc:  # infrastructure failure (COM unavailable, etc.)
        result["fatal_error"] = f"{type(exc).__name__}: {exc}"
        result["finished_utc"] = _now()
        flush()
        return 3
    finally:
        try:
            if controller is not None:
                controller.disconnect()
        except Exception:
            pass
        try:
            pythoncom.CoUninitialize()
        except Exception:
            pass
        flush()


def _com_load_many(plan_path: Path, out_path: Path) -> int:
    """The bisect: ``LoadNet`` every file named in ``plan_path`` (a JSON list of
    ``{"label", "path", "note"}``) in ONE session, recording ok/error and any
    ``.err`` per variant. Loads only — nothing saved, nothing edited."""
    import pythoncom

    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    result: dict[str, Any] = {"started_utc": _now(), "connected": False, "variants": []}

    def flush() -> None:
        out_path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True, default=str),
            encoding="utf-8",
        )

    flush()
    pythoncom.CoInitialize()
    controller = None
    try:
        from services.vissim_com import VissimController

        controller = VissimController()
        controller.connect(visible=False, launch_if_missing=True, attach_to_running=False)
        result["connected"] = True
        result["prog_id"] = controller.prog_id
        result["version"] = _read_version(controller.vissim)
        flush()

        for item in plan:
            path = Path(item["path"])
            entry: dict[str, Any] = {
                "label": item["label"],
                "path": str(path),
                "note": item.get("note"),
            }
            pre = _snapshot_err_paths(path.parent)
            t0 = time.time()
            try:
                controller.load_network(str(path))
                entry["load_ok"] = True
            except Exception as exc:
                entry["load_ok"] = False
                entry["load_error"] = f"{type(exc).__name__}: {exc}"
            entry["load_seconds"] = round(time.time() - t0, 2)
            err = _read_err(path.parent, pre)
            entry["err_new_files"] = err.get("new_files")
            entry["err_text"] = (
                "\n".join(
                    f["text"]
                    for f in err.get("files", [])
                    if f["name"] in (err.get("new_files") or [])
                )
                or None
            )
            if entry["load_ok"]:
                for name, accessor in (
                    ("link_count", "Links"),
                    ("signal_controller_count", "SignalControllers"),
                    ("signal_head_count", "SignalHeads"),
                    ("detector_count", "Detectors"),
                ):
                    try:
                        entry[name] = int(getattr(controller.vissim.Net, accessor).Count)
                    except Exception as exc:
                        entry[name] = f"<unavailable: {type(exc).__name__}>"
            result["variants"].append(entry)
            flush()
        result["finished_utc"] = _now()
        flush()
        return 0
    except Exception as exc:
        result["fatal_error"] = f"{type(exc).__name__}: {exc}"
        result["finished_utc"] = _now()
        flush()
        return 3
    finally:
        try:
            if controller is not None:
                controller.disconnect()
        except Exception:
            pass
        try:
            pythoncom.CoUninitialize()
        except Exception:
            pass
        flush()


# ---------------------------------------------------------------------------
# PARENT: process bounding + orphan-Vissim cleanup
# ---------------------------------------------------------------------------


def _vissim_pids() -> set[int]:
    """PIDs of every running process whose image name starts with "vissim".

    The task list is filtered in python rather than with ``/FI`` so this behaves
    identically under cmd, PowerShell and Git Bash (the ``/FI`` argument is
    mangled by the MSYS path converter under Git Bash)."""
    try:
        proc = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True, timeout=60
        )
    except (OSError, subprocess.SubprocessError):
        return set()
    pids: set[int] = set()
    for line in proc.stdout.splitlines():
        parts = [p.strip('"') for p in line.split('","')]
        if len(parts) < 2:
            continue
        if parts[0].strip('"').lower().startswith("vissim"):
            try:
                pids.add(int(parts[1]))
            except ValueError:
                continue
    return pids


def _kill_new_vissim(before: set[int]) -> list[int]:
    """Kill only Vissim processes that did NOT exist before we started.

    A blanket ``taskkill /IM vissim.exe`` would kill a human's open GUI; the
    whole point of ``attach_to_running=False`` is that we never touch theirs."""
    killed: list[int] = []
    for pid in sorted(_vissim_pids() - before):
        try:
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/F", "/T"], capture_output=True, timeout=60
            )
            killed.append(pid)
        except (OSError, subprocess.SubprocessError):
            continue
    return killed


def _bounded_child(cmd_tail: list[str], out_path: Path, timeout_s: float) -> dict[str, Any]:
    """Run the COM work in a child process with a wall clock, and guarantee no
    Vissim of ours outlives it."""
    if out_path.exists():
        out_path.unlink()
    before = _vissim_pids()
    _log(f"  vissim.exe PIDs already running (never touched): {sorted(before) or 'none'}")
    cmd = [sys.executable, str(Path(__file__).resolve()), *cmd_tail]
    _log(f"  child: {' '.join(cmd)}")
    _log(f"  timeout: {timeout_s:.0f}s")
    started = time.time()
    timed_out = False
    stdout = stderr = ""
    returncode: int | None = None
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout_s, cwd=str(BACKEND_ROOT)
        )
        returncode = proc.returncode
        stdout, stderr = proc.stdout, proc.stderr
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        stdout = exc.stdout.decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = exc.stderr.decode("utf-8", "replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        _log(f"  !! TIMEOUT after {timeout_s:.0f}s — killing the child and any Vissim it started")
    elapsed = round(time.time() - started, 2)

    killed = _kill_new_vissim(before)
    if killed:
        _log(f"  killed leftover Vissim PIDs (ours only): {killed}")
    leftover = sorted(_vissim_pids() - before)
    if leftover:
        _log(f"  !! WARNING: Vissim PIDs still alive after cleanup: {leftover}")
    else:
        _log("  orphan check: zero Vissim PIDs of ours left behind")

    child_result: dict[str, Any] = {}
    if out_path.exists():
        try:
            child_result = json.loads(out_path.read_text(encoding="utf-8"))
        except ValueError as exc:
            child_result = {"child_json_unreadable": str(exc)}

    return {
        "timed_out": timed_out,
        "elapsed_seconds": elapsed,
        "returncode": returncode,
        "child_stdout_tail": stdout[-4000:],
        "child_stderr_tail": stderr[-4000:],
        "vissim_pids_before": sorted(before),
        "orphan_vissim_pids_after_cleanup": leftover,
        "killed_pids": killed,
        "com": child_result,
    }


# ---------------------------------------------------------------------------
# Comparison of the COM read-back against the plan's own numbers
# ---------------------------------------------------------------------------


def compare_census_to_plan(census: dict[str, Any], expected: dict[str, Any]) -> dict[str, Any]:
    """Every group's green window as COM reports it, against the plan's own.

    ``get_signal_green_split`` reports ``green_end_s`` as the RED command's
    begin — i.e. green + amber, the split-view gap ``signals.py`` documents and
    ``test_the_split_read_path_reports_green_plus_amber`` pins. So the expected
    ``green_end_s`` here is the group's ``red_start_s``, and the plan's true
    green is recovered as ``green_end_s - green_start_s - amber``. Comparing
    against the wrong one of those two would manufacture a failure out of a
    documented naming gap."""
    out: dict[str, Any] = {"rows": [], "all_match": None}
    by_no = {int(g["no"]): g for g in expected.get("groups", [])}
    matches = True
    for entry in census.get("controllers", []):
        for group in entry.get("groups", []):
            sg = int(group["sg"])
            want = by_no.get(sg)
            split = group.get("green_split") or {}
            row: dict[str, Any] = {
                "sg": sg,
                "expected_green_start_s": want["green_start_s"] if want else None,
                "com_green_start_s": split.get("green_start_s"),
                "expected_red_start_s": want["red_start_s"] if want else None,
                "com_green_end_s": split.get("green_end_s"),
                "expected_green_s": want["green_s"] if want else None,
                "expected_amber_s": want["amber_s"] if want else None,
                "expected_red_amber_s": want["red_amber_s"] if want else None,
                "expected_min_green_s": want["min_green_s"] if want else None,
                "com_att_values": group.get("att_values"),
                "controller_type": split.get("controller_type"),
            }
            if want and split:
                recovered = None
                try:
                    recovered = round(
                        float(split["green_end_s"])
                        - float(split["green_start_s"])
                        - float(want["amber_s"]),
                        6,
                    )
                except (TypeError, ValueError, KeyError):
                    recovered = None
                row["com_recovered_green_s"] = recovered
                row["green_start_matches"] = _close(split.get("green_start_s"), want["green_start_s"])
                row["red_start_matches"] = _close(split.get("green_end_s"), want["red_start_s"])
                row["green_matches_plan"] = _close(recovered, want["green_s"])
                if not (
                    row["green_start_matches"]
                    and row["red_start_matches"]
                    and row["green_matches_plan"]
                ):
                    matches = False
            else:
                matches = False
                row["error"] = group.get("green_split_error") or "no expectation for this group"
            out["rows"].append(row)
    out["all_match"] = matches and bool(out["rows"])
    return out


def _close(a: Any, b: Any, tol: float = 1e-6) -> bool:
    try:
        return math.isclose(float(a), float(b), rel_tol=0.0, abs_tol=tol)
    except (TypeError, ValueError):
        return False


def compare_geometry_to_plan(census: dict[str, Any], expected: dict[str, Any]) -> dict[str, Any]:
    """Heads and detectors as COM resolves them, against what was emitted."""
    heads_expected = {int(h["no"]): h for h in expected.get("heads", [])}
    dets_expected = {int(d["no"]): d for d in expected.get("detectors", [])}
    head_rows = []
    for head in census.get("signal_heads", []):
        try:
            no = int(head["no"])
        except (KeyError, TypeError, ValueError):
            continue
        want = heads_expected.get(no)
        head_rows.append(
            {
                "no": no,
                "com": head,
                "expected_pos_m": want["pos_m"] if want else None,
                "pos_matches": _close(head.get("pos"), want["pos_m"], 1e-3) if want else None,
                "link_matches": (str(head.get("link")) == str(want["link"])) if want else None,
                "sg_matches": (str(head.get("sg")) == str(want["sg"])) if want else None,
            }
        )
    det_rows = []
    for det in census.get("detectors", []):
        try:
            no = int(det["no"])
        except (KeyError, TypeError, ValueError):
            continue
        want = dets_expected.get(no)
        det_rows.append(
            {
                "no": no,
                "com": det,
                "expected_pos_m": want["pos_m"] if want else None,
                "expected_length_m": want["length_m"] if want else None,
                "pos_matches": _close(det.get("pos"), want["pos_m"], 1e-3) if want else None,
                "length_matches": _close(det.get("length"), want["length_m"], 1e-3) if want else None,
            }
        )
    return {
        "heads": head_rows,
        "detectors": det_rows,
        "head_count_matches": len(head_rows) == len(heads_expected),
        "detector_count_matches": len(det_rows) == len(dets_expected),
    }


def verdict_detector_anchor_live(s6: dict[str, Any]) -> dict[str, Any]:
    """S6-c: what the live COM experiment settled."""
    out: dict[str, Any] = {
        "sub_question": "S6-c — detectors added via COM at boundary-breaking positions",
    }
    probes = {p["label"]: p for p in s6.get("probes", [])}
    out["probes"] = probes
    table = s6.get("link_att_values") or {}
    out["link_length_read_back"] = table
    end_names = {"posend", "endpos", "posto", "posfrom", "startpos", "pos2"}
    end_attrs = sorted(
        code
        for code in (s6.get("detector_attribute_codes") or [])
        if str(code).lower() in end_names
    )
    out["end_anchor_attribute_codes"] = end_attrs
    # The candidate probe is the second, independent read of the same question:
    # every end-anchor id Vissim does NOT expose comes back unavailable.
    out["end_anchor_att_value_probes"] = {
        key: value
        for key, value in (s6.get("detector_att_values") or {}).items()
        if key.lower() in end_names
    }
    out["detector_attribute_codes"] = s6.get("detector_attribute_codes")

    control = probes.get("control_mid_link") or {}
    up = probes.get("upstream_stress_pos_is_L_minus_1") or {}
    down = probes.get("downstream_stress_pos_is_1") or {}

    def _survived(p: dict[str, Any]) -> bool | None:
        if not p:
            return None
        if not p.get("added"):
            return False
        return bool(p.get("pos_kept")) and bool(p.get("length_kept"))

    out["control_round_tripped"] = _survived(control)
    out["upstream_stress_survived"] = _survived(up)
    out["downstream_stress_survived"] = _survived(down)

    # Order matters: a REFUSAL by Vissim's own geometry check is direct evidence
    # about the anchor, while an end-anchor attribute merely being present would
    # still have to be read to say which end it names. So the stress probes are
    # consulted first and the attribute table is the fallback.
    if out["control_round_tripped"] is False:
        out["verdict"] = (
            "INVALID: the mid-link control did not round-trip, so nothing can be concluded "
            "from the two stress probes"
        )
    elif out["upstream_stress_survived"] is False and out["downstream_stress_survived"]:
        out["verdict"] = (
            "UPSTREAM: the probe whose body would run past the link END was refused/clamped "
            "while the one that would start before the link START was accepted"
        )
    elif out["downstream_stress_survived"] is False and out["upstream_stress_survived"]:
        out["verdict"] = (
            "DOWNSTREAM: the probe whose body would start BEFORE the link was "
            "refused/clamped — ASSUMPTION S6 is wrong"
        )
    elif end_attrs:
        out["verdict"] = (
            f"NEITHER STRESS PROBE WAS REFUSED, but Vissim exposes {end_attrs} on a "
            "detector — read that attribute to settle the anchor"
        )
    else:
        out["verdict"] = (
            "VISSIM DOES NOT VALIDATE detector extent: both boundary-breaking probes were "
            "accepted with pos and length stored verbatim. This is the house thesis "
            "restated (Vissim fails silently) and it means the LIVE write path cannot "
            "settle S6 on its own — the PTV-library census (S6-a) is the evidence that can"
        )
    return out


# ---------------------------------------------------------------------------
# Bisect (only needed if LoadNet refuses)
# ---------------------------------------------------------------------------


def _write_tree(root: ET.Element, path: Path) -> Path:
    ET.indent(root, space="  ")
    path.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        + ET.tostring(root, encoding="unicode", xml_declaration=False)
        + "\n",
        encoding="utf-8",
        newline="",
    )
    return path


def _drop_container(root: ET.Element, tag: str) -> None:
    for child in list(root):
        if child.tag == tag:
            root.remove(child)


def build_bisect_variants(
    signalised: Path, unsignalised: Path, target_dir: Path
) -> list[dict[str, Any]]:
    """The refusal ladder: graft the signal containers onto the KNOWN-LOADABLE
    skeleton one at a time, then reduce attribute sets.

    Rungs 6 and 7 are the direct Q2/Q3 counterfactuals: identical files except
    that ``begin="0"`` becomes ``begin="1"`` (rung 6) and the zero red/amber
    becomes Vissim's own left-hand-traffic 2 s (rung 7). If either loads while
    the unmodified file does not, the refusal IS the answer to that question."""
    variants: list[dict[str, Any]] = []

    def emit(label: str, root: ET.Element, note: str) -> None:
        path = _write_tree(root, target_dir / f"{label}.inpx")
        variants.append({"label": label, "path": str(path), "note": note})

    # 1. The control: the unsignalised draft, already proven to load.
    base = _parse(unsignalised)
    emit("v1_unsignalised_control", base, "the identical draft with no signal containers")

    # 2/3/4: signal containers added back one family at a time.
    full = _parse(signalised)
    for label, drop, note in (
        (
            "v2_controllers_only",
            ("signalHeads", "detectors"),
            "signalControllers only — heads and detectors removed",
        ),
        (
            "v3_controllers_and_heads",
            ("detectors",),
            "signalControllers + signalHeads — detectors removed",
        ),
        ("v4_full_signalised", (), "the file under test, unmodified"),
    ):
        root = _parse(signalised)
        for tag in drop:
            _drop_container(root, tag)
        emit(label, root, note)

    # 5. The blob emptied: isolates the Vissig program from the element shape.
    root = _parse(signalised)
    for sc in root.findall("./signalControllers/signalController"):
        sc.set("intSupplyData", "")
    emit("v5_empty_int_supply_data", root, "every controller's Vissig blob blanked")

    # 6. Q2 counterfactual: the zero green begin moved off the cycle boundary.
    root = _parse(signalised)
    moved = 0
    for sc in root.findall("./signalControllers/signalController"):
        blob = sc.get("intSupplyData")
        if not blob:
            continue
        try:
            text = base64.b64decode(blob).decode("utf-8")
        except Exception:
            continue
        if '<cmd display="3" begin="0" />' in text:
            text = text.replace('<cmd display="3" begin="0" />', '<cmd display="3" begin="1000" />')
            moved += 1
            sc.set("intSupplyData", base64.b64encode(text.encode("utf-8")).decode("ascii"))
    emit(
        "v6_green_begin_1000_not_0",
        root,
        f'Q2 counterfactual: begin="0" -> begin="1000" on {moved} controller(s)',
    )

    # 7. Q3 counterfactual: LHT's own 2 s red/amber instead of the zero.
    root = _parse(signalised)
    for sc in root.findall("./signalControllers/signalController"):
        for sg in sc.findall("./sgs/signalGroup"):
            sg.set("redAmber", "2")
        blob = sc.get("intSupplyData")
        if not blob:
            continue
        try:
            text = base64.b64decode(blob).decode("utf-8")
        except Exception:
            continue
        text = text.replace(
            '<fixedstate display="2" duration="0" />', '<fixedstate display="2" duration="2000" />'
        ).replace(
            '<defaultDuration display="2" duration="0" />',
            '<defaultDuration display="2" duration="2000" />',
        )
        sc.set("intSupplyData", base64.b64encode(text.encode("utf-8")).decode("ascii"))
    emit("v7_red_amber_2s", root, "Q3 counterfactual: redAmber 0 -> 2 s, as Vissim's LHT writes")

    return variants


# ---------------------------------------------------------------------------
# Phases
# ---------------------------------------------------------------------------


def _fresh_dir(path: Path) -> Path:
    import shutil

    if path.exists():
        shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True, exist_ok=True)
    return path


def run_build(label: str) -> dict[str, Any]:
    _log("")
    _log("=" * 78)
    _log(f"BUILD — regenerating the signalised crossroads through services.genesis  [{label}]")
    _log("=" * 78)
    target = _fresh_dir(NETS_ROOT / label)
    built = build_signalised_network(target)
    _log(f"  osm                : {built['osm']}")
    _log(f"  fixture vs suite   : {built['fixture_check']}")
    _log(f"  draft              : {built['draft']}")
    _log(f"  A.2 plan is 1.6's  : {built['plan_check']['is_the_published_a2_composite']}  "
         f"cycle={built['plan_check']['cycle_s']} greens={built['plan_check']['greens_s']} "
         f"offsets={built['plan_check']['offsets_s']}")
    for row in built["approaches"]:
        _log(
            f"    approach link {row['link_id']:>3}  {row['name']!r}  lanes={row['lanes']}  "
            f"length={row['length_m']}  -> phase {row['phase_id']}"
        )
    _log(f"  signals ok         : {built['signals_ok']}  counts={built['counts']}")
    _log(f"  issue codes        : {built['signals_issue_codes']}")
    _log(f"  signalised   .inpx : {built['signalised_inpx']}  ({built['signalised_bytes']} bytes)")
    _log(f"  unsignalised .inpx : {built['unsignalised_inpx']}  ({built['unsignalised_bytes']} bytes)")
    _STATE.setdefault("builds", {})[label] = built
    _save_state()
    return built


def run_census() -> dict[str, Any]:
    _log("")
    _log("=" * 78)
    _log("S6-a — OFFLINE CENSUS of every PTV-authored detector in the Examples Demo library")
    _log("=" * 78)
    census = detector_anchor_census()
    for row in census.get("networks", []):
        if "error" in row:
            _log(f"  [skip] {row['network']}: {row['error']}")
            continue
        _log(
            f"  {row['network']:<40} detectors={row['detectors_resolved']:>4}  "
            f"upstream-impossible={row['upstream_anchor_impossible']:>3}  "
            f"downstream-impossible={row['downstream_anchor_impossible']:>3}"
        )
    _log(f"  TOTALS: {census.get('totals')}")
    _log(f"  VERDICT: {census.get('verdict')}")
    _STATE["s6a_census"] = census
    _save_state()
    return census


def run_live(label: str, timeout_s: float) -> dict[str, Any]:
    build = (_STATE.get("builds") or {}).get(label)
    if not build:
        build = run_build(label)
    signalised = Path(build["signalised_inpx"])
    expected = build["expected"]

    # The S6 probe link: the longest approach, so the two boundary probes are
    # unambiguous and the mid-link control is nowhere near either end.
    approach = max(build["approaches"], key=lambda row: row["length_m"])
    s6_plan = {
        "link_no": approach["link_id"],
        "lane": 1,
        "link_length_m": approach["length_m"],
        "length_m": 4.5,
        "why": (
            "§5.4.2.6's own detector length, on the longest approach so both boundary "
            "probes are unambiguous"
        ),
    }
    s6_plan_path = signalised.parent / "s6_plan.json"
    s6_plan_path.write_text(json.dumps(s6_plan, indent=2, sort_keys=True), encoding="utf-8")

    _log("")
    _log("=" * 78)
    _log(f"LIVE — LoadNet / .err / census / SaveNetAs / S6 COM probe  [{label}]")
    _log("=" * 78)
    _log(f"  network: {signalised}")
    _log(f"  S6 plan: link {s6_plan['link_no']} lane 1, link length {s6_plan['link_length_m']} m")

    out_path = signalised.parent / f"{label}_com.json"
    run = _bounded_child(
        [
            "--com-network",
            str(signalised),
            "--com-label",
            label,
            "--com-out",
            str(out_path),
            "--com-s6-plan",
            str(s6_plan_path),
        ],
        out_path,
        timeout_s,
    )
    com = run.get("com", {})
    _log(f"  connected  : {com.get('connected')}  prog_id={com.get('prog_id')}")
    _log(f"  load_ok    : {com.get('load_ok')}  ({com.get('load_seconds')}s)")
    if com.get("load_error"):
        _log(f"  load_error : {com['load_error']}")
    err = com.get("err_after_load") or {}
    _log(f"  .err new   : {err.get('new_files')}")
    for f in err.get("files", []):
        if f["name"] in (err.get("new_files") or []):
            _log(f"    --- {f['name']} ({f['bytes']} bytes) ---")
            for line in f["text"].splitlines():
                _log(f"    | {line}")
    _log(f"  save_ok    : {com.get('save_ok')}  -> {com.get('saved_path')}")

    entry: dict[str, Any] = {"label": label, "build": build, "run": run, "finished_utc": _now()}

    census = com.get("census") or {}
    if census:
        _log(
            f"  COM census : controllers={census.get('signal_controllers_count')} "
            f"heads={census.get('signal_heads_count')} detectors={census.get('detectors_count')} "
            f"links={census.get('links_count')}"
        )
        entry["timing_comparison"] = compare_census_to_plan(census, expected)
        entry["geometry_comparison"] = compare_geometry_to_plan(census, expected)
        for row in entry["timing_comparison"]["rows"]:
            _log(
                f"    SG {row['sg']}: COM green_start={row.get('com_green_start_s')} "
                f"(plan {row.get('expected_green_start_s')})  red_start={row.get('com_green_end_s')} "
                f"(plan {row.get('expected_red_start_s')})  recovered green="
                f"{row.get('com_recovered_green_s')} (plan {row.get('expected_green_s')})"
            )
        _log(f"    all timings match the plan: {entry['timing_comparison']['all_match']}")
        det_table = census.get("detector_attribute_table") or {}
        entry["detector_attribute_codes"] = det_table.get("codes")
        entry["detector_att_values"] = census.get("detector_att_values")
        _log(f"    detector COM attribute table ({det_table.get('read_via')}): "
             f"{det_table.get('codes') or det_table.get('error')}")
        _log(f"    detector COM AttValue probes: {census.get('detector_att_values')}")

    saved_path = com.get("saved_path")
    if saved_path and Path(saved_path).exists():
        emitted_index = signal_index(_parse(signalised))
        saved_index = signal_index(_parse(Path(saved_path)))
        entry["round_trip"] = {
            "emitted_path": str(signalised),
            "saved_path": saved_path,
            "emitted_bytes": signalised.stat().st_size,
            "saved_bytes": Path(saved_path).stat().st_size,
            "container_table": _container_table(emitted_index, saved_index),
            "emitted_index": emitted_index,
            "saved_index": saved_index,
        }
        entry["q2_begin_zero"] = verdict_begin_zero(emitted_index, saved_index)
        entry["q3_red_amber_zero"] = verdict_red_amber_zero(emitted_index, saved_index)
        entry["s6b_from_save"] = verdict_detector_anchor_from_save(emitted_index, saved_index)
        _log("")
        _log(f"  Q2 VERDICT : {entry['q2_begin_zero']['verdict']}")
        _log(f"  Q3 VERDICT : {entry['q3_red_amber_zero']['verdict']}")
        _log(f"  S6-b       : {entry['s6b_from_save']['verdict']}")

    s6 = com.get("s6_experiment") or {}
    if s6:
        s6 = dict(s6)
        s6["detector_attribute_codes"] = entry.get("detector_attribute_codes")
        s6["detector_att_values"] = entry.get("detector_att_values")
        entry["s6c_live"] = verdict_detector_anchor_live(s6)
        entry["s6c_raw"] = s6
        _log("")
        for probe in s6.get("probes", []):
            _log(
                f"    probe {probe['label']}: requested pos={probe['requested_pos_m']} "
                f"len={probe['requested_length_m']} | upstream-fits={probe['upstream_reading_fits']} "
                f"downstream-fits={probe['downstream_reading_fits']} | added={probe.get('added')} "
                f"readback={(probe.get('readback') or {}).get('pos')}/"
                f"{(probe.get('readback') or {}).get('length')} "
                f"error={probe.get('error')}"
            )
        _log(f"  S6-c       : {entry['s6c_live']['verdict']}")

    _log(f"  orphan Vissim PIDs after cleanup: {run.get('orphan_vissim_pids_after_cleanup')}")
    _STATE.setdefault("runs", {})[label] = entry
    _save_state()
    return entry


def run_bisect(label: str, timeout_s: float) -> dict[str, Any]:
    build = (_STATE.get("builds") or {}).get(label)
    if not build:
        build = run_build(label)
    _log("")
    _log("=" * 78)
    _log("BISECT — grafting the signal containers onto the known-loadable skeleton")
    _log("=" * 78)
    target = _fresh_dir(NETS_ROOT / f"{label}_bisect")
    variants = build_bisect_variants(
        Path(build["signalised_inpx"]), Path(build["unsignalised_inpx"]), target
    )
    plan_path = target / "plan.json"
    plan_path.write_text(json.dumps(variants, indent=2, sort_keys=True), encoding="utf-8")
    for v in variants:
        _log(f"  {v['label']:<32} {v['note']}")

    out_path = target / "bisect_com.json"
    run = _bounded_child(
        ["--com-plan", str(plan_path), "--com-out", str(out_path)], out_path, timeout_s
    )
    com = run.get("com", {})
    _log("")
    _log(f"  connected: {com.get('connected')}")
    for entry in com.get("variants", []):
        _log(
            f"  {entry['label']:<32} load_ok={entry.get('load_ok')} "
            f"links={entry.get('link_count')} scs={entry.get('signal_controller_count')} "
            f"heads={entry.get('signal_head_count')} dets={entry.get('detector_count')} "
            f"err={entry.get('err_new_files')}"
        )
        if entry.get("load_error"):
            _log(f"      error: {entry['load_error']}")
        if entry.get("err_text"):
            for line in entry["err_text"].splitlines():
                _log(f"      | {line}")
    _log(f"  orphan Vissim PIDs after cleanup: {run.get('orphan_vissim_pids_after_cleanup')}")
    result = {"variants": variants, "run": run, "finished_utc": _now()}
    _STATE.setdefault("bisects", {})[label] = result
    _save_state()
    return result


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--only",
        choices=("build", "census", "live", "bisect", "all"),
        default="all",
        help="which phase to run (default: all = build + census + live)",
    )
    parser.add_argument("--label", default="signalised", help="run label / nets subdirectory")
    parser.add_argument("--timeout", type=float, default=DEFAULT_LIVE_TIMEOUT_S)
    parser.add_argument("--bisect-timeout", type=float, default=DEFAULT_BISECT_TIMEOUT_S)
    # child-process hooks (not for interactive use)
    parser.add_argument("--com-network")
    parser.add_argument("--com-label")
    parser.add_argument("--com-out")
    parser.add_argument("--com-s6-plan")
    parser.add_argument("--com-plan")
    args = parser.parse_args(argv)

    if args.com_network and args.com_out:
        return _com_phase(
            Path(args.com_network),
            args.com_label or "child",
            Path(args.com_out),
            Path(args.com_s6_plan) if args.com_s6_plan else None,
        )
    if args.com_plan and args.com_out:
        return _com_load_many(Path(args.com_plan), Path(args.com_out))

    WORK_ROOT.mkdir(parents=True, exist_ok=True)
    NETS_ROOT.mkdir(parents=True, exist_ok=True)
    _load_state()
    _STATE["probe"] = "genesis_signals_live_check"
    _STATE["last_run_utc"] = _now()
    _STATE["python"] = sys.version
    _STATE["work_root"] = str(WORK_ROOT)
    _save_state()

    _log("")
    _log("#" * 78)
    _log(f"# genesis signals live check — {_now()} — only={args.only} label={args.label}")
    _log("#" * 78)

    started = time.time()
    if args.only in ("build", "all"):
        run_build(args.label)
    if args.only in ("census", "all"):
        run_census()
    if args.only in ("live", "all"):
        run_live(args.label, args.timeout)
    if args.only == "bisect":
        run_bisect(args.label, args.bisect_timeout)
    _STATE["wall_clock_seconds"] = round(time.time() - started, 2)
    _save_state()
    _log("")
    _log(f"wall clock: {_STATE['wall_clock_seconds']}s   state: {STATE_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
