"""Unit #38 (a) — one way id, one road.

Unit #34 measured that all 164 real ``v7_links_share_one_polyline`` findings
have draft twins byte-identical at full precision: the corruption predates the
draft, so no census-derived edit can clear it. This unit measured WHY, on the
four refused Daejeon jobs' own sources, and found ONE cause behind every single
group — a source that declares the same way id twice, verbatim:

    2712071437d0   62 shared-polyline groups   29 way ids declared twice
    4f764a7543a4   27                           9
    b3ef6abb9a85    5                           3
    f57cf27e33f5   62                          29
    (30a1f2d61dce and d31961abd950: 0 groups, 0 repeats)

100 % of the repeats carried IDENTICAL bodies (same ``<nd>`` sequence, same
tags), and 100 % of the groups involved a repeated id. Re-derived offline
through osm→draft→emit→gate at HEAD, the class goes 156 → 0 and the two
unaffected sources emit a byte-identical ``.inpx`` (sha unchanged).

The fix is in the READER (``services.genesis.osm._keep_last_declaration``), not
in the geometry: the repeat is dropped as a NAMED skip, and no coordinate is
touched. Dropping it is re-derivation, not repair — the source said it once,
effectively.

What this file pins:

* §1 the reader keeps one way per id, names the repeat, and leaves the skip
  ledger's own arithmetic (``ways_seen == ways + skipped``) intact;
* §2 the SECONDARY corruption, which is the bigger one: a repeated way visits
  its own nodes twice and ``intersection_node_ids`` counts OCCURRENCES, so
  every interior shape vertex used to be promoted to a junction and the way was
  shattered into one link per vertex;
* §3 the before/after through the REAL pipeline, with the pre-fix reader
  restored by monkeypatching the ONE seam — same source, twins before, none
  after;
* §4 an UNAFFECTED source emits a byte-identical ``.inpx`` (sha), which is the
  claim that makes this fix safe to apply to every future generation;
* §5 the diverging-body branch: the file contradicts itself, the LAST
  declaration wins (matching the source-testimony index unit #36 already reads
  this file with), and the superseded one is named with the fields that differ.
"""
from __future__ import annotations

import hashlib
from contextlib import contextmanager
from pathlib import Path

import pytest

from services.genesis.draft import build_draft
from services.genesis.emit import emit_inpx
from services.genesis.gate import run_gate
from services.genesis.osm import (
    CONVERTER_FIXES,
    SKIP_HIGHWAY_NOT_DRIVABLE,
    SKIP_REDECLARED_DIFFERENTLY,
    SKIP_REDECLARED_IDENTICALLY,
    WAY_REDECLARATION_FIX_ID,
    OsmWay,
    parse_osm_file,
)
from services.genesis import osm as osm_module

from tests.test_genesis_osm_draft import LAT0, LON0, _osm_text, _write

_CODE = "v7_links_share_one_polyline"

#: Five collinear nodes, so the way below has THREE interior vertices — the
#: shape §2 needs to show the fragmentation, and none of them is a junction.
_LINE_NODES = [
    (1, LAT0, LON0 - 0.004),
    (2, LAT0, LON0 - 0.002),
    (3, LAT0, LON0),
    (4, LAT0, LON0 + 0.002),
    (5, LAT0, LON0 + 0.004),
]

_MAIN = {"highway": "secondary", "name": "Main St", "lanes": "2"}


def _twice_declared_osm() -> str:
    """One way, declared TWICE with identical bodies — the real Overpass shape.

    A union query whose two statements both match a way emits it once per
    statement. In the real sources the two declarations were never adjacent in
    document order, so the second is written after an unrelated way here too.
    """
    return _osm_text(
        _LINE_NODES + [(9, LAT0 - 0.005, LON0)],
        [
            (101, [1, 2, 3, 4, 5], dict(_MAIN)),
            (103, [9, 1], {"highway": "footway"}),
            (101, [1, 2, 3, 4, 5], dict(_MAIN)),
        ],
    )


def _once_declared_osm() -> str:
    """The same file with the repeat removed — the UNAFFECTED source of §4."""
    return _osm_text(
        _LINE_NODES + [(9, LAT0 - 0.005, LON0)],
        [
            (101, [1, 2, 3, 4, 5], dict(_MAIN)),
            (103, [9, 1], {"highway": "footway"}),
        ],
    )


def _pre_fix_reader(monkeypatch: pytest.MonkeyPatch) -> None:
    """Restore the reader as it behaved BEFORE this unit: every declaration
    appended, no dedupe, no skip.

    Patches the one seam the fix introduced rather than re-implementing the
    parser, so every "before" measured in this repo's tests is its actual
    former behaviour and not a hand-written imitation of it.

    Always used through :func:`_pre_fix` (a MonkeyPatch CONTEXT), never through
    ``monkeypatch.undo()`` on the test's own fixture-owned patcher — undoing
    that would also revert the workspace-root patches the genesis fixtures
    install, and the failure it produces ("Unknown genesis job") does not look
    anything like its cause.
    """

    def keep_every_declaration(extract, kept, way_id, outcome):
        if isinstance(outcome, OsmWay):
            # A key no way id can collide with, so both declarations survive in
            # document order — exactly what ``extract.ways.append`` used to do.
            kept[f"{way_id}#{len(kept)}"] = outcome
            return
        extract.skipped.append(outcome)

    monkeypatch.setattr(osm_module, "_keep_last_declaration", keep_every_declaration)


@contextmanager
def _pre_fix():
    """``with _pre_fix():`` — the pre-#38 reader, reverted on exit."""
    with pytest.MonkeyPatch.context() as patcher:
        _pre_fix_reader(patcher)
        yield


def _gate_v7_count(draft, out_dir: Path, name: str) -> tuple[int, str]:
    """``(shared-polyline finding count, artifact sha256)`` from the REAL gate."""
    inpx = emit_inpx(draft, out_dir / f"{name}.inpx")
    result = run_gate(inpx, draft_metadata=draft.model_dump(mode="json"))
    count = sum(
        1
        for check in result.checks
        for finding in check.findings
        if finding.code == _CODE
    )
    return count, hashlib.sha256(inpx.read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# §1 The reader keeps ONE way per id, and names the repeat
# ---------------------------------------------------------------------------


def test_a_way_declared_twice_is_read_once_and_the_repeat_is_named(tmp_path: Path):
    """Three ``<way>`` elements, two distinct ids: one carriageway way, one
    footway skip, one named redeclaration skip. ``ways_seen`` still equals
    ``ways + skipped`` — every declaration lands in exactly one of the two."""
    extract = parse_osm_file(_write(tmp_path, "twice.osm", _twice_declared_osm()))

    assert [way.id for way in extract.ways] == [101]
    assert extract.ways_seen == 3
    assert extract.ways_seen == len(extract.ways) + len(extract.skipped)
    assert extract.skipped_summary() == {
        SKIP_HIGHWAY_NOT_DRIVABLE: 1,
        SKIP_REDECLARED_IDENTICALLY: 1,
    }
    (repeat,) = [s for s in extract.skipped if s.reason == SKIP_REDECLARED_IDENTICALLY]
    assert repeat.id == 101
    assert repeat.highway == "secondary"
    assert "declared more than once" in repeat.detail
    assert "the repeat adds no road" in repeat.detail


def test_the_kept_declaration_is_byte_for_byte_the_one_a_clean_file_yields(tmp_path: Path):
    """Deduping must not change what the way IS. The surviving ``OsmWay`` from
    the repeated file equals the one the single-declaration file produces —
    node order, lane reading, provenance and all (frozen dataclass equality)."""
    twice = parse_osm_file(_write(tmp_path, "twice.osm", _twice_declared_osm()))
    once = parse_osm_file(_write(tmp_path, "once.osm", _once_declared_osm()))

    assert twice.ways == once.ways
    assert twice.nodes == once.nodes


# ---------------------------------------------------------------------------
# §2 The secondary corruption: a repeated way used to shatter itself
# ---------------------------------------------------------------------------


def test_a_repeated_way_no_longer_promotes_its_own_vertices_to_junctions(tmp_path: Path):
    """``intersection_node_ids`` counts node OCCURRENCES across kept ways, so a
    way declared twice visited each of its five nodes twice and every one of
    them qualified as a junction.

    Hand-derived: nodes 1..5 in a line, all five occurring twice pre-fix ⇒ five
    "junctions" ⇒ the way is split at its three INTERIOR vertices ⇒ 4 segments
    × 2 directions × 2 declarations = 16 links. After the fix the way occurs
    once, no node reaches two occurrences, nothing is a junction, and one
    unsplit way yields 2 links (one per direction).
    """
    path = _write(tmp_path, "twice.osm", _twice_declared_osm())

    with _pre_fix():
        before = parse_osm_file(path)
        assert before.intersection_node_ids() == {1, 2, 3, 4, 5}
        assert len(build_draft(before, name="before").links) == 16

    after = parse_osm_file(path)
    assert after.intersection_node_ids() == set()
    assert len(build_draft(after, name="after").links) == 2


# ---------------------------------------------------------------------------
# §3 Before/after through the REAL pipeline on the same bytes
# ---------------------------------------------------------------------------


def test_the_duplicated_declaration_class_goes_to_zero_through_the_real_gate(tmp_path: Path):
    """The whole unit in one measurement: same source, osm→draft→emit→gate.

    Pre-fix the eight twin pairs (4 segments × 2 directions, each emitted
    twice) are eight ``v7_links_share_one_polyline`` groups; post-fix there are
    none — and the artifact is not merely quieter, it holds 2 links where it
    held 16.
    """
    path = _write(tmp_path, "twice.osm", _twice_declared_osm())

    with _pre_fix():
        before_draft = build_draft(parse_osm_file(path), name="before")
        before_count, _ = _gate_v7_count(before_draft, tmp_path, "before")

    after_draft = build_draft(parse_osm_file(path), name="after")
    after_count, _ = _gate_v7_count(after_draft, tmp_path, "after")

    assert before_count == 8
    assert after_count == 0


# ---------------------------------------------------------------------------
# §4 An UNAFFECTED source emits a byte-identical artifact
# ---------------------------------------------------------------------------


def test_a_source_with_no_repeat_emits_the_same_bytes_as_before_the_fix(tmp_path: Path):
    """The safety claim, stated as a sha: for a file that declares every way
    once, the fixed reader and the pre-fix reader produce the same ``.inpx``
    byte for byte.

    (The same comparison was run read-only against two REAL unaffected jobs'
    sources — 30a1f2d61dce and d31961abd950 — and both shas were identical:
    ``f9c5c6699e…`` and ``74a995fd69…``.)
    """
    path = _write(tmp_path, "once.osm", _once_declared_osm())

    with _pre_fix():
        _, before_sha = _gate_v7_count(
            build_draft(parse_osm_file(path), name="net"), tmp_path / "b", "net"
        )

    _, after_sha = _gate_v7_count(
        build_draft(parse_osm_file(path), name="net"), tmp_path / "a", "net"
    )

    assert before_sha == after_sha


@pytest.fixture(autouse=True)
def _out_dirs(tmp_path: Path):
    (tmp_path / "a").mkdir(exist_ok=True)
    (tmp_path / "b").mkdir(exist_ok=True)


# ---------------------------------------------------------------------------
# §5 The diverging branch: the file contradicts itself
# ---------------------------------------------------------------------------


def test_diverging_redeclarations_keep_the_last_and_name_what_differs(tmp_path: Path):
    """Zero occurrences in the real sources measured — and the branch must
    still be honest, because a silent first-wins is indistinguishable from
    having read the file correctly.

    Last-wins matches ``genesis_jobs``'s source-testimony index (unit #36),
    so the two layers cannot disagree about what way 101 IS. The superseded
    declaration is named, with the differing fields listed.
    """
    text = _osm_text(
        _LINE_NODES,
        [
            (101, [1, 2, 3], dict(_MAIN)),
            (101, [1, 2, 3, 4, 5], {**_MAIN, "lanes": "4"}),
        ],
    )
    extract = parse_osm_file(_write(tmp_path, "diverging.osm", text))

    (way,) = extract.ways
    assert way.node_ids == (1, 2, 3, 4, 5)  # the LAST declaration
    assert [d.lanes for d in way.directions] == [2, 2]  # lanes=4, split two ways

    (skip,) = [s for s in extract.skipped if s.reason == SKIP_REDECLARED_DIFFERENTLY]
    assert skip.id == 101
    assert "node_ids" in skip.detail and "directions" in skip.detail
    assert "the LAST declaration is the one modelled" in skip.detail


def test_a_redeclaration_that_is_not_a_carriageway_unkeeps_the_way(tmp_path: Path):
    """The file's LAST word on way 101 is ``highway=footway``. Modelling a road
    the document ends up denying is the same silent invention as modelling one
    twice, in the other direction — so the way is dropped, both declarations
    are named, and the ledger's arithmetic still balances."""
    text = _osm_text(
        _LINE_NODES,
        [
            (101, [1, 2, 3], dict(_MAIN)),
            (101, [1, 2, 3], {"highway": "footway"}),
        ],
    )
    extract = parse_osm_file(_write(tmp_path, "denied.osm", text))

    assert extract.ways == []
    assert extract.ways_seen == len(extract.skipped) == 2
    assert extract.skipped_summary() == {
        SKIP_HIGHWAY_NOT_DRIVABLE: 1,
        SKIP_REDECLARED_DIFFERENTLY: 1,
    }
    (superseded,) = [s for s in extract.skipped if s.reason == SKIP_REDECLARED_DIFFERENTLY]
    assert "the last declaration is not a carriageway" in superseded.detail


# ---------------------------------------------------------------------------
# §6 The converter-fix ledger names this fix by the codes it actually leaves
# ---------------------------------------------------------------------------


def test_the_converter_fix_ledger_names_the_skip_codes_the_reader_really_writes(
    tmp_path: Path,
):
    """A regeneration's lineage claims WHICH converter change makes it
    different (``derived_from.converter_fixes``). That claim is only worth
    something if the ids and skip reasons in the ledger are the ones the reader
    actually emits — pinned here rather than trusted."""
    (entry,) = CONVERTER_FIXES
    assert entry["id"] == WAY_REDECLARATION_FIX_ID
    assert entry["skip_reasons"] == (SKIP_REDECLARED_IDENTICALLY, SKIP_REDECLARED_DIFFERENTLY)
    # v3 rides along in the ledger's measured-effect list: the doubled way's
    # shattering also manufactured silent departure points (b3ef6abb9a85's two
    # v3 findings clear with this same fix).
    assert entry["gate_codes"] == (_CODE, "v3_silent_departure_point")

    extract = parse_osm_file(_write(tmp_path, "twice.osm", _twice_declared_osm()))
    emitted_reasons = set(extract.skipped_summary())
    assert emitted_reasons & set(entry["skip_reasons"]) == {SKIP_REDECLARED_IDENTICALLY}
