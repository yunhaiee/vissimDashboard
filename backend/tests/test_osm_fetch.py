"""Roadmap v2 §5.B row B2 — the region picker becomes a real ``.osm`` extraction.

**No test in this file touches the network, and that is a property rather than a
habit.** ``services/osm_fetch.py`` reaches the internet through exactly one
function, :func:`services.osm_fetch._open_url`, and §0 below pins that as a fact
about the module's source. Every test here replaces that one seam with a
fixtured Overpass response, so the suite behaves identically on a build machine
with no route to overpass-api.de.

The unit's claim is two sentences, and the sections follow them:

* **§1–§2 The caps refuse; they never truncate.** The region cap is the draft
  projection's own validity radius (imported, not copied), and the byte cap is
  enforced DURING the download. A shortened extract is a network with roads
  missing, which is the silent-wrong-answer failure this product exists to
  prevent.
* **§5 A fetch ends at the staging door.** The last thing it does is call the
  same ``stage_osm`` a browser upload calls, and hand back the same token. There
  is no second road into ``gate_and_publish``, and §5 asserts that against the
  module's source as well as its behaviour.

Fixtures are the generator's own synthetic crossroads (``test_genesis_osm_draft``)
wrapped in the ``<note>``/``<meta>`` envelope a real Overpass instance returns.
No client geometry anywhere.
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import main
from conftest import admin_headers, bearer_headers
from routers.datasets import MAX_UPLOAD_BYTES
from services import genesis_jobs, osm_fetch, workspaces
from services.genesis.osm import parse_osm_file
from services.genesis.project import LocalTangentPlane

from tests.test_genesis_osm_draft import LAT0, LON0, crossroads_osm

OSM_FETCH_SOURCE = Path(osm_fetch.__file__).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Fixtures — a fake Overpass, and a private workspace tree
# ---------------------------------------------------------------------------


@pytest.fixture()
def workspace_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A private workspaces root, with its default workspace bound.

    ``LEGACY_UPLOADS_ROOT`` is repointed alongside it for the reason every other
    workspace test states: patching only the first would aim the one-time
    migration at the developer's real ``runtime/uploads``.
    """
    monkeypatch.setattr(workspaces, "WORKSPACES_ROOT", tmp_path / "workspaces")
    monkeypatch.setattr(workspaces, "LEGACY_UPLOADS_ROOT", tmp_path / "legacy-uploads")
    default_id = workspaces.default_workspace_id()
    token = workspaces.set_current_workspace_id(default_id)
    try:
        yield tmp_path / "workspaces"
    finally:
        workspaces.reset_current_workspace_id(token)


@pytest.fixture()
def client(workspace_root: Path) -> TestClient:
    return TestClient(main.app)


@pytest.fixture(autouse=True)
def no_retry_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """The retry pause is real in production and pointless in a test: every
    retry test below asserts on ATTEMPTS, never on elapsed time."""
    monkeypatch.setattr(osm_fetch, "RETRY_DELAY_S", 0.0)


#: A small region well inside every cap. Centred on the synthetic crossroads so
#: the coordinates in the fixture and the coordinates in the request agree.
SMALL_REGION = osm_fetch.BoundingBox(
    south=LAT0 - 0.004, west=LON0 - 0.004, north=LAT0 + 0.004, east=LON0 + 0.004
)

OVERPASS_NOTE = (
    "The data included in this document is from www.openstreetmap.org. "
    "The data is made available under ODbL."
)


def overpass_body(inner: str | None = None, *, note: str | None = OVERPASS_NOTE) -> bytes:
    """One Overpass answer, in the envelope a real instance returns.

    The payload is the generator's OWN synthetic crossroads, so an extract that
    comes back through this module is provably the same kind of document the
    upload road already carries.
    """
    text = crossroads_osm() if inner is None else inner
    head, rest = text.split("\n", 1)  # the XML declaration
    root_open, body = rest.split("\n", 1)
    parts = [head, root_open]
    if note is not None:
        parts.append(f"<note>{note}</note>")
    parts.append('<meta osm_base="2026-08-11T00:00:00Z"/>')
    parts.append(body.rstrip("\n"))
    return ("\n".join(parts) + "\n").encode("utf-8")


class FakeResponse:
    """Just enough of an ``http.client.HTTPResponse`` for :func:`_read_capped`.

    ``chunks`` are served one ``read()`` at a time so a test can assert the
    download STOPPED rather than merely that the result was rejected.
    """

    def __init__(self, chunks: list[bytes], headers: dict[str, str] | None = None) -> None:
        self._chunks = list(chunks)
        self.headers = headers or {}
        self.reads = 0

    def read(self, _size: int = -1) -> bytes:
        self.reads += 1
        return self._chunks.pop(0) if self._chunks else b""

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_exc: Any) -> bool:
        return False


class FakeOverpass:
    """A scripted stand-in for the one outbound call.

    Each entry is either a :class:`FakeResponse` factory or an exception to
    raise; ``requests`` records what was actually sent, which is how the query
    and the User-Agent get asserted without a socket.
    """

    def __init__(self, *script: Any) -> None:
        self.script = list(script)
        self.requests: list[Any] = []
        self.timeouts: list[float] = []

    def __call__(self, request: Any, timeout: float) -> Any:
        self.requests.append(request)
        self.timeouts.append(timeout)
        step = self.script.pop(0) if self.script else self.script
        if isinstance(step, BaseException):
            raise step
        if callable(step):
            return step()
        return step

    @property
    def attempts(self) -> int:
        return len(self.requests)


def install(monkeypatch: pytest.MonkeyPatch, fake: FakeOverpass) -> FakeOverpass:
    monkeypatch.setattr(osm_fetch, "_open_url", fake)
    return fake


def ok_response(body: bytes | None = None, **headers: str) -> FakeResponse:
    payload = overpass_body() if body is None else body
    return FakeResponse([payload], headers or {"Content-Length": str(len(payload))})


def http_error(status: int, headers: dict[str, str] | None = None) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        osm_fetch.DEFAULT_OVERPASS_ENDPOINT, status, "scripted", headers or {}, None
    )


# ---------------------------------------------------------------------------
# §0 The offline guarantee, as a property of the source
# ---------------------------------------------------------------------------


def test_the_module_reaches_the_network_through_exactly_one_call_site():
    """One seam, so a test can replace it — and so a reviewer can find every
    outbound byte by reading one function.

    ``urlopen`` must appear exactly twice in the module: the import and ONE
    call, inside ``_open_url``. A second call site would be a request this
    file's tests do not intercept, i.e. a suite that starts needing the
    internet without anybody deciding that it should.
    """
    call_sites = re.findall(r"urllib\.request\.urlopen\(", OSM_FETCH_SOURCE)
    assert len(call_sites) == 1, "expected exactly one urlopen() call site"

    seam = OSM_FETCH_SOURCE[OSM_FETCH_SOURCE.index("def _open_url") :]
    seam = seam[: seam.index("\ndef ", 1)]
    assert "urllib.request.urlopen(" in seam, "the one call site must live in _open_url"


def test_nothing_here_can_publish_a_dataset():
    """B2 extends B1's pipeline; it does not grow a rival.

    The whole point of A4 is that ``gate_and_publish`` is the ONLY road from a
    generated artifact to a project's dataset. A fetch module that could call it
    — or ``datasets.save_active_dataset`` — would be a second road, and a region
    could reach a consultant's project without the gate ever seeing it.

    Asserted over the module's parsed NAMES rather than its text: the docstring
    has to be able to say the word ``gate_and_publish`` in order to explain why
    it is absent, and a substring search cannot tell an explanation from a call.
    """
    import ast

    tree = ast.parse(OSM_FETCH_SOURCE)
    names = {
        node.attr if isinstance(node, ast.Attribute) else node.id
        for node in ast.walk(tree)
        if isinstance(node, (ast.Attribute, ast.Name))
    }
    for forbidden in (
        "gate_and_publish",
        "save_active_dataset",
        "start_genesis_job",
        "create_genesis_job",
        "_register_as_active_dataset",
    ):
        assert forbidden not in names, (
            f"osm_fetch must not reference {forbidden}: a region fetch ends at the "
            "staging door, and generation is started by the caller through B1's own route"
        )
    assert "stage_osm" in names, "the hand-off is stage_osm and nothing else"


# ---------------------------------------------------------------------------
# §1 Cap 1 — the region, derived rather than chosen
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("lat", [0.0, 14.04, -32.07, 36.35, 60.0])
def test_the_region_cap_is_the_draft_projections_own_validity_radius(lat: float):
    """Not a number that looked about right: the same radius
    ``services/genesis/draft.py`` already refuses past.

    Recomputed here from ``LocalTangentPlane`` rather than quoted, so a change
    to the projection's error budget moves the fetch cap with it instead of
    leaving the two silently disagreeing.
    """
    limits = osm_fetch.region_limits(lat, 0.0)
    plane = LocalTangentPlane(lat, 0.0)
    assert limits["validity_radius_m"] == pytest.approx(plane.validity_radius_m, abs=0.05)
    assert limits["binding_term"] == plane.binding_term


def test_a_region_one_step_past_the_radius_is_refused_and_one_inside_is_not(
    monkeypatch: pytest.MonkeyPatch,
):
    """The cap is enforced, and it is enforced at the right place."""
    plane = LocalTangentPlane(0.0, 0.0)
    radius = plane.validity_radius_m
    # Half-extent is measured north-south, where a degree of latitude is a fixed
    # number of metres, so the box below is exactly `radius ± 50 m` tall.
    metres_per_degree = plane.meridional_radius_m * 3.141592653589793 / 180.0

    inside = osm_fetch.measure_region(
        osm_fetch.BoundingBox(
            south=-(radius - 50.0) / metres_per_degree,
            north=(radius - 50.0) / metres_per_degree,
            west=-0.001,
            east=0.001,
        )
    )
    outside = osm_fetch.measure_region(
        osm_fetch.BoundingBox(
            south=-(radius + 50.0) / metres_per_degree,
            north=(radius + 50.0) / metres_per_degree,
            west=-0.001,
            east=0.001,
        )
    )
    assert inside.within_region_cap is True
    assert outside.within_region_cap is False


def test_an_oversized_region_is_refused_with_its_numbers_and_never_downloaded(
    workspace_root: Path, monkeypatch: pytest.MonkeyPatch
):
    """A cap that downloads first and complains afterwards is not a cap.

    The seam is replaced with something that FAILS the test if it is called, so
    "nothing was downloaded" is proven rather than assumed.
    """

    def explode(*_args: Any, **_kwargs: Any):
        raise AssertionError("an over-large region must never reach the network")

    monkeypatch.setattr(osm_fetch, "_open_url", explode)

    with pytest.raises(osm_fetch.OsmFetchRefused) as caught:
        osm_fetch.create_region_fetch(
            bbox=osm_fetch.BoundingBox(south=36.0, west=127.0, north=36.4, east=127.4)
        )
    refusal = caught.value
    assert refusal.code == "region_too_large"
    # The refusal carries the measurement, not just an adjective.
    assert refusal.detail["half_extent_m"] > refusal.detail["validity_radius_m"]
    assert refusal.detail["binding_term"] in {"meridian_convergence", "tangent_plane_curvature"}
    assert "km²" in refusal.reasons[0]


def test_the_fetch_itself_holds_the_cap_and_not_only_the_submission_path(
    monkeypatch: pytest.MonkeyPatch,
):
    """``fetch_region`` is public and must not rely on somebody else having
    checked.

    ``create_region_fetch`` checks cap 1 too, so that a hopeless region is a 400
    the caller sees immediately rather than a job to poll. That is a courtesy,
    not the enforcement — a future queue, a CLI or a retry that reached
    ``fetch_region`` directly would otherwise sail past a limit nobody re-tested.
    """

    def explode(*_args: Any, **_kwargs: Any):
        raise AssertionError("an over-large region must never reach the network")

    monkeypatch.setattr(osm_fetch, "_open_url", explode)

    with pytest.raises(osm_fetch.OsmFetchRefused) as caught:
        osm_fetch.fetch_region(
            osm_fetch.BoundingBox(south=36.0, west=127.0, north=36.4, east=127.4)
        )
    assert caught.value.code == "region_too_large"


def test_a_region_is_refused_before_it_becomes_a_job_the_caller_must_poll(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    """400 now beats 202-then-poll-to-find-out-it-was-hopeless."""
    install(monkeypatch, FakeOverpass())
    response = client.post(
        "/api/genesis/region/fetches",
        json={"south": 36.0, "west": 127.0, "north": 36.4, "east": 127.4},
        headers=admin_headers(),
    )
    assert response.status_code == 400, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "region_too_large"
    assert detail["measurement"]["validity_radius_m"] > 0


@pytest.mark.parametrize(
    "kwargs",
    [
        {"south": 1.0, "west": 0.0, "north": 0.0, "east": 1.0},   # inverted latitude
        {"south": 0.0, "west": 1.0, "north": 1.0, "east": 0.0},   # antimeridian wrap
        {"south": 0.0, "west": 0.0, "north": 0.0, "east": 1.0},   # zero height
        {"south": -91.0, "west": 0.0, "north": 1.0, "east": 1.0},  # off the globe
    ],
)
def test_a_malformed_box_is_refused_at_construction(kwargs: dict[str, float]):
    """Including the antimeridian, which is REFUSED rather than wrapped: the
    fetch, the projection origin and the area would each need a convention none
    of them has, and guessing one turns a region into the planet minus itself."""
    with pytest.raises(osm_fetch.OsmFetchRefused) as caught:
        osm_fetch.BoundingBox(**kwargs)
    assert caught.value.code == "region_bbox_invalid"


def test_the_preview_describes_an_over_large_region_instead_of_refusing_to_answer(
    client: TestClient,
):
    """A preview whose entire job is to describe a too-large box must be able to
    describe a too-large box — 200 with ``within_region_cap: false``, not 400."""
    response = client.get(
        "/api/genesis/region/preview",
        params={"south": 36.0, "west": 127.0, "north": 36.4, "east": 127.4},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["within_region_cap"] is False
    assert payload["area_km2"] > payload["max_area_km2"]
    assert payload["estimated_bytes"] > 0
    assert "cap is enforced on the bytes that arrive" in payload["estimate_basis"]


# ---------------------------------------------------------------------------
# §2 Cap 2 — the response, and its two named terms
# ---------------------------------------------------------------------------


def test_the_response_cap_never_exceeds_the_upload_ceiling():
    """Term A of cap 2: the other road into ``stage_osm``.

    ``routers/datasets.MAX_UPLOAD_BYTES`` is what a browser upload may carry.
    Fetching more than that would let the two roads into one staging area
    disagree about what fits. Imported HERE rather than in the service, because
    a service importing a router is a layering inversion — the relationship is
    pinned instead of encoded.
    """
    assert osm_fetch.MAX_RESPONSE_BYTES <= MAX_UPLOAD_BYTES
    # Stated in the module docstring as "term B binds by ~15×". If a future
    # change ever inverted that, the docstring's derivation would be wrong and
    # this is where it is caught.
    assert osm_fetch.MAX_RESPONSE_BYTES < MAX_UPLOAD_BYTES // 10


def test_the_response_cap_is_the_measured_peak_density_over_the_largest_region():
    """Term B, recomputed from its own factors so it cannot drift into a literal."""
    assert osm_fetch.MAX_REGION_HALF_EXTENT_M == pytest.approx(
        LocalTangentPlane(0.0, 0.0).validity_radius_m
    )
    assert osm_fetch.MAX_REGION_AREA_KM2 == pytest.approx(
        (2 * osm_fetch.MAX_REGION_HALF_EXTENT_M / 1000.0) ** 2
    )
    assert osm_fetch.PEAK_BYTES_PER_KM2 == int(
        osm_fetch.REFERENCE_BYTES_PER_KM2 * osm_fetch.REFERENCE_PEAK_TO_MEAN_DENSITY
    )
    assert osm_fetch.MAX_RESPONSE_BYTES == int(
        osm_fetch.PEAK_BYTES_PER_KM2 * osm_fetch.MAX_REGION_AREA_KM2
    )
    # A cap that a normal Korean study area could trip would be a cap nobody
    # could work under: the largest region admissible at Daejeon, at that same
    # PEAK density, must still fit comfortably.
    daejeon_area = (2 * LocalTangentPlane(36.35, 127.38).validity_radius_m / 1000.0) ** 2
    assert osm_fetch.PEAK_BYTES_PER_KM2 * daejeon_area < osm_fetch.MAX_RESPONSE_BYTES / 2


def test_the_socket_budget_covers_the_servers_own_budget_plus_the_transfer():
    """Giving up while Overpass is still inside the time we ASKED it for would
    be our bug, not its."""
    assert osm_fetch.TRANSFER_BUDGET_S == -(
        -osm_fetch.MAX_RESPONSE_BYTES * 8 // osm_fetch.ASSUMED_LINK_BITS_PER_S
    )
    assert osm_fetch.SOCKET_TIMEOUT_S == (
        osm_fetch.OVERPASS_QUERY_TIMEOUT_S + osm_fetch.TRANSFER_BUDGET_S
    )
    assert osm_fetch.SOCKET_TIMEOUT_S > osm_fetch.OVERPASS_QUERY_TIMEOUT_S


def test_a_declared_oversize_response_is_refused_before_a_single_byte_is_read(
    monkeypatch: pytest.MonkeyPatch,
):
    """When the server declares the size, the refusal costs nothing at all."""
    huge = osm_fetch.MAX_RESPONSE_BYTES + 1
    response = FakeResponse([b"x" * 10], {"Content-Length": str(huge)})
    fake = install(monkeypatch, FakeOverpass(response))

    with pytest.raises(osm_fetch.OsmFetchRefused) as caught:
        osm_fetch.fetch_region(SMALL_REGION)

    assert caught.value.code == "region_response_too_large"
    assert caught.value.detail["declared_bytes"] == huge
    assert response.reads == 0, "a declared oversize response must not be read at all"
    assert fake.attempts == 1, "a cap refusal is a verdict, not a transient failure"


def test_a_response_that_grows_past_the_cap_is_stopped_mid_download(
    monkeypatch: pytest.MonkeyPatch,
):
    """A chunked response carries no Content-Length, so the cap has to hold
    DURING the read — and it must stop, never truncate-and-continue."""
    monkeypatch.setattr(osm_fetch, "MAX_RESPONSE_BYTES", 1000)
    chunks = [b"y" * 400] * 20  # 8 000 bytes on offer
    response = FakeResponse(chunks, {})
    install(monkeypatch, FakeOverpass(response))

    with pytest.raises(osm_fetch.OsmFetchRefused) as caught:
        osm_fetch.fetch_region(SMALL_REGION)

    assert caught.value.code == "region_response_too_large"
    assert caught.value.detail["read_bytes"] > 1000
    # Three reads: 400, 800, 1200 -> the third crosses the line and the download
    # stops there. The remaining 17 chunks were never asked for.
    assert response.reads == 3, "the download must stop at the cap, not run to the end"
    assert len(response._chunks) == 17


def test_a_capped_response_never_becomes_a_shorter_network(
    workspace_root: Path, monkeypatch: pytest.MonkeyPatch
):
    """The refusal reaches the record, and nothing is staged.

    A truncated extract is a network with roads missing that no reader can
    notice — which is precisely the failure mode this project refuses.
    """
    monkeypatch.setattr(osm_fetch, "MAX_RESPONSE_BYTES", 500)
    install(monkeypatch, FakeOverpass(FakeResponse([b"z" * 400] * 5, {})))

    record = osm_fetch.create_region_fetch(bbox=SMALL_REGION, name="capped")
    done = osm_fetch.run_region_fetch(record["id"], record["workspace_id"])

    assert done["status"] == osm_fetch.STATUS_REFUSED
    assert done["refusal"]["code"] == "region_response_too_large"
    assert done["upload_token"] is None
    staging = genesis_jobs.staging_dir()
    assert not staging.is_dir() or list(staging.iterdir()) == [], (
        "a refused fetch must leave nothing staged"
    )


# ---------------------------------------------------------------------------
# §3 Overpass weather is named, never a crash
# ---------------------------------------------------------------------------


def test_a_rate_limit_is_a_named_refusal_and_is_never_retried(
    monkeypatch: pytest.MonkeyPatch,
):
    """429 means "you are asking too often". An immediate second ask is the
    dishonest answer to it, and it is what gets an instance to block a whole
    network."""
    fake = install(
        monkeypatch, FakeOverpass(http_error(429, {"Retry-After": "37"}))
    )
    with pytest.raises(osm_fetch.OsmFetchRefused) as caught:
        osm_fetch.fetch_region(SMALL_REGION)

    assert caught.value.code == "overpass_rate_limited"
    assert caught.value.detail["retry_after"] == "37"
    assert osm_fetch.DEFAULT_OVERPASS_ENDPOINT in caught.value.reasons[0]
    assert fake.attempts == 1, "429 must not be retried"


def test_a_gateway_timeout_is_retried_exactly_once_and_then_refused(
    monkeypatch: pytest.MonkeyPatch,
):
    """504 is ordinary weather on a public instance: one honest retry, then a
    refusal that names the endpoint and the status."""
    fake = install(monkeypatch, FakeOverpass(http_error(504), http_error(504)))
    with pytest.raises(osm_fetch.OsmFetchRefused) as caught:
        osm_fetch.fetch_region(SMALL_REGION)

    assert caught.value.code == "overpass_unavailable"
    assert caught.value.detail["status"] == 504
    assert caught.value.detail["endpoint"] == osm_fetch.DEFAULT_OVERPASS_ENDPOINT
    assert fake.attempts == osm_fetch.MAX_ATTEMPTS == 2


def test_a_socket_timeout_is_retried_exactly_once_and_then_refused(
    monkeypatch: pytest.MonkeyPatch,
):
    fake = install(
        monkeypatch,
        FakeOverpass(
            urllib.error.URLError(TimeoutError("timed out")),
            urllib.error.URLError(TimeoutError("timed out")),
        ),
    )
    with pytest.raises(osm_fetch.OsmFetchRefused) as caught:
        osm_fetch.fetch_region(SMALL_REGION)

    assert caught.value.code == "overpass_timeout"
    # The refusal states the budget it waited out, so "it just hung" is never
    # the whole story a user gets.
    assert str(osm_fetch.SOCKET_TIMEOUT_S) in caught.value.reasons[0]
    assert fake.attempts == 2


def test_a_transient_gateway_failure_that_clears_succeeds_on_the_retry(
    monkeypatch: pytest.MonkeyPatch,
):
    """The retry is worth having, or it would not be there."""
    fake = install(monkeypatch, FakeOverpass(http_error(503), ok_response()))
    fetched = osm_fetch.fetch_region(SMALL_REGION)

    assert fake.attempts == 2
    assert fetched.provenance["attempts"] == 2
    assert fetched.way_count >= 2


def test_a_200_carrying_an_overpass_remark_is_refused(monkeypatch: pytest.MonkeyPatch):
    """The trap this check exists for.

    Overpass reports a server-side failure as a ``<remark>`` inside an otherwise
    valid ``<osm>`` document, with HTTP 200. That parses, passes ``stage_osm``'s
    root-element check, and becomes an empty network several steps later with
    nobody able to say why.
    """
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<osm version="0.6" generator="Overpass API">\n'
        f"<note>{OVERPASS_NOTE}</note>\n"
        '<meta osm_base="2026-08-11T00:00:00Z"/>\n'
        '<remark> runtime error: Query timed out in "query" at line 3 after 60 seconds. </remark>\n'
        "</osm>\n"
    ).encode("utf-8")
    install(monkeypatch, FakeOverpass(ok_response(body)))

    with pytest.raises(osm_fetch.OsmFetchRefused) as caught:
        osm_fetch.fetch_region(SMALL_REGION)

    assert caught.value.code == "overpass_query_failed"
    assert "runtime error" in caught.value.detail["remark"]


def test_an_html_error_page_is_refused_rather_than_staged(
    monkeypatch: pytest.MonkeyPatch,
):
    install(monkeypatch, FakeOverpass(ok_response(b"<html><body>502 Bad Gateway</body></html>")))
    with pytest.raises(osm_fetch.OsmFetchRefused) as caught:
        osm_fetch.fetch_region(SMALL_REGION)
    assert caught.value.code == "overpass_bad_response"
    assert "<html>" in caught.value.reasons[0]


def test_a_region_with_no_roads_in_it_is_refused_by_name(
    monkeypatch: pytest.MonkeyPatch,
):
    """A valid, empty answer. Refused here rather than three steps later, where
    it would read as "the generator could not build a network" instead of
    "there is nothing at those coordinates"."""
    empty = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<osm version="0.6" generator="Overpass API">\n'
        f"<note>{OVERPASS_NOTE}</note>\n"
        "</osm>\n"
    ).encode("utf-8")
    install(monkeypatch, FakeOverpass(ok_response(empty)))

    with pytest.raises(osm_fetch.OsmFetchRefused) as caught:
        osm_fetch.fetch_region(SMALL_REGION)
    assert caught.value.code == "region_has_no_roads"


def test_a_misconfigured_endpoint_is_refused_by_name(monkeypatch: pytest.MonkeyPatch):
    """The endpoint is an operator's setting, so the failure being guarded
    against is a typo — but it is still a named refusal rather than an
    unreadable urllib error inside a worker thread."""
    monkeypatch.setenv(osm_fetch.OVERPASS_ENDPOINT_ENV, "overpass-api.de/api/interpreter")
    with pytest.raises(osm_fetch.OsmFetchRefused) as caught:
        osm_fetch.overpass_endpoint()
    assert caught.value.code == "overpass_endpoint_misconfigured"
    # A limits call still ANSWERS, saying the configuration is broken rather
    # than 500-ing on a calculator.
    assert "<misconfigured:" in osm_fetch.region_limits()["endpoint"]
    # And a fetch is refused while the caller is still looking at the region,
    # not by a worker thread they would have to poll to hear from.
    with pytest.raises(osm_fetch.OsmFetchRefused) as at_create:
        osm_fetch.create_region_fetch(bbox=SMALL_REGION)
    assert at_create.value.code == "overpass_endpoint_misconfigured"


def test_the_endpoint_is_server_configuration_not_a_request_field():
    """A caller-supplied URL would make this route an SSRF instrument pointed at
    whatever the server can reach."""
    from routers.genesis import StartRegionFetchRequest

    assert set(StartRegionFetchRequest.model_fields) == {
        "south",
        "west",
        "north",
        "east",
        "name",
    }
    assert StartRegionFetchRequest.model_config["extra"] == "forbid"


# ---------------------------------------------------------------------------
# §4 The query, and the provenance that travels with the answer
# ---------------------------------------------------------------------------


def test_the_query_asks_for_highway_ways_their_nodes_and_turn_restrictions():
    """What the generator reads, and nothing it does not.

    Footways and paths are deliberately NOT filtered out here: ``osm.py``
    classifies them and records each as a NAMED skip, and dropping them in this
    layer would make its compliance report silent about ways nobody ever offered
    it.
    """
    query = osm_fetch.build_query(SMALL_REGION)
    assert f"[out:xml][timeout:{osm_fetch.OVERPASS_QUERY_TIMEOUT_S}]" in query
    assert 'way["highway"]' in query
    assert 'rel(bw.roads)["type"="restriction"]' in query
    assert "out body qt;" in query
    assert "out skel" not in query, "recursed nodes must keep their tags"
    for value in (SMALL_REGION.south, SMALL_REGION.west, SMALL_REGION.north, SMALL_REGION.east):
        assert f"{value:.7f}" in query


def test_the_provenance_records_instance_query_region_time_and_bytes(
    monkeypatch: pytest.MonkeyPatch,
):
    """A network whose origin cannot be reconstructed is not auditable, and
    auditability is the entire product thesis."""
    body = overpass_body()
    fake = install(monkeypatch, FakeOverpass(ok_response(body)))
    fetched = osm_fetch.fetch_region(SMALL_REGION)

    provenance = fetched.provenance
    assert provenance["endpoint"] == osm_fetch.DEFAULT_OVERPASS_ENDPOINT
    assert provenance["query"] == osm_fetch.build_query(SMALL_REGION)
    assert provenance["requested_region"] == SMALL_REGION.as_dict()
    assert provenance["bytes"] == len(body)
    import hashlib

    assert provenance["sha256"] == hashlib.sha256(body).hexdigest()
    assert provenance["fetched_at"] >= provenance["requested_at"]
    assert provenance["overpass_generator"] is not None
    assert provenance["osm_base"] == "2026-08-11T00:00:00Z"
    assert provenance["measurement"]["region"] == SMALL_REGION.as_dict()
    # The request really did carry the query and identify this client.
    sent = fake.requests[0]
    assert sent.data.decode("utf-8") == provenance["query"]
    assert sent.headers["User-agent"] == osm_fetch.USER_AGENT
    assert fake.timeouts[0] == osm_fetch.SOCKET_TIMEOUT_S


def test_the_odbl_note_is_preserved_byte_for_byte(monkeypatch: pytest.MonkeyPatch):
    """OSM data carries a licence obligation. The response body is copied
    through unchanged; only a comment is added in front of it."""
    body = overpass_body()
    install(monkeypatch, FakeOverpass(ok_response(body)))
    fetched = osm_fetch.fetch_region(SMALL_REGION)

    assert OVERPASS_NOTE.encode("utf-8") in fetched.annotated
    assert fetched.provenance["attribution_note_present"] is True
    assert fetched.provenance["attribution_note"] == OVERPASS_NOTE
    assert "ODbL" in fetched.provenance["licence"]
    # Everything after the XML declaration is the response, untouched.
    tail = body.split(b"?>", 1)[1]
    assert tail in fetched.annotated


def test_a_response_without_an_odbl_note_is_recorded_as_missing_not_invented(
    monkeypatch: pytest.MonkeyPatch,
):
    """A mirror that omits the note is a fact about the response. Writing one
    ourselves would be this app asserting an attribution it did not receive."""
    install(monkeypatch, FakeOverpass(ok_response(overpass_body(note=None))))
    fetched = osm_fetch.fetch_region(SMALL_REGION)

    assert fetched.provenance["attribution_note_present"] is False
    assert fetched.provenance["attribution_note"] is None
    # The licence obligation is still stated on the record — it is true of the
    # data regardless of whether that particular instance restated it.
    assert "ODbL" in fetched.provenance["licence"]


def test_the_provenance_travels_inside_the_file_and_survives_a_round_trip(
    monkeypatch: pytest.MonkeyPatch,
):
    """A sidecar would be lost: ``create_genesis_job`` copies only the ``.osm``
    into the job's work directory. So the record goes INTO the document, the way
    ``services/genesis/emit.py`` writes provenance into an ``.inpx``."""
    install(monkeypatch, FakeOverpass(ok_response()))
    fetched = osm_fetch.fetch_region(SMALL_REGION)

    recovered = osm_fetch.read_provenance(fetched.annotated)
    assert recovered is not None
    assert recovered["endpoint"] == fetched.provenance["endpoint"]
    assert recovered["query"] == fetched.provenance["query"]
    assert recovered["sha256"] == fetched.provenance["sha256"]
    assert osm_fetch.read_provenance(fetched.content) is None


def test_the_annotated_document_parses_exactly_as_the_raw_one_does(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """An annotation that changed what the generator reads would be a fetch that
    quietly edits the map."""
    install(monkeypatch, FakeOverpass(ok_response()))
    fetched = osm_fetch.fetch_region(SMALL_REGION)

    raw_path = tmp_path / "raw.osm"
    raw_path.write_bytes(fetched.content)
    annotated_path = tmp_path / "annotated.osm"
    annotated_path.write_bytes(fetched.annotated)

    raw = parse_osm_file(raw_path)
    annotated = parse_osm_file(annotated_path)
    assert not annotated.refused
    assert [w.id for w in annotated.ways] == [w.id for w in raw.ways]
    assert annotated.nodes.keys() == raw.nodes.keys()
    # And the staging door — the other half of the round trip — still accepts it.
    assert genesis_jobs._xml_root_tag(fetched.annotated) == "osm"


# ---------------------------------------------------------------------------
# §5 The hand-off: a fetch ends at the staging door
# ---------------------------------------------------------------------------


def test_a_completed_fetch_hands_back_the_same_upload_token_an_upload_would(
    workspace_root: Path, monkeypatch: pytest.MonkeyPatch
):
    """B1's own token, resolvable by B1's own function. That IS the hand-off."""
    install(monkeypatch, FakeOverpass(ok_response()))

    record = osm_fetch.create_region_fetch(bbox=SMALL_REGION, name="handoff", submitted_by="tester")
    done = osm_fetch.run_region_fetch(record["id"], record["workspace_id"])

    assert done["status"] == osm_fetch.STATUS_STAGED
    staged_path = genesis_jobs.staged_osm_path(done["upload_token"])
    assert staged_path.is_file()
    assert staged_path.name == "handoff.osm"
    assert staged_path.stat().st_size == done["bytes"]
    assert osm_fetch.read_provenance(staged_path.read_bytes())["endpoint"] == (
        osm_fetch.DEFAULT_OVERPASS_ENDPOINT
    )


def test_a_fetch_without_a_name_is_named_for_where_it_is(
    workspace_root: Path, monkeypatch: pytest.MonkeyPatch
):
    """Two fetches of different places must not produce the same file name, and
    the name should say where the data is from."""
    install(monkeypatch, FakeOverpass(ok_response(), ok_response()))
    first = osm_fetch.create_region_fetch(bbox=SMALL_REGION)
    second = osm_fetch.create_region_fetch(
        bbox=osm_fetch.BoundingBox(south=36.34, west=127.37, north=36.36, east=127.39)
    )
    assert first["name"] != second["name"]
    assert first["name"].startswith("region_s32")
    assert second["name"].startswith("region_n36")


def test_a_rejected_name_is_refused_rather_than_silently_rewritten():
    """``genesis_jobs``' rule: renaming somebody's file for them produces a file
    they cannot find."""
    with pytest.raises(osm_fetch.OsmFetchRefused) as caught:
        osm_fetch._resolve_name("../../etc/passwd", SMALL_REGION)
    assert caught.value.code == "region_bad_name"


def test_two_projects_cannot_see_each_others_region_fetches(
    workspace_root: Path, monkeypatch: pytest.MonkeyPatch
):
    """Physically, not by filter: the records live under the workspace's tree."""
    install(monkeypatch, FakeOverpass(ok_response()))
    mine = osm_fetch.create_region_fetch(bbox=SMALL_REGION, name="mine")

    other = workspaces.create_workspace("다른 프로젝트")
    with workspaces.bind_workspace(other["id"]):
        assert osm_fetch.list_fetches() == []
        with pytest.raises(osm_fetch.OsmFetchNotFoundError):
            osm_fetch.get_fetch(mine["id"])


def test_an_interrupted_fetch_is_terminated_at_boot_not_left_spinning(
    workspace_root: Path, monkeypatch: pytest.MonkeyPatch
):
    """The worker thread died with the old process; nothing will ever advance
    this record and a UI would poll a bar that cannot move."""
    install(monkeypatch, FakeOverpass(ok_response()))
    record = osm_fetch.create_region_fetch(bbox=SMALL_REGION, name="orphan")
    osm_fetch._update(record["id"], status=osm_fetch.STATUS_RUNNING, phase=osm_fetch.PHASE_FETCHING)

    reconciled = osm_fetch.reconcile_interrupted_fetches()

    assert any(entry.endswith(record["id"]) for entry in reconciled)
    after = osm_fetch.get_fetch(record["id"])
    assert after["status"] == osm_fetch.STATUS_ERROR
    assert after["refusal"]["code"] == "interrupted_by_restart"
    assert after["upload_token"] is None


# ---------------------------------------------------------------------------
# §6 Auth and workspace parity with B1
# ---------------------------------------------------------------------------


def test_anonymous_callers_cannot_start_a_region_fetch(client: TestClient):
    """A fetch writes into a project's tree and spends this box's one outbound
    connection to a third-party service. Neither is anonymous work."""
    response = client.post(
        "/api/genesis/region/fetches",
        json={"south": -32.08, "west": 115.83, "north": -32.07, "east": 115.84},
    )
    assert response.status_code == 401


def test_the_region_calculator_and_the_records_stay_open(client: TestClient):
    """Parity with the rest of this router: arithmetic and the record of a
    decision are readable; the decision itself needs a name on it."""
    assert client.get("/api/genesis/region/limits").status_code == 200
    assert (
        client.get(
            "/api/genesis/region/preview",
            params={"south": -32.08, "west": 115.83, "north": -32.07, "east": 115.84},
        ).status_code
        == 200
    )
    listing = client.get("/api/genesis/region/fetches")
    assert listing.status_code == 200
    assert listing.json() == {"fetches": []}


def test_a_non_member_is_403_on_another_projects_region_fetches(client: TestClient):
    """Enforced by ``main.WorkspaceScopeMiddleware``, so it covers these routes
    without either of them asking for it."""
    created = client.post("/api/workspaces", json={"name": "지역 프로젝트"}, headers=admin_headers())
    assert created.status_code == 200, created.text
    workspace_id = created.json()["id"]

    outsider = bearer_headers("region-outsider", "modeler")
    headers = {"X-Workspace-Id": workspace_id, **outsider}
    assert client.get("/api/genesis/region/fetches", headers=headers).status_code == 403
    assert (
        client.post(
            "/api/genesis/region/fetches",
            json={"south": -32.08, "west": 115.83, "north": -32.07, "east": 115.84},
            headers=headers,
        ).status_code
        == 403
    )


def test_an_unknown_fetch_id_is_404(client: TestClient):
    assert client.get("/api/genesis/region/fetches/deadbeef1234").status_code == 404
    assert client.get("/api/genesis/region/fetches/not-hex").status_code == 404


# ---------------------------------------------------------------------------
# §7 End to end, with a FIXTURED Overpass and no network at all
# ---------------------------------------------------------------------------


def _await_terminal(client: TestClient, fetch_id: str, timeout_s: float = 10.0) -> dict:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        response = client.get(f"/api/genesis/region/fetches/{fetch_id}")
        assert response.status_code == 200, response.text
        record = response.json()
        if record["status"] in osm_fetch.TERMINAL_STATUSES:
            return record
        time.sleep(0.05)
    raise AssertionError(f"region fetch {fetch_id} never reached a terminal state")


def _await_job(client: TestClient, job_id: str, timeout_s: float = 60.0) -> dict:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        response = client.get(f"/api/genesis/jobs/{job_id}")
        assert response.status_code == 200, response.text
        record = response.json()
        if record["status"] in genesis_jobs.TERMINAL_STATUSES:
            return record
        time.sleep(0.05)
    raise AssertionError(f"genesis job {job_id} never reached a terminal state")


def test_end_to_end_region_to_staged_osm_to_gated_generation(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    """B2's acceptance sentence, over HTTP, with a fixtured Overpass response.

    A region is drawn, the extract is fetched and staged, and the SAME upload
    token is handed to ``POST /api/genesis/jobs`` — B1's existing route, not a
    new one. The generation then reaches a gate verdict exactly as an uploaded
    ``.osm`` does, and the provenance of the region is still readable inside the
    job's own source document at the end of it.
    """
    install(monkeypatch, FakeOverpass(ok_response()))
    headers = admin_headers()

    started = client.post(
        "/api/genesis/region/fetches",
        json={
            "south": SMALL_REGION.south,
            "west": SMALL_REGION.west,
            "north": SMALL_REGION.north,
            "east": SMALL_REGION.east,
            "name": "e2eregion",
        },
        headers=headers,
    )
    assert started.status_code == 202, started.text
    assert started.json()["submitted_by"] == "suite-admin"

    fetched = _await_terminal(client, started.json()["id"])
    assert fetched["status"] == osm_fetch.STATUS_STAGED, fetched
    assert fetched["provenance"]["endpoint"] == osm_fetch.DEFAULT_OVERPASS_ENDPOINT
    assert fetched["provenance"]["ways"] >= 2

    # --- the hand-off: B1's own route, with B1's own token ------------------
    job = client.post(
        "/api/genesis/jobs",
        json={"upload_token": fetched["upload_token"], "name": "e2eregion"},
        headers=headers,
    )
    assert job.status_code == 202, job.text
    record = _await_job(client, job.json()["id"])

    # The gate ran and decided, exactly as it does for an uploaded extract.
    assert record["status"] == genesis_jobs.STATUS_PROVISIONAL, record
    assert record["gate"]["verdict"] == "incomplete"
    assert record["network"]["links"] > 0

    gate = client.get(f"/api/genesis/jobs/{record['id']}/gate")
    assert gate.status_code == 200, gate.text
    assert gate.json()["verdict"] == "incomplete"

    # And the region's provenance is still inside the network's own source .osm.
    source = genesis_jobs.job_dir(record["id"]) / record["source_osm"]["stored_as"]
    provenance = osm_fetch.read_provenance(source.read_bytes())
    assert provenance is not None
    assert provenance["requested_region"]["south"] == SMALL_REGION.south
    assert json.loads(json.dumps(provenance))["query"].startswith("[out:xml]")
