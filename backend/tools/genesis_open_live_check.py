"""Roadmap 2.3 GENESIS OPEN LIVE CHECK: does a ``services.genesis``-generated
``.inpx`` actually open in a REAL PTV Vissim, and what does Vissim's own save
say about the two known gaps the emitter documents but could not verify offline?

``services/genesis/emit.py``'s module docstring states, in writing, that the
generated file "has NOT been opened by a real PTV Vissim" and names two known
gaps that "cannot be verified offline". This script is that named follow-up. It
settles exactly three questions and fixes nothing:

  Q1  Does a generated ``.inpx`` LOAD? Two inputs, small first:
      (a) the 4-way-crossroads synthetic fixture from
          ``backend/tests/test_genesis_osm_draft.py`` (the ``.osm`` is
          reproduced inline here and, when the test module is importable,
          asserted byte-equal to the fixture's own text), regenerated through
          ``services.genesis`` into this run's work dir;
      (b) the already-generated real Murdoch draft, COPIED into the work dir.
      A LoadNet failure is a FINDING, not a probe failure: the exception and
      any ``.err`` Vissim wrote next to the network are captured verbatim and
      the probe continues.

  Q2  The ``<linkSegs>`` element shape (emit.py known gap 1). After a
      successful load of the SMALL network, ``SaveNetAs`` into the work dir and
      parse the SAVED file: does Vissim's own save add link-segment elements to
      its ``<node>`` containers? This is why a diff of two genesis drafts
      reports 0 movements per node -- ``genesis.diff`` reads node membership
      from ``inpx_parser._node_link_ids``, i.e. from ``<linkSegment>``.

  Q3  Connector lane index under LEFT-HAND traffic (emit.py known gap 2). The
      emitter writes ``lane="<linkNo> 1"`` for both connector endpoints. What
      does Vissim write back, what does COM resolve it to
      (``get_link()``'s ``from_lanes``/``to_lanes``), and does index 1 sit on
      the KERB side or the MEDIAN side? The geometric evidence used is the
      signed lateral offset (positive = LEFT of the direction of travel) of the
      connector's endpoint from its link's centreline, emitted vs saved: under
      left-hand traffic the median is to the RIGHT of travel and the kerb to
      the LEFT.

Also recorded: the Vissim version string, whether a default vehicle composition
(and whatever else) materialised on save, and an element-count round-trip table
(emitted vs saved links / connectors / lanes / nodes, plus per-tag attribute
sets) so anything Vissim rewrote or dropped is visible.

WHAT THIS SCRIPT TOUCHES -- and what it refuses to do
-----------------------------------------------------
* ``connect(..., attach_to_running=False)`` ALWAYS (00-EXECUTION-GUIDE rule:
  GetActiveObject can attach to a human's open GUI and destroy their work).
* It NEVER opens a real dataset in place. Every network it loads is a file it
  wrote or copied into ``runtime/genesis-open-live-check/nets/<label>/``.
* LoadNet -> read ``.err`` -> SaveNetAs (into the same work dir, under a NEW
  name) -> disconnect. NO simulation: no ``RunContinuous``, no
  ``RunSingleStep``, no edits of any kind.
* BOUNDED. The COM work runs in a CHILD process with a wall-clock timeout. On a
  timeout the child is killed and every ``vissim.exe`` that did not exist
  before the child started is killed too -- PIDs present beforehand (a human's
  session) are never touched.

TWO FURTHER PHASES, ADDED AFTER THE FIRST RUN MEASURED A REFUSAL
----------------------------------------------------------------
The 2026-08-06 run answered Q1 with "no": BOTH generated drafts are refused by
``LoadNet`` with "The network file could not be loaded.", and Vissim writes NO
``.err`` for it. That leaves Q2 and Q3 unanswerable from a generated file, so
two more phases -- same operation class, same envelope (LoadNet, ``.err``
capture, ``SaveNetAs`` into the work dir, close; no simulation, no edits, no
real dataset opened in place) -- were added:

  ``--only reference``  Clone a PTV **Examples Demo** network into the work dir,
      load it, and ``SaveNetAs`` a copy. Two of them, chosen for what each
      question needs: ``Roundabout Buehl.DE`` (the smallest example that HAS
      ``<node>`` elements -> Q2) and ``Roundabout London.UK`` (the only
      LEFT-HAND-TRAFFIC example, ``netPara trafReg="LEFTHANDTRAFFIC"`` -> Q3).
      Q2 and Q3 are then read off Vissim's OWN save.

  ``--only diagnose``  Why is the generated file refused? A bisect: a series of
      variants (generated as-is; generated with a real root element; a verbatim
      real network as the control; that real network stripped down to only the
      containers the emitter writes) each ``LoadNet``-ed in ONE session. Files
      only ever read, never edited in place.

Usage (from ``backend/``, with the venv python)::

    python tools/genesis_open_live_check.py                 # both drafts (Q1)
    python tools/genesis_open_live_check.py --only small
    python tools/genesis_open_live_check.py --only murdoch --murdoch-timeout 600
    python tools/genesis_open_live_check.py --only reference   # Q2 + Q3
    python tools/genesis_open_live_check.py --only diagnose    # why Q1 failed
    python tools/genesis_open_live_check.py --only all

EMITTER v2 ACCEPTANCE RE-RUN (2026-08-06)
-----------------------------------------
The fix list above landed as ``services/genesis/schema.py`` + emitter v2, and
this probe is the acceptance gate for it. Two changes, both minimal and both
about WHERE the inputs come from rather than what is done to them:

* ``DEFAULT_MURDOCH`` now points at the v2 regeneration (``murdoch-gen-v2``);
* ``--label-suffix`` appends to the run label and its ``nets/<label>/`` dir, so
  the acceptance run lands under ``small_v2`` / ``murdoch_v2`` and leaves the v1
  refusal evidence under ``small`` / ``murdoch`` intact to compare against.

The COM envelope is untouched: ``connect(attach_to_running=False)``, temp work
dirs only, LoadNet → ``.err`` → SaveNetAs → close, no simulation, no edits,
bounded child processes, and only PIDs this probe started are ever killed.

  python tools/genesis_open_live_check.py --only both --label-suffix _v2

Re-runnable and idempotent: each run recreates its own ``nets/<label>/``
directory from scratch and merges its results into ``state.json`` under that
label, so ``--only small`` and ``--only murdoch`` runs accumulate into one
state file rather than clobbering each other. ``log.txt`` is appended with a
run header (the house tee rule: live-check evidence lives in ``runtime/``, not
only on a console).
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
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

# EXTRACTED 2026-08-14 (unit #33). The COM leg of this probe -- LoadNet, the
# .err capture, the loaded-model census, SaveNetAs -- now lives in
# ``services/vissim_open_probe.py`` so that the queued live-check JOB
# (``services.genesis_jobs``) runs the SAME probe rather than a second copy of
# it. Nothing about this script's behaviour changed: ``probe_open`` returns the
# exact dict ``_com_phase`` used to build inline, written here with the same
# ``json.dumps`` call, so ``state.json``'s shape is unchanged. Imported AFTER
# the sys.path insert above (that is what makes ``services`` importable), and
# COM-free at import time -- ``vissim_open_probe`` reaches pywin32 only inside
# its own connect seam.
from services.vissim_open_probe import (  # noqa: E402
    probe_open,
    read_err_files as _read_err,
    read_version as _read_version,
    snapshot_err_paths as _snapshot_err_paths,
)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # pragma: no cover - detached stream
    pass

WORK_ROOT = REPO_ROOT / "runtime" / "genesis-open-live-check"
NETS_ROOT = WORK_ROOT / "nets"
STATE_PATH = WORK_ROOT / "state.json"
LOG_PATH = WORK_ROOT / "log.txt"

#: The already-generated real Murdoch draft. Copied, never opened in place.
#: RE-POINTED 2026-08-06 at the EMITTER v2 regeneration (``murdoch-gen-v2``);
#: the v1 output next door in ``murdoch-gen`` is the refused file this probe's
#: first run measured, and is kept so the two are comparable. Override with
#: ``--murdoch`` to point at either.
DEFAULT_MURDOCH = Path(
    r"C:\Users\user\AppData\Local\Temp\claude"
    r"\C--Users-user-Documents-GitHub-vissimDashboard"
    r"\cb830512-81cd-432f-8995-a267871ac006\scratchpad\murdoch-gen-v2\murdoch.inpx"
)

DEFAULT_SMALL_TIMEOUT_S = 300.0
DEFAULT_MURDOCH_TIMEOUT_S = 540.0
DEFAULT_REFERENCE_TIMEOUT_S = 420.0
DEFAULT_DIAGNOSE_TIMEOUT_S = 600.0

EXAMPLES_ROOT = Path(r"C:\Users\Public\Documents\PTV Vision\PTV Vissim 2026\Examples Demo")

#: Q2 needs a real network that HAS ``<node>`` elements; Q3 needs a real
#: LEFT-HAND-TRAFFIC network. No single Examples Demo network is both (London
#: has no nodes at all), so both are run.
REFERENCE_EXAMPLES = {
    "ref_nodes": EXAMPLES_ROOT / "Roundabout Buehl.DE",
    "ref_lht": EXAMPLES_ROOT / "Roundabout London.UK",
}

#: The version-id list and the ``.err`` read cap moved to
#: ``services/vissim_open_probe.py`` with the readers that use them (unit #33).
_MAX_FRAGMENT_CHARS = 4000


# ---------------------------------------------------------------------------
# Logging / state (tee -- the house rule: evidence lands in runtime/)
# ---------------------------------------------------------------------------


def _log(msg: str = "") -> None:
    print(msg, flush=True)
    try:
        WORK_ROOT.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(msg + "\n")
    except OSError:
        pass


_state: dict[str, Any] = {}


def _load_state() -> dict[str, Any]:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
    return {}


def _save_state() -> None:
    """Written after EVERY step, not once at the end: a probe that dies mid-COM
    must still leave behind what it had already measured."""
    WORK_ROOT.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(_state, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, STATE_PATH)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# The SMALL network: the crossroads .osm, reproduced inline
#
# Reproduced (not imported) so this script stays self-contained like every other
# live check in this directory -- but when the test module IS importable the two
# strings are compared, so a drift in the fixture cannot silently make this
# probe measure a different network than the suite does.
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


def _osm_text(nodes: list[tuple[int, float, float]], ways: list[tuple[int, list[int], dict]]) -> str:
    parts = ['<?xml version="1.0" encoding="UTF-8"?>', '<osm version="0.6" generator="prism-test">']
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
    """``test_genesis_osm_draft.crossroads_osm`` -- way 101 Main St (2 lanes per
    direction, tagged), way 102 First Ave (class default, flagged), way 103
    footway (skipped). 8 links / 12 connectors / 1 node."""
    return _osm_text(
        _CROSS_NODES + [(9, LAT0 - 0.005, LON0)],
        [
            (101, [1, 2, 3], {"highway": "secondary", "name": "Main St", "lanes": "4"}),
            (102, [4, 2, 5], {"highway": "residential", "name": "First Ave"}),
            (103, [9, 4], {"highway": "footway"}),
        ],
    )


def _fixture_matches_suite() -> dict[str, Any]:
    """Best-effort: import the test module BY PATH and compare its fixture text
    with the inline copy above."""
    test_path = BACKEND_ROOT / "tests" / "test_genesis_osm_draft.py"
    try:
        import importlib.util

        spec = importlib.util.spec_from_file_location("_genesis_fixture_probe", test_path)
        if spec is None or spec.loader is None:
            return {"compared": False, "reason": "spec_from_file_location returned None"}
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        suite_text = module.crossroads_osm()
        return {
            "compared": True,
            "identical": suite_text == crossroads_osm(),
            "source": str(test_path),
        }
    except Exception as exc:  # pragma: no cover - import-environment dependent
        return {"compared": False, "reason": f"{type(exc).__name__}: {exc}"}


def _build_small_network(target_dir: Path) -> dict[str, Any]:
    """Regenerate the crossroads draft through ``services.genesis`` into
    ``target_dir`` and return what was written."""
    from services.genesis.draft import build_draft
    from services.genesis.emit import emit_inpx
    from services.genesis.osm import parse_osm_file

    osm_path = target_dir / "cross.osm"
    osm_path.write_text(crossroads_osm(), encoding="utf-8", newline="")
    draft = build_draft(parse_osm_file(osm_path), name="cross")
    if draft.refused:
        raise RuntimeError(f"crossroads draft was refused: {draft.refusals}")
    inpx = emit_inpx(draft, target_dir / "cross.inpx")
    return {
        "osm": str(osm_path),
        "inpx": str(inpx),
        "draft_links": len(draft.links),
        "draft_connectors": len(draft.connectors),
        "draft_nodes": len(draft.nodes),
        "fixture_check": _fixture_matches_suite(),
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


def _is_connector(link_el: ET.Element) -> bool:
    return link_el.find("fromLinkEndPt") is not None or link_el.find("toLinkEndPt") is not None


def _link_index(root: ET.Element) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for el in root.findall("./links/link"):
        no = el.get("no") or ""
        pts = [
            (float(p.get("x", "0")), float(p.get("y", "0")))
            for p in el.findall("./geometry/linkPolyPts/linkPolyPoint")
        ]
        lanes = el.findall("./lanes/lane")
        from_el = el.find("fromLinkEndPt")
        to_el = el.find("toLinkEndPt")
        out[no] = {
            "no": no,
            "name": el.get("name"),
            "points": pts,
            "lane_count": len(lanes),
            "lane_attrs": [dict(l.attrib) for l in lanes],
            "is_connector": _is_connector(el),
            "from_end_pt": dict(from_el.attrib) if from_el is not None else None,
            "to_end_pt": dict(to_el.attrib) if to_el is not None else None,
            "attrs": dict(el.attrib),
        }
    return out


def _element_stats(root: ET.Element) -> dict[str, Any]:
    links = root.findall("./links/link")
    plain = [l for l in links if not _is_connector(l)]
    conns = [l for l in links if _is_connector(l)]
    nodes = root.findall("./nodes/node")
    top_level: dict[str, int] = {}
    for child in root:
        if isinstance(child.tag, str):
            top_level[child.tag] = top_level.get(child.tag, 0) + 1
    return {
        "root_tag": root.tag,
        "root_attrs": dict(root.attrib),
        "links_total_elements": len(links),
        "plain_links": len(plain),
        "connectors": len(conns),
        "lanes_on_plain_links": sum(len(l.findall("./lanes/lane")) for l in plain),
        "lanes_on_connectors": sum(len(l.findall("./lanes/lane")) for l in conns),
        "link_poly_points": sum(len(l.findall("./geometry/linkPolyPts/linkPolyPoint")) for l in links),
        "nodes": len(nodes),
        "node_children_tags": sorted({c.tag for n in nodes for c in n if isinstance(c.tag, str)}),
        "node_link_segment_elements": len(root.findall(".//node//linkSegment")),
        "node_linkSegs_containers": len(root.findall(".//node/linkSegs")),
        "top_level_containers": dict(sorted(top_level.items())),
        "total_elements": sum(1 for _ in root.iter()),
        "link_attr_names": sorted({k for l in links for k in l.attrib}),
        "node_attr_names": sorted({k for n in nodes for k in n.attrib}),
    }


_FRAGMENT_CACHE: dict[Path, str] = {}


def _raw_text(path: Path) -> str:
    if path not in _FRAGMENT_CACHE:
        _FRAGMENT_CACHE[path] = path.read_text(encoding="utf-8", errors="replace")
    return _FRAGMENT_CACHE[path]


def _raw_fragment(path: Path, tag: str, attr: str, value: str) -> str | None:
    """The verbatim source text of ``<tag attr="value"> ... </tag>``.

    Quoting the FILE's own bytes, not a re-serialised ElementTree: the point of
    this probe is what Vissim wrote, so a fragment that went through a
    round-trip of our own would be evidence about us, not about Vissim.
    ``<link>``/``<node>`` never nest inside themselves, so a non-greedy match to
    the first closing tag is exact.
    """
    text = _raw_text(path)
    pattern = re.compile(
        rf"<{tag}\b[^>]*\b{attr}=\"{re.escape(value)}\"[^>]*?(/>|>.*?</{tag}>)",
        re.DOTALL,
    )
    match = pattern.search(text)
    if match is None:
        return None
    fragment = match.group(0)
    if len(fragment) > _MAX_FRAGMENT_CHARS:
        fragment = fragment[:_MAX_FRAGMENT_CHARS] + "\n... [truncated]"
    return fragment


def _left_offset(a: tuple[float, float], b: tuple[float, float], pt: tuple[float, float]) -> float | None:
    """Signed perpendicular distance of ``pt`` from the directed line a->b.

    POSITIVE = LEFT of the direction of travel. Under left-hand traffic the
    kerb is on the left and the median on the right, which is what makes this
    number answer Q3.
    """
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = math.hypot(dx, dy)
    if length == 0:
        return None
    return ((pt[0] - a[0]) * (-dy) + (pt[1] - a[1]) * dx) / length


def _connector_lane_evidence(
    emitted: dict[str, dict[str, Any]],
    saved: dict[str, dict[str, Any]],
    emitted_path: Path,
    saved_path: Path,
    limit: int = 12,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for no, em in sorted(emitted.items(), key=lambda kv: int(kv[0]) if kv[0].isdigit() else 0):
        if not em["is_connector"]:
            continue
        sv = saved.get(no)
        row: dict[str, Any] = {
            "connector_no": no,
            "emitted_name": em["name"],
            "saved_present": sv is not None,
            "emitted_from_end_pt": em["from_end_pt"],
            "emitted_to_end_pt": em["to_end_pt"],
            "saved_from_end_pt": sv["from_end_pt"] if sv else None,
            "saved_to_end_pt": sv["to_end_pt"] if sv else None,
            "emitted_lane_count": em["lane_count"],
            "saved_lane_count": sv["lane_count"] if sv else None,
            "emitted_points": em["points"][:2] + em["points"][-1:],
            "saved_points": (sv["points"][:2] + sv["points"][-1:]) if sv else None,
        }
        from_ref = (em["from_end_pt"] or {}).get("lane", "").split()
        to_ref = (em["to_end_pt"] or {}).get("lane", "").split()
        for side, ref, endpoint_key in (
            ("from", from_ref, 0),
            ("to", to_ref, -1),
        ):
            if len(ref) != 2:
                continue
            link_no = ref[0]
            row[f"{side}_link_no"] = link_no
            row[f"{side}_lane_index_emitted"] = ref[1]
            host_em = emitted.get(link_no)
            host_sv = saved.get(link_no)
            if host_em and len(host_em["points"]) >= 2:
                row[f"{side}_link_lane_count"] = host_em["lane_count"]
                row[f"{side}_link_lane_width"] = (
                    host_em["lane_attrs"][0].get("width") if host_em["lane_attrs"] else None
                )
                a, b = host_em["points"][0], host_em["points"][-1]
                if em["points"]:
                    row[f"{side}_left_offset_emitted_m"] = _left_offset(a, b, em["points"][endpoint_key])
                if sv and sv["points"]:
                    ha, hb = (
                        (host_sv["points"][0], host_sv["points"][-1])
                        if host_sv and len(host_sv["points"]) >= 2
                        else (a, b)
                    )
                    row[f"{side}_left_offset_saved_m"] = _left_offset(ha, hb, sv["points"][endpoint_key])
        rows.append(row)
        if len(rows) >= limit:
            break
    if rows:
        first = rows[0]["connector_no"]
        rows[0]["emitted_fragment"] = _raw_fragment(emitted_path, "link", "no", first)
        rows[0]["saved_fragment"] = _raw_fragment(saved_path, "link", "no", first)
    return rows


def _analyse_round_trip(label: str, emitted_path: Path, saved_path: Path) -> dict[str, Any]:
    emitted_root = _parse(emitted_path)
    saved_root = _parse(saved_path)
    em_stats = _element_stats(emitted_root)
    sv_stats = _element_stats(saved_root)
    emitted_links = _link_index(emitted_root)
    saved_links = _link_index(saved_root)

    em_ids = {no for no, v in emitted_links.items()}
    sv_ids = {no for no, v in saved_links.items()}

    node_fragment = None
    node_nos = [n.get("no") for n in saved_root.findall("./nodes/node") if n.get("no")]
    if node_nos:
        node_fragment = _raw_fragment(saved_path, "node", "no", node_nos[0])
    emitted_node_fragment = None
    em_node_nos = [n.get("no") for n in emitted_root.findall("./nodes/node") if n.get("no")]
    if em_node_nos:
        emitted_node_fragment = _raw_fragment(emitted_path, "node", "no", em_node_nos[0])

    # A plain (non-connector) link, quoted both sides -- this is where a dropped
    # or rewritten attribute on ordinary links shows up.
    plain_no = next((no for no, v in sorted(emitted_links.items()) if not v["is_connector"]), None)

    return {
        "emitted_path": str(emitted_path),
        "saved_path": str(saved_path),
        "emitted_bytes": emitted_path.stat().st_size,
        "saved_bytes": saved_path.stat().st_size,
        "emitted_stats": em_stats,
        "saved_stats": sv_stats,
        "link_ids_missing_in_saved": sorted(em_ids - sv_ids, key=lambda s: int(s) if s.isdigit() else 0)[:50],
        "link_ids_added_in_saved": sorted(sv_ids - em_ids, key=lambda s: int(s) if s.isdigit() else 0)[:50],
        "containers_added_by_save": sorted(
            set(sv_stats["top_level_containers"]) - set(em_stats["top_level_containers"])
        ),
        "containers_dropped_by_save": sorted(
            set(em_stats["top_level_containers"]) - set(sv_stats["top_level_containers"])
        ),
        "vehicle_composition_elements_in_saved": len(saved_root.findall(".//vehicleComposition")),
        "vehicle_type_elements_in_saved": len(saved_root.findall(".//vehicleType")),
        "saved_first_node_fragment": node_fragment,
        "emitted_first_node_fragment": emitted_node_fragment,
        "emitted_plain_link_fragment": _raw_fragment(emitted_path, "link", "no", plain_no) if plain_no else None,
        "saved_plain_link_fragment": _raw_fragment(saved_path, "link", "no", plain_no) if plain_no else None,
        "connector_evidence": _connector_lane_evidence(
            emitted_links, saved_links, emitted_path, saved_path
        ),
    }


# ---------------------------------------------------------------------------
# REAL-Vissim XML shapes (Q2 + Q3 read off Vissim's own save)
#
# A real ``.inpx`` does NOT look like what the emitter writes, so these helpers
# are separate from the ones above rather than parameterised: a connector's
# endpoints are ``<fromLinkEndPt link="32" pos="…"/>`` (attribute ``link``, no
# lane token), and the lane mapping lives in a nested
# ``<lanes><lane><fromLanes><objectRef key="32 2"/>``.
# ---------------------------------------------------------------------------


def _real_link_index(root: ET.Element) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for el in root.findall("./links/link"):
        no = el.get("no") or ""
        from_el = el.find("fromLinkEndPt")
        to_el = el.find("toLinkEndPt")
        lane_els = el.findall("./lanes/lane")
        out[no] = {
            "no": no,
            "name": el.get("name"),
            "points": [
                (float(p.get("x", "0")), float(p.get("y", "0")))
                for p in el.findall("./geometry/linkPolyPts/linkPolyPoint")
            ],
            "lane_count": len(lane_els),
            "is_connector": from_el is not None or to_el is not None,
            "from_link": from_el.get("link") if from_el is not None else None,
            "from_pos": from_el.get("pos") if from_el is not None else None,
            "to_link": to_el.get("link") if to_el is not None else None,
            "to_pos": to_el.get("pos") if to_el is not None else None,
            "from_end_pt_attrs": dict(from_el.attrib) if from_el is not None else None,
            "to_end_pt_attrs": dict(to_el.attrib) if to_el is not None else None,
            "from_lane_keys": [
                ref.get("key")
                for lane in lane_els
                for ref in lane.findall("./fromLanes/objectRef")
                if ref.get("key")
            ],
            "to_lane_keys": [
                ref.get("key")
                for lane in lane_els
                for ref in lane.findall("./toLanes/objectRef")
                if ref.get("key")
            ],
        }
    return out


def _nearest_segment_offset(points: list[tuple[float, float]], pt: tuple[float, float]) -> float | None:
    """Signed lateral offset of ``pt`` from a polyline, POSITIVE = LEFT of the
    polyline's direction of travel, measured on the segment ``pt`` is closest to
    (real Vissim links are curved; the whole-polyline chord would be wrong)."""
    best: tuple[float, float] | None = None
    for a, b in zip(points, points[1:]):
        dx, dy = b[0] - a[0], b[1] - a[1]
        length = math.hypot(dx, dy)
        if length == 0:
            continue
        t = max(0.0, min(1.0, ((pt[0] - a[0]) * dx + (pt[1] - a[1]) * dy) / (length * length)))
        proj = (a[0] + t * dx, a[1] + t * dy)
        dist = math.hypot(pt[0] - proj[0], pt[1] - proj[1])
        signed = ((pt[0] - a[0]) * (-dy) + (pt[1] - a[1]) * dx) / length
        if best is None or dist < best[0]:
            best = (dist, signed)
    return None if best is None else best[1]


def _lane_side_evidence(root: ET.Element, limit: int = 400) -> dict[str, Any]:
    """Q3, empirically: is lane index 1 the KERB-side or the MEDIAN-side lane?

    Vissim stores no lane coordinates, so the lane's side is measured through
    the connectors that attach to it: a connector's first polyline point sits on
    the lane it leaves from. For every multi-lane link, the signed lateral
    offset (positive = LEFT of travel) of each attached connector is grouped by
    the lane index in its ``<fromLanes><objectRef key="<link> <lane>">``. If
    offset DECREASES as the index rises, index 1 is the LEFT-most lane; if it
    increases, index 1 is the RIGHT-most.

    Under LEFT-hand traffic the median sits to the RIGHT of travel and the kerb
    to the LEFT, which is what turns that ordering into the answer.
    """
    idx = _real_link_index(root)
    samples: list[dict[str, Any]] = []
    for no, conn in idx.items():
        if not conn["is_connector"] or not conn["points"]:
            continue
        host = idx.get(conn["from_link"] or "")
        if host is None or host["lane_count"] < 2 or len(host["points"]) < 2:
            continue
        for key in conn["from_lane_keys"]:
            parts = (key or "").split()
            if len(parts) != 2 or parts[0] != host["no"]:
                continue
            offset = _nearest_segment_offset(host["points"], conn["points"][0])
            if offset is None:
                continue
            samples.append(
                {
                    "connector_no": no,
                    "host_link_no": host["no"],
                    "host_lane_count": host["lane_count"],
                    "lane_index": int(parts[1]) if parts[1].isdigit() else parts[1],
                    "left_offset_m": round(offset, 4),
                }
            )
            if len(samples) >= limit:
                break
        if len(samples) >= limit:
            break

    # Per host link: mean offset per lane index, and the direction of the trend.
    by_link: dict[str, dict[Any, list[float]]] = {}
    for s in samples:
        by_link.setdefault(s["host_link_no"], {}).setdefault(s["lane_index"], []).append(s["left_offset_m"])
    verdicts: list[dict[str, Any]] = []
    lower_index_is_left = 0
    lower_index_is_right = 0
    no_evidence = 0
    # Below this the two lane indices' connectors attach at the same lateral
    # position, which says nothing about their order. A tie is NO EVIDENCE, not
    # a verdict -- counting it as one would manufacture agreement.
    epsilon = 0.05
    for link_no, per_lane in sorted(by_link.items()):
        if len(per_lane) < 2:
            continue
        means = {k: round(sum(v) / len(v), 4) for k, v in sorted(per_lane.items())}
        keys = sorted(means)
        trend = means[keys[-1]] - means[keys[0]]
        if abs(trend) < epsilon:
            verdict = "no_evidence_tie"
            no_evidence += 1
        elif trend < 0:
            verdict = "index_1_is_LEFTmost"
            lower_index_is_left += 1
        else:
            verdict = "index_1_is_RIGHTmost"
            lower_index_is_right += 1
        verdicts.append(
            {
                "host_link_no": link_no,
                "lane_count": idx[link_no]["lane_count"],
                "mean_left_offset_by_lane_index": means,
                "offset_change_from_lowest_to_highest_index_m": round(trend, 4),
                "verdict": verdict,
            }
        )
    return {
        "samples": samples[:60],
        "sample_count": len(samples),
        "per_link_verdicts": verdicts,
        "links_where_index_1_is_leftmost": lower_index_is_left,
        "links_where_index_1_is_rightmost": lower_index_is_right,
        "links_with_no_evidence_tie": no_evidence,
        "traffic_regulation": (root.find("./netPara").get("trafReg") if root.find("./netPara") is not None else None),
    }


def _elide_geometry(fragment: str | None) -> str | None:
    """A real connector's fragment is mostly polyline points; the interesting
    parts are the endpoints and the lane mapping. Keep those, elide the rest."""
    if fragment is None:
        return None
    def _sub(match: re.Match[str]) -> str:
        count = match.group(0).count("<linkPolyPoint")
        return f"<geometry><!-- {count} linkPolyPoint elements elided by the probe --></geometry>"

    return re.sub(r"<geometry>.*?</geometry>", _sub, fragment, flags=re.DOTALL)


def _pick_reference_inpx(folder: Path, prefer_nodes: bool) -> Path:
    """The ``.inpx`` in an Examples Demo folder, choosing the one with the most
    ``<node>`` elements when nodes are what the question needs."""
    candidates = sorted(folder.glob("*.inpx"))
    if not candidates:
        raise FileNotFoundError(f"No .inpx in {folder}")
    if not prefer_nodes:
        return candidates[0]
    return max(candidates, key=lambda p: p.read_text(encoding="utf-8", errors="replace").count("<node "))


def _analyse_reference(original: Path, saved: Path) -> dict[str, Any]:
    orig_root = _parse(original)
    saved_root = _parse(saved)
    orig_stats = _element_stats(orig_root)
    saved_stats = _element_stats(saved_root)

    node_nos = [n.get("no") for n in saved_root.findall("./nodes/node") if n.get("no")]
    conn_nos = [
        l.get("no")
        for l in saved_root.findall("./links/link")
        if l.find("fromLinkEndPt") is not None and l.get("no")
    ]
    return {
        "original_path": str(original),
        "saved_path": str(saved),
        "original_bytes": original.stat().st_size,
        "saved_bytes": saved.stat().st_size,
        "original_stats": orig_stats,
        "saved_stats": saved_stats,
        "containers_added_by_save": sorted(
            set(saved_stats["top_level_containers"]) - set(orig_stats["top_level_containers"])
        ),
        "containers_dropped_by_save": sorted(
            set(orig_stats["top_level_containers"]) - set(saved_stats["top_level_containers"])
        ),
        "vehicle_composition_before": len(orig_root.findall(".//vehicleComposition")),
        "vehicle_composition_after": len(saved_root.findall(".//vehicleComposition")),
        "vehicle_type_before": len(orig_root.findall(".//vehicleType")),
        "vehicle_type_after": len(saved_root.findall(".//vehicleType")),
        # --- Q2 -------------------------------------------------------------
        "q2_node_link_segment_elements_before": orig_stats["node_link_segment_elements"],
        "q2_node_link_segment_elements_after": saved_stats["node_link_segment_elements"],
        "q2_node_linkSegs_containers_after": saved_stats["node_linkSegs_containers"],
        "q2_node_child_tags_after": saved_stats["node_children_tags"],
        "q2_saved_node_fragment": _raw_fragment(saved, "node", "no", node_nos[0]) if node_nos else None,
        "q2_original_node_fragment": (
            _raw_fragment(original, "node", "no", node_nos[0]) if node_nos else None
        ),
        # --- Q3 -------------------------------------------------------------
        "q3_saved_connector_fragment": (
            _elide_geometry(_raw_fragment(saved, "link", "no", conn_nos[0])) if conn_nos else None
        ),
        "q3_lane_sides_saved": _lane_side_evidence(saved_root),
        "q3_lane_sides_original": _lane_side_evidence(orig_root),
        "q3_netPara_saved": (
            dict(saved_root.find("./netPara").attrib) if saved_root.find("./netPara") is not None else None
        ),
    }


# ---------------------------------------------------------------------------
# CHILD process: the only code in this file that touches COM
# ---------------------------------------------------------------------------


def _com_phase(net_path: Path, label: str, out_path: Path) -> int:
    """LoadNet -> .err -> SaveNetAs -> disconnect. Nothing else. Child process
    only, so the parent can bound it with a wall clock.

    The COM leg itself is ``services.vissim_open_probe.probe_open`` (unit #33 --
    the queued live-check job runs the same function). This wrapper is what it
    always was around it: the same ``json.dumps`` of the same dict, written
    after EVERY step, because a probe that dies mid-COM must still leave behind
    what it had already measured.
    """
    def flush(current: dict[str, Any]) -> None:
        out_path.write_text(
            json.dumps(current, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
        )

    result = probe_open(net_path, label, on_update=flush)
    return 3 if result.get("fatal_error") else 0


def _com_load_many(plan_path: Path, out_path: Path) -> int:
    """The bisect: ``LoadNet`` every file named in ``plan_path`` (a JSON list of
    ``{"label": …, "path": …}``) in ONE session, recording ok/error and any
    ``.err`` per variant. Loads only -- nothing is saved, nothing is edited."""
    import pythoncom

    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    result: dict[str, Any] = {"started_utc": _now(), "connected": False, "variants": []}

    def flush() -> None:
        out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")

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
            entry: dict[str, Any] = {"label": item["label"], "path": str(path), "note": item.get("note")}
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
            entry["err_text"] = "\n".join(
                f["text"] for f in err.get("files", []) if f["name"] in (err.get("new_files") or [])
            ) or None
            if entry["load_ok"]:
                try:
                    entry["link_count"] = int(controller.vissim.Net.Links.Count)
                except Exception as exc:
                    entry["link_count"] = f"<unavailable: {type(exc).__name__}>"
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

    The full task list is filtered in python rather than with ``/FI`` so this
    behaves identically under cmd, PowerShell and Git Bash (the ``/FI``
    argument is mangled by the MSYS path converter under Git Bash).
    """
    try:
        proc = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return set()
    pids: set[int] = set()
    for line in proc.stdout.splitlines():
        parts = [p.strip('"') for p in line.split('","')]
        if len(parts) < 2:
            continue
        name = parts[0].strip('"').lower()
        if name.startswith("vissim"):
            try:
                pids.add(int(parts[1]))
            except ValueError:
                continue
    return pids


def _kill_new_vissim(before: set[int]) -> list[int]:
    """Kill only Vissim processes that did NOT exist before we started.

    A blanket ``taskkill /IM vissim.exe`` would kill a human's open GUI; the
    whole point of ``attach_to_running=False`` is that we never touch theirs.
    """
    killed: list[int] = []
    for pid in sorted(_vissim_pids() - before):
        try:
            subprocess.run(["taskkill", "/PID", str(pid), "/F", "/T"], capture_output=True, timeout=60)
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
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s, cwd=str(BACKEND_ROOT))
        returncode = proc.returncode
        stdout, stderr = proc.stdout, proc.stderr
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        stdout = exc.stdout.decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = exc.stderr.decode("utf-8", "replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        _log(f"  !! TIMEOUT after {timeout_s:.0f}s -- killing the child and any Vissim it started")
    elapsed = round(time.time() - started, 2)

    killed = _kill_new_vissim(before)
    if killed:
        _log(f"  killed leftover Vissim PIDs (ours only): {killed}")
    leftover = sorted(_vissim_pids() - before)
    if leftover:
        _log(f"  !! WARNING: Vissim PIDs still alive after cleanup: {leftover}")

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
        "orphan_vissim_pids_after_cleanup": leftover,
        "killed_pids": killed,
        "com": child_result,
    }


def _run_com_child(net_path: Path, label: str, timeout_s: float) -> dict[str, Any]:
    out_path = net_path.parent / f"{label}_com.json"
    return _bounded_child(
        [
            "--com-network",
            str(net_path),
            "--com-label",
            label,
            "--com-out",
            str(out_path),
        ],
        out_path,
        timeout_s,
    )


def _fresh_dir(path: Path) -> Path:
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _summarise(label: str, entry: dict[str, Any]) -> None:
    com = entry.get("run", {}).get("com", {})
    _log(f"  connected      : {com.get('connected')}  prog_id={com.get('prog_id')}")
    version = (com.get("version") or {}).get("att_values", {})
    _log(f"  version AttValues: {version}")
    _log(f"  load_ok        : {com.get('load_ok')}  ({com.get('load_seconds')}s)")
    if com.get("load_error"):
        _log(f"  load_error     : {com['load_error']}")
    err = com.get("err_after_load") or {}
    _log(f"  .err after load: new={err.get('new_files')} all={[f['name'] for f in err.get('files', [])]}")
    for f in err.get("files", []):
        _log(f"  --- {f['name']} ({f['bytes']} bytes) ---")
        _log(f["text"])
        _log("  --- end ---")
    _log(f"  save_ok        : {com.get('save_ok')}  ({com.get('save_seconds')}s)")
    if com.get("save_error"):
        _log(f"  save_error     : {com['save_error']}")
    err2 = com.get("err_after_save") or {}
    if err2.get("new_files"):
        _log(f"  .err after save: new={err2.get('new_files')}")
        for f in err2.get("files", []):
            if f["name"] in err2.get("new_files", []):
                _log(f"  --- {f['name']} ---")
                _log(f["text"])
                _log("  --- end ---")
    analysis = entry.get("round_trip")
    if analysis:
        em, sv = analysis["emitted_stats"], analysis["saved_stats"]
        _log("  ROUND TRIP (emitted -> saved):")
        for key in (
            "plain_links",
            "connectors",
            "lanes_on_plain_links",
            "lanes_on_connectors",
            "link_poly_points",
            "nodes",
            "node_link_segment_elements",
            "node_linkSegs_containers",
            "total_elements",
        ):
            flag = "" if em[key] == sv[key] else "   <-- CHANGED"
            _log(f"    {key:<28} {em[key]!s:>8} -> {sv[key]!s:>8}{flag}")
        _log(f"    root attrs                 {em['root_attrs']} -> {sv['root_attrs']}")
        _log(f"    containers added by save   {analysis['containers_added_by_save']}")
        _log(f"    containers dropped by save {analysis['containers_dropped_by_save']}")
        _log(f"    vehicleComposition in saved: {analysis['vehicle_composition_elements_in_saved']}")
        _log(f"    vehicleType in saved       : {analysis['vehicle_type_elements_in_saved']}")
        _log("  SAVED first <node> fragment:")
        _log(str(analysis.get("saved_first_node_fragment")))
        _log("  EMITTED first <node> fragment:")
        _log(str(analysis.get("emitted_first_node_fragment")))
        rows = analysis.get("connector_evidence") or []
        if rows:
            _log("  CONNECTOR lane refs (emitted -> saved):")
            for row in rows:
                _log(
                    f"    #{row['connector_no']}: from {row.get('emitted_from_end_pt')} -> "
                    f"{row.get('saved_from_end_pt')} | to {row.get('emitted_to_end_pt')} -> "
                    f"{row.get('saved_to_end_pt')}"
                )
                _log(
                    f"        left-offset from-link emitted="
                    f"{row.get('from_left_offset_emitted_m')} saved={row.get('from_left_offset_saved_m')} "
                    f"(host lanes={row.get('from_link_lane_count')} width={row.get('from_link_lane_width')})"
                )
            _log("  EMITTED connector fragment:")
            _log(str(rows[0].get("emitted_fragment")))
            _log("  SAVED connector fragment:")
            _log(str(rows[0].get("saved_fragment")))


def _run_label(label: str, net_path: Path, timeout_s: float) -> dict[str, Any]:
    _log("")
    _log("=" * 78)
    _log(f"[{label}] {net_path}")
    _log("=" * 78)
    entry: dict[str, Any] = {"label": label, "network": str(net_path), "started_utc": _now()}
    _state.setdefault("runs", {})[label] = entry
    _save_state()

    entry["run"] = _run_com_child(net_path, label, timeout_s)
    entry["finished_utc"] = _now()
    _save_state()

    com = entry["run"].get("com", {})
    saved_path = com.get("saved_path")
    if com.get("save_ok") and saved_path and Path(saved_path).exists():
        try:
            entry["round_trip"] = _analyse_round_trip(label, net_path, Path(saved_path))
        except Exception as exc:
            entry["round_trip_error"] = f"{type(exc).__name__}: {exc}"
    _save_state()
    _summarise(label, entry)
    _save_state()
    return entry


def _run_reference(label: str, source: Path, timeout_s: float) -> dict[str, Any]:
    """Q2/Q3 phase: clone a real-shaped network into the work dir, load it, save
    a copy, and read the answers off Vissim's OWN save.

    ``source`` is either an Examples Demo FOLDER (cloned whole, house pattern:
    the example is NEVER opened in place and sibling files travel with the
    ``.inpx``) or a single ``.inpx`` already sitting in the work dir."""
    _log("")
    _log("=" * 78)
    _log(f"[{label}] reference network: {source}")
    _log("=" * 78)
    if source.is_dir():
        target = _fresh_dir(NETS_ROOT / label)
        shutil.copytree(source, target, dirs_exist_ok=True)
        inpx = _pick_reference_inpx(target, prefer_nodes=(label == "ref_nodes"))
    else:
        target = _fresh_dir(NETS_ROOT / label)
        inpx = target / source.name
        shutil.copy2(source, inpx)
    _log(f"  cloned -> {inpx}")

    entry: dict[str, Any] = {
        "label": label,
        "source_folder": str(source),
        "network": str(inpx),
        "started_utc": _now(),
    }
    _state.setdefault("runs", {})[label] = entry
    _save_state()

    entry["run"] = _run_com_child(inpx, label, timeout_s)
    entry["finished_utc"] = _now()
    _save_state()

    com = entry["run"].get("com", {})
    _log(f"  connected  : {com.get('connected')}  version={(com.get('version') or {}).get('att_values', {}).get('Version')}")
    _log(f"  load_ok    : {com.get('load_ok')} ({com.get('load_seconds')}s)  save_ok: {com.get('save_ok')} ({com.get('save_seconds')}s)")
    if com.get("load_error"):
        _log(f"  load_error : {com['load_error']}")
    if com.get("save_error"):
        _log(f"  save_error : {com['save_error']}")
    for phase in ("err_after_load", "err_after_save"):
        err = com.get(phase) or {}
        if err.get("new_files"):
            _log(f"  {phase}: {err['new_files']}")
            for f in err.get("files", []):
                if f["name"] in err["new_files"]:
                    _log(f["text"])

    saved = com.get("saved_path")
    if com.get("save_ok") and saved and Path(saved).exists():
        try:
            analysis = _analyse_reference(inpx, Path(saved))
            entry["reference_analysis"] = analysis
        except Exception as exc:
            entry["reference_analysis_error"] = f"{type(exc).__name__}: {exc}"
            analysis = None
        _save_state()
        if analysis:
            _log("  --- Q2: node link-segment elements ---")
            _log(f"    <linkSegment> before save: {analysis['q2_node_link_segment_elements_before']}")
            _log(f"    <linkSegment> after  save: {analysis['q2_node_link_segment_elements_after']}")
            _log(f"    <linkSegs> containers after save: {analysis['q2_node_linkSegs_containers_after']}")
            _log(f"    <node> child tags after save: {analysis['q2_node_child_tags_after']}")
            _log("    SAVED <node> fragment:")
            _log(str(analysis["q2_saved_node_fragment"]))
            _log("  --- Q3: connector endpoint + lane mapping ---")
            _log(f"    netPara trafReg (saved): {(analysis['q3_netPara_saved'] or {}).get('trafReg')}")
            _log("    SAVED connector fragment (geometry elided):")
            _log(str(analysis["q3_saved_connector_fragment"]))
            lanes = analysis["q3_lane_sides_saved"]
            _log(f"    lane-side samples: {lanes['sample_count']}")
            _log(f"    links where index 1 is LEFT-most : {lanes['links_where_index_1_is_leftmost']}")
            _log(f"    links where index 1 is RIGHT-most: {lanes['links_where_index_1_is_rightmost']}")
            _log(f"    links with no evidence (tie)     : {lanes['links_with_no_evidence_tie']}")
            for v in lanes["per_link_verdicts"][:10]:
                _log(f"      link {v['host_link_no']} ({v['lane_count']} lanes): {v['mean_left_offset_by_lane_index']} -> {v['verdict']}")
            _log("  --- round trip (original -> Vissim's save) ---")
            for key in ("links_total_elements", "connectors", "nodes", "total_elements"):
                _log(f"    {key:<24} {analysis['original_stats'][key]} -> {analysis['saved_stats'][key]}")
            _log(f"    vehicleComposition       {analysis['vehicle_composition_before']} -> {analysis['vehicle_composition_after']}")
            _log(f"    containers added by save : {analysis['containers_added_by_save']}")
            _log(f"    containers dropped       : {analysis['containers_dropped_by_save']}")
    _save_state()
    return entry


def _write_tree(root: ET.Element, path: Path) -> Path:
    ET.ElementTree(root).write(path, encoding="UTF-8", xml_declaration=True)
    return path


def _build_diagnose_variants(target: Path) -> list[dict[str, Any]]:
    """Bisect inputs for "why is the generated file refused?".

    Two directions at once: the generated file moved TOWARDS a real one (real
    root element, real ``netPara``), and a real one stripped DOWN to the
    containers the emitter writes. Every variant is a new file in the work dir;
    the real example is only ever read.
    """
    import copy

    built = _build_small_network(target)
    generated = Path(built["inpx"])
    reference_src = _pick_reference_inpx(REFERENCE_EXAMPLES["ref_nodes"], prefer_nodes=True)
    real_root = _parse(reference_src)

    plan: list[dict[str, Any]] = [
        {
            "label": "v1_generated_asis",
            "path": str(generated),
            "note": "control: exactly what services.genesis.emit writes",
        }
    ]

    # v2: our file, real root element attributes.
    v2 = _parse(generated)
    v2.attrib = dict(real_root.attrib)
    plan.append(
        {
            "label": "v2_generated_real_root_attrs",
            "path": str(_write_tree(v2, target / "v2_generated_real_root_attrs.inpx")),
            "note": f"emitted content, root attrs replaced with {dict(real_root.attrib)}",
        }
    )

    # v3: v2 plus the real <netPara> element in place of ours.
    v3 = _parse(generated)
    v3.attrib = dict(real_root.attrib)
    ours = v3.find("./netPara")
    real_netpara = real_root.find("./netPara")
    if ours is not None and real_netpara is not None:
        v3.remove(ours)
        v3.insert(0, copy.deepcopy(real_netpara))
    plan.append(
        {
            "label": "v3_generated_real_root_and_netpara",
            "path": str(_write_tree(v3, target / "v3_generated_real_root_and_netpara.inpx")),
            "note": "v2 plus the reference network's own <netPara> element",
        }
    )

    # v4: the real network, verbatim -- the control that proves the session works.
    v4 = target / "v4_real_reference_verbatim.inpx"
    shutil.copy2(reference_src, v4)
    plan.append({"label": "v4_real_reference_verbatim", "path": str(v4), "note": f"copy of {reference_src.name}"})

    # v5/v6: the real network stripped to the containers the emitter writes.
    for label, keep in (
        ("v5_real_netpara_links_nodes", {"netPara", "links", "nodes"}),
        ("v6_real_netpara_links_only", {"netPara", "links"}),
    ):
        stripped = _parse(reference_src)
        for child in list(stripped):
            if child.tag not in keep:
                stripped.remove(child)
        plan.append(
            {
                "label": label,
                "path": str(_write_tree(stripped, target / f"{label}.inpx")),
                "note": f"the real network with only {sorted(keep)} kept",
            }
        )
    return plan


#: Top-level containers are dropped from the "graft" variant when their text
#: references a link or a lane -- a route, signal head or conflict area that
#: names link 32 cannot survive having the link table replaced, and its failure
#: would say nothing about the shape of what the emitter writes.
_REFERENCE_PATTERN = re.compile(r'\blink="|\blane="|<objectRef')


def _build_diagnose2_variants(target: Path) -> list[dict[str, Any]]:
    """Round 2: WHICH difference makes a generated file unloadable?

    Round 1 established that the root element is not the cause (a generated file
    with a real root is still refused) and that a real file stripped to
    ``netPara`` + ``links`` + ``nodes`` is "loaded" as an EMPTY network. Round 2
    isolates the element shape itself, from both directions:

    * ``w1`` -- the REAL network with every ``<link>``'s attributes reduced to
      the two the emitter writes (``no``, ``name``). If a real file becomes
      unloadable by that edit alone, the missing link attribute set is the
      cause.
    * ``w2`` -- the emitter's OWN geometry, rebuilt by deep-copying real
      ``<link>`` / ``<node>`` template elements (so every attribute set, child
      order and connector endpoint form is Vissim's own) into the real
      network's skeleton. If that loads with the right link count, the fix for
      the follow-up is fully specified.

    Nothing here is a fix: these variants are experiment inputs in the work dir,
    and ``services/genesis/emit.py`` is not touched.
    """
    import copy

    built = _build_small_network(target)
    generated = _parse(Path(built["inpx"]))
    reference_src = _pick_reference_inpx(REFERENCE_EXAMPLES["ref_nodes"], prefer_nodes=True)

    plan: list[dict[str, Any]] = []

    w0 = target / "w0_real_control.inpx"
    shutil.copy2(reference_src, w0)
    plan.append({"label": "w0_real_control", "path": str(w0), "note": "verbatim real network (control)"})

    # w1: real network, link attributes reduced to what the emitter writes.
    w1_root = _parse(reference_src)
    for link in w1_root.findall("./links/link"):
        keep = {k: v for k, v in link.attrib.items() if k in ("no", "name")}
        link.attrib.clear()
        link.attrib.update(keep)
    plan.append(
        {
            "label": "w1_real_links_attrs_stripped",
            "path": str(_write_tree(w1_root, target / "w1_real_links_attrs_stripped.inpx")),
            "note": "real network, every <link> reduced to no/name (children untouched)",
        }
    )

    # w2: our geometry inside a real skeleton, built from real templates.
    w2_root = _parse(reference_src)
    real_links = w2_root.find("./links")
    real_nodes = w2_root.find("./nodes")
    link_template = copy.deepcopy(
        next(l for l in real_links.findall("link") if l.find("fromLinkEndPt") is None)
    )
    conn_template = copy.deepcopy(
        next(l for l in real_links.findall("link") if l.find("fromLinkEndPt") is not None)
    )
    node_template = copy.deepcopy(real_nodes.find("node")) if real_nodes is not None else None
    lane_template = copy.deepcopy(link_template.find("./lanes/lane"))

    dropped: list[str] = []
    for child in list(w2_root):
        if child.tag in ("netPara", "links", "nodes"):
            continue
        text = ET.tostring(child, encoding="unicode")
        if _REFERENCE_PATTERN.search(text):
            w2_root.remove(child)
            dropped.append(child.tag)

    def _set_points(element: ET.Element, points: list[tuple[float, float]], *, container: str, tag: str, z: bool) -> None:
        holder = element.find(f"./geometry/{container}")
        for old in list(holder):
            holder.remove(old)
        for x, y in points:
            attrs = {"x": f"{x:.3f}", "y": f"{y:.3f}"}
            if z:
                attrs["zOffset"] = "0"
            ET.SubElement(holder, tag, attrs)

    src_links = _link_index(generated)
    for old in list(real_links):
        real_links.remove(old)
    for no, info in sorted(src_links.items(), key=lambda kv: int(kv[0])):
        is_conn = info["is_connector"]
        element = copy.deepcopy(conn_template if is_conn else link_template)
        element.set("no", no)
        element.set("name", info["name"] or "")
        _set_points(element, info["points"], container="linkPolyPts", tag="linkPolyPoint", z=True)
        lanes = element.find("./lanes")
        for old in list(lanes):
            lanes.remove(old)
        from_link = (info["from_end_pt"] or {}).get("lane", "").split()
        to_link = (info["to_end_pt"] or {}).get("lane", "").split()
        for lane_ix in range(1, max(info["lane_count"], 1) + 1):
            lane = copy.deepcopy(lane_template)
            for old in list(lane):
                lane.remove(old)
            if "width" in lane.attrib and info["lane_attrs"]:
                lane.set("width", info["lane_attrs"][0].get("width", "3.5"))
            if is_conn and len(from_link) == 2 and len(to_link) == 2:
                ET.SubElement(ET.SubElement(lane, "fromLanes"), "objectRef", {"key": f"{from_link[0]} {lane_ix}"})
                ET.SubElement(ET.SubElement(lane, "toLanes"), "objectRef", {"key": f"{to_link[0]} {lane_ix}"})
            lanes.append(lane)
        if is_conn and len(from_link) == 2 and len(to_link) == 2:
            element.find("fromLinkEndPt").attrib.clear()
            element.find("fromLinkEndPt").attrib.update(
                {"link": from_link[0], "pos": (info["from_end_pt"] or {}).get("pos", "0")}
            )
            element.find("toLinkEndPt").attrib.clear()
            element.find("toLinkEndPt").attrib.update(
                {"link": to_link[0], "pos": (info["to_end_pt"] or {}).get("pos", "0")}
            )
        real_links.append(element)

    if real_nodes is not None and node_template is not None:
        for old in list(real_nodes):
            real_nodes.remove(old)
        for node in generated.findall("./nodes/node"):
            element = copy.deepcopy(node_template)
            element.set("no", node.get("no") or "")
            element.set("name", node.get("name") or "")
            _set_points(
                element,
                [
                    (float(p.get("x", "0")), float(p.get("y", "0")))
                    for p in node.findall("./geometry/points/point")
                ],
                container="points",
                tag="point",
                z=False,
            )
            real_nodes.append(element)

    plan.append(
        {
            "label": "w2_generated_geometry_real_shapes",
            "path": str(_write_tree(w2_root, target / "w2_generated_geometry_real_shapes.inpx")),
            "note": (
                f"the emitter's own {len(src_links)} links/connectors and "
                f"{len(generated.findall('./nodes/node'))} node(s) rebuilt from real template elements "
                f"inside the real skeleton; link-referencing containers dropped: {sorted(set(dropped))}"
            ),
        }
    )
    return plan


def _run_diagnose(timeout_s: float, *, round_two: bool = False) -> dict[str, Any]:
    name = "diagnose2" if round_two else "diagnose"
    _log("")
    _log("=" * 78)
    _log(f"[{name}] why is a generated .inpx refused? (LoadNet bisect, one session)")
    _log("=" * 78)
    target = _fresh_dir(NETS_ROOT / name)
    plan = _build_diagnose2_variants(target) if round_two else _build_diagnose_variants(target)
    plan_path = target / "plan.json"
    plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    for item in plan:
        _log(f"  variant {item['label']}: {item['note']}")

    out_path = target / f"{name}_com.json"
    entry: dict[str, Any] = {"label": name, "plan": plan, "started_utc": _now()}
    _state.setdefault("runs", {})[name] = entry
    _save_state()

    entry["run"] = _bounded_child(
        ["--com-plan", str(plan_path), "--com-out", str(out_path)], out_path, timeout_s
    )
    entry["finished_utc"] = _now()
    _save_state()

    com = entry["run"].get("com", {})
    _log(f"  connected: {com.get('connected')}  version={(com.get('version') or {}).get('att_values', {}).get('Version')}")
    for v in com.get("variants", []):
        _log(f"    {v['label']:<38} load_ok={v.get('load_ok')}  links={v.get('link_count')}  ({v.get('load_seconds')}s)")
        if v.get("load_error"):
            _log(f"        {v['load_error']}")
        if v.get("err_text"):
            _log(f"        .err -> {v['err_text']}")
    _save_state()
    return entry


def _run_roundtrip(timeout_s: float) -> None:
    """Final phase: the ONE generated-geometry variant Vissim will open (the
    round-2 ``w2`` graft) taken through a full load -> SaveNetAs -> reparse, so
    the emitter's own links, connectors and node polygon are round-tripped by
    Vissim itself. A second copy has ``<vehicleCompositions>`` removed, to test
    the "saving a network with none creates a default one" landmine on a
    network Vissim will actually load."""
    source = NETS_ROOT / "diagnose2" / "w2_generated_geometry_real_shapes.inpx"
    if not source.is_file():
        _log(f"[roundtrip] SKIPPED: {source} not found -- run --only diagnose2 first")
        return
    target = _fresh_dir(NETS_ROOT / "roundtrip_inputs")
    plain = target / "rt_generated_geometry.inpx"
    shutil.copy2(source, plain)

    stripped_root = _parse(source)
    removed = []
    for child in list(stripped_root):
        if child.tag in ("vehicleCompositions", "vehicleTypes"):
            stripped_root.remove(child)
            removed.append(child.tag)
    no_comp = _write_tree(stripped_root, target / "rt_generated_no_vehcomp.inpx")
    _log(f"[roundtrip] built {plain.name} and {no_comp.name} (removed {removed})")

    _run_reference("rt_generated", plain, timeout_s)
    _run_reference("rt_no_vehcomp", no_comp, timeout_s)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="genesis_open_live_check", description=__doc__)
    parser.add_argument(
        "--only",
        choices=(
            "small",
            "murdoch",
            "both",
            "reference",
            "diagnose",
            "diagnose2",
            "roundtrip",
            "all",
        ),
        default="both",
    )
    parser.add_argument("--murdoch", default=str(DEFAULT_MURDOCH))
    parser.add_argument(
        "--label-suffix",
        default="",
        help=(
            "appended to the run label and its nets/<label>/ directory, e.g. "
            "'_v2'. Added 2026-08-06 so the emitter-v2 acceptance run does not "
            "overwrite the v1 refusal evidence this probe's first run recorded "
            "under 'small' and 'murdoch' -- a measurement is not re-runnable "
            "if re-running it destroys what it is being compared against."
        ),
    )
    parser.add_argument("--small-timeout", type=float, default=DEFAULT_SMALL_TIMEOUT_S)
    parser.add_argument("--murdoch-timeout", type=float, default=DEFAULT_MURDOCH_TIMEOUT_S)
    parser.add_argument("--reference-timeout", type=float, default=DEFAULT_REFERENCE_TIMEOUT_S)
    parser.add_argument("--diagnose-timeout", type=float, default=DEFAULT_DIAGNOSE_TIMEOUT_S)
    # Child-process flags (COM work). Not for humans.
    parser.add_argument("--com-network")
    parser.add_argument("--com-label")
    parser.add_argument("--com-plan")
    parser.add_argument("--com-out")
    args = parser.parse_args(argv)

    if args.com_network:
        return _com_phase(Path(args.com_network), args.com_label or "child", Path(args.com_out))
    if args.com_plan:
        return _com_load_many(Path(args.com_plan), Path(args.com_out))

    global _state
    _state = _load_state()
    _state["probe"] = "genesis_open_live_check"
    _state["last_run_utc"] = _now()
    _state["work_root"] = str(WORK_ROOT)
    _state["python"] = sys.executable
    _save_state()

    _log("")
    _log("#" * 78)
    _log(f"# genesis_open_live_check  {_now()}  (--only {args.only})")
    _log("#" * 78)

    NETS_ROOT.mkdir(parents=True, exist_ok=True)

    small_label = f"small{args.label_suffix}"
    murdoch_label = f"murdoch{args.label_suffix}"

    if args.only in ("small", "both", "all"):
        small_dir = _fresh_dir(NETS_ROOT / small_label)
        built = _build_small_network(small_dir)
        _state.setdefault("inputs", {})[small_label] = built
        _save_state()
        _log(f"[{small_label}] generated: {built}")
        _run_label(small_label, Path(built["inpx"]), args.small_timeout)

    if args.only in ("murdoch", "both", "all"):
        murdoch_src = Path(args.murdoch)
        if not murdoch_src.is_file():
            _log(f"[{murdoch_label}] SKIPPED: source draft not found at {murdoch_src}")
            _state.setdefault("inputs", {})[murdoch_label] = {"missing": str(murdoch_src)}
            _save_state()
        else:
            murdoch_dir = _fresh_dir(NETS_ROOT / murdoch_label)
            copied = murdoch_dir / murdoch_src.name
            shutil.copy2(murdoch_src, copied)
            _state.setdefault("inputs", {})[murdoch_label] = {
                "source": str(murdoch_src),
                "copy": str(copied),
                "bytes": copied.stat().st_size,
            }
            _save_state()
            _log(f"[{murdoch_label}] copied {murdoch_src} -> {copied} ({copied.stat().st_size} bytes)")
            _run_label(murdoch_label, copied, args.murdoch_timeout)

    if args.only in ("reference", "all"):
        for label, folder in REFERENCE_EXAMPLES.items():
            if not folder.is_dir():
                _log(f"[{label}] SKIPPED: example folder not found at {folder}")
                continue
            _run_reference(label, folder, args.reference_timeout)

    if args.only in ("diagnose", "all"):
        _run_diagnose(args.diagnose_timeout)

    if args.only in ("diagnose2", "all"):
        _run_diagnose(args.diagnose_timeout, round_two=True)

    if args.only in ("roundtrip", "all"):
        _run_roundtrip(args.reference_timeout)

    _log("")
    _log(f"state.json: {STATE_PATH}")
    _log(f"log.txt   : {LOG_PATH}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
