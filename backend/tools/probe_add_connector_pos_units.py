"""Focused follow-up to probe_add_links_connectors.py: pin down whether
AddConnector's FromPos/ToPos are metres or display-unit-scaled, using two
SELF-CREATED links of exactly known length (100.0 m each, verified via their
own WKT, which probe_add_links_connectors.py already proved is always
metres regardless of display unit) -- removes the ambiguity of probing
against a pre-existing example link of unknown true length.

Usage: python tools/probe_add_connector_pos_units.py [example_dir]
Default: Toll Plaza Camden.US (FEET display) -- the network where a metres
vs feet mixup would be visible (a METERS network can't distinguish the two).
"""
from __future__ import annotations

import shutil
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

EXAMPLES_ROOT = Path(r"C:\Users\Public\Documents\PTV Vision\PTV Vissim 2026\Examples Demo")
DEFAULT_EXAMPLE = EXAMPLES_ROOT / "Toll Plaza Camden.US"
WORK_ROOT = BACKEND_ROOT.parent / "runtime" / "add-connector-pos-units-probe"


def _log(msg: str) -> None:
    print(msg, flush=True)


def main() -> None:
    example_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_EXAMPLE
    work_dir = WORK_ROOT / f"{example_dir.name}_run"
    if work_dir.exists():
        shutil.rmtree(work_dir)
    shutil.copytree(example_dir, work_dir)
    working = sorted(work_dir.glob("*.inpx"))[0]
    _log(f"Working copy: {working}")

    import pythoncom

    pythoncom.CoInitialize()
    from services.vissim_com import VissimController

    controller = VissimController()
    try:
        controller.connect(visible=True, launch_if_missing=True, attach_to_running=False)
        controller.load_network(str(working))
        net = controller.vissim.Net
        unit_len = str(net.NetPara.AttValue("UnitLenShort") or "").strip().upper()
        _log(f"UnitLenShort = {unit_len!r}")

        existing_nos = sorted(int(lk.AttValue("No")) for lk in net.Links.GetAll())
        no_a = existing_nos[-1] + 1
        no_b = no_a + 1
        no_c = no_b + 1  # connector

        # Link A: (0,0) -> (100,0). x,y always metres (proven) -> exactly 100 m long.
        link_a = net.Links.AddLink(no_a, "LINESTRING (0 0, 100 0)", [3.5, 3.5])
        # Link B: (200,0) -> (300,0) -- deliberately NOT touching A, so any
        # connector geometry snapping is visible.
        link_b = net.Links.AddLink(no_b, "LINESTRING (200 0, 300 0)", [3.5, 3.5])
        len_a = float(link_a.AttValue("Length2D"))
        len_b = float(link_b.AttValue("Length2D"))
        _log(f"Link A Length2D AttValue = {len_a!r} (x,y-derived: should read as plain 100.0 if "
             f"Length2D itself is NOT display-scaled, or 100/0.3048=328.08 if it IS)")
        _log(f"Link B Length2D AttValue = {len_b!r}")

        lane_a = link_a.Lanes.GetAll()[0]
        lane_b = link_b.Lanes.GetAll()[0]

        # Try FromPos = 100.0 (the RAW metres value, unconverted) -- if
        # FromPos needs display-unit scaling like every OTHER Pos param in
        # this codebase, passing a raw-metres 100.0 on a FEET network should
        # UNDERSHOOT (Vissim will interpret 100.0 as 100 ft = 30.48 m along
        # the 100 m link, not the far end).
        conn = net.Links.AddConnector(
            no_c, lane_a, 100.0, lane_b, 0.0, 1, "LINESTRING (100 0, 200 0)"
        )
        _log(f"Connector created No={conn.AttValue('No')}")

        save_path = work_dir / "after.inpx"
        controller.save_net_as(str(save_path))
        root = ET.parse(save_path).getroot()
        conn_el = next(l for l in root.findall(".//links/link") if l.get("no") == str(no_c))
        from_pt = conn_el.find("fromLinkEndPt")
        to_pt = conn_el.find("toLinkEndPt")
        _log(f"Saved fromLinkEndPt: link={from_pt.get('link')} pos={from_pt.get('pos')} "
             f"(passed FromPos=100.0 raw -- if this saved pos is 100.0 (or ~99.99), FromPos is "
             f"METRES-native like x,y; if it is ~30.48, FromPos is display-unit(FEET)-scaled "
             f"like every other Pos/Length param in this codebase)")
        _log(f"Saved toLinkEndPt: link={to_pt.get('link')} pos={to_pt.get('pos')}")

        # -------------------------------------------------------------
        # Follow-up: a 2-lane connector -- does NumberOfLanes=2 anchored at
        # FromLane index 1 map lanes [1,2]->[1,2] consecutively, matching
        # the from_lanes/to_lanes CONTIGUOUS-ASCENDING-RUN assumption this
        # unit's own add_connector verb will validate against?
        # -------------------------------------------------------------
        no_d = no_c + 1
        conn2 = net.Links.AddConnector(
            no_d, lane_a, 100.0, lane_b, 0.0, 2, "LINESTRING (100 0, 200 0)"
        )
        _log(f"\n2-lane connector created No={conn2.AttValue('No')} NumLanes={conn2.AttValue('NumLanes')}")
        for lane in conn2.Lanes.GetAll():
            from_lanes = list(lane.FromLanes.GetAll())
            to_lanes = list(lane.ToLanes.GetAll())
            _log(f"  connector lane Index={lane.AttValue('Index')}: "
                 f"FromLanes={[fl.AttValue('Link') for fl in from_lanes]} "
                 f"ToLanes={[tl.AttValue('Link') for tl in to_lanes]}")
        save_path2 = work_dir / "after2.inpx"
        controller.save_net_as(str(save_path2))
        root2 = ET.parse(save_path2).getroot()
        conn2_el = next(l for l in root2.findall(".//links/link") if l.get("no") == str(no_d))
        for lane_el in conn2_el.findall(".//lanes/lane"):
            fr = lane_el.find("fromLanes/objectRef")
            to = lane_el.find("toLanes/objectRef")
            _log(f"  saved XML lane: from={fr.get('key') if fr is not None else None} "
                 f"to={to.get('key') if to is not None else None}")

    finally:
        try:
            controller.disconnect()
        except Exception:
            pass
        try:
            pythoncom.CoUninitialize()
        except Exception:
            pass


if __name__ == "__main__":
    main()
