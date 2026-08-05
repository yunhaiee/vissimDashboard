"""Network genesis: build a Vissim network from data, and check what was built.

Roadmap Phase 2, Track G. The package is deliberately separate from the
``assistant_router.py`` monolith and from ``services/agent/`` — nothing here
edits a live model, nothing here imports Vissim, and nothing here needs a
licence seat. Everything is offline, pure-python and cross-platform.

First member: :mod:`services.genesis.diff`, the structural (topology-only)
comparison of two parsed ``.inpx`` networks that backs the Murdoch benchmark
diff report (roadmap R1.6 / row 2.3).
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

__all__ = [
    "ConnectorDiff",
    "ConnectorRewire",
    "DiffFinding",
    "DiffSeverity",
    "LaneCountChange",
    "LinkDiff",
    "LinkReclassification",
    "MovementRef",
    "NetworkSummary",
    "NetworkTopology",
    "NodeMovementDiff",
    "NodeSignalCoverage",
    "PositionScope",
    "StructuralDiff",
    "diff_network_files",
    "diff_topologies",
    "load_topology",
    "render_diff_report",
    "topology_from_root",
]
