"""Tests for M1.2's read tools: tools_network.py and tools_metrics.py.

These are the first real tools registered through the M0.4 registry
(services.agent.toolkit) — importing either module registers its tools onto
``default_registry`` at import time (see toolkit.py's module docstring), so
this file also carries the anti-drift guard extending M0.4's registry test to
real tools (docs/phase2/02-ROADMAP.md M1.2).
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

import services.agent.tools_edit as tools_edit  # noqa: F401 - import registers tools
import services.agent.tools_metrics as tools_metrics  # noqa: F401 - import registers tools
import services.agent.tools_network as tools_network  # noqa: F401 - import registers tools
import services.agent.tools_run as tools_run  # noqa: F401 - import registers tools
from services.agent.toolkit import default_registry, dispatch, render_contract

FIXTURES_DIR = Path(__file__).parent / "fixtures"
KOREAN_FIXTURE_INPX = FIXTURES_DIR / "korean_object_names.inpx"

EXPECTED_TOOL_NAMES = {
    "get_network_overview",
    "resolve_object",
    "get_vehicle_inputs",
    "get_signal_controllers",
    "get_conflict_areas",
    "get_routing_decisions",
    "get_vehicle_compositions",
    "get_run_kpis",
    "compare_scenarios",
}

# M2.3's tools_edit.py registers these write-tier tools, and M2.4's
# tools_run.py registers the one run-tier tool below, onto the SAME
# default_registry, so the anti-drift test has to know about them too, or
# it would (correctly, but confusingly) fail on every run. Both modules are
# imported directly at the top of this file: an earlier revision relied on
# pytest's collection phase importing other test modules (which import
# services.agent.orchestrator, which imports tools_edit/tools_run for their
# registration side effect) -- that held for full-suite runs but made the
# three registry-shape tests fail whenever this file ran in isolation.
# v2 unit (2) adds set_reduced_speed_area (XML-injection, not COM), the sixth.
# v2 unit (4) adds set_conflict_area_status, the seventh.
# v2 unit (6) adds set_route_relative_flow, the eighth.
# v2 unit (7) adds set_vehicle_composition_share, the ninth.
EXPECTED_WRITE_TOOL_NAMES = {
    "set_vehicle_input_volume",
    "set_signal_cycle",
    "set_signal_offset",
    "set_sim_duration",
    "set_random_seed",
    "set_reduced_speed_area",
    "set_conflict_area_status",
    "set_route_relative_flow",
    "set_vehicle_composition_share",
}

# M2.4's run-gate tool, plus v2 unit (3)'s load_check (docs/phase2/02-ROADMAP.md
# M2.4; docs/phase2/DECISIONS.md's v2 unit (3) entry). Both cost a real Vissim
# instance so both are tier="run", but they are NOT the same provider — see
# test_run_tools_are_tier_run_with_their_own_providers below.
EXPECTED_RUN_TOOL_NAMES = {
    "run_simulation",
    "load_check",
}

# v2 unit (4) adds set_conflict_area_status: the seventh write-tier tool, and
# get_conflict_areas above: the seventh read-tier tool.


@pytest.fixture(autouse=True)
def _reset_active_inpx():
    """Every test starts from the real demo default and restores it after,
    so tests can't leak an override into one another regardless of order."""
    tools_network.set_active_inpx(None)
    yield
    tools_network.set_active_inpx(None)


def _dispatch(name: str, **params: Any) -> dict[str, Any]:
    return dispatch(name, params, registry=default_registry)


# ---------------------------------------------------------------------------
# Anti-drift: registry contains exactly these 6 tools, no phantoms
# ---------------------------------------------------------------------------


def test_default_registry_has_exactly_the_expected_read_write_and_run_tools():
    """M1.2's 6 read tools + M2.3/v2 unit (2)'s 6 write tools + M2.4's 1 run
    tool, no phantoms, nothing missing. Was "...exactly_the_six_read_tools"
    before M2.3 registered tools_edit.py's write tools, then
    "...the_expected_read_and_write_tools" before M2.4 registered
    tools_run.py's run tool, onto this same default_registry — renamed and
    widened each time rather than split, since a second anti-drift test over
    the same registry would just duplicate this one's headings/duplicate-
    check logic for no real isolation (all three modules' tools share one
    registry by design)."""
    contract = render_contract(registry=default_registry)
    headings = re.findall(r"^## (\S+)$", contract, flags=re.MULTILINE)

    assert len(headings) == len(set(headings))  # no duplicates
    assert set(headings) == EXPECTED_TOOL_NAMES | EXPECTED_WRITE_TOOL_NAMES | EXPECTED_RUN_TOOL_NAMES


def test_all_read_tools_are_tier_read():
    for name in EXPECTED_TOOL_NAMES:
        spec = default_registry.get(name)
        assert spec is not None, f"{name} not registered"
        assert spec.tier == "read"


def test_all_write_tools_are_tier_write():
    for name in EXPECTED_WRITE_TOOL_NAMES:
        spec = default_registry.get(name)
        assert spec is not None, f"{name} not registered"
        assert spec.tier == "write"


def test_run_tools_are_tier_run_with_their_own_providers():
    """Both run-tier tools cost a real Vissim instance, so both are
    tier="run" — but provider stays a per-MODULE label (same convention as
    tools_network's "inspect" vs tools_metrics' "metrics" below), and
    load_check is deliberately its OWN module (tools_check.py), not folded
    into tools_run.py, so it gets its own provider rather than inheriting
    run_simulation's "run"."""
    for name in EXPECTED_RUN_TOOL_NAMES:
        spec = default_registry.get(name)
        assert spec is not None, f"{name} not registered"
        assert spec.tier == "run"

    assert default_registry.get("run_simulation").provider == "run"
    assert default_registry.get("load_check").provider == "check"


def test_network_tools_use_inspect_provider_and_metrics_tools_use_metrics_provider():
    for name in (
        "get_network_overview",
        "resolve_object",
        "get_vehicle_inputs",
        "get_signal_controllers",
        "get_conflict_areas",
        "get_routing_decisions",
    ):
        assert default_registry.get(name).provider == "inspect"
    for name in ("get_run_kpis", "compare_scenarios"):
        assert default_registry.get(name).provider == "metrics"


# ---------------------------------------------------------------------------
# get_network_overview — real demo data (data/practice.inpx)
# ---------------------------------------------------------------------------


def test_get_network_overview_returns_counts_and_compact_lists_for_demo_data():
    envelope = _dispatch("get_network_overview")

    assert envelope["status"] == "ok"
    data = envelope["data"]
    for key in ("links", "nodes", "vehicle_inputs", "signals", "conflict_areas", "routing_decisions"):
        assert key in data
        section = data[key]
        assert set(section.keys()) == {"count", "items", "truncated"}
        assert section["count"] == len(section["items"]) or section["truncated"]
        for item in section["items"]:
            assert set(item.keys()) == {"id", "name"}  # compact: no geometry/points/etc.

    # demo dataset facts (data/practice.inpx): 16 links (14 real + 2 Phase 3
    # connectors), 2 nodes, 6 vehicle inputs, 0 working signal controllers (no
    # intSupplyData in the demo file), 0 conflict areas (the demo .inpx carries
    # no <conflictArea> elements at all -- v2 unit ④), 2 static routing
    # decisions (v2 unit ⑥ -- unlike signals/conflict_areas, the demo .inpx
    # DOES carry these)
    assert data["links"]["count"] == 16
    assert data["nodes"]["count"] == 2
    assert data["vehicle_inputs"]["count"] == 6
    assert data["signals"]["count"] == 0
    assert data["conflict_areas"]["count"] == 0
    assert data["routing_decisions"]["count"] == 2
    assert data["links"]["truncated"] is False


def test_get_network_overview_rejects_unexpected_params():
    envelope = _dispatch("get_network_overview", bogus=1)

    assert envelope["status"] == "error"
    assert envelope["error_type"] == "invalid_params"


# ---------------------------------------------------------------------------
# resolve_object — Korean acceptance test (synthetic fixture, per the brief)
# ---------------------------------------------------------------------------


def test_resolve_object_korean_query_ranks_exact_above_token_overlap():
    """'중앙로 교차로' must resolve BOTH Korean-named links, with the exact
    name match ranked strictly above the token-overlap-only match."""
    tools_network.set_active_inpx(KOREAN_FIXTURE_INPX)

    envelope = _dispatch("resolve_object", query="중앙로 교차로", object_type=None)

    assert envelope["status"] == "ok"
    data = envelope["data"]
    assert data["found"] is True
    ids = [c["id"] for c in data["candidates"]]
    assert ids == ["501", "502"]  # exact match (501) ranked above token-overlap-only (502)
    assert data["candidates"][0]["score"] > data["candidates"][1]["score"]
    assert data["candidates"][0]["name"] == "중앙로 교차로"
    assert data["candidates"][1]["name"] == "중앙로 남측 접근로"


def test_resolve_object_korean_query_filtered_by_object_type_link():
    tools_network.set_active_inpx(KOREAN_FIXTURE_INPX)

    envelope = _dispatch("resolve_object", query="중앙로 교차로", object_type="link")

    data = envelope["data"]
    assert data["found"] is True
    assert all(c["object_type"] == "link" for c in data["candidates"])


def test_resolve_object_ties_broken_by_object_id_ascending():
    """Two links (503, 504 — deliberately out of document order in the
    fixture) share an identical name, so both score an identical exact
    match (3.0). The tie must resolve to id ascending, not document/
    registration order."""
    tools_network.set_active_inpx(KOREAN_FIXTURE_INPX)

    envelope = _dispatch("resolve_object", query="테스트 동일링크", object_type="link")

    data = envelope["data"]
    assert data["found"] is True
    assert [c["id"] for c in data["candidates"]] == ["503", "504"]
    assert [c["score"] for c in data["candidates"]] == [3.0, 3.0]  # genuine tie, not a fluke


def test_resolve_object_whitespace_only_query_is_a_graceful_miss():
    """A whitespace-only query scores zero on every tier (nothing to match)
    and must come back as a well-formed miss, never raise."""
    envelope = _dispatch("resolve_object", query="   ", object_type=None)

    assert envelope["status"] == "ok"
    data = envelope["data"]
    assert data["found"] is False
    assert data["error_type"] == "object_not_found"
    assert isinstance(data["candidates"], list)


# ---------------------------------------------------------------------------
# resolve_object — English resolution + object_not_found miss (real demo data)
# ---------------------------------------------------------------------------


def test_resolve_object_english_typo_resolves_via_token_overlap():
    """'Central Main' (typo'd/partial, no '&') still resolves node 1 ('Central
    & Main') by shared lowercase tokens — a successful, ranked resolution."""
    envelope = _dispatch("resolve_object", query="Central Main", object_type="node")

    data = envelope["data"]
    assert data["found"] is True
    assert data["candidates"][0]["id"] == "1"
    assert data["candidates"][0]["name"] == "Central & Main"


def test_resolve_object_miss_returns_structured_object_not_found_with_candidates():
    """A query that scores zero on every tier (exact/substring/token overlap)
    against the whole demo network must come back as a structured miss whose
    fallback candidates (difflib over ids+names, resolved to owning ids) still
    surface the near match — node id '1' ('Central & Main')."""
    envelope = _dispatch("resolve_object", query="Cntrall Man", object_type=None)

    assert envelope["status"] == "ok"  # a domain miss is still a successful tool call
    data = envelope["data"]
    assert data["found"] is False
    assert data["error_type"] == "object_not_found"
    assert "1" in data["candidates"]


def test_resolve_object_rejects_unknown_object_type():
    envelope = _dispatch("resolve_object", query="anything", object_type="banana")

    assert envelope["status"] == "error"
    assert envelope["error_type"] == "invalid_params"


# ---------------------------------------------------------------------------
# get_vehicle_inputs / get_signal_controllers — extended shadow model sections
# ---------------------------------------------------------------------------


def test_get_vehicle_inputs_on_demo_data_has_no_volume_children():
    """The demo .inpx's <vehicleInput> elements carry no/name/link attrs and
    no volume children — volume must come back None, never crash. v2 unit ⑤:
    interval_count/volumes are now real fields too (0/[] here, not absent)."""
    envelope = _dispatch("get_vehicle_inputs")

    assert envelope["status"] == "ok"
    data = envelope["data"]
    assert data["count"] == 6
    assert len(data["vehicle_inputs"]) == 6
    for vi in data["vehicle_inputs"]:
        assert set(vi.keys()) == {"no", "name", "link", "volume", "interval_count", "volumes"}
        assert vi["volume"] is None
        assert vi["interval_count"] == 0
        assert vi["volumes"] == []
    first = next(vi for vi in data["vehicle_inputs"] if vi["no"] == "1")
    assert first["name"] == "Main St EB"
    assert first["link"] == "101"


def test_get_signal_controllers_on_demo_data_is_empty_but_well_formed():
    """practice.inpx's signalController elements carry no intSupplyData, so
    signal_parser.parse_signal_controllers yields none — the tool must return
    an empty, well-formed list rather than erroring."""
    envelope = _dispatch("get_signal_controllers")

    assert envelope["status"] == "ok"
    data = envelope["data"]
    assert data == {"count": 0, "signals": []}


def test_get_conflict_areas_on_demo_data_is_empty_but_well_formed():
    """The demo .inpx carries no <conflictArea> elements at all — the tool
    must return an empty, well-formed list rather than erroring (v2 unit ④).

    D1 live-demo fix (docs/phase2/DECISIONS.md, 2026-07-27): an unfiltered
    call now always carries an explicit ``truncated`` flag (false here — an
    empty result is trivially not truncated), no ``hint`` key when nothing
    was cut."""
    envelope = _dispatch("get_conflict_areas")

    assert envelope["status"] == "ok"
    data = envelope["data"]
    assert data == {"count": 0, "conflict_areas": [], "truncated": False}
    assert "hint" not in data


# ---------------------------------------------------------------------------
# get_conflict_areas / _parse_conflict_areas — synthetic fixture with real
# conflictArea elements (v2 unit ④; the demo network has none)
# ---------------------------------------------------------------------------

_CONFLICT_AREA_FIXTURE_INPX = """<?xml version="1.0" encoding="UTF-8"?>
<network>
  <links>
    <link no="3" name="Main St EB"><lanes><lane width="3.5"/></lanes></link>
    <link no="12" name="Side St N"><lanes><lane width="3.5"/></lanes></link>
    <link no="21" name="Side St S"><lanes><lane width="3.5"/></lanes></link>
  </links>
  <conflictAreas>
    <conflictArea no="3" linkA="3" linkB="12" status="PASSIVE"/>
    <conflictArea no="7" linkA="3" linkB="21" status="AHASRIGHTOFWAY"/>
  </conflictAreas>
</network>
"""


@pytest.fixture()
def conflict_area_fixture_inpx(tmp_path: Path) -> Path:
    path = tmp_path / "conflict_areas.inpx"
    path.write_text(_CONFLICT_AREA_FIXTURE_INPX, encoding="utf-8")
    return path


def test_get_conflict_areas_on_real_conflict_area_elements(conflict_area_fixture_inpx: Path):
    tools_network.set_active_inpx(conflict_area_fixture_inpx)

    envelope = _dispatch("get_conflict_areas")

    assert envelope["status"] == "ok"
    data = envelope["data"]
    assert data["count"] == 2
    by_no = {ca["no"]: ca for ca in data["conflict_areas"]}
    assert by_no["3"] == {
        "no": "3",
        "link_a": "3",
        "link_b": "12",
        "link_pair": ("3", "12"),  # dispatch() is in-process, no JSON round-trip yet
        "status": "PASSIVE",
        "name": "Link 3 vs Link 12",
    }
    assert by_no["7"]["status"] == "AHASRIGHTOFWAY"
    assert by_no["7"]["link_pair"] == ("3", "21")
    assert data["truncated"] is False  # well under the 40-item cap
    assert "hint" not in data


# ---------------------------------------------------------------------------
# D1 live-demo fix (docs/phase2/DECISIONS.md, 2026-07-27): conflict_area_no/
# link_no server-side filters, and a bounded (capped + explicitly truncated)
# default for an unfiltered call on a large network -- so the model can
# narrow BEFORE the envelope is built instead of always getting (and having
# planner.py's renderer separately truncate) the whole unbounded list.
# ---------------------------------------------------------------------------


def test_get_conflict_areas_filters_by_conflict_area_no(conflict_area_fixture_inpx: Path):
    tools_network.set_active_inpx(conflict_area_fixture_inpx)

    envelope = _dispatch("get_conflict_areas", conflict_area_no="7")

    assert envelope["status"] == "ok"
    data = envelope["data"]
    assert data["count"] == 1
    assert data["conflict_areas"][0]["no"] == "7"
    assert data["conflict_areas"][0]["status"] == "AHASRIGHTOFWAY"
    # A filtered call is already bounded by the model's own query -- no cap
    # bookkeeping needed.
    assert "truncated" not in data
    assert "hint" not in data


def test_get_conflict_areas_filters_by_conflict_area_no_no_match_is_empty_not_an_error(
    conflict_area_fixture_inpx: Path,
):
    tools_network.set_active_inpx(conflict_area_fixture_inpx)

    envelope = _dispatch("get_conflict_areas", conflict_area_no="9999")

    assert envelope["status"] == "ok"
    assert envelope["data"] == {"count": 0, "conflict_areas": []}


def test_get_conflict_areas_filters_by_link_no_matches_either_side_of_the_pair(
    conflict_area_fixture_inpx: Path,
):
    tools_network.set_active_inpx(conflict_area_fixture_inpx)

    # link 3 is link_a on BOTH fixture conflict areas (no=3: 3 vs 12, no=7: 3
    # vs 21) -- link_no must match either side, not just link_a.
    envelope = _dispatch("get_conflict_areas", link_no="21")

    assert envelope["status"] == "ok"
    data = envelope["data"]
    assert data["count"] == 1
    assert data["conflict_areas"][0]["no"] == "7"


def test_get_conflict_areas_rejects_unexpected_params(conflict_area_fixture_inpx: Path):
    tools_network.set_active_inpx(conflict_area_fixture_inpx)

    envelope = _dispatch("get_conflict_areas", bogus=1)

    assert envelope["status"] == "error"
    assert envelope["error_type"] == "invalid_params"


_MANY_CONFLICT_AREAS_INPX_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<network>
  <links></links>
  <conflictAreas>
{areas}
  </conflictAreas>
</network>
"""


def _many_conflict_areas_inpx(tmp_path: Path, count: int) -> Path:
    """A synthetic .inpx with ``count`` real ``<conflictArea>`` elements --
    parsing has no cross-reference to a matching ``<link>`` element (see
    ``_parse_conflict_areas``), so no ``<links>`` content is needed."""
    areas = "\n".join(
        f'    <conflictArea no="{i}" linkA="{i}" linkB="{i + 1000}" status="PASSIVE"/>'
        for i in range(1, count + 1)
    )
    path = tmp_path / f"many_conflict_areas_{count}.inpx"
    path.write_text(_MANY_CONFLICT_AREAS_INPX_TEMPLATE.format(areas=areas), encoding="utf-8")
    return path


def test_get_conflict_areas_unfiltered_call_on_large_network_is_capped_and_truncated(
    tmp_path: Path,
):
    tools_network.set_active_inpx(_many_conflict_areas_inpx(tmp_path, 224))

    envelope = _dispatch("get_conflict_areas")

    assert envelope["status"] == "ok"
    data = envelope["data"]
    assert data["count"] == 224  # the TRUE total, never hidden
    assert len(data["conflict_areas"]) == tools_network._CONFLICT_AREAS_DEFAULT_CAP
    assert data["truncated"] is True
    assert "40" in data["hint"] and "224" in data["hint"]
    # A filtered follow-up narrows to exactly what was asked, proving the
    # hint's own suggestion actually works.
    followup = _dispatch("get_conflict_areas", conflict_area_no="150")
    assert followup["data"]["count"] == 1
    assert followup["data"]["conflict_areas"][0]["no"] == "150"


def test_get_conflict_areas_unfiltered_call_under_cap_is_not_truncated(tmp_path: Path):
    tools_network.set_active_inpx(_many_conflict_areas_inpx(tmp_path, 10))

    envelope = _dispatch("get_conflict_areas")

    data = envelope["data"]
    assert data["count"] == 10
    assert len(data["conflict_areas"]) == 10
    assert data["truncated"] is False
    assert "hint" not in data


def test_build_shadow_model_parses_conflict_areas_from_real_xml(conflict_area_fixture_inpx: Path):
    shadow = tools_network.build_shadow_model(conflict_area_fixture_inpx)

    assert len(shadow["conflict_areas"]) == 2
    passive = [ca for ca in shadow["conflict_areas"] if ca["status"] == "PASSIVE"]
    assert len(passive) == 1
    assert passive[0]["link_pair"] == ("3", "12")  # a real tuple at the unit level (pre-JSON)


def test_resolve_object_finds_conflict_area_by_display_name(conflict_area_fixture_inpx: Path):
    """The synthesized 'Link A vs Link B' display name makes conflict areas
    reachable through the same lexical resolution every other object type
    gets — part of "make conflict areas visible to the agent"."""
    tools_network.set_active_inpx(conflict_area_fixture_inpx)

    envelope = _dispatch("resolve_object", query="Link 3 vs Link 12", object_type="conflict_area")

    assert envelope["status"] == "ok"
    data = envelope["data"]
    assert data["found"] is True
    assert data["candidates"][0]["id"] == "3"
    assert data["candidates"][0]["object_type"] == "conflict_area"


def test_get_network_overview_includes_conflict_areas_section(conflict_area_fixture_inpx: Path):
    tools_network.set_active_inpx(conflict_area_fixture_inpx)

    envelope = _dispatch("get_network_overview")

    data = envelope["data"]
    assert data["conflict_areas"]["count"] == 2
    ids = {item["id"] for item in data["conflict_areas"]["items"]}
    assert ids == {"3", "7"}


# ---------------------------------------------------------------------------
# vehicle_inputs — multi-interval volumes (v2 unit ⑤)
# ---------------------------------------------------------------------------

_MULTI_INTERVAL_FIXTURE_INPX = """<?xml version="1.0" encoding="UTF-8"?>
<network>
  <links>
    <link no="11" name="Border Rd"><lanes><lane width="3.5"/></lanes></link>
  </links>
  <vehicleInputs>
    <vehicleInput anmFlag="false" link="11" name="" no="2">
      <timeIntVehVols>
        <timeIntervalVehVolume cont="false" timeInt="1 0" vehComp="100" volType="EXACT" volume="1000"/>
        <timeIntervalVehVolume cont="true" timeInt="1 1200000" vehComp="100" volType="EXACT" volume="1000"/>
        <timeIntervalVehVolume cont="true" timeInt="1 1800000" vehComp="100" volType="EXACT" volume="1000"/>
      </timeIntVehVols>
    </vehicleInput>
    <vehicleInput anmFlag="false" link="11" name="single" no="9"/>
  </vehicleInputs>
</network>
"""


@pytest.fixture()
def multi_interval_fixture_inpx(tmp_path: Path) -> Path:
    path = tmp_path / "multi_interval.inpx"
    path.write_text(_MULTI_INTERVAL_FIXTURE_INPX, encoding="utf-8")
    return path


def test_get_vehicle_inputs_parses_multi_interval_volumes(multi_interval_fixture_inpx: Path):
    # v2 unit ⑤: closes the vehicle_input_multi_interval_write_gap
    # CATALOG-GAP -- interval_count/volumes are now real, parsed straight
    # from every <timeIntervalVehVolume> child in document order.
    tools_network.set_active_inpx(multi_interval_fixture_inpx)

    envelope = _dispatch("get_vehicle_inputs")

    assert envelope["status"] == "ok"
    data = envelope["data"]
    multi = next(vi for vi in data["vehicle_inputs"] if vi["no"] == "2")
    assert multi["volume"] == 1000.0  # backward-compat: interval 1's volume
    assert multi["interval_count"] == 3
    assert multi["volumes"] == [
        {"interval_no": 1, "from_s": 0.0, "volume": 1000.0, "continuous": False},
        {"interval_no": 2, "from_s": 1200.0, "volume": 1000.0, "continuous": True},
        {"interval_no": 3, "from_s": 1800.0, "volume": 1000.0, "continuous": True},
    ]

    single = next(vi for vi in data["vehicle_inputs"] if vi["no"] == "9")
    assert single["volume"] is None
    assert single["interval_count"] == 0
    assert single["volumes"] == []


# ---------------------------------------------------------------------------
# get_routing_decisions / _parse_routing_decisions — v2 unit ⑥
# ---------------------------------------------------------------------------


def test_get_routing_decisions_on_demo_data():
    # Unlike signals/conflict_areas, the demo .inpx DOES carry static routing
    # decisions (2 decisions, 3 routes each) -- verified directly against
    # data/practice.inpx. Its <vehicleRouteStatic> elements carry no
    # <linkSeq> child at all (unlike a real Vissim export), so every route
    # parses as disconnected=False (vacuously — nothing to check) and
    # link_seq == [].
    envelope = _dispatch("get_routing_decisions")

    assert envelope["status"] == "ok"
    data = envelope["data"]
    assert data["count"] == 2
    first = next(d for d in data["routing_decisions"] if d["no"] == "1")
    assert first["link"] == "101"
    assert {r["route_no"] for r in first["routes"]} == {"1", "2", "3"}
    for route in first["routes"]:
        assert set(route.keys()) == {
            "route_no",
            "dest_link",
            "dest_pos",
            "rel_flow",
            "interval_count",
            "rel_flows",
            "link_seq",
            "disconnected",
        }
        assert route["link_seq"] == []
        assert route["disconnected"] is False
    route_1 = next(r for r in first["routes"] if r["route_no"] == "1")
    assert route_1["dest_link"] == "140"
    assert route_1["rel_flow"] == 780.0
    assert route_1["interval_count"] == 1
    assert route_1["rel_flows"] == [{"interval_no": 1, "from_s": 0.0, "rel_flow": 780.0}]


def test_get_routing_decisions_rejects_unexpected_params():
    envelope = _dispatch("get_routing_decisions", bogus=1)

    assert envelope["status"] == "error"
    assert envelope["error_type"] == "invalid_params"


# Synthetic fixture with real <linkSeq> children (the demo .inpx has none) --
# covers multi-interval relFlow, a single-route decision, and a genuinely
# disconnected route (a linkSeq key that is NOT among the network's links).
_ROUTING_DECISION_FIXTURE_INPX = """<?xml version="1.0" encoding="UTF-8"?>
<network>
  <links>
    <link no="101" name="Approach"><lanes><lane width="3.5"/></lanes></link>
    <link no="140" name="Exit A"><lanes><lane width="3.5"/></lanes></link>
    <link no="141" name="Exit B"><lanes><lane width="3.5"/></lanes></link>
  </links>
  <timeIntervalSets>
    <timeIntervalSet no="VEHICLEROUTESTATIC">
      <timeIntervals>
        <timeInterval start="0"/>
        <timeInterval start="5400"/>
      </timeIntervals>
    </timeIntervalSet>
  </timeIntervalSets>
  <vehicleRoutingDecisionsStatic>
    <vehicleRoutingDecisionStatic no="1" name="Main decision" link="101" pos="1.0">
      <vehRoutSta>
        <vehicleRouteStatic no="1" name="" destLink="140" destPos="2.0" formula=""
          relFlow="2 0:45, 2 5400000:45">
          <linkSeq>
            <intObjectRef key="101"/>
            <intObjectRef key="140"/>
          </linkSeq>
        </vehicleRouteStatic>
        <vehicleRouteStatic no="2" name="" destLink="141" destPos="3.0" formula=""
          relFlow="2 0:55">
          <linkSeq>
            <intObjectRef key="101"/>
            <intObjectRef key="9999"/>
            <intObjectRef key="141"/>
          </linkSeq>
        </vehicleRouteStatic>
      </vehRoutSta>
    </vehicleRoutingDecisionStatic>
    <vehicleRoutingDecisionStatic no="2" name="Single-route decision" link="101" pos="4.0">
      <vehRoutSta>
        <vehicleRouteStatic no="1" name="" destLink="140" destPos="5.0" formula=""
          relFlow="2 0:568">
          <linkSeq>
            <intObjectRef key="101"/>
            <intObjectRef key="140"/>
          </linkSeq>
        </vehicleRouteStatic>
      </vehRoutSta>
    </vehicleRoutingDecisionStatic>
  </vehicleRoutingDecisionsStatic>
</network>
"""


@pytest.fixture()
def routing_decision_fixture_inpx(tmp_path: Path) -> Path:
    path = tmp_path / "routing_decisions.inpx"
    path.write_text(_ROUTING_DECISION_FIXTURE_INPX, encoding="utf-8")
    return path


def test_get_routing_decisions_parses_multi_interval_rel_flow(routing_decision_fixture_inpx: Path):
    tools_network.set_active_inpx(routing_decision_fixture_inpx)

    envelope = _dispatch("get_routing_decisions")

    assert envelope["status"] == "ok"
    data = envelope["data"]
    assert data["count"] == 2
    decision_1 = next(d for d in data["routing_decisions"] if d["no"] == "1")
    route_1 = next(r for r in decision_1["routes"] if r["route_no"] == "1")
    assert route_1["rel_flow"] == 45.0  # interval 1's value (backward-compat top-level field)
    assert route_1["interval_count"] == 2
    assert route_1["rel_flows"] == [
        {"interval_no": 1, "from_s": 0.0, "rel_flow": 45.0},
        {"interval_no": 2, "from_s": 5400.0, "rel_flow": 45.0},
    ]
    assert route_1["disconnected"] is False


def test_get_routing_decisions_flags_a_route_referencing_a_missing_link(
    routing_decision_fixture_inpx: Path,
):
    # route no=2 on decision 1's linkSeq references link 9999, which does not
    # exist in the fixture's <links> section -- the exact "broken route"
    # shape route_integrity_unverifiable (constraints.py #31) needs.
    tools_network.set_active_inpx(routing_decision_fixture_inpx)

    envelope = _dispatch("get_routing_decisions")

    data = envelope["data"]
    decision_1 = next(d for d in data["routing_decisions"] if d["no"] == "1")
    route_2 = next(r for r in decision_1["routes"] if r["route_no"] == "2")
    assert route_2["link_seq"] == ["101", "9999", "141"]
    assert route_2["disconnected"] is True

    route_1 = next(r for r in decision_1["routes"] if r["route_no"] == "1")
    assert route_1["disconnected"] is False  # a real sibling stays unaffected


def test_get_routing_decisions_single_route_decision(routing_decision_fixture_inpx: Path):
    tools_network.set_active_inpx(routing_decision_fixture_inpx)

    envelope = _dispatch("get_routing_decisions")

    data = envelope["data"]
    decision_2 = next(d for d in data["routing_decisions"] if d["no"] == "2")
    assert len(decision_2["routes"]) == 1
    assert decision_2["routes"][0]["rel_flow"] == 568.0


def test_build_shadow_model_flattens_routes_with_synthesized_ids(
    routing_decision_fixture_inpx: Path,
):
    shadow = tools_network.build_shadow_model(routing_decision_fixture_inpx)

    assert len(shadow["routes"]) == 3
    by_id = {r["id"]: r for r in shadow["routes"]}
    assert set(by_id) == {"1.1", "1.2", "2.1"}
    assert by_id["1.2"]["decision_no"] == "1"
    assert by_id["1.2"]["route_no"] == "2"
    assert by_id["1.2"]["disconnected"] is True
    # v2 unit ⑥: manually_scaled_this_turn is a documented, permanently-open
    # gap (turn-scoped orchestrator state with no XML representation) --
    # always False from a pure reparse.
    assert all(r["manually_scaled_this_turn"] is False for r in shadow["routes"])


def test_route_integrity_unverifiable_fires_for_real_against_a_broken_link_seq(
    routing_decision_fixture_inpx: Path,
):
    """End-to-end proof that v2 unit ⑥ actually WAKES constraints.py's
    dormant route_integrity_unverifiable (#31) check, not just that
    tools_network.py computes `disconnected` in isolation: feed a REAL
    build_shadow_model() output (this module's own production code path,
    not a hand-rolled constraints.py fixture) into the real
    ``services.agent.constraints.run_checks``."""
    from services.agent import constraints

    shadow = tools_network.build_shadow_model(routing_decision_fixture_inpx)
    action = {"tool": "set_route_relative_flow", "params": {}}

    findings = constraints.run_checks("postwrite", action, shadow, {})

    matches = [f for f in findings if f.constraint_id == "route_integrity_unverifiable"]
    assert len(matches) == 1
    assert "1.2" in matches[0].messages["en"]  # the synthesized id of the broken route


def test_resolve_object_finds_routing_decision_by_name(routing_decision_fixture_inpx: Path):
    tools_network.set_active_inpx(routing_decision_fixture_inpx)

    envelope = _dispatch("resolve_object", query="Single-route decision", object_type="routing_decision")

    assert envelope["status"] == "ok"
    data = envelope["data"]
    assert data["found"] is True
    assert data["candidates"][0]["id"] == "2"
    assert data["candidates"][0]["object_type"] == "routing_decision"


def test_get_network_overview_includes_routing_decisions_section(
    routing_decision_fixture_inpx: Path,
):
    tools_network.set_active_inpx(routing_decision_fixture_inpx)

    envelope = _dispatch("get_network_overview")

    data = envelope["data"]
    assert data["routing_decisions"]["count"] == 2
    ids = {item["id"] for item in data["routing_decisions"]["items"]}
    assert ids == {"1", "2"}


# ---------------------------------------------------------------------------
# get_vehicle_compositions / _parse_vehicle_compositions — v2 unit ⑦
# ---------------------------------------------------------------------------


def test_get_vehicle_compositions_on_demo_data_is_empty_but_well_formed():
    # The demo .inpx carries no <vehicleComposition> elements at all -- the
    # tool must return an empty, well-formed list rather than erroring, same
    # shape as get_conflict_areas on the demo network (v2 unit ④).
    envelope = _dispatch("get_vehicle_compositions")

    assert envelope["status"] == "ok"
    assert envelope["data"] == {"count": 0, "vehicle_compositions": [], "truncated": False}
    assert "hint" not in envelope["data"]


_VEHICLE_COMPOSITION_FIXTURE_INPX = """<?xml version="1.0" encoding="UTF-8"?>
<network>
  <links>
    <link no="11" name="Border Rd"><lanes><lane width="3.5"/></lanes></link>
  </links>
  <vehicleCompositions>
    <vehicleComposition no="1" name="5% HGV, 5% LGV, 5% Bus">
      <vehCompRelFlows>
        <vehicleCompositionRelativeFlow desSpeedDistr="65" relFlow="0.85" vehType="100"/>
        <vehicleCompositionRelativeFlow desSpeedDistr="50" relFlow="0.05" vehType="190"/>
        <vehicleCompositionRelativeFlow desSpeedDistr="50" relFlow="0.05" vehType="200"/>
        <vehicleCompositionRelativeFlow desSpeedDistr="50" relFlow="0.05" vehType="300"/>
      </vehCompRelFlows>
    </vehicleComposition>
    <vehicleComposition no="4" name="Cycle">
      <vehCompRelFlows>
        <vehicleCompositionRelativeFlow desSpeedDistr="15" relFlow="1" vehType="600"/>
      </vehCompRelFlows>
    </vehicleComposition>
  </vehicleCompositions>
  <vehicleInputs>
    <vehicleInput anmFlag="false" link="11" name="" no="1">
      <timeIntVehVols>
        <timeIntervalVehVolume cont="false" timeInt="1 0" vehComp="1" volType="STOCHASTIC" volume="1838"/>
      </timeIntVehVols>
    </vehicleInput>
    <vehicleInput anmFlag="false" link="11" name="" no="2">
      <timeIntVehVols>
        <timeIntervalVehVolume cont="false" timeInt="1 0" vehComp="1" volType="STOCHASTIC" volume="1446"/>
      </timeIntVehVols>
    </vehicleInput>
  </vehicleInputs>
</network>
"""


@pytest.fixture()
def vehicle_composition_fixture_inpx(tmp_path: Path) -> Path:
    path = tmp_path / "vehicle_compositions.inpx"
    path.write_text(_VEHICLE_COMPOSITION_FIXTURE_INPX, encoding="utf-8")
    return path


def test_get_vehicle_compositions_on_real_elements(vehicle_composition_fixture_inpx: Path):
    # Ground truth mirrors Urban Intersection Beijing.CN's real compositions
    # 1 and 4, per the v2 unit ⑦ live probe (backend/tools/
    # probe_vehicle_compositions.py).
    tools_network.set_active_inpx(vehicle_composition_fixture_inpx)

    envelope = _dispatch("get_vehicle_compositions")

    assert envelope["status"] == "ok"
    data = envelope["data"]
    assert data["count"] == 2
    by_no = {c["no"]: c for c in data["vehicle_compositions"]}

    comp_1 = by_no["1"]
    assert comp_1["name"] == "5% HGV, 5% LGV, 5% Bus"
    assert comp_1["referencing_vehicle_input_nos"] == ["1", "2"]
    entries_by_veh_type = {e["veh_type"]: e for e in comp_1["entries"]}
    assert entries_by_veh_type["100"]["rel_flow"] == 0.85
    assert entries_by_veh_type["100"]["des_speed_distr"] == "65"
    # estimated_share_pct is DERIVED (rel_flow / sum of this composition's
    # entries), not read verbatim off relFlow -- 0.85 / 1.0 == 85%.
    assert entries_by_veh_type["100"]["estimated_share_pct"] == 85.0
    assert entries_by_veh_type["190"]["estimated_share_pct"] == 5.0

    comp_4 = by_no["4"]
    assert comp_4["referencing_vehicle_input_nos"] == []  # nothing references "Cycle" in this fixture
    assert comp_4["entries"][0]["estimated_share_pct"] == 100.0  # only entry -> always 100%

    assert data["truncated"] is False
    assert "hint" not in data


def test_get_vehicle_compositions_filters_by_composition_no(vehicle_composition_fixture_inpx: Path):
    tools_network.set_active_inpx(vehicle_composition_fixture_inpx)

    envelope = _dispatch("get_vehicle_compositions", composition_no="4")

    assert envelope["status"] == "ok"
    data = envelope["data"]
    assert data["count"] == 1
    assert data["vehicle_compositions"][0]["no"] == "4"
    # A filtered call is already bounded by the model's own query -- no cap
    # bookkeeping needed (matches get_conflict_areas' precedent).
    assert "truncated" not in data
    assert "hint" not in data


def test_get_vehicle_compositions_filters_by_composition_no_no_match_is_empty_not_an_error(
    vehicle_composition_fixture_inpx: Path,
):
    tools_network.set_active_inpx(vehicle_composition_fixture_inpx)

    envelope = _dispatch("get_vehicle_compositions", composition_no="9999")

    assert envelope["status"] == "ok"
    assert envelope["data"] == {"count": 0, "vehicle_compositions": []}


def test_get_vehicle_compositions_rejects_unexpected_params(vehicle_composition_fixture_inpx: Path):
    tools_network.set_active_inpx(vehicle_composition_fixture_inpx)

    envelope = _dispatch("get_vehicle_compositions", bogus=1)

    assert envelope["status"] == "error"
    assert envelope["error_type"] == "invalid_params"


_MANY_VEHICLE_COMPOSITIONS_INPX_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<network>
  <links></links>
  <vehicleCompositions>
{compositions}
  </vehicleCompositions>
</network>
"""


def _many_vehicle_compositions_inpx(tmp_path: Path, count: int) -> Path:
    compositions = "\n".join(
        f'    <vehicleComposition no="{i}" name="Comp {i}">'
        f'<vehCompRelFlows><vehicleCompositionRelativeFlow desSpeedDistr="50" relFlow="1" '
        f'vehType="100"/></vehCompRelFlows></vehicleComposition>'
        for i in range(1, count + 1)
    )
    path = tmp_path / f"many_vehicle_compositions_{count}.inpx"
    path.write_text(_MANY_VEHICLE_COMPOSITIONS_INPX_TEMPLATE.format(compositions=compositions), encoding="utf-8")
    return path


def test_get_vehicle_compositions_unfiltered_call_on_large_network_is_capped_and_truncated(
    tmp_path: Path,
):
    tools_network.set_active_inpx(_many_vehicle_compositions_inpx(tmp_path, 60))

    envelope = _dispatch("get_vehicle_compositions")

    assert envelope["status"] == "ok"
    data = envelope["data"]
    assert data["count"] == 60  # the TRUE total, never hidden
    assert len(data["vehicle_compositions"]) == tools_network._VEHICLE_COMPOSITIONS_DEFAULT_CAP
    assert data["truncated"] is True
    assert "40" in data["hint"] and "60" in data["hint"]
    followup = _dispatch("get_vehicle_compositions", composition_no="50")
    assert followup["data"]["count"] == 1
    assert followup["data"]["vehicle_compositions"][0]["no"] == "50"


def test_get_vehicle_compositions_unfiltered_call_under_cap_is_not_truncated(tmp_path: Path):
    tools_network.set_active_inpx(_many_vehicle_compositions_inpx(tmp_path, 5))

    envelope = _dispatch("get_vehicle_compositions")

    data = envelope["data"]
    assert data["count"] == 5
    assert len(data["vehicle_compositions"]) == 5
    assert data["truncated"] is False
    assert "hint" not in data


def test_build_shadow_model_flattens_vehicle_composition_entries_with_synthesized_ids(
    vehicle_composition_fixture_inpx: Path,
):
    shadow = tools_network.build_shadow_model(vehicle_composition_fixture_inpx)

    assert len(shadow["vehicle_composition_entries"]) == 5  # 4 entries on comp 1 + 1 on comp 4
    by_id = {e["id"]: e for e in shadow["vehicle_composition_entries"]}
    assert set(by_id) == {"1.100", "1.190", "1.200", "1.300", "4.600"}
    assert by_id["1.100"]["composition_no"] == "1"
    assert by_id["1.100"]["veh_type"] == "100"
    assert by_id["1.100"]["rel_flow"] == 0.85


def test_resolve_object_finds_vehicle_composition_by_name(vehicle_composition_fixture_inpx: Path):
    tools_network.set_active_inpx(vehicle_composition_fixture_inpx)

    envelope = _dispatch("resolve_object", query="Cycle", object_type="vehicle_composition")

    assert envelope["status"] == "ok"
    data = envelope["data"]
    assert data["found"] is True
    assert data["candidates"][0]["id"] == "4"
    assert data["candidates"][0]["object_type"] == "vehicle_composition"


def test_get_network_overview_includes_vehicle_compositions_section(
    vehicle_composition_fixture_inpx: Path,
):
    tools_network.set_active_inpx(vehicle_composition_fixture_inpx)

    envelope = _dispatch("get_network_overview")

    data = envelope["data"]
    assert data["vehicle_compositions"]["count"] == 2
    ids = {item["id"] for item in data["vehicle_compositions"]["items"]}
    assert ids == {"1", "4"}


# ---------------------------------------------------------------------------
# build_shadow_model — the extended shadow model builder (unit-level)
# ---------------------------------------------------------------------------


def test_build_shadow_model_has_exactly_the_contract_keys():
    shadow = tools_network.build_shadow_model(Path(tools_network._active_inpx_path()))

    assert set(shadow.keys()) == {
        "links",
        "nodes",
        "vehicle_inputs",
        "signals",
        "conflict_areas",
        "routing_decisions",
        "routes",
        "vehicle_compositions",
        "vehicle_composition_entries",
    }
    assert len(shadow["links"]) == 16  # 14 real + 2 Phase 3 connectors
    assert len(shadow["nodes"]) == 2
    assert len(shadow["vehicle_inputs"]) == 6
    assert shadow["signals"] == []
    # v2 unit ④: the demo .inpx carries no <conflictArea> elements at all —
    # the shadow key must still be present and well-formed (empty list, not
    # absent/None), the same "no signal controllers" shape signals already has.
    assert shadow["conflict_areas"] == []
    # v2 unit ⑥: unlike signals/conflict_areas, the demo .inpx DOES carry
    # static routing decisions (2 decisions, 3 routes each = 6 flat routes) —
    # verified directly against data/practice.inpx before pinning these
    # numbers.
    assert len(shadow["routing_decisions"]) == 2
    assert len(shadow["routes"]) == 6
    assert {r["route_no"] for r in shadow["routing_decisions"][0]["routes"]} == {"1", "2", "3"}
    # the demo .inpx's <vehicleRouteStatic> elements carry no <linkSeq> child
    # at all (unlike a real Vissim export) -- every route parses as NOT
    # disconnected (empty link_seq is vacuously "connected", not unknown).
    assert all(r["disconnected"] is False for r in shadow["routes"])
    # v2 unit ⑦: the demo .inpx carries no <vehicleComposition> elements at
    # all -- both shadow keys must still be present and well-formed (empty
    # list, not absent/None), same shape as signals/conflict_areas.
    assert shadow["vehicle_compositions"] == []
    assert shadow["vehicle_composition_entries"] == []


def test_build_shadow_model_is_mtime_cached_across_repeat_calls():
    path = tools_network._active_inpx_path()
    first = tools_network.build_shadow_model(path)
    second = tools_network.build_shadow_model(path)
    assert first is second  # same lru_cache hit, not just equal


def test_signal_to_contract_shape_carries_controller_type_but_omits_min_green_sum():
    """v2 unit ① closed the controller_type gap: the shadow model now carries
    it ("unknown" when the parsed info lacks it — constraint #4's refuse-on-
    unknown stance). min_green_intergreen_sum (#5) remains a separate, still-
    open gap and must stay OMITTED entirely (not set to None)."""
    synthetic_signal_info = {
        "sc_no": "1",
        "sc_name": "SC1",
        "cycle_time": 90,
        "num_signal_groups": 4,
        "num_phases": 2,
        "phases": [],
    }

    shaped = tools_network._signal_to_contract_shape(synthetic_signal_info)

    assert shaped == {
        "sc_no": "1",
        "sc_name": "SC1",
        "cycle_time": 90,
        "phases": [],
        "controller_type": "unknown",
    }
    assert "min_green_intergreen_sum" not in shaped


# ---------------------------------------------------------------------------
# get_run_kpis
# ---------------------------------------------------------------------------


def test_get_run_kpis_returns_compact_summary_for_known_scenario():
    envelope = _dispatch("get_run_kpis", scenario_id="S000001")

    assert envelope["status"] == "ok"
    data = envelope["data"]
    assert data["found"] is True
    assert data["scenario_id"] == "S000001"
    assert data["scenario_name"] == "Base (AM Peak)"
    assert data["run_count"] == 3
    metrics = data["metrics"]
    assert set(metrics.keys()) <= {"avg_delay_s", "avg_speed_kmh", "avg_stops", "total_travel_time_s"}
    assert metrics["avg_delay_s"] == pytest.approx(32.556666666666665)
    assert metrics["avg_speed_kmh"] == pytest.approx(38.516666666666666)


def test_get_run_kpis_never_leaks_db_paths():
    envelope = _dispatch("get_run_kpis", scenario_id="S000001")

    rendered = str(envelope["data"])
    assert ".db" not in rendered
    assert "/" not in rendered
    assert "\\" not in rendered


def test_get_run_kpis_unknown_scenario_returns_structured_not_found():
    envelope = _dispatch("get_run_kpis", scenario_id="BOGUS_SCENARIO")

    assert envelope["status"] == "ok"  # a domain miss is still a successful tool call
    data = envelope["data"]
    assert data["found"] is False
    assert data["error_type"] == "scenario_not_found"
    assert "S000001" in data["candidates"]


def test_get_run_kpis_never_forwards_raw_result_fields_like_db_path(monkeypatch):
    """get_run_kpis hand-picks its output fields (scenario_id, scenario_name,
    run_count, metrics) from get_scenario_results' return value — a raw key
    like 'db_path' living alongside 'network'/'run_count' in that dict is
    simply never read, regardless of the sanitizer. This documents that
    behavior directly rather than exercising the sanitizer (see the test
    below for that)."""
    import services.agent.tools_metrics as tools_metrics_module

    def fake_get_scenario_results(scenario_id):
        return {
            "run_count": 1,
            "network": {"DELAYAVG": 12.0, "SPEEDAVG": 40.0},
            "db_path": "/Users/someone/data/Scenarios/S000001/run.db",
        }

    monkeypatch.setattr(
        tools_metrics_module.results_reader, "get_scenario_results", fake_get_scenario_results
    )

    envelope = _dispatch("get_run_kpis", scenario_id="S000001")

    assert "db_path" not in envelope["data"]
    assert "run.db" not in str(envelope["data"])


def test_get_run_kpis_sanitizer_strips_a_path_like_value_in_a_forwarded_field(monkeypatch):
    """Unlike the test above, this exercises the sanitizer for real: inject a
    genuine path into scenario_name — a field get_run_kpis DOES forward —
    via a patched list_scenarios(), and confirm _strip_path_like removes it
    rather than letting it reach the planner."""
    import services.agent.tools_metrics as tools_metrics_module

    poisoned_name = "/Users/someone/data/Scenarios/S000001/run1.db"

    def fake_list_scenarios():
        return [{"id": "S000001", "name": poisoned_name, "color": "#000000"}]

    monkeypatch.setattr(tools_metrics_module.results_reader, "list_scenarios", fake_list_scenarios)

    envelope = _dispatch("get_run_kpis", scenario_id="S000001")

    data = envelope["data"]
    assert data["found"] is True
    # the path-like scenario_name was dropped entirely by the sanitizer
    assert data.get("scenario_name") != poisoned_name
    assert poisoned_name not in str(data)


# ---------------------------------------------------------------------------
# compare_scenarios
# ---------------------------------------------------------------------------


def test_compare_scenarios_returns_network_metrics_and_leaders_and_rankings():
    envelope = _dispatch("compare_scenarios", scenario_ids=["S000001", "S000002", "S000003"])

    assert envelope["status"] == "ok"
    data = envelope["data"]
    assert data["scenario_ids"] == ["S000001", "S000002", "S000003"]
    assert set(data["network_metrics"].keys()) == {"delay", "speed", "stops", "total_travel_time"}
    assert set(data["metric_leaders"].keys()) == {"delay", "speed", "stops", "total_travel_time"}
    assert len(data["scenario_ranking"]) == 3
    assert len(data["top_congested_nodes"]) <= 10
    # UI-card bulk (dashboard tiles) is dropped — this feeds an LLM prompt, not a dashboard
    assert "summary_cards" not in data


def test_compare_scenarios_reports_invalid_ids_without_raising():
    envelope = _dispatch("compare_scenarios", scenario_ids=["S000001", "NOT_A_SCENARIO"])

    assert envelope["status"] == "ok"
    data = envelope["data"]
    assert data["invalid_ids"] == ["NOT_A_SCENARIO"]
    assert data["scenario_ids"] == ["S000001"]


def test_compare_scenarios_never_leaks_db_paths():
    envelope = _dispatch("compare_scenarios", scenario_ids=["S000001", "S000002"])

    assert ".db" not in str(envelope["data"])


def test_compare_scenarios_keeps_km_h_unit_text_and_ranking_reasons_intact_on_real_data():
    """The IMPORTANT-1 regression: a naive path filter that flags any string
    containing '/' would silently delete key_insights' message/unit fields
    and scenario_ranking's reason field, since normal traffic units
    ('km/h', 'veh/h') contain a slash. Assert the actual text, not just
    field presence, so a re-introduced over-broad filter fails loudly."""
    envelope = _dispatch("compare_scenarios", scenario_ids=["S000001", "S000002", "S000003"])

    data = envelope["data"]
    insights_by_id = {insight["id"]: insight for insight in data["key_insights"]}

    speed_leader = insights_by_id["speed-leader"]
    assert speed_leader["message"] == "Signal Optimization leads average network speed at 51.0 km/h."
    assert speed_leader["unit"] == "km/h"

    speed_spread = insights_by_id["speed-spread"]
    assert speed_spread["message"] == "Network average speed varies by 25.4 km/h across compared scenarios."
    assert speed_spread["unit"] == "km/h"

    reasons = {entry["scenario_id"]: entry["reason"] for entry in data["scenario_ranking"]}
    assert reasons == {
        "S000002": "Best normalized delay result at 23.7 s.",
        "S000001": "Lowest normalized total travel time at 51546.0 s.",
        "S000003": "Best normalized delay result at 47.8 s.",
    }


def test_compare_scenarios_strips_a_genuine_db_path_from_synthetic_payload(monkeypatch):
    """The sanitizer must still catch a real path when one does show up,
    proving IMPORTANT-1's fix narrowed the filter rather than disabling it."""
    import services.agent.tools_metrics as tools_metrics_module

    poisoned_path = "/Users/someone/data/Scenarios/S000001/run1.db"

    def fake_get_compare_summary(ids):
        return {
            "requested_ids": ids,
            "scenario_ids": ids,
            "invalid_ids": [],
            "missing_result_ids": [],
            "network_metrics": {},
            "metric_leaders": {},
            "health_scores": [],
            "scenario_ranking": [
                {
                    "scenario_id": "S000001",
                    "reason": "Fastest at 38.5 km/h.",  # legitimate unit text alongside the path
                    "evidence_path": poisoned_path,
                }
            ],
            "top_congested_nodes": [],
            "key_insights": [],
            "status_band": {},
        }

    monkeypatch.setattr(
        tools_metrics_module.results_reader, "get_compare_summary", fake_get_compare_summary
    )

    envelope = _dispatch("compare_scenarios", scenario_ids=["S000001"])

    ranking_entry = envelope["data"]["scenario_ranking"][0]
    assert "evidence_path" not in ranking_entry  # the path-like field was dropped
    assert ranking_entry["reason"] == "Fastest at 38.5 km/h."  # the unit text survived intact
    assert poisoned_path not in str(envelope["data"])


def test_compare_scenarios_empty_scenario_ids_is_graceful_not_a_crash():
    envelope = _dispatch("compare_scenarios", scenario_ids=[])

    assert envelope["status"] == "ok"
    data = envelope["data"]
    assert data["requested_ids"] == []
    assert data["scenario_ids"] == []
    assert data["status_band"]["tone"] == "error"  # "no comparison ready", not a crash


def test_compare_scenarios_rejects_unexpected_params():
    envelope = _dispatch("compare_scenarios", scenario_ids=["S000001"], bogus=1)

    assert envelope["status"] == "error"
    assert envelope["error_type"] == "invalid_params"
