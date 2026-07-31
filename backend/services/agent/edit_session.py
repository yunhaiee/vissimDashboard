"""VissimEditSession: one live COM session for one agent turn's writes (M2.3).

Owns exactly one :class:`~services.vissim_com.VissimController`, connected
``attach_to_running=False`` (00-EXECUTION-GUIDE.md rule 3 -- automation must
never grab a human's open GUI) and loaded against the session's
``ArtifactManager.working_path`` -- never the source dataset (rule 4). This
is a thin, session-scoped wrapper: it exposes the M2.1 paired set/get verbs
plus :meth:`persist`, and owns the COM apartment lifecycle
(``CoInitialize``/``CoUninitialize``) for whichever thread constructs and
uses it.

**Threading contract.** A COM apartment is bound to the OS thread that calls
``CoInitialize`` -- every method on this class (including :meth:`close`)
MUST be called from that same thread. In this codebase that is the agent
turn's dedicated worker thread (``routers/assistant.py`` already runs
``run_agent_turn`` off the request-handling thread via ``threading.Thread``
for the streaming bridge); this class does not itself spawn a thread, it
just requires the caller not to hop threads mid-session. See the drafted
DECISIONS.md entry in the M2.3 report for why this satisfies rule 6 ("all
COM work goes through the job queue, never a request handler") even though
no job-queue submission happens here.

**Persistence.** :meth:`persist` is ``save_net_as(tmp)`` then
``os.replace(tmp, working_inpx)`` -- the sanctioned two-step route once
M2.1's ``save_net_as`` guard forbids saving directly onto the loaded path
(docs/phase2/DECISIONS.md, "save_net_as actively refuses to overwrite the
loaded source"). Verified empirically against live Vissim 2026 by
``backend/tools/edit_live_probe.py`` BEFORE this module was written: a
live session tolerates its own loaded file being replaced out from under it
via ``os.replace`` and can set + persist a second time in the same session.

**Concurrency cap.** A module-level semaphore caps live sessions at
:data:`MAX_CONCURRENT_EDIT_SESSIONS` (Vissim allows ~4 instances per
machine total; M2.4's simulation runs need instances too, so edits get a
fraction, not all of them). Acquired lazily on first COM use (not at
construction), released in :meth:`close`.

**Active-session context.** :func:`set_active_edit_session` /
:func:`get_active_edit_session` / :func:`clear_active_edit_session` mirror
``tools_network.set_active_inpx``'s documented module-global pattern
(docs/phase2/02-ROADMAP.md M1.2): a single process-wide "current session"
slot that ``tools_edit.py``'s tool functions read. Same acceptable
limitation noted there -- this is not safe for multiple concurrent agent
turns each running their OWN write in the SAME process; today's endpoint
wiring only ever has one active edit session at a time per the propose ->
approve flow's single-active-turn shape, and the M1.2 module-global gap is
already tracked in docs/phase2/DECISIONS.md ("Future multi-dataset work")
as a pre-existing limitation this module inherits rather than introduces.
"""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import Any

from services.vissim_com import VissimController

logger = logging.getLogger(__name__)

#: Vissim allows ~4 instances per machine total (00-EXECUTION-GUIDE.md
#: SS1.6); M2.4's simulation runs need instances too, so edit sessions get a
#: deliberately conservative slice rather than the whole budget.
MAX_CONCURRENT_EDIT_SESSIONS = 2

#: How long a caller will wait for a free session slot before giving up.
#: Edit sessions are meant to be short-lived (one turn's worth of writes,
#: then closed), so a bounded wait here is a real backpressure signal, not
#: an indefinite hang -- a caller stuck past this long almost certainly
#: means a leaked (never-closed) session elsewhere.
_SEMAPHORE_ACQUIRE_TIMEOUT_S = 90.0

_session_semaphore = threading.Semaphore(MAX_CONCURRENT_EDIT_SESSIONS)


class VissimEditSession:
    """One live COM session for one agent turn's writes.

    Construct with the session's working-copy .inpx path (an
    ``ArtifactManager.working_path`` that already exists on disk --
    ``clone()`` must have already run). The COM connection is established
    lazily on first verb call (``CoInitialize`` + launch + ``LoadNet``), not
    at construction, so building a session that never ends up writing
    anything (e.g. the plan is declined) never launches Vissim at all.

    Always call :meth:`close` when done (a ``finally`` block at the call
    site) -- it disconnects the COM session, releases the concurrency-cap
    semaphore slot if one was acquired, and uninitializes the COM apartment.
    Safe to call even if the session never actually connected.
    """

    def __init__(self, working_inpx: Path | str) -> None:
        self.working_inpx = Path(working_inpx)
        self._controller: VissimController | None = None
        self._com_initialized = False
        self._semaphore_acquired = False

    # -----------------------------------------------------------------
    # Connection lifecycle
    # -----------------------------------------------------------------

    def _acquire_semaphore_slot(self) -> None:
        acquired = _session_semaphore.acquire(timeout=_SEMAPHORE_ACQUIRE_TIMEOUT_S)
        if not acquired:
            raise RuntimeError(
                f"Could not start a Vissim edit session: {MAX_CONCURRENT_EDIT_SESSIONS} "
                "concurrent edit session(s) are already active on this machine. Try again "
                "shortly."
            )
        self._semaphore_acquired = True

    def _ensure_connected(self) -> VissimController:
        if self._controller is not None:
            return self._controller

        if not self.working_inpx.exists():
            raise FileNotFoundError(
                f"VissimEditSession: working copy does not exist at {self.working_inpx!s}. "
                "ArtifactManager.clone() must run before a write turn starts."
            )

        self._acquire_semaphore_slot()
        try:
            import pythoncom

            pythoncom.CoInitialize()
            self._com_initialized = True

            controller = VissimController()
            # attach_to_running=False per 00-EXECUTION-GUIDE.md rule 3: never
            # grab a human's open GUI session. visible=False -- this is
            # automation, not an interactive window for a person to watch.
            controller.connect(visible=False, launch_if_missing=True, attach_to_running=False)
            controller.load_network(str(self.working_inpx))
            self._controller = controller
            return controller
        except Exception:
            # Connection failed partway through -- release whatever this
            # attempt acquired so a failed session doesn't permanently eat a
            # concurrency slot, then let the caller see the real error.
            self._teardown_after_failed_connect()
            raise

    def _teardown_after_failed_connect(self) -> None:
        if self._com_initialized:
            try:
                import pythoncom

                pythoncom.CoUninitialize()
            except Exception:
                logger.exception("VissimEditSession: CoUninitialize after failed connect raised")
            self._com_initialized = False
        if self._semaphore_acquired:
            _session_semaphore.release()
            self._semaphore_acquired = False

    # -----------------------------------------------------------------
    # M2.1 paired verbs (set + get), one per write tool in tools_edit.py
    #
    # v2 unit ⑤: set_vehicle_input_volume/get_vehicle_input_volume gained an
    # optional interval_no (default 1, byte-compatible); get_vehicle_input_
    # continuous is a THIRD paired getter (no matching "set" here -- Cont
    # clearing is a side effect of set_vehicle_input_volume itself, see
    # VissimController.set_vehicle_input's docstring for the verified
    # live Cont-flag trap) used only for interval_no > 1 verification.
    # -----------------------------------------------------------------

    def set_vehicle_input_volume(self, input_no: int, volume: float, interval_no: int = 1) -> None:
        self._ensure_connected().set_vehicle_input(input_no, volume, interval_no)

    def get_vehicle_input_volume(self, input_no: int, interval_no: int = 1) -> float:
        return self._ensure_connected().get_vehicle_input_volume(input_no, interval_no)

    def get_vehicle_input_continuous(self, input_no: int, interval_no: int) -> bool:
        return self._ensure_connected().get_vehicle_input_continuous(input_no, interval_no)

    def set_signal_cycle(self, sc_no: int, cycle: int) -> None:
        self._ensure_connected().set_signal_cycle(sc_no, cycle)

    def get_signal_cycle(self, sc_no: int) -> int:
        return self._ensure_connected().get_signal_cycle(sc_no)

    def set_signal_offset(self, sc_no: int, offset: int) -> None:
        self._ensure_connected().set_signal_offset(sc_no, offset)

    def get_signal_offset(self, sc_no: int) -> int:
        return self._ensure_connected().get_signal_offset(sc_no)

    def set_sim_duration(self, seconds: int) -> None:
        self._ensure_connected().set_sim_duration(seconds)

    def get_sim_duration(self) -> int:
        return self._ensure_connected().get_sim_duration()

    def set_random_seed(self, seed: int) -> None:
        self._ensure_connected().set_random_seed(seed)

    def get_random_seed(self) -> int:
        return self._ensure_connected().get_random_seed()

    def set_conflict_area_status(self, conflict_area_no: int, status: str) -> None:
        self._ensure_connected().set_conflict_area_status(conflict_area_no, status)

    def get_conflict_area(self, conflict_area_no: int) -> dict[str, Any]:
        return self._ensure_connected().get_conflict_area(conflict_area_no)

    # v2b unit (1): lane closure -- per-lane, per-vehicle-class BlockedVehClasses.

    def set_lane_closure(self, link_no: int, lane_index: int, vehicle_classes: list[int]) -> None:
        self._ensure_connected().set_lane_blocked_vehicle_classes(link_no, lane_index, vehicle_classes)

    def get_lane_closure(self, link_no: int, lane_index: int) -> list[int]:
        return self._ensure_connected().get_lane_blocked_vehicle_classes(link_no, lane_index)

    # Q4 unit (2): measurement-infrastructure placement -- queue counters,
    # data-collection points/measurements, travel-time sections. All four
    # are genuine COM CREATE/REMOVE verbs (native Add*/Remove* methods
    # exist -- see vissim_com.VissimController's own module comment for the
    # full live-verified writeup, including the pywin32 object-reference
    # landmine, the display-unit landmine, and COM's own failure to enforce
    # reference integrity on data-collection-point removal).

    def add_queue_counter(self, link_no: int, pos_m: float, name: str = "") -> dict[str, Any]:
        return self._ensure_connected().add_queue_counter(link_no, pos_m, name)

    def remove_queue_counter(self, no: int) -> None:
        self._ensure_connected().remove_queue_counter(no)

    def get_queue_counter(self, no: int) -> dict[str, Any]:
        return self._ensure_connected().get_queue_counter(no)

    def add_data_collection_point(
        self, link_no: int, lane_index: int, pos_m: float, name: str = ""
    ) -> dict[str, Any]:
        return self._ensure_connected().add_data_collection_point(link_no, lane_index, pos_m, name)

    def remove_data_collection_point(self, no: int) -> None:
        self._ensure_connected().remove_data_collection_point(no)

    def get_data_collection_point(self, no: int) -> dict[str, Any]:
        return self._ensure_connected().get_data_collection_point(no)

    def add_data_collection_measurement(self, point_nos: list[int], name: str = "") -> dict[str, Any]:
        return self._ensure_connected().add_data_collection_measurement(point_nos, name)

    def remove_data_collection_measurement(self, no: int) -> None:
        self._ensure_connected().remove_data_collection_measurement(no)

    def get_data_collection_measurement(self, no: int) -> dict[str, Any]:
        return self._ensure_connected().get_data_collection_measurement(no)

    def add_travel_time_section(
        self, name: str, from_link: int, from_pos_m: float, to_link: int, to_pos_m: float
    ) -> dict[str, Any]:
        return self._ensure_connected().add_travel_time_section(
            name, from_link, from_pos_m, to_link, to_pos_m
        )

    def remove_travel_time_section(self, no: int) -> None:
        self._ensure_connected().remove_travel_time_section(no)

    def get_travel_time_section(self, no: int) -> dict[str, Any]:
        return self._ensure_connected().get_travel_time_section(no)

    # Q4 unit (4): signal-head + detector placement -- genesis milestone
    # (b)'s "hands". Both genuine COM CREATE/REMOVE verbs (native Add*/
    # Remove* methods exist -- see vissim_com.VissimController's own module
    # comment for the full live-verified writeup, including the pywin32
    # object-reference landmine, the display-unit landmine, the signal
    # head's COM-object-only SG wiring, and the detector's scalar SC/PortNo
    # wiring).

    def add_signal_head(
        self, link_no: int, lane_index: int, pos_m: float, sc_no: int, sg_no: int, name: str = ""
    ) -> dict[str, Any]:
        return self._ensure_connected().add_signal_head(link_no, lane_index, pos_m, sc_no, sg_no, name)

    def remove_signal_head(self, no: int) -> None:
        self._ensure_connected().remove_signal_head(no)

    def get_signal_head(self, no: int) -> dict[str, Any]:
        return self._ensure_connected().get_signal_head(no)

    def add_detector(
        self,
        link_no: int,
        lane_index: int,
        pos_m: float,
        length_m: float = 4.5,
        sc_no: int | None = None,
        port_no: int | None = None,
        name: str = "",
    ) -> dict[str, Any]:
        return self._ensure_connected().add_detector(
            link_no, lane_index, pos_m, length_m, sc_no, port_no, name
        )

    def remove_detector(self, no: int) -> None:
        self._ensure_connected().remove_detector(no)

    def get_detector(self, no: int) -> dict[str, Any]:
        return self._ensure_connected().get_detector(no)

    # Q4 unit (5): add/remove links & connectors -- genuine COM CREATE/REMOVE
    # (native AddLink/AddConnector/RemoveLink methods exist on ILinkContainer
    # -- see vissim_com.VissimController's own module comment for the full
    # live-verified writeup, including the coordinate-space landmine, the
    # display-unit landmine, and COM's own failure to enforce reference
    # integrity on RemoveLink).

    def add_link(
        self,
        points_xy: list[tuple[float, float]],
        lane_count: int,
        behavior_type: int | None = None,
        display_type: int | None = None,
        name: str = "",
    ) -> dict[str, Any]:
        return self._ensure_connected().add_link(points_xy, lane_count, behavior_type, display_type, name)

    def remove_link(self, no: int) -> None:
        self._ensure_connected().remove_link(no)

    def get_link(self, no: int) -> dict[str, Any]:
        return self._ensure_connected().get_link(no)

    def add_connector(
        self,
        from_link_no: int,
        from_lane_indices: list[int],
        from_pos_m: float,
        to_link_no: int,
        to_lane_indices: list[int],
        to_pos_m: float,
        name: str = "",
    ) -> dict[str, Any]:
        return self._ensure_connected().add_connector(
            from_link_no, from_lane_indices, from_pos_m, to_link_no, to_lane_indices, to_pos_m, name
        )

    # v2 unit ⑥: static routing decision route relative-flow verbs.

    def set_route_relative_flow(
        self, decision_no: int, route_no: int, rel_flow: float, interval_no: int = 1
    ) -> None:
        self._ensure_connected().set_route_relative_flow(decision_no, route_no, rel_flow, interval_no)

    def get_route_relative_flow(self, decision_no: int, route_no: int, interval_no: int = 1) -> float:
        return self._ensure_connected().get_route_relative_flow(decision_no, route_no, interval_no)

    # v2 unit ⑦: vehicle-composition relative-flow (share weight) verbs. No
    # interval_no -- RelFlow is scalar-only for vehicle compositions
    # (verified live, backend/tools/probe_vehicle_compositions.py:
    # AttValue('RelFlow(1)') raises "Too many sub-attributes: 1").

    def set_vehicle_composition_share(
        self, composition_no: int, veh_type_no: int, rel_flow: float
    ) -> None:
        self._ensure_connected().set_vehicle_composition_share(composition_no, veh_type_no, rel_flow)

    def get_vehicle_composition_share(self, composition_no: int, veh_type_no: int) -> float:
        return self._ensure_connected().get_vehicle_composition_share(composition_no, veh_type_no)

    # v2a unit ⑧: desired-speed decision per-vehicle-class distribution
    # assignment verbs. No interval_no, no shared-sum semantics -- each
    # vehicle class's assignment is fully independent (verified live,
    # backend/tools/probe_des_speed_decisions.py).

    def set_des_speed_decision(
        self, decision_no: int, veh_class_no: int, des_speed_distr_no: int
    ) -> None:
        self._ensure_connected().set_des_speed_decision(decision_no, veh_class_no, des_speed_distr_no)

    def get_des_speed_decision(self, decision_no: int, veh_class_no: int) -> str:
        return self._ensure_connected().get_des_speed_decision(decision_no, veh_class_no)

    # QUEUE 3 (v2a) unit 3: desired-speed DISTRIBUTION curve (points) verbs.
    # Atomic full-curve replace only -- COM offers no per-point surgery
    # primitive that actually works reliably (see
    # VissimController.set_des_speed_distribution_points's own module
    # comment for the live-verified silent-no-op finding on per-point
    # SetAttValue, and the ReplaceAll flat-sequence shape landmine).

    def set_des_speed_distribution_points(
        self, distribution_no: int, points: list[dict[str, float]]
    ) -> list[dict[str, float]]:
        return self._ensure_connected().set_des_speed_distribution_points(distribution_no, points)

    def get_des_speed_distribution_points(self, distribution_no: int) -> list[dict[str, float]]:
        return self._ensure_connected().get_des_speed_distribution_points(distribution_no)

    # v2a unit ⑨: fixed-time signal-program green-split verbs. FIXEDTIME
    # controllers are written via the COM-writable IntSupplyData program
    # blob, FIXEDTIMESIMPLE via plain EndRed/EndGreen attributes — both
    # verified live (backend/tools/probe_signal_programs.py); see
    # VissimController.set_signal_green_split's docstring.

    def set_signal_green_split(
        self,
        sc_no: int,
        sg_no: int,
        green_start_s: float,
        green_end_s: float,
        program_no: int | None = None,
    ) -> dict[str, Any]:
        return self._ensure_connected().set_signal_green_split(
            sc_no, sg_no, green_start_s, green_end_s, program_no
        )

    def get_signal_green_split(
        self, sc_no: int, sg_no: int, program_no: int | None = None
    ) -> dict[str, Any]:
        return self._ensure_connected().get_signal_green_split(sc_no, sg_no, program_no)

    # QUEUE 3 unit 2: priority-rule conflict-marker gap/headway verbs.
    # marker_index is a synthesized 1-based ordinal (no natural identity
    # exists -- see VissimController.set_priority_rule_gap_headway's own
    # module comment for the full landmine writeup).

    def set_priority_rule_gap_headway(
        self,
        priority_rule_no: int,
        marker_index: int,
        gap_time_s: float | None,
        headway_m: float | None,
    ) -> dict[str, float]:
        return self._ensure_connected().set_priority_rule_gap_headway(
            priority_rule_no, marker_index, gap_time_s, headway_m
        )

    def get_priority_rule_gap_headway(self, priority_rule_no: int, marker_index: int) -> dict[str, float]:
        return self._ensure_connected().get_priority_rule_gap_headway(priority_rule_no, marker_index)

    # Q4 unit (6): stop-sign + priority-rule PLACEMENT (creation) --
    # genesis counterpart to the gap/headway setters above. See
    # vissim_com.VissimController's own module comment for the full
    # live-verified writeup, including AddPriorityRule's confirmed silent
    # no-op and the DuplicatePriorityRule-based real creation path.

    def add_stop_sign(
        self,
        link_no: int,
        lane_index: int,
        pos_m: float,
        name: str = "",
        sc_no: int | None = None,
        sg_no: int | None = None,
    ) -> dict[str, Any]:
        return self._ensure_connected().add_stop_sign(link_no, lane_index, pos_m, name, sc_no, sg_no)

    def remove_stop_sign(self, no: int) -> None:
        self._ensure_connected().remove_stop_sign(no)

    def get_stop_sign(self, no: int) -> dict[str, Any]:
        return self._ensure_connected().get_stop_sign(no)

    def add_priority_rule(
        self,
        stop_link_no: int,
        stop_lane_index: int,
        stop_pos_m: float,
        markers: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return self._ensure_connected().add_priority_rule(
            stop_link_no, stop_lane_index, stop_pos_m, markers
        )

    def remove_priority_rule(self, no: int) -> None:
        self._ensure_connected().remove_priority_rule(no)

    # QUEUE (v2a) unit: vehicle-type distribution/function reference
    # verbs. Each reference field is a flat, independent attribute on the
    # VehicleType object itself -- no shared-sum semantics, no paired
    # writes (see VissimController.set_vehicle_type_reference's own module
    # comment for the full live-verified writeup).

    def set_vehicle_type_reference(self, vehicle_type_no: int, field: str, target_no: int) -> None:
        self._ensure_connected().set_vehicle_type_reference(vehicle_type_no, field, target_no)

    def get_vehicle_type_reference(self, vehicle_type_no: int, field: str) -> str | None:
        return self._ensure_connected().get_vehicle_type_reference(vehicle_type_no, field)

    # QUEUE (Q3, v2a) unit 5: time-distribution verbs. Mean/StdDev on a
    # NORMAL distribution, atomic full-curve points replace on an EMPIRICAL
    # one -- see VissimController's own module comment (time-distribution
    # block) for the full live-verified writeup, including Vissim's own
    # refusal to let Mean/StdDev be edited on an EMPIRICAL-type object.

    def set_time_distribution_normal(
        self, time_distribution_no: int, mean_s: float | None, std_dev_s: float | None
    ) -> dict[str, Any]:
        return self._ensure_connected().set_time_distribution_normal(time_distribution_no, mean_s, std_dev_s)

    def get_time_distribution(self, time_distribution_no: int) -> dict[str, Any]:
        return self._ensure_connected().get_time_distribution(time_distribution_no)

    def set_time_distribution_points(
        self, time_distribution_no: int, points: list[dict[str, float]]
    ) -> list[dict[str, float]]:
        return self._ensure_connected().set_time_distribution_points(time_distribution_no, points)

    def get_time_distribution_points(self, time_distribution_no: int) -> list[dict[str, float]]:
        return self._ensure_connected().get_time_distribution_points(time_distribution_no)

    # QUEUE 3 unit 6: driving-behavior duplicate-then-edit verbs -- see
    # VissimController's own module comment (driving-behavior block) for the
    # full live-verified writeup (DuplicateDrivingBehavior's exact-copy
    # fidelity, the whitelist's per-field reasoning, the display-unit
    # landmine, and the AddVehClassDrivingBehavior COM/pywin32 landmine).

    def duplicate_driving_behavior(self, source_no: int, new_name: str) -> dict[str, Any]:
        return self._ensure_connected().duplicate_driving_behavior(source_no, new_name)

    def get_driving_behavior(self, behavior_no: int) -> dict[str, Any]:
        return self._ensure_connected().get_driving_behavior(behavior_no)

    def set_driving_behavior_parameter(self, behavior_no: int, parameter: str, value: float) -> dict[str, Any]:
        return self._ensure_connected().set_driving_behavior_parameter(behavior_no, parameter, value)

    def set_link_behavior_type_driving_behavior(
        self, link_behavior_type_no: int, driving_behavior_no: int, veh_class_no: int | None = None
    ) -> dict[str, Any]:
        return self._ensure_connected().set_link_behavior_type_driving_behavior(
            link_behavior_type_no, driving_behavior_no, veh_class_no
        )

    def get_link_behavior_type_driving_behavior(
        self, link_behavior_type_no: int, veh_class_no: int | None = None
    ) -> dict[str, Any]:
        return self._ensure_connected().get_link_behavior_type_driving_behavior(
            link_behavior_type_no, veh_class_no
        )

    # -----------------------------------------------------------------
    # Persistence
    # -----------------------------------------------------------------

    def persist(self) -> None:
        """Write the live session's current state onto ``working_inpx``.

        Two-step (verified live by ``tools/edit_live_probe.py``): SaveNetAs
        into a sibling temp file, then ``os.replace`` it onto
        ``working_inpx``. M2.1's ``save_net_as`` guard forbids saving
        directly onto the loaded path, and the probe confirmed this
        session's own still-open COM handle does not keep an OS-level lock
        that would make ``os.replace`` fail -- Vissim's SaveNetAs is a
        discrete write, not a held-open file handle.

        Raises whatever ``save_net_as``/``os.replace`` raise -- callers
        (tools_edit.py's tool functions) deliberately do NOT catch this, so
        toolkit.dispatch's own exception handling turns it into a
        structured ``tool_execution_error`` envelope rather than a silent
        no-op that would leave the caller believing the write landed on
        disk when it did not.
        """
        controller = self._ensure_connected()
        tmp_path = self.working_inpx.with_name(
            self.working_inpx.stem + "_saving" + self.working_inpx.suffix
        )
        controller.save_net_as(tmp_path)
        os.replace(tmp_path, self.working_inpx)

    # -----------------------------------------------------------------
    # Teardown
    # -----------------------------------------------------------------

    def close(self) -> None:
        """Disconnect COM, release the concurrency slot, uninitialize the
        apartment. Idempotent and safe even if the session never connected
        (e.g. the turn never reached a write tool)."""
        if self._controller is not None:
            try:
                self._controller.disconnect()
            except Exception:
                logger.exception("VissimEditSession.close: controller.disconnect() raised")
            self._controller = None

        if self._com_initialized:
            try:
                import pythoncom

                pythoncom.CoUninitialize()
            except Exception:
                logger.exception("VissimEditSession.close: CoUninitialize raised")
            self._com_initialized = False

        if self._semaphore_acquired:
            _session_semaphore.release()
            self._semaphore_acquired = False


# ---------------------------------------------------------------------------
# Active-session context (mirrors tools_network.set_active_inpx's pattern)
# ---------------------------------------------------------------------------

_active_edit_session: VissimEditSession | None = None


def set_active_edit_session(session: VissimEditSession | None) -> None:
    """Set (or clear, with ``None``) the process-wide "current write turn"
    edit session that ``tools_edit.py``'s tool functions dispatch against.

    Module-global by design, mirroring ``tools_network.set_active_inpx``'s
    documented pattern and its same acceptable-limitation caveat: this is
    NOT safe for multiple concurrent agent turns in one process each trying
    to run a write at the same time (see this module's docstring). The
    caller (``routers/assistant.py``) sets this right before invoking
    ``run_agent_turn`` for an approved write and clears it in a ``finally``
    block.
    """
    global _active_edit_session
    _active_edit_session = session


def get_active_edit_session() -> VissimEditSession | None:
    """The current write turn's active session, or ``None`` if none is set
    (the ordinary case for every read-only turn, and for a write turn that
    has not yet been approved)."""
    return _active_edit_session


def clear_active_edit_session() -> None:
    """Equivalent to ``set_active_edit_session(None)`` -- named separately
    so call sites read as intent ("clear") rather than a magic ``None``."""
    global _active_edit_session
    _active_edit_session = None


__all__ = [
    "MAX_CONCURRENT_EDIT_SESSIONS",
    "VissimEditSession",
    "clear_active_edit_session",
    "get_active_edit_session",
    "set_active_edit_session",
]
