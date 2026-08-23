"""A plan a REVIEWER declared, converted with the same arithmetic as the sheet.

Unit #52, second pass. ``signals_daejeon.to_fixed_time_plan`` turns one 대전
sheet's own TOD table into the :class:`FixedTimePlan`
``signals.generate_node_signals`` takes. This module does the same job for the
other source this product now has: a person typing the timings into the 현시도
input template's 신호시간 sheet.

**Why it is a third source and not a fourth lie.** ``side_inputs`` discriminates
on ``plan.source``, and the ``daejeon_sheet`` branch *parses ``sheet_text``* — so
labelling a typed plan ``daejeon_sheet`` would mean fabricating a sheet for it to
parse. It gets its own ``excel_upload`` source instead, its own
``ProgramSourceKind.DECLARED`` program (the same kind the sheet path produces —
both are declared plans, neither is observed history) and provenance that says a
human typed the numbers.

**The arithmetic is the sheet's, deliberately unchanged**::

    intergreen_i = 소거_i (황색+전적색), as typed
    amber        = the CONFIRMED declaration (MappingAttestation.declared_amber_s)
    all_red_i    = intergreen_i - amber       (>= 0; exactly 0 is an ordinary design)
    split_i      = green_i + intergreen_i
    start_i      = Σ split_1..i-1              (phase 1 at cycle time 0)
    cycle        = 주기 when typed, else Σ split_i

Two consequences are load-bearing and both mirror ``signals_daejeon``:

* **Amber is per intersection, clearance is per phase.** Measured, not assumed:
  ``to_fixed_time_plan`` writes ONE ``settings.declared_amber_s`` into every
  ``PhaseData.amber_s`` while ``intergreen_s`` comes per phase off the sheet's
  YELLOW(RED) column. The template mirrors exactly that granularity — one 황색
  per intersection in 현시입력, one 소거 per phase in 신호시간 — so a value that
  is one number in the client's world is one cell in ours.
* **No amber, no plan.** ``declared_amber_unstated`` is this module's
  ``daejeon_intergreen_split_unstated``: the amber is a reviewer's declaration
  and is never defaulted to 3.0 s or anything else, here or anywhere.

No COM, no Vissim, no network, no I/O.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..dataimport.base import ImportIssue, IssueSeverity
from ..dataimport.fixed_time import FixedTimePlan, PhaseDerivation
from ..dataimport.schema import PhaseData, ProgramSourceKind, Provenance, SignalProgramData

#: How far the typed 주기 may sit from Σ(green + 소거) before the two are treated
#: as contradicting each other. A spreadsheet's own numbers add up exactly; this
#: absorbs binary-float noise (0.1 + 0.2) and nothing else.
CYCLE_TOLERANCE_S = 0.05

#: Everything this module can say at WARN tier — findings that do NOT stop a
#: plan being built but that a reviewer must be told about.
WARN_CODES: frozenset[str] = frozenset({"declared_coordination_offset_not_applied"})

REFUSE_CODES: frozenset[str] = frozenset(
    {
        "declared_no_phases",
        "declared_green_not_positive",
        "declared_intergreen_negative",
        "declared_amber_unstated",
        "declared_all_red_negative",
        "declared_cycle_contradicts_splits",
    }
)


@dataclass(frozen=True)
class DeclaredPhaseTiming:
    """One phase's typed timing. ``intergreen_s`` is 황색+전적색 combined —
    the same quantity the 대전 sheet prints as YELLOW(RED), so an exported sheet
    round-trips its own numbers rather than a re-derivation of them.

    ``min_green_s``/``max_green_s`` are the actuated band the SIGNAL TABLE
    prints. ``None`` means the source stated none, which is a different fact
    from zero and is carried as such."""

    phase_id: str
    green_s: float
    intergreen_s: float
    min_green_s: float | None = None
    max_green_s: float | None = None


@dataclass(frozen=True)
class DeclaredPlan:
    """One (수준, 계획 ID) a human typed, and where the numbers came from."""

    source_file: str
    intersection_name: str
    level_no: int
    plan_id: int
    phases: tuple[DeclaredPhaseTiming, ...]
    intersection_no: str | None = None
    cycle_s: float | None = None
    #: 연동. Recorded on the program and NEVER applied to the phase starts —
    #: see the warning this module emits, and ``signals_daejeon``'s identical
    #: refusal to apply it.
    offset_s: float | None = None
    time_of_day: str | None = None
    declared_amber_s: float | None = None
    amber_declaration_note: str | None = None

    @property
    def program_id(self) -> str:
        return f"L{self.level_no}-P{self.plan_id}"

    @property
    def controller_key(self) -> str:
        return str(self.intersection_no or self.intersection_name)


def _issue(
    severity: IssueSeverity,
    code: str,
    message: str,
    *,
    messages: dict[str, str],
    subject: str,
    context: dict | None = None,
) -> ImportIssue:
    return ImportIssue(
        severity=severity,
        code=code,
        message=message,
        messages=messages,
        subject=subject,
        context=context or {},
    )


def to_fixed_time_plan(declared: DeclaredPlan) -> tuple[FixedTimePlan | None, list[ImportIssue]]:
    """A typed plan → the ``FixedTimePlan`` the composer takes, or ``(None, issues)``.

    Same "no partial product" contract as every other converter in this chain: a
    REFUSE produces no plan at all rather than a plan with a warning stapled on.
    """
    issues: list[ImportIssue] = []
    subject = (
        f"{declared.source_file} / 교차로 {declared.intersection_name} / {declared.program_id}"
    )

    if not declared.phases:
        issues.append(
            _issue(
                IssueSeverity.REFUSE,
                "declared_no_phases",
                f"{declared.program_id} carries no phase timing at all.",
                messages={
                    "ko": f"{declared.program_id}에 현시 시간이 하나도 없습니다.",
                    "en": f"{declared.program_id} carries no phase timing at all.",
                    "ja": f"{declared.program_id} に現示時間がありません。",
                },
                subject=subject,
            )
        )
        return None, issues

    for timing in declared.phases:
        if timing.green_s <= 0:
            issues.append(
                _issue(
                    IssueSeverity.REFUSE,
                    "declared_green_not_positive",
                    f"{timing.phase_id}: green is {timing.green_s:g} s. A phase that never runs "
                    "green is not a phase this plan can carry.",
                    messages={
                        "ko": f"{timing.phase_id}: 녹색이 {timing.green_s:g}초입니다. 녹색이 "
                        "0 이하인 현시는 계획에 넣을 수 없습니다.",
                        "en": f"{timing.phase_id}: green is {timing.green_s:g} s.",
                        "ja": f"{timing.phase_id}: 緑が {timing.green_s:g}秒 です。",
                    },
                    subject=subject,
                    context={"phase": timing.phase_id, "green_s": timing.green_s},
                )
            )
            return None, issues
        if timing.intergreen_s < 0:
            issues.append(
                _issue(
                    IssueSeverity.REFUSE,
                    "declared_intergreen_negative",
                    f"{timing.phase_id}: clearance is {timing.intergreen_s:g} s.",
                    messages={
                        "ko": f"{timing.phase_id}: 소거시간이 {timing.intergreen_s:g}초입니다.",
                        "en": f"{timing.phase_id}: clearance is {timing.intergreen_s:g} s.",
                        "ja": f"{timing.phase_id}: クリアランスが {timing.intergreen_s:g}秒 です。",
                    },
                    subject=subject,
                    context={"phase": timing.phase_id, "intergreen_s": timing.intergreen_s},
                )
            )
            return None, issues

    splits = [
        round(timing.green_s + timing.intergreen_s, 6) for timing in declared.phases
    ]
    derived_cycle = round(sum(splits), 6)
    if declared.cycle_s is not None and abs(declared.cycle_s - derived_cycle) > CYCLE_TOLERANCE_S:
        issues.append(
            _issue(
                IssueSeverity.REFUSE,
                "declared_cycle_contradicts_splits",
                f"{declared.program_id}: the stated cycle is {declared.cycle_s:g} s but the "
                f"phases add up to {derived_cycle:g} s. Neither number is corrected on the "
                "reviewer's behalf — one of the two cells is wrong.",
                messages={
                    "ko": f"{declared.program_id}: 적으신 주기는 {declared.cycle_s:g}초인데 "
                    f"현시(녹색+소거)의 합은 {derived_cycle:g}초입니다. 어느 쪽이 맞는지 이 앱은 "
                    "고르지 않습니다.",
                    "en": f"{declared.program_id}: stated cycle {declared.cycle_s:g} s, phases "
                    f"sum to {derived_cycle:g} s. Neither is corrected for you.",
                    "ja": f"{declared.program_id}: 記入された周期は {declared.cycle_s:g}秒 ですが、"
                    f"現示の合計は {derived_cycle:g}秒 です。",
                },
                subject=subject,
                context={
                    "stated_cycle_s": declared.cycle_s,
                    "sum_of_splits_s": derived_cycle,
                },
            )
        )
        return None, issues

    if declared.declared_amber_s is None:
        # This module's daejeon_intergreen_split_unstated. The 소거 column is
        # 황색+전적색 combined -- exactly what the sheet prints -- so the amber
        # inside it is still a declaration, and it is still never defaulted.
        issues.append(
            _issue(
                IssueSeverity.REFUSE,
                "declared_amber_unstated",
                f"{declared.source_file}: the 소거 column is 황색+전적색 combined, so the amber "
                "inside it is not stated anywhere. Vissim needs the amber as its own display and "
                "§5.4.2.1 requires controller values to be entered unchanged, so it is not "
                "assumed here. Confirm the intersection with 황색시간 declared.",
                messages={
                    "ko": "소거시간은 황색+전적색 합산값이므로 황색만 따로 적힌 곳이 없습니다. "
                    "확인 화면에서 황색시간을 선언한 뒤 다시 확인하십시오 — 이 앱은 기본값을 "
                    "넣지 않습니다.",
                    "en": "The clearance column is 황색+전적색 combined, so the amber alone is "
                    "stated nowhere. Declare 황색시간 on the confirm form — no default is used.",
                    "ja": "クリアランスは黄色+全赤の合算値であり、黄色のみの記載はありません。"
                    "確認画面で黄色時間を宣言してください — 既定値は使用しません。",
                },
                subject=subject,
                context={"program_id": declared.program_id},
            )
        )
        return None, issues

    amber = float(declared.declared_amber_s)
    derived_by = (
        "현시도 입력 템플릿 신호시간 시트: green + 소거(황색+전적색) typed by a reviewer; "
        "amber = the confirmation's declaration"
        + (
            f" ({declared.amber_declaration_note})"
            if declared.amber_declaration_note
            else ""
        )
    )

    phase_records: list[PhaseData] = []
    rows: list[PhaseDerivation] = []
    start = 0.0
    for index, (timing, split_s) in enumerate(zip(declared.phases, splits), start=1):
        all_red = round(timing.intergreen_s - amber, 6)
        if all_red < 0:
            # Identical shape and identical reasoning to
            # signals_daejeon.daejeon_all_red_negative: zero all-red is an
            # ordinary design and is accepted; a negative one means the
            # declaration contradicts what was typed for this phase.
            issues.append(
                _issue(
                    IssueSeverity.REFUSE,
                    "declared_all_red_negative",
                    f"{timing.phase_id}: the declared amber ({amber:g} s) is longer than the "
                    f"typed clearance ({timing.intergreen_s:g} s), which would need a negative "
                    f"all-red of {all_red:g} s.",
                    messages={
                        "ko": f"{timing.phase_id}: 선언한 황색({amber:g}초)이 적으신 "
                        f"소거시간({timing.intergreen_s:g}초)보다 길어 전적색이 {all_red:g}초"
                        "(음수)가 됩니다. 전적색 0초는 정상이지만 음수는 불가능합니다.",
                        "en": f"{timing.phase_id}: declared amber {amber:g} s exceeds the typed "
                        f"clearance {timing.intergreen_s:g} s (all-red would be {all_red:g} s).",
                        "ja": f"{timing.phase_id}: 宣言した黄色 {amber:g}秒 が記入された "
                        f"クリアランス {timing.intergreen_s:g}秒 より長く、全赤が負になります。",
                    },
                    subject=subject,
                    context={
                        "phase": timing.phase_id,
                        "amber_s": amber,
                        "intergreen_s": timing.intergreen_s,
                        "all_red_s": all_red,
                    },
                )
            )
            return None, issues

        provenance = Provenance(
            source_file=declared.source_file,
            locator=(
                f"신호시간 시트 / 수준 {declared.level_no} / 계획 {declared.plan_id} / "
                f"{timing.phase_id}"
            ),
            native_id=timing.phase_id,
            derived_by=derived_by,
        )
        phase_records.append(
            PhaseData(
                provenance=provenance,
                phase_id=timing.phase_id,
                sequence_index=index - 1,
                green_s=timing.green_s,
                amber_s=amber,
                all_red_s=all_red,
                intergreen_s=timing.intergreen_s,
                min_green_s=timing.min_green_s,
                max_green_s=timing.max_green_s,
            )
        )
        rows.append(
            PhaseDerivation(
                phase_id=timing.phase_id,
                occurrences=None,
                # A DECLARED plan has no observation -- the same placeholders
                # signals_daejeon writes, and source_kind=DECLARED on the
                # program is what says so.
                observed_total_s=0.0,
                scats_average_s=None,
                frequency=None,
                average_phase_s=split_s,
                rounded_phase_s=split_s,
                start_offset_s=start,
                intergreen_s=timing.intergreen_s,
                green_s=timing.green_s,
                is_stretch_phase=False,
            )
        )
        start = round(start + split_s, 6)

    if declared.offset_s:
        # The same finding ``signals_daejeon`` reports as
        # daejeon_coordination_offset_not_applied, and for the same reason: the
        # offset is RECORDED on the program but the phase starts are not shifted,
        # because nothing states the instant it counts from. Each source names
        # its own finding, so the codes differ; the consequence is identical and
        # a reviewer is told either way.
        issues.append(
            _issue(
                IssueSeverity.WARN,
                "declared_coordination_offset_not_applied",
                f"{declared.program_id} states a 연동 of {declared.offset_s:g} s, and it is NOT "
                "applied: nothing states the instant it counts from. Φ1's green is placed at "
                "cycle time 0. Harmless stand-alone, a progression error in a coordinated "
                "corridor.",
                messages={
                    "ko": f"{declared.program_id}의 연동값 {declared.offset_s:g}초는 기록하지만 "
                    "현시 시작 시각에는 적용하지 않았습니다 — 기준 시점이 어디에도 없습니다. "
                    "단독 교차로에는 영향이 없으나 연동 축에서는 진행 오차가 됩니다.",
                    "en": f"{declared.program_id}'s 연동 of {declared.offset_s:g} s is recorded "
                    "but NOT applied — its reference instant is stated nowhere.",
                    "ja": f"{declared.program_id} のオフセット {declared.offset_s:g}秒 は記録 "
                    "しますが適用していません — 基準時点が記載されていません。",
                },
                subject=subject,
                context={"program_id": declared.program_id, "offset_s": declared.offset_s},
            )
        )

    cycle = float(declared.cycle_s) if declared.cycle_s is not None else derived_cycle
    program = SignalProgramData(
        provenance=Provenance(
            source_file=declared.source_file,
            locator=f"신호시간 시트 / 수준 {declared.level_no} / 계획 {declared.plan_id}",
            native_id=declared.program_id,
            derived_by=derived_by
            + (
                "; cycle = Σ(green + 소거)"
                if declared.cycle_s is None
                else "; cycle as typed"
            ),
        ),
        program_id=declared.program_id,
        # The 대전 path's own label for the same object ("수준 1 / ID 1"), so a
        # program that came back through the workbook is not renamed on the way.
        name=f"수준 {declared.level_no} / ID {declared.plan_id}",
        source_kind=ProgramSourceKind.DECLARED,
        cycle_time_s=cycle,
        offset_s=declared.offset_s,
        phases=phase_records,
        time_of_day=declared.time_of_day,
    )
    plan = FixedTimePlan(
        controller_id=declared.controller_key,
        source_program_id=declared.program_id,
        program=program,
        complete_cycles=None,
        stretch_phase_id=None,
        average_cycle_s=None,
        plan_cycle_s=cycle,
        round_up_residual_s=None,
        rows=tuple(rows),
        issues=tuple(i for i in issues if i.severity is not IssueSeverity.INFO),
    )
    return plan, issues


__all__ = [
    "CYCLE_TOLERANCE_S",
    "REFUSE_CODES",
    "WARN_CODES",
    "DeclaredPhaseTiming",
    "DeclaredPlan",
    "to_fixed_time_plan",
]
