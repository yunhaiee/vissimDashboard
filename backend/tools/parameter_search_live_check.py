"""PARAMETER-SEARCH LIVE CHECK: unit #51's live runner, end to end, on a REAL Vissim.

``backend/tests/test_agent_search_job.py`` is this script's offline sibling. It
proves the seam — start route → job thread → engine → ``accept_winner`` →
``propose`` → ``searched_behaviour_broker.remember`` → a later product turn that
finds it — with ONE thing faked: the runner. This script runs the same seam with
the RUNNER REAL: ``services.agent.search_runner.VissimSearchRunner`` driving
Vissim once per candidate, on materialized standalone copies, inside the job
thread.

``tools/calibration_loop_live_check.py`` is its older sibling and this file's
template: same ``--stages`` shape, same ``--label`` evidence isolation, same
state-json-after-every-step discipline, same loud foreground printing, and the
same rule that **every synthesized thing is named in the output**.

================================================================================
WHAT IS REAL HERE, AND WHAT IS NOT
================================================================================

REAL:
  * the workbook upload, through ``POST /api/validation/base-workbook`` (which
    refuses to persist a workbook that does not conform);
  * the scoring run: ``dispatch("run_simulation", ...)`` →
    ``start_agent_simulation_job`` → ``connect(attach_to_running=False)`` →
    ``RunContinuous``, one seed, on an ``ArtifactManager`` working copy;
  * the reality check: ``build_reality_check_report``, the same call the search
    runner makes per candidate;
  * the agent session: a real turn through
    ``POST /api/assistant/agent/query/stream``, so the session id the broker is
    keyed by is one the product minted;
  * THE SEARCH: ``search_job.start`` with ``runner_factory`` UNTOUCHED, so every
    candidate is a real standalone materialization, a real
    ``set_driving_behavior_parameter`` with a real read-back, a real
    ``RunContinuous`` and a real ``build_reality_check_report``;
  * the holdout: ``accept_winner`` re-running the winner AND the baseline at a
    seed the search never used;
  * the broker: ``remember`` minting a real grant from the real ledger;
  * the product turn that consumes it, and the approval card's re-minted grant.

NOT REAL — two, and BOTH are printed in capitals by stage 2 before it starts:
  1. **The planner.** ``_default_provider`` is a canned queue, exactly as
     ``tools/calibration_loop_live_check.py`` stage 4 and
     ``tools/run_gate_live_check.py`` do. No paid LLM call and no planner
     variance in the middle of an hour of COM.
  2. **The precondition GATE payload.** Stage 2 hands
     ``parameter_search.start`` a record with ``ok: True`` that this workspace
     cannot currently earn. Two legs, both named, both with a reason:

     * ``LADDER_NOT_EXHAUSTED`` — MEASURED 2026-08-21: the rail's own report is
       built with NO targets (``routers/calibration.py``'s scoring call passes
       none, and says so), so ``fixes.map_node_counts`` arm (a) returns
       ``NO_TARGET_OBJECT`` from the node-total arm and returns IMMEDIATELY
       (``fixes.py`` ~2419) — the split arm is never reached, ``_d4_refusal``
       is ``None`` and ``geh_ladder.classify_node`` answers ``UNCLASSIFIABLE`` /
       ``NO_DEMAND_TARGET``. That code is in
       ``parameter_search.CLOSABLE_MISSING_EVIDENCE``, so every missing node is
       ``EVIDENCE_MISSING`` and ``exhausted`` is False **no matter how much
       evidence is collected**. It is unreachable through the rail, not merely
       unmet here.
     * ``SIGNAL_STEP_UNVERIFIED`` — the gate requires rail step ⑥ to read
       exactly ``DONE``, which needs a CONFIRMED 현시도 review. There is no real
       phase-diagram attestation for a synthetic demo scene, and inventing one
       would be forging the very artifact the step exists to require.
       (``NOT_APPLICABLE`` is not ``DONE``: ``parameter_search._signal_step_done``
       compares against the literal, so even a signal-free net fails this leg.)

     What is NOT synthesized inside that record: **the split.**
     ``parameter_search.start`` re-derives it (``_verified_split``) and refuses a
     payload that disagrees, and ``grant_from_search`` re-derives it AGAIN and
     refuses a grant whose gate record is not the one that opened the search. So
     the holdout cannot be chosen here, and stage 2 prints ``_verified_split``'s
     own answer rather than its own.

     Everything downstream of ``ok: True`` is measured: the ledger is real runs,
     the acceptance is re-derived from that ledger at an unseen seed, and the
     grant is minted from both.

================================================================================
THE SIX-NODE WORKBOOK, AND WHY NODE 4 CARRIES THE MISS
================================================================================

The workspace this targets was built by ``calibration_loop_live_check`` with a
TWO-node workbook (node 8 missing at GEH ~7.5, node 5 a GEH-0 control). Two
graded nodes cannot be split: ``parameter_search.MIN_NODES_FOR_HOLDOUT`` is 4,
so ``split_nodes`` answers ``available: False`` and the gate refuses
``HOLDOUT_NOT_AVAILABLE``. Stage 0 therefore designs a SIX-node workbook over
the nodes the baseline run already produces modelled totals for.

Which node carries the designed miss is not a free choice. ``split_nodes`` is a
deterministic STRIDE (``HOLDOUT_STRIDE = 3``): sorted node ids, every index with
``index % 3 == 2`` goes to the holdout. For ``[1, 2, 3, 4, 5, 8]`` that is
indices 2 and 5 — nodes **3 and 8** — leaving ``[1, 2, 4, 5]`` to calibrate on.

Node 8 (the calibration harness's own miss) therefore lands in the HOLDOUT, and
the optimizer never scores a held-out node: ``ParameterSearch`` builds its
objective from ``state.calibration_nodes`` and nothing else. A search whose only
miss is held out would optimise mean GEH across four already-passing nodes —
it would have no gradient to follow and nothing to improve. So the designed miss
moves to **node 4**, which the same stride puts at index 3, in CALIBRATION; node
8 becomes a matched control on the holdout side, which is exactly what a holdout
should be. Stage 0 prints the split proof before it writes a single row.

Everything else is the calibration harness's own design, unchanged: the observed
total for the miss node is solved from ``2(O-M)^2 = G^2(M+O)`` for G = 7.5, every
other node's observed value IS its own modelled value rounded (GEH ~ 0 controls),
``Direction`` carries the run's own Vissim movement OBJECT_ID so every observed
number joins back to the row it came from, and a ``DEMO_NOTES`` sheet says in
Korean and English that the file is synthetic design, not field data.

================================================================================
THE KNOWN TRAP THIS SCRIPT WALKS INTO ON PURPOSE
================================================================================

A workbook upload changes the active dataset's REVISION (it carries
``real_counts.xlsx:<mtime>:<size>``), and
``simulation_runs.get_runtime_result_dir_for_scenario`` refuses a job whose
``dataset_revision`` no longer matches. So the prior scoring run becomes
unreadable the moment stage 0 uploads, and stage 1 MUST re-run. Stage 0 says so
in its own output rather than leaving stage 1 to discover it.

Stage 1's duration is READ, never guessed: from the newest completed job
record's own ``params.sim_duration`` (recorded by ``create_job_record`` at
submission), falling back to the calibration harness's state json. If neither
answers, this script refuses rather than picking a number.

================================================================================
COM SAFETY
================================================================================

Every Vissim session is opened by the house entry points — ``routers.simulations``'
job runner for the scoring run, ``search_runner.VissimSearchRunner`` for the
candidates — and both ``connect(visible=False, attach_to_running=False)``.
Candidates run on per-candidate CLONES under this script's own work dir; the
active dataset is never opened. Vissim PIDs present before the script starts are
recorded and NEVER killed; only PIDs this process started are cleaned up.

NO uvicorn: everything is driven in-process through ``TestClient`` and direct
service calls, so the job threads run inside this process (the way the whole
test suite works).

Evidence tees to ``runtime/parameter-search-live-check/`` as it goes —
``state.json`` (rewritten after every step, so a probe that dies mid-COM still
leaves what it had) and ``log.txt`` — plus ``artifacts/`` for the workbook and
the reality-check reports, and ``job<label>/`` for the search's own per-candidate
work dirs.

Usage (from ``backend/``, with the venv python)::

    python tools/parameter_search_live_check.py --stages 0
    python tools/parameter_search_live_check.py --stages 1
    python tools/parameter_search_live_check.py --stages 2
    python tools/parameter_search_live_check.py --stages 2 --behavior-no 2
    python tools/parameter_search_live_check.py --stages 0,1,2 --label _reproof

Stage 2 REFUSES if the workspace is not in the six-node state — no silent search
against the two-node workbook the calibration harness left behind.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
import time
import traceback
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

# The calibration harness's PURE arithmetic and process helpers, imported rather
# than re-typed. The GEH solver in particular MUST be the same one: a second
# implementation of "the observed total whose GEH is 7.5" is a second answer
# waiting to drift from the workbook this script is extending. None of these
# touches that module's own state, log file or label globals.
from tools.calibration_loop_live_check import (  # noqa: E402
    _geh,
    _kill_new_vissim,
    _movement_rows,
    _observed_total_for_geh,
    _sha256,
    _stream_events,
    _vissim_pids,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

WORK_ROOT = REPO_ROOT / "runtime" / "parameter-search-live-check"
STATE_PATH = WORK_ROOT / "state.json"
LOG_PATH = WORK_ROOT / "log.txt"
ARTIFACT_DIR = WORK_ROOT / "artifacts"
SCORING_WORK_DIRNAME = "scoring_work"
SEARCH_JOB_DIRNAME = "job"

#: The calibration harness's evidence dir — READ ONLY, and only for the baseline
#: duration when the job registry cannot answer.
CALIBRATION_WORK_ROOT = REPO_ROOT / "runtime" / "calibration-loop-live-check"

#: The workspace ``calibration_loop_live_check --label demo0821`` built.
DEFAULT_WORKSPACE_ID = "4dcf09f7604a"

WORKBOOK_FILENAME = "DEMO_합성_6노드_파라미터탐색_2026-08-21.xlsx"

#: The six nodes the baseline run carries modelled totals for. ORDER IS NOT
#: FREE — see the module docstring's split proof.
WORKBOOK_NODES = ("1", "2", "3", "4", "5", "8")

#: The node that carries the designed miss. Index 3 of the sorted six, which
#: ``split_nodes``' stride puts in CALIBRATION — the only side the optimizer
#: scores. Stage 0 PROVES this rather than trusting the comment.
MISS_NODE = "4"

#: The GEH the designed deviation aims the miss node's TOTAL at. The calibration
#: harness's own target, kept so the two scenes are comparable.
TARGET_GEH = 7.5

#: The period label every row is written under, and the one every scoring call
#: and every candidate is graded against. One window, threaded through.
PERIOD = "AM"

RUN_SEED = 42

#: The search's wall-clock ceiling. Clamped through ``search_job.clamp_budget_s``
#: so the same bounds the route enforces apply here, and the clamp is printed.
DEFAULT_BUDGET_S = 3600.0


# ---------------------------------------------------------------------------
# Evidence plumbing — the calibration harness's shapes, verbatim in kind
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
    """Merged in at startup so ``--stages 2`` can run against the workbook and
    the run an earlier invocation left behind, without repeating either."""
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


def _shout(lines: list[str]) -> None:
    """A block nobody can skim past. Reserved for the synthesized legs — this
    script's whole honesty rests on those two being impossible to miss."""
    _log("")
    _log("!" * 78)
    for line in lines:
        _log("!! " + line)
    _log("!" * 78)


def _apply_label(label: str) -> None:
    """Suffix every evidence path with ``label`` — the calibration harness's own
    rule: an acceptance RE-RUN must not overwrite the evidence of the run it is
    re-proving. Called once, before the first ``_log``."""
    global STATE_PATH, LOG_PATH, ARTIFACT_DIR, SCORING_WORK_DIRNAME, SEARCH_JOB_DIRNAME
    STATE_PATH = WORK_ROOT / f"state{label}.json"
    LOG_PATH = WORK_ROOT / f"log{label}.txt"
    ARTIFACT_DIR = WORK_ROOT / f"artifacts{label}"
    SCORING_WORK_DIRNAME = f"scoring_work{label}"
    SEARCH_JOB_DIRNAME = f"job{label}"


def _admin_bearer_headers() -> dict[str, str]:
    """``Authorization`` headers for an EXISTING enabled admin on this box.

    Borrowed, never minted — the calibration harness's rule and its reason: a
    probe that created an admin would be a script minting the most privileged
    credential in the product, which is what ``tools/manage_users.py``'s policy
    exists to prevent.
    """
    from services import auth

    admin = next(
        (user for user in auth.list_users() if user["role"] == "admin" and not user["disabled"]),
        None,
    )
    if admin is None:
        raise SystemExit(
            "This probe needs an enabled admin account to reach the demo project. Create one:\n"
            "    python tools/manage_users.py create-admin --username <you>"
        )
    token, _session = auth.create_session(admin["id"])
    _log(f"      authenticated as the existing admin {admin['username']!r}")
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Reading the workspace's own prior evidence — never guessing it
# ---------------------------------------------------------------------------


def _latest_completed_job(workspace_id: str) -> dict[str, Any] | None:
    """The newest ``done`` agent run this workspace recorded.

    ``list_job_records`` is workspace-scoped by default, and this is called
    inside ``bind_workspace``. Deliberately does NOT filter on
    ``dataset_revision``: after stage 0 uploads a workbook the revision moves,
    and this function's job is to find the run that EXISTS so its duration and
    its movement volumes can be read — not to decide whether it is still
    scoreable (which is ``get_runtime_result_dir_for_scenario``'s call, and the
    trap stage 1 exists for).
    """
    from services import simulation_runs

    for record in simulation_runs.list_job_records():
        if record.get("status") == "done":
            return record
    return None


def _baseline_duration_s(record: dict[str, Any] | None) -> tuple[int, str]:
    """``(duration, where it came from)``. REFUSES rather than guessing.

    Order: the job record's own ``params.sim_duration`` (stamped by
    ``create_job_record`` at submission — the most direct evidence there is),
    then the calibration harness's state json for this label.
    """
    params = (record or {}).get("params") or {}
    value = params.get("sim_duration")
    if isinstance(value, (int, float)) and value > 0:
        return int(value), f"job record {record.get('id')!r} params.sim_duration"

    for candidate in sorted(CALIBRATION_WORK_ROOT.glob("state*.json")):
        try:
            payload = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if str(payload.get("workspace_id") or "") != str(_state.get("workspace_id") or ""):
            continue
        baseline = (payload.get("stages") or {}).get("1_baseline") or {}
        found = baseline.get("duration_s")
        if isinstance(found, (int, float)) and found > 0:
            return int(found), f"{candidate.name} stages.1_baseline.duration_s"

    raise SystemExit(
        "REFUSING TO GUESS THE RUN DURATION. No completed job record in this workspace carries "
        "params.sim_duration, and no calibration-harness state json for it carries "
        "stages.1_baseline.duration_s. The scoring run must reproduce the baseline's own "
        "duration or its volumes are not comparable — supply the evidence, do not assume it."
    )


def _run_evidence(job_record: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """``(run_result, result_db_paths)`` for one completed job.

    The same read ``build_reality_check_report`` makes, through the same private
    helper, so the modelled totals this script designs against are the ones the
    reality check will grade. The db paths come back too because the workbook's
    own notes sheet names the database its numbers were derived from.
    """
    from services import datasets, results_evidence, simulation_runs
    from services.agent.tools_run import _collect_run_evidence

    work_dir = simulation_runs.job_dir(str(job_record["id"]))
    db_paths = results_evidence.result_db_paths(work_dir)
    if not db_paths:
        raise SystemExit(
            f"The newest completed job {job_record['id']!r} has no result database under "
            f"{work_dir} — nothing to derive modelled totals from."
        )
    if len(db_paths) > 1:
        raise SystemExit(
            f"Job {job_record['id']!r} wrote {len(db_paths)} result databases. U1 SUMS volumes "
            "across them, so the modelled side would be N runs' worth — refusing to design a "
            "workbook against it (this is MULTI_SEED_VOLUMES_NOT_COMPARABLE, one layer earlier)."
        )
    run_result = _collect_run_evidence(
        work_dir,
        datasets.get_active_inpx_path(),
        duration_s=1,
        evaluation_configured=False,
    )
    return run_result, [str(p) for p in db_paths]


def _reality_check(scenario_id: str) -> dict[str, Any]:
    """The scoring call, with NO targets and NO node_control.

    Exactly what ``routers/calibration.py`` passes, and exactly what
    ``VissimSearchRunner._score`` will pass per candidate — so what stage 1
    prints is what the search will grade against, not a friendlier variant.
    """
    from services.agent.tools_metrics import build_reality_check_report

    return build_reality_check_report(scenario_id, period=PERIOD)


def _graded(report: dict[str, Any]) -> dict[str, float | None]:
    out: dict[str, float | None] = {}
    for node in report.get("nodes") or []:
        node_id = str(node.get("node_id") or "")
        if node_id:
            out[node_id] = node.get("node_total_geh")
    return dict(sorted(out.items(), key=lambda kv: (len(kv[0]), kv[0])))


# ---------------------------------------------------------------------------
# STAGE 0 — the six-node workbook
# ---------------------------------------------------------------------------


def _print_split_proof(entry: dict[str, Any]) -> dict[str, Any]:
    """Prove — not assert — that the designed miss lands on the CALIBRATION side.

    Printed BEFORE a single workbook row is written, because if the stride put
    the miss node in the holdout the whole design would be pointless: the
    optimizer scores ``state.calibration_nodes`` and nothing else, so a search
    whose only miss is held out has no gradient to follow.
    """
    from services.agent import parameter_search as ps

    split = ps.split_nodes(list(WORKBOOK_NODES))
    ordered = sorted(WORKBOOK_NODES, key=lambda n: (len(n), n))
    _log(f"[0.2] SPLIT PROOF for nodes {ordered} (split_nodes is a pure function of this list)")
    _log(f"      MIN_NODES_FOR_HOLDOUT={ps.MIN_NODES_FOR_HOLDOUT}  HOLDOUT_STRIDE={ps.HOLDOUT_STRIDE}")
    for index, node in enumerate(ordered):
        side = "HOLDOUT" if index % ps.HOLDOUT_STRIDE == ps.HOLDOUT_STRIDE - 1 else "calibration"
        marker = "  <-- the designed miss" if node == MISS_NODE else ""
        _log(f"        index {index}: node {node:>3}  index%{ps.HOLDOUT_STRIDE}="
             f"{index % ps.HOLDOUT_STRIDE}  -> {side}{marker}")
    _log(f"      available={split['available']}  calibration={split['calibration']}  "
         f"holdout={split['holdout']}")
    entry["split_proof"] = split
    if not split["available"]:
        raise SystemExit(
            f"STAGE 0 REFUSAL: {ordered} does not split ({split['reason']}). "
            f"At least {ps.MIN_NODES_FOR_HOLDOUT} graded nodes are needed."
        )
    if MISS_NODE not in split["calibration"]:
        raise SystemExit(
            f"STAGE 0 REFUSAL: the designed miss node {MISS_NODE} landed in the HOLDOUT "
            f"{split['holdout']}, not in calibration {split['calibration']}. The optimizer scores "
            "only the calibration side, so this design would give it nothing to improve. Move the "
            "miss to a node the stride leaves in calibration."
        )
    _log(f"      => node {MISS_NODE} is in CALIBRATION and node 8 is a matched HOLDOUT control. OK.")
    return split


def _build_workbook(
    modelled_by_node: dict[str, dict[str, float]],
    design: dict[str, Any],
) -> bytes:
    """The observed side, in the exact layout ``load_base_workbook`` accepts.

    Byte-for-byte the same columns ``calibration_loop_live_check._build_demo_workbook``
    writes (the shipped template's own: SimRun / TimeInt / Period / Movement /
    Direction / QLen / QLenMax / Vehs(All) / Observed). ``Direction`` carries the
    run's OWN Vissim movement OBJECT_ID so every observed number joins back to
    the modelled row it was derived from; ``Vehs(All)`` carries that modelled row
    verbatim (the reality check ignores this column by design — it re-reads the
    modelled side from the run itself — but it makes the derivation auditable by
    eye).

    A ``TravelTimes`` sheet is required: the loader warns without one and
    ``POST /api/validation/base-workbook`` refuses to persist a workbook that
    warns. Filled from the shipped template and relabelled ``DEMO 합성``.
    """
    import openpyxl

    from services import validation

    wb = openpyxl.load_workbook(io.BytesIO(validation.create_base_workbook_template()))
    wb.remove(wb["Counts"])
    counts = wb.create_sheet("Counts", 0)
    counts.append(
        ["SimRun", "TimeInt", "Period", "Movement", "Direction",
         "QLen", "QLenMax", "Vehs(All)", "Observed"]
    )

    time_int = design["time_int"]
    per_node: dict[str, Any] = {}

    for node_id in sorted(WORKBOOK_NODES, key=lambda n: (len(n), n)):
        rows = modelled_by_node[node_id]
        scale = design["scale_by_node"][node_id]
        label = f"{node_id}: DEMO 합성 6노드 노드{node_id}"
        observed_by_id: dict[str, int] = {}
        for object_id, modelled in sorted(rows.items()):
            observed = int(round(modelled * scale))
            observed_by_id[object_id] = observed
            counts.append(
                ["Average", time_int, PERIOD, label, object_id, None, None, modelled, observed]
            )
        total_modelled = round(sum(rows.values()), 2)
        total_observed = sum(observed_by_id.values())
        counts.append(
            ["Average", time_int, PERIOD, label, "Total", None, None, total_modelled, total_observed]
        )
        counts.append([])
        per_node[node_id] = {
            "node_id": node_id,
            "label": label,
            "scale": scale,
            "movement_n": len(rows),
            "modelled_total": total_modelled,
            "observed_total": total_observed,
            "role": "designed miss" if node_id == MISS_NODE else "GEH ~0 control",
            "side": "calibration" if node_id in design["split"]["calibration"] else "holdout",
            "per_movement": {
                k: {"modelled": rows[k], "observed": observed_by_id[k]} for k in sorted(rows)
            },
        }
    design["per_node"] = per_node

    travel = wb["TravelTimes"]
    for row in travel.iter_rows():
        cell = row[2] if len(row) > 2 else None
        if cell is not None and isinstance(cell.value, str) and ":" in cell.value:
            if cell.value.strip().lower() != "vehicletraveltimemeasurement":
                cell.value = f"DEMO 합성 {cell.value}"

    notes = wb.create_sheet("DEMO_NOTES")
    node_lines: list[list[str]] = []
    for node_id, block in per_node.items():
        node_lines.append(
            [f"노드 {node_id} ({block['role']}, {block['side']}): 관측 {block['observed_total']} / "
             f"모델 {block['modelled_total']} (배율 {block['scale']:.6f}), 이동류 {block['movement_n']}건"]
        )
    for line in [
        ["합성 설계 — 보정 아닌 데모/수용 설계"],
        ["SYNTHETIC DESIGN — a demo/acceptance scene, NOT a calibration"],
        [],
        ["이 워크북은 합성 데모 데이터입니다 / THIS WORKBOOK IS SYNTHETIC DEMO DATA"],
        [f"생성 시각(UTC) / generated at: {_now()}"],
        ["생성 도구 / generated by: backend/tools/parameter_search_live_check.py"],
        [f"기준 실행 / derived from run: job {design.get('source_job_id')}"],
        [f"기준 .db / source result db: {design.get('source_db')}"],
        [],
        ["이 머신에는 이 네트워크의 실제 실측 교통량 자료가 없습니다(2026-08-06 확인)."],
        ["No real observed count data for this network exists on this machine (measured 2026-08-06)."],
        ["관측값은 위 실행의 자체 movement 교통량에서 유도했고, 편차는 의도적으로 설계했습니다."],
        ["Observed values are DERIVED from that run's own movement volumes; the deviation is designed."],
        [],
        [f"설계된 미스 = 노드 {MISS_NODE} 하나뿐. 목표 GEH {TARGET_GEH} 를 노드 총량에서 달성하도록"],
        [f"  2(O-M)^2 = G^2(M+O) 를 O 에 대해 역산 (modelled={design.get('miss_modelled_total')}, "
         f"solved O={design.get('miss_solved_observed')})"],
        ["  나머지 다섯 노드의 관측값 = 그 이동류의 모델값(반올림) -> GEH ~ 0 대조군"],
        [],
        [f"왜 노드 {MISS_NODE} 인가 / why node {MISS_NODE}:"],
        [f"  split_nodes 의 stride({design['split']['stride']}) 가 정렬된 6개 노드 중 "
         f"index%3==2 인 것을 홀드아웃으로 보냅니다"],
        [f"  -> calibration={design['split']['calibration']}, holdout={design['split']['holdout']}"],
        ["  옵티마이저는 calibration 노드만 채점하므로(ParameterSearch._Objective), 미스가 홀드아웃에"],
        ["     있으면 개선할 대상이 없습니다. 노드 8 은 매칭된 홀드아웃 대조군이 됩니다."],
        [],
        *node_lines,
        [],
        ["Direction 열 = Vissim movement OBJECT_ID (실행 결과와 1:1 조인)"],
        ["Vehs(All) 열 = 해당 실행의 모델 값 (reality check 는 이 열을 사용하지 않음)"],
        ["TravelTimes 시트 = 로더 통과용 템플릿 샘플. reality check 와 무관."],
    ]:
        notes.append(line)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def stage_0_workbook(client) -> None:
    _banner("[STAGE 0] The six-node observed side — designed synthetic, honestly labelled")
    entry = _stage("0_workbook")
    entry["started_at"] = _now()
    started = time.monotonic()

    from services import workspaces
    from services.agent import parameter_search as ps

    workspace_id = _state["workspace_id"]
    with workspaces.bind_workspace(workspace_id):
        record = _latest_completed_job(workspace_id)
        if record is None:
            raise SystemExit(
                f"STAGE 0 REFUSAL: workspace {workspace_id} has no completed simulation job. "
                "Run tools/calibration_loop_live_check.py --stages 0,1 first — this script designs "
                "a workbook AGAINST a real run's modelled volumes and will not invent them."
            )
        duration_s, duration_source = _baseline_duration_s(record)
        run_result, db_paths = _run_evidence(record)

    entry["source_job_id"] = record.get("id")
    entry["source_scenario_id"] = record.get("scenario_result_id")
    entry["source_db_paths"] = db_paths
    entry["baseline_duration_s"] = duration_s
    entry["baseline_duration_source"] = duration_source
    _state["baseline_duration_s"] = duration_s
    _log(f"[0.1] Source run: job {record.get('id')!r} scenario {record.get('scenario_result_id')!r}")
    _log(f"      result database: {db_paths[0]}")
    _log(f"      duration READ (never guessed) from {duration_source}: {duration_s}s")

    split = _print_split_proof(entry)

    modelled_by_node: dict[str, dict[str, float]] = {}
    for node_id in WORKBOOK_NODES:
        rows = _movement_rows(run_result, node_id)
        if not rows:
            raise SystemExit(
                f"STAGE 0 REFUSAL: node {node_id} has no movement rows in job "
                f"{record.get('id')!r} — cannot design an observed side against an absent "
                "modelled side. Pick a different node set."
            )
        modelled_by_node[node_id] = rows

    miss_modelled = sum(modelled_by_node[MISS_NODE].values())
    solved_observed = _observed_total_for_geh(miss_modelled, TARGET_GEH)
    miss_scale = solved_observed / miss_modelled
    _log(f"[0.3] node {MISS_NODE}: {len(modelled_by_node[MISS_NODE])} movements, modelled total "
         f"{miss_modelled:.2f} veh")
    _log(f"      solving 2(O-M)^2 = G^2(M+O) for G={TARGET_GEH}: O={solved_observed:.2f}")
    _log(f"      => per-movement scale factor f = O/M = {miss_scale:.6f}")
    for node_id in WORKBOOK_NODES:
        if node_id == MISS_NODE:
            continue
        total = sum(modelled_by_node[node_id].values())
        _log(f"      node {node_id}: {len(modelled_by_node[node_id])} movements, modelled "
             f"{total:.2f} veh -- observed = modelled (rounded), f = 1.0 (GEH ~ 0 control)")

    design: dict[str, Any] = {
        "time_int": f"0-{duration_s}",
        "target_geh": TARGET_GEH,
        "miss_node": MISS_NODE,
        "miss_modelled_total": round(miss_modelled, 2),
        "miss_solved_observed": round(solved_observed, 2),
        "scale_by_node": {n: (miss_scale if n == MISS_NODE else 1.0) for n in WORKBOOK_NODES},
        "split": split,
        "source_job_id": record.get("id"),
        "source_db": db_paths[0],
        "self_declaration": "합성 설계 — 보정 아닌 데모/수용 설계",
    }
    content = _build_workbook(modelled_by_node, design)

    for node_id, block in design["per_node"].items():
        block["geh_designed"] = _geh(block["modelled_total"], block["observed_total"])
        _log(f"[0.4] node {node_id} designed: observed {block['observed_total']} vs modelled "
             f"{block['modelled_total']} -> GEH {block['geh_designed']} "
             f"({block['role']}, {block['side']})")
    entry["design"] = design

    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    local_copy = ARTIFACT_DIR / WORKBOOK_FILENAME
    local_copy.write_bytes(content)
    entry["workbook_local_copy"] = str(local_copy)
    entry["workbook_bytes"] = len(content)
    entry["workbook_sha256"] = _sha256(local_copy)
    _log(f"[0.5] Wrote {local_copy.name} ({len(content)} bytes, sha256={entry['workbook_sha256']})")
    _log(f"      DEMO_NOTES sheet self-declares: {design['self_declaration']}")

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
    _log(f"[0.6] Uploaded through POST /api/validation/base-workbook -> {body.get('path')}")
    _log(f"      periods={body.get('periods')} warnings={body.get('warnings')} "
         f"pending_mapping={body.get('pending_mapping')!r}")
    if body.get("pending_mapping"):
        entry["ok"] = False
        _save_state()
        raise SystemExit("Workbook did NOT conform -- it was staged, not persisted. Stopping.")

    _shout([
        "THE PRIOR SCORING RUN IS NOW UNREADABLE, BY DESIGN.",
        "A workbook upload changes the dataset REVISION (it carries",
        "real_counts.xlsx:<mtime>:<size>), and",
        "simulation_runs.get_runtime_result_dir_for_scenario refuses a job whose",
        f"dataset_revision no longer matches. Job {record.get('id')!r} therefore no longer",
        "resolves to a result dir. THIS IS THE KNOWN TRAP, not a fault:",
        "run stage 1 next to produce a scoring run against the six-node workbook.",
    ])

    entry["elapsed_s"] = round(time.monotonic() - started, 2)
    entry["ok"] = True
    _save_state()


# ---------------------------------------------------------------------------
# STAGE 1 — the scoring run
# ---------------------------------------------------------------------------


def _active_workbook_nodes() -> list[str]:
    """The nodes the ACTIVE workbook grades — read from disk, not from state.

    Reported alongside the guard's own verdict so a refusal says WHICH workbook
    is live, not merely that the graded set is wrong. ``node_id`` is
    ``validation.BaseWorkbookCountRow``'s own field.
    """
    from services import datasets, validation

    observed = validation.load_base_workbook(str(datasets.get_active_real_counts_excel()))
    nodes = {
        str(row.get("node_id") or "").strip()
        for row in observed.get("counts") or []
        if str(row.get("node_id") or "").strip()
    }
    return sorted(nodes, key=lambda n: (len(n), n))


def stage_1_scoring_run(client) -> None:
    del client  # this stage is all direct service calls, inside the workspace bind
    duration_s = _state.get("baseline_duration_s")
    if not duration_s:
        raise SystemExit(
            "STAGE 1 REFUSAL: no baseline duration recorded. Run stage 0 first (it READS the "
            "duration from the prior job record and records it) — this stage will not guess one."
        )
    _banner(f"[STAGE 1] REAL scoring run ({duration_s}s, 1 seed) through the agent job path")
    entry = _stage("1_scoring_run")
    entry["started_at"] = _now()
    entry["duration_s"] = duration_s
    entry["random_seeds"] = [RUN_SEED]
    started = time.monotonic()

    import shutil

    from services import datasets, results_evidence, simulation_runs, workspaces
    from services.agent.state import ArtifactManager
    from services.agent.toolkit import default_registry, dispatch
    import services.agent.tools_run as tools_run  # noqa: F401 - registers run_simulation

    with workspaces.bind_workspace(_state["workspace_id"]):
        active_inpx = Path(datasets.get_active_inpx_path())
        entry["active_inpx"] = str(active_inpx)

        # The run source is a WORKING COPY, exactly as routers/assistant.py does
        # (tools_run.set_active_run_source(artifact_manager.working_path)) — and
        # for the measured reason the calibration harness records: the client
        # export self-declares <netPara underScenMngm="true">, which LoadNet
        # refuses, and ArtifactManager.clone() normalizes it (along with
        # numRuns) in the copy.
        work_dir = WORK_ROOT / SCORING_WORK_DIRNAME
        if work_dir.exists():
            shutil.rmtree(work_dir, ignore_errors=True)
        work_dir.mkdir(parents=True, exist_ok=True)
        manager = ArtifactManager(source_inpx=active_inpx, work_dir=work_dir)
        manager.clone()
        working = manager.working_path
        entry["working_copy"] = str(working)
        entry["working_sha256"] = _sha256(working)
        _log(f"[1.1] Run source = the session working copy {working}")
        _log(f"      clone normalized the source: {_sha256(active_inpx) != entry['working_sha256']}")

        tools_run.set_active_run_source(working)
        try:
            _log(f"[1.2] dispatch('run_simulation', duration_s={duration_s}, seeds=[{RUN_SEED}]) "
                 "-- this blocks until the job is terminal")
            envelope = dispatch(
                "run_simulation",
                {"duration_s": int(duration_s), "random_seeds": [RUN_SEED]},
                registry=default_registry,
            )
        finally:
            tools_run.clear_active_run_source()

        entry["envelope_status"] = envelope.get("status")
        entry["envelope_message"] = envelope.get("message")
        verification = envelope.get("verification") or {}
        job_id = verification.get("job_id")
        entry["job_id"] = job_id
        entry["verification"] = verification
        if envelope.get("status") != "ok" or not job_id:
            entry["ok"] = False
            entry["envelope"] = envelope
            _save_state()
            raise SystemExit(
                f"run_simulation FAILED: {json.dumps(envelope, ensure_ascii=False)[:2000]}"
            )

        job_record = simulation_runs.get_job_record(job_id) or {}
        scenario_id = job_record.get("scenario_result_id") or simulation_runs.runtime_scenario_id(job_id)
        db_paths = results_evidence.result_db_paths(simulation_runs.job_dir(job_id))
        entry["scenario_id"] = scenario_id
        entry["result_db_paths"] = [str(p) for p in db_paths]
        entry["result_db_count"] = len(db_paths)
        _log(f"[1.3] job {job_id!r} -> scenario {scenario_id!r}, {len(db_paths)} result database(s)")
        if len(db_paths) != 1:
            entry["ok"] = False
            _save_state()
            raise SystemExit(
                f"Expected exactly ONE result database, got {len(db_paths)}. The reality check "
                "refuses a multi-seed run by name (MULTI_SEED_VOLUMES_NOT_COMPARABLE) because "
                "volumes are SUMMED across databases."
            )

        report = _reality_check(scenario_id)
        entry["report_status"] = report.get("status")
        entry["report_reason_code"] = report.get("reason_code")
        entry["geh_threshold"] = report.get("geh_threshold")
        entry["workbook_warnings"] = report.get("workbook_warnings")
        graded = _graded(report)
        entry["graded"] = graded
        _state["scoring_scenario_id"] = scenario_id

        _log(f"[1.4] reality check status={report.get('status')} "
             f"threshold={report.get('geh_threshold')}")
        for node_id, geh in graded.items():
            role = "designed miss" if node_id == MISS_NODE else "control"
            _log(f"      node {node_id:>3}: GEH {geh}   ({role})")

        report_path = ARTIFACT_DIR / f"reality_check_{scenario_id}.json"
        ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )
        entry["report_artifact"] = str(report_path)

    problems = _six_node_state_problems(graded, report)
    entry["state_problems"] = problems
    if problems:
        entry["ok"] = False
        _save_state()
        _log("")
        for line in problems:
            _log(f"  !! {line}")
        raise SystemExit(
            "STAGE 1 REFUSAL: the scored run is not the six-node state stage 2 requires "
            "(see the lines above)."
        )

    entry["elapsed_s"] = round(time.monotonic() - started, 2)
    entry["ok"] = True
    _save_state()
    _log(f"[1.5] Six-node state CONFIRMED. Scenario {scenario_id!r} is ready for stage 2.")


def _six_node_state_problems(
    graded: dict[str, float | None], report: dict[str, Any]
) -> list[str]:
    """Everything wrong with the current scored state, named. Empty = ready.

    Shared by stage 1 (which produced the run) and stage 2's own guard (which
    must never trust that stage 1 ran) — one definition of "the six-node state",
    so the two cannot disagree about whether a search may start.
    """
    from services import validation

    problems: list[str] = []
    status = str(report.get("status") or "")
    if status and status != "CHECKED":
        problems.append(
            f"the reality check is not gradable: status={status!r} "
            f"reason_code={report.get('reason_code')!r}"
        )
    threshold = report.get("geh_threshold") or validation.GEH_GOOD_LT

    expected = sorted(WORKBOOK_NODES, key=lambda n: (len(n), n))
    actual = sorted(graded, key=lambda n: (len(n), n))
    if actual != expected:
        problems.append(
            f"graded nodes are {actual}, not the designed six {expected} — the active workbook "
            "is probably still the calibration harness's two-node one"
        )
    missing = [n for n, geh in graded.items() if geh is not None and geh >= threshold]
    if missing != [MISS_NODE]:
        problems.append(
            f"nodes missing the GEH criterion are {missing}, expected exactly [{MISS_NODE!r}] — "
            "the designed miss must be the only one, and it must be on the calibration side"
        )
    ungraded = [n for n, geh in graded.items() if geh is None]
    if ungraded:
        problems.append(f"nodes {ungraded} produced no GEH at all — they cannot be searched on")
    return problems


# ---------------------------------------------------------------------------
# STAGE 2 — the search, with the REAL runner
# ---------------------------------------------------------------------------


class _CannedProvider:
    """The ONE seam that is not live besides the gate: the planner.

    Same fixture shape as ``tools/calibration_loop_live_check.py``'s
    ``_CannedProvider`` and ``tests/test_agent_searched_product_path.py``'s
    ``FakeStructuredProvider``. No paid LLM call and no planner variance in the
    middle of an hour of COM.
    """

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


def _open_real_session(client) -> str:
    """A REAL agent session, minted by the product.

    The broker is keyed by session id and the ONLY thing that mints one is an
    agent turn: ``routers/assistant.py`` creates the session dir, writes its
    ``session_meta.json`` (which is what ``sessions.session_workspace_matches``
    reads) and returns the id on the terminal ``done`` event. So this runs one
    minimal turn — a single ``respond`` action through the canned planner —
    exactly the way ``calibration_loop_live_check.stage_4_approval`` opens the
    session it later approves against.

    Nothing about the session is synthesized: the id, its workspace sidecar and
    its work dir are all the product's.
    """
    from routers import assistant as assistant_endpoint

    provider = _CannedProvider([_action("respond", summary="상태 확인", answer="준비됐습니다.")])
    original = assistant_endpoint._default_provider
    assistant_endpoint._default_provider = lambda: provider
    try:
        response = client.post(
            "/api/assistant/agent/query/stream",
            json={"message": "탐색 준비 상태를 알려 주세요", "response_language": "ko"},
            headers=_state["headers"],
        )
    finally:
        assistant_endpoint._default_provider = original
    if response.status_code != 200:
        raise SystemExit(f"Session turn FAILED: {response.status_code} {response.text[:2000]}")
    events = _stream_events(response)
    done = events[-1]
    session_id = (done.get("response") or {}).get("session_id")
    if not session_id:
        raise SystemExit(
            f"The session turn produced no session_id: {json.dumps(done, ensure_ascii=False)[:1500]}"
        )
    return str(session_id)


def _synthesized_gate(report: dict[str, Any], applied_rows: list[dict[str, Any]] | None) -> dict[str, Any]:
    """The precondition record stage 2 hands ``parameter_search.start``.

    ``ok: True`` is the synthesis and it is shouted before this is used. What is
    NOT synthesized is the SPLIT: it is ``split_nodes`` over the report's own
    graded nodes, and both ``parameter_search.start`` (``_verified_split``) and
    ``grant_from_search`` re-derive it and refuse a record that disagrees. The
    ``exhaustion`` block is the REAL measurement, carried verbatim so the state
    json records what the gate actually said while this record claimed to pass.
    """
    from services.agent import parameter_search as ps

    graded = [n for n, geh in _graded(report).items() if geh is not None]
    threshold = report.get("geh_threshold") or 5.0
    missing = [n for n, geh in _graded(report).items() if geh is not None and geh >= threshold]
    exhaustion = ps.measure_exhaustion(report, applied_rows)
    return {
        "ok": True,
        "refusals": [],
        "exhaustion": exhaustion,
        "split": ps.split_nodes(graded),
        "space": [
            {
                "parameter": dim.parameter,
                "low": dim.low,
                "high": dim.high,
                "citation": dim.citation,
                "guideline_wording": dim.guideline_wording,
            }
            for dim in ps.search_space()
        ],
        "missing_nodes": missing,
        "graded_nodes": graded,
        # SELF-DECLARING. Nothing reads this key; it exists so the record in the
        # state json cannot be mistaken for a gate this workspace earned.
        "SYNTHESIZED_BY": "backend/tools/parameter_search_live_check.py stage 2",
        "SYNTHESIZED_LEGS": ["LADDER_NOT_EXHAUSTED", "SIGNAL_STEP_UNVERIFIED"],
        "REAL_GATE_WOULD_SAY": {
            "exhausted": exhaustion["exhausted"],
            "blocking_nodes": exhaustion["blocking_nodes"],
            "per_node": {k: v["verdict"] for k, v in exhaustion["per_node"].items()},
        },
    }


def _shout_the_synthesis(gate: dict[str, Any]) -> None:
    _shout([
        "TWO GATE LEGS ARE SYNTHESIZED IN THIS RUN. NEITHER IS EARNED.",
        "",
        "1. LADDER_NOT_EXHAUSTED — UNREACHABLE THROUGH THE RAIL, NOT MERELY UNMET.",
        "   routers/calibration.py's scoring call passes NO targets (it says so in its",
        "   own docstring), so fixes.map_node_counts arm (a) returns NO_TARGET_OBJECT",
        "   from the node-total arm and RETURNS IMMEDIATELY — the split arm is never",
        "   reached, _d4_refusal is None, and geh_ladder.classify_node answers",
        "   UNCLASSIFIABLE / NO_DEMAND_TARGET. That code is in",
        "   parameter_search.CLOSABLE_MISSING_EVIDENCE, so the node is EVIDENCE_MISSING",
        "   and exhausted stays False however much evidence is collected.",
        f"   The REAL measurement right now: exhausted={gate['REAL_GATE_WOULD_SAY']['exhausted']}"
        f" blocking={gate['REAL_GATE_WOULD_SAY']['blocking_nodes']}",
        "",
        "2. SIGNAL_STEP_UNVERIFIED — no real 현시도 attestation exists for a synthetic",
        "   demo scene. The gate needs rail step 6 to read exactly DONE, which needs a",
        "   CONFIRMED phase-diagram review. Inventing one would forge the artifact the",
        "   step exists to require. (NOT_APPLICABLE is not DONE — the comparison is",
        "   against the literal, so even a signal-free net fails this leg.)",
        "",
        "WHAT IS **NOT** SYNTHESIZED: the split (re-derived by _verified_split and again",
        "by grant_from_search), the ledger (real Vissim runs), the holdout acceptance",
        "(re-derived from that ledger at a seed the search never used), and the grant.",
    ])


def _poll_and_stream(job_id: str, entry: dict[str, Any]) -> dict[str, Any]:
    """Follow the job to a terminal state, printing every candidate boundary.

    Reads the job RECORD only — the same fields the ⑫ track renders. No opinion
    of its own about status: ``job_card``'s ``live`` field decides when to stop.
    """
    from services.agent import search_job, search_runner

    seen_index = 0
    infeasible = 0
    last_beat = time.monotonic()
    while True:
        record = search_job.get_job(job_id)
        if record is None:
            raise SystemExit(f"The job record for {job_id!r} vanished mid-run.")
        card = search_job.job_card(record)
        index = int(record.get("candidates_evaluated") or 0)
        if index != seen_index:
            seen_index = index
            last = record.get("last_candidate") or {}
            reason = str(last.get("reason") or "")
            _log(
                f"      candidate {index:>4}  seed={last.get('seed')}  ok={last.get('ok')}  "
                f"elapsed={record.get('elapsed_s', 0):.0f}s / budget {record.get('budget_s')}s"
                + (f"  reason={reason}" if reason else "")
            )
            # A refused-cheaply candidate is NOT a failure of the run, and the
            # log should not read like one. VECTOR_INFEASIBLE_COUPLING is the LHS
            # screen sampling the box's impossible corner (measured 2026-08-21:
            # roughly half its samples); the runner answers in microseconds and
            # the compass navigates on. Counted separately so a reader can tell
            # "the box has a corner Vissim will not hold" from "Vissim is broken".
            if reason.startswith(search_runner.VECTOR_INFEASIBLE_COUPLING):
                infeasible += 1
                _log(f"           ^ infeasible corner of the search box (#{infeasible} so far) "
                     "-- refused without starting Vissim, by design")
            entry["progress"] = {
                "candidates_evaluated": index,
                "candidates_failed": record.get("candidates_failed"),
                "candidates_infeasible": infeasible,
                "elapsed_s": record.get("elapsed_s"),
                "phase": record.get("phase"),
            }
            _save_state()
        elif time.monotonic() - last_beat > 60:
            _log(f"      ... still {record.get('phase')}, {index} candidate(s), "
                 f"{record.get('elapsed_s', 0):.0f}s elapsed")
            last_beat = time.monotonic()
        if not card["live"]:
            return record
        time.sleep(2.0)


def _prove_the_product_seam(client, session_id: str, search_id: str, entry: dict[str, Any]) -> None:
    """One propose turn on the LIVE broker state — not a fixture.

    ``test_agent_searched_product_path.py`` proves this offline against a search
    a test built. Here the search is the one this script just RAN, the broker
    entry is the one the job thread wrote, and the grant on the card is minted
    from that ledger against the live shadow model.
    """
    from routers import assistant as assistant_endpoint
    from services.agent import parameter_search as ps
    from services.agent import searched_behaviour_broker as broker

    record = broker.recall(search_id, session_id=session_id)
    entry["broker_recall_present"] = record is not None
    if record is None:
        _log("[2.9] The broker does not hold this search in this session — nothing to propose.")
        return
    actions = (record.get("proposal") or {}).get("actions") or []
    if not actions:
        _log("[2.9] The remembered proposal carries no actions (the winner IS the baseline).")
        entry["proposal_actions"] = 0
        return
    params = dict(actions[0].get("params") or {})
    entry["proposal_actions"] = len(actions)
    entry["proposed_write"] = params
    _log(f"[2.9] Proposing the searched winner's first write: {params}")

    provider = _CannedProvider([
        _action(
            ps.SEARCH_TOOL,
            summary=f"탐색 승자 {params.get('parameter')} = {params.get('value')}",
            behavior_no=params.get("behavior_no"),
            parameter=params.get("parameter"),
            value=params.get("value"),
        )
    ])
    original = assistant_endpoint._default_provider
    assistant_endpoint._default_provider = lambda: provider
    try:
        response = client.post(
            "/api/assistant/agent/query/stream",
            json={
                "message": "탐색 승자를 적용해 주세요",
                "response_language": "ko",
                "session_id": session_id,
            },
            headers=_state["headers"],
        )
    finally:
        assistant_endpoint._default_provider = original
    if response.status_code != 200:
        _log(f"[2.9] Propose turn FAILED: {response.status_code} {response.text[:1000]}")
        entry["propose_turn_status"] = response.status_code
        return

    events = _stream_events(response)
    entry["propose_event_types"] = [e.get("type") for e in events]
    card = next((e for e in events if e.get("type") == "approval_request"), None)
    if card is None:
        _log("[2.9] NO approval card was built — the write did not pause for a human.")
        entry["approval_card"] = None
        return
    searched = card.get("searched_search")
    warnings = {w.get("constraint_id") for w in (card.get("warnings") or [])}
    entry["approval_card"] = {
        "approval_id": card.get("approval_id"),
        "warnings": sorted(w for w in warnings if w),
        "searched_search_present": searched is not None,
        "searched_search_id": (searched or {}).get("search_id"),
        "grant_reason": ((searched or {}).get("grant") or {}).get("reason"),
        "grant_available": ((searched or {}).get("grant") or {}).get("available"),
    }
    _log(f"[2.9] APPROVAL CARD built (approval_id={card.get('approval_id')})")
    _log(f"      warnings: {sorted(w for w in warnings if w)}")
    _log(f"      searched_search present: {searched is not None}  "
         f"search_id={(searched or {}).get('search_id')!r}")
    _log(f"      grant: reason={((searched or {}).get('grant') or {}).get('reason')!r} "
         f"available={((searched or {}).get('grant') or {}).get('available')!r}")
    if "driving_behavior_shared_object_edit" in warnings:
        _log("      !! the shared-object refusal is STILL on the card -- the grant did not stand "
             "it down. That is the honest outcome to report, not a failure to hide.")


def stage_2_search(client, behavior_no: str | None, budget_s: float) -> None:
    _banner("[STAGE 2] THE SEARCH — real VissimSearchRunner, real job thread, real broker")
    entry = _stage("2_search")
    entry["started_at"] = _now()
    started = time.monotonic()

    from services import datasets, workspaces
    from services.agent import parameter_search as ps
    from services.agent import search_job, searched_behaviour_grant
    from services.agent import search_runner
    from services.agent.tools_network import build_shadow_model

    workspace_id = _state["workspace_id"]

    # ---- the ONE leg of evaluate_start this stage must re-check itself ------
    #
    # Building a StartPlan by hand bypasses ``search_job.evaluate_start``, and
    # with it every start refusal that function owns: RUNNER_NOT_WIRED,
    # SESSION_NOT_IN_WORKSPACE, NO_ACTIVE_NETWORK and the courteous
    # SEARCH_ALREADY_RUNNING pre-check. Most of those this stage establishes by
    # construction (it reads the active network, it mints its own session, and
    # ``search_job.start``'s atomic ``_claim`` still enforces one-at-a-time).
    #
    # RUNNER_NOT_WIRED does not establish itself, and its failure mode is quiet
    # rather than loud: with no pywin32 EVERY candidate returns
    # RUNNER_NOT_AVAILABLE in microseconds, the budget is never consumed, the
    # engine converges on nothing and reports COMPLETE with NO_CANDIDATE — a
    # search that "finished" having run no simulation at all. One named refusal
    # up front beats fifty silent ones.
    available, unavailable_reason = search_runner.runner_availability()
    entry["runner_available"] = available
    entry["runner_unavailable_reason"] = unavailable_reason
    if not available:
        entry["ok"] = False
        _save_state()
        raise SystemExit(
            f"STAGE 2 REFUSAL [RUNNER_NOT_WIRED]: {unavailable_reason} "
            "This box cannot drive Vissim, so every candidate would fail instantly and the "
            "search would report COMPLETE having simulated nothing."
        )
    _log(f"[2.0] Runner availability: WIRED ({search_runner.RUNNER_IMPLEMENTATION})")

    # ---- MATERIALIZATION PRE-FLIGHT (added 2026-08-21, after the live run) --
    #
    # The first live acceptance spent an hour discovering, one Vissim start-up
    # at a time, that its candidates could not be LoadNet-ed: the client export
    # is scenario-managed and the runner was copying it raw. The runner now
    # materializes and reads the flag back per candidate — but the operator
    # should learn that the FIRST candidate would fail before the budget starts,
    # not after it ends. This materializes one throwaway candidate from the very
    # file the search will clone and asks the runner's own predicate about it.
    # Costs one file copy and one read; catches the exact failure this round fixed.
    with workspaces.bind_workspace(workspace_id):
        preflight_source = Path(datasets.get_active_inpx_path())
        preflight_dir = WORK_ROOT / f"{SEARCH_JOB_DIRNAME}_preflight"
        probe = search_runner.VissimSearchRunner(
            source_inpx=preflight_source,
            work_dir=preflight_dir,
            behavior_no=0,
            duration_s=1,
        )
        probe_inpx, probe_failure = probe._candidate_copy(0, 0, preflight_source)
        entry["materialization_preflight"] = {
            "source": str(preflight_source),
            "candidate": str(probe_inpx),
            "failure": probe_failure,
        }
        if probe_failure is not None:
            entry["ok"] = False
            _save_state()
            raise SystemExit(
                f"STAGE 2 REFUSAL: the candidate materialization itself fails — {probe_failure}\n"
                "Every candidate would fail the same way. Nothing was run."
            )
        raw = preflight_source.read_text(encoding="utf-8", errors="replace")
        made = probe_inpx.read_text(encoding="utf-8", errors="replace")
        blocker = 'underScenMngm="true"'
        entry["materialization_preflight"].update(
            {"source_scenario_managed": blocker in raw, "candidate_scenario_managed": blocker in made}
        )
        _log("[2.0b] Materialization pre-flight OK — a candidate built from the active dataset "
             "is standalone-loadable.")
        _log(f"       source declares {blocker}: {blocker in raw}  "
             "(true = a raw copy would NOT load -- this is the 2026-08-21 finding)")
        _log(f"       candidate declares {blocker}: {blocker in made}  (must be False)")
        import shutil as _shutil

        _shutil.rmtree(preflight_dir, ignore_errors=True)

    # ---- THE GUARD: never search the two-node state silently ---------------
    with workspaces.bind_workspace(workspace_id):
        scenario_id = _state.get("scoring_scenario_id")
        if not scenario_id:
            record = _latest_completed_job(workspace_id)
            scenario_id = (record or {}).get("scenario_result_id")
        if not scenario_id:
            raise SystemExit(
                "STAGE 2 REFUSAL: no scored run is recorded or discoverable for this workspace. "
                "Run stages 0 and 1 first."
            )
        report = _reality_check(str(scenario_id))
        graded = _graded(report)
        problems = _six_node_state_problems(graded, report)
        entry["guard"] = {
            "scenario_id": scenario_id,
            "graded": graded,
            "problems": problems,
            "workbook_nodes": _active_workbook_nodes(),
        }
        _log(f"[2.1] GUARD: scoring run {scenario_id!r}, graded nodes {list(graded)}")
        for node_id, geh in graded.items():
            _log(f"      node {node_id:>3}: GEH {geh}")
        if problems:
            entry["ok"] = False
            _save_state()
            _log("")
            for line in problems:
                _log(f"  !! {line}")
            raise SystemExit(
                "STAGE 2 REFUSAL: this workspace is NOT in the six-node state. Refusing to run an "
                "hour of Vissim against the wrong observed side — run --stages 0,1 first. "
                "(A search scored against the two-node workbook would produce a winner nobody "
                "designed and a holdout of one node.)"
            )
        _log("      GUARD PASSED: six graded nodes, the designed miss is the only one.")

        # ---- the target behaviour ------------------------------------------
        active_inpx = Path(datasets.get_active_inpx_path())
        shadow = build_shadow_model(active_inpx)
        candidates = search_job.searchable_behaviours(shadow)
        entry["searchable_behaviours"] = candidates
        _log(f"[2.2] Searchable driving behaviours (all three parameters readable): "
             f"{[c['no'] for c in candidates]}")
        for candidate in candidates:
            _log(f"      behaviour {candidate['no']!r} {candidate['name']!r} "
                 f"references={candidate['reference_count']} params={candidate['params']}")
        behaviour, refusal = search_job.resolve_target_behaviour(shadow, behavior_no)
        if refusal is not None:
            entry["behaviour_refusal"] = refusal
            entry["ok"] = False
            _save_state()
            raise SystemExit(
                f"STAGE 2 REFUSAL [{refusal['code']}]: {refusal['why']['en']}\n"
                "Pass --behavior-no <no> to name one."
            )
        entry["behaviour"] = {k: behaviour.get(k) for k in ("no", "name", "reference_count")}
        radius = searched_behaviour_grant.consent_radius(behaviour)
        entry["consent_radius"] = radius
        _log(f"[2.3] Target behaviour {behaviour.get('no')!r} {behaviour.get('name')!r}")
        _log(f"      consent radius complete={radius.get('complete')} "
             f"({radius.get('referenced_by') or radius})")
        if not radius.get("complete"):
            _log("      !! the radius is NOT nameable in full, so a grant will be refused "
                 "(RADIUS_NOT_FULLY_NAMED). The search will still run and be remembered; the "
                 "approval card at [2.9] will show no grant. Reported, not hidden.")

        baseline_vector = {
            name: float(behaviour[name]) for name in ps.SEARCHABLE_PARAMETERS
        }
        _log(f"[2.4] Baseline vector (the behaviour's own current values): {baseline_vector}")
        for dim in ps.search_space():
            value = baseline_vector[dim.parameter]
            clipped = dim.clip(value)
            note = "" if clipped == value else f"  -> CLIPPED to {clipped} by the published band"
            _log(f"      {dim.parameter}: {value}  band [{dim.low}, {dim.high}]{note}")

    # ---- the synthesized gate, shouted -------------------------------------
    #
    # ``applied_rows=None`` on purpose: this record does NOT claim the Table 5-6
    # prescription is applied. It is passed to ``measure_exhaustion`` only so the
    # REAL_GATE_WOULD_SAY block records the true measurement — and on the
    # unreachable-ladder path that measurement never reaches the Table 5-6 leg
    # anyway (TABLE_5_6_NOT_APPLIED fires only for PRESCRIPTION_PENDING nodes,
    # which requires the CAPACITY_LIMIT rung this scene cannot reach).
    gate = _synthesized_gate(report, None)
    entry["preconditions"] = gate
    _shout_the_synthesis(gate)

    verified = ps._verified_split(gate["split"])
    entry["verified_split"] = verified
    _log(f"[2.5] _verified_split's OWN answer (not this script's): {verified}")
    _log(f"      calibration={verified['calibration']}  holdout={verified['holdout']}")
    if MISS_NODE not in verified["calibration"]:
        entry["ok"] = False
        _save_state()
        raise SystemExit(
            f"STAGE 2 REFUSAL: node {MISS_NODE} is not on the calibration side of the split the "
            f"engine re-derived ({verified}). The optimizer would have nothing to improve."
        )

    # ---- a REAL session ----------------------------------------------------
    session_id = _open_real_session(client)
    _state["session_id"] = session_id
    entry["session_id"] = session_id
    _log(f"[2.6] REAL agent session minted by the product: {session_id!r}")
    _log("      (one canned-planner 'respond' turn through POST /api/assistant/agent/query/stream "
         "-- the same seam calibration_loop_live_check stage 4 uses)")

    # ---- the plan ----------------------------------------------------------
    duration_s = int(_state["baseline_duration_s"])
    clamped_budget, was_clamped = search_job.clamp_budget_s(budget_s)
    plan = search_job.StartPlan(
        preconditions=gate,
        behaviour=dict(behaviour),
        baseline_vector=baseline_vector,
        scoring=search_runner.ScoringContext(period=PERIOD),
        budget_s=clamped_budget,
        budget_clamped=was_clamped,
        duration_s=duration_s,
        duration_clamped=False,
        session_id=session_id,
        workspace_id=workspace_id,
        source_inpx=active_inpx,
        scenario_id=str(scenario_id),
    )
    job_root = WORK_ROOT / SEARCH_JOB_DIRNAME
    entry["plan"] = {
        "budget_s": clamped_budget,
        "budget_clamped": was_clamped,
        "duration_s": duration_s,
        "seed": search_job.SEARCH_SEED,
        "holdout_seed": search_job.HOLDOUT_SEED,
        "scoring": plan.scoring.as_kwargs(),
        "work_root": str(job_root),
        "runner_factory": f"{search_job.runner_factory.__module__}.{search_job.runner_factory.__name__}",
    }
    _log(f"[2.7] StartPlan: budget {clamped_budget}s wall (clamped={was_clamped}), "
         f"{duration_s}s sim per candidate")
    _log(f"      seeds: search={search_job.SEARCH_SEED} holdout={search_job.HOLDOUT_SEED} "
         "(the holdout seed is one the search never uses)")
    _log(f"      scoring context threaded through: {plan.scoring.as_kwargs()}")
    _log(f"      runner_factory = {entry['plan']['runner_factory']} "
         "-- UNTOUCHED, so every candidate is a real Vissim run")
    _log(f"      candidate work dirs: {job_root}")
    _log("")
    _log("      COST PROJECTION (read this before walking away):")
    _log(f"        the engine charges the budget from each candidate's REAL wall clock.")
    _log(f"        baseline + {ps.DEFAULT_SCREENING_SAMPLES} LHS screening samples + the compass")
    _log("        search; at ~25-30s wall per 900s sim on this demo net that is roughly")
    _log(f"        {int(clamped_budget // 28)} candidates inside a {clamped_budget:.0f}s budget.")
    _log("        If the compass needs more, the honest answer is PARTIAL_BUDGET_EXHAUSTED and")
    _log("        NOTHING reaches the broker -- by design (searched_behaviour_broker L49-58).")
    _save_state()

    record = search_job.start(plan, work_root=job_root)
    if record is None:
        entry["ok"] = False
        _save_state()
        raise SystemExit(
            "STAGE 2 REFUSAL [SEARCH_ALREADY_RUNNING]: this workspace already has a live search "
            "job in this process. One Vissim, one search."
        )
    job_id = str(record["id"])
    entry["job_id"] = job_id
    _log("")
    _log(f"[2.8] SEARCH JOB {job_id!r} started (status={record.get('status')}). Streaming progress:")
    _save_state()

    if record.get("status") == search_job.FAILED:
        entry["ok"] = False
        entry["terminal"] = search_job.job_card(record)
        _save_state()
        raise SystemExit(
            f"STAGE 2 REFUSAL [{record.get('terminal_reason')}]: the job thread never started "
            f"({record.get('error')}). Nothing ran."
        )

    final = _poll_and_stream(job_id, entry)
    card = search_job.job_card(final)
    entry["terminal"] = card
    entry["elapsed_s"] = round(time.monotonic() - started, 2)
    _save_state()

    _log("")
    _log(f"[2.9] TERMINAL: status={card['status']}  reason={card.get('terminal_reason')!r}")
    _log(f"      candidates evaluated={card['candidates_evaluated']} "
         f"(failed {card['candidates_failed']}, of which "
         f"{(entry.get('progress') or {}).get('candidates_infeasible', 0)} were the search box's "
         "infeasible corner -- refused without starting Vissim)")
    _log(f"      elapsed {card['elapsed_s']:.0f}s of {card['budget_s']:.0f}s budget; "
         f"engine consumed {card['consumed_s']:.0f}s")
    _log(f"      search status={final.get('search_status')!r} "
         f"acceptance={card.get('acceptance_code')!r} proposal={card.get('proposal_outcome')!r}")
    winner = card.get("winner")
    if winner:
        _log(f"      WINNER vector: {winner.get('params')}")
        _log(f"      objective (mean GEH over calibration nodes) = {winner.get('objective')}  "
             f"baseline = {winner.get('baseline_objective')}")
        _log(f"      pass_count={winner.get('pass_count')} graded={winner.get('graded')}")
    else:
        _log("      WINNER: none (no proposable row)")
    _log(f"      remembered={card['remembered']}  search_id={card.get('search_id')!r}  "
         f"session={card.get('session_id')!r}")

    if card["status"] == search_job.COMPLETE and card.get("search_id"):
        with workspaces.bind_workspace(workspace_id):
            from services.agent import searched_behaviour_broker as broker

            remembered = broker.recall(str(card["search_id"]), session_id=session_id)
            ledger = ((remembered or {}).get("result") or {}).get("state", {}).get("ledger") or []
            entry["ledger_rows"] = len(ledger)
            holdout_rows = [r for r in ledger if r.get("node_set") == "HOLDOUT"]
            entry["holdout_rows"] = len(holdout_rows)
            _log(f"      LEDGER: {len(ledger)} rows, {len(holdout_rows)} of them HOLDOUT "
                 f"(seeds {sorted({r.get('seed') for r in holdout_rows})})")
            _prove_the_product_seam(client, session_id, str(card["search_id"]), entry)
        entry["ok"] = True
    elif card["status"] == search_job.PARTIAL:
        entry["ok"] = False
        _log("")
        _log("      PARTIAL — the budget ran out before the search converged.")
        _log(f"      NOTHING reached the broker (remembered={card['remembered']}), by design:")
        _log("      a grant may only be minted from a search THIS PROCESS ran to COMPLETE.")
        _log(f"      audit artifact: {final.get('partial_state_path')!r}")
        _log(f"      resume-from-disk is a NAMED non-feature: {card['resume_from_disk']}")
    else:
        entry["ok"] = False
        _log("")
        _log(f"      {card['status']} — {card['headline']['en']}")
        if final.get("error"):
            _log(f"      error: {final['error']}")

    _save_state()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Unit #51's live runner, end to end, against a real Vissim."
    )
    parser.add_argument("--stages", default="0,1,2")
    parser.add_argument(
        "--workspace",
        default=DEFAULT_WORKSPACE_ID,
        help=f"the project to run in. Default {DEFAULT_WORKSPACE_ID} (the demo the calibration "
        "harness built). This script never CREATES a workspace: it extends one that already "
        "carries a real network and a real baseline run.",
    )
    parser.add_argument(
        "--label",
        default="",
        help="suffix for state/log/artifacts/work dirs, e.g. --label _reproof, so a re-run "
        "lands beside earlier evidence instead of on top of it",
    )
    parser.add_argument(
        "--behavior-no",
        default=None,
        help="stage 2: which driving behaviour to search. Optional only when the network has "
        "exactly ONE behaviour with all three searched parameters readable; with several the "
        "run refuses by name (BEHAVIOUR_NOT_CHOSEN) rather than picking one.",
    )
    parser.add_argument(
        "--budget-s",
        type=float,
        default=DEFAULT_BUDGET_S,
        help=f"stage 2: the search's WALL-CLOCK ceiling in seconds (default {DEFAULT_BUDGET_S:.0f}). "
        "Clamped through search_job.clamp_budget_s, the same bounds the HTTP route enforces, "
        "and the clamp is printed. The engine charges this from each candidate's real wall "
        "clock, so it is a run-count budget in disguise.",
    )
    parser.add_argument(
        "--keep-vissim",
        action="store_true",
        help="do not kill Vissim processes this script started (for post-mortem inspection). "
        "Processes that were already running are NEVER touched either way.",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    stages = {int(s) for s in args.stages.split(",") if s.strip()}
    if args.label:
        _apply_label(args.label)

    WORK_ROOT.mkdir(parents=True, exist_ok=True)
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    _log("")
    _log("#" * 78)
    _log(f"# PARAMETER-SEARCH LIVE CHECK  {_now()}")
    _log(f"# workspace={args.workspace!r} stages={sorted(stages)} label={args.label!r}")
    _log(f"# budget_s={args.budget_s} behavior_no={args.behavior_no!r}")
    _log("# The RUNNER is real (services.agent.search_runner.VissimSearchRunner).")
    _log("# The PLANNER and TWO GATE LEGS are synthesized -- stage 2 shouts both before")
    _log("# it starts, and the state json records what the real gate would have said.")
    _log("#" * 78)

    # Bounded runs: never write gigabytes of vehicle records for a probe.
    os.environ.setdefault("AGENT_RUN_TRAJECTORY_MAX_S", "1")

    _state.update(_load_state())
    stored_workspace = _state.get("workspace_id")
    if stored_workspace and stored_workspace != args.workspace:
        raise SystemExit(
            f"WORKSPACE MISMATCH: {STATE_PATH.name} was produced against workspace "
            f"{stored_workspace!r} and this invocation says {args.workspace!r}. The workbook, the "
            "run and the search all live in one project -- use --label to start a fresh evidence "
            "set, or re-run against the recorded workspace."
        )
    _state["workspace_id"] = args.workspace
    _state["run_started_at"] = _now()
    _state["stages_requested"] = sorted(stages)
    _state["label"] = args.label
    _state["budget_s"] = args.budget_s
    _state["behavior_no"] = args.behavior_no
    _state["workbook_nodes"] = list(WORKBOOK_NODES)
    _state["miss_node"] = MISS_NODE

    pids_before = _vissim_pids()
    _state["vissim_pids_before_probe"] = sorted(pids_before)
    _log(f"Vissim PIDs already running (NEVER touched): {sorted(pids_before) or 'none'}")
    _save_state()

    import main as app_main
    from fastapi.testclient import TestClient

    failure: str | None = None
    try:
        with TestClient(app_main.app) as client:
            _log("")
            _log("[auth] borrowing an existing admin for the project-scoped HTTP calls")
            _state["headers"] = {
                "X-Workspace-Id": args.workspace,
                **_admin_bearer_headers(),
            }
            _save_state()

            if 0 in stages:
                stage_0_workbook(client)
            if 1 in stages:
                stage_1_scoring_run(client)
            if 2 in stages:
                stage_2_search(client, args.behavior_no, args.budget_s)
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
        orphans = sorted(_vissim_pids() - pids_before)
        _state["vissim_pids_started_by_probe"] = orphans
        if orphans and not args.keep_vissim:
            killed = _kill_new_vissim(pids_before)
            _state["vissim_pids_killed"] = killed
            _log(f"Cleaned up Vissim processes this probe started: {killed}")
        elif orphans:
            _log(f"--keep-vissim: leaving {orphans} running for inspection")
        _save_state()

    _banner("RESULT")
    for key, value in sorted((_state.get("stages") or {}).items()):
        _log(f"  {key}: ok={value.get('ok')} elapsed={value.get('elapsed_s')}s")
    search = (_state.get("stages") or {}).get("2_search") or {}
    terminal = search.get("terminal") or {}
    if terminal:
        _log(f"  search: status={terminal.get('status')} "
             f"candidates={terminal.get('candidates_evaluated')} "
             f"remembered={terminal.get('remembered')} search_id={terminal.get('search_id')!r}")
        _log(f"  card:   {search.get('approval_card')}")
    _log(f"  overall: {'FAILED -- ' + failure.splitlines()[0] if failure else 'COMPLETE'}")
    _log(f"  evidence: {STATE_PATH}")
    _save_state()
    return 1 if failure else 0


if __name__ == "__main__":
    # A CLI run has no HTTP request to scope it to a project, and
    # services/workspaces.py refuses to guess one. Every stage binds the target
    # workspace explicitly; this outer binding only covers the app-startup path.
    from services.workspaces import bind_workspace, default_workspace_id

    with bind_workspace(default_workspace_id()):
        raise SystemExit(main())
