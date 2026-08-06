"""WGS84 → local tangent-plane metres, for RELATIVE geometry only.

Roadmap row 2.3, Genesis (a). ``osm.py`` reads degrees; Vissim links are
metres. This module is the only place the two meet, and it exists to make
**relative** geometry — the shape of a network, the distance between two of its
nodes, the angle between two approaches. It is **not** a georeference.

NOT A GEOREFERENCE — read this before using an output coordinate
----------------------------------------------------------------
An ``.inpx`` written from these coordinates carries no datum, no EPSG code and
no map anchor. Nothing here claims to place the network on the earth, and
:mod:`services.genesis.emit` deliberately writes no ``refPointMap``. That is the
same refusal ``services.genesis.diff`` makes for the same reason: this project's
only working georeference (``backend/routers/geojson.py``) is a five-constant
Helmert fit with a ~14 m max residual whose scale term is Web-Mercator latitude
distortion, so any absolute positional claim would start four lane widths wrong.
The origin latitude/longitude IS recorded — in the draft metadata and in the
emitted file's leading comment — for exactly one purpose: so a modeller (or a
later unit) can reproduce or invert the transform. Recording the transform is
not the same as asserting the result is surveyed, and MRWA §5.3.1.1 puts the
imagery/design coordinate alignment on the modeller: this generator reports that
rule as unmet rather than pretending otherwise.

The transform
-------------
Equirectangular about a computed origin ``(φ₀, λ₀)``, using the WGS84
ellipsoid's **local radii of curvature at the origin** rather than a global mean
radius (both are one ``math`` expression; the local pair removes the
sphere-vs-ellipsoid scale error, which at the equator is 0.34 % — 9 m over
3 km — and is otherwise the largest term in the budget)::

    x = N(φ₀)·cos φ₀ · (λ − λ₀)          [radians in, metres out]
    y = M(φ₀)         · (φ − φ₀)

    N(φ) = a / √(1 − e² sin²φ)            prime-vertical radius
    M(φ) = a(1 − e²) / (1 − e² sin²φ)^1.5  meridional radius

+x is east, +y is north. The inverse is exact (same two constants, divided
rather than multiplied), so a coordinate can always be taken back to degrees.

Error bound — derived, not asserted
-----------------------------------
Let ``d`` be the **half-extent**: the larger of the bounding box's two
half-widths, i.e. ``max(|x|, |y|)`` over every projected node. Let
``R = √(M·N)`` at the origin (the Gaussian radius there). Two terms:

1. **Meridian convergence (in-plane, and the one that bites).** The east scale
   is frozen at ``cos φ₀``, but the true east-west arc at latitude ``φ`` scales
   with ``cos φ``. The relative east-scale error at a latitude offset ``δφ`` is
   therefore ``|cos φ / cos φ₀ − 1| ≈ tan|φ₀| · |δφ|``. Over a pair of points
   whose east separation is ``L`` and whose latitude offset is ``δφ``, the
   distance error is ``≈ L · tan|φ₀| · |δφ|``. Inside the box ``L ≤ 2d`` and
   ``|δφ| ≤ d/R``, so::

       ε_xy(d) = 2·d²·tan|φ₀| / R                      (metres)

   This bound is **tight**, not decorative: measured against WGS84 geodesics
   (Vincenty) over a 7×7 grid of point pairs it is attained to within 0.5 %
   at latitudes 0°, ±32°, ±35°, 36° and 60° — see
   ``test_genesis_osm_draft.py`` §3, which recomputes it rather than quoting it.
   At the equator it is exactly zero and the measured error is likewise zero
   (≈3 mm at d = 5 km), which is the check that this term, and not some other,
   is what the formula describes.

2. **Tangent-plane curvature (out of plane).** The ellipsoid falls away from
   the tangent plane by the sagitta ``h(d) = d²/(2R)`` — 0.96 m at 3.57 km.
   This is the classic "flat earth" number and it is quoted here honestly:
   it does **not** appear in 2-D distances (a tangent plane's in-plane distance
   error is third order, which is why the equator row above measures ~0). It
   bounds the z/gradient assumption instead — relevant the day §5.2.1.1's
   Z-coordinate paragraph gets implemented, and a reason not to let the box grow
   without limit even at the equator.

Validity radius and the refusal
-------------------------------
The budget is :data:`MAX_PLANIMETRIC_ERROR_M` = 1.0 m — one metre, i.e. under a
third of a 3.5 m lane, chosen so a projection error can never be mistaken for a
lane-position difference. Inverting each term for that budget::

    d_convergence = √( ε·R / (2·tan|φ₀|) )     (→ ∞ as φ₀ → 0)
    d_curvature   = √( 2·ε·R )                 ≈ 3567 m, latitude-independent

    validity_radius = min(d_convergence, d_curvature)

Convergence binds above |φ₀| = arctan(¼) = 14.04°; curvature binds below it.
At Perth (−32.07°) the radius is ≈2.25 km; at Daejeon (36.35°) ≈2.10 km; at the
equator ≈3.57 km.

A network whose half-extent exceeds that radius is **refused**
(:meth:`LocalTangentPlane.check_extent`), not silently degraded. Splitting the
area, or moving to a real projected CRS, is the caller's decision to make with
the numbers in front of them — :class:`ExtentCheck` carries the measured
half-extent, the radius, the bound at that extent and which term binds.

stdlib only (``math``); no pyproj, no shapely.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# WGS84 defining constants (NIMA TR8350.2). Written out rather than imported so
# the module has no dependency at all beyond ``math``.
# ---------------------------------------------------------------------------

#: Semi-major axis, metres.
WGS84_A = 6378137.0
#: Flattening.
WGS84_F = 1.0 / 298.257223563
#: First eccentricity squared, e² = f(2 − f).
WGS84_E2 = WGS84_F * (2.0 - WGS84_F)

#: The one-metre planimetric budget the validity radius is solved for. See the
#: module docstring: under a third of a 3.5 m lane, so a projection error can
#: never be read as a lane-position difference.
MAX_PLANIMETRIC_ERROR_M = 1.0

#: |φ₀| above which meridian convergence binds rather than curvature —
#: arctan(1/4), from ``√(εR/(2tanφ)) < √(2εR) ⟺ tan φ > 1/4``. Degrees.
CONVERGENCE_BINDS_ABOVE_DEG = math.degrees(math.atan(0.25))

#: Names for the term that sets the validity radius, so a report can say which
#: physics stopped it rather than only quoting a number.
BINDING_CONVERGENCE = "meridian_convergence"
BINDING_CURVATURE = "tangent_plane_curvature"

#: Stated in the metadata of every draft, so the string travels with the file.
PROJECTION_KIND = "equirectangular_local_tangent_plane_wgs84"

NOT_GEOREFERENCED_NOTE = (
    "RELATIVE geometry only. These metres are measured from a recorded origin; the "
    "file carries no datum, no EPSG code and no map anchor, and no absolute position "
    "is claimed. MRWA OMG v2.0 §5.3.1.1 (doc p.191) makes aligning the imagery and "
    "design coordinate systems the modeller's responsibility — this generator reports "
    "that rule as unmet rather than implying it has been satisfied."
)


def meridional_radius_m(lat_deg: float) -> float:
    """M(φ) — the ellipsoid's north-south radius of curvature, metres."""
    sin_phi = math.sin(math.radians(lat_deg))
    w = 1.0 - WGS84_E2 * sin_phi * sin_phi
    return WGS84_A * (1.0 - WGS84_E2) / (w ** 1.5)


def prime_vertical_radius_m(lat_deg: float) -> float:
    """N(φ) — the ellipsoid's east-west radius of curvature, metres."""
    sin_phi = math.sin(math.radians(lat_deg))
    w = 1.0 - WGS84_E2 * sin_phi * sin_phi
    return WGS84_A / math.sqrt(w)


@dataclass(frozen=True)
class ExtentCheck:
    """The answer to "may I project a network this big about this origin?".

    ``ok`` false is a refusal with numbers attached, never a silently degraded
    transform. ``bound_m`` is the planimetric bound at the *measured* extent, so
    a caller that is over the line can see by how much.
    """

    ok: bool
    half_extent_m: float
    validity_radius_m: float
    bound_m: float
    budget_m: float
    binding_term: str
    message: str

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "half_extent_m": round(self.half_extent_m, 3),
            "validity_radius_m": round(self.validity_radius_m, 1),
            "planimetric_error_bound_m": round(self.bound_m, 4),
            "budget_m": self.budget_m,
            "binding_term": self.binding_term,
            "message": self.message,
        }


@dataclass(frozen=True)
class LocalTangentPlane:
    """An equirectangular transform pinned to one origin.

    Frozen: an origin that could be rewritten downstream would make every
    coordinate derived from it unreproducible, which is the whole reason the
    origin is recorded in the first place.
    """

    lat0_deg: float
    lon0_deg: float

    # --- derived scale factors, all at the origin ---

    @property
    def meridional_radius_m(self) -> float:
        return meridional_radius_m(self.lat0_deg)

    @property
    def prime_vertical_radius_m(self) -> float:
        return prime_vertical_radius_m(self.lat0_deg)

    @property
    def gauss_radius_m(self) -> float:
        """R = √(M·N) at the origin — the sphere that osculates the ellipsoid
        there, and the R every bound in this module is written in."""
        return math.sqrt(self.meridional_radius_m * self.prime_vertical_radius_m)

    # --- the transform ---

    def forward(self, lat_deg: float, lon_deg: float) -> tuple[float, float]:
        """(lat, lon) degrees → (x east, y north) metres from the origin."""
        x = (
            self.prime_vertical_radius_m
            * math.cos(math.radians(self.lat0_deg))
            * math.radians(lon_deg - self.lon0_deg)
        )
        y = self.meridional_radius_m * math.radians(lat_deg - self.lat0_deg)
        return x, y

    def inverse(self, x_m: float, y_m: float) -> tuple[float, float]:
        """(x, y) metres → (lat, lon) degrees. Exact inverse of :meth:`forward`."""
        lat = self.lat0_deg + math.degrees(y_m / self.meridional_radius_m)
        lon = self.lon0_deg + math.degrees(
            x_m
            / (self.prime_vertical_radius_m * math.cos(math.radians(self.lat0_deg)))
        )
        return lat, lon

    # --- the error budget ---

    def planimetric_error_bound_m(self, half_extent_m: float) -> float:
        """ε_xy(d) = 2·d²·tan|φ₀| / R. Module docstring, term 1."""
        d = abs(half_extent_m)
        return 2.0 * d * d * abs(math.tan(math.radians(self.lat0_deg))) / self.gauss_radius_m

    def curvature_drop_m(self, half_extent_m: float) -> float:
        """h(d) = d²/(2R) — the out-of-plane sagitta. Module docstring, term 2."""
        d = abs(half_extent_m)
        return d * d / (2.0 * self.gauss_radius_m)

    @property
    def binding_term(self) -> str:
        return (
            BINDING_CONVERGENCE
            if abs(self.lat0_deg) > CONVERGENCE_BINDS_ABOVE_DEG
            else BINDING_CURVATURE
        )

    @property
    def validity_radius_m(self) -> float:
        """min(d_convergence, d_curvature) for :data:`MAX_PLANIMETRIC_ERROR_M`."""
        r = self.gauss_radius_m
        budget = MAX_PLANIMETRIC_ERROR_M
        d_curvature = math.sqrt(2.0 * budget * r)
        tan_phi = abs(math.tan(math.radians(self.lat0_deg)))
        if tan_phi <= 0.0:
            return d_curvature
        d_convergence = math.sqrt(budget * r / (2.0 * tan_phi))
        return min(d_convergence, d_curvature)

    def check_extent(self, half_extent_m: float) -> ExtentCheck:
        """Refuse-or-proceed for a network of this half-extent about this origin."""
        radius = self.validity_radius_m
        bound = self.planimetric_error_bound_m(half_extent_m)
        ok = half_extent_m <= radius
        if ok:
            message = (
                f"half-extent {half_extent_m:.0f} m is within the {radius:.0f} m validity "
                f"radius for this origin (lat {self.lat0_deg:.5f}); planimetric error bound "
                f"{bound:.2f} m <= {MAX_PLANIMETRIC_ERROR_M:.1f} m budget "
                f"(binding term: {self.binding_term})."
            )
        else:
            message = (
                f"REFUSED: half-extent {half_extent_m:.0f} m exceeds the {radius:.0f} m "
                f"validity radius for origin lat {self.lat0_deg:.5f}. The equirectangular "
                f"planimetric error bound there is {bound:.2f} m, over the "
                f"{MAX_PLANIMETRIC_ERROR_M:.1f} m budget (binding term: "
                f"{self.binding_term}). Split the area or use a projected CRS — this "
                f"module will not degrade silently."
            )
        return ExtentCheck(
            ok=ok,
            half_extent_m=half_extent_m,
            validity_radius_m=radius,
            bound_m=bound,
            budget_m=MAX_PLANIMETRIC_ERROR_M,
            binding_term=self.binding_term,
            message=message,
        )

    # --- reproducibility record ---

    def metadata(self) -> dict:
        """Everything needed to reproduce or invert this transform, and the
        statement that it is not a georeference. Goes into the draft metadata
        and the emitted file's leading comment verbatim."""
        return {
            "kind": PROJECTION_KIND,
            "datum": "WGS84",
            "origin_lat_deg": round(self.lat0_deg, 9),
            "origin_lon_deg": round(self.lon0_deg, 9),
            "meridional_radius_m": round(self.meridional_radius_m, 3),
            "prime_vertical_radius_m": round(self.prime_vertical_radius_m, 3),
            "gauss_radius_m": round(self.gauss_radius_m, 3),
            "validity_radius_m": round(self.validity_radius_m, 1),
            "planimetric_budget_m": MAX_PLANIMETRIC_ERROR_M,
            "binding_term": self.binding_term,
            "georeferenced": False,
            "note": NOT_GEOREFERENCED_NOTE,
        }


def origin_for_bounds(
    min_lat: float, min_lon: float, max_lat: float, max_lon: float
) -> LocalTangentPlane:
    """The origin for a bounding box: its centre, so the half-extent — and
    therefore the error — is as small as the box allows.

    Deterministic by construction (a mean of two numbers), which is what lets
    two runs over the same ``.osm`` produce byte-identical output.
    """
    return LocalTangentPlane(
        lat0_deg=(min_lat + max_lat) / 2.0,
        lon0_deg=(min_lon + max_lon) / 2.0,
    )
