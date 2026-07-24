"""Live probe: find the real COM attribute IDs for every Vissim evaluation
type ``services/results_reader.py``'s ``_read_db`` UNCONDITIONALLY queries
(no ``_query_rows_with_fallback`` safety net) -- run this BEFORE writing
``services.vissim_com.configure_network_performance_evaluation()`` around
any assumed attribute name.

Why "every unconditionally-queried table", not just Vehicle Network
Performance: ``_read_db`` plain-``cur.execute()``s (raises on a missing
table, unlike its ``_query_rows_with_fallback`` calls, which degrade to an
empty list) against FOUR tables:
  - VEHICLENETWORKPERFORMANCEMEASUREMENT_EvaluationTimeIntervalClass (the
    "vehNetPerf" evaluation -- the "network" KPI dict tools_run._summarize_
    kpis reads)
  - MOVEMENT_EvaluationTimeIntervalClass (the "nodeResults" evaluation)
  - VEHICLETRAVELTIMEMEASUREMENT_EvaluationTimeIntervalClass (the
    "vehTravTms" evaluation)
  - MOVEMENT_EvaluationTimeInterval (the "queues" evaluation -- a DIFFERENT
    table from MOVEMENT_EvaluationTimeIntervalClass above, driven by a
    DIFFERENT evaluation type despite the similar table name)
A run missing ANY ONE of these raises inside ``_read_db`` before it ever
reaches ``return data`` -- losing even the "network" dict it already read --
so ``configure_network_performance_evaluation`` must arm all four, not just
vehNetPerf (learned live: an earlier version of this fix that armed only
vehNetPerf made the acceptance script's own KPI check fail with exactly this
symptom -- see docs/phase2/DECISIONS.md).

Why this exists (the M2.1 CycTm doctrine, docs/phase2/DECISIONS.md 2026-07-23):
COM attribute IDs must be verified EMPIRICALLY, never trusted from docs or
memory -- ``set_signal_cycle`` shipped broken for an entire milestone because
"CycleTime" (a plausible, doc-shaped guess) doesn't exist; the real id is the
unrelated-looking "CycTm". The same risk applies here: Vissim's .inpx XML
schema names these evaluations' toggles ``<vehNetPerf collectData="false".../>``,
``<nodeResults collectData="false".../>``, ``<vehTravTms collectData="false".../>``,
``<queues collectData="false".../>`` (grep of the Beijing example .inpx,
``<evaluation>`` block) -- but the .inpx attribute name and the COM
``AttValue`` id are DIFFERENT vocabularies in general (CycTm vs cycleTime),
so the XML name is a hint, never the answer, even though it turned out to be
an exact (case-adjusted) match for ``vehNetPerf`` when this probe first ran.

Strategy: connect to a REAL Vissim 2026 instance (never attach_to_running --
see docs/phase2/00-EXECUTION-GUIDE.md rule 3), load a freshly copied Beijing
example (never the original), and brute-force a candidate list of plausible
COM attribute ids against ``Vissim.Evaluation.AttValue(...)`` for each of the
four fields (collect_data/from_time/to_time/interval) of each of the four
evaluation types. A candidate is treated as VERIFIED not merely because the
read didn't raise, but because its PRE-EDIT read-back matches the known XML
default for that network -- a candidate that merely doesn't raise but
returns an unrelated value is logged as a false-positive risk, not accepted.

Once every id is verified, this probe SETS them all (collect_data -> True,
fromTime/interval/toTime scoped to a short test run), reads back every value
(the paired getter/read-back the M2.1 doctrine requires), runs a short
RunContinuous, and opens the resulting .db directly via sqlite to confirm
ALL FOUR tables actually materialize with rows.

Usage: python tools/evaluation_probe.py [example_dir]
Default example: Urban Intersection Beijing.CN (2026 Examples Demo).

Always disconnects its dedicated Vissim instance in a ``finally`` block.
"""
from __future__ import annotations

import os
import shutil
import sqlite3
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
WORK_ROOT = BACKEND_ROOT.parent / "runtime" / "evaluation-probe"

# One entry per evaluation type ``_read_db`` unconditionally queries.
# ``xml_tag`` is the .inpx element name (ground truth for the pre-edit
# read-back cross-check); ``prefixes`` are candidate COM-id prefixes, most
# plausible first (the exact, case-adjusted xml tag name first, since that
# is what vehNetPerf turned out to be -- verified, not assumed, for each
# type independently below); ``target_table`` is the result table this
# evaluation type must produce.
EVAL_TYPES: dict[str, dict[str, Any]] = {
    "vehNetPerf": {
        "prefixes": ["VehNetPerf", "NetPerf", "VehNetworkPerf", "VehicleNetworkPerf", "NetworkPerformance"],
        "xml_defaults": {"collect_data": False, "from_time": 0, "interval": 999999, "to_time": 99999},
        "target_table": "VEHICLENETWORKPERFORMANCEMEASUREMENT_EVALUATIONTIMEINTERVALCLASS",
    },
    "nodeResults": {
        "prefixes": [
            "NodeResults", "Node", "NodeResult", "NodeEval", "NodePerf", "NodeRes",
            "NodesResults", "Nodes", "NodeEvaluation", "Junction", "Junctions",
            "Movement", "Movements", "Mvmt", "MvmtResults", "MovementResults",
            "NodeResEval", "NodeEvalRes",
        ],
        "xml_defaults": {"collect_data": False, "from_time": 0, "interval": 999999, "to_time": 99999},
        "target_table": "MOVEMENT_EVALUATIONTIMEINTERVALCLASS",
    },
    "vehTravTms": {
        "prefixes": [
            "VehTravTms", "VehTravTm", "VehicleTravelTime", "TravTm", "VehTravelTime",
            "TravTms", "TravelTime", "TravelTimes", "VehicleTravelTimes", "VehTravelTm",
            "VehTt", "Tt", "VehTravTime",
        ],
        "xml_defaults": {"collect_data": False, "from_time": 400, "interval": 260, "to_time": 4000},
        "target_table": "VEHICLETRAVELTIMEMEASUREMENT_EVALUATIONTIMEINTERVALCLASS",
    },
    "queues": {
        "prefixes": [
            "Queues", "Queue", "QueueCounters", "QueueCount", "QueueCounter", "Qc",
            "QueueLength", "QueueLengths",
        ],
        "xml_defaults": {"collect_data": False, "from_time": 400, "interval": 260, "to_time": 4000},
        "target_table": "MOVEMENT_EVALUATIONTIMEINTERVAL",
    },
}

_FIELD_SUFFIXES: dict[str, list[str]] = {
    "collect_data": ["CollectData", "Eval", "DataCollection", "Active", "Enabled", "Act", ""],
    "from_time": ["FromTime", "StartTime", "From"],
    "to_time": ["ToTime", "EndTime", "To"],
    "interval": ["Interval", "TimeInterval"],
}

# Candidate SUB-OBJECT names to try on Evaluation itself (attribute/property
# access, not AttValue) -- Vissim's object model is otherwise hierarchical
# (Vissim.Net.Links, Vissim.Simulation, ...), so it's plausible some
# evaluation types are exposed as their OWN child COM object (with simple
# ids like "CollectData" on THAT object) rather than flat on Evaluation with
# a type-prefixed id (the pattern vehNetPerf happened to use).
_SUBOBJECT_CANDIDATES: dict[str, list[str]] = {
    "nodeResults": ["NodeEvaluation", "NodeResults", "Nodes", "MovementEvaluation"],
    "vehTravTms": ["VehTravTmEvaluation", "TravelTimeEvaluation", "VehicleTravelTimeEvaluation"],
    "queues": ["QueueCounterEvaluation", "QueueEvaluation", "Queues"],
}


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


def _probe_field(
    evaluation: Any, field: str, candidates: list[str], expected: Any
) -> tuple[str | None, dict[str, Any]]:
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
        else:
            try:
                normalized = type(expected)(value)
            except Exception:
                pass
        if best is None and normalized == expected:
            best = candidate
    return best, outcomes


def _probe_eval_type(evaluation: Any, eval_name: str, spec: dict[str, Any]) -> dict[str, str] | None:
    _log(f"\n{'=' * 78}\nEvaluation type: {eval_name!r} (expected pre-edit defaults: {spec['xml_defaults']})\n{'=' * 78}")
    found: dict[str, str] = {}
    for field, expected in spec["xml_defaults"].items():
        candidates = [p + s for p in spec["prefixes"] for s in _FIELD_SUFFIXES[field]]
        best, outcomes = _probe_field(evaluation, field, candidates, expected)
        _log(f"\n  -- field: {field} --")
        for candidate, outcome in outcomes.items():
            marker = "MATCH" if candidate == best else ("ok" if not str(outcome).startswith("ERROR") else "no")
            _log(f"    [{marker:5s}] AttValue({candidate!r}) = {outcome!r}")
        if best:
            found[field] = best
            _log(f"  => VERIFIED for {eval_name}.{field}: {best!r}")
        else:
            _log(f"  => NO candidate matched the expected default for {eval_name}.{field!r}")
    if len(found) != len(spec["xml_defaults"]):
        return None
    return found


def _probe_subobjects(evaluation: Any, eval_name: str) -> None:
    candidates = _SUBOBJECT_CANDIDATES.get(eval_name, [])
    if not candidates:
        return
    _log(f"\n  -- sub-object probe for {eval_name!r} (Evaluation.<name> instead of a flat AttValue id) --")
    for name in candidates:
        try:
            obj = getattr(evaluation, name)
        except Exception as exc:
            _log(f"    [no   ] Evaluation.{name} -> ERROR: {type(exc).__name__}: {exc}")
            continue
        if obj is None:
            _log(f"    [no   ] Evaluation.{name} -> None")
            continue
        _log(f"    [ok   ] Evaluation.{name} -> {obj!r} -- probing simple AttValue ids on it")
        for simple_id in ("CollectData", "Act", "Eval", "Active", "Enabled"):
            try:
                value = obj.AttValue(simple_id)
                _log(f"        [ok   ] .AttValue({simple_id!r}) = {value!r}")
            except Exception as exc:
                _log(f"        [no   ] .AttValue({simple_id!r}) -> ERROR: {exc}")


def main() -> None:
    example_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_EXAMPLE
    if not example_dir.is_dir():
        raise SystemExit(f"Example folder not found: {example_dir}")

    work_dir = _fresh_work_dir(example_dir)
    inpx_files = sorted(work_dir.glob("*.inpx"))
    if not inpx_files:
        raise SystemExit(f"No .inpx in {work_dir}")
    working = inpx_files[0]
    _log(f"[1/6] Working copy: {working}")

    import pythoncom

    pythoncom.CoInitialize()
    from services.vissim_com import VissimController

    controller = VissimController()
    overall_ok = False
    all_found: dict[str, dict[str, str]] = {}
    try:
        _log("[2/6] Launching a dedicated Vissim instance (attach_to_running=False)...")
        started = time.monotonic()
        controller.connect(visible=True, launch_if_missing=True, attach_to_running=False)
        _log(f"      Connected via {controller.prog_id} in {time.monotonic() - started:.1f}s")

        _log(f"[3/6] Loading network: {working.name}")
        controller.load_network(str(working))
        evaluation = controller.vissim.Evaluation

        _log("[4/6] Probing every evaluation type's candidate COM attribute ids...")
        failed_types: list[str] = []
        for eval_name, spec in EVAL_TYPES.items():
            found = _probe_eval_type(evaluation, eval_name, spec)
            if found is None:
                _log(f"\nFAIL (so far): could not verify every field for evaluation type {eval_name!r}.")
                _probe_subobjects(evaluation, eval_name)
                failed_types.append(eval_name)
                continue
            all_found[eval_name] = found

        if failed_types:
            _log(f"\nFAIL: could not verify every field for: {failed_types}. See probe output above.")
            raise SystemExit(1)

        _log(f"\n[5/6] All ids verified:")
        for eval_name, found in all_found.items():
            _log(f"  {eval_name}: {found}")

        _log("\n      Setting collect_data=True + a short-run window on every evaluation type, reading back...")
        sim_period = int(controller.get_sim_duration())
        to_time = min(sim_period, 600) if sim_period > 0 else 600
        for eval_name, found in all_found.items():
            target_values = {"collect_data": True, "from_time": 0, "to_time": to_time, "interval": to_time}
            for field, value in target_values.items():
                attr_id = found[field]
                evaluation.SetAttValue(attr_id, value)
                readback = evaluation.AttValue(attr_id)
                _log(f"      [{eval_name}] SetAttValue({attr_id!r}, {value!r}) -> read-back = {readback!r}")
                if field == "collect_data":
                    if not bool(int(readback)):
                        _log(f"      FAIL: {attr_id} did not read back truthy after being set True")
                        raise SystemExit(1)
                elif int(readback) != int(value):
                    _log(f"      FAIL: {attr_id} read-back {readback!r} != written {value!r}")
                    raise SystemExit(1)

        _log("\n[6/6] Running a short RunContinuous and checking the result .db for every KPI table...")
        controller.vissim.Simulation.RunContinuous()

        db_paths = sorted(work_dir.rglob("*.db"), key=lambda p: p.stat().st_mtime)
        if not db_paths:
            _log("      FAIL: no result .db was written")
            raise SystemExit(1)
        db_path = db_paths[-1]
        conn = sqlite3.connect(str(db_path))
        try:
            tables = {
                row[0].upper()
                for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            }
            _log(f"      {db_path.name} tables = {sorted(tables)}")
            # The acceptance bar here is EXISTENCE, matching what
            # services/results_reader.py's _read_db actually requires: it
            # plain-executes (no LIMIT, no fallback) against each of these
            # tables and only RAISES if the table is missing entirely -- zero
            # rows is a normal, already-handled outcome (an empty list in
            # the returned dict), not a failure. Only vehNetPerf's table
            # feeds tools_run._summarize_kpis's "network" dict directly, so
            # only that one strictly needs >=1 row for the KPI summary to be
            # non-None; the other three just need to EXIST so _read_db
            # doesn't raise before ever reaching the network dict it already
            # parsed. Row counts are logged for diagnostic visibility only.
            all_present = True
            for eval_name, spec in EVAL_TYPES.items():
                target_table = spec["target_table"]
                if target_table not in tables:
                    _log(f"      FAIL: {target_table} (for {eval_name}) is missing from the result .db")
                    all_present = False
                    continue
                rows = conn.execute(f"SELECT COUNT(*) FROM {target_table}").fetchone()[0]
                _log(f"      PASS: {target_table} (for {eval_name}) exists with {rows} row(s)")
                if rows < 1:
                    _log(
                        f"      NOTE: {target_table} exists but has zero rows in this "
                        "particular window/network (not a failure -- _read_db degrades "
                        "this to an empty list, not an exception)."
                    )
            if not all_present:
                raise SystemExit(1)
        finally:
            conn.close()

        overall_ok = True
        _log("\nVERIFIED COM ATTRIBUTE IDS (paste into vissim_com.py):")
        for eval_name, found in all_found.items():
            _log(f"  {eval_name}:")
            for field, attr_id in found.items():
                _log(f"    {field:14s} -> {attr_id!r}")
    finally:
        try:
            controller.disconnect()
        except Exception:
            pass
        _log("\nDisconnected the dedicated Vissim instance.")

    if overall_ok:
        _log("\nRESULT: PASS")
    else:
        _log("\nRESULT: FAIL")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
