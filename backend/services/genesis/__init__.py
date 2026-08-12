"""Network genesis: build a Vissim network from data, and check what was built.

Roadmap Phase 2, Track G. The package is deliberately separate from the
``assistant_router.py`` monolith and from ``services/agent/`` — nothing here
edits a live model, nothing here imports Vissim, and nothing here needs a
licence seat. Everything is offline, pure-python and cross-platform.

Members, in the order row 2.3 runs them:

* :mod:`services.genesis.osm` — a local ``.osm`` file → drivable ways, nodes and
  intersections, every lane count carrying its provenance and every skipped way
  named.
* :mod:`services.genesis.project` — WGS84 → local tangent-plane metres, with a
  derived error bound and a refusal outside its validity radius. RELATIVE
  geometry; not a georeference.
* :mod:`services.genesis.connectivity` — which OSM nodes are the same junction,
  where a road really ends, and what the map's turn restrictions ban. Runs
  before ``draft`` and decides whether the generated network is DRIVABLE: a link
  end no connector leaves is a place vehicles silently depart the model.
  ``backend/tools/genesis_connectivity_probe.py`` measures the result.
* :mod:`services.genesis.draft` — links, connectors and the MRWA §5 compliance
  report (applied / violated / NOT CHECKABLE).
* :mod:`services.genesis.demand` — 1.2's neutral counts + a draft → vehicle
  inputs, vehicle compositions and static routing decisions, joined to the draft
  through a caller-supplied map, with §5.2.2/§5.2.3/§5.5 graded in the same three
  states. Roadmap 3.2 Genesis (c); optional, and additive to the emission.
* :mod:`services.genesis.signals` — a 1.6 ``FixedTimePlan`` + a draft node →
  fixed-time signal controllers, signal groups (one per approach), signal heads
  and §5.4.2.6-fallback detectors, with §5.4.2 graded in the same three states.
  Roadmap 3.1 Genesis (b); optional, and additive to the emission.
* :mod:`services.genesis.signals_daejeon` — roadmap 3.1's Korean half: the 대전
  ``교차로 운영 DATABASE`` sheet's text layer → neutral ``SignalControllerData``,
  plus a bridge into the ``FixedTimePlan`` ``signals`` takes. Refuses to invent
  the yellow/all-red split the sheet does not state.
* :mod:`services.genesis.compose` — the ONE order the optional layers are built
  in (fleet → demand-off-the-fleet's-map → signals) plus the cross-checks that
  make the set all-or-nothing. Extracted verbatim from the four-layer build a
  real PTV Vissim 2026 SP09 opened (``445477d``), so the product pipeline and
  the live check compose identically instead of by two copies of a convention.
* :mod:`services.genesis.side_inputs` — the DATA a caller adds to a generation
  (counts + the site→leg→link join; controllers + the approach→phase mapping),
  as the neutral ``ImportResult`` the adapters already emit. Validated before a
  token exists; refuses rather than guessing either join.
* :mod:`services.genesis.emit` — draft (+ optional signals) → ``.inpx``, scoped
  to what ``services.inpx_parser`` can read back, byte-identical across runs.
* :mod:`services.genesis.diff` — the structural (topology-only) comparison of
  two parsed ``.inpx`` networks that backs the Murdoch benchmark diff report
  (roadmap R1.6 / row 2.3), and the oracle the emitter's round-trip is tested
  against. Valid between two revisions of ONE file: it matches by id.
* :mod:`services.genesis.benchmark` — benchmark v2: correspondence between two
  networks authored INDEPENDENTLY, where id equality is meaningless and the
  match is geometric (proximity + direction + polyline overlap, every tolerance
  derived from the georeference's own residual).
* :mod:`services.genesis.gate` — the R1.5 validation gate (V1–V9), roadmap
  3.3: genesis's shipping gate. Every check is ``PASS``/``FAIL``/
  ``NOT_CHECKABLE``, a check that could not run is never a pass, and the
  verdict is a pass only when every refuse-tier check genuinely passed — which
  includes V1, so an OFFLINE run can never report an unqualified pass.

CLIs: ``backend/tools/genesis_osm_draft.py`` (osm -> draft),
``backend/tools/genesis_benchmark.py`` (draft vs consultant model),
``backend/tools/genesis_signals_daejeon_probe.py`` (대전 signal-sheet coverage),
``backend/tools/genesis_validation_gate.py`` (the R1.5 gate).
"""
from __future__ import annotations

from .benchmark import (
    MATCH_TOLERANCE_M,
    AmbiguousObject,
    BenchmarkInvariantError,
    BenchmarkMethods,
    ChainMember,
    CorridorScope,
    GeneratedScope,
    GeoNetwork,
    GeoObject,
    LaneAgreement,
    MatchBucket,
    MatchedPair,
    MissingObject,
    MissingReason,
    SignalScopeGap,
    TopologicalBenchmark,
    benchmark_network_files,
    benchmark_networks,
    georeferenced_network,
    local_frame_network,
    render_benchmark_report,
)
from .compose import (
    NO_LAYERS,
    ComposedLayers,
    compose_layers,
)
from .connectivity import (
    NODE_MERGE_TOLERANCE_M,
    TOLERANCE_BASIS as NODE_MERGE_TOLERANCE_BASIS,
    DeadEnd,
    MergeReport,
    NodeMerge,
    dead_ends,
    merge_junction_nodes,
)
from .demand import (
    NO_DEMAND,
    CompositionFlow,
    DemandCoverage,
    DemandInterval,
    DemandSettings,
    DraftDemand,
    RouteSeed,
    RoutingDecisionSeed,
    VehicleCompositionPlan,
    VehicleInputPlan,
    VehicleTypeRef,
    entry_links,
    generate_demand,
    link_exits,
    route_share,
)
from .diff import (
    ConnectorDiff,
    ConnectorRewire,
    DiffFinding,
    DiffSeverity,
    LaneCountChange,
    LinkDiff,
    LinkReclassification,
    MovementRef,
    NetworkSummary,
    NetworkTopology,
    NodeMovementDiff,
    NodeSignalCoverage,
    PositionScope,
    StructuralDiff,
    diff_network_files,
    diff_topologies,
    load_topology,
    render_diff_report,
    topology_from_root,
)
from .draft import (
    ComplianceItem,
    ComplianceReport,
    ComplianceState,
    DraftConnector,
    DraftLink,
    DraftNetwork,
    DraftNode,
    GeneratorAssumption,
    build_draft,
)
from .emit import EmitRefused, emit_inpx, to_xml_text
from .gate import (
    CheckResult,
    CheckState,
    CheckTier,
    GateFinding,
    GateGap,
    GateResult,
    GateSettings,
    GateVerdict,
    LoadOracle,
    render_report as render_gate_report,
    run_gate,
)
from .fleet import (
    NO_FLEET,
    DraftFleet,
    EmittedVehicleType,
    FleetCoverage,
    FleetSettings,
    MrwaVehicleType,
    build_fleet,
    fleet_containers,
    observed_vehicle_classes,
)
from .osm import LaneProvenance, OsmExtract, OsmWay, parse_osm_file
from .project import ExtentCheck, LocalTangentPlane, origin_for_bounds
from .signals import (
    NO_SIGNALS,
    DetectorPlan,
    DraftSignals,
    NodeSignals,
    SignalGroupPlan,
    SignalHeadPlan,
    SignalSettings,
    generate_node_signals,
    generate_signals,
    node_approaches,
)
from .side_inputs import (
    DemandSideInput,
    SideInputRefused,
    SignalsSideInput,
    build_signal_assignments,
    compose_from_documents,
    parse_side_input,
)
from .signals_daejeon import (
    DaejeonSettings,
    DaejeonSheet,
    parse_pdf as parse_daejeon_signal_pdf,
    parse_sheet_text as parse_daejeon_signal_sheet,
    parse_zip as parse_daejeon_signal_zip,
    to_fixed_time_plan as daejeon_to_fixed_time_plan,
)

__all__ = [
    "AmbiguousObject",
    "BenchmarkInvariantError",
    "BenchmarkMethods",
    "ChainMember",
    "CheckResult",
    "CheckState",
    "CheckTier",
    "ComplianceItem",
    "ComplianceReport",
    "ComplianceState",
    "ComposedLayers",
    "CompositionFlow",
    "ConnectorDiff",
    "ConnectorRewire",
    "CorridorScope",
    "DaejeonSettings",
    "DaejeonSheet",
    "DeadEnd",
    "DemandCoverage",
    "DemandInterval",
    "DemandSettings",
    "DemandSideInput",
    "DetectorPlan",
    "DiffFinding",
    "DiffSeverity",
    "DraftConnector",
    "DraftDemand",
    "DraftFleet",
    "DraftLink",
    "DraftNetwork",
    "DraftNode",
    "DraftSignals",
    "EmitRefused",
    "EmittedVehicleType",
    "ExtentCheck",
    "FleetCoverage",
    "FleetSettings",
    "GateFinding",
    "GateGap",
    "GateResult",
    "GateSettings",
    "GateVerdict",
    "GeneratedScope",
    "GeneratorAssumption",
    "GeoNetwork",
    "GeoObject",
    "LaneAgreement",
    "LaneCountChange",
    "LaneProvenance",
    "LinkDiff",
    "LinkReclassification",
    "LoadOracle",
    "LocalTangentPlane",
    "MATCH_TOLERANCE_M",
    "MatchBucket",
    "MatchedPair",
    "MergeReport",
    "MissingObject",
    "MissingReason",
    "MovementRef",
    "MrwaVehicleType",
    "NODE_MERGE_TOLERANCE_BASIS",
    "NODE_MERGE_TOLERANCE_M",
    "NO_DEMAND",
    "NO_FLEET",
    "NO_LAYERS",
    "NO_SIGNALS",
    "NetworkSummary",
    "NetworkTopology",
    "NodeMerge",
    "NodeMovementDiff",
    "NodeSignalCoverage",
    "NodeSignals",
    "OsmExtract",
    "OsmWay",
    "PositionScope",
    "RouteSeed",
    "RoutingDecisionSeed",
    "SideInputRefused",
    "SignalGroupPlan",
    "SignalHeadPlan",
    "SignalScopeGap",
    "SignalSettings",
    "SignalsSideInput",
    "StructuralDiff",
    "TopologicalBenchmark",
    "VehicleCompositionPlan",
    "VehicleInputPlan",
    "VehicleTypeRef",
    "benchmark_network_files",
    "benchmark_networks",
    "build_draft",
    "build_fleet",
    "build_signal_assignments",
    "compose_from_documents",
    "compose_layers",
    "daejeon_to_fixed_time_plan",
    "dead_ends",
    "diff_network_files",
    "diff_topologies",
    "emit_inpx",
    "entry_links",
    "fleet_containers",
    "generate_demand",
    "generate_node_signals",
    "generate_signals",
    "georeferenced_network",
    "link_exits",
    "load_topology",
    "local_frame_network",
    "merge_junction_nodes",
    "node_approaches",
    "observed_vehicle_classes",
    "origin_for_bounds",
    "parse_daejeon_signal_pdf",
    "parse_daejeon_signal_sheet",
    "parse_daejeon_signal_zip",
    "parse_osm_file",
    "parse_side_input",
    "render_benchmark_report",
    "render_diff_report",
    "render_gate_report",
    "route_share",
    "run_gate",
    "to_xml_text",
    "topology_from_root",
]
