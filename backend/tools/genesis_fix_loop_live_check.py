"""GENESIS FIX-JOB LIVE CHECK: one approved verb, applied via REAL Vissim COM,
against a REAL refused genesis job -- roadmap B5-2 stage 2's live proof.

``backend/tests/test_genesis_fix_jobs.py`` is this script's offline sibling.
It proves the WHOLE chain -- clone the refused parent's artifact -> open a
COM edit session -> preflight -> dispatch the approved verb -> postwrite ->
re-gate as a sibling job -> (if not refused) publish -- with ONE seam faked:
``genesis_jobs._build_fix_edit_session``, patched to a fake session so no
real Vissim needs to be running in pytest. This script runs the identical
``create_genesis_fix_job`` + ``run_genesis_fix_job`` pair UNPATCHED, so the
edit session, the SetAttValue write, the read-back and the SaveNetAs are all
real COM, against a real refused job already sitting in this box's
``runtime/`` (never a synthetic fixture).

WHAT IS REAL HERE
------------------
* the parent job: read straight off THIS box's genesis job registry, by id
  the caller supplies -- nothing here creates one;
* ``genesis_jobs.create_genesis_fix_job`` -- the clone, the live re-validation
  of the approved verb/params against the CURRENT tool registry, the
  ``derived_from`` lineage record;
* ``genesis_jobs.run_genesis_fix_job`` -- ``_build_fix_edit_session`` is NOT
  patched (mirrors ``calibration_loop_live_check.py``'s own "the approve
  leg: NO _build_edit_session patch. Real COM." convention), so this opens a
  genuine ``VissimEditSession`` (``attach_to_running=False``, per
  ``docs/phase2/00-EXECUTION-GUIDE.md`` rule 3), dispatches the ONE verb
  through the real tool registry with the REAL ``GuardrailEngine``
  preflight/postwrite sandwich, and SaveNetAs's onto the sibling job's own
  artifact;
* the re-gate: the UNMODIFIED ``genesis_jobs.gate_and_publish``, exactly as
  every other exit from this module.

NOT REAL, and deliberately so
------------------------------
* the FIX PROPOSAL itself, when ``--yes`` is used instead of an explicit
  ``--verb``/``--params``: this script calls
  ``services.agent.genesis_fixes.propose_for_gate_finding`` the same way a
  human reviewer's UI would, but with NO caller-supplied evidence
  (``targets=None``) -- most AUTOMATABLE catalogue entries need operator
  evidence (a measured volume, a census-derived polyline) this script cannot
  invent, so ``--yes`` only ever fires on a proposal that needed none. Pass
  ``--verb``/``--params``/``--finding-code`` explicitly for anything else --
  exactly the (verb, params) an approval UI would already have collected
  from a human.

THE CENTRAL ASSERTION
----------------------
The parent job's ``gate.json`` sha256 BEFORE this script runs must equal its
sha256 AFTER. If it does not, the diagnosis a refusal exists to preserve was
silently destroyed, which is the one failure mode this whole roadmap unit
exists to make structurally impossible -- so this script treats that
inequality as the loudest possible failure, checked first, printed first.

Usage (from ``backend/``, with the venv python)::

    # Preview: list the parent's refuse-tier findings and whatever proposals
    # need no operator evidence. Creates nothing.
    python tools/genesis_fix_loop_live_check.py --job-id <refused-job-id>

    # Auto-apply the first proposal that needs no operator evidence.
    python tools/genesis_fix_loop_live_check.py --job-id <refused-job-id> --yes

    # Apply an explicit, already-approved (verb, params) -- the normal case,
    # mirroring what a human reviewer's approval click would supply.
    python tools/genesis_fix_loop_live_check.py --job-id <refused-job-id> \\
        --finding-code v6_input_volume_is_zero \\
        --verb set_vehicle_input_volume \\
        --params "{\\"input_no\\": \\"113\\", \\"volume\\": 500, \\"interval_no\\": 1}"

    # If the job's workspace is not the default one:
    python tools/genesis_fix_loop_live_check.py --job-id <id> --workspace-id <workspace-id> --yes
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
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

WORK_ROOT = REPO_ROOT / "runtime" / "genesis-fix-loop-live-check"
STATE_PATH = WORK_ROOT / "state.json"
LOG_PATH = WORK_ROOT / "log.txt"

_state: dict[str, Any] = {}


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


def _banner(text: str) -> None:
    _log("")
    _log("=" * 78)
    _log(text)
    _log("=" * 78)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _save_state() -> None:
    WORK_ROOT.mkdir(parents=True, exist_ok=True)
    import os

    tmp = STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(_state, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, STATE_PATH)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


# ---------------------------------------------------------------------------
# Workspace / job resolution
# ---------------------------------------------------------------------------


def _resolve_workspace_for_job(job_id: str, explicit_workspace_id: str | None) -> str:
    """The workspace ``job_id`` lives in.

    ``--workspace-id`` short-circuits this entirely. Otherwise every
    workspace on the box is tried in turn (mirrors
    ``genesis_jobs.reconcile_interrupted_jobs``'s own iteration) -- a live
    check is run by an operator who knows the job id from the UI, not
    necessarily the workspace id underneath it.
    """
    from services import genesis_jobs, workspaces

    if explicit_workspace_id:
        return explicit_workspace_id

    for entry in workspaces.list_workspaces():
        workspace_id = str(entry.get("id") or "")
        if not workspace_id:
            continue
        try:
            with workspaces.bind_workspace(workspace_id):
                genesis_jobs.get_job(job_id)
        except genesis_jobs.GenesisJobError:
            continue
        return workspace_id

    raise SystemExit(
        f"Could not find job {job_id!r} in any workspace on this box. Pass --workspace-id "
        "explicitly if you know it."
    )


# ---------------------------------------------------------------------------
# Proposals -- Stage 1's registry, called the way an approval UI would
# ---------------------------------------------------------------------------


def _refuse_tier_findings(gate_json: dict[str, Any]) -> list[dict[str, Any]]:
    """Every refuse-tier FAIL finding in a stored ``gate.json``, flattened
    across checks -- the raw dicts ``genesis_fixes.propose_for_gate_finding``
    reads directly (it accepts a ``Mapping`` with ``code``/``subject``/
    ``context`` keys, the exact shape ``GateFinding`` serializes to)."""
    findings: list[dict[str, Any]] = []
    for check in gate_json.get("checks", []):
        if check.get("tier") != "refuse" or check.get("state") != "fail":
            continue
        for finding in check.get("findings", []):
            findings.append(finding)
    return findings


def _proposals_needing_no_evidence(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """``propose_for_gate_finding(finding)`` for each, ``targets=None`` --
    only what Stage 1's registry can propose with ZERO operator-supplied
    evidence. Returns one dict per finding: ``{finding, proposal}``."""
    from services.agent import genesis_fixes

    out: list[dict[str, Any]] = []
    for finding in findings:
        proposal = genesis_fixes.propose_for_gate_finding(finding, census=None, targets=None)
        out.append({"finding": finding, "proposal": proposal.to_dict()})
    return out


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--job-id", required=True, help="the REFUSED parent genesis job's id")
    parser.add_argument("--workspace-id", default=None, help="skip the workspace search")
    parser.add_argument(
        "--finding-code",
        default="operator_supplied",
        help="carried on derived_from.finding_code -- the gate finding this fix answers",
    )
    parser.add_argument("--verb", default=None, help="an explicitly-approved verb name")
    parser.add_argument(
        "--params", default=None, help="the approved verb's params, as a JSON object string"
    )
    parser.add_argument(
        "--approved-by", default="genesis_fix_loop_live_check", help="recorded on derived_from"
    )
    parser.add_argument(
        "--approval-rationale", default="Live-check operator run.", help="recorded on derived_from"
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="auto-apply the first proposal that needs no operator-supplied evidence "
        "(no effect if --verb is given)",
    )
    args = parser.parse_args()

    WORK_ROOT.mkdir(parents=True, exist_ok=True)
    _banner(f"GENESIS FIX-JOB LIVE CHECK  {_now()}")
    _log(f"job_id={args.job_id!r} workspace_id={args.workspace_id!r} yes={args.yes}")
    _state.update({"started_at": _now(), "job_id": args.job_id})
    _save_state()

    from services import datasets, genesis_jobs, workspaces

    workspace_id = _resolve_workspace_for_job(args.job_id, args.workspace_id)
    _log(f"resolved workspace_id={workspace_id!r}")

    failure: str | None = None
    try:
        with workspaces.bind_workspace(workspace_id):
            parent = genesis_jobs.get_job(args.job_id)
            if parent.get("status") != genesis_jobs.STATUS_REFUSED:
                raise SystemExit(
                    f"Job {args.job_id} is {parent.get('status')!r}, not "
                    f"{genesis_jobs.STATUS_REFUSED!r}. This script only runs against a "
                    "refused parent -- create_genesis_fix_job would refuse it anyway "
                    "(genesis_fix_parent_not_refused), but the point of THIS script is to "
                    "prove the fix pipeline, not its own guard rails."
                )

            parent_gate_path = genesis_jobs.job_dir(args.job_id) / genesis_jobs.GATE_JSON_FILENAME
            gate_before_sha = _sha256_file(parent_gate_path)
            _log(f"parent gate.json sha256 BEFORE = {gate_before_sha}")

            gate_json = json.loads(parent_gate_path.read_text(encoding="utf-8"))
            findings = _refuse_tier_findings(gate_json)
            _log(f"parent has {len(findings)} refuse-tier finding(s): "
                 f"{[f.get('code') for f in findings]}")

            verb: str | None = args.verb
            params: dict[str, Any] | None = None
            finding_code = args.finding_code
            if verb is not None:
                params = json.loads(args.params) if args.params else {}
            else:
                candidates = _proposals_needing_no_evidence(findings)
                for entry in candidates:
                    _log(
                        f"  finding={entry['finding'].get('code')} -> "
                        f"outcome={entry['proposal']['outcome']} "
                        f"verb={entry['proposal']['verb']} "
                        f"reason={entry['proposal']['reason_code']}"
                    )
                if not args.yes:
                    _banner("PREVIEW ONLY -- pass --yes or --verb/--params to actually run")
                    _log(json.dumps(candidates, ensure_ascii=False, indent=2, default=str))
                    return 0
                automatable = next(
                    (e for e in candidates if e["proposal"]["outcome"] == "PROPOSAL"), None
                )
                if automatable is None:
                    raise SystemExit(
                        "No proposal was available without operator-supplied evidence "
                        "(--yes only auto-applies one of those). Pass --verb/--params/"
                        "--finding-code explicitly for a fix that needs a human-supplied value."
                    )
                verb = automatable["proposal"]["verb"]
                params = automatable["proposal"]["params"]
                finding_code = automatable["finding"].get("code") or finding_code

            assert verb is not None and params is not None  # for the type checker
            _log(f"approved verb={verb!r} params={params!r} finding_code={finding_code!r}")

            fix_record = genesis_jobs.create_genesis_fix_job(
                args.job_id,
                finding_code=finding_code,
                verb=verb,
                params=params,
                approved_by=args.approved_by,
                approval_rationale=args.approval_rationale,
            )
            fix_job_id = fix_record["id"]
            _log(f"created sibling fix job {fix_job_id!r} (work dir cloned from the parent)")

            # THE live COM leg -- _build_fix_edit_session is NOT patched here,
            # unlike backend/tests/test_genesis_fix_jobs.py. Real VissimEditSession,
            # real preflight/dispatch/postwrite, real SaveNetAs.
            result = genesis_jobs.run_genesis_fix_job(fix_job_id, workspace_id)

            gate_after_sha = _sha256_file(parent_gate_path)
            _log(f"parent gate.json sha256 AFTER  = {gate_after_sha}")

            new_gate = result.get("gate") or {}
            new_artifact_sha = None
            try:
                new_artifact_sha = _sha256_file(genesis_jobs.artifact_path(fix_job_id, "inpx"))
            except genesis_jobs.GenesisJobNotFoundError:
                pass  # a job that errored before an artifact re-hash carries none

            active = datasets.get_active_dataset()

            _state["result"] = {
                "parent_job_id": args.job_id,
                "parent_gate_sha_before": gate_before_sha,
                "parent_gate_sha_after": gate_after_sha,
                "parent_gate_sha_unchanged": gate_before_sha == gate_after_sha,
                "fix_job_id": fix_job_id,
                "fix_job_status": result.get("status"),
                "fix_job_verdict": new_gate.get("verdict"),
                "fix_job_artifact_sha256": new_artifact_sha,
                "fix_job_refusal": result.get("refusal"),
                "active_dataset_source": active.get("source"),
                "active_dataset_filename": active.get("filename"),
            }
    except SystemExit as exc:
        failure = str(exc)
        _log(f"!! STOPPED: {failure}")
    except Exception:
        failure = traceback.format_exc()
        _log(f"!! EXCEPTION:\n{failure}")
    finally:
        _state["failure"] = failure
        _state["finished_at"] = _now()
        _save_state()

    _banner("RESULT")
    outcome = _state.get("result") or {}
    if outcome:
        unchanged = outcome["parent_gate_sha_unchanged"]
        _log(f"  parent gate.json sha BEFORE : {outcome['parent_gate_sha_before']}")
        _log(f"  parent gate.json sha AFTER  : {outcome['parent_gate_sha_after']}")
        _log(f"  parent gate.json UNCHANGED  : {'PASS' if unchanged else '!! FAIL !!'}")
        _log(f"  new job id                  : {outcome['fix_job_id']}")
        _log(f"  new job status               : {outcome['fix_job_status']}")
        _log(f"  new gate verdict             : {outcome['fix_job_verdict']}")
        _log(f"  new artifact sha256          : {outcome['fix_job_artifact_sha256']}")
        if outcome.get("fix_job_refusal"):
            _log(f"  new job refusal              : {outcome['fix_job_refusal']}")
        _log(f"  active dataset now           : {outcome['active_dataset_source']} / "
             f"{outcome['active_dataset_filename']}")
        if not unchanged:
            failure = failure or "parent gate.json sha changed -- the diagnosis was destroyed"
    overall = "FAILED -- " + failure.splitlines()[0] if failure else "COMPLETE"
    _log(f"  overall: {overall}")
    _log(f"  evidence: {STATE_PATH}")
    _save_state()
    return 1 if failure else 0


if __name__ == "__main__":
    # A CLI run has no HTTP request to scope it to a project; the workspace is
    # resolved explicitly above (search or --workspace-id) rather than assumed.
    sys.exit(main())
