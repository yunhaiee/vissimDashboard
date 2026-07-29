"""QUEUE 3 unit 2 EMPIRICAL PROBE: priority rules (``PriorityRule`` -- a
stop/yield point on a minor link, carrying one or more ``ConflictMarker``
children that reference the conflicting major-road link and the gap/headway
parameters that govern whether a yielding vehicle accepts a gap) over COM.

Static XML pre-scan (already run, not repeated here at runtime -- see the
findings pasted into ``services.agent.tools_network`` once this unit ships):
the REAL Murdoch Drive model (``runtime/uploads/current/current.inpx``, the
client's own dataset, not an example network) carries 13 real
``<priorityRule>`` elements with 20 ``<conflictMarker>`` children --
priority rules are NOT the "zero on this machine, needs an example network"
case des-speed-decisions/signal-programs were. A broader static grep across
all real Vissim 2026 example networks (7 networks: Air Taxi Vertiport,
Border Crossing Breitenau.DE, Cableways, LRT Priority Luxembourg.LU,
Roundabout Billings.US, Roundabout Buehl.DE, Signalization RBC Boise.US)
found 1000 more ``<priorityRule>`` / 1831 more ``<conflictMarker>``
elements, confirming the attribute set below is stable across 8 real
networks and 1013 total priority rules, not an artifact of one file:

    <priorityRule affectDrivDir="FORWARD" allPedTypes="true" allVehTypes="true"
                   anmFlag="false" lane="11467 1" name="" no="132"
                   pos="5.0451591673814065" sigState="GREEN" slowDownDist="3">
        <conflictMarkers>
            <conflictMarker allPedTypes="true" allVehTypes="true"
                             effectDrivDir="FORWARD" lane="632 1"
                             lookBeyRedSig="false" maxSpeed="180" minClear="20"
                             minGapTime="2.5" pos="9" />
            <conflictMarker ... lane="632 2" ... minClear="20" minGapTime="2.5" .../>
        </conflictMarkers>
    </priorityRule>

**Central empirical finding, already settled statically (reported here for
the record, re-verified live below):** across every real ``<conflictMarker>``
in every network scanned (1851 total), the attribute set is EXACTLY
``{allPedTypes, allVehTypes, effectDrivDir, intLink, lane, lookBeyRedSig,
maxSpeed, minClear, minGapTime, pos}`` -- there is NO attribute spelled
anything containing "headway" anywhere in the .inpx. The task brief's
"gap/headway" framing maps onto exactly TWO numeric per-marker parameters
that DO exist: ``minGapTime`` (values observed 0-3, unmistakably a TIME in
seconds -- the critical/acceptable gap) and ``minClear`` (values observed
6-23, unmistakably NOT a time -- most likely a spatial clearance/headway
distance in meters, Vissim's own "minimum clearance" between the yielding
vehicle and the conflicting one). Whether COM exposes a THIRD, differently-
named attribute the XML never serializes (a dormant default, like some
other object's optional fields) is exactly what Phase 1b's live AttValue
probing (with explicit "Headway"/"MinHeadway"/"Hdwy" candidate attempts) is
for -- never assumed, always tested, per the CycTm/NodeRes landmine
precedent recorded across every prior v2/v2a unit.

This probe answers, on a live Vissim 2026 instance
(attach_to_running=False, a clone of the real Murdoch Drive model, closed in
a ``finally`` block):

[Phase 1a] STATIC type-library scan (no COM instance, no licence consumed) --
same technique every prior v2/v2a-unit probe used: dump every interface
whose name contains "priority" or "conflict" (case-insensitive) so the REAL
COM attribute/collection ids come from the type library, never a guess from
the XML attribute spelling.

[Phase 1b] LIVE probe on a Murdoch Drive clone:
  1. ``Net.PriorityRules`` (name TBD by 1a) ``GetAll()`` count cross-checked
     against the XML element count (13).
  2. Rule-level attribute discovery on a MULTI-marker rule (no=132, 2
     conflict markers): No, Name, Lane, Pos, SigState, AffectDrivDir,
     AllVehTypes, AllPedTypes, AnmFlag, SlowDownDist -- read back and
     compared against the XML ground truth.
  3. Child collection discovery: try ``ConflictMarkers`` (matches the XML
     child tag) and siblings, mirroring the VehCompRelFlows/VehRoutSta/
     VehClassDesSpeedDistr precedent -- every one of which turned out to
     match the XML child tag exactly.
  4. Conflict-marker level attribute discovery on the first marker: Lane,
     Pos, MinGapTime, MinClear, MaxSpeed, LookBeyRedSig, EffectDrivDir --
     PLUS an explicit attempt at "Headway"/"MinHeadway"/"Hdwy" candidates
     (the task brief's own hypothesis) to settle, live, whether a dormant
     COM-only headway attribute exists that the XML never serializes.
  5. ItemByKey error-behavior data points: bogus priority-rule no on
     Net.PriorityRules, and whether the ConflictMarkers child collection
     exposes ItemByKey at all (unit ⑦/⑧ found vehicle-composition-entry and
     des-speed-decision-entry child collections do NOT; unit ⑨ found SGs
     DOES -- never assume either way).
  6. CONTROL: bogus attribute id fail-open/closed, tested SEPARATELY on the
     priority-rule object and the conflict-marker object (unit ④ found
     Net.AttValue fails OPEN while ConflictArea.AttValue fails CLOSED --
     never assume one object's behavior generalizes to a different one).
  7. ONE live write on the MULTI-marker rule (no=132, 2 conflict markers):
     change marker[0]'s MinGapTime AND MinClear, read back immediately, AND
     read back marker[1]'s own values in the SAME rule -- sibling-untouched
     sanity check (there is no shared-sum semantics hypothesis to test here
     at all -- each marker's own gap/clearance should be fully independent,
     but verify, never assume).
  8. Persist (save_net_as, the sanctioned VissimEditSession.persist() route)
     + grep the saved XML's minGapTime/minClear attributes for the write +
     a fresh COM reload to confirm the write survived a full round trip.

Never touches the original ``runtime/uploads/current/current.inpx`` in place
(00-EXECUTION-GUIDE.md rule 4) -- everything happens on a work-dir clone.
Always disconnects every dedicated Vissim instance in a ``finally`` block.

Usage: python tools/probe_priority_rules.py [inpx_path]
Default: a real Vissim 2026 EXAMPLE network ("Roundabout Buehl.DE", 61 real
priority rules, 26 with >=2 conflict markers) -- NOT the Murdoch Drive
production model, because ``VissimController.load_network`` empirically
RAISES on it (see the ``MURDOCH_INPX``/``DEFAULT_INPX`` module-level comment
just below the imports for the full isolation story). Pass
``MURDOCH_INPX`` explicitly to reproduce that failure.
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

MURDOCH_INPX = BACKEND_ROOT.parent / "runtime" / "uploads" / "current" / "current.inpx"

#: LIVE-CONFIRMED LANDMINE (this unit): ``VissimController.load_network``
#: (``vissim.LoadNet(path, False)``) RAISES on the real Murdoch Drive
#: production model -- reproduced against BOTH a full-directory clone AND
#: the original ``runtime/uploads/current/current.inpx`` path directly (so
#: it is not a clone/sibling-asset problem; see this unit's own report for
#: the isolation steps) -- "(-2147352567, 'Exception occurred.', (0,
#: 'VISSIM.Vissim.2600', 'The network file could not be loaded.', None, 0,
#: -2147352567), None)". No prior v2/v2a unit ever actually COM-loaded this
#: file either (every live-COM probe/check in this codebase uses a real
#: Vissim EXAMPLE network) -- this is the first unit to attempt it, and the
#: first to hit it. Root cause NOT diagnosed here (out of scope for a
#: priority-rule probe); flagged separately. This probe therefore falls back
#: to a real Vissim 2026 EXAMPLE network that ALSO carries real
#: ``<priorityRule>`` elements (verified live below) for the COM phase, per
#: the v2a unit ⑧ precedent (Beijing had zero desSpeedDecisions -> Roundabout
#: Billings.US substituted) -- the Murdoch model's STATIC XML is still the
#: primary evidence for the attribute-shape findings (see this file's module
#: docstring), cross-checked against 1000+ more real priority rules across
#: every other real example network.
DEFAULT_INPX = Path(
    r"C:\Users\Public\Documents\PTV Vision\PTV Vissim 2026\Examples Demo\Roundabout Buehl.DE\3 Roundabouts - Prio rules.inpx"
)
WORK_ROOT = BACKEND_ROOT.parent / "runtime" / "priority-rule-probe"

_CONTROL_BOGUS_ATTR = "ThisAttributeDoesNotExistProbe12345"

_RULES_COLLECTION_CANDIDATES = ["PriorityRules", "PriorityRuless"]
_MARKERS_CHILD_CANDIDATES = ["ConflictMarkers", "PriorityRuleConflictMarkers", "ConflictMarker"]
_NO_CANDIDATES = ["No", "no"]
_NAME_CANDIDATES = ["Name", "name"]
_LANE_CANDIDATES = ["Lane", "lane"]
_POS_CANDIDATES = ["Pos", "pos"]
_SIG_STATE_CANDIDATES = ["SigState", "SignalState"]
_AFFECT_DRIV_DIR_CANDIDATES = ["AffectDrivDir", "AffectDrivingDir"]
_SLOW_DOWN_DIST_CANDIDATES = ["SlowDownDist", "SlowDownDistance"]
_MIN_GAP_TIME_CANDIDATES = ["MinGapTime", "GapTm", "MinGap"]
_MIN_CLEAR_CANDIDATES = ["MinClear", "MinClearance"]
_HEADWAY_CANDIDATES = ["Headway", "MinHeadway", "Hdwy", "MinHdwy"]
_MAX_SPEED_CANDIDATES = ["MaxSpeed", "MaxSpd"]
_LOOK_BEYOND_RED_CANDIDATES = ["LookBeyRedSig", "LookBeyondRedSignal"]
_EFFECT_DRIV_DIR_CANDIDATES = ["EffectDrivDir", "EffectDrivingDir"]


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
            in_target = "priority" in class_name.lower() or "conflict" in class_name.lower()
            if in_target:
                target_hits += 1
                _log(f"\n      -- {class_name} (DispatchBaseClass) --")
            continue
        if in_target and stripped.startswith("def ") and "(self" in stripped:
            _log(f"        {stripped}")
        if in_target and (stripped.startswith("_prop_map_get_") or stripped.startswith("_prop_map_put_")):
            _log(f"        {stripped}")

    if target_hits == 0:
        _log("      NO interface with 'priority' or 'conflict' in its name found in the type library.")
    else:
        _log(f"\n      Found {target_hits} '*priority*'/'*conflict*' interface(s) in the type library (listed above).")


# ---------------------------------------------------------------------------
# Phase 1b: live probe against a Murdoch Drive clone
# ---------------------------------------------------------------------------


def _xml_priority_rules(inpx_path: Path) -> list[dict[str, Any]]:
    root = ET.parse(inpx_path).getroot()
    result = []
    for pr in root.findall(".//priorityRule"):
        markers = [dict(m.attrib) for m in pr.findall(".//conflictMarker")]
        result.append({**dict(pr.attrib), "markers": markers})
    return result


def _first_working_collection(net: Any, candidates: list[str], label: str) -> tuple[str | None, Any]:
    for name in candidates:
        try:
            obj = getattr(net, name)
            count = len(list(obj.GetAll()))
            _log(f"        [WORKS] Net.{name} -> GetAll() count = {count}")
            return name, obj
        except Exception as exc:
            _log(f"        [no]    Net.{name} -> {type(exc).__name__}: {exc}")
    _log(f"        NONE of the candidate collection names for {label} worked.")
    return None, None


def _first_working_child_collection(item: Any, candidates: list[str], label: str) -> tuple[str | None, Any]:
    for name in candidates:
        try:
            obj = getattr(item, name)
            count = len(list(obj.GetAll()))
            _log(f"        [WORKS] .{name} -> GetAll() count = {count}")
            return name, obj
        except Exception as exc:
            _log(f"        [no]    .{name} -> {type(exc).__name__}: {exc}")
    _log(f"        NONE of the candidate child-collection names for {label} worked.")
    return None, None


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


def _try_all_attrs(item: Any, candidates: list[str], label: str) -> None:
    """Like _first_working_attr but tries and reports EVERY candidate
    (doesn't stop at the first hit) -- used for the headway hypothesis test,
    where we want to know about EVERY candidate's fate, not just the first
    that happens to work."""
    for attr_id in candidates:
        try:
            value = item.AttValue(attr_id)
            _log(f"        [WORKS] AttValue({attr_id!r}) -> {value!r}")
        except Exception as exc:
            _log(f"        [no]    AttValue({attr_id!r}) -> {type(exc).__name__}: {exc}")


def main() -> None:
    inpx_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_INPX
    if not inpx_path.is_file():
        raise SystemExit(f"Source .inpx not found: {inpx_path}")

    _log("#" * 78)
    _log("# PHASE 1a -- static type-library scan (no COM instance, no licence used)")
    _log("#" * 78)
    probe_typelib()

    _log("\n" + "#" * 78)
    _log("# PHASE 1b -- live probe on a Murdoch Drive (current.inpx) clone")
    _log("#" * 78)

    work_dir = _fresh_work_dir(inpx_path.parent.name or "current")
    work_dir.mkdir(parents=True, exist_ok=True)
    # Mirror ArtifactManager.clone()'s "copy siblings too" convention (the v2
    # unit ⑤ follow-up finding: external assets living alongside the .inpx
    # can matter for LoadNet) by copying the WHOLE source directory, not just
    # the one file.
    for item in inpx_path.parent.iterdir():
        if item.is_file():
            shutil.copy2(item, work_dir / item.name)
    working = work_dir / inpx_path.name
    if not working.is_file():
        raise SystemExit(f"Clone failed, no .inpx at {working}")
    _log(f"[1b.0] Working copy: {working}")

    xml_before = _xml_priority_rules(working)
    _log(f"       XML <priorityRule> count = {len(xml_before)}")
    total_markers = sum(len(pr["markers"]) for pr in xml_before)
    _log(f"       XML <conflictMarker> count (all rules) = {total_markers}")
    for pr in xml_before:
        marker_summary = [(m.get("minGapTime"), m.get("minClear")) for m in pr["markers"]]
        _log(
            f"       rule no={pr['no']} lane={pr.get('lane')!r} "
            f"markers(minGapTime,minClear)={marker_summary}"
        )

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

        _log("\n[1b.3] Priority-rules collection name discovery on Net:")
        coll_name, rules_coll = _first_working_collection(
            controller.vissim.Net, _RULES_COLLECTION_CANDIDATES, "PriorityRules"
        )
        if rules_coll is None:
            raise SystemExit("FAIL: could not find the priority-rules collection on Net.")

        rule_items = list(rules_coll.GetAll())
        _log(f"       Net.{coll_name}.GetAll() count = {len(rule_items)} (XML count = {len(xml_before)})")

        rule_no_by_multi = next(pr["no"] for pr in xml_before if len(pr["markers"]) >= 2)
        first_rule = next(item for item in rule_items if str(item.AttValue("No")) == rule_no_by_multi)
        _log(f"\n[1b.4] Rule-level attribute discovery on rule no={rule_no_by_multi} (multi-marker):")
        rule_no_id, _ = _first_working_attr(first_rule, _NO_CANDIDATES, "No")
        rule_name_id, _ = _first_working_attr(first_rule, _NAME_CANDIDATES, "Name")
        rule_lane_id, _ = _first_working_attr(first_rule, _LANE_CANDIDATES, "Lane")
        rule_pos_id, _ = _first_working_attr(first_rule, _POS_CANDIDATES, "Pos")
        rule_sig_id, _ = _first_working_attr(first_rule, _SIG_STATE_CANDIDATES, "SigState")
        rule_dir_id, _ = _first_working_attr(first_rule, _AFFECT_DRIV_DIR_CANDIDATES, "AffectDrivDir")
        rule_slowdown_id, _ = _first_working_attr(first_rule, _SLOW_DOWN_DIST_CANDIDATES, "SlowDownDist")

        xml_first_rule = next(pr for pr in xml_before if pr["no"] == rule_no_by_multi)
        _log(
            f"       XML ground truth: no={xml_first_rule['no']} lane={xml_first_rule.get('lane')!r} "
            f"pos={xml_first_rule.get('pos')!r} sigState={xml_first_rule.get('sigState')!r} "
            f"slowDownDist={xml_first_rule.get('slowDownDist')!r}"
        )

        _log("\n[1b.5] Conflict-markers CHILD COLLECTION discovery on the rule object...")
        child_coll_name, child_coll = _first_working_child_collection(
            first_rule, _MARKERS_CHILD_CANDIDATES, "ConflictMarkers"
        )
        marker_items = list(child_coll.GetAll()) if child_coll is not None else []
        _log(f"       Marker count via COM = {len(marker_items)} (XML count for this rule = {len(xml_first_rule['markers'])})")

        min_gap_time_id = None
        min_clear_id = None
        marker_lane_id = None
        marker_pos_id = None
        if marker_items:
            first_marker = marker_items[0]
            _log(f"\n[1b.6] Conflict-marker attribute discovery (first marker of rule {rule_no_by_multi}):")
            marker_lane_id, _ = _first_working_attr(first_marker, _LANE_CANDIDATES, "Lane (marker)")
            marker_pos_id, _ = _first_working_attr(first_marker, _POS_CANDIDATES, "Pos (marker)")
            min_gap_time_id, gap_val = _first_working_attr(
                first_marker, _MIN_GAP_TIME_CANDIDATES, "MinGapTime (marker)"
            )
            min_clear_id, clear_val = _first_working_attr(
                first_marker, _MIN_CLEAR_CANDIDATES, "MinClear (marker)"
            )
            max_speed_id, _ = _first_working_attr(first_marker, _MAX_SPEED_CANDIDATES, "MaxSpeed (marker)")
            look_beyond_id, _ = _first_working_attr(
                first_marker, _LOOK_BEYOND_RED_CANDIDATES, "LookBeyRedSig (marker)"
            )
            effect_dir_id, _ = _first_working_attr(
                first_marker, _EFFECT_DRIV_DIR_CANDIDATES, "EffectDrivDir (marker)"
            )
            xml_first_marker = xml_first_rule["markers"][0]
            _log(f"       XML ground truth for this marker: {xml_first_marker}")

            _log(
                "\n[1b.7] THE KEY QUESTION -- does COM expose a THIRD, dormant 'headway' "
                "attribute the XML never serializes? Trying every candidate (not stopping at "
                "the first hit) on the SAME first marker object:"
            )
            _try_all_attrs(first_marker, _HEADWAY_CANDIDATES, "Headway (marker, hypothesis)")

            _log("\n[1b.7b] Does the marker itself carry a 'No' attribute at all (XML has none)?")
            _try_all_attrs(first_marker, _NO_CANDIDATES, "No (marker, hypothesis)")

        # -------------------------------------------------------------
        # [1b.8] CONTROL: bogus attribute id, tested on BOTH the rule object
        # and the marker object separately (unit ④'s ConflictArea-vs-Net
        # divergence -- never assume one object's behavior generalizes).
        # -------------------------------------------------------------
        _log("\n[1b.8] CONTROL: bogus attribute id on the RULE object (fail-open/closed check)...")
        try:
            bogus = first_rule.AttValue(_CONTROL_BOGUS_ATTR)
            _log(f"        [CONTROL] rule.AttValue({_CONTROL_BOGUS_ATTR!r}) -> {bogus!r} (fails OPEN)")
        except Exception as exc:
            _log(f"        [CONTROL] rule.AttValue({_CONTROL_BOGUS_ATTR!r}) -> {type(exc).__name__}: {exc} (fails CLOSED)")

        if marker_items:
            _log("\n[1b.8b] CONTROL: bogus attribute id on the MARKER object (fail-open/closed check)...")
            try:
                bogus_m = marker_items[0].AttValue(_CONTROL_BOGUS_ATTR)
                _log(f"        [CONTROL] marker.AttValue({_CONTROL_BOGUS_ATTR!r}) -> {bogus_m!r} (fails OPEN)")
            except Exception as exc:
                _log(f"        [CONTROL] marker.AttValue({_CONTROL_BOGUS_ATTR!r}) -> {type(exc).__name__}: {exc} (fails CLOSED)")

        _log("\n[1b.9] Bogus priority-rule no -- ItemByKey error behavior...")
        try:
            bogus_rule = rules_coll.ItemByKey(999999)
            _log(f"        ItemByKey(999999) -> {bogus_rule!r} (UNEXPECTED -- did not raise)")
        except Exception as exc:
            _log(f"        ItemByKey(999999) -> {type(exc).__name__}: {exc} (fails CLOSED)")

        if child_coll is not None:
            _log("\n[1b.10] Does the conflict-markers child collection expose ItemByKey?...")
            has_item_by_key = hasattr(child_coll, "ItemByKey")
            _log(f"        hasattr(child_coll, 'ItemByKey') = {has_item_by_key}")
            if has_item_by_key:
                try:
                    bogus_entry = child_coll.ItemByKey(999999)
                    _log(f"        ItemByKey(999999) -> {bogus_entry!r} (UNEXPECTED -- did not raise)")
                except Exception as exc:
                    _log(f"        ItemByKey(999999) -> {type(exc).__name__}: {exc} (fails CLOSED)")

        # -------------------------------------------------------------
        # [1b.11] MULTI-marker rule write: change marker[0]'s MinGapTime AND
        # MinClear, verify marker[1]'s own values are untouched.
        # -------------------------------------------------------------
        siblings_changed: dict[str, Any] = {}
        new_gap = new_clear = None
        if child_coll is not None and min_gap_time_id and min_clear_id and len(marker_items) >= 2:
            _log(f"\n[1b.11] MULTI-marker write test -- rule no={rule_no_by_multi}")
            before_vals: dict[int, tuple[Any, Any]] = {}
            for idx, m in enumerate(marker_items):
                before_vals[idx] = (m.AttValue(min_gap_time_id), m.AttValue(min_clear_id))
            _log(f"        BEFORE -- every marker's (MinGapTime, MinClear): {before_vals}")

            old_gap, old_clear = before_vals[0]
            new_gap = 4.5 if str(old_gap) != "4.5" else 3.5
            new_clear = 30 if str(old_clear) != "30" else 25
            _log(
                f"        SetAttValue({min_gap_time_id!r}, {new_gap}) / "
                f"SetAttValue({min_clear_id!r}, {new_clear}) on marker[0] "
                f"(was {old_gap!r}/{old_clear!r})..."
            )
            try:
                marker_items[0].SetAttValue(min_gap_time_id, new_gap)
                marker_items[0].SetAttValue(min_clear_id, new_clear)
                _log("        SetAttValue succeeded (no exception) for both attributes.")
            except Exception as exc:
                _log(f"        SetAttValue RAISED: {type(exc).__name__}: {exc}")

            after_vals: dict[int, tuple[Any, Any]] = {}
            for idx, m in enumerate(marker_items):
                after_vals[idx] = (m.AttValue(min_gap_time_id), m.AttValue(min_clear_id))
            _log(f"        AFTER -- every marker's (MinGapTime, MinClear): {after_vals}")
            siblings_changed = {
                idx: (before_vals[idx], after_vals[idx])
                for idx in before_vals
                if idx != 0 and before_vals[idx] != after_vals[idx]
            }
            _log(
                f"        Sibling markers whose values changed as a side effect: {siblings_changed} "
                "(empty == independent per-marker assignment, as expected)"
            )

            # ---------------------------------------------------------
            # [1b.11b] CONTROL: negative / zero values -- does SetAttValue
            # itself refuse an implausible gap/clearance, or accept anything
            # (fail open, our own validation required)?
            # ---------------------------------------------------------
            _log("\n[1b.11b] CONTROL: SetAttValue with a NEGATIVE MinGapTime (-1)...")
            try:
                marker_items[0].SetAttValue(min_gap_time_id, -1)
                readback_neg = marker_items[0].AttValue(min_gap_time_id)
                _log(
                    f"        SetAttValue(..., -1) succeeded; readback = {readback_neg!r} "
                    "(fails OPEN -- our own validation is required)"
                )
                marker_items[0].SetAttValue(min_gap_time_id, new_gap)  # restore
            except Exception as exc:
                _log(f"        SetAttValue(..., -1) RAISED: {type(exc).__name__}: {exc} (fails CLOSED)")

            # ---------------------------------------------------------
            # [1b.12] Persist + reparse + fresh COM reload.
            # ---------------------------------------------------------
            tmp_path = working.with_name(working.stem + "_saving" + working.suffix)
            _log(f"\n[1b.12] Persisting via save_net_as({tmp_path.name}) + os.replace onto {working.name}...")
            controller.save_net_as(tmp_path)
            os.replace(tmp_path, working)
            _log(f"        Persisted. Working copy size = {working.stat().st_size} bytes")

            xml_after = _xml_priority_rules(working)
            xml_rule_after = next(pr for pr in xml_after if pr["no"] == rule_no_by_multi)
            _log(f"[1b.13] Saved XML rule no={rule_no_by_multi} markers: {xml_rule_after['markers']}")

            _log("\n[1b.14] Fresh COM reload of the saved file (separate connection)...")
            controller.disconnect()
            verify_controller = VissimController()
            verify_controller.connect(visible=True, launch_if_missing=True, attach_to_running=False)
            verify_controller.load_network(str(working))
            reload_rules = getattr(verify_controller.vissim.Net, coll_name)
            reload_rule = reload_rules.ItemByKey(int(rule_no_by_multi))
            reload_markers = list(getattr(reload_rule, child_coll_name).GetAll())
            reload_gap = reload_markers[0].AttValue(min_gap_time_id)
            reload_clear = reload_markers[0].AttValue(min_clear_id)
            _log(
                f"        Reload rule no={rule_no_by_multi} marker[0] MinGapTime={reload_gap!r} "
                f"(intended {new_gap!r}) MinClear={reload_clear!r} (intended {new_clear!r}) -> "
                f"match: {str(reload_gap) == str(new_gap) and str(reload_clear) == str(new_clear)}"
            )

            controller = verify_controller  # so the finally block disconnects this one

            _log("\n" + "#" * 78)
            _log("# SUMMARY")
            _log("#" * 78)
            _log(f"Rules collection: Net.{coll_name}")
            _log(f"Conflict-markers child collection: .{child_coll_name}")
            _log(f"Attribute ids: MinGapTime={min_gap_time_id!r} MinClear={min_clear_id!r}")
            _log(f"Sibling untouched on multi-marker write: {not siblings_changed}")
        else:
            _log("\n[SKIP] Multi-marker write test needs child_coll + both attribute ids + >=2 markers.")

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
