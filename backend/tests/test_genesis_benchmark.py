"""Tests for the topological correspondence benchmark v2 (roadmap R1.6 / row 2.3).

Benchmark v1 matched links by id and reported "0 links removed" between two
networks that share almost nothing — the generator numbers from 1 and so does
the consultant, so unrelated roads read as the same object. Every fixture here
exists to pin a behaviour that failure needed.

Layers, in increasing order of realism:

1. **Hand-built pairs in the common metre frame**, small enough that the
   expected bucket is derived by arithmetic in the docstring rather than
   observed from a run. Coordinates are metres; the default tolerance is 22 m
   (14 georeference residual + 1 projection budget + 7 centreline allowance), so
   every offset below is chosen relative to that number and says which side of
   it it is on.
2. **A round trip through minimal ``.inpx`` XML**, so the loaders and their
   refusals are exercised rather than assumed.
3. **The real pair** — the consultant's Murdoch model against a draft
   regenerated from ``client-data/murdoch.osm``. Skipped with a clear reason on
   a machine that has neither; those are the only skips in this file.

Plus the two invariants the unit is not allowed to break: the buckets partition
the consultant network, and no positional delta reaches the report.

No COM, no Vissim, no licence seat, no network access.
"""
from __future__ import annotations

from pathlib import Path
import re
import subprocess
import sys

import pytest

from services.genesis.benchmark import (
    BEARING_BAND_DEG,
    CENTRELINE_ALLOWANCE_M,
    GEOREF_RESIDUAL_M,
    MATCH_TOLERANCE_M,
    PROJECTION_BUDGET_M,
    AmbiguousObject,
    ChainMember,
    GeneratedScope,
    GeoNetwork,
    GeoObject,
    MatchBucket,
    MatchedPair,
    MissingObject,
    MissingReason,
    TopologicalBenchmark,
    benchmark_network_files,
    benchmark_networks,
    georeferenced_network,
    local_frame_network,
    render_benchmark_report,
)
from services.genesis.diff import DiffSeverity, diff_network_files
from services.genesis.project import LocalTangentPlane

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def _link(object_id: str, points, *, lanes: int = 2, name: str = "", kind: str = "link"):
    return GeoObject(
        id=object_id, name=name, kind=kind, lanes=lanes, points=tuple(points)
    )


def _net(label: str, *objects: GeoObject, **kwargs) -> GeoNetwork:
    return GeoNetwork.from_objects(label, objects, **kwargs)


def _assert_partition(benchmark: TopologicalBenchmark, consultant: GeoNetwork) -> None:
    """The invariant every fixture repeats: three buckets, every object once.

    Also checks the generated side adds up, and that the reported bucket for
    each id agrees with the list it is actually in — a partition that only holds
    in the aggregate would still let one object be filed twice.
    """
    ok, detail = benchmark.partition_check(consultant.objects)
    assert ok, detail
    assert benchmark.consultant_object_count == len(consultant.objects)
    for object_id in consultant.objects:
        assert benchmark.bucket_of(object_id) is not None, object_id
    corridor = benchmark.corridor
    assert corridor is not None
    assert (
        corridor.matched_count
        + len(corridor.in_corridor_unmatched)
        + corridor.out_of_scope_count
        == corridor.generated_total
    )
    assert "bucket_partition_verified" in benchmark.finding_codes()


# ---------------------------------------------------------------------------
# 1. The tolerance is derived, not chosen
# ---------------------------------------------------------------------------


def test_tolerance_is_the_sum_of_its_three_stated_terms():
    """22 m = 14 + 1 + 7, and the 14 is the georeference's own max residual.

    Pinned because the whole method rests on it: if someone widens the
    tolerance the report's provenance sentence must stop being true, loudly.
    """
    assert GEOREF_RESIDUAL_M == 14.0
    assert PROJECTION_BUDGET_M == 1.0
    assert CENTRELINE_ALLOWANCE_M == 7.0
    assert MATCH_TOLERANCE_M == 22.0
    assert MATCH_TOLERANCE_M == GEOREF_RESIDUAL_M + PROJECTION_BUDGET_M + CENTRELINE_ALLOWANCE_M
    # The band has to exclude the cross street (~90 deg) and the reverse
    # carriageway (180 deg) with room to spare, or direction stops separating
    # a road from its opposite twin one lane width away.
    assert BEARING_BAND_DEG * 3 <= 90.0


# ---------------------------------------------------------------------------
# 2. One consultant link <-> a chain of generated links (the normal case)
# ---------------------------------------------------------------------------


def _chain_pair() -> tuple[GeoNetwork, GeoNetwork]:
    """A 150 m consultant link cut into three by the generator, plus a pocket.

    Hand-derivation
    ---------------
    Consultant ``C1`` runs (0,0) -> (150,0), 2 lanes. The generated draft cuts
    the same road at OSM nodes into ``G1`` [0,50], ``G2`` [50,100], ``G3``
    [100,150], each drawn 1 m north of the consultant's centreline (1 m << 22 m
    tolerance, and 1 m is what a real centreline disagreement looks like).

    * every G sample is 1 m from C1, so member coverage is 11/11 = 1.0 >= 0.8;
    * every chord is (+x), C1's local direction is (+x), so the angle is 0 <= 30;
    * all three laterals are +1, differing by 0 <= 22, so they form ONE cluster;
    * the cluster's arc union is [0,150] = 100% of C1 >= 80% -> MATCHED;
    * greedy cover from 0 takes G1 (reaches 50), then G2 (100), then G3 (150),
      so the named chain is exactly G1 + G2 + G3 in travel order;
    * same-kind lanes are [2,2,2], equal to C1's 2 -> lane agreement.

    Consultant ``C2`` is a 30 m storage lane the consultant coded from (200,0)
    to (230,0) — the turn pocket OSM does not carry. Its nearest generated
    vertex is (150,1), 50.0 m away, and 50 > 22, so nothing is even a candidate:
    MISSING with reason ``no_candidates`` and 0 near.
    """
    consultant = _net(
        "consultant",
        _link("C1", [(0, 0), (150, 0)], lanes=2, name="Main St"),
        _link("C2", [(200, 0), (230, 0)], lanes=1, name="Main St turn pocket"),
    )
    generated = _net(
        "generated",
        _link("G1", [(0, 1), (50, 1)], lanes=2),
        _link("G2", [(50, 1), (100, 1)], lanes=2),
        _link("G3", [(100, 1), (150, 1)], lanes=2),
    )
    return consultant, generated


def test_one_consultant_link_matches_a_chain_of_three_generated_links():
    consultant, generated = _chain_pair()
    benchmark = benchmark_networks(consultant, generated)
    _assert_partition(benchmark, consultant)

    assert benchmark.bucket_of("C1") is MatchBucket.MATCHED
    pair = next(p for p in benchmark.matched or [] if p.consultant_id == "C1")
    assert pair.chain_ids == ["G1", "G2", "G3"]
    assert pair.consultant_name == "Main St"
    assert pair.lane_comparable and pair.lane_agreement
    assert (pair.chain_lanes_min, pair.chain_lanes_max) == (2, 2)
    assert not pair.shorter_than_tolerance


def test_a_pocket_lane_the_draft_does_not_have_is_named_missing_with_its_road_name():
    consultant, generated = _chain_pair()
    benchmark = benchmark_networks(consultant, generated)

    assert benchmark.bucket_of("C2") is MatchBucket.MISSING
    missing = next(m for m in benchmark.missing or [] if m.consultant_id == "C2")
    assert missing.reason is MissingReason.NO_CANDIDATES
    assert missing.candidate_count == 0
    assert missing.best_chain_coverage_pct == 0
    assert missing.consultant_name == "Main St turn pocket"
    # The missing list is the deliverable, so it must survive rendering.
    assert "Main St turn pocket" in render_benchmark_report(benchmark, lang="en")


def test_every_generated_link_in_the_chain_is_reported_as_matched_not_unmatched():
    consultant, generated = _chain_pair()
    corridor = benchmark_networks(consultant, generated).corridor
    assert corridor is not None
    assert corridor.matched_ids == ["G1", "G2", "G3"]
    assert corridor.in_corridor_unmatched == []
    assert corridor.out_of_scope_count == 0
    assert corridor.scope_of("G2") is GeneratedScope.MATCHED


# ---------------------------------------------------------------------------
# 3. Ambiguity is named, never resolved
# ---------------------------------------------------------------------------


def _ambiguous_pair() -> tuple[GeoNetwork, GeoNetwork]:
    """Two parallel generated candidates, both running the consultant's way.

    Hand-derivation
    ---------------
    ``C`` runs (0,0) -> (100,0). ``GA`` is 10 m to its left (+y), ``GB`` 15 m to
    its right (-y). Both are inside the 22 m tolerance, both cover 100% of their
    own length and of C, and both run (+x), so both are admissible members.

    Their signed laterals are +10 and -15. The gap between them is 25 m, and
    25 > 22 (the lateral cluster gap), so they are TWO clusters, not one road
    drawn twice. Each cluster covers 100% >= 80%, so there are two covering
    candidates and the object is AMBIGUOUS. Neither is picked.
    """
    consultant = _net("consultant", _link("C", [(0, 0), (100, 0)], lanes=2, name="Ambiguous Rd"))
    generated = _net(
        "generated",
        _link("GA", [(0, 10), (100, 10)], lanes=2),
        _link("GB", [(0, -15), (100, -15)], lanes=2),
    )
    return consultant, generated


def test_two_separated_candidates_are_reported_ambiguous_and_never_force_picked():
    consultant, generated = _ambiguous_pair()
    benchmark = benchmark_networks(consultant, generated)
    _assert_partition(benchmark, consultant)

    assert benchmark.matched == []
    assert benchmark.missing == []
    assert benchmark.bucket_of("C") is MatchBucket.AMBIGUOUS
    item = (benchmark.ambiguous or [])[0]
    assert sorted(sum(([m.id for m in chain] for chain in item.candidates), [])) == ["GA", "GB"]
    assert len(item.candidates) == 2, "two candidates must stay two, not be merged"
    assert "ambiguous_correspondence" in benchmark.finding_codes(DiffSeverity.WARN)

    report = render_benchmark_report(benchmark, lang="en")
    assert "GA" in report and "GB" in report


# ---------------------------------------------------------------------------
# 4. Direction is what separates a carriageway from its opposite twin
# ---------------------------------------------------------------------------


def test_a_generated_link_running_the_other_way_is_not_a_match():
    """The wrong-direction twin is ALWAYS inside the positional tolerance.

    Hand-derivation: ``GR`` is drawn 1 m from ``C`` and covers all of it, so
    proximity alone would match it. Its chord is (-100, 0) while C's local
    direction over the same window is (+100, 0): the angle is 180 deg, far
    outside the +/-30 deg band, so it is rejected as a member. C therefore has
    one *near* candidate and no directed one -> MISSING / no_directed_member.
    """
    consultant = _net("consultant", _link("C", [(0, 0), (100, 0)], lanes=2, name="One Way St"))
    generated = _net("generated", _link("GR", [(100, 1), (0, 1)], lanes=2))
    benchmark = benchmark_networks(consultant, generated)
    _assert_partition(benchmark, consultant)

    missing = (benchmark.missing or [])[0]
    assert missing.reason is MissingReason.NO_DIRECTED_MEMBER
    assert missing.candidate_count == 1, "it WAS near — that is the point"
    corridor = benchmark.corridor
    assert corridor is not None
    assert corridor.in_corridor_unmatched == ["GR"]


# ---------------------------------------------------------------------------
# 5. Corridor scoping
# ---------------------------------------------------------------------------


def _corridor_pair() -> tuple[GeoNetwork, GeoNetwork]:
    """One matched road, one road just outside matching range, one far away.

    Hand-derivation at the default tolerance (22 m, buffer 2 x 22 = 44 m):

    * ``G1`` at +1 m: within tolerance, same direction -> MATCHED into C's chain.
    * ``G2`` at +30 m: 30 > 22 so it is never a candidate, but 30 <= 44 so it is
      inside the corridor -> IN_CORRIDOR_UNMATCHED. This is the interesting
      bucket: road the consultant chose not to model.
    * ``G3`` at +300 m: 300 > 44 -> OUT_OF_SCOPE, counted and never listed.
    """
    consultant = _net("consultant", _link("C", [(0, 0), (100, 0)], lanes=2, name="Main St"))
    generated = _net(
        "generated",
        _link("G1", [(0, 1), (100, 1)], lanes=2),
        _link("G2", [(0, 30), (100, 30)], lanes=2),
        _link("G3", [(0, 300), (100, 300)], lanes=1),
    )
    return consultant, generated


def test_generated_links_split_into_matched_in_corridor_and_out_of_scope():
    consultant, generated = _corridor_pair()
    benchmark = benchmark_networks(consultant, generated)
    _assert_partition(benchmark, consultant)

    corridor = benchmark.corridor
    assert corridor is not None
    assert corridor.buffer_m == 2 * MATCH_TOLERANCE_M
    assert corridor.matched_ids == ["G1"]
    assert corridor.in_corridor_unmatched == ["G2"]
    assert corridor.out_of_scope_count == 1
    assert corridor.scope_of("G3") is GeneratedScope.OUT_OF_SCOPE

    report = render_benchmark_report(benchmark, lang="en")
    assert "G2" in report
    assert "G3" not in report, "out-of-scope links are counted, never listed as failures"


# ---------------------------------------------------------------------------
# 6. Tolerance sensitivity — the number is load-bearing, not decorative
# ---------------------------------------------------------------------------


def test_halving_the_tolerance_turns_a_match_into_a_miss():
    """A 16 m offset sits between tolerance/2 (11 m) and tolerance (22 m).

    Predicted direction: MATCHED at 22, MISSING at 11 (16 > 11, so nothing is
    even a candidate), still MATCHED at 44.
    """
    consultant = _net("consultant", _link("C", [(0, 0), (100, 0)], lanes=2, name="Offset Rd"))
    generated = _net("generated", _link("G", [(0, 16), (100, 16)], lanes=2))

    assert benchmark_networks(consultant, generated).bucket_of("C") is MatchBucket.MATCHED

    tight = benchmark_networks(consultant, generated, tolerance_m=MATCH_TOLERANCE_M / 2)
    _assert_partition(tight, consultant)
    assert tight.bucket_of("C") is MatchBucket.MISSING
    assert (tight.missing or [])[0].reason is MissingReason.NO_CANDIDATES

    loose = benchmark_networks(consultant, generated, tolerance_m=MATCH_TOLERANCE_M * 2)
    assert loose.bucket_of("C") is MatchBucket.MATCHED


def test_doubling_the_tolerance_merges_two_distinct_roads_into_one_false_match():
    """The other direction of the same knob, on the ambiguous fixture.

    The two candidates are 25 m apart laterally. The lateral cluster gap follows
    the tolerance, so:

    * at 11 m: only ``GA`` (10 m away) is within tolerance at all -> one cluster,
      one covering candidate -> MATCHED to GA;
    * at 22 m: both are members, 25 > 22 -> two clusters -> AMBIGUOUS;
    * at 44 m: both are members and 25 <= 44 -> ONE cluster -> MATCHED, with two
      different roads merged into a single chain. That is the failure a loose
      tolerance buys, and it is why the tolerance is derived rather than tuned.
    """
    consultant, generated = _ambiguous_pair()

    tight = benchmark_networks(consultant, generated, tolerance_m=MATCH_TOLERANCE_M / 2)
    _assert_partition(tight, consultant)
    assert tight.bucket_of("C") is MatchBucket.MATCHED
    assert (tight.matched or [])[0].chain_ids == ["GA"]

    assert benchmark_networks(consultant, generated).bucket_of("C") is MatchBucket.AMBIGUOUS

    loose = benchmark_networks(consultant, generated, tolerance_m=MATCH_TOLERANCE_M * 2)
    _assert_partition(loose, consultant)
    assert loose.bucket_of("C") is MatchBucket.MATCHED


def test_doubling_the_tolerance_pulls_a_side_street_into_the_corridor():
    """A side street 60 m away: outside the 44 m buffer, inside the 88 m one.

    Predicted direction: OUT_OF_SCOPE at the default tolerance,
    IN_CORRIDOR_UNMATCHED at twice it. 60 > 44 either way, so it never becomes
    a match — only its scope moves.
    """
    consultant = _net("consultant", _link("C", [(0, 0), (100, 0)], lanes=2))
    generated = _net("generated", _link("GS", [(0, 60), (100, 60)], lanes=1))

    default = benchmark_networks(consultant, generated).corridor
    assert default is not None
    assert default.out_of_scope_count == 1 and default.in_corridor_unmatched == []

    loose = benchmark_networks(consultant, generated, tolerance_m=MATCH_TOLERANCE_M * 2).corridor
    assert loose is not None
    assert loose.out_of_scope_count == 0 and loose.in_corridor_unmatched == ["GS"]


# ---------------------------------------------------------------------------
# 7. The v1 failure, encoded
# ---------------------------------------------------------------------------


def test_colliding_ids_on_unrelated_roads_do_not_correspond():
    """Benchmark v1's exact bug: same id, different road, reported as unchanged.

    Consultant "1" and generated "1" are 500 m apart. An id-based diff calls
    them the same object; a geometric one calls the consultant link missing and
    the generated link out of scope, which is what is true.
    """
    consultant = _net("consultant", _link("1", [(0, 0), (100, 0)], lanes=2, name="South St"))
    generated = _net("generated", _link("1", [(0, 500), (100, 500)], lanes=4))
    benchmark = benchmark_networks(consultant, generated)
    _assert_partition(benchmark, consultant)

    assert benchmark.bucket_of("1") is MatchBucket.MISSING
    corridor = benchmark.corridor
    assert corridor is not None
    assert corridor.matched_ids == []
    assert corridor.out_of_scope_count == 1


# ---------------------------------------------------------------------------
# 8. No positional delta reaches the report
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("model", [MatchedPair, MissingObject, AmbiguousObject, ChainMember])
def test_per_object_models_cannot_carry_a_distance(model):
    """A structural guarantee, not a promise: none of the per-object models has
    a float field, so no metres number can be attached to one object.

    Tolerances ARE floats and DO appear — on ``BenchmarkMethods`` and
    ``CorridorScope``, where they are stated method rather than measured
    difference. That is the whole distinction U2 drew.
    """
    for name, info in model.model_fields.items():
        assert info.annotation is not float, f"{model.__name__}.{name} could carry a distance"


def test_the_object_sections_of_the_report_contain_no_metres():
    consultant, generated = _chain_pair()
    report = render_benchmark_report(benchmark_networks(consultant, generated), lang="en")
    body, _, _methods = report.partition("Methods and tolerances")
    assert _methods, "the methods block must exist to be excluded"
    assert not re.search(r"\d+(\.\d+)?\s*m\b", body), (
        "a metres number escaped into the per-object half of the report"
    )


# ---------------------------------------------------------------------------
# 9. Rendering, in three languages
# ---------------------------------------------------------------------------


def test_the_report_renders_in_ko_en_and_ja_with_the_same_numbers():
    consultant, generated = _chain_pair()
    benchmark = benchmark_networks(consultant, generated)
    renders = {lang: render_benchmark_report(benchmark, lang=lang) for lang in ("ko", "en", "ja")}

    assert len({*renders.values()}) == 3, "the three languages must actually differ"
    assert "요약" in renders["ko"]
    assert "Headline" in renders["en"]
    assert "要約" in renders["ja"]
    for text in renders.values():
        assert "1/2 (50.0%)" in text  # C1 matched, C2 missing — same in every language
        assert f"{MATCH_TOLERANCE_M:g} m" in text  # the tolerance travels with the report


def test_an_unknown_language_falls_back_to_korean():
    consultant, generated = _chain_pair()
    benchmark = benchmark_networks(consultant, generated)
    assert render_benchmark_report(benchmark, lang="de") == render_benchmark_report(
        benchmark, lang="ko"
    )


# ---------------------------------------------------------------------------
# 10. Loading, and refusing
# ---------------------------------------------------------------------------


def _minimal_inpx(links: list[tuple[str, int, list[tuple[float, float]]]]) -> str:
    parts = ['<network><netPara northDir="0">', '<refPointMap x="0" y="0"/>',
             '<refPointNet x="0" y="0"/>', "</netPara><links>"]
    for link_id, lanes, points in links:
        lane_xml = "".join('<lane width="3.5"/>' for _ in range(lanes))
        pts = "".join(f'<linkPolyPoint x="{x}" y="{y}"/>' for x, y in points)
        parts.append(
            f'<link no="{link_id}" name="L{link_id}"><lanes>{lane_xml}</lanes>'
            f"<geometry><linkPolyPts>{pts}</linkPolyPts></geometry></link>"
        )
    parts.append("</links></network>")
    return "".join(parts)


def test_a_draft_inpx_loads_straight_into_the_common_frame(tmp_path: Path):
    """``local_frame_network`` must NOT move the coordinates: a draft's stored
    metres already are the common frame."""
    path = tmp_path / "draft.inpx"
    path.write_text(_minimal_inpx([("7", 2, [(0.0, 1.0), (100.0, 1.0)])]), encoding="utf-8")
    network, findings = local_frame_network(path, label="draft")
    assert network is not None
    assert [f.severity for f in findings if f.severity is DiffSeverity.REFUSE] == []
    assert network.objects["7"].points == ((0.0, 1.0), (100.0, 1.0))
    assert network.objects["7"].lanes == 2


def test_declared_node_members_that_resolve_to_nothing_are_warned_about(tmp_path: Path):
    """The real consultant model's shape: nodes with ``<linkSegs>`` and no polygon.

    ``inpx_parser._node_link_ids`` resolves membership from a ``<geometry>``
    polygon, so it returns nothing for these nodes and every membership-derived
    number reads 0. The benchmark does not use node membership, but its Totals
    table prints that 0, so it must say where the 0 came from.
    """
    path = tmp_path / "declared.inpx"
    path.write_text(
        '<network><netPara northDir="0"><refPointMap x="0" y="0"/>'
        '<refPointNet x="0" y="0"/></netPara><links>'
        '<link no="1"><lanes><lane width="3.5"/></lanes><geometry><linkPolyPts>'
        '<linkPolyPoint x="0" y="0"/><linkPolyPoint x="10" y="0"/>'
        "</linkPolyPts></geometry></link></links>"
        '<nodes><node no="1"><linkSegs><linkSegment link="1" fromPos="0"/>'
        "</linkSegs></node></nodes></network>",
        encoding="utf-8",
    )
    network, findings = local_frame_network(path, label="declared")
    assert network is not None
    warning = next(f for f in findings if f.code == "node_membership_unresolved")
    assert warning.severity is DiffSeverity.WARN
    assert warning.context["declared_link_segments"] == 1
    assert warning.context["resolved_members"] == 0
    assert set(warning.messages) == {"ko", "en", "ja"}


def test_a_network_whose_node_membership_resolves_is_not_warned_about(tmp_path: Path):
    """The warning must not fire on a network the parser reads correctly, or it
    becomes noise attached to every report."""
    path = tmp_path / "polygon.inpx"
    path.write_text(
        '<network><netPara northDir="0"><refPointMap x="0" y="0"/>'
        '<refPointNet x="0" y="0"/></netPara><links>'
        '<link no="1"><lanes><lane width="3.5"/></lanes><geometry><linkPolyPts>'
        '<linkPolyPoint x="5" y="0"/><linkPolyPoint x="10" y="0"/>'
        "</linkPolyPts></geometry></link></links>"
        '<nodes><node no="1"><linkSegs><linkSegment link="1" fromPos="0"/></linkSegs>'
        '<geometry><points><point x="0" y="-10"/><point x="20" y="-10"/>'
        '<point x="20" y="10"/><point x="0" y="10"/></points></geometry>'
        "</node></nodes></network>",
        encoding="utf-8",
    )
    _network, findings = local_frame_network(path, label="polygon")
    assert "node_membership_unresolved" not in {f.code for f in findings}


def test_a_missing_file_refuses_rather_than_reporting_everything_missing(tmp_path: Path):
    """The failure mode that matters: "we could not read it" must never render
    as "the draft contains none of your network"."""
    generated = tmp_path / "draft.inpx"
    generated.write_text(_minimal_inpx([("1", 2, [(0.0, 0.0), (10.0, 0.0)])]), encoding="utf-8")
    benchmark = benchmark_network_files(
        tmp_path / "nope.inpx", generated, origin_lat_deg=-32.0, origin_lon_deg=115.8
    )
    assert benchmark.refused
    assert benchmark.matched is None and benchmark.missing is None
    assert "network_file_missing" in benchmark.finding_codes(DiffSeverity.REFUSE)
    assert "REFUSED" in render_benchmark_report(benchmark, lang="en")


def test_a_network_without_reference_points_refuses_instead_of_guessing_an_anchor(tmp_path: Path):
    """No refPointMap means no bridge. Assuming a zero offset would place the
    consultant network 11 km from the draft and report everything missing."""
    consultant = tmp_path / "consultant.inpx"
    consultant.write_text(
        '<network><links><link no="1"><lanes><lane width="3.5"/></lanes>'
        '<geometry><linkPolyPts><linkPolyPoint x="0" y="0"/>'
        '<linkPolyPoint x="10" y="0"/></linkPolyPts></geometry></link></links></network>',
        encoding="utf-8",
    )
    generated = tmp_path / "draft.inpx"
    generated.write_text(_minimal_inpx([("1", 2, [(0.0, 0.0), (10.0, 0.0)])]), encoding="utf-8")
    benchmark = benchmark_network_files(
        consultant, generated, origin_lat_deg=-32.0, origin_lon_deg=115.8
    )
    assert benchmark.refused
    assert "georeference_reference_points_missing" in benchmark.finding_codes(DiffSeverity.REFUSE)


# ---------------------------------------------------------------------------
# 11. The real pair
# ---------------------------------------------------------------------------


def _real_consultant_inpx() -> Path | None:
    """Discovered by glob rather than by hard-coded workspace id (it is
    machine-local). Read-only: nothing here writes inside ``runtime/``."""
    matches = sorted(REPO_ROOT.glob("runtime/workspaces/*/uploads/current/current.inpx"))
    return matches[0] if matches else None


def _real_osm() -> Path | None:
    candidate = REPO_ROOT / "client-data" / "murdoch.osm"
    return candidate if candidate.is_file() else None


@pytest.mark.skipif(
    _real_consultant_inpx() is None or _real_osm() is None,
    reason="needs the uploaded consultant network and client-data/murdoch.osm on this machine",
)
def test_the_real_pair_partitions_and_reports_missing_consultant_links(tmp_path: Path):
    """Regenerate the draft, benchmark it against the consultant model.

    The assertion is deliberately about SHAPE, not about a headline number: the
    match rate depends on the generator, which is still moving. What must hold
    on real data is that the buckets partition 312 consultant objects, that the
    missing list is non-empty (benchmark v1's "0 links removed" was the bug),
    and that nothing writes outside ``tmp_path``.
    """
    osm = _real_osm()
    consultant_inpx = _real_consultant_inpx()
    assert osm is not None and consultant_inpx is not None

    result = subprocess.run(
        [
            sys.executable,
            str(BACKEND_ROOT / "tools" / "genesis_osm_draft.py"),
            "--osm", str(osm),
            "--out-dir", str(tmp_path),
            "--name", "murdoch",
            "--lang", "en",
        ],
        capture_output=True,
        text=True,
        cwd=str(BACKEND_ROOT),
    )
    assert result.returncode == 0, result.stderr

    import json

    projection = json.loads((tmp_path / "murdoch.genesis.json").read_text(encoding="utf-8"))
    projection = projection["metadata"]["projection"]
    plane = LocalTangentPlane(projection["origin_lat_deg"], projection["origin_lon_deg"])

    consultant, consultant_findings = georeferenced_network(
        consultant_inpx, plane, label="consultant"
    )
    generated, _ = local_frame_network(tmp_path / "murdoch.inpx", label="generated")
    assert consultant is not None and generated is not None
    assert "frame_bridge_applied" in {f.code for f in consultant_findings}

    benchmark = benchmark_networks(consultant, generated)
    _assert_partition(benchmark, consultant)

    assert benchmark.missing, "an id-blind benchmark must find the links OSM does not carry"
    assert benchmark.matched, "and it must still find the corridor the two files share"
    rate = benchmark.match_rate()
    assert rate is not None and 0.0 < rate < 1.0
    corridor = benchmark.corridor
    assert corridor is not None
    assert corridor.out_of_scope_count > corridor.matched_count, (
        "most of an OSM extract is side streets outside the consultant's corridor"
    )
    # The scope gap is stated once, not per link.
    assert benchmark.finding_codes().count("signal_scope_gap") == 1

    report = render_benchmark_report(benchmark, lang="ko")
    assert "요약" in report and f"{MATCH_TOLERANCE_M:g} m" in report


@pytest.mark.skipif(
    _real_consultant_inpx() is None,
    reason="needs the uploaded consultant network on this machine",
)
def test_id_matching_and_geometric_matching_disagree_on_the_real_pair(tmp_path: Path):
    """Why v2 exists, measured rather than asserted, on the real network.

    A copy of the consultant model keeps every id but hands them out in reverse
    document order: the id SET is identical, so no id is added or removed, while
    no id names the road it used to. That is benchmark v1's exact failure —
    "0 links removed" between two networks that correspond nowhere — and it is
    what colliding id spaces produce in the wild.

    Expected: the id diff reports zero adds and zero removes plus a pile of
    phantom lane-count changes (v1 counted 52 of them). The geometric benchmark,
    given the same pair, matches nearly everything, because the geometry never
    moved — only the labels did.
    """
    import xml.etree.ElementTree as ET

    source = _real_consultant_inpx()
    assert source is not None
    root = ET.parse(source).getroot()
    links = root.find(".//links")
    assert links is not None
    link_elements = links.findall("link")
    ids = [link.get("no") for link in link_elements]
    for link, permuted in zip(link_elements, reversed(ids)):
        link.set("no", permuted or "")
    renumbered = tmp_path / "permuted.inpx"
    ET.ElementTree(root).write(renumbered, encoding="utf-8", xml_declaration=True)

    id_diff = diff_network_files(source, renumbered)
    assert id_diff.ok and id_diff.links is not None and id_diff.connectors is not None
    assert id_diff.links.removed == [] and id_diff.links.added == [], (
        "the v1 symptom: identical id sets, so nothing reads as removed"
    )
    assert id_diff.links.lane_count_changed, (
        "and the differences it does report are phantoms between unrelated roads"
    )

    plane = LocalTangentPlane(-32.07694175, 115.83820175)
    consultant, _ = georeferenced_network(source, plane, label="consultant")
    # The permuted copy is in the SAME Vissim frame, so it takes the same bridge.
    renumbered_net, _ = georeferenced_network(renumbered, plane, label="permuted")
    assert consultant is not None and renumbered_net is not None

    benchmark = benchmark_networks(consultant, renumbered_net)
    _assert_partition(benchmark, consultant)
    rate = benchmark.match_rate()
    assert rate is not None and rate > 0.95, (
        f"identical geometry under different ids must still correspond (got {rate})"
    )
