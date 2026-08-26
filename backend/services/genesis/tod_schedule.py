"""시간대 (time-of-day) phrases → a signal-program switching schedule.

Unit #70. The 대전 ``교차로 운영 DATABASE`` sheet prints, for every (수준, 계획 ID)
pair it schedules, the clock times that select it. ``signals_daejeon._build_controller``
already reads those into :class:`~services.genesis.signals_daejeon.TodEntry`
records and then FLATTENS them into one string —
``time_of_day=", ".join(sorted(set(clocks)))`` — which
:class:`~services.dataimport.schema.SignalProgramData` carries as
``time_of_day`` and which every downstream layer has stored and never used.
This module is the reader that turns that string back into a schedule, and it
is deliberately stricter than the writer that produced it.

Why a parser at all, when the structured records exist upstream: the string is
the ONLY form that survives to the side-input document. The Excel route (unit
#52) has no upstream records at all — a reviewer types the 시간대 cell by hand —
so the phrase is the single common currency of both routes. Parsing it is
therefore not a re-derivation of something already known; for one of the two
routes it is the only reading there has ever been.


The grammar
-----------

::

    phrase   := segment ("," segment)*
    segment  := [ "TOD" [ "PLAN" ] <n> ] window
    window   := HH:MM                       (a SWITCH POINT)
              | HH:MM SEP HH:MM             (a WINDOW)
    SEP      := "~" | "-" | "–" | "—" | "〜"

Accepted, with the source each form was measured on:

===============================  ==========================================
``"21:00"``                      a bare switch point
``"TOD1 21:00"``                 ``signals_daejeon`` writes exactly this
``"TOD1 21:00, TOD1 06:00"``     one plan selected twice in one day
``"07:00~09:00"``                measured in ``test_genesis_phase_template``
``"07:00-09:00"``                the same window, hyphenated
===============================  ==========================================

Refused BY NAME, never guessed:

* ``"TOD2"`` — a TOD plan number with no clock. The sheet's own writer only
  emits a bare ``TOD{n}`` when the 시각 column was blank, so this is precisely
  the case where the source did not say when.
* ``"25:00"``, ``"12:60"`` — not a clock.
* ``"TOD1 21:00, TOD2 06:00"`` — clock times under two DIFFERENT TOD plan
  numbers. This is the one refusal a reader might mistake for pedantry, so it
  is worth stating plainly: ``TOD PLAN n`` is a DAY TYPE, not a time slot.
  ``signals_daejeon._parse_day_assignments`` reads ``요일 TOD PLAN TIME PLAN``
  rows into :class:`~services.genesis.signals_daejeon.DayAssignment`, which maps
  a WEEKDAY to a ``tod_plan_no``; and the sheet header carries one 시각 column
  per TOD PLAN. So "TOD1 21:00, TOD2 06:00" means *under the weekday schedule
  this plan starts at 21:00; under the Saturday schedule it starts at 06:00*.
  One simulation run is one day. Collapsing two day types into one schedule
  would invent a day that the intersection never runs.
* anything left over in a segment after the TOD prefix and the window have been
  taken — a stray word is a fact this module cannot read, and reading around it
  is how a parser silently drops half a sentence.

Every finding this module produces is a **WARN**, never a REFUSE, and that is
load-bearing rather than lax. ``compose_from_documents`` folds signal findings
into ``ComposedLayers.issues``, and ``genesis_jobs._compose_side_layers`` turns
``not layers.ok`` into ``GenesisRefused("side_inputs_refused")`` — which refuses
the WHOLE generation. A 시간대 cell nobody can read must not cost a reviewer the
network they confirmed; it costs them the automatic switching, and it says so.
That is the anti-poison rule of this unit: an unreadable phrase removes ONE plan
from the schedule, and an unreadable controller falls back to the single
confirmed plan, which is exactly today's behaviour.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence

from ..dataimport.base import ImportIssue, IssueSeverity

SECONDS_PER_DAY = 24 * 3600

#: The switch-point family and the window family cannot be mixed — see
#: :func:`build_tod_schedule`.
FAMILY_SWITCH_POINT = "switch_point"
FAMILY_WINDOW = "window"

#: Every code this module can emit. Tests assert on codes, never on prose, and
#: this set is what a reviewer greps for when a schedule is smaller than the
#: sheet led them to expect.
TOD_ISSUE_CODES: frozenset[str] = frozenset(
    {
        "tod_phrase_absent",
        "tod_phrase_states_no_clock",
        "tod_phrase_not_a_clock",
        "tod_phrase_unreadable_remainder",
        "tod_phrase_empty_window",
        "tod_phrase_spans_multiple_day_plans",
        "tod_schedule_spans_multiple_day_plans",
        "tod_schedule_mixes_switch_points_and_windows",
        "tod_schedule_duplicate_start",
        "tod_schedule_uncovered_gap",
        "tod_schedule_overlapping_windows",
        "tod_schedule_single_entry",
        "tod_schedule_no_entries",
    }
)

#: ``TOD1`` / ``TOD 1`` / ``TOD PLAN 1`` / ``TODPLAN1``. Anchored at the segment
#: start: a ``TOD`` appearing after the clock is a remainder, not a prefix.
_TOD_PREFIX = re.compile(r"^TOD\s*(?:PLAN\s*)?(\d{1,2})\b", re.IGNORECASE)

#: The same shape ``signals_daejeon._CLOCK`` accepts (``^\d{1,2}:\d{2}$``),
#: matched here in the middle of a segment rather than over a whole cell.
_CLOCK = r"\d{1,2}:\d{2}"

#: Range separators seen on Korean and English sheets. The ASCII hyphen is
#: included, which is why a NEGATIVE time can never parse: the left side must
#: already be a clock for the window branch to be tried at all.
_WINDOW = re.compile(rf"^({_CLOCK})\s*[~\-–—〜]\s*({_CLOCK})$")
_SWITCH_POINT = re.compile(rf"^({_CLOCK})$")


@dataclass(frozen=True)
class TodWindow:
    """One segment of a phrase, read.

    ``end_s`` is ``None`` for the switch-point family. For the window family it
    is the END the source stated, and ``end_s == SECONDS_PER_DAY`` is how a
    stated ``24:00`` is carried — see :func:`_clock_to_seconds`.
    """

    start_s: int
    start_clock: str
    end_s: int | None
    end_clock: str | None
    day_plan_no: int | None

    @property
    def family(self) -> str:
        return FAMILY_SWITCH_POINT if self.end_s is None else FAMILY_WINDOW


@dataclass(frozen=True)
class TodCandidate:
    """One plan offered to the schedule, with its phrase already read.

    ``windows`` empty means the phrase refused; the refusal is in the issue list
    the parse returned, keyed to this ``program_key``.
    """

    program_key: str
    phrase: str | None
    windows: tuple[TodWindow, ...]


@dataclass(frozen=True)
class TodScheduleEntry:
    """At ``start_clock``, the controller switches to ``program_key``."""

    program_key: str
    start_s: int
    start_clock: str


@dataclass(frozen=True)
class TodSchedule:
    """A controller's whole day, as switch points in ascending clock order.

    ``entries`` is never shorter than 2 — a one-entry schedule is not a schedule
    and :func:`build_tod_schedule` returns ``None`` for it, naming
    ``tod_schedule_single_entry``.
    """

    entries: tuple[TodScheduleEntry, ...]
    day_plan_no: int | None
    family: str

    @property
    def program_keys(self) -> tuple[str, ...]:
        """The distinct programs the schedule names, in first-use order."""
        seen: list[str] = []
        for entry in self.entries:
            if entry.program_key not in seen:
                seen.append(entry.program_key)
        return tuple(seen)


def _issue(
    code: str,
    message: str,
    *,
    messages: dict[str, str],
    subject: str,
    context: dict | None = None,
) -> ImportIssue:
    """Always WARN. See the module docstring's anti-poison rule."""
    return ImportIssue(
        severity=IssueSeverity.WARN,
        code=code,
        message=message,
        messages=messages,
        subject=subject,
        context=context or {},
    )


def _clock_to_seconds(clock: str, *, allow_end_of_day: bool) -> int | None:
    """``"21:00"`` → 75600, or ``None`` when it is not a clock time.

    ``24:00`` is accepted ONLY as a window's end, where it means midnight at the
    far side of the day and is returned as :data:`SECONDS_PER_DAY` so that the
    day-coverage arithmetic in :func:`build_tod_schedule` needs no special case.
    As a START it is rejected: a plan that begins at the end of the day begins
    at no time in it.
    """
    hour_text, _, minute_text = clock.partition(":")
    hour, minute = int(hour_text), int(minute_text)
    if minute > 59:
        return None
    if hour == 24 and minute == 0 and allow_end_of_day:
        return SECONDS_PER_DAY
    if hour > 23:
        return None
    return hour * 3600 + minute * 60


def parse_tod_phrase(
    phrase: str | None, *, program_key: str, subject: str
) -> tuple[tuple[TodWindow, ...], list[ImportIssue]]:
    """One plan's 시간대 phrase → its windows, or ``()`` and a named WARN.

    All-or-nothing per PLAN: a phrase with one unreadable segment yields no
    windows at all rather than the readable half. Half a schedule for one plan
    is a plan that runs at some of the times the sheet states, which reads as a
    fact and is not one.
    """
    issues: list[ImportIssue] = []
    text = str(phrase or "").strip()
    if not text:
        issues.append(
            _issue(
                "tod_phrase_absent",
                f"{program_key} states no 시간대, so nothing says when it runs.",
                messages={
                    "ko": f"{program_key}에 시간대가 적혀 있지 않습니다. 언제 운영되는지 "
                    "출처가 말하지 않으므로 전환 일정에 넣지 않습니다.",
                    "en": f"{program_key} states no time-of-day, so it is left out of the "
                    "switching schedule.",
                    "ja": f"{program_key} に時間帯の記載がないため、切替スケジュールに"
                    "含めません。",
                },
                subject=subject,
                context={"program_key": program_key},
            )
        )
        return (), issues

    windows: list[TodWindow] = []
    day_plans: set[int] = set()
    for raw_segment in text.split(","):
        segment = raw_segment.strip()
        if not segment:
            continue

        day_plan_no: int | None = None
        prefix = _TOD_PREFIX.match(segment)
        if prefix is not None:
            day_plan_no = int(prefix.group(1))
            segment = segment[prefix.end() :].strip()

        if not segment:
            issues.append(
                _issue(
                    "tod_phrase_states_no_clock",
                    f"{program_key}: 시간대 segment {raw_segment.strip()!r} names a TOD plan "
                    "but no clock time. The sheet writes a bare TOD number exactly when its "
                    "시각 column was blank, so this says the source did not state when.",
                    messages={
                        "ko": f"{program_key}: 시간대 {raw_segment.strip()!r}에 TOD 번호만 "
                        "있고 시각이 없습니다. 시각을 추측하지 않고 이 계획을 전환 일정에서 "
                        "제외합니다.",
                        "en": f"{program_key}: segment {raw_segment.strip()!r} names a TOD "
                        "plan but no clock time; the plan is left out rather than guessed.",
                        "ja": f"{program_key}: {raw_segment.strip()!r} に TOD 番号のみで時刻が"
                        "ありません。推測せずこの計画を除外します。",
                    },
                    subject=subject,
                    context={"program_key": program_key, "segment": raw_segment.strip()},
                )
            )
            return (), issues

        window_match = _WINDOW.match(segment)
        point_match = _SWITCH_POINT.match(segment)
        if window_match is None and point_match is None:
            issues.append(
                _issue(
                    "tod_phrase_unreadable_remainder",
                    f"{program_key}: 시간대 segment {raw_segment.strip()!r} carries "
                    f"{segment!r}, which is neither a clock time nor a HH:MM~HH:MM window. "
                    "Reading around it would drop whatever it says.",
                    messages={
                        "ko": f"{program_key}: 시간대 {raw_segment.strip()!r}의 {segment!r} "
                        "부분을 읽을 수 없습니다. 무시하고 넘어가면 출처가 적은 내용을 "
                        "버리게 되므로 이 계획을 제외합니다.",
                        "en": f"{program_key}: {segment!r} is neither a clock time nor a "
                        "HH:MM~HH:MM window, so the plan is left out.",
                        "ja": f"{program_key}: {segment!r} は時刻でも HH:MM~HH:MM の区間でも"
                        "ないため、この計画を除外します。",
                    },
                    subject=subject,
                    context={"program_key": program_key, "segment": raw_segment.strip()},
                )
            )
            return (), issues

        if point_match is not None:
            start_clock, end_clock = point_match.group(1), None
        else:
            start_clock, end_clock = window_match.group(1), window_match.group(2)

        start_s = _clock_to_seconds(start_clock, allow_end_of_day=False)
        end_s = (
            None if end_clock is None else _clock_to_seconds(end_clock, allow_end_of_day=True)
        )
        bad = start_clock if start_s is None else (end_clock if end_s is None else None)
        if bad is not None:
            issues.append(
                _issue(
                    "tod_phrase_not_a_clock",
                    f"{program_key}: {bad!r} is not a time of day (00:00–23:59; 24:00 is "
                    "accepted only as a window's end).",
                    messages={
                        "ko": f"{program_key}: {bad!r}은(는) 실제 시각이 아닙니다"
                        "(00:00–23:59, 24:00은 구간의 종료로만 허용). 이 계획을 제외합니다.",
                        "en": f"{program_key}: {bad!r} is not a time of day, so the plan is "
                        "left out.",
                        "ja": f"{program_key}: {bad!r} は実在する時刻ではないため、この計画を"
                        "除外します。",
                    },
                    subject=subject,
                    context={"program_key": program_key, "clock": bad},
                )
            )
            return (), issues

        if end_s is not None and end_s == start_s:
            issues.append(
                _issue(
                    "tod_phrase_empty_window",
                    f"{program_key}: window {segment!r} starts and ends at the same time, so "
                    "it covers nothing.",
                    messages={
                        "ko": f"{program_key}: {segment!r}의 시작과 종료가 같아 구간이 "
                        "비어 있습니다. 이 계획을 제외합니다.",
                        "en": f"{program_key}: window {segment!r} covers no time at all.",
                        "ja": f"{program_key}: {segment!r} は開始と終了が同じで区間が空です。",
                    },
                    subject=subject,
                    context={"program_key": program_key, "segment": segment},
                )
            )
            return (), issues

        if day_plan_no is not None:
            day_plans.add(day_plan_no)
        # Clocks are CANONICALISED here, not carried as typed. ``"7:00"`` and
        # ``"07:00"`` are one time of day, and a sheet may spell both; keeping
        # the spelling would make them two distinct schedule entries that
        # de-duplication cannot see, and emit the same switch point twice.
        windows.append(
            TodWindow(
                start_s=start_s,
                start_clock=seconds_to_clock(start_s),
                end_s=end_s,
                end_clock=None if end_s is None else seconds_to_clock(end_s),
                day_plan_no=day_plan_no,
            )
        )

    if len(day_plans) > 1:
        # DO THE DAY TYPES ACTUALLY DISAGREE?
        #
        # Measured on the real authority fixture
        # (``tests/fixtures/daejeon_signal/clean_five_phase.txt``): every program
        # on it reads like ``"TOD1 00:00, TOD2 00:00, TOD3 00:00"`` — the same
        # clock under all three day types. An earlier version of this function
        # refused any phrase naming more than one TOD plan, which refused every
        # program of every real sheet and made the whole feature unreachable on
        # its own source data.
        #
        # A day type only forces a human decision when the day types state
        # DIFFERENT times. When they state the same one there is nothing to
        # choose: the plan starts at that clock whichever day is simulated, so
        # the windows collapse to one set and the day-type tag is dropped.
        by_day: dict[int | None, set[tuple[int, int | None]]] = {}
        for window in windows:
            by_day.setdefault(window.day_plan_no, set()).add(
                (window.start_s, window.end_s)
            )
        stated = list(by_day.values())
        if all(entry == stated[0] for entry in stated):
            collapsed: list[TodWindow] = []
            for window in windows:
                if any(
                    kept.start_s == window.start_s and kept.end_s == window.end_s
                    for kept in collapsed
                ):
                    continue
                collapsed.append(
                    TodWindow(
                        start_s=window.start_s,
                        start_clock=window.start_clock,
                        end_s=window.end_s,
                        end_clock=window.end_clock,
                        day_plan_no=None,
                    )
                )
            return tuple(collapsed), issues

        issues.append(
            _issue(
                "tod_phrase_spans_multiple_day_plans",
                f"{program_key}: 시간대 names clock times under TOD plans "
                f"{sorted(day_plans)}. A TOD plan is a DAY TYPE (주간신호계획 maps each "
                "weekday to one), and one run is one day, so these cannot be one schedule.",
                messages={
                    "ko": f"{program_key}: 시간대가 TOD {sorted(day_plans)} 두 개 이상의 "
                    "요일 유형에 걸쳐 있습니다. 주간신호계획은 요일마다 TOD를 지정하고 "
                    "한 번의 시뮬레이션은 하루이므로, 어느 요일인지는 사람이 정할 일입니다.",
                    "en": f"{program_key}: the phrase spans TOD plans {sorted(day_plans)}, "
                    "which are DAY TYPES; one run is one day, so which day is a human's call.",
                    "ja": f"{program_key}: 時間帯が TOD {sorted(day_plans)} という複数の曜日"
                    "種別にまたがります。どの曜日かは人が決める事項です。",
                },
                subject=subject,
                context={"program_key": program_key, "day_plans": sorted(day_plans)},
            )
        )
        return (), issues

    if not windows:
        issues.append(
            _issue(
                "tod_phrase_states_no_clock",
                f"{program_key}: 시간대 {text!r} carries no readable segment.",
                messages={
                    "ko": f"{program_key}: 시간대 {text!r}에서 읽을 수 있는 구간이 "
                    "없습니다.",
                    "en": f"{program_key}: {text!r} carries no readable segment.",
                    "ja": f"{program_key}: {text!r} に読み取れる区間がありません。",
                },
                subject=subject,
                context={"program_key": program_key, "phrase": text},
            )
        )
        return (), issues

    return tuple(windows), issues


def build_tod_schedule(
    candidates: Sequence[TodCandidate], *, subject: str
) -> tuple[TodSchedule | None, list[ImportIssue]]:
    """The parseable candidates → one controller's schedule, or ``None``.

    ``None`` means "run the single confirmed plan, exactly as before"; the
    reason is always in the returned issues. The caller never has to infer a
    fallback from an empty result.

    The two families cannot be mixed. A switch point means *run this until the
    next switch point*; a window states its own end. In one schedule a bare
    ``09:00`` sitting beside ``17:00~19:00`` gives no way to tell whether the
    09:00 plan stops at 17:00 or was meant to have an end nobody wrote — so the
    schedule is refused rather than resolved one of the two ways.

    For the window family the day must be TILED: every window's end is the next
    window's start, and the last wraps to the first. A gap is refused because
    the sheet does not say what runs in it, and carrying the previous plan
    across the gap would state, in the emitted network, an operating hour the
    intersection does not keep.
    """
    issues: list[ImportIssue] = []
    usable = [candidate for candidate in candidates if candidate.windows]

    if not usable:
        issues.append(
            _issue(
                "tod_schedule_no_entries",
                "No plan on this controller states a readable 시간대, so it keeps the single "
                "confirmed plan — today's behaviour — rather than an invented schedule.",
                messages={
                    "ko": "이 제어기의 어떤 계획도 읽을 수 있는 시간대를 갖고 있지 않습니다. "
                    "확인된 단일 계획 그대로 두고 자동 전환은 구성하지 않습니다.",
                    "en": "No plan states a readable time-of-day, so the single confirmed plan "
                    "is kept and no switching is configured.",
                    "ja": "読み取れる時間帯を持つ計画がないため、確認済みの単一計画のまま"
                    "とし自動切替は構成しません。",
                },
                subject=subject,
            )
        )
        return None, issues

    families = {window.family for candidate in usable for window in candidate.windows}
    if len(families) > 1:
        issues.append(
            _issue(
                "tod_schedule_mixes_switch_points_and_windows",
                "This controller states some 시간대 as bare switch points and others as "
                "HH:MM~HH:MM windows. A bare start beside a stated window gives no way to "
                "tell where the bare one ends, so no schedule is built.",
                messages={
                    "ko": "이 제어기의 시간대가 시각 하나(전환 시점)와 HH:MM~HH:MM 구간으로 "
                    "섞여 있습니다. 시각만 적힌 계획이 언제 끝나는지 판단할 근거가 없어 "
                    "일정을 만들지 않습니다.",
                    "en": "Some plans state a bare switch point and others a HH:MM~HH:MM "
                    "window; where the bare one ends cannot be told, so no schedule is built.",
                    "ja": "時刻のみの切替点と HH:MM~HH:MM の区間が混在しています。時刻のみの"
                    "計画の終了時刻が判断できないため、スケジュールを作成しません。",
                },
                subject=subject,
                context={"families": sorted(families)},
            )
        )
        return None, issues

    family = families.pop()
    day_plans = {
        window.day_plan_no
        for candidate in usable
        for window in candidate.windows
        if window.day_plan_no is not None
    }
    if len(day_plans) > 1:
        issues.append(
            _issue(
                "tod_schedule_spans_multiple_day_plans",
                f"The plans on this controller name TOD plans {sorted(day_plans)}, which are "
                "DAY TYPES. One run is one day; which day to build is a human's decision and "
                "no unit has made it.",
                messages={
                    "ko": f"이 제어기의 계획들이 TOD {sorted(day_plans)}(요일 유형)에 걸쳐 "
                    "있습니다. 한 번의 시뮬레이션은 하루이므로 어느 요일을 만들지는 사람이 "
                    "정할 일이며, 아직 정해지지 않았습니다.",
                    "en": f"The plans name TOD plans {sorted(day_plans)} — day types. Which "
                    "day to build is a human's decision that no unit has made.",
                    "ja": f"計画が TOD {sorted(day_plans)}(曜日種別)にまたがります。どの曜日"
                    "を構築するかは未決定の人間の判断事項です。",
                },
                subject=subject,
                context={"day_plans": sorted(day_plans)},
            )
        )
        return None, issues

    # A set, then sorted: one plan may legitimately state the same start twice
    # (``"07:00, 07:00"``), and emitting two identical switch points would put a
    # redundant instruction in the file rather than a second fact.
    unique: set[TodScheduleEntry] = set()
    for candidate in usable:
        for window in candidate.windows:
            unique.add(
                TodScheduleEntry(
                    program_key=candidate.program_key,
                    start_s=window.start_s,
                    start_clock=window.start_clock,
                )
            )
    entries = sorted(unique, key=lambda entry: (entry.start_s, entry.program_key))

    by_start: dict[int, list[str]] = {}
    for entry in entries:
        by_start.setdefault(entry.start_s, []).append(entry.program_key)
    clashes = {start: keys for start, keys in by_start.items() if len(set(keys)) > 1}
    if clashes:
        named = ", ".join(
            f"{seconds_to_clock(start)} → {sorted(set(keys))}"
            for start, keys in sorted(clashes.items())
        )
        issues.append(
            _issue(
                "tod_schedule_duplicate_start",
                f"Two plans claim the same start time ({named}). Which one runs is not "
                "derivable from the sheet, and picking one would be a guess.",
                messages={
                    "ko": f"같은 시각에 두 계획이 동시에 시작한다고 적혀 있습니다({named}). "
                    "어느 쪽이 운영되는지는 시트에서 알 수 없어 추측하지 않고 일정을 "
                    "만들지 않습니다.",
                    "en": f"Two plans claim the same start time ({named}); which one runs is "
                    "not derivable, so no schedule is built.",
                    "ja": f"同じ時刻に 2 つの計画が開始すると記載されています({named})。"
                    "どちらが運用されるか判断できないためスケジュールを作成しません。",
                },
                subject=subject,
                context={"clashes": {str(k): sorted(set(v)) for k, v in clashes.items()}},
            )
        )
        return None, issues

    if family == FAMILY_WINDOW:
        defect = _tiling_defect(usable)
        if defect is not None and defect[0] == "overlap":
            _, starts_at, ends_at = defect
            issues.append(
                _issue(
                    "tod_schedule_overlapping_windows",
                    f"Two stated windows both claim {starts_at}–{ends_at}. Two plans cannot run "
                    "at once and the sheet does not say which wins, so no schedule is built.",
                    messages={
                        "ko": f"두 구간이 {starts_at}–{ends_at}을(를) 동시에 차지합니다. 두 "
                        "계획이 동시에 운영될 수는 없고 어느 쪽이 우선인지 시트가 말하지 "
                        "않으므로 일정을 만들지 않습니다.",
                        "en": f"Two windows both claim {starts_at}–{ends_at}; the sheet does "
                        "not say which wins, so no schedule is built.",
                        "ja": f"2 つの区間が {starts_at}–{ends_at} を重複して占めています。"
                        "どちらが優先か不明のためスケジュールを作成しません。",
                    },
                    subject=subject,
                    context={"overlap_from": starts_at, "overlap_to": ends_at},
                )
            )
            return None, issues
        if defect is not None and defect[0] == "gap":
            _, after, before = defect
            issues.append(
                _issue(
                    "tod_schedule_uncovered_gap",
                    f"The stated windows leave {after}–{before} uncovered. The sheet does not "
                    "say what runs then, and stretching the previous plan across the gap would "
                    "put an operating hour in the network that the intersection does not keep.",
                    messages={
                        "ko": f"적힌 구간들이 {after}–{before}을(를) 덮지 않습니다. 그 시간에 "
                        "무엇이 운영되는지 시트가 말하지 않으며, 앞 계획을 늘려서 채우면 "
                        "실제와 다른 운영 시간을 네트워크에 넣게 되므로 일정을 만들지 "
                        "않습니다.",
                        "en": f"The stated windows leave {after}–{before} uncovered; filling "
                        "it from the previous plan would state an operating hour the "
                        "intersection does not keep.",
                        "ja": f"記載された区間が {after}–{before} を覆っていません。前の計画"
                        "で埋めると実際と異なる運用時間を記録することになります。",
                    },
                    subject=subject,
                    context={"gap_from": after, "gap_to": before},
                )
            )
            return None, issues

    if len({entry.program_key for entry in entries}) < 2:
        issues.append(
            _issue(
                "tod_schedule_single_entry",
                "Only one plan on this controller states a readable 시간대, so there is "
                "nothing to switch between; the single confirmed plan is kept.",
                messages={
                    "ko": "읽을 수 있는 시간대를 가진 계획이 하나뿐이라 전환할 대상이 "
                    "없습니다. 확인된 단일 계획 그대로 둡니다.",
                    "en": "Only one plan states a readable time-of-day, so there is nothing to "
                    "switch between; the single confirmed plan is kept.",
                    "ja": "読み取れる時間帯を持つ計画が 1 つのみで切替対象がないため、"
                    "確認済みの単一計画のままとします。",
                },
                subject=subject,
                context={"program_keys": sorted({entry.program_key for entry in entries})},
            )
        )
        return None, issues

    return (
        TodSchedule(
            entries=tuple(entries),
            day_plan_no=(day_plans.pop() if day_plans else None),
            family=family,
        ),
        issues,
    )


def _spans(candidates: Sequence[TodCandidate]) -> list[tuple[int, int]]:
    """Every stated window as ``(start, end)`` seconds inside ONE day.

    A window whose end is at or before its start is overnight; it is cut at
    midnight into two ordinary spans rather than left to run past 86400. Cutting
    rather than extending is what makes the coverage arithmetic in
    :func:`_tiling_defect` correct on the circle: a day is 86400 seconds however
    many windows wrap around it, and a span that ran to 108000 would otherwise
    count 21600 seconds of the NEXT day as covered while double-booking the same
    hours of this one — the exact confusion that made a wrap-induced overlap
    report itself as a gap.
    """
    spans: list[tuple[int, int]] = []
    for candidate in candidates:
        for window in candidate.windows:
            if window.end_s is None:
                # Unreachable while the caller checks the family first, and NOT
                # an assert: under ``python -O`` an assert vanishes and this
                # would become a TypeError on the comparison below.
                continue
            start_s, end_s = window.start_s, window.end_s
            if end_s <= start_s:
                spans.append((start_s, SECONDS_PER_DAY))
                if end_s > 0:
                    spans.append((0, end_s))
            else:
                spans.append((start_s, end_s))
    spans.sort()
    return spans


def _tiling_defect(candidates: Sequence[TodCandidate]) -> tuple[str, str, str] | None:
    """``(kind, from, to)`` for the first place the day is not tiled, or ``None``.

    ``kind`` is ``"overlap"`` or ``"gap"``, and the two are told apart by
    COVERAGE rather than by walking neighbours: the summed length of every span
    against the length of their union. Sum greater than union means some second
    is claimed twice; union short of a day means some second is claimed by
    nobody. Both can be true at once (two half-days that overlap in the middle
    and leave the evening empty sum to exactly 86400), which is why the union is
    computed rather than the total compared with 86400 alone — and why the
    overlap is reported first, being the one a reader would otherwise never see.
    """
    spans = _spans(candidates)
    if not spans:
        return None

    total = sum(end_s - start_s for start_s, end_s in spans)

    merged: list[list[int]] = []
    for start_s, end_s in spans:
        if merged and start_s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end_s)
        else:
            merged.append([start_s, end_s])
    union = sum(end_s - start_s for start_s, end_s in merged)

    if total > union:
        for (_, end_s), (next_start_s, next_end_s) in zip(spans, spans[1:]):
            if next_start_s < end_s:
                return (
                    "overlap",
                    seconds_to_clock(next_start_s),
                    seconds_to_clock(min(end_s, next_end_s)),
                )
    if union < SECONDS_PER_DAY:
        if merged[0][0] > 0:
            return "gap", seconds_to_clock(merged[-1][1]), seconds_to_clock(merged[0][0])
        for left, right in zip(merged, merged[1:]):
            if left[1] != right[0]:
                return "gap", seconds_to_clock(left[1]), seconds_to_clock(right[0])
        return "gap", seconds_to_clock(merged[-1][1]), seconds_to_clock(merged[0][0])
    return None


def seconds_to_clock(seconds: int) -> str:
    """``75600`` → ``"21:00"``. Past-midnight seconds wrap, so the overnight
    normalisation in :func:`_first_gap` still names a real clock time."""
    minutes = (seconds % SECONDS_PER_DAY) // 60
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


__all__ = [
    "FAMILY_SWITCH_POINT",
    "FAMILY_WINDOW",
    "SECONDS_PER_DAY",
    "TOD_ISSUE_CODES",
    "TodCandidate",
    "TodSchedule",
    "TodScheduleEntry",
    "TodWindow",
    "build_tod_schedule",
    "parse_tod_phrase",
    "seconds_to_clock",
]
