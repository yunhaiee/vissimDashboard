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
  against.

CLI: ``backend/tools/genesis_osm_draft.py``.
"""
from __future__ import annotations

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
    "ComplianceItem",
    "ComplianceReport",
    "ComplianceState",
    "ConnectorDiff",
    "ConnectorRewire",
    "DiffFinding",
    "DiffSeverity",
    "DraftConnector",
    "DraftLink",
    "DraftNetwork",
    "DraftNode",
    "EmitRefused",
    "ExtentCheck",
    "GeneratorAssumption",
    "LaneCountChange",
    "LaneProvenance",
    "LinkDiff",
    "LinkReclassification",
    "LocalTangentPlane",
    "MovementRef",
    "NetworkSummary",
    "NetworkTopology",
    "NodeMovementDiff",
    "NodeSignalCoverage",
    "OsmExtract",
    "OsmWay",
    "PositionScope",
    "StructuralDiff",
    "build_draft",
    "diff_network_files",
    "diff_topologies",
    "emit_inpx",
    "load_topology",
    "origin_for_bounds",
    "parse_osm_file",
    "render_diff_report",
    "to_xml_text",
    "topology_from_root",
]
