"""Live probe: find the real COM attribute ids that arm Vissim's vehicle-record
(.fzp) output, then verify the actual on-disk .fzp format against what
``services/trajectory_reader.py``'s ``parse_fzp`` currently assumes.

Ground truth from a real export (grepped directly from the Beijing.CN example's
own .inpx BEFORE this probe ever touches COM -- same "XML is a hint, AttValue id
is verified separately" discipline as ``evaluation_probe.py``):

    <vehRec filterType="ALL" fromTime="0" inclParkVehs="false" resolution="1"
            toTime="99999" writeDatabase="false" writeFile="false">
        <selectedAttributes>
            <attributeSelection attributeID="SIMSEC" .../>
            <attributeSelection attributeID="NO" .../>
            <attributeSelection attributeID="LANE\\LINK\\NO" .../>
            <attributeSelection attributeID="LANE\\INDEX" .../>
            <attributeSelection attributeID="POS" .../>
        </selectedAttributes>
    </vehRec>

Two things this immediately puts in question about the EXISTING (synthetic,
never-validated-live) trajectory_reader.py:
  1. There is no top-level "collect_data" boolean the way vehNetPerf/nodeResults/
     vehTravTms/queues all have -- this object's own enable flag looks like it
     is ``writeFile`` (or ``writeDatabase``), not a "CollectData"-suffixed id.
  2. The DEFAULT attribute selection has no LINK/LANE/SPEED columns at all --
     it has ``LANE\\LINK\\NO`` and ``LANE\\INDEX`` instead of a bare LINK/LANE
     column, and no SPEED column whatsoever. If the real .fzp header actually
     writes those backslash-qualified names verbatim, parse_fzp's
     REQUIRED_COLUMNS = (SIMSEC, NO, LINK, POS, SPEED) would treat EVERY row as
     unparseable (LINK and SPEED both absent from col_index).

Strategy (mirrors evaluation_probe.py's brute-force-candidates-verified-against-
known-xml-defaults pattern, plus check_network_probe.py's dir()-scan technique
for discovering whether the attribute SELECTION itself is COM-editable):

  [1] Brute-force candidate COM attribute ids for each of vehRec's 7 XML fields
      against Vissim.Evaluation.AttValue(...), accepting a candidate only when
      its pre-edit read-back matches the known XML default above.
  [2] dir(Evaluation) full scan + targeted candidate property/collection names,
      looking for a vehicle-record ATTRIBUTE SELECTION object (so we know
      whether SPEED can be added via COM before a run, or whether the shipped
      default selection is all we ever get).
  [3] Once ids are verified: set writeFile=True, a short time window, and
      (if an attribute-selection collection was found) attempt to add LINK/
      LANE/SPEED-equivalent columns. Run a short RunContinuous.
  [4] Locate the resulting .fzp on disk, log its exact header line, first few
      data rows verbatim, row/frame count, and file size -- then extrapolate
      size-per-60s for the AGENT_RUN_TRAJECTORY_MAX_S guard.
  [5] Print a verbatim diff against trajectory_reader.py's current
      REQUIRED_COLUMNS/OPTIONAL_COLUMNS assumptions.

Always disconnects its dedicated Vissim instance in a ``finally`` block.
Usage: python tools/vehicle_record_probe.py [example_dir] [duration_s]
Default example: Urban Intersection Beijing.CN (2026 Examples Demo), 60s.
"""
from __future__ import annotations

import os
import shutil
import sys
import time
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
WORK_ROOT = BACKEND_ROOT.parent / "runtime" / "vehicle-record-probe"

# Ground-truth defaults, grepped directly from Intersection Beijing.inpx's
# <vehRec .../> element (see module docstring) -- the pre-edit read-back a
# candidate id must match to be ACCEPTED, not merely "didn't raise".
_XML_DEFAULTS: dict[str, Any] = {
    "filter_type": "ALL",
    "from_time": 0,
    "incl_park_vehs": False,
    "resolution": 1,
    "to_time": 99999,
    "write_database": False,
    "write_file": False,
}

_FIELD_CANDIDATE_SUFFIXES: dict[str, list[str]] = {
    "filter_type": ["FilterType", "Filter"],
    "from_time": ["FromTime", "StartTime", "From"],
    "incl_park_vehs": ["InclParkVehs", "InclParkedVehs", "IncludeParkedVehicles", "ParkVehs"],
    "resolution": ["Resolution", "Res", "Interval", "TimeStep"],
    "to_time": ["ToTime", "EndTime", "To"],
    "write_database": ["WriteDatabase", "WriteDb", "WriteToDatabase"],
    "write_file": ["WriteFile", "WriteToFile", "Active", "Enabled", "CollectData"],
}

# Most plausible prefix first (exact case-adjusted xml tag, matching vehNetPerf/
# vehTravTms/queues' precedent) but nodeResults broke that pattern (real prefix
# "NodeRes", not "NodeResults") -- so every plausible shortening/synonym is
# tried too, never assumed.
_PREFIX_CANDIDATES = [
    "VehRec", "VehRecord", "VehicleRecord", "VehRecs", "Rec", "VehicleRecording",
]

# Sub-object / collection candidates for the <selectedAttributes> child --
# is the attribute SELECTION itself reachable/editable over COM at all?
_ATTR_SELECTION_SUBOBJECT_CANDIDATES = [
    "AttributeSelectionVehicleRecord", "AttributeSelection_VehicleRecord",
    "VehRecAttributeSelection", "AttributeSelectionVehRec", "VehRecAttributes",
    "AttributeSelection", "VehicleRecordAttributes",
]


def _log(msg: str) -> None:
    print(msg, flush=True)


def _fresh_work_dir(example_dir: Path) -> Path:
    for stale in WORK_ROOT.glob(f"{example_dir.name}_*"):
        try:
            shutil.rmtree(stale)
        except OSError:
            pass
    work_dir = WORK_ROOT / f"{example_dir.name}_{os.getpid()}"
    shutil.copytree(example_dir, work_dir)
    return work_dir


def _probe_field(evaluation: Any, field: str, candidates: list[str], expected: Any) -> tuple[str | None, dict[str, Any]]:
    outcomes: dict[str, Any] = {}
    best: str | None = None
    for candidate in candidates:
        try:
            value = evaluation.AttValue(candidate)
        except Exception as exc:
            outcomes[candidate] = f"ERROR: {type(exc).__name__}: {exc}"
            continue
        outcomes[candidate] = value
        normalized = value
        if isinstance(expected, bool):
            normalized = bool(int(value)) if isinstance(value, (int, float)) else bool(value)
        elif isinstance(expected, str):
            normalized = str(value)
        else:
            try:
                normalized = type(expected)(value)
            except Exception:
                pass
        if best is None and normalized == expected:
            best = candidate
    return best, outcomes


def probe_ids(evaluation: Any) -> dict[str, str]:
    _log("\n" + "=" * 78 + "\n[1] Brute-forcing vehRec COM attribute ids against known XML defaults\n" + "=" * 78)
    found: dict[str, str] = {}
    for field, expected in _XML_DEFAULTS.items():
        candidates = [p + s for p in _PREFIX_CANDIDATES for s in _FIELD_CANDIDATE_SUFFIXES[field]]
        best, outcomes = _probe_field(evaluation, field, candidates, expected)
        _log(f"\n  -- field: {field} (expected {expected!r}) --")
        for candidate, outcome in outcomes.items():
            marker = "MATCH" if candidate == best else ("ok" if not str(outcome).startswith("ERROR") else "no")
            _log(f"    [{marker:5s}] AttValue({candidate!r}) = {outcome!r}")
        if best:
            found[field] = best
            _log(f"  => VERIFIED {field}: {best!r}")
        else:
            _log(f"  => NO candidate matched for {field!r}")
    return found


def probe_attribute_selection_surface(evaluation: Any) -> None:
    _log("\n" + "=" * 78 + "\n[2] Looking for a COM-editable vehicle-record attribute SELECTION\n" + "=" * 78)
    for name in _ATTR_SELECTION_SUBOBJECT_CANDIDATES:
        try:
            obj = getattr(evaluation, name)
        except Exception as exc:
            _log(f"  [no ] Evaluation.{name} -> ERROR: {type(exc).__name__}: {exc}")
            continue
        if obj is None:
            _log(f"  [no ] Evaluation.{name} -> None")
            continue
        _log(f"  [ok ] Evaluation.{name} -> {obj!r}")
        try:
            names = sorted(n for n in dir(obj) if not n.startswith("_"))
            _log(f"        dir() -> {names}")
        except Exception as exc:
            _log(f"        dir() raised: {exc}")

    _log("\n  -- full dir(Evaluation) for manual inspection --")
    try:
        names = sorted(n for n in dir(evaluation) if not n.startswith("_"))
        for n in names:
            _log(f"    {n}")
    except Exception as exc:
        _log(f"  dir(Evaluation) raised: {exc}")


def main() -> None:
    example_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_EXAMPLE
    duration_s = int(sys.argv[2]) if len(sys.argv) > 2 else 60
    if not example_dir.is_dir():
        raise SystemExit(f"Example folder not found: {example_dir}")

    work_dir = _fresh_work_dir(example_dir)
    inpx_files = sorted(work_dir.glob("*.inpx"))
    if not inpx_files:
        raise SystemExit(f"No .inpx in {work_dir}")
    working = inpx_files[0]
    _log(f"[0/6] Working copy: {working}")

    pre_existing = set(work_dir.rglob("*.fzp")) | set(work_dir.rglob("*.db"))

    import pythoncom

    pythoncom.CoInitialize()
    from services.vissim_com import VissimController

    controller = VissimController()
    try:
        _log("[1/6] Launching a dedicated Vissim instance (attach_to_running=False)...")
        started = time.monotonic()
        controller.connect(visible=True, launch_if_missing=True, attach_to_running=False)
        _log(f"      Connected via {controller.prog_id} in {time.monotonic() - started:.1f}s")

        _log(f"[2/6] Loading network: {working.name}")
        controller.load_network(str(working))
        evaluation = controller.vissim.Evaluation

        found = probe_ids(evaluation)
        probe_attribute_selection_surface(evaluation)

        if len(found) != len(_XML_DEFAULTS):
            _log(f"\nFAIL: only verified {len(found)}/{len(_XML_DEFAULTS)} fields: {found}")
            raise SystemExit(1)

        _log(f"\n[3/6] All ids verified:\n  {found}")

        _log(f"\n[4/6] Arming vehicle-record output for a {duration_s}s window, reading back...")
        # write_file is the "arm" toggle (there is no separate "collect_data"
        # id the way the other four evaluation types have -- see module
        # docstring point 1). from_time=0, to_time=duration so the whole run
        # is captured; resolution left at its verified default (1) unless
        # that default itself turns out to mean something other than
        # "every timestep" -- logged either way.
        target = {
            "write_file": True,
            "from_time": 0,
            "to_time": duration_s,
            "resolution": found_default_or(found, evaluation, "resolution"),
        }
        for field in ("write_file", "from_time", "to_time"):
            attr_id = found[field]
            evaluation.SetAttValue(attr_id, target[field])
            readback = evaluation.AttValue(attr_id)
            _log(f"      SetAttValue({attr_id!r}, {target[field]!r}) -> read-back {readback!r}")

        also_configure_network_eval = True
        if also_configure_network_eval:
            _log("\n      Also arming the 4 network-performance evaluation types (proven ids from "
                  "vissim_com.py) so this probe's run looks like a real agent run, not a special case.")
            try:
                controller.configure_network_performance_evaluation(duration_s=duration_s)
                _log("      OK.")
            except Exception as exc:
                _log(f"      (non-fatal for this probe) could not configure network eval: {exc}")

        _log(f"\n[5/6] Running RunContinuous for sim duration {duration_s}s...")
        controller.set_sim_duration(duration_s)
        controller.set_random_seed(1)
        run_started = time.monotonic()
        controller.vissim.Simulation.RunContinuous()
        _log(f"      RunContinuous completed in {time.monotonic() - run_started:.1f}s (wall clock)")

        _log("\n[6/6] Locating the resulting .fzp file(s)...")
        new_fzp = sorted(p for p in work_dir.rglob("*.fzp") if p not in pre_existing)
        new_db = sorted(p for p in work_dir.rglob("*.db") if p not in pre_existing)
        _log(f"      New .fzp file(s): {[str(p) for p in new_fzp]}")
        _log(f"      New .db file(s):  {[str(p) for p in new_db]}")

        if not new_fzp:
            _log("\n      FAIL: no .fzp file was written. Dumping full work_dir tree for diagnosis:")
            for p in sorted(work_dir.rglob("*")):
                if p.is_file():
                    _log(f"        {p.relative_to(work_dir)}  ({p.stat().st_size} bytes)")
            raise SystemExit(1)

        fzp_path = new_fzp[0]
        size_bytes = fzp_path.stat().st_size
        _log(f"\n      Inspecting {fzp_path.name} ({size_bytes} bytes for {duration_s}s of sim time)")
        _log(f"      Extrapolated size per 60s: {size_bytes * 60 / max(duration_s, 1):.0f} bytes")

        text = fzp_path.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        header_lines = [ln for ln in lines if ln.startswith("*") or ln.startswith("$")]
        data_lines = [ln for ln in lines if ln and not ln.startswith("*") and not ln.startswith("$")]
        _log(f"      Total lines: {len(lines)} ({len(header_lines)} header/comment, {len(data_lines)} data)")
        _log("\n      --- verbatim header/comment lines ---")
        for ln in header_lines:
            _log(f"      {ln}")
        _log("\n      --- first 10 data lines verbatim ---")
        for ln in data_lines[:10]:
            _log(f"      {ln}")

        vehicle_line = next((ln for ln in header_lines if ln.upper().startswith("$VEHICLE:")), None)
        _log("\n" + "=" * 78 + "\n[DIFF] real .fzp header vs trajectory_reader.py's current assumptions\n" + "=" * 78)
        if vehicle_line is None:
            _log("      NO '$VEHICLE:' header line found at all -- trajectory_reader.parse_fzp "
                  "would treat every data row as unparseable (columns stays None).")
        else:
            real_columns = [c.strip().upper() for c in vehicle_line.split(":", 1)[1].split(";")]
            _log(f"      Real columns (order as exported): {real_columns}")
            from services.trajectory_reader import OPTIONAL_COLUMNS, REQUIRED_COLUMNS

            _log(f"      trajectory_reader.REQUIRED_COLUMNS = {REQUIRED_COLUMNS}")
            _log(f"      trajectory_reader.OPTIONAL_COLUMNS = {OPTIONAL_COLUMNS}")
            missing_required = [c for c in REQUIRED_COLUMNS if c not in real_columns]
            if missing_required:
                _log(f"      MISMATCH: required column(s) NOT present in the real export: {missing_required}")
            else:
                _log("      All REQUIRED_COLUMNS are present in the real export (by literal name).")

        _log("\nRESULT: PASS (probe completed; see [DIFF] section above for parser-compat findings)")
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


def found_default_or(found: dict[str, str], evaluation: Any, field: str) -> Any:
    """Read back whatever the network's current value for ``field`` already
    is (verified id from ``found``) rather than assuming -- resolution=1 is
    the observed default and this probe does not need to change it, only
    confirm what it actually is right before the run."""
    try:
        return evaluation.AttValue(found[field])
    except Exception:
        return _XML_DEFAULTS[field]


if __name__ == "__main__":
    main()
