"""Format-neutral data import layer (roadmap 0.7, Track D).

The package is named ``dataimport`` rather than the roadmap's ``import``
because ``import`` is a Python keyword and cannot name a package.

Layering, strictly one-directional::

    schema.py     neutral domain model — knows no file format
        ^
    base.py       adapter protocol + ImportResult/ImportIssue transport
        ^
    quality.py    MRWA collection-standard checks over neutral records (R2.5)
    fixed_time.py MRWA Appendix-A A.2/A.3 SCATS→fixed-time conversion (1.6)
    demand.py     neutral counts -> vehicle-input / composition params (R1.4)
        ^
    linsig.py     MRWA OMG §3 LinSig: Table 3-3/3-4, DoS, PRC, MMQ (1.6)
        ^
    adapters/     one module per region; the ONLY place a file format appears

``quality.py``, ``fixed_time.py`` and ``demand.py`` are siblings, not a stack:
all three read only the neutral model. (``demand.py`` imports one predicate
from ``quality.py`` — ``is_all_class_total`` — so that "which records are a sum
of other records" has exactly one definition.)

``linsig.py`` sits one step above ``fixed_time.py`` for the same
one-definition reason and no other: Appendix A §A.2 and OMG §3.5.15.2 state the
SAME weighted-average rule under different names, so the LinSig entry points
delegate to Appendix A's three functions rather than restating the arithmetic
(that module's "A.2 reconciliation" section works it out). Otherwise it is a
sibling too — it reads no file format, and it imports nothing from
``schema.py`` because its inputs are LinSig model objects (phase-sequence
cases, signal groups split by give-way period, lanes), which the neutral model
deliberately does not carry.

**Two validation regimes live in this package, and they must not be mixed.**
``fixed_time.py`` produces a plan for *Vissim*; ``linsig.py`` produces numbers
for *LinSig*, which MRWA validates on degree of saturation and queue, NOT on
GEH. Every ``linsig.py`` result and issue carries ``linsig.REGIME`` saying so.

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
from .fixed_time import (
    ConversionSettings,
    FixedTimePlan,
    PhaseDerivation,
    convert_to_fixed_time,
)
from .linsig import (
    BonusGreenTable,
    DegreeOfSaturationValidation,
    InfrequentPhaseTable,
    LinSigSettings,
    MeanMaximumQueueReport,
    PhaseObservation,
    PhaseSequenceCase,
    SignalGroupSpec,
    ValidationDataBasis,
    build_infrequent_phase_table,
    compute_bonus_green,
    convert_mean_maximum_queues,
    validate_degree_of_saturation,
)
from .quality import QualityThresholds, apply_quality_gate, run_quality_gate

# Explicit registration, guarded so a module reload cannot raise.
if get_adapter("scats_wa") is None:
    register_adapter(SCATSWAAdapter())
if get_adapter("daejeon_smart_intersection") is None:
    register_adapter(DaejeonCountAdapter())

__all__ = [
    "AdapterError",
    "AdapterInfo",
    "BonusGreenTable",
    "ConversionSettings",
    "DaejeonCountAdapter",
    "DataAdapter",
    "DegreeOfSaturationValidation",
    "DemandSeeding",
    "FieldMapping",
    "FixedTimePlan",
    "ImportIssue",
    "ImportResult",
    "InfrequentPhaseTable",
    "IssueSeverity",
    "LinSigSettings",
    "MeanMaximumQueueReport",
    "PhaseDerivation",
    "PhaseObservation",
    "PhaseSequenceCase",
    "QualityThresholds",
    "SCATSWAAdapter",
    "SignalGroupSpec",
    "SniffResult",
    "ValidationDataBasis",
    "apply_quality_gate",
    "build_demand_seeding",
    "build_infrequent_phase_table",
    "check_adapter_conformance",
    "compute_bonus_green",
    "convert_mean_maximum_queues",
    "convert_to_fixed_time",
    "get_adapter",
    "list_adapters",
    "register_adapter",
    "run_quality_gate",
    "seedable_counts",
    "select_adapter",
    "unregister_adapter",
    "validate_degree_of_saturation",
]
