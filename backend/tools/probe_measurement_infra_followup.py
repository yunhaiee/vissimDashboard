"""Follow-up to probe_measurement_infra.py: [1b.6] found AddQueueCounter/
AddDataCollectionPoint/AddVehicleTravelTimeMeasurement all raise "The Python
instance can not be converted to a COM object" when Link/Lane params are
passed as plain int/str -- EXCEPT AddDataCollectionPoint, which WORKED when
Lane was passed as an actual Lane COM object instead of a "link lane"
string. This narrow follow-up confirms the same fix (pass a real Link COM
object, via Net.Links.ItemByKey) closes AddQueueCounter and
AddVehicleTravelTimeMeasurement too, and exercises their Remove methods
(untested in the main probe because their Add calls never succeeded there).
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

WORK_DIR = BACKEND_ROOT.parent / "runtime" / "measurement-infra-probe"


def _log(msg: str) -> None:
    print(msg, flush=True)


def main() -> None:
    # Reuse the clone the main probe already made (still has its original
    # 6/31/7/9 baseline -- the main probe's own adds were removed at [1b.8]
    # except QueueCounter/VehicleTravelTimeMeasurement, which never got
    # created there at all).
    candidates = sorted(WORK_DIR.glob("Toll Plaza Camden.US_*/Toll plaza.inpx"))
    if not candidates:
        raise SystemExit("Run probe_measurement_infra.py first to create the clone.")
    working = candidates[-1]
    _log(f"Working copy: {working}")

    import pythoncom

    pythoncom.CoInitialize()
    from services.vissim_com import VissimController

    controller = VissimController()
    try:
        controller.connect(visible=True, launch_if_missing=True, attach_to_running=False)
        controller.load_network(str(working))
        net = controller.vissim.Net

        links = list(net.Links.GetAll())
        target_link_obj = next((lk for lk in links if int(lk.AttValue("NumLanes") or 0) >= 1), links[0])
        target_link_no = int(target_link_obj.AttValue("No"))
        _log(f"Target link: No={target_link_no} (Link COM object, via Net.Links.GetAll())")

        qc_before = len(list(net.QueueCounters.GetAll()))
        try:
            new_qc = net.QueueCounters.AddQueueCounter(None, target_link_obj, 20.0)
            _log(f"[WORKS] AddQueueCounter(None, <Link obj>, 20.0) -> No={new_qc.AttValue('No')}, "
                 f"Pos readback={new_qc.AttValue('Pos')!r}, count {qc_before} -> "
                 f"{len(list(net.QueueCounters.GetAll()))}")
            net.QueueCounters.RemoveQueueCounter(new_qc)
            _log(f"[WORKS] RemoveQueueCounter -> count back to {len(list(net.QueueCounters.GetAll()))}")
        except Exception as exc:
            _log(f"AddQueueCounter(None, <Link obj>, 20.0) -> {type(exc).__name__}: {exc}")

        # Also confirm ItemByKey(int) resolves a Link -- the form
        # measurement_infra.py would actually use given only a link_no
        # string/int param from the tool's own Params model.
        try:
            link_via_itembykey = net.Links.ItemByKey(target_link_no)
            _log(f"[WORKS] Net.Links.ItemByKey({target_link_no}) -> Link object, "
                 f"No={link_via_itembykey.AttValue('No')!r}")
        except Exception as exc:
            _log(f"Net.Links.ItemByKey({target_link_no}) -> {type(exc).__name__}: {exc}")

        tt_before = len(list(net.VehicleTravelTimeMeasurements.GetAll()))
        try:
            new_tt = net.VehicleTravelTimeMeasurements.AddVehicleTravelTimeMeasurement(
                None, target_link_obj, 0.0, target_link_obj, 20.0
            )
            _log(f"[WORKS] AddVehicleTravelTimeMeasurement(None, <Link obj>, 0.0, <Link obj>, 20.0) "
                 f"-> No={new_tt.AttValue('No')}, StartPos={new_tt.AttValue('StartPos')!r}, "
                 f"EndPos={new_tt.AttValue('EndPos')!r}, count {tt_before} -> "
                 f"{len(list(net.VehicleTravelTimeMeasurements.GetAll()))}")
            net.VehicleTravelTimeMeasurements.RemoveVehicleTravelTimeMeasurement(new_tt)
            _log("[WORKS] RemoveVehicleTravelTimeMeasurement -> count back to "
                 f"{len(list(net.VehicleTravelTimeMeasurements.GetAll()))}")
        except Exception as exc:
            _log(f"AddVehicleTravelTimeMeasurement(...) -> {type(exc).__name__}: {exc}")

        # Explicit integer Key (not None) -- confirm auto-numbering vs
        # explicit-key both work, for both surviving object types.
        try:
            new_qc2 = net.QueueCounters.AddQueueCounter(9999002, target_link_obj, 5.0)
            _log(f"[WORKS] AddQueueCounter(9999002, <Link obj>, 5.0) -> No={new_qc2.AttValue('No')}")
            net.QueueCounters.RemoveQueueCounter(new_qc2)
        except Exception as exc:
            _log(f"AddQueueCounter(9999002, <Link obj>, 5.0) -> {type(exc).__name__}: {exc}")

        # DataCollectionMeasurement explicit key + a SECOND point wired via
        # SetAttValue with a COMMA LIST (the earlier probe only tried a
        # single-point string) -- confirms the multi-point grouping shape.
        try:
            lane_obj = target_link_obj.Lanes.GetAll()[0]
            p1 = net.DataCollectionPoints.AddDataCollectionPoint(None, lane_obj, 5.0)
            p2 = net.DataCollectionPoints.AddDataCollectionPoint(None, lane_obj, 10.0)
            dcm = net.DataCollectionMeasurements.AddDataCollectionMeasurement(None)
            dcm.SetAttValue("DataCollectionPoints", f"{p1.AttValue('No')},{p2.AttValue('No')}")
            member_nos = sorted(str(p.AttValue("No")) for p in dcm.DataCollectionPoints.GetAll())
            _log(f"[WORKS] two-point measurement grouping via SetAttValue('DataCollectionPoints', "
                 f"'{p1.AttValue('No')},{p2.AttValue('No')}') -> members={member_nos}")
            # Now try removing p1 while the measurement still references it
            # -- should raise/refuse (mirrors what add_data_collection_point's
            # remove path will need to detect itself via the XML reference
            # registry, but worth seeing whether COM ALSO refuses this
            # natively as a second line of defense).
            try:
                net.DataCollectionPoints.RemoveDataCollectionPoint(p1)
                _log("        [UNEXPECTED] COM allowed removing a still-referenced point!")
            except Exception as exc:
                _log(f"        [expected-ish] RemoveDataCollectionPoint(<referenced point>) -> "
                     f"{type(exc).__name__}: {exc}")
            # Clean up in dependency order: measurement first, then points.
            net.DataCollectionMeasurements.RemoveDataCollectionMeasurement(dcm)
            net.DataCollectionPoints.RemoveDataCollectionPoint(p1)
            net.DataCollectionPoints.RemoveDataCollectionPoint(p2)
            _log(f"        cleanup done, final counts: DCP={len(list(net.DataCollectionPoints.GetAll()))} "
                 f"(expect 31), DCM={len(list(net.DataCollectionMeasurements.GetAll()))} (expect 7)")
        except Exception as exc:
            _log(f"two-point measurement grouping test -> {type(exc).__name__}: {exc}")

    finally:
        try:
            controller.disconnect()
        except Exception:
            pass
        try:
            pythoncom.CoUninitialize()
        except Exception:
            pass
        _log("Disconnected.")


if __name__ == "__main__":
    # Unit 1.4: a CLI run has no HTTP request to scope it to a project, and
    # services/workspaces.py refuses to guess one -- so say "the default
    # project" explicitly before anything resolves a dataset path.
    from services.workspaces import bind_workspace, default_workspace_id

    with bind_workspace(default_workspace_id()):
        main()
