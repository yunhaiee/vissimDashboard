"""Format-neutral data import layer (roadmap 0.7, Track D).

The package is named ``dataimport`` rather than the roadmap's ``import``
because ``import`` is a Python keyword and cannot name a package.

Layering, strictly one-directional::

    schema.py     neutral domain model — knows no file format
        ^
    base.py       adapter protocol + ImportResult/ImportIssue transport
        ^
    quality.py    MRWA collection-standard checks over neutral records (R2.5)
        ^
    adapters/     one module per region; the ONLY place a file format appears

The contract for whoever writes adapter #3 lives in
``docs/product/adapter-contract.md``.

Nothing in this package imports Vissim or needs a licence — everything here is
testable on a laptop with no simulator installed.
"""
from __future__ import annotations

from .adapters import SCATSWAAdapter
from .base import (
    AdapterError,
    AdapterInfo,
    DataAdapter,
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
from .quality import QualityThresholds, apply_quality_gate, run_quality_gate

# Explicit registration, guarded so a module reload cannot raise.
if get_adapter("scats_wa") is None:
    register_adapter(SCATSWAAdapter())

__all__ = [
    "AdapterError",
    "AdapterInfo",
    "DataAdapter",
    "ImportIssue",
    "ImportResult",
    "IssueSeverity",
    "QualityThresholds",
    "SCATSWAAdapter",
    "SniffResult",
    "apply_quality_gate",
    "check_adapter_conformance",
    "get_adapter",
    "list_adapters",
    "register_adapter",
    "run_quality_gate",
    "select_adapter",
    "unregister_adapter",
]
