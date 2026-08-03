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
import math
import os
import xml.etree.ElementTree as ET
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from services.agent import batch, guardrails, rollback
from services.agent.edit_session import get_active_edit_session
from services.agent.toolkit import default_registry, dispatch, tool
from services.agent.tools_network import build_shadow_model
from services.datasets import get_active_inpx_path
from services.inpx_structure import (
    ReferenceIntegrityError,
    find_id_references,
    find_lane_token_references,
)
from services.inpx_structure import remove_connector as _remove_connector_structural
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
# set_lane_closure(link_id, lane_index, vehicle_classes) -- v2b unit (1)
#
# The catalog's "Lane closure / reopen" bullet. PROBED live before shipping
# (backend/tools/probe_lane_closure.py): Vissim's OWN native mechanism is a
# COM attribute, ``Lane.BlockedVehClasses`` -- a comma-separated vehicle
# class list, empty string = fully open -- not a distinct network-object
# type (no ``Net.LaneClosures``-shaped collection exists at all; a
# type-library scan for any Lane/Closure-named COM interface found none
# beyond the routing-decision "managed lanes" family, which is an unrelated
# feature). This IS a genuinely structural edit at the .inpx level even
# though it is a plain COM ``SetAttValue`` call, exactly like the other
# object-targeting verbs in this module: the probe's saved-file diff showed
# a non-empty blocked set serializes as a NEW child element
# (``<blockedVehClasses><intObjectRef key="10"/>...</blockedVehClasses>``)
# that does not otherwise exist on an open lane -- clearing it back to an
# empty list (REOPEN) removes that child element on the next save. See
# ``services.vissim_com.VissimController.set_lane_blocked_vehicle_classes``'s
# own docstring for the full probe findings.
#
# One setter expresses both directions (mirrors ``set_conflict_area_status``'s
# "one setter, several states including the safe default" shape):
# ``vehicle_classes=[]`` IS the reopen call -- no separate tool. No "close
# to ALL classes" wildcard exists on live COM, so a full closure means the
# caller enumerates every vehicle class number it wants blocked.
# ---------------------------------------------------------------------------


class SetLaneClosureParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    link_id: str
    #: 1-based ordinal position within the link's ``<lanes>`` -- matches
    #: live COM's own ``Lane.AttValue("Index")`` numbering (see
    #: ``_parse_link_lanes``'s docstring); lanes have no other identity.
    lane_index: int = Field(ge=1)
    #: Vehicle-class numbers to block (e.g. ``["20"]`` for HGVs). Empty list
    #: reopens the lane to every class.
    vehicle_classes: list[str] = Field(default_factory=list)


@tool(name="set_lane_closure", tier="write", provider="edit", params=SetLaneClosureParams)
def set_lane_closure(params: SetLaneClosureParams) -> dict[str, Any]:
    """Close one lane to a specific set of vehicle classes (COM attribute BlockedVehClasses), or reopen it by passing an empty vehicle_classes list -- the per-lane, per-class closure Vissim's own COM surface actually supports; there is no "close to every class" wildcard, so a full closure means listing every class number to block."""
    session = _require_session()
    link_id = _coerce_id(params.link_id, "link_id")
    try:
        # Sorted (not caller order): COM's own serialized <intObjectRef>
        # child order is not guaranteed to mirror SetAttValue's input order,
        # and guardrails.py's target-persisted/diff compares are list
        # order-sensitive -- writing (and comparing against) a canonical
        # ascending order sidesteps a spurious mismatch regardless of what
        # Vissim actually does internally.
        vehicle_classes = sorted({int(vc) for vc in params.vehicle_classes})
    except (TypeError, ValueError) as exc:
        raise ValueError(f"vehicle_classes={params.vehicle_classes!r} must be integer class numbers") from exc

    session.set_lane_closure(link_id, params.lane_index, vehicle_classes)
    readback = session.get_lane_closure(link_id, params.lane_index)
    session.persist()

    written_value = [str(vc) for vc in vehicle_classes]
    readback_value = [str(vc) for vc in readback]
    if vehicle_classes:
        message = (
            f"Lane {params.lane_index} of link {params.link_id} closed to vehicle class(es) "
            f"{', '.join(written_value)}."
        )
    else:
        message = f"Lane {params.lane_index} of link {params.link_id} reopened to all vehicle classes."
    return {
        "message": message,
        "verification": {"written_value": written_value, "readback_value": readback_value},
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
# add_stop_sign(link_no, lane_index, pos_m, name="", sc_no=None, sg_no=None)
# / remove_stop_sign(no)
#
# add_priority_rule(stop_link_no, stop_lane_index, stop_pos_m, markers) /
# remove_priority_rule(no)
#
# Q4 unit (6) -- stop-sign + priority-rule/conflict-area PLACEMENT
# (creation), the genesis counterpart to QUEUE 3 unit (2)'s gap/headway
# SETTERS above. Probed before building
# (backend/tools/probe_stop_signs_priority_placement.py, Roundabout
# Buehl.DE clone): stop signs are a genuine native COM create/remove
# (``AddStopSign``/``RemoveStopSign``), mirroring add_signal_head/
# add_detector's own shape exactly.
#
# **Priority rules are NOT a native create -- the central finding of this
# unit.** ``Net.PriorityRules.AddPriorityRule(Key, Lane, Pos,
# ConflictMarkers)`` is a CONFIRMED SILENT NO-OP (live-verified exhaustively:
# every plain-Python and explicit-VARIANT argument shape either raised or
# returned ``None`` with NO exception while genuinely creating nothing,
# checked against both ``Net.PriorityRules.GetAll()``'s own before/after id
# set AND the saved .inpx). The real, working path is
# ``DuplicatePriorityRule`` + per-marker ``AddConflictMarker``/
# ``RemoveConflictMarker`` on the duplicate -- see
# ``vissim_com.VissimController.add_priority_rule``'s own module comment for
# the full mechanism and its two child-collection landmines (stale
# ``ConflictMarkers`` caching; marker order is NOT append-order). This means
# add_priority_rule REQUIRES an existing priority rule in the network to
# duplicate as a structural template -- stated as a clear refusal, not
# silently worked around, when none exists.
#
# **Conflict areas are the special case this unit's task brief asks about
# and does NOT ship a verb for.** A type-library scan of
# ``IConflictAreaContainer``/``IConflictAreaContainerBase`` (this unit's own
# probe, Phase 1a) found NO Add/Remove method of any kind on either
# interface -- only the read/set-attribute surface ``set_conflict_area_
# status`` (Q3/Q4 units already shipped) already uses. This RE-CONFIRMS,
# in the genesis-creation context specifically, what earlier units already
# established (00-EXECUTION-GUIDE.md SS3.2, Q4 unit (5)'s own live
# observation of 6 auto-created conflict areas from add_connector alone):
# conflict areas are genuinely non-creatable/non-removable via COM, derived
# purely from geometry. This family's deliverable is this DOCUMENTED
# confirmation -- the existing ``set_conflict_area_status`` setter plus
# ``_ALLOWS_NEW_OBJECTS_IN["add_link"/"add_connector"]``'s own recompute
# exemptions (Q4 unit (5)) already cover the genesis flow end to end; no new
# verb, no new exemption entry needed for THIS unit's own additions (neither
# add_stop_sign nor add_priority_rule creates/removes links or connectors,
# so neither can trigger the conflict-area recompute at all).
# ---------------------------------------------------------------------------


class AddStopSignParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    link_no: str
    lane_index: int = Field(ge=1)
    pos_m: float
    name: str = ""
    sc_no: str | None = None
    sg_no: str | None = None

    @model_validator(mode="after")
    def _sc_and_sg_together(self) -> "AddStopSignParams":
        if (self.sc_no is None) != (self.sg_no is None):
            raise ValueError(
                "add_stop_sign: sc_no and sg_no must be given together (a stop sign's optional "
                "signal-group link is the (sc_no, sg_no) pair), or both omitted to add an "
                "unwired stop sign."
            )
        return self


@tool(name="add_stop_sign", tier="write", provider="edit", params=AddStopSignParams)
def add_stop_sign(params: AddStopSignParams) -> dict[str, Any]:
    """Add a stop sign on one lane of link_no at pos_m metres (COM AddStopSign), optionally wired to signal group sg_no of controller sc_no (SetAttValue('SG', <SG object>), mirroring add_signal_head's own wiring) on the active edit session's working network -- unsignalized-control genesis. sc_no/sg_no are NOT covered by the object_not_found preflight (see this module's own comment) -- a bogus controller or signal group fails closed as a tool_execution_error instead, naming the real available numbers."""
    session = _require_session()
    link_no = _coerce_id(params.link_no, "link_no")
    sc_no = _coerce_id(params.sc_no, "sc_no") if params.sc_no is not None else None
    sg_no = _coerce_id(params.sg_no, "sg_no") if params.sg_no is not None else None
    result = session.add_stop_sign(link_no, params.lane_index, params.pos_m, params.name, sc_no, sg_no)
    session.persist()

    written_value: dict[str, Any] = {
        "link": params.link_no,
        "lane_index": params.lane_index,
        "pos": _round_pos_m(params.pos_m),
    }
    readback_value: dict[str, Any] = {
        "link": result["link"],
        "lane_index": result["lane_index"],
        "pos": _round_pos_m(result["pos"]),
    }
    # sc/sg are only ever PART OF THE CLAIM when the caller actually asked
    # to wire them -- mirrors add_detector's own sc_no/port_no precedent
    # exactly (a caller-omitted field must never become an unverifiable
    # claim; COM does not leave SG blank on an unwired stop sign -- it
    # reads back None, which is not itself surprising, but this module
    # still only ever compares what the caller actually asked for).
    if params.sc_no is not None:
        written_value["sc"] = params.sc_no
        readback_value["sc"] = result.get("sc") or ""
    if params.sg_no is not None:
        written_value["sg"] = params.sg_no
        readback_value["sg"] = result.get("sg") or ""
    verification = {"written_value": written_value, "readback_value": readback_value}
    message = (
        f"Stop sign {result['no']} added on link {params.link_no} lane {params.lane_index} at "
        f"{params.pos_m} m."
    )
    return {
        "message": message,
        "verification": verification,
        "artifacts": {"no": result["no"], "name": result["name"]},
    }


class RemoveStopSignParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    no: str


@tool(name="remove_stop_sign", tier="write", provider="edit", params=RemoveStopSignParams)
def remove_stop_sign(params: RemoveStopSignParams) -> dict[str, Any]:
    """Remove a stop sign (COM RemoveStopSign) on the active edit session's working network -- stop signs are never referenced by another object (see services.inpx_structure.ID_BEARING_ELEMENTS/this unit's own corpus-wide static scan), so no reference-integrity check runs here."""
    session = _require_session()
    no = _coerce_id(params.no, "no")
    session.remove_stop_sign(no)
    session.persist()

    still_present = _still_present_after_remove(session.working_inpx, "stop_signs", params.no)
    verification = _remove_verification(params.no, still_present)
    return {
        "message": f"Stop sign {params.no} removed.",
        "verification": verification,
    }


class PriorityRuleMarkerSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    link_no: str
    lane_index: int = Field(ge=1)
    pos_m: float
    gap_time_s: float | None = None
    headway_m: float | None = None


class AddPriorityRuleParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stop_link_no: str
    stop_lane_index: int = Field(ge=1)
    stop_pos_m: float
    markers: list[PriorityRuleMarkerSpec] = Field(min_length=1)


@tool(name="add_priority_rule", tier="write", provider="edit", params=AddPriorityRuleParams)
def add_priority_rule(params: AddPriorityRuleParams) -> dict[str, Any]:
    """Create a priority rule at (stop_link_no, stop_lane_index, stop_pos_m) with one conflict marker per entry of markers (each {link_no, lane_index, pos_m, gap_time_s?, headway_m?}) on the active edit session's working network -- unsignalized-control genesis (the multi-marker case: a minor-road stop line yielding to two conflicting major-road lanes). Vissim's own AddPriorityRule is confirmed non-functional (silent no-op, live-verified) -- this REALLY duplicates an existing priority rule as a structural template (DuplicatePriorityRule), overrides its stop-line lane/pos, replaces its inherited marker(s) with the caller's own (AddConflictMarker/RemoveConflictMarker), and sets each marker's gap_time_s/headway_m via the SAME SetAttValue mechanism set_priority_rule_gap_headway already uses -- a marker whose gap_time_s/headway_m is omitted keeps Vissim's own out-of-the-box default (MinGapTime=3.0s/MinClear=5.0m, live-verified), reported in the result, never silently. REFUSES when the network has no existing priority rule to duplicate (a real, stated limitation -- genesis of the FIRST priority rule in an empty network is not supported by this verb). Each returned marker carries its REAL, readback-verified marker_index (Vissim's own marker ordering is NOT append-order -- see vissim_com.py's own module comment) for a follow-up set_priority_rule_gap_headway call to address it by. stop_link_no/each marker's link_no are NOT all covered by the object_not_found preflight (only stop_link_no is -- see this module's own comment) -- a bogus marker link fails closed as a tool_execution_error instead."""
    session = _require_session()
    stop_link_no = _coerce_id(params.stop_link_no, "stop_link_no")
    marker_dicts: list[dict[str, Any]] = []
    for marker in params.markers:
        marker_dicts.append(
            {
                "link_no": _coerce_id(marker.link_no, "markers[].link_no"),
                "lane_index": marker.lane_index,
                "pos_m": marker.pos_m,
                "gap_time_s": marker.gap_time_s,
                "headway_m": marker.headway_m,
            }
        )
    result = session.add_priority_rule(
        stop_link_no, params.stop_lane_index, params.stop_pos_m, marker_dicts
    )
    session.persist()

    # Per-marker CONDITIONAL claim, mirroring add_detector's own sc_no/
    # port_no precedent: gap_time_s/headway_m only ever appear in the
    # written/readback pair when the caller actually supplied them for that
    # SPECIFIC marker -- an omitted field silently defaulting to Vissim's
    # own 3.0s/5.0m must never become an unverifiable "the caller asked for
    # this exact value" claim. result["markers"] is in the SAME order as
    # params.markers (vissim_com.VissimController.add_priority_rule's own
    # contract), even though each entry's own marker_index need not equal
    # its list position.
    written_markers: list[dict[str, Any]] = []
    readback_markers: list[dict[str, Any]] = []
    for marker, marker_result in zip(params.markers, result["markers"]):
        w: dict[str, Any] = {
            "link": marker.link_no,
            "lane_index": marker.lane_index,
            "pos": _round_pos_m(marker.pos_m),
        }
        r: dict[str, Any] = {
            "link": marker_result["link"],
            "lane_index": marker_result["lane_index"],
            "pos": _round_pos_m(marker_result["pos"]),
        }
        if marker.gap_time_s is not None:
            w["gap_time_s"] = marker.gap_time_s
            r["gap_time_s"] = marker_result["gap_time_s"]
        if marker.headway_m is not None:
            w["headway_m"] = marker.headway_m
            r["headway_m"] = marker_result["headway_m"]
        written_markers.append(w)
        readback_markers.append(r)

    verification = {
        "written_value": {
            "link": params.stop_link_no,
            "lane_index": params.stop_lane_index,
            "pos": _round_pos_m(params.stop_pos_m),
            "markers": written_markers,
        },
        "readback_value": {
            "link": result["link"],
            "lane_index": result["lane_index"],
            "pos": _round_pos_m(result["pos"]),
            "markers": readback_markers,
        },
    }
    marker_desc = "; ".join(
        f"marker {rm['marker_index']} on link {rm['link']} lane {rm['lane_index']} @ {rm['pos']}m "
        f"(gap={rm['gap_time_s']}s, clearance={rm['headway_m']}m)"
        for rm in result["markers"]
    )
    message = (
        f"Priority rule {result['no']} added on link {params.stop_link_no} lane "
        f"{params.stop_lane_index} at {params.stop_pos_m} m (duplicated from template rule "
        f"{result['template_rule_no']}), with {marker_desc}."
    )
    return {
        "message": message,
        "verification": verification,
        "artifacts": {"no": result["no"], "markers": result["markers"]},
    }


class RemovePriorityRuleParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    no: str


@tool(name="remove_priority_rule", tier="write", provider="edit", params=RemovePriorityRuleParams)
def remove_priority_rule(params: RemovePriorityRuleParams) -> dict[str, Any]:
    """Remove a priority rule AND every one of its own conflict markers atomically (COM RemovePriorityRule) on the active edit session's working network -- nothing else in the schema references a priority rule by its own no (verified via a corpus-wide static scan, this unit's own probe), so no reference-integrity check runs here."""
    session = _require_session()
    no = _coerce_id(params.no, "no")
    session.remove_priority_rule(no)
    session.persist()

    still_present = _still_present_after_remove(session.working_inpx, "priority_rules", params.no)
    verification = _remove_verification(params.no, still_present)
    return {
        "message": f"Priority rule {params.no} removed.",
        "verification": verification,
    }


# ---------------------------------------------------------------------------
# add_pt_stop(link_no, lane_index, pos_m, length_m=None, name="") /
# remove_pt_stop(no)
#
# add_pt_line(entry_link_no, dest_link_no, dest_pos_m, veh_type_no=None,
#             pt_stop_no=None, name="") / remove_pt_line(no)
#
# set_pt_line_stop_dwell(line_no, stop_no, time_distribution_no)
#
# Q4 unit (7) -- PT stop + PT line PLACEMENT (genesis), the natural home of
# the BusDwell finding (Q3 unit 5, docs/phase2/DECISIONS.md 2026-07-31:
# Murdoch has 16 real ptStops / 12 ptLines / 19 ptLineStops, every LIVE
# dwell assignment shares ONE 10s+-2s NORMAL distribution, and 5 staged
# BusDwell_0-4 empirical curves sit unreferenced -- built but never wired
# in). Probed before shipping (backend/tools/probe_pt_stops_lines.py, "BRT
# Priority Texas.US" clone).
#
# **PT stops are a genuine, clean native create/remove** -- the SAME shape
# add_stop_sign/add_signal_head/add_queue_counter already use
# (``Net.PTStops.AddPTStop(Key, Lane, Pos)``, no silent-no-op landmine).
#
# **PT lines are ALSO a genuine, working native create -- correcting this
# unit's own task-brief worry that line creation would need deep route
# coding.** ``Net.PTLines.AddPTLine(Key, EntryLink, DestLink, DestPos)``
# genuinely creates a new line (confirmed via a real GetAll() diff, unlike
# Q4 unit (6)'s AddPriorityRule) and comes with a MINIMAL default route (the
# entry link only) AND exactly one auto-created stop for free -- literally
# "a line on a start link with default route", this unit's own stated
# minimal-scope escape hatch, met exactly. See
# ``vissim_com.VissimController``'s own PT-stop/PT-line module comment for
# the full live-verified writeup, including the TWO honest, stated scope
# cuts this unit ships with rather than silently working around:
#   1. ``UpdateLinkSequence``'s real argument shape is UNRESOLVED (two
#      candidate shapes both failed "array contains too few elements") --
#      a genesis line's route stays Vissim's own minimal default; a real
#      follow-up, not pursued further here.
#   2. There is NO COM create surface on a line's own LineStops collection
#      AT ALL (confirmed via the type-library scan) -- a genesis line is
#      capped at the ONE auto-created stop AddPTLine itself provides;
#      ``pt_stop_no`` merely REPOINTS that one stop (a confirmed-writable
#      ``SetAttValue`` on the auto-created entry), it does not add a second
#      one. A multi-stop line needs an existing/duplicated line, not this
#      genesis verb.
#
# **LIVE-CAUGHT (backend/tools/pt_stops_lines_live_check.py [A.7]):
# dest_link_no must be genuinely REACHABLE from entry_link_no.** An
# unreachable pair ADDS successfully (no exception) but the resulting line
# fails Vissim's own RunSingleStep init later with "Public Transport Line is
# not complete" -- confirmed by contrast: the SAME check against a real
# template line's own (entry_link, dest_link, dest_pos) triple (a
# guaranteed-reachable pair) passed cleanly, with zero departure times
# either time (ruling out "needs a timetable" as the cause). No preflight
# check validates reachability (this unit's own "constraints.py minimal"
# scope) -- add_pt_line's own docstring states this explicitly.
#
# **remove_pt_stop is this unit's one real reference-integrity refusal --
# and it exists for a WORSE reason than remove_data_collection_point's own
# precedent (Q4 unit (2)).** Live-verified: Vissim's own RemovePTStop on a
# stop a real ptLineStop still references does NOT merely dangle the
# reference -- it SILENTLY CASCADE-DELETES the referencing ptLineStop entry
# outright, with zero exception, zero warning (see
# ``services.inpx_structure.ID_REFERENCE_SITES["pt_stops"]``'s own comment
# for the three real referrer sites this unit's own corpus-wide scan found:
# ptLineStop, a separate partialPTLineStop object family, and a
# PT-boarding-gate detector). This check runs BEFORE ever calling COM.
# remove_pt_line needs no such check -- a PT line is a leaf object for
# reference-integrity purposes (nothing else in the schema references one
# by its own ``no``, per the same corpus-wide scan).
# ---------------------------------------------------------------------------


class AddPtStopParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    link_no: str
    lane_index: int = Field(ge=1)
    pos_m: float
    #: Platform length in metres. Optional -- AddPTStop's own COM signature
    #: has no Length argument at all (Vissim's own out-of-the-box default is
    #: 15.0 m); given, applied via a follow-up SetAttValue (live-verified
    #: working).
    length_m: float | None = None
    name: str = ""


@tool(name="add_pt_stop", tier="write", provider="edit", params=AddPtStopParams)
def add_pt_stop(params: AddPtStopParams) -> dict[str, Any]:
    """Add a PT stop (bus/rail platform) on one lane of link_no at pos_m metres (COM AddPTStop), optionally with an explicit platform length_m (Vissim's own default is 15.0 m if omitted) on the active edit session's working network -- PT infrastructure genesis. Wire it into a route with add_pt_line's own pt_stop_no, or by editing an existing line."""
    session = _require_session()
    link_no = _coerce_id(params.link_no, "link_no")
    result = session.add_pt_stop(link_no, params.lane_index, params.pos_m, params.length_m, params.name)
    session.persist()

    written_value: dict[str, Any] = {
        "link": params.link_no,
        "lane_index": params.lane_index,
        "pos": _round_pos_m(params.pos_m),
    }
    readback_value: dict[str, Any] = {
        "link": result["link"],
        "lane_index": result["lane_index"],
        "pos": _round_pos_m(result["pos"]),
    }
    # length is only ever PART OF THE CLAIM when the caller actually asked
    # for it -- mirrors add_stop_sign's own sc_no/sg_no precedent: an
    # omitted field (Vissim's own 15.0 m default silently applies) must
    # never become an unverifiable "the caller asked for this exact value"
    # claim.
    if params.length_m is not None:
        written_value["length"] = _round_pos_m(params.length_m)
        readback_value["length"] = _round_pos_m(result["length"])
    verification = {"written_value": written_value, "readback_value": readback_value}
    message = (
        f"PT stop {result['no']} added on link {params.link_no} lane {params.lane_index} at "
        f"{params.pos_m} m (platform length {result['length']} m)."
    )
    return {
        "message": message,
        "verification": verification,
        "artifacts": {"no": result["no"], "name": result["name"], "length": result["length"]},
    }


class RemovePtStopParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    no: str


@tool(name="remove_pt_stop", tier="write", provider="edit", params=RemovePtStopParams)
def remove_pt_stop(params: RemovePtStopParams) -> dict[str, Any]:
    """Remove a PT stop (COM RemovePTStop) on the active edit session's working network -- REFUSES (never touches the file) when a real PT-line stop, partial-route PT-line stop, or PT-boarding detector still references this stop: Vissim's own removal does NOT merely dangle the reference, it SILENTLY CASCADE-DELETES the referencing PT-line stop entry outright (live-verified) -- naming every referrer here is the only guard against that."""
    session = _require_session()
    no = _coerce_id(params.no, "no")

    # COM itself does NOT refuse this -- it silently cascade-deletes the
    # referencing ptLineStop entry (worse than remove_data_collection_
    # point's own dangling-reference precedent). Check reference integrity
    # ourselves, against the CURRENT working copy XML, via the spine's
    # registry, BEFORE ever calling COM.
    root = ET.parse(session.working_inpx).getroot()
    referrers = find_id_references(root, "pt_stops", params.no)
    if referrers:
        raise ReferenceIntegrityError(f"Cannot remove PT stop {params.no}", referrers)

    session.remove_pt_stop(no)
    session.persist()

    still_present = _still_present_after_remove(session.working_inpx, "pt_stops", params.no)
    verification = _remove_verification(params.no, still_present)
    return {
        "message": f"PT stop {params.no} removed.",
        "verification": verification,
    }


class AddPtLineParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entry_link_no: str
    dest_link_no: str
    dest_pos_m: float
    #: Optional -- not an AddPTLine argument at all, assigned via a
    #: follow-up SetAttValue.
    veh_type_no: str | None = None
    #: Optional -- repoints the ONE auto-created line-stop AddPTLine itself
    #: provides to the caller's actual intent (Vissim's own default pick is
    #: whichever real PT stop sits on entry_link_no). Does NOT add a second
    #: stop -- there is no COM surface to do that at all (see this module's
    #: own section comment).
    pt_stop_no: str | None = None
    name: str = ""


@tool(name="add_pt_line", tier="write", provider="edit", params=AddPtLineParams)
def add_pt_line(params: AddPtLineParams) -> dict[str, Any]:
    """Create a PT line (bus/rail route) from entry_link_no to (dest_link_no, dest_pos_m) (COM AddPTLine) on the active edit session's working network -- genesis creation, with two HONEST, PROBE-CONFIRMED scope limits, stated not hidden: the new line's own route is Vissim's own MINIMAL default (the entry link only -- see get_pt_lines' link_seq field to inspect it; full multi-link route editing has no working COM path today), and the line is capped at exactly ONE stop, which Vissim itself auto-creates (defaulting to whichever real PT stop sits on entry_link_no) -- give pt_stop_no to repoint that one stop to your actual intent instead (there is no COM surface to add a SECOND stop to a genesis line at all; a multi-stop line requires editing an existing line, not this verb). dest_link_no MUST be genuinely reachable from entry_link_no (a real transit route's own intent, not an arbitrary link) -- live-caught: an unreachable pair ADDS successfully with no error, but Vissim's own RunSingleStep initialization later refuses with "Public Transport Line is not complete"; this only surfaces when the network is actually run, never at add time, so pick the destination carefully. veh_type_no optionally assigns the line's vehicle type. Follow up with set_pt_line_stop_dwell to wire a dwell-time distribution to the line's one stop."""
    session = _require_session()
    entry_link_no = _coerce_id(params.entry_link_no, "entry_link_no")
    dest_link_no = _coerce_id(params.dest_link_no, "dest_link_no")
    veh_type_no = (
        _coerce_id(params.veh_type_no, "veh_type_no") if params.veh_type_no is not None else None
    )
    pt_stop_no = (
        _coerce_id(params.pt_stop_no, "pt_stop_no") if params.pt_stop_no is not None else None
    )

    result = session.add_pt_line(
        entry_link_no, dest_link_no, params.dest_pos_m, veh_type_no, pt_stop_no, params.name
    )
    session.persist()

    written_value: dict[str, Any] = {
        "entry_link": params.entry_link_no,
        "dest_link": params.dest_link_no,
        "dest_pos": _round_pos_m(params.dest_pos_m),
    }
    readback_value: dict[str, Any] = {
        "entry_link": result["entry_link"],
        "dest_link": result["dest_link"],
        "dest_pos": _round_pos_m(result["dest_pos"]),
    }
    # veh_type/pt_stop_no are only ever PART OF THE CLAIM when the caller
    # actually asked for them -- the same add_stop_sign sc_no/sg_no
    # precedent this whole module follows: an omitted field (Vissim's own
    # auto-pick applies) must never become an unverifiable claim.
    if params.veh_type_no is not None:
        written_value["veh_type"] = params.veh_type_no
        readback_value["veh_type"] = result.get("veh_type") or ""
    if params.pt_stop_no is not None:
        written_value["pt_stop_no"] = params.pt_stop_no
        readback_stop = result["stops"][0]["pt_stop_no"] if result.get("stops") else ""
        readback_value["pt_stop_no"] = readback_stop
    verification = {"written_value": written_value, "readback_value": readback_value}

    stop_note = ""
    if result.get("stops"):
        auto_stop = result["stops"][0]
        stop_note = (
            f" Auto-created stop: PT stop {auto_stop['pt_stop_no']} "
            f"(dwell distribution {auto_stop['dwell_tm_distr']})."
        )
    message = (
        f"PT line {result['no']} added from link {params.entry_link_no} to link "
        f"{params.dest_link_no}@{params.dest_pos_m}m (Vissim's minimal default route: entry "
        f"link only).{stop_note}"
    )
    return {
        "message": message,
        "verification": verification,
        "artifacts": {
            "no": result["no"],
            "name": result["name"],
            "link_seq": result["link_seq"],
            "stops": result["stops"],
        },
    }


class RemovePtLineParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    no: str


@tool(name="remove_pt_line", tier="write", provider="edit", params=RemovePtLineParams)
def remove_pt_line(params: RemovePtLineParams) -> dict[str, Any]:
    """Remove a PT line AND its own stop(s) atomically (COM RemovePTLine) on the active edit session's working network -- nothing else in the schema references a PT line by its own no (verified via a corpus-wide static scan, this unit's own probe), so no reference-integrity check runs here."""
    session = _require_session()
    no = _coerce_id(params.no, "no")
    session.remove_pt_line(no)
    session.persist()

    still_present = _still_present_after_remove(session.working_inpx, "pt_lines", params.no)
    verification = _remove_verification(params.no, still_present)
    return {
        "message": f"PT line {params.no} removed.",
        "verification": verification,
    }


class SetPtLineStopDwellParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    line_no: str
    stop_no: str
    time_distribution_no: str


@tool(
    name="set_pt_line_stop_dwell",
    tier="write",
    provider="edit",
    params=SetPtLineStopDwellParams,
)
def set_pt_line_stop_dwell(params: SetPtLineStopDwellParams) -> dict[str, Any]:
    """Wire time_distribution_no as PT line line_no's stop stop_no's own dwell-time distribution (COM SetAttValue('DwellTmDistr', ...) on the matching ptLineStop entry) on the active edit session's working network -- the BusDwell story's missing verb: connects an already-staged dwell-time distribution (e.g. an EMPIRICAL curve built in the GUI but never wired to any real stop -- see get_time_distributions' own referenced_by field) to a real line-stop. time_distribution_no can reference EITHER a NORMAL or an EMPIRICAL distribution (Vissim samples dwell time from either kind identically); use get_pt_lines first to find the real (line_no, stop_no) pairs this line actually has."""
    session = _require_session()
    line_no = _coerce_id(params.line_no, "line_no")
    stop_no = _coerce_id(params.stop_no, "stop_no")
    time_distribution_no = _coerce_id(params.time_distribution_no, "time_distribution_no")

    session.set_pt_line_stop_dwell(line_no, stop_no, time_distribution_no)
    readback = session.get_pt_line_stop_dwell(line_no, stop_no)
    session.persist()

    verification: dict[str, Any] = {
        "written_value": params.time_distribution_no,
        "readback_value": readback["dwell_tm_distr"],
    }
    message = (
        f"PT line {params.line_no} stop {params.stop_no} dwell-time distribution set to "
        f"{params.time_distribution_no}."
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
    # D3 live-demo fix (docs/phase2/DECISIONS.md, 2026-07-31): the message
    # used to inline the FULL readback point list -- unbounded in curve
    # length, same class of bug as rollback_last_edit's own (see that tool's
    # module comment). The full curve already lives in
    # verification.readback_value; the message states only the bounded fact
    # (how many points) plus a pointer to where the rest is.
    return {
        "message": (
            f"Desired-speed distribution {params.distribution_no}'s curve replaced with "
            f"{len(points)} point(s) (full curve in verification.readback_value)."
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
    # D3 live-demo fix -- see the identical comment in
    # set_des_speed_distribution_points above: never inline the full,
    # unbounded readback point list in `message`.
    return {
        "message": (
            f"Time distribution {params.time_distribution_no}'s curve replaced with "
            f"{len(points)} point(s) (full curve in verification.readback_value)."
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
# add_signal_controller(source_sc_no, new_name)
#
# Q5 unit (2) -- signal-controller GENESIS, gap (2) of the GENESIS GAP LIST
# (DECISIONS.md 2026-08-01) and the last one still fully open. The FOURTH
# appearance of the duplicate-then-edit house pattern (driving behaviors
# Q3 unit 6, priority rules Q4 unit 6, controllers Q4 unit 8/Q5 unit 1):
# plain ``AddSignalController`` creates a real but NON-FUNCTIONAL skeleton
# that makes the simulation fail outright, so no verb wraps it;
# ``DuplicateSignalController`` copies a donor's whole working program and
# is the only path shipped. See vissim_com.VissimController's own
# signal-controller-genesis module comment for the full live-verified
# writeup, and backend/tools/probe_signal_controller_duplicate.py for the
# measurements behind every claim here.
#
# ONE tool, not two: unlike driving behaviors (duplicate -> edit -> assign,
# three approvals) the "edit" and "assign" halves already exist as shipped
# verbs -- ``set_signal_green_split``/``set_signal_offset`` edit the copy's
# timing, ``add_signal_head(sc_no=..., sg_no=...)`` wires real geometry to
# it. Adding a fourth verb that only renames would be padding.
#
# The copy's own new number is auto-assigned (max + 1) exactly like
# duplicate_driving_behavior/add_priority_rule -- the caller never guesses a
# free slot -- and is reported via ``artifacts``, never inside the compared
# verification dicts (an auto-assigned id is not something the caller
# "wrote"; constraint #6 compares written/readback structurally and an extra
# key on either side is a mismatch regardless of value). The one genuinely
# write-then-verify-able value here is the NAME, mirroring
# duplicate_driving_behavior's identical reasoning.
#
# guardrails._ALLOWS_NEW_OBJECTS_IN["add_signal_controller"] = {"signals",
# "signal_program_splits"} is REQUIRED for this verb -- without it every
# correct call is refused and rolled back by postwrite's own diff. That is
# not a guess: Q5 unit (1) reproduced exactly that refusal live, and this
# unit's own probe re-derived the id set from a real
# diff_shadow_models over the saved .inpx (1 "signals" row + donor_programs
# x donor_signal_groups "signal_program_splits" rows, e.g. 44 for Beijing's
# 2 x 22, 2 for Air Taxi's 1 x 2; zero other differences).
# ---------------------------------------------------------------------------


class AddSignalControllerParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_sc_no: str
    new_name: str = Field(min_length=1)


@tool(name="add_signal_controller", tier="write", provider="edit", params=AddSignalControllerParams)
def add_signal_controller(params: AddSignalControllerParams) -> dict[str, Any]:
    """Create a NEW signal controller on the active edit session's working network as an exact duplicate of an existing one (source_sc_no), named new_name -- the only supported way to add a controller, and Genesis (b)'s hand for signalizing a junction that has no controller of its own yet. Vissim CAN create a bare empty controller, but one with no real timing program makes the simulation fail outright (live-verified), so this verb always copies a working donor instead: the copy inherits the donor's controller type, cycle time, offset, active program number, every signal group, and its ENTIRE timing program (all green splits, ambers and red-ambers), then gets the next free controller number automatically and the name you give it. Two consequences to plan for: (1) the copy runs the DONOR's timings until you change them -- use set_signal_green_split (and set_signal_offset for coordination) on the NEW controller number to make them the intended ones; (2) the copy controls nothing until you wire geometry to it with add_signal_head(sc_no=<new number>, sg_no=...). REFUSED when the donor is not a fixed-time controller (RBC/VAP/railway-crossing and similar keep their program in external files this agent can neither read back nor edit) and when the network has NO signal controller at all to copy -- a network with zero controllers cannot get its first one from this verb, a real and stated limitation."""
    session = _require_session()
    source_sc_no = _coerce_id(params.source_sc_no, "source_sc_no")

    result = session.add_signal_controller(source_sc_no, params.new_name)
    session.persist()

    # written/readback carry EXACTLY the same key set (the
    # duplicate_driving_behavior precedent, and constraint #6's structural
    # compare requires it): the NAME is the one value the caller supplied
    # and this tool can verify; the auto-assigned number rides in artifacts.
    verification: dict[str, Any] = {
        "written_value": params.new_name,
        "readback_value": result["name"],
    }
    sg_count = len(result.get("signal_group_nos") or [])
    message = (
        f"Signal controller {params.source_sc_no} duplicated as new controller {result['no']} "
        f"({params.new_name!r}) -- type {result['type'] or 'unknown'}, {sg_count} signal group(s), "
        f"the donor's whole timing program copied. It runs SC {params.source_sc_no}'s timings "
        "until you edit them (set_signal_green_split on the new number), and controls nothing "
        "until signal heads are wired to it (add_signal_head)."
    )
    return {
        "message": message,
        "verification": verification,
        "artifacts": {"new_sc_no": result["no"], "signal_controller": result},
    }


# ---------------------------------------------------------------------------
# add_vehicle_input(link_no, volume_vph, name="", composition_no=None)
#
# Q5 unit (3) -- vehicle-input GENESIS, the LAST item on the GENESIS GAP
# LIST (DECISIONS.md 2026-08-01). With this verb an agent can build a
# working intersection from nothing and Vissim will run it: geometry
# (add_link/add_connector, Q4 unit 5), control (add_signal_controller +
# add_signal_head, Q5 unit 2 / Q4 unit 4), measurement (Q4 unit 2) and now
# DEMAND.
#
# The FIRST creation verb in this codebase where plain ``Add`` is the real
# path -- ``AddPriorityRule`` was a silent no-op and ``AddSignalController``
# a non-functional skeleton, both needing a ``Duplicate*`` instead. It is
# also the first with NO zero-object family limitation: a network with zero
# vehicle inputs genuinely CAN mint its first (live-proven on Air Taxi
# Vertiport). See vissim_com.VissimController's own vehicle-input-genesis
# module comment for the full writeup and
# backend/tools/probe_vehicle_input_create.py for the measurements behind
# every claim here.
#
# ``volume_vph`` is REQUIRED. A fresh input's own Volume(1) default is 0.0,
# so an input created without one is a real object that generates nothing --
# exactly the silent-success shape the guardrail doctrine exists to prevent.
#
# The new input's own number is auto-assigned (max + 1) exactly like
# add_queue_counter/duplicate_driving_behavior -- the caller never guesses a
# free slot -- and is reported via ``artifacts``, never inside the compared
# verification dicts (an auto-assigned id is not something the caller
# "wrote"; constraint #6 compares written/readback structurally and an extra
# key on either side is a mismatch regardless of value).
#
# ONE reparse after persist(), deliberately: COM exposes no interval-count
# attribute, and the number of time intervals a fresh input carries is the
# NETWORK's property, not the verb's (5 on Toll Plaza Camden.US, 12 on Air
# Taxi Vertiport, 1 on Beijing -- all live-measured). The caller's volume
# only ever lands on interval 1, so a message that did not say how many
# intervals were left at zero would be the same silent lie
# vehicle_input_created_covers_first_interval_only warns about. The reparse
# is the same build_shadow_model call every remove verb in this module
# already makes.
#
# guardrails._ALLOWS_NEW_OBJECTS_IN["add_vehicle_input"] = {"vehicle_inputs",
# "vehicle_composition_entries"} is REQUIRED -- without the first, EVERY
# correct call is refused and rolled back; without the second, every correct
# call on a network with no <vehicleComposition> yet is (Vissim materialises
# the default composition the input references when saving). Both derived
# from real diff_shadow_models runs, not guessed.
# ---------------------------------------------------------------------------


class AddVehicleInputParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    link_no: str
    volume_vph: float
    name: str = ""
    composition_no: str | None = None


@tool(name="add_vehicle_input", tier="write", provider="edit", params=AddVehicleInputParams)
def add_vehicle_input(params: AddVehicleInputParams) -> dict[str, Any]:
    """Create a NEW vehicle input (a demand source) on link_no carrying volume_vph veh/h on the active edit session's working network -- the verb that makes a network built from scratch actually carry traffic, and the last missing piece of Genesis. The new input gets the next free input number automatically, the name you give it, and Vissim's default vehicle composition unless composition_no names another. Two things to plan for: (1) volume_vph applies to TIME INTERVAL 1 only -- if this network's demand model has several intervals, the rest stay at 0 veh/h until you set them with set_vehicle_input_volume(interval_no=...); the result message reports how many intervals this network actually has. (2) A vehicle input placed on a CONNECTOR is accepted by Vissim and does generate traffic, but the demand then enters mid-junction and bypasses the upstream approach entirely -- put it on the approach link itself unless you specifically mean that."""
    session = _require_session()
    link_no = _coerce_id(params.link_no, "link_no")
    composition_no = (
        None if params.composition_no is None else _coerce_id(params.composition_no, "composition_no")
    )

    result = session.add_vehicle_input(link_no, params.volume_vph, params.name, composition_no)
    session.persist()

    # written/readback carry EXACTLY the same key set (constraint #6's
    # structural compare requires it): the LINK and the VOLUME are the two
    # values the caller supplied and this tool can verify; the auto-assigned
    # number rides in artifacts.
    verification: dict[str, Any] = {
        "written_value": {"link": params.link_no, "volume": float(params.volume_vph)},
        "readback_value": {"link": result["link"], "volume": float(result["volume"])},
    }

    # See this section's own comment for why this reparse is deliberate:
    # COM has no interval-count attribute, and "how many intervals stayed at
    # zero" is the one thing a caller cannot infer from the parameters.
    shadow_model = build_shadow_model(session.working_inpx)
    persisted = next(
        (vi for vi in shadow_model.get("vehicle_inputs", []) if str(vi.get("no")) == str(result["no"])),
        None,
    )
    interval_count = int((persisted or {}).get("interval_count") or 0)
    unset_intervals = max(interval_count - 1, 0)

    message = (
        f"Vehicle input {result['no']} added on link {params.link_no} at {params.volume_vph} veh/h "
        f"(vehicle composition {result['composition_no'] or 'default'})."
    )
    if unset_intervals:
        message += (
            f" This network's demand model has {interval_count} time intervals and this volume "
            f"applies to interval 1 only -- the other {unset_intervals} are still 0 veh/h. Use "
            "set_vehicle_input_volume(interval_no=...) on the new number to fill them."
        )
    return {
        "message": message,
        "verification": verification,
        "artifacts": {
            "new_vehicle_input_no": result["no"],
            "vehicle_input": result,
            "interval_count": interval_count,
            "intervals_left_at_zero": unset_intervals,
        },
    }


# ---------------------------------------------------------------------------
# Q4 unit (2): measurement-infrastructure placement -- queue counters,
# data-collection points/measurements, travel-time sections. Genuine COM
# CREATE/REMOVE verbs (see vissim_com.VissimController's own module comment
# for the full live-verified writeup: native Add*/Remove* methods exist for
# all four object types -- unlike lane closure's attribute-only write or
# reduced-speed-area's pure-XML injection, this is the first unit where COM
# creation is the right call). Every ADD tool's own COM readback (via
# session.add_*'s paired get_*) IS the verification -- the same set-then-get
# pattern every other COM verb in this module already uses; no reparse
# needed, unlike set_reduced_speed_area.
#
# **These are also this codebase's FIRST planner-facing REMOVE verbs.**
# (services.inpx_structure's own remove_last_lane/add_lane proof pair is
# deliberately NOT @tool-registered -- see that module's docstring.) A
# remove's "verification" is necessarily different in kind from a set's:
# there is no new value to read back, so each remove tool here reparses the
# saved working copy AFTER persist() and reports whether the removed
# object's own id genuinely no longer appears -- the guardrail doctrine's
# "every setter must have a paired reader" applied to a remove instead of a
# set (absence, not a value, is what gets verified).
#
# **remove_data_collection_point is the one tool in this unit with a real
# reference-integrity refusal.** Live-verified (backend/tools/
# probe_measurement_infra_followup.py): COM's own RemoveDataCollectionPoint
# does NOT refuse removing a point a dataCollectionMeasurement still
# references -- it succeeds silently, leaving the measurement's own
# DataCollectionPoints membership pointing at a now-nonexistent point (a
# genuinely inconsistent saved network Vissim itself will not catch, per
# the guardrail doctrine's "Vissim almost never refuses a bad edit"
# thesis). This tool checks reference integrity ITSELF, against the CURRENT
# working copy XML, via services.inpx_structure.find_id_references (the
# SAME spine every other structural removal in this codebase already
# uses -- extended this unit with a "data_collection_points" registry
# entry) BEFORE ever calling COM at all -- a genuine refusal (raises
# ReferenceIntegrityError, naming every referencing measurement), not
# merely a warning. Queue counters and travel-time sections are leaf
# objects (nothing else in the schema ever references either by id,
# verified against every real Vissim 2026 example network that uses one --
# see services.inpx_structure.ID_BEARING_ELEMENTS's own comment), so their
# own remove tools carry no such check.
#
# **Deliberate, documented scope cuts** (this unit's own "constraints.py
# minimal" instruction: _TOOL_TARGET_SPEC entries + at most one small
# constraint; richer logic lives here and in vissim_com.py instead):
# add_travel_time_section's SECOND link target (to_link) and
# add_data_collection_measurement's point_nos (a LIST, no single-param home
# in the shared table at all) get NO dedicated preflight Finding -- a bogus
# to_link/point_no instead surfaces as a plain tool_execution_error from
# vissim_com.VissimController._link_object_by_no's/add_data_collection_
# measurement's own fail-closed COM-backed existence check (still a genuine
# refusal, just without difflib-suggested candidates). A future unit can
# promote both to dedicated checks mirroring route_not_found_in_decision's
# own precedent.
# ---------------------------------------------------------------------------


def _still_present_after_remove(
    working_inpx, object_class: str, removed_no: str
) -> bool:
    """Fresh reparse of the JUST-PERSISTED working copy (never the stale
    in-memory shadow model from before this call) -- ``True`` if
    ``removed_no`` genuinely still appears in ``shadow_model[object_class]``
    after a remove tool's own ``session.persist()``. The paired-reader half
    of a remove's own verification (see this section's own module comment)."""
    shadow_model = build_shadow_model(working_inpx)
    return any(str(obj.get("no", "")) == str(removed_no) for obj in shadow_model.get(object_class, []))


# Bug fix (Q4 unit (3), found while writing THIS unit's own remove_connector
# tests): every remove tool above originally reported
# written_value={"removed_no": X} / readback_value={"removed_no": X if still
# present else None} -- for a GENUINELY SUCCESSFUL removal those two dicts
# are, BY DESIGN, different ({"removed_no": "3"} vs {"removed_no": None}).
# constraints.py's constraint #6 (write_verification_failed, applies_to
# ["*"], postwrite) does a bare written != readback compare with NO
# tool-specific exemption mechanism of its own (unlike the separate
# nothing-else-moved diff) -- so EVERY successful remove_queue_counter/
# remove_data_collection_point/remove_data_collection_measurement/
# remove_travel_time_section call was refused+rolled-back by
# GuardrailEngine.postwrite in the real orchestrator pipeline (reproduced:
# ``GuardrailEngine(disabled=False).postwrite(...)`` against the exact
# verification shape these four tools returned -- `blocking=True`,
# `write_verification_failed`). None of Q4 unit (2)'s own tests caught this
# because none of them called GuardrailEngine.postwrite directly with a real
# remove tool's own verification payload (test_agent_tools_measurement_infra.py
# dispatches the tool in isolation; test_agent_rollback_measurement_infra.py
# exercises rollback's OWN hash-based verification, a different code path).
# Fixed here, for all five remove tools (the four pre-existing ones plus
# this unit's own remove_connector) via one shared helper: written_value and
# readback_value both describe the SAME semantic claim ("this id should no
# longer be present") so they AGREE when the removal genuinely succeeded and
# genuinely DISAGREE (refuse -- correctly) when it did not.
def _remove_verification(
    removed_no: str, still_present: bool, *, id_field: str = "removed_no"
) -> dict[str, Any]:
    return {
        "written_value": {id_field: removed_no, "still_present": False},
        "readback_value": {id_field: removed_no, "still_present": still_present},
    }


# ---------------------------------------------------------------------------
# Live-check-caught fix (Q4 unit (2)): the meter<->display-unit round trip
# (vissim_com.VissimController._length_m_to_com/_length_com_to_m) introduces
# genuine floating-point noise at the ~1e-15 level on any FEET-display
# network (measured live: pos_m=20.0 in, 19.999999999999996 back) -- far
# under constraints.py's own 1e-9 float tolerance, EXCEPT that tolerance
# (``_values_differ``) only special-cases a BARE top-level int/float pair;
# written_value/readback_value here are DICTS (``{"link", "pos"}``), so an
# untouched nested float difference makes the whole-dict ``!=`` compare
# unequal and constraint #6 (write_verification_failed) genuinely refuses a
# CORRECT write (live-reproduced: backend/tools/measurement_infra_live_check.py
# Phase B, before this fix, on Roundabout Billings.US -- a FEET network).
# Rounding both sides of any pos-shaped field to millimetre precision here
# (never in vissim_com.py, which stays full-precision for any other caller)
# makes ``written == readback`` bit-for-bit whenever the true positions
# agree, without touching constraints.py's shared, foundational compare --
# same "round at the presentation boundary" move
# ``simulation_network_builder._INJECTED_PRECISION`` already established for
# RSA's OWN (unrelated, disk-only) float-noise source.
_POS_VERIFICATION_PRECISION = 6


def _round_pos_m(value: float) -> float:
    return round(float(value), _POS_VERIFICATION_PRECISION)


# ---------------------------------------------------------------------------
# add_queue_counter(link_no, pos_m, name="") / remove_queue_counter(no)
# ---------------------------------------------------------------------------


class AddQueueCounterParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    link_no: str
    pos_m: float
    name: str = ""


@tool(name="add_queue_counter", tier="write", provider="edit", params=AddQueueCounterParams)
def add_queue_counter(params: AddQueueCounterParams) -> dict[str, Any]:
    """Add a queue counter on link_no at pos_m metres from the link's start (COM AddQueueCounter) on the active edit session's working network -- measures queue length back to this point every simulation second (Genesis V9/MRWA Table 5-15's queue-length output)."""
    session = _require_session()
    link_no = _coerce_id(params.link_no, "link_no")
    result = session.add_queue_counter(link_no, params.pos_m, params.name)
    session.persist()

    verification = {
        "written_value": {"link": params.link_no, "pos": _round_pos_m(params.pos_m)},
        "readback_value": {"link": result["link"], "pos": _round_pos_m(result["pos"])},
    }
    message = f"Queue counter {result['no']} added on link {params.link_no} at {params.pos_m} m."
    return {
        "message": message,
        "verification": verification,
        "artifacts": {"no": result["no"], "name": result["name"]},
    }


class RemoveQueueCounterParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    no: str


@tool(name="remove_queue_counter", tier="write", provider="edit", params=RemoveQueueCounterParams)
def remove_queue_counter(params: RemoveQueueCounterParams) -> dict[str, Any]:
    """Remove a queue counter (COM RemoveQueueCounter) on the active edit session's working network -- queue counters are never referenced by another object (see services.inpx_structure.ID_BEARING_ELEMENTS), so no reference-integrity check runs here."""
    session = _require_session()
    no = _coerce_id(params.no, "no")
    session.remove_queue_counter(no)
    session.persist()

    still_present = _still_present_after_remove(session.working_inpx, "queue_counters", params.no)
    verification = _remove_verification(params.no, still_present)
    return {
        "message": f"Queue counter {params.no} removed.",
        "verification": verification,
    }


# ---------------------------------------------------------------------------
# add_data_collection_point(link_no, lane_index, pos_m, name="") /
# remove_data_collection_point(no)
# ---------------------------------------------------------------------------


class AddDataCollectionPointParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    link_no: str
    lane_index: int = Field(ge=1)
    pos_m: float
    name: str = ""


@tool(
    name="add_data_collection_point",
    tier="write",
    provider="edit",
    params=AddDataCollectionPointParams,
)
def add_data_collection_point(params: AddDataCollectionPointParams) -> dict[str, Any]:
    """Add a data-collection point on one lane of link_no at pos_m metres (COM AddDataCollectionPoint) on the active edit session's working network -- a placement marker only; group it into a measurement via add_data_collection_measurement to actually produce countable results (Genesis V9/MRWA Table 5-15's critical-section volumes)."""
    session = _require_session()
    link_no = _coerce_id(params.link_no, "link_no")
    result = session.add_data_collection_point(link_no, params.lane_index, params.pos_m, params.name)
    session.persist()

    verification = {
        "written_value": {
            "link": params.link_no, "lane_index": params.lane_index, "pos": _round_pos_m(params.pos_m)
        },
        "readback_value": {
            "link": result["link"], "lane_index": result["lane_index"], "pos": _round_pos_m(result["pos"])
        },
    }
    message = (
        f"Data collection point {result['no']} added on link {params.link_no} lane {params.lane_index} "
        f"at {params.pos_m} m."
    )
    return {
        "message": message,
        "verification": verification,
        "artifacts": {"no": result["no"], "name": result["name"]},
    }


class RemoveDataCollectionPointParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    no: str


@tool(
    name="remove_data_collection_point",
    tier="write",
    provider="edit",
    params=RemoveDataCollectionPointParams,
)
def remove_data_collection_point(params: RemoveDataCollectionPointParams) -> dict[str, Any]:
    """Remove a data-collection point (COM RemoveDataCollectionPoint) on the active edit session's working network -- REFUSES (never cascades) when a data-collection measurement still groups this point; remove that measurement first (remove_data_collection_measurement)."""
    session = _require_session()
    no = _coerce_id(params.no, "no")

    # COM itself does NOT refuse this (live-verified, vissim_com.py's own
    # module comment) -- check reference integrity ourselves, against the
    # CURRENT working copy XML, via the spine's registry, BEFORE ever
    # calling COM.
    root = ET.parse(session.working_inpx).getroot()
    referrers = find_id_references(root, "data_collection_points", params.no)
    if referrers:
        raise ReferenceIntegrityError(f"Cannot remove data collection point {params.no}", referrers)

    session.remove_data_collection_point(no)
    session.persist()

    still_present = _still_present_after_remove(session.working_inpx, "data_collection_points", params.no)
    verification = _remove_verification(params.no, still_present)
    return {
        "message": f"Data collection point {params.no} removed.",
        "verification": verification,
    }


# ---------------------------------------------------------------------------
# add_data_collection_measurement(point_nos, name="") /
# remove_data_collection_measurement(no)
# ---------------------------------------------------------------------------


class AddDataCollectionMeasurementParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    point_nos: list[str] = Field(min_length=1)
    name: str = ""


@tool(
    name="add_data_collection_measurement",
    tier="write",
    provider="edit",
    params=AddDataCollectionMeasurementParams,
)
def add_data_collection_measurement(params: AddDataCollectionMeasurementParams) -> dict[str, Any]:
    """Group 1+ existing data-collection points into a measurement (COM AddDataCollectionMeasurement + wiring DataCollectionPoints membership) on the active edit session's working network -- the object that actually accumulates countable results; a lone, ungrouped point reports nothing in Vissim's own evaluation. A bogus point number fails closed (COM's own existence check), named in the resulting error."""
    session = _require_session()
    point_nos = [_coerce_id(p, "point_nos") for p in params.point_nos]
    result = session.add_data_collection_measurement(point_nos, params.name)
    session.persist()

    verification = {
        "written_value": {"point_nos": params.point_nos},
        "readback_value": {"point_nos": [str(p) for p in result["point_nos"]]},
    }
    message = (
        f"Data collection measurement {result['no']} added, grouping point(s) "
        f"{', '.join(params.point_nos)}."
    )
    return {
        "message": message,
        "verification": verification,
        "artifacts": {"no": result["no"], "name": result["name"]},
    }


class RemoveDataCollectionMeasurementParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    no: str


@tool(
    name="remove_data_collection_measurement",
    tier="write",
    provider="edit",
    params=RemoveDataCollectionMeasurementParams,
)
def remove_data_collection_measurement(params: RemoveDataCollectionMeasurementParams) -> dict[str, Any]:
    """Remove a data-collection measurement (COM RemoveDataCollectionMeasurement) on the active edit session's working network -- frees its member points for individual removal (nothing else references a measurement by id, so this tool carries no reference-integrity check of its own)."""
    session = _require_session()
    no = _coerce_id(params.no, "no")
    session.remove_data_collection_measurement(no)
    session.persist()

    still_present = _still_present_after_remove(
        session.working_inpx, "data_collection_measurements", params.no
    )
    verification = _remove_verification(params.no, still_present)
    return {
        "message": f"Data collection measurement {params.no} removed.",
        "verification": verification,
    }


# ---------------------------------------------------------------------------
# add_travel_time_section(name, from_link, from_pos_m, to_link, to_pos_m) /
# remove_travel_time_section(no)
# ---------------------------------------------------------------------------


class AddTravelTimeSectionParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = ""
    from_link: str
    from_pos_m: float
    to_link: str
    to_pos_m: float


@tool(
    name="add_travel_time_section",
    tier="write",
    provider="edit",
    params=AddTravelTimeSectionParams,
)
def add_travel_time_section(params: AddTravelTimeSectionParams) -> dict[str, Any]:
    """Add a travel-time measurement section from (from_link, from_pos_m) to (to_link, to_pos_m) (COM AddVehicleTravelTimeMeasurement) on the active edit session's working network -- Genesis V9/MRWA Table 5-15's travel-time output. to_link is NOT covered by the object_not_found preflight (see this module's own comment) -- a bogus to_link fails closed as a tool_execution_error instead."""
    session = _require_session()
    from_link = _coerce_id(params.from_link, "from_link")
    to_link = _coerce_id(params.to_link, "to_link")
    result = session.add_travel_time_section(
        params.name, from_link, params.from_pos_m, to_link, params.to_pos_m
    )
    session.persist()

    verification = {
        "written_value": {
            "from_link": params.from_link,
            "from_pos": _round_pos_m(params.from_pos_m),
            "to_link": params.to_link,
            "to_pos": _round_pos_m(params.to_pos_m),
        },
        "readback_value": {
            "from_link": result["from_link"],
            "from_pos": _round_pos_m(result["from_pos"]),
            "to_link": result["to_link"],
            "to_pos": _round_pos_m(result["to_pos"]),
        },
    }
    message = (
        f"Travel time section {result['no']} added from link {params.from_link}@{params.from_pos_m}m "
        f"to link {params.to_link}@{params.to_pos_m}m."
    )
    return {
        "message": message,
        "verification": verification,
        "artifacts": {"no": result["no"], "name": result["name"]},
    }


class RemoveTravelTimeSectionParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    no: str


@tool(
    name="remove_travel_time_section",
    tier="write",
    provider="edit",
    params=RemoveTravelTimeSectionParams,
)
def remove_travel_time_section(params: RemoveTravelTimeSectionParams) -> dict[str, Any]:
    """Remove a travel-time measurement section (COM RemoveVehicleTravelTimeMeasurement) on the active edit session's working network -- travel-time sections are never referenced by another object (see services.inpx_structure.ID_BEARING_ELEMENTS), so no reference-integrity check runs here."""
    session = _require_session()
    no = _coerce_id(params.no, "no")
    session.remove_travel_time_section(no)
    session.persist()

    still_present = _still_present_after_remove(session.working_inpx, "travel_time_sections", params.no)
    verification = _remove_verification(params.no, still_present)
    return {
        "message": f"Travel time section {params.no} removed.",
        "verification": verification,
    }


# ---------------------------------------------------------------------------
# Q4 unit (3): full link closure + turn bans (movement removal). Three
# verbs, probed before shipping (backend/tools/probe_link_closure_turn_bans.py):
#
# **set_link_closure / set_turn_ban share ONE mechanism.** No dedicated
# link-level "Blocked"/"Closed" attribute exists (candidate 2 in the task
# brief -- a full LINK-attribute AttValue() candidate sweep on a live
# connector object came back empty, control-checked against a bogus
# attribute id failing closed the same way), and no Net-level
# TurnBans/MovementRestrictions/BannedTurns collection exists either (the
# ONLY "turn"/"movement"-named interfaces in the whole type library are
# IMesoTurn*/IMovement* -- MESO simulation resolution only, unrelated to
# this micro-network verb set). The real mechanism, confirmed live, is v2b
# unit (1)'s OWN per-lane ``BlockedVehClasses`` (candidate 1), composed
# across every lane of the target link/connector -- a connector resolves
# through the IDENTICAL ``Net.Links.ItemByKey(...).Lanes`` surface a plain
# link does (Vissim's own convention: a connector IS a ``<link>`` element,
# distinguished only by carrying ``fromLinkEndPt``/``toLinkEndPt``
# children), so ``set_lane_closure``'s own COM call needs zero changes to
# work against a turn -- this is legitimate reuse/composition over unit
# (1)'s machinery, not a new mechanism. ``_set_all_lanes_blocked`` below is
# the ONE shared composition helper both verbs call.
#
# **Both verbs are ONE setter expressing both directions**, mirroring
# ``set_lane_closure``/``set_conflict_area_status``'s own "one setter,
# several states including the safe default" shape:
# ``closed``/``banned`` selects the direction, and the params model's own
# validator refuses an inconsistent combination (classes given while
# reopening/un-banning, or none given while closing/banning -- Vissim has
# no "block every class" wildcard, so a full closure/ban means enumerating
# every class number, exactly like unit (1)'s own single-lane verb) rather
# than silently guessing what the caller meant.
#
# **remove_connector is the third, STRUCTURAL verb** -- the true "movement
# removal": deleting the connector's own ``<link>`` element outright (as
# opposed to the reversible ``BlockedVehClasses`` closure above). Its own
# reference-integrity story, the live-verified finding that Vissim's LoadNet
# SILENTLY drops a conflict area that still names a removed connector (never
# refuses), and why NO un-remove exists for it are documented in
# ``services.inpx_structure``'s own module comment right above
# :func:`~services.inpx_structure.remove_connector` -- this wrapper is a
# thin dispatch/verification shim around that spine function, the same
# relationship ``set_reduced_speed_area`` has with
# ``inject_reduced_speed_area``: it only ever reads ``session.working_inpx``
# as a PATH, never calls ``_ensure_connected()``, so this tool alone never
# launches a Vissim instance for the removal write itself (the spine's own
# ``apply_structural_edit`` launches a SEPARATE, dedicated instance for the
# post-write COM load oracle -- costs a real licence seat, same as every
# other structural edit built on that spine).
#
# **route consequence is a WARN, not a refusal, for the two closure verbs**
# (constraints.py's new ``link_closure_route_consequence``, preflight) --
# closing/banning leaves the link/connector syntactically intact (a route
# still names a real object, just one that currently refuses certain
# classes), so Vissim will not error, but demand assigned to a route through
# it also will not reroute itself; the warning names the affected route ids
# so the caller can decide whether to adjust them too. ``remove_connector``
# gets the OPPOSITE treatment -- a REFUSAL, not a warn -- because removing
# the connector leaves a route's own ``linkSeq`` dangling (a genuine
# reference-integrity violation, not merely an operational caveat); see
# ``services.inpx_structure.ID_REFERENCE_SITES["links"]``'s own new route/
# priority-rule/conflict-area entries.
# ---------------------------------------------------------------------------


def _set_all_lanes_blocked(
    session: Any, target_id: int, lane_count: int, vehicle_classes: list[int]
) -> dict[str, list[str]]:
    """Compose unit (1)'s ``set_lane_closure``/``get_lane_closure`` pair
    across every lane of ``target_id`` (a plain link OR a connector -- COM
    addresses both identically, live-verified
    backend/tools/probe_link_closure_turn_bans.py [1b.4]/[1b.7]/[1b.8]).
    Returns ``{"<lane_index>": [readback vehicle class strings]}`` -- the
    per-lane evidence both ``set_link_closure``/``set_turn_ban`` fold into
    their own aggregate ``verification``/``artifacts``."""
    per_lane: dict[str, list[str]] = {}
    for lane_index in range(1, lane_count + 1):
        session.set_lane_closure(target_id, lane_index, vehicle_classes)
        readback = session.get_lane_closure(target_id, lane_index)
        per_lane[str(lane_index)] = [str(vc) for vc in readback]
    return per_lane


class SetLinkClosureParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    link_id: str
    #: Vehicle-class numbers to block on EVERY lane. Required (non-empty)
    #: when closed=True (no "close to every class" wildcard exists on live
    #: COM -- see unit (1)'s own set_lane_closure); must be empty when
    #: closed=False (reopen always clears every blocked class).
    vehicle_classes: list[str] = Field(default_factory=list)
    closed: bool = True

    @model_validator(mode="after")
    def _classes_match_closed_state(self) -> "SetLinkClosureParams":
        if self.closed and not self.vehicle_classes:
            raise ValueError(
                "set_link_closure: closed=True requires at least one vehicle_classes entry -- "
                "Vissim has no 'close to every class' wildcard (verified live, "
                "backend/tools/probe_lane_closure.py), so a full closure means enumerating "
                "every class number to block."
            )
        if not self.closed and self.vehicle_classes:
            raise ValueError(
                "set_link_closure: closed=False (reopen) does not take vehicle_classes -- "
                "reopening always clears every blocked class on every lane."
            )
        return self


@tool(name="set_link_closure", tier="write", provider="edit", params=SetLinkClosureParams)
def set_link_closure(params: SetLinkClosureParams) -> dict[str, Any]:
    """Close every lane of link_id to vehicle_classes (closed=True, the catalog's "full link closure"), or reopen every lane (closed=False) -- a composition of set_lane_closure's own per-lane BlockedVehClasses across the WHOLE link; there is no dedicated link-level closure object or attribute in Vissim (probed live). Routes still routed through a closed link are NOT automatically rerouted by Vissim -- check the accompanying warning for the affected route ids."""
    session = _require_session()
    link_id = _coerce_id(params.link_id, "link_id")
    try:
        vehicle_classes = sorted({int(vc) for vc in params.vehicle_classes})
    except (TypeError, ValueError) as exc:
        raise ValueError(f"vehicle_classes={params.vehicle_classes!r} must be integer class numbers") from exc

    shadow = build_shadow_model(session.working_inpx)
    link = next((l for l in shadow["links"] if str(l.get("id", "")) == params.link_id), None)
    if link is None:
        raise ValueError(f"set_link_closure: link {params.link_id} not found")
    lane_count = int(link.get("lanes") or 0)
    if lane_count < 1:
        raise ValueError(f"set_link_closure: link {params.link_id} has no lanes to close")

    to_write = vehicle_classes if params.closed else []
    per_lane = _set_all_lanes_blocked(session, link_id, lane_count, to_write)
    session.persist()

    written_str = [str(vc) for vc in to_write]
    matching_lanes = sum(1 for v in per_lane.values() if v == written_str)
    verification = {
        "written_value": {"vehicle_classes": written_str, "lanes_matched": lane_count},
        "readback_value": {
            "vehicle_classes": written_str if matching_lanes == lane_count else None,
            "lanes_matched": matching_lanes,
        },
    }
    if params.closed:
        message = (
            f"Link {params.link_id} closed to vehicle class(es) {', '.join(written_str)} on all "
            f"{lane_count} lane(s)."
        )
    else:
        message = f"Link {params.link_id} reopened to all vehicle classes on all {lane_count} lane(s)."
    return {
        "message": message,
        "verification": verification,
        "artifacts": {"per_lane": per_lane},
    }


class SetTurnBanParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    connector_no: str
    #: Same required/forbidden shape as set_link_closure's vehicle_classes
    #: -- see that params model's own validator comment.
    vehicle_classes: list[str] = Field(default_factory=list)
    banned: bool = True

    @model_validator(mode="after")
    def _classes_match_banned_state(self) -> "SetTurnBanParams":
        if self.banned and not self.vehicle_classes:
            raise ValueError(
                "set_turn_ban: banned=True requires at least one vehicle_classes entry -- "
                "Vissim has no 'ban for every class' wildcard, so a full ban means enumerating "
                "every class number to block."
            )
        if not self.banned and self.vehicle_classes:
            raise ValueError(
                "set_turn_ban: banned=False (un-ban) does not take vehicle_classes -- "
                "un-banning always clears every blocked class on every lane of the connector."
            )
        return self


@tool(name="set_turn_ban", tier="write", provider="edit", params=SetTurnBanParams)
def set_turn_ban(params: SetTurnBanParams) -> dict[str, Any]:
    """Ban a turn (connector_no) for vehicle_classes (banned=True), or lift the ban (banned=False) -- a turn IS a connector link in Vissim (probed live); banning composes set_lane_closure's own per-lane BlockedVehClasses across every lane of the connector, the SAME mechanism set_link_closure uses for a plain link. Reversible (unlike remove_connector's structural removal) -- routes still routed through a banned turn are NOT automatically rerouted by Vissim; check the accompanying warning for the affected route ids."""
    session = _require_session()
    connector_id = _coerce_id(params.connector_no, "connector_no")
    try:
        vehicle_classes = sorted({int(vc) for vc in params.vehicle_classes})
    except (TypeError, ValueError) as exc:
        raise ValueError(f"vehicle_classes={params.vehicle_classes!r} must be integer class numbers") from exc

    shadow = build_shadow_model(session.working_inpx)
    link = next((l for l in shadow["links"] if str(l.get("id", "")) == params.connector_no), None)
    if link is None:
        raise ValueError(f"set_turn_ban: connector {params.connector_no} not found")
    if not link.get("is_connector"):
        raise ValueError(
            f"set_turn_ban: link {params.connector_no} is not a connector (turn) -- use "
            "set_link_closure for a plain link."
        )
    lane_count = int(link.get("lanes") or 0)
    if lane_count < 1:
        raise ValueError(f"set_turn_ban: connector {params.connector_no} has no lanes to ban")

    to_write = vehicle_classes if params.banned else []
    per_lane = _set_all_lanes_blocked(session, connector_id, lane_count, to_write)
    session.persist()

    written_str = [str(vc) for vc in to_write]
    matching_lanes = sum(1 for v in per_lane.values() if v == written_str)
    verification = {
        "written_value": {"vehicle_classes": written_str, "lanes_matched": lane_count},
        "readback_value": {
            "vehicle_classes": written_str if matching_lanes == lane_count else None,
            "lanes_matched": matching_lanes,
        },
    }
    if params.banned:
        message = (
            f"Turn (connector {params.connector_no}) banned for vehicle class(es) "
            f"{', '.join(written_str)} on all {lane_count} lane(s)."
        )
    else:
        message = f"Turn (connector {params.connector_no}) un-banned on all {lane_count} lane(s)."
    return {
        "message": message,
        "verification": verification,
        "artifacts": {"per_lane": per_lane},
    }


class RemoveConnectorParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    connector_no: str


@tool(name="remove_connector", tier="write", provider="edit", params=RemoveConnectorParams)
def remove_connector(params: RemoveConnectorParams) -> dict[str, Any]:
    """Structurally remove a connector -- Vissim's real representation of a turn -- via XML injection (services.inpx_structure.remove_connector), never COM (there is no COM Add/Remove for a plain link or connector). REFUSES, naming every referrer, when a route/priority-rule/conflict-area/other-connector still references it or one of its own lanes. NO un-remove exists for this tool: rollback_last_edit is the only undo path -- there is no add_connector counterpart."""
    session = _require_session()
    result = _remove_connector_structural(session.working_inpx, params.connector_no)

    shadow_after = result["shadow_model"]
    still_present = any(str(l.get("id", "")) == params.connector_no for l in shadow_after["links"])
    verification = _remove_verification(
        params.connector_no, still_present, id_field="removed_connector_no"
    )
    lane_count = result["metadata"]["removed_lane_count"]
    return {
        "message": (
            f"Connector {params.connector_no} structurally removed ({lane_count} lane(s)). "
            "No un-remove exists for this edit -- use rollback_last_edit to undo."
        ),
        "verification": verification,
        "artifacts": {"load_check_result": result.get("load_check_result")},
    }


# ---------------------------------------------------------------------------
# Q4 unit (4): signal-head + detector placement -- genesis milestone (b)'s
# "hands" (manual placement now, policy later; see docs/product/
# master-roadmap-2026-07.md item 3.1). Genuine COM CREATE/REMOVE verbs, the
# SAME shape Q4 unit (2)'s measurement infra established (native Add*/
# Remove* methods, COM-create preferred over XML injection -- see
# vissim_com.VissimController's own module comment for the full
# live-verified writeup: the pywin32 Lane-object landmine, the display-unit
# landmine on BOTH Pos and a detector's own Length, the signal head's
# COM-object-only SG wiring vs. the detector's flat scalar SC/PortNo wiring).
#
# **Reference integrity, both directions.** Probed
# (backend/tools/probe_signal_heads_detectors.py [1b.9]): nothing else in
# the schema references a signal head or detector by its own "no" -- both
# are LEAF objects like queue counters/travel-time sections, so neither
# remove tool below needs a reference-integrity refusal of its own. The
# OTHER direction -- a signal head/detector itself dangling once the LANE it
# sits on disappears -- is now covered by extending
# services.inpx_structure.LANE_TOKEN_REFERENCE_SITES this unit: a
# remove_last_lane/remove_connector call against a lane a signal head or
# detector still references now REFUSES, naming it, exactly like a
# reducedSpeedArea/priorityRule reference already did.
#
# **Preflight scope (this unit's own "constraints.py minimal" instruction).**
# Both add_ tools' PRIMARY object_not_found target is the LINK they place
# against (mirrors add_queue_counter/add_data_collection_point's own
# precedent exactly). add_signal_head's sc_no/sg_no and add_detector's
# optional sc_no get NO dedicated preflight Finding -- a bogus one fails
# CLOSED as a tool_execution_error via vissim_com.py's own
# _item_by_key/_signal_group_by_key structured errors (naming the real
# available numbers), the SAME deliberate, documented scope cut
# add_travel_time_section's to_link and add_data_collection_measurement's
# point_nos already established. This unit's own ONE new constraint,
# signal_head_detector_pos_out_of_range (below), instead covers the
# data-driven range check the shadow model can answer for free.
# ---------------------------------------------------------------------------

#: MRWA Appendix A default for a detector at a signalized-intersection
#: approach: 2.0 m back from the signal head/stop line, 4.5 m long
#: (docs/product/master-roadmap-2026-07.md item 3.1's own "§5 기본값
#: 2.0m/4.5m" citation; this codebase's local extract of Appendix A --
#: docs/product/reference/mrwa-appendix-a-extract.md -- runs to ~p.49 and
#: does not itself carry the source PDF's page 78, so that exact page cite
#: could not be independently re-verified on this machine -- the figures
#: themselves are corroborated by the roadmap entry, not invented here).
#: NOT a Vissim schema constraint (real detector lengths in the wild range
#: 1-6 m, backend/tools/probe_signal_heads_detectors.py's own static scan)
#: -- a POLICY default this tool applies, stated as such in its own
#: docstring so the LLM knows it is the authority default, not a hard rule.
_MRWA_DETECTOR_SETBACK_M = 2.0
_MRWA_DETECTOR_LENGTH_M = 4.5


class AddSignalHeadParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    link_no: str
    lane_index: int = Field(ge=1)
    pos_m: float
    sc_no: str
    sg_no: str
    name: str = ""


@tool(name="add_signal_head", tier="write", provider="edit", params=AddSignalHeadParams)
def add_signal_head(params: AddSignalHeadParams) -> dict[str, Any]:
    """Add a signal head on one lane of link_no at pos_m metres, wired to signal group sg_no of controller sc_no (COM AddSignalHead + SetAttValue('SG', <SG object>)) on the active edit session's working network -- Genesis (b)'s auto-placement hand for signal heads. sc_no/sg_no are NOT covered by the object_not_found preflight (see this module's own comment) -- a bogus controller or signal group fails closed as a tool_execution_error instead, naming the real available numbers."""
    session = _require_session()
    link_no = _coerce_id(params.link_no, "link_no")
    sc_no = _coerce_id(params.sc_no, "sc_no")
    sg_no = _coerce_id(params.sg_no, "sg_no")
    result = session.add_signal_head(link_no, params.lane_index, params.pos_m, sc_no, sg_no, params.name)
    session.persist()

    verification = {
        "written_value": {
            "link": params.link_no,
            "lane_index": params.lane_index,
            "pos": _round_pos_m(params.pos_m),
            "sc": params.sc_no,
            "sg": params.sg_no,
        },
        "readback_value": {
            "link": result["link"],
            "lane_index": result["lane_index"],
            "pos": _round_pos_m(result["pos"]),
            "sc": result["sc"],
            "sg": result["sg"],
        },
    }
    message = (
        f"Signal head {result['no']} added on link {params.link_no} lane {params.lane_index} at "
        f"{params.pos_m} m, wired to SC {params.sc_no} SG {params.sg_no}."
    )
    return {
        "message": message,
        "verification": verification,
        "artifacts": {"no": result["no"], "name": result["name"]},
    }


class RemoveSignalHeadParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    no: str


@tool(name="remove_signal_head", tier="write", provider="edit", params=RemoveSignalHeadParams)
def remove_signal_head(params: RemoveSignalHeadParams) -> dict[str, Any]:
    """Remove a signal head (COM RemoveSignalHead) on the active edit session's working network -- signal heads are never referenced by another object (see services.inpx_structure.ID_BEARING_ELEMENTS/probe [1b.9]), so no reference-integrity check runs here."""
    session = _require_session()
    no = _coerce_id(params.no, "no")
    session.remove_signal_head(no)
    session.persist()

    still_present = _still_present_after_remove(session.working_inpx, "signal_heads", params.no)
    verification = _remove_verification(params.no, still_present)
    return {
        "message": f"Signal head {params.no} removed.",
        "verification": verification,
    }


class AddDetectorParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    link_no: str
    lane_index: int = Field(ge=1)
    pos_m: float | None = None
    length_m: float = _MRWA_DETECTOR_LENGTH_M
    sc_no: str | None = None
    port_no: int | None = None
    name: str = ""

    @model_validator(mode="after")
    def _sc_and_port_together(self) -> "AddDetectorParams":
        if (self.sc_no is None) != (self.port_no is None):
            raise ValueError(
                "add_detector: sc_no and port_no must be given together (a detector's "
                "controller wiring is the (sc_no, port_no) pair -- see probe [1b.4b]/[1b.6]), "
                "or both omitted to add an unwired detector."
            )
        return self


@tool(name="add_detector", tier="write", provider="edit", params=AddDetectorParams)
def add_detector(params: AddDetectorParams) -> dict[str, Any]:
    """Add a detector on one lane of link_no, length_m long (MRWA Appendix A default 4.5 m at a signalized-intersection approach -- see this module's own _MRWA_DETECTOR_LENGTH_M comment for the citation), optionally wired to controller sc_no port port_no (COM AddDetector + SetAttValue('Length'/'SC'/'PortNo')) on the active edit session's working network -- Genesis (b)'s auto-placement hand for detectors. pos_m is OPTIONAL: when omitted, this tool looks for an EXISTING signal head on the same (link_no, lane_index) and defaults pos to that head's own pos minus the MRWA 2.0 m stop-line setback, stating so in the result message; if no such head exists, pos_m must be given explicitly (never guessed silently). sc_no is NOT covered by the object_not_found preflight (see this module's own comment) -- a bogus controller fails closed as a tool_execution_error instead."""
    session = _require_session()
    link_no = _coerce_id(params.link_no, "link_no")

    pos_m = params.pos_m
    default_note = ""
    if pos_m is None:
        shadow = build_shadow_model(session.working_inpx)
        candidate_heads = [
            head
            for head in shadow.get("signal_heads", [])
            if str(head.get("link", "")) == params.link_no and head.get("lane_index") == params.lane_index
        ]
        if not candidate_heads:
            raise ValueError(
                f"add_detector: pos_m was not given and no signal head exists on link "
                f"{params.link_no} lane {params.lane_index} to use as a stop-line proxy -- "
                "give pos_m explicitly (MRWA Appendix A default: 2.0 m back from the stop line)."
            )
        # The stop-line proxy is the head FURTHEST along the lane (closest to
        # the approach's downstream end, where a stop line sits) -- when
        # several heads share this exact (link, lane), the largest pos wins.
        stop_line_head = max(candidate_heads, key=lambda head: head.get("pos") or 0.0)
        head_pos = float(stop_line_head.get("pos") or 0.0)
        pos_m = head_pos - _MRWA_DETECTOR_SETBACK_M
        if pos_m < 0:
            raise ValueError(
                f"add_detector: default pos (signal head {stop_line_head['no']}'s own pos "
                f"{head_pos} m minus the MRWA {_MRWA_DETECTOR_SETBACK_M} m setback) is negative -- "
                "give pos_m explicitly."
            )
        default_note = (
            f" (pos defaulted to {pos_m:.3f} m -- {_MRWA_DETECTOR_SETBACK_M} m back from signal "
            f"head {stop_line_head['no']}'s own pos {head_pos} m, the MRWA stop-line default)"
        )

    sc_no = _coerce_id(params.sc_no, "sc_no") if params.sc_no is not None else None
    result = session.add_detector(
        link_no, params.lane_index, pos_m, params.length_m, sc_no, params.port_no, params.name
    )
    session.persist()

    # Live-caught (backend/tools/signal_heads_detectors_live_check.py, Phase
    # A): when sc_no/port_no are OMITTED, Vissim's own AddDetector does NOT
    # leave them blank -- it silently assigns its own default (observed on
    # Beijing: SC 1 / PortNo 1, the network's first controller). Comparing
    # the caller's own "" / None against that surprise default would make
    # constraint #6 (write_verification_failed) refuse+rollback every
    # correct, controller-less add -- so sc/port_no are only ever PART OF
    # THE CLAIM (present in written_value/readback_value at all) when the
    # caller actually asked to wire them; omitted means "no claim possible"
    # for that field, the same CATALOG-GAP philosophy this module already
    # applies to a link with no resolvable length.
    written_value: dict[str, Any] = {
        "link": params.link_no,
        "lane_index": params.lane_index,
        "pos": _round_pos_m(pos_m),
        "length": _round_pos_m(params.length_m),
    }
    readback_value: dict[str, Any] = {
        "link": result["link"],
        "lane_index": result["lane_index"],
        "pos": _round_pos_m(result["pos"]),
        "length": _round_pos_m(result["length"]),
    }
    if params.sc_no is not None:
        written_value["sc"] = params.sc_no
        readback_value["sc"] = result.get("sc") or ""
    if params.port_no is not None:
        written_value["port_no"] = params.port_no
        readback_value["port_no"] = result.get("port_no")
    verification = {"written_value": written_value, "readback_value": readback_value}
    message = (
        f"Detector {result['no']} added on link {params.link_no} lane {params.lane_index} at "
        f"{pos_m:.3f} m (length {params.length_m} m).{default_note}"
    )
    return {
        "message": message,
        "verification": verification,
        "artifacts": {"no": result["no"], "name": result["name"]},
    }


class RemoveDetectorParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    no: str


@tool(name="remove_detector", tier="write", provider="edit", params=RemoveDetectorParams)
def remove_detector(params: RemoveDetectorParams) -> dict[str, Any]:
    """Remove a detector (COM RemoveDetector) on the active edit session's working network -- detectors are never referenced by another object (see services.inpx_structure.ID_BEARING_ELEMENTS/probe [1b.9]), so no reference-integrity check runs here."""
    session = _require_session()
    no = _coerce_id(params.no, "no")
    session.remove_detector(no)
    session.persist()

    still_present = _still_present_after_remove(session.working_inpx, "detectors", params.no)
    verification = _remove_verification(params.no, still_present)
    return {
        "message": f"Detector {params.no} removed.",
        "verification": verification,
    }


# ---------------------------------------------------------------------------
# Q4 unit (5): add/remove links & connectors -- the primitive structural
# verbs genesis geometry-synthesis (milestone 2.3) stands on directly.
# Genuine COM CREATE/REMOVE (see vissim_com.VissimController's own module
# comment for the full live-verified writeup: ``ILinkContainer.AddLink``/
# ``AddConnector``/``RemoveLink`` all exist, CONTRARY to unit (3)'s own "no
# native COM Add/Remove for a plain link or connector" note -- that unit's
# probe never scanned ``ILinkContainer`` itself). Same "COM-create preferred
# over XML injection" shape units (2)/(4) already established.
#
# **Geometry fidelity shipped vs deferred (this unit's own honesty
# contract).** Shipped: a straight-line link through 2+ caller-supplied
# points (point A -> B -> ... -> N, no curvature/spline synthesis of its
# own), N lanes of the default 3.5 m width, an optional behavior/display
# type, created/verified/removable/rollback-able. Deferred, RECORDED
# FOLLOW-UPS, not silent gaps: curvature/spline fidelity beyond what Vissim
# itself auto-computes for a connector's own snapped geometry (landmine 4,
# vissim_com.py), per-lane width control (every lane gets the SAME 3.5 m --
# no ``lane_widths`` param), z-coordinates (every point's own zOffset is
# whatever Vissim itself defaults to; this unit never sets one), and
# non-contiguous/crossing lane-to-lane connector mappings (``AddConnectorEx``
# was found in the type-lib scan but not probed or used -- ``AddConnector``'s
# own "one anchor + a consecutive count" shape is what shipped).
#
# **add_link's degenerate-geometry refusals are ALL client-side, before COM
# is ever called** (live-verified, vissim_com.py's own landmine 5a: Vissim's
# OWN ``AddLink`` does NOT refuse a duplicate-point/zero-length WKT -- it
# silently creates a ``Length2D=0.0`` link; only a <2-point WKT is natively
# refused). ``AddLinkParams`` itself refuses: <2 points (``Field(min_length=
# 2)``), duplicate CONSECUTIVE points, and a total polyline length of zero.
# lane_count<1 is a hard pydantic refusal (``Field(ge=1)``); lane_count>10 is
# this unit's ONE new constraints.py entry (``add_link_lane_count_high``,
# WARN, not refuse -- a caller may genuinely want a wide facility, but 10+
# lanes on ONE link is unusual enough to flag).
#
# **remove_link's reference-integrity refusal is THE flagship finding of
# this unit** (vissim_com.py's own landmine 5b, live-verified: ``RemoveLink``
# on a link whose lane still carried a data-collection point SUCCEEDED
# SILENTLY, leaving a dangling reference -- Vissim does not enforce its own
# referential integrity here either). This tool checks BOTH directions
# itself, against the CURRENT working copy XML, via the SAME spine
# ``remove_connector``/``remove_last_lane`` already use:
# ``find_id_references`` (whole-link references -- connector endpoints,
# route linkSeq, priority-rule intLink, conflict-area linkA/linkB) PLUS
# ``find_lane_token_references`` for EVERY lane the link currently has
# (RSAs, priority-rule lane form, signal heads, detectors, another
# connector's fromLanes/toLanes) -- generalizing remove_connector's own
# per-lane loop from "every lane a CONNECTOR has" to "every lane ANY link
# has". Per the task brief: "remove_link will refuse on almost any real
# link -- that's CORRECT behavior"; the live E2E below exercises a
# freshly-created (therefore unreferenced) link specifically so the happy
# path is provable, then separately attempts a real, referenced link to
# prove the refusal fires. A CONNECTOR target is refused with a pointer to
# ``remove_connector`` instead (that verb's own reference-integrity story --
# including the silent conflict-area cascade its own module comment
# documents -- is more complete than duplicating a second, competing check
# here would be).
#
# **add_connector does not duplicate remove_connector** (already shipped,
# unit (3)) -- this is the missing CREATE half. Its own lane-mapping params
# (``from_lanes``/``to_lanes``) are validated as contiguous ascending runs
# of equal length BEFORE COM is ever called (vissim_com.py's own landmine 4:
# ``AddConnector``'s real primitive is "one anchor lane + a consecutive
# lane count" on each side independently, live-verified with a genuine
# lane-SHIFT case) -- a caller wanting a non-contiguous/crossing mapping is
# refused with a clear reason naming the "contiguous ascending run"
# requirement, not silently reinterpreted.
#
# **Reuse notes for unit ⑥ (stop signs + priority-rule placement).** Every
# helper this unit needed already existed: ``_lane_by_index``/``_item_by_
# key``/``_link_object_by_no`` (COM object resolution), ``_net_unit_length``/
# ``_length_m_to_com``/``_length_com_to_m`` (display-unit conversion --
# EVERY new Pos-shaped param needs this; the ONE exception in this whole
# codebase is WKT x/y, landmine 1), ``_remove_verification`` (the
# same-claim written/readback shape every remove tool in this module uses),
# and the spine's ``find_id_references``/``find_lane_token_references`` for
# preflight reference-integrity. A stop sign / priority-rule placement verb
# will almost certainly need the SAME lane resolution + display-unit
# conversion pattern; the spine's registries already cover priority-rule
# lane/intLink forms (extended Q4 unit (3)), so a new placement verb mainly
# needs its own COM Add*/Remove* pair in vissim_com.py plus a thin
# tools_edit.py wrapper, mirroring this unit's own shape exactly.
# ---------------------------------------------------------------------------


class AddLinkParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: >=2 (x, y) pairs, RAW METRES (vissim_com.py's own landmine 1 -- the
    #: network's base coordinate space, NOT display-unit-scaled) -- the
    #: SAME space genesis will feed real map-matched coordinates into later.
    #: Straight-line fidelity only: Vissim draws a straight segment between
    #: each consecutive pair, no curve synthesis.
    points_xy: list[tuple[float, float]] = Field(min_length=2)
    lane_count: int = Field(ge=1)
    #: COM attribute LinkBehavType (a link-behavior-type object's own "no").
    #: Omitted -> Vissim's own default (observed live: "1", the network's
    #: first behavior type) -- NOT claimed in verification unless supplied
    #: (same "COM silently fills defaults for unset optional fields"
    #: discipline Q4 unit (4)'s add_detector already established).
    behavior_type: int | None = None
    #: COM attribute DisplayType. Same silent-default caveat as behavior_type.
    display_type: int | None = None
    name: str = ""

    @model_validator(mode="after")
    def _no_degenerate_geometry(self) -> "AddLinkParams":
        pts = self.points_xy
        for i in range(1, len(pts)):
            if pts[i] == pts[i - 1]:
                raise ValueError(
                    f"add_link: points_xy has duplicate consecutive points ({pts[i - 1]!r} "
                    f"repeated at index {i}) -- a degenerate zero-length segment. Vissim itself "
                    "does NOT refuse this (live-verified: a duplicate-point WKT silently creates "
                    "a Length2D=0.0 link), so this is refused here before COM is ever called."
                )
        total_length = sum(
            math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]) for i in range(1, len(pts))
        )
        if total_length <= 0:
            raise ValueError("add_link: points_xy describes a zero-length link -- refused.")
        return self


@tool(name="add_link", tier="write", provider="edit", params=AddLinkParams)
def add_link(params: AddLinkParams) -> dict[str, Any]:
    """Add a straight-line-fidelity link through points_xy (>=2 (x, y) pairs, raw metres -- Vissim draws a straight segment between each consecutive pair, no curve synthesis) with lane_count lanes of the default 3.5 m width, created atomically (COM AddLink) on the active edit session's working network -- the genesis (2.3) geometry-synthesis primitive: point A -> B [-> ... -> N], N lanes, behavior type, display type."""
    session = _require_session()
    points_xy = [(float(x), float(y)) for x, y in params.points_xy]
    result = session.add_link(points_xy, params.lane_count, params.behavior_type, params.display_type, params.name)
    session.persist()

    def _round_points(points: list[tuple[float, float]]) -> list[list[float]]:
        return [[_round_pos_m(x), _round_pos_m(y)] for x, y in points]

    written_value: dict[str, Any] = {"points": _round_points(points_xy), "lanes": params.lane_count}
    readback_value: dict[str, Any] = {"points": _round_points(result["points"]), "lanes": result["lanes"]}
    if params.behavior_type is not None:
        written_value["behavior_type"] = str(params.behavior_type)
        readback_value["behavior_type"] = result.get("behavior_type")
    if params.display_type is not None:
        written_value["display_type"] = str(params.display_type)
        readback_value["display_type"] = result.get("display_type")

    verification = {"written_value": written_value, "readback_value": readback_value}
    message = (
        f"Link {result['no']} added: {len(points_xy)} point(s), {params.lane_count} lane(s), "
        f"length {result['length']:.3f} m (straight-line fidelity only)."
    )
    return {
        "message": message,
        "verification": verification,
        "artifacts": {"no": result["no"], "name": result["name"]},
    }


class RemoveLinkParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    no: str


@tool(name="remove_link", tier="write", provider="edit", params=RemoveLinkParams)
def remove_link(params: RemoveLinkParams) -> dict[str, Any]:
    """Remove a plain link (COM RemoveLink) on the active edit session's working network -- REFUSES, naming every referrer, when a route/priority-rule/conflict-area/connector still references the link itself OR any of its own lanes (COM's own RemoveLink does NOT enforce this -- live-verified: it silently removed a link whose lane still carried a data-collection point). Refuses a CONNECTOR target outright, pointing at remove_connector instead (that verb's own reference-integrity story is more complete). Will refuse on almost any real, in-use link -- that is correct behavior, not a bug."""
    session = _require_session()
    no = _coerce_id(params.no, "no")

    root = ET.parse(session.working_inpx).getroot()
    link_el = next((el for el in root.findall(".//links/link") if el.get("no") == params.no), None)
    if link_el is None:
        raise ValueError(f"remove_link: link {params.no} not found")
    if link_el.find("fromLinkEndPt") is not None or link_el.find("toLinkEndPt") is not None:
        raise ValueError(
            f"remove_link: {params.no} is a CONNECTOR, not a plain link -- use remove_connector "
            "instead (Q4 unit (3); it has its own, more complete reference-integrity story, "
            "including the silent conflict-area cascade that tool's own module comment documents)."
        )
    lanes_el = link_el.find("lanes")
    lane_count = len(lanes_el.findall("lane")) if lanes_el is not None else 0

    referrers = find_id_references(root, "links", params.no)
    for lane_index in range(1, lane_count + 1):
        referrers.extend(find_lane_token_references(root, params.no, lane_index))
    if referrers:
        raise ReferenceIntegrityError(f"Cannot remove link {params.no}", referrers)

    session.remove_link(no)
    session.persist()

    still_present = _still_present_after_remove(session.working_inpx, "links", params.no)
    verification = _remove_verification(params.no, still_present)
    return {
        "message": f"Link {params.no} removed ({lane_count} lane(s)).",
        "verification": verification,
    }


class AddConnectorParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    from_link: str
    #: Lane indices on from_link this connector draws FROM -- MUST be a
    #: contiguous ascending run (e.g. [1, 2], not [1, 3]): Vissim's own
    #: AddConnector primitive anchors at the FIRST lane and maps a
    #: consecutive count upward (vissim_com.py's own landmine 4), so a
    #: non-contiguous/crossing mapping has no primitive to express it with.
    from_lanes: list[int] = Field(min_length=1)
    from_pos_m: float
    to_link: str
    #: Same contiguous-ascending-run requirement as from_lanes; length must
    #: match from_lanes (Vissim connects lane-for-lane, one to one).
    to_lanes: list[int] = Field(min_length=1)
    to_pos_m: float
    name: str = ""
    #: Optional redundant cross-check against len(from_lanes) -- when given,
    #: must agree (defensive: an explicit mismatch is refused rather than
    #: silently trusting whichever of the two the caller meant).
    lane_count: int | None = None

    @model_validator(mode="after")
    def _validate_lane_mapping(self) -> "AddConnectorParams":
        if self.from_link == self.to_link:
            raise ValueError(
                "add_connector: from_link and to_link must differ -- a connector cannot join a "
                "link to itself."
            )
        for field_name, lanes in (("from_lanes", self.from_lanes), ("to_lanes", self.to_lanes)):
            ordered = sorted(lanes)
            if ordered != list(range(ordered[0], ordered[0] + len(ordered))):
                raise ValueError(
                    f"add_connector: {field_name}={lanes!r} must be a contiguous ascending run of "
                    "lane indices (e.g. [2, 3], not [2, 4]) -- Vissim's own AddConnector primitive "
                    "anchors at the first lane and maps a consecutive count upward; a non-"
                    "contiguous/crossing mapping is not expressible with it."
                )
        if len(self.from_lanes) != len(self.to_lanes):
            raise ValueError(
                "add_connector: from_lanes and to_lanes must have the same length -- Vissim "
                "connects lane-for-lane, one to one."
            )
        if self.lane_count is not None and self.lane_count != len(self.from_lanes):
            raise ValueError(
                f"add_connector: lane_count={self.lane_count} does not match "
                f"len(from_lanes)={len(self.from_lanes)}."
            )
        return self


@tool(name="add_connector", tier="write", provider="edit", params=AddConnectorParams)
def add_connector(params: AddConnectorParams) -> dict[str, Any]:
    """Add a simple connector from from_link's from_lanes at from_pos_m to to_link's to_lanes at to_pos_m (COM AddConnector; each lane list anchored at its own first index, mapped consecutively -- see this tool's own params model for the contiguous-ascending-run requirement) on the active edit session's working network. Straight-line guide geometry only -- Vissim snaps/extends the real saved shape to the resolved from/to positions regardless."""
    session = _require_session()
    from_link_no = _coerce_id(params.from_link, "from_link")
    to_link_no = _coerce_id(params.to_link, "to_link")
    from_lanes_sorted = sorted(params.from_lanes)
    to_lanes_sorted = sorted(params.to_lanes)
    result = session.add_connector(
        from_link_no, from_lanes_sorted, params.from_pos_m, to_link_no, to_lanes_sorted, params.to_pos_m,
        params.name,
    )
    session.persist()

    written_value = {
        "from_link": params.from_link,
        "from_lanes": from_lanes_sorted,
        "from_pos": _round_pos_m(params.from_pos_m),
        "to_link": params.to_link,
        "to_lanes": to_lanes_sorted,
        "to_pos": _round_pos_m(params.to_pos_m),
    }
    readback_value = {
        "from_link": result.get("from_link"),
        "from_lanes": result.get("from_lanes"),
        "from_pos": _round_pos_m(result["from_pos"]),
        "to_link": result.get("to_link"),
        "to_lanes": result.get("to_lanes"),
        "to_pos": _round_pos_m(result["to_pos"]),
    }
    verification = {"written_value": written_value, "readback_value": readback_value}
    message = (
        f"Connector {result['no']} added from link {params.from_link} lane(s) {from_lanes_sorted}@"
        f"{params.from_pos_m}m to link {params.to_link} lane(s) {to_lanes_sorted}@{params.to_pos_m}m."
    )
    return {
        "message": message,
        "verification": verification,
        "artifacts": {"no": result["no"], "name": result["name"]},
    }


# ---------------------------------------------------------------------------
# Q6 unit (1): link GEOMETRY edits on an EXISTING link -- set_link_lane_count,
# set_link_geometry, set_link_point_radius (TOOL-CATALOG v2b "Link geometry
# edits": lanes / length / curvature). Q4 unit (5) above could only create and
# remove WHOLE links; these three reshape one that already exists, which is
# what network CALIBRATION actually needs (an approach is one lane too narrow,
# a link's length is 20 m off the survey, a curve brakes traffic that should
# not brake).
#
# Every capability here was measured first by
# ``backend/tools/probe_link_geometry.py`` (ten parts, state file
# ``runtime/link-geometry-probe/state.json``, including a NULL CONTROL part
# proving an untouched load+save moves nothing at all) -- see
# ``services.vissim_com``'s own link-geometry module comment for the seven
# landmines. The three that shape THIS module's refusals:
#
# 1. **RemoveLane silently CASCADE-DELETES whatever is anchored on the lane**
#    (live-proven CONTROL/TREATMENT pair: a data-collection point and a signal
#    head created on Beijing link 4 lane 4 are simply GONE after one
#    RemoveLane -- no exception, and ``load_check`` still passes). So
#    ``set_link_lane_count`` runs the SAME reference-integrity preflight
#    ``remove_link``/``remove_connector`` already use
#    (``find_lane_token_references``, per lane about to disappear) and REFUSES
#    naming every referrer. This is the Q4 unit (4) lane-token registry doing
#    exactly the job it was built for -- and writing this unit found it did
#    not yet know about ``dataCollectionPoint@lane``, the very object the
#    probe watched get destroyed (see ``services.inpx_structure``'s own
#    LANE_TOKEN_REFERENCE_SITES comment for the corpus scan that closed that
#    gap and five more).
#
# 2. **Shortening a link silently RELOCATES everything anchored beyond the new
#    end** -- a data-collection point at 132.485 m came back at exactly the
#    new 82.803 m end, and shortening Beijing link 5 from 452 m to 226 m
#    clamped all three connectors anchored at its far end and STRETCHED their
#    own geometry to reach, turning an 11.71 m turn into a 237.70 m one. So
#    ``set_link_geometry`` refuses a shape whose polyline is shorter than the
#    furthest thing anchored on the link, naming each one and the minimum
#    length that would keep them all. (Preflight also has a ko/en/ja
#    ``link_geometry_truncates_anchored_objects`` refusal for the planner; the
#    check is deliberately implemented on BOTH sides -- the same
#    "mirror, don't share" convention constraints.py's ``_TOOL_TARGET_SPEC``
#    and guardrails.py's ``_WRITE_TARGET_FIELD_SPEC`` already follow -- because
#    constraints only run through the orchestrator, and a raw ``dispatch``
#    call must still fail closed.)
#
# 3. **COM cannot add or remove a polyline point** (``ILinkPolyPointContainer``
#    has no Add/Remove at all; there is no writable WKT attribute on a link).
#    ``set_link_geometry`` therefore requires exactly as many points as the
#    link already has, and says so -- with ``add_link`` named as the
#    alternative -- rather than silently applying a prefix.
#
# **Both geometry tools refuse a CONNECTOR target.** For lane count: a
# connector's every lane carries its own fromLanes/toLanes mapping, so a new
# lane would be created unmapped and a removed one would take a real mapping
# with it. For shape: Vissim SNAPS a connector's geometry to its resolved
# from/to positions regardless of what is written (Q4 unit (5) landmine 4,
# live-verified there) -- so a written connector shape is not what persists,
# which is precisely the "completed edit with wrong numbers" this layer
# exists to prevent. ``remove_connector`` + ``add_connector`` is the honest
# route, and both refusals say so.
#
# **What is deliberately NOT wrapped:** ``ILink.RecalculateSpline()`` /
# ``RecalculateSplineHeight()`` (they REDISTRIBUTE intermediate points and
# zero written z-offsets -- geometry mutations disguised as recomputes),
# ``ILink.SplitLink``/``InvertDirection``/``GenerateOppositeDirection``/
# ``DuplicateLink`` (real methods on the same interface, unprobed by this
# unit -- named here so a later unit knows they exist), per-point ZOffset
# writes (Q4 unit (5)'s recorded z follow-up stays open: writable and
# display-unit-scaled, but nothing in this codebase's shadow model can see a
# z-coordinate, so a write would be unverifiable by state diff), and any
# change to a link's POINT COUNT (landmine 4).
# ---------------------------------------------------------------------------


#: See set_link_geometry's own comment for why link COORDINATES round at a
#: different precision than every ``pos``-shaped value in this module.
_GEOMETRY_VERIFICATION_PRECISION = 10


def _round_xy_m(value: float) -> float:
    return round(float(value), _GEOMETRY_VERIFICATION_PRECISION)


def _link_element(root: ET.Element, link_id: str) -> ET.Element | None:
    return next((el for el in root.findall(".//links/link") if el.get("no") == str(link_id)), None)


def _link_is_connector(link_el: ET.Element) -> bool:
    return link_el.find("fromLinkEndPt") is not None or link_el.find("toLinkEndPt") is not None


def _link_points_xy(link_el: ET.Element) -> list[tuple[float, float]]:
    return [
        (float(p.get("x", 0) or 0), float(p.get("y", 0) or 0))
        for p in link_el.findall("geometry/linkPolyPts/linkPolyPoint")
    ]


def _polyline_length_m(points: list[tuple[float, float]]) -> float:
    return sum(
        math.hypot(points[i][0] - points[i - 1][0], points[i][1] - points[i - 1][1])
        for i in range(1, len(points))
    )


#: Every place in the .inpx where an object anchors itself at a POSITION along
#: a link -- the population a shortening silently relocates (landmine 5).
#: ``(label, xpath, link_attr, pos_attr, link_attr_is_lane_token)``; the lane
#: forms carry Vissim's ``"<link> <lane>"`` token, the link forms a plain
#: number. Deliberately mirrors -- rather than imports -- ``services.
#: inpx_structure``'s own reference registries: those answer "what still
#: REFERENCES this id" (removal safety), this answers "what sits at a position
#: that must stay inside the link" (a different question with a different
#: answer set: a queue counter anchors by plain link id, and a connector
#: endpoint is not a reference site at all).
_POSITION_ANCHOR_SITES: tuple[tuple[str, str, str, str, bool], ...] = (
    ("signal head", ".//signalHeads/signalHead", "lane", "pos", True),
    ("detector", ".//detectors/detector", "lane", "pos", True),
    ("data collection point", ".//dataCollectionPoints/dataCollectionPoint", "lane", "pos", True),
    ("stop sign", ".//stopSigns/stopSign", "lane", "pos", True),
    ("reduced speed area", ".//reducedSpeedAreas/reducedSpeedArea", "lane", "pos", True),
    ("PT stop", ".//ptStops/ptStop", "lane", "pos", True),
    ("priority rule", ".//priorityRules/priorityRule", "lane", "pos", True),
    ("priority rule conflict marker", ".//priorityRule/conflictMarker", "lane", "pos", True),
    ("queue counter", ".//queueCounters/queueCounter", "link", "pos", False),
)


def _position_anchors_on_link(root: ET.Element, link_id: str) -> list[dict[str, Any]]:
    """Every object anchored at a position along ``link_id``, with that
    position in metres (the .inpx is always raw metres). Connector endpoints
    are included: they anchor by ``pos`` exactly like the rest, and the probe
    proved they are clamped-and-stretched, not merely moved."""
    anchors: list[dict[str, Any]] = []
    for label, xpath, link_attr, pos_attr, is_lane_token in _POSITION_ANCHOR_SITES:
        for element in root.findall(xpath):
            raw = (element.get(link_attr) or "").strip()
            if not raw:
                continue
            owner = raw.split()[0] if is_lane_token else raw
            if owner != str(link_id):
                continue
            try:
                pos = float(element.get(pos_attr) or 0)
            except ValueError:
                continue
            anchors.append(
                {"kind": label, "no": element.get("no") or "", "pos_m": pos, "anchor": raw}
            )
    for connector in root.findall(".//links/link"):
        for side, tag in (("from", "fromLinkEndPt"), ("to", "toLinkEndPt")):
            endpoint = connector.find(tag)
            if endpoint is None or (endpoint.get("link") or "").strip() != str(link_id):
                continue
            try:
                pos = float(endpoint.get("pos") or 0)
            except ValueError:
                continue
            anchors.append(
                {
                    "kind": f"connector {side}-endpoint",
                    "no": connector.get("no") or "",
                    "pos_m": pos,
                    "anchor": f"{link_id} @ {pos:.3f} m",
                }
            )
    return anchors


class SetLinkLaneCountParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    link_id: str
    #: The link's TOTAL lane count after this edit (not a delta). ge=1 --
    #: a link with zero lanes is not a link (the same floor
    #: ``services.inpx_structure.remove_last_lane`` already enforces).
    lane_count: int = Field(ge=1)
    #: Width for lanes ADDED by this call, metres. Omitted -> the link's own
    #: current LAST lane width, so a widened link stays self-consistent
    #: instead of inheriting an unrelated 3.5 m default. Ignored when
    #: shrinking.
    lane_width_m: float | None = Field(default=None, gt=0)


@tool(name="set_link_lane_count", tier="write", provider="edit", params=SetLinkLaneCountParams)
def set_link_lane_count(params: SetLinkLaneCountParams) -> dict[str, Any]:
    """Set an EXISTING link's total lane count (COM AddLane/RemoveLane -- Vissim's NumLanes attribute is read-only). Lanes are added/removed at the HIGHEST index only. REFUSES, naming every referrer, when shrinking would delete a lane a signal head/detector/data-collection point/stop sign/reduced-speed area/priority rule/connector still uses -- Vissim's own RemoveLane silently CASCADE-DELETES those objects (live-proven). Refuses a connector target. Note: Vissim re-centers the link's polyline on every lane-count change, so the whole link silently moves sideways by half the added/removed lane width -- the result reports the measured shift."""
    session = _require_session()
    link_no = _coerce_id(params.link_id, "link_id")

    root = ET.parse(session.working_inpx).getroot()
    link_el = _link_element(root, params.link_id)
    if link_el is None:
        raise ValueError(f"set_link_lane_count: link {params.link_id} not found")
    if _link_is_connector(link_el):
        raise ValueError(
            f"set_link_lane_count: {params.link_id} is a CONNECTOR -- each connector lane carries "
            "its own from/to lane mapping, so an added lane would be unmapped and a removed one "
            "would take a real movement with it. Use remove_connector + add_connector."
        )

    lanes_el = link_el.find("lanes")
    current_count = len(lanes_el.findall("lane")) if lanes_el is not None else 0
    current_widths = [float(l.get("width") or 3.5) for l in (lanes_el.findall("lane") if lanes_el is not None else [])]
    points_before = _link_points_xy(link_el)

    referrers: list[dict[str, Any]] = []
    for lane_index in range(params.lane_count + 1, current_count + 1):
        referrers.extend(find_lane_token_references(root, params.link_id, lane_index))
    if referrers:
        # Kept SHORT on purpose: toolkit.dispatch caps a raised exception's
        # text at 300 characters, so the actionable half (which lanes, what
        # would be destroyed) has to come before any explanation or the
        # planner never sees it.
        raise ReferenceIntegrityError(
            f"set_link_lane_count: link {params.link_id} {current_count}->{params.lane_count} "
            f"lanes refused -- Vissim's RemoveLane would SILENTLY DELETE what is anchored on "
            f"lane(s) {list(range(params.lane_count + 1, current_count + 1))}",
            referrers,
        )

    result = session.set_link_lane_count(link_no, params.lane_count, params.lane_width_m)
    session.persist()

    points_after = [(p["x"], p["y"]) for p in result["points"]]
    shift_m = max(
        (math.hypot(a[0] - b[0], a[1] - b[1]) for b, a in zip(points_before, points_after)),
        default=0.0,
    )
    verification = {
        "written_value": params.lane_count,
        "readback_value": result["lanes"],
    }
    direction = "widened" if params.lane_count > current_count else (
        "narrowed" if params.lane_count < current_count else "unchanged"
    )
    message = (
        f"Link {params.link_id} {direction}: {current_count} -> {result['lanes']} lane(s) "
        f"(widths {[_round_pos_m(w) for w in current_widths]} -> "
        f"{[_round_pos_m(w) for w in result['lane_widths']]} m). Vissim re-centered the link's "
        f"polyline: every point moved {shift_m:.3f} m sideways. Length unchanged at "
        f"{result['length']:.3f} m."
    )
    return {
        "message": message,
        "verification": verification,
        "artifacts": {
            "no": str(result["no"]),
            "lanes": result["lanes"],
            "centerline_shift_m": _round_pos_m(shift_m),
        },
    }


class SetLinkGeometryParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    link_id: str
    #: The link's polyline points, RAW METRES (the network's own base
    #: coordinate space -- vissim_com's landmine 7: x/y are the ONE
    #: length-shaped COM value that is never display-unit-scaled, now
    #: confirmed in BOTH directions on a FEET network). MUST have exactly as
    #: many entries as the link already has: COM cannot add or remove a
    #: polyline point.
    points_xy: list[tuple[float, float]] = Field(min_length=2)

    @model_validator(mode="after")
    def _no_degenerate_geometry(self) -> "SetLinkGeometryParams":
        pts = self.points_xy
        for i in range(1, len(pts)):
            if pts[i] == pts[i - 1]:
                raise ValueError(
                    f"set_link_geometry: points_xy has duplicate consecutive points "
                    f"({pts[i - 1]!r} repeated at index {i}) -- a degenerate zero-length segment. "
                    "Vissim itself does NOT refuse this (live-verified for AddLink: a "
                    "duplicate-point geometry silently yields Length2D=0.0), so it is refused "
                    "here before COM is ever called."
                )
        if _polyline_length_m([(float(x), float(y)) for x, y in pts]) <= 0:
            raise ValueError("set_link_geometry: points_xy describes a zero-length link -- refused.")
        return self


@tool(name="set_link_geometry", tier="write", provider="edit", params=SetLinkGeometryParams)
def set_link_geometry(params: SetLinkGeometryParams) -> dict[str, Any]:
    """Move an EXISTING link's polyline points to points_xy (raw metres, straight segments between consecutive points), changing its shape and therefore its length -- Vissim recomputes Length2D from the polyline. points_xy MUST have exactly as many points as the link already has: COM cannot add or remove a polyline point (use add_link/remove_link to change the point count). REFUSES a shortening that would move anything anchored past the new end -- Vissim silently clamps such objects TO the new end and stretches attached connectors to reach them (live-proven: an 11.7 m turn became a 237.7 m one). Refuses a connector target (Vissim overwrites a connector's written shape by snapping it to its endpoints)."""
    session = _require_session()
    link_no = _coerce_id(params.link_id, "link_id")
    points_xy = [(float(x), float(y)) for x, y in params.points_xy]

    root = ET.parse(session.working_inpx).getroot()
    link_el = _link_element(root, params.link_id)
    if link_el is None:
        raise ValueError(f"set_link_geometry: link {params.link_id} not found")
    if _link_is_connector(link_el):
        raise ValueError(
            f"set_link_geometry: {params.link_id} is a CONNECTOR -- Vissim SNAPS a connector's "
            "geometry to its resolved endpoints regardless of what is written (measured), so the "
            "shape written here is not the shape that persists. Use remove_connector + "
            "add_connector."
        )

    points_before = _link_points_xy(link_el)
    if len(points_before) != len(points_xy):
        raise ValueError(
            f"set_link_geometry: link {params.link_id} has {len(points_before)} polyline "
            f"point(s), not {len(points_xy)} -- COM cannot add or remove a link's polyline point "
            "(no Add/Remove on the point container, no writable WKT attribute). Supply exactly "
            f"{len(points_before)}, or build the new shape with add_link + remove_link."
        )

    new_length = _polyline_length_m(points_xy)
    anchors = _position_anchors_on_link(root, params.link_id)
    truncated = [a for a in anchors if a["pos_m"] > new_length + 1e-6]
    if truncated:
        # Message order and length are deliberate: toolkit.dispatch caps a
        # raised exception's text at 300 characters, so the minimum safe
        # length and the named objects come FIRST -- an explanation-first
        # message would be truncated exactly where the actionable part starts.
        worst = max(a["pos_m"] for a in truncated)
        shown = sorted(truncated, key=lambda a: -a["pos_m"])[:3]
        named = "; ".join(f"{a['kind']} {a['no']} @ {a['pos_m']:.1f}m" for a in shown)
        more = f" (+{len(truncated) - len(shown)} more)" if len(truncated) > len(shown) else ""
        raise ValueError(
            f"set_link_geometry: refusing to shorten link {params.link_id} to {new_length:.3f} m "
            f"-- keep it at least {worst:.3f} m. {len(truncated)} object(s) sit beyond the new "
            f"end and Vissim would SILENTLY relocate each one to it: {named}{more}."
        )

    result = session.set_link_geometry(link_no, points_xy)
    session.persist()

    # Rounded to _GEOMETRY_VERIFICATION_PRECISION, NOT to _POS_VERIFICATION_
    # PRECISION like every other pos-shaped verification in this module -- and
    # the difference is load-bearing in both directions. Constraint #6
    # (write_verification_failed) compares written vs readback with a
    # NON-recursive helper: a list compares with a bare ``!=``, so any bit of
    # float noise refuses a correct write, which is what the coarse rounding
    # exists to absorb. But guardrails._check_one_target_persisted compares
    # this SAME written_value against the FULL-PRECISION points inpx_parser
    # reads back out of the saved file, with a 1e-9 tolerance -- so rounding
    # at 1e-6 (what _round_pos_m does) would itself exceed that tolerance and
    # refuse the same correct write from the other side. 1e-10 satisfies both:
    # ~20x inside the persistence tolerance, and far coarser than Vissim's own
    # ~5.7e-14 re-serialization drift. x/y need no unit-conversion de-noising
    # at all here (landmine 7: they are the one length-shaped COM value never
    # display-unit-scaled), so this rounding is purely about those two
    # comparison semantics meeting in the middle.
    written_points = [[_round_xy_m(x), _round_xy_m(y)] for x, y in points_xy]
    readback_points = [[_round_xy_m(p["x"]), _round_xy_m(p["y"])] for p in result["points"]]
    verification = {"written_value": written_points, "readback_value": readback_points}
    length_before = _polyline_length_m(points_before)
    message = (
        f"Link {params.link_id} reshaped: {len(points_xy)} point(s) moved, length "
        f"{length_before:.3f} m -> {result['length']:.3f} m. "
        + (
            f"{len(anchors)} object(s) anchored on this link stay inside it (furthest at "
            f"{max(a['pos_m'] for a in anchors):.3f} m)."
            if anchors
            else "Nothing is anchored on this link."
        )
    )
    return {
        "message": message,
        "verification": verification,
        "artifacts": {
            "no": str(result["no"]),
            "length_m": _round_pos_m(result["length"]),
            "point_count": result["point_count"],
        },
    }


class SetLinkPointRadiusParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    link_id: str
    #: 1-based polyline point index. Must be INTERIOR (2..N-1): a polyline's
    #: end points carry no curve radius at all (measured -- Rad reads None
    #: on every endpoint probed).
    point_index: int = Field(ge=2)
    #: Curve radius in metres, or omitted/null to hand the radius back to
    #: Vissim's own polyline-derived computation (RadiusSrc=CALCULATED).
    radius_m: float | None = Field(default=None, gt=0)


@tool(name="set_link_point_radius", tier="write", provider="edit", params=SetLinkPointRadiusParams)
def set_link_point_radius(params: SetLinkPointRadiusParams) -> dict[str, Any]:
    """Override the curve radius Vissim uses at one INTERIOR polyline point of a link (COM RadiusSrc=USERINPUT then Rad -- Vissim refuses the value write outright while the source is CALCULATED), or omit radius_m to restore Vissim's own polyline-derived radius. This changes only the radius the curve-speed model reads; it does NOT bend the link (the geometry is the polyline -- use set_link_geometry for that), so an override that disagrees with the drawn shape is a real modelling claim the caller owns."""
    session = _require_session()
    link_no = _coerce_id(params.link_id, "link_id")

    root = ET.parse(session.working_inpx).getroot()
    link_el = _link_element(root, params.link_id)
    if link_el is None:
        raise ValueError(f"set_link_point_radius: link {params.link_id} not found")
    point_els = link_el.findall("geometry/linkPolyPts/linkPolyPoint")
    if params.point_index >= len(point_els):
        raise ValueError(
            f"set_link_point_radius: link {params.link_id} has {len(point_els)} polyline "
            f"point(s), so its INTERIOR points are 2..{len(point_els) - 1} -- point "
            f"{params.point_index} is an end point or past the end, and a polyline's end points "
            "carry no curve radius (measured: Rad reads None there)."
        )

    result = session.set_link_point_radius(link_no, params.point_index, params.radius_m)
    session.persist()

    # The shadow model carries no per-point curvature at all (links[].points
    # is x/y only), so guardrails' own postwrite diff and target-persisted
    # check are both structurally BLIND to this write -- documented, not
    # assumed. The verification below is therefore a genuine reparse of the
    # JUST-PERSISTED working copy (the same "read the file, not the return
    # value" discipline _still_present_after_remove uses), not a COM echo.
    persisted = ET.parse(session.working_inpx).getroot()
    persisted_link = _link_element(persisted, params.link_id)
    persisted_points = (
        persisted_link.findall("geometry/linkPolyPts/linkPolyPoint")
        if persisted_link is not None
        else []
    )
    persisted_point = (
        persisted_points[params.point_index - 1] if params.point_index <= len(persisted_points) else None
    )
    saved_src = (persisted_point.get("radiusSrc") if persisted_point is not None else None) or None
    saved_rad = persisted_point.get("rad") if persisted_point is not None else None
    written_value = {
        "radius_src": "CALCULATED" if params.radius_m is None else "USERINPUT",
        "radius_m": None if params.radius_m is None else _round_pos_m(params.radius_m),
    }
    readback_value = {
        "radius_src": saved_src,
        # A CALCULATED point's radius is Vissim's own recomputation, which is
        # deliberately NOT claimed as matching anything the caller wrote.
        "radius_m": (
            None
            if params.radius_m is None
            else (_round_pos_m(float(saved_rad)) if saved_rad not in (None, "") else None)
        ),
    }
    verification = {"written_value": written_value, "readback_value": readback_value}

    if params.radius_m is None:
        message = (
            f"Link {params.link_id} point {params.point_index}: curve radius handed back to "
            f"Vissim's own polyline-derived computation (radiusSrc=CALCULATED). Vissim now "
            f"reports {result['points'][params.point_index - 1]['radius']} m for it."
        )
    else:
        message = (
            f"Link {params.link_id} point {params.point_index}: curve radius overridden to "
            f"{params.radius_m:g} m (radiusSrc=USERINPUT). The link's shape and its "
            f"{result['length']:.3f} m length are unchanged -- only the radius the curve-speed "
            f"model reads."
        )
    return {
        "message": message,
        "verification": verification,
        "artifacts": {"no": str(result["no"]), "point_index": params.point_index},
    }


# ---------------------------------------------------------------------------
# add_parking_lot(link_no, lane_index, pos_m, capacity, len_per_space_m=None,
#                 name="") / set_parking_lot_capacity(parking_lot_no,
#                 capacity, len_per_space_m=None) / remove_parking_lot(no)
#
# Q6 unit (2). See ``vissim_com.VissimController``'s own parking-lot module
# comment for the full live-verified writeup and
# ``backend/tools/probe_parking_lot.py``'s docstring for every number. The two
# facts that shape THIS layer:
#
# * **``capacity`` is a DERIVED read-only attribute.** Vissim refuses
#   ``SetAttValue("Capacity", n)`` outright and computes it as
#   ``floor(Length / LenPerSpc)``. Both verbs below therefore take the
#   planner's own vocabulary ("a 20-space lot") and realise it as a LENGTH
#   write, then verify against Vissim's own readback of the derived count --
#   never against the number the caller asked for. That is the whole reason
#   ``written_value``/``readback_value`` compare ``capacity`` at all: if the
#   derivation ever disagreed with the request (a clamp, a rounding, a
#   Vissim version change), constraint #6 refuses and rolls back rather than
#   reporting a lot that is not the size the caller asked for.
#
# * **``remove_parking_lot`` must check reference integrity ITSELF.** COM's
#   own ``RemoveParkingLot`` silently CASCADE-DELETES the
#   ``vehicleRouteParking`` that referenced the lot (measured: Karlsruhe
#   ``{'parking_lots': -1, 'parking_routes': -1}``, no exception,
#   ``load_check`` green) -- the ``remove_pt_stop`` species. The preflight
#   below runs against the CURRENT working-copy XML, before COM is ever
#   asked, via ``services.inpx_structure.ID_REFERENCE_SITES["parking_lots"]``.
#
# The link-fit refusal lives in constraints.py
# (``parking_lot_does_not_fit_link``) rather than inline here, because it is
# a genuine semantic screen with ko/en/ja text and a numeric suggested fix --
# and because our rule is deliberately CONSERVATIVE (refuse when
# ``pos + capacity x len_per_space`` exceeds the link's own length), i.e. at
# least as strict as Vissim's own loud "Parking Lot 2 does not fit on link 1
# (335.122 m)". Over-refusal on this edge is fail-safe; a silently clamped
# lot is not.
# ---------------------------------------------------------------------------


class AddParkingLotParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    link_no: str
    lane_index: int = Field(ge=1)
    pos_m: float
    #: Space count. REQUIRED, and ge=1: a fresh lot's own Vissim default is
    #: ONE space (live-measured), so a lot created and left alone is a
    #: single-bay lot rather than the multi-space one a caller almost always
    #: means -- the same "a created object's default makes it useless"
    #: reasoning that made ``add_vehicle_input``'s ``volume_vph`` required.
    capacity: int = Field(ge=1)
    #: Metres of link per parking space. Omitted -> Vissim's own default
    #: (6.0 display units on a fresh lot). Given, it is written together with
    #: the length so the two can never disagree.
    len_per_space_m: float | None = Field(default=None, gt=0)
    name: str = ""


@tool(name="add_parking_lot", tier="write", provider="edit", params=AddParkingLotParams)
def add_parking_lot(params: AddParkingLotParams) -> dict[str, Any]:
    """Add a parking lot on one lane of link_no at pos_m metres holding `capacity` vehicles (COM AddParkingLot) on the active edit session's working network. Vissim's Capacity attribute is READ-ONLY and derived as floor(length / length-per-space), so the requested space count is realised by writing the lot's LENGTH and verified against Vissim's own capacity readback. A fresh lot defaults to ONE space, so capacity is required. NOTE: a parking lot on its own attracts no traffic -- it is reachable only through a parking routing decision (vehicleRoutingDecisionParking), which this toolkit does not create."""
    session = _require_session()
    link_no = _coerce_id(params.link_no, "link_no")
    # Canonical id in the CLAIM, not the caller's raw spelling -- COM reads
    # "1" back for a "01"/" 1 " argument, so an uncanonicalised written_value
    # makes constraint #6 refuse a correct write on a formatting difference.
    # Same normalisation discipline remove_parking_lot's referrer guard needs
    # (see its own comment for the measured bypass that motivated it).
    canonical_link = str(link_no)

    result = session.add_parking_lot(
        link_no,
        params.lane_index,
        params.pos_m,
        params.capacity,
        params.name,
        params.len_per_space_m,
    )
    session.persist()

    written_value: dict[str, Any] = {
        "link": canonical_link,
        "lane_index": params.lane_index,
        "pos": _round_pos_m(params.pos_m),
        "capacity": params.capacity,
    }
    readback_value: dict[str, Any] = {
        "link": result["link"],
        "lane_index": result["lane_index"],
        "pos": _round_pos_m(result["pos"]),
        "capacity": result["capacity"],
    }
    # len_per_space is only PART OF THE CLAIM when the caller actually asked
    # for it -- mirrors add_pt_stop's own length_m / add_stop_sign's own
    # sc_no precedent: an omitted field (Vissim's own default silently
    # applies) must never become an unverifiable "the caller asked for this
    # exact value" claim. The same rule is why `des_speed_distr_no` is
    # reported in artifacts but never verified: Vissim picks it itself (the
    # network's lowest speed-distribution id, live-measured), and it is not
    # the caller's choice.
    if params.len_per_space_m is not None:
        written_value["len_per_space"] = _round_pos_m(params.len_per_space_m)
        readback_value["len_per_space"] = _round_pos_m(result["len_per_space"])
    verification = {"written_value": written_value, "readback_value": readback_value}
    message = (
        f"Parking lot {result['no']} added on link {canonical_link} lane {params.lane_index} at "
        f"{params.pos_m} m: {result['capacity']} space(s) over {result['length']:.3f} m "
        f"({result['len_per_space']:.3f} m per space). Vissim assigned desired-speed "
        f"distribution {result['des_speed_distr_no']} by default. The lot attracts no traffic "
        f"until a parking routing decision references it."
    )
    return {
        "message": message,
        "verification": verification,
        "artifacts": {
            "no": result["no"],
            "name": result["name"],
            "capacity": result["capacity"],
            "length": result["length"],
            "len_per_space": result["len_per_space"],
            "des_speed_distr_no": result["des_speed_distr_no"],
        },
    }


class SetParkingLotCapacityParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    parking_lot_no: str
    capacity: int = Field(ge=1)
    len_per_space_m: float | None = Field(default=None, gt=0)


@tool(
    name="set_parking_lot_capacity",
    tier="write",
    provider="edit",
    params=SetParkingLotCapacityParams,
)
def set_parking_lot_capacity(params: SetParkingLotCapacityParams) -> dict[str, Any]:
    """Change an existing parking lot's space count on the active edit session's working network. Vissim's Capacity attribute is READ-ONLY (SetAttValue fails loudly) and derived as floor(length / length-per-space), so this writes the lot's LENGTH and verifies against Vissim's own capacity readback. Optionally also sets the metres-per-space. Refuses when the resulting length would not fit on the lot's own link."""
    session = _require_session()
    parking_lot_no = _coerce_id(params.parking_lot_no, "parking_lot_no")
    canonical_no = str(parking_lot_no)

    result = session.set_parking_lot_capacity(
        parking_lot_no, params.capacity, params.len_per_space_m
    )
    session.persist()

    # BARE SCALARS, not a dict -- and that is a live-caught correction, not a
    # style choice. This tool HAS a _WRITE_TARGET_FIELD_SPEC entry, so
    # guardrails' target-persisted check compares written_value against the
    # shadow model's own ``capacity`` field, which is an int; a
    # ``{"capacity": 25}`` dict can never equal ``25``, so every CORRECT call
    # was refused by write_verification_failed until this was fixed
    # (reproduced by backend/tools/parking_lot_live_check.py Phase B.2 on a
    # real Beijing clone, before the fix). Same shape as set_link_lane_count's
    # own bare ``params.lane_count`` / ``result["lanes"]`` pair.
    #
    # ``len_per_space_m`` is deliberately NOT a second verified field: it has
    # no home in a single-value spec, and it does not need one, because
    # capacity is a FUNCTION of it. Vissim derives capacity as
    # floor(length / len_per_space) and this tool writes
    # length = capacity x requested_len_per_space -- so a bay size that landed
    # anywhere other than requested (the measured SILENT CLAMP is the real
    # case) necessarily produces a capacity readback that disagrees with the
    # request, and this scalar compare refuses. The bay size is transitively
    # verified rather than unverified; it is still reported in the message and
    # artifacts so the caller sees what actually landed.
    verification = {"written_value": params.capacity, "readback_value": result["capacity"]}
    message = (
        f"Parking lot {canonical_no} now holds {result['capacity']} space(s) over "
        f"{result['length']:.3f} m ({result['len_per_space']:.3f} m per space)."
    )
    return {
        "message": message,
        "verification": verification,
        "artifacts": {
            "no": result["no"],
            "capacity": result["capacity"],
            "length": result["length"],
            "len_per_space": result["len_per_space"],
        },
    }


class RemoveParkingLotParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    no: str


@tool(name="remove_parking_lot", tier="write", provider="edit", params=RemoveParkingLotParams)
def remove_parking_lot(params: RemoveParkingLotParams) -> dict[str, Any]:
    """Remove a parking lot (COM RemoveParkingLot) on the active edit session's working network -- REFUSES (never touches the file) when a parking route still references this lot: Vissim's own removal does NOT dangle the reference, it SILENTLY CASCADE-DELETES the referencing parking route outright (live-verified on the only example network that has both), so naming every referrer here is the only guard against a routing decision quietly losing a destination."""
    session = _require_session()
    no = _coerce_id(params.no, "no")

    # COM itself does NOT refuse this -- it silently cascade-deletes the
    # referencing vehicleRouteParking (the remove_pt_stop species, worse than
    # remove_data_collection_point's dangling-reference one). Check reference
    # integrity ourselves, against the CURRENT working copy XML, via the
    # spine's registry, BEFORE ever calling COM.
    #
    # **The CANONICAL id goes to the guard, not the caller's raw string.**
    # find_id_references compares ``value == str(object_id)`` (inpx_structure),
    # so passing ``params.no`` verbatim made "0014", " 14", "+14" and "14 "
    # all MISS the referrer lookup, sail past this refusal, reach COM, and
    # trigger exactly the silent cascade-delete this guard exists to stop
    # (verifier-measured). _coerce_id already normalised the value on the line
    # above for the ACTION; the GUARD has to see the same value or the two
    # disagree about which object is being talked about. In the orchestrated
    # pipeline preflight's object_not_found rejects those spellings first, so
    # this is defense-in-depth -- but a direct dispatch has no such shield.
    canonical_no = str(no)
    root = ET.parse(session.working_inpx).getroot()
    referrers = find_id_references(root, "parking_lots", canonical_no)
    if referrers:
        raise ReferenceIntegrityError(f"Cannot remove parking lot {canonical_no}", referrers)

    session.remove_parking_lot(no)
    session.persist()

    still_present = _still_present_after_remove(session.working_inpx, "parking_lots", canonical_no)
    verification = _remove_verification(canonical_no, still_present)
    return {
        "message": f"Parking lot {canonical_no} removed.",
        "verification": verification,
    }


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


# D3 live-demo fix (docs/phase2/DECISIONS.md, 2026-07-31): rollback_last_edit's
# own `message` is the ONE field guaranteed to survive planner.py's
# history-rendering truncation (see that module's `_render_observation` and
# its message-preserving fallback tier) -- but only if the tool actually
# keeps it bounded. The trace-proven bug: `message` used to inline EVERY diff
# entry's FULL description, and a field_changed entry on a list/dict-valued
# field (e.g. a desired-speed distribution's `points` curve) embedded the
# RAW repr() of both the before AND after value -- unbounded in the number of
# points. Rolling back a large-enough edit therefore produced a `message`
# itself too big to fit even the generous per-iteration budget, so rendering
# fell through to the generic "too large to display" marker and the model
# genuinely could not tell what had happened (it re-requested the very write
# it had just successfully undone). Two independent, cheap caps fix this:
# `_compact_diff_value` never inlines a full list/dict, only its shape, and
# `_MAX_DIFF_ENTRIES_IN_MESSAGE`/`_cap_message` below bound how much of the
# diff the message inlines at all. The FULL, uncapped diff always still lives
# in `verification.reverted_targets` (what postwrite's dynamic exemption
# actually reads) -- nothing here changes what is RECORDED, only what the
# human/model-readable summary string inlines.
_DIFF_VALUE_REPR_MAX_CHARS = 60


def _compact_diff_value(value: Any) -> str:
    """A bounded text rendering of one diff entry's before/after value --
    never the raw, unbounded ``repr()`` of a list/dict. A list/dict is
    summarized by its item/key count only; scalars are ``repr()``'d but
    still capped, as defense in depth against a pathologically long string
    value."""
    if isinstance(value, list):
        return f"[{len(value)} item(s)]"
    if isinstance(value, dict):
        return f"{{{len(value)} field(s)}}"
    text = repr(value)
    if len(text) > _DIFF_VALUE_REPR_MAX_CHARS:
        text = text[: _DIFF_VALUE_REPR_MAX_CHARS - 1] + "…"
    return text


def _describe_diff_entry(entry: dict[str, Any]) -> str:
    label = f"{entry['object_class']}#{entry['object_id']}"
    if entry["change"] == "field_changed":
        # entry["before"] is the CURRENT (pre-rollback, about-to-be-undone)
        # value; entry["after"] is the RESTORED (post-rollback, target) value
        # -- diff_shadow_models(current_model, restored_model) puts current
        # in the "before" slot, so this reads naturally as new -> old.
        return (
            f"{label}.{entry['field']}: {_compact_diff_value(entry['before'])} → "
            f"{_compact_diff_value(entry['after'])}"
        )
    if entry["change"] == "removed":
        return f"{label} (removed -- undoes an object the edit had added)"
    return f"{label} (re-added -- restores an object the edit had removed)"  # change == "added"


#: How many diff-entry descriptions ``rollback_last_edit``'s own message
#: inlines directly, largest-first-by-diff-order -- everything beyond this is
#: summarized as a bare count with a pointer to the structured field that
#: still carries it all. Keeps the message's own length independent of how
#: many objects/fields a single undone edit happened to touch (a batch's
#: rollback, in particular, can touch many).
_MAX_DIFF_ENTRIES_IN_MESSAGE = 2

#: Hard backstop on the WHOLE message string, regardless of how it was built
#: -- keeps it comfortably inside planner.py's OLDER-entry compact-history
#: budget (``_OBSERVATION_SNIPPET_CHARS`` = 300) alongside the envelope's
#: other fixed keys, even in a shape neither cap above anticipated. Shared by
#: ``rollback_last_edit`` and ``apply_edit_plan`` below -- both tools' own
#: "what happened" story needs the same guarantee.
_MESSAGE_MAX_CHARS = 240


def _cap_message(text: str, max_chars: int = _MESSAGE_MAX_CHARS) -> str:
    """Truncate ``text`` to ``max_chars``, ellipsis-marked -- the final,
    unconditional backstop under the smarter per-field caps above."""
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


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
            # D3 fix: inline at most _MAX_DIFF_ENTRIES_IN_MESSAGE entries --
            # the rest is a bare count pointing at the field that still
            # carries all of them (see the module comment above
            # _describe_diff_entry).
            shown = target_descriptions[:_MAX_DIFF_ENTRIES_IN_MESSAGE]
            extra = len(target_descriptions) - len(shown)
            summary_text = "; ".join(shown)
            if extra > 0:
                summary_text += f"; +{extra} more (see verification.reverted_targets)"
            undo_summary += f" ({summary_text})"
    else:
        undo_summary = "the most recently applied edit (its own history record is unavailable)"

    message = (
        f"Rolled back to version {plan.target_version} (undid version {plan.reverted_from_version}: "
        f"{undo_summary}). Restore verified byte-for-byte identical."
    )
    if plan.run_since_edit:
        message += (
            " NOTE: a simulation was run after that edit -- its results no longer correspond to "
            "the model now that it has been undone."
        )
    # D3 fix: the hard backstop applies LAST, after every optional note has
    # been appended -- see _cap_message's own comment.
    message = _cap_message(message)

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

#: D3 live-demo fix companion (see the comment above rollback_last_edit's
#: _MAX_DIFF_ENTRIES_IN_MESSAGE): how many per-step "applied, readback ok"
#: one-liners apply_edit_plan's own message inlines directly -- a plan can
#: have up to batch.MAX_STEPS (10) steps, so without a cap the message's own
#: length would scale with plan size instead of staying the compact,
#: guaranteed-visible outcome summary planner.py's history-rendering
#: truncation is built to preserve. The FULL per-step ledger always lives in
#: `artifacts.steps` regardless of what the message inlines.
_MAX_STEP_LINES_IN_MESSAGE = 3


def _build_apply_edit_plan_message(applied: list[dict[str, Any]], total_steps: int) -> str:
    """The compact, self-sufficient outcome summary for apply_edit_plan's
    `message` -- one line per applied step (tool + "readback ok"), capped,
    plus which version this batch was saved as (the orchestrator's generic
    post-dispatch save_version() call, unconditional for any "ok" write
    envelope -- see this tool's own module comment -- fires immediately
    after this function returns, so stating it here is accurate by the time
    any caller ever reads this message)."""
    lines = [f"{a['index']}. {a['tool']}: applied, readback ok" for a in applied]
    shown = lines[:_MAX_STEP_LINES_IN_MESSAGE]
    text = "; ".join(shown)
    extra = len(lines) - len(shown)
    if extra > 0:
        text += f"; +{extra} more (see artifacts.steps)"
    return _cap_message(
        f"Applied all {total_steps} step(s) of the edit plan ({text}). Saved as one new version."
    )


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
    message = _build_apply_edit_plan_message(applied, total_steps)
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
