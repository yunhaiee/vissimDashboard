"""Write-tier tools: the M2.3 verbs that actually mutate the working network.

Every tool here follows the same shape: read the active
:class:`~services.agent.edit_session.VissimEditSession` (module-global
context, set by ``routers/assistant.py`` for the duration of an approved
write turn -- see ``edit_session.py``'s docstring for the same
"acceptable limitation" caveat ``tools_network.set_active_inpx`` already
documents), COM-set the value, COM-get it back (read-back verification),
:meth:`~services.agent.edit_session.VissimEditSession.persist` (SaveNetAs +
replace onto the working copy), then return an envelope whose
``verification`` carries both values for
``guardrails.GuardrailEngine.postwrite`` to compare (constraint #6,
``write_verification_failed``) and, separately, to confirm the value
actually reached the saved file (the "target-persisted" check,
docs/phase2/DECISIONS.md, "postwrite gains a target-persisted check").

Param names for the object-targeting tools (vehicle input, signal cycle,
signal offset, conflict area status, -- v2 unit ⑥ -- routing decision
route relative flow, -- v2 unit ⑦ -- vehicle composition relative
flow, -- v2a unit ⑧ -- desired-speed decision distribution assignment,
-- v2a unit ⑨ -- signal green split, -- QUEUE 3 unit 2 --
priority-rule conflict-marker gap/headway, and -- QUEUE 3 (v2a) unit 3 --
desired-speed DISTRIBUTION curve points) match
``guardrails._WRITE_TARGET_FIELD_SPEC`` EXACTLY
(tool name AND field names) -- that table is what lets the postwrite
"nothing else moved" diff exempt each tool's own intended target field;
drifting a name here silently reintroduces false-positive diff evidence on
every write. ``input_no``/``sc_no``/``decision_no``/``route_no`` are typed
``str`` here (matching the shadow model's own string id fields and
``constraints.py``'s ``_TOOL_TARGET_SPEC``/``guardrails.py``'s
``_WRITE_TARGET_FIELD_SPEC`` string-keyed lookups) and converted to ``int``
only at the COM boundary, inside each tool function.

**v2 unit ⑥: ``set_route_relative_flow`` has a TWO-PART object identity.**
Every other object-targeting write tool addresses its target with ONE param
(``input_no``/``sc_no``/``conflict_area_no``) that maps 1:1 onto a flat
shadow-model object list's own id field. A routing-decision route has no
single-token identity in Vissim itself -- it is addressed by the
(``decision_no``, ``route_no``) PAIR. ``guardrails.py``'s
``_WRITE_TARGET_FIELD_SPEC`` entry for this tool therefore uses
``id_param_parts`` (not ``id_param``) to synthesize the SAME
``"{decision_no}.{route_no}"`` id ``tools_network._flatten_routes`` already
uses as the flat ``shadow_model["routes"]`` list's own ``id`` field -- see
that helper's docstring and ``guardrails._target_id_for_spec``.

**v2 unit ⑦: ``set_vehicle_composition_share`` is the SECOND two-part
identity write tool**, addressed by (``composition_no``, ``veh_type_no``)
for the identical reason as routes -- a composition's relative-flow entry
has no single-token identity in Vissim itself (worse: its own COM
collection has no ``ItemByKey`` at all, verified live --
``backend/tools/probe_vehicle_compositions.py``). ``guardrails.py``'s spec
entry uses the same ``id_param_parts`` mechanism against
``tools_network._flatten_vehicle_composition_entries``'s synthesized
``"{composition_no}.{veh_type}"`` id.

**Failure shape.** ``toolkit.dispatch`` can only ever produce an
envelope-level ``status: "error"`` when the tool FUNCTION RAISES (unknown
tool / invalid params / a raised exception -- see toolkit.py's dispatch()
docstring); a tool that merely RETURNS a dict, even one shaped like
``{"status": "error", ...}``, is unconditionally wrapped as ``status: "ok"``
by ``_build_success_envelope``. Every failure path below therefore RAISES
rather than returns a dict -- returning one instead would make
``orchestrator.py``'s ``if envelope["status"] == "ok":`` gate treat a
refused/failed write as a landed one, calling ``artifact_manager.
save_version()`` on an unchanged file and reporting false success. See the
M2.3 report's drafted DECISIONS.md entry for the ``no_edit_session`` case
specifically -- the task brief asked for a literal ``error_type:
"no_edit_session"`` field, which ``toolkit.dispatch`` has no path to
produce (every raised exception's ``error_type`` is the fixed string
``"tool_execution_error"``); :class:`NoEditSessionError`'s message carries
the ``no_edit_session`` marker as literal, greppable text instead.

**v2a unit ⑧: ``set_des_speed_decision`` is the first ATOMIC PAIRED write.**
Every other write tool in this module mutates exactly ONE shadow-model
object per call. A desired-speed decision is PERMANENT once passed (there is
no time-boxing like ``set_reduced_speed_area``) -- the conventional way to
model a temporary slow zone is a PAIR: one decision at the zone start
(assigns a slower distribution) and a SECOND decision immediately downstream
that restores the original distribution. Editing only the first is the
catalog's "desired-speed decisions are permanent unless paired with a
restoring decision" silent-failure trap
(docs/phase2/00-EXECUTION-GUIDE.md §3.2). When the caller supplies the
optional ``restore_decision_no``/``restore_distr_no`` pair, THIS SAME
approved action writes BOTH decisions' assignments for the SAME
``veh_class_no``, and -- unlike every single-target write tool above, which
persists unconditionally and lets ``guardrails.py``'s postwrite #6 catch a
mismatch after the fact -- proactively verifies BOTH read-backs BEFORE ever
calling ``session.persist()``: a mismatch on either write raises (never
returns), so the whole action fails and NEITHER write reaches the saved
file. This is a deliberate deviation from the single-write precedent, forced
by the fact that postwrite's generic write_verification_failed check (#6)
only ever inspects ONE top-level ``written_value``/``readback_value`` pair —
a RESTORE-write-only mismatch would otherwise sail through undetected and
get persisted as a silently half-paired edit. The UNPAIRED call path (no
``restore_decision_no``) keeps the exact single-write precedent unchanged
(persist unconditionally; comparison is postwrite's job) for byte-compatible
behavior with the other tools in this module.

``guardrails.py``'s ``_WRITE_TARGET_FIELD_SPEC`` is single-target-only (one
(object_class, id) exemption per tool) -- there is no built-in multi-target
mechanism, so this tool's PAIRED write needed a small, additive extension
(``_PAIRED_EXTRA_TARGET_SPEC``) rather than a bigger multi-target redesign;
see that module's own comment at the extension point. Entry identity is the
SAME two-part ``"{decision_no}.{veh_class_no}"`` synthesized id
``tools_network._flatten_des_speed_decision_entries`` already uses, mirroring
the ``id_param_parts`` precedent ``set_route_relative_flow``/
``set_vehicle_composition_share`` established.

**v2 unit (2): ``set_reduced_speed_area`` closes the M2.3 deferral.** The
roadmap's sixth verb (``set_desired_speed_area`` in the original naming;
renamed here per the controller's v2 design to match its actual object --
see the M2.3 report's drafted DECISIONS.md entry, "set_desired_speed_area
deferred", now superseded) is implemented below, but it is deliberately NOT
shaped like the five COM verbs above: it never touches
:class:`~services.agent.edit_session.VissimEditSession`'s COM surface at
all, only its ``working_inpx`` PATH attribute -- the write itself is
:func:`services.simulation_network_builder.inject_reduced_speed_area`, a
direct XML mutation of the session's working copy. See that tool's own
docstring for why this is safe (it never triggers ``_ensure_connected()``,
so no Vissim instance is ever launched for this tool) and for its distinct
verification story (a reparse readback, not a COM read-back -- no COM getter
exists for an XML-injected object).
"""

from __future__ import annotations

import hashlib
import os
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from services.agent import batch, guardrails, rollback
from services.agent.edit_session import get_active_edit_session
from services.agent.toolkit import default_registry, dispatch, tool
from services.agent.tools_network import build_shadow_model
from services.datasets import get_active_inpx_path
from services.simulation_network_builder import (
    inject_reduced_speed_area,
    reparse_reduced_speed_area,
)


class NoEditSessionError(RuntimeError):
    """No active :class:`~services.agent.edit_session.VissimEditSession` for
    this turn. Raised (never returned as a dict -- see module docstring) so
    ``toolkit.dispatch`` reports a genuine ``status: "error"`` envelope
    instead of a false ``"ok"``."""


def _require_session():
    """The active edit session, or raise :class:`NoEditSessionError`.

    The message embeds the literal marker ``no_edit_session`` so it stays
    greppable/assertable even though ``toolkit.dispatch`` always reports
    the envelope's own ``error_type`` as the fixed string
    ``"tool_execution_error"`` for any raised exception (see module
    docstring).
    """
    session = get_active_edit_session()
    if session is None:
        raise NoEditSessionError(
            "No active Vissim edit session for this turn (error_type: no_edit_session). "
            "Write tools only run during an approved write turn, activated by the "
            "propose -> approve flow."
        )
    return session


def _coerce_id(raw: str, field_name: str) -> int:
    """``str`` shadow-model id -> ``int`` COM key. Preflight's
    ``object_not_found`` check (constraints.py) already confirms ``raw``
    matches a real shadow-model id before a write tool ever runs, so this
    should not fail in practice; kept defensive (a clear ValueError beats a
    confusing COM-layer TypeError) rather than assumed."""
    try:
        return int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name}={raw!r} is not a valid integer id") from exc


# ---------------------------------------------------------------------------
# set_vehicle_input_volume(input_no, volume)
# ---------------------------------------------------------------------------


class SetVehicleInputVolumeParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input_no: str
    volume: float
    # v2 unit ⑤: addresses a specific time interval on a multi-interval
    # vehicle input (shadow_model["vehicle_inputs"][].volumes[], populated by
    # tools_network._parse_vehicle_inputs). Default 1 is byte-compatible with
    # the original single-interval verb -- ge=1 rejects 0/negative at the
    # pydantic layer (invalid_params) before this ever reaches a tool
    # function; an interval_no beyond what the target input actually has is
    # constraints.py's vehicle_input_interval_out_of_range's job to refuse
    # (when interval_count is known) or Vissim's own live COM raise (verified
    # empirically, backend/tools/vehicle_input_interval_probe.py: reading/
    # writing a genuinely non-existent interval index fails CLOSED, "Object N
    # not found") when it is not.
    interval_no: int = Field(default=1, ge=1)


@tool(
    name="set_vehicle_input_volume",
    tier="write",
    provider="edit",
    params=SetVehicleInputVolumeParams,
)
def set_vehicle_input_volume(params: SetVehicleInputVolumeParams) -> dict[str, Any]:
    """Set a vehicle input's volume (veh/h) for a given time interval (interval_no, default 1) on the active edit session's working network -- a multi-interval input's OTHER intervals are left untouched by design; see get_vehicle_inputs for the per-interval profile."""
    session = _require_session()
    input_no = _coerce_id(params.input_no, "input_no")

    if params.interval_no == 1:
        # Exact original two-call path -- byte-compatible with every caller
        # (fake and real) that predates v2 unit ⑤ and never passes
        # interval_no. Interval 1 is never continuous (verified live: every
        # probed network's Cont(1) reads false), so there is no Cont
        # handling to report here.
        session.set_vehicle_input_volume(input_no, params.volume)
        readback = session.get_vehicle_input_volume(input_no)
        verification: dict[str, Any] = {"written_value": params.volume, "readback_value": readback}
        message = f"Vehicle input {params.input_no} volume set to {params.volume} veh/h."
    else:
        session.set_vehicle_input_volume(input_no, params.volume, params.interval_no)
        readback = session.get_vehicle_input_volume(input_no, params.interval_no)
        cont_after = session.get_vehicle_input_continuous(input_no, params.interval_no)
        verification = {
            "written_value": params.volume,
            "readback_value": readback,
            "cont_after": cont_after,
        }
        message = (
            f"Vehicle input {params.input_no} interval {params.interval_no} volume set to "
            f"{params.volume} veh/h."
        )

    session.persist()
    return {"message": message, "verification": verification}


# ---------------------------------------------------------------------------
# set_signal_cycle(sc_no, cycle)
# ---------------------------------------------------------------------------


class SetSignalCycleParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sc_no: str
    cycle: int


@tool(name="set_signal_cycle", tier="write", provider="edit", params=SetSignalCycleParams)
def set_signal_cycle(params: SetSignalCycleParams) -> dict[str, Any]:
    """Set a signal controller's cycle time (seconds; COM attribute CycTm) on the active edit session's working network."""
    session = _require_session()
    sc_no = _coerce_id(params.sc_no, "sc_no")
    session.set_signal_cycle(sc_no, params.cycle)
    readback = session.get_signal_cycle(sc_no)
    session.persist()
    return {
        "message": f"Signal controller {params.sc_no} cycle set to {params.cycle}s.",
        "verification": {"written_value": params.cycle, "readback_value": readback},
    }


# ---------------------------------------------------------------------------
# set_signal_offset(sc_no, offset)
# ---------------------------------------------------------------------------


class SetSignalOffsetParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sc_no: str
    offset: int


@tool(name="set_signal_offset", tier="write", provider="edit", params=SetSignalOffsetParams)
def set_signal_offset(params: SetSignalOffsetParams) -> dict[str, Any]:
    """Set a signal controller's offset (seconds) on the active edit session's working network."""
    session = _require_session()
    sc_no = _coerce_id(params.sc_no, "sc_no")
    session.set_signal_offset(sc_no, params.offset)
    readback = session.get_signal_offset(sc_no)
    session.persist()
    return {
        "message": f"Signal controller {params.sc_no} offset set to {params.offset}s.",
        "verification": {"written_value": params.offset, "readback_value": readback},
    }


# ---------------------------------------------------------------------------
# set_sim_duration(seconds)
# ---------------------------------------------------------------------------


class SetSimDurationParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    seconds: int


@tool(name="set_sim_duration", tier="write", provider="edit", params=SetSimDurationParams)
def set_sim_duration(params: SetSimDurationParams) -> dict[str, Any]:
    """Set the simulation period (seconds; COM attribute SimPeriod) on the active edit session's working network."""
    session = _require_session()
    session.set_sim_duration(params.seconds)
    readback = session.get_sim_duration()
    session.persist()
    return {
        "message": f"Simulation duration set to {params.seconds}s.",
        "verification": {"written_value": params.seconds, "readback_value": readback},
    }


# ---------------------------------------------------------------------------
# set_random_seed(seed)
# ---------------------------------------------------------------------------


class SetRandomSeedParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    seed: int


@tool(name="set_random_seed", tier="write", provider="edit", params=SetRandomSeedParams)
def set_random_seed(params: SetRandomSeedParams) -> dict[str, Any]:
    """Set the simulation random seed (COM attribute RandSeed) on the active edit session's working network."""
    session = _require_session()
    session.set_random_seed(params.seed)
    readback = session.get_random_seed()
    session.persist()
    return {
        "message": f"Random seed set to {params.seed}.",
        "verification": {"written_value": params.seed, "readback_value": readback},
    }


# ---------------------------------------------------------------------------
# set_conflict_area_status(conflict_area_no, status)
#
# v2 unit (4): the catalog's single most dangerous silent failure -- conflict
# areas default to Passive (no right-of-way control at all; vehicles drive
# through each other and the run finishes and reports confident numbers) --
# finally becomes fixable. COM attribute ids (No/LinkA/LinkB/Status) verified
# live against Vissim 2026 by backend/tools/conflict_area_probe.py: they
# match the .inpx XML attribute names exactly, unlike CycTm/NodeRes. Status
# is restricted to the 4 values actually observed across the .inpx XML of
# ALL 25 real Vissim 2026 example networks (7294+574+334+292 conflict areas
# tallied) -- a value outside that set is a structured invalid_params
# rejection, never reaches COM. Conflict areas have NO Add/Remove via COM
# (docs/phase2/00-EXECUTION-GUIDE.md SS3.2, derived from geometry) -- this
# tool can only ever mutate an EXISTING conflict area's status.
# ---------------------------------------------------------------------------


class SetConflictAreaStatusParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conflict_area_no: str
    status: Literal["PASSIVE", "AHASRIGHTOFWAY", "BHASRIGHTOFWAY", "UNDETERMINED"]


@tool(
    name="set_conflict_area_status",
    tier="write",
    provider="edit",
    params=SetConflictAreaStatusParams,
)
def set_conflict_area_status(params: SetConflictAreaStatusParams) -> dict[str, Any]:
    """Set a conflict area's right-of-way status (PASSIVE/AHASRIGHTOFWAY/BHASRIGHTOFWAY/UNDETERMINED; COM attribute Status) on the active edit session's working network -- fixes the catalog's most dangerous silent failure: Passive conflict areas let vehicles drive through each other while the run reports clean results."""
    session = _require_session()
    conflict_area_no = _coerce_id(params.conflict_area_no, "conflict_area_no")
    session.set_conflict_area_status(conflict_area_no, params.status)
    after = session.get_conflict_area(conflict_area_no)
    session.persist()
    return {
        "message": (
            f"Conflict area {params.conflict_area_no} (link {after['link_a']} vs link "
            f"{after['link_b']}) status set to {params.status}."
        ),
        "verification": {"written_value": params.status, "readback_value": after["status"]},
        "artifacts": {"link_a": after["link_a"], "link_b": after["link_b"]},
    }


# ---------------------------------------------------------------------------
# set_reduced_speed_area(link_id, speed_kmh, from_m, length_m)
#
# v2 unit (2). NOT a COM verb -- see the module docstring's "v2 unit (2)"
# paragraph. Does not call session.persist() (no COM session is ever
# connected for this tool -- there is nothing to SaveNetAs): the write
# itself already lands directly on session.working_inpx via
# inject_reduced_speed_area's own temp-file + os.replace, so by the time
# this function returns the working copy on disk already reflects the edit.
# ---------------------------------------------------------------------------


class SetReducedSpeedAreaParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    link_id: str
    speed_kmh: float
    from_m: float
    length_m: float


@tool(
    name="set_reduced_speed_area",
    tier="write",
    provider="edit",
    params=SetReducedSpeedAreaParams,
)
def set_reduced_speed_area(params: SetReducedSpeedAreaParams) -> dict[str, Any]:
    """Add a permanent, spatially-bounded reduced-speed area on one link (XML injection, not COM) -- the SAFE speed-editing verb, since it is bounded by position and length rather than being a network-wide desired-speed decision that stays in effect until a second, restoring decision undoes it.

    Injects a ``reducedSpeedArea`` (+ its own ``speedDistribution``) onto
    EVERY lane of ``link_id`` over ``[from_m, from_m + length_m)``, active for
    the whole simulation (no time-window restriction) -- directly into the
    active edit session's ``working_inpx`` file
    (:func:`services.simulation_network_builder.inject_reduced_speed_area`),
    never through COM: :func:`~services.agent.edit_session.
    get_active_edit_session`'s session is used ONLY for its ``working_inpx``
    path attribute, so this tool never launches a Vissim instance (unlike
    every other tool in this module).

    **Verification is a reparse readback, not a COM read-back.** There is no
    COM getter for an object that was never created through COM -- the
    session's own COM controller (if one happens to exist this turn because
    a write plan mixed this tool with a COM one) is never even touched here.
    Instead, immediately after the injection, the SAME working copy is
    re-read fresh from disk
    (:func:`~services.simulation_network_builder.reparse_reduced_speed_area`)
    and the newly created element's actual attributes (link, speed, position,
    length) are extracted and compared against what was intended -- this IS
    the tool's whole proof that the write landed, standing in for the
    set-then-get pattern every COM verb in this module uses.
    ``guardrails.GuardrailEngine.postwrite``'s generic write-verification
    compare (constraint #6) still runs against this ``verification`` payload
    exactly like any other tool's (it is tool-agnostic, ``applies_to:
    ["*"]``), so a written-vs-reparsed mismatch still refuses the turn and
    rolls back. What does NOT run for this tool is the separate
    "target-persisted" postwrite check and the "nothing else moved" diff's
    exemption bookkeeping: the shadow model (``build_shadow_model`` in
    ``tools_network.py``) has no ``reduced_speed_areas`` key at all today, so
    this tool is deliberately left OUT of ``guardrails._WRITE_TARGET_FIELD_
    SPEC`` -- an unmapped tool gets no target-persisted claim (degrades
    silently, same as any other unmapped write) and the diff sees no
    reduced-speed-area object class to compare either way. The reparse
    readback above is this tool's OWN, self-contained proof; it does not
    lean on those two shadow-model-based checks the way the three
    object-targeting COM verbs do.
    """
    session = _require_session()
    injected = inject_reduced_speed_area(
        session.working_inpx,
        link_no=params.link_id,
        speed_kmh=params.speed_kmh,
        from_m=params.from_m,
        length_m=params.length_m,
    )
    readback = reparse_reduced_speed_area(session.working_inpx, injected["area_nos"])
    # written_value/readback_value must carry EXACTLY the same key set:
    # constraints.py's generic #6 (write_verification_failed, applies_to
    # ["*"]) compares them with a bare `!=` for any non-numeric top-level
    # pair (see that check's own _values_differ) -- a dict with an extra key
    # on either side is a structural mismatch REGARDLESS of whether every
    # shared key's value actually agrees, so lane_count (present in
    # reparse_reduced_speed_area's return, useful diagnostic info but not
    # part of what THIS write intended to set) is reported separately via
    # `artifacts`, never inside the compared dicts.
    readback_value = {key: readback[key] for key in ("link_id", "speed_kmh", "from_m", "length_m")}
    return {
        "message": (
            f"Reduced-speed area added on link {params.link_id}: {params.speed_kmh} km/h "
            f"over {params.length_m} m starting at {params.from_m} m "
            f"({injected['lane_count']} lane(s))."
        ),
        "verification": {
            "written_value": injected["written_value"],
            "readback_value": readback_value,
        },
        "artifacts": {
            "area_nos": injected["area_nos"],
            "distribution_no": injected["distribution_no"],
            "lane_count": readback["lane_count"],
        },
    }


# ---------------------------------------------------------------------------
# set_route_relative_flow(decision_no, route_no, rel_flow, interval_no)
#
# v2 unit ⑥. A COM verb, same shape as set_vehicle_input_volume/
# set_conflict_area_status: set -> get (readback) -> persist ->
# verification envelope. RelFlow is a RAW weight, not a percentage --
# verified live (backend/tools/routing_decision_probe.py) that Vissim does
# NOT renormalize sibling routes' own stored values when one route's
# RelFlow is written; each route's ACTUAL simulated traffic share is
# derived at RUN time as this value divided by the sum of every route's
# RelFlow in the SAME decision. A raw number alone on the approval card
# would misleadingly read like a percentage/share, so the message here AND
# the dedicated route_relative_flow_split_confirm preflight constraint
# (constraints.py) both spell out the normalization semantics explicitly
# rather than just stating "changed to X".
# ---------------------------------------------------------------------------


class SetRouteRelativeFlowParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision_no: str
    route_no: str
    rel_flow: float
    # v2 unit ⑥, same convention as set_vehicle_input_volume's interval_no
    # (v2 unit ⑤): default 1 addresses the byte-compatible common case (a
    # single-interval route, or a multi-interval route's first interval).
    # RelFlow has NO scalar COM form at all (verified live -- see
    # vissim_com.set_route_relative_flow's docstring) so, unlike
    # set_signal_cycle/set_signal_offset, every write ALWAYS goes through an
    # indexed AttValue call regardless of interval_no.
    interval_no: int = Field(default=1, ge=1)


@tool(
    name="set_route_relative_flow",
    tier="write",
    provider="edit",
    params=SetRouteRelativeFlowParams,
)
def set_route_relative_flow(params: SetRouteRelativeFlowParams) -> dict[str, Any]:
    """Set one route's relative-flow weight within a static vehicle routing decision (interval_no, default 1) on the active edit session's working network -- relFlow is a RAW weight, not a percentage: Vissim computes each route's actual traffic share by normalizing its relFlow against the SUM of every route's relFlow in the SAME decision at simulation time, and writing this value never changes any sibling route's own stored relFlow (verified live; siblings are always left untouched)."""
    session = _require_session()
    decision_no = _coerce_id(params.decision_no, "decision_no")
    route_no = _coerce_id(params.route_no, "route_no")

    session.set_route_relative_flow(decision_no, route_no, params.rel_flow, params.interval_no)
    readback = session.get_route_relative_flow(decision_no, route_no, params.interval_no)
    session.persist()

    verification: dict[str, Any] = {"written_value": params.rel_flow, "readback_value": readback}
    interval_note = "" if params.interval_no == 1 else f" interval {params.interval_no}"
    message = (
        f"Decision {params.decision_no} route {params.route_no}{interval_note} relative flow "
        f"set to {params.rel_flow} (a raw weight, not a percentage -- Vissim normalizes each "
        f"route's actual traffic share across decision {params.decision_no}'s routes at "
        "simulation time; sibling routes' own relative flow are left untouched)."
    )
    return {"message": message, "verification": verification}


# ---------------------------------------------------------------------------
# set_vehicle_composition_share(composition_no, veh_type_no, rel_flow)
#
# v2 unit ⑦. A COM verb, same shape as set_route_relative_flow: set -> get
# (readback) -> persist -> verification envelope. No interval_no -- RelFlow
# is SCALAR ONLY for vehicle compositions (verified live,
# backend/tools/probe_vehicle_compositions.py: AttValue('RelFlow(1)') raises
# "Too many sub-attributes: 1"), unlike route RelFlow. RelFlow is a RAW
# weight, not a percentage -- verified live that Vissim does NOT renormalize
# sibling entries' own stored values when one entry's RelFlow is written
# (exactly like route RelFlow, v2 unit ⑥); each vehicle type's ACTUAL
# simulated composition share is derived at RUN time as this value divided
# by the sum of every entry's RelFlow in the SAME composition. This is the
# OPPOSITE of the premise constraints.py's prior forward-declared placeholder
# assumed ("raising one class's share automatically lowers the others") --
# see that check's own corrected comment. The message here AND the dedicated
# vehicle_composition_share_redistribution_gap preflight constraint both
# spell out the normalization semantics explicitly, mirroring the route
# precedent, rather than just stating "changed to X".
# ---------------------------------------------------------------------------


class SetVehicleCompositionShareParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    composition_no: str
    veh_type_no: str
    rel_flow: float


@tool(
    name="set_vehicle_composition_share",
    tier="write",
    provider="edit",
    params=SetVehicleCompositionShareParams,
)
def set_vehicle_composition_share(params: SetVehicleCompositionShareParams) -> dict[str, Any]:
    """Set one vehicle type's relative-flow weight within a vehicle composition on the active edit session's working network -- relFlow is a RAW weight, not a percentage: Vissim computes each vehicle type's actual composition share by normalizing its relFlow against the SUM of every vehicle type's relFlow in the SAME composition at simulation time, and writing this value never changes any other vehicle type's own stored relFlow (verified live; siblings are always left untouched)."""
    session = _require_session()
    composition_no = _coerce_id(params.composition_no, "composition_no")
    veh_type_no = _coerce_id(params.veh_type_no, "veh_type_no")

    session.set_vehicle_composition_share(composition_no, veh_type_no, params.rel_flow)
    readback = session.get_vehicle_composition_share(composition_no, veh_type_no)
    session.persist()

    verification: dict[str, Any] = {"written_value": params.rel_flow, "readback_value": readback}
    message = (
        f"Vehicle composition {params.composition_no} vehicle type {params.veh_type_no} relative "
        f"flow set to {params.rel_flow} (a raw weight, not a percentage -- Vissim normalizes each "
        f"vehicle type's actual composition share across composition {params.composition_no}'s "
        "entries at simulation time; other vehicle types' own relative flow are left untouched)."
    )
    return {"message": message, "verification": verification}


# ---------------------------------------------------------------------------
# set_des_speed_decision(decision_no, veh_class_no, des_speed_distr_no,
#                         restore_decision_no=None, restore_distr_no=None)
#
# v2a unit ⑧ -- see the module docstring's own "v2a unit ⑧" paragraph for the
# atomic-pair design rationale. No shared-sum semantics at all (verified
# live, backend/tools/probe_des_speed_decisions.py): each vehicle class's
# distribution assignment is fully independent, unlike route/vehicle-
# composition RelFlow -- so there is no "raw weight" framing needed here.
# ---------------------------------------------------------------------------


class SetDesSpeedDecisionParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision_no: str
    veh_class_no: str
    des_speed_distr_no: str
    # Explicit, caller-specified restore target -- no magic pairing inference
    # (constraints.py's speed_decision_unpaired's own docstring: pairing is a
    # pure human convention, never auto-detected from geometry). Both must be
    # given together or both omitted; a half-specified pair is refused as
    # invalid_params before this model even validates successfully (pydantic
    # catches the ValueError the validator below raises).
    restore_decision_no: str | None = None
    restore_distr_no: str | None = None

    @model_validator(mode="after")
    def _restore_pair_is_all_or_nothing(self) -> "SetDesSpeedDecisionParams":
        if (self.restore_decision_no is None) != (self.restore_distr_no is None):
            raise ValueError(
                "restore_decision_no and restore_distr_no must be given together (an explicit "
                "restore target) or both omitted -- a half-specified restore pair is refused "
                "before dispatch."
            )
        return self


@tool(
    name="set_des_speed_decision",
    tier="write",
    provider="edit",
    params=SetDesSpeedDecisionParams,
)
def set_des_speed_decision(params: SetDesSpeedDecisionParams) -> dict[str, Any]:
    """Set a desired-speed decision's distribution assignment for one vehicle class on the active edit session's working network -- optionally, in the SAME approved action, ALSO write a second (restore) decision's assignment for the SAME vehicle class via restore_decision_no/restore_distr_no, atomically: both writes and both read-backs must succeed or NEITHER is persisted. A desired-speed decision is PERMANENT once a vehicle passes it -- pairing a slow-zone start with an immediate restore decision downstream is the only way to avoid leaving the rest of the network slowed for the whole trip; Vissim has no structural link between the two, so identify the restore decision yourself."""
    session = _require_session()
    decision_no = _coerce_id(params.decision_no, "decision_no")
    veh_class_no = _coerce_id(params.veh_class_no, "veh_class_no")
    des_speed_distr_no = _coerce_id(params.des_speed_distr_no, "des_speed_distr_no")

    session.set_des_speed_decision(decision_no, veh_class_no, des_speed_distr_no)
    readback = session.get_des_speed_decision(decision_no, veh_class_no)

    # written_value/readback_value are STRINGS -- des_speed_distr_no is an
    # id (which distribution object), not a continuous physical quantity,
    # matching the shadow model's own string-typed field and
    # set_conflict_area_status's "status" convention (not the float-weight
    # convention route/vehicle-composition RelFlow use). The COM boundary
    # above still uses the coerced INT (des_speed_distr_no); comparisons and
    # the envelope use the original string params throughout so they always
    # agree in type with session.get_des_speed_decision's own string return.
    verification: dict[str, Any] = {
        "written_value": params.des_speed_distr_no,
        "readback_value": readback,
    }
    message = (
        f"Desired speed decision {params.decision_no} vehicle class {params.veh_class_no} "
        f"distribution set to {params.des_speed_distr_no}."
    )

    if params.restore_decision_no is not None:
        # ATOMIC PAIR (module docstring, "v2a unit ⑧"). Proactively verify
        # the PRIMARY write's own read-back before ever attempting the
        # restore write or calling persist() -- postwrite's generic #6 check
        # only ever inspects the top-level written_value/readback_value
        # pair, so without this the primary half could silently mismatch
        # and still get persisted alongside a correct restore write.
        if readback != params.des_speed_distr_no:
            raise RuntimeError(
                f"Desired speed decision {params.decision_no} vehicle class "
                f"{params.veh_class_no}: read-back {readback!r} does not match the written "
                f"value {params.des_speed_distr_no!r} -- aborting the paired write before "
                "touching the restore decision; nothing has been persisted."
            )

        restore_decision_no = _coerce_id(params.restore_decision_no, "restore_decision_no")
        assert params.restore_distr_no is not None  # enforced by the model validator
        restore_distr_no = _coerce_id(params.restore_distr_no, "restore_distr_no")

        session.set_des_speed_decision(restore_decision_no, veh_class_no, restore_distr_no)
        restore_readback = session.get_des_speed_decision(restore_decision_no, veh_class_no)
        if restore_readback != params.restore_distr_no:
            raise RuntimeError(
                f"Restore decision {params.restore_decision_no} vehicle class "
                f"{params.veh_class_no}: read-back {restore_readback!r} does not match the "
                f"written value {params.restore_distr_no!r} -- aborting; the primary write on "
                f"decision {params.decision_no} above is NOT persisted either (atomic pair)."
            )

        verification["restore_written_value"] = params.restore_distr_no
        verification["restore_readback_value"] = restore_readback
        message = (
            f"Desired speed decision {params.decision_no} vehicle class {params.veh_class_no} "
            f"distribution set to {params.des_speed_distr_no}, AND restore decision "
            f"{params.restore_decision_no} vehicle class {params.veh_class_no} distribution "
            f"set to {params.restore_distr_no} (paired set+restore, both writes verified "
            "before saving)."
        )

    # Persisted ONCE, after every write this action makes has already been
    # verified above (unpaired: just the one write, matching every other
    # tool's precedent; paired: both writes) -- a single SaveNetAs call
    # serializes the WHOLE live network, so both halves of a paired write
    # reach the saved file together or (on an earlier raise) neither does.
    session.persist()
    return {"message": message, "verification": verification}


# ---------------------------------------------------------------------------
# set_signal_green_split(sc_no, sg_no, program_no, green_start_s, green_end_s)
#
# v2a unit ⑨. A COM verb -- but NOT a plain SetAttValue on the target object:
# on a program-driven FIXEDTIME controller the per-SG EndGreen/EndRed
# attribute ids exist yet REFUSE writes ("Attribute End of green cannot be
# changed" -- the CycTm pattern, verified live by
# backend/tools/probe_signal_programs.py), and the Vissim 26.0 type library
# has NO signal-program interface at all. The real write path, proven live
# end to end (blob edit -> LoadNet -> SimBreakAt+SigState behavior flip
# GREEN->RED at t=11 -> SaveNetAs round trip): the whole Vissig program XML
# is itself a COM-readable AND COM-writable attribute (IntSupplyData), so
# vissim_com.set_signal_green_split decodes it, rewrites the ONE (program,
# signal group) pair's green/red command begins, and writes it back. On
# FIXEDTIMESIMPLE controllers the same verb is a plain EndRed/EndGreen
# SetAttValue pair (also live-verified, persists to endRed/endGreen XML
# attributes). program_no is REQUIRED (three-part identity: controller,
# program, signal group) -- a controller can carry several programs and only
# ProgNo's is simulated (Beijing ships progNo=2 of {1,2}); forcing the
# planner to name the program (from get_signal_programs, which marks the
# active one) kills the wrong-program silent failure and gives guardrails'
# id_param_parts a fully-determined "{sc}.{prog}.{sg}" target id.
# ---------------------------------------------------------------------------


class SetSignalGreenSplitParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sc_no: str
    sg_no: str
    program_no: str
    green_start_s: float
    green_end_s: float


@tool(
    name="set_signal_green_split",
    tier="write",
    provider="edit",
    params=SetSignalGreenSplitParams,
)
def set_signal_green_split(params: SetSignalGreenSplitParams) -> dict[str, Any]:
    """Set one signal group's green window inside a fixed-time signal program on the active edit session's working network -- green_start_s is when the group switches to green, green_end_s is when it switches to red (any amber runs immediately before that point per the program's own fixed states; a wrapping window with start > end is valid, green spans the cycle boundary). Use get_signal_programs first: the write needs the exact (sc_no, program_no, sg_no) triple, a controller can carry SEVERAL programs and only the active one (active_program_no) is simulated, and Vissim will NOT stop you from creating overlapping/conflicting green windows -- it accepts them silently, so check the other signal groups' windows yourself before proposing."""
    session = _require_session()
    sc_no = _coerce_id(params.sc_no, "sc_no")
    sg_no = _coerce_id(params.sg_no, "sg_no")
    program_no = _coerce_id(params.program_no, "program_no")

    written = session.set_signal_green_split(
        sc_no, sg_no, params.green_start_s, params.green_end_s, program_no
    )
    readback = session.get_signal_green_split(sc_no, sg_no, program_no)
    session.persist()

    # written_value/readback_value carry EXACTLY the same key set (the
    # set_reduced_speed_area precedent): constraint #6 compares them with a
    # recursive, numeric-tolerant compare, so the dict shape must agree on
    # both sides. controller_type/program_no are diagnostics, reported via
    # artifacts, never inside the compared dicts.
    verification: dict[str, Any] = {
        "written_value": {
            "green_start_s": params.green_start_s,
            "green_end_s": params.green_end_s,
        },
        "readback_value": {
            "green_start_s": readback["green_start_s"],
            "green_end_s": readback["green_end_s"],
        },
    }
    return {
        "message": (
            f"Signal controller {params.sc_no} program {params.program_no} signal group "
            f"{params.sg_no} green window set to [{params.green_start_s}s → "
            f"{params.green_end_s}s] (green_end_s is the switch-to-red point; Vissim does "
            "not check overlapping or conflicting green windows -- the guardrail warnings "
            "on this action list any temporal overlaps found)."
        ),
        "verification": verification,
        "artifacts": {
            "controller_type": written["controller_type"],
            "program_no": written["program_no"],
        },
    }


# ---------------------------------------------------------------------------
# set_priority_rule_gap_headway(priority_rule_no, marker_index, gap_time_s,
#                                headway_m)
#
# QUEUE 3 unit 2. A COM verb, shaped like set_signal_green_split: ONE object
# (a priority rule's conflict marker), TWO related numeric fields written
# together in one call and verified as one compound value_field
# (guardrails.py). Unlike green_start_s/green_end_s (always both meaningful
# together, a window), gap_time_s and headway_m are two INDEPENDENT safety
# parameters a caller may reasonably want to change one at a time -- so both
# are OPTIONAL here (at least one required), not both mandatory like
# green_split's window edges. The field NOT given is still read back and
# reported (its current, unchanged value) so the result always shows the
# complete pair, matching green_split's "value_field is a dict holding both
# sub-values, compared whole" guardrails mechanism unmodified.
#
# CENTRAL FINDING (backend/tools/probe_priority_rules.py, live-verified):
# headway_m is Vissim's MinClear/MinHdwy attribute -- COM exposes it under
# TWO aliases that write the SAME underlying value (Vissim's own error text
# calls it "Minimum clearance"), and it is a spatial clearance in METRES,
# NOT a time in seconds, despite "headway" (and the "Hdwy" COM alias)
# suggesting a temporal quantity. See vissim_com.set_priority_rule_gap_headway's
# own module comment for the full write+persist+reload proof.
# ---------------------------------------------------------------------------


class SetPriorityRuleGapHeadwayParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    priority_rule_no: str
    # 1-based, per rule -- a synthesized ordinal (conflict markers carry no
    # identity attribute of their own at all; see get_priority_rules).
    marker_index: int = Field(ge=1)
    gap_time_s: float | None = None
    headway_m: float | None = None

    @model_validator(mode="after")
    def _at_least_one_field(self) -> "SetPriorityRuleGapHeadwayParams":
        if self.gap_time_s is None and self.headway_m is None:
            raise ValueError(
                "At least one of gap_time_s or headway_m must be given -- a call that changes "
                "neither has nothing to do."
            )
        return self


@tool(
    name="set_priority_rule_gap_headway",
    tier="write",
    provider="edit",
    params=SetPriorityRuleGapHeadwayParams,
)
def set_priority_rule_gap_headway(params: SetPriorityRuleGapHeadwayParams) -> dict[str, Any]:
    """Set a priority rule's conflict marker's critical gap time (gap_time_s, seconds -- the acceptable gap a yielding vehicle needs before crossing/merging) and/or minimum clearance (headway_m, METRES -- Vissim's own COM alias for this attribute is MinHdwy, but it is the SAME underlying value as its "minimum clearance" attribute: a spatial buffer kept from the conflicting vehicle, NOT a time, despite the name) on the active edit session's working network. Give either field alone to change just that one, or both together -- the field you don't give is still read back and reported at its current, unchanged value so the result always shows the complete pair. marker_index is 1-based, per rule (see get_priority_rules for the current values and marker count)."""
    session = _require_session()
    priority_rule_no = _coerce_id(params.priority_rule_no, "priority_rule_no")

    written = session.set_priority_rule_gap_headway(
        priority_rule_no, params.marker_index, params.gap_time_s, params.headway_m
    )
    readback = session.get_priority_rule_gap_headway(priority_rule_no, params.marker_index)
    session.persist()

    # written_value/readback_value carry EXACTLY the same key set (the
    # set_signal_green_split precedent): constraint #6 compares them
    # recursively with numeric tolerance, so both sides must agree on shape.
    # The UNCHANGED field's own current value (echoed identically by
    # set_priority_rule_gap_headway/get_priority_rule_gap_headway) appears
    # in both dicts on purpose -- there is nothing to "diff out" since it is
    # identical on both sides by construction when not written this call.
    verification: dict[str, Any] = {
        "written_value": {"gap_time_s": written["gap_time_s"], "headway_m": written["headway_m"]},
        "readback_value": {"gap_time_s": readback["gap_time_s"], "headway_m": readback["headway_m"]},
    }
    changed: list[str] = []
    if params.gap_time_s is not None:
        changed.append(f"gap time -> {params.gap_time_s}s")
    if params.headway_m is not None:
        changed.append(f"headway/clearance -> {params.headway_m}m")
    message = (
        f"Priority rule {params.priority_rule_no} conflict marker {params.marker_index}: "
        f"{', '.join(changed)} (now gap_time_s={readback['gap_time_s']}, "
        f"headway_m={readback['headway_m']})."
    )
    return {"message": message, "verification": verification}


# ---------------------------------------------------------------------------
# set_des_speed_distribution_points(distribution_no, points)
#
# QUEUE 3 (v2a) unit 3 -- the "legitimate CC1 route" showcase. ATOMIC
# full-curve replace: the whole point list is replaced in one call, never a
# single point edited in place. This is not merely a design preference --
# COM's own SpeedDistrDatPts collection offers no reliable per-point surgery
# at all (see VissimController.set_des_speed_distribution_points's own
# module comment: no ItemByKey, no per-point Add/Remove, and per-point
# SetAttValue was live-verified to sometimes silently no-op) -- ReplaceAll
# (atomic, whole-collection) is the ONLY mechanism this codebase trusts.
# ---------------------------------------------------------------------------


class SpeedDistributionPointParam(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Deliberately NOT bounded here (no ge=0/le=1, no min_length on the
    # parent list) -- every mathematical invariant (>= 2 points, fraction in
    # [0, 1], non-decreasing speed/fraction, endpoints 0/1) is a
    # constraints.py preflight refuse-tier check instead, per the guardrail
    # doctrine: a bare pydantic ValidationError has no ko/en/ja rendering
    # and no candidate/suggested-fix machinery, so every semantic bound
    # belongs in the constraint catalog, not the params model.
    speed_kmh: float
    cum_fraction: float


class SetDesSpeedDistributionPointsParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    distribution_no: str
    points: list[SpeedDistributionPointParam]


@tool(
    name="set_des_speed_distribution_points",
    tier="write",
    provider="edit",
    params=SetDesSpeedDistributionPointsParams,
)
def set_des_speed_distribution_points(params: SetDesSpeedDistributionPointsParams) -> dict[str, Any]:
    """Atomically replace a desired-speed distribution's ENTIRE cumulative curve on the active edit session's working network -- points is the complete new point list (each {speed_kmh, cum_fraction}), replacing the whole curve in one operation, never editing a single point in isolation. This is the LEGITIMATE way to change how fast traffic wants to go (editing driving-behavior CC parameters directly is a separate, expert-gated capability). Requirements Vissim itself enforces (curve needs >= 2 points; every cum_fraction in [0, 1]; the first point's cum_fraction must be exactly 0.0 and the last exactly 1.0) plus requirements only this codebase checks (Vissim silently accepts a genuine speed decrease between points, a mid-curve fraction reversal, or an absurdly large speed -- all refused here before ever reaching Vissim; two consecutive points sharing the SAME speed with different fractions is legitimate -- a step-function curve seen in real data). A distribution may be a SHARED target: use get_des_speed_distributions first to see which desired-speed decisions, vehicle compositions, reduced-speed areas, and other elements reference it -- editing it here changes the curve for every one of them simultaneously."""
    session = _require_session()
    distribution_no = _coerce_id(params.distribution_no, "distribution_no")
    points = [{"speed_kmh": p.speed_kmh, "cum_fraction": p.cum_fraction} for p in params.points]

    session.set_des_speed_distribution_points(distribution_no, points)
    readback = session.get_des_speed_distribution_points(distribution_no)
    session.persist()

    verification: dict[str, Any] = {"written_value": points, "readback_value": readback}
    return {
        "message": (
            f"Desired-speed distribution {params.distribution_no}'s curve replaced with "
            f"{len(points)} point(s): {readback}."
        ),
        "verification": verification,
    }


# ---------------------------------------------------------------------------
# set_vehicle_type_reference(vehicle_type_no, field, target_no)
#
# QUEUE (v2a) unit -- "vehicle type properties, SCALARS/REFERENCES only".
# A COM verb, shaped like set_conflict_area_status/set_des_speed_decision:
# ONE object, ONE field, set -> get (readback) -> persist -> verification
# envelope. Unlike set_priority_rule_gap_headway/set_signal_green_split
# (fixed field NAMES, a value_field dict holding both), this tool's SINGLE
# target field is chosen at call time via the "field" enum param -- the
# task brief's own suggested shape ("set_vehicle_type_reference(vehicle_
# type_no, field, target_no) with an explicit field whitelist"). Probed
# live (backend/tools/probe_vehicle_types.py, Roundabout Billings.US): every
# reference is fully independent (no shared-sum/renormalization semantics,
# verified via a sibling-untouched check), and COM itself fails CLOSED on a
# bogus target_no -- constraints.py's vehicle_type_reference_target_not_found
# still refuses proactively (with a candidate list) so the failure reaches
# the user as a structured refusal, mirroring set_des_speed_decision's own
# des_speed_distribution_not_found precedent.
# ---------------------------------------------------------------------------

#: The 8 reference fields this unit ships a write verb for -- matches
#: vissim_com.VissimController.VEHICLE_TYPE_REFERENCE_FIELDS' keys exactly
#: (that dict is the single source of truth for the COM attribute id each
#: maps to). Deliberately excludes category/capacity/capExact (scalars this
#: unit ships READ-ONLY via get_vehicle_types -- see that tool's own
#: docstring for the scope reasoning) and colorDistr1-4/occupDistr (real,
#: probed COM attributes but outside the task brief's own "accel/decel
#: curves, lengths, weight/power" scope).
VEHICLE_TYPE_REFERENCE_FIELD_NAMES = (
    "model2d3d_distr",
    "weight_distr",
    "power_distr",
    "des_accel_func",
    "max_accel_func",
    "des_decel_func",
    "max_decel_func",
    "des_curve_speed_func",
)


class SetVehicleTypeReferenceParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    vehicle_type_no: str
    field: Literal[
        "model2d3d_distr",
        "weight_distr",
        "power_distr",
        "des_accel_func",
        "max_accel_func",
        "des_decel_func",
        "max_decel_func",
        "des_curve_speed_func",
    ]
    target_no: str


@tool(
    name="set_vehicle_type_reference",
    tier="write",
    provider="edit",
    params=SetVehicleTypeReferenceParams,
)
def set_vehicle_type_reference(params: SetVehicleTypeReferenceParams) -> dict[str, Any]:
    """Reassign one of a vehicle type's DISTRIBUTION/FUNCTION references on the active edit session's working network -- field is one of model2d3d_distr (the 2D/3D model shapes used to draw/size this type, resolving to a length summary), weight_distr/power_distr (physical weight/power distributions), or des_accel_func/max_accel_func/des_decel_func/max_decel_func/des_curve_speed_func (the accel/decel/curve-speed FUNCTION curves this type follows). target_no is the target object's own number in ITS OWN collection (use get_vehicle_types first to see current assignments and valid target numbers). Each reference is fully independent -- reassigning one never changes any other vehicle type's own assignment. This only reassigns WHICH distribution/function the type points at; editing a distribution's or function's own curve is not supported by this agent."""
    session = _require_session()
    vehicle_type_no = _coerce_id(params.vehicle_type_no, "vehicle_type_no")
    target_no = _coerce_id(params.target_no, "target_no")

    session.set_vehicle_type_reference(vehicle_type_no, params.field, target_no)
    readback = session.get_vehicle_type_reference(vehicle_type_no, params.field)
    session.persist()

    # written_value/readback_value are STRINGS -- target_no is an id (which
    # distribution/function object), not a continuous physical quantity,
    # matching set_des_speed_decision's own des_speed_distr_no convention.
    verification: dict[str, Any] = {"written_value": params.target_no, "readback_value": readback}
    message = (
        f"Vehicle type {params.vehicle_type_no}'s {params.field} reference set to "
        f"{params.target_no}."
    )
    return {"message": message, "verification": verification}


# ---------------------------------------------------------------------------
# set_time_distribution_normal(time_distribution_no, mean_s, std_dev_s)
# set_time_distribution_points(time_distribution_no, points)
#
# QUEUE (Q3, v2a) unit 5 -- dwell-time distributions. TWO write verbs for ONE
# shared object type (Net.TimeDistributions), gated by the distribution's OWN
# "type" attribute: NORMAL distributions carry mean_s/std_dev_s (this
# codebase's own proactive gate on top of Vissim's live-verified own refusal
# -- "Attribute Mean cannot be changed" on an EMPIRICAL target), EMPIRICAL
# distributions carry an atomic full-curve points replace (unit 3's proven
# ReplaceAll shape, re-verified live for this sibling COM collection --
# backend/tools/probe_dwell_time_distributions.py). Both mirror
# set_priority_rule_gap_headway's/set_des_speed_distribution_points' own
# shapes exactly -- see vissim_com.VissimController's own time-distribution
# module comment for the full live-verified writeup (Mean/StdDev proven NOT
# to auto-recompute from points or vice versa, so neither setter needs a
# coupled_fields sibling-scalar entry -- only the points setter needs one,
# for its own derived point_count/points_truncated).
# ---------------------------------------------------------------------------


class SetTimeDistributionNormalParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    time_distribution_no: str
    mean_s: float | None = None
    std_dev_s: float | None = None

    @model_validator(mode="after")
    def _at_least_one_field(self) -> "SetTimeDistributionNormalParams":
        if self.mean_s is None and self.std_dev_s is None:
            raise ValueError(
                "At least one of mean_s or std_dev_s must be given -- a call that changes "
                "neither has nothing to do."
            )
        return self


@tool(
    name="set_time_distribution_normal",
    tier="write",
    provider="edit",
    params=SetTimeDistributionNormalParams,
)
def set_time_distribution_normal(params: SetTimeDistributionNormalParams) -> dict[str, Any]:
    """Set a NORMAL-type time distribution's mean_s and/or std_dev_s (SECONDS) on the active edit session's working network -- either field alone, or both in the same call. time_distribution_no must refer to a distribution whose type is NORMAL (use get_time_distributions first to check) -- calling this on an EMPIRICAL distribution is refused (its actual sampled behavior comes from its points, not these fields; use set_time_distribution_points instead). This is a SHARED object type: get_time_distributions' referenced_by shows every PT-stop dwell assignment, driving-behavior CC1/zipper-overtake-time assignment, and other element pointing at this distribution -- editing it changes behavior for every one of them simultaneously."""
    session = _require_session()
    time_distribution_no = _coerce_id(params.time_distribution_no, "time_distribution_no")

    written = session.set_time_distribution_normal(time_distribution_no, params.mean_s, params.std_dev_s)
    # A SEPARATE, independent readback -- never trust the set call's own
    # return value as "proof it landed" (mirrors set_priority_rule_gap_
    # headway's own written/readback split exactly; write_verification_
    # failed (#6) would be meaningless if both sides came from one call).
    readback = session.get_time_distribution(time_distribution_no)
    session.persist()

    verification: dict[str, Any] = {
        "written_value": {"mean_s": written["mean_s"], "std_dev_s": written["std_dev_s"]},
        "readback_value": {"mean_s": readback["mean_s"], "std_dev_s": readback["std_dev_s"]},
    }
    changed: list[str] = []
    if params.mean_s is not None:
        changed.append(f"mean -> {params.mean_s}s")
    if params.std_dev_s is not None:
        changed.append(f"std dev -> {params.std_dev_s}s")
    message = (
        f"Time distribution {params.time_distribution_no}: {', '.join(changed)} "
        f"(now mean_s={readback['mean_s']}, std_dev_s={readback['std_dev_s']})."
    )
    return {"message": message, "verification": verification}


class TimeDistributionPointParam(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Deliberately NOT bounded here -- every mathematical invariant (>= 2
    # points, fraction in [0, 1], non-decreasing duration/fraction, endpoints
    # 0/1) is a constraints.py preflight refuse-tier check instead, mirroring
    # SpeedDistributionPointParam's own reasoning exactly.
    duration_s: float
    cum_fraction: float


class SetTimeDistributionPointsParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    time_distribution_no: str
    points: list[TimeDistributionPointParam]


@tool(
    name="set_time_distribution_points",
    tier="write",
    provider="edit",
    params=SetTimeDistributionPointsParams,
)
def set_time_distribution_points(params: SetTimeDistributionPointsParams) -> dict[str, Any]:
    """Atomically replace an EMPIRICAL time distribution's ENTIRE cumulative duration curve on the active edit session's working network -- points is the complete new point list (each {duration_s, cum_fraction}), replacing the whole curve in one operation, never editing a single point in isolation. time_distribution_no must refer to a distribution whose type is EMPIRICAL (use get_time_distributions first to check) -- calling this on a NORMAL distribution is refused (use set_time_distribution_normal instead). Requirements Vissim itself enforces (curve needs >= 2 points; every cum_fraction in [0, 1]; the first point's cum_fraction must be exactly 0.0 and the last exactly 1.0; duration_s cannot be negative) plus requirements only this codebase checks (Vissim silently accepts a genuine duration DECREASE between points, a mid-curve fraction reversal, or an absurdly large duration -- all refused here before ever reaching Vissim; two consecutive points sharing the SAME duration with different fractions is legitimate -- a step-function curve seen in real data). A distribution may be a SHARED target: use get_time_distributions first to see which PT-stop dwell assignments, driving behaviors, and other elements reference it -- editing it here changes the curve for every one of them simultaneously."""
    session = _require_session()
    time_distribution_no = _coerce_id(params.time_distribution_no, "time_distribution_no")
    points = [{"duration_s": p.duration_s, "cum_fraction": p.cum_fraction} for p in params.points]

    session.set_time_distribution_points(time_distribution_no, points)
    readback = session.get_time_distribution_points(time_distribution_no)
    session.persist()

    verification: dict[str, Any] = {"written_value": points, "readback_value": readback}
    return {
        "message": (
            f"Time distribution {params.time_distribution_no}'s curve replaced with "
            f"{len(points)} point(s): {readback}."
        ),
        "verification": verification,
    }


# ---------------------------------------------------------------------------
# duplicate_driving_behavior(source_no, new_name)
# set_driving_behavior_parameter(behavior_no, parameter, value)
# set_link_behavior_type_driving_behavior(link_behavior_type_no,
#                                          driving_behavior_no, veh_class_no)
#
# QUEUE 3 unit 6 -- driving-behavior parameters via duplicate-then-edit.
# THREE separate approvals, matching the product-owner-approved safety
# design exactly (never a compound auto-flow; batch composition is a later
# unit's job): duplicate (creates a brand-new, zero-reference object -- the
# FIRST tool in this codebase that creates a new object via a native COM
# "Duplicate" method, live-verified an EXACT full attribute copy, see
# vissim_com.VissimController's own driving-behavior module comment) -> edit
# the copy (refused unless it has ZERO referencing consumers --
# constraints.py's rewritten driving_behavior_shared_object_edit, a REAL
# reference-count check against the shadow model's own driving_behaviors[]
# .reference_count, not a caller-supplied "trust me" flag) -> assign the
# edited copy to its intended link-behavior-type (the ONLY real assignment
# surface, live-verified -- links reference a link-behavior-type, never a
# driving behavior directly).
#
# set_driving_behavior_parameter's own whitelist (the 8 keys of
# vissim_com.VissimController.DRIVING_BEHAVIOR_PARAM_FIELDS) deliberately
# EXCLUDES every W99 CC0-CC9 -- constraints.py's PRE-EXISTING
# agency_restricted_parameter_write constraint (registered before this unit,
# catalog §6, sourced from WSDOT/Maryland DOT guidance) already refuses every
# CC parameter write unconditionally, for a DIFFERENT reason than the
# shared-object blast-radius problem duplication solves; this unit's own
# task brief suggested "W99 CC0/CC2/CC7-ish" as an EXAMPLE whitelist, but
# shipping any of them would silently route around that pre-existing,
# catalog-sourced policy refusal rather than respect it -- see this unit's
# drafted DECISIONS.md entry for the full reasoning.
#
# set_link_behavior_type_driving_behavior can reassign a link-behavior-
# type's DEFAULT assignment OR REASSIGN an EXISTING per-vehicle-class
# override -- it CANNOT create a brand-new override for a vehicle class that
# has none today (a real, reproducible pywin32/COM landmine live-verified
# three separate ways -- see vissim_com.VissimController's own module
# comment); a caller trying that gets a clear, structured refusal
# (link_behavior_type_veh_class_override_not_found), never a raw COM error.
# ---------------------------------------------------------------------------


class DuplicateDrivingBehaviorParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_no: str
    new_name: str = Field(min_length=1)


@tool(
    name="duplicate_driving_behavior",
    tier="write",
    provider="edit",
    params=DuplicateDrivingBehaviorParams,
)
def duplicate_driving_behavior(params: DuplicateDrivingBehaviorParams) -> dict[str, Any]:
    """Create an exact, full-attribute duplicate of an existing driving behavior (source_no) on the active edit session's working network, named new_name -- the FIRST step of the duplicate-then-edit flow for driving behaviors, which are GLOBAL, SHARED objects that must never be edited in place (see set_driving_behavior_parameter). The copy gets the next free driving-behavior number automatically (you never need to guess a free slot) and starts with EVERY attribute -- including every Wiedemann-74/99 car-following parameter and lane-change setting -- identical to source_no (a native Vissim COM copy operation, not an attribute-by-attribute reconstruction). The copy has ZERO referencing consumers by construction, so it is immediately eligible for set_driving_behavior_parameter -- but it is dead weight (referenced by nothing) until you also call set_link_behavior_type_driving_behavior to wire it to its intended link-behavior-type."""
    session = _require_session()
    source_no = _coerce_id(params.source_no, "source_no")

    result = session.duplicate_driving_behavior(source_no, params.new_name)
    session.persist()

    # written_value/readback_value carry EXACTLY the same key set (the
    # set_reduced_speed_area precedent): constraint #6 (write_verification_
    # failed, applies_to ["*"]) compares them structurally, and a dict with
    # an extra key on either side is a mismatch REGARDLESS of whether every
    # shared key's value agrees. The auto-assigned new number (result["no"])
    # is not something the caller "wrote" (it was computed by this tool, not
    # supplied) -- it is reported separately via `artifacts`, never inside
    # the compared dicts; the one genuinely write-then-verify-able value here
    # is the NAME.
    verification: dict[str, Any] = {"written_value": params.new_name, "readback_value": result["name"]}
    message = (
        f"Driving behavior {params.source_no} duplicated as new behavior {result['no']} "
        f"({params.new_name!r}) -- an exact copy, zero referencing consumers so far."
    )
    return {
        "message": message,
        "verification": verification,
        "artifacts": {"new_no": result["no"], "driving_behavior": result},
    }


class SetDrivingBehaviorParameterParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    behavior_no: str
    parameter: Literal[
        "ax",
        "bx_add",
        "bx_mult",
        "saf_dist_fact_ln_chg",
        "coop_ln_chg_speed_diff",
        "min_speed_for_lat",
        "lat_dist_driv_def",
        "lat_dist_stand_def",
    ]
    value: float


@tool(
    name="set_driving_behavior_parameter",
    tier="write",
    provider="edit",
    params=SetDrivingBehaviorParameterParams,
)
def set_driving_behavior_parameter(params: SetDrivingBehaviorParameterParams) -> dict[str, Any]:
    """Set ONE whitelisted calibration parameter on a driving behavior on the active edit session's working network -- REFUSED unless behavior_no has ZERO referencing link-behavior-types (get_driving_behaviors shows reference_count/referenced_by): driving behaviors are global objects shared by every link-behavior-type that points at them, so editing a shared one in place would silently change car-following/lane-change physics everywhere it is used at once. Duplicate first (duplicate_driving_behavior), then edit the zero-reference copy. parameter is one of: ax/bx_add/bx_mult (Wiedemann-74 car-following -- ax is a distance in METERS, bx_add/bx_mult are dimensionless multipliers -- refused if behavior_no's car_following_model is not 74), or saf_dist_fact_ln_chg/coop_ln_chg_speed_diff/min_speed_for_lat/lat_dist_driv_def/lat_dist_stand_def (lane-change/lateral aggressiveness -- saf_dist_fact_ln_chg is a dimensionless factor in [0, 1], coop_ln_chg_speed_diff/min_speed_for_lat are speeds in KM/H, lat_dist_driv_def/lat_dist_stand_def are distances in METERS -- model-independent, apply regardless of car_following_model). W99 CC0-CC9 are NOT writable through this agent at all (a separate, unconditional agency-policy restriction; use default and get written engineering sign-off to deviate) and CC1 is a Time Distribution reference, not a scalar."""
    session = _require_session()
    behavior_no = _coerce_id(params.behavior_no, "behavior_no")

    result = session.set_driving_behavior_parameter(behavior_no, params.parameter, params.value)
    session.persist()

    readback = result[params.parameter]
    verification: dict[str, Any] = {"written_value": params.value, "readback_value": readback}
    message = (
        f"Driving behavior {params.behavior_no} {params.parameter} set to {params.value} "
        f"(now {readback})."
    )
    return {"message": message, "verification": verification, "artifacts": {"driving_behavior": result}}


class SetLinkBehaviorTypeDrivingBehaviorParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    link_behavior_type_no: str
    driving_behavior_no: str
    # None (default) targets the link-behavior-type's DEFAULT assignment;
    # given, targets an EXISTING per-vehicle-class override (creating a new
    # one is not supported -- see this tool's own docstring and the module
    # docstring's "QUEUE 3 unit 6" paragraph).
    veh_class_no: str | None = None


@tool(
    name="set_link_behavior_type_driving_behavior",
    tier="write",
    provider="edit",
    params=SetLinkBehaviorTypeDrivingBehaviorParams,
)
def set_link_behavior_type_driving_behavior(
    params: SetLinkBehaviorTypeDrivingBehaviorParams,
) -> dict[str, Any]:
    """Assign driving_behavior_no as a link-behavior-type's DEFAULT driving behavior (veh_class_no omitted) or reassign an EXISTING per-vehicle-class override's target (veh_class_no given) on the active edit session's working network -- the ASSIGNMENT step of the duplicate-then-edit flow: without this, a duplicated-and-edited behavior is never actually used by anything. Links reference a link-behavior-type (get_driving_behaviors' referenced_by shows each behavior's current link-behavior-type consumers, including that type's own real link_count -- how many links inherit this assignment), never a driving behavior directly -- there is no single-link-level assignment. veh_class_no must name an EXISTING per-vehicle-class override (creating a brand-new override for a vehicle class that has none today is not supported by this agent -- a known COM interface limitation, not an oversight); reassigning the DEFAULT (veh_class_no omitted) has no such restriction."""
    session = _require_session()
    link_behavior_type_no = _coerce_id(params.link_behavior_type_no, "link_behavior_type_no")
    driving_behavior_no = _coerce_id(params.driving_behavior_no, "driving_behavior_no")
    veh_class_no = (
        None if params.veh_class_no is None else _coerce_id(params.veh_class_no, "veh_class_no")
    )

    result = session.set_link_behavior_type_driving_behavior(
        link_behavior_type_no, driving_behavior_no, veh_class_no
    )
    session.persist()

    verification: dict[str, Any] = {
        "written_value": params.driving_behavior_no,
        "readback_value": result["driving_behavior_no"],
    }
    target_note = (
        "default assignment" if params.veh_class_no is None else f"vehicle-class {params.veh_class_no} override"
    )
    message = (
        f"Link-behavior-type {params.link_behavior_type_no}'s {target_note} set to driving "
        f"behavior {params.driving_behavior_no}."
    )
    return {"message": message, "verification": verification}


# ---------------------------------------------------------------------------
# rollback_last_edit()
#
# QUEUE 3 unit 7. NOT a COM verb -- like set_reduced_speed_area, this tool
# only ever touches session.working_inpx as a PATH; _ensure_connected() is
# never called, so no Vissim instance is ever launched for a rollback. See
# services.agent.rollback's own module docstring for the full
# persistence-reality map (what "artifact versions already exist" concretely
# means here: ArtifactManager's append-only edit_NNN.inpx chain + its
# existing rollback_to(version), plus the session's persisted state.json
# history for descriptive metadata) and the "one level of undo only" design
# decision (a naive latest-1 scheme breaks the moment a rollback has itself
# happened once -- see that module for why).
#
# Rollback is itself an edit: registered tier="write" like every other tool
# in this module, so it goes through the EXACT SAME approval card, preflight,
# and postwrite verification as any write (task requirement #1) -- nothing
# in orchestrator.py needed to change for that; the gate/preflight/postwrite
# sandwich is generic over tier, not over tool name.
#
# No params at all: the tool is entirely self-determining (it always targets
# "whatever the last applied edit was"), so there is nothing for a caller to
# supply, and nothing to get wrong.
#
# Verification-first (task requirement #2): the restore is a byte-for-byte
# file copy from the archived version file (or the active dataset's source
# .inpx for target_version == 0 -- ArtifactManager.rollback_to's own
# pristine-clone special case), and BOTH sides of the copy are hashed
# (SHA-256) independently -- written_value is the archived file's own hash,
# readback_value is a FRESH hash of the working copy AFTER the copy landed.
# These must agree byte-for-byte; constraint #6 (write_verification_failed,
# applies_to ["*"]) already refuses+rolls-back on any mismatch, exactly like
# every other tool in this module -- no bespoke verification path needed for
# that half. The "nothing else moved" diff (guardrails.py's postwrite) does
# NOT get a comparison against the archived shadow model for free (that
# machinery is built to compare THIS action's own before/after, not an
# arbitrary earlier snapshot) -- so this tool implements its own comparison
# instead, per the task brief's own fallback: it reparses the working copy
# both immediately before and immediately after the restore and computes the
# FULL structural diff between the two (guardrails.diff_shadow_models --
# reusing the SAME comparison logic postwrite itself uses, unexempted), then
# reports every entry back as `verification["reverted_targets"]`.
# guardrails.py's GuardrailEngine.postwrite reads that key to build a DYNAMIC
# exemption covering exactly those object(s)/field(s)/addition(s)/
# removal(s) (see that module's own comment), so a clean rollback's postwrite
# diff comes back genuinely EMPTY, and any OTHER, unexplained drift still
# refuses+rolls-back like it would for any other tool. This diff-driven
# design (rather than looking the undone tool up in the static
# _WRITE_TARGET_FIELD_SPEC table) is deliberate: it works uniformly whether
# the undone tool is a plain field edit (e.g. set_vehicle_input_volume) or
# one that ADDS a whole new object (e.g. set_reduced_speed_area's own
# des_speed_distribution -- reverting it makes that object DISAPPEAR again,
# which a static per-tool table has no vocabulary for at all), and it never
# needs updating when a new write tool is added elsewhere in this module.
#
# Structured refusals (task requirement #3): rollback.compute_rollback_plan
# raises one of three typed errors -- nothing to undo, target snapshot
# missing, or "already at the rollback boundary" (requirement #4: only ONE
# level of undo is supported, per rollback.py's own documented design
# decision) -- each embedding a literal, greppable error_type marker exactly
# like NoEditSessionError's own "no_edit_session" convention. Raised, never
# returned as a dict, so toolkit.dispatch reports a genuine status: "error"
# envelope (this module's own docstring explains why every failure path here
# raises).
#
# Run interaction (task requirement #5): rollback.RollbackPlan.run_since_edit
# is threaded into verification["run_since_edit"], which guardrails.py's
# GuardrailEngine.postwrite forwards into constraints.py's extra dict for the
# new rollback_invalidates_run_results warn-tier constraint (applies_to
# ["rollback_last_edit"]) -- see that constraint's own comment.
# ---------------------------------------------------------------------------


class RollbackLastEditParams(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _describe_diff_entry(entry: dict[str, Any]) -> str:
    label = f"{entry['object_class']}#{entry['object_id']}"
    if entry["change"] == "field_changed":
        # entry["before"] is the CURRENT (pre-rollback, about-to-be-undone)
        # value; entry["after"] is the RESTORED (post-rollback, target) value
        # -- diff_shadow_models(current_model, restored_model) puts current
        # in the "before" slot, so this reads naturally as new -> old.
        return f"{label}.{entry['field']}: {entry['before']!r} → {entry['after']!r}"
    if entry["change"] == "removed":
        return f"{label} (removed -- undoes an object the edit had added)"
    return f"{label} (re-added -- restores an object the edit had removed)"  # change == "added"


@tool(name="rollback_last_edit", tier="write", provider="edit", params=RollbackLastEditParams)
def rollback_last_edit(params: RollbackLastEditParams) -> dict[str, Any]:
    """Undo the most recently applied edit on the active edit session's working network, restoring it byte-for-byte to the state immediately before that edit -- refused with a clear reason when there is nothing to undo, the archived pre-edit snapshot cannot be found, or the immediately preceding action was itself an undo (only one level of rollback is supported at a time; make a new edit before rolling back again). Takes no parameters -- it always targets whichever edit was applied most recently."""
    del params
    session = _require_session()
    work_dir = session.working_inpx.parent

    # Raises NothingToRollBackError / RollbackTargetNotRecoverableError /
    # RollbackAlreadyAtBoundaryError -- never returned as a dict (module
    # docstring's "Failure shape" section).
    plan = rollback.compute_rollback_plan(work_dir)

    before_mtime = session.working_inpx.stat().st_mtime
    current_model = build_shadow_model(session.working_inpx)

    target_path = (
        get_active_inpx_path()
        if plan.target_version == 0
        else rollback.version_path(work_dir, plan.target_version)
    )
    written_hash = rollback.restore_working_copy(session.working_inpx, target_path)
    readback_hash = hashlib.sha256(session.working_inpx.read_bytes()).hexdigest()

    # Cache-busting guard, mirrors ArtifactManager._refresh_shadow_model
    # exactly -- build_shadow_model is mtime-keyed (tools_network.py), and a
    # fast filesystem can copy-then-reparse within the SAME mtime tick, which
    # would otherwise silently hand back the STALE pre-restore parse here.
    after_mtime = session.working_inpx.stat().st_mtime
    if after_mtime <= before_mtime:
        after_mtime = before_mtime + 0.001
        os.utime(session.working_inpx, (after_mtime, after_mtime))
    restored_model = build_shadow_model(session.working_inpx)

    # The tool's own oracle: exactly what did this restore change? (module
    # docstring's "Verification-first" paragraph.)
    diff = guardrails.diff_shadow_models(current_model, restored_model)

    reverted_targets: dict[tuple[str, str], dict[str, Any]] = {}
    for entry in diff:
        key = (entry["object_class"], entry["object_id"])
        bucket = reverted_targets.setdefault(
            key,
            {
                "object_class": entry["object_class"],
                "object_id": entry["object_id"],
                "fields": [],
                "expect_removed": False,
                "expect_added": False,
            },
        )
        if entry["change"] == "field_changed":
            bucket["fields"].append(entry["field"])
        elif entry["change"] == "removed":
            bucket["expect_removed"] = True
        elif entry["change"] == "added":
            bucket["expect_added"] = True
    for bucket in reverted_targets.values():
        bucket["fields"] = sorted(set(bucket["fields"]))

    target_descriptions = [_describe_diff_entry(entry) for entry in diff]

    if plan.undone_tool:
        undo_summary = plan.undone_tool
        if target_descriptions:
            undo_summary += f" ({'; '.join(target_descriptions)})"
    else:
        undo_summary = "the most recently applied edit (its own history record is unavailable)"

    message = (
        f"Rolled back to version {plan.target_version} (undid version {plan.reverted_from_version}: "
        f"{undo_summary}). Restored working copy verified byte-for-byte identical to the archived "
        "pre-edit snapshot."
    )
    if plan.run_since_edit:
        message += (
            " NOTE: a simulation was run after that edit -- its results no longer correspond to "
            "the model now that it has been undone."
        )

    return {
        "message": message,
        "verification": {
            "written_value": written_hash,
            "readback_value": readback_hash,
            "reverted_targets": list(reverted_targets.values()),
            "run_since_edit": plan.run_since_edit,
        },
        "artifacts": {
            "restored_to_version": plan.target_version,
            "reverted_from_version": plan.reverted_from_version,
            "undone_tool": plan.undone_tool,
            "undone_params": plan.undone_params,
            "history_available": plan.history_available,
        },
    }


# ---------------------------------------------------------------------------
# apply_edit_plan(steps)
#
# QUEUE 3 unit 8, the FINAL unit of the queue -- batch/compound edits: one
# approval for a validated multi-edit plan. All-or-nothing (user-approved
# design): one approval card lists every step's diff -- services.agent.
# batch.validate_plan (run via constraints.py's apply_edit_plan_validation)
# builds every finding the card needs (per-step preflight, same-target
# collisions, patched-shadow combined-effect detection, an always-present
# plan-overview finding), and approval_focus.py's own apply_edit_plan
# resolver builds the card's combined mini-map by reusing every step's own
# EXISTING single-tool resolver -- see both modules' own docstrings for the
# full design. Steps then apply SERIALLY with per-step readback below; the
# first failed step of ANY kind aborts the WHOLE batch and rolls back every
# already-applied step -- no partial-success end state, ever.
#
# Tier="write" like every other tool in this module, so it goes through the
# EXACT SAME approval card / preflight / postwrite sandwich as any single
# write (rollback_last_edit's own precedent) -- nothing in orchestrator.py
# needed to change for that; the gate/preflight/postwrite sandwich is
# generic over tier, not over tool name.
#
# **Version-chain design decision (why this function never calls
# ArtifactManager.save_version()/rollback_to() itself, and rollback_last_edit
# needed NO changes at all to undo a whole batch in one call):** a successful
# batch must advance the on-disk version chain by exactly ONE saved version,
# so that rollback_last_edit's existing one-level "latest - 1" undo mechanism
# undoes the WHOLE batch (the task's own explicit ask). The orchestrator's
# own generic post-dispatch save_version() call -- run unconditionally for
# any successful write-tier tool, completely unmodified by this unit -- IS
# that one version: it fires exactly once, right after THIS function returns
# an "ok" envelope for the WHOLE plan. This function therefore must NOT save
# a version per step (that would fragment the batch into N separately
# undoable versions, defeating the one-call-undoes-everything guarantee --
# and a second rollback_last_edit call to walk further back would refuse
# outright anyway, per unit 7's own one-level-undo boundary). Each step's
# OWN in-loop dispatch call DOES call session.persist() for real (every
# step's own tool function does this unmodified, exactly as it would
# standalone), so the working copy on disk genuinely reflects every applied
# step; only the DURABLE, numbered snapshot is deferred to the orchestrator's
# single post-dispatch call. The pre-batch CHECKPOINT this function needs for
# its OWN mid-batch abort path is therefore a plain file-level snapshot
# (services.agent.rollback.snapshot_working_copy/restore_working_copy -- the
# SAME byte-copy+SHA-256 machinery unit 7's rollback_last_edit uses, reused
# here in the opposite direction), never a real saved version. Consequence,
# stated plainly: a rolled-back batch reads as ONE undone thing in the
# ledger (its own single StepRecord, action="apply_edit_plan"), and
# rollback_last_edit after a SUCCESSFUL batch undoes the WHOLE batch in one
# call, exactly matching a single-step edit's own undo semantics -- there is
# no way to partially undo just the last N steps of an applied batch.
#
# **Serial apply with per-step readback.** Each step is applied via
# toolkit.dispatch(step["tool"], step["params"]) -- that tool's OWN
# registered function, called EXACTLY as it would be for a standalone
# single-step write (set -> COM readback -> session.persist()), never forked
# or re-implemented here. This loop adds ONE more check on top:
# batch.values_differ(written_value, readback_value) on the step's own
# returned verification -- an inner step's own postwrite protection
# (guardrails.GuardrailEngine.postwrite's constraint #6) never runs for it
# (only the OUTER apply_edit_plan action gets a postwrite check, once, for
# the whole batch), so this loop is this batch's own per-step stand-in for
# that same protection.
#
# **On ANY failure**: the working copy is restored to the pre-batch snapshot
# (rollback.restore_working_copy), the restore is verified byte-identical
# (SHA-256 compare against the snapshot's own hash), and a
# batch.BatchAbortedError is raised -- never returned as a dict (this
# module's own "Failure shape" convention: toolkit.dispatch only ever
# produces a genuine status: "error" envelope when the tool FUNCTION
# RAISES). Its .details dict is surfaced verbatim as the error envelope's
# `artifacts` (toolkit.dispatch's own additive convention -- see that
# function's own comment) -- naming which step failed, why, and the full
# per-step ledger (applied/rolled_back/failed/never_reached): the
# "structured result" the task brief asks for, independent of the
# human-readable message's own 300-char length cap.
# ---------------------------------------------------------------------------


class EditPlanStepParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool: str
    params: dict[str, Any] = Field(default_factory=dict)


class ApplyEditPlanParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Deliberately NOT bounded here (no min_length=2/max_length=10) -- the
    # 2..10 step-count bound is a constraints.py preflight refuse-tier check
    # instead (services.agent.batch.MIN_STEPS/MAX_STEPS), per the guardrail
    # doctrine every other semantic bound in this module already follows
    # (SetDesSpeedDistributionPointsParams' own docstring: "a bare pydantic
    # ValidationError has no ko/en/ja rendering ... every semantic bound
    # belongs in the constraint catalog, not the params model").
    steps: list[EditPlanStepParams]


#: Scratch file for the pre-batch snapshot -- never matches rollback.py's
#: `_VERSION_RE`, so it is never mistaken for a real saved edit_NNN.inpx
#: version. Deleted in a `finally` block regardless of outcome.
_BATCH_SNAPSHOT_FILENAME = "_apply_edit_plan_pre_batch.inpx"


@tool(name="apply_edit_plan", tier="write", provider="edit", params=ApplyEditPlanParams)
def apply_edit_plan(params: ApplyEditPlanParams) -> dict[str, Any]:
    """Apply a validated multi-step edit plan (2-10 steps) as ONE all-or-nothing action on the active edit session's working network -- every step is preflighted individually and shown on ONE approval card before anything runs (including cross-step checks: two steps that would silently overwrite the same target, and steps whose COMBINED effect trips a constraint no single step trips on its own). Steps then apply serially, each with its own read-back verification; the FIRST step to fail of any kind aborts the whole plan and rolls back every step already applied, so the network ends up either fully updated or completely unchanged -- never partially edited. Each step is {tool, params} for any OTHER registered write-tier tool -- run_simulation, every read tool, rollback_last_edit, and apply_edit_plan itself may never appear as a step (no recursion)."""
    session = _require_session()
    steps = [{"tool": step.tool, "params": step.params} for step in params.steps]
    total_steps = len(steps)

    work_dir = session.working_inpx.parent
    snapshot_path = work_dir / _BATCH_SNAPSHOT_FILENAME
    pre_batch_hash = rollback.snapshot_working_copy(session.working_inpx, snapshot_path)
    # Captured BEFORE any step dispatches -- the "before" half of this
    # batch's own postwrite diff (see the reverted_targets comment below).
    before_mtime = session.working_inpx.stat().st_mtime
    before_model = build_shadow_model(session.working_inpx)

    applied: list[dict[str, Any]] = []
    current_index = 0
    current_tool = ""
    try:
        for index, step in enumerate(steps, start=1):
            current_index, current_tool = index, step["tool"]

            # Second line of defense: preflight (constraints.apply_edit_plan_
            # validation) should already have refused an ineligible step
            # before this action was ever approved, but re-checking here
            # costs nothing and matches the guardrail doctrine's bias toward
            # a refused edit over a completed one with wrong numbers.
            spec = default_registry.get(step["tool"])
            if spec is None or spec.tier != "write" or step["tool"] in batch.BATCH_STEP_BLOCKLIST:
                raise RuntimeError(
                    f"{step['tool']!r} is not an eligible batch-step tool (must be a registered "
                    f"write-tier tool, never one of {sorted(batch.BATCH_STEP_BLOCKLIST)}) -- "
                    "preflight should already have refused this plan; refusing at apply time as a "
                    "second line of defense."
                )

            envelope = dispatch(step["tool"], step["params"], registry=default_registry)
            if envelope["status"] != "ok":
                raise RuntimeError(
                    envelope.get("message")
                    or f"{step['tool']} dispatch failed ({envelope.get('error_type')})"
                )

            verification = envelope.get("verification") or {}
            if "written_value" in verification and "readback_value" in verification:
                if batch.values_differ(verification["written_value"], verification["readback_value"]):
                    raise RuntimeError(
                        f"read-back mismatch after apply (wrote {verification['written_value']!r}, "
                        f"read back {verification['readback_value']!r})"
                    )

            target = batch.resolve_write_target({"tool": step["tool"], "params": step["params"]})
            applied.append(
                {
                    "index": index,
                    "tool": step["tool"],
                    "params": step["params"],
                    "status": "applied",
                    "message": envelope.get("message", ""),
                    "verification": verification,
                    "target": target,
                }
            )
    except Exception as exc:  # noqa: BLE001 - any failure aborts and rolls back the whole batch
        restored_hash = rollback.restore_working_copy(session.working_inpx, snapshot_path)
        restore_verified = restored_hash == pre_batch_hash
        never_reached = [
            {"index": i, "tool": s["tool"]} for i, s in enumerate(steps, start=1) if i > current_index
        ]
        raise batch.BatchAbortedError(
            step_index=current_index,
            total_steps=total_steps,
            tool=current_tool,
            reason=str(exc),
            rolled_back=[{"index": a["index"], "tool": a["tool"]} for a in applied],
            never_reached=never_reached,
            restore_verified=restore_verified,
        ) from exc
    finally:
        snapshot_path.unlink(missing_ok=True)

    # Every step landed. `reverted_targets` reuses rollback_last_edit's own
    # dynamic diff-exemption channel (guardrails.GuardrailEngine.postwrite
    # already reads this key generically, tool-agnostic -- see that method's
    # own comment) -- but, UNLIKE the first draft of this tool, built from a
    # REAL structural diff (guardrails.diff_shadow_models) of the shadow
    # model immediately before this batch's first step vs. immediately after
    # its last, mirroring rollback_last_edit's own "verification-first"
    # mechanism exactly (see that tool's own comment for why: this is its
    # own oracle, not a prediction). This is deliberately NOT built from each
    # step's own resolve_write_target -- a step like set_reduced_speed_area
    # has no resolvable static target AND creates a brand-new side-effect
    # object (a des_speed_distributions entry) that no per-tool table
    # predicts; that tool's own guardrails._ALLOWS_NEW_OBJECTS_IN entry is
    # keyed to ITS OWN name, not "apply_edit_plan", so it would never apply
    # to a batched call -- a real diff sidesteps that gap entirely: the
    # exemption is exactly "whatever changed while this batch ran," verified
    # mechanically. See batch.reverted_targets_from_diff's own docstring.
    after_mtime = session.working_inpx.stat().st_mtime
    if after_mtime <= before_mtime:
        after_mtime = before_mtime + 0.001
        os.utime(session.working_inpx, (after_mtime, after_mtime))
    after_model = build_shadow_model(session.working_inpx)
    diff = guardrails.diff_shadow_models(before_model, after_model)
    reverted_targets = batch.reverted_targets_from_diff(diff)

    step_summary = [{"index": a["index"], "tool": a["tool"]} for a in applied]
    message = (
        f"Applied all {total_steps} step(s) of the edit plan: "
        + "; ".join(f"{a['index']}. {a['tool']}" for a in applied)
        + "."
    )
    return {
        "message": message,
        "verification": {
            # No single scalar written/read-back pair exists at the BATCH
            # level -- each step already did its own set -> get compare
            # inside the loop above. written_value/readback_value are
            # therefore an identical structural summary on both sides (a
            # trivial match by construction, since this line is only reached
            # once every step's OWN readback already verified) -- constraint
            # #6 (write_verification_failed, applies_to ["*"]) still runs
            # against this pair for every tool uniformly, it just is not the
            # primary safety net for THIS tool; `reverted_targets` above is.
            "written_value": step_summary,
            "readback_value": step_summary,
            "reverted_targets": reverted_targets,
        },
        "artifacts": {"steps": applied, "step_count": total_steps},
    }
