"""The 13-step calibration flow rail — a STATUS READER, never a calibrator.

Unit #50. MRWA op §2.12.1 names thirteen calibration steps in a fixed order
(:data:`services.agent.mrwa_reference.CALIBRATION_STEPS_2_12_1`, already
transcribed and cited there). This module answers ONE question per step:

    what does the evidence already in this workspace say about this step?

and it is allowed exactly six answers — :data:`DONE`,
:data:`CONFIRMED_BY_PERSON`, :data:`IN_PROGRESS`, :data:`NEXT`,
:data:`NOT_APPLICABLE`, :data:`NO_EVIDENCE`.

WHY THIS IS NOT CALIBRATION LOGIC
---------------------------------
Nothing here grades a network, proposes an edit, or decides a parameter. Every
verdict this module reads was produced elsewhere and is QUOTED, not re-derived:

* ``services.genesis.gate`` -> the V1-V9 verdict (step 1)
* ``services.agent.health_check`` -> the model-inspection findings (step 1)
* ``services.genesis.phase_diagram`` -> 현시도 attestations (step 6)
* ``services.agent.saturation_flow.verify_applied_row`` -> Table 5-6 row
  membership (steps 7 and 12)
* ``services.agent.reality_check`` / ``services.agent.geh_ladder`` /
  ``services.agent.fixes.entrance_measurement`` -> the run's GEH outcome, the
  diagnosis rung, and the D4 entrance verdicts (steps 10 and 11)
* ``services.calibration_attestation`` -> a NAMED HUMAN's signature on one of
  the nine steps no artifact speaks to (unit #53)

THE SIXTH WORD, AND THE RANK IT SITS AT (#53)
----------------------------------------------
A signature is evidence of a different KIND, so it gets a word of its own
(:data:`CONFIRMED_BY_PERSON`) and one precedence rule, applied to all nine
signable steps by :func:`_apply_attestation`:

* it upgrades :data:`NO_EVIDENCE` and NOTHING ELSE. Every machine-evidence
  state outranks testimony — a ``NEXT`` (a proposal is waiting) or an
  ``IN_PROGRESS`` (an entrance is unverified) is a fact about this network that
  a person's recollection cannot overrule, so the machine verdict stands and
  the basis SAYS an attestation exists and was outranked;
* :data:`DONE` likewise stands: a coexisting signature is provenance on the
  basis line, never a change of status;
* :data:`NOT_APPLICABLE` stands too (step ⑨ on a network with no PT lines) —
  the network proved there is nothing to do, which a signature cannot undo;
* a signature made against a DIFFERENT ``.inpx`` than the one on disk is STALE:
  it never upgrades anything, and the step falls back to its underlying derived
  status with the basis naming the stale signature — the same 재확인 필요
  treatment 현시도 confirmations already get.

:func:`derive_rail` is PURE: it takes a dict of already-gathered artifacts and
returns a dict. It opens no file, makes no network call, and touches no COM.
The gathering lives in ``routers/calibration.py``; the rules live here so a
test can hand this function a synthetic evidence bundle and pin every branch.

THE ONE RULE THAT MATTERS
-------------------------
**A step is :data:`DONE` only when a named artifact carries a positive
verdict, and the sentence that says so names the artifact.** There is no
branch anywhere below that reaches ``DONE`` from an absence, and — since #53 —
none that reaches it from a SIGNATURE either. Six of the thirteen steps have no
evidence source in this product at all; they are reported as
:data:`NO_EVIDENCE`, or as :data:`CONFIRMED_BY_PERSON` when an accountable
human has signed for one, and never as done — a rail that shows thirteen green
ticks on an uncalibrated network would be the exact failure this codebase
exists to prevent (CLAUDE.md: "a refused edit with a clear reason beats a
completed edit with wrong numbers").

THE DEFAULT-COINCIDENCE TRAP (steps 7 and 12)
----------------------------------------------
Vissim's stock Wiedemann-74 behaviour carries ``bx_add=2.00, bx_mult=3.00`` —
which is EXACTLY MRWA Table 5-6's 2050 pcu row
(:data:`services.agent.mrwa_reference.TABLE_5_6_ROWS`). So
``verify_applied_row`` returns ``MATCHES_PUBLISHED_ROW`` on a network nobody
has ever calibrated, and a naive "matched a row ⇒ step 7 done" rule would tick
step 7 green on every fresh upload in the product.

What separates the two is ``ax``: §5.2.5.1 states an average standstill
distance of 2.5 m and Vissim's default is 2.0 m, so
``standstill_matches_guideline`` is the signal that the guideline's set was
actually applied rather than coincidentally met. Both steps therefore require
it, and a row match WITHOUT it is reported as "indistinguishable from the
Vissim default" — a named absence, not a tick.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from services.agent import geh_ladder, mrwa_reference, parameter_search, saturation_flow
from services.genesis.gate import GateVerdict

#: The gate's OWN word for "every refuse-tier check genuinely passed", taken
#: from :class:`services.genesis.gate.GateVerdict` rather than typed here.
#:
#: REFUTER 2026-08-19: it WAS typed here, as ``"passed"`` — a string
#: ``GateVerdict`` cannot produce (its three values are ``pass`` / ``refused``
#: / ``incomplete``, and ``genesis_jobs.gate_payload`` puts ``verdict.value``
#: on the wire). So step 1's whole gate branch could never reach :data:`DONE`,
#: and a generation that passed V1–V9 rendered as ``IN_PROGRESS`` under the
#: sentence "생성 게이트 판정: pass" — the rail contradicting itself in one
#: line. The unit's own tests hard-coded ``"passed"`` too, so they agreed with
#: the bug instead of with the producer. Same rule as
#: :data:`_DEFAULT_COINCIDENT_ROW_PCU` below: read the value off the module
#: that defines it, so a re-spelling breaks loudly instead of silently
#: disabling a branch.
_GATE_PASS = GateVerdict.PASS.value

# ---------------------------------------------------------------------------
# The status vocabulary — five words, and no sixth
# ---------------------------------------------------------------------------

#: A named artifact carries a positive verdict for this step.
DONE = "DONE"
#: Work on this step is evidenced and unfinished (or partially applied).
IN_PROGRESS = "IN_PROGRESS"
#: The evidence says this step is what remains to be done.
NEXT = "NEXT"
#: The network provably has nothing for this step to act on.
NOT_APPLICABLE = "NOT_APPLICABLE"
#: Nothing in this workspace speaks to this step. A MANUAL-CHECK area, and the
#: honest answer for the seven steps this product has no artifact for.
NO_EVIDENCE = "NO_EVIDENCE"
#: A NAMED HUMAN signed for this step (unit #53). The sixth word, and the only
#: one no machine produced.
#:
#: WHY IT IS NOT ``DONE``, AND MUST NEVER BECOME IT. ``DONE`` means a named
#: ARTIFACT carries a positive verdict — the generation gate, the 현시도
#: attestations, ``verify_applied_row``, a D4 entrance measurement. This word
#: means a person said so, and the two are not the same claim: an artifact can
#: be re-read and disagreed with, a signature can only be believed or revoked.
#: Nine of the thirteen steps have no artifact at all
#: (:data:`ATTESTABLE_STEPS`), and before #53 they said :data:`NO_EVIDENCE`
#: forever — true, and unable to distinguish "nobody did this" from "somebody
#: did it and this tool cannot see it". This word draws that distinction
#: WITHOUT letting it be mistaken for a measurement: the basis sentence names
#: the attester and the date, the chip says 확인됨(사람 증언), and it falls back
#: the moment the ``.inpx`` it was signed against changes
#: (``services.calibration_attestation``).
CONFIRMED_BY_PERSON = "CONFIRMED_BY_PERSON"

STATUSES = (DONE, CONFIRMED_BY_PERSON, IN_PROGRESS, NEXT, NOT_APPLICABLE, NO_EVIDENCE)

#: Statuses that assert something POSITIVE about the step. Every one of them
#: must be reachable only from a named artifact OR a named person — pinned by
#: the tests, which walk an empty bundle and assert not one step reaches any of
#: these.
POSITIVE_STATUSES = (DONE, CONFIRMED_BY_PERSON)

#: The steps a human may SIGN for (#53), and the four that are excluded.
#:
#: A step is signable exactly when this product has no evidence path that can
#: prove or refuse it: the six permanent 수동 확인 영역 steps, plus ⑨ (whose
#: only machine fact is the provable ABSENCE of PT lines) and ⑩⑪ (whose
#: machine evidence exists only once a run has been scored, and speaks about
#: that run rather than about the step).
#:
#: ①⑥⑦⑫ are excluded BY NAME rather than by omission: each has an artifact
#: that answers — the generation gate / model inspection, the 현시도
#: attestations, and ``verify_applied_row`` twice. A signature there would sit
#: beside a measurement, and the first time the two disagreed this screen would
#: have to pick one. ``calibration_attestation`` reads this tuple and refuses
#: the other four with a sentence that names them, so the rule has one home.
ATTESTABLE_STEPS = (2, 3, 4, 5, 8, 9, 10, 11, 13)

#: The six §2.12.1 steps this product carries no evidence source for. Listed
#: as data (not buried in a branch) so a future unit that BUILDS one of these
#: evidence sources has one place to remove its number from, and so a test can
#: assert every one of them stays NO_EVIDENCE whatever else is in the bundle.
#:
#: Step 9 (public transport) moved OUT of this list as an approved follow-up
#: to unit #50 (2026-08-19 user decision): ``shadow_model["pt_lines"]`` now
#: backs a narrow :data:`NOT_APPLICABLE` the same way ``signal_controller_count
#: == 0`` backs step 6's — see :func:`_step_9`.
STEPS_WITHOUT_EVIDENCE_SOURCE = (2, 3, 4, 5, 8, 13)

#: Where each step's work lives on the calibration workspace screen. The rail
#: is a navigation control as well as a status board (#50: "각 단계 클릭 →
#: 해당 작업 영역으로 스크롤/이동"), and an anchor that names no section is a
#: dead click, so every step carries one.
_ANCHORS: dict[int, str] = {
    1: "station-network",
    6: "station-signals",
    7: "station-behaviour",
    10: "station-scoring",
    11: "station-scoring",
    12: "station-behaviour",
}
_MANUAL_ANCHOR = "station-manual"

#: Korean/Japanese renderings of §2.12.1's step labels. The ENGLISH label is
#: the guideline's own words and is passed through verbatim from
#: ``mrwa_reference``; these two are TRANSLATIONS this product wrote for its
#: own UI and are marked as such in the payload (``label_source``), so nobody
#: can mistake them for quoted authority text.
_LABELS_KO: dict[int, str] = {
    1: "기본 네트워크 코딩",
    2: "차량 속도",
    3: "비신호 교차로",
    4: "차단(blocking)",
    5: "회전 차로",
    6: "신호 교차로",
    7: "정지선 포화교통류",
    8: "차로 이용률",
    9: "대중교통",
    10: "수요 보정",
    11: "경로 선택(배분)",
    12: "행태 파라미터",
    13: "소프트웨어 고유 항목",
}
_LABELS_JA: dict[int, str] = {
    1: "基本ネットワークコーディング",
    2: "車両速度",
    3: "無信号交差点",
    4: "ブロッキング",
    5: "右左折車線",
    6: "信号交差点",
    7: "停止線飽和交通流",
    8: "車線利用率",
    9: "公共交通",
    10: "需要キャリブレーション",
    11: "経路選択(配分)",
    12: "挙動パラメータ",
    13: "ソフトウェア固有項目",
}


def _text(ko: str, en: str, ja: str) -> dict[str, str]:
    return {"ko": ko, "en": en, "ja": ja}


def _seq(value: Any) -> list[Any]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return list(value)
    return []


def _map(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


# ---------------------------------------------------------------------------
# The human signature (#53) — read once, applied by ONE rule
# ---------------------------------------------------------------------------


def _attestation(evidence: Mapping[str, Any], number: int) -> Mapping[str, Any]:
    """The signature that STANDS for step ``number``, or ``{}``.

    ``evidence["step_attestations"]`` is
    ``services.calibration_attestation.index_payload()`` — already one record
    per step, already newest-wins, already free of revoked records. This
    function re-checks ``revoked`` anyway: two guards, because deleting either
    one must not silently restore a withdrawn claim, and a mutation that drops
    the filter on one side dies against the other.

    A step outside :data:`ATTESTABLE_STEPS` reads ``{}`` no matter what the
    bundle carries, so a registry hand-edited to hold a signature on ① cannot
    move ①.
    """
    if number not in ATTESTABLE_STEPS:
        return {}
    index = _map(evidence.get("step_attestations"))
    entry = index.get(number)
    if entry is None:
        # A registry round-tripped through JSON keys its steps by string.
        entry = index.get(str(number))
    record = _map(entry)
    if record.get("revoked") is True:
        return {}
    return record


def _attester(record: Mapping[str, Any]) -> tuple[str, str] | None:
    """``(who, when)``, or ``None`` when the record cannot name both.

    An attestation this module cannot attribute is not treated as one: it
    upgrades nothing and it is not mentioned on a basis line, because a sentence
    saying "somebody confirmed this" is exactly the unaccountable tick the whole
    record type exists to replace.
    """
    who = str(record.get("attested_by") or "").strip()
    when = str(record.get("attested_at") or "").strip()
    if not who or not when:
        return None
    return who, when


def _with_note(verdict: dict[str, Any], record: Mapping[str, Any], note: dict[str, str]) -> dict[str, Any]:
    """The same verdict, with one sentence appended to its basis in all three
    languages, and the signature named in ``detail``."""
    basis = _map(verdict.get("basis"))
    detail = dict(_map(verdict.get("detail")))
    detail["attestation"] = _attestation_detail(record)
    return {
        **verdict,
        "basis": {
            lang: f"{str(basis.get(lang) or '').rstrip()} {note[lang]}".strip()
            for lang in ("ko", "en", "ja")
        },
        "detail": detail,
    }


def _attestation_detail(record: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": record.get("id"),
        "attested_by": record.get("attested_by"),
        "attested_at": record.get("attested_at"),
        "stale": bool(record.get("stale")),
    }


def _apply_attestation(
    verdict: dict[str, Any], record: Mapping[str, Any]
) -> dict[str, Any]:
    """THE precedence rule, in one place, for all nine signable steps.

    See the module docstring's "the sixth word" section for the ranking and why
    it is that way round. Every branch here either leaves the machine verdict's
    STATUS exactly as it was, or lifts a :data:`NO_EVIDENCE` to
    :data:`CONFIRMED_BY_PERSON` — there is no path from a signature to
    :data:`DONE`.
    """
    if not record:
        return verdict
    named = _attester(record)
    if named is None:
        return verdict
    who, when = named

    if record.get("stale") is not False:
        # Stale, or a freshness this server could not establish (an unreadable
        # ``.inpx`` reads as stale — unknown is not fresh). Never an upgrade.
        return _with_note(
            verdict,
            record,
            {
                "ko": f"증언이 있으나 네트워크가 바뀜(재확인 필요) — {who}, {when}.",
                "en": (
                    f"An attestation exists but the network has changed since it was signed "
                    f"(re-confirmation needed) — {who}, {when}."
                ),
                "ja": f"証言はありますがネットワークが変わりました(再確認が必要) — {who}、{when}。",
            },
        )

    if verdict.get("status") == NO_EVIDENCE:
        return {
            "status": CONFIRMED_BY_PERSON,
            "source": "calibration_attestation",
            "basis": _text(
                f"{who} 님이 {when}에 이 단계를 확인했다고 서명했습니다 — 기계 증거가 아니라 "
                "사람의 증언입니다.",
                f"{who} signed for this step on {when} — a named person's testimony, not machine "
                "evidence.",
                f"{who} が {when} にこの段階を確認したと署名しました — 機械の証拠ではなく人の"
                "証言です。",
            ),
            "detail": {
                **dict(_map(verdict.get("detail"))),
                "attestation": _attestation_detail(record),
                # What the step would say WITHOUT the signature, kept so nobody
                # has to re-derive it to learn what the machine found (nothing).
                "underlying_status": verdict.get("status"),
                "underlying_source": verdict.get("source"),
            },
        }

    if verdict.get("status") == DONE:
        return _with_note(
            verdict,
            record,
            {
                "ko": f"사람 증언도 함께 기록되어 있습니다 — {who}, {when}.",
                "en": f"A human attestation is also on record — {who}, {when}.",
                "ja": f"人の証言も併せて記録されています — {who}、{when}。",
            },
        )

    return _with_note(
        verdict,
        record,
        {
            "ko": f"사람 증언이 있으나 기계 증거가 우선합니다 — {who}, {when}.",
            "en": f"A human attestation exists but machine evidence outranks it — {who}, {when}.",
            "ja": f"人の証言はありますが機械の証拠が優先します — {who}、{when}。",
        },
    )


# ---------------------------------------------------------------------------
# Step 1 — basic network coding
# ---------------------------------------------------------------------------


def _step_1(evidence: Mapping[str, Any]) -> dict[str, Any]:
    """Generation gate V1-V9 first, model inspection as the fallback.

    The gate is preferred because it is the stronger claim: it can REFUSE to
    publish, and its verdict is stored beside the artifact it judged. A model
    inspection is the answer for a consultant ``.inpx`` nobody generated here.
    """
    gate = _map(evidence.get("gate"))
    if gate:
        verdict = str(gate.get("verdict") or "")
        counts = _map(gate.get("counts"))
        passed = counts.get("pass")
        failed = counts.get("fail")
        not_checkable = counts.get("not_checkable")
        if verdict == _GATE_PASS:
            return {
                "status": DONE,
                "source": "genesis_gate",
                "basis": _text(
                    f"생성 게이트 V1–V9 통과 (통과 {passed}건, 실패 {failed}건).",
                    f"Generation gate V1–V9 passed ({passed} pass, {failed} fail).",
                    f"生成ゲートV1–V9が通過 (通過{passed}件、失敗{failed}件)。",
                ),
                "detail": {"verdict": verdict, "counts": dict(counts)},
            }
        return {
            "status": IN_PROGRESS,
            "source": "genesis_gate",
            "basis": _text(
                f"생성 게이트 판정: {verdict} (실패 {failed}건, 판정 불가 {not_checkable}건).",
                f"Generation gate verdict: {verdict} ({failed} fail, {not_checkable} not checkable).",
                f"生成ゲート判定: {verdict} (失敗{failed}件、判定不可{not_checkable}件)。",
            ),
            "detail": {"verdict": verdict, "counts": dict(counts)},
        }

    health = _map(evidence.get("health"))
    if health:
        # REFUTER 2026-08-19: this used to be ``_seq(health.get("findings"))``,
        # and ``_seq`` answers ``[]`` for anything that is not a sequence —
        # including a MISSING key. So a health payload whose findings list was
        # renamed, or truncated away, read as "zero findings" and ticked step 1
        # DONE. That is precisely the branch this module's docstring promises
        # does not exist ("no branch anywhere below reaches DONE from an
        # absence"). An unreadable inspection is now missing evidence, which is
        # what it always was.
        findings = health.get("findings")
        if not isinstance(findings, list):
            return _no_evidence(
                "health_check",
                ko="모델 검진 결과에서 지적 목록을 읽지 못했습니다 — 지적 0건이라는 뜻이 아닙니다.",
                en=(
                    "The model inspection carries no readable findings list — which is not the same "
                    "as zero findings."
                ),
                ja="モデル検診結果から指摘リストを読めませんでした — 指摘0件という意味ではありません。",
            )
        if not findings:
            return {
                "status": DONE,
                "source": "health_check",
                "basis": _text(
                    "모델 검진에서 지적 사항이 0건입니다.",
                    "The model inspection reports zero findings.",
                    "モデル検診の指摘は0件です。",
                ),
                "detail": {"findings": 0},
            }
        return {
            "status": IN_PROGRESS,
            "source": "health_check",
            "basis": _text(
                f"모델 검진 지적 {len(findings)}건이 남아 있습니다.",
                f"The model inspection reports {len(findings)} finding(s).",
                f"モデル検診の指摘が{len(findings)}件残っています。",
            ),
            "detail": {"findings": len(findings)},
        }

    return _no_evidence(
        "genesis_gate|health_check",
        ko="이 데이터셋에는 생성 게이트 판정도 모델 검진 결과도 없습니다.",
        en="This dataset carries neither a generation-gate verdict nor a model inspection.",
        ja="このデータセットには生成ゲート判定もモデル検診結果もありません。",
    )


# ---------------------------------------------------------------------------
# Step 6 — signalised intersections (현시도 attestation, C1-b)
# ---------------------------------------------------------------------------


def _step_6(evidence: Mapping[str, Any]) -> dict[str, Any]:
    reviews = _seq(evidence.get("phase_diagrams"))
    controllers = evidence.get("signal_controller_count")

    if not reviews and isinstance(controllers, int) and controllers == 0:
        return {
            "status": NOT_APPLICABLE,
            "source": "shadow_model.signals",
            "basis": _text(
                "네트워크에 신호제어기가 없습니다 — 이 단계는 해당하지 않습니다.",
                "The network carries no signal controllers — this step does not apply.",
                "ネットワークに信号制御機がありません — この段階は該当しません。",
            ),
            "detail": {"signal_controllers": 0},
        }

    if not reviews:
        return _no_evidence(
            "genesis.phase_diagrams",
            ko=(
                "확인된 현시도 검토가 없습니다"
                + (f" (신호제어기 {controllers}개)" if isinstance(controllers, int) else "")
                + "."
            ),
            en=(
                "No 현시도 (phase-diagram) review exists"
                + (f" ({controllers} signal controller(s) in the network)" if isinstance(controllers, int) else "")
                + "."
            ),
            ja=(
                "現示図レビューがありません"
                + (f" (信号制御機{controllers}基)" if isinstance(controllers, int) else "")
                + "。"
            ),
            detail={"signal_controllers": controllers, "reviews": 0},
        )

    confirmed = [r for r in reviews if _map(r).get("confirmed") is True]
    detail = {
        "reviews": len(reviews),
        "confirmed": len(confirmed),
        "signal_controllers": controllers,
    }
    if len(confirmed) == len(reviews):
        return {
            "status": DONE,
            "source": "genesis.phase_diagrams",
            "basis": _text(
                f"현시도 확인이 {len(confirmed)}건 모두 완료됐습니다 (C1-b 확인서).",
                f"All {len(confirmed)} 현시도 mapping(s) are attested (C1-b).",
                f"現示図確認が{len(confirmed)}件すべて完了しました (C1-b)。",
            ),
            "detail": detail,
        }
    if confirmed:
        return {
            "status": IN_PROGRESS,
            "source": "genesis.phase_diagrams",
            "basis": _text(
                f"현시도 {len(reviews)}건 중 {len(confirmed)}건만 확인됐습니다.",
                f"{len(confirmed)} of {len(reviews)} 현시도 review(s) are attested.",
                f"現示図{len(reviews)}件のうち{len(confirmed)}件のみ確認済みです。",
            ),
            "detail": detail,
        }
    return {
        "status": NEXT,
        "source": "genesis.phase_diagrams",
        "basis": _text(
            f"현시도 {len(reviews)}건이 확인을 기다리고 있습니다.",
            f"{len(reviews)} 현시도 review(s) are waiting for attestation.",
            f"現示図{len(reviews)}件が確認待ちです。",
        ),
        "detail": detail,
    }


# ---------------------------------------------------------------------------
# Step 9 — public transport (#50 follow-up, 2026-08-19 user decision)
# ---------------------------------------------------------------------------


def _step_9(evidence: Mapping[str, Any]) -> dict[str, Any]:
    """The network's own NOT_APPLICABLE, then a person's signature (#53).

    The order is the whole content of this function. ``pt_line_count == 0`` is a
    PROOF that there is nothing to calibrate, and a signature cannot overrule a
    proof — so :func:`_step_9_machine` answers first and
    :func:`_apply_attestation` only ever lifts its :data:`NO_EVIDENCE` (the
    PT-carrying network, which this product measures nothing about) to
    :data:`CONFIRMED_BY_PERSON`.
    """
    return _apply_attestation(_step_9_machine(evidence), _attestation(evidence, 9))


def _step_9_machine(evidence: Mapping[str, Any]) -> dict[str, Any]:
    """Narrowed from "always NO_EVIDENCE" to step 6's own NOT_APPLICABLE rule.

    Why only a PROVABLE zero earns :data:`NOT_APPLICABLE`, and nothing else:
    this product has no artifact anywhere that attests a PT line was reviewed
    or calibrated — no phase-diagram-style attestation store, no scoring
    pass, nothing. So a POSITIVE ``pt_line_count`` cannot move past
    :data:`NO_EVIDENCE`: a network that runs PT lines might be fully
    calibrated or might never have been touched, and this product cannot
    tell the two apart, so it must not guess DONE (or anything better than
    NO_EVIDENCE) for either. The one fact the network CAN prove about itself
    is the absence of the very thing being calibrated — zero PT lines means
    there is nothing for this step to act on — which is exactly the
    inference ``signal_controller_count == 0`` already licenses for step 6.
    An absent count (network unreadable) is treated the same as a positive
    one: unknown is not zero.
    """
    count = evidence.get("pt_line_count")
    if isinstance(count, int) and count == 0:
        return {
            "status": NOT_APPLICABLE,
            "source": "shadow_model.pt_lines",
            "basis": _text(
                "네트워크에 대중교통 노선이 없습니다 — 이 단계는 해당하지 않습니다.",
                "The network carries no public-transport lines — this step does not apply.",
                "ネットワークに公共交通路線がありません — この段階は該当しません。",
            ),
            "detail": {"pt_lines": 0},
        }
    return _no_evidence(
        "shadow_model.pt_lines",
        ko=(
            "대중교통 노선이 보정됐다는 증거가 없습니다"
            + (f" (대중교통 노선 {count}개)" if isinstance(count, int) else "")
            + "."
        ),
        en=(
            "No evidence that public-transport lines were calibrated"
            + (f" ({count} public-transport line(s) in the network)" if isinstance(count, int) else "")
            + "."
        ),
        ja=(
            "公共交通路線が較正された証拠はありません"
            + (f" (公共交通路線{count}件)" if isinstance(count, int) else "")
            + "。"
        ),
        detail={"pt_lines": count},
    )


# ---------------------------------------------------------------------------
# Steps 7 and 12 — the Table 5-6 pair, and the default-coincidence guard
# ---------------------------------------------------------------------------

#: The Table 5-6 row Vissim's own Wiedemann-74 default sits on. Read off
#: ``TABLE_5_6_ROWS`` rather than typed, so a re-transcription of the table
#: cannot silently move this guard.
_DEFAULT_COINCIDENT_ROW_PCU = next(
    (row[0] for row in mrwa_reference.TABLE_5_6_ROWS if row[1] == 2.00 and row[2] == 3.00),
    None,
)


def summarize_applied_rows(verdicts: Sequence[Any] | None) -> dict[str, Any]:
    """Count what ``verify_applied_row`` said across a network's behaviours.

    Each entry is ``{"behavior_no", "behavior_name", "verdict"}`` where
    ``verdict`` is a :func:`services.agent.saturation_flow.verify_applied_row`
    result, UNMODIFIED. This function only counts; it never re-decides a
    verdict, which is why steps 7 and 12 can disagree in emphasis without ever
    disagreeing about a fact.
    """
    readable: list[dict[str, Any]] = []
    on_row: list[dict[str, Any]] = []
    off_row: list[dict[str, Any]] = []
    guideline: list[dict[str, Any]] = []
    default_coincident: list[dict[str, Any]] = []

    for entry in _seq(verdicts):
        item = _map(entry)
        verdict = _map(item.get("verdict"))
        status = verdict.get("status")
        if status == saturation_flow.ROW_UNREADABLE:
            continue
        row = _map(verdict.get("row"))
        record = {
            "behavior_no": item.get("behavior_no"),
            "behavior_name": item.get("behavior_name"),
            "status": status,
            "row_pcu": row.get("saturation_flow_pcu"),
            "standstill_matches_guideline": verdict.get("standstill_matches_guideline"),
        }
        readable.append(record)
        if status == saturation_flow.ROW_MATCHED:
            on_row.append(record)
            if verdict.get("standstill_matches_guideline") is True:
                guideline.append(record)
            elif row.get("saturation_flow_pcu") == _DEFAULT_COINCIDENT_ROW_PCU:
                default_coincident.append(record)
        elif status == saturation_flow.ROW_UNMATCHED:
            off_row.append(record)

    return {
        "readable": readable,
        "on_published_row": on_row,
        "off_published_row": off_row,
        "guideline_standstill": guideline,
        "default_coincident": default_coincident,
        "unreadable": max(0, len(_seq(verdicts)) - len(readable)),
        "default_coincident_row_pcu": _DEFAULT_COINCIDENT_ROW_PCU,
    }


def _step_7(evidence: Mapping[str, Any]) -> dict[str, Any]:
    """Was a MEASURED saturation flow turned into a Table 5-6 set and applied?

    The row match alone cannot answer this (see the module docstring's
    default-coincidence section), so ``ax == 2.5`` — the guideline's own
    standstill distance, which Vissim's default is not — is what promotes a
    row match to evidence of an application.
    """
    verdicts = evidence.get("applied_rows")
    if verdicts is None:
        return _no_evidence(
            "saturation_flow.verify_applied_row",
            ko="주행행태를 읽지 못해 Table 5-6 적용 여부를 판단할 수 없습니다.",
            en="No driving behaviour could be read, so Table 5-6 application cannot be judged.",
            ja="走行挙動を読めず、Table 5-6の適用可否を判断できません。",
        )
    summary = summarize_applied_rows(verdicts)
    if not summary["readable"]:
        return _no_evidence(
            "saturation_flow.verify_applied_row",
            ko="어떤 주행행태에서도 ax/bx_add/bx_mult를 읽지 못했습니다 — 지적이 아니라 판독 실패입니다.",
            en="No behaviour yielded readable ax/bx_add/bx_mult — a missing reading, not an accusation.",
            ja="いずれの走行挙動でもax/bx_add/bx_multを読めませんでした — 指摘ではなく判読失敗です。",
            detail={"unreadable": summary["unreadable"]},
        )

    detail = _row_detail(summary)
    if summary["guideline_standstill"]:
        rows = sorted({str(r["row_pcu"]) for r in summary["guideline_standstill"]})
        return {
            "status": DONE,
            "source": "saturation_flow.verify_applied_row",
            "basis": _text(
                f"주행행태 {len(summary['guideline_standstill'])}개가 Table 5-6 공표 행"
                f"({', '.join(rows)} pcu) 위에 있고 정지 시 거리도 지침값 2.5 m입니다.",
                f"{len(summary['guideline_standstill'])} behaviour(s) sit on a published Table 5-6 row "
                f"({', '.join(rows)} pcu) with the guideline standstill distance of 2.5 m.",
                f"走行挙動{len(summary['guideline_standstill'])}件がTable 5-6の公表行"
                f"({', '.join(rows)} pcu)にあり、停止時距離も指針値2.5 mです。",
            ),
            "detail": detail,
            "citation": mrwa_reference.TABLE_5_6_CITATION,
        }
    if summary["on_published_row"]:
        return _no_evidence(
            "saturation_flow.verify_applied_row",
            ko=(
                f"안전거리 값은 공표 행 위에 있지만 정지 시 거리가 지침값(2.5 m)이 아닙니다 — "
                f"Vissim 기본값({_DEFAULT_COINCIDENT_ROW_PCU:g} pcu 행과 우연히 일치)과 구별되지 않으므로 "
                "측정이 제출됐다는 증거가 아닙니다."
            ),
            en=(
                "The safety-distance pair sits on a published row but the standstill distance is not "
                f"the guideline's 2.5 m — indistinguishable from the Vissim default (which coincides "
                f"with the {_DEFAULT_COINCIDENT_ROW_PCU:g} pcu row), so this is not evidence that a "
                "measurement was submitted."
            ),
            ja=(
                "安全距離の値は公表行にありますが停止時距離が指針値(2.5 m)ではありません — "
                f"Vissim既定値({_DEFAULT_COINCIDENT_ROW_PCU:g} pcu行と偶然一致)と区別できず、"
                "測定が提出された証拠にはなりません。"
            ),
            detail=detail,
        )
    return {
        "status": NEXT,
        "source": "saturation_flow.verify_applied_row",
        "basis": _text(
            f"주행행태 {len(summary['off_published_row'])}개가 어떤 공표 행에도 있지 않습니다 — "
            "측정 제출과 Table 5-6 세트 적용이 남아 있습니다.",
            f"{len(summary['off_published_row'])} behaviour(s) sit on no published row — submitting a "
            "measurement and applying the Table 5-6 set is what remains.",
            f"走行挙動{len(summary['off_published_row'])}件がいずれの公表行にもありません — "
            "測定提出とTable 5-6セットの適用が残っています。",
        ),
        "detail": detail,
        "citation": mrwa_reference.TABLE_5_6_CITATION,
    }


def _step_12(evidence: Mapping[str, Any]) -> dict[str, Any]:
    """Does EVERY readable behaviour sit on a published row, with 2.5 m ax?

    Step 7 asks whether the application happened anywhere; this asks whether
    anything is left half-applied — the state ``verify_applied_row`` was
    written to name (#44).
    """
    verdicts = evidence.get("applied_rows")
    if verdicts is None:
        return _no_evidence(
            "saturation_flow.verify_applied_row",
            ko="주행행태를 읽지 못했습니다.",
            en="No driving behaviour could be read.",
            ja="走行挙動を読めませんでした。",
        )
    summary = summarize_applied_rows(verdicts)
    detail = _row_detail(summary)
    if not summary["readable"]:
        return _no_evidence(
            "saturation_flow.verify_applied_row",
            ko="어떤 주행행태에서도 ax/bx_add/bx_mult를 읽지 못했습니다.",
            en="No behaviour yielded readable ax/bx_add/bx_mult.",
            ja="いずれの走行挙動でもax/bx_add/bx_multを読めませんでした。",
            detail=detail,
        )
    if not summary["guideline_standstill"]:
        return {
            "status": NEXT,
            "source": "saturation_flow.verify_applied_row",
            "basis": _text(
                "지침 세트가 적용된 흔적이 없습니다 (정지 시 거리 2.5 m인 주행행태 0개).",
                "No behaviour carries the guideline set (zero behaviours with a 2.5 m standstill distance).",
                "指針セットが適用された形跡がありません (停止時距離2.5 mの走行挙動0件)。",
            ),
            "detail": detail,
            "citation": mrwa_reference.TABLE_5_6_CITATION,
        }
    if summary["off_published_row"] or len(summary["guideline_standstill"]) != len(summary["readable"]):
        off = len(summary["off_published_row"])
        return {
            "status": IN_PROGRESS,
            "source": "saturation_flow.verify_applied_row",
            "basis": _text(
                f"읽은 주행행태 {len(summary['readable'])}개 중 {len(summary['guideline_standstill'])}개만 "
                f"공표 행 + 지침 정지 시 거리를 갖췄습니다 (공표 행 밖 {off}개) — 세트가 일부만 적용된 상태입니다.",
                f"Only {len(summary['guideline_standstill'])} of {len(summary['readable'])} readable "
                f"behaviour(s) carry a published row plus the guideline standstill distance ({off} off-row) — "
                "the set is partially applied.",
                f"読み取った走行挙動{len(summary['readable'])}件のうち{len(summary['guideline_standstill'])}件のみが"
                f"公表行と指針の停止時距離を備えています (公表行外{off}件) — セットが一部のみ適用された状態です。",
            ),
            "detail": detail,
            "citation": mrwa_reference.TABLE_5_6_CITATION,
        }
    return {
        "status": DONE,
        "source": "saturation_flow.verify_applied_row",
        "basis": _text(
            f"읽은 주행행태 {len(summary['readable'])}개가 모두 공표 행 위에 있고 정지 시 거리도 2.5 m입니다.",
            f"All {len(summary['readable'])} readable behaviour(s) sit on a published row with a 2.5 m "
            "standstill distance.",
            f"読み取った走行挙動{len(summary['readable'])}件すべてが公表行にあり、停止時距離も2.5 mです。",
        ),
        "detail": detail,
        "citation": mrwa_reference.TABLE_5_6_CITATION,
    }


def _row_detail(summary: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "readable": len(summary["readable"]),
        "on_published_row": len(summary["on_published_row"]),
        "off_published_row": len(summary["off_published_row"]),
        "guideline_standstill": len(summary["guideline_standstill"]),
        "default_coincident": len(summary["default_coincident"]),
        "unreadable": summary["unreadable"],
        "behaviors": summary["readable"][:_BEHAVIOR_DETAIL_CAP],
    }


#: Bounded like every other list this codebase puts on a wire (the 2026-07-27
#: lesson in ``tools_network``): a real network can carry dozens of behaviours
#: and the rail needs the COUNTS, not the roster.
_BEHAVIOR_DETAIL_CAP = 12


# ---------------------------------------------------------------------------
# Steps 10 and 11 — what the run's own reality check says
# ---------------------------------------------------------------------------

#: ``fixes.map_node_counts`` scope tokens this rail reads. Quoted rather than
#: imported to keep this module free of the proposal machinery; a test pins
#: them equal to ``geh_ladder``'s own spellings.
_SCOPE_DEMAND = "DEMAND_LEVEL"
_SCOPE_ROUTING = "ROUTING_SPLIT"
_OUTCOME_PROPOSAL = "PROPOSAL"
_ENTRANCE_MEASURED = "measured"
_ENTRANCE_UNVERIFIED = "unverified"


def _reality_check_usable(report: Mapping[str, Any]) -> bool:
    return bool(report) and str(report.get("status") or "") == "CHECKED"


def _proposals_with_scope(report: Mapping[str, Any], scope: str) -> list[Mapping[str, Any]]:
    found: list[Mapping[str, Any]] = []
    for node in _seq(report.get("nodes")):
        outcome = _map(_map(node).get("outcome"))
        if outcome.get("outcome") == _OUTCOME_PROPOSAL and outcome.get("scope") == scope:
            found.append(_map(node))
    return found


def _entrance_verdicts(report: Mapping[str, Any]) -> list[str]:
    """Every node's D4 verdict, read through the ONE reader.

    It used to spell the lookup itself — ``outcome.evidence.target.
    entrance_measurement``, which is where the demand arm puts it — and that
    made this step BLIND to the displaced case. ``fixes.map_node_counts`` HOLDS
    the D4 refusal when the split lever is also available and hangs it on the
    routing proposal's ``also_refused`` (its arm (b)), so a node whose entrance
    is measured AND whose split misses carried its verdict somewhere this
    function never looked. The consequence was not a missing count, it was a
    FALSE SENTENCE: with that as the only graded node, step ⑩ printed "no
    entrance carries a D4 measured verdict either" about a report that carried
    one, and in mixed reports it undercounted ``entrance_measured``.

    :func:`geh_ladder.entrance_verdict` looks in both places and is what the #51
    gate reads, so the rail and the gate can no longer disagree about the same
    report.

    WHY NOT the node's ``diagnosis.evidence.entrance_measurement`` (which is the
    same verdict, already unpacked): ``geh_ladder.classify_node`` attaches it
    only after its DEMAND_LEVEL and ROUTING_SPLIT rungs have returned, so on a
    routing proposal — precisely the displaced case — that key is absent. A
    diagnosis-first read would have re-created this same blind spot one layer up.
    """
    statuses: list[str] = []
    for node in _seq(report.get("nodes")):
        entrance = _map(geh_ladder.entrance_verdict(_map(_map(node).get("outcome"))))
        status = entrance.get("status")
        if isinstance(status, str) and status:
            statuses.append(status)
    return statuses


def _step_10(evidence: Mapping[str, Any]) -> dict[str, Any]:
    """The scored run first, a person's signature only where it said nothing (#53).

    Every machine state this step can reach — a waiting demand proposal, an
    unverified entrance, a D4 measured verdict, a comparison that graded nodes —
    is a fact about THIS run, and outranks a recollection about the network. So
    the signature can only lift the branch where the report is absent or
    unusable, and on every other branch it is named on the basis as outranked.
    """
    return _apply_attestation(_step_10_machine(evidence), _attestation(evidence, 10))


def _step_10_machine(evidence: Mapping[str, Any]) -> dict[str, Any]:
    report = _map(evidence.get("reality_check"))
    if not _reality_check_usable(report):
        return _no_evidence(
            "reality_check",
            ko=_not_checked_ko(report),
            en=_not_checked_en(report),
            ja=_not_checked_ja(report),
        )

    demand_proposals = _proposals_with_scope(report, _SCOPE_DEMAND)
    statuses = _entrance_verdicts(report)
    measured = statuses.count(_ENTRANCE_MEASURED)
    unverified = statuses.count(_ENTRANCE_UNVERIFIED)
    compared = _map(_map(report.get("coverage")).get("counts")).get("compared")
    detail = {
        "demand_proposals": len(demand_proposals),
        "entrance_measured": measured,
        "entrance_unverified": unverified,
        "nodes_compared": compared,
    }

    if demand_proposals:
        return {
            "status": NEXT,
            "source": "reality_check + fixes.entrance_measurement",
            "basis": _text(
                f"수요 제안 {len(demand_proposals)}건이 승인을 기다리고 있습니다"
                + (f" (실측 미확인 입구 {unverified}건)." if unverified else "."),
                f"{len(demand_proposals)} demand proposal(s) await approval"
                + (f" ({unverified} entrance(s) not verified as measured)." if unverified else "."),
                f"需要提案{len(demand_proposals)}件が承認待ちです"
                + (f" (実測未確認の入口{unverified}件)。" if unverified else "。"),
            ),
            "detail": detail,
        }
    if unverified:
        return {
            "status": IN_PROGRESS,
            "source": "fixes.entrance_measurement",
            "basis": _text(
                f"입구 교통량 {unverified}건이 실측으로 확인되지 않았습니다 (D4) — "
                "'미실측'이 아니라 '확인되지 않음'입니다.",
                f"{unverified} entrance volume(s) are not verified as measured (D4) — "
                "\"unverified\", never \"unmeasured\".",
                f"入口交通量{unverified}件が実測として確認されていません (D4) — "
                "「未実測」ではなく「未確認」です。",
            ),
            "detail": detail,
        }
    if measured:
        return {
            "status": DONE,
            "source": "fixes.entrance_measurement",
            "basis": _text(
                f"입구 교통량 {measured}건이 실측으로 확인됐고 대기 중인 수요 제안이 없습니다.",
                f"{measured} entrance volume(s) are verified as measured and no demand proposal is pending.",
                f"入口交通量{measured}件が実測として確認され、保留中の需要提案はありません。",
            ),
            "detail": detail,
        }
    if isinstance(compared, int) and compared > 0:
        return {
            "status": IN_PROGRESS,
            "source": "reality_check",
            "basis": _text(
                f"노드 {compared}곳이 실측을 재현해 수요 제안은 없지만, 입구 실측 확인(D4) 판정은 "
                "한 건도 없습니다.",
                f"{compared} node(s) reproduce the observed counts so no demand proposal was raised, but "
                "no entrance carries a D4 measured verdict either.",
                f"ノード{compared}箇所が実測を再現し需要提案はありませんが、入口の実測確認(D4)判定は1件もありません。",
            ),
            "detail": detail,
        }
    return _no_evidence(
        "reality_check",
        ko="이번 대조에서 비교된 노드가 없습니다.",
        en="This check compared no nodes.",
        ja="今回の対照で比較されたノードはありません。",
        detail=detail,
    )


def _step_11(evidence: Mapping[str, Any]) -> dict[str, Any]:
    """Same rank as ⑩ (#53): the ladder's own answer first, testimony second.

    Note which branch this makes signable, because it is the one that matters:
    "this check raised no route-choice proposal and no route-choice diagnosis —
    which is not evidence that route choice was confirmed" is exactly the
    sentence a modeller who DID review the splits has been unable to answer.
    Now they can sign it, and the sentence they replace it with names them.
    """
    return _apply_attestation(_step_11_machine(evidence), _attestation(evidence, 11))


def _step_11_machine(evidence: Mapping[str, Any]) -> dict[str, Any]:
    report = _map(evidence.get("reality_check"))
    if not _reality_check_usable(report):
        return _no_evidence(
            "reality_check",
            ko=_not_checked_ko(report),
            en=_not_checked_en(report),
            ja=_not_checked_ja(report),
        )
    routing_proposals = _proposals_with_scope(report, _SCOPE_ROUTING)
    by_rung = _map(_map(report.get("diagnosis")).get("by_rung"))
    routing_nodes = _seq(by_rung.get(_SCOPE_ROUTING))
    detail = {
        "routing_proposals": len(routing_proposals),
        "routing_rung_nodes": len(routing_nodes),
    }
    if routing_proposals:
        return {
            "status": NEXT,
            "source": "reality_check",
            "basis": _text(
                f"배분(경로 분할) 제안 {len(routing_proposals)}건이 승인을 기다리고 있습니다.",
                f"{len(routing_proposals)} route-split proposal(s) await approval.",
                f"配分(経路分割)提案{len(routing_proposals)}件が承認待ちです。",
            ),
            "detail": detail,
        }
    if routing_nodes:
        return {
            "status": IN_PROGRESS,
            "source": "geh_ladder",
            "basis": _text(
                f"노드 {len(routing_nodes)}곳이 배분 사다리(ROUTING_SPLIT)로 진단됐습니다.",
                f"{len(routing_nodes)} node(s) are diagnosed on the ROUTING_SPLIT rung.",
                f"ノード{len(routing_nodes)}箇所が配分ラダー(ROUTING_SPLIT)と診断されました。",
            ),
            "detail": detail,
        }
    return _no_evidence(
        "reality_check",
        ko=(
            "이번 대조에서 배분 관련 제안도 진단도 나오지 않았습니다 — "
            "배분이 확인됐다는 증거는 아닙니다."
        ),
        en=(
            "This check raised no route-choice proposal and no route-choice diagnosis — which is not "
            "evidence that route choice was confirmed."
        ),
        ja=(
            "今回の対照で配分に関する提案も診断も出ていません — "
            "配分が確認された証拠ではありません。"
        ),
        detail=detail,
    )


def _not_checked_ko(report: Mapping[str, Any]) -> str:
    if not report:
        return "실행–실측 대조 결과가 없습니다. 런을 선택해 채점하세요."
    return f"실행–실측 대조가 완료되지 않았습니다 ({report.get('status')} / {report.get('reason_code')})."


def _not_checked_en(report: Mapping[str, Any]) -> str:
    if not report:
        return "No run-versus-observed check is available. Pick a run and score it."
    return f"The run-versus-observed check did not complete ({report.get('status')} / {report.get('reason_code')})."


def _not_checked_ja(report: Mapping[str, Any]) -> str:
    if not report:
        return "実行と実測の対照結果がありません。ランを選択して採点してください。"
    return f"実行と実測の対照が完了していません ({report.get('status')} / {report.get('reason_code')})。"


# ---------------------------------------------------------------------------
# The steps with no evidence source at all
# ---------------------------------------------------------------------------


def _no_evidence(
    source: str, *, ko: str, en: str, ja: str, detail: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    return {
        "status": NO_EVIDENCE,
        "source": source,
        "basis": _text(ko, en, ja),
        "detail": dict(detail or {}),
    }


def _manual_area(number: int, evidence: Mapping[str, Any]) -> dict[str, Any]:
    """The honest answer for a step this product measures nothing for — unless
    a NAMED PERSON has signed for it (#53).

    Not "not started" and not "unknown": a MANUAL-CHECK area. The modeller does
    this step in Vissim and records it outside this tool, and the rail says so
    rather than leaving a gap a reader would fill in with an assumption.

    #53 stops this being evidence-BLIND rather than stopping it being honest.
    "This product measures nothing for this step" is still true after a
    signature — which is precisely why the signature produces
    :data:`CONFIRMED_BY_PERSON` and never :data:`DONE`: what changed is that
    somebody accountable said they did it, not that anything was measured. With
    no signature (and with a stale one) the sentence below is unchanged, word
    for word, from what it has always been.
    """
    return _apply_attestation(
        _no_evidence(
            "none",
            ko="증거 없음 — 수동 확인 영역입니다. 이 제품은 이 단계를 측정하지 않습니다.",
            en="No evidence — a manual-check area. This product measures nothing for this step.",
            ja="証拠なし — 手動確認領域です。本製品はこの段階を測定しません。",
        ),
        _attestation(evidence, number),
    )


# ---------------------------------------------------------------------------
# The rail
# ---------------------------------------------------------------------------

_DERIVERS = {
    1: _step_1,
    6: _step_6,
    7: _step_7,
    9: _step_9,
    10: _step_10,
    11: _step_11,
    12: _step_12,
}

#: Unit #51 (범위-구속 파라미터 탐색) hangs a SECOND track off step 12, after
#: Table 5-6. This is the track's LOCKED shape — the default the rail shows
#: whenever the precondition gate has not opened, which is most of the time and
#: is meant to be. #51 shipped, so the "why" no longer says "not built yet"; it
#: says what the gate is waiting for, which is the sentence a modeller can act
#: on. :func:`parameter_search_track` replaces the ``why`` with the gate's own
#: named refusals as soon as a scored run exists to evaluate them against.
PARAMETER_SEARCH_TRACK = {
    "key": "parameter_search",
    "unit": "#51",
    "locked": True,
    "label": _text(
        "범위-구속 파라미터 탐색",
        "Range-bounded parameter search",
        "範囲拘束パラメータ探索",
    ),
    "why": _text(
        "사다리를 다 쓴 뒤에만 열립니다. 실측 고정(D4)·Table 5-6 처방 적용·신호 단계 검증이 모두 "
        "확인되고, 실측과 조인되는 노드가 홀드아웃을 나눌 만큼 있어야 합니다.",
        "It opens only after the ladder is exhausted: measured entrances (D4), the Table 5-6 "
        "prescription applied, the signal step verified, and enough joined nodes to split a holdout.",
        "はしごを使い切った後にのみ開きます。実測固定(D4)・Table 5-6処方の適用・信号段階の検証がすべて"
        "確認され、実測と結合するノードがホールドアウトを分割できるだけ必要です。",
    ),
    "refusals": [],
    "card": None,
}


def parameter_search_track(evidence: Mapping[str, Any]) -> dict[str, Any]:
    """Step ⑫'s second track, LOCKED or AVAILABLE, from the same evidence bundle.

    The lock is not decorative and it is not this module's opinion: it is
    ``parameter_search.evaluate_preconditions`` run over the reality check, the
    Table 5-6 verdicts and this very rail's own step 6, and it carries that
    gate's NAMED refusals so a reader learns which door to open rather than that
    a door exists.

    Unlocked, the track carries the ask-first trigger card — the user's decision
    ② — and nothing starts on its own. The card is a question with its evidence
    attached; it is never a search in progress.

    The rail's own step 6 is passed to the gate rather than re-derived, so "the
    rail says signals are done" and "the gate says signals are done" cannot
    disagree on the same screen.

    ``guardrail`` (#51-c) rides on the track in BOTH states, locked or not: it is
    ``parameter_search.guardrail_policy()`` — what becomes of a searched value on
    each of the two paths, and the user's recorded answer on the collision
    (DECISIONS.md 2026-08-20). It belongs to the TRACK rather than to the trigger
    card because it is true of this door whether or not it has opened, and because
    a locked track is what this screen shows most of the time. It forecasts
    nothing: no behaviour has been chosen here, so the approved path carries a
    CONDITION rather than a verdict (see that function). The per-behaviour verdict
    lives on the approval card, where a behaviour finally exists to read.

    ``search_job`` (#51 live runner, 2026-08-21) rides on the track in BOTH states
    for the same reason ``guardrail`` does, plus one of its own: a search that
    SUCCEEDS closes the gate behind it. Every node's GEH now passes, so the next
    rail read refuses with ``GEH_ALREADY_PASSES`` and ``card`` is ``None`` — and a
    job block that lived only on the card would erase the completion the user is
    waiting to see, at the exact moment it arrived. It is mirrored onto the card
    as well when there is one, so a reader of either object sees the same state.
    Its content is the CALLER's (``routers/calibration.py`` asks
    ``services.agent.search_job``); this module composes none of it, and stays a
    pure function of the evidence bundle it is handed.
    """
    job = evidence.get("parameter_search_job")
    job = dict(job) if isinstance(job, Mapping) else None
    behaviours = evidence.get("parameter_search_behaviours")
    behaviours = [dict(item) for item in behaviours if isinstance(item, Mapping)] if isinstance(
        behaviours, (list, tuple)
    ) else []
    base = {
        **PARAMETER_SEARCH_TRACK,
        "guardrail": parameter_search.guardrail_policy(),
        "search_job": job,
        # Which driving behaviours a search could be run on, from the network the
        # caller read. The screen offers the choice; nothing here picks one, and
        # an empty list is the honest "no behaviour in this network carries all
        # three searched parameters readably".
        "behaviours": behaviours,
        "default_behavior_no": behaviours[0]["no"] if len(behaviours) == 1 else None,
    }
    reality = evidence.get("reality_check")
    if not isinstance(reality, Mapping):
        return base
    preconditions = parameter_search.evaluate_preconditions(
        reality_check=reality,
        applied_rows=evidence.get("applied_rows"),
        rail=evidence.get("_rail_so_far"),
    )
    if not preconditions["ok"]:
        return {
            **base,
            "locked": True,
            # `detail` rides along too (#57): the lock card's per-reason action
            # chips (frontend/components/calibration/FlowRail.tsx) read the
            # blocking node ids, the Table 5-6 off-row count and the signal-
            # timing node list FROM this dict to know where to send a reader —
            # they never recompute a navigation target of their own. Before
            # this it was dropped at the wire: `_refusal()` already built it in
            # `parameter_search.py`, but this comprehension copied only `code`,
            # `why` and `opens_when` off each refusal, so a chip built from the
            # rail payload would have had nothing to read no matter how the
            # frontend rendered it.
            "refusals": [
                {
                    "code": item["code"],
                    "why": item["why"],
                    "opens_when": item["opens_when"],
                    "detail": item.get("detail", {}),
                }
                for item in preconditions["refusals"]
            ],
        }
    return {
        **base,
        "locked": False,
        "why": _text(
            "사다리가 소진되어 탐색을 시작할 수 있는 상태입니다. 시작 여부는 사람이 정합니다 — "
            "자동으로 시작하지 않습니다.",
            "The ladder is exhausted, so a search may now be started. Whether to start it is a "
            "human's call — nothing starts on its own.",
            "はしごが尽き、探索を開始できる状態です。開始するかどうかは人が決めます — 自動では"
            "開始しません。",
        ),
        "refusals": [],
        "card": parameter_search.trigger_card(preconditions, search_job=job),
    }


def derive_rail(evidence: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """The thirteen §2.12.1 steps, each with the status its evidence supports.

    ``evidence`` keys, every one optional and every one a payload some OTHER
    module already produced:

    ``gate``
        ``services.genesis_jobs.gate_payload`` for the dataset's generation.
    ``health``
        ``services.agent.health_check.HealthCheckRunner.run``.
    ``phase_diagrams``
        ``services.genesis.phase_diagram.PhaseDiagramStore.index_payload``.
    ``signal_controller_count``
        ``len(shadow_model["signals"])``.
    ``pt_line_count``
        ``len(shadow_model["pt_lines"])`` (#50 follow-up, 2026-08-19).
    ``applied_rows``
        ``[{"behavior_no", "behavior_name", "verdict"}]`` where ``verdict`` is
        a ``saturation_flow.verify_applied_row`` result.
    ``reality_check``
        ``services.agent.tools_metrics.build_reality_check_report``.
    ``parameter_search_job``
        ``services.agent.search_job.job_card`` for the newest search THIS
        workspace started (#51's live runner), or ``None``. Read only by
        :func:`parameter_search_track`.
    ``step_attestations``
        ``services.calibration_attestation.index_payload()`` — ``{step number:
        the signature that stands}`` for the nine :data:`ATTESTABLE_STEPS`
        (#53). Gathered by the router for the same reason the search job is:
        this module reads a bundle and never a registry.

    An absent key is an absent EVIDENCE SOURCE, and it produces
    :data:`NO_EVIDENCE` — never a tick and never a failure.
    """
    bundle = _map(evidence)
    steps: list[dict[str, Any]] = []
    for number, label_en, citation in mrwa_reference.CALIBRATION_STEPS_2_12_1:
        if number in STEPS_WITHOUT_EVIDENCE_SOURCE:
            verdict = _manual_area(number, bundle)
        else:
            verdict = _DERIVERS[number](bundle)
        attestation = _attestation(bundle, number)
        step = {
            "number": number,
            "label": label_en,
            "label_ko": _LABELS_KO[number],
            "label_ja": _LABELS_JA[number],
            "label_source": "MRWA op §2.12.1 (English verbatim); ko/ja are this product's translations",
            "citation": citation,
            "anchor": _ANCHORS.get(number, _MANUAL_ANCHOR),
            "manual_area": number in STEPS_WITHOUT_EVIDENCE_SOURCE,
            # #53. Whether this step may be SIGNED is the server's answer, not a
            # list the browser keeps: a client-side copy of ATTESTABLE_STEPS
            # would be a second opinion about which steps have machine evidence,
            # and it would drift the first time one of them gained an artifact.
            "attestable": number in ATTESTABLE_STEPS,
            # The signature that STANDS, whatever it did to the status — a stale
            # one is here too, so the screen can show the record and offer to
            # revoke it rather than reporting 재확인 필요 about nothing visible.
            # ``None`` when there is none; a REVOKED one is never here (it is
            # history, and history lives in the store).
            "attestation": dict(attestation) if attestation else None,
            **verdict,
        }
        if number == 12:
            # The track's gate reads this rail's OWN step 6 rather than
            # re-deriving the signal verdict — one opinion per screen. Steps are
            # built in order, so everything before 12 is already in the list.
            step["tracks"] = [
                parameter_search_track({**bundle, "_rail_so_far": {"steps": steps}})
            ]
        steps.append(step)

    counts = {status: sum(1 for step in steps if step["status"] == status) for status in STATUSES}
    return {
        "steps": steps,
        "counts": counts,
        "citation": mrwa_reference.CALIBRATION_STEPS_CITATION,
        "principles": [
            {"text": text, "citation": citation}
            for text, citation in mrwa_reference.CALIBRATION_PRINCIPLES_2_12_1
        ],
        "statuses": list(STATUSES),
    }


__all__ = [
    "ATTESTABLE_STEPS",
    "CONFIRMED_BY_PERSON",
    "DONE",
    "IN_PROGRESS",
    "NEXT",
    "NOT_APPLICABLE",
    "NO_EVIDENCE",
    "STATUSES",
    "POSITIVE_STATUSES",
    "STEPS_WITHOUT_EVIDENCE_SOURCE",
    "PARAMETER_SEARCH_TRACK",
    "parameter_search_track",
    "derive_rail",
    "summarize_applied_rows",
]
