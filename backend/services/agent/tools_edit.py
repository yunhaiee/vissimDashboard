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

Param names for the three object-targeting tools match
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

**Deferred:** ``set_desired_speed_area`` (the roadmap's sixth M2.3 verb) is
NOT implemented here -- controller-decided deferral, see the M2.3 report's
drafted DECISIONS.md entry. It is a separate, non-COM (XML-injection) write
path deserving its own verification pass, and the milestone's acceptance
criteria do not require it.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from services.agent.edit_session import get_active_edit_session
from services.agent.toolkit import tool


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


@tool(
    name="set_vehicle_input_volume",
    tier="write",
    provider="edit",
    params=SetVehicleInputVolumeParams,
)
def set_vehicle_input_volume(params: SetVehicleInputVolumeParams) -> dict[str, Any]:
    """Set a vehicle input's volume (veh/h) on the active edit session's working network."""
    session = _require_session()
    input_no = _coerce_id(params.input_no, "input_no")
    session.set_vehicle_input_volume(input_no, params.volume)
    readback = session.get_vehicle_input_volume(input_no)
    session.persist()
    return {
        "message": f"Vehicle input {params.input_no} volume set to {params.volume} veh/h.",
        "verification": {"written_value": params.volume, "readback_value": readback},
    }


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
