"""Q5 unit (2) PROBE: what does ``DuplicateSignalController`` actually
create, and what does the shadow model see afterwards?

Runs BEFORE any verb code, per the house rule -- ``add_signal_controller``
is gap (2) of the GENESIS GAP LIST (docs/phase2/DECISIONS.md, 2026-08-01)
and the last one still fully open. Q5 unit (1) (2026-08-03) already proved,
live and twice, that a bare ``AddSignalController`` + ``.SGs.AddSignalGroup``
makes a NON-FUNCTIONAL skeleton (``RunSingleStep`` fails outright) while
``DuplicateSignalController`` copies a donor's whole working program. This
probe does NOT re-litigate that verdict; it measures the things a real verb
has to be built on and that no prior unit ever measured:

  A. **Which argument shape does ``DuplicateSignalController`` accept?** The
     codebase has BOTH precedents: ``DuplicateDrivingBehavior(source_NO,
     new_key)`` takes an int key, ``DuplicatePriorityRule(source_OBJECT,
     new_key)`` takes a COM object. Q5 unit (1) only ever tried the object
     form, so the int form was genuinely unknown. Both shapes are tried
     independently, each with its own free key, each judged by a
     ``GetAll()`` id-set diff -- never by a bare non-exception (the
     ``AddPriorityRule`` silent-no-op precedent, Q4 unit 6).
  B. **What does the copy inherit, and is it independently editable?**
     Name/Type/CycTm/Offset/ProgNo/SG set/program-blob size read off both
     donor and copy; a rename on the copy (the verb's own read-back-verifiable
     write) with a donor re-read to prove the rename did not touch the donor;
     and finally a real ``set_signal_green_split`` write against the COPY.
  C. **Exactly which shadow-model object classes gain rows** when the copy
     is saved -- measured with ``guardrails.diff_shadow_models`` over a real
     before/after parse of the saved .inpx, which is the SAME machinery
     ``GuardrailEngine.postwrite`` runs. This is where
     ``_ALLOWS_NEW_OBJECTS_IN["add_signal_controller"]`` comes from: derived,
     not accepted on faith.
  D. **The family limitation, live rather than argued:** on a real network
     with ZERO signal controllers, can a duplicate-based verb mint the first?
  E. **Is a non-fixed-time donor safe to duplicate?** Only 27 of the
     corpus's 129 controllers are fixed-time family (20 FIXEDTIME + 7
     FIXEDTIMESIMPLE); the other 102 are external-logic types (56
     RINGBARRIERCONTROLLER, 25 RAILWAYCROSSING, 16 VAP, 3
     TWOSTAGECONTROLLER, 2 PEDESTRIANCROSSING). This is the MAJORITY case,
     not an edge case.

Parts, each donor CHOSEN from this file's own static corpus scan [0] rather
than guessed:

  a. ``Urban Intersection Beijing.CN``   -- 1 SC, FIXEDTIME (the Q5 unit (1)
     donor, deliberately reused so its 2 programs x 22 signal groups finding
     is re-confirmed rather than assumed).
  b. ``Air Taxi Vertiport``              -- 3 FIXEDTIMESIMPLE SCs (the OTHER
     allowed donor family; its programs live in plain ``endRed``/``endGreen``
     attributes, not in the ``intSupplyData`` blob, so "the verb works on
     FIXEDTIME" proves nothing about it).
  c. ``Roundabout Billings.US``          -- 0 SCs.
  d. ``Signalization RBC Boise.US``      -- 49 RINGBARRIERCONTROLLER SCs.

**Per-part process boundaries, not one long-lived process** -- gap (1)'s
validated fix (Q5 unit (1)): sequential COM connect/disconnect cycles in one
process segfault after ~2-3. ``main()`` shells out to itself once per part;
each part process holds exactly ONE COM cycle. State crosses process
boundaries through a JSON file.

Hard rules honored: no git commands; no edits to ``services/`` from here
(this file is read/measure-only); work dirs confined to
``runtime/signal-controller-duplicate-probe/``; every connection is
``attach_to_running=False`` (a human's Vissim GUI is open on this machine);
donor networks are COPIED into the work dir, never opened in place.

---------------------------------------------------------------------------
FINDINGS (live, 2026-08-03, PTV Vissim 2026 SP09 on this machine)
---------------------------------------------------------------------------

**A1. BOTH argument shapes work.** ``DuplicateSignalController`` accepts an
int key AND a COM object as ``Source``: two independent duplicates were made
in one session (int form -> SC 2, object form -> SC 3), each proven by its
own ``GetAll()`` id-set diff, neither raising. The verb uses the INT form,
matching ``duplicate_driving_behavior``'s precedent -- no object plumbing,
and ``_item_by_key`` still existence-checks the source first so a bogus
number raises this codebase's own structured ``VissimObjectNotFoundError``
rather than a raw COM error.

**A2. The copy is a full, independent copy.** Beijing SC 2 came back with
the donor's ``Type`` (FIXEDTIME), ``CycTm`` (0.0 -- the donor is
``cycTmIsVar="true"``, so 0 is the REAL stored value, not a read failure),
``Offset`` (0.0), ``ProgNo`` (2), all 22 of the donor's signal-group numbers,
and an ``IntSupplyData`` blob of the identical length (30956 chars).
``SetAttValue('Name', ...)`` on the copy works and reads back; the donor's
own name re-read immediately afterwards was UNCHANGED (an alias would have
shown the new name), so the copy is genuinely independent.
``RunSingleStep`` with the duplicate present: OK (Q5 unit (1)'s verdict,
third reproduction). **The copy also inherits the donor's NAME verbatim** --
invisible on Beijing (whose controller is unnamed) but plain in Part B,
where the copy came back as a second ``'Terminal entrance door'``. That is
why the verb renames in the SAME call rather than leaving it to a follow-up:
two identically-named controllers is exactly the confusion a genesis flow
must not create.

**A3. THE exemption, derived: exactly two classes gain rows, nothing else
moves.** A real ``diff_shadow_models`` over the saved .inpx for ONE
duplicate reports ``signals: 1 added`` (``2:added``) and
``signal_program_splits: 44 added`` (``2.{1,2}.{1..22}``) and **zero**
non-"added" differences -- no field change anywhere, no other class touched.
That is exactly the id set the guardrail named when Q5 unit (1)'s harness
made a raw duplicate inside ``add_signal_head``'s postwrite window, so::

    _ALLOWS_NEW_OBJECTS_IN["add_signal_controller"] = {"signals",
                                                       "signal_program_splits"}

is right AND sufficient. Note the shape: 44 = donor programs (2) x donor
signal groups (22), a PER-DONOR number -- which is why the exemption has to
be a per-object-CLASS opt-in and could never be an id list.

**A4. The copy's program is editable, and editing it moves only the copy.**
``set_signal_green_split`` (v2a unit 9's COM method) against the COPY's SG 1
wrote ``[7.0, 33.0]`` s and read back ``{'green_start_s': 7.0,
'green_end_s': 33.0, 'controller_type': 'FIXEDTIME', 'program_no': 2}``; a
second save + diff against the first shows the ONLY change anywhere is
``signal_program_splits#2.2.1:field_changed.green_window`` -- one row, on
the copy, in the copy's own active program. The duplicate is a real,
independently editable controller, not a decorative row (Q4 unit (6)'s
"prove the new object is genuinely editable, not just present" precedent,
satisfied at COM level; the dispatch-level version lives in
``backend/tools/signal_controller_live_check.py``).

**A5. LANDMINE (live-caught here, not in any prior unit's notes):
structural signal-controller edits are REFUSED once a simulation has
started.** After a single ``RunSingleStep``, ``RemoveSignalController``
raised ``CCOMSignalControllerContainerBase<CSignalControllerManager>::
RemoveSignalController: This action cannot be performed during simulation.``
This probe therefore does all structural work BEFORE stepping, and step [A.8]
re-demonstrates the refusal deliberately, after the measured artifact is
already saved. Harmless for ``add_signal_controller`` itself (an edit
session never runs a simulation) but a real ordering constraint for any
future build-then-run job that reuses one COM session.

**B. FIXEDTIMESIMPLE duplicates the same way.** Air Taxi Vertiport's SC 11
(``CycTm`` 999.0, 2 signal groups, ``intSupplyData`` length 0 -- there is no
blob at all; its program is plain ``endRed``/``endGreen`` attributes)
duplicated cleanly via the same int-key call into SC 22, inheriting Type,
CycTm, Offset, ProgNo, both signal-group numbers and the donor's name, and
contributed exactly ``signals: 1 added`` (``22:added``) +
``signal_program_splits: 2 added`` (``22.1.1``, ``22.1.2``) with zero other
differences. Both allowed donor families are therefore measured, not one
measured and one assumed -- and the per-call split count is confirmed to be
donor-specific (44 for Beijing, 2 here).

**C. The family limitation is real and reproduces live.**
``Roundabout Billings.US`` has 0 signal controllers;
``Net.SignalControllers.GetAll()`` returns an empty collection there -- the
SAME enumeration that returns 1 item on Beijing and 5 on Air Taxi, so this
is a measurement, not a silent failure (Q5 unit (1)'s "a 'none found' result
is evidence only if the window could have contained one" rule). Both
duplicate attempts RAISED and created nothing: a nonexistent int source ->
``Object 1 of type Signal Controller does not exist.``; a ``None`` source ->
``Empty values are not permitted here.``; ``GetAll()`` still empty after
both. A zero-controller network genuinely cannot mint its first controller
from a duplicate-based verb -- the identical, already-recorded shape of
``add_priority_rule``'s limitation (Q4 unit 6). Stated, not engineered
around.

**D. Non-fixed-time donors: refused, and here is the evidence base --
including what could NOT be measured.** The COM half is BLOCKED on this
machine: ``LoadNet`` on Boise raises "The network file could not be loaded",
reproducing the block already recorded in DECISIONS.md (2026-07-31) as RBC
controller LICENSING, not a file problem -- and this probe re-confirms it is
not the scenario-management flag either (Boise's own
``<netPara underScenMngm="false">``, statically read here). So **it was not
possible to observe an RBC duplicate live, and this probe does not claim
one.** What IS measured, statically and decisively:

  - every one of Boise's 49 RBC controllers carries its program in an
    EXTERNAL file (``supplyFile1='#data#<no>.prbc'``, one per controller)
    and carries NO ``intSupplyData`` at all -- the attribute is ABSENT, not
    present-and-empty: the literal token ``intSupplyData`` occurs 0 times in
    the whole 2.7 MB Boise .inpx, against exactly 1 populated base64 blob in
    Beijing's. (This probe's own measurement is
    ``len(sc.get('intSupplyData') or '')``, which returns 0 for an absent
    attribute -- hence the looser "empty" this line used to say. Nothing
    downstream depends on the distinction, but absent and empty are not the
    same measurement and this file is cited as evidence.) The
    opaque-external-logic family of 00-EXECUTION-GUIDE.md SS3.2;
  - ``build_shadow_model`` produces **0** ``signal_program_splits`` rows for
    all 49 of them (vs 44 for Beijing's single FIXEDTIME controller and 5
    for Air Taxi's three FIXEDTIMESIMPLE ones), so a copy's inherited timing
    would be invisible to every post-hoc check this codebase owns;
  - constraint #4 (``signal_controller_type_unsupported``, pre-existing)
    already refuses every cycle/offset/green-split edit on such a controller,
    so the copy would be permanently uneditable by this agent anyway.

The remaining step -- "a duplicate would inherit ``supplyFile1`` verbatim
and thus SHARE the donor's external program file" -- is an INFERENCE from
[A2]'s verbatim-copy result on FIXEDTIME, not a measurement on RBC. The
refusal is justified without it (unverifiable + uneditable is already
enough), and the verb's refusal message says the type is unsupported, never
that a specific file would be shared. If a licensed RBC install ever becomes
available, this is the one open question to settle.

Usage: ``python tools/probe_signal_controller_duplicate.py`` -- runs every
part end to end (via internal per-part subprocesses) and prints one PASS/
FAIL summary. Advanced: ``--part {a,b,c,d} --state <path>`` runs exactly one
part against a persisted JSON state file (what the orchestrator shells out
to).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

EXAMPLES_ROOT = Path(r"C:\Users\Public\Documents\PTV Vision\PTV Vissim 2026\Examples Demo")
WORK_ROOT = BACKEND_ROOT.parent / "runtime" / "signal-controller-duplicate-probe"

#: Donors, chosen from this file's own static corpus scan (see [0]).
FIXEDTIME_NET = EXAMPLES_ROOT / "Urban Intersection Beijing.CN"
FIXEDTIMESIMPLE_NET = EXAMPLES_ROOT / "Air Taxi Vertiport"
ZERO_SC_NET = EXAMPLES_ROOT / "Roundabout Billings.US"
NON_FIXEDTIME_NET = EXAMPLES_ROOT / "Signalization RBC Boise.US"

_PART_LABELS = {
    "a": "Part A: FIXEDTIME donor -- arg shape, inheritance, shadow delta, editability",
    "b": "Part B: FIXEDTIMESIMPLE donor -- the other allowed family",
    "c": "Part C: zero-controller network -- the family limitation, live",
    "d": "Part D: non-fixed-time donor -- the evidence base for refusing it",
}
_PART_ORDER = ["a", "b", "c", "d"]


def _log(msg: str) -> None:
    print(msg, flush=True)


def _fresh_work_dir(name: str) -> Path:
    WORK_ROOT.mkdir(parents=True, exist_ok=True)
    for stale in WORK_ROOT.glob(f"{name}_*"):
        try:
            shutil.rmtree(stale)
        except OSError:
            pass
    work_dir = WORK_ROOT / f"{name}_{os.getpid()}"
    work_dir.mkdir(parents=True, exist_ok=True)
    return work_dir


def _clone(example_dir: Path, name: str) -> Path:
    """Copy the whole example folder (sibling assets matter) into our own
    work dir and return the .inpx inside it -- the donor is NEVER opened in
    place (00-EXECUTION-GUIDE.md rule 4)."""
    work_dir = _fresh_work_dir(name)
    shutil.copytree(example_dir, work_dir, dirs_exist_ok=True)
    return sorted(work_dir.glob("*.inpx"))[0]


def _load_state(state_path: Path) -> dict[str, Any]:
    if state_path.exists():
        return json.loads(state_path.read_text(encoding="utf-8"))
    return {"part_results": []}


def _save_state(state_path: Path, state: dict[str, Any]) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")


def _record(state: dict[str, Any], part: str, status: str, detail: str = "") -> None:
    state["part_results"].append({"part": part, "status": status, "detail": detail})
    _log(f"\n>>> [{part}] {status}{(' -- ' + detail) if detail else ''}")


# ---------------------------------------------------------------------------
# [0] Static corpus scan -- no COM, no licence. Chooses/justifies the donors.
# ---------------------------------------------------------------------------


def static_corpus_scan() -> dict[str, Any]:
    _log("=" * 78)
    _log("[0] STATIC corpus scan: <signalController type=...> across Examples Demo")
    _log("=" * 78)
    by_type: dict[str, int] = {}
    zero_sc_networks: list[str] = []
    for inpx in sorted(EXAMPLES_ROOT.rglob("*.inpx")):
        try:
            root = ET.parse(inpx).getroot()
        except Exception as exc:
            _log(f"      {inpx.parent.name}: PARSE FAILED ({type(exc).__name__}) -- skipped")
            continue
        scs = root.findall(".//signalController")
        if not scs:
            zero_sc_networks.append(inpx.parent.name)
        for sc in scs:
            key = (sc.get("type") or "").strip() or "unknown"
            by_type[key] = by_type.get(key, 0) + 1
    _log(f"      controller types across the corpus: {dict(sorted(by_type.items()))}")
    _log(f"      networks with ZERO signal controllers: {sorted(set(zero_sc_networks))}")
    supported = by_type.get("FIXEDTIME", 0) + by_type.get("FIXEDTIMESIMPLE", 0)
    total = sum(by_type.values())
    _log(
        f"      => only {supported}/{total} controllers in the corpus are of a family this "
        "codebase can read a program out of; the majority are external-logic types (see [D])."
    )
    return {"by_type": by_type, "zero_sc_networks": sorted(set(zero_sc_networks))}


def _static_sc_summary(inpx: Path) -> list[dict[str, Any]]:
    """Per-controller (no, type, name, sg count, attributes) straight from the
    XML -- the cheap oracle every live reading below is checked against."""
    root = ET.parse(inpx).getroot()
    out: list[dict[str, Any]] = []
    for sc in root.findall(".//signalController"):
        out.append(
            {
                "no": sc.get("no", ""),
                "type": (sc.get("type") or "").strip(),
                "name": (sc.get("name") or "").strip(),
                "sg_count": len(sc.findall(".//sgs/signalGroup")),
                "int_supply_data_len": len(sc.get("intSupplyData") or ""),
                "attrs": {k: v for k, v in sc.attrib.items() if k != "intSupplyData"},
            }
        )
    return out


# ---------------------------------------------------------------------------
# Live helpers.
# ---------------------------------------------------------------------------


def _read_sc(sc_obj: Any) -> dict[str, Any]:
    """Every attribute a verb might want off a live ISignalController, each
    read defensively -- an attribute that does not exist on this Vissim
    version must show up as a NAMED failure, never as a confident wrong
    value (Q5 unit (1)'s harness-landmine-2 lesson: a bare ``except`` turned
    a nonexistent attribute into a false measurement)."""
    out: dict[str, Any] = {}
    for attr in ("No", "Name", "Type", "CycTm", "Offset", "ProgNo"):
        try:
            out[attr] = sc_obj.AttValue(attr)
        except Exception as exc:
            out[attr] = f"<UNREADABLE: {type(exc).__name__}: {exc}>"
    try:
        out["sg_nos"] = sorted(int(sg.AttValue("No")) for sg in sc_obj.SGs.GetAll())
    except Exception as exc:
        out["sg_nos"] = f"<UNREADABLE: {type(exc).__name__}: {exc}>"
    try:
        blob = sc_obj.AttValue("IntSupplyData")
        out["int_supply_data_len"] = len(blob) if blob else 0
    except Exception as exc:
        out["int_supply_data_len"] = f"<UNREADABLE: {type(exc).__name__}: {exc}>"
    return out


def _sc_ids(net: Any) -> set[int]:
    return {int(s.AttValue("No")) for s in net.SignalControllers.GetAll()}


def _try_duplicate(net: Any, label: str, source: Any, new_key: int) -> tuple[bool, Any, str]:
    """One ``DuplicateSignalController`` attempt, judged by a ``GetAll()``
    id-set diff -- never by the bare return value (``AddPriorityRule``'s
    silent-no-op precedent: it returns None, raises nothing, creates
    nothing). Returns ``(created, returned_object, detail)``."""
    before = _sc_ids(net)
    returned: Any = None
    err = ""
    try:
        returned = net.SignalControllers.DuplicateSignalController(source, new_key)
        _log(f"      [no exception] DuplicateSignalController({label}, {new_key}) -> {returned!r}")
    except Exception as exc:
        err = f"{type(exc).__name__}: {exc}"
        _log(f"      [raised]       DuplicateSignalController({label}, {new_key}) -> {err}")
    after = _sc_ids(net)
    added = sorted(after - before)
    _log(f"      GetAll() id-set diff: before={sorted(before)} after={sorted(after)} added={added}")
    detail = f"added={added}" + (f" err={err}" if err else "")
    return bool(added), returned, detail


def _grouped_diff(before_model: dict[str, Any], after_model: dict[str, Any]) -> dict[str, list[str]]:
    """``diff_shadow_models`` (the SAME machinery ``GuardrailEngine.postwrite``
    runs), grouped by object class and printed."""
    from services.agent.guardrails import diff_shadow_models

    by_class: dict[str, list[str]] = {}
    for entry in diff_shadow_models(before_model, after_model):
        label = f"{entry['object_id']}:{entry['change']}"
        if entry["change"] == "field_changed":
            label += f".{entry.get('field')}"
        by_class.setdefault(entry["object_class"], []).append(label)
    for object_class, entries in sorted(by_class.items()):
        _log(
            f"        {object_class}: {len(entries)} -- {entries[:6]}"
            f"{' ...' if len(entries) > 6 else ''}"
        )
    if not by_class:
        _log("        (no differences at all)")
    return by_class



# ---------------------------------------------------------------------------
# Part A: FIXEDTIME donor.
# ---------------------------------------------------------------------------


def part_a(state: dict[str, Any]) -> bool:
    import pythoncom

    from services.agent.tools_network import build_shadow_model
    from services.vissim_com import VissimController

    _log("=" * 78)
    _log(f"[A] {_PART_LABELS['a']}")
    _log("=" * 78)

    working = _clone(FIXEDTIME_NET, "fixedtime")
    _log(f"    clone: {working}")
    static_before = _static_sc_summary(working)
    _log(f"    static SC summary BEFORE: {static_before}")

    shadow_before = build_shadow_model(working)
    donor_no = int(static_before[0]["no"])
    _log(
        f"    shadow BEFORE: signals={len(shadow_before['signals'])} "
        f"signal_program_splits={len(shadow_before['signal_program_splits'])} "
        f"(donor SC {donor_no}: {static_before[0]['sg_count']} signal groups)"
    )

    findings: dict[str, Any] = {"network": FIXEDTIME_NET.name, "donor_no": donor_no}

    pythoncom.CoInitialize()
    controller = VissimController()
    try:
        controller.connect(visible=False, launch_if_missing=True, attach_to_running=False)
        controller.load_network(str(working))
        net = controller.vissim.Net

        donor_before = _read_sc(net.SignalControllers.ItemByKey(donor_no))
        _log(f"\n[A.1] donor SC {donor_no} live read: {donor_before}")
        findings["donor_before"] = donor_before

        _log("\n[A.2] argument shape -- which does DuplicateSignalController accept?")
        int_key = max(_sc_ids(net)) + 1
        int_ok, _i, int_detail = _try_duplicate(net, f"{donor_no} (int key)", donor_no, int_key)
        obj_key = max(_sc_ids(net)) + 1
        obj_ok, _o, obj_detail = _try_duplicate(
            net, "<COM object>", net.SignalControllers.ItemByKey(donor_no), obj_key
        )
        findings.update(
            {
                "int_arg_shape_works": int_ok,
                "obj_arg_shape_works": obj_ok,
                "int_arg_detail": int_detail,
                "obj_arg_detail": obj_detail,
            }
        )
        if not int_ok:
            _log("      The INT form did not create anything -- the verb's chosen shape is wrong.")
            return False
        copy_no = int_key

        copy_read = _read_sc(net.SignalControllers.ItemByKey(copy_no))
        _log(f"\n[A.3] copy SC {copy_no} live read: {copy_read}")
        findings["copy_read"] = copy_read
        inherited = {}
        for attr in ("Type", "CycTm", "Offset", "ProgNo", "sg_nos", "int_supply_data_len"):
            same = donor_before.get(attr) == copy_read.get(attr)
            inherited[attr] = same
            _log(f"      {attr}: donor={donor_before.get(attr)!r} copy={copy_read.get(attr)!r} "
                 f"-> {'IDENTICAL' if same else 'DIFFERENT'}")
        findings["inherited"] = inherited

        new_name = f"Probe copy of SC {donor_no}"
        copy_obj = net.SignalControllers.ItemByKey(copy_no)
        copy_obj.SetAttValue("Name", new_name)
        readback = copy_obj.AttValue("Name")
        donor_after = _read_sc(net.SignalControllers.ItemByKey(donor_no))
        _log(f"\n[A.4] rename the copy -> wrote {new_name!r}, read back {readback!r}")
        _log(f"      donor name after the copy's rename: {donor_after.get('Name')!r} "
             f"(was {donor_before.get('Name')!r}) -- an ALIAS would show the new name here")
        findings["rename_ok"] = readback == new_name
        findings["donor_name_unchanged"] = donor_after.get("Name") == donor_before.get("Name")

        # Drop the arg-shape control copy BEFORE saving and BEFORE any
        # simulation step (landmine A5) so the measured delta below is
        # exactly ONE duplicate's worth.
        if obj_ok:
            net.SignalControllers.RemoveSignalController(net.SignalControllers.ItemByKey(obj_key))
            _log(f"\n[A.5] removed the arg-shape control copy SC {obj_key} -- the saved file "
                 "carries exactly ONE duplicate, so the delta below is per-call.")
            findings["remove_control_copy_ok"] = True

        saved = working.with_name(working.stem + "_saved.inpx")
        controller.save_net_as(saved)
        _log(f"      saved: {saved}")

        # -- editability of the COPY's own program (Q4 unit (6)'s "prove the
        # new object is genuinely editable, not just present" precedent).
        sg_no = int(copy_read["sg_nos"][0])
        write_info = controller.set_signal_green_split(copy_no, sg_no, 7.0, 33.0)
        split_readback = controller.get_signal_green_split(copy_no, sg_no)
        _log(f"\n[A.6] set_signal_green_split on the COPY (SC {copy_no} SG {sg_no}) -> "
             f"{write_info}; readback={split_readback}")
        findings["copy_green_split_write"] = write_info
        findings["copy_green_split_readback"] = split_readback
        findings["copy_editable"] = (
            abs(float(split_readback["green_start_s"]) - 7.0) < 1e-6
            and abs(float(split_readback["green_end_s"]) - 33.0) < 1e-6
        )
        saved_edited = working.with_name(working.stem + "_saved_edited.inpx")
        controller.save_net_as(saved_edited)
        _log(f"      saved (after the edit): {saved_edited}")

        # -- run survival, LAST (it puts Vissim "in simulation", which blocks
        # further structural edits -- landmine A5).
        try:
            controller.run_single_step()
            findings["run_single_step_ok"] = True
            _log("\n[A.7] RunSingleStep with the duplicate present: OK")
        except Exception as exc:
            findings["run_single_step_ok"] = False
            _log(f"\n[A.7] RunSingleStep FAILED: {type(exc).__name__}: {exc}")

        # -- landmine A5, demonstrated deliberately AFTER both artifacts are
        # already on disk so it cannot pollute a measurement.
        _log("\n[A.8] landmine check -- are structural controller edits still allowed once a "
             "simulation has started?")
        try:
            net.SignalControllers.RemoveSignalController(net.SignalControllers.ItemByKey(copy_no))
            findings["structural_edit_during_simulation"] = "ALLOWED (unexpected)"
            _log("      RemoveSignalController after RunSingleStep: ALLOWED (unexpected).")
        except Exception as exc:
            findings["structural_edit_during_simulation"] = f"{type(exc).__name__}: {exc}"
            _log(f"      RemoveSignalController after RunSingleStep: REFUSED -- {exc}")
    finally:
        try:
            controller.disconnect()
        except Exception:
            pass
        try:
            pythoncom.CoUninitialize()
        except Exception:
            pass

    # -- THE measurement the guardrail exemption is derived from.
    shadow_after = build_shadow_model(saved)
    _log(
        f"\n[A.9] shadow AFTER one duplicate: signals={len(shadow_after['signals'])} "
        f"signal_program_splits={len(shadow_after['signal_program_splits'])}"
    )
    _log("      diff_shadow_models (the SAME machinery GuardrailEngine.postwrite runs):")
    by_class = _grouped_diff(shadow_before, shadow_after)
    findings["diff_classes"] = {k: len(v) for k, v in by_class.items()}
    findings["diff_sample"] = {k: v[:6] for k, v in by_class.items()}

    from services.agent.guardrails import diff_shadow_models

    raw = diff_shadow_models(shadow_before, shadow_after)
    added_classes = sorted({e["object_class"] for e in raw if e["change"] == "added"})
    other = [e for e in raw if e["change"] != "added"]
    _log(f"\n      => object classes that gained rows: {added_classes}")
    _log(f"      => non-'added' differences (must be EMPTY for a clean exemption): {other}")
    findings["added_classes"] = added_classes
    findings["other_change_count"] = len(other)

    # -- and the edit's own delta: only the copy's own split may move.
    shadow_edited = build_shadow_model(saved_edited)
    _log("\n[A.10] diff of the GREEN-SPLIT edit alone (saved -> saved_edited):")
    edit_by_class = _grouped_diff(shadow_after, shadow_edited)
    findings["edit_diff"] = {k: v for k, v in edit_by_class.items()}

    state["part_a"] = findings
    return (
        bool(findings.get("int_arg_shape_works"))
        and bool(findings.get("rename_ok"))
        and bool(findings.get("donor_name_unchanged"))
        and bool(findings.get("copy_editable"))
        and added_classes == ["signal_program_splits", "signals"]
        and not other
    )


# ---------------------------------------------------------------------------
# Part B: FIXEDTIMESIMPLE donor -- the other allowed family.
# ---------------------------------------------------------------------------


def part_b(state: dict[str, Any]) -> bool:
    import pythoncom

    from services.agent.guardrails import diff_shadow_models
    from services.agent.tools_network import build_shadow_model
    from services.vissim_com import VissimController

    _log("=" * 78)
    _log(f"[B] {_PART_LABELS['b']}")
    _log("=" * 78)

    working = _clone(FIXEDTIMESIMPLE_NET, "fixedtimesimple")
    _log(f"    clone: {working}")
    static_before = _static_sc_summary(working)
    donor = next(sc for sc in static_before if sc["type"] == "FIXEDTIMESIMPLE")
    donor_no = int(donor["no"])
    _log(f"    donor: SC {donor_no} type={donor['type']} name={donor['name']!r} "
         f"sgs={donor['sg_count']} intSupplyData_len={donor['int_supply_data_len']} "
         "(FIXEDTIMESIMPLE keeps its program in plain endRed/endGreen attributes, NOT the blob)")

    shadow_before = build_shadow_model(working)
    _log(
        f"    shadow BEFORE: signals={len(shadow_before['signals'])} "
        f"signal_program_splits={len(shadow_before['signal_program_splits'])}"
    )

    findings: dict[str, Any] = {
        "network": FIXEDTIMESIMPLE_NET.name,
        "donor_no": donor_no,
        "donor_sg_count": donor["sg_count"],
    }

    pythoncom.CoInitialize()
    controller = VissimController()
    try:
        controller.connect(visible=False, launch_if_missing=True, attach_to_running=False)
        controller.load_network(str(working))
        net = controller.vissim.Net

        donor_before = _read_sc(net.SignalControllers.ItemByKey(donor_no))
        _log(f"\n[B.1] donor live read: {donor_before}")
        findings["donor_before"] = donor_before

        new_key = max(_sc_ids(net)) + 1
        created, _obj, detail = _try_duplicate(net, f"{donor_no} (int key)", donor_no, new_key)
        findings["int_arg_shape_works"] = created
        findings["detail"] = detail
        if not created:
            _log("      FIXEDTIMESIMPLE duplicate did NOT create anything.")
            state["part_b"] = findings
            return False

        copy_read = _read_sc(net.SignalControllers.ItemByKey(new_key))
        _log(f"\n[B.2] copy SC {new_key} live read: {copy_read}")
        findings["copy_read"] = copy_read
        findings["inherited_sg_nos"] = donor_before.get("sg_nos") == copy_read.get("sg_nos")
        findings["inherited_type"] = donor_before.get("Type") == copy_read.get("Type")

        name = f"Probe copy of SC {donor_no}"
        copy_obj = net.SignalControllers.ItemByKey(new_key)
        copy_obj.SetAttValue("Name", name)
        findings["rename_ok"] = copy_obj.AttValue("Name") == name

        saved = working.with_name(working.stem + "_saved.inpx")
        controller.save_net_as(saved)
        _log(f"\n[B.3] saved: {saved}")
    finally:
        try:
            controller.disconnect()
        except Exception:
            pass
        try:
            pythoncom.CoUninitialize()
        except Exception:
            pass

    shadow_after = build_shadow_model(saved)
    _log(
        f"\n[B.4] shadow AFTER: signals={len(shadow_after['signals'])} "
        f"signal_program_splits={len(shadow_after['signal_program_splits'])}"
    )
    _log("      diff_shadow_models:")
    by_class = _grouped_diff(shadow_before, shadow_after)
    raw = diff_shadow_models(shadow_before, shadow_after)
    added_classes = sorted({e["object_class"] for e in raw if e["change"] == "added"})
    other = [e for e in raw if e["change"] != "added"]
    _log(f"\n      => object classes that gained rows: {added_classes}")
    _log(f"      => non-'added' differences: {other}")
    findings["diff_classes"] = {k: len(v) for k, v in by_class.items()}
    findings["added_classes"] = added_classes
    findings["other_change_count"] = len(other)

    state["part_b"] = findings
    return bool(findings.get("int_arg_shape_works")) and added_classes == [
        "signal_program_splits",
        "signals",
    ] and not other


# ---------------------------------------------------------------------------
# Part C: zero-controller network -- the family limitation, live.
# ---------------------------------------------------------------------------


def part_c(state: dict[str, Any]) -> bool:
    import pythoncom

    from services.vissim_com import VissimController

    _log("=" * 78)
    _log(f"[C] {_PART_LABELS['c']}")
    _log("=" * 78)

    working = _clone(ZERO_SC_NET, "zero-sc")
    _log(f"    clone: {working}")
    static_before = _static_sc_summary(working)
    _log(f"    static SC summary: {static_before} (expected: empty)")

    findings: dict[str, Any] = {"network": ZERO_SC_NET.name, "static_sc_count": len(static_before)}

    pythoncom.CoInitialize()
    controller = VissimController()
    try:
        controller.connect(visible=False, launch_if_missing=True, attach_to_running=False)
        controller.load_network(str(working))
        net = controller.vissim.Net

        # The enumeration is PROVEN capable of returning items -- Parts A/B's
        # identical call returns 1 on Beijing and 5 on Air Taxi. An empty
        # result here is therefore a MEASUREMENT, not a silent failure (Q5
        # unit (1)'s "a 'none found' result is evidence only if the window
        # could have contained one" house rule).
        ids = _sc_ids(net)
        _log(f"\n[C.1] Net.SignalControllers.GetAll() -> {len(ids)} controller(s): {sorted(ids)}")
        findings["live_sc_count"] = len(ids)
        findings["available_signal_controller_nos"] = controller.available_signal_controller_nos()
        _log("      VissimController.available_signal_controller_nos() -> "
             f"{findings['available_signal_controller_nos']}")

        _log("\n[C.2] can DuplicateSignalController mint the FIRST controller with no donor?")
        int_created, _o1, int_detail = _try_duplicate(net, "1 (nonexistent int key)", 1, 1)
        none_created, _o2, none_detail = _try_duplicate(net, "None", None, 1)
        findings["duplicate_from_nonexistent_source_created"] = int_created
        findings["duplicate_from_none_source_created"] = none_created
        findings["detail"] = {"int": int_detail, "none": none_detail}
        findings["sc_count_after_attempts"] = len(_sc_ids(net))
    finally:
        try:
            controller.disconnect()
        except Exception:
            pass
        try:
            pythoncom.CoUninitialize()
        except Exception:
            pass

    _log(
        f"\n[C.3] VERDICT: zero controllers before, {findings['sc_count_after_attempts']} after "
        "both attempts -- the duplicate path genuinely cannot mint a network's FIRST signal "
        "controller. Same shape as add_priority_rule's own recorded limitation (Q4 unit 6). "
        "The verb states this as a clear error; it is not engineered around."
    )
    state["part_c"] = findings
    return findings["live_sc_count"] == 0 and findings["sc_count_after_attempts"] == 0


# ---------------------------------------------------------------------------
# Part D: non-fixed-time donor -- the evidence base for refusing it.
# ---------------------------------------------------------------------------


def part_d(state: dict[str, Any]) -> bool:
    import pythoncom

    from services.agent.tools_network import build_shadow_model
    from services.vissim_com import VissimController

    _log("=" * 78)
    _log(f"[D] {_PART_LABELS['d']}")
    _log("=" * 78)

    working = _clone(NON_FIXEDTIME_NET, "non-fixedtime")
    _log(f"    clone: {working}")

    root = ET.parse(working).getroot()
    net_para = root.find(".//netPara")
    under_scen = net_para.get("underScenMngm") if net_para is not None else "<no netPara>"
    static_before = _static_sc_summary(working)
    donor = static_before[0]
    _log(f"\n[D.1] STATIC: {len(static_before)} controller(s), donor SC {donor['no']} "
         f"type={donor['type']} name={donor['name']!r}")
    _log(f"      donor XML attributes (intSupplyData elided): {donor['attrs']}")
    external_refs = {
        k: v for k, v in donor["attrs"].items()
        if v and any(token in k.lower() for token in ("file", "prog"))
    }
    _log(f"      EXTERNAL-program references on the donor: {external_refs}")
    _log(f"      donor intSupplyData length: {donor['int_supply_data_len']} "
         "(0 = there is no in-file program at all -- the timing lives outside the .inpx)")

    # Not just the donor: how many of this network's controllers carry an
    # external supply file AND no in-file program at all? (A one-controller
    # observation would not support a family-wide refusal.)
    with_supply_file = [sc for sc in static_before if (sc["attrs"].get("supplyFile1") or "")]
    with_blob = [sc for sc in static_before if sc["int_supply_data_len"] > 0]
    _log(f"      across ALL {len(static_before)} controllers here: "
         f"{len(with_supply_file)} carry an external supplyFile1, {len(with_blob)} carry an "
         "in-file intSupplyData program")

    shadow = build_shadow_model(working)
    _log(
        f"\n[D.2] shadow model: signals={len(shadow['signals'])} "
        f"signal_program_splits={len(shadow['signal_program_splits'])}"
    )
    _log("      => this codebase can read back ZERO timing rows for this whole controller "
         "family, so a copy's inherited program would be unverifiable by any postwrite check "
         "we own (compare: Beijing's ONE FIXEDTIME controller yields 44 rows).")

    findings: dict[str, Any] = {
        "network": NON_FIXEDTIME_NET.name,
        "sc_count": len(static_before),
        "donor_no": donor["no"],
        "donor_type": donor["type"],
        "donor_external_refs": external_refs,
        "donor_int_supply_data_len": donor["int_supply_data_len"],
        "controllers_with_external_supply_file": len(with_supply_file),
        "controllers_with_in_file_program": len(with_blob),
        "shadow_signals": len(shadow["signals"]),
        "shadow_signal_program_splits": len(shadow["signal_program_splits"]),
        "under_scen_mngm": under_scen,
    }

    # The COM half: attempted honestly, expected to be BLOCKED on this
    # machine (DECISIONS.md 2026-07-31 records Boise as an RBC LICENSING
    # block). Reported as a recorded limitation, not as a probe failure --
    # and the scenario-management hypothesis is ruled out statically first.
    _log(f"\n[D.3] COM load attempt (expected BLOCKED). netPara underScenMngm={under_scen!r} "
         "-- so this is NOT the scenario-management class of load failure "
         "(DECISIONS.md 2026-07-31).")
    pythoncom.CoInitialize()
    controller = VissimController()
    try:
        controller.connect(visible=False, launch_if_missing=True, attach_to_running=False)
        try:
            controller.load_network(str(working))
            findings["com_load"] = "LOADED"
            ids = _sc_ids(controller.vissim.Net)
            _log(f"      LOADED after all -- {len(ids)} controllers live. The RBC duplicate "
                 "question can now be settled; see this module's docstring [D].")
        except Exception as exc:
            findings["com_load"] = f"BLOCKED: {type(exc).__name__}: {exc}"
            _log(f"      BLOCKED as recorded: {exc}")
    finally:
        try:
            controller.disconnect()
        except Exception:
            pass
        try:
            pythoncom.CoUninitialize()
        except Exception:
            pass

    _log(
        "\n[D.4] VERDICT (policy, argued from the measurements above -- COM itself was never "
        "asked to refuse anything): add_signal_controller REFUSES a donor whose type is not "
        "FIXEDTIME/FIXEDTIMESIMPLE. Grounds: (1) zero readable timing rows, so no postwrite "
        "check can verify what the copy inherited; (2) constraint #4 already refuses every "
        "cycle/offset/green-split edit on that family, so the copy would be permanently "
        "uneditable by this agent; (3) the donor's program lives in an external file "
        "(supplyFile1) that COM cannot reach. NOT claimed: that a copy would share that file "
        "-- that follows from Part A's verbatim-copy result but was never observed on an RBC "
        "controller, because the COM load is blocked on this machine."
    )
    state["part_d"] = findings
    # Static evidence is the deliverable here; a blocked COM load is an
    # expected, already-recorded environment limitation, not a failure.
    return findings["shadow_signal_program_splits"] == 0 and bool(external_refs)


# ---------------------------------------------------------------------------
# Orchestration.
# ---------------------------------------------------------------------------

_PARTS = {"a": part_a, "b": part_b, "c": part_c, "d": part_d}


def _run_one_part(part: str, state_path: Path) -> int:
    state = _load_state(state_path)
    try:
        ok = _PARTS[part](state)
    except Exception as exc:
        import traceback

        traceback.print_exc()
        _record(state, part, "FAIL", f"{type(exc).__name__}: {exc}")
        _save_state(state_path, state)
        return 1
    _record(state, part, "PASS" if ok else "FAIL", f"pid={os.getpid()}")
    _save_state(state_path, state)
    return 0 if ok else 1


def _run_all() -> int:
    WORK_ROOT.mkdir(parents=True, exist_ok=True)
    state_path = WORK_ROOT / "state.json"
    if state_path.exists():
        state_path.unlink()

    state = _load_state(state_path)
    state["static_scan"] = static_corpus_scan()
    _save_state(state_path, state)

    failures: list[str] = []
    for part in _PART_ORDER:
        _log(f"\n\n########## spawning {_PART_LABELS[part]} ##########")
        proc = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "--part", part, "--state", str(state_path)],
            cwd=str(BACKEND_ROOT),
        )
        state = _load_state(state_path)
        recorded = [r for r in state["part_results"] if r["part"] == part]
        if not recorded:
            # The subprocess died before recording its own outcome -- the
            # native-crash case gap (1) exists for. Recorded HERE so a crash
            # can never read as a silent pass.
            _record(state, part, "CRASHED", f"subprocess exit={proc.returncode}, no state record")
            _save_state(state_path, state)
            failures.append(part)
        elif recorded[-1]["status"] != "PASS":
            failures.append(part)

    _log("\n" + "=" * 78)
    _log("SUMMARY")
    _log("=" * 78)
    for record in _load_state(state_path)["part_results"]:
        _log(f"  [{record['part']}] {record['status']}  {record['detail']}")
    _log(f"\nstate file: {state_path}")
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Q5 unit (2) signal-controller duplicate probe")
    parser.add_argument("--part", choices=_PART_ORDER)
    parser.add_argument("--state", type=Path)
    args = parser.parse_args()
    if args.part:
        if args.state is None:
            parser.error("--part requires --state")
        return _run_one_part(args.part, args.state)
    return _run_all()


if __name__ == "__main__":
    raise SystemExit(main())
