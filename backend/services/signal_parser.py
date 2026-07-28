"""Parse VISSIM signal controller timing and infer phase movements."""
from __future__ import annotations

import base64
import math
import os
import xml.etree.ElementTree as ET
from functools import lru_cache
from pathlib import Path
from typing import TypedDict

CARDINAL_DIRECTIONS = ("north", "east", "south", "west")

# ---------------------------------------------------------------------------
# Machine-readable reason codes (Murdoch Drive finding, 2026-07-28)
#
# A read that returns an empty list, or a number nobody could operate a real
# intersection with, must SAY WHY in a form the answer layer can branch on --
# never a silent blank and never an unqualified figure. Every code below is
# attached from directly-read XML evidence, never inferred.
# ---------------------------------------------------------------------------

#: ``sg_splits`` is empty because the program is STAGE-BASED (Vissig
#: ``<stageProgs>``): the program stores a stage/interstage sequence, not
#: per-signal-group green windows, so there is no green window to report.
SG_SPLITS_STAGE_BASED = "STAGE_BASED_PROGRAM"

#: ``programs`` is empty because the controller carries no ``intSupplyData``.
PROGRAMS_NO_BLOB = "NO_INTSUPPLYDATA_BLOB"
#: ``programs`` is empty because the base64 blob would not decode/parse.
PROGRAMS_BLOB_UNREADABLE = "BLOB_NOT_DECODABLE"
#: ``programs`` is empty because the blob decoded fine but holds neither a
#: ``<prog>`` nor a ``<stageProg>`` -- a controller with no program at all.
PROGRAMS_BLOB_EMPTY = "BLOB_HAS_NO_PROGRAMS"

#: Program-level data-quality flags. These FLAG, they never refuse, alter or
#: hide a value: the number stays exactly as the model stores it and the code
#: tells the answer layer the figure needs qualifying rather than quoting.
DQ_CYCLE_ABOVE_TYPICAL_MAX = "CYCLE_ABOVE_TYPICAL_MAX"
DQ_CYCLE_BELOW_TYPICAL_MIN = "CYCLE_BELOW_TYPICAL_MIN"
DQ_NEAR_PERMANENT_GREEN = "NEAR_PERMANENT_GREEN"
DQ_SINGLE_SIGNAL_GROUP = "SINGLE_SIGNAL_GROUP_PROGRAM"

#: Controller-level flag: the controller exists and carries programs, but NO
#: ``<signalHead>`` in the network references it, so nothing it does can
#: affect a single vehicle. Editing its timings is a guaranteed no-op --
#: exactly the silent failure this project exists to surface.
CONTROLLER_NO_SIGNAL_HEADS = "NO_SIGNAL_HEADS"

#: Plausibility band for an urban fixed-time cycle. Deliberately generous:
#: the point is to catch a stub/placeholder (Murdoch SC 9's 375 s), not to
#: second-guess an engineer. The longest genuine program in the Murdoch
#: production model is 159 s; PTV's own example networks top out at 130 s.
_TYPICAL_CYCLE_MAX_S = 240.0
_TYPICAL_CYCLE_MIN_S = 20.0
#: A single signal group holding green for >= this share of the cycle is not
#: an operating plan -- Murdoch SC 9 program 11 holds 357 s of a 375 s cycle.
_NEAR_PERMANENT_GREEN_RATIO = 0.9

# Standard Korean 8-SG convention: SG1-8 = N(1-2), E(3-4), S(5-6), W(7-8)
_STANDARD_4WAY_MOVEMENTS: dict[str, tuple[str, str, str]] = {
    "1": ("north", "south", "straight"),
    "2": ("north", "east",  "left"),
    "3": ("east",  "west",  "straight"),
    "4": ("east",  "south", "left"),
    "5": ("south", "north", "straight"),
    "6": ("south", "west",  "left"),
    "7": ("west",  "east",  "straight"),
    "8": ("west",  "north", "left"),
}


class PhaseMovement(TypedDict):
    from_direction: str
    to_direction: str
    turn: str


class PhaseInfo(TypedDict):
    start: int     # seconds from cycle start
    duration: int  # effective green duration in seconds
    signal_groups: list[str]
    signal_group_ids: list[str]
    movements: list[PhaseMovement]


class SignalInfo(TypedDict):
    sc_no: str
    sc_name: str
    cycle_time: int          # seconds; 0 = variable / unknown
    controller_type: str     # <signalController type="..."> verbatim (e.g. "FIXEDTIME",
                              # "FIXEDTIMESIMPLE", "VAP", "RINGBARRIERCONTROLLER",
                              # "RAILWAYCROSSING", "PEDESTRIANCROSSING",
                              # "TWOSTAGECONTROLLER"); "unknown" when the attribute is
                              # genuinely absent/blank (never guessed/inferred).
    num_signal_groups: int
    num_phases: int
    phases: list[PhaseInfo]


class ConnectorDef(TypedDict):
    to_link: str
    lane_pairs: list[tuple[int | None, int | None]]


class SignalTopology(TypedDict):
    sg_lanes: dict[str, dict[str, list[tuple[str, int]]]]
    connectors_by_from: dict[str, list[ConnectorDef]]
    link_points: dict[str, list[tuple[float, float]]]


def _parse_one_sc(
    sc_el: ET.Element,
    *,
    sg_names: dict[str, str],
    sg_head_counts: dict[str, int],
) -> SignalInfo | None:
    no = sc_el.get("no", "")
    name = sc_el.get("name", "")
    isd = sc_el.get("intSupplyData", "")
    # Empirically verified against all 25 real PTV Vissim 2026 "Examples Demo"
    # networks (2026-07-24): <signalController>'s own "type" attribute carries
    # the controller family verbatim -- FIXEDTIME/FIXEDTIMESIMPLE (COM-writable
    # per the M2.1 CycTm finding), VAP/RINGBARRIERCONTROLLER/RAILWAYCROSSING/
    # PEDESTRIANCROSSING/TWOSTAGECONTROLLER (program logic Vissim's COM API
    # cannot safely rewrite via CycTm/Offset -- see constraints.py #4). Never
    # inferred/guessed: a blank or missing attribute becomes the literal
    # string "unknown", which constraints.py #4 already refuses on.
    controller_type = (sc_el.get("type") or "").strip() or "unknown"
    if not isd:
        # Empirically verified (2026-07-24) against all 25 real example
        # networks: intSupplyData is populated ONLY on type="FIXEDTIME"
        # controllers. Every other real type -- including FIXEDTIMESIMPLE
        # (Air Taxi Vertiport) -- carries a real "type" attribute but NO
        # intSupplyData at all; dropping those entirely (the old behavior)
        # would make FIXEDTIMESIMPLE structurally invisible to the shadow
        # model, defeating the M2.1 finding that its cycle/offset ARE
        # COM-writable. Return a type-only, timing-blind SignalInfo instead
        # of None whenever the element carries a real "type" (existence +
        # controller family is still genuine, verifiable data) -- but keep
        # returning None when BOTH isd and type are absent (the synthetic
        # demo .inpx's <signalController no="1" name="..."/> shape, which
        # must stay invisible per test_agent_read_tools.py's pinned
        # empty-shadow-signals contract).
        if not sc_el.get("type"):
            return None
        return SignalInfo(
            sc_no=no,
            sc_name=name,
            cycle_time=0,
            controller_type=controller_type,
            num_signal_groups=0,
            num_phases=0,
            phases=[],
        )

    try:
        inner = ET.fromstring(base64.b64decode(isd).decode("utf-8"))
    except Exception:
        return None

    sgs_el = inner.find("sgs")
    num_sgs = len(list(sgs_el)) if sgs_el is not None else 0

    # Fallback for stage-based controllers (no <progs> blocks).
    stage_cycle_s, stage_phases = _parse_stage_program_phases(
        inner=inner,
        sg_names=sg_names,
        sg_head_counts=sg_head_counts,
        active_prog_no=(sc_el.get("progNo") or "").strip() or None,
    )

    progs_el = inner.find("progs")
    if progs_el is None or len(list(progs_el)) == 0:
        return SignalInfo(
            sc_no=no,
            sc_name=name,
            cycle_time=stage_cycle_s,
            controller_type=controller_type,
            num_signal_groups=num_sgs,
            num_phases=len(stage_phases),
            phases=stage_phases,
        )

    # The program Vissim RUNS is the one ``progNo`` selects, not the first in
    # the blob (Urban Intersection Beijing.CN: progNo="2" of progs {1, 2}, and
    # the live SimBreakAt/SigState oracle in backend/tools/
    # probe_signal_programs.py follows program 2). Reading progs[0] reported a
    # plan the simulation never runs -- the same falsehood the stage path
    # carried, closed here at the same time (chip task_eeca6837, 2026-07-28).
    # Falls back to the first program when progNo names nothing, so a
    # malformed progNo degrades to the old behavior rather than to no phases.
    progs = list(progs_el)
    active_prog_no = (sc_el.get("progNo") or "").strip()
    prog = next((p for p in progs if (p.get("id") or "").strip() == active_prog_no), progs[0])
    cycle_ms = int(prog.get("cycletime", 0))
    cycle_s = cycle_ms // 1000

    # green_start_s -> {"duration": int, "groups": set[str], "group_ids": set[str]}
    green_windows: dict[int, dict[str, object]] = {}

    prog_sgs = prog.find("sgs")
    if prog_sgs is not None:
        for sg_index, sg_in_prog in enumerate(list(prog_sgs), start=1):
            cmds_el = sg_in_prog.find("cmds")
            if cmds_el is None:
                continue

            green_start: int | None = None
            red_start: int | None = None
            sg_no = (
                sg_in_prog.get("sg_id")
                or sg_in_prog.get("sg")
                or sg_in_prog.get("no")
                or str(sg_index)
            ).strip()
            if not sg_no:
                continue

            sg_name = sg_names.get(sg_no) or sg_in_prog.get("name") or f"SG{sg_no}"
            head_count = sg_head_counts.get(sg_no)
            sg_label = f"{sg_name} ({head_count} heads)" if head_count else sg_name
            for cmd in list(cmds_el):
                d = cmd.get("display")
                b = int(cmd.get("begin", 0)) // 1000
                if d == "3":
                    green_start = b
                elif d == "1":
                    red_start = b

            # Amber duration
            amber_s = 3
            fs_el = sg_in_prog.find("fixedstates")
            if fs_el is not None:
                for fs in list(fs_el):
                    if fs.get("display") == "4":
                        amber_s = int(fs.get("duration", 3000)) // 1000

            if green_start is not None and red_start is not None:
                dur = max(0, red_start - green_start - amber_s)
                if dur <= 0:
                    continue

                if green_start not in green_windows:
                    green_windows[green_start] = {
                        "duration": dur,
                        "groups": {sg_label},
                        "group_ids": {sg_no},
                    }
                    continue

                existing = green_windows[green_start]
                existing["duration"] = max(int(existing["duration"]), dur)
                existing_groups = existing["groups"]
                if isinstance(existing_groups, set):
                    existing_groups.add(sg_label)
                existing_ids = existing["group_ids"]
                if isinstance(existing_ids, set):
                    existing_ids.add(sg_no)

    # Cluster nearby start times (within 5 s) into phases
    sorted_starts = sorted(green_windows.keys())
    phases: list[PhaseInfo] = []
    visited: set[int] = set()

    for gs in sorted_starts:
        if gs in visited:
            continue
        cluster = [s for s in sorted_starts if abs(s - gs) <= 5]
        for s in cluster:
            visited.add(s)
        best_start = min(cluster)
        best_dur = max(int(green_windows[s]["duration"]) for s in cluster)
        group_names: set[str] = set()
        group_ids: set[str] = set()
        for s in cluster:
            groups = green_windows[s]["groups"]
            if isinstance(groups, set):
                group_names.update(str(item) for item in groups if item)
            ids = green_windows[s]["group_ids"]
            if isinstance(ids, set):
                group_ids.update(str(item) for item in ids if item)
        if best_dur > 0:
            phases.append(
                PhaseInfo(
                    start=best_start,
                    duration=best_dur,
                    signal_groups=sorted(group_names),
                    signal_group_ids=sorted(group_ids),
                    movements=[],
                )
            )

    return SignalInfo(
        sc_no=no,
        sc_name=name,
        cycle_time=cycle_s,
        controller_type=controller_type,
        num_signal_groups=num_sgs,
        num_phases=len(phases),
        phases=sorted(phases, key=lambda p: p["start"]),
    )


def parse_signal_programs(inpx_path: Path) -> list[dict[str, object]]:
    """Per-controller fixed-time signal PROGRAM data — the green splits.

    v2a unit ⑨ (2026-07-27). This is a SEPARATE read path from
    :func:`parse_signal_controllers` on purpose: that function's ``phases``
    output reads ``progs[0]`` (the FIRST program in the blob), while the
    program Vissim actually RUNS is selected by the outer element's
    ``progNo`` attribute (verified live on Urban Intersection Beijing.CN:
    ``progNo="2"`` of programs {1, 2}, and the SimBreakAt/SigState behavior
    oracle in ``backend/tools/probe_signal_programs.py`` follows program 2,
    not program 1). The existing ``phases`` shape is pinned by many
    dashboard/agent tests and is left byte-untouched; THIS function reports
    every program with the active one marked, so green-split reads/writes
    can never silently describe the wrong program.

    Storage reality (static scan of all 25 real Vissim 2026 example
    networks + the live probe):

    - ``type="FIXEDTIME"`` (Vissig): the ENTIRE program lives in the base64
      ``intSupplyData`` blob — decoded, ``<progs><prog id cycletime(ms)>``
      → ``<sgs><sg sg_id><cmds><cmd display begin(ms)>``; display 3 = the
      "switch to green" command, display 1 = the "switch to red" command,
      ``<fixedstates>`` carry amber (display 4) / red-amber (display 2)
      durations. ``progFile``/``supplyFile1..3`` are EMPTY on every real
      FIXEDTIME controller — the program is always embedded, never an
      external file (external files are the VAP/RBC family, refused by
      design elsewhere).
    - ``type="FIXEDTIMESIMPLE"``: no blob; the green window lives as plain
      ``<signalGroup>`` attributes (``endRed`` = green start, ``endGreen``
      = green end, plus ``amber``/``redAmber``/``minGreen``), cycle on the
      controller's own ``cycTm``. Reported here as ONE synthesized program
      (``prog_no`` = the outer ``progNo``, "1" on every real example).
    - ``type="FIXEDTIME"`` (Vissig) with an EMPTY ``<progs/>`` and a
      populated ``<stageProgs>``: a STAGE-BASED program. Found first on the
      real Murdoch Drive (Perth) production model, 2026-07-28 — 5 of its 6
      controllers are this shape and NOT ONE of the 25 PTV example networks
      is, which is why unit ⑨ never saw it. The program is a cycle-long
      sequence of STAGES (``<stages>``, each naming which signal groups are
      ON) joined by INTERSTAGES (``<interstageProgs>``, each with explicit
      ``fromStage``/``toStage``); ``<stageProg>`` lists only the transition
      instants as ``<interstage display="..." begin="...(ms)"/>`` where
      ``display`` is the **interstageProg id, NOT a stage id** (proven: all
      28 Murdoch stage programs form a closed, chain-consistent stage cycle
      under that reading, and several ``display`` values exceed the
      controller's stage count entirely). Reported here as one program per
      ``<stageProg>`` with ``program_kind="stage_based"``, a resolved
      ``stage_sequence``, and an EXPLICITLY LABELLED empty ``sg_splits``
      (``sg_splits_unavailable_reason = "STAGE_BASED_PROGRAM"``) — there is
      no per-signal-group green window stored to report, and the realized
      interstage lengths are not statically derivable (``<sc
      interstagesUsingMinDurations="true">`` compresses them: Murdoch SC 6
      program 11 has a nominally 16 s interstage between transitions only
      6 s apart). Green windows are therefore NOT synthesized for this
      family — a labelled gap beats a plausible-looking guess.
    - every other controller type: ``programs`` is empty (external or
      non-fixed-time program logic — nothing verifiable to report).

    A controller whose ``programs`` list is empty always carries a
    ``programs_unavailable_reason`` (see the ``PROGRAMS_*`` codes above), and
    every program carries a ``data_quality`` list of flags for figures that
    must not be quoted unqualified (see the ``DQ_*`` codes). Both are
    machine-readable on purpose: the answer layer branches on the code, never
    on prose.

    Each program's ``sg_splits`` entry: ``{sg_no, sg_name, green_start_s,
    green_end_s, amber_s, red_amber_s, min_green_s, head_count, simple}``.
    ``green_start_s`` is when the signal group switches to green;
    ``green_end_s`` is when it switches to red (any amber runs immediately
    before that point per the program's fixed states). A WRAPPING window
    (``green_start_s > green_end_s``, green spanning the cycle boundary) is
    real and valid — Beijing program 1 SG 1 wraps. ``simple`` is False when
    the SG's command list is anything other than exactly one green + one red
    command (multi-window programs exist in principle); such SGs report
    ``green_start_s``/``green_end_s`` as None and are refused by the write
    path rather than guessed at.
    """
    root = ET.parse(inpx_path).getroot()
    head_index = _build_signal_head_index(root)
    result: list[dict[str, object]] = []
    for sc_el in root.findall(".//signalController"):
        sc_no = (sc_el.get("no") or "").strip()
        if not sc_no:
            continue
        controller_type = (sc_el.get("type") or "").strip() or "unknown"
        sg_names = _build_signal_group_name_map(sc_el)
        head_counts = head_index.get(sc_no, {})
        active_prog_no = (sc_el.get("progNo") or "").strip() or None

        programs: list[dict[str, object]] = []
        unavailable_reason: str | None = None
        signal_groups: list[dict[str, object]] | None = None
        if controller_type == "FIXEDTIME":
            programs, unavailable_reason, signal_groups = _parse_fixedtime_programs(
                sc_el, sg_names, head_counts
            )
        elif controller_type == "FIXEDTIMESIMPLE":
            programs = _parse_fixedtimesimple_program(sc_el, sg_names, head_counts, active_prog_no)

        entry: dict[str, object] = {
            "sc_no": sc_no,
            "sc_name": (sc_el.get("name") or "").strip(),
            "controller_type": controller_type,
            "active_program_no": active_prog_no,
            "programs": programs,
        }
        if not programs and unavailable_reason:
            entry["programs_unavailable_reason"] = unavailable_reason
        if signal_groups is not None:
            # Only stage-based controllers get a roster: for <prog> families
            # the same facts already ride on every sg_splits entry, and
            # duplicating them would bloat a deliberately bounded read.
            entry["signal_groups"] = signal_groups
        # A controller no <signalHead> points at cannot influence a single
        # vehicle; say so rather than letting a planner "fix" its timings.
        if programs and not head_counts:
            entry["controller_flags"] = [CONTROLLER_NO_SIGNAL_HEADS]
        result.append(entry)
    return result


def _parse_fixedtime_programs(
    sc_el: ET.Element,
    sg_names: dict[str, str],
    head_counts: dict[str, int],
) -> tuple[list[dict[str, object]], str | None, list[dict[str, object]] | None]:
    """Dispatch a FIXEDTIME controller's blob to whichever program family it
    actually uses. Returns ``(programs, unavailable_reason, sg_roster)``;
    ``sg_roster`` is non-None only for the stage-based family."""
    isd = sc_el.get("intSupplyData", "")
    if not isd:
        return [], PROGRAMS_NO_BLOB, None
    try:
        inner = ET.fromstring(base64.b64decode(isd).decode("utf-8"))
    except Exception:
        return [], PROGRAMS_BLOB_UNREADABLE, None

    progs_el = inner.find("progs")
    if progs_el is not None and len(list(progs_el)) > 0:
        return _parse_fixedtime_blob_programs(inner, sg_names, head_counts), None, None

    stage_progs_el = inner.find("stageProgs")
    if stage_progs_el is not None and len(list(stage_progs_el)) > 0:
        programs, roster = _parse_stage_blob_programs(inner, sc_el, sg_names, head_counts)
        return programs, None, roster

    return [], PROGRAMS_BLOB_EMPTY, None


def _stage_index(inner: ET.Element) -> dict[str, dict[str, object]]:
    """``{stage_id: {"name", "sg_ids"}}`` from the blob's ``<stages>``.

    ``sg_ids`` are the signal groups the stage holds GREEN — the activations
    marked ``ON``; ``OFF`` ones are listed explicitly in the XML and are
    dropped here."""
    stages_el = inner.find("stages")
    index: dict[str, dict[str, object]] = {}
    for stage in list(stages_el) if stages_el is not None else []:
        stage_id = (stage.get("id") or "").strip()
        if not stage_id:
            continue
        activations = stage.find("activations")
        sg_ids = [
            (a.get("sg_id") or "").strip()
            for a in (list(activations) if activations is not None else [])
            if (a.get("activation") or "").upper() == "ON" and (a.get("sg_id") or "").strip()
        ]
        index[stage_id] = {"name": (stage.get("name") or "").strip() or stage_id, "sg_ids": sg_ids}
    return index


def _interstage_index(inner: ET.Element) -> dict[str, tuple[str, str]]:
    """``{interstageProg_id: (fromStage, toStage)}``.

    THE landmine of the stage-based family: a ``<stageProg>``'s
    ``<interstage display="N">`` names the INTERSTAGE PROGRAM ``N``, not
    stage ``N``. Reading ``display`` as a stage id silently mis-attributes
    every phase to the wrong signal groups (and invents phases with no
    signal groups at all when ``N`` exceeds the stage count) -- which is
    exactly what this parser did to the Murdoch model before 2026-07-28."""
    inter_el = inner.find("interstageProgs")
    index: dict[str, tuple[str, str]] = {}
    for item in list(inter_el) if inter_el is not None else []:
        item_id = (item.get("id") or "").strip()
        from_stage = (item.get("fromStage") or "").strip()
        to_stage = (item.get("toStage") or "").strip()
        if item_id and from_stage and to_stage:
            index[item_id] = (from_stage, to_stage)
    return index


def _stage_transitions(
    stage_prog: ET.Element, interstages: dict[str, tuple[str, str]]
) -> list[tuple[int, str, str]]:
    """``[(begin_ms, from_stage_id, to_stage_id), ...]`` in cycle order.

    Entries whose ``display`` resolves to no known interstage program are
    dropped rather than guessed at."""
    interstages_el = stage_prog.find("interstages")
    out: list[tuple[int, str, str]] = []
    for item in list(interstages_el) if interstages_el is not None else []:
        ref = (item.get("display") or "").strip()
        pair = interstages.get(ref)
        if pair is None:
            continue
        try:
            begin_ms = int(item.get("begin", "0"))
        except (TypeError, ValueError):
            continue
        out.append((max(0, begin_ms), pair[0], pair[1]))
    out.sort(key=lambda row: row[0])
    return out


def _parse_stage_blob_programs(
    inner: ET.Element,
    sc_el: ET.Element,
    sg_names: dict[str, str],
    head_counts: dict[str, int],
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """Every ``<stageProg>`` as a program, plus the controller's signal-group
    roster.

    Only directly-read facts are emitted: the program's own cycle time and
    offset, and the resolved stage sequence (transition instant + the stage
    that takes over + which signal groups that stage holds green). Per-signal
    -group green windows are NOT synthesized -- see this module's
    ``parse_signal_programs`` docstring for why they are not statically
    derivable -- so ``sg_splits`` stays empty and says why."""
    stages = _stage_index(inner)
    interstages = _interstage_index(inner)

    programs: list[dict[str, object]] = []
    stage_progs_el = inner.find("stageProgs")
    for stage_prog in list(stage_progs_el) if stage_progs_el is not None else []:
        try:
            cycle_s: float | None = int(stage_prog.get("cycletime", "0")) / 1000.0
        except (TypeError, ValueError):
            cycle_s = None
        try:
            offset_s: float | None = int(stage_prog.get("offset", "0")) / 1000.0
        except (TypeError, ValueError):
            offset_s = None

        sequence: list[dict[str, object]] = []
        for begin_ms, from_stage, to_stage in _stage_transitions(stage_prog, interstages):
            target = stages.get(to_stage, {"name": to_stage, "sg_ids": []})
            source = stages.get(from_stage, {"name": from_stage, "sg_ids": []})
            sg_ids = [str(x) for x in target["sg_ids"]]  # type: ignore[index]
            sequence.append(
                {
                    "transition_at_s": begin_ms / 1000.0,
                    "from_stage": str(source["name"]),
                    "to_stage": str(target["name"]),
                    # Ids only: the controller-level ``signal_groups`` roster
                    # already carries every group's name/heads/min-green once,
                    # and a stage program can have 6+ steps x 8 programs --
                    # repeating names per step is pure output bloat (the
                    # bounded-output discipline unit ⑨ set).
                    "signal_groups": sg_ids,
                }
            )

        programs.append(
            {
                "prog_no": (stage_prog.get("id") or "").strip(),
                "name": (stage_prog.get("name") or "").strip(),
                "cycle_s": cycle_s,
                "offset_s": offset_s,
                "program_kind": "stage_based",
                "sg_splits": [],
                "sg_splits_unavailable_reason": SG_SPLITS_STAGE_BASED,
                "stage_sequence": sequence,
                "data_quality": _program_data_quality(cycle_s, []),
            }
        )

    # Roster: reported ONCE per controller (not once per program) because
    # these attributes live on the controller's own <signalGroup> elements
    # and are shared by every program.
    stages_by_sg: dict[str, list[str]] = {}
    for stage in stages.values():
        for sg_id in stage["sg_ids"]:  # type: ignore[index]
            stages_by_sg.setdefault(str(sg_id), []).append(str(stage["name"]))

    roster: list[dict[str, object]] = []
    outer_sgs = sc_el.find("sgs")
    for sg in outer_sgs.findall("signalGroup") if outer_sgs is not None else []:
        sg_no = (sg.get("no") or "").strip()
        if not sg_no:
            continue
        roster.append(
            {
                "sg_no": sg_no,
                "sg_name": sg_names.get(sg_no, "") or f"SG{sg_no}",
                "head_count": head_counts.get(sg_no, 0),
                "amber_s": _float_or_none(sg.get("amber")),
                "red_amber_s": _float_or_none(sg.get("redAmber")),
                "min_green_s": _float_or_none(sg.get("minGreen")),
                "green_in_stages": stages_by_sg.get(sg_no, []),
            }
        )
    return programs, roster


def _float_or_none(raw: str | None) -> float | None:
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _program_data_quality(
    cycle_s: float | None, splits: list[dict[str, object]]
) -> list[str]:
    """Machine-readable flags for figures the answer layer must qualify.

    Nothing here changes a value or hides a program: Murdoch SC 9's 375 s
    cycle IS what the .inpx stores and stays reported verbatim — the codes
    exist so an answer can say "the model says 375 s, which is not an
    operable plan" instead of "the cycle is 375 s"."""
    flags: list[str] = []
    if cycle_s is not None and cycle_s > 0:
        if cycle_s > _TYPICAL_CYCLE_MAX_S:
            flags.append(DQ_CYCLE_ABOVE_TYPICAL_MAX)
        elif cycle_s < _TYPICAL_CYCLE_MIN_S:
            flags.append(DQ_CYCLE_BELOW_TYPICAL_MIN)
    if len(splits) == 1:
        flags.append(DQ_SINGLE_SIGNAL_GROUP)
    if cycle_s and cycle_s > 0:
        for split in splits:
            start = split.get("green_start_s")
            end = split.get("green_end_s")
            if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
                continue
            green = (end - start) if end >= start else (cycle_s - start + end)
            if green >= _NEAR_PERMANENT_GREEN_RATIO * cycle_s:
                flags.append(DQ_NEAR_PERMANENT_GREEN)
                break
    return flags


def _parse_fixedtime_blob_programs(
    inner: ET.Element,
    sg_names: dict[str, str],
    head_counts: dict[str, int],
) -> list[dict[str, object]]:
    progs_el = inner.find("progs")
    if progs_el is None:
        return []

    programs: list[dict[str, object]] = []
    for prog in list(progs_el):
        try:
            cycle_s: float | None = int(prog.get("cycletime", "0")) / 1000.0
        except (TypeError, ValueError):
            cycle_s = None
        try:
            offset_s: float | None = int(prog.get("offset", "0")) / 1000.0
        except (TypeError, ValueError):
            offset_s = None
        splits: list[dict[str, object]] = []
        prog_sgs = prog.find("sgs")
        for sg in (list(prog_sgs) if prog_sgs is not None else []):
            sg_no = (sg.get("sg_id") or "").strip()
            if not sg_no:
                continue
            green_begins: list[float] = []
            red_begins: list[float] = []
            cmds_el = sg.find("cmds")
            for cmd in (list(cmds_el) if cmds_el is not None else []):
                try:
                    begin_s = int(cmd.get("begin", "0")) / 1000.0
                except (TypeError, ValueError):
                    continue
                if cmd.get("display") == "3":
                    green_begins.append(begin_s)
                elif cmd.get("display") == "1":
                    red_begins.append(begin_s)
            amber_s = 0.0
            red_amber_s = 0.0
            fs_el = sg.find("fixedstates")
            for fs in (list(fs_el) if fs_el is not None else []):
                try:
                    duration_s = int(fs.get("duration", "0")) / 1000.0
                except (TypeError, ValueError):
                    continue
                if fs.get("display") == "4":
                    amber_s = duration_s
                elif fs.get("display") == "2":
                    red_amber_s = duration_s
            simple = len(green_begins) == 1 and len(red_begins) == 1
            splits.append(
                {
                    "sg_no": sg_no,
                    "sg_name": sg_names.get(sg_no, "") or f"SG{sg_no}",
                    "green_start_s": green_begins[0] if simple else None,
                    "green_end_s": red_begins[0] if simple else None,
                    "amber_s": amber_s,
                    "red_amber_s": red_amber_s,
                    # min-green is NOT reported for blob programs: the blob's
                    # per-SG defaultDurations semantics are unverified — an
                    # unproven datum is omitted, never guessed (CycTm lesson).
                    "min_green_s": None,
                    "head_count": head_counts.get(sg_no, 0),
                    "simple": simple,
                }
            )
        programs.append(
            {
                "prog_no": (prog.get("id") or "").strip(),
                "name": (prog.get("name") or "").strip(),
                "cycle_s": cycle_s,
                "offset_s": offset_s,
                "program_kind": "signal_group",
                "sg_splits": splits,
                "data_quality": _program_data_quality(cycle_s, splits),
            }
        )
    return programs


def _parse_fixedtimesimple_program(
    sc_el: ET.Element,
    sg_names: dict[str, str],
    head_counts: dict[str, int],
    active_prog_no: str | None,
) -> list[dict[str, object]]:
    def _sec(el: ET.Element, attr: str) -> float | None:
        raw = el.get(attr)
        if raw is None:
            return None
        try:
            return float(raw)
        except (TypeError, ValueError):
            return None

    cycle_raw = sc_el.get("cycTm")
    try:
        cycle_s: float | None = float(cycle_raw) if cycle_raw is not None else None
    except (TypeError, ValueError):
        cycle_s = None

    splits: list[dict[str, object]] = []
    sgs_el = sc_el.find("sgs")
    for sg in (sgs_el.findall("signalGroup") if sgs_el is not None else []):
        sg_no = (sg.get("no") or "").strip()
        if not sg_no:
            continue
        splits.append(
            {
                "sg_no": sg_no,
                "sg_name": sg_names.get(sg_no, "") or f"SG{sg_no}",
                # endRed = when red ENDS (green begins, after any red-amber);
                # endGreen = when green ENDS. Same green-window vocabulary as
                # the blob path above.
                "green_start_s": _sec(sg, "endRed"),
                "green_end_s": _sec(sg, "endGreen"),
                "amber_s": _sec(sg, "amber") or 0.0,
                "red_amber_s": _sec(sg, "redAmber") or 0.0,
                "min_green_s": _sec(sg, "minGreen"),
                "head_count": head_counts.get(sg_no, 0),
                "simple": True,
            }
        )
    return [
        {
            "prog_no": active_prog_no or "1",
            "name": "",
            "cycle_s": cycle_s,
            "offset_s": _float_or_none(sc_el.get("offset")),
            "program_kind": "signal_group_simple",
            "sg_splits": splits,
            "data_quality": _program_data_quality(cycle_s, splits),
        }
    ]


def parse_signal_controllers(inpx_path: Path) -> dict[str, SignalInfo]:
    """Return {sc_no: SignalInfo} for all signal controllers in the INPX."""
    root = ET.parse(inpx_path).getroot()
    head_index = _build_signal_head_index(root)
    result: dict[str, SignalInfo] = {}
    for sc_el in root.findall(".//signalController"):
        sc_no = sc_el.get("no", "")
        info = _parse_one_sc(
            sc_el,
            sg_names=_build_signal_group_name_map(sc_el),
            sg_head_counts=head_index.get(sc_no, {}),
        )
        if info is not None:
            result[info["sc_no"]] = info
    return result


def _build_signal_group_name_map(sc_el: ET.Element) -> dict[str, str]:
    names: dict[str, str] = {}
    sgs_el = sc_el.find("sgs")
    if sgs_el is None:
        return names

    for sg in sgs_el.findall("signalGroup"):
        no = sg.get("no", "").strip()
        if not no:
            continue
        names[no] = sg.get("name", "").strip() or f"SG{no}"
    return names


def _build_signal_head_index(root: ET.Element) -> dict[str, dict[str, int]]:
    """Return {sc_no: {sg_no: head_count}} using signalHead@sg='sc sg' references."""
    index: dict[str, dict[str, int]] = {}
    for head in root.findall(".//signalHead"):
        sg_ref = (head.get("sg") or "").strip()
        if not sg_ref:
            continue
        parts = sg_ref.split()
        if len(parts) != 2:
            continue
        sc_no, sg_no = parts[0], parts[1]
        if not sc_no or not sg_no:
            continue
        index.setdefault(sc_no, {})
        index[sc_no][sg_no] = index[sc_no].get(sg_no, 0) + 1
    return index


def _parse_stage_program_phases(
    *,
    inner: ET.Element,
    sg_names: dict[str, str],
    sg_head_counts: dict[str, int],
    active_prog_no: str | None = None,
) -> tuple[int, list[PhaseInfo]]:
    """Phase timeline for a STAGE-BASED controller (``<stageProgs>``).

    Two things here were wrong until the Murdoch Drive finding (2026-07-28)
    and are fixed together, because either alone still lies:

    1. **Which program.** The reported plan must be the one Vissim RUNS —
       the ``<stageProg>`` whose ``id`` equals the controller's ``progNo``,
       not ``stageProgs[0]``. (The Murdoch model has up to EIGHT stage
       programs per controller: Base/DoMin/TMP1/TMP2 x AM/PM.)
    2. **Which stage.** ``<interstage display="N">`` is the id of an
       ``<interstageProg>``, NOT of a ``<stage>``. Each interstage program
       carries explicit ``fromStage``/``toStage``, so the stage that takes
       over at instant ``begin`` is that program's ``toStage``. Reading
       ``display`` as a stage id mis-attributed 3 of Murdoch SC 6's 5
       phases to the wrong signal groups, dropped stage G entirely, and
       produced a phase with NO signal groups on SC 7 (``display="6"``
       against a 5-stage controller). Proof of the correct reading: under
       it all 28 Murdoch stage programs resolve to a closed stage cycle
       where every transition's ``toStage`` is the next one's ``fromStage``.

    ``duration`` is the stage INTERVAL — from the instant its introducing
    interstage begins to the instant the next one does — so it INCLUDES that
    interstage (green + intergreen), not green alone. That is deliberate:
    ``<sc interstagesUsingMinDurations="true">`` compresses interstages to a
    length that is not statically derivable (Murdoch SC 6 program 11 has a
    nominally 16 s interstage between transitions 6 s apart), so subtracting
    the nominal figure would produce negative greens. Cross-checked against
    the project engineer's own SCATS workbook for SC 6 AM: intervals
    41/20/35/29+6 s vs the engineer's measured green+intergreen
    40.9/19.5/34.6/33.9 s."""
    stage_progs = inner.find("stageProgs")
    if stage_progs is None or not list(stage_progs):
        return 0, []

    candidates = list(stage_progs)
    chosen = next(
        (p for p in candidates if (p.get("id") or "").strip() == (active_prog_no or "")),
        candidates[0],
    )
    try:
        cycle_ms = int(chosen.get("cycletime", "0"))
    except (TypeError, ValueError):
        cycle_ms = 0
    cycle_s = max(0, cycle_ms // 1000)
    if cycle_s <= 0:
        return 0, []

    stage_groups = _build_stage_group_info(inner, sg_names, sg_head_counts)
    if not stage_groups:
        return cycle_s, []

    interstage_map = _interstage_index(inner)
    transitions: list[tuple[str, int]] = [
        (to_stage, begin_ms // 1000)
        for begin_ms, _from_stage, to_stage in _stage_transitions(chosen, interstage_map)
    ]

    phases: list[PhaseInfo] = []
    if transitions:
        transitions.sort(key=lambda pair: pair[1])
        for idx, (stage_id, start_s) in enumerate(transitions):
            next_start = transitions[(idx + 1) % len(transitions)][1]
            if idx == len(transitions) - 1:
                duration = (cycle_s - start_s) + next_start
            else:
                duration = next_start - start_s
            duration = max(0, duration)
            group_info = stage_groups.get(stage_id, {"labels": [], "ids": []})
            if duration > 0:
                phases.append(
                    PhaseInfo(
                        start=start_s,
                        duration=duration,
                        signal_groups=group_info["labels"],
                        signal_group_ids=group_info["ids"],
                        movements=[],
                    )
                )
    else:
        # Last-resort fallback when interstage transitions are unavailable.
        stage_ids = sorted(stage_groups.keys(), key=lambda s: int(s) if s.isdigit() else s)
        if not stage_ids:
            return cycle_s, []
        per_stage = max(1, cycle_s // len(stage_ids))
        for idx, stage_id in enumerate(stage_ids):
            group_info = stage_groups.get(stage_id, {"labels": [], "ids": []})
            phases.append(
                PhaseInfo(
                    start=idx * per_stage,
                    duration=per_stage,
                    signal_groups=group_info["labels"],
                    signal_group_ids=group_info["ids"],
                    movements=[],
                )
            )

    return cycle_s, sorted(phases, key=lambda p: p["start"])


def _build_stage_group_info(
    inner: ET.Element,
    sg_names: dict[str, str],
    sg_head_counts: dict[str, int],
) -> dict[str, dict[str, list[str]]]:
    stages_el = inner.find("stages")
    if stages_el is None:
        return {}

    result: dict[str, dict[str, list[str]]] = {}
    for stage in list(stages_el):
        stage_id = (stage.get("id") or "").strip()
        if not stage_id:
            continue
        activations = stage.find("activations")
        if activations is None:
            continue

        labels: list[str] = []
        sg_ids: list[str] = []
        for activation in list(activations):
            if (activation.get("activation") or "").upper() != "ON":
                continue
            sg_no = (activation.get("sg_id") or "").strip()
            if not sg_no:
                continue
            sg_ids.append(sg_no)
            sg_name = sg_names.get(sg_no) or f"SG{sg_no}"
            head_count = sg_head_counts.get(sg_no)
            label = f"{sg_name} ({head_count} heads)" if head_count else sg_name
            labels.append(label)

        result[stage_id] = {
            "labels": sorted(set(labels)),
            "ids": sorted(set(sg_ids)),
        }
    return result


def _normalize(s: str) -> str:
    return s.replace(" ", "").replace("-", "").lower()


def match_sc_to_nodes(
    sc_map: dict[str, SignalInfo],
    node_names: dict[str, str],
) -> dict[str, SignalInfo | None]:
    """Match signal controllers to nodes by normalized name."""
    sc_by_norm = {_normalize(v["sc_name"]): v for v in sc_map.values()}
    return {
        node_id: sc_by_norm.get(_normalize(name))
        for node_id, name in node_names.items()
    }


def match_sc_to_nodes_by_signal_heads(
    inpx_path: Path,
    sc_map: dict[str, SignalInfo],
) -> dict[str, SignalInfo]:
    """Match signal controllers to nodes geometrically.

    Name matching fails when nodes have no name (e.g. the SouthSt model), so
    instead map each controller to the node whose links host the most of its
    signal heads. A ``<signalHead>`` carries ``sg="<sc_no> <sg_no>"`` and
    ``lane="<link_id> <lane_idx>"``; a node lists its links via ``<linkSegs>``.
    """
    try:
        root = ET.parse(inpx_path).getroot()
    except ET.ParseError:
        return {}

    node_links: dict[str, set[str]] = {}
    for node in root.findall(".//nodes/node"):
        node_id = node.get("no", "")
        node_links[node_id] = {
            seg.get("link", "") for seg in node.findall(".//linkSegment") if seg.get("link")
        }

    votes: dict[str, dict[str, int]] = {}
    for head in root.findall(".//signalHead"):
        sg = head.get("sg", "")
        lane = head.get("lane", "")
        if not sg or not lane:
            continue
        sc_no = sg.split(" ")[0]
        link_id = lane.split(" ")[0]
        for node_id, links in node_links.items():
            if link_id in links:
                votes.setdefault(sc_no, {})[node_id] = votes.setdefault(sc_no, {}).get(node_id, 0) + 1
                break

    matched: dict[str, SignalInfo] = {}
    for sc_no, node_votes in votes.items():
        sc = sc_map.get(sc_no)
        if sc is None or not node_votes:
            continue
        best_node = max(node_votes, key=node_votes.get)
        matched[best_node] = sc
    return matched


def _infer_from_standard_sg_map(phases: list[PhaseInfo]) -> list[list[PhaseMovement]]:
    """Fallback for intersections without signal heads: use standard Korean 8-SG convention."""
    result: list[list[PhaseMovement]] = []
    for phase in phases:
        movs: list[PhaseMovement] = []
        for sg_id in phase.get("signal_group_ids", []):
            entry = _STANDARD_4WAY_MOVEMENTS.get(str(sg_id))
            if entry:
                movs.append(PhaseMovement(
                    from_direction=entry[0],
                    to_direction=entry[1],
                    turn=entry[2],
                ))
        result.append(movs)
    return result


def infer_phase_movements_for_controller(
    inpx_path: Path,
    *,
    sc_no: str,
    node_center: tuple[float, float],
    phases: list[PhaseInfo],
) -> list[list[PhaseMovement]]:
    """Infer phase movements using signalHead lane and connector link topology."""
    if not phases:
        return []

    try:
        mtime = os.path.getmtime(inpx_path)
    except OSError:
        return [[] for _ in phases]

    topology = _load_signal_topology(str(inpx_path), mtime)
    sg_lanes = topology["sg_lanes"].get(sc_no, {})
    connectors_by_from = topology["connectors_by_from"]
    link_points = topology["link_points"]

    if not sg_lanes:
        return _infer_from_standard_sg_map(phases)

    phase_movements: list[list[PhaseMovement]] = []
    for phase in phases:
        items: list[PhaseMovement] = []
        for sg_id in phase.get("signal_group_ids", []):
            heads = sg_lanes.get(str(sg_id), [])
            for from_link, from_lane in heads:
                outgoing = connectors_by_from.get(from_link, [])
                connector_hits = _expand_connector_pairs_for_lane(outgoing, from_lane)
                for to_link in connector_hits:
                    from_direction = _classify_link_direction(node_center, link_points.get(from_link, []))
                    to_direction = _classify_link_direction(node_center, link_points.get(to_link, []))
                    turn = _get_turn_kind(from_direction, to_direction)
                    items.append(
                        PhaseMovement(
                            from_direction=from_direction,
                            to_direction=to_direction,
                            turn=turn,
                        )
                    )

            standard_entry = _STANDARD_4WAY_MOVEMENTS.get(str(sg_id))
            if standard_entry:
                items.append(
                    PhaseMovement(
                        from_direction=standard_entry[0],
                        to_direction=standard_entry[1],
                        turn=standard_entry[2],
                    )
                )

        dedup: dict[tuple[str, str, str], PhaseMovement] = {}
        for item in items:
            key = (item["from_direction"], item["to_direction"], item["turn"])
            dedup[key] = item

        ordered = sorted(
            dedup.values(),
            key=lambda m: (
                CARDINAL_DIRECTIONS.index(m["from_direction"])
                if m["from_direction"] in CARDINAL_DIRECTIONS
                else 99,
                m["turn"],
                CARDINAL_DIRECTIONS.index(m["to_direction"])
                if m["to_direction"] in CARDINAL_DIRECTIONS
                else 99,
            ),
        )
        phase_movements.append(ordered)

    return phase_movements


@lru_cache(maxsize=8)
def _load_signal_topology(inpx_path: str, mtime: float) -> SignalTopology:
    del mtime  # cache-busting arg
    root = ET.parse(inpx_path).getroot()
    return SignalTopology(
        sg_lanes=_build_sg_lane_index(root),
        connectors_by_from=_build_connectors_by_from(root),
        link_points=_build_link_points(root),
    )


def _build_sg_lane_index(root: ET.Element) -> dict[str, dict[str, list[tuple[str, int]]]]:
    """Return {sc_no: {sg_no: [(link_id, lane_no), ...]}} from signalHead records."""
    result: dict[str, dict[str, list[tuple[str, int]]]] = {}
    for head in root.findall(".//signalHead"):
        sg_ref = (head.get("sg") or "").strip()
        lane_ref = (head.get("lane") or "").strip()
        if not sg_ref or not lane_ref:
            continue

        sg_parts = sg_ref.split()
        lane_parts = lane_ref.split()
        if len(sg_parts) != 2 or len(lane_parts) != 2:
            continue
        sc_no, sg_no = sg_parts[0], sg_parts[1]
        link_id = lane_parts[0]
        try:
            lane_no = int(float(lane_parts[1]))
        except ValueError:
            continue
        if not sc_no or not sg_no or not link_id:
            continue

        result.setdefault(sc_no, {})
        result[sc_no].setdefault(sg_no, [])
        result[sc_no][sg_no].append((link_id, lane_no))

    for sc_no, by_sg in result.items():
        for sg_no, values in by_sg.items():
            dedup: list[tuple[str, int]] = []
            seen: set[tuple[str, int]] = set()
            for value in values:
                if value in seen:
                    continue
                seen.add(value)
                dedup.append(value)
            result[sc_no][sg_no] = dedup
    return result


def _build_link_points(root: ET.Element) -> dict[str, list[tuple[float, float]]]:
    result: dict[str, list[tuple[float, float]]] = {}
    for link in root.findall(".//links/link"):
        link_id = (link.get("no") or "").strip()
        if not link_id:
            continue
        points: list[tuple[float, float]] = []
        pts_el = link.find("geometry/linkPolyPts")
        if pts_el is not None:
            for point in pts_el.findall("linkPolyPoint"):
                points.append((float(point.get("x", "0")), float(point.get("y", "0"))))
        result[link_id] = points
    return result


def _build_connectors_by_from(root: ET.Element) -> dict[str, list[ConnectorDef]]:
    result: dict[str, list[ConnectorDef]] = {}
    for link in root.findall(".//links/link"):
        from_end = link.find("fromLinkEndPt")
        to_end = link.find("toLinkEndPt")
        if from_end is None or to_end is None:
            continue

        from_link = (from_end.get("link") or "").strip()
        to_link = (to_end.get("link") or "").strip()
        if not from_link or not to_link:
            continue

        lane_pairs: list[tuple[int | None, int | None]] = []
        lanes_el = link.find("lanes")
        if lanes_el is not None:
            for lane in lanes_el.findall("lane"):
                from_lanes = _extract_lane_numbers(lane.find("fromLanes"))
                to_lanes = _extract_lane_numbers(lane.find("toLanes"))
                if not from_lanes and not to_lanes:
                    continue
                from_candidates = from_lanes or [None]
                to_candidates = to_lanes or [None]
                for from_lane in from_candidates:
                    for to_lane in to_candidates:
                        lane_pairs.append((from_lane, to_lane))

        dedup_pairs: list[tuple[int | None, int | None]] = []
        seen_pairs: set[tuple[int | None, int | None]] = set()
        for pair in lane_pairs:
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            dedup_pairs.append(pair)

        result.setdefault(from_link, [])
        result[from_link].append(ConnectorDef(to_link=to_link, lane_pairs=dedup_pairs))
    return result


def _extract_lane_numbers(lanes_el: ET.Element | None) -> list[int]:
    if lanes_el is None:
        return []
    lane_numbers: list[int] = []
    for ref in lanes_el.findall("objectRef"):
        parsed = _parse_lane_ref(ref.get("key", ""))
        if parsed is None:
            continue
        _, lane_no = parsed
        lane_numbers.append(lane_no)
    return lane_numbers


def _parse_lane_ref(key: str) -> tuple[str, int] | None:
    parts = key.strip().split()
    if len(parts) != 2:
        return None
    link_id, lane = parts[0], parts[1]
    if not link_id:
        return None
    try:
        lane_no = int(float(lane))
    except ValueError:
        return None
    return link_id, lane_no


def _expand_connector_pairs_for_lane(connectors: list[ConnectorDef], from_lane: int) -> list[str]:
    to_links: list[str] = []
    matched_any = False
    for connector in connectors:
        lane_pairs = connector["lane_pairs"]
        if not lane_pairs:
            matched_any = True
            to_links.append(connector["to_link"])
            continue

        matched = False
        for source_lane, _ in lane_pairs:
            if source_lane is None or source_lane == from_lane:
                matched = True
                break
        if matched:
            matched_any = True
            to_links.append(connector["to_link"])

    if not matched_any:
        to_links = [connector["to_link"] for connector in connectors]

    dedup: list[str] = []
    seen: set[str] = set()
    for to_link in to_links:
        if to_link in seen:
            continue
        seen.add(to_link)
        dedup.append(to_link)
    return dedup


def _classify_link_direction(node_center: tuple[float, float], points: list[tuple[float, float]]) -> str:
    if len(points) < 2:
        return "east"

    start = points[0]
    end = points[-1]
    start_dist = _distance(node_center, start)
    end_dist = _distance(node_center, end)
    far = end if start_dist <= end_dist else start

    dx = far[0] - node_center[0]
    dy = far[1] - node_center[1]
    if abs(dx) >= abs(dy):
        return "east" if dx >= 0 else "west"
    return "north" if dy >= 0 else "south"


def _get_turn_kind(from_direction: str, to_direction: str) -> str:
    if from_direction == to_direction:
        return "uturn"
    if from_direction not in CARDINAL_DIRECTIONS or to_direction not in CARDINAL_DIRECTIONS:
        return "other"

    from_index = CARDINAL_DIRECTIONS.index(from_direction)
    left_target = CARDINAL_DIRECTIONS[(from_index + 1) % 4]
    straight_target = CARDINAL_DIRECTIONS[(from_index + 2) % 4]
    right_target = CARDINAL_DIRECTIONS[(from_index + 3) % 4]

    if to_direction == left_target:
        return "left"
    if to_direction == straight_target:
        return "straight"
    if to_direction == right_target:
        return "right"
    return "other"


def _distance(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])
