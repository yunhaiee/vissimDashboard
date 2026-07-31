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
