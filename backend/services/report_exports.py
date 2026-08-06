"""Report-style exports (Word .docx and PNG charts) for the dashboard.

These mirror the formal traffic-report deliverables:
  - LoS summary (intersection + per-movement, colour-coded)  -> .docx
  - Flow / GEH validation summary (AM & PM)                  -> .docx
  - Travel-time cumulative charts (NB/SB x AM/PM)            -> .zip of PNGs

Heavy libs (python-docx, matplotlib) are imported lazily inside each builder so
this module always imports even if they are missing; the builder then raises a
clear error that the router surfaces as a 500.
"""
from __future__ import annotations

import re
import zipfile
from io import BytesIO

from services.datasets import (
    get_active_inpx_path,
    get_active_modifications_dir,
    get_active_dataset,
    get_active_real_counts_excel,
)
from services.results_reader import (
    get_scenario_results,
    list_scenarios,
    _load_movement_metadata,
    _classify_link_direction,
)
from services.validation import (
    geh as _geh,
    geh_pass_rate,
    load_base_workbook,
    load_real_counts,
)
from services.validation_profiles import (
    evaluate_critical_intersections,
    flow_within_tolerance,
    get_active_profile,
    parse_critical_node_ids,
)

# --- shared helpers ---------------------------------------------------------

_ANGLE = {"north": 0, "east": 90, "south": 180, "west": 270}
_APPROACH = {0: "NB", 90: "EB", 180: "SB", 270: "WB"}
_OID = re.compile(r"^(?P<node>\d+)-(?P<from>\d+)-[-\d.]+-(?P<to>\d+)-[-\d.]+$")
_LOS_FILL = {"A": "00B050", "B": "92D050", "C": "C6E0B4", "D": "FFFF00", "E": "ED7D31", "F": "FF0000"}
_APPR_ORDER = {"EB": 0, "WB": 1, "NB": 2, "SB": 3}
_TURN_ORDER = {"LT": 0, "TH": 1, "RT": 2, "U": 3, "?": 4}


def _los(delay):
    if delay is None:
        return "-"
    for limit, grade in ((10, "A"), (20, "B"), (35, "C"), (55, "D"), (80, "E")):
        if delay <= limit:
            return grade
    return "F"


# A link's name encodes its travel bound (e.g. "Murdoch Dr NB"); the leg it
# touches the node from differs for the incoming vs outgoing link. This is far
# more reliable than geometry for divided carriageways and long links that span
# past the node (which fooled the geometric far-point classifier).
_BOUND_RE = re.compile(r"(?<![A-Za-z])(NB|SB|EB|WB)(?![A-Za-z])", re.IGNORECASE)
_FROM_LEG = {"NB": "south", "SB": "north", "EB": "west", "WB": "east"}
_TO_LEG = {"NB": "north", "SB": "south", "EB": "east", "WB": "west"}


def _leg_from_name(name: str, is_from: bool) -> str | None:
    if not name:
        return None
    m = _BOUND_RE.search(name)
    if not m:
        return None
    return (_FROM_LEG if is_from else _TO_LEG)[m.group(1).upper()]


def _turn_label(from_dir, to_dir):
    if from_dir not in _ANGLE or to_dir not in _ANGLE:
        return "?"
    in_heading = (_ANGLE[from_dir] + 180) % 360
    delta = (_ANGLE[to_dir] - in_heading) % 360
    return {0: "TH", 90: "RT", 180: "U", 270: "LT"}.get(delta, "?")


def _approach_label(from_dir):
    if from_dir not in _ANGLE:
        return "?"
    return _APPROACH[(_ANGLE[from_dir] + 180) % 360]


def _scenario_for_period(period: str) -> str | None:
    """Pick the scenario id for AM/PM. Prefer name match, else order (AM=0, PM=1)."""
    scenarios = list_scenarios()
    if not scenarios:
        return None
    for sc in scenarios:
        if period.upper() in str(sc.get("name", "")).upper():
            return sc["id"]
    idx = 0 if period.upper() == "AM" else 1
    return scenarios[idx]["id"] if idx < len(scenarios) else scenarios[0]["id"]


def _movement_rows(scenario_id: str) -> list[dict]:
    """Per-movement rows: node, approach, turn, volume, delay, los, qmax."""
    res = get_scenario_results(scenario_id) if scenario_id else None
    if not res:
        return []
    meta_all = _load_movement_metadata(
        str(get_active_inpx_path()),
        str(get_active_modifications_dir()),
        str(get_active_dataset().get("revision") or ""),
    )
    qmap = {str(q.get("OBJECT_ID")): q for q in (res.get("queues") or [])}
    rows = []
    for m in res.get("movements") or []:
        oid = str(m.get("OBJECT_ID", ""))
        mt = _OID.match(oid)
        if not mt:
            continue
        node = mt.group("node")
        meta = meta_all.get(node)
        if not meta:
            continue
        names = meta.get("link_names", {})
        fd = _leg_from_name(names.get(mt.group("from"), ""), True) or \
            _classify_link_direction(meta["center"], meta["links"].get(mt.group("from"), []))
        td = _leg_from_name(names.get(mt.group("to"), ""), False) or \
            _classify_link_direction(meta["center"], meta["links"].get(mt.group("to"), []))
        vol = m.get("VEHS_MEAN")
        delay = m.get("VEHDELAY_MEAN")
        qmax = (qmap.get(oid) or {}).get("QLENMAX_MEAN")
        rows.append({
            "node": node,
            "approach": _approach_label(fd),
            "turn": _turn_label(fd, td),
            "volume": round(vol) if vol is not None else None,
            "delay": round(delay, 1) if delay is not None else None,
            "los": _los(delay),
            "qmax": round(qmax, 1) if qmax is not None else None,
        })
    return rows


# --- docx cell helpers ------------------------------------------------------

def _setcell(cell, text, *, bold=False, fill=None, size=9, align="center"):
    from docx.shared import Pt
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml.ns import qn
    from docx.oxml import OxmlElement

    cell.text = ""
    p = cell.paragraphs[0]
    p.alignment = WD_ALIGN_PARAGRAPH.LEFT if align == "left" else WD_ALIGN_PARAGRAPH.CENTER
    run = p.add_run("" if text is None else str(text))
    run.bold = bold
    run.font.size = Pt(size)
    tcPr = cell._tc.get_or_add_tcPr()
    va = OxmlElement("w:vAlign"); va.set(qn("w:val"), "center"); tcPr.append(va)
    if fill:
        shd = OxmlElement("w:shd")
        shd.set(qn("w:val"), "clear"); shd.set(qn("w:color"), "auto"); shd.set(qn("w:fill"), fill)
        tcPr.append(shd)


def _set_border(cell, edge, sz=12, color="E69138"):
    from docx.oxml.ns import qn
    from docx.oxml import OxmlElement

    tcPr = cell._tc.get_or_add_tcPr()
    borders = tcPr.find(qn("w:tcBorders"))
    if borders is None:
        borders = OxmlElement("w:tcBorders"); tcPr.append(borders)
    el = borders.find(qn(f"w:{edge}"))
    if el is None:
        el = OxmlElement(f"w:{edge}"); borders.append(el)
    el.set(qn("w:val"), "single"); el.set(qn("w:sz"), str(sz)); el.set(qn("w:space"), "0"); el.set(qn("w:color"), color)


# --- 1. LoS summary (.docx) -------------------------------------------------

def los_summary_rows() -> dict:
    """Structured LoS rows (per movement + per-node weighted-overall).

    The single source both the .docx table (:func:`add_los_section`) and the
    CSV data-extract endpoint (:func:`los_summary_csv_rows`, used by the report
    page's "데이터 내보내기" button) render from -- the row/weighting math is
    computed exactly once here, never re-derived in a second pipeline.
    """
    am = _movement_rows(_scenario_for_period("AM"))
    pm = _movement_rows(_scenario_for_period("PM"))

    def idx(rows):
        return {(r["node"], r["approach"], r["turn"]): r for r in rows}
    ai, pi = idx(am), idx(pm)
    keys = sorted(set(ai) | set(pi), key=lambda k: (int(k[0]), _APPR_ORDER.get(k[1], 9), _TURN_ORDER.get(k[2], 9)))

    def vol(d):
        return (d or {}).get("volume") or 0
    keys = [k for k in keys if vol(ai.get(k)) > 0 or vol(pi.get(k)) > 0]
    nodes: dict[str, list] = {}
    for k in keys:
        nodes.setdefault(k[0], []).append(k)

    def weighted(rowkeys, table):
        wd = w = 0.0
        for k in rowkeys:
            r = table.get(k)
            if not r:
                continue
            v = r.get("volume") or 0
            d = r.get("delay")
            if d is not None:
                ww = v if v > 0 else 1
                wd += d * ww; w += ww
        return round(wd / w, 2) if w else None

    per_node = []
    for nid in sorted(nodes, key=int):
        rowkeys = nodes[nid]
        movement_rows = []
        for k in rowkeys:
            a = ai.get(k, {}); p = pi.get(k, {})
            movement_rows.append({
                "node": nid, "approach": k[1], "turn": k[2],
                "am_volume": a.get("volume"), "am_delay": a.get("delay"), "am_los": a.get("los"), "am_qmax": a.get("qmax"),
                "pm_volume": p.get("volume"), "pm_delay": p.get("delay"), "pm_los": p.get("los"), "pm_qmax": p.get("qmax"),
            })
        amd = weighted(rowkeys, ai); pmd = weighted(rowkeys, pi)
        per_node.append({
            "node": nid,
            "rows": movement_rows,
            "weighted_overall": {"am_delay": amd, "am_los": _los(amd), "pm_delay": pmd, "pm_los": _los(pmd)},
        })
    return {"per_node": per_node, "am_count": len(am), "pm_count": len(pm)}


def los_summary_csv_rows() -> list[dict]:
    """Flat rows for CSV export -- identical numbers to the .docx table:
    one row per movement, plus one 'Weighted Overall' row per node."""
    data = los_summary_rows()
    rows: list[dict] = []
    for node in data["per_node"]:
        for r in node["rows"]:
            rows.append({**r, "row_type": "movement"})
        wo = node["weighted_overall"]
        rows.append({
            "node": node["node"], "approach": "", "turn": "Weighted Overall",
            "am_volume": None, "am_delay": wo["am_delay"], "am_los": wo["am_los"], "am_qmax": None,
            "pm_volume": None, "pm_delay": wo["pm_delay"], "pm_los": wo["pm_los"], "pm_qmax": None,
            "row_type": "weighted_overall",
        })
    return rows


def add_los_section(doc, *, level: int = 1, heading: str = "VISSIM LoS Summary", with_heading: bool = True) -> None:
    """Append the colour-coded LoS table (AM/PM) to an existing Document."""
    from docx.shared import Pt
    from docx.enum.table import WD_TABLE_ALIGNMENT

    data = los_summary_rows()

    if with_heading:
        doc.add_heading(heading, level=level)
    ds = get_active_dataset().get("filename") or "active dataset"
    note = doc.add_paragraph().add_run(
        f"Source: {ds}. AM/PM = base scenarios. LOS = HCM signalized thresholds on VISSIM control delay "
        "(A<=10, B<=20, C<=35, D<=55, E<=80, F>80 s/veh). Max Queue = QLENMAX (per lane group, network units)."
    )
    note.font.size = Pt(8)

    if data["am_count"] == 0 and data["pm_count"] == 0:
        doc.add_paragraph("No simulation results are available yet. Run a scenario, then re-export.")
        return

    per_node = data["per_node"]
    total_rows = 2 + sum(len(n["rows"]) + 1 for n in per_node)
    t = doc.add_table(rows=total_rows, cols=11)
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    t.style = "Table Grid"

    r0 = t.rows[0].cells
    _setcell(r0[0].merge(r0[1]).merge(r0[2]), "Base", bold=True, fill="FFC000")
    _setcell(r0[3].merge(r0[4]).merge(r0[5]).merge(r0[6]), "AM Peak", bold=True, fill="FFC000")
    _setcell(r0[7].merge(r0[8]).merge(r0[9]).merge(r0[10]), "PM Peak", bold=True, fill="FFC000")
    sub = ["Location", "Approach", "Direction", "Volume", "Delay", "LOS", "Max Queue Length",
           "Volume", "Delay", "LOS", "Max Queue Length"]
    for i, c in enumerate(t.rows[1].cells):
        _setcell(c, sub[i], bold=True, fill="D9D9D9", size=8)

    ri = 2
    for node in per_node:
        nid = node["node"]
        start = ri
        for r_data in node["rows"]:
            r = t.rows[ri].cells
            _setcell(r[1], r_data["approach"]); _setcell(r[2], r_data["turn"])
            _setcell(r[3], r_data["am_volume"]); _setcell(r[4], r_data["am_delay"])
            _setcell(r[5], r_data["am_los"], bold=True, fill=_LOS_FILL.get(r_data["am_los"]))
            _setcell(r[6], r_data["am_qmax"])
            _setcell(r[7], r_data["pm_volume"]); _setcell(r[8], r_data["pm_delay"])
            _setcell(r[9], r_data["pm_los"], bold=True, fill=_LOS_FILL.get(r_data["pm_los"]))
            _setcell(r[10], r_data["pm_qmax"])
            ri += 1
        r = t.rows[ri].cells
        _setcell(r[1].merge(r[2]), "Weighted Overall", bold=True)
        wo = node["weighted_overall"]
        amd, pmd = wo["am_delay"], wo["pm_delay"]
        _setcell(r[3], ""); _setcell(r[4], amd, bold=True)
        _setcell(r[5], wo["am_los"], bold=True, fill=_LOS_FILL.get(wo["am_los"]))
        _setcell(r[6], ""); _setcell(r[7], ""); _setcell(r[8], pmd, bold=True)
        _setcell(r[9], wo["pm_los"], bold=True, fill=_LOS_FILL.get(wo["pm_los"]))
        _setcell(r[10], "")
        overall = ri
        ri += 1
        loc = t.cell(start, 0)
        for rr in range(start + 1, overall + 1):
            loc = loc.merge(t.cell(rr, 0))
        _setcell(loc, f"Node {nid}", bold=True)
        # merge approach groups
        i = start
        while i < overall:
            appr_text = t.cell(i, 1).text
            j = i
            while j + 1 < overall and t.cell(j + 1, 1).text == appr_text:
                j += 1
            if j > i:
                base_c = t.cell(i, 1)
                for rr in range(i + 1, j + 1):
                    base_c = base_c.merge(t.cell(rr, 1))
                _setcell(base_c, appr_text)
            i = j + 1


def export_los_summary() -> bytes:
    from docx import Document
    doc = Document()
    add_los_section(doc)
    out = BytesIO(); doc.save(out); return out.getvalue()


# --- 2. Flow / GEH validation (.docx) ---------------------------------------

def _flow_pass(m, o):
    """One movement against its MRWA Table 5-12 flow band.

    The 700 / 100 / 2700 / 15 % / 400 numbers used to be hard-coded right
    here, with no connection to frontend/lib/validationProfiles.ts -- a
    guideline revision applied on one side would have shipped a report and a
    dashboard that disagreed. They now come from
    ``shared/mrwa-validation-criteria.json`` via
    ``services.validation_profiles``, the SAME file the frontend module is
    pinned to.

    Values are unchanged (Table 5-12 "Traffic flow validation criteria",
    §5.6.3.1, doc p.213 -- doc page, not PDF page). The historical
    ``if o > 0 else diff == 0`` branch was unreachable: the ``o < 700`` arm
    above it already catches every non-positive observed value.

    Returns ``None`` (falsy, so the ``sum(1 for ... if _flow_pass(...))``
    callers below are unaffected) when either side is missing.
    """
    return flow_within_tolerance(m, o)


def _node_stats(node):
    dirs = [d for d in (node.get("directions") or []) if d.get("real") is not None and d.get("sim_basam") is not None]
    obs = node.get("total_real") or sum(d["real"] for d in dirs)
    mdl = node.get("total_sim_basam") or sum(d["sim_basam"] for d in dirs)
    # GEH gets its OWN denominator: a movement with 0 observed AND 0 modelled
    # has an undefined GEH (services.validation.geh -> None), so it is excluded
    # from both the numerator and the denominator rather than scoring a free
    # 0.0 pass (unit 1.5 D3). ``counts`` keeps its old meaning -- the number of
    # movement counts compared -- so the Flow % column is untouched; the
    # difference is published as ``geh_excluded``.
    rate = geh_pass_rate(_geh(d["sim_basam"], d["real"]) for d in dirs)
    return {
        "counts": len(dirs),
        "obs": obs,
        "mdl": mdl,
        "flow_ok": sum(1 for d in dirs if _flow_pass(d["sim_basam"], d["real"])),
        "geh_ok": rate["passed"],
        "geh_counts": rate["total"],
        "geh_excluded": rate["excluded"],
    }


def _collect_flow_geh(period):
    path = str(get_active_real_counts_excel())
    load_real_counts.cache_clear()
    rc = load_real_counts(path, period)
    rows, tot = [], {"counts": 0, "obs": 0, "mdl": 0, "flow_ok": 0, "geh_ok": 0, "geh_counts": 0, "geh_excluded": 0}
    for nid in sorted(rc, key=lambda x: int(x) if x.isdigit() else 999):
        s = _node_stats(rc[nid])
        rows.append((nid, s))
        for k in tot:
            tot[k] += s[k]
    return rows, tot


def _pct_diff(m, o):
    return (m - o) / o * 100 if o else 0.0


def _flow_geh_row(label, node, s):
    # Rounding matches the .docx table's display precision exactly (%Diff to
    # 1dp, Flow/GEH % to whole percent) -- see build()'s _setcell calls below
    # -- so a CSV value and its rendered table cell are the same number.
    return {
        "period": label, "node": node, "counts": s["counts"],
        "observed": round(s["obs"]), "modelled": round(s["mdl"]),
        "pct_diff": round(_pct_diff(s["mdl"], s["obs"]), 1),
        "flow_pct": round(s["flow_ok"] / s["counts"] * 100) if s["counts"] else 0,
        # GEH % is over ``geh_counts`` (rows with a DEFINED GEH), not ``counts``
        # -- see _node_stats. ``geh_excluded`` publishes the difference so the
        # shrunken denominator is disclosed rather than hidden.
        #
        # ``None`` -- NOT 0 -- when every count at this node is undefined. A
        # node with nothing to measure published "0%" here, which a client reads
        # as total failure; the previous 0.0-GEH behaviour published "100%",
        # which reads as a perfect pass. Both are inventions. The other two
        # surfaces already render this state as "not a number"
        # (``report_builder`` prints "-", ``exporters`` leaves the cell blank),
        # so this one says so too -- rendered "-" in the .docx and blank in the
        # CSV (``routers/report.py`` maps None -> "").
        "geh_pct": round(s["geh_ok"] / s["geh_counts"] * 100) if s["geh_counts"] else None,
        "geh_counts": s["geh_counts"],
        "geh_excluded": s["geh_excluded"],
    }


def _pct_cell(value) -> str:
    """Percent cell for the .docx: ``-`` when the statistic is undefined."""
    return "-" if value is None else f"{value:.0f}%"


# --- MRWA §5.6.3 criteria coverage -------------------------------------------
#
# Three §5.6.3 criteria of the same standing as Table 5-12 had no
# representation in this export at all. Two of them have NO ingest path in
# this codebase today (there is nowhere to upload an observed SCATS cycle/
# green time, and nowhere to upload an observed saturation flow), so they are
# published as an explicit "not assessable" state rather than being omitted --
# an omitted criterion reads as a criterion that passed.
#
# Citations name the TABLE and the DOC PAGE, never an in-text label: §5's
# in-text figure/table cross-references carry nine measured errors (see the
# "Doc defects found" block of docs/product/reference/mrwa-section5-extract.md).

_NOT_ASSESSABLE = "관측값 없음 — 평가 불가 (not assessable: no observed data in this project)"
_NO_CRITICAL_DESIGNATED = (
    "critical 교차로 미지정 — 검사 미실행 "
    "(no critical intersection designated: this check did not run)"
)


def _node_geh_values(node) -> list:
    """Per-movement GEH at one node, ``None`` where GEH is undefined."""
    out = []
    for d in node.get("directions") or []:
        if d.get("real") is None or d.get("sim_basam") is None:
            continue
        out.append(_geh(d["sim_basam"], d["real"]))
    return out


def _critical_inputs_for_period(period: str) -> list[dict]:
    path = str(get_active_real_counts_excel())
    load_real_counts.cache_clear()
    rc = load_real_counts(path, period)
    return [
        {
            "node_id": str(nid),
            "node_name": rc[nid].get("node_name"),
            "geh_values": _node_geh_values(rc[nid]),
        }
        for nid in sorted(rc, key=lambda x: int(x) if str(x).isdigit() else 999)
    ]


def flow_geh_verdict(critical_node_ids=None, category: str | None = None) -> dict:
    """Overall traffic-volume verdict, WITH the §5.6.3.1 critical override.

    §5.6.3.1 (body text, doc p.213): "Even if the model outputs meet the
    traffic volume validation criteria for the entire network, the modeller
    should check and ensure that turning volumes at critical intersection(s)
    achieve GEH less than 5."

    So a network-wide PASS is not sufficient. This returns
    ``{"verdict": "PASS"|"FAIL"|"NOT_ASSESSABLE", ...}`` where a single
    designated intersection carrying one movement at GEH >= 5 flips the
    verdict to FAIL and names the node -- even when the network-wide rate
    clears the category's Table 5-12 threshold.

    Note on scope: this table publishes ONE aggregate "Flow %" over all three
    Table 5-12 bands rather than the per-band rates the table actually
    specifies, so the network leg here is evaluated on the GEH < 5 rate only
    and ``network_note`` says so. Inventing a per-band verdict from an
    aggregate would be worse than declaring the gap.
    """
    profile = get_active_profile(category)
    requested = parse_critical_node_ids(critical_node_ids)

    periods: dict[str, dict] = {}
    network_passed: bool | None = None
    for period, label in (("AM", "AM Peak"), ("PM", "PM Peak")):
        _rows, tot = _collect_flow_geh(period)
        geh_pct = round(tot["geh_ok"] / tot["geh_counts"] * 100) if tot["geh_counts"] else None
        leg = geh_pct is not None and geh_pct >= profile.geh_under_5_pct
        if geh_pct is not None:
            network_passed = leg if network_passed is None else (network_passed and leg)
        periods[label] = {
            "geh_pct": geh_pct,
            "geh_counts": tot["geh_counts"],
            "passed": None if geh_pct is None else leg,
            "critical": evaluate_critical_intersections(
                _critical_inputs_for_period(period),
                requested,
                profile.critical_intersection_geh_max,
            ),
        }

    failing: list[str] = []
    for label, leg in periods.items():
        for node in leg["critical"]["nodes"]:
            if node["passed"] is False:
                failing.append(
                    f"{label} {node['node_name'] or 'Node'} (id {node['node_id']}), "
                    f"worst GEH {node['worst_geh']:.2f}"
                )

    critical_passed: bool | None
    if not requested:
        critical_passed = None
    elif failing:
        critical_passed = False
    elif any(leg["critical"]["passed"] is True for leg in periods.values()):
        critical_passed = True
    else:
        critical_passed = None

    reasons: list[str] = []
    if critical_passed is False:
        verdict = "FAIL"
        reasons.append(
            "MRWA §5.6.3.1 (doc p.213) critical-intersection override: "
            + "; ".join(failing)
            + f" — GEH must be < {profile.critical_intersection_geh_max:g} at every "
            "designated critical intersection, even when the network-wide "
            "criteria are met."
        )
    elif network_passed is False:
        verdict = "FAIL"
        reasons.append(
            f"Network-wide GEH < 5 rate is below the {profile.name_en} "
            f"Table 5-12 (doc p.213) threshold of {profile.geh_under_5_pct:g}%."
        )
    elif network_passed is True:
        verdict = "PASS"
    else:
        verdict = "NOT_ASSESSABLE"
        reasons.append("No count comparison with a defined GEH is available.")

    if not requested:
        reasons.append(_NO_CRITICAL_DESIGNATED)

    return {
        "category": profile.id,
        "category_name_en": profile.name_en,
        "geh_under_5_pct_required": profile.geh_under_5_pct,
        "network_passed": network_passed,
        "network_note": (
            "Evaluated on the GEH < 5 rate only; this table's Flow % aggregates "
            "all three Table 5-12 bands and is not split per band here."
        ),
        "periods": periods,
        "critical_requested_ids": requested,
        "critical_passed": critical_passed,
        "critical_failures": failing,
        "verdict": verdict,
        "reasons": reasons,
    }


def criteria_coverage_rows(critical_node_ids=None, category: str | None = None) -> list[dict]:
    """The three §5.6.3 criteria this export can speak to, and their state.

    One row each, so a reader can see WHICH criteria were evaluated and which
    could not be. Never a silent pass and never an invented number.
    """
    verdict = flow_geh_verdict(critical_node_ids, category)
    geh_max = get_active_profile(category).critical_intersection_geh_max

    if verdict["critical_passed"] is False:
        critical_state = "FAIL — " + "; ".join(verdict["critical_failures"])
    elif verdict["critical_passed"] is True:
        names = ", ".join(verdict["critical_requested_ids"])
        critical_state = f"PASS — 지정 교차로 {names} 모두 GEH < {geh_max:g}"
    elif verdict["critical_requested_ids"]:
        critical_state = (
            "관측값 없음 — 평가 불가 (designated critical intersections "
            + ", ".join(verdict["critical_requested_ids"])
            + " have no comparable turning movement in this workbook)"
        )
    else:
        critical_state = _NO_CRITICAL_DESIGNATED

    def _row(criterion: str, label: str, state: str) -> dict:
        # Same KEY SET as _flow_geh_row so every element of flow_geh_csv_rows()
        # stays uniform -- a consumer that indexes row["pct_diff"] must not
        # start raising KeyError just because a criterion row joined the list.
        # The numeric columns are None (rendered blank), never 0: a criterion
        # row has no observed flow, and 0 would read as a measurement.
        return {
            "period": "", "node": label, "counts": None,
            "observed": None, "modelled": None, "pct_diff": None,
            "flow_pct": None, "geh_pct": None,
            "geh_counts": None, "geh_excluded": None,
            "criterion": criterion,
            "criterion_state": state,
            "row_type": "criterion",
        }

    return [
        _row(
            "critical_intersection",
            "Critical intersections (5.6.3.1, doc p.213)",
            critical_state,
        ),
        # Within 3 s or 5 % (cycle) / 3 s or 10 % (green), whichever is
        # SMALLER -- the opposite direction from travel time's "greater".
        # There is no ingest path for an observed SCATS cycle/green time in
        # this codebase, so there is nothing to compare against.
        _row("signal_timing", "Signal timing (Table 5-14, doc p.216)", _NOT_ASSESSABLE),
        # Modelled within 10 % of observed. No ingest path for an observed
        # saturation flow either.
        _row("saturation_flow", "Saturation flow (5.6.3.5, doc p.216)", _NOT_ASSESSABLE),
    ]


def flow_geh_csv_rows(critical_node_ids=None, category: str | None = None) -> list[dict]:
    """Flat (period, node, ...) rows for CSV export -- built from the exact
    same :func:`_collect_flow_geh` the .docx table renders from (including the
    same ``_pct_diff``/flow%/GEH% formulas), plus a 'Total' row per period.

    Trailing ``row_type="criterion"`` rows publish the §5.6.3 criteria this
    export cannot evaluate (see :func:`criteria_coverage_rows`), the same way
    ``los_summary_csv_rows`` appends its ``weighted_overall`` rows. They carry
    no numeric columns -- only ``node`` (the citation) and ``criterion_state``.
    """
    rows: list[dict] = []
    for period, label in (("AM", "AM Peak"), ("PM", "PM Peak")):
        node_rows, tot = _collect_flow_geh(period)
        for nid, s in node_rows:
            rows.append({**_flow_geh_row(label, f"Node {nid}", s), "row_type": "node"})
        rows.append({**_flow_geh_row(label, "Total", tot), "row_type": "total"})
    rows.extend(criteria_coverage_rows(critical_node_ids, category))
    return rows


def add_flow_geh_section(
    doc,
    *,
    level: int = 1,
    heading: str = "Traffic Flow Validation Summary",
    with_heading: bool = True,
    critical_node_ids=None,
    category: str | None = None,
) -> None:
    """Append the Flow/GEH validation tables (AM & PM) to an existing Document.

    ``critical_node_ids`` designates the §5.6.3.1 critical intersections
    (doc p.213). When any of them carries a movement at GEH >= 5 the section's
    verdict is FAIL and the node is named, even if the network-wide rates
    clear the category threshold. When nothing is designated the section says
    so explicitly rather than implying the check ran.
    """
    from docx.shared import Pt
    from docx.enum.table import WD_TABLE_ALIGNMENT

    if with_heading:
        doc.add_heading(heading, level=level)
    ds = get_active_dataset().get("filename") or "active dataset"
    profile = get_active_profile(category)
    # Corrected 2026-08-06. This note used to attribute the bands to "the DMRB
    # flow criterion" and to state a flat "Target: >85% on both", which is not
    # any MRWA row: the bands are Table 5-12 (§5.6.3.1, doc p.213) and the
    # required pass rates are per category (GEH<5: 95/85/80 %, flow bands:
    # 95/90/85 %) -- 85 % is Category 3's GEH<5 rate only, and stating it as a
    # universal target silently relaxed Category 1 by ten points.
    band_rates = (
        profile.flow_low_within_pct,
        profile.flow_mid_within_pct,
        profile.flow_high_within_pct,
    )
    band_target = (
        f"{band_rates[0]:g}%"
        if len(set(band_rates)) == 1
        else "/".join(f"{r:g}%" for r in band_rates)
    )
    note = doc.add_paragraph().add_run(
        f"Source: {ds}. Observed = surveyed counts; Modelled = base-model volumes. "
        "Flow = % of movement counts meeting the MRWA OMG v2.0 Table 5-12 flow bands "
        "(<700 veh/h: +/-100 veh/h; 700-2700 veh/h: +/-15%; >2700 veh/h: +/-400 veh/h; "
        "Table 5-12, section 5.6.3.1, doc p.213). "
        "GEH = % of counts with GEH < 5, over the counts where GEH is defined. "
        f"Target for {profile.name_en}: GEH < 5 on >= {profile.geh_under_5_pct:g}% of counts, "
        f"GEH < 10 on >= {profile.geh_under_10_pct:g}%, each flow band met on "
        f">= {band_target} of its counts."
    )
    note.font.size = Pt(8)

    head = ["", "No. Counts", "Observed Flow", "Modelled Flow", "% Diff", "Flow", "GEH"]

    def build(title, rows, tot):
        doc.add_heading(title, level=2)
        if not rows:
            doc.add_paragraph("No observed counts for this period.")
            return
        t = doc.add_table(rows=1 + len(rows) + 1, cols=7)
        t.alignment = WD_TABLE_ALIGNMENT.CENTER
        for i, h in enumerate(t.rows[0].cells):
            _setcell(h, head[i], bold=True, align=("left" if i == 0 else "center"))
            _set_border(h, "bottom", sz=18, color="E69138")
        # Cells render from _flow_geh_row -- the SAME dict the CSV export emits
        # -- so a table cell and its CSV value can never be two computations.
        for rio, (nid, s) in enumerate(rows, start=1):
            r = _flow_geh_row(title, f"Node {nid}", s)
            c = t.rows[rio].cells
            _setcell(c[0], r["node"], align="left")
            _setcell(c[1], r["counts"])
            _setcell(c[2], f"{r['observed']:,}")
            _setcell(c[3], f"{r['modelled']:,}")
            _setcell(c[4], f"{r['pct_diff']:+.1f}%")
            _setcell(c[5], _pct_cell(r["flow_pct"]))
            _setcell(c[6], _pct_cell(r["geh_pct"]))
            if rio <= 2:
                for cell in c:
                    _set_border(cell, "bottom", sz=12, color="E69138")
        r = _flow_geh_row(title, "Total", tot)
        c = t.rows[-1].cells
        _setcell(c[0], "Total", bold=True, align="left")
        _setcell(c[1], r["counts"], bold=True)
        _setcell(c[2], f"{r['observed']:,}", bold=True)
        _setcell(c[3], f"{r['modelled']:,}", bold=True)
        _setcell(c[4], f"{r['pct_diff']:+.1f}%", bold=True)
        _setcell(c[5], _pct_cell(r["flow_pct"]), bold=True)
        _setcell(c[6], _pct_cell(r["geh_pct"]), bold=True)
        for cell in c:
            _set_border(cell, "top", sz=12, color="999999")
        # DISCLOSURE (unit 1.5 D3): the GEH % denominator is smaller than
        # "No. Counts" whenever a movement has 0 observed AND 0 modelled
        # vehicles. Saying so beats letting the reader assume the two columns
        # share a denominator.
        if r["geh_excluded"]:
            excl = doc.add_paragraph().add_run(
                f"GEH % is calculated over {r['geh_counts']} of {r['counts']} counts: "
                f"{r['geh_excluded']} count(s) excluded because GEH is undefined "
                "(0 observed and 0 modelled vehicles). A row showing \"-\" in the GEH "
                "column has NO count with a defined GEH, so no percentage exists for "
                "it -- that is not a 0% result."
            )
            excl.font.size = Pt(8)

    for period, label in (("AM", "AM Peak"), ("PM", "PM Peak")):
        rows, tot = _collect_flow_geh(period)
        build(label, rows, tot)
        doc.add_paragraph("")

    _add_criteria_coverage(doc, critical_node_ids=critical_node_ids, category=category)


def _add_criteria_coverage(doc, *, critical_node_ids=None, category: str | None = None) -> None:
    """The §5.6.3 criteria-coverage block and the overall verdict.

    Three criteria of the same standing as Table 5-12 were absent from this
    export entirely. Two of them have no observed-side ingest path in this
    codebase, so they publish an explicit "not assessable" state -- an omitted
    criterion reads as a criterion that passed. The third, the §5.6.3.1
    critical-intersection override, IS implementable and is applied here.
    """
    from docx.shared import Pt

    verdict = flow_geh_verdict(critical_node_ids, category)
    rows = criteria_coverage_rows(critical_node_ids, category)

    doc.add_paragraph("")
    head = doc.add_paragraph().add_run(
        "MRWA OMG v2.0 section 5.6.3 criteria coverage"
    )
    head.bold = True
    head.font.size = Pt(9)

    t = doc.add_table(rows=1 + len(rows), cols=2)
    for i, label in enumerate(("Criterion", "State")):
        _setcell(t.rows[0].cells[i], label, bold=True, fill="D9D9D9", size=8,
                 align=("left" if i == 0 else "left"))
        _set_border(t.rows[0].cells[i], "bottom", sz=18, color="E69138")
    for ri, row in enumerate(rows, start=1):
        _setcell(t.rows[ri].cells[0], row["node"], size=8, align="left")
        _setcell(t.rows[ri].cells[1], row["criterion_state"], size=8, align="left")

    verdict_run = doc.add_paragraph().add_run(
        f"Traffic volume verdict ({verdict['category_name_en']}): {verdict['verdict']}."
        + ("" if not verdict["reasons"] else " " + " ".join(verdict["reasons"]))
    )
    verdict_run.bold = verdict["verdict"] == "FAIL"
    verdict_run.font.size = Pt(8)

    note = doc.add_paragraph().add_run(verdict["network_note"])
    note.font.size = Pt(8)


def export_flow_geh_validation(critical_node_ids=None, category: str | None = None) -> bytes:
    from docx import Document
    doc = Document()
    add_flow_geh_section(doc, critical_node_ids=critical_node_ids, category=category)
    out = BytesIO(); doc.save(out); return out.getvalue()


# --- 3. Travel-time cumulative charts (.zip of PNG) -------------------------

def _mmss(sec):
    sec = max(0, int(round(sec)))
    return f"{sec // 60:d}:{sec % 60:02d}"


def _route_distances() -> dict[str, float]:
    """route_id -> distance(km) from the AM scenario traveltime DISTTRAV."""
    out = {}
    sid = _scenario_for_period("AM")
    res = get_scenario_results(sid) if sid else None
    for r in (res or {}).get("traveltime", []) or []:
        rid = str(r.get("OBJECT_ID"))
        dist = r.get("DISTTRAV_MEAN")
        if dist:
            out[rid] = dist / 1000.0
    return out


def travel_chart_specs() -> list[dict]:
    """Per (route, period) chart inputs from the active validation workbook.

    Each spec: {name, period, dist_km, obs, model, low, high, max_dist}. Prefers
    the dedicated validation columns (TomTom avg / model output / explicit
    bands), else falls back to the raw Observed / TravelTime columns."""
    load_base_workbook.cache_clear()
    wb = load_base_workbook(str(get_active_real_counts_excel()))
    tt = wb.get("travel_times") or []
    dists = _route_distances()
    max_dist = max(dists.values()) if dists else 2.0

    routes: dict[str, dict] = {}
    for r in tt:
        rid = str(r.get("route_id"))
        per = str(r.get("period") or "").upper()
        if per not in ("AM", "PM"):
            continue
        entry = routes.setdefault(rid, {"name": r.get("route_name") or f"Route {rid}"})
        obs = r.get("tomtom_avg_sec")
        if obs is None:
            obs = r.get("observed_travel_time_sec")
        model = r.get("model_output_sec")
        if model is None:
            model = r.get("model_travel_time_sec")
        entry[per] = {"obs": obs, "model": model, "low": r.get("tomtom_low_sec"), "high": r.get("tomtom_high_sec")}

    specs = []
    for rid, info in sorted(routes.items()):
        d = dists.get(rid, max_dist)
        for per in ("AM", "PM"):
            pd = info.get(per)
            if not pd or pd.get("obs") is None or pd.get("model") is None:
                continue
            obs = float(pd["obs"]); mdl = float(pd["model"])
            specs.append({
                "name": info["name"], "period": per, "dist_km": d, "obs": obs, "model": mdl,
                "high": float(pd["high"]) if pd.get("high") is not None else obs * 1.15,
                "low": float(pd["low"]) if pd.get("low") is not None else obs * 0.85,
                "max_dist": max_dist,
            })
    return specs


# Company-style overrides (R4.2). Every key is optional; ``style=None`` (or
# any key left out/None) reproduces the exact literal defaults below, which
# ARE the pre-existing hardcoded values -- so a caller that never passes
# ``style`` gets byte-for-byte the same chart as before this feature existed.
# ``palette`` is positional-by-role, not a generic series list: [0] = model
# line, [1] = observed line. The tolerance band always stays neutral grey --
# it is a reference guide, not a data series, so it is not palette-driven.
_DEFAULT_CHART_MODEL_COLOR = "#e8000b"
_DEFAULT_CHART_OBSERVED_COLOR = "#2f4b7c"
_DEFAULT_CHART_BAND_COLOR = "#9a9a9a"
_DEFAULT_CHART_TITLE_SIZE = 12
_DEFAULT_CHART_LEGEND_SIZE = 9


def render_travel_chart(spec: dict, style: dict | None = None) -> bytes:
    """Render one cumulative travel-time chart spec to PNG bytes.

    ``style`` (optional): {"palette": [model_hex, observed_hex, ...],
    "font_family": str, "font_size_title": int, "font_size_label": int}. See
    the module docstring above -- omitting a key (or the whole dict) keeps
    that element exactly as it was before this parameter existed.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.ticker import FuncFormatter, MultipleLocator

    style = style or {}
    palette = style.get("palette") or []
    model_color = palette[0] if len(palette) > 0 else _DEFAULT_CHART_MODEL_COLOR
    obs_color = palette[1] if len(palette) > 1 else _DEFAULT_CHART_OBSERVED_COLOR
    band_color = _DEFAULT_CHART_BAND_COLOR
    title_size = style.get("font_size_title") or _DEFAULT_CHART_TITLE_SIZE
    legend_size = style.get("font_size_label") or _DEFAULT_CHART_LEGEND_SIZE
    # Axis-label/tick sizing had no explicit fontsize before (matplotlib's
    # rcParams default applied) -- only pass one when the caller opts in, so
    # the no-style path calls set_xlabel/set_ylabel exactly as before.
    axis_label_kwargs = {"fontsize": style["font_size_label"]} if style.get("font_size_label") else {}
    font_family = style.get("font_family") or None
    rc = {"font.family": font_family} if font_family else {}

    name = spec["name"]; per = spec["period"]; d = spec["dist_km"]
    obs = spec["obs"]; mdl = spec["model"]; high = spec["high"]; low = spec["low"]
    max_dist = spec.get("max_dist") or max(d, 2.0)
    x = [0.0, d]
    with plt.rc_context(rc):
        fig, ax = plt.subplots(figsize=(9.6, 6.0), dpi=140)
        ax.plot(x, [0, high], color=band_color, lw=1.3, ls="--", label="+15%")
        ax.plot(x, [0, low], color=band_color, lw=1.3, ls="--", label="-15%")
        ax.plot(x, [0, obs], color=obs_color, lw=2.4, label="TomTom Average")
        ax.plot(x, [0, mdl], color=model_color, lw=2.4, marker="o", ms=6, label="Base 2022")
        mdl_dy, obs_dy = (10, -16) if mdl >= obs else (-16, 10)
        ax.annotate(_mmss(mdl), (d, mdl), textcoords="offset points", xytext=(10, mdl_dy),
                    color=model_color, fontsize=10, fontweight="bold")
        ax.annotate(_mmss(obs), (d, obs), textcoords="offset points", xytext=(10, obs_dy),
                    color=obs_color, fontsize=10)
        ax.annotate("0:00", (0, 0), textcoords="offset points", xytext=(-2, 8), color=model_color, fontsize=9)
        ax.set_xlim(0, max_dist * 1.18)
        ax.set_ylim(0, max(high, mdl) * 1.18)
        ax.yaxis.set_major_formatter(FuncFormatter(lambda s, _p=None: _mmss(s)))
        ax.yaxis.set_major_locator(MultipleLocator(30))
        ax.xaxis.set_major_locator(MultipleLocator(0.5))
        ax.grid(True, color="#ededed", lw=0.8)
        ax.set_axisbelow(True)
        for spine_side in ("top", "right"):
            ax.spines[spine_side].set_visible(False)
        ax.set_xlabel("Relative Distance from Start (km)", **axis_label_kwargs)
        ax.set_ylabel("Travel Time from Start (mm:ss)", **axis_label_kwargs)
        ax.set_title(f"{name}  -  {per} Peak", fontsize=title_size, fontweight="bold")
        if axis_label_kwargs:
            ax.tick_params(labelsize=axis_label_kwargs["fontsize"])
        ax.legend(loc="upper left", frameon=False, ncol=4, fontsize=legend_size)
        fig.tight_layout()
        png = BytesIO(); fig.savefig(png, bbox_inches="tight"); plt.close(fig)
    return png.getvalue()


def export_travel_time_charts() -> bytes:
    buf = BytesIO()
    zf = zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED)
    specs = travel_chart_specs()
    for spec in specs:
        safe = re.sub(r"[^A-Za-z0-9_]+", "_", str(spec["name"])).strip("_")
        zf.writestr(f"TravelTime_{safe}_{spec['period']}.png", render_travel_chart(spec))
    if not specs:
        zf.writestr("README.txt", "No travel-time data with both observed and model values was available.")
    zf.close()
    return buf.getvalue()
