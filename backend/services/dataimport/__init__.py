"""Format-neutral data import layer (roadmap 0.7, Track D).

The package is named ``dataimport`` rather than the roadmap's ``import``
because ``import`` is a Python keyword and cannot name a package.

Layering, strictly one-directional::

    schema.py     neutral domain model — knows no file format
        ^
    base.py       adapter protocol + ImportResult/ImportIssue transport
        ^
    quality.py    MRWA collection-standard checks over neutral records (R2.5)
    demand.py     neutral counts -> vehicle-input / composition params (R1.4)
        ^
    adapters/     one module per region; the ONLY place a file format appears

``quality.py`` and ``demand.py`` are siblings, not a stack: both read only
the neutral model. (``demand.py`` imports one predicate
from ``quality.py`` — ``is_all_class_total`` — so that "which records are a sum
of other records" has exactly one definition.)

The contract for whoever writes adapter #3 lives in
``docs/product/adapter-contract.md``.

Nothing in this package imports Vissim or needs a licence — everything here is
testable on a laptop with no simulator installed.
"""
from __future__ import annotations

from .adapters import DaejeonCountAdapter, SCATSWAAdapter
from .base import (
    AdapterError,
    AdapterInfo,
    DataAdapter,
    FieldMapping,
    ImportIssue,
    ImportResult,
    IssueSeverity,
    SniffResult,
    check_adapter_conformance,
    get_adapter,
    list_adapters,
    register_adapter,
    select_adapter,
    unregister_adapter,
)
from .demand import DemandSeeding, build_demand_seeding, seedable_counts
from .quality import QualityThresholds, apply_quality_gate, run_quality_gate

# Explicit registration, guarded so a module reload cannot raise.
if get_adapter("scats_wa") is None:
    register_adapter(SCATSWAAdapter())
if get_adapter("daejeon_smart_intersection") is None:
    register_adapter(DaejeonCountAdapter())

__all__ = [
    "AdapterError",
    "AdapterInfo",
    "DaejeonCountAdapter",
    "DataAdapter",
    "DemandSeeding",
    "FieldMapping",
    "ImportIssue",
    "ImportResult",
    "IssueSeverity",
    "QualityThresholds",
    "SCATSWAAdapter",
    "SniffResult",
    "apply_quality_gate",
    "build_demand_seeding",
    "check_adapter_conformance",
    "get_adapter",
    "list_adapters",
    "register_adapter",
    "run_quality_gate",
    "seedable_counts",
    "select_adapter",
    "unregister_adapter",
]
