"""CALIBRATION-LOOP LIVE CHECK: the whole approval loop, against a REAL Vissim.

``backend/tests/test_calibration_loop_e2e.py`` is this script's offline sibling.
It proves the CHAIN -- run evidence -> reality check -> fix proposal -> approval
pause -> authenticated approve -> audit DOCX -- with two seams faked
(``_default_provider`` and ``_build_edit_session``). This script runs the same
chain with the RUN, the EVIDENCE, the APPLY and the RE-RUN all real, on a COPY
of the consultant network, in its own demo workspace.

WHAT IS REAL HERE (and what is not)
-----------------------------------
REAL:
  * the demo workspace + the network upload, through the real HTTP routes
    (``POST /api/workspaces``, ``POST /api/datasets/upload``);
  * the baseline run: ``run_simulation`` -> ``start_agent_simulation_job`` ->
    ``VissimController.connect(attach_to_running=False)`` -> ``LoadNet`` ->
    ``configure_network_performance_evaluation`` -> ``RunContinuous``;
  * the run evidence: U1's ``_collect_run_evidence`` over the run's OWN .db;
  * the observed workbook upload, through ``POST /api/validation/base-workbook``
    (which refuses to persist a workbook that does not conform);
  * the reality check: ``dispatch("run_reality_check", ...)``, registry path;
  * the approval: a real account in the real app DB, a real login, the real
    ``pending_approval.json`` pause, the real role gate;
  * THE APPLY: the real ``VissimEditSession`` -- real COM, real SetAttValue,
    real read-back, real SaveNetAs onto the session's working copy;
  * the re-run: a second real Vissim run on that EDITED working copy;
  * the audit DOCX, through ``GET /api/agent/audit/{id}/export.docx``.

NOT REAL, and deliberately so -- one seam, the same one
``tools/run_gate_live_check.py`` uses for its own live check:
  * the PLANNER. ``_default_provider`` is replaced by a canned queue so the
    turn proposes exactly the verb the reality check produced, with no paid
    LLM call and no planner-quality variance in the middle of a COM demo.
    Nothing about the run, the evidence, the write or the document is canned.

THE OBSERVED SIDE IS SYNTHETIC, BY MEASUREMENT
----------------------------------------------
There is no real observed count data for this network on this machine
(measured 2026-08-06). So the observed workbook is DERIVED FROM THE BASELINE
RUN'S OWN MOVEMENT VOLUMES with two designed deviations, and is named
``DEMO_합성_실측대조_*.xlsx`` so it can never be mistaken for field data:

  * NODE 8 -- the demand-proposal node. Every movement's observed value is the
    baseline's own modelled value scaled by one factor f, chosen so the node
    TOTAL lands at GEH ~= 7.5 (>= the 5.0 threshold). Node 8 was picked because
    ONE vehicle input dominates its demand: link 617 (Murdoch Dr_NB) carries
    71.9% of everything that can reach node 8, and input 113 alone carries
    69.0% -- which is the closest this network gets to
    ``fixes.ASSUME_INPUT_DRIVES_NODE_TOTAL`` actually being true.
  * NODE 5 -- the refusal node. Every movement's observed value IS the
    baseline's own modelled value, rounded. Node total GEH ~= 0, every movement
    reconciles, so ``map_node_counts`` reaches its
    ``MODEL_REPRODUCES_OBSERVED`` refusal and proposes nothing. Node 5 is also
    a CONTROL: a forward walk of the connector graph from link 617 reaches
    nodes 3, 4 and 8 -- never node 5 -- so the approved edit must not move it.

The workbook's ``Movement``/``Direction`` columns carry the node label and the
run's OWN Vissim movement OBJECT_ID, so every observed number joins back to the
row it was derived from.

TWO MEASURED BLOCKERS, BOTH HIT ON THE FIRST LIVE ATTEMPT (2026-08-07)
----------------------------------------------------------------------
1. ``LoadNet`` refused the raw uploaded ``.inpx`` -- "The network file could
   not be loaded.", no ``.err``. Known landmine: the export self-declares
   ``<netPara underScenMngm="true">`` (docs/phase2/DECISIONS.md 2026-07-31).
   NOT a workaround: a production agent turn never runs the raw dataset, it
   runs ``ArtifactManager.working_path``, and ``clone()`` normalizes that flag.
   Stage 1 therefore runs a clone, exactly as ``routers/assistant.py`` does.

2. ``numRuns``. The export carries ``<simulation numRuns="5" randSeed="42"
   randSeedIncr="123">`` and Vissim's ``RunContinuous`` honours it, so
   ``run_simulation(random_seeds=[42])`` produced FIVE result databases (seeds
   42/165/288/411/534). U1 sums VEHS across every ``.db``, so
   ``movement_volumes`` carried five runs' worth of vehicles, and
   ``run_reality_check`` refused the whole comparison with
   ``MULTI_SEED_VOLUMES_NOT_COMPARABLE`` -- correctly, and invisibly to
   ``run_simulation``'s own envelope, which still says ``random_seeds: [42]``
   and ``simulationrun_verified: true``.

   This IS a product gap and it is reported as one:
   ``simulation_network_builder._make_network_standalone`` (the INTERACTIVE
   run path) sets ``numRuns="1"`` on the very next line after
   ``underScenMngm="false"``; ``ArtifactManager.clone()`` received only the
   ``underScenMngm`` half of the 2026-07-31 fix, and
   ``_run_agent_simulation_job`` sets SimPeriod and RandSeed but never
   NumRuns. The refusal is preserved verbatim in
   ``artifacts/FINDING_agent_run_numRuns_blocker.json``.

   Production modules are read-only for this probe, so the loop is closed by
   preparing the DEMO COPY instead: stage 0 applies that same one attribute
   (``numRuns="5"`` -> ``"1"``) to the copy's bytes before uploading, prints
   the full textual diff to prove it is one attribute and nothing else, and
   never touches the consultant original. Stated plainly: **the loop below
   closes on the real client model only because of that one prepared
   attribute; without the product-side fix it does not close at all.**

COM SAFETY
----------
Every Vissim session is opened by the house entry points
(``routers.simulations``' job runner and ``services.agent.edit_session``), both
of which ``connect(attach_to_running=False)``. The live consultant workspace
(``runtime/workspaces/bfc69251bd04``) is READ ONCE and never written -- its
``current.inpx`` is hashed before and after and the hashes are compared. Runs
are bounded (``--duration``, default 900 sim-seconds; one seed). Vissim PIDs
present before the script starts are recorded and NEVER killed; only PIDs this
process started are cleaned up, and the exit banner states the orphan count.

NO uvicorn: everything is driven in-process through ``TestClient`` and direct
service calls, so the job threads run inside this process (the way the whole
test suite works).

Evidence tees to ``runtime/calibration-loop-live-check/`` as it goes --
``state.json`` (rewritten after every step, so a probe that dies mid-COM still
leaves what it had) and ``log.txt`` -- plus ``artifacts/`` for the DOCX, the
workbook, the two reality-check reports and the before/after GEH table.

Usage (from ``backend/``, with the venv python)::

    python tools/calibration_loop_live_check.py
    python tools/calibration_loop_live_check.py --duration 600
    python tools/calibration_loop_live_check.py --stages 0,1     # stop after the baseline
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import traceback
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_ROOT.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # pragma: no cover - detached stream
    pass

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

WORK_ROOT = REPO_ROOT / "runtime" / "calibration-loop-live-check"
STATE_PATH = WORK_ROOT / "state.json"
LOG_PATH = WORK_ROOT / "log.txt"
ARTIFACT_DIR = WORK_ROOT / "artifacts"

#: The live consultant dataset. READ ONLY -- copied, never opened in place.
CONSULTANT_INPX = (
    REPO_ROOT / "runtime" / "workspaces" / "bfc69251bd04" / "uploads" / "current" / "current.inpx"
)

DEMO_WORKSPACE_NAME = "라이브 데모 2026-08-07"
UPLOAD_FILENAME = "DEMO_라이브데모_사본_current.inpx"
WORKBOOK_FILENAME = "DEMO_합성_실측대조_2026-08-07.xlsx"

DEFAULT_DURATION_S = 900
RUN_SEED = 42

#: The demand-proposal node and the vehicle input that dominates its demand.
#: Measured offline from the connector graph (see the module docstring).
DEMAND_NODE = "8"
DEMAND_INPUT_NO = "113"
DEMAND_INPUT_SHARE = 0.690  # input 113 / everything that can reach node 8
#: The reconciling refusal node -- also the control: unreachable from link 617.
REFUSAL_NODE = "5"

#: The GEH the designed deviation aims the demand node's TOTAL at.
TARGET_BEFORE_GEH = 7.5

#: A clearly-named demo account in the real app DB (runtime/db/app.db).
DEMO_REVIEWER_USERNAME = "demo-reviewer-2026-08-07"
DEMO_REVIEWER_DISPLAY = "데모 검토자"
DEMO_REVIEWER_ROLE = "reviewer"
DEMO_REVIEWER_PASSWORD = "demo-live-calibration-2026"  # >= auth.MIN_PASSWORD_CHARS

ALL_STAGES = (0, 1, 2, 3, 4, 5, 6, 7)


# ---------------------------------------------------------------------------
# Logging / state (the house tee rule: evidence lands in runtime/)
# ---------------------------------------------------------------------------


def _log(msg: str = "") -> None:
    print(msg, flush=True)
    try:
        WORK_ROOT.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(msg + "\n")
    except OSError:
        pass


_state: dict[str, Any] = {}


def _load_state() -> dict[str, Any]:
    """Merged in at startup so ``--stages 4,5,6`` can resume a run whose COM
    leg died halfway without repeating the two Vissim runs before it."""
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
    return {}


def _save_state() -> None:
    """Written after EVERY step, never once at the end."""
    WORK_ROOT.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(_state, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, STATE_PATH)


def _stage(key: str) -> dict[str, Any]:
    return _state.setdefault("stages", {}).setdefault(key, {})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _banner(text: str) -> None:
    _log("")
    _log("=" * 78)
    _log(text)
    _log("=" * 78)


#: The ONE attribute this probe normalizes on the demo copy -- see stage 0.
_NUM_RUNS_RE = re.compile(r'numRuns="\d+"')


def _one_line_diff(before: str, after: str) -> list[str]:
    """Every line that differs between the two texts, elided to the changed
    neighbourhood -- so "one attribute and nothing else" is shown, not claimed."""
    out: list[str] = []
    before_lines = before.splitlines()
    after_lines = after.splitlines()
    if len(before_lines) != len(after_lines):
        out.append(f"!! line count changed: {len(before_lines)} -> {len(after_lines)}")
    for index, (a, b) in enumerate(zip(before_lines, after_lines)):
        if a != b:
            out.append(f"line {index + 1}:")
            out.append(f"  - {a.strip()[:300]}")
            out.append(f"  + {b.strip()[:300]}")
    if not out:
        out.append("(no textual difference)")
    return out


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Vissim process bookkeeping (kill only what WE started)
# ---------------------------------------------------------------------------


def _vissim_pids() -> set[int]:
    try:
        proc = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True, timeout=60
        )
    except (OSError, subprocess.SubprocessError):
        return set()
    pids: set[int] = set()
    for line in proc.stdout.splitlines():
        parts = [p.strip('"') for p in line.split('","')]
        if len(parts) < 2:
            continue
        if parts[0].strip('"').lower().startswith("vissim"):
            try:
                pids.add(int(parts[1]))
            except ValueError:
                continue
    return pids


def _kill_new_vissim(before: set[int]) -> list[int]:
    killed: list[int] = []
    for pid in sorted(_vissim_pids() - before):
        try:
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/F", "/T"], capture_output=True, timeout=60
            )
            killed.append(pid)
        except (OSError, subprocess.SubprocessError):
            continue
    return killed


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _geh(model: float, observed: float) -> float | None:
    from services import validation

    return validation.geh(model, observed)


def _observed_total_for_geh(modelled: float, target_geh: float) -> float:
    """The observed total ABOVE ``modelled`` whose GEH equals ``target_geh``.

    GEH^2 = 2(O-M)^2/(M+O)  =>  2O^2 - (4M + G^2)O + (2M^2 - G^2 M) = 0.
    The larger root is the over-count side (observed > modelled), which is what
    a demand-shortfall demo needs.
    """
    g2 = target_geh * target_geh
    b = 4.0 * modelled + g2
    c = 2.0 * modelled * modelled - g2 * modelled
    disc = b * b - 8.0 * c
    return (b + math.sqrt(max(disc, 0.0))) / 4.0


def _movement_rows(run_result: dict[str, Any], node_id: str) -> dict[str, float]:
    """{OBJECT_ID: VEHS} for one node, EXCLUDING the node-level aggregate row
    (whose OBJECT_ID is the bare node id) -- the same exclusion
    ``reality_check.run_nodes`` applies."""
    volumes = run_result.get("movement_volumes") or {}
    if not isinstance(volumes, dict):
        return {}
    out: dict[str, float] = {}
    for object_id, row in volumes.items():
        if not isinstance(row, dict):
            continue
        if str(row.get("node")) != str(node_id):
            continue
        if str(object_id) == str(node_id):
            continue
        vehs = row.get("vehs")
        if vehs is None:
            continue
        out[str(object_id)] = float(vehs)
    return out


def _db_table_census(db_path: Path) -> dict[str, Any]:
    """Row counts per table + the movement tables' interval structure, read
    DIRECTLY via sqlite -- never via the tool envelope's own claim."""
    census: dict[str, Any] = {"db": str(db_path), "tables": {}}
    conn = sqlite3.connect(str(db_path))
    try:
        names = [
            r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        ]
        for name in sorted(names):
            try:
                census["tables"][name] = conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
            except sqlite3.Error as exc:
                census["tables"][name] = f"<unreadable: {exc}>"
        for table in ("MOVEMENT_EvaluationTimeInterval", "MOVEMENT_EvaluationTimeIntervalClass"):
            if table not in names:
                census[f"{table}__ARG_TIMEINTERVAL"] = "<table absent>"
                continue
            try:
                cols = [r[1] for r in conn.execute(f'PRAGMA table_info("{table}")').fetchall()]
                census[f"{table}__columns"] = cols
                if "ARG_TIMEINTERVAL" in cols:
                    rows = conn.execute(
                        f'SELECT ARG_TIMEINTERVAL, COUNT(*) FROM "{table}" '
                        "GROUP BY ARG_TIMEINTERVAL ORDER BY ARG_TIMEINTERVAL"
                    ).fetchall()
                    census[f"{table}__ARG_TIMEINTERVAL"] = [
                        {"interval": r[0], "rows": r[1]} for r in rows
                    ]
                if "ARG_VEHICLECLASS" in cols:
                    rows = conn.execute(
                        f'SELECT ARG_VEHICLECLASS, COUNT(*) FROM "{table}" '
                        "GROUP BY ARG_VEHICLECLASS ORDER BY ARG_VEHICLECLASS"
                    ).fetchall()
                    census[f"{table}__ARG_VEHICLECLASS"] = [
                        {"class": r[0], "rows": r[1]} for r in rows
                    ]
            except sqlite3.Error as exc:
                census[f"{table}__ARG_TIMEINTERVAL"] = f"<unreadable: {exc}>"
        try:
            row = conn.execute(
                "SELECT RANDSEED, SIMPERIOD, SIMEND, VISSIMVER FROM SIMULATIONRUN "
                "ORDER BY NO DESC LIMIT 1"
            ).fetchone()
            if row:
                census["simulationrun"] = {
                    "randseed": row[0],
                    "simperiod": row[1],
                    "simend": row[2],
                    "vissimver": row[3],
                }
        except sqlite3.Error:
            pass
    finally:
        conn.close()
    return census


def _shadow_vehicle_input(inpx: Path, input_no: str) -> dict[str, Any] | None:
    from services.agent.tools_network import build_shadow_model

    shadow = build_shadow_model(inpx)
    for entry in shadow.get("vehicle_inputs") or []:
        if str(entry.get("no")) == str(input_no):
            return entry
    return None


def _stream_events(response) -> list[dict[str, Any]]:
    return [json.loads(line) for line in response.text.splitlines() if line.strip()]


# ---------------------------------------------------------------------------
# STAGE 0 -- workspace isolation
# ---------------------------------------------------------------------------


def stage_0_workspace(client) -> None:
    _banner("[STAGE 0] Demo workspace + a COPY of the consultant network")
    entry = _stage("0_workspace")
    entry["started_at"] = _now()
    started = time.monotonic()

    if not CONSULTANT_INPX.is_file():
        raise SystemExit(f"Consultant network not found: {CONSULTANT_INPX}")

    source_hash_before = _sha256(CONSULTANT_INPX)
    source_bytes = CONSULTANT_INPX.read_bytes()
    entry["consultant_inpx"] = str(CONSULTANT_INPX)
    entry["consultant_sha256_before"] = source_hash_before
    entry["consultant_bytes"] = len(source_bytes)
    _log(f"[0.1] Consultant network read (never opened in place): {CONSULTANT_INPX}")
    _log(f"      {len(source_bytes)} bytes, sha256={source_hash_before}")

    # ---- ONE declared dataset preparation, made on the COPY ---------------
    # MEASURED 2026-08-07 (artifacts/FINDING_agent_run_numRuns_blocker.json):
    # this export carries <simulation numRuns="5" randSeed="42"
    # randSeedIncr="123">, and Vissim's RunContinuous honours NumRuns. The
    # agent run path never overrides it -- routers/simulations.py sets
    # SimPeriod and RandSeed only -- so a run_simulation call that asks for
    # ONE seed produced FIVE result databases (seeds 42/165/288/411/534), U1
    # summed VEHS across all five, and run_reality_check refused the whole
    # comparison with MULTI_SEED_VOLUMES_NOT_COMPARABLE. The product already
    # owns the fix for the INTERACTIVE path
    # (simulation_network_builder._make_network_standalone sets numRuns="1"
    # right next to underScenMngm="false"); the agent clone path received
    # only the underScenMngm half of it.
    #
    # This probe may not edit production modules, so the SAME one-attribute
    # normalization is applied here to the DEMO COPY's bytes before they are
    # uploaded -- it is a property of the dataset a modeller sets, not a
    # behaviour of the product, and it is diffed below so the change is
    # provably one attribute and nothing else.
    source_text = source_bytes.decode("utf-8", errors="strict")
    prepared_text, n_runs = _NUM_RUNS_RE.subn('numRuns="1"', source_text, count=1)
    prepared_bytes = prepared_text.encode("utf-8")
    entry["preparation"] = {
        "what": 'simulation/@numRuns "5" -> "1" on the DEMO COPY only',
        "occurrences_replaced": n_runs,
        "why": (
            "the agent run path does not normalize numRuns (measured blocker, see "
            "artifacts/FINDING_agent_run_numRuns_blocker.json); "
            "simulation_network_builder._make_network_standalone already applies exactly this "
            "line for the interactive path"
        ),
        "diff": _one_line_diff(source_text, prepared_text),
        "bytes_before": len(source_bytes),
        "bytes_after": len(prepared_bytes),
    }
    _log(f"[0.2] DECLARED DATASET PREPARATION on the copy: numRuns 5 -> 1 "
         f"({n_runs} occurrence). Diff:")
    for line in entry["preparation"]["diff"]:
        _log(f"        {line}")
    if n_runs != 1:
        raise SystemExit(f"Expected exactly one numRuns attribute, replaced {n_runs}.")

    workspace_id = _state.get("workspace_id")
    if workspace_id:
        _log(f"[0.3] Reusing the demo workspace already recorded: {workspace_id}")
        entry["reused_existing_workspace"] = True
    else:
        created = client.post("/api/workspaces", json={"name": DEMO_WORKSPACE_NAME})
        if created.status_code != 200:
            raise SystemExit(f"Workspace creation failed: {created.status_code} {created.text}")
        record = created.json()
        workspace_id = record["id"]
        entry["workspace"] = record
        _log(f"[0.3] Created workspace {workspace_id!r} name={record['name']!r}")
    _state["workspace_id"] = workspace_id

    headers = {"X-Workspace-Id": workspace_id}
    _state["headers"] = headers
    from urllib.parse import quote

    upload = client.post(
        f"/api/datasets/upload?filename={quote(UPLOAD_FILENAME)}",
        content=prepared_bytes,
        headers=headers,
    )
    if upload.status_code != 200:
        raise SystemExit(f"Dataset upload failed: {upload.status_code} {upload.text}")
    active = upload.json()
    entry["upload_response"] = active
    _state["demo_inpx"] = active["path"]
    _log(f"[0.4] Uploaded the PREPARED COPY through POST /api/datasets/upload -> {active['path']}")
    _log(f"      source={active.get('source')} kind={active.get('kind')} revision={active.get('revision')}")

    demo_path = Path(active["path"])
    demo_hash = _sha256(demo_path)
    entry["demo_inpx_sha256"] = demo_hash
    entry["demo_matches_prepared_bytes"] = (
        demo_hash == hashlib.sha256(prepared_bytes).hexdigest()
    )
    # Compared as BYTES: Path.read_text() applies universal-newline translation
    # and would report a difference this file does not have.
    entry["differs_from_source_only_by_the_declared_preparation"] = (
        demo_path.read_bytes() == prepared_bytes
    )
    _log(f"[0.5] Demo copy sha256={demo_hash}")
    _log(f"      is exactly the prepared bytes, nothing else changed: "
         f"{entry['differs_from_source_only_by_the_declared_preparation']}")

    _state["prepared_source_text_len"] = len(prepared_text)
    source_hash_after = _sha256(CONSULTANT_INPX)
    entry["consultant_sha256_after"] = source_hash_after
    entry["consultant_untouched"] = source_hash_after == source_hash_before
    _log(f"[0.5] Consultant network UNTOUCHED: {entry['consultant_untouched']}")
    if not entry["consultant_untouched"]:
        raise SystemExit("FATAL: the live consultant dataset changed. Aborting.")

    entry["elapsed_s"] = round(time.monotonic() - started, 2)
    entry["ok"] = True
    _save_state()


# ---------------------------------------------------------------------------
# STAGE 1 -- the real baseline run through the agent job path
# ---------------------------------------------------------------------------


def _run_through_agent_job_path(source_inpx: Path, duration_s: int, label: str) -> dict[str, Any]:
    """``dispatch("run_simulation", ...)`` -- the registry tool, which submits
    to ``routers.simulations.start_agent_simulation_job`` (RunContinuous,
    attach_to_running=False) and blocks until the job is terminal."""
    import services.agent.tools_run as tools_run  # registers run_simulation
    from services.agent.toolkit import default_registry, dispatch

    tools_run.set_active_run_source(source_inpx)
    started = time.monotonic()
    try:
        envelope = dispatch(
            "run_simulation",
            {"duration_s": duration_s, "random_seeds": [RUN_SEED]},
            registry=default_registry,
        )
    finally:
        tools_run.clear_active_run_source()
    elapsed = round(time.monotonic() - started, 2)
    _log(f"      [{label}] dispatch returned in {elapsed}s, status={envelope.get('status')}")
    return {"envelope": envelope, "elapsed_s": elapsed}


def _describe_run(envelope: dict[str, Any]) -> dict[str, Any]:
    from services import results_evidence
    from services.simulation_runs import get_job_record, job_dir

    data = envelope.get("data") or {}
    run_result = data.get("run_result") or {}
    verification = envelope.get("verification") or {}
    job_id = verification.get("job_id")
    out: dict[str, Any] = {
        "job_id": job_id,
        "verification": verification,
        "message": envelope.get("message"),
        "evidence_descriptor": run_result.get("evidence"),
        "evaluation_interval_s": run_result.get("evaluation_interval_s"),
        "tables": run_result.get("tables"),
        "kpis": data.get("kpis"),
    }
    record = get_job_record(job_id) if job_id else None
    if record:
        out["scenario_result_id"] = record.get("scenario_result_id")
        out["dataset_revision"] = record.get("dataset_revision")
        out["per_seed_durations"] = record.get("per_seed_durations")
    if job_id:
        wd = job_dir(job_id)
        out["job_dir"] = str(wd)
        db_paths = results_evidence.result_db_paths(wd)
        out["result_db_paths"] = [str(p) for p in db_paths]
        out["result_db_count"] = len(db_paths)
        out["db_census"] = [_db_table_census(p) for p in db_paths]

    volumes = run_result.get("movement_volumes")
    if isinstance(volumes, dict):
        by_node: dict[str, dict[str, Any]] = {}
        for object_id, row in volumes.items():
            if not isinstance(row, dict):
                continue
            node = str(row.get("node"))
            bucket = by_node.setdefault(node, {"movements": 0, "aggregate_row": None, "total": 0.0})
            if str(object_id) == node:
                bucket["aggregate_row"] = row.get("vehs")
            else:
                bucket["movements"] += 1
                if row.get("vehs") is not None:
                    bucket["total"] += float(row["vehs"])
        out["movement_volumes_by_node"] = {
            k: {**v, "total": round(v["total"], 2)} for k, v in sorted(by_node.items())
        }
        out["movement_volume_row_count"] = len(volumes)
    else:
        out["movement_volumes_by_node"] = None
        out["movement_volumes_marker"] = volumes
    return out


def stage_1_baseline(duration_s: int) -> None:
    _banner(f"[STAGE 1] REAL baseline run ({duration_s}s, 1 seed) through the agent job path")
    entry = _stage("1_baseline")
    entry["started_at"] = _now()
    entry["duration_s"] = duration_s
    entry["random_seeds"] = [RUN_SEED]
    started = time.monotonic()

    from services import results_evidence
    from services.agent.state import ArtifactManager

    demo_inpx = Path(_state["demo_inpx"])

    # MEASURED 2026-08-07, first attempt: submitting the RAW uploaded .inpx
    # made Vissim refuse it -- "The network file could not be loaded." (job
    # 966455b0, no .err written). That is the known scenario-management
    # landmine: the client export self-declares <netPara underScenMngm="true">
    # and LoadNet(additive=False) raises for exactly that class
    # (docs/phase2/DECISIONS.md, 2026-07-31). The shipped fix lives in
    # ArtifactManager.clone(), which normalizes the flag IN THE WORKING COPY
    # only -- and a production agent turn always runs its OWN working copy
    # (routers/assistant.py sets tools_run.set_active_run_source(
    # artifact_manager.working_path)). So the baseline runs a working copy
    # too: same code path as the turn, and the same bytes stage 4 will edit.
    # A FRESH work dir every time: ArtifactManager.clone() refuses to overwrite
    # an existing working copy that has no saved version (it would silently
    # discard unsaved edits), which is correct behaviour and not something to
    # work around -- so a re-run gets its own directory rather than reusing one.
    baseline_work = WORK_ROOT / "baseline_work"
    if baseline_work.exists():
        shutil.rmtree(baseline_work, ignore_errors=True)
    baseline_work.mkdir(parents=True, exist_ok=True)
    artifact_manager = ArtifactManager(source_inpx=demo_inpx, work_dir=baseline_work)
    artifact_manager.clone()
    working = artifact_manager.working_path
    entry["baseline_working_copy"] = str(working)
    entry["baseline_working_sha256"] = _sha256(working)
    entry["source_sha256"] = _sha256(demo_inpx)
    entry["clone_normalized_the_source"] = entry["baseline_working_sha256"] != entry["source_sha256"]
    source_text = demo_inpx.read_text(encoding="utf-8", errors="replace")
    working_text = working.read_text(encoding="utf-8", errors="replace")
    entry["under_scen_mngm_source"] = 'underScenMngm="true"' in source_text
    entry["under_scen_mngm_working"] = 'underScenMngm="true"' in working_text
    _state["baseline_working_inpx"] = str(working)
    _log(f"[1.1] Run source = the session working copy {working}")
    _log(f"      ArtifactManager.clone() normalized the copy: {entry['clone_normalized_the_source']}")
    _log(f"      underScenMngm=\"true\" in source={entry['under_scen_mngm_source']} "
         f"in working copy={entry['under_scen_mngm_working']}")
    _log("      trajectory (.fzp) recording is disabled for this probe via")
    _log(f"      AGENT_RUN_TRAJECTORY_MAX_S={os.environ.get('AGENT_RUN_TRAJECTORY_MAX_S')!r}")
    _save_state()

    result = _run_through_agent_job_path(working, duration_s, "baseline")
    envelope = result["envelope"]
    entry["dispatch_elapsed_s"] = result["elapsed_s"]
    entry["envelope_status"] = envelope.get("status")
    if envelope.get("status") != "ok":
        entry["envelope"] = envelope
        entry["ok"] = False
        _save_state()
        raise SystemExit(f"Baseline run FAILED: {json.dumps(envelope, ensure_ascii=False)[:2000]}")

    described = _describe_run(envelope)
    entry.update(described)
    _state["baseline"] = {
        "job_id": described["job_id"],
        "scenario_result_id": described.get("scenario_result_id"),
    }

    run_result = (envelope.get("data") or {}).get("run_result") or {}
    _state["_baseline_run_result"] = run_result

    # MEASURED 2026-08-07: `dataset_revision` (services/datasets.py) folds the
    # observed workbook into the SAME revision string as the network --
    # `...|real_counts.xlsx:missing` becomes `...|real_counts.xlsx:<stamp>` the
    # moment a workbook is uploaded. `get_runtime_result_dir_for_scenario`
    # compares that whole string, so uploading observed counts retroactively
    # makes every EARLIER run unresolvable: the reality check answered
    # RUN_NOT_FOUND for a job that had completed 90 seconds before. Observed
    # counts cannot change a run's results, so this is over-invalidation.
    #
    # Consequence for this probe (recorded, not worked around): the run that
    # DESIGNS the workbook and the run that is GRADED must be separate, with
    # the workbook upload in between. Both use the same seed, the same
    # duration and the same network, so they are expected to be identical --
    # which is asserted rather than assumed, right here.
    by_node = {k: v["total"] for k, v in (described.get("movement_volumes_by_node") or {}).items()}
    previous = _state.get("_design_run_by_node")
    if previous is None:
        _state["_design_run_by_node"] = by_node
        entry["role"] = "design run (the workbook is derived from this one)"
    else:
        entry["role"] = "graded run (post-workbook-upload, must reproduce the design run)"
        entry["design_run_by_node"] = previous
        entry["graded_run_by_node"] = by_node
        entry["reproduces_design_run"] = previous == by_node
        _log(f"[1.1b] Same seed, same duration, same network as the design run.")
        _log(f"       design run per-node totals: {json.dumps(previous, ensure_ascii=False)}")
        _log(f"       this run per-node totals  : {json.dumps(by_node, ensure_ascii=False)}")
        _log(f"       reproduces it exactly     : {entry['reproduces_design_run']}")

    _log(f"[1.2] job_id={described['job_id']} scenario_result_id={described.get('scenario_result_id')}")
    _log(f"      result .db count = {described.get('result_db_count')}")
    for census in described.get("db_census") or []:
        _log(f"      {Path(census['db']).name}: SIMULATIONRUN={census.get('simulationrun')}")
        _log(f"        tables/rows = {json.dumps(census['tables'], ensure_ascii=False)}")

    _banner("[STAGE 1 VERDICT] Do the MOVEMENT tables populate via the AGENT path?")
    descriptor = described.get("evidence_descriptor") or {}
    for table in (
        results_evidence.MOVEMENT_INTERVAL_TABLE,
        results_evidence.MOVEMENT_INTERVAL_CLASS_TABLE,
    ):
        state = descriptor.get(table)
        _log(f"  {table}: {state}")
        entry.setdefault("movement_table_verdict", {})[table] = state
    for census in described.get("db_census") or []:
        for table in (
            "MOVEMENT_EvaluationTimeInterval",
            "MOVEMENT_EvaluationTimeIntervalClass",
        ):
            _log(f"  {table} ARG_TIMEINTERVAL: {census.get(f'{table}__ARG_TIMEINTERVAL')}")
            _log(f"  {table} ARG_VEHICLECLASS: {census.get(f'{table}__ARG_VEHICLECLASS')}")
    _log(f"  evaluation_interval_s reported by U1: {described.get('evaluation_interval_s')}")
    _log(f"  movement rows in run_result: {described.get('movement_volume_row_count')}")
    _log(f"  per-node totals: {json.dumps(described.get('movement_volumes_by_node'), ensure_ascii=False)}")

    class_state = descriptor.get(results_evidence.MOVEMENT_INTERVAL_CLASS_TABLE)
    entry["movement_class_present"] = bool(results_evidence.is_present(class_state))
    if not entry["movement_class_present"]:
        _log("")
        _log("  !! MAJOR FINDING: the movement class table did NOT populate via the agent")
        _log("     path. Everything measured above is captured; the loop cannot continue")
        _log("     honestly without a modelled side, so the probe stops here.")
        entry["ok"] = False
        entry["elapsed_s"] = round(time.monotonic() - started, 2)
        _save_state()
        raise SystemExit("STAGE 1 REFUSAL: no movement evidence via the agent run path.")

    entry["elapsed_s"] = round(time.monotonic() - started, 2)
    entry["ok"] = True
    _save_state()


# ---------------------------------------------------------------------------
# STAGE 2 -- the designed synthetic observed workbook
# ---------------------------------------------------------------------------


def _build_demo_workbook(
    demand_rows: dict[str, float],
    refusal_rows: dict[str, float],
    factor: float,
    design: dict[str, Any],
) -> bytes:
    """The observed side, in the exact layout ``load_base_workbook`` accepts.

    Counts columns are the shipped template's own
    (``validation.create_base_workbook_template``): SimRun / TimeInt / Period /
    Movement / Direction / QLen / QLenMax / Vehs(All) / Observed. ``Direction``
    carries the run's OWN movement OBJECT_ID so every observed number joins
    back to the modelled row it was derived from; ``Vehs(All)`` carries that
    modelled row verbatim (the reality check IGNORES this column by design --
    it re-reads the modelled side from the run itself -- but it makes the
    derivation auditable by eye).

    A ``TravelTimes`` sheet is required: the loader emits a warning without one
    and ``POST /api/validation/base-workbook`` refuses to persist a workbook
    that warns. It is filled with the shipped template's own sample rows,
    relabelled ``DEMO 합성`` -- unused by the reality check, and marked so.
    A ``DEMO_NOTES`` sheet (ignored by the loader) states the synthesis.
    """
    import openpyxl
    from services import validation

    wb = openpyxl.load_workbook(io.BytesIO(validation.create_base_workbook_template()))

    wb.remove(wb["Counts"])
    counts = wb.create_sheet("Counts", 0)
    counts.append(
        ["SimRun", "TimeInt", "Period", "Movement", "Direction", "QLen", "QLenMax", "Vehs(All)", "Observed"]
    )

    time_int = design["time_int"]

    def _emit(node_id: str, label: str, rows: dict[str, float], scale: float) -> dict[str, Any]:
        observed_by_id: dict[str, int] = {}
        for object_id, modelled in sorted(rows.items()):
            observed = int(round(modelled * scale))
            observed_by_id[object_id] = observed
            counts.append(
                ["Average", time_int, "AM", label, object_id, None, None, modelled, observed]
            )
        total_observed = sum(observed_by_id.values())
        total_modelled = round(sum(rows.values()), 2)
        counts.append(
            ["Average", time_int, "AM", label, "Total", None, None, total_modelled, total_observed]
        )
        counts.append([])
        return {
            "node_id": node_id,
            "label": label,
            "scale": scale,
            "movement_n": len(rows),
            "modelled_total": total_modelled,
            "observed_total": total_observed,
            "per_movement": {
                k: {"modelled": rows[k], "observed": observed_by_id[k]} for k in sorted(rows)
            },
        }

    design["demand"] = _emit(
        DEMAND_NODE, f"{DEMAND_NODE}: DEMO 합성 실측 노드{DEMAND_NODE}", demand_rows, factor
    )
    design["refusal"] = _emit(
        REFUSAL_NODE, f"{REFUSAL_NODE}: DEMO 합성 실측 노드{REFUSAL_NODE}", refusal_rows, 1.0
    )

    # TravelTimes: relabel the template's sample measurement names so nobody
    # can read them as field data.
    travel = wb["TravelTimes"]
    for row in travel.iter_rows():
        cell = row[2] if len(row) > 2 else None
        if cell is not None and isinstance(cell.value, str) and ":" in cell.value:
            if cell.value.strip().lower() != "vehicletraveltimemeasurement":
                cell.value = f"DEMO 합성 {cell.value}"

    notes = wb.create_sheet("DEMO_NOTES")
    for line in [
        ["이 워크북은 합성 데모 데이터입니다 / THIS WORKBOOK IS SYNTHETIC DEMO DATA"],
        [],
        [f"생성 시각(UTC) / generated at: {_now()}"],
        [f"생성 도구 / generated by: backend/tools/calibration_loop_live_check.py"],
        [f"기준 실행 / derived from run: job {design.get('source_job_id')}"],
        [f"기준 .db / source result db: {design.get('source_db')}"],
        [],
        ["이 머신에는 이 네트워크의 실제 실측 교통량 자료가 없습니다(2026-08-06 확인)."],
        ["No real observed count data for this network exists on this machine (measured 2026-08-06)."],
        ["따라서 관측값은 위 실행의 자체 movement 교통량에서 유도했고, 편차는 의도적으로 설계했습니다."],
        ["Observed values are DERIVED from that run's own movement volumes; the deviations are designed."],
        [],
        [f"노드 {DEMAND_NODE} (수요 제안 노드 / demand-proposal node): 관측 = 모델 x {factor:.6f}"],
        [
            f"  목표 GEH {TARGET_BEFORE_GEH} 를 노드 총량에서 달성하도록 배율을 역산 "
            f"(modelled_total={design['demand']['modelled_total']}, observed_total={design['demand']['observed_total']})"
        ],
        [
            f"  노드 {DEMAND_NODE} 은 차량입력 {DEMAND_INPUT_NO}(link 617, Murdoch Dr_NB)가 "
            f"도달 가능 수요의 약 {DEMAND_INPUT_SHARE*100:.1f}% 를 차지 -> "
            "fixes.ASSUME_INPUT_DRIVES_NODE_TOTAL 에 가장 근접한 노드"
        ],
        [f"노드 {REFUSAL_NODE} (거부 노드 / refusal node): 관측 = 모델 (반올림) -> GEH ~ 0"],
        [
            f"  링크 617 에서 전진 도달 가능한 노드는 3,4,8 뿐 -> 노드 {REFUSAL_NODE} 은 "
            "이번 수정의 대조군 / control"
        ],
        [],
        ["Direction 열 = Vissim movement OBJECT_ID (실행 결과와 1:1 조인)"],
        ["Vehs(All) 열 = 해당 실행의 모델 값 (reality check 는 이 열을 사용하지 않음)"],
        ["TravelTimes 시트 = 로더 통과용 템플릿 샘플. reality check 와 무관."],
    ]:
        notes.append(line)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def stage_2_workbook(client) -> None:
    _banner("[STAGE 2] The observed side -- designed synthetic, honestly labelled")
    entry = _stage("2_workbook")
    entry["started_at"] = _now()
    started = time.monotonic()

    run_result = _state["_baseline_run_result"]
    baseline = _stage("1_baseline")

    demand_rows = _movement_rows(run_result, DEMAND_NODE)
    refusal_rows = _movement_rows(run_result, REFUSAL_NODE)
    if not demand_rows or not refusal_rows:
        entry["ok"] = False
        entry["demand_movement_n"] = len(demand_rows)
        entry["refusal_movement_n"] = len(refusal_rows)
        _save_state()
        raise SystemExit(
            f"STAGE 2 REFUSAL: node {DEMAND_NODE} has {len(demand_rows)} movement rows and node "
            f"{REFUSAL_NODE} has {len(refusal_rows)} in this run -- cannot design a deviation "
            "against an absent modelled side."
        )

    demand_modelled = sum(demand_rows.values())
    target_observed = _observed_total_for_geh(demand_modelled, TARGET_BEFORE_GEH)
    factor = target_observed / demand_modelled

    _log(f"[2.1] node {DEMAND_NODE}: {len(demand_rows)} movements, modelled total {demand_modelled:.2f} veh")
    _log(f"      solving 2(O-M)^2 = G^2(M+O) for G={TARGET_BEFORE_GEH}: O={target_observed:.2f}")
    _log(f"      => per-movement scale factor f = O/M = {factor:.6f}")
    _log(f"[2.2] node {REFUSAL_NODE}: {len(refusal_rows)} movements, modelled total "
         f"{sum(refusal_rows.values()):.2f} veh -- observed = modelled (rounded), f = 1.0")

    design: dict[str, Any] = {
        "time_int": f"0-{_stage('1_baseline')['duration_s']}",
        "target_before_geh": TARGET_BEFORE_GEH,
        "solved_observed_total": round(target_observed, 2),
        "factor": factor,
        "source_job_id": baseline.get("job_id"),
        "source_db": (baseline.get("result_db_paths") or [None])[0],
    }
    content = _build_demo_workbook(demand_rows, refusal_rows, factor, design)

    # The arithmetic that ACTUALLY lands (per-movement rounding shifts the total)
    for key in ("demand", "refusal"):
        block = design[key]
        block["geh_designed"] = _geh(block["modelled_total"], block["observed_total"])
    entry["design"] = design
    _log(f"[2.3] node {DEMAND_NODE} designed: observed {design['demand']['observed_total']} vs "
         f"modelled {design['demand']['modelled_total']} -> GEH {design['demand']['geh_designed']}")
    _log(f"      node {REFUSAL_NODE} designed: observed {design['refusal']['observed_total']} vs "
         f"modelled {design['refusal']['modelled_total']} -> GEH {design['refusal']['geh_designed']}")

    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    local_copy = ARTIFACT_DIR / WORKBOOK_FILENAME
    local_copy.write_bytes(content)
    entry["workbook_local_copy"] = str(local_copy)
    entry["workbook_bytes"] = len(content)

    from urllib.parse import quote

    response = client.post(
        f"/api/validation/base-workbook?filename={quote(WORKBOOK_FILENAME)}",
        content=content,
        headers=_state["headers"],
    )
    entry["upload_status"] = response.status_code
    if response.status_code != 200:
        entry["upload_body"] = response.text[:4000]
        entry["ok"] = False
        _save_state()
        raise SystemExit(f"Workbook upload FAILED: {response.status_code} {response.text[:2000]}")
    body = response.json()
    entry["upload_response"] = {
        k: body.get(k)
        for k in ("filename", "path", "exists", "warnings", "periods", "sheets", "pending_mapping")
    }
    _log(f"[2.4] Uploaded through POST /api/validation/base-workbook -> {body.get('path')}")
    _log(f"      warnings={body.get('warnings')} periods={body.get('periods')} "
         f"pending_mapping={body.get('pending_mapping')!r}")
    if body.get("pending_mapping"):
        entry["ok"] = False
        _save_state()
        raise SystemExit("Workbook did NOT conform -- it was staged, not persisted. Stopping.")

    entry["elapsed_s"] = round(time.monotonic() - started, 2)
    entry["ok"] = True
    _save_state()


# ---------------------------------------------------------------------------
# STAGE 3 -- the reality check, through the registry dispatch
# ---------------------------------------------------------------------------


def _reality_check(scenario_id: str, current_volume: float) -> dict[str, Any]:
    import services.agent.tools_metrics  # noqa: F401 - registers run_reality_check
    from services.agent.toolkit import default_registry, dispatch

    envelope = dispatch(
        "run_reality_check",
        {
            "scenario_id": scenario_id,
            "period": "AM",
            "targets": [
                {
                    "node_id": DEMAND_NODE,
                    "vehicle_input_no": DEMAND_INPUT_NO,
                    "vehicle_input_current_volume": current_volume,
                    "interval_no": 1,
                }
            ],
        },
        registry=default_registry,
    )
    if envelope.get("status") != "ok":
        raise SystemExit(f"run_reality_check FAILED: {json.dumps(envelope, ensure_ascii=False)[:2000]}")
    return envelope["data"]["reality_check"]


def _node_entry(report: dict[str, Any], node_id: str) -> dict[str, Any] | None:
    for node in report.get("nodes") or []:
        if str(node.get("node_id")) == str(node_id):
            return node
    return None


def stage_3_reality_check() -> None:
    _banner("[STAGE 3] Reality check -- dispatch('run_reality_check') against the baseline run")
    entry = _stage("3_reality_check")
    entry["started_at"] = _now()
    started = time.monotonic()

    demo_inpx = Path(_state["demo_inpx"])
    vi = _shadow_vehicle_input(demo_inpx, DEMAND_INPUT_NO)
    if vi is None:
        raise SystemExit(f"Vehicle input {DEMAND_INPUT_NO} not found in {demo_inpx}")
    current_volume = float(vi["volume"])
    entry["target_vehicle_input"] = {
        "no": vi.get("no"),
        "name": vi.get("name"),
        "link": vi.get("link"),
        "interval_1_volume": current_volume,
        "interval_count": vi.get("interval_count"),
    }
    _state["demand_input_before_volume"] = current_volume
    _log(f"[3.1] Target vehicle input {DEMAND_INPUT_NO} ({vi.get('name')}) on link {vi.get('link')}: "
         f"interval-1 volume = {current_volume} veh/h ({vi.get('interval_count')} intervals)")

    scenario_id = _state["baseline"]["scenario_result_id"]
    _log(f"[3.2] scenario_id = {scenario_id} (the baseline job's own runtime scenario)")
    report = _reality_check(scenario_id, current_volume)
    _state["_before_report"] = report

    entry["status"] = report.get("status")
    entry["reason_code"] = report.get("reason_code")
    entry["geh_threshold"] = report.get("geh_threshold")
    entry["coverage"] = report.get("coverage")
    entry["workbook_warnings"] = report.get("workbook_warnings")
    entry["proposal_node_ids"] = [n["node_id"] for n in report.get("proposals") or []]
    entry["refusal_node_ids"] = [n["node_id"] for n in report.get("refusals") or []]
    _log(f"      status={report.get('status')} reason={report.get('reason_code')} "
         f"threshold={report.get('geh_threshold')}")
    _log(f"      proposals={entry['proposal_node_ids']} refusals={entry['refusal_node_ids']}")

    demand_node = _node_entry(report, DEMAND_NODE)
    refusal_node = _node_entry(report, REFUSAL_NODE)
    if demand_node is None or refusal_node is None:
        entry["ok"] = False
        _save_state()
        raise SystemExit("Reality check did not grade both designed nodes.")

    proposal = demand_node["outcome"]
    refusal = refusal_node["outcome"]
    entry["demand_node"] = demand_node
    entry["refusal_node"] = refusal_node
    _state["_proposal"] = proposal

    _banner("[STAGE 3] THE PROPOSAL (verbatim)")
    _log(f"  node            : {demand_node['node_id']} ({demand_node.get('node_name')})")
    _log(f"  observed_total  : {demand_node.get('observed_total')}")
    _log(f"  modelled_total  : {demand_node.get('modelled_total')}")
    _log(f"  node_total_geh  : {demand_node.get('node_total_geh')}")
    _log(f"  outcome/scope   : {proposal.get('outcome')} / {proposal.get('scope')}")
    _log(f"  verb            : {proposal.get('verb')}")
    _log(f"  params          : {json.dumps(proposal.get('params'), ensure_ascii=False)}")
    _log(f"  confidence      : {proposal.get('confidence')}")
    _log(f"  assumptions     : {proposal.get('assumptions')}")
    _log(f"  evidence        : {json.dumps(proposal.get('evidence'), ensure_ascii=False)}")
    for lang in ("ko", "en", "ja"):
        text = (proposal.get("rationale") or {}).get(lang, "")
        _log(f"  rationale[{lang}] ({len(text)} chars): {text}")

    _banner("[STAGE 3] THE REFUSAL (verbatim)")
    _log(f"  node            : {refusal_node['node_id']} ({refusal_node.get('node_name')})")
    _log(f"  observed_total  : {refusal_node.get('observed_total')}")
    _log(f"  modelled_total  : {refusal_node.get('modelled_total')}")
    _log(f"  node_total_geh  : {refusal_node.get('node_total_geh')}")
    _log(f"  outcome         : {refusal.get('outcome')}  reason_code={refusal.get('reason_code')}")
    _log(f"  verb/params     : {refusal.get('verb')!r} / {refusal.get('params')!r}")
    _log(f"  evidence        : {json.dumps(refusal.get('evidence'), ensure_ascii=False)}")
    for lang in ("ko", "en", "ja"):
        _log(f"  rationale[{lang}]: {(refusal.get('rationale') or {}).get(lang, '')}")

    if proposal.get("outcome") != "PROPOSAL" or proposal.get("verb") != "set_vehicle_input_volume":
        entry["ok"] = False
        _save_state()
        raise SystemExit("The demand node did not produce a demand-level proposal. Stopping.")

    # The prediction, stated BEFORE the re-run (stage 5 measures it).
    modelled = float(demand_node["modelled_total"])
    observed = float(demand_node["observed_total"])
    proposed = float(proposal["params"]["volume"])
    ratio = observed / modelled
    predicted_modelled = modelled * (1.0 + DEMAND_INPUT_SHARE * (ratio - 1.0))
    prediction = {
        "input_share_of_node_feed": DEMAND_INPUT_SHARE,
        "requested_demand_ratio": round(ratio, 6),
        "before_geh": demand_node.get("node_total_geh"),
        "predicted_after_modelled_total": round(predicted_modelled, 2),
        "predicted_after_geh": _geh(predicted_modelled, observed),
        "direction": "DOWN (toward reconciliation)",
        "why_not_zero": (
            f"the proposal scales ONE vehicle input ({DEMAND_INPUT_NO}) by {ratio:.4f}, but that "
            f"input supplies only ~{DEMAND_INPUT_SHARE*100:.1f}% of what can reach node "
            f"{DEMAND_NODE}; the arithmetic assumes 100% "
            "(fixes.ASSUME_INPUT_DRIVES_NODE_TOTAL), so the node total is expected to move "
            "toward the observed count without fully reaching it."
        ),
        "control_node": REFUSAL_NODE,
        "control_expectation": (
            f"node {REFUSAL_NODE} is not forward-reachable from link 617, so its GEH should stay "
            "below the 5.0 threshold; any movement is Vissim's global RNG stream shifting, not "
            "the edit propagating."
        ),
    }
    entry["prediction"] = prediction
    _banner("[STAGE 3] PREDICTION, recorded BEFORE the edit and the re-run")
    for key, value in prediction.items():
        _log(f"  {key}: {value}")
    _log(f"  proposed volume: {proposed} veh/h (from {_state['demand_input_before_volume']} veh/h)")

    entry["elapsed_s"] = round(time.monotonic() - started, 2)
    entry["ok"] = True
    _save_state()


# ---------------------------------------------------------------------------
# STAGE 4 -- authenticated approval, applied through REAL COM
# ---------------------------------------------------------------------------


class _CannedProvider:
    """The ONE seam that is not live: the planner. Same fixture shape as
    tools/run_gate_live_check.py's and test_calibration_loop_e2e.py's."""

    def __init__(self, queue: list[tuple[dict[str, Any] | None, dict[str, Any]]]) -> None:
        self._queue = list(queue)
        self.calls = 0

    def generate_structured(self, messages, tools, schema=None, *, system=None, max_tokens=None):
        self.calls += 1
        if not self._queue:
            raise AssertionError("canned planner queue exhausted")
        return self._queue.pop(0)


def _action(tool_name: str, *, summary: str, **params: Any):
    params["summary"] = summary
    return {"tool": tool_name, "params": params}, {"mode": "canned", "fallback_rung": 0}


def stage_4_approval(client) -> None:
    _banner("[STAGE 4] Authenticated approval -> REAL edit session -> REAL COM write")
    entry = _stage("4_approval")
    entry["started_at"] = _now()
    started = time.monotonic()

    from routers import assistant as assistant_endpoint
    from routers import auth as auth_router
    from services import audit_trail, auth
    from services.agent.edit_session import clear_active_edit_session
    from services.agent.tools_network import build_shadow_model

    proposal = _state["_proposal"]

    # --- the demo reviewer, in the real app DB -----------------------------
    existing = auth.get_user_by_username(DEMO_REVIEWER_USERNAME)
    if existing is None:
        auth.create_user(
            DEMO_REVIEWER_USERNAME,
            DEMO_REVIEWER_PASSWORD,
            DEMO_REVIEWER_ROLE,
            display_name=DEMO_REVIEWER_DISPLAY,
        )
        _log(f"[4.1] Created demo account {DEMO_REVIEWER_USERNAME!r} role={DEMO_REVIEWER_ROLE}")
    else:
        _log(f"[4.1] Demo account {DEMO_REVIEWER_USERNAME!r} already exists (role={existing['role']})")
    login = client.post(
        "/api/auth/login",
        json={"username": DEMO_REVIEWER_USERNAME, "password": DEMO_REVIEWER_PASSWORD},
        headers=_state["headers"],
    )
    if login.status_code != 200:
        raise SystemExit(f"Login FAILED: {login.status_code} {login.text}")
    token = login.cookies[auth_router.SESSION_COOKIE_NAME]
    user = login.json()["user"]
    entry["reviewer"] = user
    _log(f"[4.2] Real login OK -- {user.get('display_name')} ({user.get('username')} / {user.get('role')})")

    # --- the propose turn (canned planner, real everything else) -----------
    provider = _CannedProvider(
        [
            _action(proposal["verb"], summary="실측 대조 결과에 따른 수요 보정 제안", **proposal["params"]),
            _action(proposal["verb"], summary="승인된 변경을 적용합니다", **proposal["params"]),
            _action("respond", summary="변경을 완료했습니다", answer="차량 입력 볼륨을 조정했습니다."),
        ]
    )
    original_provider = assistant_endpoint._default_provider
    assistant_endpoint._default_provider = lambda: provider
    clear_active_edit_session()
    pids_before_edit = _vissim_pids()
    try:
        stream = client.post(
            "/api/assistant/agent/query/stream",
            json={
                "message": "실측 대조에서 나온 수요 보정 제안을 적용해줘",
                "response_language": "ko",
            },
            headers=_state["headers"],
        )
        if stream.status_code != 200:
            raise SystemExit(f"Propose turn FAILED: {stream.status_code} {stream.text[:2000]}")
        events = _stream_events(stream)
        done = events[-1]
        entry["propose_event_types"] = [e.get("type") for e in events]
        if done.get("type") != "done" or done["response"].get("kind") != "approval_request":
            entry["propose_done"] = done
            entry["ok"] = False
            _save_state()
            raise SystemExit(
                f"Expected an approval_request; got {json.dumps(done, ensure_ascii=False)[:2000]}"
            )
        session_id = done["response"]["session_id"]
        approval_event = next(e for e in events if e.get("type") == "approval_request")
        approval_id = approval_event["approval_id"]
        _state["session_id"] = session_id
        entry["session_id"] = session_id
        entry["approval_id"] = approval_id
        _log(f"[4.3] The loop PAUSED: session={session_id} approval_id={approval_id}")

        from services import workspaces

        with workspaces.bind_workspace(_state["workspace_id"]):
            work_dir = assistant_endpoint._agent_session_work_dir(session_id)
        _state["work_dir"] = str(work_dir)
        entry["work_dir"] = str(work_dir)
        pending_path = work_dir / "pending_approval.json"
        pending = json.loads(pending_path.read_text(encoding="utf-8"))
        entry["pending_action"] = pending["actions"][0]
        entry["pending_params_equal_proposal"] = pending["actions"][0]["params"] == proposal["params"]
        _log(f"[4.4] pending_approval.json action = "
             f"{json.dumps(pending['actions'][0], ensure_ascii=False)}")
        _log(f"      params ARE the proposal's params: {entry['pending_params_equal_proposal']}")

        working_inpx = work_dir / "working.inpx"
        # The comparison that matters: the network this turn is about to edit
        # must be byte-identical to the one the BASELINE RUN was graded on --
        # both are ArtifactManager clones of the same demo dataset, so both
        # carry the same underScenMngm normalization. (It is NOT byte-equal to
        # the raw uploaded .inpx, and asserting that would be asserting the
        # landmine back in.)
        baseline_working = Path(_state["baseline_working_inpx"])
        entry["working_copy_matches_baseline_run_network"] = (
            working_inpx.read_bytes() == baseline_working.read_bytes()
        )
        _log(f"[4.5] working.inpx is byte-identical to the network the baseline run graded: "
             f"{entry['working_copy_matches_baseline_run_network']}")
        before_vi = _shadow_vehicle_input(working_inpx, DEMAND_INPUT_NO)
        entry["working_copy_volume_before"] = before_vi.get("volume") if before_vi else None

        # --- the approve leg: NO _build_edit_session patch. Real COM. ------
        rationale = (proposal.get("rationale") or {}).get("ko") or ""
        max_chars = assistant_endpoint.MAX_APPROVAL_REASON_CHARS
        entry["rationale_ko_chars"] = len(rationale)
        entry["max_approval_reason_chars"] = max_chars
        if len(rationale) <= max_chars:
            reason = rationale
            entry["reason_is_verbatim_rationale"] = True
        else:
            evidence = proposal.get("evidence") or {}
            reason = (
                f"실측 대조 결과 노드 {DEMAND_NODE} 총량 GEH {evidence.get('node_total_geh')} "
                f">= 기준 {evidence.get('geh_threshold')}. 수요 보정: {evidence.get('arithmetic')}"
            )[:max_chars]
            entry["reason_is_verbatim_rationale"] = False
            entry["reason_overflow_note"] = (
                f"the ko rationale is {len(rationale)} chars against a {max_chars}-char cap, so a "
                "compact reason carrying the same GEH numbers and arithmetic was signed instead"
            )
            _log(f"      !! MEASURED CAVEAT: {entry['reason_overflow_note']}")
        entry["approval_reason"] = reason

        _log(f"[4.6] Approving as {DEMO_REVIEWER_USERNAME} -- this applies through the REAL")
        _log("      VissimEditSession (_build_edit_session is NOT patched): real COM, real")
        _log("      SetAttValue, real read-back, real SaveNetAs onto working.inpx.")
        approve_started = time.monotonic()
        approve = client.post(
            "/api/assistant/agent/approve",
            json={
                "session_id": session_id,
                "approval_id": approval_id,
                "approved": True,
                "reason": reason,
            },
            headers={**_state["headers"], "Authorization": f"Bearer {token}"},
        )
        entry["approve_elapsed_s"] = round(time.monotonic() - approve_started, 2)
        entry["approve_status"] = approve.status_code
        _log(f"      approve returned {approve.status_code} in {entry['approve_elapsed_s']}s")
        if approve.status_code != 200:
            entry["approve_body"] = approve.text[:4000]
            entry["ok"] = False
            _save_state()
            raise SystemExit(f"Approve FAILED: {approve.status_code} {approve.text[:2000]}")
        approve_events = _stream_events(approve)
        entry["approve_event_types"] = [e.get("type") for e in approve_events]
        entry["approve_final"] = approve_events[-1].get("response", {}).get("kind")
        _log(f"      final response kind = {entry['approve_final']}")
    finally:
        assistant_endpoint._default_provider = original_provider
        clear_active_edit_session()

    entry["vissim_pids_started_by_edit"] = sorted(_vissim_pids() - pids_before_edit)

    # --- the postwrite evidence -------------------------------------------
    from services import workspaces

    with workspaces.bind_workspace(_state["workspace_id"]):
        records = audit_trail.read_records(work_dir / f"trace_{session_id}.jsonl")
    entry["trace_kinds"] = [r.get("kind") for r in records]
    postwrites = [r for r in records if r.get("kind") == "postwrite"]
    entry["postwrite_records"] = postwrites
    confirms = [
        r["payload"]
        for r in records
        if r.get("kind") == "user_confirm" and (r.get("payload") or {}).get("stage") == "approved"
    ]
    entry["approved_confirm"] = confirms[0] if confirms else None
    dispatches = [r for r in records if r.get("kind") in ("dispatch", "observation", "step")]
    entry["dispatch_records"] = dispatches[:10]

    after_vi = _shadow_vehicle_input(working_inpx, DEMAND_INPUT_NO)
    entry["working_copy_volume_after"] = after_vi.get("volume") if after_vi else None
    entry["written_value"] = proposal["params"]["volume"]
    entry["readback_equals_written"] = (
        entry["working_copy_volume_after"] == proposal["params"]["volume"]
    )
    entry["pending_consumed"] = not (work_dir / "pending_approval.json").exists()
    _state["working_inpx"] = str(working_inpx)

    _banner("[STAGE 4] POSTWRITE -- written vs read back")
    _log(f"  working.inpx                : {working_inpx}")
    _log(f"  vehicle input {DEMAND_INPUT_NO} volume BEFORE : {entry['working_copy_volume_before']} veh/h")
    _log(f"  written value               : {entry['written_value']} veh/h")
    _log(f"  re-parsed shadow model AFTER: {entry['working_copy_volume_after']} veh/h")
    _log(f"  read-back == written        : {entry['readback_equals_written']}")
    _log(f"  pending_approval consumed   : {entry['pending_consumed']}")
    for record in postwrites:
        _log(f"  postwrite trace: {json.dumps(record.get('payload'), ensure_ascii=False)[:1500]}")
    if entry["approved_confirm"]:
        actor = entry["approved_confirm"].get("actor")
        _log(f"  ledger actor  : {json.dumps(actor, ensure_ascii=False)}")
        _log(f"  ledger params : {json.dumps(entry['approved_confirm'].get('params'), ensure_ascii=False)}")
        _log(f"  ledger reason : {entry['approved_confirm'].get('reason')}")

    if not entry["readback_equals_written"]:
        entry["ok"] = False
        _save_state()
        raise SystemExit("The approved write did not land on the working copy. Stopping.")

    entry["elapsed_s"] = round(time.monotonic() - started, 2)
    entry["ok"] = True
    _save_state()


# ---------------------------------------------------------------------------
# STAGE 5 -- the re-run on the EDITED working copy
# ---------------------------------------------------------------------------


def stage_5_rerun(duration_s: int) -> None:
    _banner(f"[STAGE 5] REAL re-run on the EDITED working copy ({duration_s}s, seed {RUN_SEED})")
    entry = _stage("5_rerun")
    entry["started_at"] = _now()
    started = time.monotonic()

    working_inpx = Path(_state["working_inpx"])
    _log(f"[5.1] Run source = {working_inpx} (carries the approved edit)")
    result = _run_through_agent_job_path(working_inpx, duration_s, "re-run")
    envelope = result["envelope"]
    entry["dispatch_elapsed_s"] = result["elapsed_s"]
    entry["envelope_status"] = envelope.get("status")
    if envelope.get("status") != "ok":
        entry["envelope"] = envelope
        entry["ok"] = False
        _save_state()
        raise SystemExit(f"Re-run FAILED: {json.dumps(envelope, ensure_ascii=False)[:2000]}")

    described = _describe_run(envelope)
    entry.update(described)
    _state["rerun"] = {
        "job_id": described["job_id"],
        "scenario_result_id": described.get("scenario_result_id"),
    }
    _log(f"[5.2] job_id={described['job_id']} scenario_result_id={described.get('scenario_result_id')}")
    _log(f"      per-node totals: {json.dumps(described.get('movement_volumes_by_node'), ensure_ascii=False)}")

    # The AFTER reality check: the SAME observed workbook, the SAME targets,
    # but the target's current volume is now the value that was approved --
    # anything else would grade the new run against a volume nobody wrote.
    new_volume = _shadow_vehicle_input(working_inpx, DEMAND_INPUT_NO)["volume"]
    entry["target_current_volume_after"] = new_volume
    after = _reality_check(described["scenario_result_id"], float(new_volume))
    _state["_after_report"] = after

    before = _state["_before_report"]
    table: list[dict[str, Any]] = []
    for node_id in (DEMAND_NODE, REFUSAL_NODE):
        b = _node_entry(before, node_id) or {}
        a = _node_entry(after, node_id) or {}
        row = {
            "node_id": node_id,
            "observed_total": b.get("observed_total"),
            "modelled_total_before": b.get("modelled_total"),
            "modelled_total_after": a.get("modelled_total"),
            "geh_before": b.get("node_total_geh"),
            "geh_after": a.get("node_total_geh"),
            "outcome_before": (b.get("outcome") or {}).get("outcome"),
            "outcome_after": (a.get("outcome") or {}).get("outcome"),
            "reason_code_after": (a.get("outcome") or {}).get("reason_code"),
        }
        if row["geh_before"] is not None and row["geh_after"] is not None:
            row["geh_delta"] = round(row["geh_after"] - row["geh_before"], 3)
            row["moved_toward_reconciliation"] = row["geh_after"] < row["geh_before"]
        table.append(row)
    entry["before_after"] = table
    entry["after_proposal_node_ids"] = [n["node_id"] for n in after.get("proposals") or []]
    entry["after_refusal_node_ids"] = [n["node_id"] for n in after.get("refusals") or []]

    prediction = _stage("3_reality_check").get("prediction") or {}
    demand_row = table[0]
    entry["prediction_vs_measured"] = {
        "predicted_after_modelled_total": prediction.get("predicted_after_modelled_total"),
        "measured_after_modelled_total": demand_row["modelled_total_after"],
        "predicted_after_geh": prediction.get("predicted_after_geh"),
        "measured_after_geh": demand_row["geh_after"],
        "predicted_direction": prediction.get("direction"),
        "measured_direction": (
            "DOWN (toward reconciliation)"
            if demand_row.get("moved_toward_reconciliation")
            else "NOT toward reconciliation"
        ),
    }

    _banner("[STAGE 5] BEFORE / AFTER")
    _log(f"  {'node':>5} {'observed':>10} {'model_bef':>10} {'model_aft':>10} {'GEH_bef':>8} {'GEH_aft':>8} {'delta':>8}")
    for row in table:
        _log(
            f"  {row['node_id']:>5} {row['observed_total']!s:>10} {row['modelled_total_before']!s:>10} "
            f"{row['modelled_total_after']!s:>10} {row['geh_before']!s:>8} {row['geh_after']!s:>8} "
            f"{row.get('geh_delta')!s:>8}"
        )
    _log("")
    _log(f"  PREDICTED (stated at stage 3): after-modelled {prediction.get('predicted_after_modelled_total')}, "
         f"after-GEH {prediction.get('predicted_after_geh')}, {prediction.get('direction')}")
    _log(f"  MEASURED                     : after-modelled {demand_row['modelled_total_after']}, "
         f"after-GEH {demand_row['geh_after']}, {entry['prediction_vs_measured']['measured_direction']}")
    _log(f"  control node {REFUSAL_NODE}: GEH {table[1]['geh_before']} -> {table[1]['geh_after']} "
         f"(outcome {table[1]['outcome_before']} -> {table[1]['outcome_after']})")

    entry["elapsed_s"] = round(time.monotonic() - started, 2)
    entry["ok"] = True
    _save_state()


# ---------------------------------------------------------------------------
# STAGE 6 -- the artifact
# ---------------------------------------------------------------------------


def stage_6_artifact(client) -> None:
    _banner("[STAGE 6] The artifact -- audit DOCX + reality-check reports + GEH table")
    entry = _stage("6_artifact")
    entry["started_at"] = _now()
    started = time.monotonic()
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)

    session_id = _state["session_id"]
    summary = client.get(f"/api/agent/audit/{session_id}/summary", headers=_state["headers"])
    entry["summary_status"] = summary.status_code
    entry["summary"] = summary.json() if summary.status_code == 200 else summary.text[:2000]
    _log(f"[6.1] GET summary -> {summary.status_code} {json.dumps(entry['summary'], ensure_ascii=False)}")

    docx_paths = {}
    for language in ("ko", "en"):
        response = client.get(
            f"/api/agent/audit/{session_id}/export.docx",
            params={"language": language},
            headers=_state["headers"],
        )
        if response.status_code != 200:
            entry[f"docx_{language}_status"] = response.status_code
            continue
        path = ARTIFACT_DIR / f"DEMO_감사문서_{session_id}_{language}.docx"
        path.write_bytes(response.content)
        docx_paths[language] = str(path)
        _log(f"[6.2] {language}: {len(response.content)} bytes -> {path}")
    entry["docx_paths"] = docx_paths

    if "ko" not in docx_paths:
        entry["ok"] = False
        _save_state()
        raise SystemExit("The ko audit DOCX could not be exported -- nothing to assert against.")

    from docx import Document

    doc = Document(io.BytesIO(Path(docx_paths["ko"]).read_bytes()))
    tables = [[[cell.text for cell in row.cells] for row in table.rows] for table in doc.tables]
    paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
    entry["docx_table_count"] = len(tables)
    entry["docx_tables"] = tables
    entry["docx_paragraphs"] = paragraphs

    _banner("[STAGE 6] AUDIT DOCX (ko) -- assertion excerpts")
    for index, rows in enumerate(tables):
        _log(f"  table {index}:")
        for row in rows:
            _log(f"    {row}")
    for text in paragraphs:
        _log(f"  para: {text}")

    proposal = _state["_proposal"]
    approval = _stage("4_approval")
    change_rows = tables[0][1:] if tables else []
    entry["change_row_count"] = len(change_rows)
    if change_rows:
        row = change_rows[0]
        entry["change_row"] = row
        joined = "\n".join(
            [cell for tbl in tables for tbl_row in tbl for cell in tbl_row] + paragraphs
        )
        entry["assertions"] = {
            "one_change_row": len(change_rows) == 1,
            "target_names_input_and_interval": row[2],
            "before_after_cell": row[4],
            "tool_cell": row[5],
            "verification_cell": row[6],
            "approver_cell": row[7],
            "approver_names_the_reviewer": DEMO_REVIEWER_DISPLAY in row[7],
            "rationale_cell": row[8],
            "rationale_carries_geh": "GEH" in row[8],
            "rationale_equals_signed_reason": row[8] == approval.get("approval_reason"),
            "refusal_node_absent_from_document": f"노드 {REFUSAL_NODE}" not in joined,
            "written_volume_in_document": str(proposal["params"]["volume"]) in joined,
        }
        _log("")
        for key, value in entry["assertions"].items():
            _log(f"  ASSERT {key}: {value!r}")

    before_path = ARTIFACT_DIR / "reality_check_BEFORE.json"
    after_path = ARTIFACT_DIR / "reality_check_AFTER.json"
    before_path.write_text(
        json.dumps(_state["_before_report"], ensure_ascii=False, indent=2), encoding="utf-8"
    )
    after_path.write_text(
        json.dumps(_state["_after_report"], ensure_ascii=False, indent=2), encoding="utf-8"
    )
    entry["reality_check_before"] = str(before_path)
    entry["reality_check_after"] = str(after_path)
    _log(f"[6.3] Reality-check reports written: {before_path.name}, {after_path.name}")
    _log("      (there is no reality-check report EXPORTER in the product -- measured: the tool's")
    _log("       return value is the only egress -- so these are the raw envelopes, verbatim.)")

    geh_path = ARTIFACT_DIR / "before_after_geh.json"
    geh_table = {
        "duration_s": _stage("1_baseline").get("duration_s"),
        "random_seed": RUN_SEED,
        "baseline_job": _state["baseline"],
        "rerun_job": _state["rerun"],
        "applied_change": {
            "verb": proposal["verb"],
            "params": proposal["params"],
            "volume_before": _state["demand_input_before_volume"],
            "volume_after": approval.get("working_copy_volume_after"),
            "approver": approval.get("reviewer"),
            "reason": approval.get("approval_reason"),
        },
        "prediction": _stage("3_reality_check").get("prediction"),
        "prediction_vs_measured": _stage("5_rerun").get("prediction_vs_measured"),
        "rows": _stage("5_rerun").get("before_after"),
    }
    geh_path.write_text(json.dumps(geh_table, ensure_ascii=False, indent=2), encoding="utf-8")
    entry["before_after_geh"] = str(geh_path)
    _log(f"[6.4] Before/after GEH table -> {geh_path}")

    entry["elapsed_s"] = round(time.monotonic() - started, 2)
    entry["ok"] = True
    _save_state()


# ---------------------------------------------------------------------------
# STAGE 7 -- teardown
# ---------------------------------------------------------------------------


def stage_7_teardown(pids_before: set[int]) -> None:
    _banner("[STAGE 7] Teardown -- process scan, artifact inventory")
    entry = _stage("7_teardown")
    entry["started_at"] = _now()

    time.sleep(2.0)
    now = _vissim_pids()
    ours = sorted(now - pids_before)
    entry["vissim_pids_before_probe"] = sorted(pids_before)
    entry["vissim_pids_at_teardown"] = sorted(now)
    entry["orphans_started_by_us"] = ours
    _log(f"  Vissim PIDs present BEFORE this probe (never touched): {sorted(pids_before) or 'none'}")
    _log(f"  Vissim PIDs present now                              : {sorted(now) or 'none'}")
    if ours:
        killed = _kill_new_vissim(pids_before)
        entry["killed"] = killed
        time.sleep(1.0)
        remaining = sorted(_vissim_pids() - pids_before)
        entry["orphans_after_cleanup"] = remaining
        _log(f"  Killed OUR leftovers: {killed}; still alive: {remaining or 'none'}")
    else:
        entry["killed"] = []
        entry["orphans_after_cleanup"] = []
        _log("  Zero orphans: every Vissim this probe started exited on its own.")

    inventory = {
        "demo_workspace": {
            "id": _state.get("workspace_id"),
            "name": DEMO_WORKSPACE_NAME,
            "dir": str(REPO_ROOT / "runtime" / "workspaces" / str(_state.get("workspace_id"))),
            "disposition": "KEPT -- this workspace and its runs ARE the demo, not litter.",
        },
        "runs": {"baseline": _state.get("baseline"), "rerun": _state.get("rerun")},
        "agent_session": {"session_id": _state.get("session_id"), "work_dir": _state.get("work_dir")},
        "demo_account": {
            "username": DEMO_REVIEWER_USERNAME,
            "role": DEMO_REVIEWER_ROLE,
            "db": str(REPO_ROOT / "runtime" / "db" / "app.db"),
            "disposition": "KEPT -- the DOCX names this reviewer; deleting it would orphan the signature.",
        },
        "evidence_dir": str(WORK_ROOT),
        "artifacts": sorted(str(p) for p in ARTIFACT_DIR.glob("*")) if ARTIFACT_DIR.is_dir() else [],
    }
    entry["inventory"] = inventory
    _log("")
    _log("  ARTIFACTS LEFT BEHIND:")
    _log(json.dumps(inventory, ensure_ascii=False, indent=2))
    entry["ok"] = True
    _save_state()


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--duration", type=int, default=DEFAULT_DURATION_S)
    parser.add_argument("--stages", default="0,1,2,3,4,5,6,7")
    args = parser.parse_args()

    stages = {int(s) for s in args.stages.split(",") if s.strip()}

    WORK_ROOT.mkdir(parents=True, exist_ok=True)
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    _log("")
    _log("#" * 78)
    _log(f"# CALIBRATION-LOOP LIVE CHECK  {_now()}")
    _log(f"# duration={args.duration}s seed={RUN_SEED} stages={sorted(stages)}")
    _log("#" * 78)

    # Bounded runs: never write gigabytes of vehicle records for a probe.
    os.environ.setdefault("AGENT_RUN_TRAJECTORY_MAX_S", "1")

    _state.update(_load_state())
    _state["run_started_at"] = _now()
    _state["duration_s"] = args.duration
    _state["random_seed"] = RUN_SEED
    _state["stages_requested"] = sorted(stages)

    pids_before = _vissim_pids()
    _state["vissim_pids_before_probe"] = sorted(pids_before)
    _log(f"Vissim PIDs already running (NEVER touched): {sorted(pids_before) or 'none'}")
    _save_state()

    import main as app_main
    from fastapi.testclient import TestClient

    failure: str | None = None
    try:
        from services import workspaces

        with TestClient(app_main.app) as client:
            if 0 in stages:
                stage_0_workspace(client)
            # Every direct (non-HTTP) service call below must be scoped to the
            # DEMO project: start_agent_simulation_job stamps the submitting
            # thread's workspace onto the job record, and
            # get_runtime_result_dir_for_scenario refuses a job that belongs to
            # another project -- so a baseline run submitted under the default
            # workspace would be invisible to the reality check.
            if 1 in stages:
                with workspaces.bind_workspace(_state["workspace_id"]):
                    stage_1_baseline(args.duration)
            if 2 in stages:
                stage_2_workbook(client)
            if 3 in stages:
                with workspaces.bind_workspace(_state["workspace_id"]):
                    stage_3_reality_check()
            if 4 in stages:
                stage_4_approval(client)
            if 5 in stages:
                with workspaces.bind_workspace(_state["workspace_id"]):
                    stage_5_rerun(args.duration)
            if 6 in stages:
                stage_6_artifact(client)
    except SystemExit as exc:
        failure = str(exc)
        _log("")
        _log(f"!! STOPPED: {failure}")
    except Exception:
        failure = traceback.format_exc()
        _log("")
        _log(f"!! EXCEPTION:\n{failure}")
    finally:
        _state["failure"] = failure
        _save_state()
        if 7 in stages:
            try:
                stage_7_teardown(pids_before)
            except Exception:
                _log(f"!! teardown failed:\n{traceback.format_exc()}")

    _banner("RESULT")
    for key, value in sorted((_state.get("stages") or {}).items()):
        _log(f"  {key}: ok={value.get('ok')} elapsed={value.get('elapsed_s')}s")
    _log(f"  overall: {'FAILED -- ' + failure.splitlines()[0] if failure else 'COMPLETE'}")
    _log(f"  evidence: {STATE_PATH}")
    _save_state()
    return 1 if failure else 0


if __name__ == "__main__":
    # A CLI run has no HTTP request to scope it to a project, and
    # services/workspaces.py refuses to guess one. The demo workspace is created
    # inside stage 0 through the HTTP route (which binds its own scope); this
    # outer binding only covers the app-startup path.
    from services.workspaces import bind_workspace, default_workspace_id

    with bind_workspace(default_workspace_id()):
        raise SystemExit(main())
