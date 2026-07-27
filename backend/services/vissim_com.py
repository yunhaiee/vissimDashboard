"""VISSIM COM API wrapper.

Every caller must call pythoncom.CoInitialize() in the same thread before using
this controller.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class VissimObjectNotFoundError(ValueError):
    pass


# ---------------------------------------------------------------------------
# Network-performance evaluation configuration
#
# COM attribute ids verified EMPIRICALLY against live Vissim 2026
# (backend/tools/evaluation_probe.py, run against the "Urban Intersection
# Beijing.CN" example -- see docs/phase2/DECISIONS.md's M2.1 CycTm entry for
# why this can never be trusted from docs/memory alone: a plausible-looking
# id ("CycleTime") has already been proven wrong once in this codebase).
# The probe cross-checked each candidate's pre-edit read-back against the
# example's own .inpx ground truth (<vehNetPerf collectData="false"
# fromTime="0" interval="999999" toTime="99999"/> and its 3 siblings below)
# before accepting it.
#
# FOUR evaluation types are configured together, not just "vehicle network
# performance" alone: services/results_reader.py's _read_db plain-executes
# (no fallback/degrade-to-empty) against FOUR result tables, and raises --
# losing even a successfully-read "network" dict -- if ANY ONE of them is
# missing from the result .db. Discovered live: an earlier version of this
# fix that armed only vehicle-network-performance made a real run's KPIs
# come back None because MOVEMENT_EvaluationTimeIntervalClass didn't exist.
# See docs/phase2/DECISIONS.md.
#
#   internal key                  -> .inpx element  -> result table (via _read_db)
#   vehicle_network_performance   -> <vehNetPerf>    -> VEHICLENETWORKPERFORMANCEMEASUREMENT_EvaluationTimeIntervalClass
#   node_results                  -> <nodeResults>   -> MOVEMENT_EvaluationTimeIntervalClass
#   vehicle_travel_time           -> <vehTravTms>    -> VEHICLETRAVELTIMEMEASUREMENT_EvaluationTimeIntervalClass
#   queue_counters                -> <queues>        -> MOVEMENT_EvaluationTimeInterval
#
# The COM id prefix does NOT reliably match the .inpx element name (the
# whole reason this must be verified live, not assumed): vehNetPerf,
# vehTravTms, and queues happened to match their xml tag name exactly
# (VehNetPerf/VehTravTms/Queues), but nodeResults did NOT -- its real COM
# prefix is the shorter "NodeRes", not "NodeResults".
# ---------------------------------------------------------------------------

_EVAL_TYPE_ATTR_IDS: dict[str, dict[str, str]] = {
    "vehicle_network_performance": {
        "collect_data": "VehNetPerfCollectData",
        "from_time": "VehNetPerfFromTime",
        "to_time": "VehNetPerfToTime",
        "interval": "VehNetPerfInterval",
    },
    "node_results": {
        "collect_data": "NodeResCollectData",
        "from_time": "NodeResFromTime",
        "to_time": "NodeResToTime",
        "interval": "NodeResInterval",
    },
    "vehicle_travel_time": {
        "collect_data": "VehTravTmsCollectData",
        "from_time": "VehTravTmsFromTime",
        "to_time": "VehTravTmsToTime",
        "interval": "VehTravTmsInterval",
    },
    "queue_counters": {
        "collect_data": "QueuesCollectData",
        "from_time": "QueuesFromTime",
        "to_time": "QueuesToTime",
        "interval": "QueuesInterval",
    },
}


# ---------------------------------------------------------------------------
# Vehicle-record (.fzp) output configuration -- vehicle-trajectory REPLAY
# data layer, distinct from the four KPI evaluation types above.
#
# COM attribute ids verified EMPIRICALLY against live Vissim 2026
# (backend/tools/vehicle_record_probe.py, run against "Urban Intersection
# Beijing.CN" -- same live-first discipline as _EVAL_TYPE_ATTR_IDS above; see
# docs/phase2/DECISIONS.md's dated entry for this unit for the full findings
# pasted verbatim). Ground truth from the example's own .inpx:
#   <vehRec filterType="ALL" fromTime="0" inclParkVehs="false" resolution="1"
#           toTime="99999" writeDatabase="false" writeFile="false">
#     <selectedAttributes>
#       <attributeSelection attributeID="SIMSEC" .../>
#       <attributeSelection attributeID="NO" .../>
#       <attributeSelection attributeID="LANE\LINK\NO" .../>
#       <attributeSelection attributeID="LANE\INDEX" .../>
#       <attributeSelection attributeID="POS" .../>
#     </selectedAttributes>
#   </vehRec>
#
# Unlike the four KPI evaluation types, vehRec has NO "CollectData"-suffixed
# arm toggle -- the verified id is ``VehRecWriteFile`` (writeFile in the XML).
# ``filterType``/``inclParkVehs`` are left at whatever the source network
# already has, same "closest verifiable subset" policy as
# configure_network_performance_evaluation's own field selection.
#
# EMPIRICAL FINDING (do not "fix" without re-probing live): the exported
# .fzp's real column set does NOT match this codebase's original synthetic
# assumption. Vissim writes ``LANE\LINK\NO``/``LANE\INDEX`` (backslash-
# qualified sub-attribute paths), never a bare ``LINK``/``LANE`` column, and
# the network's DEFAULT attribute selection carries no ``SPEED`` column at
# all. ``Evaluation.VehRecSelectedAttributes`` (an ``IAttributeSelectionContainer``)
# is READ-accessible over COM but every attempt at adding/replacing its
# attribute set (``ReplaceAll``, ``SetMultipleAttributes``, ``SetMultiAttValues``,
# no ``Add``/``AddAttribute`` method exists at all) raised -- their error text
# suggests these methods are not actually meant for redefining which columns
# get exported (see backend/tools/vehicle_record_attrs_probe.py/_probe2.py).
# ``services/trajectory_reader.py``'s parser was adapted (format detection via
# column-name aliasing, SPEED made optional) rather than this codebase
# forking a second parsing pipeline -- see that module's own docstring.
#
# The real export also opens with a UTF-8 BOM glued to a ``$VISION`` marker
# line, which ``parse_fzp`` must read with ``utf-8-sig`` to avoid corrupting
# whatever line happens to be first.
#
# SIZE FINDING (live acceptance, tools/trajectory_replay_live_check.py): at
# the network's DEFAULT resolution (1 -- one row per simulation timestep,
# 0.1s on Beijing.CN), a real 300s run on a moderately busy real network
# (Urban Intersection Beijing.CN) produced a 21MB .fzp that resolves to a
# ~90MB JSON response -- the browser aborted the fetch outright. Verified
# live (tools/vehicle_record_resolution_probe.py) that ``VehRecResolution``
# is a genuine, proportional write-cadence divisor: resolution=5 on the SAME
# 60s run produced EXACTLY a 0.5s step (5.00x coarser) and ~0.2x the file
# size. ``configure_vehicle_record_evaluation`` therefore also arms
# ``resolution`` (default 5, i.e. 0.5s cadence -- matching the synthetic demo
# generator's own step, and a genuine root-cause fix rather than only
# stride-decimating after the fact at the endpoint).
# ---------------------------------------------------------------------------

_VEHICLE_RECORD_ATTR_IDS: dict[str, str] = {
    "write_file": "VehRecWriteFile",
    "write_database": "VehRecWriteDatabase",
    "from_time": "VehRecFromTime",
    "to_time": "VehRecToTime",
    "resolution": "VehRecResolution",
}

# Default write cadence divisor (see the SIZE FINDING comment above) --
# every Nth simulation timestep is written, not every one. 5 matches the
# synthetic demo generator's own 0.5s step and cut a real 300s/busy-network
# .fzp roughly 5x, which is what made that run's JSON payload browser-
# fetchable at all. Callers (routers/simulations.py) may override.
DEFAULT_VEHICLE_RECORD_RESOLUTION = 5


class VissimController:
    def __init__(self) -> None:
        self.vissim = None
        self._owns_instance = False
        self.prog_id: str | None = None
        self._max_sim_sec = 0.0
        self._sim_run_ended = False
        self._loaded_net_path: Path | None = None

    def connect(
        self,
        visible: bool = False,
        launch_if_missing: bool = True,
        attach_to_running: bool = True,
    ) -> None:
        """Attach to a running Vissim or launch a new one.

        Pass ``attach_to_running=False`` for automation that must own its
        instance: GetActiveObject grabs an arbitrary registered instance, which
        may be a human's interactive session — LoadNet into it would destroy
        their open work.
        """
        import win32com.client

        progids = [
            "VISSIM.Vissim.2026",
            "VISSIM.Vissim-64.2600",
            "VISSIM.Vissim.2025",
            "VISSIM.Vissim-64.2500",
            "VISSIM.Vissim.2024",
            "VISSIM.Vissim-64.2400",
            "VISSIM.Vissim",
        ]

        if attach_to_running:
            for prog_id in progids:
                try:
                    self.vissim = win32com.client.GetActiveObject(prog_id)
                    self._owns_instance = False
                    self.prog_id = prog_id
                    logger.info("Attached to running VISSIM via %s", prog_id)
                    break
                except Exception:
                    continue

        if self.vissim is None and launch_if_missing:
            for prog_id in progids:
                try:
                    self.vissim = win32com.client.Dispatch(prog_id)
                    self._owns_instance = True
                    self.prog_id = prog_id
                    logger.info("Launched new VISSIM via %s", prog_id)
                    break
                except Exception:
                    continue

        if self.vissim is None:
            raise RuntimeError("No running VISSIM COM instance was found.")

        if not visible:
            try:
                self.vissim.Graphics.CurrentNetworkWindow.SetAttValue("QuickMode", 1)
            except Exception:
                pass

    def load_network(self, inpx_path: str) -> None:
        self.vissim.LoadNet(str(inpx_path), False)
        self._reset_run_tracking()
        self._loaded_net_path = Path(inpx_path)
        try:
            self.vissim.Net.SetAttValue("ScenManagmt", 0)
            logger.info("Scenario management disabled")
        except Exception:
            logger.warning("Could not disable scenario management; ensure VISSIM is in Edit Scenario mode")

    def save_net_as(self, path: str | Path) -> None:
        """Persist the currently loaded network to ``path`` via COM SaveNetAs.

        This is how agent edits get written out of the live COM session into
        a job work dir (docs/phase2/01-ARCHITECTURE.md, agent-state
        artifacts). Refuses -- without touching COM at all -- when ``path``
        resolves to the same file as the network this session loaded, so an
        edit session can never silently overwrite the source .inpx it was
        cloned from (docs/phase2/00-EXECUTION-GUIDE.md rule 4). Callers must
        always pass a new path inside the job work dir (e.g. an
        ``edit_001.inpx`` version file).
        """
        target = Path(path)
        if self._loaded_net_path is not None and _same_path(target, self._loaded_net_path):
            raise ValueError(
                f"Refusing to overwrite the loaded source network file at {target}. "
                "Pass a new path inside the job work dir instead."
            )
        try:
            self.vissim.SaveNetAs(str(target))
        except Exception as exc:
            raise RuntimeError(f"Could not save network to {target}: {exc}") from exc

    def configure_network_performance_evaluation(
        self, *, duration_s: int, from_time: int = 0
    ) -> dict[str, dict[str, Any]]:
        """Arm the FOUR Vissim evaluation types
        ``services/results_reader.py``'s ``_read_db`` unconditionally
        requires (see the module-level comment above
        :data:`_EVAL_TYPE_ATTR_IDS` for the full table -- vehicle network
        performance, node results, vehicle travel time, and queue counters)
        on the LOADED network, so a completed run's result .db contains
        every table ``_read_db`` needs without raising partway through and
        losing the KPI dict it already read.

        Evaluations are NOT retroactive (docs/phase2/00-EXECUTION-GUIDE.md
        SS3.1) -- this must run after ``load_network`` and before
        ``RunContinuous``/``RunSingleStep``, every time a network is loaded
        fresh (there is nothing to "reuse" across separate loads/instances).
        Reconfiguring evaluation DOES delete any existing results
        (docs/phase2/DECISIONS.md's ``evaluation_change_deletes_results``
        constraint) -- safe here because every caller in this codebase runs
        on its own per-job/per-seed clone that has no prior results to lose.

        ``interval`` is deliberately set equal to ``to_time`` for every type
        (one single aggregated bucket spanning the whole run): this
        guarantees at least one result row regardless of how short
        ``duration_s`` is, mirroring the untouched network default itself
        (``vehNetPerf``'s own default is ``interval=999999 > toTime=99999``
        -- Vissim already treats an interval longer than the window as "one
        bucket", verified by ``tools/evaluation_probe.py`` against the
        Beijing example's on-disk defaults before this method ever touches
        them).

        Raises ``RuntimeError`` (never returns a lie) if any SetAttValue
        call fails OR if the paired read-back
        (:meth:`get_network_performance_evaluation_config`) doesn't match
        what was just written -- the M2.1 doctrine's "every setter has a
        paired reader used for post-write verification".
        """
        to_time = max(int(duration_s), 1)
        target_values: dict[str, dict[str, Any]] = {
            eval_type: {
                "collect_data": True,
                "from_time": int(from_time),
                "to_time": to_time,
                "interval": to_time,
            }
            for eval_type in _EVAL_TYPE_ATTR_IDS
        }
        try:
            evaluation = self.vissim.Evaluation
            for eval_type, fields in _EVAL_TYPE_ATTR_IDS.items():
                for field, attr_id in fields.items():
                    evaluation.SetAttValue(attr_id, target_values[eval_type][field])
        except Exception as exc:
            raise RuntimeError(
                f"Could not configure network-performance evaluation: {exc}"
            ) from exc

        config = self.get_network_performance_evaluation_config()
        if config != target_values:
            raise RuntimeError(
                "Network-performance evaluation read-back mismatch after "
                f"configuration: wrote {target_values}, read back {config}."
            )
        return config

    def get_network_performance_evaluation_config(self) -> dict[str, dict[str, Any]]:
        """Paired getter for :meth:`configure_network_performance_evaluation`.

        Also usable standalone to check whether a loaded network already has
        every required evaluation type armed (e.g. before deciding to
        reconfigure it). Returns ``{eval_type: {"collect_data", "from_time",
        "to_time", "interval"}}`` for every key in :data:`_EVAL_TYPE_ATTR_IDS`.
        """
        try:
            evaluation = self.vissim.Evaluation
            return {
                eval_type: {
                    "collect_data": bool(int(evaluation.AttValue(fields["collect_data"]))),
                    "from_time": int(evaluation.AttValue(fields["from_time"])),
                    "to_time": int(evaluation.AttValue(fields["to_time"])),
                    "interval": int(evaluation.AttValue(fields["interval"])),
                }
                for eval_type, fields in _EVAL_TYPE_ATTR_IDS.items()
            }
        except Exception as exc:
            raise RuntimeError(
                f"Could not read network-performance evaluation config: {exc}"
            ) from exc

    def configure_vehicle_record_evaluation(
        self,
        *,
        duration_s: int,
        from_time: int = 0,
        resolution: int = DEFAULT_VEHICLE_RECORD_RESOLUTION,
    ) -> dict[str, Any]:
        """Arm Vissim's vehicle-record (.fzp) output for the duration of a
        run -- the vehicle-trajectory REPLAY data source, separate from the
        four KPI evaluation types :meth:`configure_network_performance_evaluation`
        arms. Ids verified empirically (see the module-level comment above
        :data:`_VEHICLE_RECORD_ATTR_IDS`); ``write_file`` is the arm toggle
        (there is no separate "collect data" flag), ``write_database`` is
        explicitly forced False (this codebase only ever reads the .fzp file,
        never Vissim's own results database for vehicle records).

        ``resolution`` (default :data:`DEFAULT_VEHICLE_RECORD_RESOLUTION`,
        i.e. write every 5th simulation timestep) is a genuine, live-verified
        proportional write-cadence divisor -- NOT left at the network's own
        default, unlike ``filterType``/``inclParkVehs``. See the module-level
        SIZE FINDING comment: a real, moderately busy network at the default
        resolution (1) produced a payload the browser outright aborted.

        Must run after ``load_network`` and before ``RunContinuous``/
        ``RunSingleStep`` (evaluations are not retroactive -- see
        docs/phase2/00-EXECUTION-GUIDE.md SS3.1), same timing contract as
        :meth:`configure_network_performance_evaluation`.

        Raises ``RuntimeError`` (never returns a lie) if any ``SetAttValue``
        call fails OR if the paired read-back
        (:meth:`get_vehicle_record_evaluation_config`) doesn't match what was
        just written -- callers (``routers/simulations.py``'s agent-run job
        runner) MUST treat this failure as non-fatal to the run itself (KPIs
        are primary; trajectory recording is best-effort) and simply record
        ``trajectory_recorded: False`` rather than aborting the job.
        """
        to_time = max(int(duration_s), 1)
        target = {
            "write_file": True,
            "write_database": False,
            "from_time": int(from_time),
            "to_time": to_time,
            "resolution": max(int(resolution), 1),
        }
        try:
            evaluation = self.vissim.Evaluation
            for field, attr_id in _VEHICLE_RECORD_ATTR_IDS.items():
                evaluation.SetAttValue(attr_id, target[field])
        except Exception as exc:
            raise RuntimeError(
                f"Could not configure vehicle-record evaluation: {exc}"
            ) from exc

        config = self.get_vehicle_record_evaluation_config()
        if config != target:
            raise RuntimeError(
                "Vehicle-record evaluation read-back mismatch after configuration: "
                f"wrote {target}, read back {config}."
            )
        return config

    def get_vehicle_record_evaluation_config(self) -> dict[str, Any]:
        """Paired getter for :meth:`configure_vehicle_record_evaluation`."""
        try:
            evaluation = self.vissim.Evaluation
            return {
                "write_file": bool(int(evaluation.AttValue(_VEHICLE_RECORD_ATTR_IDS["write_file"]))),
                "write_database": bool(int(evaluation.AttValue(_VEHICLE_RECORD_ATTR_IDS["write_database"]))),
                "from_time": int(evaluation.AttValue(_VEHICLE_RECORD_ATTR_IDS["from_time"])),
                "to_time": int(evaluation.AttValue(_VEHICLE_RECORD_ATTR_IDS["to_time"])),
                "resolution": int(evaluation.AttValue(_VEHICLE_RECORD_ATTR_IDS["resolution"])),
            }
        except Exception as exc:
            raise RuntimeError(
                f"Could not read vehicle-record evaluation config: {exc}"
            ) from exc

    def set_vehicle_input(self, input_no: int, volume: float, interval_no: int = 1) -> None:
        """Set a vehicle input's Volume(``interval_no``) (veh/h; 1-based --
        verified live, ``backend/tools/vehicle_input_interval_probe.py``).

        ``interval_no=1`` (default) is byte-compatible with the pre-v2-unit-⑤
        single-interval verb: the exact same ``SetAttValue("Volume(1)", ...)``
        call, no ``Cont`` handling (every real network probed reads
        ``Cont(1)`` as always false/0 -- interval 1 has nothing before it to
        continue from).

        ``interval_no > 1`` clears ``Cont(interval_no)`` FIRST when Vissim
        reports it continuous. Verified live: ``SetAttValue`` on
        ``Volume(i)`` for ``i > 1`` RAISES ("Attribute Volume (...) cannot be
        changed") whenever ``Cont(i)`` is true (the interval inherits the
        previous interval's volume) -- it is not a silent no-op. Reading an
        interval index that does not exist on this input at all (beyond its
        own ``timeIntervalVehVolume`` count) also RAISES ("Object N not
        found") -- per-object indexed sub-attributes fail CLOSED, unlike
        ``Net.AttValue``'s fail-OPEN behavior for unknown top-level ids (v2
        unit ③'s landmine); this method does not attempt to catch/soften
        that, it is the caller's (constraints.py's preflight bounds check,
        when the datum is known) job to refuse before this is ever reached.
        """
        vehicle_input = self._item_by_key(
            self.vissim.Net.VehicleInputs,
            input_no,
            "Vehicle input",
            self.available_vehicle_input_nos(),
        )
        try:
            if interval_no != 1 and bool(int(vehicle_input.AttValue(f"Cont({interval_no})"))):
                vehicle_input.SetAttValue(f"Cont({interval_no})", False)
            vehicle_input.SetAttValue(f"Volume({interval_no})", volume)
        except Exception as exc:
            raise RuntimeError(
                f"Could not set vehicle input {input_no} interval {interval_no} volume to {volume}: {exc}"
            ) from exc

    def get_vehicle_input_volume(self, input_no: int, interval_no: int = 1) -> float:
        """Paired getter for :meth:`set_vehicle_input`. ``interval_no=1``
        (default) reads the exact same ``Volume(1)`` attribute as before."""
        vehicle_input = self._item_by_key(
            self.vissim.Net.VehicleInputs,
            input_no,
            "Vehicle input",
            self.available_vehicle_input_nos(),
        )
        try:
            return float(vehicle_input.AttValue(f"Volume({interval_no})"))
        except Exception as exc:
            raise RuntimeError(
                f"Could not read vehicle input {input_no} interval {interval_no} volume: {exc}"
            ) from exc

    def get_vehicle_input_continuous(self, input_no: int, interval_no: int) -> bool:
        """Read the ``Cont(interval_no)`` flag -- a second paired getter so
        callers can report the post-write continuous state in their
        verification payload (00-EXECUTION-GUIDE.md §4: every setter needs a
        read-back) when :meth:`set_vehicle_input` had to clear it."""
        vehicle_input = self._item_by_key(
            self.vissim.Net.VehicleInputs,
            input_no,
            "Vehicle input",
            self.available_vehicle_input_nos(),
        )
        try:
            return bool(int(vehicle_input.AttValue(f"Cont({interval_no})")))
        except Exception as exc:
            raise RuntimeError(
                f"Could not read vehicle input {input_no} interval {interval_no} continuous flag: {exc}"
            ) from exc

    def set_sim_duration(self, seconds: int) -> None:
        self.vissim.Simulation.SetAttValue("SimPeriod", seconds)
        self._reset_run_tracking()

    def get_sim_duration(self) -> int:
        try:
            return int(self.vissim.Simulation.AttValue("SimPeriod"))
        except Exception as exc:
            raise RuntimeError(f"Could not read simulation duration (SimPeriod): {exc}") from exc

    def set_random_seed(self, seed: int) -> None:
        self.vissim.Simulation.SetAttValue("RandSeed", seed)

    def get_random_seed(self) -> int:
        try:
            return int(self.vissim.Simulation.AttValue("RandSeed"))
        except Exception as exc:
            raise RuntimeError(f"Could not read random seed (RandSeed): {exc}") from exc

    def set_signal_cycle(self, sc_no: int, cycle: int) -> None:
        """Set the controller's top-level cycle-time attribute (COM id
        ``CycTm``; Vissim's own long name for it is "Cycle time").

        NOTE (verified empirically against live Vissim 2026 for M2.1): the
        correct COM attribute id is ``CycTm`` -- ``CycleTime``, used by this
        method before this fix, does not exist on ISignalController in this
        Vissim version and always raised "Could not find attribute with ID
        CYCLETIME". ``CycTm`` itself is, on many real fixed-time controllers,
        a read-only rollup: when the controller runs a program from the
        internal opaque supply-data blob (the ``intSupplyData`` base64 blob
        ``services/signal_parser.py`` decodes), the EFFECTIVE cycle time
        lives inside that blob and Vissim raises "Attribute Cycle time
        cannot be changed" for this call -- there is no COM-writable path to
        that blob (matches docs/phase2/00-EXECUTION-GUIDE.md SS3.2's
        opaque-blob landmine). That refusal surfaces here as a normal
        RuntimeError, which is the correct, safe behavior per the guardrail
        doctrine: callers must not assume ``CycTm`` drives the simulated
        cycle just because this method returns without raising on some
        OTHER, non-program-driven controller -- see get_signal_cycle.
        """
        try:
            signal_controller = self._item_by_key(
                self.vissim.Net.SignalControllers,
                sc_no,
                "Signal controller",
                self.available_signal_controller_nos(),
            )
            signal_controller.SetAttValue("CycTm", cycle)
        except VissimObjectNotFoundError:
            raise
        except Exception as exc:
            raise RuntimeError(f"Could not set signal controller {sc_no} cycle to {cycle}: {exc}") from exc

    def get_signal_cycle(self, sc_no: int) -> int:
        """Read back the same ``CycTm`` attribute ``set_signal_cycle`` writes.

        See that method's docstring: ``CycTm`` is NOT the field
        ``services.signal_parser`` / ``services.agent.tools_network.
        build_shadow_model`` report as ``cycle_time`` for opaque-blob-program
        controllers (that field has no COM read OR write path at all). This
        getter verifies a ``set_signal_cycle`` write landed in the live COM
        session (and, after ``save_net_as`` + reload, on disk) -- it is not
        a source of the "real" simulated cycle time.
        """
        try:
            signal_controller = self._item_by_key(
                self.vissim.Net.SignalControllers,
                sc_no,
                "Signal controller",
                self.available_signal_controller_nos(),
            )
            return int(signal_controller.AttValue("CycTm"))
        except VissimObjectNotFoundError:
            raise
        except Exception as exc:
            raise RuntimeError(f"Could not read signal controller {sc_no} cycle: {exc}") from exc

    def set_signal_offset(self, sc_no: int, offset: int) -> None:
        try:
            signal_controller = self._item_by_key(
                self.vissim.Net.SignalControllers,
                sc_no,
                "Signal controller",
                self.available_signal_controller_nos(),
            )
            signal_controller.SetAttValue("Offset", offset)
        except VissimObjectNotFoundError:
            raise
        except Exception as exc:
            raise RuntimeError(f"Could not set signal controller {sc_no} offset to {offset}: {exc}") from exc

    def get_signal_offset(self, sc_no: int) -> int:
        try:
            signal_controller = self._item_by_key(
                self.vissim.Net.SignalControllers,
                sc_no,
                "Signal controller",
                self.available_signal_controller_nos(),
            )
            return int(signal_controller.AttValue("Offset"))
        except VissimObjectNotFoundError:
            raise
        except Exception as exc:
            raise RuntimeError(f"Could not read signal controller {sc_no} offset: {exc}") from exc

    def set_conflict_area_status(self, conflict_area_no: int, status: str) -> None:
        """Set a conflict area's right-of-way status (COM attribute id
        ``Status`` -- verified empirically live against Vissim 2026 via
        ``backend/tools/conflict_area_probe.py`` on the Urban Intersection
        Beijing.CN example: unlike the CycTm/NodeRes landmines in
        docs/phase2/DECISIONS.md, this id matches the .inpx XML attribute
        name exactly).

        Conflict areas have **no Add/Remove via COM** -- they are derived
        from geometry (docs/phase2/00-EXECUTION-GUIDE.md SS3.2). This method
        only ever mutates an EXISTING conflict area's status; it can never
        create or delete one. The empirically-observed status vocabulary
        (all 25 real Vissim 2026 example networks) is exactly ``PASSIVE``
        (no right-of-way control -- the catalog's most dangerous silent
        failure), ``AHASRIGHTOFWAY``, ``BHASRIGHTOFWAY``, and
        ``UNDETERMINED``; the caller (``tools_edit.py``) restricts the param
        to that literal set before this method is ever reached.
        """
        try:
            conflict_area = self._item_by_key(
                self.vissim.Net.ConflictAreas,
                conflict_area_no,
                "Conflict area",
                self.available_conflict_area_nos(),
            )
            conflict_area.SetAttValue("Status", status)
        except VissimObjectNotFoundError:
            raise
        except Exception as exc:
            raise RuntimeError(
                f"Could not set conflict area {conflict_area_no} status to {status}: {exc}"
            ) from exc

    def get_conflict_area(self, conflict_area_no: int) -> dict[str, Any]:
        """Paired getter for :meth:`set_conflict_area_status`.

        Also returns the link pair (``LinkA``/``LinkB``, the same COM ids
        the probe confirmed) in the SAME read, not a separate call --
        conflict-area NUMBERS are not stable identity across geometry ops
        (docs/phase2/00-EXECUTION-GUIDE.md SS3.2), so every caller that acts
        on a conflict area number should have the link pair on hand to
        confirm it is really the junction the user meant, without a second
        COM round trip.
        """
        try:
            conflict_area = self._item_by_key(
                self.vissim.Net.ConflictAreas,
                conflict_area_no,
                "Conflict area",
                self.available_conflict_area_nos(),
            )
            return {
                "no": conflict_area_no,
                "link_a": str(conflict_area.AttValue("LinkA")),
                "link_b": str(conflict_area.AttValue("LinkB")),
                "status": str(conflict_area.AttValue("Status")),
            }
        except VissimObjectNotFoundError:
            raise
        except Exception as exc:
            raise RuntimeError(f"Could not read conflict area {conflict_area_no}: {exc}") from exc

    def available_conflict_area_nos(self) -> list[int]:
        return self._collection_nos(self.vissim.Net.ConflictAreas)

    def set_route_relative_flow(
        self, decision_no: int, route_no: int, rel_flow: float, interval_no: int = 1
    ) -> None:
        """Set a static routing decision's route's ``RelFlow(interval_no)``
        (COM attribute id ``RelFlow`` -- INDEXED ONLY, verified live via
        ``backend/tools/routing_decision_probe.py`` against Vissim 2026:
        ``AttValue('RelFlow')`` with no index RAISES ("The subattribute no.
        1 of type 'Time Interval' of the attribute 'RELFLOW()' is invalid:
        Sub-attribute not specified") -- there is no scalar form at all,
        unlike ``CycTm``/``Offset``. 1-based interval index, matching
        ``Volume(i)``/``Cont(i)`` (v2 unit ⑤), but with NO Cont-flag
        equivalent: a second live probe on a genuine 2-interval route (LRT
        Priority Luxembourg.LU, decision 1 route 1) confirmed every interval
        always carries an explicit stored value with nothing to "clear"
        first, so this method never needs the clear-then-set two-step
        ``set_vehicle_input`` uses.

        **Normalization semantics (the central empirical finding, verified
        live).** ``RelFlow`` is a RAW weight, not a pre-computed proportion:
        writing route 1's RelFlow from 245.0 to 745.0 on a real 3-route
        decision left routes 2 and 3's OWN stored RelFlow values completely
        UNCHANGED (139.0 and 1454.0, byte-identical before/after) -- Vissim
        does not renormalize sibling routes at write time. Each route's
        ACTUAL simulated traffic share is only computed at RUN time, as this
        route's RelFlow divided by the SUM of every route's RelFlow in the
        same decision. Writing RelFlow on a decision's ONLY route (verified
        live, Beijing decision no=9) succeeds and reads back normally like
        any other write -- there is simply nothing to redistribute against,
        so the actual split stays 100% down that one route regardless of
        the number.

        Error behavior (per the 3-point AttValue taxonomy in
        docs/phase2/00-EXECUTION-GUIDE.md / DECISIONS.md): a bogus
        ``decision_no`` and a bogus ``route_no`` (within a real decision)
        both fail CLOSED via ``ItemByKey`` ("object does not exist"),
        matching every other per-object collection lookup in this module
        (conflict areas, vehicle inputs, signal controllers) -- not
        ``Net.AttValue``'s fail-OPEN exception. Writing/reading a genuinely
        non-existent interval index on a route that HAS fewer intervals also
        fails CLOSED ("Object N not found"), matching ``Volume(i)``'s own
        per-object-indexed behavior -- not a new data point, a
        reconfirmation.
        """
        try:
            decision = self._item_by_key(
                self.vissim.Net.VehicleRoutingDecisionsStatic,
                decision_no,
                "Vehicle routing decision",
                self.available_routing_decision_nos(),
            )
            route = self._item_by_key(
                decision.VehRoutSta,
                route_no,
                "Route",
                self._collection_nos(decision.VehRoutSta),
            )
            route.SetAttValue(f"RelFlow({interval_no})", rel_flow)
        except VissimObjectNotFoundError:
            raise
        except Exception as exc:
            raise RuntimeError(
                f"Could not set decision {decision_no} route {route_no} interval {interval_no} "
                f"relative flow to {rel_flow}: {exc}"
            ) from exc

    def get_route_relative_flow(self, decision_no: int, route_no: int, interval_no: int = 1) -> float:
        """Paired getter for :meth:`set_route_relative_flow`."""
        try:
            decision = self._item_by_key(
                self.vissim.Net.VehicleRoutingDecisionsStatic,
                decision_no,
                "Vehicle routing decision",
                self.available_routing_decision_nos(),
            )
            route = self._item_by_key(
                decision.VehRoutSta,
                route_no,
                "Route",
                self._collection_nos(decision.VehRoutSta),
            )
            return float(route.AttValue(f"RelFlow({interval_no})"))
        except VissimObjectNotFoundError:
            raise
        except Exception as exc:
            raise RuntimeError(
                f"Could not read decision {decision_no} route {route_no} interval {interval_no} "
                f"relative flow: {exc}"
            ) from exc

    def available_routing_decision_nos(self) -> list[int]:
        return self._collection_nos(self.vissim.Net.VehicleRoutingDecisionsStatic)

    def get_sim_sec(self) -> float:
        try:
            value = float(self.vissim.Simulation.AttValue("SimSec"))
        except Exception:
            return 0.0
        if value < self._max_sim_sec:
            # Vissim resets SimSec to 0 after the final step (even with NumRuns=1);
            # a backwards jump is the only reliable end-of-run signal over COM.
            # One more RunSingleStep after that would silently start a new run.
            self._sim_run_ended = True
        else:
            self._max_sim_sec = value
        return value

    def get_sim_period(self) -> float:
        try:
            return float(self.vissim.Simulation.AttValue("SimPeriod"))
        except Exception:
            return 0.0

    def run_single_step(self) -> None:
        self.vissim.Simulation.RunSingleStep()

    def is_sim_finished(self) -> bool:
        if self._sim_run_ended:
            return True
        period = self.get_sim_period()
        return period > 0 and self.get_sim_sec() >= period

    def _reset_run_tracking(self) -> None:
        self._max_sim_sec = 0.0
        self._sim_run_ended = False

    def available_vehicle_input_nos(self) -> list[int]:
        return self._collection_nos(self.vissim.Net.VehicleInputs)

    def available_signal_controller_nos(self) -> list[int]:
        return self._collection_nos(self.vissim.Net.SignalControllers)

    def _item_by_key(self, collection: Any, key: int, label: str, available_keys: list[int]) -> Any:
        try:
            return collection.ItemByKey(key)
        except Exception as exc:
            available_text = _format_available_keys(available_keys)
            raise VissimObjectNotFoundError(
                f"{label} {key} does not exist in the loaded VISSIM network. "
                f"Available {label.lower()} numbers: {available_text}."
            ) from exc

    def _collection_nos(self, collection: Any) -> list[int]:
        try:
            items = list(collection.GetAll())
        except Exception:
            return []

        numbers: list[int] = []
        for item in items:
            try:
                value = item.AttValue("No")
            except Exception:
                continue
            try:
                numbers.append(int(value))
            except (TypeError, ValueError):
                continue
        return sorted(set(numbers))

    def disconnect(self) -> None:
        try:
            if self.vissim and self._owns_instance:
                self.vissim.Exit()
        except Exception:
            logger.exception("Error disconnecting from VISSIM")
        self.vissim = None
        self._owns_instance = False
        self.prog_id = None


def _same_path(a: str | Path, b: str | Path) -> bool:
    """Case-insensitive, absolute-path comparison (Windows filesystem semantics)."""
    return os.path.normcase(os.path.abspath(str(a))) == os.path.normcase(os.path.abspath(str(b)))


def _format_available_keys(values: list[int], limit: int = 20) -> str:
    if not values:
        return "none found"
    shown = values[:limit]
    suffix = f", ... (+{len(values) - limit} more)" if len(values) > limit else ""
    return ", ".join(str(value) for value in shown) + suffix
