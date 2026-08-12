"""Measure the DANGLING LINK ENDS of a generated ``.inpx`` — is the network drivable?

Usage (run from ``backend/``, with the venv python)::

    python tools/genesis_connectivity_probe.py --inpx OUT/murdoch.inpx
    python tools/genesis_connectivity_probe.py --inpx OUT/murdoch.inpx --osm ../client-data/murdoch.osm
    python tools/genesis_connectivity_probe.py --inpx A.inpx --baseline-json before.json
    python tools/genesis_connectivity_probe.py --inpx A.inpx --json > after.json

Why this exists
---------------
A generated network can be schema-perfect, load in Vissim, and still be a
**trap**: a link whose end no connector leaves is a place where vehicles depart
the network. Vissim reports nothing — no error, no warning, no ``.err`` — the
demand simply evaporates and every downstream number is wrong by an amount
nothing measures. That failure is invisible in a rendering (the ~12 m
intersection setback makes every *correct* junction look like a gap too), so it
has to be counted rather than looked at.

What it measures, and where the classification comes from
---------------------------------------------------------
A **dangling end** is a plain link with no connector leaving it. Every one is
then attributed to a CAUSE. Causes come from topology, never from proximity:

* with ``--osm``, **from the generator itself.** This tool does not classify
  anything on that path: it regenerates the draft and reads the census the
  generator wrote into ``metadata['connectivity']['dangling_approaches']``, so
  the probe and the generator cannot disagree — not because they run similar
  code, but because there is only one classifier and the generator owns it.
  That distinction was not free. This tool used to carry its own fallback for
  the cases ``connectivity.dead_ends()`` structurally could not see, and it gave
  them confident names that appeared in no generator artifact at all: 8 of the
  Murdoch network's approaches were silent vehicle traps visible only if you
  happened to run this script. A measurement the thing being measured cannot
  reproduce is not a measurement of it;
* without ``--osm``, from the ``.inpx`` alone, using the generator's own link
  naming (``… [FWD] w<way>s<segment>``) plus the node polygons. Weaker by
  construction, and it says so.

The distinction matters and is the whole reason this tool is not a one-liner: a
FWD link end 3.5 m from its own REV link's start is what a cul-de-sac looks
like, and it is *also* what a mid-block pair looks like when the junction
connectors failed for some other reason. Those two need opposite fixes, and only
the topology tells them apart. A proximity-only classifier reported the Murdoch
network's split roundabout as a "node-merge tolerance" problem; it was a
way-segment dropped for being shorter than the setback could survive.

Exit codes: ``0`` measured, ``1`` a usage error, ``2`` the file could not be
read. Measuring never fails on a bad number — a network with 10 000 dangling
ends still exits 0, because this tool reports, it does not grade.

WHAT THIS SCRIPT TOUCHES
------------------------
The files it is given, read-only. No ``data/``, no ``runtime/``, no workspace
binding, no network access, no Vissim COM. Offline, and safe to run in CI.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_UNREADABLE = 2

#: ``draft._link_name`` writes ``"<base> [FWD|REV] w<way id>s<segment index>"``.
#: Parsed rather than re-derived so this probe reads a file the same way the
#: generator wrote it; a rename there breaks this pattern loudly, which is the
#: intent.
LINK_NAME_RE = re.compile(r"\[(FWD|REV)\]\s+w(\d+)s(\d+)$")

# --- cause codes -----------------------------------------------------------
# Stable strings. CI and the compliance report both key off them — and so does
# the generator, which is why they are DEFINED in ``services.genesis.
# connectivity`` and only re-exported here. One definition, so a rename cannot
# leave the tool and the network it measures speaking different vocabularies.
from services.genesis.connectivity import (  # noqa: E402
    BENIGN_DANGLING_CAUSES as BENIGN_CAUSES,
    DANGLING_ALL_MOVEMENTS_U as CAUSE_ALL_MOVEMENTS_U,
    DANGLING_CUL_DE_SAC as CAUSE_CUL_DE_SAC,
    DANGLING_MODEL_BOUNDARY as CAUSE_MODEL_BOUNDARY,
    DANGLING_NO_EXIT_AT_JUNCTION as CAUSE_NO_EXIT_AT_JUNCTION,
    DANGLING_ONE_WAY as CAUSE_ONE_WAY_DEAD_END,
    DANGLING_RESTRICTED as CAUSE_RESTRICTED,
    DANGLING_UNATTRIBUTED as CAUSE_UNKNOWN,
    DANGLING_UNMERGED_NEIGHBOUR as CAUSE_UNMERGED_NEIGHBOUR,
)


def _read_inpx(path: Path) -> tuple[dict, list[tuple[str, str]]]:
    """(plain links by ``no``, connector (from, to) pairs) out of an ``.inpx``.

    A connector is a ``<link>`` carrying ``<fromLinkEndPt>``/``<toLinkEndPt>``;
    everything else under ``<links>`` is a plain link. That is Vissim's own
    distinction, and ``emit.py`` writes both into the same container.
    """
    root = ET.parse(str(path)).getroot()
    container = root.find(".//links")
    plain: dict[str, dict] = {}
    connectors: list[tuple[str, str]] = []
    if container is None:
        return plain, connectors
    for link in container.findall("link"):
        no = link.get("no") or ""
        start, end = link.find("fromLinkEndPt"), link.find("toLinkEndPt")
        if start is not None and end is not None:
            connectors.append((start.get("link") or "", end.get("link") or ""))
            continue
        points = [
            (float(p.get("x", "0")), float(p.get("y", "0")))
            for p in link.iter("linkPolyPoint")
        ]
        if len(points) < 2:
            continue
        name = link.get("name", "")
        match = LINK_NAME_RE.search(name)
        plain[no] = {
            "name": name,
            "start": points[0],
            "end": points[-1],
            "way_id": int(match.group(2)) if match else None,
            "segment_index": int(match.group(3)) if match else None,
            "forward": match.group(1) == "FWD" if match else None,
        }
    return plain, connectors


def _requested_bbox(osm: Path) -> tuple[float, float, float, float] | None:
    """The bbox the ``.osm`` was fetched for, read out of the file's own bytes.

    The generator reads this (``genesis_jobs._requested_bbox_of``) and it MOVES
    the model boundary, so a probe that did not would rebuild a different
    network from the same file and report its numbers under this file's name —
    the split brain this tool's docstring forbids. Same function, same seam.

    Function-local import: ``services.osm_fetch`` is a workspace-aware module
    and this tool touches no workspace, so it is pulled in only on the path that
    actually needs the parser, and only to run a pure bytes → dict function.
    """
    from services.osm_fetch import read_provenance  # noqa: PLC0415
    from services.genesis.osm import requested_bbox_from_provenance  # noqa: PLC0415

    try:
        return requested_bbox_from_provenance(read_provenance(osm.read_bytes()))
    except OSError:  # pragma: no cover - the parse below reports the same file
        return None


def _classify_with_osm(inpx: Path, osm: Path, *, use_provenance_bbox: bool = True) -> dict:
    """Read the generator's own dangling-end census off a regenerated draft.

    No classification happens here. ``draft.build_draft`` writes every link no
    connector leaves into ``metadata['connectivity']['dangling_approaches']``
    with the cause IT decided, and this function groups that list. The tool's
    job is to run the generator and report; deciding what a dead end is belongs
    to the module that built the network.
    """
    from services.genesis.draft import build_draft  # noqa: PLC0415
    from services.genesis.osm import parse_osm_file  # noqa: PLC0415

    bbox = _requested_bbox(osm) if use_provenance_bbox else None
    extract = parse_osm_file(osm, requested_bbox=bbox)
    if extract.refused:
        return {"error": "; ".join(f"{r.code}: {r.message}" for r in extract.refusals)}
    draft = build_draft(extract, name=inpx.stem)
    if draft.refused:
        return {"error": "; ".join(draft.refusals)}

    connectivity = draft.metadata.get("connectivity", {})
    buckets: dict[str, list[str]] = defaultdict(list)
    for entry in connectivity.get("dangling_approaches", []):
        buckets[entry.get("cause", CAUSE_UNKNOWN)].append(
            f"link {entry.get('link_id')} {entry.get('link', '')}"
        )

    # The .osm path attributes causes by REBUILDING the draft, so it is only
    # describing the given file if the two agree. Say so when they do not,
    # rather than reporting a rebuild's numbers under the file's name.
    file_links, file_connectors = _read_inpx(inpx)
    mismatch = (
        None
        if (len(file_links), len(file_connectors)) == (len(draft.links), len(draft.connectors))
        else (
            f"the .inpx holds {len(file_links)} links / {len(file_connectors)} connectors but "
            f"regenerating from {osm.name} gives {len(draft.links)} / {len(draft.connectors)} — "
            "the causes below describe the REGENERATED network, not this file. Regenerate it, "
            "or drop --osm for a file-only measurement."
        )
    )

    return {
        "source": (
            "the generator's own census "
            "(draft.metadata['connectivity']['dangling_approaches'])"
        ),
        "requested_bbox": list(bbox) if bbox else None,
        "boundary": draft.metadata.get("boundary", {}).get("source", ""),
        "file_matches_regeneration": mismatch is None,
        **({"warning": mismatch} if mismatch else {}),
        "links": len(draft.links),
        "connectors": len(draft.connectors),
        "nodes": len(draft.nodes),
        "dangling_ends": sum(len(v) for v in buckets.values()),
        "by_cause": {k: len(v) for k, v in sorted(buckets.items())},
        "examples": {k: sorted(v)[:6] for k, v in sorted(buckets.items())},
        "connectivity": connectivity,
    }


def _classify_from_inpx(path: Path) -> dict:
    """Attribute every dangling end from the ``.inpx`` alone.

    Weaker than the ``--osm`` path by construction and says so: without the OSM
    topology this cannot tell a cul-de-sac from a junction whose connectors
    failed, so it reports the pairing it CAN see — a link end whose own
    opposite-direction twin starts nearby is a cul-de-sac candidate — and marks
    everything else unattributed rather than guessing a cause.
    """
    plain, connectors = _read_inpx(path)
    if not plain:
        return {"error": f"{path.name}: no plain links found"}
    leaving = {frm for frm, _to in connectors}

    starts_by_segment: dict[tuple[int, int], list[str]] = defaultdict(list)
    for no, data in plain.items():
        if data["way_id"] is not None:
            starts_by_segment[(data["way_id"], data["segment_index"])].append(no)

    xs = [d["start"][0] for d in plain.values()] + [d["end"][0] for d in plain.values()]
    ys = [d["start"][1] for d in plain.values()] + [d["end"][1] for d in plain.values()]
    minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)

    buckets: dict[str, list[str]] = defaultdict(list)
    for no, data in sorted(plain.items(), key=lambda kv: int(kv[0])):
        if no in leaving:
            continue
        label = f"link {no} {data['name']}"
        x, y = data["end"]
        if min(x - minx, maxx - x, y - miny, maxy - y) <= 1.0:
            buckets[CAUSE_MODEL_BOUNDARY].append(label)
            continue
        twin = [
            other
            for other in starts_by_segment.get(
                (data["way_id"], data["segment_index"]), []
            )
            if other != no and plain[other]["forward"] is not data["forward"]
        ]
        if twin and min(math.dist(data["end"], plain[t]["start"]) for t in twin) <= 30.0:
            buckets[CAUSE_CUL_DE_SAC].append(label)
        else:
            buckets[CAUSE_UNKNOWN].append(label)

    return {
        "source": ".inpx only (no --osm: causes are weaker, see the module docstring)",
        "links": len(plain),
        "connectors": len(connectors),
        "dangling_ends": sum(len(v) for v in buckets.values()),
        "by_cause": {k: len(v) for k, v in sorted(buckets.items())},
        "examples": {k: v[:6] for k, v in sorted(buckets.items())},
    }


def render(result: dict, baseline: dict | None = None) -> str:
    if "error" in result:
        return f"REFUSED: {result['error']}"
    lines = [f"source          : {result['source']}"]
    if result.get("boundary"):
        lines.append(f"model boundary  : {result['boundary']}")
    if result.get("requested_bbox"):
        lines.append(f"requested bbox  : {result['requested_bbox']}")
    if result.get("warning"):
        lines.append(f"WARNING         : {result['warning']}")
    lines += [
        f"plain links     : {result['links']}",
        f"connectors      : {result['connectors']}",
    ]
    if "nodes" in result:
        lines.append(f"nodes           : {result['nodes']}")
    lines.append(f"DANGLING ENDS   : {result['dangling_ends']}")
    benign = sum(
        count for cause, count in result["by_cause"].items() if cause in BENIGN_CAUSES
    )
    lines.append(
        f"  of which correct (model boundary / banned movement): {benign}"
    )
    lines.append("")
    base_causes = (baseline or {}).get("by_cause", {})
    width = max([len(c) for c in result["by_cause"]] + [len(c) for c in base_causes] + [5])
    header = f"{'cause':<{width}}  {'count':>7}"
    if baseline:
        header += f"  {'before':>7}  {'delta':>7}"
    lines.append(header)
    lines.append("-" * len(header))
    for cause in sorted(set(result["by_cause"]) | set(base_causes)):
        count = result["by_cause"].get(cause, 0)
        row = f"{cause:<{width}}  {count:>7}"
        if baseline:
            before = base_causes.get(cause, 0)
            row += f"  {before:>7}  {count - before:>+7}"
        lines.append(row)
    if baseline:
        lines.append("-" * len(header))
        lines.append(
            f"{'TOTAL':<{width}}  {result['dangling_ends']:>7}  "
            f"{baseline.get('dangling_ends', 0):>7}  "
            f"{result['dangling_ends'] - baseline.get('dangling_ends', 0):>+7}"
        )
    for cause, examples in result.get("examples", {}).items():
        if not examples:
            continue
        lines.append("")
        lines.append(f"{cause}:")
        lines.extend(f"    {e[:110]}" for e in examples)
    connectivity = result.get("connectivity")
    if connectivity:
        lines.append("")
        lines.append("connectivity pass (from the generator itself):")
        merge = connectivity.get("node_merge", {})
        lines.append(
            f"    node merge  : {len(merge.get('clusters', []))} junction(s) from "
            f"{merge.get('nodes_merged', 0)} node(s); "
            f"{len(merge.get('refusals', []))} refused"
        )
        lines.append(
            f"    U-turns     : {connectivity.get('u_turn_connectors_emitted', 0)} emitted, "
            f"{len(connectivity.get('u_turns_unbuildable', []))} unbuildable"
        )
        for reason, count in sorted(
            connectivity.get("dead_ends_by_reason", {}).items()
        ):
            lines.append(f"    dead end    : {reason} = {count}")
        lines.append(
            f"    restrictions: {connectivity.get('osm_turn_restrictions_parsed', 0)} parsed, "
            f"{connectivity.get('osm_turn_restrictions_enforced', 0)} ENFORCED "
            f"(a movement was actually removed), "
            f"{len(connectivity.get('osm_turn_restrictions_inert', []))} inert, "
            f"{len(connectivity.get('osm_turn_restrictions_refused', []))} not applied"
        )
    return "\n".join(lines)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="genesis_connectivity_probe",
        description=(
            "Count and attribute the dangling link ends of a generated .inpx — the "
            "places a vehicle silently leaves the network. Offline; no Vissim, no COM."
        ),
    )
    parser.add_argument("--inpx", required=True, help="path to a generated .inpx")
    parser.add_argument(
        "--osm",
        default=None,
        help=(
            "the .osm it was generated from. Strongly recommended: without it the "
            "causes are guessed from link names and geometry alone."
        ),
    )
    parser.add_argument(
        "--ignore-requested-bbox",
        action="store_true",
        help=(
            "rebuild WITHOUT the bbox recorded in the .osm's fetch provenance. Off by "
            "default because the generator uses it: this flag is for measuring what the "
            "boundary rule changed, not for ordinary use."
        ),
    )
    parser.add_argument(
        "--json", action="store_true", help="emit the measurement as JSON on stdout"
    )
    parser.add_argument(
        "--baseline-json",
        default=None,
        help="a previous --json run to diff against (prints a before/after table)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    # UTF-8 explicitly, not just errors="replace". `--json` writes
    # ensure_ascii=False, so the payload carries the em dashes that live in
    # TOLERANCE_BASIS and the refusal details. Those are cp1252-encodable, so on
    # this project's Windows dev box `errors="replace"` never fired and the
    # redirected file came out as valid JSON in cp1252 — which
    # `json.load(open(path, encoding="utf-8"))` cannot read. A report that only
    # parses on the machine that wrote it is not a report; measured on 2026-08-10
    # as `UnicodeDecodeError: 'utf-8' codec can't decode byte 0x97`.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):  # pragma: no cover - detached stream
                pass

    inpx = Path(args.inpx)
    if not inpx.is_file():
        print(f"no such .inpx: {inpx}", file=sys.stderr)
        return EXIT_UNREADABLE
    try:
        if args.osm:
            osm = Path(args.osm)
            if not osm.is_file():
                print(f"no such .osm: {osm}", file=sys.stderr)
                return EXIT_UNREADABLE
            result = _classify_with_osm(
                inpx, osm, use_provenance_bbox=not args.ignore_requested_bbox
            )
        else:
            result = _classify_from_inpx(inpx)
    except ET.ParseError as exc:
        print(f"{inpx.name}: not well-formed XML — {exc}", file=sys.stderr)
        return EXIT_UNREADABLE

    baseline = None
    if args.baseline_json:
        baseline_path = Path(args.baseline_json)
        if not baseline_path.is_file():
            print(f"no such baseline json: {baseline_path}", file=sys.stderr)
            return EXIT_UNREADABLE
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))

    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False))
    else:
        print(render(result, baseline))
    return EXIT_UNREADABLE if "error" in result else EXIT_OK


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
