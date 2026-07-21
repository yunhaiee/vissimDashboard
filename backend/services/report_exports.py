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

import math
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
from services.validation import load_base_workbook, load_real_counts

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

def add_los_section(doc, *, level: int = 1, heading: str = "VISSIM LoS Summary", with_heading: bool = True) -> None:
    """Append the colour-coded LoS table (AM/PM) to an existing Document."""
    from docx.shared import Pt
    from docx.enum.table import WD_TABLE_ALIGNMENT

    am = _movement_rows(_scenario_for_period("AM"))
    pm = _movement_rows(_scenario_for_period("PM"))

    if with_heading:
        doc.add_heading(heading, level=level)
    ds = get_active_dataset().get("filename") or "active dataset"
    note = doc.add_paragraph().add_run(
        f"Source: {ds}. AM/PM = base scenarios. LOS = HCM signalized thresholds on VISSIM control delay "
        "(A<=10, B<=20, C<=35, D<=55, E<=80, F>80 s/veh). Max Queue = QLENMAX (per lane group, network units)."
    )
    note.font.size = Pt(8)

    if not am and not pm:
        doc.add_paragraph("No simulation results are available yet. Run a scenario, then re-export.")
        return

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

    total_rows = 2 + sum(len(v) + 1 for v in nodes.values())
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
    for nid in sorted(nodes, key=int):
        rowkeys = nodes[nid]
        start = ri
        for k in rowkeys:
            a = ai.get(k, {}); p = pi.get(k, {})
            r = t.rows[ri].cells
            _setcell(r[1], k[1]); _setcell(r[2], k[2])
            _setcell(r[3], a.get("volume")); _setcell(r[4], a.get("delay"))
            _setcell(r[5], a.get("los"), bold=True, fill=_LOS_FILL.get(a.get("los")))
            _setcell(r[6], a.get("qmax"))
            _setcell(r[7], p.get("volume")); _setcell(r[8], p.get("delay"))
            _setcell(r[9], p.get("los"), bold=True, fill=_LOS_FILL.get(p.get("los")))
            _setcell(r[10], p.get("qmax"))
            ri += 1
        r = t.rows[ri].cells
        _setcell(r[1].merge(r[2]), "Weighted Overall", bold=True)
        amd = weighted(rowkeys, ai); pmd = weighted(rowkeys, pi)
        _setcell(r[3], ""); _setcell(r[4], amd, bold=True)
        _setcell(r[5], _los(amd), bold=True, fill=_LOS_FILL.get(_los(amd)))
        _setcell(r[6], ""); _setcell(r[7], ""); _setcell(r[8], pmd, bold=True)
        _setcell(r[9], _los(pmd), bold=True, fill=_LOS_FILL.get(_los(pmd)))
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

def _geh(m, o):
    s = m + o
    return math.sqrt(2 * (m - o) ** 2 / s) if s > 0 else 0.0


def _flow_pass(m, o):
    diff = abs(m - o)
    if o < 700:
        return diff <= 100
    if o <= 2700:
        return (diff / o) <= 0.15 if o > 0 else diff == 0
    return diff <= 400


def _node_stats(node):
    dirs = [d for d in (node.get("directions") or []) if d.get("real") is not None and d.get("sim_basam") is not None]
    obs = node.get("total_real") or sum(d["real"] for d in dirs)
    mdl = node.get("total_sim_basam") or sum(d["sim_basam"] for d in dirs)
    return {
        "counts": len(dirs),
        "obs": obs,
        "mdl": mdl,
        "flow_ok": sum(1 for d in dirs if _flow_pass(d["sim_basam"], d["real"])),
        "geh_ok": sum(1 for d in dirs if _geh(d["sim_basam"], d["real"]) < 5),
    }


def _collect_flow_geh(period):
    path = str(get_active_real_counts_excel())
    load_real_counts.cache_clear()
    rc = load_real_counts(path, period)
    rows, tot = [], {"counts": 0, "obs": 0, "mdl": 0, "flow_ok": 0, "geh_ok": 0}
    for nid in sorted(rc, key=lambda x: int(x) if x.isdigit() else 999):
        s = _node_stats(rc[nid])
        rows.append((nid, s))
        for k in tot:
            tot[k] += s[k]
    return rows, tot


def add_flow_geh_section(doc, *, level: int = 1, heading: str = "Traffic Flow Validation Summary", with_heading: bool = True) -> None:
    """Append the Flow/GEH validation tables (AM & PM) to an existing Document."""
    from docx.shared import Pt
    from docx.enum.table import WD_TABLE_ALIGNMENT

    if with_heading:
        doc.add_heading(heading, level=level)
    ds = get_active_dataset().get("filename") or "active dataset"
    note = doc.add_paragraph().add_run(
        f"Source: {ds}. Observed = surveyed counts; Modelled = base-model volumes. "
        "Flow = % of movement counts meeting the DMRB flow criterion (<700: +/-100 veh; 700-2700: +/-15%; >2700: +/-400 veh). "
        "GEH = % of counts with GEH < 5. Target: >85% on both."
    )
    note.font.size = Pt(8)

    head = ["", "No. Counts", "Observed Flow", "Modelled Flow", "% Diff", "Flow", "GEH"]

    def pct_diff(m, o):
        return (m - o) / o * 100 if o else 0.0

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
        for rio, (nid, s) in enumerate(rows, start=1):
            c = t.rows[rio].cells
            _setcell(c[0], f"Node {nid}", align="left")
            _setcell(c[1], s["counts"])
            _setcell(c[2], f"{round(s['obs']):,}")
            _setcell(c[3], f"{round(s['mdl']):,}")
            _setcell(c[4], f"{pct_diff(s['mdl'], s['obs']):+.1f}%")
            _setcell(c[5], f"{(s['flow_ok'] / s['counts'] * 100 if s['counts'] else 0):.0f}%")
            _setcell(c[6], f"{(s['geh_ok'] / s['counts'] * 100 if s['counts'] else 0):.0f}%")
            if rio <= 2:
                for cell in c:
                    _set_border(cell, "bottom", sz=12, color="E69138")
        c = t.rows[-1].cells
        _setcell(c[0], "Total", bold=True, align="left")
        _setcell(c[1], tot["counts"], bold=True)
        _setcell(c[2], f"{round(tot['obs']):,}", bold=True)
        _setcell(c[3], f"{round(tot['mdl']):,}", bold=True)
        _setcell(c[4], f"{pct_diff(tot['mdl'], tot['obs']):+.1f}%", bold=True)
        _setcell(c[5], f"{(tot['flow_ok'] / tot['counts'] * 100 if tot['counts'] else 0):.0f}%", bold=True)
        _setcell(c[6], f"{(tot['geh_ok'] / tot['counts'] * 100 if tot['counts'] else 0):.0f}%", bold=True)
        for cell in c:
            _set_border(cell, "top", sz=12, color="999999")

    for period, label in (("AM", "AM Peak"), ("PM", "PM Peak")):
        rows, tot = _collect_flow_geh(period)
        build(label, rows, tot)
        doc.add_paragraph("")


def export_flow_geh_validation() -> bytes:
    from docx import Document
    doc = Document()
    add_flow_geh_section(doc)
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


def render_travel_chart(spec: dict) -> bytes:
    """Render one cumulative travel-time chart spec to PNG bytes."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.ticker import FuncFormatter, MultipleLocator

    name = spec["name"]; per = spec["period"]; d = spec["dist_km"]
    obs = spec["obs"]; mdl = spec["model"]; high = spec["high"]; low = spec["low"]
    max_dist = spec.get("max_dist") or max(d, 2.0)
    x = [0.0, d]
    fig, ax = plt.subplots(figsize=(9.6, 6.0), dpi=140)
    ax.plot(x, [0, high], color="#9a9a9a", lw=1.3, ls="--", label="+15%")
    ax.plot(x, [0, low], color="#9a9a9a", lw=1.3, ls="--", label="-15%")
    ax.plot(x, [0, obs], color="#2f4b7c", lw=2.4, label="TomTom Average")
    ax.plot(x, [0, mdl], color="#e8000b", lw=2.4, marker="o", ms=6, label="Base 2022")
    mdl_dy, obs_dy = (10, -16) if mdl >= obs else (-16, 10)
    ax.annotate(_mmss(mdl), (d, mdl), textcoords="offset points", xytext=(10, mdl_dy),
                color="#e8000b", fontsize=10, fontweight="bold")
    ax.annotate(_mmss(obs), (d, obs), textcoords="offset points", xytext=(10, obs_dy),
                color="#2f4b7c", fontsize=10)
    ax.annotate("0:00", (0, 0), textcoords="offset points", xytext=(-2, 8), color="#e8000b", fontsize=9)
    ax.set_xlim(0, max_dist * 1.18)
    ax.set_ylim(0, max(high, mdl) * 1.18)
    ax.yaxis.set_major_formatter(FuncFormatter(lambda s, _p=None: _mmss(s)))
    ax.yaxis.set_major_locator(MultipleLocator(30))
    ax.xaxis.set_major_locator(MultipleLocator(0.5))
    ax.grid(True, color="#ededed", lw=0.8)
    ax.set_axisbelow(True)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    ax.set_xlabel("Relative Distance from Start (km)")
    ax.set_ylabel("Travel Time from Start (mm:ss)")
    ax.set_title(f"{name}  -  {per} Peak", fontsize=12, fontweight="bold")
    ax.legend(loc="upper left", frameon=False, ncol=4, fontsize=9)
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
