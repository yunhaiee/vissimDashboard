"""Demo prep for unit #32 (roadmap B5-2) -- manufacture the ONE genesis job the
live demo is still missing: a REFUSED job whose refusal names an
AUTOMATABLE, ask-first fix (``v6_input_volume_is_zero`` on an entrance
nobody has measured), so the detail page's B5-2 approval card can be shown
doing its real job end to end.

A read-only scan of the demo workspace's existing refused jobs found only
UNTREATABLE codes (``v3_silent_departure_point`` / ``v7_links_share_one_polyline``
-- both ``NOT_AUTOMATABLE`` in ``services.agent.genesis_fixes``'s catalogue).
Nothing in the demo workspace could show the approval card actually proposing
a fix. This script builds one job that can.

WHY THE ARTIFACT IS NOT BUILT THROUGH ``generate_demand`` -- THE HONEST PATH
------------------------------------------------------------------------------
The obvious way to get a vehicle input at zero volume would be
``services.genesis.demand.generate_demand`` over a counts document with one
approach counted at zero vehicles. **That path is closed by design, not by
accident.** ``generate_demand``'s ``_build_inputs`` REFUSES
(``genesis_demand_zero_observed_total``) the instant a joined approach's
seeded volume is <= 0: "A vehicle input written at zero asserts there is no
demand on this entry; the survey asserts nothing of the kind, it just did not
see any here." That refusal is unconditional and is in
``demand.REFUSE_CODES`` -- it refuses the WHOLE demand layer (an
all-or-nothing ``DraftDemand``, never a partial one), which would make the
whole generation an ``error`` job with no artifact at all, not a REFUSED job
with a stored ``gate.json`` naming V6. So the real counts pipeline can NEVER
legitimately produce the byte pattern R1.5's V6 check flags as
``v6_input_volume_is_zero`` -- confirmed empirically against this repo's own
``backend/tests/test_genesis_demand.py`` fixtures before writing this script.
That is also why ``backend/tests/test_genesis_fix_jobs.py``'s own V6 fixture
(``_v6_zero_volume_network``) hand-writes raw ``.inpx`` XML text rather than
calling ``generate_demand`` -- the same structural fact, worked around there
by not using the real geometry pipeline at all.

This script keeps MORE of the real pipeline than that test fixture does. The
draft is 100% real (``services.genesis.draft.build_draft`` over the actual
``demo-crossroads.osm``), the fleet layer is 100% real
(``services.genesis.fleet.build_fleet``, exercised with one hand-declared
``VehicleClassRef`` -- a real MRWA canonical-bucket lookup, not a copy of its
output), and the EMISSION is 100% real: ``services.genesis.emit.emit_inpx``
and ``demand.demand_containers`` do not care how a ``DraftDemand`` was built,
only that it is one -- they read exactly seven fields off it
(``VehicleCompositionPlan.no/name/flows``, ``VehicleInputPlan.no/link_id/
composition_no/emitted_volume_vph``, ``RoutingDecisionSeed.no/link_id/pos_m/
routes``), all traced directly to this module's Read pass. What is NOT real
is the ONE step ``generate_demand`` refuses to ever perform: this script
constructs a single ``VehicleInputPlan`` BY HAND, using the same public,
frozen dataclasses ``services.genesis.compose``/``demand`` themselves use,
with ``emitted_volume_vph=0.0`` on a real entry link that this workspace has
never observed -- an entrance a surveyor genuinely could record as carrying
zero vehicles in a bin, which is exactly the case D4's doctrine
(``fixes.entrance_measurement``) exists to treat as an ASK-FIRST demand
estimate rather than a fact to invent. The gate that grades the result
(``services.genesis.gate.run_gate``, via the real ``genesis_jobs.gate_and_
publish``) is never told which path built its input; it re-parses the emitted
XML from scratch, so verdict (b) below is exactly as real as if a modeller
had hand-typed that one zero into an otherwise-generated file and asked
Prism to grade it.

A routing decision is added by hand too (also real dataclasses), covering
every one of the target link's real drafted exits with an even split -- not
because the zero-volume input needs traffic routed, but because V6 also
refuses ``v6_input_without_routing_coverage`` for ANY input reaching an
un-covered branch, real volume or not, and this script wants the ONE
deliberate finding to be the demo's whole story wherever the network's own
geometry allows it.

HOW THE JOB IS REGISTERED -- THE SAME TECHNIQUE, NOT TEST SCRATCH DIRS
------------------------------------------------------------------------
``services.genesis_jobs.create_genesis_job``'s ``name=`` is validated against
``_NAME_RE`` (ASCII filename characters only), which the required
``DEMO-수정루프-`` marker cannot satisfy. ``backend/tests/test_genesis_jobs.py``'s
own ``_seed_job`` already solves exactly this for the test suite: it builds a
full job record by hand and writes it through ``genesis_jobs._upsert`` --
the module's own one-writer registry primitive, the same one ``_update``
calls internally, and already called directly from outside the module by
that test file. This script reuses that exact technique (not the test's
scratch ``tmp_path`` workspace, but the SAME ``genesis_jobs.job_dir``/
``_upsert`` calls against whatever real workspace ``--workspace`` names) --
record shape copied field-for-field from ``create_genesis_job``'s own
construction (``services/genesis_jobs.py`` lines ~1138-1174), filenames kept
ASCII-safe on disk while ``record["name"]`` carries the Korean marker
verbatim. Once the record exists, gating runs through the REAL, unmodified
``genesis_jobs.gate_and_publish`` -- the ONLY function in that module allowed
to turn an artifact into a workspace dataset, and it is not our path here
since the file is a deliberate refusal.

VERIFICATION (b)-(d) REUSES THE APP'S OWN ROUTE, NOT A FRESH COPY OF ITS LOGIC
--------------------------------------------------------------------------------
(c)'s disposition check imports ``routers.genesis._render_fix_disposition``
directly -- the exact function ``GET /api/genesis/jobs/{id}/fixes`` calls --
rather than re-deriving "what counts as ask-first" a second time in this
script, which could silently drift from the route's own answer. (d)'s
preflight replays ``services.genesis_jobs.create_genesis_fix_job``'s own
documented validation order (its docstring, steps 1-6) using the SAME public
functions that function calls, stopping one step short of minting a job
directory -- the live demo click is what does that, not this script.

Usage (from ``backend/``, with the venv python)::

    python tools/make_b52_demo_job.py --workspace 7486428cfede
    python tools/make_b52_demo_job.py --workspace 7486428cfede --name DEMO-수정루프-20260814
    python tools/make_b52_demo_job.py --workspace 7486428cfede --force-new
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

BACKEND_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_ROOT.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # pragma: no cover - detached stream
    pass

from services import genesis_jobs, workspaces  # noqa: E402
from services.agent import fixes as agent_fixes  # noqa: E402
from services.agent import genesis_fixes  # noqa: E402
from services.dataimport.schema import (  # noqa: E402
    CanonicalVehicleClass,
    ClassificationScheme,
    VehicleClassRef,
)
from services.genesis.demand import (  # noqa: E402
    CompositionFlow,
    DemandInterval,
    DraftDemand,
    RouteSeed,
    RoutingDecisionSeed,
    VehicleCompositionPlan,
    VehicleInputPlan,
    entry_links,
    link_exits,
)
from services.genesis.draft import build_draft  # noqa: E402
from services.genesis.emit import emit_inpx  # noqa: E402
from services.genesis.fleet import build_fleet  # noqa: E402
from services.genesis.osm import parse_osm_file  # noqa: E402
from routers.genesis import _render_fix_disposition  # noqa: E402

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEMO_OSM = REPO_ROOT / "client-data" / "데모-교차로" / "demo-crossroads.osm"

#: Required marker: nothing this script writes may be mistaken for a real
#: client generation. Both the job's display ``name`` and, transitively, the
#: idempotence scan below key off this exact prefix.
NAME_PREFIX = "DEMO-수정루프-"

TARGET_FINDING_CODE = "v6_input_volume_is_zero"
TARGET_VERB = "set_vehicle_input_volume"

#: The demo's own submitter tag, so a reader of the job record can tell this
#: apart from an operator-submitted generation at a glance.
SUBMITTED_BY = "tools/make_b52_demo_job.py"


class DemoJobRefused(RuntimeError):
    """A named, deliberate refusal to build or reuse a demo job.

    Never raised for "something unexpected broke" -- those propagate as
    whatever exception they already are. This is for the cases this script
    itself checks for and states a reason for: a measured entrance, an
    unusable workspace, a target finding the honest pipeline did not reach.
    """


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def default_name() -> str:
    return f"{NAME_PREFIX}{datetime.now():%Y%m%d}"


# ---------------------------------------------------------------------------
# Workspace argument handling
# ---------------------------------------------------------------------------


def _resolve_workspace_arg(workspace_id: str | None) -> str:
    """``--workspace`` validated against the real registry, or a refusal that
    NAMES the ids that do exist -- so a caller who forgot the flag, or typed
    it wrong, does not have to go spelunking for a valid one."""
    available = workspaces.list_workspaces()
    ids = [str(w.get("id")) for w in available]
    if not workspace_id:
        listing = ", ".join(ids) if ids else "(no workspaces exist)"
        raise DemoJobRefused(
            f"--workspace is required. Available workspace ids: {listing}"
        )
    if workspace_id not in ids:
        listing = ", ".join(ids) if ids else "(no workspaces exist)"
        raise DemoJobRefused(
            f"Unknown workspace {workspace_id!r}. Available workspace ids: {listing}"
        )
    return workspace_id


# ---------------------------------------------------------------------------
# Building the honest artifact
# ---------------------------------------------------------------------------


def _build_demo_demand(draft, target_link_id: int) -> tuple[DraftDemand, "object"]:
    """The fleet (real) + the one hand-built zero-volume ``DraftDemand`` --
    see the module docstring's "WHY THE ARTIFACT IS NOT BUILT THROUGH
    generate_demand" for exactly why the demand half cannot be the real
    ``generate_demand`` and what stays real regardless (the fleet build, and
    every byte the emitter writes)."""
    vclass = VehicleClassRef(
        scheme=ClassificationScheme.SOURCE_SPECIFIC,
        code="demo_zero_volume_car",
        canonical=CanonicalVehicleClass.LIGHT_VEHICLE,
        label="DEMO 합성 승용차 (B5-2 데모)",
    )
    fleet = build_fleet([vclass])
    if not fleet.emitting:
        reasons = "; ".join(i.message for i in fleet.refusals) or "no reason recorded"
        raise DemoJobRefused(f"build_fleet could not build even one vehicle type: {reasons}")
    veh_type_no = sorted(fleet.declared_type_nos())[0]
    speed_distr_no = fleet.speed_distr_no

    composition = VehicleCompositionPlan(
        no=1,
        name="DEMO 합성 차종 구성 (B5-2)",
        site_id="demo-b52",
        flows=(
            CompositionFlow(
                veh_type_no=veh_type_no,
                des_speed_distr_no=speed_distr_no,
                rel_flow=1.0,
                share=1.0,
                class_code=vclass.code,
                class_scheme=vclass.scheme.value,
                counted_records=0,
            ),
        ),
    )

    window_start = datetime(2026, 8, 1, 7, 0, tzinfo=timezone.utc)
    window_end = window_start + timedelta(minutes=15)
    interval = DemandInterval(
        index=0,
        start=window_start,
        end=window_end,
        volume_vph=0.0,
        counted_volume=0.0,
        interval_minutes=15.0,
        contributing_records=0,
        emitted=True,
    )
    input_no = 1
    vehicle_input = VehicleInputPlan(
        no=input_no,
        link_id=target_link_id,
        site_id="demo-b52",
        approach_leg_id="demo-entrance",
        composition_no=composition.no,
        intervals=(interval,),
        reduction=None,
        emitted_volume_vph=0.0,
    )

    exits = link_exits(draft, target_link_id)
    decisions: tuple[RoutingDecisionSeed, ...] = ()
    if exits:
        n = len(exits)
        routes = []
        for route_no, connector in enumerate(exits, start=1):
            dest_link = draft.link_by_id(connector.to_link)
            routes.append(
                RouteSeed(
                    no=route_no,
                    turn=connector.turn,
                    connector_id=connector.id,
                    dest_link_id=connector.to_link,
                    dest_pos_m=dest_link.length_m,
                    rel_flow=1.0,
                    observed_volume=0.0,
                    share=1.0 / n,
                    contributing_records=0,
                )
            )
        decisions = (
            RoutingDecisionSeed(
                no=1,
                link_id=target_link_id,
                pos_m=0.0,
                site_id="demo-b52",
                approach_leg_id="demo-entrance",
                observed_total=0.0,
                routes=tuple(routes),
            ),
        )

    demand = DraftDemand(
        inputs=(vehicle_input,),
        compositions=(composition,),
        decisions=decisions,
    )
    return demand, fleet


def _pick_target_link(draft) -> Any:
    """The real entry link this demo's zero-volume input is attached to --
    deterministic (lowest drafted id) so a re-run against the same OSM picks
    the same link every time."""
    entries = sorted(entry_links(draft), key=lambda link: link.id)
    if not entries:
        raise DemoJobRefused(
            f"{DEMO_OSM.name} drafted no entry links at all -- cannot attach a demo "
            "vehicle input anywhere."
        )
    return entries[0]


def plan_demo_network(stem: str) -> dict[str, Any]:
    """Draft (real) -> fleet (real) -> demand (hand-built, see module
    docstring). Pure in-memory work plus the ONE evidence read
    (``derive_v6_node_evidence``) that can refuse the whole demo -- nothing
    here touches a job directory, so a measured-entrance refusal (or any
    other refusal below) leaves NOTHING on disk to clean up. Splitting this
    from :func:`emit_demo_artifact` is what makes that true: a job id is only
    minted, and a work dir only created, once this function has already
    returned successfully.
    """
    if not DEMO_OSM.is_file():
        raise DemoJobRefused(f"Demo network not found: {DEMO_OSM}")

    extract = parse_osm_file(DEMO_OSM, exclude_service=False)
    if extract.refused:
        raise DemoJobRefused(
            f"{DEMO_OSM.name} refused to parse: "
            f"{'; '.join(r.message for r in extract.refusals)}"
        )

    draft = build_draft(extract, name=stem)
    if draft.refused:
        raise DemoJobRefused(
            f"build_draft refused this known-good demo network: {'; '.join(draft.refusals)}"
        )

    target_link = _pick_target_link(draft)

    measured = genesis_jobs.derive_v6_node_evidence(str(target_link.id))
    if measured is not None:
        raise DemoJobRefused(
            "genesis_measured_entrance_would_contaminate_demo: this workspace's ingested "
            f"observations already cover link {target_link.id} (node {measured.get('node_id')!r}, "
            f"{measured.get('total_real')} veh observed) -- attaching a zero-volume vehicle input "
            "there would produce a STRUCTURAL refusal card (INPUT_VOLUME_IS_OBSERVED_DATA), not "
            "the ask-first AUTOMATABLE card this demo needs. Refusing rather than silently "
            "building the wrong demo. Ingest fewer observation sets, or point this workspace's "
            "V6 demo at a workspace with none covering this network's links."
        )

    demand, fleet = _build_demo_demand(draft, target_link.id)
    return {
        "draft": draft,
        "target_link_id": target_link.id,
        "input_no": demand.inputs[0].no,
        "demand": demand,
        "fleet": fleet,
    }


def emit_demo_artifact(job_dir: Path, stem: str, plan: dict[str, Any]) -> dict[str, Any]:
    """The REAL emitter, over a plan :func:`plan_demo_network` already built.
    The only step that touches ``job_dir``."""
    draft = plan["draft"]
    inpx_path = emit_inpx(
        draft, job_dir / f"{stem}.inpx", signals=None, demand=plan["demand"], fleet=plan["fleet"]
    )
    compliance_path = job_dir / f"{stem}.compliance.md"
    compliance_path.write_text(draft.render_compliance("ko"), encoding="utf-8", newline="")
    census_path = job_dir / f"{stem}.genesis.json"
    census_path.write_text(
        json.dumps(draft.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="",
    )

    return {
        "draft": draft,
        "target_link_id": plan["target_link_id"],
        "input_no": plan["input_no"],
        "inpx_path": inpx_path,
        "compliance_path": compliance_path,
        "census_path": census_path,
        "network": {
            "links": len(draft.links),
            "connectors": len(draft.connectors),
            "nodes": len(draft.nodes),
            "compliance": draft.compliance.counts(),
        },
    }


# ---------------------------------------------------------------------------
# Verification (a)-(d) -- reusable for BOTH a freshly-built job and a
# candidate found by the idempotence scan.
# ---------------------------------------------------------------------------


def verify_demo_job(record: dict[str, Any]) -> dict[str, Any]:
    """Prove (a)-(d) against a job record that ALREADY has a stored gate
    verdict. Raises :class:`DemoJobRefused` naming exactly which check failed;
    returns a small report dict on success (used for both the printed runbook
    and the idempotence scan's own accept/reject decision)."""
    job_id = record["id"]

    # (a) status refused.
    if record.get("status") != genesis_jobs.STATUS_REFUSED:
        raise DemoJobRefused(
            f"job {job_id} is {record.get('status')!r}, not {genesis_jobs.STATUS_REFUSED!r} -- "
            "(a) failed."
        )

    # (b) stored gate.json contains v6_input_volume_is_zero.
    gate_path = genesis_jobs.job_dir(job_id) / genesis_jobs.GATE_JSON_FILENAME
    if not gate_path.is_file():
        raise DemoJobRefused(f"job {job_id} has no stored {genesis_jobs.GATE_JSON_FILENAME} -- (b) failed.")
    raw_gate = json.loads(gate_path.read_text(encoding="utf-8"))
    if raw_gate.get("verdict") != "refused":
        raise DemoJobRefused(
            f"job {job_id}'s stored gate verdict is {raw_gate.get('verdict')!r}, not 'refused' -- "
            "(b) failed."
        )
    match = None
    check_id = origin = None
    for cid, org, finding in genesis_jobs.iter_stored_gate_findings(raw_gate):
        if finding["code"] == TARGET_FINDING_CODE:
            match = finding
            check_id, origin = cid, org
            break
    if match is None:
        raise DemoJobRefused(
            f"job {job_id}'s stored gate.json names no {TARGET_FINDING_CODE!r} finding -- (b) failed."
        )
    input_no = match["subject"]

    # (c) propose_genesis_fix, via the SAME route function the UI's GET
    # .../fixes calls, yields an AUTOMATABLE ask-first disposition -- never a
    # structural refusal. And: no observations cover this input's link.
    target_link_id = (match.get("context") or {}).get("link")
    if target_link_id:
        measured = genesis_jobs.derive_v6_node_evidence(str(target_link_id))
        if measured is not None:
            raise DemoJobRefused(
                f"job {job_id}'s target link {target_link_id} is now covered by this workspace's "
                f"observations (node {measured.get('node_id')!r}) -- this demo job has gone stale "
                "and would show a structural-refusal card, not the ask-first one. (c) failed."
            )

    draft = genesis_jobs.job_draft_or_none(job_id)
    census = draft.metadata if draft is not None else None
    targets_no_params = genesis_jobs.derive_fix_targets(
        TARGET_FINDING_CODE, match, draft=draft, params=None
    )
    proposal_no_params = genesis_fixes.propose_genesis_fix(
        TARGET_FINDING_CODE, finding=match, census=census, targets=targets_no_params
    )
    entry = _render_fix_disposition(
        check_id=str(check_id), origin=str(origin), finding=match, proposal=proposal_no_params
    )
    disposition = entry["disposition"]
    if disposition["status"] != agent_fixes.AUTOMATABLE:
        raise DemoJobRefused(
            f"job {job_id}'s {TARGET_FINDING_CODE} disposition is {disposition['status']!r}, not "
            f"{agent_fixes.AUTOMATABLE!r} -- (c) failed."
        )
    if disposition["verb"] != TARGET_VERB:
        raise DemoJobRefused(
            f"job {job_id}'s disposition names verb {disposition['verb']!r}, expected "
            f"{TARGET_VERB!r} -- (c) failed."
        )
    if disposition.get("reason_code") == agent_fixes.INPUT_VOLUME_IS_OBSERVED_DATA:
        raise DemoJobRefused(
            f"job {job_id}'s disposition is a STRUCTURAL refusal ({agent_fixes.INPUT_VOLUME_IS_OBSERVED_DATA}) "
            "-- (c) failed: this is exactly the untreatable shape the demo must NOT show."
        )
    if disposition["requires"] != ["volume"]:
        raise DemoJobRefused(
            f"job {job_id}'s disposition requires {disposition['requires']!r}, expected ['volume'] "
            "-- (c) failed."
        )

    # (d) create_genesis_fix_job's own preflight (its docstring, steps 1-6)
    # replayed with a real caller-supplied volume, WITHOUT minting a fix job.
    demo_params = {"input_no": str(input_no), "volume": 500.0, "interval_no": 1}
    resolution = agent_fixes.resolve_verb(TARGET_VERB)
    if not resolution["resolved"]:
        raise DemoJobRefused(f"{TARGET_VERB!r} does not resolve in the live tool registry -- (d) failed.")
    ok, error = agent_fixes.validate_params(TARGET_VERB, demo_params)
    if not ok:
        raise DemoJobRefused(f"{TARGET_VERB!r}'s own schema rejected the demo params ({error}) -- (d) failed.")
    addressing_field = genesis_fixes.VERB_ADDRESSING_FIELD.get(TARGET_VERB)
    expected_target = genesis_fixes.expected_fix_target(TARGET_FINDING_CODE, match)
    if not addressing_field or expected_target is None or demo_params[addressing_field] != expected_target:
        raise DemoJobRefused(
            f"job {job_id}: demo params address {demo_params.get(addressing_field)!r}, the finding "
            f"names {expected_target!r} -- (d) failed (params/subject mismatch)."
        )
    targets_with_params = genesis_jobs.derive_fix_targets(
        TARGET_FINDING_CODE, match, draft=draft, params=demo_params
    )
    proposal_with_params = genesis_fixes.propose_genesis_fix(
        TARGET_FINDING_CODE, finding=match, census=census, targets=targets_with_params
    )
    if proposal_with_params.outcome != agent_fixes.PROPOSAL or proposal_with_params.verb != TARGET_VERB:
        raise DemoJobRefused(
            f"job {job_id}: create_genesis_fix_job's own re-derivation would refuse this proposal "
            f"(outcome={proposal_with_params.outcome!r}, reason_code={proposal_with_params.reason_code!r}) "
            "-- (d) failed: the parent is not fix-job eligible."
        )

    return {
        "job_id": job_id,
        "input_no": input_no,
        "target_link_id": target_link_id,
        "disposition": disposition,
        "preflight_params": demo_params,
        "preflight_proposal_verb": proposal_with_params.verb,
    }


# ---------------------------------------------------------------------------
# Idempotence -- look for an existing satisfying demo job first
# ---------------------------------------------------------------------------


def _find_existing_demo_job() -> dict[str, Any] | None:
    for record in genesis_jobs.list_jobs():
        name = str(record.get("name") or "")
        if not name.startswith(NAME_PREFIX):
            continue
        if record.get("status") != genesis_jobs.STATUS_REFUSED:
            continue
        try:
            report = verify_demo_job(record)
        except DemoJobRefused as exc:
            print(f"  (skipping stale candidate {record['id']}: {exc})")
            continue
        return {"record": record, "report": report}
    return None


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def run_demo_job(workspace_id: str, *, name: str | None = None, force_new: bool = False) -> dict[str, Any]:
    """The whole script, minus argument parsing and printing. Binds
    ``workspace_id`` for its entire body -- every ``genesis_jobs`` call below
    resolves paths off ``workspaces.current_workspace_id()``.

    Returns ``{"workspace_id", "reused", "record", "report"}`` on success.
    Raises :class:`DemoJobRefused` for every named refusal (bad/missing
    workspace, a bad ``--name``, a measured entrance, the honest pipeline not
    reaching the target finding, verification (a)-(d) failing on a job this
    call itself just built). Never silently returns a job that does not meet
    the spec.
    """
    resolved_workspace = _resolve_workspace_arg(workspace_id)
    job_name = (name or default_name()).strip()
    if not job_name.startswith(NAME_PREFIX):
        raise DemoJobRefused(
            f"--name {job_name!r} does not start with the required marker {NAME_PREFIX!r} -- "
            "nothing this script writes may be mistaken for a real generation."
        )

    with workspaces.bind_workspace(resolved_workspace):
        if not force_new:
            existing = _find_existing_demo_job()
            if existing is not None:
                return {
                    "workspace_id": resolved_workspace,
                    "reused": True,
                    "record": existing["record"],
                    "report": existing["report"],
                }

        # Planned BEFORE any job id is minted or any directory created: a
        # refusal here (most importantly the measured-entrance check) leaves
        # nothing on disk to clean up. The draft's own internal name is a
        # fixed label (cosmetic -- it heads the compliance report) and is
        # independent of the job id assigned below.
        plan = plan_demo_network("DEMO-B52")

        job_id = uuid4().hex[:12]
        work_dir = genesis_jobs.job_dir(job_id)
        work_dir.mkdir(parents=True, exist_ok=True)
        stem = f"demo_b52_{job_id}"
        built = emit_demo_artifact(work_dir, stem, plan)

        record = _register_job_record_at(job_id, work_dir, job_name, resolved_workspace, built)
        gated = genesis_jobs.gate_and_publish(job_id)
        if gated.get("status") != genesis_jobs.STATUS_REFUSED:
            raise DemoJobRefused(
                f"job {job_id} gated to status {gated.get('status')!r}, not "
                f"{genesis_jobs.STATUS_REFUSED!r}. The honest pipeline did not reach the target "
                "finding -- STOPPING rather than forging a gate.json. Gate summary: "
                f"{json.dumps(gated.get('gate'), ensure_ascii=False)[:2000]}"
            )
        report = verify_demo_job(gated)
        return {
            "workspace_id": resolved_workspace,
            "reused": False,
            "record": gated,
            "report": report,
        }


def _register_job_record_at(
    job_id: str, work_dir: Path, job_name: str, workspace_id: str, built: dict[str, Any]
) -> dict[str, Any]:
    osm_bytes = DEMO_OSM.read_bytes()
    stored_source = work_dir / "source.osm"
    stored_source.write_bytes(osm_bytes)

    inpx_path: Path = built["inpx_path"]
    now = _now_iso()
    record = {
        "id": job_id,
        "workspace_id": workspace_id,
        "name": job_name,
        "status": genesis_jobs.STATUS_RUNNING,
        "phase": genesis_jobs.PHASE_GATING,
        "progress": 0.85,
        "created_at": now,
        "started_at": now,
        "completed_at": None,
        "submitted_by": SUBMITTED_BY,
        "options": {"exclude_service": False},
        "requested_region": None,
        "source_osm": {
            "filename": DEMO_OSM.name,
            "bytes": len(osm_bytes),
            "sha256": _sha256_bytes(osm_bytes),
            "stored_as": stored_source.name,
        },
        "side_inputs": None,
        "network": built["network"],
        "artifact": {
            "inpx": inpx_path.name,
            "compliance_md": built["compliance_path"].name,
            "genesis_json": built["census_path"].name,
            "sha256": _sha256_file(inpx_path),
            "bytes": inpx_path.stat().st_size,
        },
        "layers": None,
        "gate": None,
        "publication": None,
        "refusal": None,
        "error": None,
    }
    genesis_jobs._upsert(record)  # noqa: SLF001 - see module docstring
    return record


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _print_runbook(result: dict[str, Any]) -> None:
    record = result["record"]
    report = result["report"]
    job_id = record["id"]
    verb_ok = "yes" if report["preflight_proposal_verb"] == TARGET_VERB else "no"
    print()
    print("=" * 78)
    print(f"{'REUSED' if result['reused'] else 'CREATED'} B5-2 demo job")
    print("=" * 78)
    print(f"  workspace         : {result['workspace_id']}")
    print(f"  job id            : {job_id}")
    print(f"  job name          : {record['name']}")
    print(f"  status            : {record['status']}")
    print(f"  finding           : {TARGET_FINDING_CODE} (vehicle input {report['input_no']}, "
          f"link {report['target_link_id']})")
    print(f"  disposition       : status={report['disposition']['status']} "
          f"verb={report['disposition']['verb']} requires={report['disposition']['requires']}")
    print(f"  create_genesis_fix_job preflight would accept: {verb_ok}")
    print()
    print(f"  URL               : /genesis/jobs/{job_id}")
    print(f"  which check       : V6 (차량 유입 / demand coverage) -> "
          f"{TARGET_FINDING_CODE}")
    print("  what the card should show:")
    print("    - an AUTOMATABLE fix proposing 'set_vehicle_input_volume'")
    print("    - asking first for a volume (no number invented by the system)")
    print("    - NOT a structural refusal (no 'measured entrance' language)")
    print("  reminder: approving this card applies the edit via a real COM")
    print("            VissimEditSession and re-gates the result as a NEW")
    print("            sibling job -- the parent above is never modified.")
    print("=" * 78)


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--workspace",
        default=None,
        help="Workspace id to build the demo job in (required).",
    )
    parser.add_argument(
        "--name",
        default=None,
        help=f"Job display name. Default: {NAME_PREFIX}YYYYMMDD. Must start with {NAME_PREFIX!r}.",
    )
    parser.add_argument(
        "--force-new",
        action="store_true",
        help="Skip the idempotence scan and always create a new demo job.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)
    try:
        result = run_demo_job(args.workspace, name=args.name, force_new=args.force_new)
    except DemoJobRefused as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 1
    _print_runbook(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
