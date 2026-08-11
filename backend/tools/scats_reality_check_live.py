"""THE REAL COMPARISON: the consultant model against the REAL WA SCATS counts.

``tools/calibration_loop_live_check.py`` proves the calibration CHAIN end to
end, but its observed side is synthetic by measurement — derived from the
baseline run's own movement volumes with a designed deviation, because on
2026-08-06 no real observed data for this network existed on this machine. That
is no longer true. ``client-data/Traffic signal data.zip`` holds three real Main
Roads WA SCATS detector-volume workbooks, and commit ``fd2937e`` made them
readable. This script is the thing that had never been done: **a real observed
number compared against a real model run.**

What is real here, and what is not
----------------------------------
REAL: the counts (the client's own .xlsx workbooks, read by the shipped
``scats_wa`` adapter through the shipped quality gate), the site -> node join
(``services.site_node_map``, derived from coordinates both sides state), the
observation set (``services.observations.ingest_observation_set``), the model
(the consultant's own ``.inpx``), the run (real COM,
``connect(attach_to_running=False)``, ``RunContinuous``), and the GEH
(``services.agent.tools_metrics.run_reality_check``, registry dispatch).

NOT REAL: nothing. There is no planner in this script and no canned queue —
it never asks an LLM anything, so the seam ``calibration_loop_live_check`` fakes
does not exist here.

WHAT THIS COMPARISON IS, EXACTLY
--------------------------------
**Site total against node total, and nothing finer.** A SCATS detector number is
local to its site and carries no turning movement; lifting detector counts to
movements needs the MRWA Appendix-A LMA/LMB site drawing, which
``adapters.scats_wa.build_turning_counts_from_detector_map`` measured to be
absent from this drop and therefore refuses. So the observed side arrives as
``count_basis="detector_counts"``, ``directions`` empty, and every label the
report renders says so. Do not read a per-movement number out of this run;
there is not one.

Two properties of the observed side that bound what the GEH means, both
measured rather than assumed, and both restated in the final banner:

1. **A site total is the sum of that site's detector volumes.** Whether that
   equals "vehicles entering the junction" depends on the site's detector
   layout, which is exactly what the missing LMA drawing would tell us. A site
   with both stop-line and upstream loops on one lane would double-count it.
   This is the single largest uncertainty in the numbers below and it is NOT
   resolvable from the files in this drop.
2. **The modelled side starts from an empty network.** Vissim fills the network
   over the first minutes of a run, so a run's throughput is biased low against
   a field count of the same length. The model's own declared period is used
   (``simPeriod``, measured below) to keep that fraction as small as the dataset
   allows, and the bias is named rather than corrected by an invented factor.

   It is also **measurable, and measuring it beats arguing about it.** Run this
   script twice at two window lengths (``--label _60min --period-start ...
   --period-minutes 60 --duration 3600`` beside the default 90) and the pair
   separates three effects that a single run reports as one number:

   * *the fill deficit* — modelled throughput is very nearly linear in run
     length, so fitting ``modelled(T) = rate x T - D`` across the two runs gives
     ``D``, the vehicles lost to filling an empty network, as a number rather
     than a worry;
   * *demand-profile shape* — comparing the OBSERVED rate in the short window
     against the observed rate in the long one says how much the field peaks
     WITHIN the modelled period. This model's demand is flat across all six of
     its 15-minute intervals, so any such peaking is something it cannot
     reproduce by construction, and it inflates the short-window error;
   * *the residual* — what is left is the model's actual disagreement with the
     field, which is the only part a calibration edit should ever act on.

   A single-duration GEH cannot tell these apart, and treating the sum of all
   three as a demand error is how a calibrated model gets pushed the wrong way.

THE PERIOD, AND WHY THIS ONE
----------------------------
Declared, never inferred — ``observations.ingest_observation_set`` requires it
for the reason its docstring states. Chosen from the drop by measurement (see
``--period-start``/``--period-minutes`` to override):

* **Tuesday 2026-02-17, 07:15-08:45.** The drop covers Mon 2026-02-16 to Sun
  2026-02-22 in 15-minute bins at all three sites.
* *Why a Tuesday:* Monday and Friday carry atypical weekday profiles, so a
  mid-week day is the conventional neutral choice. Measured constraint on top of
  that convention: Thu 19, Fri 20 and Sun 22 have blank detector cells at the
  smallest site, and a blank is an unobserved interval rather than zero flow
  (the adapter says so and writes no record), so a window containing one would
  make that site's total a partial sum. Tue 17, Wed 18, Mon 16 and Sat 21 have
  none. Tuesday 17 is the first neutral weekday that is also complete.
* *Why 07:15-08:45:* the rolling one-hour AM maximum measured on that day starts
  at 07:30 at two sites and 07:15 at the third, so 07:15-08:45 is the shortest
  window containing every site's own AM peak hour.
* *Why 90 minutes:* it is the model's OWN declared simulation period, and its
  demand profile is flat across all six 15-minute intervals (measured: each
  carries within a percentage point of one sixth of the total). Matching the two
  durations means neither side is scaled, and a scaling factor is a number
  nobody measured.

WHICH MODEL, AND WHY NOT THE OTHER ONE
--------------------------------------
The **consultant's own ``.inpx``** (the live dataset the 2026-08-07 calibration
ran, hashed before and after and never opened in place). Three reasons, any one
of which is sufficient:

* This project's only georeference — ``routers/geojson.py``'s Helmert fit — is
  fitted to nine surveyed intersections OF THIS NETWORK. Without it a SCATS
  lat/lon cannot be placed against a node at all, so no site -> node map exists
  for any other network.
* Its nodes are the signalised junctions those SCATS sites control, and it
  carries real demand and real signal timings. A GEH against it answers a
  question a client asked: is the calibrated model reproducing the field counts?

The **generated OSM draft is rejected**, not overlooked:

* it is geometry-only — ``genesis.emit`` writes links, connectors and node
  polygons, and demand only when a caller supplies it, so a plain draft's
  modelled node totals would be zero. Grading a network that was never given
  traffic is not a calibration result;
* its node ids are sequential from 1 with no relation to the consultant's — the
  exact id-collision that made benchmark v1 report "0 links removed" and that
  ``genesis.benchmark`` was rewritten to kill;
* it lives in its own tangent plane about its own origin, so even the frame is
  a different object.

COM SAFETY
----------
The run goes through the house entry point (``dispatch("run_simulation")`` ->
``routers.simulations.start_agent_simulation_job``), which
``connect(attach_to_running=False)`` and ``RunContinuous``. The consultant
workspace is READ ONCE: its ``current.inpx`` is hashed before and after and the
script aborts if the hashes differ. The run happens on an ``ArtifactManager``
working copy in this script's own work dir, never on the client dataset.
Vissim PIDs present before the script starts are recorded and never killed.

Usage (from ``backend/``, with the venv python)::

    python tools/scats_reality_check_live.py --stages 0,1     # observed side only, no COM
    python tools/scats_reality_check_live.py                  # the whole thing, live
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import traceback
import zipfile
from datetime import datetime, timedelta, timezone
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

WORK_ROOT = REPO_ROOT / "runtime" / "scats-reality-check-live"
STATE_PATH = WORK_ROOT / "state.json"
LOG_PATH = WORK_ROOT / "log.txt"
ARTIFACT_DIR = WORK_ROOT / "artifacts"

#: The real WA SCATS drop. READ ONLY, and never copied anywhere outside
#: ``runtime/`` — client-data is confidential.
SCATS_ZIP = REPO_ROOT / "client-data" / "Traffic signal data.zip"

#: The live consultant dataset. READ ONLY — copied, never opened in place.
CONSULTANT_INPX = (
    REPO_ROOT / "runtime" / "workspaces" / "bfc69251bd04" / "uploads" / "current" / "current.inpx"
)

DEMO_WORKSPACE_NAME = "실측 대조 (WA SCATS) 2026-08-11"
UPLOAD_FILENAME = "SCATS대조_라이브_사본_current.inpx"

#: The session working copy stage 2 runs. Suffixed by ``--label`` too, so a
#: sensitivity run does not rmtree the directory the primary run left behind.
RUN_WORK_DIRNAME = "run_work"

#: Declared observation period. See the module docstring for the measurement
#: behind every one of these three numbers.
PERIOD_START = datetime(2026, 2, 17, 7, 15)
PERIOD_MINUTES = 90


def period_name(start: datetime, minutes: int) -> str:
    """The declared window's own name, derived from the window itself.

    Derived rather than a constant so a sensitivity run (a different length,
    a different day) cannot silently reuse the name of the window it is being
    compared against — two different windows under one name is exactly what
    ``observations._validate_periods`` refuses at ingest.
    """
    end = start + timedelta(minutes=minutes)
    return f"AM_{start:%Y-%m-%d_%H%M}-{end:%H%M}"


PERIOD_NAME = period_name(PERIOD_START, PERIOD_MINUTES)

RUN_SEED = 42

ALL_STAGES = (0, 1, 2, 3)


# ---------------------------------------------------------------------------
# Logging / state — the house tee rule: evidence lands in runtime/
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


def _apply_label(label: str) -> None:
    """Suffix every evidence path and the demo workspace with ``label``.

    A sensitivity run must not overwrite the evidence of the run it is testing,
    and it must not resume that run's workspace either — a second observation
    set for a DIFFERENT window belongs beside the first, not on top of it.
    Called once, before the first ``_log``.
    """
    global STATE_PATH, LOG_PATH, ARTIFACT_DIR, DEMO_WORKSPACE_NAME, RUN_WORK_DIRNAME
    STATE_PATH = WORK_ROOT / f"state{label}.json"
    LOG_PATH = WORK_ROOT / f"log{label}.txt"
    ARTIFACT_DIR = WORK_ROOT / f"artifacts{label}"
    RUN_WORK_DIRNAME = f"run_work{label}"
    DEMO_WORKSPACE_NAME = f"{DEMO_WORKSPACE_NAME}{label}"


def _stage(key: str) -> dict[str, Any]:
    return _state.setdefault("stages", {}).setdefault(key, {})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _banner(text: str) -> None:
    _log("")
    _log("=" * 78)
    _log(text)
    _log("=" * 78)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


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
# STAGE 0 — the site -> node map, from coordinates both sides state
# ---------------------------------------------------------------------------


def _extract_workbooks() -> list[Path]:
    """The three client workbooks, unpacked into this script's own work dir.

    ``client-data/`` is never written to and the extracted copies stay under
    ``runtime/`` (git-ignored), which is where every other probe's working
    material lives.
    """
    target = WORK_ROOT / "workbooks"
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    with zipfile.ZipFile(SCATS_ZIP) as archive:
        for name in sorted(archive.namelist()):
            if not name.lower().endswith((".xlsx", ".xlsm")):
                continue
            destination = target / Path(name).name
            destination.write_bytes(archive.read(name))
            paths.append(destination)
    return paths


def stage_0_site_node_map() -> None:
    _banner("[STAGE 0] Which modelled junction is each signal? — the site -> node map")
    entry = _stage("0_site_node_map")
    entry["started_at"] = _now()
    started = time.monotonic()

    from services.dataimport.adapters.scats_wa import SCATSWAAdapter
    from services import site_node_map

    if not SCATS_ZIP.is_file():
        raise SystemExit(f"SCATS drop not found: {SCATS_ZIP}")
    if not CONSULTANT_INPX.is_file():
        raise SystemExit(f"Consultant network not found: {CONSULTANT_INPX}")

    consultant_hash_before = _sha256(CONSULTANT_INPX)
    entry["consultant_inpx"] = str(CONSULTANT_INPX)
    entry["consultant_sha256_before"] = consultant_hash_before
    _log(f"[0.1] Consultant network (READ ONLY, never opened in place): {CONSULTANT_INPX}")
    _log(f"      sha256={consultant_hash_before}")

    workbooks = _extract_workbooks()
    _state["workbooks"] = [str(path) for path in workbooks]
    entry["workbook_count"] = len(workbooks)
    entry["workbook_names"] = [path.name for path in workbooks]
    _log(f"[0.2] {len(workbooks)} client workbook(s) unpacked into {WORK_ROOT / 'workbooks'}")

    # Site identity comes from the SAME adapter that will read the counts, so
    # the map cannot be built against a site id the counts do not use.
    adapter = SCATSWAAdapter()
    sites: list[Any] = []
    for path in workbooks:
        result = adapter.parse([path])
        sites.extend(result.sites)
    entry["sites_parsed"] = [site.site_id for site in sites]
    _log(f"[0.3] {len(sites)} site(s) parsed by the {adapter.info.name!r} adapter: "
         f"{', '.join(site.site_id for site in sites)}")

    positions, position_refusals = site_node_map.site_positions_from_metadata(sites)
    for refusal in position_refusals:
        _log(f"      REFUSED before matching: [{refusal.code}] {refusal.message}")

    mapping = site_node_map.map_sites_to_nodes(CONSULTANT_INPX, positions)
    mapping.refusals.extend(position_refusals)
    summary = site_node_map.summarize(mapping)
    entry["mapping"] = summary
    _state["site_nodes"] = mapping.site_nodes

    _banner("[STAGE 0] THE MAP")
    _log(f"  tolerance      : {mapping.tolerance_m:g} m")
    _log(f"  derivation     : {mapping.tolerance_basis}")
    _log(f"  comparison     : {mapping.comparison_level}")
    _log(f"  {mapping.comparison_level_note}")
    _log(f"  nodes considered      : {mapping.nodes_considered}")
    _log(f"  nodes without position: {mapping.nodes_without_position}")
    _log(f"  closest node pair     : {summary['min_node_separation_m']} m")
    _log(f"  a site cannot reach two nodes at this tolerance: "
         f"{mapping.tolerance_cannot_reach_two_nodes}")
    _log("")
    for match in mapping.matched:
        flag = "  <- THIN MARGIN" if match.thin_margin else ""
        _log(f"  {match.site_id} -> node {match.node_id}  {match.distance_m:.1f} m "
             f"(margin {match.margin_m:.1f} m; runner-up node {match.runner_up_node_id} at "
             f"{match.runner_up_distance_m:.1f} m = {match.separation_ratio}x further){flag}")
    for refusal in mapping.refusals:
        _log(f"  REFUSED {refusal.site_id}: [{refusal.code}] {refusal.message}")

    if not mapping.matched:
        entry["ok"] = False
        _save_state()
        raise SystemExit("STAGE 0 REFUSAL: not one site resolved to a node. Nothing to compare.")

    consultant_hash_after = _sha256(CONSULTANT_INPX)
    entry["consultant_sha256_after"] = consultant_hash_after
    entry["consultant_untouched"] = consultant_hash_after == consultant_hash_before
    _log(f"[0.4] Consultant network UNTOUCHED: {entry['consultant_untouched']}")
    if not entry["consultant_untouched"]:
        raise SystemExit("FATAL: the live consultant dataset changed. Aborting.")

    entry["elapsed_s"] = round(time.monotonic() - started, 2)
    entry["ok"] = True
    _save_state()


# ---------------------------------------------------------------------------
# STAGE 1 — the observation set: real counts, declared period, quality gate
# ---------------------------------------------------------------------------


def _admin_bearer_headers() -> dict[str, str]:
    """Borrow an EXISTING enabled admin — never create one.

    Same policy and the same reason as ``calibration_loop_live_check``: a probe
    minting the most privileged credential in the product is precisely what
    ``tools/manage_users.py``'s "only an operator, only with a password they
    typed" rule exists to prevent.
    """
    from services import auth

    admin = next(
        (user for user in auth.list_users() if user["role"] == "admin" and not user["disabled"]),
        None,
    )
    if admin is None:
        raise SystemExit(
            "This probe needs an enabled admin account to create its demo project.\n"
            "    python tools/manage_users.py create-admin --username <you>"
        )
    token, _session = auth.create_session(admin["id"])
    _log(f"[1.0] Authenticated as the existing admin {admin['username']!r}")
    return {"Authorization": f"Bearer {token}"}


def stage_1_observation_set(client, period_start: datetime, period_minutes: int) -> None:
    _banner("[STAGE 1] The observed side — REAL SCATS counts through the shipped ingest path")
    entry = _stage("1_observation_set")
    entry["started_at"] = _now()
    started = time.monotonic()

    from services import observations, workspaces

    admin_auth = _admin_bearer_headers()
    workspace_id = _state.get("workspace_id")
    if workspace_id:
        _log(f"[1.1] Reusing the demo workspace already recorded: {workspace_id}")
    else:
        created = client.post(
            "/api/workspaces", json={"name": DEMO_WORKSPACE_NAME}, headers=admin_auth
        )
        if created.status_code != 200:
            raise SystemExit(f"Workspace creation failed: {created.status_code} {created.text}")
        workspace_id = created.json()["id"]
        _log(f"[1.1] Created workspace {workspace_id!r}")
    _state["workspace_id"] = workspace_id
    _state["headers"] = {"X-Workspace-Id": workspace_id, **admin_auth}

    period_end = period_start + timedelta(minutes=period_minutes)
    name = period_name(period_start, period_minutes)
    _state["period_name"] = name
    period = observations.ObservationPeriod(name=name, start=period_start, end=period_end)
    entry["period"] = {
        "name": period.name,
        "start": period.start.isoformat(),
        "end": period.end.isoformat(),
        "minutes": period_minutes,
    }
    _log(f"[1.2] DECLARED period: {period.name} = {period.start} -> {period.end} "
         f"({period_minutes} min)")

    workbooks = [Path(item) for item in _state["workbooks"]]
    # Every non-request execution context binds explicitly — the store is
    # per-workspace by construction and refuses to default silently.
    try:
        with workspaces.bind_workspace(workspace_id):
            record = observations.ingest_observation_set(
                workbooks,
                name=f"WA SCATS 2026-02 · {name}",
                periods=[period],
                adapter_name="scats_wa",
            )
    except observations.ObservationRefusal as exc:
        entry["ok"] = False
        entry["refusal"] = exc.to_dict()
        _save_state()
        _log(f"[1.3] INGEST REFUSED: {exc.code}")
        _log(json.dumps(exc.to_dict(), ensure_ascii=False, indent=2)[:4000])
        raise SystemExit("STAGE 1 REFUSAL: the observation set was not stored.")

    _state["observation_set_id"] = record["id"]
    entry["observation_set_id"] = record["id"]
    entry["site_ids"] = record["site_ids"]
    entry["coverage"] = record["coverage"]
    entry["gate_counts"] = record["gate"]["counts"]
    entry["gate_codes"] = record["gate"]["codes"]
    entry["period_coverage"] = record["periods"]

    _log(f"[1.3] Stored observation set {record['id']} — {record['site_count']} site(s)")
    _log(f"      adapter        : {record['adapter']}")
    _log(f"      gate           : {record['gate']['counts']} codes={record['gate']['codes']}")
    coverage = record["coverage"]
    _log(f"      detector records read/aggregated: "
         f"{coverage['detector_records_read']}/{coverage['detector_records_aggregated']}")
    _log(f"      turning-count records read      : {coverage['records_read']} "
         "(a SCATS drop has none, by construction — no movement exists in this source)")
    _log(f"      interval minutes                : {coverage['interval_minutes']}")
    for window in record["periods"]:
        _log(f"      period {window['name']}: sites with data={window['sites_with_data']} "
             f"without={window['sites_without_data']} detector records={window['detector_records']}")

    # The count basis, per site, straight off the stored aggregates. This is the
    # label everything downstream must keep saying.
    bases = {
        site_id: aggregate["count_basis"]
        for site_id, aggregate in (record["aggregates"].get(name) or {}).items()
    }
    entry["count_basis_per_site"] = bases
    _log(f"[1.4] count_basis per site: {bases}")
    if any(basis != ["detector_counts"] for basis in bases.values()):
        _log("      !! NOTE: a site reports a basis other than detector_counts alone.")

    # The observed side as the reality check will see it — built here so a
    # failure of the JOIN is separated from a failure of the RUN.
    with workspaces.bind_workspace(workspace_id):
        observed = observations.observed_side(
            record["id"], period=name, site_nodes=_state["site_nodes"]
        )
    entry["observed_side"] = {
        "problem": observed["problem"],
        "coverage": observed["coverage"],
        "nodes": {
            node_id: {
                "node_id": node["node_id"],
                "count_basis": node["count_basis"],
                "direction_count": len(node["directions"]),
            }
            for node_id, node in observed["nodes"].items()
        },
    }
    _log(f"[1.5] observed_side problem={observed['problem']!r} "
         f"joined={observed['coverage']['counts']}")
    for node_id, node in sorted(observed["nodes"].items()):
        _log(f"      node {node_id}: basis={node['count_basis']} "
             f"directions={len(node['directions'])} (empty is CORRECT for detector loops)")
    if observed["problem"] is not None:
        entry["ok"] = False
        _save_state()
        raise SystemExit(f"STAGE 1 REFUSAL: observed side unusable ({observed['problem']}).")

    entry["elapsed_s"] = round(time.monotonic() - started, 2)
    entry["ok"] = True
    _save_state()


# ---------------------------------------------------------------------------
# STAGE 2 — the model run (LIVE COM)
# ---------------------------------------------------------------------------


def _declared_sim_period(inpx: Path) -> int | None:
    import xml.etree.ElementTree as ET

    try:
        simulation = ET.parse(inpx).getroot().find(".//simulation")
    except (ET.ParseError, OSError):
        return None
    if simulation is None:
        return None
    try:
        return int(float(simulation.get("simPeriod", "")))
    except (TypeError, ValueError):
        return None


def stage_2_run(client, duration_s: int) -> None:
    _banner(f"[STAGE 2] The modelled side — a REAL Vissim run ({duration_s}s, 1 seed)")
    entry = _stage("2_run")
    entry["started_at"] = _now()
    entry["duration_s"] = duration_s
    entry["random_seeds"] = [RUN_SEED]
    started = time.monotonic()

    from urllib.parse import quote

    from services import results_evidence
    from services.agent.state import ArtifactManager

    source_bytes = CONSULTANT_INPX.read_bytes()
    consultant_hash_before = _sha256(CONSULTANT_INPX)

    upload = client.post(
        f"/api/datasets/upload?filename={quote(UPLOAD_FILENAME)}",
        content=source_bytes,
        headers=_state["headers"],
    )
    if upload.status_code != 200:
        raise SystemExit(f"Dataset upload failed: {upload.status_code} {upload.text}")
    active = upload.json()
    demo_inpx = Path(active["path"])
    entry["demo_inpx"] = str(demo_inpx)
    entry["demo_is_byte_identical_to_consultant"] = demo_inpx.read_bytes() == source_bytes
    _log(f"[2.1] Uploaded a byte-identical COPY -> {demo_inpx}")
    _log(f"      identical to the consultant original: "
         f"{entry['demo_is_byte_identical_to_consultant']}")

    # The run source is the SESSION WORKING COPY, exactly as a production agent
    # turn runs it: ArtifactManager.clone() normalizes underScenMngm (the
    # 2026-07-31 LoadNet landmine) and numRuns (cb98a7a) in the copy only.
    work_dir = WORK_ROOT / RUN_WORK_DIRNAME
    if work_dir.exists():
        shutil.rmtree(work_dir, ignore_errors=True)
    work_dir.mkdir(parents=True, exist_ok=True)
    manager = ArtifactManager(source_inpx=demo_inpx, work_dir=work_dir)
    manager.clone()
    working = manager.working_path
    entry["working_copy"] = str(working)
    entry["declared_sim_period_s"] = _declared_sim_period(demo_inpx)
    _log(f"[2.2] Run source = the session working copy {working}")
    _log(f"      the dataset's own declared simPeriod: {entry['declared_sim_period_s']}s; "
         f"this run asks for {duration_s}s")
    _save_state()

    import services.agent.tools_run as tools_run
    from services.agent.toolkit import default_registry, dispatch

    tools_run.set_active_run_source(working)
    dispatch_started = time.monotonic()
    try:
        envelope = dispatch(
            "run_simulation",
            {"duration_s": duration_s, "random_seeds": [RUN_SEED]},
            registry=default_registry,
        )
    finally:
        tools_run.clear_active_run_source()
    entry["dispatch_elapsed_s"] = round(time.monotonic() - dispatch_started, 2)
    entry["envelope_status"] = envelope.get("status")
    _log(f"[2.3] dispatch returned in {entry['dispatch_elapsed_s']}s, "
         f"status={envelope.get('status')}")
    if envelope.get("status") != "ok":
        entry["ok"] = False
        entry["envelope"] = envelope
        _save_state()
        raise SystemExit(f"Run FAILED: {json.dumps(envelope, ensure_ascii=False)[:2000]}")

    from services.simulation_runs import get_job_record, job_dir

    data = envelope.get("data") or {}
    run_result = data.get("run_result") or {}
    job_id = (envelope.get("verification") or {}).get("job_id")
    record = get_job_record(job_id) if job_id else None
    scenario_id = (record or {}).get("scenario_result_id")
    entry["job_id"] = job_id
    entry["scenario_result_id"] = scenario_id
    _state["scenario_id"] = scenario_id

    db_paths = results_evidence.result_db_paths(job_dir(job_id)) if job_id else []
    entry["result_db_count"] = len(db_paths)
    _log(f"[2.4] job={job_id} scenario={scenario_id} result .db count={len(db_paths)}")
    if len(db_paths) != 1:
        _log("      !! more than one result database: a multi-seed sum is not comparable to a "
             "single field window, and run_reality_check will (correctly) refuse it.")

    descriptor = run_result.get("evidence") or {}
    class_state = descriptor.get(results_evidence.MOVEMENT_INTERVAL_CLASS_TABLE)
    entry["movement_class_present"] = bool(results_evidence.is_present(class_state))
    volumes = run_result.get("movement_volumes")
    by_node: dict[str, float] = {}
    if isinstance(volumes, dict):
        for object_id, row in volumes.items():
            if not isinstance(row, dict):
                continue
            node = str(row.get("node"))
            if str(object_id) == node or row.get("vehs") is None:
                continue
            by_node[node] = by_node.get(node, 0.0) + float(row["vehs"])
    entry["modelled_node_totals"] = {k: round(v, 2) for k, v in sorted(by_node.items())}
    _log(f"[2.5] movement class evidence present: {entry['movement_class_present']}")
    _log(f"      modelled node totals: {entry['modelled_node_totals']}")

    if not entry["movement_class_present"]:
        entry["ok"] = False
        _save_state()
        raise SystemExit("STAGE 2 REFUSAL: no movement evidence — there is no modelled side.")

    entry["consultant_untouched"] = _sha256(CONSULTANT_INPX) == consultant_hash_before
    _log(f"[2.6] Consultant network UNTOUCHED: {entry['consultant_untouched']}")
    if not entry["consultant_untouched"]:
        raise SystemExit("FATAL: the live consultant dataset changed. Aborting.")

    entry["elapsed_s"] = round(time.monotonic() - started, 2)
    entry["ok"] = True
    _save_state()


# ---------------------------------------------------------------------------
# STAGE 3 — the reality check: the first real GEH
# ---------------------------------------------------------------------------


def stage_3_reality_check() -> None:
    _banner("[STAGE 3] THE REAL NUMBER — run_reality_check against the stored SCATS set")
    entry = _stage("3_reality_check")
    entry["started_at"] = _now()
    started = time.monotonic()

    import services.agent.tools_metrics  # noqa: F401 - registers run_reality_check
    from services.agent.toolkit import default_registry, dispatch

    site_nodes = _state["site_nodes"]
    envelope = dispatch(
        "run_reality_check",
        {
            "scenario_id": _state["scenario_id"],
            "observation_set_id": _state["observation_set_id"],
            "observation_period": _state["period_name"],
            "site_nodes": [
                {"site_id": site_id, "node_id": node_id}
                for site_id, node_id in sorted(site_nodes.items())
            ],
        },
        registry=default_registry,
    )
    entry["envelope_status"] = envelope.get("status")
    if envelope.get("status") != "ok":
        entry["ok"] = False
        entry["envelope"] = envelope
        _save_state()
        raise SystemExit(
            f"run_reality_check FAILED: {json.dumps(envelope, ensure_ascii=False)[:3000]}"
        )

    report = envelope["data"]["reality_check"]
    entry["report"] = report
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    (ARTIFACT_DIR / "reality_check_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
    )

    _log(f"  status        : {report.get('status')}  reason={report.get('reason_code')}")
    _log(f"  geh threshold : {report.get('geh_threshold')}")
    _log(f"  observed src  : {report.get('sources', {}).get('observed')}")
    _log(f"  coverage      : {json.dumps(report.get('coverage'), ensure_ascii=False)}")

    _banner("[STAGE 3] SITE-LEVEL GEH — modelled node total vs observed site total")
    _log("  (SITE LEVEL ONLY. A SCATS detector carries no turning movement, so there is no")
    _log("   per-movement number here and 'directions' is empty by construction.)")
    _log("")
    _log(f"  {'site':<10} {'node':<6} {'observed':>10} {'modelled':>10} {'GEH':>8}  outcome")
    rows: list[dict[str, Any]] = []
    node_to_site = {node_id: site_id for site_id, node_id in site_nodes.items()}
    for node in report.get("nodes") or []:
        node_id = str(node.get("node_id"))
        row = {
            "site_id": node_to_site.get(node_id),
            "node_id": node_id,
            "node_name": node.get("node_name"),
            "count_basis": node.get("count_basis"),
            "observed_total": node.get("observed_total"),
            "modelled_total": node.get("modelled_total"),
            "node_total_geh": node.get("node_total_geh"),
            "outcome": (node.get("outcome") or {}).get("outcome"),
            "reason_code": (node.get("outcome") or {}).get("reason_code"),
        }
        rows.append(row)
        _log(f"  {str(row['site_id']):<10} {node_id:<6} {str(row['observed_total']):>10} "
             f"{str(row['modelled_total']):>10} {str(row['node_total_geh']):>8}  "
             f"{row['outcome']} / {row['reason_code']}")
    entry["site_level_geh"] = rows

    graded = [row for row in rows if row["node_total_geh"] is not None]
    entry["nodes_graded"] = len(graded)
    if graded:
        under = [row for row in graded if float(row["node_total_geh"]) < 5.0]
        entry["nodes_under_geh_5"] = len(under)
        entry["share_under_geh_5"] = round(100.0 * len(under) / len(graded), 1)
        _log("")
        _log(f"  {len(under)} of {len(graded)} mapped sites are under GEH 5 "
             f"({entry['share_under_geh_5']}%).")

    _banner("[STAGE 3] WHAT THESE NUMBERS DO AND DO NOT MEAN")
    for line in (
        "1. SITE LEVEL ONLY. Observed = the sum of that SCATS site's detector volumes over the",
        "   declared window. Whether that equals 'vehicles entering the junction' depends on the",
        "   site's detector layout, which only the MRWA LMA/LMB drawing states — and that drawing",
        "   is not in this drop. This is the largest uncertainty in the table above.",
        "2. The modelled side starts from an EMPTY network and fills over the first minutes of the",
        "   run, so a run's throughput is biased LOW against a field count of the same length.",
        "   No correction factor is applied: an invented factor would be a worse error than a",
        "   named bias. To turn this from a caveat into a number, run a second window length",
        "   (--label _60min --period-start ... --period-minutes 60 --duration 3600) and fit",
        "   modelled(T) = rate x T - D across the pair; D is the fill deficit in vehicles.",
        "3. This model's demand is FLAT across all six of its 15-minute intervals, so any peaking",
        "   the field shows WITHIN the modelled period is something the model cannot reproduce by",
        "   construction. The same two-window pair measures it: compare the observed RATE in the",
        "   short window against the observed rate in the long one. Only what survives both",
        "   corrections is a disagreement a calibration edit should act on.",
        "4. The two sides are the same DURATION by construction (the declared window and the run",
        "   length are both stated above), so neither side has been scaled.",
    ):
        _log("  " + line)

    entry["caveats"] = {
        "site_level_only": (
            "observed = sum of the site's detector volumes; the detector->movement map needs the "
            "MRWA LMA/LMB drawing, which is absent from this drop"
        ),
        "empty_network_warmup": (
            "the run starts from an empty network, biasing modelled throughput low against a field "
            "count of the same duration; no correction factor applied. Measurable by running two "
            "window lengths and fitting modelled(T) = rate x T - D"
        ),
        "flat_model_demand_profile": (
            "the dataset's demand is flat across its six 15-minute intervals, so field peaking "
            "WITHIN the modelled period is not reproducible by construction; measurable from the "
            "observed rate in a short window vs a long one"
        ),
        "durations_matched": "declared observed window and run length are equal; neither side scaled",
    }
    entry["elapsed_s"] = round(time.monotonic() - started, 2)
    entry["ok"] = True
    _save_state()


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--stages",
        default=",".join(str(s) for s in ALL_STAGES),
        help="Comma-separated stage numbers. 0,1 runs the observed side with NO COM at all.",
    )
    parser.add_argument(
        "--duration",
        type=int,
        default=PERIOD_MINUTES * 60,
        help="Simulation seconds. Defaults to the declared observed window, so the two sides "
        "match without either being scaled.",
    )
    parser.add_argument(
        "--label",
        default="",
        help="Suffix for state/log/artifacts and the demo workspace, so a sensitivity run lands "
        "beside the primary evidence instead of on top of it (e.g. --label _60min).",
    )
    parser.add_argument("--period-start", default=PERIOD_START.isoformat())
    parser.add_argument("--period-minutes", type=int, default=PERIOD_MINUTES)
    args = parser.parse_args()

    stages = {int(part) for part in args.stages.split(",") if part.strip()}
    period_start = datetime.fromisoformat(args.period_start)
    if args.label:
        _apply_label(args.label)

    _state.update(_load_state())
    WORK_ROOT.mkdir(parents=True, exist_ok=True)
    _banner(f"SCATS REALITY CHECK — LIVE. stages={sorted(stages)} started {_now()}")
    _log(f"  work root: {WORK_ROOT}")

    pids_before = _vissim_pids()
    _state["vissim_pids_before"] = sorted(pids_before)
    _log(f"  Vissim PIDs already running (never killed): {sorted(pids_before)}")

    # No uvicorn: everything is driven in-process, the way the test suite works.
    os.environ.setdefault("AGENT_RUN_TRAJECTORY_MAX_S", "0")
    from fastapi.testclient import TestClient

    import main as app_main

    client = TestClient(app_main.app)

    status = 0
    try:
        if 0 in stages:
            stage_0_site_node_map()
        if 1 in stages:
            stage_1_observation_set(client, period_start, args.period_minutes)
        if stages & {2, 3}:
            from services import workspaces

            workspace_id = _state.get("workspace_id")
            if not workspace_id:
                raise SystemExit(
                    "Stages 2/3 need the demo workspace stage 1 creates. Run --stages 0,1 first."
                )
            # The run job and the reality check both resolve workspace-scoped
            # state (the dataset, the run registry, the observation store), so
            # the binding has to span both rather than being set per call.
            with workspaces.bind_workspace(workspace_id):
                if 2 in stages:
                    stage_2_run(client, args.duration)
                if 3 in stages:
                    stage_3_reality_check()
    except SystemExit as exc:
        _log("")
        _log(f"STOPPED: {exc}")
        status = 1
    except Exception:  # noqa: BLE001 - a probe reports its own crash
        _log("")
        _log("CRASHED:")
        _log(traceback.format_exc())
        status = 2
    finally:
        killed = _kill_new_vissim(pids_before)
        orphans = sorted(_vissim_pids() - pids_before - set(killed))
        _state["vissim_pids_killed"] = killed
        _state["vissim_orphans"] = orphans
        _save_state()
        _banner("TEARDOWN")
        _log(f"  Vissim processes this run started and cleaned up: {killed}")
        _log(f"  orphans left behind: {orphans}")
        _log(f"  state: {STATE_PATH}")
        _log(f"  log  : {LOG_PATH}")
    return status


if __name__ == "__main__":
    raise SystemExit(main())
