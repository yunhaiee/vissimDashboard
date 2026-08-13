"""U-1-map — a genesis draft becomes degrees, and a published one moves the dashboard map.

Two surfaces, one fact between them: a genesis network was BUILT from WGS84 and
so it can be taken back to WGS84 exactly, which a consultant's ``.inpx`` cannot.

* **§1 ``GET /api/genesis/jobs/{id}/artifact/geojson``** — the findings map's
  source. The round-trip test is the load-bearing one: it does not compare the
  output against a table of numbers this file wrote down, it takes the OSM
  degrees the fixture was built from, follows them through the real generator,
  and asserts the endpoint hands back the SAME PLACE within
  ``project.MAX_PLANIMETRIC_ERROR_M`` — the projection's own budget, so the
  assertion is calibrated by the module under test rather than by a guess.
* **§2 the dashboard map** (``routers/geojson.py``) — a genesis-published
  dataset is drawn with its OWN origin, and a dataset with no georeference
  record still takes the SouthSt Helmert path, output for output.

Synthetic geometry only (``tests/test_genesis_osm_draft.py``'s 4-way crossroads
at Murdoch, the real generator's own test input) and no COM anywhere.
"""
from __future__ import annotations

import json
import math
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import main
from conftest import admin_headers, bearer_headers
from routers import geojson as geojson_router
from services import datasets, genesis_jobs, workspaces
from services.genesis.geojson import (
    KIND_CONNECTOR,
    KIND_LINK,
    DraftGeoJsonRefused,
    draft_to_geojson,
)
from services.genesis.project import (
    MAX_PLANIMETRIC_ERROR_M,
    LocalTangentPlane,
    meridional_radius_m,
    prime_vertical_radius_m,
)

from tests.test_genesis_jobs import client, crossroads_osm_file, workspace_root  # noqa: F401
from tests.test_genesis_gate import link, network
from tests.test_genesis_osm_draft import LAT0, LON0, crossroads_osm


def _generate(osm_path: Path, **kwargs) -> dict:
    """Submit and run ONE generation synchronously — the same two calls
    ``start_genesis_job`` makes, minus the thread."""
    record = genesis_jobs.create_genesis_job(osm_path=osm_path, **kwargs)
    return genesis_jobs.run_genesis_job(record["id"], record["workspace_id"])


def _metres_apart(a_lat: float, a_lon: float, b_lat: float, b_lon: float) -> float:
    """Ground distance between two nearby WGS84 points, in metres.

    Local-scale equirectangular with the ellipsoid's radii AT the first point —
    the same two constants ``project.py`` uses — because the pairs compared here
    are metres apart, where that is exact to well under a millimetre.
    """
    dy = math.radians(b_lat - a_lat) * meridional_radius_m(a_lat)
    dx = (
        math.radians(b_lon - a_lon)
        * prime_vertical_radius_m(a_lat)
        * math.cos(math.radians(a_lat))
    )
    return math.hypot(dx, dy)


# ---------------------------------------------------------------------------
# §1 The genesis GeoJSON artifact
# ---------------------------------------------------------------------------


def test_geojson_round_trips_a_known_osm_node_to_within_the_projection_budget(
    client: TestClient, crossroads_osm_file: Path
):
    """THE test. The OSM node the fixture declares comes back out of the
    endpoint, at the same place on the earth.

    Node 2 of the crossroads fixture is at exactly ``(LAT0, LON0)`` and is the
    only node shared by two kept ways, so every link in the draft has an
    endpoint there. Forward-projecting it with the draft's own recorded plane
    gives the metres the generator wrote; the endpoint must invert those metres
    back to within the projection's stated planimetric budget — one metre, i.e.
    under a third of a lane. (The inverse is algebraically exact, so the real
    residual is float noise; the budget is the honest ceiling to assert
    against, and it comes from the module under test rather than from a number
    typed here.)
    """
    record = _generate(crossroads_osm_file, name="roundtrip")
    draft = json.loads(
        genesis_jobs.artifact_path(record["id"], "genesis_json").read_text(encoding="utf-8")
    )
    plane = LocalTangentPlane(
        lat0_deg=draft["metadata"]["projection"]["origin_lat_deg"],
        lon0_deg=draft["metadata"]["projection"]["origin_lon_deg"],
    )

    # (a) THE KNOWN NODE. The fixture's node 2 is the crossroads at exactly
    # (LAT0, LON0) and is the only node two kept ways share, so the generator
    # emits exactly one DraftNode — at the projection of that point. Forward,
    # then back, must land on the same place within the projection's budget.
    junction_x, junction_y = plane.forward(LAT0, LON0)
    junction = min(
        draft["nodes"], key=lambda n: math.hypot(n["x"] - junction_x, n["y"] - junction_y)
    )
    assert math.hypot(junction["x"] - junction_x, junction["y"] - junction_y) <= 1e-6, (
        "the fixture's shared OSM node must be the draft node this test inverts"
    )
    back_lat, back_lon = plane.inverse(junction["x"], junction["y"])
    assert _metres_apart(LAT0, LON0, back_lat, back_lon) <= MAX_PLANIMETRIC_ERROR_M

    response = client.get(
        f"/api/genesis/jobs/{record['id']}/artifact/geojson", headers=admin_headers()
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["type"] == "FeatureCollection"
    assert payload["features"], "a generated crossroads must produce drawable geometry"

    # (b) THE ENDPOINT USED THAT PLANE, not another one. Each emitted vertex is
    # compared against this test's own inverse of the SAME draft point, matched
    # by feature id and vertex index — so an endpoint that reconstructed a
    # different origin (a re-derived centre, (0, 0), the Helmert constants)
    # fails here by kilometres rather than passing on a loose bound.
    links_by_id = {link["id"]: link for link in draft["links"]}
    checked = 0
    for feature in payload["features"]:
        if feature["properties"]["kind"] != KIND_LINK:
            continue
        source = links_by_id[feature["properties"]["id"]]
        for (lon, lat), (x, y) in zip(feature["geometry"]["coordinates"], source["points"]):
            expected_lat, expected_lon = plane.inverse(x, y)
            assert _metres_apart(expected_lat, expected_lon, lat, lon) <= MAX_PLANIMETRIC_ERROR_M
            checked += 1
    assert checked > 0, "no link vertex was compared -- the payload shape changed"

    # (c) AND IT IS ACTUALLY AT MURDOCH. The fixture spans ~283 m east and
    # ~222 m north of the crossroads, so every drawn vertex is inside a
    # kilometre of it. Independent of (b): this is the assertion that would
    # catch the draft and the endpoint agreeing on a wrong origin.
    for feature in payload["features"]:
        for lon, lat in feature["geometry"]["coordinates"]:
            assert _metres_apart(LAT0, LON0, lat, lon) < 1000.0

    # The recorded origin travels with the payload rather than being re-derived
    # by the client, and the standard bbox lets a map fit without walking it.
    assert payload["origin"]["lat"] == pytest.approx(plane.lat0_deg)
    assert payload["origin"]["lon"] == pytest.approx(plane.lon0_deg)
    west, south, east, north = payload["bbox"]
    assert west < east and south < north


def test_geojson_names_the_kind_of_every_feature(
    client: TestClient, crossroads_osm_file: Path
):
    """Links and connectors are both LineStrings and are NOT the same object —
    the kind travels on the feature so the renderer never infers it from an id
    range that a differently-sized generation would break."""
    record = _generate(crossroads_osm_file, name="kinds")
    payload = client.get(
        f"/api/genesis/jobs/{record['id']}/artifact/geojson", headers=admin_headers()
    ).json()

    kinds = {feature["properties"]["kind"] for feature in payload["features"]}
    assert kinds == {KIND_LINK, KIND_CONNECTOR}
    for feature in payload["features"]:
        assert feature["geometry"]["type"] == "LineString"
        assert isinstance(feature["properties"]["id"], int)
        assert "name" in feature["properties"]


def test_geojson_requires_a_signed_in_user(client: TestClient, crossroads_osm_file: Path):
    """Authenticated for the same reason the artifact download is: this is the
    network's geometry, and an anonymous caller has no identity to hand it to."""
    record = _generate(crossroads_osm_file, name="anon")
    assert (
        client.get(f"/api/genesis/jobs/{record['id']}/artifact/geojson").status_code == 401
    )


def test_geojson_cannot_read_another_projects_generation(
    client: TestClient, crossroads_osm_file: Path
):
    """Workspace isolation, in the shape every other genesis read has it: a job
    in another project is 404, never a peek at its geometry."""
    record = _generate(crossroads_osm_file, name="scoped")
    created = client.post(
        "/api/workspaces", json={"name": "다른 프로젝트"}, headers=admin_headers()
    )
    assert created.status_code in (200, 201)
    other = {"X-Workspace-Id": created.json()["id"], **admin_headers()}

    assert (
        client.get(
            f"/api/genesis/jobs/{record['id']}/artifact/geojson", headers=other
        ).status_code
        == 404
    )


def test_geojson_404s_a_generation_that_never_produced_a_network(
    client: TestClient, workspace_root: Path
):
    """A job that errored before a draft existed has no census to convert — the
    same 404 the artifact route gives, not an empty FeatureCollection that
    would read as 'this network has no roads'."""
    # Lowercase hex, because ``genesis_jobs._ID_RE`` is what decides whether an
    # id can name a directory at all.
    job_id = "0badc0ffee11"
    work_dir = genesis_jobs.job_dir(job_id)
    work_dir.mkdir(parents=True, exist_ok=True)
    genesis_jobs._upsert(
        {
            "id": job_id,
            "workspace_id": workspaces.current_workspace_id(),
            "name": "nogeometry",
            "status": genesis_jobs.STATUS_ERROR,
            "phase": genesis_jobs.PHASE_DONE,
            "progress": 1.0,
            "created_at": "2026-08-13T00:00:00+00:00",
            "started_at": None,
            "completed_at": None,
            "submitted_by": "tester",
            "options": {"exclude_service": False},
            "source_osm": {"filename": "x.osm", "bytes": 0, "sha256": "", "path": ""},
            "network": None,
            "artifact": None,
            "gate": None,
            "publication": None,
            "refusal": None,
            "error": "generation_failed",
        }
    )

    assert (
        client.get(
            f"/api/genesis/jobs/{job_id}/artifact/geojson", headers=admin_headers()
        ).status_code
        == 404
    )


def test_a_draft_with_no_recorded_origin_is_refused_never_guessed():
    """The refusal that makes this conversion honest.

    A draft carrying no ``origin_lat_deg``/``origin_lon_deg`` cannot be placed
    on the earth. Inventing an origin — (0, 0), or one re-derived from the
    points — would draw every link in the right SHAPE at the wrong PLACE, which
    is the silent wrong answer, not the visible one.
    """
    with pytest.raises(DraftGeoJsonRefused) as caught:
        draft_to_geojson(
            {
                "metadata": {"projection": {"kind": "equirectangular_local_tangent_plane_wgs84"}},
                "links": [{"id": 1, "name": "x", "points": [[0.0, 0.0], [10.0, 0.0]]}],
                "connectors": [],
            }
        )
    assert caught.value.code == "genesis_draft_has_no_projection_origin"
    assert "origin_lat_deg" in caught.value.reason


def test_the_geojson_route_is_matched_before_the_artifact_kind_route():
    """Declaration order is load-bearing: FastAPI matches in order, and
    ``/artifact/{kind}`` would swallow ``/artifact/geojson`` if it came first.
    Asserted on the app's own route table rather than on a comment."""
    paths = [
        getattr(route, "path", "")
        for route in main.app.routes
        if getattr(route, "path", "").startswith("/api/genesis/jobs/{job_id}/artifact")
    ]
    assert paths.index("/api/genesis/jobs/{job_id}/artifact/geojson") < paths.index(
        "/api/genesis/jobs/{job_id}/artifact/{kind}"
    )


# ---------------------------------------------------------------------------
# §2 The dashboard map — one transform per dataset
# ---------------------------------------------------------------------------


def test_a_genesis_published_dataset_draws_with_its_own_origin(
    client: TestClient, crossroads_osm_file: Path
):
    """The reported bug, closed: the dashboard map is DEAD for a genesis dataset.

    A fresh generation publishes as ``provisional``, which registers it as the
    workspace's active dataset. Before this unit, ``/api/geojson/network`` then
    tried to read a ``refPointMap`` the genesis emitter deliberately never
    writes and 500'd. Now the dataset carries its own plane and the network is
    drawn at Murdoch — where its OSM ways actually are — rather than nowhere.
    """
    geojson_router._cache.clear()
    record = _generate(crossroads_osm_file, name="published")
    assert record["status"] == genesis_jobs.STATUS_PROVISIONAL
    assert record["publication"] is not None

    stored = datasets.get_active_dataset_georeference()
    assert stored is not None, "publishing must record the draft's plane on the dataset"
    assert stored["source"] == "genesis_draft"
    assert stored["origin_lat_deg"] == pytest.approx(LAT0, abs=1e-3)

    response = client.get("/api/geojson/network", headers=admin_headers())
    assert response.status_code == 200
    features = response.json()["features"]
    assert features, "the published network must draw"

    for feature in features:
        coordinates = (
            feature["geometry"]["coordinates"]
            if feature["geometry"]["type"] == "LineString"
            else [feature["geometry"]["coordinates"]]
        )
        for lon, lat in coordinates:
            # Every vertex within a kilometre of the fixture's own crossroads.
            # The fixture spans ~±283 m east and ~±222 m north of it, so this
            # bound would fail loudly for the Helmert path (which puts these
            # metres ~14 km away) and for a (0, 0) fallback.
            assert _metres_apart(LAT0, LON0, lat, lon) < 1000.0


def test_a_dataset_with_no_georeference_record_keeps_the_helmert_path(
    client: TestClient, workspace_root: Path
):
    """The consultant path, unchanged and provably so.

    No georeference record ⇒ ``_dataset_plane()`` is ``None`` ⇒ the five
    ``DEFAULT_MAP_*`` constants and the file's own anchor, exactly as before.
    Asserted by recomputing the transform here from the SAME ``GeoTransform``
    the router loads and comparing the emitted coordinates value for value —
    an equality, not a bounding box.
    """
    geojson_router._cache.clear()
    assert datasets.get_active_dataset_georeference() is None

    response = client.get("/api/geojson/network")
    assert response.status_code == 200
    line = next(
        feature
        for feature in response.json()["features"]
        if feature["geometry"]["type"] == "LineString"
    )

    inpx_path = datasets.get_active_inpx_path()
    transform = geojson_router._load_transform(
        inpx_path.stat().st_mtime, str(inpx_path), ""
    )
    from services.inpx_parser import parse_network

    net = parse_network(str(inpx_path))
    source = next(
        link
        for link in net["links"]
        if link["id"] == line["properties"]["id"] and not link.get("is_connector")
    )
    expected = []
    for vx, vy in source["points"]:
        lat, lon = transform.to_wgs84(
            vx,
            vy,
            offset_x=geojson_router.DEFAULT_MAP_OFFSET_X,
            offset_y=geojson_router.DEFAULT_MAP_OFFSET_Y,
            rotation_deg=geojson_router.DEFAULT_MAP_ROTATION_DEG,
            scale_x=geojson_router.DEFAULT_MAP_SCALE_X,
            scale_y=geojson_router.DEFAULT_MAP_SCALE_Y,
        )
        expected.append([lon, lat])
    assert line["geometry"]["coordinates"] == expected


def test_a_georeference_record_naming_another_projection_is_not_inverted(
    client: TestClient, workspace_root: Path, monkeypatch: pytest.MonkeyPatch
):
    """``_dataset_plane`` knows how to invert exactly ONE projection.

    A record naming a different ``kind`` must fall through to the existing path
    rather than be run through the wrong algebra — the failure mode this whole
    unit exists to avoid is geometry drawn confidently in the wrong place.
    """
    monkeypatch.setattr(
        datasets,
        "get_active_dataset_georeference",
        lambda: {"kind": "utm_zone_50s", "origin_lat_deg": -32.0, "origin_lon_deg": 115.8},
    )
    monkeypatch.setattr(
        geojson_router,
        "get_active_dataset_georeference",
        datasets.get_active_dataset_georeference,
    )
    assert geojson_router._dataset_plane() is None


def test_the_center_of_a_genesis_dataset_is_its_own_origin(
    client: TestClient, crossroads_osm_file: Path
):
    """``/center`` takes the same fork — it also calls ``_load_transform``, and
    a genesis .inpx has no anchor for it to read."""
    geojson_router._cache.clear()
    _generate(crossroads_osm_file, name="centred")

    payload = client.get("/api/geojson/center", headers=admin_headers()).json()
    assert _metres_apart(LAT0, LON0, payload["lat"], payload["lon"]) < 1000.0
    assert payload["zoom"] == 16


# ---------------------------------------------------------------------------
# §3 The two ways a recorded origin can still be wrong
# ---------------------------------------------------------------------------


def test_a_swapped_but_numeric_origin_is_refused_not_inverted():
    """PRESENT IS NOT USABLE, and this is the case that proves it.

    A metadata record whose two origin fields were stored the wrong way round
    (``lat=115.836, lon=-32.07`` -- a Perth coordinate, transposed) is numeric on
    BOTH fields, so every is-it-a-number guard passes it. The transform then
    runs happily: ``cos(115.836 deg)`` is negative, so east and west trade places
    and the network is drawn mirrored onto the far side of the planet with
    nothing on screen to say so. That is the silent wrong answer, and it must be
    a refusal instead.
    """
    corrupted = {
        "metadata": {
            "projection": {
                "kind": "equirectangular_local_tangent_plane_wgs84",
                "origin_lat_deg": LON0,  # the two, deliberately transposed
                "origin_lon_deg": LAT0,
            }
        },
        "links": [{"id": 1, "name": "x", "points": [[0.0, 0.0], [10.0, 0.0]]}],
        "connectors": [],
    }
    with pytest.raises(DraftGeoJsonRefused) as caught:
        draft_to_geojson(corrupted)
    assert caught.value.code == "genesis_draft_projection_origin_out_of_range"
    # Both values are quoted, so a reader can SEE the transposition rather than
    # being told only that something was out of range.
    assert str(LON0) in caught.value.reason and str(LAT0) in caught.value.reason


@pytest.mark.parametrize(
    "lat, lon",
    [
        (91.0, 10.0),          # past the pole
        (-90.5, 10.0),
        (10.0, 180.5),         # past the antimeridian
        (10.0, -181.0),
        (True, 10.0),          # bool IS an int in Python -- would become lat 1.0
        (10.0, False),
        (float("nan"), 10.0),
        (float("inf"), 10.0),
        ("36.35", 127.38),     # a string that LOOKS like a coordinate
    ],
)
def test_every_unusable_origin_is_refused_by_range(lat, lon):
    """The whole guard, one case per way a stored origin can be unusable.

    ``bool`` and the non-finite floats are in here because they are the values
    that pass an ``isinstance(x, (int, float))`` check -- the guard this
    replaced -- and would have become latitude 1.0 or a NaN network.
    """
    with pytest.raises(DraftGeoJsonRefused) as caught:
        draft_to_geojson(
            {
                "metadata": {"projection": {"origin_lat_deg": lat, "origin_lon_deg": lon}},
                "links": [{"id": 1, "name": "x", "points": [[0.0, 0.0], [10.0, 0.0]]}],
                "connectors": [],
            }
        )
    assert caught.value.code == "genesis_draft_projection_origin_out_of_range"


def test_a_corrupted_origin_never_reaches_the_dashboard_map(
    client: TestClient, workspace_root: Path, monkeypatch: pytest.MonkeyPatch
):
    """The same range check on the dashboard map's own read side.

    ``routers/geojson.py`` reads the origin back out of the DATASET's metadata,
    which is a different file from the draft -- a record hand-edited (or written
    by an older build) there must fall back to the existing Helmert path rather
    than mirror the network. Falling back is honest here: for a genesis dataset
    that path then reports its missing anchor instead of inventing a place.
    """
    geojson_router._cache.clear()
    monkeypatch.setattr(
        datasets,
        "get_active_dataset_georeference",
        lambda: {
            "kind": "equirectangular_local_tangent_plane_wgs84",
            "origin_lat_deg": LON0,
            "origin_lon_deg": LAT0,
        },
    )
    monkeypatch.setattr(
        geojson_router,
        "get_active_dataset_georeference",
        datasets.get_active_dataset_georeference,
    )
    assert geojson_router._dataset_plane() is None


def test_a_swapped_origin_is_never_recorded_onto_a_dataset():
    """And the write side refuses it too, so it cannot be stored in the first
    place. ``None`` CLEARS the record rather than leaving the previous
    dataset's plane attached to this one."""
    assert (
        genesis_jobs._dataset_georeference(
            {"metadata": {"projection": {"origin_lat_deg": LON0, "origin_lon_deg": LAT0}}}
        )
        is None
    )
    assert (
        genesis_jobs._dataset_georeference(
            {"metadata": {"projection": {"origin_lat_deg": LAT0, "origin_lon_deg": LON0}}}
        )
        is not None
    )


# ---------------------------------------------------------------------------
# §4 Promote + georeference are ONE step, or two datasets swap planes
# ---------------------------------------------------------------------------


def test_a_concurrent_registration_cannot_inherit_the_other_datasets_plane(
    workspace_root: Path, monkeypatch: pytest.MonkeyPatch
):
    """THE INTERLEAVING, driven for real rather than reasoned about.

    Before ``save_active_dataset_with_georeference`` existed, publishing took
    the workspace's upload lock TWICE -- once to promote the bytes, once to write
    the projection origin -- and released it in between. A second registration
    landing in that window promoted ITS dataset and wrote ITS origin, and then
    the first publish's second write landed on top: dataset B's bytes under
    dataset A's plane, both writes individually successful, nothing recording
    that the pair disagree. Every coordinate the dashboard map draws for B is
    then confidently in A's place.

    This test opens that window as wide as it can get -- a real second THREAD,
    released at the exact moment the first publish has promoted but not yet
    recorded -- and asserts the two never cross. With the two writes under one
    lock the second thread simply waits; with them apart it wins the race and
    the active dataset ends up carrying the other publish's origin.
    """
    a_lat, a_lon = LAT0, LON0
    b_lat, b_lon = 36.3504, 127.3845  # Daejeon -- nowhere near Perth

    def georef(lat: float, lon: float) -> dict:
        return {
            "kind": "equirectangular_local_tangent_plane_wgs84",
            "datum": "WGS84",
            "origin_lat_deg": lat,
            "origin_lon_deg": lon,
            "source": "genesis_draft",
        }

    inpx_a = network(links=[link(1, [(0.0, 0.0), (100.0, 0.0)])])
    inpx_b = network(links=[link(1, [(0.0, 0.0), (200.0, 0.0)])])

    workspace_id = workspaces.current_workspace_id()
    promoted_a = threading.Event()
    second_done = threading.Event()
    failures: list[BaseException] = []

    original_set = datasets.set_active_dataset_georeference

    def instrumented(record: dict | None) -> None:
        # Called INSIDE the critical section. Releasing the second thread here is
        # what makes the window as wide as it can possibly be: if the lock is not
        # held across both writes, thread B has time to complete an entire
        # registration before this one records anything.
        if record is not None and record["origin_lat_deg"] == a_lat:
            promoted_a.set()
            second_done.wait(timeout=3.0)
        original_set(record)

    monkeypatch.setattr(datasets, "set_active_dataset_georeference", instrumented)

    def publish_b() -> None:
        try:
            promoted_a.wait(timeout=3.0)
            token = workspaces.set_current_workspace_id(workspace_id)
            try:
                datasets.save_active_dataset_with_georeference(
                    "b.inpx", inpx_b.encode("utf-8"), georef(b_lat, b_lon)
                )
            finally:
                workspaces.reset_current_workspace_id(token)
        except BaseException as exc:  # noqa: BLE001 - re-raised on the main thread
            failures.append(exc)
        finally:
            second_done.set()

    thread = threading.Thread(target=publish_b, daemon=True)
    thread.start()
    try:
        datasets.save_active_dataset_with_georeference(
            "a.inpx", inpx_a.encode("utf-8"), georef(a_lat, a_lon)
        )
    finally:
        second_done.set()
        thread.join(timeout=5.0)

    assert not failures, f"the second registration raised: {failures[0]!r}"
    assert not thread.is_alive(), "the second registration never finished"

    active = datasets.get_active_dataset()
    stored = datasets.get_active_dataset_georeference()
    assert stored is not None

    # WHICHEVER dataset ended up active, its plane must be its OWN. Asserted as a
    # PAIRING rather than as "b wins", because the lock decides the order and the
    # order is not the property under test -- the property is that the bytes and
    # the origin can never come from two different publishes.
    pairs = {"a.inpx": (a_lat, a_lon), "b.inpx": (b_lat, b_lon)}
    expected_lat, expected_lon = pairs[active["filename"]]
    assert (stored["origin_lat_deg"], stored["origin_lon_deg"]) == (
        expected_lat,
        expected_lon,
    ), (
        f"active dataset {active['filename']} is carrying the OTHER publish's projection "
        f"origin ({stored['origin_lat_deg']}, {stored['origin_lon_deg']}) -- the promote "
        "and the georeference write were not one indivisible step"
    )
