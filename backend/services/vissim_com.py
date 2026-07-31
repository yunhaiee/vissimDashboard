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

    # -----------------------------------------------------------------
    # v2a unit ⑨: fixed-time signal-program GREEN SPLITS.
    #
    # Storage + writability verified live against Vissim 2026
    # (backend/tools/probe_signal_programs.py, Urban Intersection Beijing.CN
    # clone for FIXEDTIME + Air Taxi Vertiport clone for FIXEDTIMESIMPLE):
    #
    # - The Vissim 26.0 type library has NO signal-program interface at all
    #   (static scan of every interface name), no SignalPrograms/Progs
    #   collection exists on SignalController or Net (all candidates raise
    #   AttributeError live), and the per-SG green-window attribute ids
    #   EndGreen/EndRed — real ids, confirmed readable — are REFUSED for
    #   writing on a program-driven FIXEDTIME controller ("Attribute End of
    #   green cannot be changed", the exact CycTm pattern) and read a
    #   disconnected 0.0 there.
    # - BUT the whole program blob IS a plain COM attribute:
    #   AttValue('IntSupplyData') returns the base64 Vissig <sc> XML
    #   document verbatim, and SetAttValue('IntSupplyData', ...) is
    #   accepted. Editing the decoded XML (prog → sg → cmd begins) and
    #   loading the result was PROVEN to drive real signal behavior
    #   (SimBreakAt + SigState oracle: SG1 GREEN→RED at t=11 after moving
    #   its red command from 15 s to 8 s), Vissim does NOT validate the
    #   blob's checkSum on load and does NOT recompute it on SaveNetAs, and
    #   a declaration-less ElementTree re-serialization of the inner XML is
    #   accepted.
    # - On FIXEDTIMESIMPLE controllers EndRed (green start) / EndGreen
    #   (green end) are plain writable attributes that persist to the .inpx
    #   (endRed/endGreen) and survive a fresh reload. Vissim itself refuses
    #   a NEGATIVE value but silently ACCEPTS a value beyond the cycle time
    #   and fully-overlapping green windows across signal groups — those
    #   screens are constraints.py's job, never assumed to be Vissim's.
    #
    # display codes inside the blob: 3 = switch-to-green command, 1 =
    # switch-to-red command; begin values are MILLISECONDS.
    # -----------------------------------------------------------------

    def set_signal_green_split(
        self,
        sc_no: int,
        sg_no: int,
        green_start_s: float,
        green_end_s: float,
        program_no: int | None = None,
    ) -> dict[str, Any]:
        """Set one signal group's green window inside a fixed-time program.

        ``green_start_s`` = when the group switches to green; ``green_end_s``
        = when it switches to red (amber, if any, runs immediately before
        that point per the program's own fixed states). A wrapping window
        (start > end) is valid — real programs use it.

        Returns ``{"controller_type", "program_no"}`` (the RESOLVED program
        actually written — ``program_no=None`` targets the controller's own
        active ``ProgNo``) so callers can report exactly what was touched.
        """
        signal_controller = self._item_by_key(
            self.vissim.Net.SignalControllers,
            sc_no,
            "Signal controller",
            self.available_signal_controller_nos(),
        )
        controller_type = str(signal_controller.AttValue("Type") or "")
        resolved_program_no = program_no
        if resolved_program_no is None:
            try:
                resolved_program_no = int(signal_controller.AttValue("ProgNo"))
            except Exception:
                resolved_program_no = 1

        if controller_type == "FIXEDTIMESIMPLE":
            sg = self._signal_group_by_key(signal_controller, sc_no, sg_no)
            try:
                sg.SetAttValue("EndRed", green_start_s)
                sg.SetAttValue("EndGreen", green_end_s)
            except Exception as exc:
                raise RuntimeError(
                    f"Could not set signal controller {sc_no} signal group {sg_no} green "
                    f"window to [{green_start_s}, {green_end_s}]s: {exc}"
                ) from exc
            return {"controller_type": controller_type, "program_no": resolved_program_no}

        if controller_type == "FIXEDTIME":
            import base64 as _base64
            import xml.etree.ElementTree as _ET

            try:
                blob = signal_controller.AttValue("IntSupplyData")
                inner = _ET.fromstring(_base64.b64decode(blob).decode("utf-8"))
            except Exception as exc:
                raise RuntimeError(
                    f"Could not read signal controller {sc_no}'s internal program data "
                    f"(IntSupplyData): {exc}"
                ) from exc

            prog = self._blob_program(inner, sc_no, resolved_program_no)
            sg_el = self._blob_program_sg(prog, sc_no, resolved_program_no, sg_no)
            cmds_el = sg_el.find("cmds")
            green_cmds = [c for c in (list(cmds_el) if cmds_el is not None else []) if c.get("display") == "3"]
            red_cmds = [c for c in (list(cmds_el) if cmds_el is not None else []) if c.get("display") == "1"]
            if len(green_cmds) != 1 or len(red_cmds) != 1:
                raise RuntimeError(
                    f"Signal controller {sc_no} program {resolved_program_no} signal group "
                    f"{sg_no} has a non-simple command shape ({len(green_cmds)} green / "
                    f"{len(red_cmds)} red commands) — only programs with exactly one green "
                    "window per signal group are supported; edit this program in the Vissim "
                    "GUI instead."
                )
            green_cmds[0].set("begin", str(int(round(green_start_s * 1000))))
            red_cmds[0].set("begin", str(int(round(green_end_s * 1000))))

            new_blob = _base64.b64encode(
                _ET.tostring(inner, encoding="unicode").encode("utf-8")
            ).decode("ascii")
            try:
                signal_controller.SetAttValue("IntSupplyData", new_blob)
            except Exception as exc:
                raise RuntimeError(
                    f"Could not write signal controller {sc_no}'s updated program data "
                    f"(IntSupplyData): {exc}"
                ) from exc
            return {"controller_type": controller_type, "program_no": resolved_program_no}

        raise RuntimeError(
            f"Signal controller {sc_no} has controller type '{controller_type or 'unknown'}' — "
            "green splits are only editable on FIXEDTIME/FIXEDTIMESIMPLE controllers."
        )

    def get_signal_green_split(
        self, sc_no: int, sg_no: int, program_no: int | None = None
    ) -> dict[str, Any]:
        """Read back one signal group's green window (see the setter's
        docstring for semantics). Returns ``{"green_start_s", "green_end_s",
        "controller_type", "program_no"}``."""
        signal_controller = self._item_by_key(
            self.vissim.Net.SignalControllers,
            sc_no,
            "Signal controller",
            self.available_signal_controller_nos(),
        )
        controller_type = str(signal_controller.AttValue("Type") or "")
        resolved_program_no = program_no
        if resolved_program_no is None:
            try:
                resolved_program_no = int(signal_controller.AttValue("ProgNo"))
            except Exception:
                resolved_program_no = 1

        if controller_type == "FIXEDTIMESIMPLE":
            sg = self._signal_group_by_key(signal_controller, sc_no, sg_no)
            try:
                return {
                    "green_start_s": float(sg.AttValue("EndRed")),
                    "green_end_s": float(sg.AttValue("EndGreen")),
                    "controller_type": controller_type,
                    "program_no": resolved_program_no,
                }
            except Exception as exc:
                raise RuntimeError(
                    f"Could not read signal controller {sc_no} signal group {sg_no}'s green "
                    f"window: {exc}"
                ) from exc

        if controller_type == "FIXEDTIME":
            import base64 as _base64
            import xml.etree.ElementTree as _ET

            try:
                blob = signal_controller.AttValue("IntSupplyData")
                inner = _ET.fromstring(_base64.b64decode(blob).decode("utf-8"))
            except Exception as exc:
                raise RuntimeError(
                    f"Could not read signal controller {sc_no}'s internal program data "
                    f"(IntSupplyData): {exc}"
                ) from exc
            prog = self._blob_program(inner, sc_no, resolved_program_no)
            sg_el = self._blob_program_sg(prog, sc_no, resolved_program_no, sg_no)
            cmds_el = sg_el.find("cmds")
            green_cmds = [c for c in (list(cmds_el) if cmds_el is not None else []) if c.get("display") == "3"]
            red_cmds = [c for c in (list(cmds_el) if cmds_el is not None else []) if c.get("display") == "1"]
            if len(green_cmds) != 1 or len(red_cmds) != 1:
                raise RuntimeError(
                    f"Signal controller {sc_no} program {resolved_program_no} signal group "
                    f"{sg_no} has a non-simple command shape — no single green window to read."
                )
            return {
                "green_start_s": int(green_cmds[0].get("begin", "0")) / 1000.0,
                "green_end_s": int(red_cmds[0].get("begin", "0")) / 1000.0,
                "controller_type": controller_type,
                "program_no": resolved_program_no,
            }

        raise RuntimeError(
            f"Signal controller {sc_no} has controller type '{controller_type or 'unknown'}' — "
            "green splits are only readable on FIXEDTIME/FIXEDTIMESIMPLE controllers."
        )

    def _signal_group_by_key(self, signal_controller: Any, sc_no: int, sg_no: int) -> Any:
        """SGs.ItemByKey with a structured not-found error listing the
        controller's own available signal-group numbers. SGs.ItemByKey exists
        and fails CLOSED on a bogus key (verified live — unlike the
        vehicle-composition/des-speed-decision child collections, which have
        no ItemByKey at all)."""
        sgs = signal_controller.SGs
        available = self._collection_nos(sgs)
        return self._item_by_key(sgs, sg_no, f"Signal controller {sc_no} signal group", available)

    def _blob_program(self, inner: Any, sc_no: int, program_no: int) -> Any:
        progs_el = inner.find("progs")
        progs = list(progs_el) if progs_el is not None else []
        for prog in progs:
            if (prog.get("id") or "").strip() == str(program_no):
                return prog
        available = ", ".join((p.get("id") or "?").strip() for p in progs) or "-"
        raise VissimObjectNotFoundError(
            f"Signal controller {sc_no} has no signal program {program_no}. "
            f"Available program numbers: {available}."
        )

    def _blob_program_sg(self, prog: Any, sc_no: int, program_no: int, sg_no: int) -> Any:
        sgs_el = prog.find("sgs")
        sgs = list(sgs_el) if sgs_el is not None else []
        for sg in sgs:
            if (sg.get("sg_id") or "").strip() == str(sg_no):
                return sg
        available = ", ".join((s.get("sg_id") or "?").strip() for s in sgs) or "-"
        raise VissimObjectNotFoundError(
            f"Signal controller {sc_no} program {program_no} has no signal group {sg_no}. "
            f"Available signal group numbers: {available}."
        )

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

    # ---------------------------------------------------------------------
    # Lane closure -- v2b unit (1). COM attribute id ``BlockedVehClasses``
    # (verified empirically live against Vissim 2026,
    # ``backend/tools/probe_lane_closure.py`` on Roundabout Billings.US):
    # a comma-separated list of vehicle-class numbers (e.g. ``"10,20"``,
    # semicolon REJECTED -- "is not a valid key list"), empty string
    # ``""`` means the lane is open to every class. This is Vissim's own
    # native per-lane, per-vehicle-class closure mechanism -- attribute-level
    # over COM (a plain ``AttValue``/``SetAttValue`` pair, exactly like
    # ``set_conflict_area_status``), but genuinely STRUCTURAL in the saved
    # .inpx: a lane with a non-empty blocked set serializes with a NEW child
    # element the probe confirmed does not otherwise exist,
    # ``<lane>...<blockedVehClasses><intObjectRef key="10"/>...</lane>``
    # (one ``<intObjectRef>`` per blocked class) -- clearing it back to ``""``
    # removes that child element entirely on the next save. No "close to
    # ALL classes" wildcard was found on live COM; a full closure means the
    # caller enumerates every class number it wants blocked.
    #
    # A ``Lane`` is reached as ``Net.Links.ItemByKey(link_no).Lanes.
    # ItemByKey(lane_index)`` -- lanes have NO identity attribute of their
    # own in the .inpx (``<lane width="..."/>``, no ``no``/``id``); COM's
    # own ``Lanes`` collection keys them by the SAME 1-based ordinal
    # (``Index``) the .inpx document order already implies (verified live:
    # ``Lanes.ItemByKey(1)`` returns the lane whose own ``AttValue("Index")``
    # reads back ``1``).
    # ---------------------------------------------------------------------

    def _lane_by_index(self, link_no: int, lane_index: int) -> Any:
        link = self._item_by_key(
            self.vissim.Net.Links, link_no, "Link", self._collection_nos(self.vissim.Net.Links)
        )
        try:
            return link.Lanes.ItemByKey(lane_index)
        except Exception as exc:
            available = self.available_lane_indices(link_no)
            raise VissimObjectNotFoundError(
                f"Lane {lane_index} does not exist on link {link_no} in the loaded VISSIM "
                f"network. Available lane indices for link {link_no}: "
                f"{_format_available_keys(available)}."
            ) from exc

    def available_lane_indices(self, link_no: int) -> list[int]:
        try:
            link = self.vissim.Net.Links.ItemByKey(link_no)
            return sorted(int(lane.AttValue("Index")) for lane in link.Lanes.GetAll())
        except Exception:
            return []

    @staticmethod
    def _format_blocked_vehicle_classes(vehicle_classes: list[int]) -> str:
        return ",".join(str(int(vc)) for vc in vehicle_classes)

    @staticmethod
    def _parse_blocked_vehicle_classes(raw: Any) -> list[int]:
        text = str(raw or "").strip()
        if not text:
            return []
        return [int(token) for token in text.split(",") if token.strip()]

    def set_lane_blocked_vehicle_classes(
        self, link_no: int, lane_index: int, vehicle_classes: list[int]
    ) -> None:
        """Set the exact set of vehicle classes blocked from one lane
        (``BlockedVehClasses``) -- REPLACES whatever was blocked before,
        same "one setter, several states including the safe open default"
        shape as :meth:`set_conflict_area_status`. An empty list is the
        REOPEN case: the lane becomes open to every vehicle class again.
        """
        try:
            lane = self._lane_by_index(link_no, lane_index)
            lane.SetAttValue(
                "BlockedVehClasses", self._format_blocked_vehicle_classes(vehicle_classes)
            )
        except VissimObjectNotFoundError:
            raise
        except Exception as exc:
            raise RuntimeError(
                f"Could not set lane {lane_index} of link {link_no} BlockedVehClasses="
                f"{vehicle_classes!r}: {exc}"
            ) from exc

    def get_lane_blocked_vehicle_classes(self, link_no: int, lane_index: int) -> list[int]:
        """Paired getter for :meth:`set_lane_blocked_vehicle_classes`."""
        try:
            lane = self._lane_by_index(link_no, lane_index)
            return self._parse_blocked_vehicle_classes(lane.AttValue("BlockedVehClasses"))
        except VissimObjectNotFoundError:
            raise
        except Exception as exc:
            raise RuntimeError(
                f"Could not read lane {lane_index} of link {link_no} BlockedVehClasses: {exc}"
            ) from exc

    # ---------------------------------------------------------------------
    # Q4 unit (2): measurement-infrastructure placement -- queue counters,
    # data-collection points/measurements, travel-time sections. Unlike
    # lane closure (attribute-only) and reduced-speed areas (XML injection,
    # no COM at all), these four object types have genuine, purpose-built
    # native COM CREATE/REMOVE methods (verified live,
    # backend/tools/probe_measurement_infra.py + its _followup script,
    # against Toll Plaza Camden.US -- a FEET-display network chosen because
    # it is the only 2026 example carrying real instances of all four
    # types): ``Net.QueueCounters.AddQueueCounter(Key, Link, Pos)`` /
    # ``RemoveQueueCounter(obj)``, ``Net.DataCollectionPoints.
    # AddDataCollectionPoint(Key, Lane, Pos)`` / ``RemoveDataCollectionPoint(obj)``,
    # ``Net.DataCollectionMeasurements.AddDataCollectionMeasurement(Key)`` /
    # ``RemoveDataCollectionMeasurement(obj)`` (no points param -- membership
    # is wired separately, see :meth:`add_data_collection_measurement`), and
    # ``Net.VehicleTravelTimeMeasurements.AddVehicleTravelTimeMeasurement(
    # Key, StartLink, StartPos, EndLink, EndPos)`` / ``RemoveVehicleTravelTimeMeasurement(obj)``.
    # This is the FIRST measurement-infra-adjacent unit where COM creation
    # is the right call (preferred per the task's own decision framework:
    # native methods exist, readback is clean, less schema risk than
    # reinventing the XML shape by hand) -- contrast v2b unit (1)'s lane
    # closure (COM-write but XML-structural) and unit (2)'s reduced-speed
    # areas (pure XML injection, no COM Add exists at all).
    #
    # **Landmine 1 (pywin32 marshalling):** every ``Add*`` method's
    # Link/Lane param REQUIRES an actual COM object (``Net.Links.ItemByKey(...)``
    # / ``link.Lanes.ItemByKey(...)``), not a plain int/string token --
    # passing a raw int/str raises ``TypeError: The Python instance can not
    # be converted to a COM object`` (live-verified both ways: the SAME call
    # succeeds the instant a real object replaces the scalar).
    #
    # **Landmine 2 (the THIRD display-unit confirmation):** every ``Pos``/
    # ``StartPos``/``EndPos`` param, on BOTH the Add call and the readback
    # ``AttValue``, is in the network's OWN ``NetPara.UnitLenShort`` display
    # unit -- NOT always metres (live-verified round trip on Camden, a FEET
    # network: passing ``Pos=20.0`` to ``AddDataCollectionPoint`` saved as
    # ``pos="6.096"`` in the .inpx, i.e. exactly 20 ft; reading an existing
    # object's ``AttValue('Pos')`` back against its own known-metres XML
    # value gave a 3.280840 ratio, exactly ``1/0.3048``). The SAVED .inpx
    # attribute is always metres, matching every other length-typed COM
    # surface this codebase has already found (des-speed-decision Pos,
    # driving-behavior ax/latDist) -- :meth:`_net_unit_length`/
    # :meth:`_length_m_to_com`/:meth:`_length_com_to_m` (already used by the
    # driving-behavior block above) are reused verbatim here, never
    # reimplemented.
    #
    # **Landmine 3 (COM does NOT enforce reference integrity):** live-
    # verified (probe_measurement_infra_followup.py) that
    # ``RemoveDataCollectionPoint`` SUCCEEDS on a point a
    # ``DataCollectionMeasurement`` still references -- Vissim silently
    # leaves the measurement's own membership pointing at a now-nonexistent
    # point. This module does NOT add its own guard for that (it would be a
    # second, competing source of truth against the XML-based one) --
    # ``tools_edit.remove_data_collection_point`` checks reference
    # integrity itself, against the CURRENT working copy, via
    # ``services.inpx_structure.find_id_references`` (the SAME spine
    # every other structural removal in this codebase already uses) BEFORE
    # ever calling this module's own :meth:`remove_data_collection_point`.
    #
    # Object numbering: every ``Add*`` call here uses an EXPLICIT
    # ``max(existing) + 1`` key (mirrors :meth:`duplicate_driving_behavior`'s
    # own convention, and ``services.inpx_structure.next_id``'s identical
    # rule for XML-level structural adds) rather than relying on Vissim's
    # own auto-numbering (``Key=None`` also works, live-verified, but an
    # explicit key keeps this module's numbering behavior consistent and
    # predictable across both creation paths in this codebase).
    # ---------------------------------------------------------------------

    def available_queue_counter_nos(self) -> list[int]:
        return self._collection_nos(self.vissim.Net.QueueCounters)

    def available_data_collection_point_nos(self) -> list[int]:
        return self._collection_nos(self.vissim.Net.DataCollectionPoints)

    def available_data_collection_measurement_nos(self) -> list[int]:
        return self._collection_nos(self.vissim.Net.DataCollectionMeasurements)

    def available_travel_time_section_nos(self) -> list[int]:
        return self._collection_nos(self.vissim.Net.VehicleTravelTimeMeasurements)

    def _link_object_by_no(self, link_no: int) -> Any:
        """Resolve a real ``Link`` COM object (not just a validated number) --
        every measurement-infra ``Add*`` method's Link-shaped param needs the
        OBJECT (landmine 1 above), not the plain int every other verb in
        this module addresses links by."""
        return self._item_by_key(
            self.vissim.Net.Links, link_no, "Link", self._collection_nos(self.vissim.Net.Links)
        )

    def add_queue_counter(self, link_no: int, pos_m: float, name: str = "") -> dict[str, Any]:
        """Create a queue counter on ``link_no`` at ``pos_m`` metres from the
        link's start. Returns :meth:`get_queue_counter`'s shape for the new
        object (readback-verified: the new object's own ``No``/``Link``/
        ``Pos`` are read straight back from COM, not merely echoed)."""
        try:
            link = self._link_object_by_no(link_no)
            existing = self.available_queue_counter_nos()
            new_no = (max(existing) if existing else 0) + 1
            pos_com = self._length_m_to_com(float(pos_m), self._net_unit_length())
            counter = self.vissim.Net.QueueCounters.AddQueueCounter(new_no, link, pos_com)
            if name:
                counter.SetAttValue("Name", name)
        except VissimObjectNotFoundError:
            raise
        except Exception as exc:
            raise RuntimeError(
                f"Could not add a queue counter on link {link_no} at pos {pos_m}: {exc}"
            ) from exc
        return self.get_queue_counter(new_no)

    def remove_queue_counter(self, no: int) -> None:
        try:
            counter = self._item_by_key(
                self.vissim.Net.QueueCounters, no, "Queue counter", self.available_queue_counter_nos()
            )
            self.vissim.Net.QueueCounters.RemoveQueueCounter(counter)
        except VissimObjectNotFoundError:
            raise
        except Exception as exc:
            raise RuntimeError(f"Could not remove queue counter {no}: {exc}") from exc

    def get_queue_counter(self, no: int) -> dict[str, Any]:
        try:
            counter = self._item_by_key(
                self.vissim.Net.QueueCounters, no, "Queue counter", self.available_queue_counter_nos()
            )
            unit = self._net_unit_length()
            return {
                "no": no,
                "name": str(counter.AttValue("Name") or ""),
                "link": str(counter.AttValue("Link")),
                "pos": self._length_com_to_m(float(counter.AttValue("Pos")), unit),
            }
        except VissimObjectNotFoundError:
            raise
        except Exception as exc:
            raise RuntimeError(f"Could not read queue counter {no}: {exc}") from exc

    def add_data_collection_point(
        self, link_no: int, lane_index: int, pos_m: float, name: str = ""
    ) -> dict[str, Any]:
        """Create a data-collection point on lane ``lane_index`` of
        ``link_no`` at ``pos_m`` metres. The ``Lane`` param needs the SAME
        Lane object :meth:`_lane_by_index` already resolves for
        :meth:`set_lane_blocked_vehicle_classes` -- reused verbatim, so a
        bogus lane index fails with the identical structured error either
        verb would give."""
        try:
            lane = self._lane_by_index(link_no, lane_index)
            existing = self.available_data_collection_point_nos()
            new_no = (max(existing) if existing else 0) + 1
            pos_com = self._length_m_to_com(float(pos_m), self._net_unit_length())
            point = self.vissim.Net.DataCollectionPoints.AddDataCollectionPoint(new_no, lane, pos_com)
            if name:
                point.SetAttValue("Name", name)
        except VissimObjectNotFoundError:
            raise
        except Exception as exc:
            raise RuntimeError(
                f"Could not add a data collection point on link {link_no} lane {lane_index} at "
                f"pos {pos_m}: {exc}"
            ) from exc
        return self.get_data_collection_point(new_no)

    def remove_data_collection_point(self, no: int) -> None:
        """Removes the point unconditionally -- Vissim's own COM raises no
        objection even when a measurement still references it (landmine 3
        above). Callers MUST check reference integrity themselves before
        calling this (``tools_edit.remove_data_collection_point`` does, via
        ``services.inpx_structure.find_id_references``)."""
        try:
            point = self._item_by_key(
                self.vissim.Net.DataCollectionPoints,
                no,
                "Data collection point",
                self.available_data_collection_point_nos(),
            )
            self.vissim.Net.DataCollectionPoints.RemoveDataCollectionPoint(point)
        except VissimObjectNotFoundError:
            raise
        except Exception as exc:
            raise RuntimeError(f"Could not remove data collection point {no}: {exc}") from exc

    def get_data_collection_point(self, no: int) -> dict[str, Any]:
        try:
            point = self._item_by_key(
                self.vissim.Net.DataCollectionPoints,
                no,
                "Data collection point",
                self.available_data_collection_point_nos(),
            )
            unit = self._net_unit_length()
            # AttValue('Lane') returns the hyphenated "<link>-<lane>" COM form
            # (the SAME landmine des-speed-decision/priority-rule Lane reads
            # already documented) -- split defensively rather than trusting
            # a fixed separator count.
            lane_raw = str(point.AttValue("Lane") or "")
            link_part, _, lane_part = lane_raw.partition("-")
            return {
                "no": no,
                "name": str(point.AttValue("Name") or ""),
                "link": link_part,
                "lane_index": int(lane_part) if lane_part.isdigit() else None,
                "pos": self._length_com_to_m(float(point.AttValue("Pos")), unit),
            }
        except VissimObjectNotFoundError:
            raise
        except Exception as exc:
            raise RuntimeError(f"Could not read data collection point {no}: {exc}") from exc

    def add_data_collection_measurement(self, point_nos: list[int], name: str = "") -> dict[str, Any]:
        """Create a data-collection measurement grouping ``point_nos`` --
        the object that actually produces countable results (a lone point
        is a placement marker only, live-verified: Vissim's evaluation UI
        has nothing to report against an unmeasured point). ``AddDataCollectionMeasurement``
        takes no points param; membership is wired via a SECOND call,
        ``SetAttValue('DataCollectionPoints', "<no>,<no>,...")`` -- a
        comma-separated key list, live-verified against a real 2-point
        group (probe_measurement_infra_followup.py) and matching the exact
        ``<intObjectRef>`` shape every real Vissim export already uses for
        this element.

        Every entry of ``point_nos`` is existence-checked (via
        ``_item_by_key``) BEFORE the measurement is created, so a bogus
        point number fails with a structured error naming it rather than a
        raw COM "not a valid key list" string blaming the whole list.
        """
        try:
            if not point_nos:
                raise ValueError("add_data_collection_measurement: point_nos must be non-empty")
            available_points = self.available_data_collection_point_nos()
            for point_no in point_nos:
                self._item_by_key(
                    self.vissim.Net.DataCollectionPoints, point_no, "Data collection point", available_points
                )
            existing = self.available_data_collection_measurement_nos()
            new_no = (max(existing) if existing else 0) + 1
            measurement = self.vissim.Net.DataCollectionMeasurements.AddDataCollectionMeasurement(new_no)
            measurement.SetAttValue(
                "DataCollectionPoints", ",".join(str(int(p)) for p in point_nos)
            )
            if name:
                measurement.SetAttValue("Name", name)
        except VissimObjectNotFoundError:
            raise
        except Exception as exc:
            raise RuntimeError(
                f"Could not add a data collection measurement grouping points {point_nos!r}: {exc}"
            ) from exc
        return self.get_data_collection_measurement(new_no)

    def remove_data_collection_measurement(self, no: int) -> None:
        try:
            measurement = self._item_by_key(
                self.vissim.Net.DataCollectionMeasurements,
                no,
                "Data collection measurement",
                self.available_data_collection_measurement_nos(),
            )
            self.vissim.Net.DataCollectionMeasurements.RemoveDataCollectionMeasurement(measurement)
        except VissimObjectNotFoundError:
            raise
        except Exception as exc:
            raise RuntimeError(f"Could not remove data collection measurement {no}: {exc}") from exc

    def get_data_collection_measurement(self, no: int) -> dict[str, Any]:
        try:
            measurement = self._item_by_key(
                self.vissim.Net.DataCollectionMeasurements,
                no,
                "Data collection measurement",
                self.available_data_collection_measurement_nos(),
            )
            point_nos = sorted(int(p.AttValue("No")) for p in measurement.DataCollectionPoints.GetAll())
            return {
                "no": no,
                "name": str(measurement.AttValue("Name") or ""),
                "point_nos": point_nos,
            }
        except VissimObjectNotFoundError:
            raise
        except Exception as exc:
            raise RuntimeError(f"Could not read data collection measurement {no}: {exc}") from exc

    def add_travel_time_section(
        self, name: str, from_link: int, from_pos_m: float, to_link: int, to_pos_m: float
    ) -> dict[str, Any]:
        """Create a travel-time measurement section from ``(from_link,
        from_pos_m)`` to ``(to_link, to_pos_m)`` -- ``AddVehicleTravelTimeMeasurement``
        takes flat ``StartLink``/``StartPos``/``EndLink``/``EndPos`` params
        even though the SAVED .inpx nests them as ``<start link="" pos=""/>``/
        ``<end .../>`` children; Vissim itself handles the (de)nesting."""
        try:
            start_link = self._link_object_by_no(from_link)
            end_link = self._link_object_by_no(to_link)
            existing = self.available_travel_time_section_nos()
            new_no = (max(existing) if existing else 0) + 1
            unit = self._net_unit_length()
            start_pos_com = self._length_m_to_com(float(from_pos_m), unit)
            end_pos_com = self._length_m_to_com(float(to_pos_m), unit)
            section = self.vissim.Net.VehicleTravelTimeMeasurements.AddVehicleTravelTimeMeasurement(
                new_no, start_link, start_pos_com, end_link, end_pos_com
            )
            if name:
                section.SetAttValue("Name", name)
        except VissimObjectNotFoundError:
            raise
        except Exception as exc:
            raise RuntimeError(
                f"Could not add a travel time section from link {from_link}@{from_pos_m} to link "
                f"{to_link}@{to_pos_m}: {exc}"
            ) from exc
        return self.get_travel_time_section(new_no)

    def remove_travel_time_section(self, no: int) -> None:
        try:
            section = self._item_by_key(
                self.vissim.Net.VehicleTravelTimeMeasurements,
                no,
                "Travel time section",
                self.available_travel_time_section_nos(),
            )
            self.vissim.Net.VehicleTravelTimeMeasurements.RemoveVehicleTravelTimeMeasurement(section)
        except VissimObjectNotFoundError:
            raise
        except Exception as exc:
            raise RuntimeError(f"Could not remove travel time section {no}: {exc}") from exc

    def get_travel_time_section(self, no: int) -> dict[str, Any]:
        try:
            section = self._item_by_key(
                self.vissim.Net.VehicleTravelTimeMeasurements,
                no,
                "Travel time section",
                self.available_travel_time_section_nos(),
            )
            unit = self._net_unit_length()
            return {
                "no": no,
                "name": str(section.AttValue("Name") or ""),
                "from_link": str(section.AttValue("StartLink")),
                "from_pos": self._length_com_to_m(float(section.AttValue("StartPos")), unit),
                "to_link": str(section.AttValue("EndLink")),
                "to_pos": self._length_com_to_m(float(section.AttValue("EndPos")), unit),
            }
        except VissimObjectNotFoundError:
            raise
        except Exception as exc:
            raise RuntimeError(f"Could not read travel time section {no}: {exc}") from exc

    # ---------------------------------------------------------------------
    # Q4 unit (4): signal-head + detector placement -- genesis milestone
    # (b)'s "hands" (manual placement now, policy later). COM surfaces
    # verified live (backend/tools/probe_signal_heads_detectors.py, run
    # against Urban Intersection Beijing.CN -- METERS display -- and Toll
    # Plaza Camden.US -- FEET display, for the unit-length round trip):
    # ``Net.SignalHeads.AddSignalHead(Key, Lane, Pos)`` / ``RemoveSignalHead
    # (obj)`` and ``Net.Detectors.AddDetector(Key, Lane, Pos)`` /
    # ``RemoveDetector(obj)`` -- the SAME "purpose-built native Add/Remove,
    # COM-create over XML injection" shape Q4 unit (2)'s measurement infra
    # already established, with the SAME two landmines:
    #
    # **Landmine 1 (pywin32 marshalling, re-confirmed):** both Add methods'
    # ``Lane`` param needs the REAL ``Net.Links...Lanes.ItemByKey(...)``
    # object -- passing the ``"<link> <lane>"`` string token Vissim's own
    # saved XML uses raises ``TypeError: The Python instance can not be
    # converted to a COM object`` (live-verified both ways). Reused verbatim:
    # :meth:`_lane_by_index` (the SAME helper ``set_lane_blocked_vehicle_
    # classes``/``add_data_collection_point`` already resolve a Lane through).
    #
    # **Landmine 2 (the FOURTH display-unit confirmation):** ``Pos`` on BOTH
    # Add calls AND ``Length`` on a Detector are network-display-unit-typed --
    # live-verified on Camden (FEET): passing ``Pos=15.0``/``Length=4.5``
    # saved as ``pos="4.572"``/``length="1.3716000000000002"`` (exactly
    # 15.0/4.5 * 0.3048). Reused verbatim: :meth:`_length_m_to_com`/
    # :meth:`_length_com_to_m`.
    #
    # **The signal-head reference question (new to this unit).** A signal
    # head's saved XML carries ONE composite attribute, ``sg="<sc_no>
    # <sg_no>"`` -- there is no separate ``sc``/controller attribute at all
    # (a signal group's own identity is already scoped to its controller,
    # matching :meth:`_signal_group_by_key`'s existing ``SGs.ItemByKey``
    # precedent). Live-verified this does NOT accept the composite STRING on
    # write (``SetAttValue('SG', '1 22')`` -> "is not a valid key") nor
    # exists under any other attribute id tried (``SigGroup``/
    # ``SignalGroup`` both raise "Could not find attribute") -- the ONLY
    # working write is ``SetAttValue('SG', <SG COM object>)``, resolved via
    # the SAME :meth:`_signal_group_by_key` helper ``set_signal_green_split``
    # already uses. Readback (``AttValue('SG')``) returns the HYPHENATED
    # ``"1-22"`` COM form -- the SAME hyphen-vs-space landmine
    # ``get_data_collection_point``'s own ``Lane`` readback already
    # documents (``AttValue('Lane')`` here also returns ``"10023-1"``,
    # hyphenated) -- split via ``.partition("-")``, never a literal-space
    # split, on any COM readback.
    #
    # **The detector controller-wiring question (new to this unit).**
    # Resolves the pre-existing catalog note ("port numbers bind detectors
    # to external controllers", constraints.py's #35 docstring) into an
    # exact schema: TWO flat, independently-writable scalar attributes,
    # ``SC`` (the owning signal controller's plain ``no`` -- an INT, no COM
    # object needed, unlike the signal head's own ``SG``) and ``PortNo``
    # (the detector's port/channel number on that controller). Every real
    # detector in every 2026 example network that has one carries BOTH
    # (0/826 missing either, static scan) -- there is no "unbound" detector
    # in the wild, even on controllers with zero vehicle signal heads at all
    # (e.g. Air Taxi Vertiport's railway-crossing/gate controllers still
    # populate ``sc``) -- but neither is required by COM itself, so both stay
    # optional params here (the caller-facing tool validates them as an
    # all-or-nothing pair; see ``tools_edit.add_detector``).
    #
    # **Reference-map finding (probe [1b.9]):** nothing else in the schema
    # references a signal head or detector by its own ``no`` (0/2240+
    # coincidental small-integer collisions on either scan, all traced to
    # unrelated elements) -- both are LEAF objects like queue counters/
    # travel-time sections, so neither remove method below needs a
    # reference-integrity check of its own. The OTHER direction (signal
    # heads/detectors THEMSELVES reference a lane, and must not be left
    # dangling when a lane/connector is removed) is handled in
    # ``services.inpx_structure.LANE_TOKEN_REFERENCE_SITES``, extended this
    # unit -- see that module's own comment.
    # ---------------------------------------------------------------------

    def available_signal_head_nos(self) -> list[int]:
        return self._collection_nos(self.vissim.Net.SignalHeads)

    def available_detector_nos(self) -> list[int]:
        return self._collection_nos(self.vissim.Net.Detectors)

    def add_signal_head(
        self, link_no: int, lane_index: int, pos_m: float, sc_no: int, sg_no: int, name: str = ""
    ) -> dict[str, Any]:
        """Create a signal head on lane ``lane_index`` of ``link_no`` at
        ``pos_m`` metres, wired to signal group ``sg_no`` of controller
        ``sc_no``. Both the lane and the signal group are resolved to real
        COM objects before the Add call (landmine 1 above); a bogus
        ``sc_no``/``sg_no`` fails CLOSED via :meth:`_item_by_key`/
        :meth:`_signal_group_by_key`'s own structured errors, naming the
        controller's real available signal-group numbers."""
        try:
            lane = self._lane_by_index(link_no, lane_index)
            signal_controller = self._item_by_key(
                self.vissim.Net.SignalControllers,
                sc_no,
                "Signal controller",
                self.available_signal_controller_nos(),
            )
            sg = self._signal_group_by_key(signal_controller, sc_no, sg_no)
            existing = self.available_signal_head_nos()
            new_no = (max(existing) if existing else 0) + 1
            pos_com = self._length_m_to_com(float(pos_m), self._net_unit_length())
            head = self.vissim.Net.SignalHeads.AddSignalHead(new_no, lane, pos_com)
            head.SetAttValue("SG", sg)
            if name:
                head.SetAttValue("Name", name)
        except VissimObjectNotFoundError:
            raise
        except Exception as exc:
            raise RuntimeError(
                f"Could not add a signal head on link {link_no} lane {lane_index} at pos {pos_m} "
                f"wired to SC {sc_no} SG {sg_no}: {exc}"
            ) from exc
        return self.get_signal_head(new_no)

    def remove_signal_head(self, no: int) -> None:
        try:
            head = self._item_by_key(
                self.vissim.Net.SignalHeads, no, "Signal head", self.available_signal_head_nos()
            )
            self.vissim.Net.SignalHeads.RemoveSignalHead(head)
        except VissimObjectNotFoundError:
            raise
        except Exception as exc:
            raise RuntimeError(f"Could not remove signal head {no}: {exc}") from exc

    def get_signal_head(self, no: int) -> dict[str, Any]:
        try:
            head = self._item_by_key(
                self.vissim.Net.SignalHeads, no, "Signal head", self.available_signal_head_nos()
            )
            unit = self._net_unit_length()
            # Lane/SG both read back HYPHENATED ("10023-1"/"1-22") -- landmine
            # 2's own comment above -- split on the FIRST "-" only (link/sc
            # numbers never contain one).
            lane_link, _, lane_idx = str(head.AttValue("Lane") or "").partition("-")
            sg_sc, _, sg_no = str(head.AttValue("SG") or "").partition("-")
            return {
                "no": no,
                "name": str(head.AttValue("Name") or ""),
                "link": lane_link,
                "lane_index": int(lane_idx) if lane_idx.isdigit() else None,
                "pos": self._length_com_to_m(float(head.AttValue("Pos")), unit),
                "sc": sg_sc,
                "sg": sg_no,
            }
        except VissimObjectNotFoundError:
            raise
        except Exception as exc:
            raise RuntimeError(f"Could not read signal head {no}: {exc}") from exc

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
        """Create a detector on lane ``lane_index`` of ``link_no`` at
        ``pos_m`` metres, ``length_m`` long (MRWA Appendix A default 4.5 m --
        see ``tools_edit.add_detector``'s own docstring for the citation).
        ``sc_no``/``port_no`` wire the detector to an external controller's
        port (both plain scalars, no COM object needed for either -- unlike
        the signal head's own ``SG``) -- an all-or-nothing pair enforced by
        the caller (``tools_edit.AddDetectorParams``), not this method. A
        bogus ``sc_no`` fails CLOSED via :meth:`_item_by_key`'s own
        structured error; ``port_no`` has no existence check anywhere in
        Vissim's own object model (it is just an integer field inside the
        controller's own opaque wiring, not a queryable collection) so an
        unused/duplicate port is accepted silently, matching the guardrail
        doctrine's "Vissim almost never refuses a bad edit" thesis -- not a
        gap this method can close.

        **Live-caught landmine (backend/tools/signal_heads_detectors_live_
        check.py, Phase A):** when ``sc_no``/``port_no`` are BOTH ``None``
        (the caller never asked to wire a controller), Vissim's own
        ``AddDetector`` does NOT leave the saved ``sc``/``portNo``
        attributes blank -- it silently assigns its own default (observed
        live on Urban Intersection Beijing.CN: ``SC=1``/``PortNo=1``, the
        network's first controller). ``tools_edit.add_detector`` deliberately
        does NOT compare against this surprise value (its own
        written_value/readback_value only ever include ``sc``/``port_no``
        when the caller actually supplied them) -- see that tool's own
        comment for why a caller-omitted field must never become an
        unverifiable claim."""
        try:
            lane = self._lane_by_index(link_no, lane_index)
            existing = self.available_detector_nos()
            new_no = (max(existing) if existing else 0) + 1
            unit = self._net_unit_length()
            pos_com = self._length_m_to_com(float(pos_m), unit)
            detector = self.vissim.Net.Detectors.AddDetector(new_no, lane, pos_com)
            detector.SetAttValue("Length", self._length_m_to_com(float(length_m), unit))
            if sc_no is not None:
                self._item_by_key(
                    self.vissim.Net.SignalControllers,
                    sc_no,
                    "Signal controller",
                    self.available_signal_controller_nos(),
                )
                detector.SetAttValue("SC", sc_no)
            if port_no is not None:
                detector.SetAttValue("PortNo", port_no)
            if name:
                detector.SetAttValue("Name", name)
        except VissimObjectNotFoundError:
            raise
        except Exception as exc:
            raise RuntimeError(
                f"Could not add a detector on link {link_no} lane {lane_index} at pos {pos_m}: {exc}"
            ) from exc
        return self.get_detector(new_no)

    def remove_detector(self, no: int) -> None:
        try:
            detector = self._item_by_key(
                self.vissim.Net.Detectors, no, "Detector", self.available_detector_nos()
            )
            self.vissim.Net.Detectors.RemoveDetector(detector)
        except VissimObjectNotFoundError:
            raise
        except Exception as exc:
            raise RuntimeError(f"Could not remove detector {no}: {exc}") from exc

    def get_detector(self, no: int) -> dict[str, Any]:
        try:
            detector = self._item_by_key(
                self.vissim.Net.Detectors, no, "Detector", self.available_detector_nos()
            )
            unit = self._net_unit_length()
            lane_link, _, lane_idx = str(detector.AttValue("Lane") or "").partition("-")
            sc_raw = detector.AttValue("SC")
            port_raw = detector.AttValue("PortNo")
            sc_str = str(sc_raw) if sc_raw not in (None, "") else ""
            try:
                port_int = int(port_raw) if port_raw not in (None, "") else None
            except (TypeError, ValueError):
                port_int = None
            return {
                "no": no,
                "name": str(detector.AttValue("Name") or ""),
                "link": lane_link,
                "lane_index": int(lane_idx) if lane_idx.isdigit() else None,
                "pos": self._length_com_to_m(float(detector.AttValue("Pos")), unit),
                "length": self._length_com_to_m(float(detector.AttValue("Length")), unit),
                "sc": sc_str,
                "port_no": port_int,
            }
        except VissimObjectNotFoundError:
            raise
        except Exception as exc:
            raise RuntimeError(f"Could not read detector {no}: {exc}") from exc

    # ---------------------------------------------------------------------
    # Q4 unit (5): add/remove links & connectors -- the primitive verbs
    # genesis geometry-synthesis (milestone 2.3) stands on. Genuine COM
    # CREATE/REMOVE, discovered by static type-lib scan
    # (backend/tools/probe_add_links_connectors.py [1a]) then live-verified
    # (same probe [1b] + backend/tools/probe_add_connector_pos_units.py) --
    # CONTRARY to Q4 unit (3)'s own "no native COM Add/Remove for a plain
    # link or connector" note (that unit's own probe never scanned
    # ILinkContainer itself, only the Lane-container/closure-shaped surface
    # its own task needed): ``ILinkContainer`` genuinely exposes
    # ``AddLink(Key, WktLinestring, LaneWidths)``, ``AddConnector(Key,
    # FromLane, FromPos, ToLane, ToPos, NumberOfLanes, WktLinestring)``, and
    # ``RemoveLink(Link)`` (no separate ``RemoveConnector`` -- a connector IS
    # a ``Links`` entry, removed the same way, matching unit (3)'s own "a
    # connector is a link" finding).
    #
    # **Landmine 1 (coordinate space):** a link's ``WktLinestring`` x/y
    # (``"LINESTRING (x1 y1, x2 y2, ...)"``) are ALWAYS in the network's own
    # base metric coordinate space -- live-verified round-tripped IDENTICAL
    # on both a METERS-display network (Urban Intersection Beijing.CN) and a
    # FEET-display network (Toll Plaza Camden.US): requesting ``x=1000``
    # saved as ``x="1000"`` in both, never scaled by ``UnitLenShort``. This
    # is the ONE length-shaped COM param in this whole codebase that is
    # NEVER display-unit-converted -- every other Pos/Length param IS (see
    # landmine 2). Readback (``link.LinkPolyPts.GetAll()``, each point's
    # ``AttValue('X')``/``('Y')``) mirrors the input space (verified on the
    # METERS network; inferred, not independently re-verified on a FEET
    # network's COM readback specifically, on the strength of the SAVED FILE
    # already being confirmed raw metres on both).
    #
    # **Landmine 2 (display-unit scaling, the FIFTH confirmation in this
    # codebase):** ``AddLink``'s ``LaneWidths`` and ``AddConnector``'s
    # ``FromPos``/``ToPos`` ARE display-unit-scaled, like every other
    # Pos/Length COM param already found (queue counter/DCP/travel-time
    # Pos, signal head Pos, detector Pos/Length) -- live-verified on Camden
    # (FEET): a caller-requested ``LaneWidths=[3.5]`` (meant as 3.5 m) saved
    # as ``width="1.0668"`` (exactly 3.5 FEET) when passed RAW, and an
    # ``AddConnector`` ``FromPos=100.0`` passed RAW against a link of known
    # 100 m length saved as ``pos="30.48"`` (exactly 100 FEET converted to
    # metres) -- both confirm the SAME ``_length_m_to_com``/
    # ``_length_com_to_m`` conversion this module already uses everywhere
    # else is required here too, and is NOT needed for the WKT x/y
    # themselves (landmine 1).
    #
    # **Landmine 3 (does AddLink create lanes atomically?):** YES -- unit
    # (1)'s own ``AddLane``/``RemoveLane`` proof pair (XML-level) has a live
    # COM twin (``ILaneContainer.AddLane(Key, Width)``/``RemoveLane(Lane)``,
    # confirmed in the type-lib scan and live), but ``AddLink``'s own
    # ``LaneWidths`` array creates every lane in the SAME call -- live-
    # verified: ``AddLink(No, wkt, [3.5, 3.5])`` returns a link whose
    # ``Lanes.GetAll()`` already has 2 entries, no follow-up ``AddLane``
    # needed. This module always uses the atomic path (never AddLink-then-
    # AddLane) -- calling ``Lanes.AddLane`` AFTER creation was live-verified
    # to RECOMPUTE the link's own visual centerline (the saved geometry's own
    # y-coordinate shifted by exactly half the new lane's width), a
    # surprising side effect this module avoids entirely by asking for the
    # right lane count up front.
    #
    # **Landmine 4 (AddConnector's lane mapping):** ``FromLane``/``ToLane``
    # are single anchor Lane COM objects (the SAME ``_lane_by_index``-shaped
    # resolution every other Add* method in this codebase already needs, not
    # a bare int/link object -- live-verified: passing a Link object or a
    # bare int both fail), and ``NumberOfLanes`` maps that many lanes
    # CONSECUTIVELY upward from each anchor independently -- live-verified
    # with a genuine lane-SHIFT case (from_link lanes [2,3] -> to_link lanes
    # [1,2], anchors ``Lanes.ItemByKey(2)``/``ItemByKey(1)``,
    # ``NumberOfLanes=2``): the saved connector's own per-lane
    # ``fromLanes``/``toLanes`` mapping matched exactly. ``WktLinestring`` is
    # NOT optional despite the type lib's ``defaultNamedNotOptArg`` label
    # (omitting it raises "Parameter not optional"; passing ``None`` raises
    # "is not a valid wkt linestring definition") -- this module always
    # supplies a real, if approximate, guide line (:meth:`_connector_guide_
    # wkt`); Vissim itself SNAPS/EXTENDS the saved geometry to the real
    # resolved from/to positions regardless of what guide line is supplied
    # (live-verified: an intentionally wrong guide line still produced a
    # connector whose saved ``fromLinkEndPt``/``toLinkEndPt`` matched the
    # REQUESTED ``FromPos``/``ToPos``, with Vissim's own extra interpolated
    # points in between) -- so the guide line's own precision only affects
    # curvature/intermediate-point placement, never the connector's real
    # endpoints. This is this unit's own straight-line-fidelity scope cut in
    # action: the guide is deliberately the simplest possible 2-point line
    # (this module's own last point of ``from_link`` to the first point of
    # ``to_link``), not an attempt at spline/curvature fidelity.
    #
    # **Landmine 5 (Vissim does not refuse a degenerate zero-length link, or
    # enforce reference integrity on RemoveLink):** live-verified twice, the
    # SAME "Vissim almost never refuses a bad edit" thesis this whole
    # validation layer exists to counter -- (a) ``AddLink(No, "LINESTRING (x
    # y, x y)", [3.5])`` (duplicate points) SUCCEEDS silently, creating a
    # ``Length2D=0.0`` link (a single-point WKT IS refused natively, with a
    # clear "must be at least 2" error -- only the duplicate-point,
    # zero-LENGTH case is silent); ``services.agent.tools_edit.AddLinkParams``
    # refuses both client-side before this method is ever called. (b)
    # ``RemoveLink`` on a link whose lane still carries another object (a
    # data-collection point, in the live test) SUCCEEDS silently, leaving a
    # dangling reference -- this module does NOT guard against that itself
    # (it would be a second, competing source of truth against the XML-based
    # one); ``tools_edit.remove_link`` checks reference integrity itself,
    # against the CURRENT working copy, via ``services.inpx_structure.
    # find_id_references``/``find_lane_token_references`` (the SAME spine
    # ``remove_connector``/``remove_data_collection_point`` already use)
    # BEFORE ever calling this module's own :meth:`remove_link`.
    #
    # Object numbering: ``max(existing) + 1`` against ``Net.Links`` (shared
    # between plain links AND connectors -- ONE numbering space, matching
    # unit (3)'s own "a connector is a links entry" finding), the same
    # explicit-key convention every other ``Add*`` method in this module
    # already follows.
    # ---------------------------------------------------------------------

    def available_link_nos(self) -> list[int]:
        return self._collection_nos(self.vissim.Net.Links)

    @staticmethod
    def _wkt_linestring(points_xy: list[tuple[float, float]]) -> str:
        coords = ", ".join(f"{x} {y}" for x, y in points_xy)
        return f"LINESTRING ({coords})"

    def add_link(
        self,
        points_xy: list[tuple[float, float]],
        lane_count: int,
        behavior_type: int | None = None,
        display_type: int | None = None,
        name: str = "",
    ) -> dict[str, Any]:
        """Create a straight-line-fidelity link through ``points_xy`` (raw
        metres -- landmine 1 above) with ``lane_count`` lanes of the default
        3.5 m width (created atomically via ``LaneWidths`` -- landmine 3;
        NOT display-unit-scaled at this call site, since ``_length_m_to_com``
        is applied per-width below). Returns :meth:`get_link`'s shape for the
        new object (readback-verified: the new object's own points/lane
        count/length are read straight back from COM, not merely echoed)."""
        try:
            wkt = self._wkt_linestring(points_xy)
            unit = self._net_unit_length()
            lane_widths_com = [self._length_m_to_com(3.5, unit) for _ in range(lane_count)]
            existing = self.available_link_nos()
            new_no = (max(existing) if existing else 0) + 1
            link = self.vissim.Net.Links.AddLink(new_no, wkt, lane_widths_com)
            if behavior_type is not None:
                link.SetAttValue("LinkBehavType", behavior_type)
            if display_type is not None:
                link.SetAttValue("DisplayType", display_type)
            if name:
                link.SetAttValue("Name", name)
        except VissimObjectNotFoundError:
            raise
        except Exception as exc:
            raise RuntimeError(f"Could not add a link with points {points_xy!r}: {exc}") from exc
        return self.get_link(new_no)

    def remove_link(self, no: int) -> None:
        """Removes the link/connector unconditionally -- Vissim's own COM
        raises no objection even when one of its lanes is still referenced
        by another object (landmine 5b above). Callers MUST check reference
        integrity themselves before calling this
        (``tools_edit.remove_link`` does, via ``services.inpx_structure.
        find_id_references``/``find_lane_token_references``)."""
        try:
            link = self._item_by_key(self.vissim.Net.Links, no, "Link", self.available_link_nos())
            self.vissim.Net.Links.RemoveLink(link)
        except VissimObjectNotFoundError:
            raise
        except Exception as exc:
            raise RuntimeError(f"Could not remove link {no}: {exc}") from exc

    def get_link(self, no: int) -> dict[str, Any]:
        """{"no", "name", "points" (list of raw-metre (x, y) tuples),
        "lanes", "length" (metres), "is_connector", "behavior_type",
        "display_type"} for any link, PLUS (when ``is_connector``)
        "from_link"/"to_link"/"from_pos"/"to_pos" (metres) and "from_lanes"/
        "to_lanes" (sorted lane-index lists, read from each of the
        connector's own lanes' ``FromLanes``/``ToLanes`` nested collection --
        landmine 4 above)."""
        try:
            link = self._item_by_key(self.vissim.Net.Links, no, "Link", self.available_link_nos())
            unit = self._net_unit_length()
            points = [
                (float(p.AttValue("X")), float(p.AttValue("Y"))) for p in link.LinkPolyPts.GetAll()
            ]
            is_connector = bool(int(link.AttValue("IsConn") or 0))
            behavior_raw = link.AttValue("LinkBehavType")
            display_raw = link.AttValue("DisplayType")
            result: dict[str, Any] = {
                "no": no,
                "name": str(link.AttValue("Name") or ""),
                "points": points,
                "lanes": int(link.AttValue("NumLanes")),
                "length": self._length_com_to_m(float(link.AttValue("Length2D")), unit),
                "is_connector": is_connector,
                "behavior_type": str(behavior_raw) if behavior_raw not in (None, "") else None,
                "display_type": str(display_raw) if display_raw not in (None, "") else None,
            }
            if is_connector:
                result["from_link"] = str(link.AttValue("FromLink"))
                result["to_link"] = str(link.AttValue("ToLink"))
                result["from_pos"] = self._length_com_to_m(float(link.AttValue("FromPos")), unit)
                result["to_pos"] = self._length_com_to_m(float(link.AttValue("ToPos")), unit)
                from_lanes: list[int] = []
                to_lanes: list[int] = []
                for lane in link.Lanes.GetAll():
                    fl = list(lane.FromLanes.GetAll())
                    tl = list(lane.ToLanes.GetAll())
                    if fl:
                        from_lanes.append(int(fl[0].AttValue("Index")))
                    if tl:
                        to_lanes.append(int(tl[0].AttValue("Index")))
                result["from_lanes"] = sorted(from_lanes)
                result["to_lanes"] = sorted(to_lanes)
            return result
        except VissimObjectNotFoundError:
            raise
        except Exception as exc:
            raise RuntimeError(f"Could not read link {no}: {exc}") from exc

    def _connector_guide_wkt(self, from_link_no: int, to_link_no: int) -> str:
        """Best-effort straight-line WKT guide (landmine 4 above: Vissim
        snaps/extends the real saved geometry to the resolved FromPos/ToPos
        regardless) from ``from_link``'s LAST polyline point to
        ``to_link``'s FIRST -- the common "connect the end of A to the start
        of B" case; a caller connecting mid-link needs no more precision
        than this per this unit's own straight-line-fidelity scope. A
        degenerate (identical start/end) guide is nudged by a tiny epsilon,
        since ``AddLink`` itself was live-verified to silently accept (never
        refuse) a duplicate-point WKT elsewhere (landmine 5a) -- this avoids
        relying on that being equally safe for ``AddConnector``, untested."""
        from_link_obj = self._link_object_by_no(from_link_no)
        to_link_obj = self._link_object_by_no(to_link_no)
        from_pts = list(from_link_obj.LinkPolyPts.GetAll())
        to_pts = list(to_link_obj.LinkPolyPts.GetAll())
        start = (float(from_pts[-1].AttValue("X")), float(from_pts[-1].AttValue("Y"))) if from_pts else (0.0, 0.0)
        end = (float(to_pts[0].AttValue("X")), float(to_pts[0].AttValue("Y"))) if to_pts else (0.0, 0.0)
        if start == end:
            end = (end[0] + 0.01, end[1])
        return self._wkt_linestring([start, end])

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
        """Create a simple connector from ``from_link_no``'s
        ``from_lane_indices`` at ``from_pos_m`` metres to ``to_link_no``'s
        ``to_lane_indices`` at ``to_pos_m`` metres (COM ``AddConnector``,
        anchored at each list's own FIRST index -- landmine 4 above;
        ``tools_edit.AddConnectorParams`` validates both lists are
        contiguous ascending runs of equal length BEFORE this is ever
        called, matching the primitive's own "one anchor + a lane count"
        shape). Returns :meth:`get_link`'s shape for the new object."""
        try:
            from_lane = self._lane_by_index(from_link_no, from_lane_indices[0])
            to_lane = self._lane_by_index(to_link_no, to_lane_indices[0])
            lane_count = len(from_lane_indices)
            unit = self._net_unit_length()
            from_pos_com = self._length_m_to_com(float(from_pos_m), unit)
            to_pos_com = self._length_m_to_com(float(to_pos_m), unit)
            wkt = self._connector_guide_wkt(from_link_no, to_link_no)
            existing = self.available_link_nos()
            new_no = (max(existing) if existing else 0) + 1
            connector = self.vissim.Net.Links.AddConnector(
                new_no, from_lane, from_pos_com, to_lane, to_pos_com, lane_count, wkt
            )
            if name:
                connector.SetAttValue("Name", name)
        except VissimObjectNotFoundError:
            raise
        except Exception as exc:
            raise RuntimeError(
                f"Could not add a connector from link {from_link_no} lanes {from_lane_indices}@"
                f"{from_pos_m} to link {to_link_no} lanes {to_lane_indices}@{to_pos_m}: {exc}"
            ) from exc
        return self.get_link(new_no)

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

    def set_vehicle_composition_share(
        self, composition_no: int, veh_type_no: int, rel_flow: float
    ) -> None:
        """Set a vehicle composition's per-vehicle-type ``RelFlow`` (COM
        attribute id ``RelFlow`` on the entry -- SCALAR ONLY, verified live
        via ``backend/tools/probe_vehicle_compositions.py`` against Vissim
        2026: ``AttValue('RelFlow(1)')`` RAISES ("Too many sub-attributes:
        1") -- unlike route ``RelFlow``, there is NO time-interval dimension
        at all for vehicle-composition relative flows.

        **Entry lookup has no COM ``ItemByKey`` at all** (verified via both
        the type-library scan and a live probe:
        ``IVehicleCompositionRelativeFlowContainer`` exposes only
        ``GetAll``/``FilteredBy``/``GetFilteredSet``/
        ``AddVehicleCompositionRelativeFlow``/
        ``RemoveVehicleCompositionRelativeFlow``/``SetMulti*`` -- no
        ``ItemByKey``, unlike every other per-object collection in this
        module (conflict areas, routes, vehicle inputs, signal controllers).
        ``FilteredBy('VehType=<n>')`` was also tried live and rejected by
        Vissim's own formula engine ("A variable is not permitted:
        'VehType'"), so the entry is found by scanning ``GetAll()`` and
        matching ``AttValue('VehType')`` client-side --
        :meth:`_vehicle_composition_entry_by_veh_type`. A bogus ``veh_type_no``
        therefore has NO COM-level existence signal to relay; this wrapper
        raises :class:`VissimObjectNotFoundError` itself when the scan finds
        no match, mirroring :meth:`_item_by_key`'s contract for every other
        object class even though the underlying mechanism differs.

        **Normalization semantics (the central empirical finding, verified
        live).** ``RelFlow`` is a RAW weight, not a pre-computed proportion
        -- EXACTLY like :meth:`set_route_relative_flow`'s own finding, and
        the OPPOSITE of the premise ``constraints.py``'s old forward-declared
        placeholder assumed ("raising one class's share automatically lowers
        the others"). Writing composition no=1's vehType=100 entry from
        0.85 to 5.85 on a real 4-entry composition (Urban Intersection
        Beijing.CN) left every SIBLING entry's OWN stored ``RelFlow``
        (vehType 190/200/300, each 0.05) completely UNCHANGED -- confirmed
        both immediately after the write and after a ``SaveNetAs`` + fresh
        COM reload. Each vehicle type's ACTUAL simulated composition share is
        only computed at RUN time (and for display), as this entry's
        ``RelFlow`` divided by the SUM of every entry's ``RelFlow`` in the
        SAME composition. Writing the ``RelFlow`` on a composition's ONLY
        entry (verified live, composition no=4 "Cycle") succeeds normally --
        there is nothing to redistribute against, so the share stays 100%
        regardless of the number, matching the single-route precedent.

        Error behavior (per the AttValue taxonomy in
        docs/phase2/00-EXECUTION-GUIDE.md / DECISIONS.md): a bogus
        ``composition_no`` fails CLOSED via ``ItemByKey`` ("object does not
        exist"), matching every other per-object collection lookup in this
        module. A bogus attribute id on a real entry also fails CLOSED
        (``AttValue`` raises "Could not find attribute with ID ..."),
        matching conflict areas/routes -- not ``Net.AttValue``'s fail-OPEN
        exception.
        """
        try:
            composition = self._item_by_key(
                self.vissim.Net.VehicleCompositions,
                composition_no,
                "Vehicle composition",
                self.available_vehicle_composition_nos(),
            )
            entry = self._vehicle_composition_entry_by_veh_type(composition, veh_type_no)
            entry.SetAttValue("RelFlow", rel_flow)
        except VissimObjectNotFoundError:
            raise
        except Exception as exc:
            raise RuntimeError(
                f"Could not set vehicle composition {composition_no} vehicle type "
                f"{veh_type_no} relative flow to {rel_flow}: {exc}"
            ) from exc

    def get_vehicle_composition_share(self, composition_no: int, veh_type_no: int) -> float:
        """Paired getter for :meth:`set_vehicle_composition_share`."""
        try:
            composition = self._item_by_key(
                self.vissim.Net.VehicleCompositions,
                composition_no,
                "Vehicle composition",
                self.available_vehicle_composition_nos(),
            )
            entry = self._vehicle_composition_entry_by_veh_type(composition, veh_type_no)
            return float(entry.AttValue("RelFlow"))
        except VissimObjectNotFoundError:
            raise
        except Exception as exc:
            raise RuntimeError(
                f"Could not read vehicle composition {composition_no} vehicle type "
                f"{veh_type_no} relative flow: {exc}"
            ) from exc

    def _vehicle_composition_entry_by_veh_type(self, composition: Any, veh_type_no: int) -> Any:
        """The composition's ``VehCompRelFlows`` entry whose ``VehType``
        equals ``veh_type_no``, found by scanning ``GetAll()`` -- there is no
        ``ItemByKey`` on this collection (see :meth:`set_vehicle_composition_share`'s
        docstring). Raises :class:`VissimObjectNotFoundError` when no entry
        matches, listing the composition's own available vehicle-type
        numbers (mirrors :meth:`_item_by_key`'s message shape)."""
        available: list[int] = []
        for item in composition.VehCompRelFlows.GetAll():
            try:
                item_veh_type = int(item.AttValue("VehType"))
            except Exception:
                continue
            available.append(item_veh_type)
            if item_veh_type == veh_type_no:
                return item
        available_text = _format_available_keys(sorted(available))
        raise VissimObjectNotFoundError(
            f"Vehicle type {veh_type_no} does not exist in this vehicle composition's "
            f"relative-flow entries. Available vehicle type numbers: {available_text}."
        )

    def available_vehicle_composition_nos(self) -> list[int]:
        return self._collection_nos(self.vissim.Net.VehicleCompositions)

    def set_des_speed_decision(
        self, decision_no: int, veh_class_no: int, des_speed_distr_no: int
    ) -> None:
        """Set a desired-speed decision's per-vehicle-class distribution
        assignment (COM attribute id ``DesSpeedDistr`` on the veh-class
        CHILD entry -- SCALAR, verified live via
        ``backend/tools/probe_des_speed_decisions.py`` against Vissim 2026
        on Roundabout Billings.US: ``AttValue('DesSpeedDistr')`` with no
        index works directly on the ``VehClassDesSpeedDistr`` child entry --
        unlike route ``RelFlow``, there is no time-interval dimension here at
        all. A SECOND form, ``decision.AttValue('DesSpeedDistr(<vehClass>)')``
        directly on the decision object, was also confirmed to WORK FOR
        READING in the same probe, but was never verified for writing and is
        not used here -- the child-entry route below is the one proven set +
        read-back + persist + fresh-reload, end to end.

        **Entry lookup has no COM ``ItemByKey`` at all** (verified via a live
        ``hasattr`` check -- the SAME landmine :meth:`set_vehicle_composition_share`
        documents for vehicle-composition entries): the entry is found by
        scanning ``GetAll()`` and matching ``AttValue('VehClass')``
        client-side -- :meth:`_des_speed_decision_entry_by_veh_class`.

        **No shared-sum/renormalization semantics at all**, unlike route/
        vehicle-composition ``RelFlow``: writing vehClass=10's assignment on
        a real 2-class decision (Roundabout Billings.US, decision no=6) left
        vehClass=20's own assignment completely UNCHANGED -- confirmed both
        immediately after the write and after a ``SaveNetAs`` + fresh COM
        reload. Each vehicle class's assignment is a fully independent value;
        there is no derived "share" to compute or report.

        **COM itself fails CLOSED on a bogus distribution number** -- verified
        live: ``SetAttValue('DesSpeedDistr', 999999)`` on a real entry RAISES
        ("AttValue failed: Attribute Desired speed distribution: Object
        999999 of type Speed Distribution does not exist."). This codebase's
        own ``constraints.py`` ``des_speed_distribution_not_found`` preflight
        check still refuses proactively (with a candidate list) so the
        failure reaches the user as a structured refusal rather than this
        raw COM error string reaching all the way through.

        Error behavior otherwise matches every other per-object collection in
        this module: a bogus ``decision_no`` fails CLOSED via ``ItemByKey``
        ("object does not exist"); a bogus attribute id on a real object also
        fails CLOSED.
        """
        try:
            decision = self._item_by_key(
                self.vissim.Net.DesSpeedDecisions,
                decision_no,
                "Desired speed decision",
                self.available_des_speed_decision_nos(),
            )
            entry = self._des_speed_decision_entry_by_veh_class(decision, veh_class_no)
            entry.SetAttValue("DesSpeedDistr", des_speed_distr_no)
        except VissimObjectNotFoundError:
            raise
        except Exception as exc:
            raise RuntimeError(
                f"Could not set desired speed decision {decision_no} vehicle class "
                f"{veh_class_no} distribution to {des_speed_distr_no}: {exc}"
            ) from exc

    def get_des_speed_decision(self, decision_no: int, veh_class_no: int) -> str:
        """Paired getter for :meth:`set_des_speed_decision`.

        Returns the distribution number as a STRING -- COM's own
        ``AttValue('DesSpeedDistr')`` read-back form (verified live: e.g.
        ``'50'``), matching the shadow model's own string-typed
        ``des_speed_distr_no`` field (an id, not a continuous physical
        quantity) so ``guardrails.py``'s postwrite compares never trip on an
        int/str type mismatch -- mirrors :meth:`get_conflict_area`'s own
        string ``status`` convention, not the float-weight convention
        ``get_route_relative_flow``/``get_vehicle_composition_share`` use.
        """
        try:
            decision = self._item_by_key(
                self.vissim.Net.DesSpeedDecisions,
                decision_no,
                "Desired speed decision",
                self.available_des_speed_decision_nos(),
            )
            entry = self._des_speed_decision_entry_by_veh_class(decision, veh_class_no)
            return str(entry.AttValue("DesSpeedDistr"))
        except VissimObjectNotFoundError:
            raise
        except Exception as exc:
            raise RuntimeError(
                f"Could not read desired speed decision {decision_no} vehicle class "
                f"{veh_class_no} distribution: {exc}"
            ) from exc

    def _des_speed_decision_entry_by_veh_class(self, decision: Any, veh_class_no: int) -> Any:
        """The decision's ``VehClassDesSpeedDistr`` entry whose ``VehClass``
        equals ``veh_class_no``, found by scanning ``GetAll()`` -- there is no
        ``ItemByKey`` on this collection (see :meth:`set_des_speed_decision`'s
        docstring). Raises :class:`VissimObjectNotFoundError` when no entry
        matches, listing the decision's own available vehicle-class numbers
        (mirrors :meth:`_vehicle_composition_entry_by_veh_type`'s contract)."""
        available: list[int] = []
        for item in decision.VehClassDesSpeedDistr.GetAll():
            try:
                item_veh_class = int(item.AttValue("VehClass"))
            except Exception:
                continue
            available.append(item_veh_class)
            if item_veh_class == veh_class_no:
                return item
        available_text = _format_available_keys(sorted(available))
        raise VissimObjectNotFoundError(
            f"Vehicle class {veh_class_no} does not exist in this desired speed decision's "
            f"vehicle-class assignments. Available vehicle class numbers: {available_text}."
        )

    def available_des_speed_decision_nos(self) -> list[int]:
        return self._collection_nos(self.vissim.Net.DesSpeedDecisions)

    def available_des_speed_distribution_nos(self) -> list[int]:
        """Available ``Net.SpeedDistributions`` numbers -- verified live
        (``backend/tools/probe_des_speed_decisions.py``) that this is the
        SAME collection ``set_reduced_speed_area`` (v2 unit ②) already
        injects into, not a dedicated ``DesSpeedDistributions`` collection
        (which does not exist -- ``AttributeError`` on ``Net``)."""
        return self._collection_nos(self.vissim.Net.SpeedDistributions)

    # -----------------------------------------------------------------
    # Desired-speed DISTRIBUTION curve (points) verbs -- QUEUE 3 (v2a) unit 3
    #
    # COM ids/behavior verified EMPIRICALLY live against Vissim 2026
    # (backend/tools/probe_des_speed_curves.py, "Roundabout Billings.US"
    # clone -- the SAME network unit ⑧'s own des-speed-decision probe used).
    #
    # ``Net.SpeedDistributions`` (unit ⑧'s own finding, reused unchanged) ->
    # per-distribution ``.SpeedDistrDatPts`` child collection (matches the
    # XML child tag ``speedDistrDatPts`` exactly) of point objects carrying
    # ``X`` (speed) / ``FX`` (cumulative fraction). The type library
    # (``ISpeedDistributionDataPointContainer``) exposes ``GetAll`` and
    # ``ReplaceAll`` but genuinely NO ``ItemByKey``, NO
    # ``AddSpeedDistributionDataPoint``/``RemoveSpeedDistributionDataPoint``
    # -- confirmed live via ``hasattr`` -- unlike the sibling
    # ``ISpeedDistributionContainer`` one level up (distributions
    # themselves), which DOES have ``ItemByKey``/``Add``/``Remove``. COM
    # itself offers no per-point surgery primitive for an EXISTING
    # distribution's points: only whole-collection ``ReplaceAll`` (this
    # module's exclusive write path here) or per-point ``SetAttValue`` on an
    # object already returned by ``GetAll()``. Per-point ``SetAttValue`` was
    # tried live and found UNRELIABLE (worked in one isolated retest, but a
    # fuller probe run -- more COM calls between fetching the point object
    # and mutating it -- left the value COMPLETELY UNCHANGED with no
    # exception at all, a genuine silent no-op) -- this codebase never uses
    # it, exclusively writing through ``ReplaceAll``'s atomic whole-curve
    # replace, exactly matching the task's own "atomic full-curve replace
    # beats point surgery" directive, now backed by a live-verified reason
    # beyond design taste: point-level writes are demonstrably unreliable
    # over this COM surface.
    #
    # **``ReplaceAll`` SHAPE LANDMINE (live-verified, easy to get silently
    # wrong):** ``Elements`` must be a FLAT, alternating sequence
    # ``(x1, fx1, x2, fx2, ..., xn, fxn)`` -- e.g. ``(0, 0, 50, 0.5, 100, 1)``
    # for a 3-point curve. A NESTED tuple-of-(x, fx)-pairs shape (the more
    # "obviously correct"-looking guess, e.g. ``((0, 0), (50, 0.5), (100,
    # 1))``) is SILENTLY IGNORED: the call reports success (no exception,
    # matching the documented "at least 2 items" length check, which DOES
    # fire correctly even for a nested shape since only the outer length is
    # inspected first) but a fresh ``GetAll()`` -- even via a brand-new
    # ``Net.SpeedDistributions.ItemByKey(...).SpeedDistrDatPts`` handle, not
    # a stale collection reference -- shows the ORIGINAL, untouched points.
    # Verified with three candidate shapes (nested (x,fx) tuples, nested
    # (fx,x) tuples, nested lists) -- ALL THREE silently no-op; only the flat
    # alternating sequence actually replaces the collection. This is exactly
    # the kind of "reports success, does nothing" trap the whole validation
    # layer exists to catch -- documented here so it is never re-guessed.
    #
    # **UNITS LANDMINE (live-verified, both directions):** ``X`` is read AND
    # WRITTEN in the network's OWN display unit
    # (``Net.NetPara.AttValue('UnitSpeed')`` -- ``"KILOMETERSPERHOUR"`` or
    # ``"MILESPERHOUR"`` on the 25 real Vissim 2026 example networks scanned,
    # 21/4 split), NOT always km/h -- the SAME "COM reports/accepts the
    # display unit; the .inpx ALWAYS stores km/h internally" pattern unit
    # ⑧'s own probe found for ``Pos`` (read-only there). Proven for BOTH
    # directions here: on a MILESPERHOUR-display network, writing X=40/X=80
    # via ``ReplaceAll`` and then persisting + re-parsing the SAVED XML
    # showed ``x="64.37376"``/``x="128.74752"`` -- EXACTLY 40/80 * 1.609344
    # (the mph-to-km/h conversion constant), not the literal 40/80 written.
    # ``FX`` needs NO conversion (unitless [0, 1], verified to round-trip
    # exactly regardless of display unit). This module converts at the COM
    # boundary so every caller above it (edit_session.py, tools_edit.py,
    # guardrails.py, the shadow model) deals exclusively in genuine km/h,
    # matching :func:`services.agent.tools_network._parse_speed_distribution_points`'s
    # own km/h-native XML parsing -- never guessed, never left for a caller
    # to get wrong.
    #
    # **Vissim's OWN validation, live-verified via the silent-failure map**
    # (``backend/tools/probe_des_speed_curves.py``'s own transcript has the
    # full table): REJECTS (COM raises) a curve with fewer than 2 points,
    # any FX outside [0, 1], a first point whose FX != 0, a last point whose
    # FX != 1, and any speed < 0 (Vissim's own floor is 0, matching the
    # priority-rule gap/headway floor). SILENTLY ACCEPTS a genuine speed
    # DECREASE between consecutive points, FX that decreases in the MIDDLE
    # of the curve (as long as the first/last points are 0/1), an absurdly
    # large speed (unbounded upward), an exact duplicate point, and --
    # correctly, matching real PTV-authored data ("Rollercoaster.inpx") --
    # two consecutive points sharing the SAME speed with DIFFERENT FX (a
    # legitimate step function). ``constraints.py``'s own preflight checks
    # close every SILENTLY-ACCEPTED gap; the ones Vissim already refuses are
    # still re-checked proactively so the failure reaches the caller as a
    # structured refusal, not a raw COM error string.
    # -----------------------------------------------------------------

    _KMH_PER_MPH = 1.609344

    def _net_unit_speed(self) -> str:
        """The active network's own speed display unit
        (``Net.NetPara.AttValue('UnitSpeed')``) -- ``"KILOMETERSPERHOUR"`` or
        ``"MILESPERHOUR"`` on every real Vissim 2026 example network
        probed. Defensively defaults to km/h (no conversion) on any
        failure -- matches the .inpx's own fallback when ``<netPara>``
        itself is absent (the synthetic demo network's case)."""
        try:
            return str(self.vissim.Net.NetPara.AttValue("UnitSpeed") or "").strip().upper()
        except Exception:
            return "KILOMETERSPERHOUR"

    def _speed_kmh_to_com(self, speed_kmh: float, unit: str) -> float:
        if unit == "MILESPERHOUR":
            return speed_kmh / self._KMH_PER_MPH
        return speed_kmh

    def _speed_com_to_kmh(self, value: float, unit: str) -> float:
        if unit == "MILESPERHOUR":
            return value * self._KMH_PER_MPH
        return value

    def get_des_speed_distribution_points(self, distribution_no: int) -> list[dict[str, float]]:
        """The distribution's current curve as ``[{"speed_kmh", "cum_fraction"},
        ...]`` in document order, genuine km/h regardless of the network's
        own display unit (see this block's own module comment)."""
        try:
            distribution = self._item_by_key(
                self.vissim.Net.SpeedDistributions,
                distribution_no,
                "Speed distribution",
                self.available_des_speed_distribution_nos(),
            )
            unit = self._net_unit_speed()
            points = []
            for point in distribution.SpeedDistrDatPts.GetAll():
                points.append(
                    {
                        "speed_kmh": self._speed_com_to_kmh(float(point.AttValue("X")), unit),
                        "cum_fraction": float(point.AttValue("FX")),
                    }
                )
            return points
        except VissimObjectNotFoundError:
            raise
        except Exception as exc:
            raise RuntimeError(f"Could not read speed distribution {distribution_no} points: {exc}") from exc

    def set_des_speed_distribution_points(
        self, distribution_no: int, points: list[dict[str, float]]
    ) -> list[dict[str, float]]:
        """Atomic full-curve replace: ``ReplaceAll`` on the distribution's
        ``SpeedDistrDatPts`` collection with a FLAT, alternating
        ``(x1, fx1, x2, fx2, ...)`` sequence -- the ONLY ``Elements`` shape
        that actually takes effect (see this block's own module comment for
        the nested-tuple silent-no-op landmine). ``points`` speeds are
        genuine km/h; converted to the network's own display unit at the
        COM boundary, then the ACTUAL post-write values are read back and
        converted back to km/h for the return -- never merely echoing the
        caller's own input, so a caller relying on this return value never
        needs to guess whether Vissim silently modified anything Beyond
        raising.

        Raises whatever Vissim's own ``ReplaceAll`` raises for an invalid
        curve (fewer than 2 points, FX outside [0, 1], first/last FX not
        0/1, negative speed -- see the silent-failure map in this block's
        own module comment) -- ``constraints.py``'s own preflight checks
        exist so these are refused BEFORE ever reaching this call, with a
        structured, bilingual reason instead of this raw COM error string.
        """
        try:
            distribution = self._item_by_key(
                self.vissim.Net.SpeedDistributions,
                distribution_no,
                "Speed distribution",
                self.available_des_speed_distribution_nos(),
            )
            unit = self._net_unit_speed()
            flat: list[float] = []
            for point in points:
                flat.append(self._speed_kmh_to_com(float(point["speed_kmh"]), unit))
                flat.append(float(point["cum_fraction"]))
            distribution.SpeedDistrDatPts.ReplaceAll(tuple(flat))
        except VissimObjectNotFoundError:
            raise
        except Exception as exc:
            raise RuntimeError(
                f"Could not replace speed distribution {distribution_no} points: {exc}"
            ) from exc
        return self.get_des_speed_distribution_points(distribution_no)

    # -----------------------------------------------------------------
    # Priority-rule gap/headway verbs -- QUEUE 3 unit 2
    #
    # COM ids verified EMPIRICALLY live against Vissim 2026
    # (backend/tools/probe_priority_rules.py, Roundabout Buehl.DE clone --
    # the real Murdoch Drive production model has 13 real <priorityRule>
    # elements too (verified statically, 20 conflict markers, same attribute
    # set), but VissimController.load_network itself RAISES on that file
    # ("The network file could not be loaded.") -- reproduced against both a
    # full-directory clone AND the original path directly, so it is not a
    # clone/sibling-asset problem. Root cause NOT diagnosed here (out of
    # scope for a priority-rule probe; flagged separately in this unit's
    # report) -- no prior v2/v2a unit ever actually COM-loaded this file
    # either, every live-COM probe/check in this codebase uses a real Vissim
    # EXAMPLE network, mirroring the v2a unit ⑧ precedent (Beijing had zero
    # desSpeedDecisions -> Roundabout Billings.US substituted).
    #
    # Net.PriorityRules -> per-rule .ConflictMarkers child collection (matches
    # the XML child tag ConflictMarkers/conflictMarker exactly, mirroring
    # every other per-object child collection in this codebase). A conflict
    # marker carries NO "No"/identity attribute of its own at all (verified
    # live: AttValue('No') raises "Could not find attribute with ID NO") AND
    # the ConflictMarkers collection has NO ItemByKey (verified live via
    # hasattr) -- the SAME "no ItemByKey" landmine unit ⑦/⑧ found for
    # vehicle-composition/des-speed-decision entries, but WORSE here: those
    # entries at least had a real distinguishing attribute (VehType/VehClass)
    # to scan-and-match on; a conflict marker has none at all. GetAll()
    # order was verified live to match the .inpx's own document order (every
    # marker's values compared 1:1 against a fresh XML parse of the same
    # file, before AND after a write+persist round trip), so marker identity
    # here is a SYNTHESIZED 1-based ordinal index within its rule's own
    # ConflictMarkers.GetAll() order -- :meth:`_priority_rule_marker_by_index`.
    #
    # CENTRAL FINDING: the task's "gap/headway" framing maps onto TWO REAL,
    # independently-writable per-marker attributes -- but NOT the two most
    # XML-attribute-obvious ones. ``minGapTime``/``MinGapTime`` (values 0-8s
    # observed across two real networks, XML attribute name matches the COM
    # id exactly) is unambiguously the "gap" -- a TIME, the critical/
    # acceptable gap a yielding vehicle needs. The SECOND task-requested
    # parameter, "headway", does NOT exist under any id spelled that way
    # AS A DISTINCT ATTRIBUTE -- but live probing found ``AttValue('MinHdwy')``
    # DOES work. A network-wide scan (61 rules, 124 markers, 73 with a
    # non-zero reading) plus a direct write-then-persist test PROVED
    # ``MinHdwy`` is an ALIAS for the SAME underlying attribute as
    # ``MinClear`` (the XML's own ``minClear`` attribute): every non-zero
    # ``MinHdwy`` reading exactly equalled that SAME marker's own
    # ``MinClear`` reading (73/73 checked, zero counterexamples); writing
    # ``MinHdwy`` to 2.5 changed the value ``MinClear`` read back as 2.5 and
    # persisted to the saved XML as ``minClear="2.5"`` (``minGapTime``
    # untouched); and Vissim's own COM error text for an out-of-range
    # ``MinHdwy`` write names the attribute "Minimum clearance" ("AttValue
    # failed: Value -1 m is lower than minimum value of attribute Minimum
    # clearance (Min: 0 m)"), not "headway" -- confirming Vissim's own
    # internal caption for this attribute is "minimum clearance", with
    # ``MinHdwy`` as a secondary COM-only alias id. It is also a SPATIAL
    # CLEARANCE in METRES (unit "m" in the error text; observed values 0-30
    # across two real networks), NOT a time in seconds, despite the "Hdwy"
    # spelling. This codebase writes/reads it via ``MinClear`` (matches the
    # .inpx attribute name exactly, the same "prefer the XML-matching id"
    # convention every prior unit followed), and names the tool-facing param
    # ``headway_m`` (not ``headway_s``) so the unit is never ambiguous in
    # code -- see docs/phase2/DECISIONS.md for the drafted entry recording
    # this as a deviation from the task brief's literal "seconds" framing
    # for headway.
    # -----------------------------------------------------------------

    def _priority_rule_marker_by_index(self, rule: Any, marker_index: int) -> Any:
        """The rule's ``ConflictMarkers`` entry at 1-based ``marker_index``,
        in ``GetAll()`` order (verified live to match the .inpx's own
        document order -- see this block's own comment). Raises
        :class:`VissimObjectNotFoundError` listing how many markers the rule
        actually has when out of range -- mirrors
        :meth:`_des_speed_decision_entry_by_veh_class`'s contract even
        though the underlying identity mechanism (ordinal position, not a
        matched attribute value) differs, because there is no attribute to
        match on at all (see this block's own comment)."""
        markers = list(rule.ConflictMarkers.GetAll())
        if 1 <= marker_index <= len(markers):
            return markers[marker_index - 1]
        raise VissimObjectNotFoundError(
            f"Conflict marker index {marker_index} does not exist on this priority rule. "
            f"It has {len(markers)} conflict marker(s), indexed 1..{len(markers)}."
        )

    def set_priority_rule_gap_headway(
        self,
        priority_rule_no: int,
        marker_index: int,
        gap_time_s: float | None,
        headway_m: float | None,
    ) -> dict[str, float]:
        """Set a priority rule conflict marker's critical gap time
        (``MinGapTime``, seconds) and/or minimum clearance/"headway"
        (``MinClear``, METRES -- see this block's own module comment) --
        either field alone, or both in the same call (at least one must be
        given; enforced by the caller's params model, not here). Returns
        ``{"gap_time_s", "headway_m"}`` with the value ACTUALLY IN EFFECT
        after this call for each -- when a field is not given, its CURRENT
        (unchanged) value is read and returned instead, so the caller always
        gets a complete pair to verify against (mirrors
        :meth:`set_signal_green_split`'s always-both-edges verification
        shape).

        Both attributes fail CLOSED on a negative value (verified live:
        ``SetAttValue('MinGapTime', -1)``/``SetAttValue('MinClear', -1)``
        both RAISE, "... is lower than minimum value ... (Min: 0 ...)") --
        Vissim's own floor is 0, not >0: real data legitimately carries 0 on
        both attributes (a majority of the real Murdoch Drive model's own 20
        conflict markers have ``minGapTime="0"``), so this codebase's own
        preflight refuses NEGATIVE values, not non-positive ones (see
        ``constraints.py``'s ``priority_rule_gap_headway_invalid`` docstring
        for the full reasoning) -- this method still lets Vissim's own raise
        be the final backstop for any caller that bypasses preflight.
        """
        try:
            rule = self._item_by_key(
                self.vissim.Net.PriorityRules,
                priority_rule_no,
                "Priority rule",
                self.available_priority_rule_nos(),
            )
            marker = self._priority_rule_marker_by_index(rule, marker_index)
            if gap_time_s is not None:
                marker.SetAttValue("MinGapTime", gap_time_s)
            if headway_m is not None:
                marker.SetAttValue("MinClear", headway_m)
            return {
                "gap_time_s": float(marker.AttValue("MinGapTime")),
                "headway_m": float(marker.AttValue("MinClear")),
            }
        except VissimObjectNotFoundError:
            raise
        except Exception as exc:
            raise RuntimeError(
                f"Could not set priority rule {priority_rule_no} conflict marker "
                f"{marker_index} gap_time_s={gap_time_s} headway_m={headway_m}: {exc}"
            ) from exc

    def get_priority_rule_gap_headway(self, priority_rule_no: int, marker_index: int) -> dict[str, float]:
        """Paired getter for :meth:`set_priority_rule_gap_headway`."""
        try:
            rule = self._item_by_key(
                self.vissim.Net.PriorityRules,
                priority_rule_no,
                "Priority rule",
                self.available_priority_rule_nos(),
            )
            marker = self._priority_rule_marker_by_index(rule, marker_index)
            return {
                "gap_time_s": float(marker.AttValue("MinGapTime")),
                "headway_m": float(marker.AttValue("MinClear")),
            }
        except VissimObjectNotFoundError:
            raise
        except Exception as exc:
            raise RuntimeError(
                f"Could not read priority rule {priority_rule_no} conflict marker "
                f"{marker_index} gap/headway: {exc}"
            ) from exc

    def available_priority_rule_nos(self) -> list[int]:
        return self._collection_nos(self.vissim.Net.PriorityRules)

    # -----------------------------------------------------------------
    # Vehicle-type reference-assignment verbs -- QUEUE (v2a) unit
    #
    # COM ids/behavior verified EMPIRICALLY live against Vissim 2026
    # (backend/tools/probe_vehicle_types.py, "Roundabout Billings.US" clone).
    #
    # ``Net.VehicleTypes`` (``ItemByKey`` fails CLOSED for a bogus no, like
    # every other per-object collection in this module except vehicle-
    # composition/des-speed-decision/route entries) -> a FLAT attribute on
    # the type object itself for each reference -- unlike conflict areas'
    # separate No/LinkA/LinkB precedent, EVERY reference id here matches the
    # vehicleType's own .inpx XML attribute spelling EXACTLY, including the
    # "Curve" (not "Curv") spelling of ``DesCurveSpeedFunc`` -- even though
    # the TARGET collection's own name is ``Net.CurvSpeedFuncs`` ("Curv", no
    # e), matching the referenced XML section/element (``curvSpeedFuncs``/
    # ``curvSpeedFunc``) instead. This spelling mismatch between a
    # reference's OWN attribute id and its TARGET collection's name is a new,
    # narrow landmine: never assume a reference id and its target collection
    # share one spelling convention.
    #
    # **Nullable references, verified live.** A PEDESTRIAN vehicle type's
    # ``WeightDistr``/``PowerDistr``/``DesCurveSpeedFunc`` (absent from the
    # XML entirely on every real PEDESTRIAN type probed) read back as ``None``
    # over COM too -- not an exception, not a sentinel number. This is
    # DIFFERENT from the unit 3 (v2a queue) ``Net.AttValue`` fail-OPEN
    # landmine (a genuinely UNKNOWN attribute id returning ``None``): here the
    # attribute id itself is valid and works on every other vehicle type --
    # the value is legitimately unset for THIS object. Getters below return
    # ``str | None`` accordingly.
    #
    # **No shared-sum/renormalization semantics at all** (verified live,
    # sibling-untouched check on a real 6-type network): each vehicle type's
    # own reference assignment is fully independent, exactly like desired-
    # speed decision assignments (v2a unit ⑧) -- never a raw-weight/
    # normalized-share situation like route/vehicle-composition RelFlow.
    #
    # **COM itself fails CLOSED on a bogus reference target** (verified live:
    # ``SetAttValue('WeightDistr', 999999)`` raises "Object 999999 of type
    # Weight Distribution does not exist") -- the same protective pattern
    # ``set_des_speed_decision`` found for ``DesSpeedDistr``; this codebase's
    # own ``constraints.py`` ``vehicle_type_reference_target_not_found``
    # still refuses proactively (with a candidate list) so the failure
    # reaches the user as a structured refusal rather than this raw COM
    # error string.
    # -----------------------------------------------------------------

    #: {planner-facing field name: COM AttValue id on the VehicleType object}
    #: -- the SOLE source of truth mapping ``set_vehicle_type_reference``'s
    #: ``field`` enum onto a real COM attribute, verified live (see the class
    #: docstring above). Deliberately does NOT include ``category``/
    #: ``capacity``/``capExact`` (verified live too, but out of this unit's
    #: scope -- "SCALARS/REFERENCES only" per the task brief means the
    #: REFERENCE half of that phrase is what ships a write verb; the
    #: category enum and capacity/capExact scalars are read-only via
    #: get_vehicle_types this unit, not independently justified as their own
    #: write verb without a concrete use case).
    VEHICLE_TYPE_REFERENCE_FIELDS: dict[str, str] = {
        "model2d3d_distr": "Model2D3DDistr",
        "weight_distr": "WeightDistr",
        "power_distr": "PowerDistr",
        "des_accel_func": "DesAccelFunc",
        "max_accel_func": "MaxAccelFunc",
        "des_decel_func": "DesDecelFunc",
        "max_decel_func": "MaxDecelFunc",
        "des_curve_speed_func": "DesCurveSpeedFunc",
    }

    def set_vehicle_type_reference(self, vehicle_type_no: int, field: str, target_no: int) -> None:
        """Set one of a vehicle type's DISTRIBUTION/FUNCTION reference
        attributes (``field`` -- one of :data:`VEHICLE_TYPE_REFERENCE_FIELDS`'
        keys) to ``target_no`` (the target distribution/function's own
        number in its OWN collection -- e.g. a ``Net.WeightDistributions``
        number for ``field="weight_distr"``).

        ``field`` is validated against :data:`VEHICLE_TYPE_REFERENCE_FIELDS`
        here defensively (the caller's pydantic params model already
        restricts it to a ``Literal`` of the same 8 values, so this should
        never actually fail in practice -- kept for the same reason
        ``_coerce_id`` stays defensive in ``tools_edit.py``: a clear
        ``ValueError`` beats a confusing ``AttributeError`` deeper in COM).
        """
        attr_id = self.VEHICLE_TYPE_REFERENCE_FIELDS.get(field)
        if attr_id is None:
            raise ValueError(f"Unknown vehicle-type reference field: {field!r}")
        try:
            vehicle_type = self._item_by_key(
                self.vissim.Net.VehicleTypes,
                vehicle_type_no,
                "Vehicle type",
                self.available_vehicle_type_nos(),
            )
            vehicle_type.SetAttValue(attr_id, target_no)
        except VissimObjectNotFoundError:
            raise
        except Exception as exc:
            raise RuntimeError(
                f"Could not set vehicle type {vehicle_type_no} {field} to {target_no}: {exc}"
            ) from exc

    def get_vehicle_type_reference(self, vehicle_type_no: int, field: str) -> str | None:
        """Paired getter for :meth:`set_vehicle_type_reference`.

        Returns the target's number as a STRING (matching the shadow model's
        own string-typed reference-number fields, the same convention
        :meth:`get_des_speed_decision` established for a distribution
        reference) -- or ``None`` when this vehicle type genuinely carries no
        value for ``field`` (the live-verified PEDESTRIAN-category nullable-
        reference finding above; NOT a raised error).
        """
        attr_id = self.VEHICLE_TYPE_REFERENCE_FIELDS.get(field)
        if attr_id is None:
            raise ValueError(f"Unknown vehicle-type reference field: {field!r}")
        try:
            vehicle_type = self._item_by_key(
                self.vissim.Net.VehicleTypes,
                vehicle_type_no,
                "Vehicle type",
                self.available_vehicle_type_nos(),
            )
            value = vehicle_type.AttValue(attr_id)
            return None if value is None else str(value)
        except VissimObjectNotFoundError:
            raise
        except Exception as exc:
            raise RuntimeError(
                f"Could not read vehicle type {vehicle_type_no} {field}: {exc}"
            ) from exc

    def available_vehicle_type_nos(self) -> list[int]:
        return self._collection_nos(self.vissim.Net.VehicleTypes)

    # -----------------------------------------------------------------
    # Time-distribution verbs -- QUEUE (Q3, v2a) unit 5
    #
    # COM ids/behavior verified EMPIRICALLY live against Vissim 2026
    # (backend/tools/probe_dwell_time_distributions.py, "BRT Priority
    # Texas.US" clone -- "Signalization RBC Boise.US", the richer PT network
    # this unit's static scan originally picked as its live target, turned
    # out to RAISE on LoadNet itself ("The network file could not be
    # loaded.") -- a NEW, previously-unrecorded landmine for RBC-branded
    # example networks (never actually COM-loaded by any prior unit either;
    # QUEUE 3 unit 2's own priority-rule probe named it only as a STATIC
    # cross-check network, substituting Roundabout Buehl.DE for its live
    # phase). Substituted per that same "pick a different real example
    # network when the usual richest target can't COM-load" precedent.
    #
    # ``Net.TimeDistributions`` (``ItemByKey`` fails CLOSED for a bogus no,
    # matching every per-object collection in this module except
    # vehicle-composition/des-speed-decision/route entries) -> No/Name/Type/
    # Mean/StdDev scalar attributes matching the .inpx XML spelling exactly
    # (no CycTm-class landmine here) PLUS, for ``Type == "EMPIRICAL"``
    # distributions only, a ``.DurDistrDataPts`` child collection (matches the
    # XML child tag exactly) of (``X`` duration, ``FX`` cumulative fraction)
    # points -- the SAME shape unit 3's ``SpeedDistrDatPts`` already
    # established (verified live: no ``ItemByKey`` on the points collection),
    # needing the SAME atomic flat-alternating ``ReplaceAll``.
    #
    # **CENTRAL FINDING -- "editing the wrong half" is BLOCKED BY VISSIM
    # ITSELF, live-verified, for one direction:** ``SetAttValue('Mean', ...)``
    # on an EMPIRICAL-type distribution RAISES ("Attribute Mean cannot be
    # changed") -- Vissim's own COM layer refuses to let a caller edit the
    # decorative Mean/StdDev summary on a distribution whose actual sampled
    # behavior comes from its points. The REVERSE direction is NOT similarly
    # protected: ``.DurDistrDataPts`` remains ACCESSIBLE (returns an empty
    # ``GetAll()``) on a NORMAL-type distribution -- whether ``ReplaceAll`` on
    # that empty collection would silently succeed and write points Vissim
    # then ignores (Type still "NORMAL") was NOT tested live (a genuinely
    # destructive, low-value probe step against a real distribution); this
    # module and constraints.py both refuse a points write against a
    # non-EMPIRICAL target PROACTIVELY, treating the untested direction as
    # unsafe by default per the guardrail doctrine ("verify before acting").
    # ALSO live-verified: ``Type`` itself is a plain writable attribute
    # (``SetAttValue('Type', 'NORMAL'/'EMPIRICAL')`` both ACCEPTED) -- a real
    # landmine (silently flipping a distribution's fundamental representation
    # is one ``SetAttValue`` away) this unit's own verbs deliberately never
    # expose (no "type" param on either setter below, mirroring unit ⑨'s
    # "ProgNo is writable but not shipped as a verb this unit" precedent).
    #
    # **Mean/StdDev are FROZEN informational labels on an EMPIRICAL
    # distribution, live-verified NOT to auto-recompute from a points
    # ReplaceAll** (a real 2-point curve rewritten to 3 different points left
    # Mean/StdDev completely unchanged in the persisted XML) -- and writing
    # Mean/StdDev on a NORMAL distribution was live-verified NOT to touch its
    # own ``Name`` label either (a real write, 550->42/7.5, left
    # ``name="550 s"`` untouched in the persisted XML) -- so, unlike unit ⑧'s
    # ``des_speed_distr``/``des_speed_distr_name`` coupling, NEITHER setter
    # here needs a ``coupled_fields`` entry for a sibling scalar; only the
    # points setter needs one, for its OWN derived ``point_count``/
    # ``points_truncated`` (the SAME shape unit 3 already established).
    #
    # **Vissim's OWN validation, live-verified via the silent-failure map**
    # (``backend/tools/probe_dwell_time_distributions.py``'s own transcript
    # has the full table, run independently rather than assumed to
    # generalize from unit 3's speed-curve findings even though the
    # mechanism -- ``ReplaceAll``, flat X/FX sequence -- is the same):
    # Mean/StdDev REFUSE a negative value (Vissim's own floor is 0, matching
    # every other "negative refused" attribute in this codebase) but ACCEPT
    # StdDev==0 (legitimate -- real data has plenty) and an unbounded large
    # Mean (999999 accepted with no complaint). Points REFUSE fewer than 2
    # points, any FX outside [0, 1] (via first/last-endpoint framing in
    # Vissim's own error text), a first point's FX != 0, a last point's FX !=
    # 1, and a negative duration value; SILENTLY ACCEPT a genuine duration
    # DECREASE between points, an absurdly large duration (unbounded), an
    # exact duplicate point, and a legitimate step function (same X, two
    # different FX values) -- the identical accept/reject shape unit 3 found
    # for speed, now independently confirmed for duration too.
    #
    # **No display-unit conversion needed** (unlike unit 3's speed-in-mph
    # landmine): every literal value written (2.0, 5.0, 10.0, 42.0, 7.5,
    # 99999.0, ...) read back and persisted EXACTLY as written, with no
    # scaling factor observed at any step of this probe -- Vissim has no
    # per-network "display unit" setting for durations the way it does for
    # speed (``NetPara@unitSpeed``) or position (feet vs metres).
    # -----------------------------------------------------------------

    def available_time_distribution_nos(self) -> list[int]:
        return self._collection_nos(self.vissim.Net.TimeDistributions)

    def get_time_distribution(self, time_distribution_no: int) -> dict[str, Any]:
        """{"no", "name", "type", "mean_s", "std_dev_s"} for a time
        distribution's scalar attributes -- present regardless of Type (an
        EMPIRICAL distribution's Mean/StdDev are a frozen, NOT
        recomputed-from-points, informational summary -- see this block's own
        module comment)."""
        try:
            distribution = self._item_by_key(
                self.vissim.Net.TimeDistributions,
                time_distribution_no,
                "Time distribution",
                self.available_time_distribution_nos(),
            )
            return {
                "no": str(distribution.AttValue("No")),
                "name": str(distribution.AttValue("Name") or ""),
                "type": str(distribution.AttValue("Type") or ""),
                "mean_s": float(distribution.AttValue("Mean")),
                "std_dev_s": float(distribution.AttValue("StdDev")),
            }
        except VissimObjectNotFoundError:
            raise
        except Exception as exc:
            raise RuntimeError(f"Could not read time distribution {time_distribution_no}: {exc}") from exc

    def set_time_distribution_normal(
        self, time_distribution_no: int, mean_s: float | None, std_dev_s: float | None
    ) -> dict[str, Any]:
        """Set a NORMAL-type time distribution's Mean and/or StdDev (seconds)
        -- either field alone, or both in the same call. Returns the CURRENT
        (post-write) scalar dict (see :meth:`get_time_distribution`) so the
        caller always gets a complete readback to verify against, mirroring
        :meth:`set_priority_rule_gap_headway`'s always-both-fields shape.

        Raises whatever Vissim's own ``SetAttValue`` raises for a negative
        value (its own floor is 0 -- see this block's own module comment) OR
        for ANY value at all on a non-NORMAL (EMPIRICAL) distribution
        ("Attribute Mean cannot be changed", live-verified) --
        constraints.py's own preflight checks exist so both are refused
        BEFORE ever reaching this call, with a structured, bilingual reason
        instead of this raw COM error string.
        """
        try:
            distribution = self._item_by_key(
                self.vissim.Net.TimeDistributions,
                time_distribution_no,
                "Time distribution",
                self.available_time_distribution_nos(),
            )
            if mean_s is not None:
                distribution.SetAttValue("Mean", mean_s)
            if std_dev_s is not None:
                distribution.SetAttValue("StdDev", std_dev_s)
        except VissimObjectNotFoundError:
            raise
        except Exception as exc:
            raise RuntimeError(
                f"Could not set time distribution {time_distribution_no} mean_s={mean_s} "
                f"std_dev_s={std_dev_s}: {exc}"
            ) from exc
        return self.get_time_distribution(time_distribution_no)

    def get_time_distribution_points(self, time_distribution_no: int) -> list[dict[str, float]]:
        """The EMPIRICAL distribution's current curve as ``[{"duration_s",
        "cum_fraction"}, ...]`` in document order -- genuine seconds, no unit
        conversion needed (see this block's own module comment)."""
        try:
            distribution = self._item_by_key(
                self.vissim.Net.TimeDistributions,
                time_distribution_no,
                "Time distribution",
                self.available_time_distribution_nos(),
            )
            points = []
            for point in distribution.DurDistrDataPts.GetAll():
                points.append(
                    {
                        "duration_s": float(point.AttValue("X")),
                        "cum_fraction": float(point.AttValue("FX")),
                    }
                )
            return points
        except VissimObjectNotFoundError:
            raise
        except Exception as exc:
            raise RuntimeError(
                f"Could not read time distribution {time_distribution_no} points: {exc}"
            ) from exc

    def set_time_distribution_points(
        self, time_distribution_no: int, points: list[dict[str, float]]
    ) -> list[dict[str, float]]:
        """Atomic full-curve replace on an EMPIRICAL time distribution's
        ``DurDistrDataPts`` -- ``ReplaceAll`` with a FLAT, alternating
        ``(x1, fx1, x2, fx2, ...)`` sequence, the ONLY shape unit 3's own
        probe proved actually takes effect for this sibling COM collection
        family.

        Raises whatever Vissim's own ``ReplaceAll`` raises for an invalid
        curve (fewer than 2 points, FX outside [0, 1], first/last FX not
        0/1, negative duration -- see this block's own module comment for
        the full silent-failure map) OR for a target that is not itself
        EMPIRICAL-type -- constraints.py's own preflight checks exist so
        every one of these is refused BEFORE ever reaching this call.
        """
        try:
            distribution = self._item_by_key(
                self.vissim.Net.TimeDistributions,
                time_distribution_no,
                "Time distribution",
                self.available_time_distribution_nos(),
            )
            flat: list[float] = []
            for point in points:
                flat.append(float(point["duration_s"]))
                flat.append(float(point["cum_fraction"]))
            distribution.DurDistrDataPts.ReplaceAll(tuple(flat))
        except VissimObjectNotFoundError:
            raise
        except Exception as exc:
            raise RuntimeError(
                f"Could not replace time distribution {time_distribution_no} points: {exc}"
            ) from exc
        return self.get_time_distribution_points(time_distribution_no)

    # -----------------------------------------------------------------
    # Driving-behavior duplicate-then-edit verbs -- QUEUE 3 unit 6
    #
    # COM ids/behavior verified EMPIRICALLY live against Vissim 2026
    # (backend/tools/probe_driving_behaviors.py + two follow-up probe scripts,
    # "Roundabout Billings.US" clone -- the real Murdoch Drive production
    # model is, like every prior QUEUE-3 unit, known to raise on
    # ``load_network`` ("The network file could not be loaded"); its STATIC
    # XML is still the ground truth cross-checked against this network's own
    # findings, see tools/probe_driving_behaviors.py's own module docstring).
    #
    # **THE DUPLICATE MECHANISM.** ``Net.DrivingBehaviors`` exposes TWO
    # object-creating methods, live-verified to behave VERY differently:
    # ``AddDrivingBehavior(no)`` creates a brand-new object seeded from
    # Vissim's own INTERNAL DEFAULT TEMPLATE -- live-verified NOT a copy of
    # any source (Name comes back blank; several stochastic-looking scalars
    # -- CoopLnChgSpeedDiff, LatDistDrivDef, LatDistStandDef, MinSpeedForLat
    # -- differ from a real source no=1's own values). ``DuplicateDrivingBehavior
    # (Source, NewKey)`` is a SEPARATE, purpose-built method -- live-verified
    # to be an EXACT, full attribute-for-attribute copy (every one of 26
    # scalars checked, INCLUDING Name and the w99cc1Distr/zipperOvtTmDistr
    # distribution references, came back byte-identical to the source; the
    # ONLY difference is the new object's own ``No``). This module therefore
    # calls ``DuplicateDrivingBehavior`` exclusively -- no attribute-by-
    # attribute copy loop is needed, and ``AddDrivingBehavior`` is never
    # called. Both methods require an EXPLICIT integer ``NewKey``/second arg
    # (the no-args overload raises "This VARIANT data type is unknown.") --
    # this module computes the next free number itself
    # (``max(existing) + 1``) so callers never need to guess a free slot in a
    # real network's non-contiguous number space (the real Murdoch model's
    # own 15 behaviors use no 1-6, 10-13, 19-20, 101-103).
    #
    # **THE WHITELIST -- why only these 8 scalars, and no W99 CC0-CC9 at
    # all.** The full driving-behavior object carries ~130 attributes. This
    # unit's write verb (``set_driving_behavior_parameter``) only ever
    # touches a small, explicitly-reasoned subset:
    #   - Wiedemann-74 car-following (``ax``/``bx_add``/``bx_mult`` ->
    #     ``W74ax``/``W74bxAdd``/``W74bxMult``): the task brief's own
    #     suggested whitelist example.
    #   - Lane-change/lateral aggressiveness (``saf_dist_fact_ln_chg``,
    #     ``coop_ln_chg_speed_diff``, ``min_speed_for_lat``,
    #     ``lat_dist_driv_def``, ``lat_dist_stand_def``): the task brief's
    #     own "lane-change aggressiveness" ask, verified live to be
    #     model-independent (present and meaningful on both a WIEDEMANN74 and
    #     a WIEDEMANN99 real behavior).
    #   - Wiedemann-99 CC0-CC9 (the task brief's OWN suggested "CC0/CC2/CC7-
    #     ish" example) are DELIBERATELY EXCLUDED -- ``constraints.py``'s
    #     PRE-EXISTING ``agency_restricted_parameter_write`` constraint
    #     (registered before this unit, catalog §6 "Some parameters are
    #     off-limits by agency policy": WSDOT/Maryland DOT both say of this
    #     parameter tier simply "use default") already refuses EVERY CC0-CC9
    #     write unconditionally, for a DIFFERENT reason than the shared-object
    #     blast-radius problem duplication solves -- duplication fixes "this
    #     edit changes every link using the behavior", not "an AI is
    #     self-certifying a deviation from agency-recommended defaults". Both
    #     concerns are real and independent; excluding CC0-CC9 from this
    #     unit's own whitelist keeps that pre-existing, catalog-sourced
    #     refusal fully intact rather than silently routing around it. See
    #     this unit's drafted DECISIONS.md entry for the full reasoning.
    #   - CC1 (``w99cc1Distr``) was never a candidate at all: unit 5 already
    #     proved it is a Time Distribution REFERENCE, not a scalar --
    #     unrelated to this unit's scalar whitelist and edited (if ever) via
    #     a completely different verb shape.
    #
    # **DISPLAY-UNIT LANDMINE, a THIRD family beyond speed's own
    # (``NetPara@UnitSpeed``).** ``Net.NetPara`` has NO queryable
    # ``UnitLength``/``UnitDist``/``UnitPosition`` attribute at all (all three
    # raise "Could not find attribute") -- live-verified the REAL id is
    # ``UnitLenShort`` (``"METERS"`` or ``"FEET"`` on the two real networks
    # probed; the .inpx's own ``<netPara unitLenShort="...">`` attribute
    # confirms the same value statically). Distance-type driving-behavior
    # scalars (``ax``, ``lat_dist_driv_def``, ``lat_dist_stand_def``) are
    # read/written by COM in THIS display unit, not always meters -- live-
    # verified both directions on "Roundabout Billings.US" (a FEET-display
    # network): XML-stored ``w74ax="2"`` (meters, Vissim's internal storage
    # is always metric) read back over COM as ``W74ax=6.561679790026246``,
    # exactly ``2 / 0.3048``. Speed-type scalars (``coop_ln_chg_speed_diff``,
    # ``min_speed_for_lat``) reuse the EXISTING ``_net_unit_speed``/
    # ``_speed_kmh_to_com``/``_speed_com_to_kmh`` machinery unit 3 already
    # built (verified live: XML ``coopLnChgSpeedDiff="10.8"`` km/h read back
    # as ``6.710808876163207``, exactly ``10.8 * 0.621371``). ``bx_add``/
    # ``bx_mult``/``saf_dist_fact_ln_chg`` are verified DIMENSIONLESS
    # multipliers/factors -- read back byte-identical to the XML-stored value
    # on the same FEET-display network, no conversion applied by COM at all.
    #
    # **SILENT-FAILURE MAP** (``backend/tools/probe_driving_behaviors.py`` +
    # two follow-up probe scripts, run against a duplicated SCRATCH behavior,
    # never the shared source): ``ax``/``lat_dist_driv_def``/
    # ``lat_dist_stand_def`` REFUSE a negative value (Vissim's own floor is 0
    # -- "... (Min: 0 ft)"); ``saf_dist_fact_ln_chg`` REFUSES both negative
    # (floor 0) AND above 1 (Vissim's own ceiling -- "... (Max: 1)"), matching
    # its real nature as a [0, 1] factor. ``bx_add``/``bx_mult``/
    # ``coop_ln_chg_speed_diff``/``min_speed_for_lat`` ALL SILENTLY ACCEPT a
    # negative value (Vissim's own model genuinely allows it for these four)
    # AND an absurdly large one (99999, unbounded upward on every field
    # tested) -- ``constraints.py``'s own preflight checks proactively refuse
    # the four floor/ceiling violations (a nicer, bilingual message than
    # Vissim's raw COM error) and WARN on implausible magnitudes across the
    # whole whitelist, mirroring every prior unit's "refuse what Vissim
    # refuses anyway, warn what it silently accepts" pattern.
    #
    # **THE ASSIGNMENT SURFACE.** ``Net.LinkBehaviorTypes`` (a SEPARATE
    # collection from ``Net.DrivingBehaviors`` and from ``Net.Links`` --
    # links reference a link-behavior-TYPE via their own ``LinkBehavType``
    # attribute, live-verified read-only informational context this module
    # does not write) has a plain, always-writable ``DrivBehavDef`` scalar
    # (the type's DEFAULT driving-behavior assignment, live-verified fails
    # CLOSED on a bogus target: "Object 999999 of type Driving Behavior does
    # not exist") PLUS a per-vehicle-class override child collection
    # (``.VehClassDrivBehav``, matching the XML's own ``vehClassDrivBehav``/
    # ``vehClassDrivingBehavior`` nesting) whose entries carry ``VehClass``/
    # ``DrivBehav``. **A REAL, REPRODUCIBLE COM LANDMINE, live-verified
    # THREE separate ways (plain call, ``CastTo`` to the typed interface,
    # ``gencache.EnsureDispatch``):** the type library exposes
    # ``AddVehClassDrivingBehavior(VehClass)`` on this child collection, but
    # every attempt to CALL it raises ``TypeError: The Python instance can
    # not be converted to a COM object`` -- a pywin32 marshalling failure,
    # not a Vissim-side refusal (the identical call pattern works fine for
    # ``DuplicateDrivingBehavior``/``DuplicateLinkBehaviorType`` on sibling
    # collections). Mutating an EXISTING override's own ``DrivBehav`` (plain
    # ``SetAttValue`` on an object already returned by ``GetAll()``, no
    # ``Add`` involved) works perfectly and fails closed on a bogus target,
    # live-verified. This module therefore supports writing the link-
    # behavior-type's DEFAULT assignment, and REASSIGNING an EXISTING
    # per-vehicle-class override -- creating a BRAND NEW override for a
    # vehicle class that has none today is NOT supported (a genuine,
    # documented interface gap, not an oversight); see this unit's drafted
    # DECISIONS.md entry.
    # -----------------------------------------------------------------

    _M_PER_FT = 0.3048

    def _net_unit_length(self) -> str:
        """The active network's own SHORT-length display unit
        (``Net.NetPara.AttValue('UnitLenShort')``) -- ``"METERS"`` or
        ``"FEET"`` on the two real Vissim 2026 example networks probed (no
        queryable ``UnitLength``/``UnitDist``/``UnitPosition`` attribute
        exists at all -- verified live, all three raise). Defensively
        defaults to meters (no conversion) on any failure, mirroring
        :meth:`_net_unit_speed`'s own fallback."""
        try:
            return str(self.vissim.Net.NetPara.AttValue("UnitLenShort") or "").strip().upper()
        except Exception:
            return "METERS"

    def _length_m_to_com(self, value_m: float, unit: str) -> float:
        if unit == "FEET":
            return value_m / self._M_PER_FT
        return value_m

    def _length_com_to_m(self, value: float, unit: str) -> float:
        if unit == "FEET":
            return value * self._M_PER_FT
        return value

    #: {canonical parameter name: (COM AttValue id, unit_kind)} -- the SOLE
    #: source of truth for ``set_driving_behavior_parameter``'s whitelist and
    #: ``get_driving_behavior``'s scalar readout, both keyed off this same
    #: dict so the two can never drift apart. ``unit_kind`` is one of
    #: "length" (meters, converted via ``_length_m_to_com``/
    #: ``_length_com_to_m``), "speed" (km/h, converted via the existing
    #: ``_speed_kmh_to_com``/``_speed_com_to_kmh``), or "none" (dimensionless,
    #: no conversion -- verified live for every field tagged "none" below).
    #: See this block's own module comment for the full live-verified
    #: per-field reasoning (why these 8, why not W99 CC0-CC9).
    DRIVING_BEHAVIOR_PARAM_FIELDS: dict[str, tuple[str, str]] = {
        "ax": ("W74ax", "length"),
        "bx_add": ("W74bxAdd", "none"),
        "bx_mult": ("W74bxMult", "none"),
        "saf_dist_fact_ln_chg": ("SafDistFactLnChg", "none"),
        "coop_ln_chg_speed_diff": ("CoopLnChgSpeedDiff", "speed"),
        "min_speed_for_lat": ("MinSpeedForLat", "speed"),
        "lat_dist_driv_def": ("LatDistDrivDef", "length"),
        "lat_dist_stand_def": ("LatDistStandDef", "length"),
    }

    #: {"WIEDEMANN74": 74, "WIEDEMANN99": 99} -- every other real
    #: ``CarFollowModType`` value observed (``"NOINTERACTION"`` on Footpath/
    #: pedestrian behaviors, live-verified on both the real Murdoch model and
    #: "Roundabout Billings.US") maps to ``None`` -- matches
    #: ``constraints.py``'s pre-existing ``car_following_model`` int contract
    #: (``driving_behavior_parameter_model_mismatch`` already compared
    #: against literal ``74``/``99``) rather than introducing a second,
    #: string-keyed convention.
    _CAR_FOLLOW_MODEL_INTS: dict[str, int] = {"WIEDEMANN74": 74, "WIEDEMANN99": 99}

    def available_driving_behavior_nos(self) -> list[int]:
        return self._collection_nos(self.vissim.Net.DrivingBehaviors)

    def get_driving_behavior(self, behavior_no: int) -> dict[str, Any]:
        """{"no", "name", "car_follow_mod_type", "car_following_model",
        <8 whitelisted params>} for one driving behavior -- every whitelisted
        param already converted to its genuine canonical unit (meters/km/h),
        never the network's own raw display unit."""
        try:
            behavior = self._item_by_key(
                self.vissim.Net.DrivingBehaviors,
                behavior_no,
                "Driving behavior",
                self.available_driving_behavior_nos(),
            )
            length_unit = self._net_unit_length()
            speed_unit = self._net_unit_speed()
            car_follow_mod_type = str(behavior.AttValue("CarFollowModType") or "").strip().upper()
            result: dict[str, Any] = {
                "no": str(behavior.AttValue("No")),
                "name": str(behavior.AttValue("Name") or ""),
                "car_follow_mod_type": car_follow_mod_type,
                "car_following_model": self._CAR_FOLLOW_MODEL_INTS.get(car_follow_mod_type),
            }
            for canonical, (attr_id, unit_kind) in self.DRIVING_BEHAVIOR_PARAM_FIELDS.items():
                raw = float(behavior.AttValue(attr_id))
                if unit_kind == "length":
                    result[canonical] = self._length_com_to_m(raw, length_unit)
                elif unit_kind == "speed":
                    result[canonical] = self._speed_com_to_kmh(raw, speed_unit)
                else:
                    result[canonical] = raw
            return result
        except VissimObjectNotFoundError:
            raise
        except Exception as exc:
            raise RuntimeError(f"Could not read driving behavior {behavior_no}: {exc}") from exc

    def set_driving_behavior_parameter(self, behavior_no: int, parameter: str, value: float) -> dict[str, Any]:
        """Set ONE whitelisted scalar (``parameter`` -- a
        :data:`DRIVING_BEHAVIOR_PARAM_FIELDS` key) on a driving behavior,
        genuine canonical units in (meters/km/h), converted to the network's
        own display unit at the COM boundary. Returns
        :meth:`get_driving_behavior`'s full post-write dict so the caller
        always gets a complete readback to verify against.

        Raises whatever Vissim's own ``SetAttValue`` raises for a value
        outside its floor/ceiling (see this block's own module comment for
        the live-verified silent-failure map) -- ``constraints.py``'s own
        preflight checks exist so every known floor/ceiling violation is
        refused BEFORE ever reaching this call, with a structured, bilingual
        reason instead of this raw COM error string. Callers MUST NOT call
        this on a SHARED behavior (more than one link-behavior-type
        referencing it) -- that is a preflight refusal
        (``driving_behavior_shared_object_edit``), never enforced here.
        """
        field = self.DRIVING_BEHAVIOR_PARAM_FIELDS.get(parameter)
        if field is None:
            raise ValueError(f"Unknown driving-behavior parameter: {parameter!r}")
        attr_id, unit_kind = field
        try:
            behavior = self._item_by_key(
                self.vissim.Net.DrivingBehaviors,
                behavior_no,
                "Driving behavior",
                self.available_driving_behavior_nos(),
            )
            if unit_kind == "length":
                com_value = self._length_m_to_com(float(value), self._net_unit_length())
            elif unit_kind == "speed":
                com_value = self._speed_kmh_to_com(float(value), self._net_unit_speed())
            else:
                com_value = float(value)
            behavior.SetAttValue(attr_id, com_value)
        except VissimObjectNotFoundError:
            raise
        except Exception as exc:
            raise RuntimeError(
                f"Could not set driving behavior {behavior_no} {parameter}={value}: {exc}"
            ) from exc
        return self.get_driving_behavior(behavior_no)

    def duplicate_driving_behavior(self, source_no: int, new_name: str) -> dict[str, Any]:
        """Duplicate driving behavior ``source_no`` via the native
        ``DuplicateDrivingBehavior`` COM method (see this block's own module
        comment for why -- an EXACT, live-verified full attribute copy, never
        ``AddDrivingBehavior``'s default-template object), auto-assigning the
        next free number (``max(existing) + 1``), then renaming the copy to
        ``new_name``. Returns :meth:`get_driving_behavior`'s full dict for
        the NEW object (readback-verified: ``no`` is the new number, ``name``
        is ``new_name``) -- the copy has ZERO referencing consumers by
        construction (nothing points at a brand-new object number), so it is
        immediately eligible for :meth:`set_driving_behavior_parameter`.

        Raises whatever Vissim's own ``DuplicateDrivingBehavior``/
        ``SetAttValue('Name', ...)`` raise (e.g. a genuinely bogus
        ``source_no`` -- surfaced first via :meth:`_item_by_key`'s existence
        check on the SOURCE before ever calling ``DuplicateDrivingBehavior``,
        so a not-found source produces the same structured
        ``VissimObjectNotFoundError`` every other verb in this module uses,
        not a raw COM error naming the wrong object).
        """
        try:
            existing_nos = self.available_driving_behavior_nos()
            # Existence-check the SOURCE explicitly (via _item_by_key) before
            # calling DuplicateDrivingBehavior -- a bogus source_no passed
            # straight to Vissim's own method still raises, but with a less
            # specific error than this module's own VissimObjectNotFoundError
            # shape every other verb here already gives.
            self._item_by_key(
                self.vissim.Net.DrivingBehaviors, source_no, "Driving behavior", existing_nos
            )
            new_no = (max(existing_nos) if existing_nos else 0) + 1
            copy = self.vissim.Net.DrivingBehaviors.DuplicateDrivingBehavior(source_no, new_no)
            copy.SetAttValue("Name", new_name)
        except VissimObjectNotFoundError:
            raise
        except Exception as exc:
            raise RuntimeError(
                f"Could not duplicate driving behavior {source_no} as {new_name!r}: {exc}"
            ) from exc
        return self.get_driving_behavior(new_no)

    def available_link_behavior_type_nos(self) -> list[int]:
        return self._collection_nos(self.vissim.Net.LinkBehaviorTypes)

    def _link_behavior_type_veh_class_override(self, link_behavior_type: Any, veh_class_no: int) -> Any:
        """The link-behavior-type's EXISTING ``VehClassDrivBehav`` entry for
        ``veh_class_no``, or raises :class:`VissimObjectNotFoundError` naming
        every vehicle class that DOES have an override -- there is no
        ``ItemByKey`` on this child collection (verified live), and creating
        a brand-new override is a reproducible pywin32 landmine (see this
        block's own module comment) this module deliberately does not
        attempt -- only an EXISTING override can be reassigned."""
        overrides = list(link_behavior_type.VehClassDrivBehav.GetAll())
        for override in overrides:
            if int(override.AttValue("VehClass")) == veh_class_no:
                return override
        available = sorted(int(o.AttValue("VehClass")) for o in overrides)
        raise VissimObjectNotFoundError(
            f"Link-behavior-type {int(link_behavior_type.AttValue('No'))} has no existing "
            f"per-vehicle-class override for vehicle class {veh_class_no}. Available override "
            f"vehicle classes: {_format_available_keys(available)}. Creating a brand-new "
            "override is not supported by this agent (a known COM interface limitation) -- "
            "only an existing override can be reassigned."
        )

    def get_link_behavior_type_driving_behavior(
        self, link_behavior_type_no: int, veh_class_no: int | None = None
    ) -> dict[str, Any]:
        """The driving-behavior number currently assigned as
        ``link_behavior_type_no``'s DEFAULT (``veh_class_no=None``) or as its
        EXISTING per-vehicle-class override (``veh_class_no`` given)."""
        try:
            link_behavior_type = self._item_by_key(
                self.vissim.Net.LinkBehaviorTypes,
                link_behavior_type_no,
                "Link behavior type",
                self.available_link_behavior_type_nos(),
            )
            if veh_class_no is None:
                return {
                    "link_behavior_type_no": str(link_behavior_type.AttValue("No")),
                    "veh_class_no": None,
                    "driving_behavior_no": str(link_behavior_type.AttValue("DrivBehavDef")),
                }
            override = self._link_behavior_type_veh_class_override(link_behavior_type, veh_class_no)
            return {
                "link_behavior_type_no": str(link_behavior_type.AttValue("No")),
                "veh_class_no": str(veh_class_no),
                "driving_behavior_no": str(override.AttValue("DrivBehav")),
            }
        except VissimObjectNotFoundError:
            raise
        except Exception as exc:
            raise RuntimeError(
                f"Could not read link behavior type {link_behavior_type_no} "
                f"(veh_class_no={veh_class_no}) driving-behavior assignment: {exc}"
            ) from exc

    def set_link_behavior_type_driving_behavior(
        self, link_behavior_type_no: int, driving_behavior_no: int, veh_class_no: int | None = None
    ) -> dict[str, Any]:
        """Assign ``driving_behavior_no`` as ``link_behavior_type_no``'s
        DEFAULT (``veh_class_no=None``, ``SetAttValue('DrivBehavDef', ...)``)
        or as its EXISTING per-vehicle-class override's target
        (``veh_class_no`` given, ``SetAttValue('DrivBehav', ...)`` on the
        matched override) -- the assignment half of the duplicate-then-edit
        flow: without this, a freshly duplicated-and-edited behavior is dead
        weight, referenced by nothing. Returns
        :meth:`get_link_behavior_type_driving_behavior`'s full dict for a
        readback-verified result.

        Raises whatever Vissim's own ``SetAttValue`` raises for a bogus
        ``driving_behavior_no`` (live-verified fails CLOSED: "Object N of
        type Driving Behavior does not exist") -- ``constraints.py``'s own
        preflight checks refuse this proactively with a structured reason;
        raises :class:`VissimObjectNotFoundError` (via
        :meth:`_link_behavior_type_veh_class_override`) when ``veh_class_no``
        is given but has no EXISTING override to reassign.
        """
        try:
            link_behavior_type = self._item_by_key(
                self.vissim.Net.LinkBehaviorTypes,
                link_behavior_type_no,
                "Link behavior type",
                self.available_link_behavior_type_nos(),
            )
            if veh_class_no is None:
                link_behavior_type.SetAttValue("DrivBehavDef", driving_behavior_no)
            else:
                override = self._link_behavior_type_veh_class_override(link_behavior_type, veh_class_no)
                override.SetAttValue("DrivBehav", driving_behavior_no)
        except VissimObjectNotFoundError:
            raise
        except Exception as exc:
            raise RuntimeError(
                f"Could not set link behavior type {link_behavior_type_no} "
                f"(veh_class_no={veh_class_no}) driving-behavior assignment to "
                f"{driving_behavior_no}: {exc}"
            ) from exc
        return self.get_link_behavior_type_driving_behavior(link_behavior_type_no, veh_class_no)

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
