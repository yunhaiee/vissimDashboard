"""The §5.2.3 vehicle-type layer — the fleet a demand draft references.

Roadmap 3.2's demand genesis (``services.genesis.demand``) named this module as
its one missing external dependency. A demand-carrying draft's
``<vehicleCompositionRelativeFlow>`` rows reference a ``vehType`` and a
``desSpeedDistr`` that the demand emission deliberately does **not** declare
(demand.py's "The one reference this emission does not declare"): a Vissim
``<vehicleType>`` reaches a dozen further containers, and §5.2.3 requires MRWA's
own Austroads types, so a demand draft is valid only against a network that
already declares those types. This module builds that network's type layer, so
geometry + demand + types compose into ONE self-contained draft.

What this module is
-------------------
It produces two things from an observed vehicle-class set:

* the ``{observed class code → VehicleTypeRef}`` map ``generate_demand``
  consumes (``DraftFleet.map``) — so the caller no longer has to hand-write the
  vehicle-type numbers 1.2 refused to derive, and
* the ``<vehicleTypes>`` container and its transitive closure
  (``fleet_containers``), so the compositions that reference those numbers now
  resolve inside the same file.

Everything is offline, pure-python and Vissim-free, like the rest of
``services.genesis``. It is additive by construction: a type-less draft never
calls it and the emitter writes not one byte differently for one — pinned by
``test_genesis_fleet.py::test_type_less_draft_bytes_are_unchanged``.

The MRWA §5.2.3 fleet — VERBATIM, cited to the extract
------------------------------------------------------
Table 5-1's recommended minimum vehicle types, Table 5-2's heavy-vehicle desired
acceleration, Table 5-3's power/weight distributions and Table 5-4's colours are
transcribed here as CIT-cited constants, the same discipline ``draft.py`` and
``signals.py`` follow: every prose citation quotes the extract verbatim so
``test_genesis_fleet.py`` can compare each fragment against the document, and the
numeric tables are pinned by hand-derived value tests. Where §5.2.3 defers to
Vissim's defaults ("the default desired and maximum deceleration attributes can
be adopted", §5.2.3.2), the value is copied from a Vissim-saved reference and the
source is named per value — the emitter-v2 law: copy the reference's shape, never
invent.

The canonical → MRWA-type map — and the class that does not map
---------------------------------------------------------------
Unit 1.2's ``CanonicalVehicleClass`` is the coarse cross-scheme bucket that is
*safely* mappable (its own docstring: "Mapping Austroads class 7 onto a Korean
12-class code is a research question, not an import step; mapping either onto
HEAVY_VEHICLE is not"). This module maps that coarse bucket onto ONE MRWA
§5.2.3 type:

===================  =========================================================
canonical            MRWA §5.2.3 type
===================  =========================================================
LIGHT_VEHICLE        1 Short
HEAVY_VEHICLE        a representative Austroads heavy type — a SETTING, default
                     3 Two axle truck (the coarse bucket cannot distinguish
                     대형 from 중형 or rigid from articulated; that coarseness
                     is NAMED, never resolved by guessing)
BUS                  30 Bus (Table 5-4 row 6)
MOTORCYCLE           — none: Table 5-1 has no motorcycle type
BICYCLE              — none: not a road vehicle-input type
===================  =========================================================

A canonical class with no MRWA type, and an observed class whose ``canonical``
is ``None`` (the Daejeon ``화물_소형`` 1.2 left unmapped precisely because a
Korean 소형화물차 behaves like a light vehicle and calling it either LIGHT or
HEAVY would decide a question the source did not answer), are **named in a
coverage finding, never force-fitted** — the same call 1.2 made, applied one
layer on. When the caller demanded full coverage the module refuses instead.

What is emitted, and what is deliberately shrunk (and named)
------------------------------------------------------------
Emitted, resolving entirely in-file: ``vehicleTypes``, ``speedDistributions``
(the desired-speed profile §5.2.5.3's default-≤60 rule adopts), ``colorDistributions``
(Table 5-4), and the four accel/decel function families a ``<vehicleType>``
names. Each vehicleType also names a 2D/3D model distribution; that reference is
resolved by a **minimal** ``model2D3DDistributions`` → ``models2D3D`` closure
carrying ONE representative model per category, copied verbatim from the
reference. That model's ``file3D`` names a ``.u3dm``/``.fbx`` asset on the Vissim
install — the ONE reference no ``.inpx`` can embed and this offline unit cannot
verify resolves. That boundary is the live open test, named in an always-on
disclosure, a §5.2.3.1 ``not_checkable(figure_lost)`` item (Figure 5-4, the
specific 2D/3D models, is a lost figure), and the provenance comment — exactly as
demand.py named its own undeclared reference.

Also emitted, and NOT optional — the four references a type-with-a-model needs
to instantiate. A ``<vehicleType>`` also names ``occupDistr``, ``powerDistr``,
``weightDistr`` and ``desCurveSpeedFunc``. These were once shrunk here on the
theory that a reference vehicleType lacking them exists — and one does, but only
for a type with NO 2D/3D model attached. LIVE-MEASURED (2026-08-08,
``backend/tools/genesis_fullstack_live_check.py``, PTV Vissim 2026 SP09): once a
``model2D3D`` is attached, a ``<vehicleType>`` that omits ANY of the four makes
Vissim refuse the WHOLE network load, silently, with no ``.err`` — the exact
silent-failure class this project exists to catch. The closure probe proved all
four are required together (occupancy+curve alone, or power+weight alone, still
refuses; all four loads). So each type references a single entry (``no="1"``) of
a minimal ``occupancyDistributions`` / ``powerDistributions`` /
``weightDistributions`` / ``curvSpeedFuncs`` closure, every value copied VERBATIM
from ref_lht. Those are Vissim's own defaults for loadability, NOT MRWA guideline
values: Table 5-3's power/weight (§5.2.3.3) is still deferred to the calibration
stage and graded not_checkable, and curve-speed stays a §5.3.5 reduced-speed-area
concern — the emitted default distribution only lets the typed model instantiate;
it does not claim to apply the guideline.

Shrunk and named, because nothing this file emits references it: ``vehicleClasses``
(§5.2.4 — nothing this file writes references a vehicle class, so declaring the
container would copy values no reader checks).

What the repo's own readers recover — the named gap
---------------------------------------------------
Measured, on this module's own output. ``services.inpx_parser`` — the
map-geometry reader — recovers only ``vehicleComposition`` no → name and the
``vehComp`` reference; it reads NONE of ``vehicleType``, the distributions or the
functions. ``services.genesis.diff`` compares topology only, so a fleet is
invisible to it. The richer ``services.agent.tools_network`` shadow model
recovers vehicleType no/name/category and its reference attributes, the
composition rows' ``vehType``/``desSpeedDistr``, the speed-distribution points
and the function no+name (never the function's curve points). So the fleet is
proved by a DIRECT XML round trip in the test (emitted → parsed → equal to the
cited value), exactly as demand.py proves its volume, and teaching a reader to
surface the rest is a named follow-up, not something this module reaches into.

Determinism
-----------
Emitted types are the referenced ones in ascending ``no``; every container's
children are written in id order through the same sorted-attribute ``_element``
the emitter uses; every number is a string copied from the extract or the
reference. Two runs from the same class set produce byte-identical files.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Iterable, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field

from ..dataimport.base import ImportIssue, ImportResult, IssueSeverity
from ..dataimport.schema import CanonicalVehicleClass, VehicleClassRef
from .demand import VehicleTypeRef
from .draft import (
    REASON_FIGURE_LOST,
    REASON_OUTSIDE_EMITTED_SUBSET,
    ComplianceItem,
    ComplianceState,
)
from .osm import EXTRACT

# ---------------------------------------------------------------------------
# Citations — §5.2.3 and its subsections, plus §5.2.4 / §5.2.5.3
#
# Same contract as ``draft.py``/``signals.py``/``demand.py``: every constant
# names the extract file and quotes it VERBATIM, so the citation test can
# mechanically compare each fragment against the document. Locators name a § and
# a doc page and never an in-text figure label — §5 mislabels nine of its own
# Tables as Figures.
# ---------------------------------------------------------------------------

CIT_S523_VEHICLE_TYPES = (
    f"MRWA OMG v2.0 §5.2.3 Vehicle Types, doc p.182 ({EXTRACT}) — "
    "'Traffic surveys should contain information on the vehicle compositions to be modelled. "
    "Vehicles in Western Australia can be classified using the Austroads Vehicle Classification "
    "System.'"
)

CIT_S523_MINIMUM_TYPES = (
    f"MRWA OMG v2.0 §5.2.3 Vehicle Types, doc p.182 ({EXTRACT}) — "
    "'The model should include vehicle types which are critical to the performance of the "
    "network or are a primary component of the project's objectives.'"
)

CIT_S5231_2D3D = (
    f"MRWA OMG v2.0 §5.2.3.1 2D/3D Model, doc p.183 ({EXTRACT}) — "
    "'For all vehicle types, 2D and 3D models should be defined based on the vehicle model "
    "profiles and connected with the corresponding 2D and 3D model distributions and vehicle "
    "types.'"
)

CIT_S5232_ACCEL_DEFAULTS = (
    f"MRWA OMG v2.0 §5.2.3.2 Acceleration and Deceleration Attributes, doc p.183 ({EXTRACT}) — "
    "'In the base model build, the default desired and maximum deceleration attributes can be "
    "adopted as a starting point for the vehicle types.'"
)

CIT_S5232_HEAVY_ACCEL = (
    f"MRWA OMG v2.0 §5.2.3.2 Acceleration and Deceleration Attributes, doc p.184 ({EXTRACT}) — "
    "'The desired acceleration values were estimated by the Main Roads' Operational Modelling and "
    "Visualisation team'"
)

CIT_S5233_POWER_WEIGHT = (
    f"MRWA OMG v2.0 §5.2.3.3 Power and Weight Distributions, doc p.185 ({EXTRACT}) — "
    "'appropriate power and weight distributions should be identified and defined for the heavy "
    "vehicle types.'"
)

CIT_S5234_COLOUR = (
    f"MRWA OMG v2.0 §5.2.3.4 Colour Distributions, doc p.185 ({EXTRACT}) — "
    "'In the base model, colour distributions should be defined and adopted for the vehicle "
    "types.'"
)

CIT_S5234_BUS_GREEN = (
    f"MRWA OMG v2.0 §5.2.3.4 Colour Distributions, doc p.185 ({EXTRACT}) — "
    "'The recommended colour for buses is green.'"
)

CIT_S524_VEHICLE_CLASSES = (
    f"MRWA OMG v2.0 §5.2.4 Vehicle Classes, doc p.186 ({EXTRACT}) — "
    "'The defined vehicle types should be grouped into vehicle classes based on vehicle "
    "characteristics such as vehicle length and turning speed.'"
)

CIT_S5253_SPEED_PROFILE = (
    f"MRWA OMG v2.0 §5.2.5.3 Desired Speed Profile, doc p.190 ({EXTRACT}) — "
    "'Default speed profiles (linear) should be adopted for all speed limits up to and including "
    "60 km/h.'"
)

CIT_S5253_CONSULT = (
    f"MRWA OMG v2.0 §5.2.5.3 Desired Speed Profile, doc p.190 ({EXTRACT}) — "
    "'Modellers should consult Main Roads for the speed distributions for roads with speed "
    "limits of 70km/h or greater prior to commencing modelling.'"
)


# ---------------------------------------------------------------------------
# Table 5-1 — the recommended minimum vehicle types (doc p.182), VERBATIM.
#
# Each type carries: MRWA's own number and name; the Vissim ``category`` (the
# 2D/3D model class of §5.2.3.1 — CAR/HGV/BUS); the coarse canonical bucket it
# is the representative of, if any; the Table 5-4 colour number; and whether its
# desired acceleration comes from Table 5-2 (heavy types only — §5.2.3.2 scopes
# that table to "the desired heavy vehicle acceleration"; light types keep the
# Vissim default).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MrwaVehicleType:
    """One Table 5-1 vehicle type. ``no``/``name`` are the guideline's own."""

    no: int
    name: str
    category: str  # CAR / HGV / BUS — §5.2.3.1's 2D/3D model class
    colour_no: int  # Table 5-4 colour distribution
    heavy_desired_accel: bool  # Table 5-2 (heavy) vs Vissim default (light/bus)
    class_no: int  # Table 5-5 vehicle class (§5.2.4), advisory
    class_name: str


CAT_CAR = "CAR"
CAT_HGV = "HGV"
CAT_BUS = "BUS"

#: Table 5-1 (doc p.182) + the Bus type of Table 5-4 row 6 (doc p.185). The
#: "1 Short" row's Axles value is missing from the extract dump and is NOT
#: reproduced (extract's ``[EXTRACTION UNCERTAIN p.182]``); only fields that
#: survived extraction are carried. Class grouping (``class_no``) follows Table
#: 5-5 (doc p.186), whose consistency note this module keeps: type 2 is class
#: "1 Short" though Table 5-1 gives it a Medium length group.
MRWA_FLEET: tuple[MrwaVehicleType, ...] = (
    MrwaVehicleType(1, "Short", CAT_CAR, 1, False, 1, "Short"),
    MrwaVehicleType(2, "Short-towing", CAT_CAR, 1, False, 1, "Short"),
    MrwaVehicleType(3, "Two axle truck", CAT_HGV, 2, True, 2, "Medium"),
    MrwaVehicleType(4, "Three axle truck", CAT_HGV, 2, True, 2, "Medium"),
    MrwaVehicleType(5, "Four axle truck", CAT_HGV, 2, True, 2, "Medium"),
    MrwaVehicleType(6, "Three axle articulated", CAT_HGV, 3, True, 3, "Long"),
    MrwaVehicleType(7, "Four axle articulated", CAT_HGV, 3, True, 3, "Long"),
    MrwaVehicleType(8, "Five axle articulated", CAT_HGV, 3, True, 3, "Long"),
    MrwaVehicleType(9, "Six axle articulated", CAT_HGV, 3, True, 3, "Long"),
    MrwaVehicleType(10, "B-double", CAT_HGV, 4, True, 4, "Medium combination"),
    MrwaVehicleType(11, "Double road train", CAT_HGV, 4, True, 4, "Medium combination"),
    MrwaVehicleType(12, "Triple road train", CAT_HGV, 5, True, 5, "Large combination"),
    MrwaVehicleType(30, "Bus", CAT_BUS, 6, False, 6, "Bus"),
)

MRWA_TYPE_BY_NO: dict[int, MrwaVehicleType] = {t.no: t for t in MRWA_FLEET}

#: Table 5-2 (doc p.184) — desired acceleration for heavy vehicle types, VERBATIM.
#: Speed axis in km/h; each row is (yMin, y, yMax) in m/s², where ``y`` is the
#: desired (mean) value and yMin/yMax the envelope. Types 1, 2 are NOT in this
#: table — §5.2.3.2 scopes it to heavy vehicles; light types keep the default.
TABLE_5_2_SPEED_KMH: tuple[int, ...] = (0, 32, 40, 48, 56, 64, 72, 80, 88, 96, 120)

#: type_no -> {"yMin": (...), "y": (...), "yMax": (...)}, each an 11-tuple aligned
#: to TABLE_5_2_SPEED_KMH. Transcribed from the extract's Table 5-2 (doc p.184).
TABLE_5_2_HEAVY_DES_ACCEL: dict[int, dict[str, tuple[float, ...]]] = {
    3: {
        "yMin": (0.664, 0.664, 0.597, 0.530, 0.419, 0.309, 0.278, 0.247, 0.172, 0.098, 0.000),
        "y": (1.053, 1.053, 1.041, 1.029, 0.809, 0.588, 0.556, 0.524, 0.283, 0.043, 0.000),
        "yMax": (1.930, 1.930, 1.930, 1.930, 1.587, 1.243, 1.112, 0.980, 0.506, 0.031, 0.000),
    },
    4: {
        "yMin": (0.552, 0.552, 0.470, 0.387, 0.308, 0.230, 0.199, 0.167, 0.140, 0.113, 0.000),
        "y": (0.761, 0.761, 0.708, 0.655, 0.517, 0.379, 0.347, 0.316, 0.200, 0.084, 0.000),
        "yMax": (1.004, 1.004, 0.986, 0.967, 0.760, 0.553, 0.521, 0.489, 0.269, 0.050, 0.000),
    },
    5: {
        "yMin": (0.482, 0.482, 0.390, 0.298, 0.240, 0.181, 0.148, 0.115, 0.114, 0.112, 0.000),
        "y": (0.618, 0.618, 0.544, 0.471, 0.374, 0.277, 0.245, 0.214, 0.159, 0.104, 0.000),
        "yMax": (0.761, 0.761, 0.708, 0.655, 0.517, 0.379, 0.347, 0.316, 0.200, 0.084, 0.000),
    },
    6: {
        "yMin": (0.599, 0.599, 0.523, 0.446, 0.355, 0.263, 0.232, 0.200, 0.154, 0.107, 0.000),
        "y": (0.761, 0.761, 0.708, 0.655, 0.517, 0.379, 0.347, 0.316, 0.200, 0.084, 0.000),
        "yMax": (1.819, 1.819, 1.819, 1.819, 1.489, 1.160, 1.042, 0.925, 0.478, 0.031, 0.000),
    },
    7: {
        "yMin": (0.499, 0.499, 0.409, 0.318, 0.255, 0.191, 0.16, 0.129, 0.125, 0.121, 0.000),
        "y": (0.761, 0.761, 0.708, 0.655, 0.517, 0.379, 0.347, 0.316, 0.200, 0.084, 0.000),
        "yMax": (1.819, 1.819, 1.819, 1.819, 1.489, 1.160, 1.042, 0.925, 0.478, 0.031, 0.000),
    },
    8: {
        "yMin": (0.491, 0.491, 0.399, 0.308, 0.247, 0.186, 0.154, 0.123, 0.123, 0.122, 0.000),
        "y": (0.664, 0.664, 0.597, 0.530, 0.419, 0.309, 0.278, 0.247, 0.172, 0.098, 0.000),
        "yMax": (0.831, 0.831, 0.787, 0.744, 0.586, 0.429, 0.397, 0.365, 0.220, 0.074, 0.000),
    },
    9: {
        "yMin": (0.501, 0.501, 0.411, 0.321, 0.257, 0.193, 0.162, 0.130, 0.126, 0.121, 0.000),
        "y": (0.649, 0.649, 0.580, 0.510, 0.405, 0.299, 0.267, 0.236, 0.168, 0.100, 0.000),
        "yMax": (0.883, 0.883, 0.847, 0.811, 0.638, 0.466, 0.434, 0.403, 0.235, 0.067, 0.000),
    },
    10: {
        "yMin": (0.409, 0.409, 0.317, 0.225, 0.191, 0.157, 0.100, 0.043, 0.017, 0.000, 0.000),
        "y": (0.518, 0.518, 0.430, 0.342, 0.274, 0.205, 0.174, 0.143, 0.130, 0.118, 0.000),
        "yMax": (0.717, 0.717, 0.657, 0.598, 0.473, 0.347, 0.316, 0.284, 0.187, 0.090, 0.000),
    },
    11: {
        "yMin": (0.368, 0.368, 0.276, 0.185, 0.126, 0.068, 0.007, 0.000, 0.000, 0.000, 0.000),
        "y": (0.409, 0.409, 0.317, 0.225, 0.191, 0.157, 0.100, 0.043, 0.017, 0.000, 0.000),
        "yMax": (0.622, 0.622, 0.549, 0.476, 0.378, 0.279, 0.248, 0.217, 0.160, 0.104, 0.000),
    },
    12: {
        "yMin": (0.346, 0.346, 0.254, 0.162, 0.082, 0.002, 0.000, 0.000, 0.000, 0.000, 0.000),
        "y": (0.464, 0.464, 0.372, 0.280, 0.228, 0.175, 0.136, 0.097, 0.090, 0.082, 0.000),
        "yMax": (0.582, 0.582, 0.503, 0.425, 0.338, 0.251, 0.219, 0.188, 0.149, 0.109, 0.000),
    },
}

#: Table 5-3 (doc p.185) — recommended power/weight distributions for heavy
#: vehicles, VERBATIM. (name -> (power_kW, weight_min_t, weight_med_t,
#: weight_max_t).) Kept as a CIT-cited constant but NOT emitted: the power column
#: is a single kW value, not a Vissim distribution range, and §5.2.3.3 itself
#: says these are "referenced at the calibration stage" — so the §5.2.3.3 rule is
#: graded not_checkable rather than a range being invented.
TABLE_5_3_POWER_WEIGHT: dict[str, tuple[float, float, float, float]] = {
    "240 hp": (180.0, 6.0, 9.7, 25.7),
    "300 hp": (225.0, 7.5, 12.2, 32.1),
    "400 hp": (300.0, 10.0, 16.2, 42.9),
    "500 hp": (375.0, 12.5, 20.3, 53.6),
    "600 hp": (450.0, 15.0, 24.3, 64.3),
}

#: Table 5-4 (doc p.185) — recommended colour distribution, VERBATIM. colour_no
#: -> (name, (r, g, b)). One colour per class at 100% share (Table 5-4's "Share"
#: column is 100% on every row). Vissim stores colour as ``ffRRGGBB`` (opaque).
TABLE_5_4_COLOURS: dict[int, tuple[str, tuple[int, int, int]]] = {
    1: ("Short", (0, 255, 255)),  # Aqua
    2: ("Medium", (182, 255, 0)),  # Lime Green
    3: ("Long", (255, 127, 237)),  # Pink
    4: ("Medium combination", (255, 106, 0)),  # Orange
    5: ("Large combination", (255, 0, 0)),  # Red
    6: ("Bus", (38, 127, 0)),  # Green — §5.2.3.4 recommends green for buses (CIT_S5234_BUS_GREEN)
}


def _colour_hex(rgb: tuple[int, int, int]) -> str:
    """Table 5-4's RGB -> Vissim's ``ffRRGGBB`` opaque colour string."""
    r, g, b = rgb
    return f"ff{r:02x}{g:02x}{b:02x}"


def table_5_2_envelope_anomalies(type_no: int) -> list[tuple[int, float, float, float]]:
    """Points in a Table 5-2 heavy row where the envelope is INVERTED.

    A Vissim acceleration function's stochastic envelope expects
    ``yMin ≤ y ≤ yMax``. Table 5-2 as transcribed from the extract does NOT hold
    that at 96 km/h for every heavy type (type 3, e.g., reads yMin=0.098 > y=0.043
    > yMax=0.031 there). The three values are MRWA's own and are PRESERVED — the
    source table :data:`TABLE_5_2_HEAVY_DES_ACCEL` keeps them inverted, verbatim,
    and this function still reports the inversion so :func:`build_fleet` raises it
    as a guideline finding (WARN). What the emitter WRITES is a min/max LABEL
    reorder of those same three values (see :func:`_mrwa_heavy_points`): Vissim
    refuses the whole network load on any point where yMin > yMax (LIVE-MEASURED
    2026-08-08), so the emitted point is relabelled to satisfy that contract — no
    value is invented, y is unchanged and still lands inside. Returns
    ``(speed_kmh, yMin, y, yMax)`` for each offending point in the SOURCE.
    """
    row = TABLE_5_2_HEAVY_DES_ACCEL[type_no]
    out: list[tuple[int, float, float, float]] = []
    for i, speed in enumerate(TABLE_5_2_SPEED_KMH):
        y_min, y, y_max = row["yMin"][i], row["y"][i], row["yMax"][i]
        if not (y_min <= y <= y_max):
            out.append((speed, y_min, y, y_max))
    return out


# ---------------------------------------------------------------------------
# Vissim defaults — copied VERBATIM from ``ref_lht`` (Vissim's own save of the
# left-hand London example), the same authority ``schema.py`` uses. Each is
# named. These are the values §5.2.3.2 tells the modeller to "adopt as a starting
# point", so copying rather than inventing them is following the guideline.
#
# The four function families and the desired-speed distribution below carry only
# the functions/distributions a CAR/HGV/BUS type references; declaring the rest
# of the reference's catalogue would copy values no reader could check.
# ---------------------------------------------------------------------------

#: Source: ref_lht ``desAccelerationFunction no="1"`` (Car) — the light-vehicle
#: desired acceleration §5.2.3.2 leaves at the Vissim default. (x, y, yMin, yMax).
DEFAULT_DES_ACCEL_CAR: tuple[tuple[str, str, str, str], ...] = (
    ("0", "3", "1.96", "3.5"), ("10", "2.7", "1.493", "3.5"), ("20", "2.286", "1.3", "3.5"),
    ("30", "1.968", "1.152", "3.5"), ("40", "1.7", "1.027", "3.5"), ("50", "1.464", "0.917", "3.273"),
    ("60", "1.314", "0.817", "2.918"), ("70", "1.245", "0.725", "2.59"), ("80", "1.176", "0.64", "2.286"),
    ("90", "1.107", "0.56", "2"), ("100", "1.038", "0.484", "1.73"), ("110", "0.969", "0.452", "1.614"),
    ("120", "0.899", "0.42", "1.499"), ("130", "0.83", "0.387", "1.384"), ("140", "0.761", "0.355", "1.268"),
    ("150", "0.692", "0.323", "1.153"), ("160", "0.623", "0.291", "1.038"), ("170", "0.553", "0.258", "0.922"),
    ("180", "0.484", "0.226", "0.807"), ("190", "0.415", "0.194", "0.692"), ("200", "0.346", "0.161", "0.577"),
    ("210", "0.277", "0.129", "0.461"), ("220", "0.208", "0.097", "0.346"), ("230", "0.138", "0.065", "0.231"),
    ("240", "0.069", "0.032", "0.115"), ("250", "0", "0", "0"),
)

#: Source: ref_lht ``desAccelerationFunction no="3"`` (Bus).
DEFAULT_DES_ACCEL_BUS: tuple[tuple[str, str, str, str], ...] = (
    ("0", "1.24", "1.042", "1.488"), ("10", "1.24", "1.042", "1.488"), ("20", "1.24", "1.042", "1.488"),
    ("30", "1.24", "1.042", "1.488"), ("40", "1.1", "0.924", "1.32"), ("50", "1", "0.84", "1.2"),
    ("60", "0.9", "0.756", "1.08"), ("70", "0.8", "0.672", "0.96"), ("80", "0.6", "0.504", "0.72"),
    ("90", "0.4", "0.336", "0.48"), ("100", "0", "0", "0"), ("110", "0", "0", "0"), ("120", "0", "0", "0"),
    ("130", "0", "0", "0"), ("140", "0", "0", "0"), ("150", "0", "0", "0"), ("160", "0", "0", "0"),
    ("170", "0", "0", "0"), ("180", "0", "0", "0"), ("190", "0", "0", "0"), ("200", "0", "0", "0"),
    ("210", "0", "0", "0"), ("220", "0", "0", "0"), ("230", "0", "0", "0"), ("240", "0", "0", "0"),
    ("250", "0", "0", "0"),
)

#: Source: ref_lht ``maxAccelerationFunction no="1"/"2"/"3"`` (Car/HGV/Bus).
DEFAULT_MAX_ACCEL_CAR: tuple[tuple[str, str, str, str], ...] = (
    ("0", "3.5", "1.96", "3.5"), ("10", "3.2", "1.493", "3.5"), ("20", "2.786", "1.3", "3.5"),
    ("30", "2.468", "1.152", "3.5"), ("40", "2.2", "1.027", "3.5"), ("50", "1.964", "0.917", "3.273"),
    ("60", "1.751", "0.817", "2.918"), ("70", "1.554", "0.725", "2.59"), ("80", "1.372", "0.64", "2.286"),
    ("90", "1.2", "0.56", "2"), ("100", "1.038", "0.484", "1.73"), ("110", "0.969", "0.452", "1.614"),
    ("120", "0.899", "0.42", "1.499"), ("130", "0.83", "0.387", "1.384"), ("140", "0.761", "0.355", "1.268"),
    ("150", "0.692", "0.323", "1.153"), ("160", "0.623", "0.291", "1.038"), ("170", "0.553", "0.258", "0.922"),
    ("180", "0.484", "0.226", "0.807"), ("190", "0.415", "0.194", "0.692"), ("200", "0.346", "0.161", "0.577"),
    ("210", "0.277", "0.129", "0.461"), ("220", "0.208", "0.097", "0.346"), ("230", "0.138", "0.065", "0.231"),
    ("240", "0.069", "0.032", "0.115"), ("250", "0", "0", "0"),
)
DEFAULT_MAX_ACCEL_HGV: tuple[tuple[str, str, str, str], ...] = (
    ("0", "7.3", "2.5", "9.3"), ("10", "6.9", "2.4", "9.3"), ("20", "3.1", "1.12", "5.2"),
    ("30", "2", "0.73", "3.2"), ("40", "1.52", "0.53", "2.35"), ("50", "0.95", "0.32", "1.55"),
    ("60", "0.79", "0.25", "1.31"), ("70", "0.64", "0.19", "1.1"), ("80", "0.52", "0.15", "0.9"),
    ("90", "0.41", "0.11", "0.75"), ("100", "0.35", "0.08", "0.65"), ("110", "0.2", "0", "0.55"),
    ("120", "0", "0", "0.1"), ("130", "0", "0", "0"), ("140", "0", "0", "0"), ("150", "0", "0", "0"),
    ("160", "0", "0", "0"), ("170", "0", "0", "0"), ("180", "0", "0", "0"), ("190", "0", "0", "0"),
    ("200", "0", "0", "0"), ("210", "0", "0", "0"), ("220", "0", "0", "0"), ("230", "0", "0", "0"),
    ("240", "0", "0", "0"), ("250", "0", "0", "0"),
)
DEFAULT_MAX_ACCEL_BUS: tuple[tuple[str, str, str, str], ...] = (
    ("0", "1.24", "1.042", "1.488"), ("10", "1.24", "1.042", "1.488"), ("20", "1.24", "1.042", "1.488"),
    ("30", "1.24", "1.042", "1.488"), ("40", "1.1", "0.924", "1.32"), ("50", "1", "0.84", "1.2"),
    ("60", "0.9", "0.756", "1.08"), ("70", "0.8", "0.672", "0.96"), ("80", "0.6", "0.504", "0.72"),
    ("90", "0.4", "0.336", "0.48"), ("100", "0", "0", "0"), ("110", "0", "0", "0"), ("120", "0", "0", "0"),
    ("130", "0", "0", "0"), ("140", "0", "0", "0"), ("150", "0", "0", "0"), ("160", "0", "0", "0"),
    ("170", "0", "0", "0"), ("180", "0", "0", "0"), ("190", "0", "0", "0"), ("200", "0", "0", "0"),
    ("210", "0", "0", "0"), ("220", "0", "0", "0"), ("230", "0", "0", "0"), ("240", "0", "0", "0"),
    ("250", "0", "0", "0"),
)

#: Source: ref_lht ``desDecelerationFunction no="1"/"2"/"3"`` — the "default
#: desired ... deceleration" §5.2.3.2 adopts. Flat curves in the reference.
DEFAULT_DES_DECEL_CAR: tuple[tuple[str, str, str, str], ...] = tuple(
    (str(x), "-2.75", "-3", "-2.55") for x in range(0, 250, 10)
)
DEFAULT_DES_DECEL_HGV: tuple[tuple[str, str, str, str], ...] = tuple(
    (str(x), "-1.25", "-1.5", "-1.05") for x in range(0, 250, 10)
)
DEFAULT_DES_DECEL_BUS: tuple[tuple[str, str, str, str], ...] = tuple(
    (str(x), "-0.85", "-1", "-0.73") for x in range(0, 250, 10)
)

#: Source: ref_lht ``maxDecelerationFunction no="1"/"2"/"3"`` — the "default ...
#: maximum deceleration" §5.2.3.2 adopts.
DEFAULT_MAX_DECEL_CAR: tuple[tuple[str, str, str, str], ...] = (
    ("0", "-7.5", "-8.5", "-6.5"), ("10", "-7.4", "-8.4", "-6.4"), ("20", "-7.3", "-8.3", "-6.3"),
    ("30", "-7.2", "-8.2", "-6.2"), ("40", "-7.1", "-8.1", "-6.1"), ("50", "-7", "-8", "-6"),
    ("60", "-6.9", "-7.9", "-5.9"), ("70", "-6.8", "-7.8", "-5.8"), ("80", "-6.7", "-7.7", "-5.7"),
    ("90", "-6.6", "-7.6", "-5.6"), ("100", "-6.5", "-7.5", "-5.5"), ("110", "-6.4", "-7.4", "-5.4"),
    ("120", "-6.3", "-7.3", "-5.3"), ("130", "-6.2", "-7.2", "-5.2"), ("140", "-6.1", "-7.1", "-5.1"),
    ("150", "-6", "-7", "-5"), ("160", "-5.9", "-6.9", "-4.9"), ("170", "-5.8", "-6.8", "-4.8"),
    ("180", "-5.7", "-6.7", "-4.7"), ("190", "-5.6", "-6.6", "-4.6"), ("200", "-5.5", "-6.5", "-4.5"),
    ("210", "-5.4", "-6.4", "-4.4"), ("220", "-5.3", "-6.3", "-4.3"), ("230", "-5.2", "-6.2", "-4.2"),
    ("240", "-5.1", "-6.1", "-4.1"),
)
DEFAULT_MAX_DECEL_HGV: tuple[tuple[str, str, str, str], ...] = tuple(
    (str(x), "-5.5", "-6", "-5") for x in range(0, 250, 10)
)
DEFAULT_MAX_DECEL_BUS: tuple[tuple[str, str, str, str], ...] = DEFAULT_MAX_DECEL_CAR

#: Source: ref_lht ``occupancyDistribution``/``vehicleType`` boilerplate. The
#: non-computed vehicleType attributes present on every reference type, split by
#: category because the PT dwell attrs differ (Bus carries capacity/board/alight
#: from ref_lht's own Bus type). ``occupDistr``/``powerDistr``/``weightDistr``/
#: ``desCurveSpeedFunc`` each name a single default distribution/function
#: (``no="1"``) that :func:`fleet_containers` emits — LIVE-MEASURED required once
#: a model2D3D is attached (2026-08-08 genesis_fullstack_live_check): a type
#: omitting any of the four refuses the whole network load. See the module
#: docstring.
VT_BOILERPLATE_ROAD: dict[str, str] = {
    "alightTm": "0", "anmFlag": "false", "boardTm": "0", "capExact": "false", "capacity": "0",
    "clearTmPT": "0", "desCurveSpeedFunc": "1", "distCoeffPerMeter": "0", "doorClosDur": "2",
    "dwellTmIsAdd": "true", "equip": "", "extDriver": "false", "extDriverDLLFile": "",
    "extDriverParFile": "", "extEmissionDLLFile": "", "linkCostCoeff": "1", "occupDistr": "1",
    "powerDistr": "1", "travTmCoeff": "1", "weightDistr": "1",
}
VT_BOILERPLATE_BUS: dict[str, str] = {
    **VT_BOILERPLATE_ROAD,
    "alightTm": "1", "boardTm": "2", "capacity": "110", "clearTmPT": "8",
}

#: Source: ref_lht ``vehicleType/parkLotSelPars`` — identical on all reference
#: types. A nested (tag, attrs, children) literal, built through the same
#: ElementTree path as everything else.
PARK_LOT_SEL_PARS: tuple = (
    "parkLotSelPars",
    {},
    tuple(
        (
            "parkingLotSelectionParameters",
            {
                "attrac": "0", "avail": "0", "decSituation": sit,
                "distFromDestM": "0", "genCost": "0", "parkFee": "0",
            },
            (),
        )
        for sit in (
            "DEPARTUREFROMPARKINGLOT",
            "DYNAMICROUTINGDECISION",
            "ROUTEGUIDANCETACTIC1",
            "ROUTEGUIDANCETACTIC2",
        )
    ),
)

#: Source: ref_lht ``models2D3D`` — ONE representative model per category,
#: VERBATIM (including the ``file3D`` .u3dm/.fbx path, the ONE external boundary).
#: Nested (tag, attrs, children) literals.
MODEL_2D3D_BY_CATEGORY: dict[str, tuple] = {
    CAT_CAR: (
        "model2D3D",
        {"name": "Car - Fiat 500", "no": "1"},
        (
            ("model2D3DSegs", {}, (
                ("model2D3DSegment", {
                    "axleFront": "0.71", "axleRear": "3.01",
                    "file3D": "#3dmodels#Vehicles\\Road\\Car - Fiat 500 (2018).u3dm",
                    "height": "1.6729680758395749", "jointFront": "0", "jointRear": "3.561",
                    "length": "3.5608121156692505", "motionAnim": "", "partWOutPassFront": "0",
                    "partWOutPassRear": "0", "scale": "1", "shaftLen": "0",
                    "width": "1.8827282245047858", "yawAngle": "0",
                }, (
                    ("posRel", {"xOffset": "0", "yOffset": "0", "zOffset": "0"}, ()),
                )),
            )),
        ),
    ),
    CAT_HGV: (
        "model2D3D",
        {"name": "HGV - Tractor Box Volvo", "no": "21"},
        (
            ("model2D3DSegs", {}, (
                ("model2D3DSegment", {
                    "axleFront": "1.52", "axleRear": "6.84",
                    "file3D": "#3dmodels#Vehicles\\Road\\HGV - Tractor Box Volvo FM 370.u3dm",
                    "height": "4.01", "jointFront": "0", "jointRear": "9.23", "length": "10.26",
                    "motionAnim": "", "partWOutPassFront": "0", "partWOutPassRear": "0",
                    "scale": "1", "shaftLen": "0", "width": "2.3", "yawAngle": "0",
                }, (
                    ("posRel", {"xOffset": "0", "yOffset": "0", "zOffset": "0"}, ()),
                )),
            )),
        ),
    ),
    CAT_BUS: (
        "model2D3D",
        {"name": "Bus - C2 Standard", "no": "31"},
        (
            ("model2D3DSegs", {}, (
                ("model2D3DSegment", {
                    "axleFront": "2.825", "axleRear": "8.77",
                    "file3D": "#3dmodels#Vehicles\\Road\\Bus - C2 Standard 2-doors.fbx",
                    "height": "3.12", "jointFront": "0.07", "jointRear": "12.135",
                    "length": "12.135", "motionAnim": "", "partWOutPassFront": "2",
                    "partWOutPassRear": "0.6", "scale": "1", "shaftLen": "0",
                    "width": "2.55", "yawAngle": "0",
                }, (
                    ("doors", {}, (
                        ("door", {"pos": "0.44340571761131287", "side": "RIGHT", "usage": "BOARDING",
                                  "width": "0.75893500447273254", "yOffset": "1.2446038722991943",
                                  "zOffset": "0.29556810855865479"}, ()),
                        ("door", {"pos": "1.2023407220840454", "side": "RIGHT", "usage": "BOTH",
                                  "width": "0.75893497467041016", "yOffset": "1.2446038722991943",
                                  "zOffset": "0.29556810855865479"}, ()),
                        ("door", {"pos": "6.3879051208496094", "side": "RIGHT", "usage": "BOTH",
                                  "width": "0.75893497467041016", "yOffset": "1.2446038722991943",
                                  "zOffset": "0.29556810855865479"}, ()),
                        ("door", {"pos": "7.1468410491943359", "side": "RIGHT", "usage": "BOTH",
                                  "width": "0.75893497467041016", "yOffset": "1.2446038722991943",
                                  "zOffset": "0.29556810855865479"}, ()),
                    )),
                    ("posRel", {"xOffset": "0", "yOffset": "0", "zOffset": "0"}, ()),
                )),
            )),
        ),
    ),
}

#: The 2D/3D model number each category's representative model carries (its own
#: ``no`` above), and the model2D3DDistribution number the vehicleType references.
#: Conventional Vissim category numbers (10 Car / 20 HGV / 30 Bus).
MODEL_NO_BY_CATEGORY: dict[str, int] = {CAT_CAR: 1, CAT_HGV: 21, CAT_BUS: 31}
MODEL_DISTR_NO_BY_CATEGORY: dict[str, int] = {CAT_CAR: 10, CAT_HGV: 20, CAT_BUS: 30}

#: The Vissim-default accel/decel function numbers per category, copied above.
DES_ACCEL_DEFAULT_NO: dict[str, int] = {CAT_CAR: 1, CAT_HGV: 2, CAT_BUS: 3}
MAX_ACCEL_NO: dict[str, int] = {CAT_CAR: 1, CAT_HGV: 2, CAT_BUS: 3}
DES_DECEL_NO: dict[str, int] = {CAT_CAR: 1, CAT_HGV: 2, CAT_BUS: 3}
MAX_DECEL_NO: dict[str, int] = {CAT_CAR: 1, CAT_HGV: 2, CAT_BUS: 3}
DES_ACCEL_POINTS: dict[int, tuple] = {1: DEFAULT_DES_ACCEL_CAR, 3: DEFAULT_DES_ACCEL_BUS}
MAX_ACCEL_POINTS: dict[int, tuple] = {1: DEFAULT_MAX_ACCEL_CAR, 2: DEFAULT_MAX_ACCEL_HGV, 3: DEFAULT_MAX_ACCEL_BUS}
DES_DECEL_POINTS: dict[int, tuple] = {1: DEFAULT_DES_DECEL_CAR, 2: DEFAULT_DES_DECEL_HGV, 3: DEFAULT_DES_DECEL_BUS}
MAX_DECEL_POINTS: dict[int, tuple] = {1: DEFAULT_MAX_DECEL_CAR, 2: DEFAULT_MAX_DECEL_HGV, 3: DEFAULT_MAX_DECEL_BUS}
DEFAULT_ACCEL_NAME: dict[str, str] = {CAT_CAR: "Car", CAT_HGV: "HGV", CAT_BUS: "Bus"}

#: The MRWA heavy desired-acceleration function's number is offset off the type
#: number so it never collides with the default functions {1, 2, 3}.
MRWA_DES_ACCEL_FUNC_OFFSET = 500

#: Source: ref_lht ``speedDistributions`` — the two default linear profiles
#: §5.2.5.3 adopts for limits ≤60 km/h, VERBATIM (no -> (lo, hi) km/h band).
SPEED_DISTRIBUTIONS_KMH: dict[int, tuple[str, str]] = {50: ("48", "58"), 60: ("58", "68")}

#: Source: ref_lht ``occupancyDistributions``/``powerDistributions``/
#: ``weightDistributions``/``curvSpeedFuncs`` — the single ``no="1"`` entry of
#: each, copied VERBATIM (extracted by script from Roundabout London.inpx, not
#: transcribed). Every emitted vehicleType references these four by ``no="1"``
#: (see :data:`VT_BOILERPLATE_ROAD`); they are the transitive closure a
#: type-with-a-model needs to load (module docstring's live measurement). The
#: values are Vissim's own defaults, named per the emitter-v2 law — NOT MRWA
#: guideline numbers (Table 5-3's power/weight stays a calibration-stage constant).
#: Each is a nested (tag, attrs, children) literal rendered through :func:`_node`.
OCCUPANCY_DISTRIBUTION_DEFAULT: tuple = (
    "occupancyDistribution",
    {"mean": "1", "name": "Single Occupancy", "no": "1", "stdDev": "0", "type": "NORMAL"},
    (),
)
POWER_DISTRIBUTION_DEFAULT: tuple = (
    "powerDistribution",
    {"name": "", "no": "1"},
    (
        ("distrDataPts", {}, (
            ("distributionDataPoint", {"fx": "0", "x": "55"}, ()),
            ("distributionDataPoint", {"fx": "1", "x": "160"}, ()),
        )),
    ),
)
WEIGHT_DISTRIBUTION_DEFAULT: tuple = (
    "weightDistribution",
    {"name": "Car", "no": "1"},
    (
        ("distrDataPts", {}, (
            ("distributionDataPoint", {"fx": "0", "x": "800"}, ()),
            ("distributionDataPoint", {"fx": "1", "x": "2000"}, ()),
        )),
    ),
)
CURV_SPEED_FUNC_DEFAULT: tuple = (
    "curvSpeedFunc",
    {
        "name": (
            "Road. Car. Based on Schimmelpfennig & Nackenhorst (1985), Lindenmann & Ranft "
            "(1978), and German road design guidelines FGSV RASt, RAL & RAA"
        ),
        "no": "1",
    },
    (
        ("curvSpeedFuncDataPts", {}, (
            ("curvSpeedFuncDataPt", {"x": "5", "y": "8.603621", "yMax": "9.7827", "yMin": "6.643041"}, ()),
            ("curvSpeedFuncDataPt", {"x": "10", "y": "16.407384", "yMax": "18.450547", "yMin": "12.883856"}, ()),
            ("curvSpeedFuncDataPt", {"x": "30", "y": "38.099541", "yMax": "41.322955", "yMin": "31.94514"}, ()),
            ("curvSpeedFuncDataPt", {"x": "60", "y": "56.796123", "yMax": "60.002031", "yMin": "50.510204"}, ()),
            ("curvSpeedFuncDataPt", {"x": "100", "y": "70.836488", "yMax": "73.867618", "yMin": "64.8379"}, ()),
            ("curvSpeedFuncDataPt", {"x": "150", "y": "82.291103", "yMax": "85.119673", "yMin": "76.694153"}, ()),
            ("curvSpeedFuncDataPt", {"x": "200", "y": "89.967679", "yMax": "92.669511", "yMin": "84.602588"}, ()),
            ("curvSpeedFuncDataPt", {"x": "275", "y": "98.035935", "yMax": "100.623351", "yMin": "92.89602"}, ()),
            ("curvSpeedFuncDataPt", {"x": "350", "y": "104.151354", "yMax": "106.64827", "yMin": "99.196834"}, ()),
            ("curvSpeedFuncDataPt", {"x": "450", "y": "110.328812", "yMax": "112.740138", "yMin": "105.550202"}, ()),
            ("curvSpeedFuncDataPt", {"x": "550", "y": "115.08469", "yMax": "117.430115", "yMin": "110.427376"}, ()),
            ("curvSpeedFuncDataPt", {"x": "750", "y": "123.152003", "yMax": "125.361344", "yMin": "118.780741"}, ()),
            ("curvSpeedFuncDataPt", {"x": "1000", "y": "129.915386", "yMax": "132.036441", "yMin": "125.726192"}, ()),
        )),
    ),
)


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


class FleetSettings(BaseModel):
    """Every choice §5.2.3 or a measurement does not force, in one place."""

    model_config = ConfigDict(extra="forbid")

    heavy_representative_type_no: int = Field(
        default=3,
        description=(
            "Which Austroads heavy type the coarse HEAVY_VEHICLE bucket maps onto. Default 3 "
            "(Two axle truck) — the lightest/most-common urban heavy. A modelling choice with an "
            "authority (Table 5-1), not a schema constant: the bucket cannot distinguish 대형 "
            "from 중형 or rigid from articulated, and that coarseness is named, not resolved."
        ),
    )
    desired_speed_kmh: int = Field(
        default=50,
        description=(
            "The desired-speed profile every type is given. §5.2.5.3 adopts default linear "
            "profiles for limits ≤60; only the measured 50/60 profiles are carried, and ≥70 "
            "refuses (§5.2.5.3: 'consult Main Roads')."
        ),
    )
    require_full_coverage: bool = Field(
        default=False,
        description=(
            "When True, an observed class with no MRWA §5.2.3 type is a REFUSAL rather than a "
            "coverage finding — for a caller that needs every counted class to carry a type."
        ),
    )


DEFAULT_SETTINGS = FleetSettings()


# ---------------------------------------------------------------------------
# The module's vocabulary
# ---------------------------------------------------------------------------

#: Every code that produces NO fleet at all. Data, so the "refuses and produces
#: nothing" claim is testable, exactly as ``demand.REFUSE_CODES`` is.
REFUSE_CODES: frozenset[str] = frozenset(
    {
        "genesis_fleet_class_without_mrwa_type",
        "genesis_fleet_desired_speed_distribution_not_built",
        "genesis_fleet_high_speed_needs_main_roads",
        "genesis_fleet_composition_target_absent",
    }
)

#: The canonical buckets §5.2.3 covers, and the fixed MRWA type each maps to
#: (HEAVY is resolved from the setting). Buckets absent here have no §5.2.3 type.
_FIXED_CANONICAL_TYPE: dict[CanonicalVehicleClass, int] = {
    CanonicalVehicleClass.LIGHT_VEHICLE: 1,
    CanonicalVehicleClass.BUS: 30,
}


def _issue(
    severity: IssueSeverity,
    code: str,
    message: str,
    *,
    messages: dict[str, str] | None = None,
    subject: str | None = None,
    citation: str | None = None,
    context: dict | None = None,
) -> ImportIssue:
    return ImportIssue(
        severity=severity,
        code=code,
        message=message,
        messages=messages or {},
        subject=subject,
        citation=citation,
        context=context or {},
    )


# ---------------------------------------------------------------------------
# What one draft's fleet IS
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EmittedVehicleType:
    """One ``<vehicleType>`` this fleet writes, with its resolved references."""

    no: int
    name: str
    category: str
    colour_no: int
    des_accel_func_no: int
    max_accel_func_no: int
    des_decel_func_no: int
    max_decel_func_no: int
    model_distr_no: int
    heavy_desired_accel: bool

    @property
    def uses_mrwa_accel(self) -> bool:
        return self.heavy_desired_accel


def _empty_coverage() -> dict[str, list[str]]:
    return {
        "classes_observed": [],
        "classes_mapped": [],
        "classes_unmapped": [],
        "canonicals_covered": [],
        "canonicals_uncovered": [],
    }


@dataclass(frozen=True)
class FleetCoverage:
    """Who mapped onto a §5.2.3 type and who did not — never a silent drop."""

    entries: dict[str, list[str]] = field(default_factory=_empty_coverage)

    def add(self, key: str, value: str) -> None:
        bucket = self.entries[key]
        if value not in bucket:
            bucket.append(value)

    def sorted_entries(self) -> dict[str, list[str]]:
        return {key: sorted(value) for key, value in sorted(self.entries.items())}

    def counts(self) -> dict[str, int]:
        return {key: len(value) for key, value in sorted(self.entries.items())}


@dataclass(frozen=True)
class DraftFleet:
    """Everything one draft's vehicle-type layer contributes to the emission.

    All-or-nothing like ``DraftDemand``: ``types`` is empty exactly when a
    REFUSE issue is present. A refused fleet contributes findings and no object.
    """

    types: tuple[EmittedVehicleType, ...] = ()
    map: Mapping[str, VehicleTypeRef] = field(default_factory=dict)
    canonical_map: Mapping[CanonicalVehicleClass, VehicleTypeRef] = field(default_factory=dict)
    speed_distr_no: int = 0
    issues: tuple[ImportIssue, ...] = ()
    compliance: tuple[ComplianceItem, ...] = ()
    coverage: FleetCoverage = field(default_factory=FleetCoverage)
    settings: FleetSettings = field(default_factory=lambda: DEFAULT_SETTINGS)

    @property
    def refusals(self) -> tuple[ImportIssue, ...]:
        return tuple(i for i in self.issues if i.severity is IssueSeverity.REFUSE)

    @property
    def warnings(self) -> tuple[ImportIssue, ...]:
        return tuple(i for i in self.issues if i.severity is IssueSeverity.WARN)

    @property
    def infos(self) -> tuple[ImportIssue, ...]:
        return tuple(i for i in self.issues if i.severity is IssueSeverity.INFO)

    @property
    def ok(self) -> bool:
        return not self.refusals

    @property
    def emitting(self) -> bool:
        """Whether there is anything to write."""
        return self.ok and bool(self.types)

    def issue_codes(self, severity: IssueSeverity | None = None) -> list[str]:
        return [i.code for i in self.issues if severity is None or i.severity is severity]

    def declared_type_nos(self) -> set[int]:
        return {t.no for t in self.types}

    def check_composition_targets(self, demand) -> list[ImportIssue]:
        """Named refusal for "a composition target class absent from the fleet".

        A cross-check for the compose step: every ``vehType``/``desSpeedDistr``
        a demand's compositions reference must be a type this fleet declares and
        the speed distribution it built. Returns issues (empty when consistent) —
        it never mutates, so a caller decides whether to emit.
        """
        issues: list[ImportIssue] = []
        declared = self.declared_type_nos()
        for plan in getattr(demand, "compositions", ()):
            for flow in plan.flows:
                if flow.veh_type_no not in declared:
                    issues.append(
                        _issue(
                            IssueSeverity.REFUSE,
                            "genesis_fleet_composition_target_absent",
                            f"Composition {plan.no} references vehType {flow.veh_type_no}, which "
                            f"this fleet does not declare (declares {sorted(declared)}). Emitting "
                            "both would put a dangling reference in the file, which Vissim drops "
                            "silently while reporting a successful load.",
                            messages={
                                "ko": f"구성 {plan.no}이(가) 이 플릿에 없는 차종 "
                                f"{flow.veh_type_no}을(를) 참조합니다(선언: {sorted(declared)}). "
                                "끊어진 참조는 Vissim이 조용히 버립니다.",
                                "en": f"Composition {plan.no} references vehType "
                                f"{flow.veh_type_no}, absent from this fleet ({sorted(declared)}).",
                                "ja": f"構成 {plan.no} がこのフリートにない車種 {flow.veh_type_no} "
                                f"を参照しています(宣言: {sorted(declared)})。",
                            },
                            citation=CIT_S523_VEHICLE_TYPES,
                            context={"veh_type_no": flow.veh_type_no, "declared": sorted(declared)},
                        )
                    )
                if flow.des_speed_distr_no != self.speed_distr_no:
                    issues.append(
                        _issue(
                            IssueSeverity.REFUSE,
                            "genesis_fleet_composition_target_absent",
                            f"Composition {plan.no} references desSpeedDistr "
                            f"{flow.des_speed_distr_no}, which this fleet did not build (built "
                            f"{self.speed_distr_no}).",
                            messages={
                                "ko": f"구성 {plan.no}이(가) 이 플릿이 만들지 않은 희망속도분포 "
                                f"{flow.des_speed_distr_no}을(를) 참조합니다(생성: {self.speed_distr_no}).",
                                "en": f"Composition {plan.no} references desSpeedDistr "
                                f"{flow.des_speed_distr_no}, not built by this fleet "
                                f"({self.speed_distr_no}).",
                                "ja": f"構成 {plan.no} がこのフリートが作成していない希望速度分布 "
                                f"{flow.des_speed_distr_no} を参照しています(生成: {self.speed_distr_no})。",
                            },
                            citation=CIT_S5253_SPEED_PROFILE,
                            context={
                                "des_speed_distr_no": flow.des_speed_distr_no,
                                "built": self.speed_distr_no,
                            },
                        )
                    )
        return issues


#: The empty fleet. ``emit`` treats this and ``None`` identically.
NO_FLEET = DraftFleet()


# ---------------------------------------------------------------------------
# Building the fleet
# ---------------------------------------------------------------------------


def observed_vehicle_classes(result: ImportResult) -> list[VehicleClassRef]:
    """The distinct real vehicle classes in an import, in ``code`` order.

    Skips the all-class aggregate (``canonical=ALL_VEHICLES``) the way
    ``demand.seedable_counts`` does: a total is not a class and carries no type.
    """
    seen: dict[str, VehicleClassRef] = {}
    for count in result.counts:
        ref = count.vehicle_class
        if ref.canonical is CanonicalVehicleClass.ALL_VEHICLES:
            continue
        seen.setdefault(ref.code, ref)
    return [seen[code] for code in sorted(seen)]


def _canonical_type_no(canonical: CanonicalVehicleClass | None, settings: FleetSettings) -> int | None:
    if canonical is CanonicalVehicleClass.HEAVY_VEHICLE:
        return settings.heavy_representative_type_no
    if canonical is None:
        return None
    return _FIXED_CANONICAL_TYPE.get(canonical)


def build_fleet(
    classes: Iterable[VehicleClassRef],
    *,
    settings: FleetSettings | None = None,
) -> DraftFleet:
    """The §5.2.3 fleet for a set of observed vehicle classes.

    Maps each observed class → a MRWA §5.2.3 vehicle type through its
    ``canonical`` bucket, emits exactly the referenced types + their transitive
    closure, and returns the ``{class code → VehicleTypeRef}`` map
    ``generate_demand`` consumes. A class that does not map is NAMED in the
    coverage (or refused, under ``require_full_coverage``); nothing is
    force-fitted.

    Returns a :class:`DraftFleet` ALWAYS — the import layer's report-not-raise
    contract. ``ok`` is false and every collection empty when a check refused.
    """
    settings = settings or DEFAULT_SETTINGS
    issues: list[ImportIssue] = []
    coverage = FleetCoverage()

    def _bail() -> DraftFleet:
        return DraftFleet(issues=tuple(issues), coverage=coverage, settings=settings)

    # -- the desired-speed profile (§5.2.5.3) --------------------------------
    speed = settings.desired_speed_kmh
    if speed >= 70:
        issues.append(
            _issue(
                IssueSeverity.REFUSE,
                "genesis_fleet_high_speed_needs_main_roads",
                f"A desired speed of {speed} km/h was requested, but §5.2.5.3 requires modellers "
                "to consult Main Roads for the speed distributions of roads with limits of 70 km/h "
                "or greater before modelling. This module carries only the default linear profiles "
                "for limits up to 60 km/h, and will not invent a distribution for a higher limit.",
                messages={
                    "ko": f"희망속도 {speed} km/h가 요청되었으나 §5.2.5.3은 70 km/h 이상 도로의 "
                    "속도분포는 Main Roads와 협의하도록 요구합니다. 60 km/h 이하 기본 프로파일만 "
                    "제공하며 더 높은 값은 지어내지 않습니다.",
                    "en": f"Desired speed {speed} km/h needs Main Roads consultation (§5.2.5.3, "
                    "limits ≥70 km/h); only default profiles ≤60 km/h are carried.",
                    "ja": f"希望速度 {speed} km/h は §5.2.5.3 により 70 km/h 以上の道路では Main "
                    "Roads との協議が必要です。60 km/h 以下の既定プロファイルのみ提供します。",
                },
                citation=CIT_S5253_CONSULT,
                context={"desired_speed_kmh": speed},
            )
        )
        return _bail()
    if speed not in SPEED_DISTRIBUTIONS_KMH:
        issues.append(
            _issue(
                IssueSeverity.REFUSE,
                "genesis_fleet_desired_speed_distribution_not_built",
                f"No measured desired-speed distribution exists for {speed} km/h. Only the Vissim "
                f"default linear profiles {sorted(SPEED_DISTRIBUTIONS_KMH)} km/h are carried "
                "(§5.2.5.3), and building a distribution for another limit would invent a speed "
                "band nobody measured.",
                messages={
                    "ko": f"{speed} km/h에 대한 실측 희망속도분포가 없습니다. "
                    f"{sorted(SPEED_DISTRIBUTIONS_KMH)} km/h 기본 프로파일만 제공합니다.",
                    "en": f"No measured desired-speed distribution for {speed} km/h; only "
                    f"{sorted(SPEED_DISTRIBUTIONS_KMH)} km/h are carried.",
                    "ja": f"{speed} km/h の実測希望速度分布がありません。"
                    f"{sorted(SPEED_DISTRIBUTIONS_KMH)} km/h のみ提供します。",
                },
                citation=CIT_S5253_SPEED_PROFILE,
                context={"desired_speed_kmh": speed, "available": sorted(SPEED_DISTRIBUTIONS_KMH)},
            )
        )
        return _bail()

    # -- the class → type join -----------------------------------------------
    demand_map: dict[str, VehicleTypeRef] = {}
    canonical_map: dict[CanonicalVehicleClass, VehicleTypeRef] = {}
    referenced_type_nos: set[int] = set()
    unmapped: list[str] = []

    distinct: dict[str, VehicleClassRef] = {}
    for ref in classes:
        if ref.canonical is CanonicalVehicleClass.ALL_VEHICLES:
            continue
        distinct.setdefault(ref.code, ref)

    for code in sorted(distinct):
        ref = distinct[code]
        coverage.add("classes_observed", code)
        type_no = _canonical_type_no(ref.canonical, settings)
        if type_no is None:
            unmapped.append(code)
            coverage.add("classes_unmapped", code)
            if ref.canonical is not None:
                coverage.add("canonicals_uncovered", ref.canonical.value)
            continue
        veh_ref = VehicleTypeRef(veh_type_no=type_no, des_speed_distr_no=speed)
        demand_map[code] = veh_ref
        referenced_type_nos.add(type_no)
        coverage.add("classes_mapped", f"{code}->type {type_no}")
        if ref.canonical is not None:
            canonical_map[ref.canonical] = veh_ref
            coverage.add("canonicals_covered", ref.canonical.value)

    if unmapped:
        if settings.require_full_coverage:
            issues.append(
                _issue(
                    IssueSeverity.REFUSE,
                    "genesis_fleet_class_without_mrwa_type",
                    f"Observed class code(s) {sorted(unmapped)} have no MRWA §5.2.3 vehicle type, "
                    "and full coverage was demanded. The §5.2.3 types are Austroads (WA); a Korean "
                    "소형화물차 (화물_소형) 1.2 left canonical=None because it behaves like a light "
                    "vehicle and calling it either LIGHT or HEAVY decides a question the count does "
                    "not answer. Rather than force-fit a wrong type, this refuses.",
                    messages={
                        "ko": f"관측 차종 {sorted(unmapped)}에 대응하는 MRWA §5.2.3 차종이 없고 전체 "
                        "커버리지가 요구되었습니다. 잘못된 차종을 억지로 붙이지 않고 거부합니다.",
                        "en": f"Class code(s) {sorted(unmapped)} have no MRWA §5.2.3 type and full "
                        "coverage was demanded; refused rather than force-fit a wrong type.",
                        "ja": f"観測車種 {sorted(unmapped)} に対応する MRWA §5.2.3 車種がなく、完全な"
                        "カバレッジが要求されました。誤った車種を無理に割り当てず拒否します。",
                    },
                    citation=CIT_S523_VEHICLE_TYPES,
                    context={"unmapped_classes": sorted(unmapped)},
                )
            )
            return _bail()
        issues.append(
            _issue(
                IssueSeverity.INFO,
                "genesis_fleet_class_not_covered",
                f"Observed class code(s) {sorted(unmapped)} carry no MRWA §5.2.3 vehicle type and "
                "are named here rather than force-fitted: the §5.2.3 types are Austroads, and a "
                "class 1.2 left canonical=None (화물_소형) or a bucket §5.2.3 has no type for "
                "(motorcycle, bicycle) has no defensible mapping. A demand referencing such a class "
                "will refuse in generate_demand until the caller resolves it — reduced coverage, "
                "not a guess.",
                citation=CIT_S523_VEHICLE_TYPES,
                context={"unmapped_classes": sorted(unmapped)},
            )
        )

    # -- the referenced types + their transitive closure ---------------------
    types = tuple(_emit_type(no) for no in sorted(referenced_type_nos))
    _disclosures(types, speed, issues)

    # Table 5-2's envelope is inverted at 96 km/h for every heavy type. The
    # values are emitted VERBATIM (never silently corrected), so the anomaly is
    # named rather than hidden — a data finding to raise with the client.
    anomalies = {
        t.no: table_5_2_envelope_anomalies(t.no)
        for t in types
        if t.heavy_desired_accel and table_5_2_envelope_anomalies(t.no)
    }
    if anomalies:
        issues.append(
            _issue(
                IssueSeverity.WARN,
                "genesis_fleet_table_5_2_envelope_inverted",
                "Table 5-2's desired-acceleration envelope is inverted (yMin > yMax) at 96 km/h for "
                f"heavy type(s) {sorted(anomalies)}: the source table keeps MRWA's three values "
                "verbatim and this is recorded as a guideline finding (in Table 5-2 or its "
                "extraction) to raise with the client. Because Vissim refuses the whole network "
                "load on any point where yMin > yMax, the emitter writes a min/max relabel of "
                "those same three values (y unchanged, still inside) so the network loads — no "
                "value is invented.",
                messages={
                    "ko": f"Table 5-2의 희망가속도 포락선이 96 km/h에서 역전(yMin > yMax)됩니다"
                    f"(중차종 {sorted(anomalies)}). 원본 표는 MRWA 3개 값을 그대로 보존하며 지침 "
                    "소견으로 기록합니다. Vissim은 yMin > yMax 지점에서 전체 네트워크 로드를 거부하므로, "
                    "파일에는 같은 3개 값의 min/max 라벨만 재정렬해(값은 지어내지 않음) 로드되게 합니다.",
                    "en": f"Table 5-2's acceleration envelope is inverted (yMin > yMax) at 96 km/h "
                    f"for heavy type(s) {sorted(anomalies)}; the source keeps MRWA's three values "
                    "verbatim as a recorded finding, and the emitter relabels min/max (no value "
                    "invented) so the network loads.",
                    "ja": f"Table 5-2 の希望加速度エンベロープが 96 km/h で反転(yMin > yMax)します"
                    f"(重車種 {sorted(anomalies)})。原表は MRWA の3値をそのまま保持し所見として記録します。"
                    "Vissim は yMin > yMax の点で全体の読み込みを拒否するため、同じ3値の min/max ラベル"
                    "のみ並べ替えて(値は捏造せず)読み込み可能にします。",
                },
                citation=CIT_S5232_HEAVY_ACCEL,
                context={"inverted_points": {no: pts for no, pts in anomalies.items()}},
            )
        )

    compliance = tuple(_compliance(types, speed, settings))

    return DraftFleet(
        types=types,
        map=demand_map,
        canonical_map=canonical_map,
        speed_distr_no=speed,
        issues=tuple(issues),
        compliance=compliance,
        coverage=coverage,
        settings=settings,
    )


def _emit_type(type_no: int) -> EmittedVehicleType:
    mrwa = MRWA_TYPE_BY_NO[type_no]
    cat = mrwa.category
    des_accel = (
        MRWA_DES_ACCEL_FUNC_OFFSET + type_no if mrwa.heavy_desired_accel else DES_ACCEL_DEFAULT_NO[cat]
    )
    return EmittedVehicleType(
        no=type_no,
        name=mrwa.name,
        category=cat,
        colour_no=mrwa.colour_no,
        des_accel_func_no=des_accel,
        max_accel_func_no=MAX_ACCEL_NO[cat],
        des_decel_func_no=DES_DECEL_NO[cat],
        max_decel_func_no=MAX_DECEL_NO[cat],
        model_distr_no=MODEL_DISTR_NO_BY_CATEGORY[cat],
        heavy_desired_accel=mrwa.heavy_desired_accel,
    )


def _disclosures(
    types: Sequence[EmittedVehicleType], speed: int, issues: list[ImportIssue]
) -> None:
    """What this module decided for itself, said out loud on every run."""
    if not types:
        return
    issues.append(
        _issue(
            IssueSeverity.INFO,
            "genesis_fleet_2d3d_model_is_install_asset",
            f"{len(types)} vehicle type(s) each reference a 2D/3D model whose file3D names a "
            "Vissim-install asset (a .u3dm/.fbx under #3dmodels#). That file is the ONE reference "
            "no .inpx can embed and this offline unit cannot verify resolves; §5.2.3.1's specific "
            "2D/3D models live in Figure 5-4, a lost figure, so a single Vissim-default model per "
            "category is copied verbatim rather than the figure's models invented. Whether the "
            "asset resolves is the live open test, a named follow-up.",
            citation=CIT_S5231_2D3D,
            context={"vehicle_types": [t.no for t in types]},
        )
    )
    issues.append(
        _issue(
            IssueSeverity.INFO,
            "genesis_fleet_desired_speed_is_default_profile",
            f"Every type is given the §5.2.5.3 default linear desired-speed profile for "
            f"{speed} km/h. Desired speed is a property of the road (its posted limit), not of the "
            "vehicle type, so one profile is shared; the posted limit itself (§5.3.4 desired-speed "
            "decisions) is outside this draft's emitted scope.",
            citation=CIT_S5253_SPEED_PROFILE,
            context={"desired_speed_kmh": speed},
        )
    )


# ---------------------------------------------------------------------------
# Compliance — §5.2.3 and subsections, in the draft's three-state style
# ---------------------------------------------------------------------------


def _compliance(
    types: Sequence[EmittedVehicleType], speed: int, settings: FleetSettings
) -> list[ComplianceItem]:
    if not types:
        return []
    type_nos = [t.no for t in types]
    heavy = [t.no for t in types if t.heavy_desired_accel]
    items: list[ComplianceItem] = []

    items.append(
        ComplianceItem(
            rule_id="5.2.3-vehicle-types-defined",
            section="§5.2.3",
            doc_page=182,
            state=ComplianceState.APPLIED,
            citation=CIT_S523_VEHICLE_TYPES,
            instances=len(types),
            subjects=[f"type {t.no} {t.name}" for t in types],
            evidence={
                "type_nos": type_nos,
                "recommended_minimum": [t.no for t in MRWA_FLEET],
                "container_written": True,
            },
            detail={
                "ko": "Table 5-1의 Austroads 차종을 <vehicleTypes>로 선언했습니다. 참조된 차종만 "
                "내보내며(관측 클래스가 매핑되는 것), 권고 최소 집합 전체는 상수로 보관합니다.",
                "en": "Table 5-1's Austroads types are declared in <vehicleTypes>. Only the "
                "referenced types are emitted; the full recommended-minimum set is kept as constants.",
                "ja": "Table 5-1 の Austroads 車種を <vehicleTypes> として宣言しました。参照される"
                "車種のみ出力し、推奨最小セット全体は定数として保持します。",
            },
        )
    )

    items.append(
        ComplianceItem(
            rule_id="5.2.3.1-2d3d-model",
            section="§5.2.3.1",
            doc_page=183,
            state=ComplianceState.NOT_CHECKABLE,
            reason=REASON_FIGURE_LOST,
            citation=CIT_S5231_2D3D,
            instances=len(types),
            subjects=[f"type {t.no} ({t.category})" for t in types],
            evidence={"model_distr_nos": sorted({t.model_distr_no for t in types})},
            detail={
                "ko": "각 차종에 카테고리별 2D/3D 모델 분포를 연결했습니다. §5.2.3.1이 요구하는 "
                "구체적 모델(Figure 5-4)은 유실 그림이라 Vissim 기본 모델 하나를 그대로 복사했고 "
                "그 file3D(.u3dm/.fbx)는 Vissim 설치 자산이라 오프라인에서 확인할 수 없습니다.",
                "en": "Each type is connected to a per-category 2D/3D model distribution. §5.2.3.1's "
                "specific models live in Figure 5-4 (lost), so one Vissim-default model per category "
                "is copied verbatim, and its file3D (.u3dm/.fbx) is a Vissim-install asset not "
                "checkable offline.",
                "ja": "各車種にカテゴリ別 2D/3D モデル分布を接続しました。§5.2.3.1 の具体的モデルは "
                "Figure 5-4(消失図)にあるため、カテゴリごとに Vissim 既定モデルを1つ複製し、その "
                "file3D(.u3dm/.fbx)は Vissim インストール資産でオフライン検証不可です。",
            },
        )
    )

    items.append(
        ComplianceItem(
            rule_id="5.2.3.2-heavy-desired-acceleration",
            section="§5.2.3.2",
            doc_page=184,
            state=ComplianceState.APPLIED if heavy else ComplianceState.NOT_CHECKABLE,
            reason=None if heavy else REASON_OUTSIDE_EMITTED_SUBSET,
            citation=CIT_S5232_HEAVY_ACCEL,
            instances=len(heavy),
            subjects=[f"type {no}" for no in heavy],
            evidence={
                "heavy_type_nos": heavy,
                "speed_axis_kmh": list(TABLE_5_2_SPEED_KMH),
                "default_accel_decel_source": "Vissim reference (§5.2.3.2 'adopt the default')",
                "table_5_2_envelope_inverted_at_96kmh": sorted(
                    no for no in heavy if table_5_2_envelope_anomalies(no)
                ),
            },
            detail={
                "ko": "중차량 희망가속도는 Table 5-2를 그대로 desAccelerationFunction으로 내보냈습니다. "
                "최대가속·희망감속·최대감속은 §5.2.3.2에 따라 Vissim 기본값을 채택했습니다(경차·버스 "
                "가속도도 기본값 — Table 5-2는 중차량만 다룸).",
                "en": "Heavy desired acceleration is emitted verbatim from Table 5-2 as a "
                "desAccelerationFunction. Max-accel and des/max-decel adopt the Vissim defaults per "
                "§5.2.3.2 (light/bus acceleration also default — Table 5-2 scopes to heavy).",
                "ja": "重車両の希望加速度は Table 5-2 をそのまま desAccelerationFunction として出力"
                "しました。最大加速・希望/最大減速は §5.2.3.2 に従い Vissim 既定値を採用しました。",
            },
        )
    )

    items.append(
        ComplianceItem(
            rule_id="5.2.3.3-power-weight",
            section="§5.2.3.3",
            doc_page=185,
            state=ComplianceState.NOT_CHECKABLE,
            reason=REASON_OUTSIDE_EMITTED_SUBSET,
            citation=CIT_S5233_POWER_WEIGHT,
            instances=len(heavy),
            evidence={
                "table_5_3_names": sorted(TABLE_5_3_POWER_WEIGHT),
                "power_weight_written": False,
            },
            detail={
                "ko": "Table 5-3의 출력·중량 분포는 상수로 보관하되 내보내지 않았습니다: 출력 열은 단일 "
                "kW 값이라 Vissim 분포 범위가 아니며, §5.2.3.3 스스로 이 값들을 '보정 단계에서 참조'한다고 "
                "합니다. 범위를 지어내지 않고 보정 단계로 미룹니다.",
                "en": "Table 5-3's power/weight distributions are kept as constants but not emitted: "
                "the power column is a single kW value, not a Vissim distribution range, and §5.2.3.3 "
                "itself says these are 'referenced at the calibration stage'. Deferred rather than a "
                "range invented.",
                "ja": "Table 5-3 の出力・重量分布は定数として保持し出力していません:出力列は単一の kW "
                "値で Vissim の分布範囲ではなく、§5.2.3.3 自身がこれらを「校正段階で参照」するとしています。",
            },
        )
    )

    items.append(
        ComplianceItem(
            rule_id="5.2.3.4-colour-distributions",
            section="§5.2.3.4",
            doc_page=185,
            state=ComplianceState.APPLIED,
            citation=CIT_S5234_COLOUR,
            instances=len({t.colour_no for t in types}),
            subjects=[
                f"colour {no} {TABLE_5_4_COLOURS[no][0]}" for no in sorted({t.colour_no for t in types})
            ],
            evidence={
                "colour_nos": sorted({t.colour_no for t in types}),
                "bus_is_green": any(t.category == CAT_BUS for t in types),
            },
            detail={
                "ko": "Table 5-4의 차종별 색상 분포를 그대로 내보냈습니다(버스는 초록 — §5.2.3.4). "
                "모델 감사자가 시뮬레이션 중 차종을 색으로 구분할 수 있습니다.",
                "en": "Table 5-4's per-type colour distributions are emitted verbatim (buses green — "
                "§5.2.3.4), so an auditor can tell types apart by colour while the model runs.",
                "ja": "Table 5-4 の車種別カラー分布をそのまま出力しました(バスは緑 — §5.2.3.4)。",
            },
        )
    )

    items.append(
        ComplianceItem(
            rule_id="5.2.4-vehicle-classes",
            section="§5.2.4",
            doc_page=186,
            state=ComplianceState.NOT_CHECKABLE,
            reason=REASON_OUTSIDE_EMITTED_SUBSET,
            citation=CIT_S524_VEHICLE_CLASSES,
            instances=len(types),
            evidence={"classes_container_written": False},
            detail={
                "ko": "Table 5-5의 차종 분류(§5.2.4)는 상수로 보관하되 <vehicleClasses>를 내보내지 "
                "않았습니다: 이 파일이 쓰는 어떤 객체도 차종 클래스를 참조하지 않아, 컨테이너를 쓰면 "
                "아무도 확인하지 않는 값을 복사하게 됩니다.",
                "en": "Table 5-5's class grouping (§5.2.4) is kept as constants but <vehicleClasses> "
                "is not emitted: nothing this file writes references a vehicle class, so declaring "
                "the container would copy values no reader checks.",
                "ja": "Table 5-5 のクラス分類(§5.2.4)は定数として保持し <vehicleClasses> は出力"
                "していません:このファイルが書くどのオブジェクトも車種クラスを参照しないためです。",
            },
        )
    )

    items.append(
        ComplianceItem(
            rule_id="5.2.5.3-desired-speed-profile",
            section="§5.2.5.3",
            doc_page=190,
            state=ComplianceState.APPLIED if speed <= 60 else ComplianceState.NOT_CHECKABLE,
            citation=CIT_S5253_SPEED_PROFILE,
            instances=1,
            evidence={"desired_speed_kmh": speed, "profile": "default linear"},
            detail={
                "ko": "§5.2.5.3에 따라 60 km/h 이하 기본 선형 프로파일을 채택했습니다.",
                "en": "The §5.2.5.3 default linear profile is adopted for the ≤60 km/h limit.",
                "ja": "§5.2.5.3 に従い 60 km/h 以下の既定線形プロファイルを採用しました。",
            },
        )
    )
    return items


# ---------------------------------------------------------------------------
# XML element construction — used by ``emit.build_tree``
# ---------------------------------------------------------------------------


def _element(parent: ET.Element, tag: str, attrs: dict) -> ET.Element:
    """One ``SubElement`` with attributes in FIXED (sorted) order — the same
    determinism rule, and reason, as ``emit._element``."""
    return ET.SubElement(parent, tag, {key: attrs[key] for key in sorted(attrs)})


def _node(parent: ET.Element, spec: tuple) -> None:
    """Render a nested (tag, attrs, children) literal through ElementTree."""
    tag, attrs, children = spec
    element = _element(parent, tag, attrs)
    for child in children:
        _node(element, child)


def _accel_function(
    parent: ET.Element,
    func_tag: str,
    pts_tag: str,
    pt_tag: str,
    no: int,
    name: str,
    points: Sequence[tuple[str, str, str, str]],
) -> None:
    element = _element(parent, func_tag, {"name": name, "no": str(no)})
    container = ET.SubElement(element, pts_tag)
    for x, y, y_min, y_max in points:
        _element(container, pt_tag, {"x": x, "y": y, "yMin": y_min, "yMax": y_max})


def _mrwa_heavy_points(type_no: int) -> tuple[tuple[str, str, str, str], ...]:
    """Table 5-2's heavy desired-acceleration row -> (x, y, yMin, yMax) strings.

    Table 5-2 as transcribed is inverted (yMin > yMax) at 96 km/h for every heavy
    type (see :func:`table_5_2_envelope_anomalies`). Vissim refuses the WHOLE
    network load on any ``desAccelerationFunction`` point where ``yMin > yMax``
    (LIVE-MEASURED 2026-08-08, ``genesis_fullstack_live_check``), so at EMIT TIME
    the yMin/yMax LABELS are assigned by ``min()``/``max()`` over the same three
    MRWA values. No value is invented: ``y`` is unchanged and always lands inside
    (it is one of the three), so only which value carries the yMin/yMax label is
    normalised. The inverted source stays verbatim in
    :data:`TABLE_5_2_HEAVY_DES_ACCEL` and the finding still fires as a WARN — the
    reorder here is exactly what the live positive control loaded (type 3 @ 96
    km/h: source yMin=0.098 y=0.043 yMax=0.031 -> emitted yMin=0.031 yMax=0.098).
    """
    row = TABLE_5_2_HEAVY_DES_ACCEL[type_no]

    def _fmt(value: float) -> str:
        # Table 5-2 is given to 3 dp; render without trailing-zero noise so the
        # emitted number reads like the guideline (0.664, not 0.664000).
        text = f"{value:.3f}".rstrip("0").rstrip(".")
        return text or "0"

    points: list[tuple[str, str, str, str]] = []
    for i in range(len(TABLE_5_2_SPEED_KMH)):
        y_min, y, y_max = row["yMin"][i], row["y"][i], row["yMax"][i]
        lo, hi = min(y_min, y, y_max), max(y_min, y, y_max)
        points.append((str(TABLE_5_2_SPEED_KMH[i]), _fmt(y), _fmt(lo), _fmt(hi)))
    return tuple(points)


def _vehicle_type_element(parent: ET.Element, veh: EmittedVehicleType) -> None:
    boilerplate = VT_BOILERPLATE_BUS if veh.category == CAT_BUS else VT_BOILERPLATE_ROAD
    element = _element(
        parent,
        "vehicleType",
        {
            **boilerplate,
            "category": veh.category,
            "colorDistr1": str(veh.colour_no),
            "desAccelFunc": str(veh.des_accel_func_no),
            "desDecelFunc": str(veh.des_decel_func_no),
            "maxAccelFunc": str(veh.max_accel_func_no),
            "maxDecelFunc": str(veh.max_decel_func_no),
            "model2D3DDistr": str(veh.model_distr_no),
            "name": veh.name,
            "no": str(veh.no),
        },
    )
    _node(element, PARK_LOT_SEL_PARS)


def fleet_containers(fleet: DraftFleet | None) -> dict[str, ET.Element]:
    """The fleet containers, or ``{}`` when there is nothing to write.

    ``{}`` is the whole additivity guarantee: ``emit.build_tree`` merges this
    into its container map, so a type-less draft — ``None``, :data:`NO_FLEET`, or
    a fleet that refused — contributes no key and the emitted bytes are exactly
    what they were before this module existed.
    """
    if fleet is None or not fleet.emitting:
        return {}

    types = sorted(fleet.types, key=lambda t: t.no)
    categories = {t.category for t in types}
    containers: dict[str, ET.Element] = {}

    # colours (§5.2.3.4) — only the referenced Table 5-4 colours
    colours = ET.Element("colorDistributions")
    for no in sorted({t.colour_no for t in types}):
        name, rgb = TABLE_5_4_COLOURS[no]
        element = _element(colours, "colorDistribution", {"name": name, "no": str(no)})
        el = ET.SubElement(element, "colorDistrEl")
        _element(el, "colorDistributionElement", {"color": _colour_hex(rgb), "share": "1"})
    containers["colorDistributions"] = colours

    # desired acceleration — Vissim defaults (Car/Bus) + Table 5-2 heavy
    des_accel = ET.Element("desAccelerationFunctions")
    for no in sorted({t.des_accel_func_no for t in types}):
        if no in DES_ACCEL_POINTS:
            name = "Car" if no == 1 else "Bus"
            _accel_function(des_accel, "desAccelerationFunction", "accelFuncDataPts",
                            "accelerationFunctionDataPoint", no, name, DES_ACCEL_POINTS[no])
        else:
            type_no = no - MRWA_DES_ACCEL_FUNC_OFFSET
            _accel_function(des_accel, "desAccelerationFunction", "accelFuncDataPts",
                            "accelerationFunctionDataPoint", no,
                            f"MRWA Type {type_no} (Table 5-2)", _mrwa_heavy_points(type_no))
    containers["desAccelerationFunctions"] = des_accel

    # max acceleration / des+max deceleration — Vissim defaults
    max_accel = ET.Element("maxAccelerationFunctions")
    for no in sorted({t.max_accel_func_no for t in types}):
        _accel_function(max_accel, "maxAccelerationFunction", "accelFuncDataPts",
                        "accelerationFunctionDataPoint", no, _default_func_name(no), MAX_ACCEL_POINTS[no])
    containers["maxAccelerationFunctions"] = max_accel

    des_decel = ET.Element("desDecelerationFunctions")
    for no in sorted({t.des_decel_func_no for t in types}):
        _accel_function(des_decel, "desDecelerationFunction", "decelFuncDataPts",
                        "decelerationFunctionDataPoint", no, _default_func_name(no), DES_DECEL_POINTS[no])
    containers["desDecelerationFunctions"] = des_decel

    max_decel = ET.Element("maxDecelerationFunctions")
    for no in sorted({t.max_decel_func_no for t in types}):
        _accel_function(max_decel, "maxDecelerationFunction", "decelFuncDataPts",
                        "decelerationFunctionDataPoint", no, _default_func_name(no), MAX_DECEL_POINTS[no])
    containers["maxDecelerationFunctions"] = max_decel

    # 2D/3D — a minimal distribution per category, one representative model each
    distr = ET.Element("model2D3DDistributions")
    for cat in sorted(categories):
        element = _element(
            distr, "model2D3DDistribution",
            {"name": cat.title(), "no": str(MODEL_DISTR_NO_BY_CATEGORY[cat])},
        )
        el = ET.SubElement(element, "model2D3DDistrEl")
        _element(el, "model2D3DDistributionElement",
                 {"model2D3D": str(MODEL_NO_BY_CATEGORY[cat]), "share": "1"})
    containers["model2D3DDistributions"] = distr

    models = ET.Element("models2D3D")
    for cat in sorted(categories, key=lambda c: MODEL_NO_BY_CATEGORY[c]):
        _node(models, MODEL_2D3D_BY_CATEGORY[cat])
    containers["models2D3D"] = models

    # desired-speed distribution (§5.2.5.3)
    speeds = ET.Element("speedDistributions")
    lo, hi = SPEED_DISTRIBUTIONS_KMH[fleet.speed_distr_no]
    element = _element(speeds, "speedDistribution",
                       {"name": f"{fleet.speed_distr_no} km/h", "no": str(fleet.speed_distr_no)})
    pts = ET.SubElement(element, "speedDistrDatPts")
    _element(pts, "speedDistributionDataPoint", {"fx": "0", "x": lo})
    _element(pts, "speedDistributionDataPoint", {"fx": "1", "x": hi})
    containers["speedDistributions"] = speeds

    # the four references a type-with-a-model needs to instantiate — occupancy,
    # power, weight and curve-speed. LIVE-MEASURED required once a model2D3D is
    # attached (module docstring): a minimal single-entry (no=1) closure, every
    # value copied verbatim from ref_lht. Every emitted vehicleType references
    # these by no=1 (VT_BOILERPLATE_ROAD).
    occupancy = ET.Element("occupancyDistributions")
    _node(occupancy, OCCUPANCY_DISTRIBUTION_DEFAULT)
    containers["occupancyDistributions"] = occupancy

    power = ET.Element("powerDistributions")
    _node(power, POWER_DISTRIBUTION_DEFAULT)
    containers["powerDistributions"] = power

    weight = ET.Element("weightDistributions")
    _node(weight, WEIGHT_DISTRIBUTION_DEFAULT)
    containers["weightDistributions"] = weight

    curv = ET.Element("curvSpeedFuncs")
    _node(curv, CURV_SPEED_FUNC_DEFAULT)
    containers["curvSpeedFuncs"] = curv

    # the vehicle types themselves
    veh_types = ET.Element("vehicleTypes")
    for veh in types:
        _vehicle_type_element(veh_types, veh)
    containers["vehicleTypes"] = veh_types

    return containers


def _default_func_name(no: int) -> str:
    return {1: "Car", 2: "HGV", 3: "Bus"}.get(no, f"Function {no}")


def fleet_provenance_note(fleet: DraftFleet | None) -> str:
    """The sentence the emitter adds to its provenance comment for a
    type-carrying draft. Empty for one without, so the comment — and the file —
    is unchanged."""
    if fleet is None or not fleet.emitting:
        return ""
    type_nos = sorted(t.no for t in fleet.types)
    return (
        f"Fleet declares MRWA §5.2.3 vehicle types {type_nos} with Table 5-4 colours and, for "
        "heavy types, Table 5-2 desired acceleration (min/max relabelled at emit where the source "
        "envelope is inverted, so the network loads); other accel/decel attributes adopt Vissim "
        "defaults (§5.2.3.2). EACH TYPE NAMES A 2D/3D MODEL WHOSE file3D IS A VISSIM-INSTALL "
        ".u3dm/.fbx ASSET THIS FILE CANNOT EMBED - opening it needs an install that carries those "
        "models, which is the live open test. Each type also names a default occupancy/power/"
        "weight/curve-speed distribution (no=1, from ref_lht) that a typed model requires to load; "
        "these are Vissim defaults for loadability, not guideline values — Table 5-3's power/weight "
        "(§5.2.3.3) stays a calibration-stage constant and vehicle classes (§5.2.4) are not "
        "emitted; see the compliance report. "
    )


__all__ = [
    "CIT_S523_VEHICLE_TYPES",
    "CIT_S523_MINIMUM_TYPES",
    "CIT_S5231_2D3D",
    "CIT_S5232_ACCEL_DEFAULTS",
    "CIT_S5232_HEAVY_ACCEL",
    "CIT_S5233_POWER_WEIGHT",
    "CIT_S5234_COLOUR",
    "CIT_S5234_BUS_GREEN",
    "CIT_S524_VEHICLE_CLASSES",
    "CIT_S5253_SPEED_PROFILE",
    "CIT_S5253_CONSULT",
    "EXTRACT",
    "MRWA_FLEET",
    "MRWA_TYPE_BY_NO",
    "TABLE_5_2_HEAVY_DES_ACCEL",
    "TABLE_5_2_SPEED_KMH",
    "TABLE_5_3_POWER_WEIGHT",
    "TABLE_5_4_COLOURS",
    "SPEED_DISTRIBUTIONS_KMH",
    "REFUSE_CODES",
    "DEFAULT_SETTINGS",
    "DraftFleet",
    "EmittedVehicleType",
    "FleetCoverage",
    "FleetSettings",
    "MrwaVehicleType",
    "NO_FLEET",
    "build_fleet",
    "fleet_containers",
    "fleet_provenance_note",
    "observed_vehicle_classes",
    "table_5_2_envelope_anomalies",
]
