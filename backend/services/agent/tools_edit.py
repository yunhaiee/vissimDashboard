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

Param names for the four object-targeting tools (vehicle input, signal cycle,
signal offset, conflict area status) match
``guardrails._WRITE_TARGET_FIELD_SPEC`` EXACTLY (tool name AND field names)
-- that table is what lets the postwrite "nothing else moved" diff exempt
each tool's own intended target field; drifting a name here silently
reintroduces false-positive diff evidence on every write. ``input_no``/
``sc_no`` are typed ``str`` here (matching the shadow model's own string id
fields and ``constraints.py``'s ``_TOOL_TARGET_SPEC``/``guardrails.py``'s
``_WRITE_TARGET_FIELD_SPEC`` string-keyed lookups) and converted to ``int``
only at the COM boundary, inside each tool function.

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

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from services.agent.edit_session import get_active_edit_session
from services.agent.toolkit import tool
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
