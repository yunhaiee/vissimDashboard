"""Unit #70 LIVE CHECK: does Vissim accept the time-of-day switching schedule?

**S7 SETTLED 2026-08-26 — the assumption was CORRECT.** This tool is what
settled it, and it stays as the instrument that RE-VERIFIES it after a Vissim
upgrade. Result of that run, on the network unit #70 emits:

* controller 1 — ``s7="KEPT_VERBATIM"``, ``differences=[]``: Vissim returned the
  schedule with every element name, attribute name and value identical to what
  was emitted, the explicit ``from="0"`` midnight entry included;
* ``s7b_programs_survived=true``, both programs present (ids 1 and 2, cycletime
  78000 ms each);
* no controller missing from the save and none added;
* ``LoadNet`` and ``SaveNetAs`` both succeeded, and Vissim wrote no ``.err``.

The named acceptance follow-up of what was then ASSUMPTION S7, written at the
same time as the assumption — the C2/S6 precedent, where recording an assumption
honestly turned closing it into a RUN rather than an investigation. Its verdicts
stay meaningful: a future Vissim that renames or drops the element reports
REWRITTEN or DROPPED here rather than failing silently in a network.

**It fixes nothing.** If the emission is wrong, the finding names the fix and
stops there.

WHAT IS ALREADY MEASURED, and therefore NOT under test here
-----------------------------------------------------------
* A controller may carry SEVERAL ``<prog>`` in one ``<progs>``. Measured offline
  on a Vissim-authored network (one of its controllers carries 2), and settled
  live long before this unit: ``services.signal_parser`` follows ``progNo="2"``
  of programs ``{1, 2}`` on Urban Intersection Beijing.CN against the
  SimBreakAt/SigState oracle.
* ``<dailyProgLists>`` EXISTS as the last child of ``<sc>``. All 6 Vissim-authored
  controllers of the consultant network carry it — every one of them EMPTY,
  which is exactly why its content had to be assumed.
* Vissig states durations in milliseconds.

THE QUESTION S7 ASKED — and its answer
--------------------------------------
Every controller on disk carries ``<dailyProgLists>`` empty, so nothing showed
what belongs inside it. ``signals._append_daily_prog_lists`` writes::

    <dailyProgList id="1" name="TOD">
      <dailyProg from="<ms from midnight>" prog_id="<n>" />
    </dailyProgList>

The element and attribute NAMES were the guess; their FORM followed measured
Vissig rules (containers pluralise over their content — ``sgs``/``sg``,
``progs``/``prog``; cross-references take an ``_id`` suffix — ``<sg sg_id=...>``),
which is why the guess landed. **Vissim returned all of them unchanged.** The
legs below are how that was established and how it is checked again.

  S7-a  **Save/reload.** Open a generated TOD network, ``SaveNetAs``, decode the
        controller's ``intSupplyData`` again. Vissim REWRITES the element (its
        own names are then the answer, verbatim) / DROPS it (names wrong — set
        ``SignalSettings.emit_tod_schedule_element=False`` and drive the schedule
        through the COM API instead) / REFUSES the file (malformed, not merely
        misnamed).
        The comparison is STRUCTURAL — grandchild tag, attribute names AND
        values, entry by entry. A classifier that judged from the container name
        alone would report "S7 correct" for an attribute rename, an element
        rename, or half the entries being silently dropped, and would retire the
        assumption while the emission was still wrong.

        The first entry is always ``from="0"`` (the program running at midnight,
        stated rather than implied — see ``signals._build_tod_programs``), so it
        is checked like any other: an implementation that ran nothing until the
        first switch shows up here as a dropped or altered entry 0.

  S7-b  **Do the programs survive?** Independent of the schedule: both ``<prog>``
        elements must come back with their own cycle times. If the schedule
        element is dropped but the programs survive, the fallback path is proven
        good and the unit still ships its measured half.
  S7-c  **What does COM call it?** Dump the signal controller's COM attribute
        table and look for anything daily-programme shaped, so a name Vissim
        exposes is FOUND rather than assumed absent. This is the same move S6-c
        made when it dumped the detector's whole attribute table.

HOW TO RUN IT

This tool GENERATES NOTHING and OPENS NOTHING BY ITSELF. It compares two files
the caller already has: the ``.inpx`` this repo emitted, and the same network
after Vissim's own ``SaveNetAs``. Producing that pair is the caller's step::

    # 1. emit a TOD network (opt in at staging: time_of_day_switching=true)
    # 2. open it in Vissim and SaveNetAs to a second path
    # 3. compare:
    python backend/tools/genesis_tod_live_check.py \
        --emitted runtime/tod/emitted.inpx \
        --saved   runtime/tod/saved.inpx \
        --out     runtime/tod-live-check

``--skip-com`` makes the run fully offline (S7-a and S7-b only). Without it the
tool additionally opens ``--emitted`` over COM for the S7-c attribute census,
always with ``attach_to_running=False`` (00-EXECUTION-GUIDE: ``GetActiveObject``
would grab a human's open GUI and can destroy their work). It writes only its
own JSON report and never modifies either input.
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


def decode_blobs(inpx: Path) -> list[tuple[str, str]]:
    """``[(controller no, decoded <sc> document)]`` for every controller."""
    raw = inpx.read_bytes().decode("utf-8", errors="replace")
    out: list[tuple[str, str]] = []
    for match in re.finditer(r"<signalController\b([^>]*)>", raw):
        attrs = match.group(1)
        no = (re.search(r'\bno="([^"]*)"', attrs) or [None, "?"])[1]
        blob = re.search(r'intSupplyData="([^"]*)"', attrs)
        if not blob or not blob.group(1).strip():
            out.append((no, ""))
            continue
        out.append((no, base64.b64decode(blob.group(1)).decode("utf-8", errors="replace")))
    return out


def describe_schedule(document: str) -> dict[str, Any]:
    """What one decoded ``<sc>`` says about its programs and its schedule."""
    if not document.strip():
        return {"parsed": False, "reason": "empty intSupplyData"}
    try:
        root = ET.fromstring(document)
    except ET.ParseError as exc:
        return {"parsed": False, "reason": f"not well-formed XML: {exc}"}

    progs = root.findall("./progs/prog")
    container = root.find("./dailyProgLists")
    entries: list[dict[str, str | None]] = []
    child_tags: list[str] = []
    if container is not None:
        for child in container:
            child_tags.append(child.tag)
            for grandchild in child:
                entries.append(
                    {"tag": grandchild.tag, **{k: v for k, v in grandchild.attrib.items()}}
                )
    return {
        "parsed": True,
        "sc_children": [child.tag for child in root],
        "programs": [
            {"id": p.get("id"), "cycletime": p.get("cycletime"), "name": p.get("name")}
            for p in progs
        ],
        "daily_container_present": container is not None,
        "daily_child_tags": child_tags,
        "daily_entries": entries,
    }


def _entry_shape(entries: list[dict[str, Any]]) -> list[tuple[str, tuple[str, ...]]]:
    """Each entry as ``(tag, sorted attribute NAMES)`` — the part a rename hits."""
    return [
        (str(entry.get("tag")), tuple(sorted(k for k in entry if k != "tag")))
        for entry in entries
    ]


def _structural_diff(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    """Every way the returned schedule differs from the emitted one, named.

    This is the whole guard against a false KEPT_VERBATIM. An earlier draft
    judged the outcome from ``daily_child_tags`` alone — the ONE name that is
    close to certain, because ``dailyProgList`` is the singular of a container
    measured on Vissim's own files. Under that test an attribute rename
    (``from``→``start``, ``prog_id``→``progNo``), a grandchild rename
    (``dailyProg``→``entry``) and half the entries being dropped ALL reported
    "S7 correct, nothing to fix" — closing the assumption on the strength of the
    only name that was never really in doubt.
    """
    differences: list[str] = []
    tags_before = before.get("daily_child_tags") or []
    tags_after = after.get("daily_child_tags") or []
    if tags_before != tags_after:
        differences.append(f"list element renamed: {tags_before} -> {tags_after}")

    entries_before = before.get("daily_entries") or []
    entries_after = after.get("daily_entries") or []
    if len(entries_before) != len(entries_after):
        differences.append(
            f"entry count changed: {len(entries_before)} -> {len(entries_after)}"
        )

    shape_before = _entry_shape(entries_before)
    shape_after = _entry_shape(entries_after)
    renamed_tags = {b[0] for b in shape_before} ^ {a[0] for a in shape_after}
    if renamed_tags:
        differences.append(
            f"entry element renamed: {sorted({b[0] for b in shape_before})} -> "
            f"{sorted({a[0] for a in shape_after})}"
        )
    keys_before = {key for _, keys in shape_before for key in keys}
    keys_after = {key for _, keys in shape_after for key in keys}
    if keys_before != keys_after:
        differences.append(
            f"attribute names changed: {sorted(keys_before)} -> {sorted(keys_after)}"
        )

    for index, (left, right) in enumerate(zip(entries_before, entries_after)):
        if left != right:
            differences.append(f"entry {index} values changed: {left} -> {right}")
    return differences


def verdict(before: dict[str, Any], after: dict[str, Any] | None) -> dict[str, Any]:
    """S7-a and S7-b, as a finding rather than a pass/fail.

    ``after`` is ``None`` when Vissim's save carries no controller with this
    one's number at all — a DELETED controller, which is a finding in its own
    right and must never be a silent skip.
    """
    if after is None:
        return {
            "s7": "CONTROLLER_MISSING",
            "meaning": "Vissim's save carries no controller with this number. The controller "
            "was not merely stripped of its schedule — it is gone.",
            "action": "Treat as a REFUSAL of the emitted file: re-run with "
            "emit_tod_schedule_element=False to find out whether the schedule element caused it.",
            "s7b_programs_survived": False,
            "programs_before": before.get("programs"),
            "programs_after": None,
        }
    if not after.get("parsed"):
        return {
            "s7": "REFUSED_OR_UNPARSEABLE",
            "meaning": "Vissim did not give the controller back in a readable form ("
            f"{after.get('reason')}). The guessed schedule element is malformed, not merely "
            "misnamed.",
            "action": "Set SignalSettings.emit_tod_schedule_element=False and re-run; if that "
            "round-trips, the container's CONTENT is the whole problem.",
            "s7b_programs_survived": False,
            "programs_before": before.get("programs"),
            "programs_after": None,
        }

    programs_survived = (before.get("programs") or []) == (after.get("programs") or [])
    entries_before = before.get("daily_entries") or []
    entries_after = after.get("daily_entries") or []
    differences = _structural_diff(before, after)

    if not entries_before:
        outcome = "NOTHING_TO_JUDGE"
        meaning = "No schedule was emitted for this controller, so this run says nothing about S7."
        action = "Re-run against a network that actually opted in to switching."
    elif entries_after and not differences:
        outcome = "KEPT_VERBATIM"
        meaning = (
            "Vissim returned the schedule with every element name, attribute name and value "
            "identical to what was emitted. ASSUMPTION S7 was correct."
        )
        action = "Record S7 as settled; nothing to fix."
    elif entries_after:
        outcome = "REWRITTEN"
        meaning = (
            "Vissim returned a schedule that differs from the emitted one. Whatever it returned "
            "IS the answer — the differences below are the correction."
        )
        action = (
            "Copy the observed element/attribute names and values verbatim into "
            "signals._append_daily_prog_lists, then delete the assumption."
        )
    else:
        outcome = "DROPPED"
        meaning = (
            "Vissim read the file but discarded the schedule. The names are wrong; the "
            "programs themselves came back unchanged."
            if programs_survived
            else "Vissim discarded the schedule AND changed or lost programs — worse than a "
            "naming miss."
        )
        action = (
            "Set emit_tod_schedule_element=False (programs only, which is measured-safe) and "
            "drive the switching through the COM API instead."
        )

    return {
        "s7": outcome,
        "meaning": meaning,
        "action": action,
        "differences": differences,
        "s7b_programs_survived": programs_survived,
        "programs_before": before.get("programs"),
        "programs_after": after.get("programs"),
    }


#: Attribute labels that would be a daily-programme list under some other name.
_DAILY_SHAPED = re.compile(r"dai?ly|prog.*list|list.*prog|tod|timeofday", re.IGNORECASE)


def com_attribute_census(inpx: Path) -> dict[str, Any]:
    """S7-c: what does COM itself call a daily programme list, if anything?

    Reuses ``genesis_signals_live_check._attribute_table`` rather than reading
    the table its own way. That helper carries a measurement this one would
    otherwise have had to repeat and got wrong: MEASURED 2026-08-07, an ITEM
    wrapper exposes no ``Attributes`` property at all — the table hangs off the
    CONTAINER (``Net.SignalControllers.Attributes``) — and it tries both, in
    that order, recording which answered.

    Two stale COM names were fixed here after the live run:
    ``VissimController.open_net`` (never existed; the API is ``load_network``)
    and ``item.AttributesHelp`` (an item has no such property). Both would have
    raised only on a machine with Vissim installed, which is why the guard in
    ``test_genesis_tod_live_check`` now checks every attribute this function
    reaches for against the real class.
    """
    try:
        from services.vissim_com import VissimController
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "reason": f"vissim_com not importable: {exc}"}
    try:
        from tools.genesis_signals_live_check import _attribute_table
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "reason": f"attribute-table helper unavailable: {exc}"}

    controller = VissimController()
    try:
        # attach_to_running=False, ALWAYS: GetActiveObject would grab a human's
        # open GUI and can destroy their work.
        controller.connect(visible=False, launch_if_missing=True, attach_to_running=False)
        controller.load_network(str(inpx))
        collection = controller.vissim.Net.SignalControllers
        item = None
        try:
            item = list(collection.GetAll())[0]
        except Exception:  # noqa: BLE001 - the container table is the fallback
            item = None
        table = _attribute_table(item, collection)
        codes = table.get("codes") or []
        return {
            "available": True,
            "read_via": table.get("read_via"),
            "attribute_count": table.get("count"),
            "daily_shaped_attributes": sorted(
                {code for code in codes if _DAILY_SHAPED.search(str(code))}
            ),
            "error": table.get("error"),
        }
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "reason": f"{type(exc).__name__}: {exc}"}
    finally:
        try:
            controller.disconnect()
        except Exception:  # noqa: BLE001
            pass


def pair_controllers(
    emitted: Path, saved: Path
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], list[str]]:
    """``(before, after, switching)`` — both files read and JOINED BY CONTROLLER NUMBER.

    Extracted from :func:`main` so the join can be tested. It carries the two
    wiring decisions this instrument's correctness rests on, and both are
    invisible in a report that looks plausible either way:

    * **Pairing is by ``no``, never positional.** ``SaveNetAs`` may reorder
      ``<signalController>`` elements, and a positional pair would then compare
      one controller's schedule against another's — reporting a confident,
      wrong verdict for both, with no symptom in the output.
    * **A controller absent from the save is KEPT, as ``None``.** ``after`` is
      looked up with ``.get`` at the call site precisely so a deleted controller
      reaches :func:`verdict` and becomes ``CONTROLLER_MISSING``. Building the
      findings with a comprehension that skips missing keys would drop the most
      serious outcome the probe can observe — Vissim rejecting the controller
      outright — and the run would read as a clean pass.

    ``switching`` lists only the controllers that were EMITTED with a schedule;
    a controller the save invented is reported in ``main``'s counts but is not
    something S7 has anything to say about.
    """
    before = {no: describe_schedule(doc) for no, doc in decode_blobs(emitted)}
    after = {no: describe_schedule(doc) for no, doc in decode_blobs(saved)}
    switching = sorted(no for no, entry in before.items() if entry.get("daily_entries"))
    return before, after, switching


def build_report(emitted: Path, saved: Path) -> dict[str, Any]:
    """The whole offline half of the run: S7-a and S7-b for every controller."""
    before, after, switching = pair_controllers(emitted, saved)
    report: dict[str, Any] = {
        "emitted": str(emitted),
        "saved": str(saved),
        "controllers_emitted": sorted(before),
        "controllers_saved": sorted(after),
        "controllers_missing_from_save": sorted(set(before) - set(after)),
        "controllers_added_by_save": sorted(set(after) - set(before)),
        "switching_controllers": switching,
        # ``.get``, not ``after[no]``: see pair_controllers' second bullet.
        "findings": {no: verdict(before[no], after.get(no)) for no in switching},
    }
    report["verdicts"] = sorted({f["s7"] for f in report["findings"].values()})
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--emitted", type=Path, required=True, help="The .inpx this repo generated."
    )
    parser.add_argument(
        "--saved",
        type=Path,
        required=True,
        help="The same network after Vissim's own SaveNetAs.",
    )
    parser.add_argument("--out", type=Path, default=Path("runtime/tod-live-check"))
    parser.add_argument(
        "--skip-com", action="store_true", help="Offline comparison only (no Vissim needed)."
    )
    args = parser.parse_args()

    report = build_report(args.emitted, args.saved)
    if not args.skip_com:
        report["s7c_com_attributes"] = com_attribute_census(args.emitted)

    args.out.mkdir(parents=True, exist_ok=True)
    target = args.out / "tod-live-check.json"
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"\nwritten: {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
