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
* :mod:`services.genesis.draft` — links, connectors and the MRWA §5 compliance
  report (applied / violated / NOT CHECKABLE).
* :mod:`services.genesis.emit` — draft → ``.inpx``, scoped to what
  ``services.inpx_parser`` can read back, byte-identical across runs.
* :mod:`services.genesis.diff` — the structural (topology-only) comparison of
  two parsed ``.inpx`` networks that backs the Murdoch benchmark diff report
  (roadmap R1.6 / row 2.3), and the oracle the emitter's round-trip is tested
  against. Valid between two revisions of ONE file: it matches by id.
* :mod:`services.genesis.benchmark` — benchmark v2: correspondence between two
  networks authored INDEPENDENTLY, where id equality is meaningless and the
  match is geometric (proximity + direction + polyline overlap, every tolerance
  derived from the georeference's own residual).

CLIs: ``backend/tools/genesis_osm_draft.py`` (osm -> draft),
``backend/tools/genesis_benchmark.py`` (draft vs consultant model).
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
from .osm import LaneProvenance, OsmExtract, OsmWay, parse_osm_file
from .project import ExtentCheck, LocalTangentPlane, origin_for_bounds

__all__ = [
    "MATCH_TOLERANCE_M",
    "AmbiguousObject",
    "BenchmarkInvariantError",
    "BenchmarkMethods",
    "ChainMember",
    "ComplianceItem",
    "ComplianceReport",
    "ComplianceState",
    "ConnectorDiff",
    "ConnectorRewire",
    "CorridorScope",
    "DiffFinding",
    "DiffSeverity",
    "DraftConnector",
    "DraftLink",
    "DraftNetwork",
    "DraftNode",
    "EmitRefused",
    "ExtentCheck",
    "GeneratedScope",
    "GeneratorAssumption",
    "GeoNetwork",
    "GeoObject",
    "LaneAgreement",
    "LaneCountChange",
    "LaneProvenance",
    "LinkDiff",
    "LinkReclassification",
    "LocalTangentPlane",
    "MatchBucket",
    "MatchedPair",
    "MissingObject",
    "MissingReason",
    "MovementRef",
    "NetworkSummary",
    "NetworkTopology",
    "NodeMovementDiff",
    "NodeSignalCoverage",
    "OsmExtract",
    "OsmWay",
    "PositionScope",
    "SignalScopeGap",
    "StructuralDiff",
    "TopologicalBenchmark",
    "benchmark_network_files",
    "benchmark_networks",
    "build_draft",
    "diff_network_files",
    "diff_topologies",
    "emit_inpx",
    "georeferenced_network",
    "load_topology",
    "local_frame_network",
    "origin_for_bounds",
    "parse_osm_file",
    "render_benchmark_report",
    "render_diff_report",
    "to_xml_text",
    "topology_from_root",
]
