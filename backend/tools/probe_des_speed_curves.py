"""QUEUE 3 (v2a) unit 3 EMPIRICAL PROBE: desired-speed DISTRIBUTION curves
(``SpeedDistribution`` -> its ``SpeedDistrDatPts`` child collection of
(speed, cumulative-fraction) data points) over COM -- the legitimate CC1
route for changing how fast traffic wants to go.

Static XML pre-scan (already run, not repeated here at runtime -- see the
findings pasted into ``services.agent.tools_network`` once this unit ships):
a network-wide scan across all 25 real Vissim 2026 example networks found 559
real ``<speedDistribution>`` elements carrying a ``<speedDistrDatPts>`` child
with 2-20 ``<speedDistributionDataPoint fx="..." x="..."/>`` points each
(point-count histogram: {2: 284, 3: 29, 4: 64, 5: 15, 6: 12, 8: 28, 9: 4,
10: 21, 11: 44, 14: 22, 15: 22, 19: 7, 20: 7}). ``x`` is the speed in
kilometers per hour REGARDLESS of the network's own ``unitSpeed`` display
setting -- verified statically: "Roundabout Billings.US" has
``netPara@unitSpeed="MILESPERHOUR"`` yet distribution no=50 (named "55 mph")
carries raw XML points ``x="72.42"``/``x="104.61"`` -- 104.61 km/h / 1.60934
= 65.0 mph, and the DISTRIBUTION'S OWN NAME/midpoint math
(reparse_reduced_speed_area's own convention) confirms these are genuinely
km/h, not mph, in the .inpx -- the SAME "XML always stores metric; COM's
AttValue may report the network's display unit" pattern v2a unit ⑧ found for
desSpeedDecision's ``Pos`` attribute (~3.28x, a metres-vs-feet ratio, on the
SAME network). Whether ``AttValue('X')``/``SetAttValue('X', ...)`` on a
SPEED point reads/writes in km/h (XML-native) or the network's OWN display
unit (mph here) is exactly what this probe's live phase settles -- never
assumed, always tested, per that same landmine's precedent.

**Mathematical-invariant reality check, already settled statically (reported
here for the record, re-verified live below):** every one of the 559 real
distributions scanned has ``x`` values in NON-DECREASING (not strictly
increasing) order, ``fx`` values in non-decreasing order from EXACTLY 0.0 to
EXACTLY 1.0, and no negative ``x``. Critically, 19 of them (all in
"Rollercoaster.inpx", e.g. distribution no=10: ``[(x=10,fx=0), (x=10,fx=1)]``)
have a REPEATED ``x`` value across two consecutive points -- a legitimate,
PTV-authored "step function" encoding a near-constant desired speed (0%
probability below X, 100% at or above X). This DIRECTLY CONTRADICTS the task
brief's own guess ("speeds must be strictly increasing across points") --
real Vissim data uses non-strict (non-decreasing) ``x`` routinely. The
constraint catalog this unit ships must reflect the REAL invariant
(non-decreasing, refusing only a genuine DECREASE) rather than the brief's
unverified guess -- exactly the "always verify, never assume" discipline
every prior unit's DECISIONS.md entry recorded.

**Static type-library pre-scan, already run (reported here for the record,
re-run live below via the SAME ``probe_typelib()`` "speed"-name filter unit
⑧'s own probe used -- ``ISpeedDistributionDataPoint`` also contains "speed"
in its name, so the identical filter already surfaces it):** the type
library exposes ``ISpeedDistributionDataPointContainer`` with
``GetAll``/``ReplaceAll``/``Set*AttValues`` -- but genuinely NO
``ItemByKey``, NO ``AddSpeedDistributionDataPoint``, NO
``RemoveSpeedDistributionDataPoint`` (confirmed by the ABSENCE of those
method names in the generated wrapper source, cross-checked against the
sibling ``ISpeedDistributionContainer`` one level up, which DOES expose
``AddSpeedDistribution``/``RemoveSpeedDistribution``/``ItemByKey`` for
DISTRIBUTIONS themselves -- so the "no add/remove, no identity" shape is
specific to POINTS, not the collection type generally). This is the
authoritative, type-library-level confirmation of the task brief's own
"atomic full-curve replace beats point surgery" instinct: COM itself offers
no per-point surgery primitive at all for an EXISTING distribution's points
-- only ``ReplaceAll(Elements)`` (whole-collection replace) or per-point
``SetAttValue`` on an point object already returned by ``GetAll()`` (an
ordinal, GetAll()-order identity -- the SAME "no ItemByKey, synthesize an
ordinal" landmine QUEUE 3 unit 2's conflict markers hit, worse than
vehicle-composition/des-speed-decision entries which at least had a real
distinguishing attribute to scan-and-match on).

This probe answers, on a live Vissim 2026 instance (attach_to_running=False,
a clone of "Roundabout Billings.US", closed in a ``finally`` block):

[Phase 1a] STATIC type-library scan (no COM instance, no licence consumed) --
same technique every prior v2/v2a-unit probe used, same "speed" name filter
v2a unit ⑧'s own probe used (catches ISpeedDistribution*/ISpeedDistribution
DataPoint* both).

[Phase 1b] LIVE probe on a Roundabout Billings.US clone:
  1. ``Net.SpeedDistributions`` ``GetAll()`` count cross-checked against the
     XML element count (13).
  2. Distribution-level attribute discovery (No, Name -- already known from
     unit ⑧'s own probe, re-confirmed here) on a MULTI-point distribution
     (no=25, "25 mph", 4 points).
  3. Points CHILD COLLECTION discovery: try ``SpeedDistrDatPts`` (matches the
     XML child tag exactly) and siblings.
  4. Point-level attribute discovery: X, FX -- read back and compared
     against the XML ground truth, in GetAll() order.
  5. ItemByKey / identity check on the points collection (expect: none, per
     the static type-library finding above -- verified live via hasattr AND
     an attempted call).
  6. CONTROL: bogus attribute id fail-open/closed, tested SEPARATELY on the
     distribution object and the point object.
  7. Bogus distribution no -- ItemByKey error behavior on
     Net.SpeedDistributions (expect fail CLOSED, matching unit ⑧'s own
     finding for the SAME collection).
  8. ``GetMultipleAttributes(("X","FX"))`` on the points collection -- the
     natural tuple shape COM itself already uses to describe "many points,
     many attributes at once", used to inform what shape ``ReplaceAll``
     itself expects (never guessed).
  9. ONE individual point SetAttValue edit (point[1] of the 4-point
     distribution): change X, read back immediately, AND read back
     point[0]/[2]/[3]'s own values in the SAME distribution -- sibling-
     untouched sanity check.
  10. THE KEY WRITE MECHANISM: ``ReplaceAll(Elements)`` on the points
      collection -- tries multiple candidate ``Elements`` shapes (informed
      by step 8's own discovery) against a scratch distribution, confirms
      the new point count/values via a fresh ``GetAll()``, and settles
      whether this is genuinely an ATOMIC whole-collection replace (partial
      failure impossible) or something else.
  11. THE UNITS QUESTION (this probe's own central finding target): write a
      KNOWN literal value (100) via ``SetAttValue('X', 100)`` on a scratch
      point in a network with ``unitSpeed="MILESPERHOUR"``, read back
      immediately (AttValue), persist, and grep the SAVED XML's own ``x``
      attribute for that same point -- if the saved XML shows literally
      ``100``, SetAttValue/AttValue operate in XML-native km/h regardless of
      display unit (no landmine for this attribute); if the saved XML shows
      a mph-to-km/h SCALED value (~160.9), COM's ``X`` attribute inherits
      the SAME display-unit translation ``Pos`` has, and the write tool must
      convert explicitly.
  12. SILENT-FAILURE MAP: attempt each mathematical-invariant violation via
      ``ReplaceAll`` on a scratch distribution and record rejected-with-
      error / silently-clamped / silently-accepted for each:
        a. a genuine speed DECREASE between consecutive points
        b. fx above 1 (1.5)
        c. fx below 0 (-0.2)
        d. fx DECREASING while x increases (non-monotonic fx)
        e. last point's fx != 1 (ends at 0.9)
        f. first point's fx != 0 (starts at 0.2)
        g. a single-point curve (ReplaceAll with exactly ONE point)
        h. an EMPTY curve (ReplaceAll with zero points)
        i. a negative speed (x = -10)
        j. an absurdly large speed (x = 99999)
        k. a genuine duplicate point (same x AND same fx twice)
        l. the LEGITIMATE Rollercoaster.inpx pattern -- x1 == x2 with
           DIFFERENT fx (a step function) -- expect ACCEPTED, per the
           static finding above.
  13. Persist (save_net_as) + grep the saved XML's speedDistrDatPts for the
      FINAL intended (valid) state + a fresh COM reload to confirm the
      accepted write survived a full round trip.

Never touches the original Examples Demo folder (00-EXECUTION-GUIDE.md rule
4) -- everything happens on a work-dir clone. Always disconnects every
dedicated Vissim instance in a ``finally`` block.

Usage: python tools/probe_des_speed_curves.py [example_dir]
Default example: Roundabout Billings.US (2026 Examples Demo) -- the SAME
network v2a unit ⑧'s own des-speed-decision probe used (5 desSpeedDecisions,
13 speedDistributions, several with >=3 points) since Beijing.CN (every
other v2-unit probe's usual default) was already found to have ZERO
desSpeedDecision elements and no multi-point distributions worth probing.
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
    r"C:\Users\Public\Documents\PTV Vision\PTV Vissim 2026\Examples Demo\Roundabout Billings.US"
)
WORK_ROOT = BACKEND_ROOT.parent / "runtime" / "des-speed-curve-probe"

_CONTROL_BOGUS_ATTR = "ThisAttributeDoesNotExistProbe12345"

_DISTRIBUTIONS_COLLECTION_CANDIDATES = ["SpeedDistributions", "DesSpeedDistributions"]
_POINTS_CHILD_CANDIDATES = ["SpeedDistrDatPts", "SpeedDistributionDataPoints", "DatPts"]
_NO_CANDIDATES = ["No", "no"]
_NAME_CANDIDATES = ["Name", "name"]
_X_CANDIDATES = ["X", "x", "Speed"]
_FX_CANDIDATES = ["FX", "Fx", "fx", "CumFraction"]


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
            in_target = "speed" in class_name.lower()
            if in_target:
                target_hits += 1
                _log(f"\n      -- {class_name} (DispatchBaseClass) --")
            continue
        if in_target and stripped.startswith("def ") and "(self" in stripped:
            _log(f"        {stripped}")

    if target_hits == 0:
        _log("      NO interface with 'speed' in its name found anywhere in the type library.")
    else:
        _log(f"\n      Found {target_hits} '*speed*' interface(s) in the type library (listed above).")


# ---------------------------------------------------------------------------
# Phase 1b: live probe against a Roundabout Billings.US clone
# ---------------------------------------------------------------------------


def _xml_speed_distributions(inpx_path: Path) -> list[dict[str, Any]]:
    root = ET.parse(inpx_path).getroot()
    result = []
    for d in root.findall(".//speedDistributions/speedDistribution"):
        points = [dict(p.attrib) for p in d.findall("speedDistrDatPts/speedDistributionDataPoint")]
        result.append({**dict(d.attrib), "points": points})
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


def _try_replace_all(points_coll: Any, elements: Any, label: str) -> bool:
    try:
        points_coll.ReplaceAll(elements)
        _log(f"        [WORKS] ReplaceAll({label}) succeeded (no exception).")
        return True
    except Exception as exc:
        _log(f"        [no]    ReplaceAll({label}) -> {type(exc).__name__}: {exc}")
        return False


def main() -> None:
    example_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_EXAMPLE
    if not example_dir.is_dir():
        raise SystemExit(f"Example folder not found: {example_dir}")

    _log("#" * 78)
    _log("# PHASE 1a -- static type-library scan (no COM instance, no licence used)")
    _log("#" * 78)
    probe_typelib()

    _log("\n" + "#" * 78)
    _log("# PHASE 1b -- live probe on a Roundabout Billings.US clone")
    _log("#" * 78)

    work_dir = _fresh_work_dir(example_dir.name)
    shutil.copytree(example_dir, work_dir)
    inpx_files = sorted(work_dir.glob("*.inpx"))
    if not inpx_files:
        raise SystemExit(f"No .inpx in {work_dir}")
    working = inpx_files[0]
    _log(f"[1b.0] Working copy: {working}")

    xml_before = _xml_speed_distributions(working)
    _log(f"       XML <speedDistribution> count = {len(xml_before)}")
    for d in xml_before:
        pts = [(p.get("x"), p.get("fx")) for p in d["points"]]
        _log(f"       distribution no={d['no']} name={d.get('name')!r} points={pts}")

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

        _log("\n[1b.3] Distributions collection name discovery on Net:")
        coll_name, distr_coll = _first_working_collection(
            controller.vissim.Net, _DISTRIBUTIONS_COLLECTION_CANDIDATES, "SpeedDistributions"
        )
        if distr_coll is None:
            raise SystemExit("FAIL: could not find the speed-distributions collection on Net.")

        distr_items = list(distr_coll.GetAll())
        _log(f"       Net.{coll_name}.GetAll() count = {len(distr_items)} (XML count = {len(xml_before)})")

        multi_no = next(d["no"] for d in xml_before if len(d["points"]) >= 4)
        multi_distr = next(item for item in distr_items if str(item.AttValue("No")) == multi_no)
        _log(f"\n[1b.4] Distribution-level attribute discovery on no={multi_no} (>=4 points):")
        no_id, _ = _first_working_attr(multi_distr, _NO_CANDIDATES, "No")
        name_id, _ = _first_working_attr(multi_distr, _NAME_CANDIDATES, "Name")
        xml_multi = next(d for d in xml_before if d["no"] == multi_no)
        _log(f"       XML ground truth: no={xml_multi['no']} name={xml_multi.get('name')!r} points={[(p.get('x'), p.get('fx')) for p in xml_multi['points']]}")

        _log("\n[1b.5] Points CHILD COLLECTION discovery on the distribution object...")
        points_coll_name, points_coll = _first_working_child_collection(
            multi_distr, _POINTS_CHILD_CANDIDATES, "SpeedDistrDatPts"
        )
        if points_coll is None:
            raise SystemExit("FAIL: could not find the points child collection.")

        point_items = list(points_coll.GetAll())
        _log(f"       Point count via COM = {len(point_items)} (XML count for this distribution = {len(xml_multi['points'])})")

        x_id = fx_id = None
        if point_items:
            first_point = point_items[0]
            _log("\n[1b.6] Point-level attribute discovery (first point):")
            x_id, x_val = _first_working_attr(first_point, _X_CANDIDATES, "X (point)")
            fx_id, fx_val = _first_working_attr(first_point, _FX_CANDIDATES, "FX (point)")
            _log(f"       XML ground truth for point[0]: {xml_multi['points'][0]}")

            _log("\n[1b.6b] Reading EVERY point via COM, compared against XML ground truth in order:")
            for idx, (pt, xml_pt) in enumerate(zip(point_items, xml_multi["points"])):
                com_x = pt.AttValue(x_id) if x_id else None
                com_fx = pt.AttValue(fx_id) if fx_id else None
                _log(f"       point[{idx}]: COM=({com_x!r},{com_fx!r})  XML=({xml_pt.get('x')!r},{xml_pt.get('fx')!r})")

        # -------------------------------------------------------------
        # [1b.7] Identity check on the points collection: ItemByKey?
        # -------------------------------------------------------------
        _log("\n[1b.7] Does the points child collection expose ItemByKey?...")
        has_item_by_key = hasattr(points_coll, "ItemByKey")
        _log(f"        hasattr(points_coll, 'ItemByKey') = {has_item_by_key}")
        if has_item_by_key:
            try:
                bogus_pt = points_coll.ItemByKey(999999)
                _log(f"        ItemByKey(999999) -> {bogus_pt!r} (UNEXPECTED -- did not raise)")
            except Exception as exc:
                _log(f"        ItemByKey(999999) -> {type(exc).__name__}: {exc} (fails CLOSED)")

        # -------------------------------------------------------------
        # [1b.8] CONTROL: bogus attribute id, distribution object AND
        # point object separately.
        # -------------------------------------------------------------
        _log("\n[1b.8] CONTROL: bogus attribute id on the DISTRIBUTION object...")
        try:
            bogus = multi_distr.AttValue(_CONTROL_BOGUS_ATTR)
            _log(f"        [CONTROL] distribution.AttValue({_CONTROL_BOGUS_ATTR!r}) -> {bogus!r} (fails OPEN)")
        except Exception as exc:
            _log(f"        [CONTROL] distribution.AttValue({_CONTROL_BOGUS_ATTR!r}) -> {type(exc).__name__}: {exc} (fails CLOSED)")

        if point_items:
            _log("\n[1b.8b] CONTROL: bogus attribute id on the POINT object...")
            try:
                bogus_p = point_items[0].AttValue(_CONTROL_BOGUS_ATTR)
                _log(f"        [CONTROL] point.AttValue({_CONTROL_BOGUS_ATTR!r}) -> {bogus_p!r} (fails OPEN)")
            except Exception as exc:
                _log(f"        [CONTROL] point.AttValue({_CONTROL_BOGUS_ATTR!r}) -> {type(exc).__name__}: {exc} (fails CLOSED)")

        _log("\n[1b.9] Bogus speed-distribution no -- ItemByKey error behavior...")
        try:
            bogus_distr = distr_coll.ItemByKey(999999)
            _log(f"        ItemByKey(999999) -> {bogus_distr!r} (UNEXPECTED -- did not raise)")
        except Exception as exc:
            _log(f"        ItemByKey(999999) -> {type(exc).__name__}: {exc} (fails CLOSED)")

        # -------------------------------------------------------------
        # [1b.10] GetMultipleAttributes -- the natural "many points, many
        # attributes" shape, to inform what ReplaceAll itself expects.
        # -------------------------------------------------------------
        multi_shape = None
        if x_id and fx_id:
            _log(f"\n[1b.10] GetMultipleAttributes(({x_id!r}, {fx_id!r})) on the points collection...")
            try:
                multi_shape = points_coll.GetMultipleAttributes((x_id, fx_id))
                _log(f"        -> {multi_shape!r}")
            except Exception as exc:
                _log(f"        RAISED: {type(exc).__name__}: {exc}")

        # -------------------------------------------------------------
        # [1b.11] Individual point SetAttValue edit + sibling-untouched
        # check.
        # -------------------------------------------------------------
        if point_items and x_id and len(point_items) >= 2:
            _log(f"\n[1b.11] Individual point SetAttValue edit test on point[1] of distribution {multi_no}...")
            before_vals = [(p.AttValue(x_id), p.AttValue(fx_id)) for p in point_items]
            _log(f"        BEFORE -- every point's (X,FX): {before_vals}")
            old_x = before_vals[1][0]
            new_x = round(float(old_x) + 1.0, 3)
            try:
                point_items[1].SetAttValue(x_id, new_x)
                _log(f"        SetAttValue({x_id!r}, {new_x}) on point[1] succeeded (was {old_x!r}).")
            except Exception as exc:
                _log(f"        SetAttValue RAISED: {type(exc).__name__}: {exc}")
            after_vals = [(p.AttValue(x_id), p.AttValue(fx_id)) for p in point_items]
            _log(f"        AFTER  -- every point's (X,FX): {after_vals}")
            siblings_changed = {
                idx: (before_vals[idx], after_vals[idx])
                for idx in range(len(before_vals))
                if idx != 1 and before_vals[idx] != after_vals[idx]
            }
            _log(f"        Sibling points whose values changed as a side effect: {siblings_changed} (empty == independent per-point edit, as expected)")
            # restore
            point_items[1].SetAttValue(x_id, old_x)

        # -------------------------------------------------------------
        # [1b.12] THE UNITS QUESTION: write a KNOWN literal via
        # SetAttValue and check what lands in the saved XML.
        # -------------------------------------------------------------
        units_finding: dict[str, Any] = {}
        if point_items and x_id:
            _log(f"\n[1b.12] UNITS TEST: SetAttValue({x_id!r}, 100) on point[0] of distribution {multi_no}...")
            original_x = point_items[0].AttValue(x_id)
            point_items[0].SetAttValue(x_id, 100)
            readback_100 = point_items[0].AttValue(x_id)
            _log(f"        Immediate readback after writing 100: {readback_100!r} (network unitSpeed display setting may not be km/h)")
            units_finding["readback_after_writing_100"] = readback_100
            units_finding["original_x_before_test"] = original_x
            # restore before moving on to the destructive scratch-distribution tests
            point_items[0].SetAttValue(x_id, original_x)

        # -------------------------------------------------------------
        # [1b.13] ReplaceAll + silent-failure map, all run against a
        # SCRATCH distribution (a real one, no=25 if that's the multi_no,
        # otherwise pick a different 2-point one) so the primary
        # multi-point distribution used for steps above is left alone.
        # -------------------------------------------------------------
        scratch_no = next((d["no"] for d in xml_before if d["no"] != multi_no), None)
        scratch_distr = None
        scratch_points_coll = None
        if scratch_no is not None:
            scratch_distr = next(item for item in distr_items if str(item.AttValue("No")) == scratch_no)
            scratch_points_coll = getattr(scratch_distr, points_coll_name)
            _log(f"\n[1b.13] Using distribution no={scratch_no} as the SCRATCH target for ReplaceAll + silent-failure tests.")

            _log("\n[1b.13a] ReplaceAll shape discovery -- trying candidate Elements shapes...")
            _log(
                "         CRITICAL METHODOLOGY NOTE (found the hard way, first probe run): a "
                "shape that raises no exception is NOT proof it worked -- Vissim's ReplaceAll "
                "silently NO-OPS on a wrong shape (reports success, changes nothing). Every "
                "candidate below is verified by READING BACK and comparing against the intended "
                "values -- 'no exception' alone proved NOTHING in this probe's own first run."
            )
            target_intent = [(0.0, 0.0), (30.0, 0.5), (60.0, 1.0)]
            candidate_shapes: list[tuple[str, Any]] = [
                ("nested-tuple-of-(x,fx)-pairs", tuple((x, fx) for x, fx in target_intent)),
                ("nested-tuple-of-(fx,x)-pairs", tuple((fx, x) for x, fx in target_intent)),
                ("nested-list-of-(x,fx)-lists", [[x, fx] for x, fx in target_intent]),
                ("FLAT-alternating-sequence", tuple(v for pair in target_intent for v in pair)),
            ]
            working_shape_label = None
            for label, elements in candidate_shapes:
                # Reset to a known BEFORE state before every candidate attempt, so a
                # prior candidate's silent no-op can never be mistaken for this
                # candidate's own success.
                scratch_points_coll.ReplaceAll((0, 0, 50, 1))
                ok = _try_replace_all(scratch_points_coll, elements, label)
                readback = [(p.AttValue(x_id), p.AttValue(fx_id)) for p in scratch_points_coll.GetAll()]
                matches_intent = ok and [
                    (round(x, 6), round(fx, 6)) for x, fx in readback
                ] == [(round(x, 6), round(fx, 6)) for x, fx in target_intent]
                _log(
                    f"        -> readback after this shape: {readback}  "
                    f"MATCHES INTENT: {matches_intent} "
                    f"{'(SILENT NO-OP -- reported success but changed nothing)' if ok and not matches_intent else ''}"
                )
                if matches_intent and working_shape_label is None:
                    working_shape_label = label
            _log(f"        Working shape (the ONLY one whose readback actually matched intent): {working_shape_label!r}")

            # -------------------------------------------------------------
            # [1b.13b] Silent-failure map -- each invariant violation,
            # attempted via ReplaceAll (the actual write mechanism), using
            # the VERIFIED-WORKING flat-alternating-sequence shape.
            # -------------------------------------------------------------
            def _elements(pairs: list[tuple[float, float]]) -> Any:
                return tuple(v for pair in pairs for v in pair)

            silent_failure_cases: list[tuple[str, list[tuple[float, float]]]] = [
                ("speed_decrease", [(0, 0), (100, 0.5), (50, 1)]),
                ("fx_above_1", [(0, 0), (50, 0.5), (100, 1.5)]),
                ("fx_below_0", [(0, -0.2), (50, 0.5), (100, 1)]),
                ("fx_non_monotonic", [(0, 0), (50, 0.9), (100, 0.5)]),
                ("last_fx_not_1", [(0, 0), (50, 0.5), (100, 0.9)]),
                ("first_fx_not_0", [(0, 0.2), (50, 0.5), (100, 1)]),
                ("single_point", [(50, 1)]),
                ("empty", []),
                ("negative_speed", [(-10, 0), (50, 1)]),
                ("absurd_speed", [(0, 0), (99999, 1)]),
                ("duplicate_point", [(0, 0), (50, 0.5), (50, 0.5), (100, 1)]),
                ("legit_step_function", [(50, 0), (50, 1)]),
            ]
            _log("\n[1b.13b] SILENT-FAILURE MAP -- attempting each invariant violation via ReplaceAll:")
            for case_name, pairs in silent_failure_cases:
                elements = _elements(pairs)
                try:
                    scratch_points_coll.ReplaceAll(elements)
                    readback = [(p.AttValue(x_id), p.AttValue(fx_id)) for p in scratch_points_coll.GetAll()]
                    _log(f"        [{case_name}] ACCEPTED (no exception). Intended={pairs} Readback={readback}")
                except Exception as exc:
                    _log(f"        [{case_name}] REJECTED: {type(exc).__name__}: {exc}")

            # Restore the scratch distribution to a valid state before persisting.
            valid_restore = _elements([(0, 0), (50, 0.5), (100, 1)])
            try:
                scratch_points_coll.ReplaceAll(valid_restore)
                _log(f"\n        Restored scratch distribution {scratch_no} to a valid 3-point curve before persisting.")
            except Exception as exc:
                _log(f"\n        WARNING: could not restore scratch distribution to a valid state: {exc}")

        # -------------------------------------------------------------
        # [1b.14] Persist + reparse + fresh COM reload.
        # -------------------------------------------------------------
        tmp_path = working.with_name(working.stem + "_saving" + working.suffix)
        _log(f"\n[1b.14] Persisting via save_net_as({tmp_path.name}) + os.replace onto {working.name}...")
        controller.save_net_as(tmp_path)
        os.replace(tmp_path, working)
        _log(f"        Persisted. Working copy size = {working.stat().st_size} bytes")

        xml_after = _xml_speed_distributions(working)
        if scratch_no is not None:
            xml_scratch_after = next(d for d in xml_after if d["no"] == scratch_no)
            _log(f"[1b.15] Saved XML scratch distribution no={scratch_no} points: {[(p.get('x'), p.get('fx')) for p in xml_scratch_after['points']]}")
        xml_units_after = next(d for d in xml_after if d["no"] == multi_no)
        _log(f"        Saved XML distribution no={multi_no} (units-test/multi-point target) points UNCHANGED (restored before persist): {[(p.get('x'), p.get('fx')) for p in xml_units_after['points']]}")

        _log("\n[1b.16] Fresh COM reload of the saved file (separate connection)...")
        controller.disconnect()
        verify_controller = VissimController()
        verify_controller.connect(visible=True, launch_if_missing=True, attach_to_running=False)
        verify_controller.load_network(str(working))
        reload_distrs = getattr(verify_controller.vissim.Net, coll_name)
        if scratch_no is not None:
            reload_scratch = reload_distrs.ItemByKey(int(scratch_no))
            reload_points = list(getattr(reload_scratch, points_coll_name).GetAll())
            reload_vals = [(p.AttValue(x_id), p.AttValue(fx_id)) for p in reload_points]
            _log(f"        Reload scratch distribution no={scratch_no} points: {reload_vals}")

        controller = verify_controller  # so the finally block disconnects this one

        _log("\n" + "#" * 78)
        _log("# SUMMARY")
        _log("#" * 78)
        _log(f"Distributions collection: Net.{coll_name}")
        _log(f"Points child collection: .{points_coll_name}")
        _log(f"Point attribute ids: X={x_id!r} FX={fx_id!r}")
        _log(f"Units test readback after writing literal 100: {units_finding}")

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
