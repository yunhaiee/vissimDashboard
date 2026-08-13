"""Phase 2 unit U10 — the per-workspace observation store.

**Every fixture here is synthetic.** The real client drop lives in the
gitignored ``client-data/`` and never enters the repo or a test: these
workbooks are built by roadmap 1.2's own fixture builders
(``tests/test_dataimport_daejeon.py``), which reproduce the *shape* of the
대전 스마트교차로 export — the three-row header, six class columns plus 합계,
interleaved 소계 rows, the merged 유입부 column — with invented intersection
names and invented volumes.

What this file pins, in order:

1. the arithmetic (a hand-derived total, stated in the test);
2. that a REFUSE-tier finding stores NOTHING and hands back the gate;
3. that the ingest is STREAMING — measured, against a one-shot parse of the
   same files, because the 6.3 GB peak that motivates this unit is a
   measurement and so is its fix;
4. that a set in one workspace is invisible from another;
5. that a corrupt registry is quarantined, never swallowed;
6. that 1.2's client finding (every workbook's final bin missing) survives
   into the stored set instead of being flattened away;
7. that the site → node join is reported in both directions and refuses
   rather than guesses.

Offline by construction: openpyxl and the neutral model, no Vissim, no COM.
"""
from __future__ import annotations

import json
import tracemalloc
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from services import observations, workspaces
from services.dataimport import DaejeonCountAdapter, apply_quality_gate
from services.dataimport.demand import seedable_counts
from services.observations import ObservationPeriod
from tests.test_dataimport_daejeon import (
    BUSY,
    QUARTER,
    WEDNESDAY,
    _approach_rows,
    _corrupt_a_row_past_the_header,
    _file_name,
    _write_workbook,
)

# ---------------------------------------------------------------------------
# The arithmetic, written down once
#
# _approach_rows emits 15-minute rows for `hours` hours with an hourly 소계
# after every four. Per row, per approach, the builder writes three movement
# blocks (좌회전 / 직 진 / 우회전) and leaves 유 턴 blank:
#
#     QUARTER = (0, 1, 2, 0, 1, 20)  -> six class columns summing to 24
#     BUSY    = (1, 2, 3, 1, 4, 50)  -> six class columns summing to 61
#
# plus each block's own 합계 column, which the store EXCLUDES from every sum
# (it is a stated total of records that are also present -- the same exclusion
# dataimport.demand.seedable_counts makes, pinned by a test below).
#
# So one 15-minute row of one approach contributes:
#     summable records : 3 movements x 6 classes            = 18
#     excluded totals  : 3 movements x 1 합계               =  3
#     volume           : 24 (left) + 61 (through) + 24 (right) = 109
# ---------------------------------------------------------------------------

ROW_VOLUME = 109.0
ROW_SUMMABLE_RECORDS = 18
ROW_EXCLUDED_TOTALS = 3
LEFT_PER_ROW = 24.0
THROUGH_PER_ROW = 61.0
RIGHT_PER_ROW = 24.0

APPROACH = "가람방면"
SITE = "가람네거리"

#: 07:00–10:00 on the fixture's neutral Wednesday: the whole 3-hour block.
FULL_PERIOD = ObservationPeriod(name="AM", start=WEDNESDAY, end=WEDNESDAY + timedelta(hours=3))
#: The first hour only — four bins, used to prove the window actually cuts.
FIRST_HOUR = ObservationPeriod(
    name="AM1", start=WEDNESDAY, end=WEDNESDAY + timedelta(hours=1)
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def workspace_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A private workspaces root for one test, with the legacy-migration source
    pointed somewhere empty so the migration is a guaranteed no-op.

    Verbatim the fixture ``tests/test_workspaces.py`` uses, and for the same
    reason: the observation store derives its path from the BOUND workspace, so
    a test that calls it directly needs both the root and the binding.
    """
    root = tmp_path / "workspaces"
    monkeypatch.setattr(workspaces, "WORKSPACES_ROOT", root)
    monkeypatch.setattr(workspaces, "LEGACY_UPLOADS_ROOT", tmp_path / "legacy-uploads")
    default_id = workspaces.default_workspace_id()
    token = workspaces.set_current_workspace_id(default_id)
    try:
        yield root
    finally:
        workspaces.reset_current_workspace_id(token)


def _one_site_workbook(root: Path, *, site: str = SITE, hours: int = 3) -> Path:
    """One workbook, one approach, ``hours`` hours of 15-minute bins."""
    return _write_workbook(
        root,
        _approach_rows(APPROACH, start=WEDNESDAY, hours=hours),
        file_name=_file_name(
            site,
            start=WEDNESDAY,
            last_start=WEDNESDAY + timedelta(hours=hours, minutes=-15),
        ),
    )


def _multi_day_workbook(
    root: Path,
    *,
    site: str,
    days: int,
    approaches: tuple[str, ...] = (APPROACH,),
    hours: int = 3,
) -> Path:
    """One site, several neutral-Wednesday-anchored days, several approaches.

    Days are consecutive so the file name's declared window is honest; each day
    carries one contiguous 3-hour block, which is what keeps the gate's
    ≥3 h peak-coverage minimum satisfied per site per day.
    """
    rows: list[list[str]] = []
    for approach in approaches:
        for day in range(days):
            rows.extend(
                _approach_rows(
                    approach, start=WEDNESDAY + timedelta(days=day), hours=hours
                )
            )
    last_start = WEDNESDAY + timedelta(days=days - 1, hours=hours, minutes=-15)
    return _write_workbook(
        root,
        rows,
        file_name=_file_name(site, start=WEDNESDAY, last_start=last_start),
    )


def _ingest(paths: list[Path], *, periods=(FULL_PERIOD,), name: str = "테스트 세트"):
    return observations.ingest_observation_set(paths, name=name, periods=list(periods))


# ---------------------------------------------------------------------------
# 1. The arithmetic
# ---------------------------------------------------------------------------


def test_the_stored_aggregate_matches_the_hand_derived_sums(workspace_root: Path, tmp_path: Path):
    """12 bins x 109 veh = 1308, split 288 / 732 / 288 across the three turns.

    Every number in this test is derived at the top of the file from the
    fixture builder's own constants, not copied out of a run.
    """
    record = _ingest([_one_site_workbook(tmp_path)])

    period = record["aggregates"]["AM"]
    assert list(period) == [SITE]
    site = period[SITE]

    bins = 12  # 3 hours of 15-minute rows
    assert site["interval_count"] == bins
    assert site["records"] == bins * ROW_SUMMABLE_RECORDS == 216
    assert site["total"] == pytest.approx(bins * ROW_VOLUME) == pytest.approx(1308.0)
    assert site["movements"] == {
        f"{APPROACH}|left": {"volume": bins * LEFT_PER_ROW, "records": bins * 6},
        f"{APPROACH}|through": {"volume": bins * THROUGH_PER_ROW, "records": bins * 6},
        f"{APPROACH}|right": {"volume": bins * RIGHT_PER_ROW, "records": bins * 6},
    }
    # The node total IS the sum of its movements -- no third, independently
    # derived number that could disagree with them.
    assert site["total"] == pytest.approx(
        sum(movement["volume"] for movement in site["movements"].values())
    )

    coverage = record["coverage"]
    assert coverage["records_aggregated"] == bins * ROW_SUMMABLE_RECORDS
    assert coverage["records_excluded_as_totals"] == bins * ROW_EXCLUDED_TOTALS == 36
    assert coverage["interval_minutes"] == [15.0]
    assert coverage["first_interval_start"] == WEDNESDAY.isoformat()
    assert coverage["last_interval_end"] == (WEDNESDAY + timedelta(hours=3)).isoformat()


def test_a_narrower_period_cuts_the_survey_instead_of_summing_all_of_it(
    workspace_root: Path, tmp_path: Path
):
    """4 bins x 109 = 436 — the whole point of declaring a window.

    Comparing five weeks of counts against one hour of simulation is the same
    class of error ``reality_check.MULTI_SEED_VOLUMES_NOT_COMPARABLE`` refuses.
    """
    record = _ingest([_one_site_workbook(tmp_path)], periods=(FULL_PERIOD, FIRST_HOUR))

    assert record["aggregates"]["AM1"][SITE]["total"] == pytest.approx(4 * ROW_VOLUME)
    assert record["aggregates"]["AM1"][SITE]["interval_count"] == 4
    assert record["aggregates"]["AM"][SITE]["total"] == pytest.approx(12 * ROW_VOLUME)
    assert [entry["name"] for entry in record["periods"]] == ["AM", "AM1"]


def test_the_summable_predicate_is_the_same_one_demand_seeding_uses(tmp_path: Path):
    """``is_summable`` is the per-record form of ``seedable_counts``.

    The store cannot materialise a second filtered list of a workbook's counts
    (that would double the peak this unit exists to bound), so it applies the
    predicate record by record — and this pins the two against each other so
    "which rows are already a sum of other rows" still has ONE definition.
    """
    path = _one_site_workbook(tmp_path)
    result = DaejeonCountAdapter().parse([path])

    assert [count for count in result.counts if observations.is_summable(count)] == (
        seedable_counts(result.counts)
    )
    # And the exclusion is not vacuous: the 합계 column really is in there.
    assert len(result.counts) > len(seedable_counts(result.counts))


def test_two_sites_are_aggregated_side_by_side_not_merged(workspace_root: Path, tmp_path: Path):
    first = _one_site_workbook(tmp_path, site="가람네거리")
    second = _write_workbook(
        tmp_path,
        _approach_rows("한밭방면", start=WEDNESDAY, hours=3),
        file_name=_file_name(
            "한밭네거리", start=WEDNESDAY, last_start=WEDNESDAY + timedelta(hours=2, minutes=45)
        ),
    )

    record = _ingest([first, second])

    assert record["site_count"] == 2
    assert record["site_ids"] == ["가람네거리", "한밭네거리"]
    assert set(record["aggregates"]["AM"]) == {"가람네거리", "한밭네거리"}
    for site in record["aggregates"]["AM"].values():
        assert site["total"] == pytest.approx(12 * ROW_VOLUME)


# ---------------------------------------------------------------------------
# 2. Refusals store nothing
# ---------------------------------------------------------------------------


def test_a_gate_refusal_stores_nothing_and_returns_the_gate(workspace_root: Path, tmp_path: Path):
    """One hour of survey is below MRWA's 3 h minimum: the gate REFUSEs, so the
    set is not stored at all. Storing it would let a later reality check grade
    a run against data we have already refused."""
    path = _one_site_workbook(tmp_path, hours=1)

    with pytest.raises(observations.ObservationRefusal) as excinfo:
        _ingest([path])

    refusal = excinfo.value
    assert refusal.code == "observation_gate_refused"
    assert refusal.status == 422
    body = refusal.to_dict()
    assert body["stored"] is False
    assert "peak_coverage_below_minimum" in body["gate"]["codes"]
    assert body["gate"]["counts"]["refuse"] >= 1
    # ko/en/ja on the envelope, and the finding's own evidence carried through.
    assert set(body["message"]) == {"ko", "en", "ja"}
    finding = next(
        issue for issue in body["gate"]["issues"] if issue["code"] == "peak_coverage_below_minimum"
    )
    assert finding["severity"] == "refuse"
    assert finding["context"]["required_h"] == 3.0
    assert finding["citation"]

    assert observations.list_observation_sets() == []
    assert not observations.registry_path().exists()


def test_an_adapter_refusal_also_stores_nothing(workspace_root: Path, tmp_path: Path):
    """The adapter's own REFUSE tier blocks storage exactly like the gate's —
    ``ImportResult.issues`` carries both and the store does not rank them."""
    path = _write_workbook(
        tmp_path, _approach_rows(APPROACH), file_name="counts-without-the-export-pattern.xlsx"
    )

    with pytest.raises(observations.ObservationRefusal) as excinfo:
        _ingest([path])

    assert "daejeon_site_identity_unavailable" in excinfo.value.to_dict()["gate"]["codes"]
    assert observations.list_observation_sets() == []


def test_a_workbook_that_only_fails_during_the_full_parse_is_refused_not_a_raw_exception(
    workspace_root: Path, tmp_path: Path
):
    """Regression: ``ingest_observation_set`` used to call ``adapter.parse``
    with no ``try``/``except`` at all. A workbook that opens fine (what
    ``stage_workbook``'s upload-time check proves) and sniffs fine (an
    adapter's ``sniff`` reads far less of the file than a full parse does —
    ``_peek_header`` reads exactly the first row) but is corrupt deeper in
    used to escape ``ingest_observation_set`` as a raw ``AdapterError``
    instead of the ``ObservationRefusal`` every other unreadable-input path
    in this module produces. Mirrors ``services.genesis_jobs.stage_counts``'s
    ``AdapterError`` -> refusal mapping for the same adapter contract.
    """
    path = _one_site_workbook(tmp_path)
    _corrupt_a_row_past_the_header(path)

    with pytest.raises(observations.ObservationRefusal) as excinfo:
        _ingest([path])

    refusal = excinfo.value
    assert refusal.code == "observation_workbook_unreadable"
    assert refusal.status == 400
    body = refusal.to_dict()
    assert body["stored"] is False
    assert set(body["message"]) == {"ko", "en", "ja"}
    # The underlying AdapterError's own text is carried, not swallowed.
    assert "could not be read as a worksheet" in body["message"]["en"]
    assert path.name in body["message"]["en"]

    assert observations.list_observation_sets() == []
    assert not observations.registry_path().exists()


def test_declaring_no_period_is_refused_rather_than_guessed(workspace_root: Path, tmp_path: Path):
    with pytest.raises(observations.ObservationRefusal) as excinfo:
        observations.ingest_observation_set(
            [_one_site_workbook(tmp_path)], name="세트", periods=[]
        )
    assert excinfo.value.code == "observation_periods_required"


def test_a_period_that_catches_no_counts_is_refused_not_stored_empty(
    workspace_root: Path, tmp_path: Path
):
    elsewhere = ObservationPeriod(
        name="PM",
        start=WEDNESDAY + timedelta(hours=9),
        end=WEDNESDAY + timedelta(hours=12),
    )
    with pytest.raises(observations.ObservationRefusal) as excinfo:
        _ingest([_one_site_workbook(tmp_path)], periods=(elsewhere,))

    assert excinfo.value.code == "observation_no_counts_in_declared_periods"
    assert observations.list_observation_sets() == []


def test_a_timezone_aware_window_is_refused_by_the_period_model():
    """The export states no timezone at all (the adapter marks
    ``SiteMetadata.timezone`` absent), so its stamps are naive; an aware bound
    would silently shift the window by an offset nobody wrote down."""
    from datetime import timezone as tz

    with pytest.raises(ValueError, match="timezone"):
        ObservationPeriod(
            name="AM",
            start=WEDNESDAY.replace(tzinfo=tz.utc),
            end=(WEDNESDAY + timedelta(hours=3)).replace(tzinfo=tz.utc),
        )


# ---------------------------------------------------------------------------
# 3. The 23:45 finding survives into the stored set
# ---------------------------------------------------------------------------


def test_the_declared_window_warning_rides_along_in_the_stored_gate_summary(
    workspace_root: Path, tmp_path: Path
):
    """1.2's CLIENT finding: every real workbook's file name declares a window
    the sheet does not deliver — the final 23:45 bin is missing in all eleven.

    It arrives as the adapter's ``daejeon_declared_window_not_covered`` WARN.
    A store that kept only refusals would present a survey as complete that the
    client already knows is not, so the WARN must be IN the stored record — not
    merely counted, and not flattened into a number.
    """
    path = _write_workbook(
        tmp_path,
        _approach_rows(APPROACH, start=WEDNESDAY, hours=3),
        # The sheet stops at 09:45; the name claims bins out to 23:45.
        file_name=_file_name(
            SITE, start=WEDNESDAY, last_start=WEDNESDAY.replace(hour=23, minute=45)
        ),
    )

    record = _ingest([path])

    assert "daejeon_declared_window_not_covered" in record["gate"]["codes"]
    warn = next(
        issue
        for issue in record["gate"]["issues"]
        if issue["code"] == "daejeon_declared_window_not_covered"
    )
    assert warn["severity"] == "warn"
    assert record["gate"]["counts"]["warn"] >= 1
    # The adapter's own evidence, not a summary of it: the specific shortfall
    # ("declared last interval ... is not covered") is still readable.
    problems = " ".join(warn["context"]["problems"])
    assert "23:45" in problems
    assert warn["subject"] == path.name
    # And it survives a round trip through the registry file, which is what a
    # report six months from now will actually read.
    reloaded = observations.get_observation_set(record["id"])
    assert "daejeon_declared_window_not_covered" in reloaded["gate"]["codes"]


def test_the_file_name_site_id_warning_is_stored_too(workspace_root: Path, tmp_path: Path):
    """The other half of 1.2's documented deviation: the site id came from the
    file name and is a LABEL, not a controller id. The set has to carry that,
    because it is the reason the site -> node join is caller knowledge."""
    record = _ingest([_one_site_workbook(tmp_path)])
    assert "daejeon_site_id_from_file_name" in record["gate"]["codes"]


# ---------------------------------------------------------------------------
# 4. Streaming — the measured property
# ---------------------------------------------------------------------------


def test_ingest_peaks_far_below_a_one_shot_parse_of_the_same_files(
    workspace_root: Path, tmp_path: Path
):
    """MEASURED (chip task_b4b38b63): parsing the full drop in ONE
    ``adapter.parse(files)`` call peaks at ~6.3 GB, because all 3.09M pydantic
    records are alive at once inside one ``ImportResult``.

    U10's ingest therefore parses one WORKBOOK at a time, gates it, folds it
    into the accumulator and releases it. This test builds four multi-week
    workbooks and measures both shapes with ``tracemalloc``: the streaming
    ingest must peak materially below the one-shot parse of the same files,
    and in megabytes.

    The bound is deliberately loose (peaks move with openpyxl and pydantic
    versions); what is pinned is the RELATIONSHIP, which is the property that
    actually keeps a real drop off the swap file.
    """
    files = [
        _multi_day_workbook(
            tmp_path,
            site=f"측정{index}네거리",
            days=14,
            approaches=("동측방면", "서측방면", "남측방면"),
        )
        for index in range(4)
    ]
    period = ObservationPeriod(
        name="ALL", start=WEDNESDAY, end=WEDNESDAY + timedelta(days=14)
    )

    tracemalloc.start()
    try:
        record = _ingest(files, periods=(period,))
        streaming_peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()

    # The control: exactly what U10 must not do — every file in one parse.
    tracemalloc.start()
    try:
        one_shot = apply_quality_gate(DaejeonCountAdapter().parse(files))
        one_shot_records = len(one_shot.counts)
        one_shot_peak = tracemalloc.get_traced_memory()[1]
        del one_shot
    finally:
        tracemalloc.stop()

    # 12 bins x 14 days x 3 approaches x 18 summable records x 4 workbooks.
    assert record["site_count"] == 4
    assert record["coverage"]["records_aggregated"] == 36_288
    print(
        f"\n[U10 memory] streaming ingest peak = {streaming_peak / 1e6:.1f} MB; "
        f"one-shot parse of the same {len(files)} files = {one_shot_peak / 1e6:.1f} MB "
        f"({one_shot_records} records retained, "
        f"{one_shot_peak / max(one_shot_records, 1):.0f} bytes/record)"
    )
    assert streaming_peak < one_shot_peak, (
        f"streaming peak {streaming_peak} was not below the one-shot peak {one_shot_peak} — "
        "the ingest is holding the whole drop after all"
    )
    # Megabytes, not gigabytes. Measured at 24.3 MB against a 92.8 MB one-shot
    # (4 workbooks, 42,336 records, ~2.2 kB/record — which extrapolates the
    # real drop's 3.09M records to ~6.8 GB and corroborates the 6.3 GB the chip
    # measured). The bound leaves 2.5x headroom over the measurement while
    # still failing if the loop ever starts retaining records across files.
    assert streaming_peak < 64 * 1024 * 1024


def test_the_aggregate_is_identical_however_the_files_were_parsed(
    workspace_root: Path, tmp_path: Path
):
    """Streaming must not change the answer. Same files, per-file ingest vs a
    one-shot parse summed by hand: the same totals."""
    files = [
        _multi_day_workbook(tmp_path, site=f"동치{index}네거리", days=2)
        for index in range(3)
    ]
    period = ObservationPeriod(name="ALL", start=WEDNESDAY, end=WEDNESDAY + timedelta(days=2))

    record = observations.ingest_observation_set(files, name="동치", periods=[period])

    one_shot = DaejeonCountAdapter().parse(files)
    expected: dict[str, float] = {}
    for count in seedable_counts(one_shot.counts):
        if period.start <= count.interval_start < period.end:
            expected[count.site_id] = expected.get(count.site_id, 0.0) + count.volume

    assert {
        site_id: site["total"] for site_id, site in record["aggregates"]["ALL"].items()
    } == pytest.approx(expected)


# ---------------------------------------------------------------------------
# 5. Workspace isolation
# ---------------------------------------------------------------------------


def test_a_set_ingested_in_one_workspace_is_invisible_from_another(
    workspace_root: Path, tmp_path: Path
):
    """U5's cross-workspace pattern, applied to the store. Isolation here is
    PHYSICAL — the registry file itself lives inside the workspace directory —
    so it cannot be lost by forgetting a filter."""
    default_id = workspaces.default_workspace_id()
    other_id = workspaces.create_workspace("B")["id"]

    with workspaces.bind_workspace(default_id):
        record = _ingest([_one_site_workbook(tmp_path)])
        assert [entry["id"] for entry in observations.list_observation_sets()] == [record["id"]]
        path_a = observations.registry_path()

    with workspaces.bind_workspace(other_id):
        assert observations.list_observation_sets() == []
        assert observations.get_observation_set(record["id"]) is None
        assert observations.delete_observation_set(record["id"]) is False
        assert observations.observed_side(record["id"])["problem"] == observations.SET_NOT_FOUND
        path_b = observations.registry_path()

    assert path_a != path_b
    assert workspaces.workspace_dir(default_id) in path_a.parents
    assert workspaces.workspace_dir(other_id) in path_b.parents

    # And the reverse: B's own set does not appear in A.
    with workspaces.bind_workspace(other_id):
        other_record = _ingest([_one_site_workbook(tmp_path)])
    with workspaces.bind_workspace(default_id):
        assert [entry["id"] for entry in observations.list_observation_sets()] == [record["id"]]
        assert other_record["id"] != record["id"]


def test_delete_removes_only_the_named_set(workspace_root: Path, tmp_path: Path):
    first = _ingest([_one_site_workbook(tmp_path)], name="첫째")
    second = _ingest([_one_site_workbook(tmp_path)], name="둘째")

    assert observations.delete_observation_set(first["id"]) is True
    assert observations.delete_observation_set(first["id"]) is False
    assert [entry["id"] for entry in observations.list_observation_sets()] == [second["id"]]


@pytest.mark.parametrize(
    "bad_id", ["", "../../etc/passwd", "NOTHEX", "abc", "z" * 12, "a" * 64, "12/34"]
)
def test_a_malformed_set_id_never_reaches_the_filesystem(workspace_root: Path, bad_id: str):
    """Same hex-only, length-capped shape every other client-supplied id in this
    repo uses — a set id can neither hold a separator nor a ".." segment."""
    assert observations.get_observation_set(bad_id) is None
    assert observations.delete_observation_set(bad_id) is False
    assert observations.observed_side(bad_id)["problem"] == observations.SET_NOT_FOUND


# ---------------------------------------------------------------------------
# 6. Registry corruption -> quarantine (unit U6's contract)
# ---------------------------------------------------------------------------


def _corrupt_siblings(path: Path) -> list[Path]:
    return sorted(path.parent.glob(f"{path.name}.corrupt-*"))


@pytest.mark.parametrize(
    "corrupt_bytes",
    [
        pytest.param(b"", id="zero-length (the torn-write shape)"),
        pytest.param(b'{"sets": {"a": {"id"', id="truncated mid-write"),
        pytest.param(b"\xff\xfe\x00garbage", id="undecodable bytes"),
        pytest.param(b'["not", "a", "registry"]', id="parses, wrong shape"),
    ],
)
def test_a_registry_that_does_not_parse_is_quarantined_not_swallowed(
    workspace_root: Path, tmp_path: Path, corrupt_bytes: bytes
):
    record = _ingest([_one_site_workbook(tmp_path)])
    registry = observations.registry_path()
    assert record["id"] in json.loads(registry.read_text(encoding="utf-8"))["sets"]

    registry.write_bytes(corrupt_bytes)

    # A read degrades to "no sets" -- and says so, loudly and durably.
    assert observations.list_observation_sets() == []

    quarantined = _corrupt_siblings(registry)
    assert len(quarantined) == 1, f"expected exactly one quarantine sibling, got {quarantined}"
    assert quarantined[0].read_bytes() == corrupt_bytes, "the corrupt bytes were not preserved"

    status = observations.registry_health_status()
    assert status["code"] == "registry_quarantined"
    assert status["lost_record_count"] == 1
    assert status["quarantine_path"] == str(quarantined[0])
    for lang in ("ko", "en", "ja"):
        assert str(quarantined[0]) in status["messages"][lang]

    # The fresh registry is usable immediately, and still discloses the loss.
    again = _ingest([_one_site_workbook(tmp_path)], name="복구 후")
    assert [entry["id"] for entry in observations.list_observation_sets()] == [again["id"]]
    assert observations.registry_health_status()["code"] == "registry_quarantined"


def test_a_write_refuses_when_the_corrupt_file_cannot_be_moved_aside(
    workspace_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Writing a fresh registry over bytes we could not quarantine would destroy
    the only copy of them, so the write refuses instead."""
    _ingest([_one_site_workbook(tmp_path)])
    registry = observations.registry_path()
    registry.write_bytes(b"corrupt but locked")

    def _refuse(path: Path) -> Path:
        raise OSError(13, "Permission denied")

    monkeypatch.setattr(observations, "_quarantine_registry_file", _refuse)

    with pytest.raises(observations.RegistryUnavailableError):
        _ingest([_one_site_workbook(tmp_path)], name="이 세트는 저장되면 안 됨")

    assert registry.read_bytes() == b"corrupt but locked"
    assert _corrupt_siblings(registry) == []
    # The READ path still degrades quietly rather than raising into a listing.
    assert observations.list_observation_sets() == []


def test_a_non_object_entry_is_dropped_rather_than_crashing_the_listing(
    workspace_root: Path, tmp_path: Path
):
    record = _ingest([_one_site_workbook(tmp_path)])
    registry = observations.registry_path()
    payload = json.loads(registry.read_text(encoding="utf-8"))
    payload["sets"]["junk"] = "not an object"
    registry.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    listed = [entry["id"] for entry in observations.list_observation_sets()]

    assert listed == [record["id"]]
    assert _corrupt_siblings(registry) == [], "a repairable entry is not corruption"


# ---------------------------------------------------------------------------
# 7. The observed side: the site -> node join, reported in both directions
# ---------------------------------------------------------------------------


def _two_site_set(tmp_path: Path) -> dict[str, Any]:
    first = _one_site_workbook(tmp_path, site="가람네거리")
    second = _write_workbook(
        tmp_path,
        _approach_rows("한밭방면", start=WEDNESDAY, hours=3),
        file_name=_file_name(
            "한밭네거리", start=WEDNESDAY, last_start=WEDNESDAY + timedelta(hours=2, minutes=45)
        ),
    )
    return _ingest([first, second])


def test_the_observed_side_needs_a_caller_supplied_site_to_node_map(
    workspace_root: Path, tmp_path: Path
):
    """Daejeon site ids are intersection NAMES read from file names (1.2's
    documented deviation). They are not Vissim node ids, and nothing here
    guesses the join."""
    record = _two_site_set(tmp_path)

    side = observations.observed_side(record["id"], period="AM")

    assert side["nodes"] == {}
    assert side["problem"] == observations.SITE_NODE_MAP_MISSING
    # Named, not counted: the caller is told exactly which sites need mapping.
    assert side["coverage"]["sites_without_node_mapping"] == ["가람네거리", "한밭네거리"]


def test_a_mapped_site_joins_and_an_unmapped_one_is_named_not_dropped(
    workspace_root: Path, tmp_path: Path
):
    record = _two_site_set(tmp_path)

    side = observations.observed_side(
        record["id"], period="AM", site_nodes={"가람네거리": "1"}
    )

    assert side["problem"] is None
    assert list(side["nodes"]) == ["1"]
    node = side["nodes"]["1"]
    assert node["total_real"] == 12 * ROW_VOLUME
    assert node["node_name"] == "가람네거리"
    assert [direction["direction"] for direction in node["directions"]] == [
        f"{APPROACH}|left",
        f"{APPROACH}|right",
        f"{APPROACH}|through",
    ]
    assert node["total_sim_basam"] is None, "the store holds no modelled number, by construction"

    coverage = side["coverage"]
    assert coverage["sites_joined"] == ["가람네거리->1"]
    assert coverage["sites_without_node_mapping"] == ["한밭네거리"]
    assert coverage["counts"] == {
        "observation_sites": 2,
        "joined": 1,
        "unmapped": 1,
        "conflicting": 0,
    }


def test_two_sites_claiming_one_node_are_both_excluded_and_named(
    workspace_root: Path, tmp_path: Path
):
    """Merging two counted intersections into one node would be a modelling
    claim the store has no basis for, and a silent one at that."""
    record = _two_site_set(tmp_path)

    side = observations.observed_side(
        record["id"], period="AM", site_nodes={"가람네거리": "7", "한밭네거리": "7"}
    )

    assert side["nodes"] == {}
    assert side["problem"] == observations.NO_MAPPED_SITES
    assert side["coverage"]["sites_with_conflicting_node_mapping"] == [
        "가람네거리->7",
        "한밭네거리->7",
    ]


def test_a_mapped_site_the_set_does_not_hold_is_named(workspace_root: Path, tmp_path: Path):
    record = _two_site_set(tmp_path)

    side = observations.observed_side(
        record["id"], period="AM", site_nodes={"가람네거리": "1", "없는네거리": "9"}
    )

    assert side["coverage"]["mapped_sites_absent_from_observations"] == ["없는네거리"]
    assert list(side["nodes"]) == ["1"]


def test_a_node_id_that_would_be_misread_as_a_workbook_payload_is_refused(
    workspace_root: Path, tmp_path: Path
):
    """``reality_check.observed_nodes`` decides whether a payload is a loader
    result by looking for a ``counts``/``warnings`` key. A node id spelled like
    one would flip that branch and be read as a workbook, so it is refused by
    name instead of producing a silently wrong observed side."""
    record = _two_site_set(tmp_path)

    side = observations.observed_side(
        record["id"], period="AM", site_nodes={"가람네거리": "counts", "한밭네거리": "2"}
    )

    assert list(side["nodes"]) == ["2"]
    assert side["coverage"]["sites_with_rejected_node_id"] == ["가람네거리->counts"]


def test_an_absent_period_refuses_and_lists_the_ones_that_exist(
    workspace_root: Path, tmp_path: Path
):
    record = _ingest([_one_site_workbook(tmp_path)], periods=(FULL_PERIOD, FIRST_HOUR))

    side = observations.observed_side(record["id"], period="PM", site_nodes={SITE: "1"})

    assert side["problem"] == observations.PERIOD_ABSENT
    assert side["periods"] == ["AM", "AM1"]


def test_omitting_the_period_only_works_when_there_is_exactly_one(
    workspace_root: Path, tmp_path: Path
):
    single = _ingest([_one_site_workbook(tmp_path)], name="한 시간대")
    assert observations.observed_side(single["id"], site_nodes={SITE: "1"})["problem"] is None

    several = _ingest(
        [_one_site_workbook(tmp_path)], periods=(FULL_PERIOD, FIRST_HOUR), name="두 시간대"
    )
    side = observations.observed_side(several["id"], site_nodes={SITE: "1"})
    assert side["problem"] == observations.PERIOD_ABSENT
    assert side["periods"] == ["AM", "AM1"]


# ---------------------------------------------------------------------------
# 8. Staging
# ---------------------------------------------------------------------------


def test_staging_keeps_the_original_file_name(workspace_root: Path, tmp_path: Path):
    """The adapter reads the SITE ID and the declared window out of the file
    name, so a staging area that renamed the upload would destroy both."""
    path = _one_site_workbook(tmp_path)
    staged = observations.stage_workbook(path.name, path.read_bytes())

    assert staged["filename"] == path.name
    resolved = observations.staged_workbook_path(staged["token"])
    assert resolved.name == path.name

    record = _ingest([resolved])
    assert record["site_ids"] == [SITE]

    observations.discard_staged_workbook(staged["token"])
    with pytest.raises(observations.StagedWorkbookNotFoundError):
        observations.staged_workbook_path(staged["token"])


@pytest.mark.parametrize("token", ["", "../../secrets", "nothex", "a" * 31, "A" * 32])
def test_a_malformed_upload_token_is_rejected(workspace_root: Path, token: str):
    with pytest.raises(observations.StagedWorkbookNotFoundError):
        observations.staged_workbook_path(token)


def test_a_non_workbook_upload_is_refused_at_stage_time(workspace_root: Path):
    with pytest.raises(observations.ObservationRefusal) as excinfo:
        observations.stage_workbook("notes.txt", b"not a workbook")
    assert excinfo.value.code == "observation_upload_not_a_workbook"

    with pytest.raises(observations.ObservationRefusal) as excinfo:
        observations.stage_workbook("counts.xlsx", b"still not a workbook")
    assert excinfo.value.code == "observation_upload_unreadable"
    # The failed stage left nothing behind.
    assert list(observations.staging_dir().iterdir()) == []
