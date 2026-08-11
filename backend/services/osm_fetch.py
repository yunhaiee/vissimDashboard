"""Region → ``.osm`` extraction, the front door B1's pipeline was missing.

Roadmap v2 §5.B row **B2**. B1 (``4750936``) made generation a job: stage an
``.osm``, start a job, gate it, publish it. What it could not do was *get* the
``.osm`` — a consultant had to find an Overpass export somewhere and upload the
file by hand. This module draws that step inside the product: a region in,
a staged extract out.

**It ends where B1 begins, deliberately.** The last thing a fetch does is call
:func:`services.genesis_jobs.stage_osm` — the exact function
``POST /api/genesis/uploads`` calls — and hand back the same upload token. From
that point the network takes the road that already works: ``POST
/api/genesis/jobs`` → draft → emit → **gate** → publish. Nothing here talks to
``gate_and_publish``, imports ``services.genesis.*`` for anything but geometry
constants, or writes a dataset. A second road into the gate is exactly what A4
and B1 exist to prevent, so this one stops at the staging area.

The two caps, and where their numbers come from
-----------------------------------------------
An unbounded bbox on a public Overpass instance is a request for hundreds of
megabytes. Both caps below **refuse with the measured numbers**; neither ever
truncates, because a silently shortened extract is a network with roads missing
and no reader can notice an absence.

**Cap 1 — the region (a half-extent, in metres).** Not a number chosen for
feeling about right: it is
:attr:`services.genesis.project.LocalTangentPlane.validity_radius_m` at the
region's own centre latitude, IMPORTED rather than copied. That radius is the
half-extent at which the draft's equirectangular projection reaches its
:data:`~services.genesis.project.MAX_PLANIMETRIC_ERROR_M` = 1 m budget, and
``services.genesis.draft.build_draft`` **already refuses** anything past it
(``projection_out_of_range``). So a larger region is not "risky", it is data the
pipeline is guaranteed to reject: ≈2 254 m at Perth, ≈2 103 m at Daejeon,
≈3 567 m at the equator, where the binding physics is meridian convergence above
|φ| = 14.04° and tangent-plane curvature below it. Refusing it here saves a
download that could only end in a refusal, and the refusal says which term binds.

Because the cap is latitude-dependent it is computed per region, never stored as
a constant. :data:`MAX_REGION_HALF_EXTENT_M` — the equator's value, the largest
any origin can allow — exists only to size cap 2.

**Cap 2 — the response (bytes).** Two named terms, and the smaller wins:

* *Term A — staging parity.* ``routers/datasets.MAX_UPLOAD_BYTES`` (250 MB) is
  the ceiling on the other road into :func:`~services.genesis_jobs.stage_osm`.
  Fetching more than a browser upload may carry would make the two roads into
  one staging area disagree. Not imported here (a service importing a router is
  a layering inversion); the relationship is pinned instead by
  ``tests/test_osm_fetch.py::test_the_response_cap_never_exceeds_the_upload_ceiling``.
* *Term B — what the largest admissible region can weigh.* Measured, 2026-08-11,
  on a 41 km² Overpass extract produced by **this module's own query shape**
  (``way["highway"]`` plus its nodes with tags): mean
  :data:`REFERENCE_BYTES_PER_KM2` = 28 017 B/km². A mean is the wrong number for
  a cap, so the peak was measured too — tiling that extract at 1 km² gives a
  densest-tile-to-mean ratio of :data:`REFERENCE_PEAK_TO_MEAN_DENSITY` = 11.8.
  The cap is that PEAK density held across the largest region cap 1 can ever
  admit (:data:`MAX_REGION_AREA_KM2` ≈ 50.9 km², the equator square), i.e.
  ≈16.8 MB.

Term B binds, by a factor of ~15. It is comfortable rather than tight for the
regions this product actually builds: a Daejeon-maximum region (≈17.7 km²) at
that same peak density is ≈5.8 MB, a third of the cap. A region that genuinely
exceeds it is refused with its measured byte count and told to shrink — which is
the honest answer, not a truncation.

Discipline on the one outbound call
-----------------------------------
This is the only place in the backend that reaches the public internet for
model data, so:

* **It never runs in a request handler.** ``POST /api/genesis/region/fetches``
  returns 202 and a worker thread does the work, the same shape
  ``start_genesis_job`` uses and for the same reason
  (``docs/phase2/00-EXECUTION-GUIDE.md`` §1).
* **Timeout, then at most one retry.** :data:`SOCKET_TIMEOUT_S` is
  :data:`OVERPASS_QUERY_TIMEOUT_S` (what the query itself asks the server for)
  plus the transfer time of a cap-sized response over a
  :data:`ASSUMED_LINK_BITS_PER_S` link — giving up while the server is still
  inside the budget we asked it for would be our bug, not its. One retry, on the
  transient family only (a socket timeout, a connection failure, 502/503/504).
  **HTTP 429 is never retried**: it means "you are asking too often", and an
  immediate second ask is the dishonest response to it. Both end as NAMED
  refusals carrying the endpoint — 429 and 504 are ordinary weather on a public
  Overpass instance, not crashes.
* **A 200 is not a success.** Overpass reports a server-side failure as a
  ``<remark>`` element *inside an otherwise valid* ``<osm>`` document. That
  parses, passes ``stage_osm``'s root-element check, and becomes an empty
  network several steps later with nobody able to say why. :func:`_scan_osm`
  looks for it and refuses at the door.
* **Provenance travels with the file, not beside it.** A sidecar in the staging
  directory would not survive: ``create_genesis_job`` copies only the ``.osm``
  into the job's work directory. So the provenance record is written INTO the
  document as a leading XML comment — the same thing ``services/genesis/emit.py``
  does to an ``.inpx`` — where it reaches the job, the artifact download and any
  reader with a text editor. It names the instance, the exact query, the
  requested region, the fetch time, the byte count and the sha256 of the bytes
  as received. A network whose origin cannot be reconstructed is not auditable.
* **The ODbL note is preserved, never rewritten.** OSM data carries a licence
  obligation; Overpass states it in a ``<note>`` element and this module copies
  the response body through byte-for-byte, adding only the comment above it.
  Whether that note was present is itself recorded — measured, not asserted.
* **The endpoint is server configuration, not client input**
  (:data:`OVERPASS_ENDPOINT_ENV`). A caller-supplied URL would make this an
  SSRF instrument pointed at whatever the server can reach.

Records are one JSON file per fetch under ``<workspace>/genesis/regions/``.
Deliberately not ``genesis_jobs``' single-file registry: that file needs a lock,
a retry ladder and a "never overwrite what you could not read" rule because
every write is a read-modify-write of *every* record. One file per fetch has no
such window, so it needs none of that machinery.

stdlib only for the transport (``urllib.request``): ``requirements.txt`` is not
this unit's to change, and ``services/license_guard.py`` already makes the same
call the same way.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import threading
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

from services import genesis_jobs, workspaces
from services.genesis.project import LocalTangentPlane

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# The endpoint — server configuration, never a request parameter
# ---------------------------------------------------------------------------

#: Environment variable naming the Overpass instance to use. Set it to a
#: self-hosted instance to keep client study areas off a public server.
OVERPASS_ENDPOINT_ENV = "PRISM_OVERPASS_ENDPOINT"

DEFAULT_OVERPASS_ENDPOINT = "https://overpass-api.de/api/interpreter"

#: Overpass asks clients to identify themselves; an anonymous flood is what
#: gets an IP range blocked for everybody.
USER_AGENT = "Prism-Genesis/1.0 (Dochak Vissim network genesis; region extract)"


def overpass_endpoint() -> str:
    """The configured Overpass instance.

    Validated for shape only — this is an operator's setting, so the failure
    being guarded against is a typo, not an attacker. It is still checked rather
    than trusted, because a malformed value would otherwise surface as an
    unreadable ``urllib`` error inside a worker thread.
    """
    raw = (os.getenv(OVERPASS_ENDPOINT_ENV) or DEFAULT_OVERPASS_ENDPOINT).strip()
    parts = urlsplit(raw)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise OsmFetchRefused(
            "overpass_endpoint_misconfigured",
            [
                f"{OVERPASS_ENDPOINT_ENV}={raw!r} is not an http(s) URL. "
                "Set it to an Overpass interpreter endpoint, or unset it to use "
                f"{DEFAULT_OVERPASS_ENDPOINT}."
            ],
        )
    return raw


# ---------------------------------------------------------------------------
# Cap 1 — the region. Latitude-dependent, so computed per region.
# ---------------------------------------------------------------------------

#: The largest half-extent ANY origin can admit — the equator's validity radius
#: (≈3 567 m, where tangent-plane curvature binds). Imported behaviour, not a
#: copied number. Used only to size cap 2; the cap a caller is actually held to
#: is :func:`region_limits`, computed at their own centre latitude.
MAX_REGION_HALF_EXTENT_M = LocalTangentPlane(0.0, 0.0).validity_radius_m

#: The square that half-extent describes, in km². ≈50.9 km².
MAX_REGION_AREA_KM2 = (2.0 * MAX_REGION_HALF_EXTENT_M / 1000.0) ** 2


# ---------------------------------------------------------------------------
# Cap 2 — the response. Term B (measured) binds; term A is pinned by a test.
# ---------------------------------------------------------------------------

#: Measured 2026-08-11 over a 41 km² Overpass extract produced by this module's
#: own query shape — mean bytes per km². A mean alone would be the wrong cap,
#: which is what the peak ratio below is for.
REFERENCE_BYTES_PER_KM2 = 28_017

#: Measured on the same extract: bytes in the densest 1 km² tile ÷ the mean.
#: 11.76, carried as 11.8. This is what turns a mean into a cap that a dense
#: urban core does not immediately trip.
REFERENCE_PEAK_TO_MEAN_DENSITY = 11.8

#: Peak density = mean × the measured peak-to-mean ratio.
PEAK_BYTES_PER_KM2 = int(REFERENCE_BYTES_PER_KM2 * REFERENCE_PEAK_TO_MEAN_DENSITY)

#: **Measured against a REAL Overpass instance, 2026-08-11, central Daejeon.**
#: The two constants above were measured on a suburban Perth-area extract, and
#: this project's target market is a dense Korean CBD — so the first live fetch
#: this module ever made was also a test of whether that generalises. It does
#: not: a 0.36 km² block of Dunsan-dong returned 200,309 B, i.e.
#: **555,837 B/km² — 19.8× the reference MEAN and 1.68× the reference PEAK.**
#:
#: What that does and does not break, stated separately because they have
#: different answers:
#:
#: * **The cap holds.** The largest region cap 1 admits at Daejeon's latitude is
#:   17.3 km², which at the measured density is ≈9.6 MB against a 16.8 MB cap —
#:   1.75× headroom. No legitimate Daejeon region is refused for size.
#: * **The ESTIMATE lied.** It was ``area × mean``, so it told this fetch's
#:   caller ~10 KB and delivered 200 KB. An estimate a user sizes a decision
#:   against must not be twenty times light, and "measured on a suburban extract"
#:   is exactly the population-generalisation error this repo has now paid for
#:   four times (``a522ec6``: a rule measured on one population generalises only
#:   to that population).
#:
#: So the estimate now uses the PEAK density — deliberately conservative, and
#: still 1.68× under what Daejeon actually returned, which is why it is reported
#: as an upper-ish bound rather than a prediction. One 0.36 km² sample is not a
#: density model; it is a counter-example, and it is recorded as one. Re-deriving
#: a proper Korean-CBD mean wants a tiled measurement like the original.
DAEJEON_CBD_MEASURED_BYTES_PER_KM2 = 555_837

#: Cap 2, term B: the peak density held across the largest region cap 1 can
#: admit. ≈16.8 MB. Term A (``routers/datasets.MAX_UPLOAD_BYTES``, 250 MB) is
#: ~15× larger and therefore not binding; the tests pin that ordering so a
#: future change to either one cannot silently invert it.
MAX_RESPONSE_BYTES = int(PEAK_BYTES_PER_KM2 * MAX_REGION_AREA_KM2)


# ---------------------------------------------------------------------------
# Timeouts and the one retry
# ---------------------------------------------------------------------------

#: What the query itself asks the server for (``[timeout:N]``). Public Overpass
#: defaults to 180 s; we ask for less because cap 1 bounds the region to at most
#: ~51 km² of highway ways, and a bbox-bounded query of that size that has not
#: finished in a minute means a server under load — which is a "come back
#: later" refusal, not something to sit and wait out.
OVERPASS_QUERY_TIMEOUT_S = 60

#: The link speed the transfer budget assumes. 2 Mbit/s is deliberately
#: pessimistic: the cost of over-estimating is a socket that waits longer than
#: necessary on a thread nothing is blocked on, and the cost of
#: under-estimating is abandoning a response that was still arriving.
ASSUMED_LINK_BITS_PER_S = 2_000_000

#: Transfer time for a cap-sized response over that link, seconds.
TRANSFER_BUDGET_S = int(math.ceil(MAX_RESPONSE_BYTES * 8 / ASSUMED_LINK_BITS_PER_S))

#: Socket timeout = the server's own budget + the transfer of what it may send.
#: Giving up inside the budget we asked the server for would be our bug.
SOCKET_TIMEOUT_S = OVERPASS_QUERY_TIMEOUT_S + TRANSFER_BUDGET_S

#: Statuses worth ONE more attempt: a gateway that was momentarily unavailable
#: or a query the gateway timed out. 429 is NOT here — see the module docstring.
RETRYABLE_STATUSES = frozenset({502, 503, 504})

RATE_LIMITED_STATUS = 429

#: Attempts, total. "One honest retry at most" — literally two calls.
MAX_ATTEMPTS = 2

#: Pause before the retry. Short, because the transient family it covers is a
#: gateway blip rather than a load signal, and the caller is polling a job.
RETRY_DELAY_S = 1.0

#: Bytes per read. Small enough that the cap is enforced DURING the download and
#: never after it — the point of a cap is not to buffer 400 MB and then complain.
_READ_CHUNK_BYTES = 256 * 1024


# ---------------------------------------------------------------------------
# Records on disk
# ---------------------------------------------------------------------------

REGIONS_DIRNAME = "regions"
RECORD_FILENAME = "fetch.json"

STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
#: The extract is staged and its upload token is on the record. The ONLY
#: success state, and it means exactly "B1 can take it from here".
STATUS_STAGED = "staged"
#: A named refusal: a cap, a rate limit, an unreachable instance, a region with
#: no roads in it. Carries ``refusal.code`` and ``refusal.reasons``.
STATUS_REFUSED = "refused"
#: Something nobody predicted. Distinct from ``refused`` so "we said no, and
#: why" never reads like "we fell over".
STATUS_ERROR = "error"

TERMINAL_STATUSES = frozenset({STATUS_STAGED, STATUS_REFUSED, STATUS_ERROR})

PHASE_QUEUED = "queued"
PHASE_FETCHING = "fetching"
PHASE_STAGING = "staging"
PHASE_DONE = "done"

_PHASE_PROGRESS = {
    PHASE_QUEUED: 0.0,
    PHASE_FETCHING: 0.2,
    PHASE_STAGING: 0.9,
    PHASE_DONE: 1.0,
}

#: Fetch ids. Hex only, for the containment reason ``genesis_jobs._ID_RE``
#: states: the id becomes a directory name.
_ID_RE = re.compile(r"^[0-9a-f]{8,32}$")

#: A human label for the region. Same alphabet as a genesis network name, since
#: it becomes the staged file's stem and then the artifact base name.
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class OsmFetchError(RuntimeError):
    """Base class for this module's failures."""


class OsmFetchNotFoundError(OsmFetchError):
    """No such fetch in THIS workspace — indistinguishable from an unknown id,
    so the answer cannot be used to probe what exists on the box."""


class OsmFetchRefused(OsmFetchError):
    """A refusal with a machine-readable code and human reasons.

    Same shape as ``genesis_jobs.GenesisRefused`` so ``routers/genesis.py`` maps
    both to the same 400 body and the frontend needs one renderer, not two.
    """

    def __init__(self, code: str, reasons: list[str], *, detail: dict[str, Any] | None = None) -> None:
        super().__init__(f"{code}: {'; '.join(reasons) or code}")
        self.code = code
        self.reasons = reasons
        #: Measured numbers behind the refusal (the region's area against the
        #: cap, the bytes against the byte cap, the endpoint). Rendered by the
        #: UI beside the sentence, because "too large" without a number is not
        #: an actionable refusal.
        self.detail = detail or {}


# ---------------------------------------------------------------------------
# The region and its measurement
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BoundingBox:
    """A south/west/north/east box in WGS84 degrees.

    Validated on construction rather than at use: a box that is inverted, zero
    sized or off the globe is a client bug, and finding it out after a download
    would be finding it out too late.
    """

    south: float
    west: float
    north: float
    east: float

    def __post_init__(self) -> None:
        for name, value in (
            ("south", self.south),
            ("west", self.west),
            ("north", self.north),
            ("east", self.east),
        ):
            if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                raise OsmFetchRefused(
                    "region_bbox_invalid", [f"{name}={value!r} is not a finite number."]
                )
        if not (-90.0 <= self.south <= 90.0 and -90.0 <= self.north <= 90.0):
            raise OsmFetchRefused(
                "region_bbox_invalid",
                [f"Latitudes must be within ±90° (south={self.south}, north={self.north})."],
            )
        if not (-180.0 <= self.west <= 180.0 and -180.0 <= self.east <= 180.0):
            raise OsmFetchRefused(
                "region_bbox_invalid",
                [f"Longitudes must be within ±180° (west={self.west}, east={self.east})."],
            )
        if self.north <= self.south:
            raise OsmFetchRefused(
                "region_bbox_invalid",
                [f"north ({self.north}) must be greater than south ({self.south})."],
            )
        if self.east <= self.west:
            # Refused rather than wrapped: a box that crosses the antimeridian
            # would need the fetch, the projection origin and the area to agree
            # on a convention none of them currently has, and guessing one is
            # how a region silently becomes the whole planet minus itself.
            raise OsmFetchRefused(
                "region_bbox_invalid",
                [
                    f"east ({self.east}) must be greater than west ({self.west}). "
                    "A region crossing the antimeridian is not supported; split it in two."
                ],
            )

    @property
    def centre(self) -> tuple[float, float]:
        return (self.south + self.north) / 2.0, (self.west + self.east) / 2.0

    def as_dict(self) -> dict[str, float]:
        return {
            "south": self.south,
            "west": self.west,
            "north": self.north,
            "east": self.east,
        }

    @classmethod
    def from_mapping(cls, payload: Any) -> "BoundingBox":
        if not isinstance(payload, dict):
            raise OsmFetchRefused(
                "region_bbox_invalid", ["A region must be an object with south/west/north/east."]
            )
        missing = [key for key in ("south", "west", "north", "east") if key not in payload]
        if missing:
            raise OsmFetchRefused(
                "region_bbox_invalid", [f"Region is missing: {', '.join(missing)}."]
            )
        try:
            return cls(
                south=float(payload["south"]),
                west=float(payload["west"]),
                north=float(payload["north"]),
                east=float(payload["east"]),
            )
        except (TypeError, ValueError) as exc:
            raise OsmFetchRefused(
                "region_bbox_invalid", [f"Region coordinates are not numbers ({exc})."]
            ) from exc


@dataclass(frozen=True)
class RegionMeasurement:
    """What a region measures, against the caps, BEFORE anything is downloaded.

    Every field is a measurement or a derived limit; nothing here is an opinion.
    ``estimated_bytes`` is explicitly an ESTIMATE and is labelled as one
    everywhere it surfaces — the cap is enforced against the bytes that actually
    arrive, never against this number.

    It uses the **PEAK** density, not the mean. It used the mean until the first
    live fetch measured central Daejeon at 19.8× the reference mean and told its
    caller ~10 KB before delivering 200 KB (see
    :data:`DAEJEON_CBD_MEASURED_BYTES_PER_KM2`). An estimate a user sizes a
    decision against must not be twenty times light, so it is now deliberately
    conservative — and still 1.68× under what Daejeon actually returned, which is
    why the UI must read it as "up to", never as a prediction.
    """

    bbox: BoundingBox
    centre_lat: float
    centre_lon: float
    width_m: float
    height_m: float
    half_extent_m: float
    area_km2: float
    validity_radius_m: float
    binding_term: str
    planimetric_error_bound_m: float
    within_region_cap: bool
    estimated_bytes: int
    max_response_bytes: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "region": self.bbox.as_dict(),
            "centre_lat": round(self.centre_lat, 6),
            "centre_lon": round(self.centre_lon, 6),
            "width_m": round(self.width_m, 1),
            "height_m": round(self.height_m, 1),
            "half_extent_m": round(self.half_extent_m, 1),
            "area_km2": round(self.area_km2, 4),
            "validity_radius_m": round(self.validity_radius_m, 1),
            "max_area_km2": round((2.0 * self.validity_radius_m / 1000.0) ** 2, 4),
            "binding_term": self.binding_term,
            "planimetric_error_bound_m": round(self.planimetric_error_bound_m, 4),
            "within_region_cap": self.within_region_cap,
            "estimated_bytes": self.estimated_bytes,
            "max_response_bytes": self.max_response_bytes,
            "estimate_basis": (
                f"mean {REFERENCE_BYTES_PER_KM2} B/km² measured on a reference Overpass "
                "extract of this query shape; the cap is enforced on the bytes that arrive, "
                "not on this estimate"
            ),
        }


def measure_region(bbox: BoundingBox) -> RegionMeasurement:
    """Measure a region against cap 1, with no network call and no side effects.

    The projection is built about the region's OWN centre, exactly as
    ``draft.origin_for_bounds`` builds it about the extract's centre, so the
    radius quoted here is the radius the draft will hold the data to.
    """
    lat0, lon0 = bbox.centre
    plane = LocalTangentPlane(lat0, lon0)
    corners = [
        plane.forward(bbox.south, bbox.west),
        plane.forward(bbox.south, bbox.east),
        plane.forward(bbox.north, bbox.west),
        plane.forward(bbox.north, bbox.east),
    ]
    xs = [x for x, _ in corners]
    ys = [y for _, y in corners]
    width_m = max(xs) - min(xs)
    height_m = max(ys) - min(ys)
    half_extent_m = max(max(abs(x), abs(y)) for x, y in corners)
    area_km2 = (width_m / 1000.0) * (height_m / 1000.0)
    radius = plane.validity_radius_m
    return RegionMeasurement(
        bbox=bbox,
        centre_lat=lat0,
        centre_lon=lon0,
        width_m=width_m,
        height_m=height_m,
        half_extent_m=half_extent_m,
        area_km2=area_km2,
        validity_radius_m=radius,
        binding_term=plane.binding_term,
        planimetric_error_bound_m=plane.planimetric_error_bound_m(half_extent_m),
        within_region_cap=half_extent_m <= radius,
        # PEAK, not mean -- see RegionMeasurement's docstring and the Daejeon
        # counter-example. An estimate that is 20x light is worse than none.
        estimated_bytes=int(area_km2 * PEAK_BYTES_PER_KM2),
        max_response_bytes=MAX_RESPONSE_BYTES,
    )


def region_limits(lat: float = 0.0, lon: float = 0.0) -> dict[str, Any]:
    """The caps in force at one latitude, for a UI that has not drawn a box yet."""
    plane = LocalTangentPlane(lat, lon)
    radius = plane.validity_radius_m
    return {
        "centre_lat": lat,
        "centre_lon": lon,
        "validity_radius_m": round(radius, 1),
        "max_area_km2": round((2.0 * radius / 1000.0) ** 2, 4),
        "binding_term": plane.binding_term,
        "planimetric_budget_m": plane.planimetric_error_bound_m(radius),
        "max_response_bytes": MAX_RESPONSE_BYTES,
        "reference_bytes_per_km2": REFERENCE_BYTES_PER_KM2,
        "peak_bytes_per_km2": PEAK_BYTES_PER_KM2,
        "endpoint": _endpoint_for_display(),
        "query_timeout_s": OVERPASS_QUERY_TIMEOUT_S,
        "socket_timeout_s": SOCKET_TIMEOUT_S,
    }


def _endpoint_for_display() -> str:
    """The configured endpoint, or the refusal's own text if it is malformed —
    a limits call must be able to SAY the configuration is broken rather than
    500 on it."""
    try:
        return overpass_endpoint()
    except OsmFetchRefused as exc:
        return f"<misconfigured: {'; '.join(exc.reasons)}>"


def _assert_within_region_cap(measurement: RegionMeasurement) -> None:
    if measurement.within_region_cap:
        return
    raise OsmFetchRefused(
        "region_too_large",
        [
            f"This region's half-extent is {measurement.half_extent_m:.0f} m, past the "
            f"{measurement.validity_radius_m:.0f} m limit at latitude "
            f"{measurement.centre_lat:.4f} (binding term: {measurement.binding_term}). "
            f"The generator's projection would refuse it as projection_out_of_range "
            f"(planimetric error {measurement.planimetric_error_bound_m:.2f} m against a "
            "1.00 m budget), so it is refused before the download rather than after. "
            f"Select a region of at most {(2.0 * measurement.validity_radius_m / 1000.0) ** 2:.1f} km² "
            "here, or split it into two."
        ],
        detail=measurement.as_dict(),
    )


# ---------------------------------------------------------------------------
# The query
# ---------------------------------------------------------------------------


def build_query(bbox: BoundingBox, *, timeout_s: int = OVERPASS_QUERY_TIMEOUT_S) -> str:
    """The exact Overpass QL sent, and recorded verbatim in the provenance.

    Two element families, and no more:

    * every ``highway=*`` way in the box — the whole vocabulary
      ``services/genesis/osm.py`` classifies, INCLUDING the ones it will skip by
      name (footway, path, steps). Filtering them out here would move a decision
      the generator documents into an invisible layer, and its compliance report
      would then be silent about ways nobody ever offered it;
    * the ``type=restriction`` relations attached to those ways, which
      ``osm.py`` reads to leave banned turns out of the network.

    ``out body`` twice, never ``out skel``: the recursed nodes keep their tags.
    The parser reads only id/lat/lon from a node today, but an extract fetched
    by this module must not be a POORER document than one a user downloads by
    hand — a later unit that starts reading ``highway=traffic_signals`` would
    otherwise find it silently missing from every region we fetched.
    ``out meta`` is deliberately not used: versions and changeset ids would
    inflate the response without the generator reading one of them.
    """
    return (
        f"[out:xml][timeout:{int(timeout_s)}];\n"
        f"way[\"highway\"]({bbox.south:.7f},{bbox.west:.7f},{bbox.north:.7f},{bbox.east:.7f})->.roads;\n"
        "(\n"
        "  .roads;\n"
        "  rel(bw.roads)[\"type\"=\"restriction\"];\n"
        ");\n"
        "out body;\n"
        ">;\n"
        "out body qt;\n"
    )


# ---------------------------------------------------------------------------
# The one outbound call
# ---------------------------------------------------------------------------


@dataclass
class FetchedOsm:
    """What came back, plus everything needed to reconstruct how it was asked for."""

    content: bytes
    provenance: dict[str, Any]
    way_count: int = 0
    node_count: int = 0
    annotated: bytes = field(default=b"", repr=False)


def _open_url(request: urllib.request.Request, timeout: float):
    """The single seam through which this module reaches the network.

    Exists so the tests can fixture an Overpass response without a socket:
    ``tests/test_osm_fetch.py`` monkeypatches THIS name, and pins that it is the
    only ``urlopen`` call site in the module. A test that needs the internet is
    a test that fails in CI.
    """
    return urllib.request.urlopen(request, timeout=timeout)  # noqa: S310 - endpoint is server config


def _read_capped(response: Any, *, cap: int) -> bytes:
    """Read up to ``cap`` bytes, then REFUSE — never truncate, never buffer more.

    The refusal carries what was read and (when the server declared one) the
    Content-Length, so "too large" comes with the number that made it too large.
    """
    declared: int | None = None
    try:
        raw_length = response.headers.get("Content-Length")
        declared = int(raw_length) if raw_length else None
    except (AttributeError, TypeError, ValueError):
        declared = None
    if declared is not None and declared > cap:
        raise OsmFetchRefused(
            "region_response_too_large",
            [
                f"The Overpass instance declared a {declared:,}-byte response, past this "
                f"app's {cap:,}-byte limit. Nothing was downloaded. Select a smaller region."
            ],
            detail={"declared_bytes": declared, "max_response_bytes": cap},
        )

    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = response.read(_READ_CHUNK_BYTES)
        if not chunk:
            break
        total += len(chunk)
        if total > cap:
            raise OsmFetchRefused(
                "region_response_too_large",
                [
                    f"The Overpass response passed this app's {cap:,}-byte limit "
                    f"(at least {total:,} bytes). The download was stopped and nothing was "
                    "staged — a truncated extract is a network with roads missing. "
                    "Select a smaller region."
                ],
                detail={"read_bytes": total, "max_response_bytes": cap},
            )
        chunks.append(chunk)
    return b"".join(chunks)


def _scan_osm(content: bytes) -> dict[str, Any]:
    """One streaming pass for everything the refusals and the provenance need.

    Returns the root element's tag and ``generator``, the ODbL ``<note>``, the
    ``<meta osm_base>`` timestamp, any ``<remark>`` (Overpass's in-band error
    channel) and the way/node counts. Returns ``root_tag=None`` when the bytes
    are not XML at all.
    """
    found: dict[str, Any] = {
        "root_tag": None,
        "generator": None,
        "note": None,
        "osm_base": None,
        "remark": None,
        "ways": 0,
        "nodes": 0,
    }
    root: ET.Element | None = None
    since_sweep = 0
    try:
        for event, element in ET.iterparse(BytesIO(content), events=("start", "end")):
            tag = str(element.tag).rsplit("}", 1)[-1]
            if event == "start":
                if found["root_tag"] is None:
                    found["root_tag"] = tag
                    found["generator"] = element.get("generator")
                    root = element
                continue
            if tag == "note" and found["note"] is None:
                found["note"] = (element.text or "").strip() or None
            elif tag == "meta" and found["osm_base"] is None:
                found["osm_base"] = element.get("osm_base")
            elif tag == "remark" and found["remark"] is None:
                found["remark"] = (element.text or "").strip() or None
            elif tag in ("way", "node"):
                found["ways" if tag == "way" else "nodes"] += 1
                element.clear()
                # Elements the parser has finished with are still children of
                # the root, so clearing them alone does not bound memory. Drop
                # the accumulated siblings periodically — the standard iterparse
                # idiom — so a cap-sized response peaks at its own bytes rather
                # than at a whole DOM built on top of them. Only elements
                # ALREADY seen are dropped, so a <remark> further down the
                # stream is still found.
                since_sweep += 1
                if root is not None and since_sweep >= 5000:
                    del root[:]
                    since_sweep = 0
    except ET.ParseError as exc:
        found["parse_error"] = f"{type(exc).__name__}: {exc}"
    return found


def fetch_region(
    bbox: BoundingBox,
    *,
    measurement: RegionMeasurement | None = None,
) -> FetchedOsm:
    """Ask Overpass for this region and return the extract, or refuse by name.

    Runs on a worker thread (see :func:`run_region_fetch`); nothing in a request
    handler ever calls it.
    """
    measurement = measurement or measure_region(bbox)
    _assert_within_region_cap(measurement)

    endpoint = overpass_endpoint()
    query = build_query(bbox)
    body = query.encode("utf-8")
    requested_at = _now_iso()
    started = time.monotonic()

    content: bytes | None = None
    attempts = 0
    last_transient: OsmFetchRefused | None = None
    while attempts < MAX_ATTEMPTS and content is None:
        attempts += 1
        try:
            request = urllib.request.Request(
                endpoint,
                data=body,
                headers={
                    "Content-Type": "text/plain; charset=utf-8",
                    "Accept": "application/xml",
                    "User-Agent": USER_AGENT,
                },
                method="POST",
            )
            with _open_url(request, SOCKET_TIMEOUT_S) as response:
                content = _read_capped(response, cap=MAX_RESPONSE_BYTES)
        except OsmFetchRefused:
            # A cap refusal is a verdict about the region, not a transient
            # failure. Retrying it would ask for the same bytes again.
            raise
        except urllib.error.HTTPError as exc:
            status = int(getattr(exc, "code", 0) or 0)
            if status == RATE_LIMITED_STATUS:
                retry_after = None
                try:
                    retry_after = exc.headers.get("Retry-After")
                except AttributeError:  # pragma: no cover - header-less error
                    retry_after = None
                raise OsmFetchRefused(
                    "overpass_rate_limited",
                    [
                        f"{endpoint} answered HTTP 429 (rate limited)"
                        + (f"; it asked us to wait {retry_after} s." if retry_after else ".")
                        + " Nothing was retried — an immediate second request is what gets an "
                        "instance to block a whole network. Try again shortly, or point "
                        f"{OVERPASS_ENDPOINT_ENV} at your own Overpass instance."
                    ],
                    detail={"endpoint": endpoint, "status": status, "retry_after": retry_after},
                ) from exc
            transient = status in RETRYABLE_STATUSES
            refusal = OsmFetchRefused(
                "overpass_unavailable",
                [
                    f"{endpoint} answered HTTP {status} "
                    f"({getattr(exc, 'reason', '') or 'no reason given'}) "
                    f"after {attempts} attempt(s)."
                ],
                detail={"endpoint": endpoint, "status": status, "attempts": attempts},
            )
            if not transient or attempts >= MAX_ATTEMPTS:
                raise refusal from exc
            last_transient = refusal
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            code = "overpass_timeout" if isinstance(reason, TimeoutError) or isinstance(exc, TimeoutError) else "overpass_unreachable"
            refusal = OsmFetchRefused(
                code,
                [
                    f"{endpoint} could not be reached after {attempts} attempt(s) "
                    f"({type(exc).__name__}: {reason}). The socket budget is "
                    f"{SOCKET_TIMEOUT_S} s ({OVERPASS_QUERY_TIMEOUT_S} s query + "
                    f"{TRANSFER_BUDGET_S} s transfer)."
                ],
                detail={"endpoint": endpoint, "attempts": attempts},
            )
            if attempts >= MAX_ATTEMPTS:
                raise refusal from exc
            last_transient = refusal
        if content is None and attempts < MAX_ATTEMPTS:
            time.sleep(RETRY_DELAY_S)

    if content is None:  # pragma: no cover - the loop raises on its last attempt
        raise last_transient or OsmFetchRefused(
            "overpass_unreachable", [f"{endpoint} returned nothing."]
        )

    elapsed_ms = int((time.monotonic() - started) * 1000)
    scan = _scan_osm(content)

    if scan.get("remark"):
        # HTTP 200 with a server-side failure in the body. This parses, and it
        # would sail through stage_osm's root-element check and become an empty
        # network several steps later with nobody able to say why.
        raise OsmFetchRefused(
            "overpass_query_failed",
            [
                f"{endpoint} returned HTTP 200 but reported a query failure in the document: "
                f"{scan['remark']}"
            ],
            detail={"endpoint": endpoint, "remark": scan["remark"]},
        )
    if scan.get("root_tag") != "osm":
        raise OsmFetchRefused(
            "overpass_bad_response",
            [
                f"{endpoint} did not return an OSM XML document"
                + (
                    f" (root element is <{scan['root_tag']}>)."
                    if scan.get("root_tag")
                    else f" ({scan.get('parse_error') or 'the body is not well-formed XML'})."
                )
            ],
            detail={"endpoint": endpoint, "bytes": len(content)},
        )
    if scan["ways"] == 0:
        raise OsmFetchRefused(
            "region_has_no_roads",
            [
                "This region contains no highway ways at all, so there is no network to "
                "build. Check the coordinates — a region over water, farmland or the wrong "
                "hemisphere looks exactly like this."
            ],
            detail={"endpoint": endpoint, "bytes": len(content), "region": bbox.as_dict()},
        )

    provenance = {
        "source": "overpass",
        "endpoint": endpoint,
        "query": query,
        "requested_region": bbox.as_dict(),
        "requested_at": requested_at,
        "fetched_at": _now_iso(),
        "elapsed_ms": elapsed_ms,
        "attempts": attempts,
        "bytes": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
        "user_agent": USER_AGENT,
        "socket_timeout_s": SOCKET_TIMEOUT_S,
        "query_timeout_s": OVERPASS_QUERY_TIMEOUT_S,
        "max_response_bytes": MAX_RESPONSE_BYTES,
        "overpass_generator": scan.get("generator"),
        "osm_base": scan.get("osm_base"),
        "ways": scan["ways"],
        "nodes": scan["nodes"],
        # The licence obligation, recorded as measured rather than asserted: the
        # response's own note is copied if it carried one, and its absence is
        # stated instead of being papered over with a note we wrote ourselves.
        "licence": "ODbL — https://www.openstreetmap.org/copyright",
        "attribution_note_present": bool(scan.get("note")),
        "attribution_note": scan.get("note"),
        "measurement": measurement.as_dict(),
    }
    return FetchedOsm(
        content=content,
        provenance=provenance,
        way_count=scan["ways"],
        node_count=scan["nodes"],
        annotated=annotate(content, provenance),
    )


# ---------------------------------------------------------------------------
# Provenance, written INTO the document
# ---------------------------------------------------------------------------

PROVENANCE_COMMENT_OPEN = "<!-- prism-osm-fetch"
PROVENANCE_COMMENT_CLOSE = "-->"

_XML_DECLARATION_RE = re.compile(rb"^\s*<\?xml[^>]*\?>[\r\n]*")


def annotate(content: bytes, provenance: dict[str, Any]) -> bytes:
    """Return ``content`` with the provenance as a leading XML comment.

    The body is copied through BYTE-FOR-BYTE — the ODbL ``<note>``, the
    ``<meta>``, every element — and only a comment is added, after the XML
    declaration if there is one. A comment is invisible to
    ``xml.etree.ElementTree.iterparse``, which is what both
    ``genesis_jobs._xml_root_tag`` and ``services/genesis/osm.py`` read the file
    with, so an annotated extract stages and parses exactly as the raw one does.

    ``--`` is illegal inside an XML comment and would make the document
    unparseable, so the serialized JSON is escaped for it rather than trusted.
    """
    payload = json.dumps(provenance, ensure_ascii=False, indent=2, sort_keys=True)
    payload = payload.replace("--", "––")
    comment = (
        f"{PROVENANCE_COMMENT_OPEN}\n"
        "This extract was fetched by Prism from the Overpass instance named below.\n"
        "The OpenStreetMap data it contains is licensed under the ODbL "
        "(https://www.openstreetmap.org/copyright).\n"
        f"{payload}\n"
        f"{PROVENANCE_COMMENT_CLOSE}\n"
    ).encode("utf-8")

    match = _XML_DECLARATION_RE.match(content)
    if match:
        return content[: match.end()] + comment + content[match.end() :]
    return comment + content


def read_provenance(content: bytes) -> dict[str, Any] | None:
    """The provenance back out of an annotated document, or ``None``.

    The inverse of :func:`annotate`, so a reader holding only the ``.osm`` — the
    job's ``source.osm``, an artifact download — can recover where it came from
    without this app's help.
    """
    text = content.decode("utf-8", errors="replace")
    start = text.find(PROVENANCE_COMMENT_OPEN)
    if start == -1:
        return None
    end = text.find(PROVENANCE_COMMENT_CLOSE, start)
    if end == -1:
        return None
    block = text[start + len(PROVENANCE_COMMENT_OPEN) : end]
    brace = block.find("{")
    if brace == -1:
        return None
    try:
        return json.loads(block[brace:].replace("––", "--"))
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Records — one JSON file per fetch, under the workspace's own tree
# ---------------------------------------------------------------------------


def regions_dir() -> Path:
    """``<current workspace>/genesis/regions``. Beside the genesis jobs, under
    the same workspace root, so two projects cannot see each other's fetches
    physically rather than by filter."""
    return genesis_jobs.genesis_dir() / REGIONS_DIRNAME


def fetch_dir(fetch_id: str) -> Path:
    """The directory for ``fetch_id``, validated twice — regex, then
    resolve()+relative_to — exactly as ``genesis_jobs.job_dir`` is."""
    if not fetch_id or not _ID_RE.match(fetch_id):
        raise OsmFetchNotFoundError(f"Unknown region fetch: {fetch_id!r}")
    root = regions_dir()
    root.mkdir(parents=True, exist_ok=True)
    resolved_root = root.resolve()
    target = (resolved_root / fetch_id).resolve()
    try:
        target.relative_to(resolved_root)
    except ValueError:  # pragma: no cover - the regex rules this out
        raise OsmFetchNotFoundError(f"Unknown region fetch: {fetch_id!r}") from None
    return target


def _write_record(record: dict[str, Any]) -> dict[str, Any]:
    """Atomically replace ONE fetch's file. No lock: one record per file means
    no read-modify-write, so there is no window two writers could interleave in."""
    target = fetch_dir(str(record["id"])) / RECORD_FILENAME
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f"{RECORD_FILENAME}.{os.getpid()}.{uuid4().hex[:8]}.tmp")
    serialized = json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True)
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:  # pragma: no cover - inert leftover
            logger.warning("osm_fetch: could not remove temp record %s", tmp)
        raise
    return record


def get_fetch(fetch_id: str) -> dict[str, Any]:
    path = fetch_dir(fetch_id) / RECORD_FILENAME
    if not path.is_file():
        raise OsmFetchNotFoundError(f"Unknown region fetch: {fetch_id!r}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise OsmFetchError(
            f"The region fetch record at {path} could not be read ({type(exc).__name__}: {exc}). "
            "Nothing will be written over it."
        ) from exc


def list_fetches() -> list[dict[str, Any]]:
    """This workspace's region fetches, newest first. An unreadable record is
    logged and skipped rather than 500-ing a listing — the same call every other
    list view in this app makes."""
    root = regions_dir()
    if not root.is_dir():
        return []
    records: list[dict[str, Any]] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        path = child / RECORD_FILENAME
        if not path.is_file():
            continue
        try:
            records.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError) as exc:
            logger.error("osm_fetch: unreadable record %s (%s)", path, exc)
    return sorted(records, key=lambda item: str(item.get("created_at") or ""), reverse=True)


def _update(fetch_id: str, **updates: Any) -> dict[str, Any]:
    record = get_fetch(fetch_id)
    record.update(updates)
    if "phase" in updates and "progress" not in updates:
        record["progress"] = _PHASE_PROGRESS.get(str(updates["phase"]), record.get("progress", 0.0))
    if updates.get("status") in TERMINAL_STATUSES:
        record["completed_at"] = _now_iso()
    if updates.get("status") == STATUS_RUNNING and not record.get("started_at"):
        record["started_at"] = _now_iso()
    return _write_record(record)


# ---------------------------------------------------------------------------
# Submitting and running a fetch
# ---------------------------------------------------------------------------


def _resolve_name(name: str | None, bbox: BoundingBox) -> str:
    """The staged file's stem, which becomes the generated network's name.

    A caller's name is VALIDATED rather than rewritten (``genesis_jobs``'
    rule — silently renaming produces files nobody can find). With no name, one
    is derived from the region's own centre, so two fetches of different places
    never collide and the file says where it is from.
    """
    if name is not None and name.strip():
        cleaned = name.strip()
        if not _NAME_RE.match(cleaned):
            raise OsmFetchRefused(
                "region_bad_name",
                [
                    f"{name!r} is not a usable region name. Use 1–64 characters from "
                    "A–Z, a–z, 0–9, '.', '_' and '-', starting with a letter or digit."
                ],
            )
        return cleaned
    lat, lon = bbox.centre
    return "region_{0}{1:.4f}_{2}{3:.4f}".format(
        "s" if lat < 0 else "n", abs(lat), "w" if lon < 0 else "e", abs(lon)
    ).replace(".", "_")


def create_region_fetch(
    *,
    bbox: BoundingBox,
    name: str | None = None,
    submitted_by: str | None = None,
) -> dict[str, Any]:
    """Register a fetch WITHOUT running it.

    The workspace is captured HERE, on the request thread, and stored on the
    record: ContextVars do not cross ``threading.Thread`` boundaries and
    ``current_workspace_id()`` raises rather than falling back to somebody
    else's project. Same freeze-at-enqueue discipline as
    ``genesis_jobs.create_genesis_job``.

    Cap 1 and the endpoint configuration are checked here too, not only inside
    the worker: something that can never succeed should be refused while the
    caller is still looking at the region, with a 400 rather than a job they
    have to poll in order to learn about it.
    """
    workspace_id = workspaces.current_workspace_id()
    measurement = measure_region(bbox)
    _assert_within_region_cap(measurement)
    endpoint = overpass_endpoint()
    resolved_name = _resolve_name(name, bbox)

    fetch_id = uuid4().hex[:12]
    record = {
        "id": fetch_id,
        "workspace_id": workspace_id,
        "name": resolved_name,
        "status": STATUS_QUEUED,
        "phase": PHASE_QUEUED,
        "progress": 0.0,
        "created_at": _now_iso(),
        "started_at": None,
        "completed_at": None,
        "submitted_by": submitted_by,
        "region": bbox.as_dict(),
        "measurement": measurement.as_dict(),
        "endpoint": endpoint,
        # Filled in by the worker.
        "upload_token": None,
        "filename": None,
        "bytes": None,
        "provenance": None,
        "refusal": None,
        "error": None,
    }
    fetch_dir(fetch_id).mkdir(parents=True, exist_ok=True)
    return _write_record(record)


def start_region_fetch(**kwargs: Any) -> dict[str, Any]:
    """:func:`create_region_fetch`, then run it on a daemon worker thread.

    The request handler returns the accepted record immediately and the client
    polls. The house pattern, and here it is not optional: this is the one code
    path in the backend that waits on the public internet.
    """
    record = create_region_fetch(**kwargs)
    threading.Thread(
        target=run_region_fetch,
        args=(record["id"], record["workspace_id"]),
        daemon=True,
    ).start()
    return record


def run_region_fetch(fetch_id: str, workspace_id: str) -> dict[str, Any]:
    """The worker body, workspace re-bound explicitly.

    Public and synchronous so tests (and any future queue) can drive one fetch
    end to end without a thread.
    """
    with workspaces.bind_workspace(workspace_id):
        return _run_region_fetch_locked(fetch_id)


def _run_region_fetch_locked(fetch_id: str) -> dict[str, Any]:
    record = get_fetch(fetch_id)
    try:
        _update(fetch_id, status=STATUS_RUNNING, phase=PHASE_FETCHING)
        bbox = BoundingBox.from_mapping(record.get("region"))
        fetched = fetch_region(bbox)

        _update(fetch_id, phase=PHASE_STAGING)
        # THE hand-off. `stage_osm` is the same function POST /api/genesis/uploads
        # calls, and the token it returns is the same token — so from here the
        # extract takes the road B1 already proved, byte for byte.
        staged = genesis_jobs.stage_osm(f"{record['name']}.osm", fetched.annotated)
        return _update(
            fetch_id,
            status=STATUS_STAGED,
            phase=PHASE_DONE,
            progress=1.0,
            upload_token=staged["token"],
            filename=staged["filename"],
            bytes=staged["bytes"],
            provenance=fetched.provenance,
            refusal=None,
            error=None,
        )
    except OsmFetchRefused as exc:
        logger.info("osm_fetch: fetch %s refused (%s)", fetch_id, exc.code)
        return _update(
            fetch_id,
            status=STATUS_REFUSED,
            phase=PHASE_DONE,
            progress=1.0,
            refusal={"code": exc.code, "reasons": exc.reasons, "detail": exc.detail},
            error=str(exc),
        )
    except genesis_jobs.GenesisRefused as exc:
        # The staging door refused the bytes (not a .osm, empty). Reported with
        # ITS code rather than reshaped into one of ours, so the two roads into
        # `stage_osm` refuse in the same words.
        logger.info("osm_fetch: fetch %s could not be staged (%s)", fetch_id, exc.code)
        return _update(
            fetch_id,
            status=STATUS_REFUSED,
            phase=PHASE_DONE,
            progress=1.0,
            refusal={"code": exc.code, "reasons": exc.reasons, "detail": {}},
            error=str(exc),
        )
    except Exception as exc:  # noqa: BLE001 - a fetch failure is data, not a crash
        logger.exception("osm_fetch: fetch %s failed", fetch_id)
        return _update(
            fetch_id,
            status=STATUS_ERROR,
            phase=PHASE_DONE,
            progress=1.0,
            refusal={
                "code": "region_fetch_failed",
                "reasons": [f"{type(exc).__name__}: {exc}"],
                "detail": {},
            },
            error=f"{type(exc).__name__}: {exc}",
        )


def reconcile_interrupted_fetches() -> list[str]:
    """Terminate fetches a dead process left claiming to run. Called at boot.

    Same reasoning as ``genesis_jobs.reconcile_interrupted_jobs``: a fetch runs
    in a worker thread inside the API process, so a restart leaves records
    nothing will ever advance and a UI polling a bar that cannot move. They go
    to ``error``, never to ``staged`` — no extract was staged for them.
    """
    reconciled: list[str] = []
    try:
        entries = workspaces.list_workspaces()
    except Exception:  # noqa: BLE001 - a boot sweep never blocks startup
        logger.exception("osm_fetch: could not enumerate workspaces to reconcile fetches")
        return reconciled

    for entry in entries:
        workspace_id = str(entry.get("id") or "")
        if not workspace_id:
            continue
        try:
            with workspaces.bind_workspace(workspace_id):
                for record in list_fetches():
                    if record.get("status") in TERMINAL_STATUSES:
                        continue
                    fetch_id = str(record.get("id") or "")
                    if not fetch_id:
                        continue
                    _update(
                        fetch_id,
                        status=STATUS_ERROR,
                        phase=PHASE_DONE,
                        progress=1.0,
                        refusal={
                            "code": "interrupted_by_restart",
                            "reasons": [
                                "The process running this region fetch stopped before it "
                                "finished. Nothing was staged; start it again."
                            ],
                            "detail": {},
                        },
                        error="interrupted_by_restart",
                    )
                    reconciled.append(f"{workspace_id}:{fetch_id}")
        except Exception:  # noqa: BLE001 - one bad workspace must not stop the rest
            logger.exception("osm_fetch: could not reconcile fetches for workspace %s", workspace_id)
    if reconciled:
        logger.info("osm_fetch: reconciled %d interrupted fetch(es) at startup", len(reconciled))
    return reconciled


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


__all__ = [
    "ASSUMED_LINK_BITS_PER_S",
    "BoundingBox",
    "DEFAULT_OVERPASS_ENDPOINT",
    "FetchedOsm",
    "MAX_ATTEMPTS",
    "MAX_REGION_AREA_KM2",
    "MAX_REGION_HALF_EXTENT_M",
    "MAX_RESPONSE_BYTES",
    "OVERPASS_ENDPOINT_ENV",
    "OVERPASS_QUERY_TIMEOUT_S",
    "OsmFetchError",
    "OsmFetchNotFoundError",
    "OsmFetchRefused",
    "PEAK_BYTES_PER_KM2",
    "RATE_LIMITED_STATUS",
    "REFERENCE_BYTES_PER_KM2",
    "REFERENCE_PEAK_TO_MEAN_DENSITY",
    "RETRYABLE_STATUSES",
    "RegionMeasurement",
    "SOCKET_TIMEOUT_S",
    "STATUS_ERROR",
    "STATUS_QUEUED",
    "STATUS_REFUSED",
    "STATUS_RUNNING",
    "STATUS_STAGED",
    "TERMINAL_STATUSES",
    "TRANSFER_BUDGET_S",
    "annotate",
    "build_query",
    "create_region_fetch",
    "fetch_dir",
    "fetch_region",
    "get_fetch",
    "list_fetches",
    "measure_region",
    "overpass_endpoint",
    "read_provenance",
    "reconcile_interrupted_fetches",
    "region_limits",
    "regions_dir",
    "run_region_fetch",
    "start_region_fetch",
]
