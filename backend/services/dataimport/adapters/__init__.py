"""Regional data adapters.

One module per region/source system. Adapters are registered explicitly by
``services.dataimport.__init__`` — never by import-time side effects inside
these modules — so "which adapters are live" stays greppable and a
work-in-progress adapter cannot silently start winning format sniffs.
"""
from __future__ import annotations

from .daejeon import DAEJEON_FIELD_MAP, DaejeonCountAdapter
from .scats_wa import SCATS_FIELD_MAP, SCATSWAAdapter

__all__ = [
    "DAEJEON_FIELD_MAP",
    "DaejeonCountAdapter",
    "SCATSWAAdapter",
    "SCATS_FIELD_MAP",
]
