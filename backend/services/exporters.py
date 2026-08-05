"""Build downloadable Excel (.xlsx) exports for the model deliverables.

Each ``export_*`` function returns the workbook as bytes. Data is sourced from
the active dataset's inpx (demand, PT, vehicle composition), the validation
workbook (counts / travel times) and the scenario result DBs (model outputs).
"""
from __future__ import annotations

from io import BytesIO
import re
import xml.etree.ElementTree as ET

import openpyxl
from openpyxl.styles import Font

from services.datasets import get_active_inpx_path, get_active_real_counts_excel
from services.inpx_parser import parse_network
from services.results_reader import get_scenario_results, list_scenarios
from services.validation import geh, geh_pass_rate, load_base_workbook, pct_error
from services import signal_parser
from services.report_exports import (
    export_los_summary,
    export_flow_geh_validation,
    export_travel_time_charts,
)

_XLSX_MEDIA = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_DOCX_MEDIA = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_ZIP_MEDIA = "application/zip"


# --- workbook helpers -------------------------------------------------------

def _new_wb() -> openpyxl.Workbook:
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    return wb


def _sheet(wb: openpyxl.Workbook, title: str, headers: list[str]):
    ws = wb.create_sheet(title[:31])
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True)
    ws.freeze_panes = "A2"
    return ws


def _finalize(wb: openpyxl.Workbook) -> bytes:
    if not wb.worksheets:
        wb.create_sheet("Empty").append(["No data available"])
    for ws in wb.worksheets:
        for column_cells in ws.columns:
            longest = 0
            for cell in column_cells:
                if cell.value is not None:
                    longest = max(longest, len(str(cell.value)))
            ws.column_dimensions[column_cells[0].column_letter].width = min(max(longest + 2, 10), 48)
    out = BytesIO()
    wb.save(out)
    return out.getvalue()


def _f(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _round(value, ndigits=2):
    return round(value, ndigits) if isinstance(value, (int, float)) else value


def _inpx_root() -> ET.Element:
    return ET.parse(get_active_inpx_path()).getroot()


def _node_of(object_id: str) -> str | None:
    m = re.match(r"^\s*(\d+)", str(object_id))
    return m.group(1) if m else None


# --- 1. Traffic daily profile ----------------------------------------------

def export_traffic_daily_profile() -> bytes:
    root = _inpx_root()
    offsets: set[float] = set()
    rows = []
    for vi in root.findall(".//vehicleInput"):
        vols: dict[float, float] = {}
        for vol in vi.findall(".//timeIntervalVehVolume"):
            time_int = vol.get("timeInt", "")
            raw = time_int.split(" ")[1] if " " in time_int else time_int
            off = _f(raw)
            minutes = off / 1000.0 / 60.0 if off is not None else None
            if minutes is None:
                continue
            vols[minutes] = _f(vol.get("volume")) or 0.0
            offsets.add(minutes)
        rows.append((vi.get("no"), vi.get("name"), vi.get("link"), vols))

    ordered = sorted(offsets)
    wb = _new_wb()
    ws = _sheet(wb, "Traffic Daily Profile", ["Input No", "Name", "Link"] + [f"+{o:g} min" for o in ordered])
    for no, name, link, vols in rows:
        ws.append([no, name, link] + [vols.get(o) for o in ordered])
    return _finalize(wb)


# --- 2. Signal timing -------------------------------------------------------

def _collect_signals() -> list[tuple[str, dict, list[dict]]]:
    inpx = get_active_inpx_path()
    net = parse_network(str(inpx))
    node_names = {n["id"]: n["name"] for n in net["nodes"]}
    node_centers = {n["id"]: (_f(n.get("x")) or 0.0, _f(n.get("y")) or 0.0) for n in net["nodes"]}
    sc_map = signal_parser.parse_signal_controllers(inpx)
    matched = signal_parser.match_sc_to_nodes(sc_map, node_names)
    for node_id, sc in signal_parser.match_sc_to_nodes_by_signal_heads(inpx, sc_map).items():
        if matched.get(node_id) is None:
            matched[node_id] = sc

    result = []
    for node_id in sorted(node_names, key=lambda x: int(x) if x.isdigit() else 999):
        sc = matched.get(node_id)
        if not sc:
            continue
        movements = signal_parser.infer_phase_movements_for_controller(
            inpx, sc_no=sc["sc_no"], node_center=node_centers.get(node_id, (0.0, 0.0)), phases=sc["phases"],
        )
        phases = []
        for idx, phase in enumerate(sc["phases"]):
            enriched = dict(phase)
            enriched["movements"] = movements[idx] if idx < len(movements) else []
            phases.append(enriched)
        result.append((node_id, sc, phases))
    return result


def export_signal_timing() -> bytes:
    data = _collect_signals()
    wb = _new_wb()
    summary = _sheet(wb, "Signal Summary", ["Node", "Controller", "SC No", "Cycle (s)", "Offset (s)", "# Signal Groups", "# Phases"])
    for node_id, sc, _phases in data:
        summary.append([node_id, sc.get("sc_name"), sc.get("sc_no"), sc.get("cycle_time"), sc.get("offset"), sc.get("num_signal_groups"), sc.get("num_phases")])

    phase_ws = _sheet(wb, "Phase Plan", ["Node", "Controller", "Phase", "Start (s)", "Duration (s)", "Signal Groups", "Movements"])
    for node_id, sc, phases in data:
        for idx, phase in enumerate(phases, 1):
            movements = "; ".join(
                f"{m.get('from_direction')}→{m.get('to_direction')} ({m.get('turn')})"
                for m in phase.get("movements", [])
            )
            groups = ", ".join(str(g) for g in phase.get("signal_groups", []))
            phase_ws.append([node_id, sc.get("sc_name"), idx, phase.get("start"), phase.get("duration"), groups, movements])
    return _finalize(wb)


# --- 3. Travel time ---------------------------------------------------------

def export_travel_time() -> bytes:
    data = load_base_workbook(str(get_active_real_counts_excel()))
    wb = _new_wb()
    ws = _sheet(wb, "Travel Time", ["Period", "Route ID", "Route Name", "Time Label", "Observed (s)", "Model (s)", "Diff (s)", "% Error"])
    for row in data.get("travel_times", []):
        obs = row.get("observed_travel_time_sec")
        mod = row.get("model_travel_time_sec")
        diff = (mod - obs) if (obs is not None and mod is not None) else None
        ws.append([row.get("period"), row.get("route_id"), row.get("route_name"), row.get("time_label"), obs, mod, _round(diff), _round(pct_error(mod, obs))])
    return _finalize(wb)


# --- 4. Public transport ----------------------------------------------------

def export_public_transport() -> bytes:
    root = _inpx_root()
    wb = _new_wb()
    lines_ws = _sheet(wb, "PT Lines", ["Line No", "Name", "Veh Type", "Entry Link", "# Departures", "First Dep (s)", "Headway (s)", "# Stops"])
    stops_ws = _sheet(wb, "PT Stops & Dwell", ["Line No", "Line Name", "Stop No", "Dwell Time Distr", "Board %", "Alight %"])
    for line in root.findall(".//ptLine"):
        deps = sorted(int(d.get("dep", "0") or 0) for d in line.findall(".//departureTime"))
        headway = (deps[1] - deps[0]) if len(deps) >= 2 else None
        stops = line.findall(".//ptLineStop")
        lines_ws.append([line.get("no"), line.get("name"), line.get("vehType"), line.get("entryLink"), len(deps), deps[0] if deps else None, headway, len(stops)])
        for stop in stops:
            stops_ws.append([line.get("no"), line.get("name"), stop.get("ptStop"), stop.get("dwellTmDistr"), stop.get("boardPerc"), stop.get("alightPerc")])
    return _finalize(wb)


# --- 5. Vehicle composition -------------------------------------------------

def export_vehicle_composition() -> bytes:
    root = _inpx_root()
    wb = _new_wb()
    comp_ws = _sheet(wb, "Vehicle Composition", ["Composition No", "Composition Name", "Veh Type", "Rel Flow", "Share %", "Des Speed Distr"])
    for comp in root.findall(".//vehicleComposition"):
        flows = comp.findall(".//vehicleCompositionRelativeFlow")
        total = sum(_f(flow.get("relFlow")) or 0.0 for flow in flows) or 1.0
        for flow in flows:
            rel = _f(flow.get("relFlow")) or 0.0
            comp_ws.append([comp.get("no"), comp.get("name"), flow.get("vehType"), rel, round(rel / total * 100, 1), flow.get("desSpeedDistr")])

    types_ws = _sheet(wb, "Vehicle Types", ["Type No", "Name", "Category", "Capacity"])
    for vt in root.findall(".//vehicleType"):
        types_ws.append([vt.get("no"), vt.get("name"), vt.get("category"), vt.get("capacity")])

    classes_ws = _sheet(wb, "Vehicle Classes", ["Class No", "Name"])
    for vc in root.findall(".//vehicleClass"):
        classes_ws.append([vc.get("no"), vc.get("name")])
    return _finalize(wb)


# --- 6. Calibration ---------------------------------------------------------

def _r_squared(pairs: list[tuple[float, float]]) -> float | None:
    if len(pairs) < 2:
        return None
    sum_xy = sum(x * y for x, y in pairs)
    sum_x2 = sum(x * x for x, y in pairs)
    slope = sum_xy / sum_x2 if sum_x2 > 0 else None
    if slope is None:
        return None
    mean_y = sum(y for _, y in pairs) / len(pairs)
    ss_res = sum((y - slope * x) ** 2 for x, y in pairs)
    ss_tot = sum((y - mean_y) ** 2 for _, y in pairs)
    return (1 - ss_res / ss_tot) if ss_tot > 0 else None


def export_calibration() -> bytes:
    data = load_base_workbook(str(get_active_real_counts_excel()))
    counts = data.get("counts", [])
    wb = _new_wb()
    ws = _sheet(wb, "Calibration (GEH)", ["Period", "Node", "Movement", "Observed", "Model", "Abs Diff", "% Error", "GEH", "GEH<5"])
    pairs: dict[str, list[tuple[float, float]]] = {}
    geh_values: dict[str, list[float | None]] = {}
    for c in counts:
        obs = c.get("observed_volume")
        mod = c.get("model_volume")
        period = c.get("period") or ""
        g = geh(mod, obs)
        diff = abs(mod - obs) if (obs is not None and mod is not None) else None
        ok = "Y" if (g is not None and g < 5) else ("N" if g is not None else None)
        if obs is not None and mod is not None:
            pairs.setdefault(period, []).append((obs, mod))
            geh_values.setdefault(period, []).append(g)
        ws.append([period, c.get("node_id"), c.get("movement"), obs, mod, _round(diff), _round(pct_error(mod, obs)), _round(g), ok])

    # "GEH<5 (%)" is over the rows where GEH is DEFINED (unit 1.5 D1): a row
    # with 0 observed and 0 modelled used to be counted as a failure here,
    # which deflated the percentage exactly as report_exports' 0.0 inflated
    # its own. The excluded count is published beside it rather than hidden.
    summary = _sheet(wb, "R2 & GEH Summary", ["Period", "N", "R2", "GEH<5 (%)", "GEH undefined (excluded)"])
    for period in sorted(pairs):
        n = len(pairs[period])
        rate = geh_pass_rate(geh_values.get(period, []))
        pct = round(rate["rate"], 1) if rate["total"] else None
        summary.append([period, n, _round(_r_squared(pairs[period]), 4), pct, rate["excluded"]])
    return _finalize(wb)


# --- 7. Validation ----------------------------------------------------------

def export_validation() -> bytes:
    data = load_base_workbook(str(get_active_real_counts_excel()))
    wb = _new_wb()
    tol = 0.10
    tt_ws = _sheet(wb, "Travel Time Validation", ["Period", "Route", "Observed (s)", "Model (s)", "Lower -10%", "Upper +10%", "In Band"])
    for row in data.get("travel_times", []):
        obs = row.get("observed_travel_time_sec")
        mod = row.get("model_travel_time_sec")
        lo = obs * (1 - tol) if obs is not None else None
        hi = obs * (1 + tol) if obs is not None else None
        in_band = "Y" if (obs is not None and mod is not None and lo <= mod <= hi) else ("N" if (obs is not None and mod is not None) else None)
        tt_ws.append([row.get("period"), row.get("route_name"), obs, mod, _round(lo), _round(hi), in_band])

    stab_ws = _sheet(wb, "Stability", ["Scenario", "Metric", "Mean", "StDev", "CoV (%)"])
    for sc in list_scenarios():
        res = get_scenario_results(sc["id"])
        if not res:
            continue
        net = res.get("network", {})
        for key, label in (("DELAYAVG", "Avg Delay (s)"), ("SPEEDAVG", "Avg Speed (km/h)"), ("VEHARR", "Vehicles Arrived")):
            mean = net.get(key)
            sd = net.get(f"{key}_STDEV")
            cov = (sd / mean * 100) if (isinstance(mean, (int, float)) and mean and isinstance(sd, (int, float))) else None
            stab_ws.append([sc["id"], label, _round(mean), _round(sd), _round(cov)])
    return _finalize(wb)


# --- 8. Model outputs -------------------------------------------------------

def _los(delay: float | None) -> str | None:
    if delay is None:
        return None
    thresholds = [(10, "A"), (20, "B"), (35, "C"), (55, "D"), (80, "E")]
    for limit, grade in thresholds:
        if delay <= limit:
            return grade
    return "F"


def export_model_outputs() -> bytes:
    wb = _new_wb()
    node_ws = _sheet(wb, "Intersection Performance", ["Scenario", "Node", "Avg Delay (s)", "LOS", "Max Queue (m)", "Stops/veh", "Vehicles"])
    net_ws = _sheet(wb, "Network Performance", ["Scenario", "VKT (km)", "VHT (h)", "Avg Speed (km/h)", "Total Delay (h)", "Avg Delay (s)", "Vehs Active", "Vehs Arrived", "Latent Demand"])

    for sc in list_scenarios():
        res = get_scenario_results(sc["id"])
        if not res:
            continue
        net = res.get("network", {})
        veh_act = net.get("VEHACT")
        veh_arr = net.get("VEHARR")
        latent = (veh_arr - veh_act) if (isinstance(veh_arr, (int, float)) and isinstance(veh_act, (int, float))) else None
        net_ws.append([
            sc["id"],
            _round((net.get("DISTTOT") or 0) / 1000.0, 1),
            _round((net.get("TRAVTMTOT") or 0) / 3600.0, 1),
            _round(net.get("SPEEDAVG"), 1),
            _round((net.get("DELAYTOT") or 0) / 3600.0, 2),
            _round(net.get("DELAYAVG"), 1),
            veh_act,
            veh_arr,
            latent,
        ])

        # Aggregate movement delay / queue per node.
        delay_num: dict[str, float] = {}
        delay_den: dict[str, float] = {}
        stops_num: dict[str, float] = {}
        vehs: dict[str, float] = {}
        for mv in res.get("movements", []):
            node = _node_of(mv.get("OBJECT_ID"))
            if node is None:
                continue
            v = _f(mv.get("VEHS_MEAN")) or 0.0
            d = _f(mv.get("VEHDELAY_MEAN"))
            s = _f(mv.get("STOPS_MEAN"))
            vehs[node] = vehs.get(node, 0.0) + v
            if d is not None:
                delay_num[node] = delay_num.get(node, 0.0) + d * v
                delay_den[node] = delay_den.get(node, 0.0) + v
            if s is not None:
                stops_num[node] = stops_num.get(node, 0.0) + s * v
        queue_max: dict[str, float] = {}
        for q in res.get("queues", []):
            node = _node_of(q.get("OBJECT_ID"))
            if node is None:
                continue
            qmax = _f(q.get("QLENMAX_MEAN"))
            if qmax is not None:
                queue_max[node] = max(queue_max.get(node, 0.0), qmax)

        for node in sorted(vehs, key=lambda x: int(x) if x.isdigit() else 999):
            den = delay_den.get(node, 0.0)
            avg_delay = (delay_num.get(node, 0.0) / den) if den else None
            stops = (stops_num.get(node, 0.0) / vehs[node]) if vehs.get(node) else None
            node_ws.append([sc["id"], node, _round(avg_delay, 1), _los(avg_delay), _round(queue_max.get(node), 1), _round(stops, 2), _round(vehs.get(node), 0)])
    return _finalize(wb)


# --- registry ---------------------------------------------------------------

EXPORTS: dict[str, dict] = {
    "daily-profile": {"label": "Traffic daily profile", "filename": "traffic_daily_profile.xlsx", "builder": export_traffic_daily_profile},
    "signal-timing": {"label": "Signal timing data", "filename": "signal_timing.xlsx", "builder": export_signal_timing},
    "travel-time": {"label": "Travel time data", "filename": "travel_time.xlsx", "builder": export_travel_time},
    "public-transport": {"label": "Public transport services", "filename": "public_transport.xlsx", "builder": export_public_transport},
    "vehicle-composition": {"label": "Traffic flow / vehicle composition", "filename": "vehicle_composition.xlsx", "builder": export_vehicle_composition},
    "calibration": {"label": "Calibration (GEH, R²)", "filename": "calibration.xlsx", "builder": export_calibration},
    "validation": {"label": "Validation (travel time, stability)", "filename": "validation.xlsx", "builder": export_validation},
    "model-outputs": {"label": "Model outputs", "filename": "model_outputs.xlsx", "builder": export_model_outputs},
    "los-summary": {"label": "LoS summary (report)", "filename": "los_summary.docx", "builder": export_los_summary, "media_type": _DOCX_MEDIA},
    "flow-geh-validation": {"label": "Flow / GEH validation (report)", "filename": "flow_geh_validation.docx", "builder": export_flow_geh_validation, "media_type": _DOCX_MEDIA},
    "travel-time-charts": {"label": "Travel-time charts (report)", "filename": "travel_time_charts.zip", "builder": export_travel_time_charts, "media_type": _ZIP_MEDIA},
}


def export_media_type(key: str) -> str:
    """Media type for an export key (defaults to .xlsx for the legacy exports)."""
    meta = EXPORTS.get(key) or {}
    return meta.get("media_type", _XLSX_MEDIA)


# --- combined export (selected items -> one workbook, one sheet per item) ----

def _safe_sheet_title(title: str, wb: openpyxl.Workbook) -> str:
    base = re.sub(r"[\\/?*\[\]:]", " ", str(title)).strip()[:31] or "Sheet"
    name = base
    counter = 2
    existing = set(wb.sheetnames)
    while name in existing:
        suffix = f" ({counter})"
        name = base[: 31 - len(suffix)] + suffix
        counter += 1
    return name


def _copy_sheet_into(src_ws, dst_wb: openpyxl.Workbook, title: str) -> None:
    ws = dst_wb.create_sheet(_safe_sheet_title(title, dst_wb))
    for row in src_ws.iter_rows(values_only=True):
        ws.append(list(row))
    if ws.max_row >= 1:
        for cell in ws[1]:
            cell.font = Font(bold=True)
        ws.freeze_panes = "A2"


def build_combined_export(keys: list[str]) -> bytes:
    """Build one workbook with each selected export as its own sheet(s).

    Reuses the individual ``export_*`` builders (each returns a full .xlsx) and
    copies their sheets into a single combined workbook: single-sheet exports are
    named after their item label, multi-sheet exports keep their sheet titles.
    """
    wb = _new_wb()
    skipped: list[str] = []
    for key in keys:
        meta = EXPORTS.get(key)
        if meta is None:
            continue
        # The combined file is a single .xlsx; report exports (.docx/.zip) can't be
        # embedded as sheets, so skip them here (download those individually).
        if meta.get("media_type", _XLSX_MEDIA) != _XLSX_MEDIA:
            skipped.append(meta.get("label") or key)
            continue
        try:
            src_wb = openpyxl.load_workbook(BytesIO(meta["builder"]()))
        except Exception as exc:  # noqa: BLE001 - one bad item must not kill the file
            ws = wb.create_sheet(_safe_sheet_title(f"{meta.get('label', key)} (error)", wb))
            ws.append([f"Failed to build '{key}': {exc}"])
            continue
        multi = len(src_wb.worksheets) > 1
        for src_ws in src_wb.worksheets:
            title = src_ws.title if multi else (meta.get("label") or src_ws.title)
            _copy_sheet_into(src_ws, wb, title)
    if skipped:
        ws = wb.create_sheet(_safe_sheet_title("Notes", wb))
        ws.append(["Report exports below are .docx/.zip and were not embedded here."])
        ws.append(["Download them individually from the list:"])
        for label in skipped:
            ws.append([label])
    return _finalize(wb)
