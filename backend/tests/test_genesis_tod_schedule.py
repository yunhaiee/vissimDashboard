"""Unit #70 — the 시간대 grammar and the schedule it builds.

Table-driven on purpose: the phrase is the one input this unit takes from a
human's typing, so the matrix of what is read and what is refused BY NAME is the
specification. Every case asserts a code, never prose.
"""

from __future__ import annotations

import pytest

from services.dataimport.base import IssueSeverity
from services.genesis import tod_schedule as tod

SUBJECT = "node 7 / 교차로 원신흥"


def _parse(phrase, key="L1-P1"):
    return tod.parse_tod_phrase(phrase, program_key=key, subject=SUBJECT)


def _candidate(key, phrase):
    windows, _ = _parse(phrase, key)
    return tod.TodCandidate(program_key=key, phrase=phrase, windows=windows)


# ---------------------------------------------------------------------------
# The phrase grammar
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "phrase,starts",
    [
        ("21:00", ("21:00",)),
        ("TOD1 21:00", ("21:00",)),
        ("TOD 1 21:00", ("21:00",)),
        ("TOD PLAN 1 21:00", ("21:00",)),
        ("tod1 07:00", ("07:00",)),
        ("07:00~09:00", ("07:00",)),
        ("07:00-09:00", ("07:00",)),
        ("07:00 ~ 09:00", ("07:00",)),
        ("00:00", ("00:00",)),
        ("23:59", ("23:59",)),
        # Canonicalised, not echoed: "7:00" and "07:00" are one time of day.
        ("9:30", ("09:30",)),
        ("7:00", ("07:00",)),
        # One plan selected twice in ONE day type is not a conflict.
        ("TOD1 21:00, TOD1 06:00", ("21:00", "06:00")),
        # The sheet's own writer joins with ", " — the exact shape
        # signals_daejeon._build_controller emits.
        ("TOD1 06:00, TOD1 21:00", ("06:00", "21:00")),
    ],
)
def test_readable_phrases_yield_their_start_times(phrase, starts):
    windows, issues = _parse(phrase)
    assert tuple(w.start_clock for w in windows) == starts
    assert [i.code for i in issues] == []


@pytest.mark.parametrize(
    "phrase,code",
    [
        (None, "tod_phrase_absent"),
        ("", "tod_phrase_absent"),
        ("   ", "tod_phrase_absent"),
        # The real messy shape from the brief: a bare TOD number with no clock.
        ("TOD2", "tod_phrase_states_no_clock"),
        ("TOD 2", "tod_phrase_states_no_clock"),
        ("TOD PLAN 3", "tod_phrase_states_no_clock"),
        ("25:00", "tod_phrase_not_a_clock"),
        ("24:00", "tod_phrase_not_a_clock"),
        ("12:60", "tod_phrase_not_a_clock"),
        ("99:99", "tod_phrase_not_a_clock"),
        ("07:00~25:00", "tod_phrase_not_a_clock"),
        ("07:00~07:00", "tod_phrase_empty_window"),
        ("아침", "tod_phrase_unreadable_remainder"),
        ("07:00 아침", "tod_phrase_unreadable_remainder"),
        ("peak", "tod_phrase_unreadable_remainder"),
        ("07:00~09:00~11:00", "tod_phrase_unreadable_remainder"),
        ("7", "tod_phrase_unreadable_remainder"),
        # Two DIFFERENT day types in one phrase.
        ("TOD1 21:00, TOD2 06:00", "tod_phrase_spans_multiple_day_plans"),
    ],
)
def test_unreadable_phrases_refuse_by_name(phrase, code):
    windows, issues = _parse(phrase)
    assert windows == ()
    assert [i.code for i in issues] == [code]


def test_the_real_brief_phrase_reads_its_clock_half_and_refuses_the_bare_half():
    """``"TOD1 21:00, TOD2"`` — the shape the unit brief quotes.

    It refuses, and the refusal names the bare segment rather than the phrase:
    the readable ``TOD1 21:00`` half is NOT kept, because half a plan's schedule
    reads as the whole of it.
    """
    windows, issues = _parse("TOD1 21:00, TOD2")
    assert windows == ()
    assert [i.code for i in issues] == ["tod_phrase_states_no_clock"]
    assert "TOD2" in issues[0].context["segment"]


#: Every phrase-level code, with a phrase that provokes it. Kept as a table so
#: that adding a code without pinning its SEVERITY fails
#: ``test_every_declared_code_is_provoked_and_is_a_warning`` below.
PHRASE_CASES: dict[str, str | None] = {
    "tod_phrase_absent": None,
    "tod_phrase_states_no_clock": "TOD2",
    "tod_phrase_not_a_clock": "25:00",
    "tod_phrase_unreadable_remainder": "아침",
    "tod_phrase_empty_window": "07:00~07:00",
    "tod_phrase_spans_multiple_day_plans": "TOD1 21:00, TOD2 06:00",
}

#: The same, at schedule level.
SCHEDULE_CASES: dict[str, list[tuple[str, str | None]]] = {
    "tod_schedule_no_entries": [("a", "TOD2"), ("b", None)],
    "tod_schedule_single_entry": [("a", "07:00"), ("b", "TOD2")],
    "tod_schedule_duplicate_start": [("a", "07:00"), ("b", "07:00"), ("c", "09:00")],
    "tod_schedule_spans_multiple_day_plans": [("a", "TOD1 07:00"), ("b", "TOD2 17:00")],
    "tod_schedule_mixes_switch_points_and_windows": [("a", "09:00"), ("b", "17:00~19:00")],
    "tod_schedule_uncovered_gap": [("a", "07:00~09:00"), ("b", "17:00~19:00")],
    "tod_schedule_overlapping_windows": [("a", "00:00~22:00"), ("b", "22:00~06:00")],
}


def test_every_finding_is_a_warning_never_a_refusal():
    """The anti-poison rule: a 시간대 nobody can read must not cost the reviewer
    the network. A REFUSE here would reach ``genesis_jobs._compose_side_layers``
    as ``side_inputs_refused`` and fail the whole generation."""
    for phrase in PHRASE_CASES.values():
        _, issues = _parse(phrase)
        assert issues
        assert all(i.severity is IssueSeverity.WARN for i in issues)


@pytest.mark.parametrize("code,phrase", sorted(PHRASE_CASES.items()))
def test_each_phrase_code_is_reachable_and_is_a_warning(code, phrase):
    _, issues = _parse(phrase)
    assert [i.code for i in issues] == [code]
    assert issues[0].severity is IssueSeverity.WARN


@pytest.mark.parametrize("code,candidates", sorted(SCHEDULE_CASES.items()))
def test_each_schedule_code_is_reachable_and_is_a_warning(code, candidates):
    schedule, issues = tod.build_tod_schedule(
        [_candidate(key, phrase) for key, phrase in candidates], subject=SUBJECT
    )
    assert schedule is None
    assert [i.code for i in issues] == [code]
    assert issues[0].severity is IssueSeverity.WARN


def test_every_declared_code_is_provoked_and_is_a_warning():
    """Both directions: no declared code is unreachable, and no reachable code
    is undeclared. A new code added without a case here fails this test rather
    than shipping unpinned."""
    covered = set(PHRASE_CASES) | set(SCHEDULE_CASES)
    assert covered == set(tod.TOD_ISSUE_CODES)


def test_every_emitted_code_is_declared_in_the_module_s_own_set():
    seen = set()
    for phrase in ("TOD2", "25:00", "아침", None, "07:00~07:00", "TOD1 21:00, TOD2"):
        _, issues = _parse(phrase)
        seen.update(i.code for i in issues)
    assert seen <= tod.TOD_ISSUE_CODES


def test_messages_are_three_language_and_ko_primary():
    for phrase in ("TOD2", "25:00", "아침"):
        _, issues = _parse(phrase)
        for issue in issues:
            assert set(issue.messages) == {"ko", "en", "ja"}
            assert all(text.strip() for text in issue.messages.values())


def test_clock_seconds_are_measured_from_midnight():
    windows, _ = _parse("21:00")
    assert windows[0].start_s == 21 * 3600
    windows, _ = _parse("00:00")
    assert windows[0].start_s == 0


def test_a_window_end_of_24_00_is_the_far_side_of_the_day():
    """``24:00`` is refused as a START and accepted as an END — where it is the
    one clock a tiling schedule needs and cannot write as 00:00."""
    windows, issues = _parse("21:00~24:00")
    assert [i.code for i in issues] == []
    assert windows[0].end_s == tod.SECONDS_PER_DAY


# ---------------------------------------------------------------------------
# The schedule
# ---------------------------------------------------------------------------


def test_switch_points_become_a_schedule_in_clock_order():
    schedule, issues = tod.build_tod_schedule(
        [
            _candidate("L1-P2", "TOD1 17:00"),
            _candidate("L1-P1", "TOD1 07:00"),
            _candidate("L1-P3", "TOD1 21:00"),
        ],
        subject=SUBJECT,
    )
    assert schedule is not None
    assert [e.start_clock for e in schedule.entries] == ["07:00", "17:00", "21:00"]
    assert [e.program_key for e in schedule.entries] == ["L1-P1", "L1-P2", "L1-P3"]
    assert schedule.family == tod.FAMILY_SWITCH_POINT
    assert schedule.day_plan_no == 1
    assert [i.code for i in issues] == []


def test_a_plan_that_refuses_leaves_the_others_scheduled():
    """The anti-poison guarantee at schedule level: one unreadable sibling does
    not cost the intersection its schedule."""
    schedule, issues = tod.build_tod_schedule(
        [
            _candidate("L1-P1", "07:00"),
            _candidate("L1-P2", "TOD2"),  # unreadable
            _candidate("L1-P3", "21:00"),
        ],
        subject=SUBJECT,
    )
    assert schedule is not None
    assert schedule.program_keys == ("L1-P1", "L1-P3")
    assert "L1-P2" not in {e.program_key for e in schedule.entries}
    assert [i.code for i in issues] == []


def test_a_tiled_day_of_windows_is_a_schedule():
    schedule, issues = tod.build_tod_schedule(
        [
            _candidate("L1-P1", "07:00~09:00"),
            _candidate("L1-P2", "09:00~17:00"),
            _candidate("L1-P3", "17:00~07:00"),  # overnight wrap
        ],
        subject=SUBJECT,
    )
    assert schedule is not None
    assert schedule.family == tod.FAMILY_WINDOW
    assert [e.start_clock for e in schedule.entries] == ["07:00", "09:00", "17:00"]
    assert [i.code for i in issues] == []


def test_a_day_of_windows_that_leaves_a_gap_is_refused_by_name():
    """The AM and PM peaks alone. Start-only scheduling would silently run the
    AM plan through the middle of the day.

    The stretch named is the maximal uncovered one joined ACROSS midnight
    (19:00–07:00) rather than the midday hole, because midnight is a boundary
    the sheet never mentions and splitting there would name half a gap."""
    schedule, issues = tod.build_tod_schedule(
        [
            _candidate("L1-P1", "07:00~09:00"),
            _candidate("L1-P2", "17:00~19:00"),
        ],
        subject=SUBJECT,
    )
    assert schedule is None
    assert [i.code for i in issues] == ["tod_schedule_uncovered_gap"]
    assert issues[0].context["gap_from"] == "19:00"
    assert issues[0].context["gap_to"] == "07:00"


def test_a_wrap_induced_double_booking_is_an_overlap_not_a_gap():
    """``00:00~22:00`` + ``22:00~06:00`` covers 22:00→06:00 twice over midnight.

    Walking neighbours after adding a day to the overnight end reported this as
    ``tod_schedule_uncovered_gap`` 06:00→00:00 — a stretch that is in fact
    DOUBLE-booked, so the reviewer was sent to add a window where one plan had
    to be removed. Coverage arithmetic tells the two apart.
    """
    schedule, issues = tod.build_tod_schedule(
        [_candidate("L1-P1", "00:00~22:00"), _candidate("L1-P2", "22:00~06:00")],
        subject=SUBJECT,
    )
    assert schedule is None
    assert [i.code for i in issues] == ["tod_schedule_overlapping_windows"]
    assert issues[0].context["overlap_from"] == "00:00"
    assert issues[0].context["overlap_to"] == "06:00"


def test_windows_that_both_overlap_and_gap_report_the_overlap():
    """Two half-days summing to exactly 86400 while overlapping at noon and
    leaving the evening empty. A total-seconds check alone would call this a
    tiled day; the union is what catches it."""
    schedule, issues = tod.build_tod_schedule(
        [_candidate("L1-P1", "00:00~12:00"), _candidate("L1-P2", "06:00~18:00")],
        subject=SUBJECT,
    )
    assert schedule is None
    assert [i.code for i in issues] == ["tod_schedule_overlapping_windows"]
    assert issues[0].context["overlap_from"] == "06:00"
    assert issues[0].context["overlap_to"] == "12:00"


def test_windows_that_do_not_wrap_to_the_first_start_are_refused():
    schedule, issues = tod.build_tod_schedule(
        [
            _candidate("L1-P1", "07:00~09:00"),
            _candidate("L1-P2", "09:00~17:00"),
        ],
        subject=SUBJECT,
    )
    assert schedule is None
    assert [i.code for i in issues] == ["tod_schedule_uncovered_gap"]


def test_overlapping_windows_are_named_as_an_overlap_not_as_a_gap():
    """Seen through the gap test's arithmetic an overlap reads as a backwards
    gap ("leaves 10:00–09:00 uncovered"), which would send whoever fixes the
    sheet after the wrong defect."""
    schedule, issues = tod.build_tod_schedule(
        [
            _candidate("L1-P1", "07:00~10:00"),
            _candidate("L1-P2", "09:00~17:00"),
            _candidate("L1-P3", "17:00~07:00"),
        ],
        subject=SUBJECT,
    )
    assert schedule is None
    assert [i.code for i in issues] == ["tod_schedule_overlapping_windows"]
    assert issues[0].context["overlap_from"] == "09:00"
    assert issues[0].context["overlap_to"] == "10:00"


@pytest.mark.parametrize("phrase", ["07:00, 07:00", "TOD1 7:00, TOD1 07:00", "7:00, 07:00"])
def test_one_plan_stating_the_same_start_twice_yields_one_switch_point(phrase):
    """Including MIXED SPELLINGS. De-duplication keys on the entry, so a clock
    carried as typed would make ``7:00`` and ``07:00`` two entries and emit the
    same switch point twice — which is why the parse canonicalises."""
    schedule, _ = tod.build_tod_schedule(
        [_candidate("L1-P1", phrase), _candidate("L1-P2", "17:00")],
        subject=SUBJECT,
    )
    assert schedule is not None
    assert [(e.program_key, e.start_clock) for e in schedule.entries] == [
        ("L1-P1", "07:00"),
        ("L1-P2", "17:00"),
    ]


def test_switch_points_and_windows_cannot_be_mixed():
    schedule, issues = tod.build_tod_schedule(
        [
            _candidate("L1-P1", "09:00"),
            _candidate("L1-P2", "17:00~19:00"),
        ],
        subject=SUBJECT,
    )
    assert schedule is None
    assert [i.code for i in issues] == ["tod_schedule_mixes_switch_points_and_windows"]


def test_two_plans_claiming_one_start_time_refuse_rather_than_pick():
    schedule, issues = tod.build_tod_schedule(
        [
            _candidate("L1-P1", "07:00"),
            _candidate("L1-P2", "07:00"),
            _candidate("L1-P3", "17:00"),
        ],
        subject=SUBJECT,
    )
    assert schedule is None
    assert [i.code for i in issues] == ["tod_schedule_duplicate_start"]


def test_plans_spanning_two_day_types_refuse_at_schedule_level_too():
    """Each phrase is individually fine; together they are two different days."""
    schedule, issues = tod.build_tod_schedule(
        [
            _candidate("L1-P1", "TOD1 07:00"),
            _candidate("L1-P2", "TOD2 17:00"),
        ],
        subject=SUBJECT,
    )
    assert schedule is None
    assert [i.code for i in issues] == ["tod_schedule_spans_multiple_day_plans"]


def test_one_readable_plan_is_not_a_schedule():
    schedule, issues = tod.build_tod_schedule(
        [_candidate("L1-P1", "07:00"), _candidate("L1-P2", "TOD2")],
        subject=SUBJECT,
    )
    assert schedule is None
    assert [i.code for i in issues] == ["tod_schedule_single_entry"]


def test_one_plan_at_two_clocks_is_still_not_a_schedule():
    """Two entries, one program: there is no second program to switch TO."""
    schedule, issues = tod.build_tod_schedule(
        [_candidate("L1-P1", "TOD1 06:00, TOD1 21:00")], subject=SUBJECT
    )
    assert schedule is None
    assert [i.code for i in issues] == ["tod_schedule_single_entry"]


def test_no_readable_plan_falls_back_and_says_so():
    schedule, issues = tod.build_tod_schedule(
        [_candidate("L1-P1", "TOD2"), _candidate("L1-P2", None)], subject=SUBJECT
    )
    assert schedule is None
    assert [i.code for i in issues] == ["tod_schedule_no_entries"]


def test_one_program_may_appear_at_several_switch_points():
    """An overnight plan that also covers the pre-dawn hours is one program with
    two entries — legitimate, and distinct from two plans claiming one time."""
    schedule, _ = tod.build_tod_schedule(
        [
            _candidate("L1-P1", "TOD1 06:00, TOD1 21:00"),
            _candidate("L1-P2", "TOD1 09:00"),
        ],
        subject=SUBJECT,
    )
    assert schedule is not None
    assert [e.program_key for e in schedule.entries] == ["L1-P1", "L1-P2", "L1-P1"]
    assert schedule.program_keys == ("L1-P1", "L1-P2")


def test_every_schedule_level_code_is_declared():
    seen = set()
    for candidates in (
        [_candidate("a", "07:00"), _candidate("b", "17:00~19:00")],
        [_candidate("a", "07:00"), _candidate("b", "07:00"), _candidate("c", "09:00")],
        [_candidate("a", "TOD1 07:00"), _candidate("b", "TOD2 17:00")],
        [_candidate("a", "07:00~09:00"), _candidate("b", "17:00~19:00")],
        [_candidate("a", "TOD2")],
        [_candidate("a", "07:00"), _candidate("b", "TOD2")],
    ):
        _, issues = tod.build_tod_schedule(candidates, subject=SUBJECT)
        seen.update(i.code for i in issues)
    assert seen <= tod.TOD_ISSUE_CODES
    assert all(
        i.severity is IssueSeverity.WARN
        for candidates in ([_candidate("a", "07:00"), _candidate("b", "07:00")],)
        for i in tod.build_tod_schedule(candidates, subject=SUBJECT)[1]
    )
