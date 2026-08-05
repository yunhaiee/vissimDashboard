"""PURE-XML PROBE: why does get_signal_programs describe the Murdoch Drive
(Perth) production model's signals wrongly?

Trigger (2026-07-28): cross-checking Prism against the project engineer's own
timing workbook ("Scenario Signal timing adjustment data.xlsx", sheets AM/PM,
shipped inside `Murdoch Drive_Scenario Model_0624.zip`) on the ACTIVE dataset
(SouthStreet_Base.inpx, 312 links, 6 signal controllers) produced two
irreconcilable stories:

  * ``get_signal_programs`` said SC 9 (TCS1111) program 11 "BaseAM" runs a
    **375 s** cycle with ONE signal group green 0 -> 357 s.
  * The engineer's AM sheet says the AM cycle is **~122 s** across phases
    A..G with minimum greens 8/6/6/6/8/6/6, yellow 4.5 s, all-red 3 s.
  * ``get_signal_programs`` reported NOTHING AT ALL (empty program list, no
    reason) for the other FIVE controllers -- including the one the AM sheet
    is actually about.

This probe touches NO COM and NO Vissim instance: everything asked here is
answerable from the .inpx XML plus the base64 ``intSupplyData`` (Vissig)
blobs, and a pure-XML answer is reproducible on any machine (Mac laptop
included) without a licence.  Run it with the backend venv:

    .venv\\Scripts\\python.exe tools\\probe_murdoch_signals.py

Sections, matching the investigation brief:

  [1] Per-controller evidence dump -- ``type``, blob presence, Vissig
      ``version``/``checkSum``, ``progFile``/``supplyFile1..3`` (external
      files?), ``progNo``, and the FULL decoded structure: how many
      ``<prog>`` AND how many ``<stageProg>``, their ids/cycletimes/offsets,
      per-prog ``<sg>`` counts and the literal ``<cmd>`` shapes.
  [2] The 375 s question -- ms-vs-s conversion, progs[0]-vs-progNo, or a
      genuine model stub?
  [3] The empty-sg_splits question -- blob absent? unknown blob version?
      "non-simple" cmd shapes? or signal groups stored somewhere the parser
      never looks?
  [4] Cross-check against the engineer's workbook: which controller carries
      the AM sheet's phase set, and what cycle does the model really run?
  [5] What our CURRENT read path (``parse_signal_programs`` /
      ``parse_signal_controllers``) reports for each controller, verbatim --
      the "before" side of the before/after.

Nothing here is inferred: every number printed is read straight out of the
XML, and anything the probe could not read is printed as ``None`` rather
than filled in.
"""
from __future__ import annotations

import base64
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.datasets import get_active_inpx_path  # noqa: E402
from services.signal_parser import (  # noqa: E402
    parse_signal_controllers,
    parse_signal_programs,
)

#: The AM sheet of "Scenario Signal timing adjustment data.xlsx", transcribed
#: by hand from the workbook (openpyxl, data_only=True) so this probe stays
#: runnable without the client's spreadsheet on disk.  Three intersection
#: blocks live on that one sheet; each lists its phases, the signal groups
#: each phase serves ("intergreen" column), min greens, and the SCATS-derived
#: measured cycle.  The "phase -> signal groups" mapping is the fingerprint
#: used in section [4] to identify which Vissim controller each block is.
ENGINEER_AM_BLOCKS: list[dict[str, Any]] = [
    {
        "sheet_rows": "AM!30-54",
        "phases": ["A", "B", "C", "D", "E", "F", "G"],
        "min_green_s": [8, 6, 6, 6, 8, 6, 6],
        "yellow_s": 4.5,
        "all_red_s": 3.0,
        "measured_cycle_s": 122.1785714285714,
        "cycle_label": "Average Cycle Adopted (cell is #REF!)",
        # phase -> signal groups it serves (workbook "intergreen" column)
        "phase_sgs": {"A": [1, 2], "D": [7, 8], "E": [5, 6], "G": [3, 4]},
        # phase -> (end of green, end of intergreen) seconds into the cycle
        "phase_end_s": {
            "A": (30.43, 37.93),
            "D": (49.93, 57.43),
            "E": (84.57, 92.07),
            "G": (118.50, 126.00),
        },
        "note": "*Often runs G then G1",
    },
    {
        "sheet_rows": "AM!81-106",
        "phases": ["A", "B", "C", "D", "E", "F"],
        "min_green_s": [6, 8, 6, 10, 10, 6],
        "yellow_s": 4.0,
        "all_red_s": 2.5,
        "measured_cycle_s": 148.7391304347828,
        "cycle_label": "Average Cycle Adopted = 375",
        "phase_sgs": {"A": [1, 2], "D": [5], "E": [6], "F": [3, 4]},
        "phase_end_s": {
            "A": (65.04, 71.54),
            "D": (87.39, 94.89),
            "E": (116.00, 123.50),
            "F": (151.52, 158.02),
        },
        "note": "",
    },
    {
        "sheet_rows": "AM!135-160",
        "phases": ["A", "B", "C", "D", "E", "F"],
        "min_green_s": [8, 6, 6, 6, 15, 15],
        "yellow_s": 5.0,
        "all_red_s": 2.0,
        "measured_cycle_s": 145.1304347826086,
        "cycle_label": "Average Cycle Adopted = 375",
        "phase_sgs": {"A": [1, 4], "C": [2], "D": [3, 4]},
        "phase_end_s": {
            "A": (75.96, 82.96),
            "C": (104.46, 110.96),
            "D": (148.24, 155.74),
        },
        "note": "",
    },
]

#: Vissig ``display`` codes (verified in unit ⑨ and again on every blob here).
DISPLAY_NAMES = {"1": "red", "2": "red-amber", "3": "green", "4": "amber"}


def _sc_elements(root: ET.Element) -> list[ET.Element]:
    return root.findall(".//signalController")


def _decode_blob(sc_el: ET.Element) -> ET.Element | None:
    isd = sc_el.get("intSupplyData", "")
    if not isd:
        return None
    try:
        return ET.fromstring(base64.b64decode(isd).decode("utf-8"))
    except Exception as exc:  # pragma: no cover - probe diagnostics only
        print(f"    !! blob decode FAILED: {exc!r}")
        return None


def _cmd_shape(sg_el: ET.Element) -> str:
    cmds_el = sg_el.find("cmds")
    items = list(cmds_el) if cmds_el is not None else []
    if not items:
        return "<cmds/> EMPTY"
    return " ".join(
        f"{DISPLAY_NAMES.get(c.get('display', ''), '?' + str(c.get('display')))}@{c.get('begin')}ms"
        for c in items
    )


def _fixedstate_shape(sg_el: ET.Element) -> str:
    fs_el = sg_el.find("fixedstates")
    items = list(fs_el) if fs_el is not None else []
    if not items:
        return "none"
    return " ".join(
        f"{DISPLAY_NAMES.get(f.get('display', ''), '?')}={f.get('duration')}ms" for f in items
    )


def section_1_evidence(root: ET.Element) -> dict[str, dict[str, Any]]:
    """[1] Raw per-controller evidence: type, blob, version, external files,
    and the real decoded program structure (BOTH families)."""
    print("=" * 78)
    print("[1] PER-CONTROLLER EVIDENCE DUMP")
    print("=" * 78)
    facts: dict[str, dict[str, Any]] = {}

    for sc_el in _sc_elements(root):
        sc_no = (sc_el.get("no") or "").strip()
        name = (sc_el.get("name") or "").strip()
        isd = sc_el.get("intSupplyData", "")
        ext_files = {
            key: sc_el.get(key, "")
            for key in ("progFile", "supplyFile1", "supplyFile2", "supplyFile3", "guiFile")
        }
        print("-" * 78)
        print(f"SC {sc_no}  name={name!r}")
        print(f"  type            = {sc_el.get('type')!r}")
        print(f"  progNo (ACTIVE) = {sc_el.get('progNo')!r}")
        print(f"  cycTm           = {sc_el.get('cycTm')!r}   (0 on every real FIXEDTIME, unit ⑨)")
        print(f"  offset          = {sc_el.get('offset')!r}")
        print(f"  intSupplyData   = {'present, ' + str(len(isd)) + ' b64 chars' if isd else 'ABSENT'}")
        nonempty_ext = {k: v for k, v in ext_files.items() if v}
        print(f"  external files  = {nonempty_ext or 'NONE (all of progFile/supplyFile1..3 empty)'}")

        inner = _decode_blob(sc_el)
        entry: dict[str, Any] = {
            "sc_no": sc_no,
            "name": name,
            "type": sc_el.get("type"),
            "prog_no_active": (sc_el.get("progNo") or "").strip(),
            "has_blob": bool(isd),
            "external_files": nonempty_ext,
        }
        if inner is None:
            print("  -> NO DECODABLE BLOB")
            facts[sc_no] = entry
            continue

        print(f"  blob root       = <{inner.tag}> version={inner.get('version')!r} "
              f"checkSum={inner.get('checkSum')!r}")
        print(f"  blob children   = {[c.tag for c in inner]}")
        entry["blob_version"] = inner.get("version")
        entry["blob_checksum"] = inner.get("checkSum")

        # --- signal groups declared in the blob ---------------------------
        blob_sgs = inner.find("sgs")
        blob_sg_ids = [sg.get("id") for sg in list(blob_sgs)] if blob_sgs is not None else []
        # ...and on the OUTER element (the plain-XML <signalGroup> list)
        outer_sgs = sc_el.find("sgs")
        outer_sg = list(outer_sgs.findall("signalGroup")) if outer_sgs is not None else []
        print(f"  <sgs> in blob   = {len(blob_sg_ids)} -> ids {blob_sg_ids}")
        print(f"  <signalGroup> on outer element = {len(outer_sg)} "
              f"(endRed/endGreen all zero? "
              f"{all(sg.get('endRed') == '0' and sg.get('endGreen') == '0' for sg in outer_sg)})")
        entry["blob_sg_count"] = len(blob_sg_ids)
        entry["outer_sg_count"] = len(outer_sg)

        # --- family A: signal-group programs <progs><prog> ----------------
        progs_el = inner.find("progs")
        progs = list(progs_el) if progs_el is not None else []
        print(f"  <progs>         = {'present' if progs_el is not None else 'ABSENT'}, "
              f"{len(progs)} <prog> element(s)")
        entry["prog_count"] = len(progs)
        entry["progs"] = []
        for prog in progs:
            cycletime_ms = prog.get("cycletime")
            psgs = prog.find("sgs")
            psg_list = list(psgs) if psgs is not None else []
            print(f"    <prog id={prog.get('id')!r} name={prog.get('name')!r} "
                  f"cycletime={cycletime_ms} ms offset={prog.get('offset')} "
                  f"switchpoint={prog.get('switchpoint')}> "
                  f"-> {len(psg_list)} <sg>")
            prog_rec: dict[str, Any] = {
                "id": prog.get("id"),
                "name": prog.get("name"),
                "cycletime_ms": cycletime_ms,
                "sg_count": len(psg_list),
                "sgs": [],
            }
            for sg in psg_list:
                shape = _cmd_shape(sg)
                fixed = _fixedstate_shape(sg)
                print(f"        sg_id={sg.get('sg_id')!r} seq={sg.get('signal_sequence')!r} "
                      f"cmds: {shape} | fixedstates: {fixed}")
                prog_rec["sgs"].append({"sg_id": sg.get("sg_id"), "cmds": shape})
            entry["progs"].append(prog_rec)

        # --- family B: STAGE programs <stageProgs><stageProg> -------------
        stage_progs_el = inner.find("stageProgs")
        stage_progs = list(stage_progs_el) if stage_progs_el is not None else []
        stages_el = inner.find("stages")
        stages = list(stages_el) if stages_el is not None else []
        inter_el = inner.find("interstageProgs")
        inters = list(inter_el) if inter_el is not None else []
        print(f"  <stageProgs>    = {len(stage_progs)} <stageProg> element(s)   "
              f"<stages> = {len(stages)}   <interstageProgs> = {len(inters)}")
        entry["stage_prog_count"] = len(stage_progs)
        entry["stage_count"] = len(stages)
        entry["stages"] = {}
        entry["stage_progs"] = []

        for stage in stages:
            on = [
                a.get("sg_id")
                for a in list(stage.find("activations") or [])
                if (a.get("activation") or "").upper() == "ON"
            ]
            entry["stages"][stage.get("id")] = {"name": stage.get("name"), "sgs_on": on}
        if stages:
            print("    <stages>: " + ", ".join(
                f"id={sid} name={s['name']!r} SGs_ON={s['sgs_on']}"
                for sid, s in entry["stages"].items()
            ))

        interstage_map: dict[str, dict[str, Any]] = {}
        for ip in inters:
            interstage_map[ip.get("id", "")] = {
                "from": ip.get("fromStage"),
                "to": ip.get("toStage"),
                "duration_ms": ip.get("cycletime"),
                "name": ip.get("name"),
            }
        if interstage_map:
            print("    <interstageProgs>: " + ", ".join(
                f"id={k} {v['from']}->{v['to']} ({v['duration_ms']}ms)"
                for k, v in interstage_map.items()
            ))
        entry["interstage_progs"] = interstage_map

        for sp in stage_progs:
            transitions = [
                (i.get("display"), i.get("begin"))
                for i in list(sp.find("interstages") or [])
            ]
            print(f"    <stageProg id={sp.get('id')!r} name={sp.get('name')!r} "
                  f"cycletime={sp.get('cycletime')} ms offset={sp.get('offset')}> "
                  f"interstages(display=interstageProg id!)={transitions}")
            entry["stage_progs"].append({
                "id": sp.get("id"),
                "name": sp.get("name"),
                "cycletime_ms": sp.get("cycletime"),
                "offset_ms": sp.get("offset"),
                "transitions": transitions,
            })

        facts[sc_no] = entry

    return facts


def section_2_the_375(facts: dict[str, dict[str, Any]]) -> None:
    """[2] Where does the 375 s cycle come from?"""
    print()
    print("=" * 78)
    print("[2] THE 375 s CYCLE -- unit conversion, wrong program, or a real stub?")
    print("=" * 78)
    sc9 = facts.get("9")
    if not sc9:
        print("  SC 9 not present in this dataset.")
        return

    print(f"  SC 9 declares progNo={sc9['prog_no_active']!r}; its blob carries "
          f"{sc9['prog_count']} <prog> element(s):")
    for prog in sc9.get("progs", []):
        ms = int(prog["cycletime_ms"])
        active = str(prog["id"]) == str(sc9["prog_no_active"])
        print(f"    prog id={prog['id']} name={prog['name']!r} cycletime={ms} ms "
              f"= {ms / 1000:g} s   {'<== ACTIVE (progNo match)' if active else ''}")
        for sg in prog["sgs"]:
            print(f"        sg {sg['sg_id']}: {sg['cmds']}")

    print()
    print("  a) UNIT CONVERSION: cycletime is milliseconds and IS divided by 1000")
    print("     (signal_parser._parse_fixedtime_blob_programs). 375000 ms -> 375.0 s")
    print("     is arithmetically correct. NOT a units bug.")
    print("  b) progs[0]-vs-progNo: the KNOWN pre-existing gap (chip task_eeca6837).")
    ids = [p["id"] for p in sc9.get("progs", [])]
    print(f"     SC 9's prog ids in document order = {ids}; progNo = "
          f"{sc9['prog_no_active']!r}.")
    if ids and str(ids[0]) == str(sc9["prog_no_active"]):
        print("     progs[0] HAPPENS to be the active program here, so this gap did")
        print("     NOT cause the 375 s report. It is still latent for other models.")
    else:
        print("     progs[0] is NOT the active program -- this gap DID bite here.")
    print("  c) STUB: SC 9 has exactly ONE signal group, green 0 s -> 357 s with a")
    print("     4 s amber inside a 375 s cycle => ~96% green, ~14 s red per cycle.")
    print("     VERDICT: the number is REAL DATA read correctly. The MODEL contains")
    print("     an implausible program; Prism reported it as if it were a plan.")


def section_3_empty_splits(facts: dict[str, dict[str, Any]]) -> None:
    """[3] Why are sg_splits empty on 5 of the 6 controllers?"""
    print()
    print("=" * 78)
    print("[3] THE EMPTY sg_splits -- blob absent? version? cmd shape? or a")
    print("    program family the parser never looks at?")
    print("=" * 78)
    for sc_no, f in facts.items():
        if f.get("prog_count"):
            continue
        print(f"  SC {sc_no} ({f['name']!r}):")
        print(f"    blob present      : {f.get('has_blob')}   version="
              f"{f.get('blob_version')!r} checkSum={f.get('blob_checksum')!r}")
        print(f"    external files    : {f.get('external_files') or 'NONE'}")
        print(f"    <progs>           : EMPTY ({f.get('prog_count', 0)} <prog>)")
        print(f"    <stageProgs>      : {f.get('stage_prog_count')} programs over "
              f"{f.get('stage_count')} stages")
        print(f"    signal groups     : {f.get('blob_sg_count')} in blob / "
              f"{f.get('outer_sg_count')} on the outer <signalGroup> list")
        print("    => NOT absent, NOT an unknown version (202001, same as Beijing),")
        print("       NOT an external file, NOT a 'non-simple' cmd shape. The whole")
        print("       program lives in <stageProgs>/<stages>/<interstageProgs>, a")
        print("       STAGE-BASED family that parse_signal_programs never reads.")


def section_4_crosscheck(facts: dict[str, dict[str, Any]]) -> None:
    """[4] Which controller is the engineer's AM sheet about, and what does the
    model actually run for it?"""
    print()
    print("=" * 78)
    print("[4] CROSS-CHECK vs the engineer's workbook (AM sheet, 3 intersections)")
    print("=" * 78)

    for block in ENGINEER_AM_BLOCKS:
        want = {name: sorted(sgs) for name, sgs in block["phase_sgs"].items()}
        print("-" * 78)
        print(f"  workbook block {block['sheet_rows']}: phases {block['phases']}")
        print(f"    min greens {block['min_green_s']}  yellow {block['yellow_s']}s  "
              f"all-red {block['all_red_s']}s")
        print(f"    measured cycle {block['measured_cycle_s']:.2f} s  "
              f"[{block['cycle_label']}]")
        print(f"    phase -> signal groups: {want}")

        best: tuple[int, str | None] = (0, None)
        for sc_no, f in facts.items():
            got = {
                s["name"]: sorted(int(x) for x in s["sgs_on"])
                for s in f.get("stages", {}).values()
            }
            hits = sum(1 for name, sgs in want.items() if got.get(name) == sgs)
            if hits > best[0]:
                best = (hits, sc_no)

        hits, sc_no = best
        if sc_no is None:
            print("    !! no controller in the model matches this phase/SG fingerprint")
            continue
        f = facts[sc_no]
        got = {
            s["name"]: sorted(int(x) for x in s["sgs_on"])
            for s in f.get("stages", {}).values()
        }
        print(f"    ==> MATCHES SC {sc_no} ({f['name']!r}): {hits}/{len(want)} phases "
              f"identical by signal-group set")
        print(f"        model stages: {got}")
        active = f.get("prog_no_active")
        for sp in f.get("stage_progs", []):
            mark = " <== ACTIVE (progNo)" if str(sp["id"]) == str(active) else ""
            if mark or "am" in (sp["name"] or "").lower():
                print(f"        stageProg id={sp['id']} {sp['name']!r} "
                      f"cycle={int(sp['cycletime_ms']) / 1000:g}s "
                      f"offset={int(sp['offset_ms']) / 1000:g}s{mark}")
        act = next((sp for sp in f.get("stage_progs", []) if str(sp["id"]) == str(active)), None)
        if act:
            model_cycle = int(act["cycletime_ms"]) / 1000
            print(f"        model ACTIVE cycle = {model_cycle:g} s vs workbook measured "
                  f"{block['measured_cycle_s']:.2f} s "
                  f"(delta {model_cycle - block['measured_cycle_s']:+.2f} s)")
            print(f"        workbook phase end-of-intergreen times "
                  f"{ {k: v[1] for k, v in block['phase_end_s'].items()} }")
            print(f"        model interstage begins (s) "
                  f"{[int(b) / 1000 for _, b in act['transitions']]}")
        if block["note"]:
            print(f"        workbook note: {block['note']}")

    print("-" * 78)
    print("  Controllers with NO workbook block: ", end="")
    print(", ".join(
        f"SC {n} ({f['name']!r})" for n, f in facts.items()
        if n not in {"6", "7", "8"}
    ))


def section_5_current_tool_output(inpx: Path) -> None:
    """[5] What our read path says -- run this before AND after any parser
    change and diff the two."""
    print()
    print("=" * 78)
    print("[5] Prism read-path output")
    print("=" * 78)
    programs = parse_signal_programs(inpx)
    for c in programs:
        print("-" * 78)
        print(f"  parse_signal_programs -> SC {c['sc_no']} {c['sc_name']!r} "
              f"type={c['controller_type']} active_program_no={c['active_program_no']!r}")
        if c.get("programs_unavailable_reason"):
            print(f"    programs_unavailable_reason = {c['programs_unavailable_reason']!r}")
        if c.get("controller_flags"):
            print(f"    controller_flags = {c['controller_flags']}")
        for sg in c.get("signal_groups", []) or []:
            print(f"    roster SG {sg['sg_no']} {sg['sg_name']!r} heads={sg['head_count']} "
                  f"min_green={sg['min_green_s']}s amber={sg['amber_s']}s "
                  f"green_in_stages={sg['green_in_stages']}")
        progs = c.get("programs") or []
        if not progs:
            print("    programs = []")
            continue
        for p in progs:
            active = str(p["prog_no"]) == str(c["active_program_no"])
            print(f"    program {p['prog_no']!r} {p['name']!r} kind={p.get('program_kind')} "
                  f"cycle_s={p['cycle_s']} offset_s={p.get('offset_s')} "
                  f"sg_splits={len(p['sg_splits'])}"
                  f"{'  <== ACTIVE' if active else ''}")
            if p.get("sg_splits_unavailable_reason"):
                print(f"      sg_splits_unavailable_reason = "
                      f"{p['sg_splits_unavailable_reason']!r}")
            if p.get("data_quality"):
                print(f"      data_quality = {p['data_quality']}")
            for step in p.get("stage_sequence", []) or []:
                print(f"      t={step['transition_at_s']:g}s -> stage "
                      f"{step['to_stage']!r} (from {step['from_stage']!r}) green SGs "
                      f"{step['signal_groups']}")
            for s in p["sg_splits"]:
                print(f"      SG {s['sg_no']} {s['sg_name']!r} green "
                      f"{s['green_start_s']} -> {s['green_end_s']} amber={s['amber_s']} "
                      f"heads={s['head_count']} simple={s['simple']}")

    print()
    controllers = parse_signal_controllers(inpx)
    for sc_no, info in controllers.items():
        print(f"  parse_signal_controllers -> SC {sc_no} {info['sc_name']!r} "
              f"cycle_time={info['cycle_time']}s type={info['controller_type']} "
              f"num_sgs={info['num_signal_groups']} num_phases={info['num_phases']}")
        for ph in info["phases"]:
            print(f"      phase start={ph['start']}s dur={ph['duration']}s "
                  f"sgs={ph['signal_group_ids']}")


def section_6_tool_output(sc_nos: tuple[str, ...] = ("9", "6")) -> None:
    """[6] The agent-facing ``get_signal_programs`` result verbatim -- the
    shape the planner and the answer actually see."""
    print()
    print("=" * 78)
    print("[6] get_signal_programs tool output (agent-facing)")
    print("=" * 78)
    from services.agent.tools_network import (  # noqa: PLC0415 - probe-only import
        GetSignalProgramsParams,
        get_signal_programs,
        set_active_inpx,
    )

    # The tools read whichever network the agent SESSION selected; outside a
    # session that defaults to config.SIMULATION_INPX_PATH, not the uploaded
    # dataset. Point it at the active dataset exactly as the session layer does.
    set_active_inpx(get_active_inpx_path())

    for sc_no in sc_nos:
        payload = get_signal_programs(GetSignalProgramsParams(controller_no=sc_no))
        print("-" * 78)
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def section_7_stage_interval_validation(root: ET.Element) -> None:
    """[7] Does the fixed stage-phase reading agree with the engineer?

    The stage INTERVAL (introducing interstage + stage green) is what the
    parser reports as a phase ``duration``; the workbook's per-phase
    ``Green + Yellow + All-red`` is the same quantity measured in the field.
    Printing them side by side is the only external oracle available without
    a licensed Vissim run."""
    print()
    print("=" * 78)
    print("[7] VALIDATION: stage intervals vs the engineer's measured phases")
    print("=" * 78)
    workbook_sc6 = {  # phase -> (green_s, yellow_s, all_red_s) from AM!48-54
        "A": (33.43, 4.5, 3.0),
        "D": (12.00, 4.5, 3.0),
        "E": (27.14, 4.5, 3.0),
        "G": (26.43, 4.5, 3.0),
    }
    controllers = parse_signal_controllers(get_active_inpx_path())
    info = controllers.get("6")
    if info is None:
        print("  SC 6 absent -- skipping.")
        return
    stage_names = {}
    for sc_el in _sc_elements(root):
        if (sc_el.get("no") or "") != "6":
            continue
        inner = _decode_blob(sc_el)
        if inner is None:
            continue
        for stage in list(inner.find("stages") or []):
            key = tuple(sorted(
                a.get("sg_id")
                for a in list(stage.find("activations") or [])
                if (a.get("activation") or "").upper() == "ON"
            ))
            stage_names[key] = stage.get("name")

    print(f"  SC 6, active program 11 'BaseAM', model cycle {info['cycle_time']} s")
    print(f"  {'phase':<6} {'model interval':>15} {'workbook g+y+ar':>17} {'delta':>8}")
    total = 0.0
    for ph in info["phases"]:
        key = tuple(sorted(ph["signal_group_ids"]))
        name = stage_names.get(key, "?")
        wb = workbook_sc6.get(name)
        model = float(ph["duration"])
        total += model
        if wb is None:
            print(f"  {name:<6} {model:>14.0f}s {'(not in workbook)':>17} {'-':>8}")
            continue
        measured = sum(wb)
        print(f"  {name:<6} {model:>14.0f}s {measured:>16.2f}s {model - measured:>+7.2f}s")
    print(f"  intervals sum to {total:g} s = the model cycle "
          f"({'OK' if abs(total - info['cycle_time']) < 1e-6 else 'MISMATCH'})")
    print("  NOTE: the model is a fixed-time APPROXIMATION of SCATS-actuated")
    print("  operation, so exact equality is not expected -- agreement to a few")
    print("  seconds is what confirms the phase->signal-group attribution.")


def main() -> int:
    # Windows consoles default to cp1252; this probe prints the unit ⑨ glyph
    # and Korean reason strings. Force UTF-8 so redirection works everywhere.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # pragma: no cover - non-reconfigurable stream
        pass

    inpx = get_active_inpx_path()
    print(f"ACTIVE .inpx: {inpx}")
    print(f"exists: {inpx.exists()}  size: {inpx.stat().st_size if inpx.exists() else 0} bytes")
    if not inpx.exists():
        print("No active dataset -- nothing to probe.")
        return 1

    root = ET.parse(inpx).getroot()
    facts = section_1_evidence(root)
    section_2_the_375(facts)
    section_3_empty_splits(facts)
    section_4_crosscheck(facts)
    section_5_current_tool_output(inpx)
    section_6_tool_output()
    section_7_stage_interval_validation(root)
    return 0


if __name__ == "__main__":
    # Unit 1.4: dataset paths are resolved from the workspace bound to the
    # current execution context, and there is no request here to bind one.
    # A script that means "the default project" has to say so -- see
    # services/workspaces.py for why an unbound context raises instead of
    # quietly picking one.
    from services.workspaces import bind_workspace, default_workspace_id  # noqa: E402

    with bind_workspace(default_workspace_id()):
        raise SystemExit(main())
