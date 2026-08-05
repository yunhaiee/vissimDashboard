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


class VissimParkingLotTypeError(ValueError):
    """Q6 unit (2): a parking verb was pointed at a lot whose SPECIES it
    cannot serve. The object exists (so it is not an
    :class:`VissimObjectNotFoundError`) and the write would not raise over
    COM either -- it would simply not do what the caller asked, which is the
    class of silent wrongness this layer exists to refuse. See
    :meth:`VissimController.set_parking_lot_capacity` for the measured
    type-scoping (573/573 vs 56/56)."""


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
#                                                    -> MOVEMENT_EvaluationTimeInterval
#   vehicle_travel_time           -> <vehTravTms>    -> VEHICLETRAVELTIMEMEASUREMENT_EvaluationTimeIntervalClass
#   queue_counters                -> <queues>        -> QUEUECOUNTER_EvaluationTimeInterval
#
# CORRECTION (unit U1, 2026-08-05): the last two rows used to read
# "queue_counters -> <queues> -> MOVEMENT_EvaluationTimeInterval". That was
# wrong, and measurably so — 136 real result .dbs in this checkout settle it
# read-only, no COM needed:
#   * 70 customer-model .dbs whose network arms <nodeResults collectData="true">
#     and leaves <queues collectData="false"> carry MOVEMENT_EvaluationTimeInterval
#     with 62-67 rows and NO QUEUECOUNTER_EvaluationTimeInterval table at all;
#   * 48 vendor-example .dbs produced by this job runner (which arms all four
#     types) carry QUEUECOUNTER_EvaluationTimeInterval with 25 rows and
#     MOVEMENT_EvaluationTimeInterval present but EMPTY — those example
#     networks define no nodes, so nodeResults creates the table and fills
#     nothing.
# MOVEMENT_EvaluationTimeInterval — the per-movement QLEN/QLENMAX table
# results_reader._read_db reads — is a nodeResults product. Do not "fix"
# that SQL to point at QUEUECOUNTER_*: it would read zero rows on the only
# family of networks in this checkout that has queue data at all. The COM
# attribute ids below are untouched by this correction; they were verified
# live and only the prose was wrong.
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

    # -----------------------------------------------------------------
    # Q5 unit (3): vehicle-input GENESIS -- the LAST item on the GENESIS
    # GAP LIST (docs/phase2/DECISIONS.md, 2026-08-01). Live findings, all
    # from backend/tools/probe_vehicle_input_create.py (2026-08-03, Vissim
    # 2026 SP09, seven parts across four example networks) unless
    # attributed to an earlier unit:
    #
    # - ``Net.VehicleInputs.AddVehicleInput(Key, Link)`` is the REAL path
    #   and needs no duplicate-then-edit dance. This is the FIRST creation
    #   verb in this codebase where plain ``Add`` is not a trap:
    #   ``AddPriorityRule`` was a silent no-op (Q4 unit 6),
    #   ``AddSignalController`` a non-functional skeleton (Q5 unit 1/2),
    #   and both needed a ``Duplicate*`` instead. ``DuplicateVehicleInput``
    #   does exist here too and is deliberately NOT wrapped -- there is
    #   nothing it gives that Add + a volume does not.
    # - **The ``Link`` argument needs a real COM OBJECT** (the pywin32
    #   marshalling landmine Q4 unit (2)'s measurement-infra block already
    #   recorded): a raw int/str raises ``TypeError: The Python instance
    #   can not be converted to a COM object``, a Lane object raises
    #   ``Type mismatch.``, ``None`` raises "Argument Link must be
    #   provided.", and the 1-argument form raises "Parameter not
    #   optional." -- each proven to create NOTHING by its own ``GetAll()``
    #   id-set diff. Nothing here fails silently, so
    #   :meth:`_link_object_by_no` (reused verbatim, not reimplemented) is
    #   the whole guard needed.
    # - **A fresh input's defaults:** ``Name=''``, ``Volume(1)=0.0``,
    #   ``VehComp(1)='1'``, ``VolType(1)='STOCHASTIC'``, ``Cont(1)=0``. A
    #   created-and-left-alone input therefore generates NOTHING, which is
    #   why ``volume_vph`` is a required parameter here, not an optional
    #   one.
    # - **No family limitation.** A network with ZERO vehicle inputs CAN
    #   mint its first (live-proven on ``Air Taxi Vertiport``: ``GetAll()``
    #   ``[] -> [1]``, saved file reloads ``load_ok``/``init_ok``) --
    #   unlike :meth:`add_priority_rule` and :meth:`add_signal_controller`,
    #   which are duplicate-based and genuinely stuck without a donor. That
    #   is what makes "build an intersection from nothing" reachable.
    # - **Two shadow-model classes gain rows, not one.** One created input
    #   adds a ``vehicle_inputs`` row everywhere, AND -- on a network with
    #   no ``<vehicleComposition>`` at all -- a ``vehicle_composition_
    #   entries`` row too, because saving MATERIALISES the default
    #   composition 1 the input references. Hence
    #   ``guardrails._ALLOWS_NEW_OBJECTS_IN["add_vehicle_input"] =
    #   {"vehicle_inputs", "vehicle_composition_entries"}``.
    # - **A CONNECTOR target is accepted and genuinely generates traffic**
    #   (measured against a null control: same network, all demand zeroed,
    #   0 vehicles with no input vs 13 with one, on either a plain link or
    #   a connector). It is NOT refused here or in constraints.py -- it is
    #   WARNED about (``vehicle_input_on_connector``), because what
    #   actually differs is that demand enters mid-junction and bypasses
    #   the upstream approach entirely.
    # - ``No`` reads back as a python ``int`` on this collection (unlike
    #   ``SignalControllers``, which hands back a float). The ``int()`` in
    #   :meth:`get_vehicle_input` is a defensive house convention matching
    #   :meth:`_collection_nos`, not a fix for anything observed here.
    # -----------------------------------------------------------------

    def get_vehicle_input(self, input_no: int) -> dict[str, Any]:
        """Read one vehicle input's identity + interval-1 demand:
        ``{"no", "name", "link", "volume", "composition_no",
        "volume_type"}``.

        The paired reader for :meth:`add_vehicle_input` (guardrail
        doctrine: every setter needs a reader used for post-write
        verification). Complements the two narrower pre-existing readers
        (:meth:`get_vehicle_input_volume`, :meth:`get_vehicle_input_continuous`)
        rather than replacing them: those answer "what is interval i's
        volume/continuity", this one answers "what IS this object".

        ``volume``/``composition_no``/``volume_type`` are interval 1's --
        the same interval :meth:`set_vehicle_input` defaults to. An input
        on a multi-interval network carries more; the caller
        (``tools_edit.add_vehicle_input``) reports the real interval count
        from a reparse of the persisted file, since COM exposes no
        interval-count attribute this method could read.
        """
        try:
            vehicle_input = self._item_by_key(
                self.vissim.Net.VehicleInputs,
                input_no,
                "Vehicle input",
                self.available_vehicle_input_nos(),
            )
            return {
                # READ BACK, never echo the argument. ``add_vehicle_input``
                # returns this dict for a number it COMPUTED (max+1), and
                # ``artifacts.new_vehicle_input_no`` is taken straight from
                # here -- echoing ``input_no`` would make the verb's own
                # identity claim unfalsifiable, which is exactly the
                # verify-by-state-diff-never-return-value rule this layer
                # exists to enforce. (Q5 unit (2) shipped precisely that
                # defect in ``get_signal_controller`` and had to fix it.)
                # ``int()`` mirrors :meth:`_collection_nos`'s own guard so a
                # float-typed ``No`` could never render as "13.0" and miss
                # every shadow-model id keyed "13".
                "no": str(int(vehicle_input.AttValue("No"))),
                "name": str(vehicle_input.AttValue("Name") or ""),
                "link": str(vehicle_input.AttValue("Link") or ""),
                "volume": float(vehicle_input.AttValue("Volume(1)")),
                "composition_no": str(vehicle_input.AttValue("VehComp(1)") or ""),
                "volume_type": str(vehicle_input.AttValue("VolType(1)") or ""),
            }
        except VissimObjectNotFoundError:
            raise
        except Exception as exc:
            raise RuntimeError(f"Could not read vehicle input {input_no}: {exc}") from exc

    def add_vehicle_input(
        self,
        link_no: int,
        volume_vph: float,
        name: str = "",
        composition_no: int | None = None,
    ) -> dict[str, Any]:
        """Create a vehicle input on ``link_no`` carrying ``volume_vph``
        veh/h in interval 1, auto-assigning the next free number
        (``max(existing) + 1``, or 1 on a network with none -- mirroring
        :meth:`add_queue_counter`/:meth:`duplicate_driving_behavior`).

        ``volume_vph`` is REQUIRED, not optional: a fresh input's own
        ``Volume(1)`` default is 0.0 (live-measured), i.e. an input created
        and left alone generates nothing at all -- the exact shape of
        silent failure this layer exists to prevent.

        ``composition_no`` is optional; omitted leaves Vissim's own default
        (``VehComp(1) = '1'``). On a network that has no
        ``<vehicleComposition>`` yet, saving materialises composition 1
        automatically (see this block's own module comment).

        Returns :meth:`get_vehicle_input`'s dict for the NEW input. Raises
        :class:`VissimObjectNotFoundError` for a bogus ``link_no`` (checked
        via :meth:`_link_object_by_no` before COM is asked, so the error
        names the real available link numbers).
        """
        try:
            link = self._link_object_by_no(link_no)
            existing = self.available_vehicle_input_nos()
            new_no = (max(existing) if existing else 0) + 1
            vehicle_input = self.vissim.Net.VehicleInputs.AddVehicleInput(new_no, link)
            if name:
                vehicle_input.SetAttValue("Name", name)
            if composition_no is not None:
                vehicle_input.SetAttValue("VehComp(1)", composition_no)
            vehicle_input.SetAttValue("Volume(1)", float(volume_vph))
        except VissimObjectNotFoundError:
            raise
        except Exception as exc:
            raise RuntimeError(
                f"Could not add a vehicle input on link {link_no} at {volume_vph} veh/h: {exc}"
            ) from exc
        return self.get_vehicle_input(new_no)

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

    # -----------------------------------------------------------------
    # Q5 unit (2): signal-controller GENESIS -- duplicate-then-edit, the
    # FOURTH appearance of this codebase's now-named house pattern (driving
    # behaviors Q3 unit 6, priority rules Q4 unit 6, controllers Q4 unit
    # 8/Q5 unit 1). Live findings, all from
    # backend/tools/probe_signal_controller_duplicate.py (2026-08-03, Vissim
    # 2026 SP09) unless attributed to an earlier unit:
    #
    # - ``Net.SignalControllers.AddSignalController(Key)`` +
    #   ``.SGs.AddSignalGroup(Key)`` DO create real objects, but the result
    #   is a NON-FUNCTIONAL skeleton: a controller with no real timing
    #   program makes ``RunSingleStep`` fail outright (Q4 unit 8 / Q5 unit
    #   1, live-verified twice). Plain ``Add`` is the trap; it is
    #   deliberately NOT wrapped by any verb here.
    # - ``DuplicateSignalController(Source, NewKey)`` is the real path, and
    #   it accepts BOTH argument shapes -- an int key (the
    #   ``DuplicateDrivingBehavior`` convention) AND a COM object (the
    #   ``DuplicatePriorityRule`` convention). Both were tried independently
    #   in one session, each proven by its own ``GetAll()`` id-set diff
    #   (never by a bare non-exception -- ``AddPriorityRule``'s silent-no-op
    #   precedent). This method uses the INT form, so the source is
    #   existence-checked by :meth:`_item_by_key` first and a bogus number
    #   raises this module's own structured ``VissimObjectNotFoundError``.
    # - The copy is an EXACT, INDEPENDENT copy: Type, CycTm, Offset, ProgNo,
    #   every signal-group number and the whole ``IntSupplyData`` program
    #   blob (byte-length-identical) come across, **including the donor's
    #   NAME** -- which is why this method renames in the same call rather
    #   than leaving two identically-named controllers behind. Renaming the
    #   copy does NOT touch the donor (re-read live; an alias would have
    #   shown the new name). The copy survives ``RunSingleStep`` and its own
    #   green splits are immediately editable via
    #   :meth:`set_signal_green_split` -- a real edit against the copy moved
    #   exactly one ``signal_program_splits`` row, the copy's own.
    # - **Family limitation, stated not engineered around:** a network with
    #   ZERO signal controllers cannot get its first from this verb. Proven
    #   live on ``Roundabout Billings.US`` (0 controllers): a nonexistent
    #   int source raises "Object 1 of type Signal Controller does not
    #   exist.", a ``None`` source raises "Empty values are not permitted
    #   here.", and ``GetAll()`` is still empty after both. Identical shape
    #   to :meth:`add_priority_rule`'s own recorded limitation.
    # - **Ordering landmine:** structural controller edits are refused once
    #   a simulation has started -- after one ``RunSingleStep``,
    #   ``RemoveSignalController`` raised "This action cannot be performed
    #   during simulation." Harmless here (an edit session never runs a
    #   simulation) but real for any future build-then-run job that reuses
    #   one COM session.
    # - Non-fixed-time donors are refused at the CONSTRAINTS layer, not
    #   here (constraints.py's signal_controller_duplicate_source_type_
    #   unsupported): COM itself permits duplicating a
    #   RINGBARRIERCONTROLLER/VAP/... controller, but such a controller's
    #   program lives in an external file (all 49 of Boise's RBC controllers
    #   carry ``supplyFile1='#data#<no>.prbc'`` and NO ``intSupplyData``
    #   attribute at all -- absent, not present-and-empty; the token occurs
    #   0 times in the whole Boise .inpx), ``signal_parser`` yields ZERO
    #   ``signal_program_splits`` rows for the whole family, and constraint
    #   #4 already refuses every timing edit on it -- i.e. the copy would be
    #   both unverifiable and permanently uneditable. That is a policy
    #   decision from measured data, recorded in this unit's DECISIONS entry.
    # -----------------------------------------------------------------

    def get_signal_controller(self, sc_no: int) -> dict[str, Any]:
        """Read one signal controller's identity + top-level timing
        attributes: ``{"no", "name", "type", "cycle_time_s", "offset_s",
        "program_no", "signal_group_nos"}``.

        The paired reader for :meth:`add_signal_controller` (guardrail
        doctrine: every setter needs a reader used for post-write
        verification). ``cycle_time_s`` is the same ``CycTm`` rollup
        :meth:`get_signal_cycle` reads -- on a program-driven fixed-time
        controller it is NOT the effective simulated cycle (see
        :meth:`set_signal_cycle`'s docstring; Beijing's own controller is
        ``cycTmIsVar="true"`` and genuinely stores 0.0 there), so it is
        reported as-is and never presented as the real cycle.
        """
        try:
            signal_controller = self._item_by_key(
                self.vissim.Net.SignalControllers,
                sc_no,
                "Signal controller",
                self.available_signal_controller_nos(),
            )
            raw_cycle = signal_controller.AttValue("CycTm")
            raw_offset = signal_controller.AttValue("Offset")
            raw_prog = signal_controller.AttValue("ProgNo")
            return {
                # READ BACK, never echo the argument. ``add_signal_controller``
                # returns this dict for a number it COMPUTED (max+1), and
                # ``artifacts.new_sc_no`` is taken straight from here -- echoing
                # ``sc_no`` would make the verb's own identity claim
                # unfalsifiable, which is exactly the
                # verify-by-state-diff-never-return-value rule this layer
                # exists to enforce. ``int()`` first because COM hands ``No``
                # back as a float on some collections (:meth:`_collection_nos`
                # does the same), and a "2.0" here would silently miss every
                # shadow-model id keyed "2".
                "no": str(int(signal_controller.AttValue("No"))),
                "name": str(signal_controller.AttValue("Name") or ""),
                "type": str(signal_controller.AttValue("Type") or ""),
                "cycle_time_s": None if raw_cycle is None else float(raw_cycle),
                "offset_s": None if raw_offset is None else float(raw_offset),
                "program_no": None if raw_prog in (None, "") else str(raw_prog),
                "signal_group_nos": [str(no) for no in self._collection_nos(signal_controller.SGs)],
            }
        except VissimObjectNotFoundError:
            raise
        except Exception as exc:
            raise RuntimeError(f"Could not read signal controller {sc_no}: {exc}") from exc

    def add_signal_controller(self, source_sc_no: int, new_name: str) -> dict[str, Any]:
        """Create a new signal controller as an exact duplicate of
        ``source_sc_no`` (native ``DuplicateSignalController``, int-key
        form -- see this block's own module comment for why plain
        ``AddSignalController`` is a trap), auto-assigning the next free
        number (``max(existing) + 1``, mirroring
        :meth:`duplicate_driving_behavior`/:meth:`add_priority_rule`), then
        renaming the copy to ``new_name`` (the copy inherits the donor's own
        name otherwise).

        Returns :meth:`get_signal_controller`'s dict for the NEW controller.

        Raises :class:`VissimObjectNotFoundError` for a bogus
        ``source_sc_no`` (checked before the duplicate call, so the error
        names the real available numbers) and :class:`RuntimeError` when the
        network has NO signal controller to duplicate at all -- the stated
        genesis limitation, never a silent failure.
        """
        try:
            existing_nos = self.available_signal_controller_nos()
            if not existing_nos:
                raise RuntimeError(
                    "add_signal_controller: this network has no existing signal controller to "
                    "duplicate. Net.SignalControllers.AddSignalController does create a bare "
                    "controller, but one with no real timing program makes the simulation fail "
                    "outright (live-verified), and DuplicateSignalController needs a real source "
                    "-- genesis of the FIRST signal controller in a network with none today is "
                    "not supported by this verb."
                )
            # Existence-check the SOURCE explicitly before calling Vissim's
            # own method, so a bogus number produces this module's
            # structured not-found error rather than a raw COM one
            # (duplicate_driving_behavior's precedent).
            self._item_by_key(
                self.vissim.Net.SignalControllers,
                source_sc_no,
                "Signal controller",
                existing_nos,
            )
            new_no = max(existing_nos) + 1
            copy = self.vissim.Net.SignalControllers.DuplicateSignalController(source_sc_no, new_no)
            copy.SetAttValue("Name", new_name)
        except (VissimObjectNotFoundError, RuntimeError):
            raise
        except Exception as exc:
            raise RuntimeError(
                f"Could not duplicate signal controller {source_sc_no} as {new_name!r}: {exc}"
            ) from exc
        return self.get_signal_controller(new_no)

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

    # ---------------------------------------------------------------------
    # Q6 unit (1): link GEOMETRY edits on an EXISTING link -- lane count,
    # polyline shape/length, curvature. Q4 unit (5) could only CREATE and
    # REMOVE whole links; this block reshapes one that is already there, and
    # every claim below is measured by ``backend/tools/probe_link_geometry.py``
    # (state file: ``runtime/link-geometry-probe/state.json``), on Urban
    # Intersection Beijing.CN (METERS) and Toll Plaza Camden.US (FEET), with a
    # NULL CONTROL part proving an untouched load+save moves nothing at all
    # (0 links moved, 0 lane changes, empty ``diff_shadow_models``) -- so every
    # diff attributed below is attributable.
    #
    # **Landmine 1 (the lane count is NOT an attribute).**
    # ``SetAttValue("NumLanes", n)`` fails LOUDLY -- "put_AttValue failed - the
    # attribute NUMLANES is not editable" -- and the lane index set is
    # unchanged after it (judged by a ``Lanes.GetAll()`` Index-set diff, never
    # by the absence of an exception). The real primitive is
    # ``ILaneContainer.AddLane(Key, Width)`` / ``RemoveLane(Lane)``, both
    # live-proven on EXISTING links (Beijing link 4: 4 -> 5 lanes; link 8:
    # 4 -> 3). This CORRECTS the pre-M0.3 catalog note "there is no per-lane
    # add/remove on an existing link" that ``constraints.
    # lane_count_edit_unsupported`` was built on -- see that constraint's own
    # updated comment.
    #
    # **Landmine 2 (every lane-count change silently MOVES the link's
    # centerline by half the lane width, perpendicular).** Measured three
    # times, on two networks and in both directions: Beijing link 4 +1 lane of
    # 3.5 m -> every persisted point moved 1.75 m; link 8 -1 lane of 3.0 m ->
    # 1.5 m; Camden link 1 +1 lane of 1.0668 m -> a uniform 0.5334 m
    # translation of the WHOLE polyline. ``Length2D`` never changes. Vissim
    # keeps the link's lanes centered on its polyline, so widening/narrowing
    # re-centers the polyline itself -- a real geometry change nobody asked
    # for, which is why ``set_link_lane_count`` reports the measured shift and
    # ``constraints.link_lane_count_shifts_centerline`` warns about it.
    #
    # **Landmine 3 (RemoveLane SILENTLY CASCADE-DELETES whatever is anchored
    # on the lane).** The taxonomy's third species (Q4 unit (7)'s
    # ``RemovePTStop`` precedent), now proven for lanes by a controlled
    # CONTROL/TREATMENT pair in ONE session: a data-collection point and a
    # signal head created on Beijing link 4 lane 4 are both present in the
    # control save (53 heads / 32 points) and both GONE from the treatment
    # save (52 / 31) after a single ``RemoveLane`` -- COM's own ``ItemByKey``
    # says "object does not exist" for each immediately after the call, no
    # exception is raised, and ``load_check`` on the result returns
    # load_ok/init_ok, so Vissim never objects either. This module does NOT
    # guard against it (that would be a second, competing source of truth
    # against the XML-based one); ``tools_edit.set_link_lane_count`` refuses
    # first, naming every referrer, via the SAME ``services.inpx_structure``
    # spine ``remove_link``/``remove_connector`` already use.
    #
    # **Landmine 4 (points are writable one coordinate at a time; the point
    # COUNT is not writable at all).** ``X``/``Y``/``ZOffset`` are all
    # ``SetAttValue``-able with a genuine readback, and ``Length2D`` follows
    # exactly (a half-length write took 165.606 m -> 82.803 m, ratio 0.5000).
    # But ``ILinkPolyPointContainer``/``ILinkPolyPointCollection`` expose NO
    # Add/Remove/Insert of any kind (type-library scan + a live ``dir()``),
    # and there is no ``WktLinestring``/``WKT``/``Geometry`` attribute on
    # ``ILink`` at all (all five candidate spellings raise "Could not find
    # attribute") -- WKT exists ONLY as an ``AddLink``/``AddConnector``
    # argument. So COM can RESHAPE a link but never re-point it:
    # :meth:`set_link_geometry` requires exactly as many points as the link
    # already has, and refuses otherwise rather than silently truncating.
    #
    # **Landmine 5 (shortening a link silently RELOCATES everything anchored
    # beyond the new end).** Two independent live measurements: a
    # data-collection point at pos 132.485 m on a link shortened to 82.803 m
    # came back at pos 82.80285441921609 -- exactly the new end -- and
    # persisted that way; and Beijing link 5 (452.06 m) shortened to 226.03 m
    # clamped ALL THREE connectors anchored at its far end from FromPos 452.06
    # to 226.03 AND stretched their own geometry to reach, turning connector
    # 10006 from an 11.71 m turn into a 237.70 m one (10008: 12.88 -> 238.86).
    # ``load_check`` passes, conflict areas stay at 224, nothing anywhere
    # reports it. This is the flagship silent failure of this unit and the
    # reason ``tools_edit.set_link_geometry`` refuses a shortening that would
    # move any anchored object.
    #
    # **Landmine 6 (curvature is not spline data -- it is a per-point radius
    # OVERRIDE, and it is a two-step write).** A link's geometry IS its
    # polyline; each point carries ``Rad`` + ``RadiusSrc`` + ``RadiusEffect``
    # (the persisted ``rad``/``radiusSrc``/``radiusEffect`` attributes;
    # 16 082 of 60 420 corpus points are already ``USERINPUT``). ``Rad`` is
    # REFUSED LOUDLY while the source is ``CALCULATED`` ("AttValue failed:
    # Object : Attribute Radius cannot be changed") and becomes writable only
    # after ``RadiusSrc='USERINPUT'`` -- proven live (24292.24 -> 222.0,
    # persisted ``rad="222" radiusSrc="USERINPUT"``), with x/y and Length2D
    # unchanged. Endpoints read ``Rad`` as ``None`` on every point measured,
    # so only INTERIOR points are addressable. ``ILink.RecalculateSpline()``
    # exists and is deliberately NOT wrapped: it REDISTRIBUTES the
    # intermediate points evenly (Beijing link 12's middle point moved ~30 m;
    # spacing 27.4/87.5 -> 57.4/57.4) with no return value, and
    # ``RecalculateSplineHeight()`` zeroed a ZOffset that had just been
    # written -- both are geometry mutations disguised as recomputes.
    #
    # **Landmine 7 (units -- x/y are the exception, in BOTH directions, and z
    # is NOT).** On Camden (FEET) a point's COM ``X``/``Y`` equal the
    # persisted XML x/y exactly (ratio 1.0 on every coordinate measured), and
    # a written ``X`` lands raw -- confirming, in the write direction and on a
    # FEET network, the coordinate-space landmine Q4 unit (5) could only infer
    # there. EVERYTHING ELSE on this surface is display-unit-scaled, each
    # measured on the same network: ``Length2D`` reads 200.0906 against a
    # 60.9876 m polyline (ratio 3.280840 = 1/0.3048), ``AddLane(4, 3.5)``
    # passed RAW persisted ``width="1.0668"`` (3.5 FEET), ``Rad`` reads 940.95
    # against a persisted ``rad`` of 286.802 (same ratio), and ``ZOffset``
    # reads 22.0013 against a persisted ``zOffset`` of 6.706 (same ratio) --
    # so x and y are raw metres while z, on the SAME point object, is not.
    # ---------------------------------------------------------------------

    def get_link_geometry(self, no: int) -> dict[str, Any]:
        """Full geometry readback for one link/connector: ``{"no", "name",
        "is_connector", "lanes", "lane_widths" (metres), "length" (metres),
        "point_count", "points"}`` where each point is ``{"index" (1-based),
        "x", "y" (RAW METRES -- landmine 7), "z" (metres), "radius" (metres
        or ``None`` when Vissim has none for that point), "radius_src",
        "radius_effect"}``.

        The paired reader for all three geometry setters below. ``"no"`` is a
        genuine ``int(AttValue("No"))`` readback off the resolved COM object,
        never the argument echoed back (Q5 unit (2)'s ``get_signal_controller``
        defect: a reader that echoes its own argument makes the setter's
        identity claim unfalsifiable by construction; ``int()`` because COM
        returns ``No`` as a float on some collections).
        """
        try:
            link = self._item_by_key(self.vissim.Net.Links, no, "Link", self.available_link_nos())
            unit = self._net_unit_length()
            points: list[dict[str, Any]] = []
            for index, point in enumerate(link.LinkPolyPts.GetAll(), start=1):
                radius_raw = point.AttValue("Rad")
                points.append(
                    {
                        "index": index,
                        # RAW METRES, never converted -- landmine 7.
                        "x": float(point.AttValue("X")),
                        "y": float(point.AttValue("Y")),
                        "z": self._length_com_to_m(float(point.AttValue("ZOffset") or 0.0), unit),
                        "radius": (
                            self._length_com_to_m(float(radius_raw), unit)
                            if radius_raw not in (None, "")
                            else None
                        ),
                        "radius_src": str(point.AttValue("RadiusSrc") or ""),
                        "radius_effect": str(point.AttValue("RadiusEffect") or ""),
                    }
                )
            return {
                "no": int(link.AttValue("No")),
                "name": str(link.AttValue("Name") or ""),
                "is_connector": bool(int(link.AttValue("IsConn") or 0)),
                "lanes": int(link.AttValue("NumLanes")),
                "lane_widths": [
                    self._length_com_to_m(float(lane.AttValue("Width")), unit)
                    for lane in link.Lanes.GetAll()
                ],
                "length": self._length_com_to_m(float(link.AttValue("Length2D")), unit),
                "point_count": len(points),
                "points": points,
            }
        except VissimObjectNotFoundError:
            raise
        except Exception as exc:
            raise RuntimeError(f"Could not read the geometry of link {no}: {exc}") from exc

    def set_link_lane_count(
        self, link_no: int, lane_count: int, lane_width_m: float | None = None
    ) -> dict[str, Any]:
        """Grow or shrink an EXISTING link to exactly ``lane_count`` lanes via
        ``ILaneContainer.AddLane``/``RemoveLane`` (landmine 1 -- ``NumLanes``
        itself is read-only). New lanes are appended at the highest index and
        get ``lane_width_m`` metres, defaulting to the link's own CURRENT last
        lane width so a widened link stays self-consistent rather than
        inheriting an unrelated 3.5 m default. Shrinking removes the HIGHEST
        indices first, which is what makes the operation expressible at all --
        a lane has no identity besides its ordinal, so removing an interior
        one would silently re-number every lane above it (the same reasoning
        ``services.inpx_structure.remove_last_lane`` already documents).

        **Removal SILENTLY CASCADE-DELETES anything anchored on the removed
        lane** (landmine 3, live-proven). This method does NOT guard against
        that -- ``tools_edit.set_link_lane_count`` refuses first, naming every
        referrer. Returns :meth:`get_link_geometry`'s shape for the link,
        read back from COM (so the caller can see the centerline shift
        landmine 2 describes, not merely the new lane count).

        The lane collection is re-fetched on every iteration: Q4 unit (6)
        live-verified that HELD child-collection references under-report
        after writes.
        """
        try:
            link = self._item_by_key(
                self.vissim.Net.Links, link_no, "Link", self.available_link_nos()
            )
            unit = self._net_unit_length()
            current = self.available_lane_indices(link_no)
            if lane_width_m is None:
                widths = [float(lane.AttValue("Width")) for lane in link.Lanes.GetAll()]
                width_com = widths[-1] if widths else self._length_m_to_com(3.5, unit)
            else:
                width_com = self._length_m_to_com(float(lane_width_m), unit)

            while len(self.available_lane_indices(link_no)) < lane_count:
                indices = self.available_lane_indices(link_no)
                next_index = (max(indices) if indices else 0) + 1
                link.Lanes.AddLane(next_index, width_com)
            while len(self.available_lane_indices(link_no)) > lane_count:
                indices = self.available_lane_indices(link_no)
                link.Lanes.RemoveLane(link.Lanes.ItemByKey(max(indices)))

            final = self.available_lane_indices(link_no)
            if len(final) != lane_count:
                raise RuntimeError(
                    f"lane count is {len(final)} (indices {final}) after asking for {lane_count} "
                    f"-- started from {current}"
                )
        except VissimObjectNotFoundError:
            raise
        except Exception as exc:
            raise RuntimeError(
                f"Could not set link {link_no} to {lane_count} lane(s): {exc}"
            ) from exc
        return self.get_link_geometry(link_no)

    def set_link_geometry(
        self, link_no: int, points_xy: list[tuple[float, float]]
    ) -> dict[str, Any]:
        """Move an EXISTING link's polyline points to ``points_xy`` (RAW
        METRES -- landmine 7), in order, one ``SetAttValue`` per coordinate.

        ``points_xy`` MUST have exactly as many entries as the link already
        has points: COM offers no way to add or remove a polyline point at
        all (landmine 4), so any other count is refused here rather than
        silently applied to a prefix. Each point is re-fetched by index
        rather than held across writes (Q4 unit (6)'s held-reference
        landmine). Returns :meth:`get_link_geometry`'s shape, so the caller
        sees the recomputed ``length`` rather than assuming it.
        """
        try:
            link = self._item_by_key(
                self.vissim.Net.Links, link_no, "Link", self.available_link_nos()
            )
            existing = len(list(link.LinkPolyPts.GetAll()))
            if existing != len(points_xy):
                raise ValueError(
                    f"link {link_no} has {existing} polyline point(s) but {len(points_xy)} were "
                    "supplied -- Vissim COM cannot add or remove a link's polyline points "
                    "(ILinkPolyPointContainer has no Add/Remove at all, and there is no writable "
                    "WKT attribute on a link); supply exactly as many points as the link has, or "
                    "build a new link with add_link."
                )
            for index, (x, y) in enumerate(points_xy):
                point = list(link.LinkPolyPts.GetAll())[index]
                point.SetAttValue("X", float(x))
                point.SetAttValue("Y", float(y))
        except (VissimObjectNotFoundError, ValueError):
            raise
        except Exception as exc:
            raise RuntimeError(
                f"Could not set the geometry of link {link_no} to {points_xy!r}: {exc}"
            ) from exc
        return self.get_link_geometry(link_no)

    def set_link_point_radius(
        self, link_no: int, point_index: int, radius_m: float | None
    ) -> dict[str, Any]:
        """Override (or restore) the curve radius Vissim uses at one INTERIOR
        polyline point (landmine 6).

        ``radius_m`` in metres sets ``RadiusSrc='USERINPUT'`` FIRST and then
        ``Rad`` -- in that order, because Vissim refuses the value write
        outright while the source is ``CALCULATED``. ``radius_m=None``
        restores ``RadiusSrc='CALCULATED'``, handing the radius back to
        Vissim's own polyline-derived computation (the ``Rad`` value is NOT
        cleared explicitly: it is not writable once the source is CALCULATED
        again, and Vissim recomputes it).

        ``point_index`` is 1-based and must be an INTERIOR point: the first
        and last point of a polyline have no curve radius at all (measured:
        ``Rad`` reads ``None`` on every endpoint probed). Returns
        :meth:`get_link_geometry`'s shape.
        """
        try:
            link = self._item_by_key(
                self.vissim.Net.Links, link_no, "Link", self.available_link_nos()
            )
            unit = self._net_unit_length()
            points = list(link.LinkPolyPts.GetAll())
            if not 1 <= point_index <= len(points):
                raise VissimObjectNotFoundError(
                    f"Link {link_no} has no polyline point {point_index}. Available point "
                    f"indices for link {link_no}: {_format_available_keys(list(range(1, len(points) + 1)))}."
                )
            if point_index in (1, len(points)):
                raise ValueError(
                    f"point {point_index} is an END point of link {link_no}'s polyline -- only "
                    f"INTERIOR points (2..{len(points) - 1}) carry a curve radius in Vissim "
                    "(measured: an endpoint's Rad reads as None)."
                )
            point = points[point_index - 1]
            if radius_m is None:
                point.SetAttValue("RadiusSrc", "CALCULATED")
            else:
                point.SetAttValue("RadiusSrc", "USERINPUT")
                point.SetAttValue("Rad", self._length_m_to_com(float(radius_m), unit))
        except (VissimObjectNotFoundError, ValueError):
            raise
        except Exception as exc:
            raise RuntimeError(
                f"Could not set the curve radius of link {link_no} point {point_index} to "
                f"{radius_m!r}: {exc}"
            ) from exc
        return self.get_link_geometry(link_no)

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
    # Stop-sign + priority-rule PLACEMENT (creation) -- Q4 unit (6), the
    # genesis counterpart to QUEUE 3 unit (2)'s gap/headway SETTERS above.
    #
    # COM ids/behavior verified EMPIRICALLY live against Vissim 2026
    # (backend/tools/probe_stop_signs_priority_placement.py, Roundabout
    # Buehl.DE clone -- 61 real priority rules, 0 stop signs).
    #
    # **Stop signs.** ``Net.StopSigns.AddStopSign(Key, Lane, Pos)`` creates
    # and RETURNS the new object directly (unlike AddPriorityRule below) --
    # the same shape :meth:`add_signal_head`/:meth:`add_detector`/
    # :meth:`add_queue_counter` already use. ``AnmFlag`` ("animation flag")
    # is UNREADABLE on this dev box -- ``AttValue('AnmFlag')`` raises
    # "get_AttValue failed - module not active" (gated behind a licensed
    # add-on module this installation does not have) -- never touched by
    # this codebase, not exposed by :meth:`get_stop_sign`. ``SG`` (an
    # OPTIONAL signal-group link -- real .inpx attribute, present on a real
    # minority of stop signs across the 25-network corpus, e.g. 19/27 on
    # BRT Priority Texas.US, 0/112 on Signalization RBC Boise.US) reads back
    # ``None`` when unwired, and is wired the SAME way :meth:`add_signal_head`
    # wires a head's own SG -- ``SetAttValue('SG', <SG COM object>)``,
    # resolved via the existing :meth:`_signal_group_by_key` -- an optional,
    # all-or-nothing (sc_no, sg_no) pair, mirroring :meth:`add_detector`'s
    # own (sc_no, port_no) precedent.
    #
    # **Priority rules -- the load-bearing finding of this unit.**
    # ``Net.PriorityRules.AddPriorityRule(Key, Lane, Pos, ConflictMarkers)``
    # is a GENUINE SILENT NO-OP. Tried live, exhaustively: explicit ``Key``
    # and ``Key=None``; ``ConflictMarkers`` as a plain Python list of
    # ``(Lane, Pos)`` 2-tuples AND ``(Key, Lane, Pos)`` 3-tuples; and
    # explicit ``win32com.client.VARIANT(VT_ARRAY|VT_VARIANT, ...)`` nested
    # arrays. Every shape either RAISED (an empty/omitted ``ConflictMarkers``
    # raises "Argument ConflictMarkers is not an array" / "The number of
    # Conflict markers must be at least 1"; a malformed VARIANT nesting
    # raises "The array contains too few elements") or -- the dangerous
    # case -- returned ``None`` with NO exception at all while genuinely
    # creating nothing (verified against BOTH ``Net.PriorityRules.GetAll()``'s
    # own before/after id set AND the saved .inpx -- neither ever gained a
    # new rule). This is a WORSE silent failure than any prior unit's own
    # "COM does not enforce referential integrity" findings: the call looks
    # like ordinary success (no exception, a legal-looking ``None`` return
    # matching several OTHER Add* methods' own "returns None, read back
    # separately" convention elsewhere in this codebase).
    #
    # The real creation path is ``DuplicatePriorityRule(Source, NewKey)`` --
    # copies an EXISTING rule (attributes AND its own conflict markers)
    # wholesale, mirroring the ``duplicate_driving_behavior`` genesis
    # pattern QUEUE 3 unit 6 already established for driving behaviors
    # (also an object family with no from-scratch Add at all). This means
    # :meth:`add_priority_rule` REQUIRES at least one existing priority rule
    # somewhere in the network to use as a structural template -- a real,
    # STATED limitation (raised as a clear error, never silently worked
    # around): a network with zero priority rules cannot genesis its first
    # one through this verb today.
    #
    # Once duplicated: ``SetAttValue('Lane', <Lane object>)``/``('Pos', ...)``
    # on the rule itself overrides the copied stop-line location (verified
    # live: works on a duplicate exactly like any other SetAttValue in this
    # module). The duplicate's own INHERITED conflict marker(s) are then
    # replaced with the caller's own: ``rule.ConflictMarkers.
    # AddConflictMarker(Key, Lane, Pos)`` genuinely creates a NEW marker on
    # an ALREADY-EXISTING rule (``Key`` accepts ``None``; the 2-arg form --
    # Lane, Pos with no Key -- fails "The Python instance can not be
    # converted to a COM object" since positional Key is genuinely
    # required, unlike every other ``Add*`` method in this codebase). A
    # freshly-created marker's own out-of-the-box default is
    # ``MinGapTime=3.0`` s / ``MinClear=5.0`` m (verified live, read
    # directly off the just-returned marker object before this codebase
    # ever calls ``SetAttValue`` on it) -- Austroads/MRWA movement-type-
    # specific defaults are deliberately NOT applied here (no cheap
    # movement-type inference exists in this codebase's shadow model, per
    # this unit's own task-brief escape hatch); :meth:`add_priority_rule`
    # instead reports whichever value is actually in effect (caller-supplied
    # or this Vissim default) for every marker, explicitly, never silently.
    # ``ConflictMarkers.RemoveConflictMarker(<marker object>)`` removes the
    # template's own now-superseded marker(s) afterward.
    #
    # TWO LANDMINES specific to this child collection (both live-caught,
    # both worse than any prior single-object COM landmine in this
    # codebase):
    #   1. STALE CACHING -- a ``.ConflictMarkers`` PROPERTY ACCESS held once
    #      in a variable and reused across multiple ``.GetAll()`` calls
    #      returns a STALE snapshot after Add/RemoveConflictMarker WITHIN
    #      THE SAME COM session (reproduced twice: a held reference
    #      under-counted a marker the SAVED .inpx already contained, both
    #      immediately and after further calls). This module always
    #      RE-READS ``rule.ConflictMarkers`` fresh immediately before every
    #      ``.GetAll()``/``.AddConflictMarker()``/``.RemoveConflictMarker()``
    #      call below -- never holds the child-collection object across
    #      calls.
    #   2. NOT APPEND-ORDER -- a newly-added marker does NOT necessarily
    #      land at the END of ``GetAll()``'s own order (observed live: a
    #      marker added at Pos=4.0 landed BEFORE a pre-existing Pos=18.48
    #      marker on the same rule, consistent with an ascending-by-Pos
    #      ordering Vissim applies on its own, not insertion order). The
    #      1-based ``marker_index`` QUEUE 3 unit 2's own
    #      ``set_priority_rule_gap_headway``/``get_priority_rule_gap_headway``
    #      already address markers by is therefore NEVER assumed to equal
    #      the caller's own input order here -- :meth:`add_priority_rule`
    #      re-derives each marker's real final index from a FRESH
    #      post-creation readback, matched back to the caller's own request
    #      by (link, lane_index, pos_m), and reports that mapping explicitly
    #      in its own return value so a FOLLOW-UP
    #      ``set_priority_rule_gap_headway`` call addresses the right one.
    #
    # ``RemovePriorityRule(rule object)`` removes the rule AND every one of
    # its own conflict markers atomically (verified live: a fresh reload
    # after removal shows neither the rule nor any orphaned marker).
    # -----------------------------------------------------------------

    def available_stop_sign_nos(self) -> list[int]:
        return self._collection_nos(self.vissim.Net.StopSigns)

    def add_stop_sign(
        self,
        link_no: int,
        lane_index: int,
        pos_m: float,
        name: str = "",
        sc_no: int | None = None,
        sg_no: int | None = None,
    ) -> dict[str, Any]:
        """Create a stop sign on lane ``lane_index`` of ``link_no`` at
        ``pos_m`` metres (COM ``AddStopSign``). ``sc_no``/``sg_no`` OPTIONALLY
        wire the stop sign to a signal group (real, minority .inpx shape --
        see this block's own module comment), resolved via the existing
        :meth:`_signal_group_by_key`, mirroring :meth:`add_signal_head`'s own
        SG-wiring precedent."""
        try:
            lane = self._lane_by_index(link_no, lane_index)
            existing = self.available_stop_sign_nos()
            new_no = (max(existing) if existing else 0) + 1
            pos_com = self._length_m_to_com(float(pos_m), self._net_unit_length())
            stop_sign = self.vissim.Net.StopSigns.AddStopSign(new_no, lane, pos_com)
            if name:
                stop_sign.SetAttValue("Name", name)
            if sc_no is not None and sg_no is not None:
                signal_controller = self._item_by_key(
                    self.vissim.Net.SignalControllers,
                    sc_no,
                    "Signal controller",
                    self.available_signal_controller_nos(),
                )
                sg = self._signal_group_by_key(signal_controller, sc_no, sg_no)
                stop_sign.SetAttValue("SG", sg)
        except VissimObjectNotFoundError:
            raise
        except Exception as exc:
            raise RuntimeError(
                f"Could not add a stop sign on link {link_no} lane {lane_index} at pos {pos_m}: {exc}"
            ) from exc
        return self.get_stop_sign(new_no)

    def remove_stop_sign(self, no: int) -> None:
        try:
            stop_sign = self._item_by_key(
                self.vissim.Net.StopSigns, no, "Stop sign", self.available_stop_sign_nos()
            )
            self.vissim.Net.StopSigns.RemoveStopSign(stop_sign)
        except VissimObjectNotFoundError:
            raise
        except Exception as exc:
            raise RuntimeError(f"Could not remove stop sign {no}: {exc}") from exc

    def get_stop_sign(self, no: int) -> dict[str, Any]:
        """{"no", "name", "link", "lane_index", "pos", "sc", "sg"} -- ``sc``/
        ``sg`` are ``None`` when the stop sign carries no signal-group link
        (the common case -- see this block's own module comment). Does NOT
        expose ``AnmFlag`` -- unreadable on this installation (see this
        block's own module comment); no reliable read path exists."""
        try:
            stop_sign = self._item_by_key(
                self.vissim.Net.StopSigns, no, "Stop sign", self.available_stop_sign_nos()
            )
            unit = self._net_unit_length()
            lane_link, _, lane_idx = str(stop_sign.AttValue("Lane") or "").partition("-")
            sg_raw = stop_sign.AttValue("SG")
            sc: str | None = None
            sg: str | None = None
            if sg_raw not in (None, ""):
                sc, _, sg = str(sg_raw).partition("-")
            return {
                "no": no,
                "name": str(stop_sign.AttValue("Name") or ""),
                "link": lane_link,
                "lane_index": int(lane_idx) if lane_idx.isdigit() else None,
                "pos": self._length_com_to_m(float(stop_sign.AttValue("Pos")), unit),
                "sc": sc,
                "sg": sg,
            }
        except VissimObjectNotFoundError:
            raise
        except Exception as exc:
            raise RuntimeError(f"Could not read stop sign {no}: {exc}") from exc

    def add_priority_rule(
        self,
        stop_link_no: int,
        stop_lane_index: int,
        stop_pos_m: float,
        markers: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Create a priority rule at (``stop_link_no``, ``stop_lane_index``,
        ``stop_pos_m``) with one conflict marker per entry of ``markers``
        (each ``{"link_no", "lane_index", "pos_m", "gap_time_s"?,
        "headway_m"?}``) -- see this block's own module comment for the full
        live-verified mechanism (``DuplicatePriorityRule`` + per-marker
        ``AddConflictMarker``/``RemoveConflictMarker``, since
        ``AddPriorityRule`` itself is a confirmed silent no-op) and its two
        landmines (stale ``ConflictMarkers`` caching; not-append-order).

        Raises :class:`ValueError` when ``markers`` is empty (Vissim's own
        floor -- a priority rule needs >=1 conflict marker). Raises
        :class:`RuntimeError` when the network has no EXISTING priority rule
        to duplicate as a structural template -- a real, stated limitation,
        not a silent failure.

        Returns ``{"no", "link", "lane_index", "pos", "template_rule_no",
        "markers": [{"marker_index", "link", "lane_index", "pos",
        "gap_time_s", "headway_m"}, ...]}`` -- ``markers`` is ordered to
        match the CALLER's own input order (not necessarily
        ``marker_index`` order -- see the module comment's landmine 2), each
        entry carrying its REAL, readback-verified ``marker_index`` for a
        follow-up ``set_priority_rule_gap_headway`` call to address it by.
        """
        if not markers:
            raise ValueError(
                "add_priority_rule: at least one conflict marker is required -- Vissim's own "
                "floor is >=1 marker per priority rule (live-verified: AddPriorityRule itself "
                "raises 'The number of Conflict markers must be at least 1' on an empty array)."
            )
        try:
            templates = list(self.vissim.Net.PriorityRules.GetAll())
            if not templates:
                raise RuntimeError(
                    "add_priority_rule: this network has no existing priority rule to duplicate "
                    "as a structural template. Net.PriorityRules.AddPriorityRule is confirmed "
                    "non-functional (silent no-op, live-verified against Vissim 2026), and "
                    "DuplicatePriorityRule needs a real source rule -- genesis of the FIRST "
                    "priority rule in a network with none today is not supported by this verb."
                )
            template = templates[0]
            template_no = int(template.AttValue("No"))
            existing_nos = self.available_priority_rule_nos()
            new_no = (max(existing_nos) if existing_nos else 0) + 1
            unit = self._net_unit_length()

            stop_lane = self._lane_by_index(stop_link_no, stop_lane_index)

            self.vissim.Net.PriorityRules.DuplicatePriorityRule(template, new_no)
            rule = self._item_by_key(
                self.vissim.Net.PriorityRules,
                new_no,
                "Priority rule",
                self.available_priority_rule_nos(),
            )
            rule.SetAttValue("Lane", stop_lane)
            rule.SetAttValue("Pos", self._length_m_to_com(float(stop_pos_m), unit))

            # Landmine 1: re-read .ConflictMarkers fresh, never hold it.
            inherited_markers = list(rule.ConflictMarkers.GetAll())

            requested: list[dict[str, Any]] = []
            for marker in markers:
                marker_link_no = int(marker["link_no"])
                marker_lane_index = int(marker["lane_index"])
                marker_pos_m = round(float(marker["pos_m"]), 6)
                marker_lane = self._lane_by_index(marker_link_no, marker_lane_index)
                marker_pos_com = self._length_m_to_com(marker_pos_m, unit)
                new_marker = rule.ConflictMarkers.AddConflictMarker(None, marker_lane, marker_pos_com)
                gap_time_s = marker.get("gap_time_s")
                headway_m = marker.get("headway_m")
                if gap_time_s is not None:
                    new_marker.SetAttValue("MinGapTime", gap_time_s)
                if headway_m is not None:
                    new_marker.SetAttValue("MinClear", headway_m)
                requested.append(
                    {
                        "link_no": marker_link_no,
                        "lane_index": marker_lane_index,
                        "pos_m": marker_pos_m,
                    }
                )

            # Remove the template's own inherited marker(s) now that the
            # caller's own are in place -- these are live COM object
            # references captured BEFORE any add, so they stay valid
            # regardless of how the collection's own order shifted
            # (landmine 2).
            for old_marker in inherited_markers:
                rule.ConflictMarkers.RemoveConflictMarker(old_marker)

            # Re-derive each caller marker's REAL final marker_index
            # (landmine 2: not append order) via a fresh readback, matched
            # by (link, lane_index, rounded pos_m).
            final_markers = list(rule.ConflictMarkers.GetAll())
            by_key: dict[tuple[str, str, float], dict[str, Any]] = {}
            for idx, com_marker in enumerate(final_markers, start=1):
                lane_raw = str(com_marker.AttValue("Lane") or "")
                m_link, _, m_lane = lane_raw.partition("-")
                m_pos = round(self._length_com_to_m(float(com_marker.AttValue("Pos")), unit), 6)
                by_key[(m_link, m_lane, m_pos)] = {
                    "marker_index": idx,
                    "link": m_link,
                    "lane_index": int(m_lane) if m_lane.isdigit() else None,
                    "pos": m_pos,
                    "gap_time_s": float(com_marker.AttValue("MinGapTime")),
                    "headway_m": float(com_marker.AttValue("MinClear")),
                }

            result_markers: list[dict[str, Any]] = []
            for req in requested:
                key = (str(req["link_no"]), str(req["lane_index"]), req["pos_m"])
                matched = by_key.get(key)
                if matched is None:
                    raise RuntimeError(
                        f"add_priority_rule: could not match requested marker "
                        f"{req!r} back to a created marker by (link, lane_index, pos) after "
                        "creation -- this should not happen; every requested marker's own "
                        "(link, lane, pos) should be unique on this rule."
                    )
                result_markers.append(matched)
        except VissimObjectNotFoundError:
            raise
        except Exception as exc:
            raise RuntimeError(
                f"Could not add priority rule on link {stop_link_no} lane {stop_lane_index} at "
                f"pos {stop_pos_m}: {exc}"
            ) from exc

        return {
            "no": new_no,
            "link": str(stop_link_no),
            "lane_index": stop_lane_index,
            "pos": round(float(stop_pos_m), 6),
            "template_rule_no": template_no,
            "markers": result_markers,
        }

    def remove_priority_rule(self, no: int) -> None:
        """Removes the rule AND every one of its own conflict markers
        atomically (verified live -- see this block's own module comment).
        Nothing else in the schema references a priority rule by its own
        ``no`` (verified via a corpus-wide static scan, this unit's own
        probe) so no reference-integrity check is needed here -- unlike
        ``remove_data_collection_point``'s own COM-does-not-enforce-this
        precedent."""
        try:
            rule = self._item_by_key(
                self.vissim.Net.PriorityRules, no, "Priority rule", self.available_priority_rule_nos()
            )
            self.vissim.Net.PriorityRules.RemovePriorityRule(rule)
        except VissimObjectNotFoundError:
            raise
        except Exception as exc:
            raise RuntimeError(f"Could not remove priority rule {no}: {exc}") from exc

    # -----------------------------------------------------------------
    # PT stop + PT line PLACEMENT (creation) -- Q4 unit (7), the natural home
    # of the BusDwell finding (Q3 unit 5, docs/phase2/DECISIONS.md
    # 2026-07-31: Murdoch has 16 real ptStops / 12 ptLines / 19 ptLineStops,
    # every LIVE dwell assignment shares ONE 10s+-2s NORMAL distribution, and
    # 5 staged BusDwell_0-4 empirical curves sit unreferenced).
    #
    # COM ids/behavior verified EMPIRICALLY live against Vissim 2026
    # (backend/tools/probe_pt_stops_lines.py, "BRT Priority Texas.US" clone --
    # 32 real ptStops, 10 real ptLines, 62 real ptLineStops).
    #
    # **PT stops are a genuine, clean native create/remove**, the same shape
    # :meth:`add_stop_sign`/:meth:`add_signal_head` already use:
    # ``Net.PTStops.AddPTStop(Key, Lane, Pos)`` creates and returns the new
    # object directly -- no silent-no-op landmine like AddPriorityRule.
    # ``AddPTStop``'s OWN signature has no ``Length`` parameter (unlike the
    # XML's own ``length`` attribute) -- a fresh stop's out-of-the-box
    # default is 15.0 m (live-verified); ``Length`` IS settable afterward via
    # a plain follow-up ``SetAttValue`` (also live-verified), exactly like
    # every other placement verb in this module that writes a scalar right
    # after creation (e.g. :meth:`add_stop_sign`'s own ``Name``).
    # ``RemovePTStop`` removes cleanly -- but see the reference-integrity
    # finding below: Vissim's own remove does NOT protect a still-referenced
    # stop, unlike its Add being trustworthy.
    #
    # **PT lines are a genuine, working native create too -- correcting the
    # task brief's own worry that this would need deep route-coding.**
    # ``Net.PTLines.AddPTLine(Key, EntryLink, DestLink, DestPos)`` (NOT
    # ``(Key, VehType, EntryLink)`` as first guessed from the object's own
    # scalar attribute names -- the real signature, confirmed via the
    # type-library scan, needs entry AND destination links) genuinely
    # creates a new line (``GetAll()`` diff proved a real addition, unlike
    # AddPriorityRule's confirmed no-op) and, as a byproduct with real
    # planner value:
    #   - auto-computes a MINIMAL default route: the new line's own
    #     ``LinkSeq`` child collection contains exactly ONE entry, the entry
    #     link itself (live-verified) -- literally "a line on a start link
    #     with default route", the task brief's own stated minimal-scope
    #     escape hatch, met exactly rather than approximated.
    #   - auto-creates exactly ONE line-stop on the new line's own
    #     ``LineStops`` child collection, defaulting its ``PtStop`` to
    #     whichever real PT stop happens to sit on the entry link (live-
    #     verified: entry link 13270462 hosts stop 99569, and the new line's
    #     auto line-stop's own ``PtStop`` read back exactly ``'99569'``) and
    #     its ``DwellTmDistr`` to a Vissim-chosen default (observed: ``'1'``).
    # ``VehType`` is NOT an ``AddPTLine`` argument at all (despite being a
    # scalar attribute on the object) -- set via a follow-up ``SetAttValue``,
    # confirmed writable both via a real ``VehicleType`` COM object.
    # ``PtStop`` on an EXISTING line-stop entry is ALSO confirmed writable via
    # plain ``SetAttValue`` (a real COM ``PTStop`` object, not a bare int) --
    # this is how :meth:`add_pt_line`'s own optional ``pt_stop_no`` repoints
    # the auto-created line-stop to the caller's actual intent instead of
    # silently leaving Vissim's own entry-link-derived guess in place.
    #
    # **Two genuine, stated scope cuts (not silently worked around):**
    #   1. ``IPTLine.UpdateLinkSequence(RouteSegments)`` exists (confirmed via
    #      the type-library scan) but its real argument shape is UNRESOLVED --
    #      both a plain Python list and a tuple of real ``Link`` COM objects
    #      (the template line's own real, WORKING 19-link route) raised "The
    #      array contains too few elements" from COM. :meth:`add_pt_line`
    #      therefore ships with Vissim's own minimal (entry-link-only)
    #      default route ONLY -- a caller wanting a real multi-link route
    #      needs a follow-up unit that resolves this method's true argument
    #      marshalling (a SAFEARRAY typing issue is suspected, not chased
    #      further here per this unit's own honest-scope allowance).
    #   2. There is NO create surface of any kind on a line's own
    #      ``LineStops``/``PTLineStops`` child collection (confirmed via the
    #      type-library scan of ``IPTLineStopContainer``/
    #      ``IPTLineStopContainerBase`` -- generic collection methods only,
    #      no ``AddPTLineStop``) -- a genesis line is therefore CAPPED at the
    #      single auto-created line-stop AddPTLine itself provides; a
    #      multi-stop PT line requires ``DuplicatePTLine`` (confirmed working,
    #      copies a template's full line-stop pattern verbatim, the SAME
    #      duplicate-then-edit shape :meth:`add_priority_rule`/
    #      ``duplicate_driving_behavior`` already established) rather than
    #      genesis creation -- out of THIS unit's scope, a real and stated
    #      limitation.
    # ``AddDepartureTime(Key, Dep)`` on a line's own ``DepTimes`` child
    # collection IS confirmed to work (creates + returns a real object,
    # readback-verified) -- a genuinely tractable follow-up, matching the
    # task brief's own "departure times as follow-up" allowance; not wired
    # into any verb this unit ships.
    #
    # **LIVE-CAUGHT route-reachability trap (backend/tools/
    # pt_stops_lines_live_check.py's own [A.7]): an UNREACHABLE dest_link_no
    # produces a line that ADDS successfully (no exception, correct readback)
    # but FAILS Vissim's own RunSingleStep init later with "Public Transport
    # Line "<no>" is not complete."** First reproduced with an
    # entry/dest pair that shared no route at all; re-run with a REAL
    # template line's own (entry_link, dest_link, dest_pos) triple (a
    # guaranteed-reachable pair) passed the SAME load oracle cleanly, with
    # zero departure times either time -- isolating the cause to route
    # reachability, not the missing timetable. No preflight check validates
    # dest_link_no is actually reachable from entry_link_no (this unit's own
    # "constraints.py minimal" scope; mirrors add_travel_time_section's own
    # documented to_link gap) -- a caller MUST pick a genuinely routable
    # destination (matching a real transit line's own intent, not an
    # arbitrary link) or risk this exact silent-until-run-time trap.
    #
    # **Reference integrity -- the flagship finding of this unit, worse than
    # the RemoveDataCollectionPoint precedent (Q4 unit (2)).** Live-verified:
    # ``RemovePTStop`` on a stop a REAL ptLineStop still references does NOT
    # dangle the reference (leaving a readable-but-broken pointer, like
    # RemoveDataCollectionPoint) -- it SILENTLY CASCADE-DELETES the
    # referencing ``ptLineStop`` entry outright (confirmed via a fresh
    # reparse: the line's own ``LineStops`` count dropped, with zero
    # exception, zero warning, zero trace in the saved XML of what happened).
    # For a real production PT model this would silently truncate a bus
    # route's own stop pattern -- exactly the "Vissim almost never refuses a
    # bad edit" thesis this codebase's validation layer exists to counter,
    # one rung more dangerous than a mere dangling id. A static corpus-wide
    # attribute-name scan (all 25 real Vissim 2026 example networks) found
    # THREE real sites that reference a PT stop by its own ``no``:
    # ``ptLineStop.ptStop`` (real, correctly nested inside ``<ptLine>``),
    # ``partialPTLineStop.ptStop`` (a SEPARATE object family entirely --
    # ``vehicleRoutingDecisionsPartialPT``, a partial-route PT decision, NOT
    # nested inside a ``<ptLine>`` at all -- see the module comment in
    # ``tools_network.py``'s own PT-stop reference registry for why this
    # disproves half of Q3 unit 5's own reference-scan claim), and
    # ``detector.ptStop`` (a newly-found site, e.g. a PT-boarding-gate
    # detector with a ``tmBefDep`` "time before departure" attribute --
    # genuinely references a PT stop, previously unregistered anywhere in
    # this codebase). ``services.inpx_structure``'s reference-integrity
    # registry is extended with all three so :func:`tools_edit.remove_pt_stop`
    # refuses (naming every referrer) BEFORE ever calling this silently-
    # cascading COM method -- see that spine module's own registry comment.
    # PT lines themselves are a LEAF object for reference-integrity purposes
    # (the SAME corpus-wide scan found no attribute anywhere that references
    # a ptLine by its own ``no`` -- the only ``*PTLine*``-named attribute
    # besides the real ones above, ``allPTLines`` on ``boardingVolume``/
    # ``vehicleRoutingDecisionPartialPT``, is a boolean SCOPE FLAG ("applies
    # to every PT line"), not an id reference) -- ``RemovePTLine`` needs no
    # reference-integrity check of its own, mirroring
    # ``remove_priority_rule``'s own "leaf object" precedent.
    # -----------------------------------------------------------------

    def available_pt_stop_nos(self) -> list[int]:
        return self._collection_nos(self.vissim.Net.PTStops)

    def add_pt_stop(
        self,
        link_no: int,
        lane_index: int,
        pos_m: float,
        length_m: float | None = None,
        name: str = "",
    ) -> dict[str, Any]:
        """Create a PT stop on lane ``lane_index`` of ``link_no`` at
        ``pos_m`` metres (COM ``AddPTStop``). ``length_m`` is OPTIONAL --
        ``AddPTStop`` itself has no Length argument (Vissim's own default is
        15.0 m); given, it is applied via a follow-up ``SetAttValue``."""
        try:
            lane = self._lane_by_index(link_no, lane_index)
            existing = self.available_pt_stop_nos()
            new_no = (max(existing) if existing else 0) + 1
            unit = self._net_unit_length()
            pos_com = self._length_m_to_com(float(pos_m), unit)
            pt_stop = self.vissim.Net.PTStops.AddPTStop(new_no, lane, pos_com)
            if length_m is not None:
                pt_stop.SetAttValue("Length", self._length_m_to_com(float(length_m), unit))
            if name:
                pt_stop.SetAttValue("Name", name)
        except VissimObjectNotFoundError:
            raise
        except Exception as exc:
            raise RuntimeError(
                f"Could not add a PT stop on link {link_no} lane {lane_index} at pos {pos_m}: {exc}"
            ) from exc
        return self.get_pt_stop(new_no)

    def remove_pt_stop(self, no: int) -> None:
        """Removes the stop (COM ``RemovePTStop``). Reference-integrity is
        NOT enforced by COM itself here (see this block's own module
        comment -- a still-referenced stop's removal SILENTLY
        CASCADE-DELETES the referencing ptLineStop entry); callers MUST
        check ``services.inpx_structure.find_id_references`` against the
        working copy's current XML BEFORE calling this method (see
        ``tools_edit.remove_pt_stop``)."""
        try:
            pt_stop = self._item_by_key(
                self.vissim.Net.PTStops, no, "PT stop", self.available_pt_stop_nos()
            )
            self.vissim.Net.PTStops.RemovePTStop(pt_stop)
        except VissimObjectNotFoundError:
            raise
        except Exception as exc:
            raise RuntimeError(f"Could not remove PT stop {no}: {exc}") from exc

    def get_pt_stop(self, no: int) -> dict[str, Any]:
        try:
            pt_stop = self._item_by_key(
                self.vissim.Net.PTStops, no, "PT stop", self.available_pt_stop_nos()
            )
            unit = self._net_unit_length()
            lane_link, _, lane_idx = str(pt_stop.AttValue("Lane") or "").partition("-")
            return {
                "no": no,
                "name": str(pt_stop.AttValue("Name") or ""),
                "link": lane_link,
                "lane_index": int(lane_idx) if lane_idx.isdigit() else None,
                "pos": self._length_com_to_m(float(pt_stop.AttValue("Pos")), unit),
                "length": self._length_com_to_m(float(pt_stop.AttValue("Length")), unit),
            }
        except VissimObjectNotFoundError:
            raise
        except Exception as exc:
            raise RuntimeError(f"Could not read PT stop {no}: {exc}") from exc

    def available_pt_line_nos(self) -> list[int]:
        return self._collection_nos(self.vissim.Net.PTLines)

    def add_pt_line(
        self,
        entry_link_no: int,
        dest_link_no: int,
        dest_pos_m: float,
        veh_type_no: int | None = None,
        pt_stop_no: int | None = None,
        name: str = "",
    ) -> dict[str, Any]:
        """Create a PT line from ``entry_link_no`` to (``dest_link_no``,
        ``dest_pos_m``) (COM ``AddPTLine``) -- see this block's own module
        comment for the full live-verified writeup: the new line gets a
        MINIMAL auto default route (entry link only) and exactly ONE
        auto-created line-stop (Vissim's own pick -- whichever real PT stop
        sits on the entry link). ``veh_type_no`` OPTIONALLY assigns the
        line's vehicle type (a follow-up ``SetAttValue`` -- not an
        ``AddPTLine`` argument at all). ``pt_stop_no`` OPTIONALLY repoints
        the auto-created line-stop's own ``PtStop`` reference to the
        caller's actual intent, instead of silently leaving Vissim's own
        entry-link-derived guess in place -- the BusDwell story's genesis
        half: a caller can now create a line AND aim its one stop at a real
        PT stop in the same approved action, ready for a follow-up
        :meth:`set_pt_line_stop_dwell` to wire a dwell distribution."""
        try:
            entry_link = self._link_object_by_no(entry_link_no)
            dest_link = self._link_object_by_no(dest_link_no)
            existing = self.available_pt_line_nos()
            new_no = (max(existing) if existing else 0) + 1
            unit = self._net_unit_length()
            dest_pos_com = self._length_m_to_com(float(dest_pos_m), unit)
            pt_line = self.vissim.Net.PTLines.AddPTLine(new_no, entry_link, dest_link, dest_pos_com)
            if name:
                pt_line.SetAttValue("Name", name)
            if veh_type_no is not None:
                veh_type = self._item_by_key(
                    self.vissim.Net.VehicleTypes,
                    veh_type_no,
                    "Vehicle type",
                    self._collection_nos(self.vissim.Net.VehicleTypes),
                )
                pt_line.SetAttValue("VehType", veh_type)
            if pt_stop_no is not None:
                # Re-fetch LineStops FRESH -- never hold a child-collection
                # reference across calls (the ConflictMarkers stale-caching
                # landmine, Q4 unit (6), applies to every per-item child
                # collection in this codebase on principle, re-applied here
                # defensively even though this unit did not itself reproduce
                # staleness on LineStops specifically).
                auto_stops = list(pt_line.LineStops.GetAll())
                if not auto_stops:
                    raise RuntimeError(
                        "the newly-created line has no auto-created line-stop to repoint -- "
                        "this should not happen (Vissim auto-creates exactly one on every "
                        "AddPTLine call, live-verified)"
                    )
                target_stop = self._item_by_key(
                    self.vissim.Net.PTStops, pt_stop_no, "PT stop", self.available_pt_stop_nos()
                )
                auto_stops[0].SetAttValue("PtStop", target_stop)
        except VissimObjectNotFoundError:
            raise
        except Exception as exc:
            raise RuntimeError(
                f"Could not add a PT line from link {entry_link_no} to link {dest_link_no} at pos "
                f"{dest_pos_m}: {exc}"
            ) from exc
        return self.get_pt_line(new_no)

    def remove_pt_line(self, no: int) -> None:
        """Removes the line AND its own line-stops atomically (COM
        ``RemovePTLine``) -- nothing else in the schema references a PT line
        by its own ``no`` (verified via a corpus-wide static scan, this
        unit's own probe; the only other ``*PTLine*``-named attribute,
        ``allPTLines``, is a boolean scope flag, not an id reference), so no
        reference-integrity check is needed here."""
        try:
            pt_line = self._item_by_key(
                self.vissim.Net.PTLines, no, "PT line", self.available_pt_line_nos()
            )
            self.vissim.Net.PTLines.RemovePTLine(pt_line)
        except VissimObjectNotFoundError:
            raise
        except Exception as exc:
            raise RuntimeError(f"Could not remove PT line {no}: {exc}") from exc

    def get_pt_line(self, no: int) -> dict[str, Any]:
        try:
            pt_line = self._item_by_key(
                self.vissim.Net.PTLines, no, "PT line", self.available_pt_line_nos()
            )
            unit = self._net_unit_length()
            veh_type_raw = pt_line.AttValue("VehType")
            entry_tm_distr_raw = pt_line.AttValue("EntryTmDistr")
            stops: list[dict[str, Any]] = []
            for entry in pt_line.LineStops.GetAll():
                stops.append(
                    {
                        "pt_stop_no": str(entry.AttValue("PtStop") or ""),
                        "dwell_tm_distr": str(entry.AttValue("DwellTmDistr") or ""),
                        "active": bool(entry.AttValue("Active")),
                    }
                )
            # LinkSeq's own GetAll() items are REAL Link COM objects (live-
            # caught: AttValue('Key') raises "LINK: Could not find attribute
            # with ID KEY" -- the XML's own <intObjectRef key="..."/> naming
            # does NOT carry through to this child collection's own COM item
            # shape the way it does for a static routing decision's linkSeq;
            # a genuine link/connector object exposes its own number via
            # 'No', the same attribute id every other link lookup in this
            # module already uses), so read 'No' -- with a defensive 'Key'
            # fallback in case a future Vissim version's shape differs.
            link_seq: list[str] = []
            for el in pt_line.LinkSeq.GetAll():
                try:
                    link_seq.append(str(el.AttValue("No")))
                except Exception:
                    link_seq.append(str(el.AttValue("Key")))
            return {
                "no": no,
                "name": str(pt_line.AttValue("Name") or ""),
                "veh_type": str(veh_type_raw) if veh_type_raw not in (None, "") else None,
                "entry_link": str(pt_line.AttValue("EntryLink")),
                "dest_link": str(pt_line.AttValue("DestLink")),
                "dest_pos": self._length_com_to_m(float(pt_line.AttValue("DestPos")), unit),
                "entry_tm_distr": str(entry_tm_distr_raw) if entry_tm_distr_raw not in (None, "") else None,
                "link_seq": link_seq,
                "stops": stops,
            }
        except VissimObjectNotFoundError:
            raise
        except Exception as exc:
            raise RuntimeError(f"Could not read PT line {no}: {exc}") from exc

    def _pt_line_stop_by_key(self, pt_line: Any, stop_no: int) -> Any:
        """Fresh-fetch (never hold) a ptLine's own ``LineStops`` collection
        and return the ONE entry whose ``PtStop`` matches ``stop_no`` --
        Vissim's own ``LineStops`` collection has no ``ItemByKey`` at all
        (mirrors ``ConflictMarkers``' own ordinal-only addressing, Q4 unit
        (6)), so a line-stop is found by scanning, never by key lookup.
        Returns ``None`` (never raises) when this line has no line-stop for
        that stop -- callers turn that into their own structured error."""
        for entry in pt_line.LineStops.GetAll():
            if str(entry.AttValue("PtStop")) == str(stop_no):
                return entry
        return None

    def set_pt_line_stop_dwell(self, line_no: int, stop_no: int, time_distribution_no: int) -> dict[str, Any]:
        """Wire ``time_distribution_no`` as PT line ``line_no``'s stop
        ``stop_no`` own dwell-time distribution (``SetAttValue('DwellTmDistr',
        <TimeDistribution COM object>)`` on the matching ``ptLineStop`` entry)
        -- the BusDwell story's missing verb (Q3 unit 5, docs/phase2/
        DECISIONS.md 2026-07-31): TK's 5 staged-but-unreferenced BusDwell_0-4
        empirical curves can finally be connected to a real line-stop through
        this codebase's agent, rather than only by hand in the Vissim GUI.
        Live-verified: COM itself fails CLOSED on a bogus time-distribution
        number ("Object N of type Time Distribution does not exist")."""
        try:
            pt_line = self._item_by_key(
                self.vissim.Net.PTLines, line_no, "PT line", self.available_pt_line_nos()
            )
            entry = self._pt_line_stop_by_key(pt_line, stop_no)
            if entry is None:
                raise RuntimeError(
                    f"PT line {line_no} has no line-stop referencing PT stop {stop_no} -- see "
                    "get_pt_lines for this line's real stops."
                )
            distr = self._item_by_key(
                self.vissim.Net.TimeDistributions,
                time_distribution_no,
                "Time distribution",
                self._collection_nos(self.vissim.Net.TimeDistributions),
            )
            entry.SetAttValue("DwellTmDistr", distr)
        except VissimObjectNotFoundError:
            raise
        except Exception as exc:
            raise RuntimeError(
                f"Could not set PT line {line_no} stop {stop_no}'s dwell distribution to "
                f"{time_distribution_no}: {exc}"
            ) from exc
        return self.get_pt_line_stop_dwell(line_no, stop_no)

    def get_pt_line_stop_dwell(self, line_no: int, stop_no: int) -> dict[str, Any]:
        try:
            pt_line = self._item_by_key(
                self.vissim.Net.PTLines, line_no, "PT line", self.available_pt_line_nos()
            )
            entry = self._pt_line_stop_by_key(pt_line, stop_no)
            if entry is None:
                raise RuntimeError(
                    f"PT line {line_no} has no line-stop referencing PT stop {stop_no}."
                )
            return {
                "line_no": str(line_no),
                "stop_no": str(stop_no),
                "dwell_tm_distr": str(entry.AttValue("DwellTmDistr") or ""),
            }
        except VissimObjectNotFoundError:
            raise
        except Exception as exc:
            raise RuntimeError(
                f"Could not read PT line {line_no} stop {stop_no}'s dwell distribution: {exc}"
            ) from exc

    # -----------------------------------------------------------------
    # Parking-lot verbs -- Q6 unit (2)
    #
    # COM ids/behavior verified EMPIRICALLY live against Vissim 2026
    # (backend/tools/probe_parking_lot.py, ten parts, "Urban Intersection
    # Beijing.CN" / "3D - Complex Intersection Karlsruhe.DE" / "Toll Plaza
    # Camden.US" clones). The probe's own docstring carries the full findings
    # and the numbers; the four that shape THIS code:
    #
    # 1. ``ParkingLots.AddParkingLot(Key, Lane, Pos)`` is a genuine native
    #    create -- NOT a member of the duplicate-then-edit family (driving
    #    behaviors Q3 ⑥, priority rules Q4 ⑥, signal controllers Q5 ②). It
    #    works on a network that has no ``<parkingLots>`` section at all
    #    (Beijing: 0 lots -> 1, judged by a ``GetAll()`` id-set diff, and the
    #    saved file materialises the section). ``DuplicateParkingLot`` exists
    #    and is deliberately unwrapped -- there is nothing a copy buys here
    #    that a fresh create does not, and a copy would silently inherit the
    #    donor's name/fees/attractivity.
    #
    # 2. **``Capacity`` is NOT writable -- it is DERIVED from ``Length`` /
    #    ``LenPerSpc``.** ``SetAttValue("Capacity", n)`` fails LOUDLY
    #    ("Attribute Capacity cannot be changed") on a REALPARKINGSPACE.
    #    Measured against an untouched control lot in the same session:
    #    Length 6/LenPerSpc 6 -> Capacity 1; Length 60 -> 10; LenPerSpc 4 ->
    #    15; Length 0 -> 0. The persisted files satisfy
    #    ``capacity == floor(length / lenPerSpc)``. So the only honest way to
    #    give a caller "an N-space parking lot" is to write
    #    ``Length = N x LenPerSpc`` and then READ ``Capacity`` BACK -- which
    #    is exactly what :meth:`add_parking_lot`/
    #    :meth:`set_parking_lot_capacity` do. Never echo the requested count.
    #
    # 3. Two SILENT edges guarded by the callers, not by Vissim:
    #    ``LenPerSpc`` larger than ``Length`` is silently CLAMPED to Length
    #    (1000 requested against a 60 m lot came back 60.0, no exception, and
    #    the capacity fell 15 -> 1); ``Length = 0`` is silently ACCEPTED
    #    (capacity 0, LenPerSpc forced to 1.0, ``load_check`` still green) --
    #    a zero-space parking lot Vissim never complains about. One LOUD edge
    #    that needs no guard of ours but is worth pre-empting with a better
    #    message: a Length past the link's end is refused with Vissim's own
    #    "Parking Lot 2 does not fit on link 1 (335.122 m)".
    #
    # 4. **``RemoveParkingLot`` SILENTLY CASCADE-DELETES the parking ROUTE
    #    that references the lot** -- the ``RemovePTStop`` species (Q4 unit
    #    (7)), not the ``RemoveDataCollectionPoint`` dangle. Live on
    #    Karlsruhe (573 lots / 573 routes, every lot referenced): removing
    #    lot 14 moved the persisted counts ``{'parking_lots': -1,
    #    'parking_routes': -1}``, no exception, no dangling reference left
    #    behind, ``load_check`` green. Callers MUST consult
    #    ``services.inpx_structure.find_id_references(root, "parking_lots",
    #    no)`` BEFORE calling :meth:`remove_parking_lot` (``tools_edit.
    #    remove_parking_lot`` does).
    #
    # UNITS (probe Part F, Toll Plaza Camden.US, ``UnitLenShort='FEET'``):
    # ``Pos``, ``Length`` and ``LenPerSpc`` are ALL display-unit-scaled --
    # COM/persisted-XML ratio 3.280840 on every one of them -- while the XML
    # itself stores metres. This is the OPPOSITE of a link polyline's X/Y
    # (raw metres in both directions, Q6 unit (1)). Every method below
    # converts through :meth:`_length_m_to_com`/:meth:`_length_com_to_m`.
    #
    # Deliberately NOT wrapped, each for a stated reason:
    #   * ``Type`` writes -- a ``type`` write is not an attribute edit at all
    #     but a structural re-anchor that MINTS A NEW OBJECT: flipping a
    #     fresh Beijing lot to ZONECONNECTOR dropped its ``Lane`` ('1-1' ->
    #     None), set ``Zone`` None -> '1' on a network with ZERO zones,
    #     changed Capacity 1 -> 100, and created ``<zone no="1">``; flipping
    #     back restored the lane and the capacity but LEFT THE ZONE BEHIND
    #     (persisted delta ``{'parking_lots': +1, 'zones': +1}``). Not
    #     reversible, and zones are a class the shadow model does not carry.
    #   * ``AddAbstractParkingLot(Key, Link, Pos, Zone)`` -- works (produces
    #     ``type="ABSTRACTPARKINGLOT"``, a THIRD value the 629-lot corpus
    #     never shows), but requires a ``<zone>`` the shadow model cannot
    #     see, so a verb around it would be unverifiable by construction
    #     (Q4 unit (4)'s signal-head precedent, applied as a refusal instead
    #     of a shipped verb).
    #   * ``Net.Zones`` / ``Net.ParkLotGrps`` /
    #     ``Net.VehicleRoutingDecisionsParking`` / ``AddVehicleRouteParking``
    #     -- the routing machinery that makes a lot reachable at all. Real,
    #     and out of this unit's scope; :meth:`add_parking_lot`'s caller
    #     WARNS that a lot without one is inert rather than pretending
    #     otherwise.
    # -----------------------------------------------------------------

    # NOTE: there is deliberately no "default length per space" constant here.
    # Vissim's own fresh-lot default is 6.0 DISPLAY units (live-measured: 6.0
    # on a METERS network, 6.0 display-feet = 1.8288 m on Toll Plaza
    # Camden.US), so a metric constant would be wrong on a feet network --
    # :meth:`_write_parking_capacity` READS the lot's current ``LenPerSpc``
    # instead whenever the caller supplies none. (``constraints.py`` does
    # carry a metric 6.0 for its preflight fit screen, and states there why
    # the larger of the two possible real values keeps that screen
    # conservative.)

    def available_parking_lot_nos(self) -> list[int]:
        return self._collection_nos(self.vissim.Net.ParkingLots)

    def _parking_lot_by_no(self, no: int) -> Any:
        return self._item_by_key(
            self.vissim.Net.ParkingLots, no, "Parking lot", self.available_parking_lot_nos()
        )

    def get_parking_lot(self, no: int) -> dict[str, Any]:
        """Read one parking lot: ``{"no", "name", "type", "link",
        "lane_index", "zone", "pos", "length", "len_per_space", "capacity",
        "attractivity", "max_park_time_s", "des_speed_distr_no"}``.

        The paired reader for every parking write below (guardrail doctrine:
        every setter needs a reader used for post-write verification).

        ``no`` is READ BACK (``str(int(AttValue("No")))``), never echoed --
        :meth:`add_parking_lot` returns this dict for a number it COMPUTED
        (``max+1``) and the agent layer lifts ``artifacts.no`` straight out
        of it, so echoing the argument would make the verb's own identity
        claim unfalsifiable, exactly the defect Q5 unit (2) shipped in
        ``get_signal_controller`` and had to fix. ``int()`` mirrors
        :meth:`_collection_nos`'s own guard so a float-typed ``No`` could
        never render "1.0" and miss every shadow-model id keyed "1".

        ``lane_index`` parses COM's HYPHENATED ``Lane`` form (``"1-1"`` --
        NOT the XML's space-separated ``"1 1"``; same split
        :meth:`get_pt_stop` already does) and is ``None`` on a lot that has
        no lane at all (the ZONECONNECTOR/ABSTRACTPARKINGLOT species, which
        anchors on a whole link). ``pos``/``length``/``len_per_space`` come
        back in METRES.
        """
        try:
            lot = self._parking_lot_by_no(no)
            unit = self._net_unit_length()
            lane_link, _, lane_idx = str(lot.AttValue("Lane") or "").partition("-")
            link = lane_link or str(lot.AttValue("Link") or "")
            return {
                "no": str(int(lot.AttValue("No"))),
                "name": str(lot.AttValue("Name") or ""),
                "type": str(lot.AttValue("Type") or ""),
                "link": link,
                "lane_index": int(lane_idx) if lane_idx.isdigit() else None,
                "zone": str(lot.AttValue("Zone") or ""),
                "pos": self._length_com_to_m(float(lot.AttValue("Pos")), unit),
                "length": self._length_com_to_m(float(lot.AttValue("Length")), unit),
                "len_per_space": self._length_com_to_m(float(lot.AttValue("LenPerSpc")), unit),
                "capacity": int(lot.AttValue("Capacity")),
                "attractivity": float(lot.AttValue("Attrac")),
                "max_park_time_s": float(lot.AttValue("ParkTmMax")),
                "des_speed_distr_no": str(lot.AttValue("DesSpeedDistrDef") or ""),
            }
        except VissimObjectNotFoundError:
            raise
        except Exception as exc:
            raise RuntimeError(f"Could not read parking lot {no}: {exc}") from exc

    def _write_parking_capacity(self, lot: Any, capacity: int, len_per_space_m: float | None) -> None:
        """Realise a requested SPACE COUNT through the only surface Vissim
        offers: ``Length = capacity x LenPerSpc`` (see landmine 2 above).

        **Write ORDER is chosen per call, and that is load-bearing.** A
        ``LenPerSpc`` larger than the lot's CURRENT ``Length`` is silently
        clamped to that length with no exception at all (landmine 3), so the
        order has to guarantee that whenever ``LenPerSpc`` is written there is
        already enough ``Length`` to hold it.

        The decision is made on **TARGET LENGTH vs CURRENT LENGTH**, not on
        whether the bay size grew or shrank:

        * ``target_length >= current_length`` -- write ``Length`` first, then
          ``LenPerSpc``. The lot is now at least as long as the target, and
          ``capacity >= 1`` means ``LenPerSpc <= target_length``, so the bay
          write cannot clamp.
        * ``target_length < current_length`` -- write ``LenPerSpc`` first,
          then ``Length``. The bay is written while the lot is still at its
          (larger) current length, and ``LenPerSpc <= target_length <
          current_length``, so again it cannot clamp.

        The earlier version branched on ``per_space_m > current_per_space_m``
        instead, which is right in every ordinary case but has a real hole a
        verifier found: from a DEGENERATE pre-existing state
        ``{Length 0, LenPerSpc 1}`` -- which the ``Length = 0`` landmine can
        produce outside these verbs -- a request for 10 spaces of 0.5 m looks
        like a SHRINKING bay (0.5 < 1), so ``LenPerSpc`` went first, was
        clamped to 0.0 against the zero length, and the lot ended at capacity
        0 instead of 10. The target-length test picks Length-first there
        (5.0 >= 0.0) and lands the correct pair.

        When ``len_per_space_m`` is ``None`` the lot's own current
        ``LenPerSpc`` is READ and reused (never a hardcoded metric default --
        Vissim's own fresh-lot default is 6.0 DISPLAY units, which is 1.8288 m
        on a feet network).

        ``capacity < 1`` is refused here as well as at the verb layer: the
        pydantic ``ge=1`` means production cannot reach it, but a zero target
        length would re-create the very degenerate state described above, so
        this function is made safe standalone rather than relying on its only
        current caller.
        """
        if int(capacity) < 1:
            raise ValueError(
                f"Parking lot capacity must be at least 1 space (got {capacity}); a zero-length "
                "lot is accepted silently by Vissim and reports as a zero-space lot on a network "
                "that still loads and runs."
            )
        unit = self._net_unit_length()
        current_per_space_m = self._length_com_to_m(float(lot.AttValue("LenPerSpc")), unit)
        current_length_m = self._length_com_to_m(float(lot.AttValue("Length")), unit)
        per_space_m = (
            float(len_per_space_m) if len_per_space_m is not None else current_per_space_m
        )
        target_length_m = float(capacity) * per_space_m

        if target_length_m >= current_length_m:
            lot.SetAttValue("Length", self._length_m_to_com(target_length_m, unit))
            if len_per_space_m is not None:
                lot.SetAttValue("LenPerSpc", self._length_m_to_com(per_space_m, unit))
        else:
            if len_per_space_m is not None:
                lot.SetAttValue("LenPerSpc", self._length_m_to_com(per_space_m, unit))
            lot.SetAttValue("Length", self._length_m_to_com(target_length_m, unit))

    def add_parking_lot(
        self,
        link_no: int,
        lane_index: int,
        pos_m: float,
        capacity: int,
        name: str = "",
        len_per_space_m: float | None = None,
    ) -> dict[str, Any]:
        """Create a parking lot on lane ``lane_index`` of ``link_no`` at
        ``pos_m`` metres holding ``capacity`` vehicles, auto-assigning the
        next free number (``max(existing) + 1``, or 1 on a network with none
        -- mirroring :meth:`add_pt_stop`/:meth:`add_vehicle_input`).

        ``capacity`` is REQUIRED and is realised as ``Length = capacity x
        LenPerSpc`` (landmine 2): a fresh lot's own default is ONE space, so
        a lot created and left alone is a single-bay lot, not the multi-space
        one a caller almost always means. The returned dict's ``capacity`` is
        Vissim's own READBACK of the derived value, never the argument.

        Returns :meth:`get_parking_lot`'s dict for the NEW lot. Raises
        :class:`VissimObjectNotFoundError` for a bogus ``link_no``/
        ``lane_index`` (checked via :meth:`_lane_by_index` before COM is
        asked, so the error names the real available lane indices).
        """
        try:
            lane = self._lane_by_index(link_no, lane_index)
            existing = self.available_parking_lot_nos()
            new_no = (max(existing) if existing else 0) + 1
            unit = self._net_unit_length()
            pos_com = self._length_m_to_com(float(pos_m), unit)
            lot = self.vissim.Net.ParkingLots.AddParkingLot(new_no, lane, pos_com)
            self._write_parking_capacity(lot, int(capacity), len_per_space_m)
            if name:
                lot.SetAttValue("Name", name)
        # ValueError unwrapped, same reasoning as set_parking_lot_capacity's
        # own handler: VissimObjectNotFoundError and _write_parking_capacity's
        # capacity floor are both deliberate, already-actionable messages.
        except ValueError:
            raise
        except Exception as exc:
            raise RuntimeError(
                f"Could not add a parking lot on link {link_no} lane {lane_index} at pos "
                f"{pos_m} with capacity {capacity}: {exc}"
            ) from exc
        return self.get_parking_lot(new_no)

    #: The ONLY lot species whose ``Capacity`` is derived from
    #: ``Length``/``LenPerSpc`` -- see :meth:`set_parking_lot_capacity` for
    #: the corpus evidence that the other two store it independently.
    PARKING_LOT_DERIVED_CAPACITY_TYPE = "REALPARKINGSPACE"

    def set_parking_lot_capacity(
        self, parking_lot_no: int, capacity: int, len_per_space_m: float | None = None
    ) -> dict[str, Any]:
        """Change an existing lot's space count -- via ``Length = capacity x
        LenPerSpc``, because ``Capacity`` itself is read-only (landmine 2).
        Returns :meth:`get_parking_lot`'s dict, whose ``capacity`` is the
        genuine readback.

        **REFUSES on any species but ``REALPARKINGSPACE``**, because the
        derivation landmine 2 documents is TYPE-SCOPED and the shipped claim
        was not. Measured: ``capacity == floor(length / lenPerSpc)`` holds on
        573/573 REALPARKINGSPACE lots in the corpus and is violated by 56/56
        ZONECONNECTORs (``capacity="10000"`` stored alongside ``length="3"``
        / ``lenPerSpc="6"``) and by this unit's own probe-created
        ABSTRACTPARKINGLOT (``length="6"``, ``lenPerSpc="6"``,
        ``capacity="100"``). On those species capacity is stored
        INDEPENDENTLY, so writing a length changes nothing about the space
        count -- the readback compare would then refuse and roll back, which
        is fail-safe but tells the caller nothing about why. An in-tool
        refusal that names the actual type is the ``remove_link``-refuses-a-
        connector precedent (no new constraint needed).
        """
        try:
            lot = self._parking_lot_by_no(parking_lot_no)
            lot_type = str(lot.AttValue("Type") or "").strip().upper()
            if lot_type != self.PARKING_LOT_DERIVED_CAPACITY_TYPE:
                # Kept SHORT on purpose: toolkit.dispatch truncates a raised
                # exception's text at 300 chars, so the actionable half has to
                # come first.
                raise VissimParkingLotTypeError(
                    f"Parking lot {parking_lot_no} is a {lot_type or 'unknown'}, not a "
                    f"{self.PARKING_LOT_DERIVED_CAPACITY_TYPE}: its capacity is stored "
                    "independently, not derived from length/length-per-space, so this verb "
                    "cannot change it. Edit that species in the Vissim GUI."
                )
            self._write_parking_capacity(lot, int(capacity), len_per_space_m)
        # ValueError is re-raised UNWRAPPED: every ValueError this block can
        # produce is one WE raised deliberately with an actionable message
        # (VissimObjectNotFoundError and VissimParkingLotTypeError both
        # subclass it, as does _write_parking_capacity's own capacity floor).
        # A real COM failure arrives as com_error/RuntimeError and still gets
        # the wrapping context below.
        except ValueError:
            raise
        except Exception as exc:
            raise RuntimeError(
                f"Could not set parking lot {parking_lot_no} capacity to {capacity}: {exc}"
            ) from exc
        return self.get_parking_lot(parking_lot_no)

    def remove_parking_lot(self, no: int) -> None:
        """Removes the lot (COM ``RemoveParkingLot``). Reference-integrity is
        NOT enforced by COM itself (landmine 4 above -- a still-referenced
        lot's removal SILENTLY CASCADE-DELETES the referencing
        ``vehicleRouteParking``); callers MUST check
        ``services.inpx_structure.find_id_references`` against the working
        copy's current XML BEFORE calling this method (see
        ``tools_edit.remove_parking_lot``)."""
        try:
            lot = self._parking_lot_by_no(no)
            self.vissim.Net.ParkingLots.RemoveParkingLot(lot)
        except VissimObjectNotFoundError:
            raise
        except Exception as exc:
            raise RuntimeError(f"Could not remove parking lot {no}: {exc}") from exc

    # -----------------------------------------------------------------
    # PEDESTRIAN (Viswalk) verbs -- Q6 unit (3)
    #
    # Everything below is measured live against Vissim 2026 SP09 by
    # ``backend/tools/probe_pedestrian.py`` (18 parts, null control,
    # ``--audit`` re-derivation); that file's docstring is the long form.
    #
    # **THE LICENCE VERDICT (asked first, because a refusal would have made
    # this whole block a read-only representation instead).** This box CAN
    # create and simulate Viswalk objects. There is NO licence-naming COM
    # attribute to interrogate -- ``LicenseInfo``/``License``/``Modules``/
    # ``AddOns`` and four more all raise "Could not find attribute with ID";
    # the only two that resolve are ``Version`` (``'PTV Vissim Academic 2026
    # (SP 09)'``) and ``VersionNumber`` (``'260009'``). So the verdict is
    # BEHAVIOURAL, measured at four levels: a Viswalk-bearing network loads,
    # all nine pedestrian containers enumerate, ``RunSingleStep`` runs, and
    # both ``AddPedestrianInput`` and ``AddArea`` create. Recorded so nobody
    # re-derives it: that was an ACADEMIC licence. A deployment box whose
    # Vissim licence lacks the Viswalk add-on is NOT covered by this
    # measurement and its failure mode is unmeasured.
    #
    # **Plain ``Add`` works -- no duplicate-then-edit.** Only the second
    # family (after vehicle inputs) where that is true; contrast driving
    # behaviours, priority rules and signal controllers, all of which need a
    # ``Duplicate*``. ``AddPedestrianInput(Key, Area)`` needs a real COM Area
    # OBJECT, not an int -- the same pywin32 marshalling shape
    # ``AddVehicleInput``'s ``Link`` has. And there is NO family limitation: a
    # network with zero areas can mint its first and then its first input on
    # it (proven on two different networks).
    #
    # **A fresh input's defaults, live-read:** ``Name=''``, ``Area`` = the
    # area passed, ``Volume(1)=0.0``, ``PedComp(1)='1'``,
    # ``VolType(1)='STOCHASTIC'``, ``Cont(1)=0``, ``X=0.0``, ``Y=0.0``. A
    # created-and-left-alone input therefore generates NOTHING, which is why
    # ``volume_pph`` is a REQUIRED parameter of :meth:`add_pedestrian_input`,
    # not an optional one. Created inputs DO generate real pedestrians: 36
    # observed in a bounded 60 s window at 2000 ped/h (expected 33.3) against
    # a null control of 0, with the ambient vehicle count identical (208) in
    # both arms as the "the run really ran" witness.
    #
    # **LANDMINE 1 -- the origin area needs a ROUTING DECISION.** A pedestrian
    # input whose origin area carries none makes the SAVED network fail
    # Vissim's own initialisation: ``LoadNet`` succeeds, init dies with "The
    # simulation could not be continued." Six probe arms isolate the cause to
    # the AREA: volume 0 fails exactly as volume 750 does, and an area that
    # SHIPPED with the network fails exactly as a freshly created one does,
    # while a bare created area with no input on it is harmless. This layer
    # cannot create routing decisions, so ``tools_edit.add_pedestrian_input``
    # REFUSES before COM is touched (constraint
    # ``pedestrian_input_origin_area_has_no_route``).
    #
    # **LANDMINE 2 -- ``RemoveArea`` silently cascade-deletes routes with a
    # GREEN oracle.** Removing Beijing's area 1 moved the persisted counts by
    # ``{'areas': -1, 'pedestrian_inputs': -1, 'ped_volume_intervals': -1,
    # 'ped_routing_decisions_static': -1, 'ped_routes_static': -4,
    # 'ped_route_locations': -4}`` -- a bigger blast radius than its 4 direct
    # referrers suggest (the routing decision that ORIGINATES there takes its
    # own 2 routes, and 2 further routes elsewhere that used area 1 as a
    # DESTINATION go too) -- and ``load_ok``/``init_ok`` were both True
    # afterwards. Vissim's own oracle is green on a network that just lost 4
    # pedestrian routes. No area-removal verb ships in this unit; see
    # ``constraints.pedestrian_area_edit_unsupported``.
    #
    # **LANDMINE 3 -- intervals belong to the NETWORK.** One input created on
    # the 14-interval Air Taxi Vertiport network persisted with all 14
    # intervals: interval 1 carrying the requested volume, 2..14 at zero. On
    # 1-interval Beijing the same call adds exactly 1. ``Volume(2)`` on a
    # 1-interval network raises outright ("Object 2 not found"), so this
    # module writes interval 1 only and the agent layer WARNS about the rest.
    #
    # **Writability, readback-judged:** ``Name``/``Volume(1)``/``VolType(1)``
    # writable exactly; ``PedComp(1)`` writable (measured on a FOUR-composition
    # network -- on single-composition Beijing the write is indistinguishable
    # from a no-op, which is why it was re-measured elsewhere); ``Area`` and
    # ``No`` both writable (they re-anchor / renumber an existing input, and
    # neither is wrapped here). ``Cont(1)`` is REFUSED LOUDLY ("Attribute
    # Continued (0-MAX) cannot be changed"), and so is a bogus composition
    # number ("Object 1011 of type Pedestrian Composition does not exist") --
    # nothing in this family fails silently, which is unusual enough to be
    # worth stating.
    # -----------------------------------------------------------------

    def available_pedestrian_input_nos(self) -> list[int]:
        return self._collection_nos(self.vissim.Net.PedestrianInputs)

    def available_pedestrian_area_nos(self) -> list[int]:
        return self._collection_nos(self.vissim.Net.Areas)

    def _pedestrian_input_by_no(self, no: int) -> Any:
        return self._item_by_key(
            self.vissim.Net.PedestrianInputs,
            no,
            "Pedestrian input",
            self.available_pedestrian_input_nos(),
        )

    def _pedestrian_area_by_no(self, no: int) -> Any:
        return self._item_by_key(
            self.vissim.Net.Areas, no, "Pedestrian area", self.available_pedestrian_area_nos()
        )

    def get_pedestrian_input(self, no: int) -> dict[str, Any]:
        """Read one pedestrian input's identity + interval-1 demand:
        ``{"no", "name", "area", "volume", "composition_no",
        "volume_type"}``.

        The paired reader for every pedestrian write below (guardrail
        doctrine: every setter needs a reader used for post-write
        verification).

        ``no`` is READ BACK (``str(int(AttValue("No")))``), never echoed.
        :meth:`add_pedestrian_input` returns this dict for a number it
        COMPUTED (``max+1``) and the agent layer lifts the new object's id
        straight out of it -- echoing the argument would make the verb's own
        identity claim unfalsifiable, which is exactly the defect Q5 unit (2)
        shipped in ``get_signal_controller``, Q5 unit (3) had to guard
        against in ``get_vehicle_input``, and Q6 unit (2) found eleven more
        instances of. ``int()`` mirrors :meth:`_collection_nos`'s own guard so
        a float-typed ``No`` could never render as "9.0" and miss every
        shadow-model id keyed "9".

        ``volume``/``composition_no``/``volume_type`` are interval 1's -- the
        only interval this module writes. A multi-interval network carries
        more; the caller reports the real interval count from a reparse of
        the persisted file, since COM exposes no interval-count attribute
        this method could read (measured: ``Volume(2)`` on a 1-interval
        network raises rather than returning anything).
        """
        try:
            pedestrian_input = self._pedestrian_input_by_no(no)
            return {
                "no": str(int(pedestrian_input.AttValue("No"))),
                "name": str(pedestrian_input.AttValue("Name") or ""),
                "area": str(pedestrian_input.AttValue("Area") or ""),
                "volume": float(pedestrian_input.AttValue("Volume(1)")),
                "composition_no": str(pedestrian_input.AttValue("PedComp(1)") or ""),
                "volume_type": str(pedestrian_input.AttValue("VolType(1)") or ""),
            }
        except VissimObjectNotFoundError:
            raise
        except Exception as exc:
            raise RuntimeError(f"Could not read pedestrian input {no}: {exc}") from exc

    def add_pedestrian_input(
        self,
        area_no: int,
        volume_pph: float,
        name: str = "",
        composition_no: int | None = None,
    ) -> dict[str, Any]:
        """Create a pedestrian input on ``area_no`` carrying ``volume_pph``
        pedestrians/h in interval 1, auto-assigning the next free number
        (``max(existing) + 1``, or 1 on a network with none).

        ``volume_pph`` is REQUIRED, not optional: a fresh input's own
        ``Volume(1)`` default is 0.0 (live-measured), i.e. an input created
        and left alone generates nothing at all.

        ``composition_no`` is optional; omitted leaves Vissim's own default
        (``PedComp(1) = '1'``). A bogus number is refused by Vissim itself,
        loudly, and that refusal is surfaced rather than swallowed.

        Returns :meth:`get_pedestrian_input`'s dict for the NEW input. Raises
        :class:`VissimObjectNotFoundError` for a bogus ``area_no`` (checked
        via :meth:`_pedestrian_area_by_no` before the create is attempted, so
        the error names the real available area numbers).

        CALLER CONTRACT: this method does NOT check whether ``area_no`` has a
        pedestrian routing decision. It cannot -- COM exposes the routing
        decisions but the refusal has to happen before any COM session opens
        to be useful. ``tools_edit.add_pedestrian_input`` performs that check
        against the working copy's XML (see landmine 1 above).
        """
        try:
            area = self._pedestrian_area_by_no(area_no)
            existing = self.available_pedestrian_input_nos()
            new_no = (max(existing) if existing else 0) + 1
            pedestrian_input = self.vissim.Net.PedestrianInputs.AddPedestrianInput(new_no, area)
            if name:
                pedestrian_input.SetAttValue("Name", name)
            if composition_no is not None:
                pedestrian_input.SetAttValue("PedComp(1)", composition_no)
            pedestrian_input.SetAttValue("Volume(1)", float(volume_pph))
        except VissimObjectNotFoundError:
            raise
        except Exception as exc:
            raise RuntimeError(
                f"Could not add a pedestrian input on area {area_no} at "
                f"{volume_pph} ped/h: {exc}"
            ) from exc
        return self.get_pedestrian_input(new_no)

    def set_pedestrian_input_volume(self, no: int, volume_pph: float) -> dict[str, Any]:
        """Set an existing pedestrian input's interval-1 ``Volume(1)``
        (ped/h) and return :meth:`get_pedestrian_input`'s dict, read back
        from Vissim rather than echoed.

        Interval 1 only, deliberately: the interval STRUCTURE belongs to the
        network (landmine 3), and ``Volume(i)`` for an ``i`` the network does
        not have raises rather than silently creating one.
        """
        try:
            pedestrian_input = self._pedestrian_input_by_no(no)
            pedestrian_input.SetAttValue("Volume(1)", float(volume_pph))
        except VissimObjectNotFoundError:
            raise
        except Exception as exc:
            raise RuntimeError(
                f"Could not set pedestrian input {no}'s volume to {volume_pph} ped/h: {exc}"
            ) from exc
        return self.get_pedestrian_input(no)

    def remove_pedestrian_input(self, no: int) -> None:
        """Remove the pedestrian input (COM ``RemovePedestrianInput``).

        Measured CLEAN, unlike its area-side sibling: removing Beijing's
        input 1 moved the persisted counts by exactly
        ``{'pedestrian_inputs': -1, 'ped_volume_intervals': -1}`` -- areas
        16->16, routing decisions 8->8 -- and the load oracle stayed green.
        Nothing in the corpus references a pedestrian input by id (the
        static referrer scan finds zero sites), so there is no cascade to
        guard here; the cascade risk in this family is entirely on
        ``RemoveArea``, which this module deliberately does not wrap.
        """
        try:
            pedestrian_input = self._pedestrian_input_by_no(no)
            self.vissim.Net.PedestrianInputs.RemovePedestrianInput(pedestrian_input)
        except VissimObjectNotFoundError:
            raise
        except Exception as exc:
            raise RuntimeError(f"Could not remove pedestrian input {no}: {exc}") from exc

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


#: Character budget for the rendered key list inside a
#: :class:`VissimObjectNotFoundError`. The COUNT cap alone is not enough:
#: ``toolkit.dispatch`` truncates a raised exception's text at 300 chars, and
#: 20 EIGHT-DIGIT ids (a real shape -- Motorway Dyn Assign Mainz.DE's own
#: zones are numbered 1000000+, and a 573-lot network can carry ids that wide)
#: measured 313 chars of ``str(exc)``, so dispatch cut the message mid-number.
#: An arbitrary mid-number cut is strictly worse than a controlled tail --
#: the same reasoning ``inpx_structure.ReferenceIntegrityError`` already
#: documents for its own referrer list. 180 leaves room for the surrounding
#: "<Label> <key> does not exist ... Available <label> numbers: ..." frame
#: inside the 300-char cap for every label this module uses.
_AVAILABLE_KEYS_CHAR_BUDGET = 180


def _format_available_keys(values: list[int], limit: int = 20) -> str:
    """Bounded rendering of "which ids DO exist" -- capped by COUNT (``limit``)
    and, since Q6 unit (2)'s verification, by rendered LENGTH as well (see
    :data:`_AVAILABLE_KEYS_CHAR_BUDGET`). Whatever is dropped by either cap is
    reported honestly as ``(+N more)``.

    Behaviour change shared by EVERY verb that raises through
    ``_item_by_key``/``_lane_by_index`` -- in the fail-safe direction (a
    shorter, complete message instead of a longer, truncated one)."""
    if not values:
        return "none found"

    shown: list[str] = []
    used = 0
    for value in values[:limit]:
        rendered = str(value)
        # +2 for the ", " separator every entry after the first costs.
        cost = len(rendered) + (2 if shown else 0)
        if shown and used + cost > _AVAILABLE_KEYS_CHAR_BUDGET:
            break
        shown.append(rendered)
        used += cost

    overflow = len(values) - len(shown)
    suffix = f", ... (+{overflow} more)" if overflow > 0 else ""
    return ", ".join(shown) + suffix
