"""v2 unit (4) Phase 1 EMPIRICAL PROBE: conflict areas over COM.

Answers, on a live Vissim 2026 instance (attach_to_running=False,
Beijing.CN clone, closed in a ``finally`` block), the questions
``docs/phase2/00-EXECUTION-GUIDE.md`` SS3.2 and the M3.1 CATALOG-GAP note on
``conflict_area_data_unavailable`` leave open:

[Phase 1a] STATIC type-library scan (no COM instance, no licence consumed) --
same technique ``tools/check_network_probe.py`` used for v2 unit (3): generate
the makepy wrapper for the installed "Vissim Object Library" and dump the
FULL, verbatim method/property list of every interface whose name contains
"Confl" (``IConflictArea``, ``IConflictAreas`` and friends) -- this is the
REAL attribute-id source, not a guess from the .inpx XML attribute names
(``linkA``/``linkB``/``status`` in the XML are not guaranteed to be the COM
``AttValue`` ids -- the CycTm/NodeRes landmines in DECISIONS.md both burned
a plausible-looking guess).

[Phase 1b] LIVE probe on a Beijing.CN clone:
  1. Net.ConflictAreas.GetAll() count, cross-checked against the working
     copy's own ``<conflictArea>`` element count in the XML (identity/count
     stability check -- SS3.2 says conflict-area NUMBERS are not stable
     identity ACROSS GEOMETRY OPS; this probe does zero geometry ops, so it
     establishes the WITHIN-SESSION baseline the roadmap asks for).
  2. For every conflict area: read back the candidate attribute ids
     discovered in Phase 1a (No, the two link references, Status) and print
     the first 10 plus a full tally of every distinct Status value observed
     -- cross-checked against a bogus-id CONTROL probe (Net.AttValue fails
     OPEN per the v2 unit (3) landmine, but a per-OBJECT AttValue on a
     COM-native ConflictArea item is a different surface and must be
     verified independently, not assumed to share that failure-open
     behavior).
  3. ONE SetAttValue status change on ONE conflict area, read-back, then
     ``save_net_as`` (the sanctioned session.persist() route) + reload the
     saved file fresh via a SEPARATE COM connection AND via a raw XML
     reparse -- grepping for how conflict areas and their status actually
     serialize in the saved file (does the roadmap's "if the XML does NOT
     serialize conflict areas, the shadow stays unpopulated" branch even
     apply here, or does the .inpx carry them after all?).
  4. Re-enumerate Net.ConflictAreas.GetAll() after the SaveNetAs + reload to
     confirm the untouched conflict areas kept the SAME numbers (further
     within-session identity-stability evidence).

Never touches the original Examples Demo folder (00-EXECUTION-GUIDE.md rule
4) -- everything happens on a work-dir clone. Always disconnects every
dedicated Vissim instance in a ``finally`` block.

Usage: python tools/conflict_area_probe.py [example_dir]
Default example: Urban Intersection Beijing.CN (2026 Examples Demo) -- same
default as every other live probe/check tool in this directory; it has 224
real conflict areas (verified via a plain XML grep before this script was
written), a large enough population to see the full Status vocabulary.
"""
from __future__ import annotations

import os
import shutil
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

DEFAULT_EXAMPLE = Path(
    r"C:\Users\Public\Documents\PTV Vision\PTV Vissim 2026\Examples Demo\Urban Intersection Beijing.CN"
)
WORK_ROOT = BACKEND_ROOT.parent / "runtime" / "conflict-area-probe"

_CONTROL_BOGUS_ATTR = "ThisAttributeDoesNotExistProbe12345"

# Every plausible COM attribute id for the three fields we need, tried in
# this order -- the FIRST one that reads back on a real object without
# raising is treated as the real id for the rest of the probe. Deliberately
# includes both the XML attribute's own spelling (linkA/linkB/status,
# case-sensitively as Vissim writes them) and Vissim's usual COM
# capitalization convention (matches the CycTm/RandSeed/SimPeriod precedent
# of "no separators, each word capitalized").
_NO_CANDIDATES = ["No", "no"]
_LINK_A_CANDIDATES = ["LinkA", "Link1", "linkA", "LinkOfNodeA", "LinkA1"]
_LINK_B_CANDIDATES = ["LinkB", "Link2", "linkB", "LinkOfNodeB", "LinkB1"]
_STATUS_CANDIDATES = ["Status", "status", "ConflStatus", "ConflictStatus"]


def _log(msg: str) -> None:
    print(msg, flush=True)


def _fresh_work_dir(name: str) -> Path:
    for stale in WORK_ROOT.glob(f"{name}_*"):
        try:
            shutil.rmtree(stale)
        except OSError:
            pass
    return WORK_ROOT / f"{name}_{os.getpid()}"


# ---------------------------------------------------------------------------
# Phase 1a: static type-library scan (no COM instance required)
# ---------------------------------------------------------------------------


def probe_typelib() -> None:
    import win32com.client.makepy as makepy
    import win32com.client.selecttlb as selecttlb
    from win32com.client import gencache

    _log("[1a] Locating the installed 'Vissim Object Library' type library via selecttlb...")
    candidates = [
        tlb
        for tlb in selecttlb.EnumTlbs()
        if "vissim object library" in (getattr(tlb, "desc", "") or "").lower()
    ]
    if not candidates:
        raise RuntimeError(
            "No registered 'Vissim Object Library' type library found via "
            "win32com.client.selecttlb.EnumTlbs() -- cannot run the static scan."
        )

    def _hex_int(value: str) -> int:
        return int(value, 16)

    match = max(candidates, key=lambda t: (_hex_int(t.major), _hex_int(t.minor)))
    for tlb in candidates:
        _log(f"      registered: {tlb.desc}  clsid={tlb.clsid} major={tlb.major} minor={tlb.minor}")
    _log(f"      using: {match.desc}  clsid={match.clsid}")

    major_i, minor_i = _hex_int(match.major), _hex_int(match.minor)
    makepy.GenerateFromTypeLibSpec((match.clsid, match.lcid, major_i, minor_i))
    mod = gencache.EnsureModule(match.clsid, match.lcid, major_i, minor_i)
    src_path = Path(mod.__file__)
    _log(f"      Generated wrapper: {src_path} ({src_path.stat().st_size} bytes)")
    lines = src_path.read_text(encoding="utf-8", errors="replace").splitlines()

    class_name = None
    in_target = False
    target_hits = 0
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("class I") and "(DispatchBaseClass)" in stripped:
            class_name = stripped.split("(")[0].removeprefix("class ").strip()
            in_target = "confl" in class_name.lower()
            if in_target:
                target_hits += 1
                _log(f"\n      -- {class_name} (DispatchBaseClass) --")
            continue
        if in_target and stripped.startswith("def ") and "(self" in stripped:
            _log(f"        {stripped}")
        if in_target and (stripped.startswith("_prop_map_get_") or stripped.startswith("_prop_map_put_")):
            _log(f"        {stripped}")

    if target_hits == 0:
        _log("      NO interface with 'Confl' in its name found anywhere in the type library.")
    else:
        _log(f"\n      Found {target_hits} 'Confl*' interface(s) in the type library (listed above).")


# ---------------------------------------------------------------------------
# Phase 1b: live probe against a Beijing.CN clone
# ---------------------------------------------------------------------------


def _xml_conflict_areas(inpx_path: Path) -> list[dict[str, str]]:
    root = ET.parse(inpx_path).getroot()
    return [dict(ca.attrib) for ca in root.findall(".//conflictArea")]


def _first_working_attr(item: Any, candidates: list[str], label: str) -> tuple[str | None, Any]:
    for attr_id in candidates:
        try:
            value = item.AttValue(attr_id)
            _log(f"        [WORKS] AttValue({attr_id!r}) -> {value!r}")
            return attr_id, value
        except Exception as exc:
            _log(f"        [no]    AttValue({attr_id!r}) -> {type(exc).__name__}: {exc}")
    _log(f"        NONE of the candidate ids for {label} worked on this item.")
    return None, None


def main() -> None:
    example_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_EXAMPLE
    if not example_dir.is_dir():
        raise SystemExit(f"Example folder not found: {example_dir}")

    _log("#" * 78)
    _log("# PHASE 1a -- static type-library scan (no COM instance, no licence used)")
    _log("#" * 78)
    probe_typelib()

    _log("\n" + "#" * 78)
    _log("# PHASE 1b -- live probe on a Beijing.CN clone")
    _log("#" * 78)

    work_dir = _fresh_work_dir(example_dir.name)
    shutil.copytree(example_dir, work_dir)
    inpx_files = sorted(work_dir.glob("*.inpx"))
    if not inpx_files:
        raise SystemExit(f"No .inpx in {work_dir}")
    working = inpx_files[0]
    _log(f"[1b.0] Working copy: {working}")

    xml_before = _xml_conflict_areas(working)
    xml_nos_before = sorted(int(ca["no"]) for ca in xml_before if ca.get("no"))
    xml_status_tally: dict[str, int] = {}
    for ca in xml_before:
        xml_status_tally[ca.get("status", "")] = xml_status_tally.get(ca.get("status", ""), 0) + 1
    _log(f"       XML <conflictArea> count = {len(xml_before)}")
    _log(f"       XML 'no' range = [{min(xml_nos_before)}, {max(xml_nos_before)}] (gaps confirm non-stable numbering)")
    _log(f"       XML status tally (ground truth): {xml_status_tally}")

    import pythoncom

    pythoncom.CoInitialize()
    from services.vissim_com import VissimController

    controller = VissimController()
    try:
        _log("[1b.1] Launching a dedicated Vissim instance (attach_to_running=False)...")
        started = time.monotonic()
        controller.connect(visible=True, launch_if_missing=True, attach_to_running=False)
        _log(f"       Connected via {controller.prog_id} in {time.monotonic() - started:.1f}s")

        _log(f"[1b.2] Loading network: {working.name}")
        controller.load_network(str(working))

        conflict_areas = controller.vissim.Net.ConflictAreas
        items = list(conflict_areas.GetAll())
        _log(f"[1b.3] Net.ConflictAreas.GetAll() count = {len(items)} (XML count = {len(xml_before)})")

        if not items:
            raise SystemExit("FAIL: no conflict areas found on this network -- cannot run the probe")

        _log("\n[1b.4] Attribute-id discovery on the FIRST conflict area item:")
        first = items[0]
        _log("      -- No candidates --")
        no_id, no_val = _first_working_attr(first, _NO_CANDIDATES, "No")
        _log("      -- Link-A candidates --")
        link_a_id, link_a_val = _first_working_attr(first, _LINK_A_CANDIDATES, "LinkA")
        _log("      -- Link-B candidates --")
        link_b_id, link_b_val = _first_working_attr(first, _LINK_B_CANDIDATES, "LinkB")
        _log("      -- Status candidates --")
        status_id, status_val = _first_working_attr(first, _STATUS_CANDIDATES, "Status")
        _log("      -- CONTROL: bogus attribute id (per-object AttValue fail-open check) --")
        try:
            bogus = first.AttValue(_CONTROL_BOGUS_ATTR)
            _log(f"        [CONTROL] AttValue({_CONTROL_BOGUS_ATTR!r}) -> {bogus!r} (UNEXPECTED -- did not raise)")
        except Exception as exc:
            _log(f"        [CONTROL] AttValue({_CONTROL_BOGUS_ATTR!r}) -> {type(exc).__name__}: {exc} (raises for a made-up id -- fails CLOSED here, unlike Net.AttValue)")

        if no_id is None or link_a_id is None or link_b_id is None or status_id is None:
            raise SystemExit(
                "FAIL: could not discover working attribute ids for No/LinkA/LinkB/Status -- "
                "expand the candidate lists above and re-run."
            )

        _log(f"\n[1b.5] Reading ALL {len(items)} conflict areas via the discovered ids "
             f"(No={no_id!r}, LinkA={link_a_id!r}, LinkB={link_b_id!r}, Status={status_id!r})...")
        com_rows: list[dict[str, Any]] = []
        for item in items:
            com_rows.append(
                {
                    "no": int(item.AttValue(no_id)),
                    "link_a": str(item.AttValue(link_a_id)),
                    "link_b": str(item.AttValue(link_b_id)),
                    "status": str(item.AttValue(status_id)),
                }
            )
        for row in com_rows[:10]:
            _log(f"       {row}")
        if len(com_rows) > 10:
            _log(f"       ... ({len(com_rows) - 10} more)")

        com_status_tally: dict[str, int] = {}
        for row in com_rows:
            com_status_tally[row["status"]] = com_status_tally.get(row["status"], 0) + 1
        _log(f"       COM status tally: {com_status_tally}")
        _log(f"       XML status tally: {xml_status_tally}")
        _log(f"       COM/XML status tallies match: {com_status_tally == xml_status_tally}")

        com_nos = sorted(row["no"] for row in com_rows)
        _log(f"\n[1b.6] Identity-stability check (no geometry ops performed):")
        _log(f"       COM Nos == XML Nos: {com_nos == xml_nos_before} "
             f"(COM count={len(com_nos)}, XML count={len(xml_nos_before)})")

        # -------------------------------------------------------------
        # ONE SetAttValue status change on ONE conflict area
        # -------------------------------------------------------------
        target = next((r for r in com_rows if r["status"].upper() == "PASSIVE"), com_rows[0])
        target_no = target["no"]
        new_status = "AHASRIGHTOFWAY" if target["status"].upper() != "AHASRIGHTOFWAY" else "PASSIVE"
        _log(f"\n[1b.7] Target conflict area no={target_no} "
             f"(linkA={target['link_a']}, linkB={target['link_b']}), "
             f"current status={target['status']!r} -> attempting SetAttValue({status_id!r}, {new_status!r})")

        target_item = conflict_areas.ItemByKey(target_no)
        try:
            target_item.SetAttValue(status_id, new_status)
            _log(f"       SetAttValue succeeded (no exception).")
        except Exception as exc:
            _log(f"       SetAttValue RAISED: {type(exc).__name__}: {exc}")
            raise

        readback = str(target_item.AttValue(status_id))
        _log(f"       Read-back immediately after set (same live session): {readback!r}")
        _log(f"       Read-back matches intended new_status: {readback.upper() == new_status.upper()}")

        # -------------------------------------------------------------
        # save_net_as (sanctioned persist route) + reload
        # -------------------------------------------------------------
        tmp_path = working.with_name(working.stem + "_saving" + working.suffix)
        _log(f"\n[1b.8] Persisting via save_net_as({tmp_path.name}) + os.replace onto {working.name} "
             f"(same route VissimEditSession.persist() uses)...")
        controller.save_net_as(tmp_path)
        os.replace(tmp_path, working)
        _log(f"       Persisted. Working copy size = {working.stat().st_size} bytes")

        _log("[1b.9] Grepping the SAVED XML for the target conflictArea's serialized status...")
        xml_after = _xml_conflict_areas(working)
        xml_after_by_no = {int(ca["no"]): ca for ca in xml_after if ca.get("no")}
        target_xml_after = xml_after_by_no.get(target_no)
        if target_xml_after is None:
            _log(f"       FAIL: conflictArea no={target_no} not found in saved XML at all "
                 f"(possible identity/renumber issue, or conflict areas do not serialize).")
        else:
            _log(f"       Saved XML conflictArea no={target_no}: {target_xml_after}")
            _log(f"       Saved XML status == intended new_status: "
                 f"{target_xml_after.get('status', '').upper() == new_status.upper()}")

        xml_after_nos = sorted(xml_after_by_no.keys())
        _log(f"[1b.10] Post-save XML 'no' set unchanged vs pre-save: {xml_after_nos == xml_nos_before}")

        _log("[1b.11] Fresh COM reload of the saved file (separate connection) to confirm persistence...")
        controller.disconnect()
        verify_controller = VissimController()
        verify_controller.connect(visible=True, launch_if_missing=True, attach_to_running=False)
        verify_controller.load_network(str(working))
        reload_items = list(verify_controller.vissim.Net.ConflictAreas.GetAll())
        reload_nos = sorted(int(it.AttValue(no_id)) for it in reload_items)
        reload_target = verify_controller.vissim.Net.ConflictAreas.ItemByKey(target_no)
        reload_status = str(reload_target.AttValue(status_id))
        _log(f"       Fresh reload conflict area count = {len(reload_items)} (before = {len(items)})")
        _log(f"       Fresh reload Nos == pre-write COM Nos: {reload_nos == com_nos}")
        _log(f"       Fresh reload target no={target_no} status = {reload_status!r} "
             f"(intended {new_status!r}) -> match: {reload_status.upper() == new_status.upper()}")

        controller = verify_controller  # so the finally block disconnects this one

        _log("\n" + "#" * 78)
        _log("# SUMMARY")
        _log("#" * 78)
        _log(f"Discovered attribute ids: No={no_id!r} LinkA={link_a_id!r} LinkB={link_b_id!r} Status={status_id!r}")
        _log(f"Status value vocabulary observed (XML+COM): {sorted(set(xml_status_tally) | set(com_status_tally))}")
        _log(f"Identity stability (no geometry ops, same session): COM Nos == XML Nos before write: {com_nos == xml_nos_before}")
        _log(f"Identity stability across a SaveNetAs + fresh reload: {reload_nos == com_nos}")
        _log(f"Write proof: live read-back matched: {readback.upper() == new_status.upper()}; "
             f"saved-XML matched: {target_xml_after is not None and target_xml_after.get('status','').upper() == new_status.upper()}; "
             f"fresh-reload matched: {reload_status.upper() == new_status.upper()}")

    finally:
        try:
            controller.disconnect()
        except Exception:
            pass
        try:
            pythoncom.CoUninitialize()
        except Exception:
            pass
        _log("\nDisconnected the dedicated Vissim instance.")


if __name__ == "__main__":
    main()
