"""The reality check (Phase 2 unit U5) — the RUN versus the field counts.

Roadmap row 2.1's chain ends here: evidence (U1, ``run_result``) → anomaly
catalogue (U3, ``anomalies.py``) → fix mapping (U4, ``fixes.py``) → **reality
check (this module)** → the existing approval loop. This is requirement
R2.3's "관측 데이터 대조": for every node the run and the counts workbook BOTH
know about, compare what the simulation just produced against what was
measured in the field, and end in a concrete proposal or a stated refusal.

The one distinction this unit exists for
---------------------------------------
**The modelled side comes from the RUN, never from the workbook's model
column.** The base workbook carries a ``Vehs(All)`` column, and it is a
PASTED EXPORT: it was true of whichever run the analyst copied it from, which
is not the run the agent just did. Comparing observed counts against that
column would grade a network nobody is looking at any more. So this module
BLANKS the workbook's modelled numbers (:func:`_observed_side_only`) and lets
``fixes.merge_modelled_from_run_result`` fill them back in from U1's
``run_result["movement_volumes"]`` — and a movement the run cannot speak to
stays blank and is reported as ungradable rather than quietly graded against
a stale number.

Everything else is borrowed, deliberately
-----------------------------------------
* GEH is **not** respelled. ``services.validation.geh`` /
  ``validation.GEH_GOOD_LT`` are the only formula home, reached through U4's
  :func:`fixes.map_node_counts` — this module never compares two volumes
  itself.
* The three outcomes, their rationales and their refusal shape are U4's.
* "Why the run evidence is not usable" is **U3's** language, read straight
  out of ``anomalies`` (private helpers, read-only, the same one-way reuse
  ``services/inpx_structure.py`` already makes of ``tools_check._load_check``)
  rather than paraphrased into a second vocabulary that could drift.
* The observed side is **the real loader's** output —
  ``validation.load_base_workbook``'s normalized count rows, aggregated by
  ``validation._real_counts_from_count_rows``, the same call
  ``load_real_counts`` makes.

Unit U10 gave the observed side a SECOND origin without giving this module a
second shape: ``services/observations.py`` stores per-workspace aggregates of
counts ingested through the 1.2 adapter and hands them over as the very
``{node_id: NodeRealCounts}`` mapping :func:`observed_nodes` has always
accepted. What changes here is honesty book-keeping, not comparison logic —
four reason codes for the ways a stored set can be unusable, and an
``observed_source`` override so the report's ``sources`` block names the origin
that was actually read. The site-id ↔ node-id join for a stored set is CALLER
knowledge for the same reason ``movement_ids`` is: a Daejeon site id is an
intersection name lifted from a file name (roadmap 1.2's documented
deviation), and it does not match a Vissim node id.

The join, and why it is reported instead of assumed
---------------------------------------------------
U1 puts the node token on every ``movement_volumes`` entry (the leading
integer run of the Vissim movement OBJECT_ID, e.g. ``1`` in
``1-508-100.8-510-62.1``); the workbook carries node ids. The node-level join
is on those two, and **every id that fails to join is named**:

* ``observed_nodes_absent_from_run`` — counted in the field, not present in
  this run (a count site outside the model boundary, or a node whose
  evaluation was not armed);
* ``run_nodes_without_observed`` — simulated, never counted;
* ``unjoined_run_object_ids`` — the individual movements behind those nodes.

A silent inner join that drops both sides and reports a clean "3 of 3 nodes
match" is the classic lie in this class of tool. It is not available here:
the coverage block is built before any comparison and is always present.

The MOVEMENT-level join is a different problem and is not guessed either. A
workbook movement is a turn token ("W-E"); a run movement is an OBJECT_ID.
Nothing in the run evidence says which is which — that is the network's
knowledge, not the run's — so the caller supplies ``movement_ids`` the way it
supplies U4's ``targets`` (the shadow model knows the geometry). Without it
the node TOTAL is still compared in full; the per-movement splits are
reported as ungradable, named, and no routing proposal is invented.

Applies nothing, touches nothing
--------------------------------
No COM, no edit session, no writes. Every function here is a pure transform of
payloads somebody else produced. The registry tool that wraps it
(``tools_metrics.run_reality_check``) is read-tier, and the proposals it
returns go to the human through the EXISTING approval loop — this module has
no opinion about whether they are applied.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from services import results_evidence, validation
from services.agent import anomalies, fixes
from services.agent.toolkit import ToolRegistry

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Outcome of one check (a property of the evidence, not of the caller)
# ---------------------------------------------------------------------------

#: Both sides were usable and at least one node was compared.
CHECKED = "CHECKED"
#: The check could not be performed. The report says why, in ko/en/ja.
NOT_CHECKABLE = "NOT_CHECKABLE"

STATUSES = (CHECKED, NOT_CHECKABLE)


# ---------------------------------------------------------------------------
# Reason codes — a NOT_CHECKABLE report always carries exactly one
# ---------------------------------------------------------------------------

#: No counts workbook / no count rows at all. "There is nothing to compare
#: against" is a refusal, never an empty success.
NO_OBSERVED_COUNTS = "NO_OBSERVED_COUNTS"
#: The workbook exists but the loader could not get usable rows out of it.
#: The loader's OWN warnings are carried verbatim in ``workbook_warnings``.
OBSERVED_WORKBOOK_UNREADABLE = "OBSERVED_WORKBOOK_UNREADABLE"
#: The workbook has counts, but none for the requested period.
OBSERVED_PERIOD_ABSENT = "OBSERVED_PERIOD_ABSENT"
#: There is no run payload at all.
NO_RUN_RESULT = "NO_RUN_RESULT"
#: Both sides were usable and NOTHING joined — every observed node is absent
#: from the run and vice versa. Named separately from "no counts" because the
#: fix is different (a node-id mismatch, not a missing file).
NO_JOINABLE_NODES = "NO_JOINABLE_NODES"
#: The run's ``movement_volumes`` was not usable. One code per U3 evidence
#: state, so the reason survives into a machine consumer as well as prose.
RUN_EVIDENCE_ARMED_BUT_EMPTY = f"RUN_EVIDENCE_{results_evidence.ARMED_BUT_EMPTY}"
RUN_EVIDENCE_TABLE_ABSENT = f"RUN_EVIDENCE_{results_evidence.TABLE_ABSENT}"
RUN_EVIDENCE_KEY_OMITTED = f"RUN_EVIDENCE_{anomalies.KEY_OMITTED}"
RUN_EVIDENCE_EMPTY = f"RUN_EVIDENCE_{anomalies.EMPTY}"
#: The run's volumes are summed across more than one result database (U1's
#: ``read_movement_class_rows`` adds VEHS across every .db in the run), so
#: they are N seeds' worth of vehicles and are not comparable to a
#: single-period observed count. Raised by the TOOL, which is the layer that
#: knows how many databases there were.
MULTI_SEED_VOLUMES_NOT_COMPARABLE = "MULTI_SEED_VOLUMES_NOT_COMPARABLE"
#: The tool could not resolve the run the caller named.
RUN_NOT_FOUND = "RUN_NOT_FOUND"

# -- unit U10: the observed side came from the observation store -------------
#
# Four more ways the observed side can be unusable, all of them specific to a
# STORED set rather than to the base workbook. They live here, beside the other
# reason codes, because a report carries exactly one reason code and this
# module owns that vocabulary; the store itself (services/observations.py)
# emits its own neutral codes and ``tools_metrics`` maps them across, so the
# non-agent import layer never has to depend on services.agent.

#: The caller named an observation set this workspace does not hold.
OBSERVATION_SET_NOT_FOUND = "OBSERVATION_SET_NOT_FOUND"
#: The set exists but carries no aggregates for the named period — or carries
#: several and none was named, which is not something to pick for the caller.
OBSERVATION_SET_PERIOD_ABSENT = "OBSERVATION_SET_PERIOD_ABSENT"
#: No site -> node mapping was supplied. Observation site ids are survey
#: labels (Daejeon's come from file names, roadmap 1.2's documented deviation)
#: and do NOT match Vissim node ids, so the join is caller knowledge exactly
#: like ``movement_ids`` and ``decision_movements`` are.
OBSERVATION_SITE_NODE_MAP_MISSING = "OBSERVATION_SITE_NODE_MAP_MISSING"
#: A map was supplied and not one of its entries produced a usable node.
OBSERVATION_NO_MAPPED_SITES = "OBSERVATION_NO_MAPPED_SITES"

#: U3 evidence state -> this module's reason code.
_STATE_REASONS = {
    results_evidence.ARMED_BUT_EMPTY: RUN_EVIDENCE_ARMED_BUT_EMPTY,
    results_evidence.TABLE_ABSENT: RUN_EVIDENCE_TABLE_ABSENT,
    anomalies.KEY_OMITTED: RUN_EVIDENCE_KEY_OMITTED,
    anomalies.EMPTY: RUN_EVIDENCE_EMPTY,
}


# ---------------------------------------------------------------------------
# The run_result key and the table behind it — U1 owns both spellings
# ---------------------------------------------------------------------------

MOVEMENT_VOLUMES_KEY = "movement_volumes"
_MOVEMENT_VOLUMES_TABLES = (results_evidence.MOVEMENT_INTERVAL_CLASS_TABLE,)

#: Same two-axis bound U3's scan report applies to its own details: a payload
#: whose size tracks NETWORK size gets rendered straight into a UI list.
_MAX_DETAIL_ITEMS = 20


# ---------------------------------------------------------------------------
# ko/en/ja for this module's own reasons (U3's states are quoted, not restated)
# ---------------------------------------------------------------------------

_MESSAGES: dict[str, dict[str, str]] = {
    CHECKED: {
        "ko": "실행 결과와 실측 교통량을 노드 {compared}곳에서 대조했습니다: 제안 {proposals}건, "
        "거부 {refusals}건. 모델 값은 이번 실행의 movement_volumes에서 가져왔으며, 워크북의 "
        "모델 열(Vehs(All))은 과거 실행의 붙여넣기일 수 있으므로 사용하지 않았습니다.",
        "en": "Compared this run against the observed counts at {compared} node(s): "
        "{proposals} proposal(s), {refusals} refusal(s). The modelled side came from THIS "
        "run's movement_volumes; the workbook's own model column (Vehs(All)) was not used, "
        "because a pasted export may describe a different run.",
        "ja": "実行結果と実測交通量をノード{compared}箇所で対照しました: 提案{proposals}件、"
        "拒否{refusals}件。モデル値は今回の実行のmovement_volumesから取得し、ワークブックの"
        "モデル列(Vehs(All))は過去の実行の貼り付けである可能性があるため使用していません。",
    },
    NO_OBSERVED_COUNTS: {
        "ko": "실측 교통량 워크북이 없습니다 — 대조할 관측 데이터가 없으므로 확인 불가로 "
        "보고합니다. 데이터가 없다는 것은 '문제 없음'이 아닙니다.",
        "en": "There is no observed-counts workbook — nothing to compare against, so this is "
        "reported as not checkable. Absence of data is not a clean bill of health.",
        "ja": "実測交通量のワークブックがありません — 対照する観測データがないため確認不可と"
        "して報告します。データが無いことは「問題なし」ではありません。",
    },
    OBSERVED_WORKBOOK_UNREADABLE: {
        "ko": "실측 워크북을 읽었지만 사용할 수 있는 교통량 행이 없습니다. 로더가 보고한 사유를 "
        "그대로 전달합니다: {warnings}",
        "en": "The observed workbook was read but yielded no usable count rows. The loader's "
        "own reasons are passed through verbatim: {warnings}",
        "ja": "実測ワークブックを読み込みましたが、使用可能な交通量の行がありません。ローダーが"
        "報告した理由をそのまま伝えます: {warnings}",
    },
    OBSERVED_PERIOD_ABSENT: {
        "ko": "실측 워크북에 '{period}' 시간대 데이터가 없습니다(있는 시간대: {periods}). "
        "다른 시간대의 값을 대신 쓰지 않고 확인 불가로 보고합니다.",
        "en": "The observed workbook carries no '{period}' period (it has: {periods}). "
        "Reported as not checkable rather than silently substituting another period's counts.",
        "ja": "実測ワークブックに「{period}」時間帯のデータがありません(存在する時間帯: "
        "{periods})。他の時間帯の値で代用せず、確認不可として報告します。",
    },
    NO_RUN_RESULT: {
        "ko": "실행 결과 자체가 없습니다 — 대조할 모델 값이 없습니다. 워크북의 모델 열은 "
        "과거 실행의 값일 수 있으므로 대체재로 쓰지 않습니다.",
        "en": "There is no run payload at all — no modelled side to compare. The workbook's own "
        "model column is NOT used as a substitute: it may describe a different run.",
        "ja": "実行結果自体がありません — 対照するモデル値がありません。ワークブックのモデル列は"
        "過去の実行の値である可能性があるため代用しません。",
    },
    NO_JOINABLE_NODES: {
        "ko": "양쪽 모두 데이터는 있지만 노드 id가 하나도 일치하지 않습니다 — 실측 노드 "
        "{observed}, 실행 노드 {run}. 조인되지 않은 id를 그대로 보고하며, 임의로 짝지어 "
        "비교하지 않습니다.",
        "en": "Both sides carry data but not one node id joins — observed nodes {observed}, run "
        "nodes {run}. The unjoined ids are reported as they are; nothing is paired by guesswork.",
        "ja": "双方にデータはありますが、ノードidが一つも一致しません — 実測ノード{observed}、"
        "実行ノード{run}。結合できなかったidをそのまま報告し、推測で対応付けはしません。",
    },
    MULTI_SEED_VOLUMES_NOT_COMPARABLE: {
        "ko": "이 실행은 결과 DB {count}개(시드 {count}개)로 구성되어 있고, movement_volumes는 "
        "그 VEHS를 모두 합산합니다 — 즉 {count}회분의 차량 대수입니다. 1개 시간대의 실측 "
        "교통량과 비교하면 {count}배의 수요 오차를 만들어 냅니다. 단일 시드 실행으로 다시 "
        "확인하십시오.",
        "en": "This run is made of {count} result databases ({count} seeds), and "
        "movement_volumes SUMS their VEHS — i.e. {count} runs' worth of vehicles. Comparing "
        "that against one period of observed counts would manufacture a {count}x demand error. "
        "Re-check with a single-seed run.",
        "ja": "この実行は結果DB {count}個({count}シード)で構成され、movement_volumesはその"
        "VEHSを合算します — つまり{count}回分の車両台数です。1時間帯の実測交通量と比較すると"
        "{count}倍の需要誤差を作り出します。単一シードの実行で再確認してください。",
    },
    RUN_NOT_FOUND: {
        "ko": "'{scenario_id}'에 해당하는 실행 결과를 찾을 수 없습니다. 존재하지 않는 실행을 "
        "대조했다고 보고하지 않습니다.",
        "en": "No run results could be resolved for '{scenario_id}'. This will not be reported "
        "as a comparison against a run that does not exist.",
        "ja": "'{scenario_id}'に対応する実行結果が見つかりません。存在しない実行と対照したとは"
        "報告しません。",
    },
    OBSERVATION_SET_NOT_FOUND: {
        "ko": "이 프로젝트에 '{observation_set_id}' 관측 세트가 없습니다. 다른 세트로 대신 "
        "비교하지 않고 확인 불가로 보고합니다.",
        "en": "This project holds no observation set '{observation_set_id}'. Reported as not "
        "checkable rather than silently comparing against a different set.",
        "ja": "このプロジェクトに観測セット '{observation_set_id}' がありません。別のセットで"
        "代用せず、確認不可として報告します。",
    },
    OBSERVATION_SET_PERIOD_ABSENT: {
        "ko": "관측 세트에 '{period}' 시간대 집계가 없습니다(있는 시간대: {periods}). 어느 "
        "시간대를 모델과 비교할지는 모델러의 결정이므로 임의로 고르지 않습니다.",
        "en": "The observation set carries no '{period}' aggregate (it has: {periods}). Which "
        "window the run is compared against is the modeller's decision, so none was picked here.",
        "ja": "観測セットに「{period}」時間帯の集計がありません(存在する時間帯: {periods})。"
        "どの時間帯と比較するかはモデラーの決定であり、ここでは選びません。",
    },
    OBSERVATION_SITE_NODE_MAP_MISSING: {
        "ko": "관측 지점과 Vissim 노드를 잇는 대응표(site_nodes)가 없습니다. 관측 지점 id는 "
        "조사 자료의 교차로 이름(대전 자료는 파일명에서 읽은 값)이고 노드 번호와 일치하지 "
        "않으므로, 추측으로 짝지어 비교하지 않습니다. 대응이 필요한 지점: {sites}",
        "en": "No site-to-node map (site_nodes) was supplied. Observation site ids are survey "
        "labels — the Daejeon set's come from the file names — and do not match Vissim node "
        "numbers, so nothing is paired by guesswork. Sites needing a mapping: {sites}",
        "ja": "観測地点とVissimノードを結ぶ対応表(site_nodes)がありません。観測地点idは調査"
        "資料の交差点名(大田の資料はファイル名から読んだ値)であり、ノード番号とは一致しない"
        "ため、推測で対応付けはしません。対応が必要な地点: {sites}",
    },
    OBSERVATION_NO_MAPPED_SITES: {
        "ko": "대응표가 있지만 사용할 수 있는 노드가 하나도 만들어지지 않았습니다. 대응되지 "
        "않은 관측 지점: {unmapped}. 대응표에 있으나 세트에 없는 지점: {absent}.",
        "en": "A site-to-node map was supplied but produced no usable node. Observation sites "
        "left unmapped: {unmapped}. Mapped sites absent from the set: {absent}.",
        "ja": "対応表はありますが、使用可能なノードが一つも作られませんでした。対応されなかった"
        "観測地点: {unmapped}。対応表にあるがセットに無い地点: {absent}。",
    },
}

_RUN_EVIDENCE_PREFIX = {
    "ko": "이번 실행의 이동류별 통행량(movement_volumes)을 사용할 수 없어 실측 대조를 수행할 수 "
    "없습니다.",
    "en": "This run's per-movement volumes (movement_volumes) are not usable, so no comparison "
    "against the observed counts can be performed.",
    "ja": "今回の実行の移動別交通量(movement_volumes)が使用できないため、実測との対照を実行"
    "できません。",
}


def _message(reason: str, **kwargs: Any) -> dict[str, str]:
    template = _MESSAGES.get(reason)
    if template is None:  # pragma: no cover - every reason above is covered
        return {lang: reason for lang in ("ko", "en", "ja")}
    return {lang: text.format(**kwargs) for lang, text in template.items()}


def _bound(items: Sequence[Any]) -> list[Any]:
    if len(items) <= _MAX_DETAIL_ITEMS:
        return list(items)
    return list(items[:_MAX_DETAIL_ITEMS]) + [f"... +{len(items) - _MAX_DETAIL_ITEMS} more"]


def _node_sort_key(node_id: str) -> tuple[int, Any]:
    return (0, int(node_id)) if str(node_id).isdigit() else (1, str(node_id))


# ---------------------------------------------------------------------------
# The observed side
# ---------------------------------------------------------------------------


def observed_nodes(
    observed: Mapping[str, Any] | None, *, period: str | None = None
) -> dict[str, Any]:
    """Normalize whatever the caller has into ``{node_id: NodeRealCounts}``.

    Accepts either shape a caller realistically holds:

    * ``validation.load_base_workbook``'s payload (``{"counts": [...],
      "warnings": [...], ...}``) — aggregated here through
      ``validation._real_counts_from_count_rows``, the SAME call
      ``load_real_counts`` makes internally, so the two import paths can never
      disagree about what a node's total is; or
    * an already-aggregated ``{node_id: NodeRealCounts}`` mapping.

    Returns ``{"nodes", "warnings", "periods", "problem"}``. ``problem`` is
    ``None`` when the observed side is usable, otherwise one of this module's
    reason codes — the loader's own warnings ride along either way, never
    swallowed.
    """
    if not observed:
        return {"nodes": {}, "warnings": [], "periods": [], "problem": NO_OBSERVED_COUNTS}

    if "counts" in observed or "warnings" in observed:
        warnings = [str(item) for item in (observed.get("warnings") or [])]
        rows = list(observed.get("counts") or [])
        periods = [str(item) for item in (observed.get("periods") or [])]
        if not rows:
            # "The file is not there" and "the file is there and the loader
            # could not use it" are different facts with different fixes, so
            # they get different reason codes. exists=False is the loader's own
            # word for the first.
            problem = (
                NO_OBSERVED_COUNTS
                if observed.get("exists") is False or not warnings
                else OBSERVED_WORKBOOK_UNREADABLE
            )
            return {"nodes": {}, "warnings": warnings, "periods": periods, "problem": problem}
        nodes = validation._real_counts_from_count_rows(rows, period)
        if not nodes:
            # _real_counts_from_count_rows returns {} for a period the workbook
            # does not carry -- that is a period miss, not an empty workbook.
            return {
                "nodes": {},
                "warnings": warnings,
                "periods": periods,
                "problem": OBSERVED_PERIOD_ABSENT,
            }
        return {"nodes": nodes, "warnings": warnings, "periods": periods, "problem": None}

    nodes = {
        str(node_id): node
        for node_id, node in observed.items()
        if isinstance(node, Mapping)
    }
    problem = None if nodes else NO_OBSERVED_COUNTS
    return {"nodes": nodes, "warnings": [], "periods": [], "problem": problem}


def _observed_side_only(node_evidence: dict[str, Any]) -> dict[str, Any]:
    """Blank the workbook's OWN modelled numbers.

    This is the line R2.3 turns on. ``node_evidence_from_real_counts`` copies
    the workbook's ``sim_basam``/``total_sim_basam`` columns into the modelled
    slots; those are a pasted export of some earlier run. Blanking them means
    the only way a movement gets a modelled number is
    ``merge_modelled_from_run_result`` putting THIS run's number there — and a
    movement the run cannot speak to stays ``None``, which
    ``fixes._grade_movements`` reports as ungradable instead of grading.
    """
    stripped = dict(node_evidence)
    stripped["modelled_total"] = None
    stripped["movements"] = [
        {**movement, "modelled": None} for movement in (node_evidence.get("movements") or [])
    ]
    sources = dict(stripped.get("sources") or {})
    sources["modelled"] = "run_result"
    sources["workbook_model_column_ignored"] = True
    stripped["sources"] = sources
    return stripped


# ---------------------------------------------------------------------------
# The run side
# ---------------------------------------------------------------------------


def run_evidence_state(run_result: Mapping[str, Any] | None) -> dict[str, Any]:
    """U3's own verdict on ``run_result["movement_volumes"]``.

    Read straight out of ``anomalies`` rather than re-derived: it already
    distinguishes "the table was never armed" (``TABLE_ABSENT``) from "it was
    armed and collected nothing" (``ARMED_BUT_EMPTY``) from "the key is simply
    not here", and it already carries the ko/en/ja sentence for each. A second
    implementation of that distinction is exactly the drift U3 exists to
    prevent.
    """
    if not run_result:
        return {
            "key": MOVEMENT_VOLUMES_KEY,
            "state": anomalies.NO_RUN_RESULT,
            "tables": list(_MOVEMENT_VOLUMES_TABLES),
            "why": None,
            "marker_reason": None,
            "marker_source": None,
        }
    return anomalies._evidence_state(
        run_result,
        MOVEMENT_VOLUMES_KEY,
        _MOVEMENT_VOLUMES_TABLES,
        anomalies._descriptor(run_result),
    )


def run_nodes(run_result: Mapping[str, Any] | None) -> dict[str, list[str]]:
    """``{node token: [OBJECT_ID, ...]}`` for the run's movement volumes.

    The node-level aggregate row Vissim writes alongside the movements (its
    OBJECT_ID IS the bare node id) counts as evidence THAT the node exists but
    is not a movement, so it is recorded under the node and excluded from the
    id list — the same split ``fixes.modelled_from_run_result`` makes.
    """
    volumes = (run_result or {}).get(MOVEMENT_VOLUMES_KEY)
    if not isinstance(volumes, Mapping):
        return {}
    by_node: dict[str, list[str]] = {}
    for object_id, row in volumes.items():
        if isinstance(row, Mapping) and row.get("node"):
            token = str(row["node"])
        else:
            token = results_evidence.node_token(object_id)
        entries = by_node.setdefault(token, [])
        if str(object_id) != token:
            entries.append(str(object_id))
    return by_node


def _aliased_run_view(
    run_result: Mapping[str, Any],
    node_id: str,
    aliases: Mapping[str, str] | None,
) -> tuple[dict[str, Any], list[str]]:
    """A ``run_result`` view whose movement keys are the WORKBOOK's ids.

    ``aliases`` is ``{workbook movement id: run OBJECT_ID}`` — the caller's
    knowledge of the network, never guessed here. Returns the view plus any
    aliases that could not be applied (an OBJECT_ID this run does not carry,
    or two workbook movements claiming the same OBJECT_ID), so an ambiguous
    map degrades to "ungradable, and here is why" rather than to one movement
    silently overwriting another.
    """
    volumes = run_result.get(MOVEMENT_VOLUMES_KEY)
    if not isinstance(volumes, Mapping):
        return {MOVEMENT_VOLUMES_KEY: {}}, []

    rejected: list[str] = []
    by_object_id: dict[str, str] = {}
    for workbook_id, object_id in (aliases or {}).items():
        key = str(object_id)
        if key not in volumes:
            rejected.append(f"{workbook_id}->{key} (not in this run)")
            continue
        if key in by_object_id:
            rejected.append(f"{workbook_id}->{key} (already claimed by {by_object_id[key]})")
            continue
        by_object_id[key] = str(workbook_id)

    view: dict[str, Any] = {}
    for object_id, row in volumes.items():
        if not isinstance(row, Mapping):
            continue
        token = str(row.get("node") or results_evidence.node_token(object_id))
        if token != node_id:
            view[str(object_id)] = row
            continue
        renamed = by_object_id.get(str(object_id), str(object_id))
        # The node token is re-stated explicitly: once the key stops being an
        # OBJECT_ID, node_token() of it would no longer resolve, and
        # modelled_from_run_result falls back to that when "node" is absent.
        view[renamed] = {**row, "node": token}
    return {MOVEMENT_VOLUMES_KEY: view}, rejected


# ---------------------------------------------------------------------------
# The entry point
# ---------------------------------------------------------------------------


def not_checkable(
    reason: str,
    *,
    detail: Mapping[str, Any] | None = None,
    **message_kwargs: Any,
) -> dict[str, Any]:
    """A NOT_CHECKABLE report — the same shape a successful one has.

    Exported because the layer that resolves a run (the registry tool) can
    discover a blocker this pure function cannot see, and must report it in
    one shape rather than inventing a second.
    """
    return {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "roadmap_rows": ["2.1"],
        "requirement": "R2.3",
        "status": NOT_CHECKABLE,
        "reason_code": reason,
        "message": _message(reason, **message_kwargs),
        "geh_threshold": validation.GEH_GOOD_LT,
        "period": None,
        "sources": _SOURCES,
        "run_evidence": None,
        "coverage": _empty_coverage(),
        "nodes": [],
        "proposals": [],
        "refusals": [],
        "workbook_warnings": [],
        "detail": dict(detail or {}),
    }


_SOURCES = {
    "observed": "validation.load_base_workbook -> Counts sheet (Observed column)",
    "modelled": "run_result['movement_volumes'] (unit U1)",
    "modelled_not_used": "the workbook's own model column (Vehs(All)) -- a pasted export "
    "that may describe a different run",
    "geh": "services.validation.geh / GEH_GOOD_LT",
    "mapping": "services.agent.fixes.map_node_counts (unit U4)",
}


def _sources(observed_source: str | None = None) -> dict[str, Any]:
    """The report's provenance block, with the OBSERVED line overridable.

    Unit U10 gave the observed side a second origin (a stored observation set
    ingested through the 1.2 adapter). ``sources`` is what a reader — and the
    report builder — uses to say where a number came from, so it must name the
    origin that was actually used; leaving the base-workbook sentence in place
    while reading a stored set would be a provenance lie in the one block whose
    entire job is provenance.
    """
    if not observed_source:
        return dict(_SOURCES)
    return {**_SOURCES, "observed": observed_source}


def _empty_coverage() -> dict[str, Any]:
    return {
        "nodes_compared": [],
        "observed_nodes_absent_from_run": [],
        "run_nodes_without_observed": [],
        "unjoined_run_object_ids": [],
        "ungradable_movements": [],
        "rejected_movement_aliases": [],
        "counts": {
            "observed_nodes": 0,
            "run_nodes": 0,
            "compared": 0,
            "movements_compared": 0,
            "movements_ungradable": 0,
        },
    }


def check_run_against_counts(
    run_result: Mapping[str, Any] | None,
    observed: Mapping[str, Any] | None,
    *,
    period: str | None = None,
    targets: Mapping[str, Mapping[str, Any]] | None = None,
    movement_ids: Mapping[str, Mapping[str, str]] | None = None,
    geh_threshold: float = validation.GEH_GOOD_LT,
    registry: ToolRegistry | None = None,
    observed_source: str | None = None,
) -> dict[str, Any]:
    """RUN evidence vs OBSERVED counts, per node → proposals and refusals.

    ``run_result`` is U1's payload (only ``movement_volumes`` and ``evidence``
    are read). ``observed`` is either ``load_base_workbook``'s payload or an
    already-aggregated ``{node_id: NodeRealCounts}``.

    ``targets`` and ``movement_ids`` are both keyed by node id and both come
    from the CALLER, because both are network knowledge this module refuses to
    guess:

    * ``targets[node_id]`` is U4's target payload (``{"vehicle_input": ...,
      "routes": {...}}``) — the objects an edit could touch;
    * ``movement_ids[node_id]`` is ``{workbook movement id: run OBJECT_ID}`` —
      which simulated movement is which counted turn.

    Their absence never fabricates a proposal: U4 refuses with
    ``NO_TARGET_OBJECT``, and an unmapped movement is reported ungradable.

    A routes entry also carries ``decision_movements``, the movements that
    decision governs — the denominator U4 takes the observed share against.
    It is network knowledge like the rest of the target, so a routes entry
    without it is refused (``EVIDENCE_INSUFFICIENT``) rather than scaled
    against the node total; see :func:`fixes._build_routing_proposal`.
    """
    targets = targets or {}
    movement_ids = movement_ids or {}
    # ``observed_source``, when given, names where the observed side actually
    # came from (unit U10's observation store). Every return path below carries
    # it, including the refusals -- a NOT_CHECKABLE report that misattributed
    # its observed side would send the reader to the wrong file.
    sources = _sources(observed_source)

    observed_side = observed_nodes(observed, period=period)
    warnings = observed_side["warnings"]
    if observed_side["problem"] is not None:
        report = not_checkable(
            observed_side["problem"],
            warnings="; ".join(warnings) or "-",
            period=period or "-",
            periods=", ".join(observed_side["periods"]) or "-",
        )
        report["workbook_warnings"] = warnings
        report["period"] = period
        report["sources"] = sources
        return report

    evidence_state = run_evidence_state(run_result)
    if evidence_state["state"] != anomalies.AVAILABLE:
        reason = _STATE_REASONS.get(evidence_state["state"], NO_RUN_RESULT)
        report = not_checkable(reason)
        # U3's own sentence for this state, attached rather than restated. It
        # is the more specific of the two, so it goes second. A state with no
        # sentence (there is none today) still gets the framing rather than a
        # bare reason code.
        why = evidence_state.get("why") or {}
        report["message"] = {
            lang: " ".join(filter(None, (_RUN_EVIDENCE_PREFIX[lang], why.get(lang))))
            for lang in ("ko", "en", "ja")
        }
        report["run_evidence"] = evidence_state
        report["workbook_warnings"] = warnings
        report["period"] = period
        report["sources"] = sources
        report["coverage"]["counts"]["observed_nodes"] = len(observed_side["nodes"])
        report["coverage"]["observed_nodes_absent_from_run"] = sorted(
            observed_side["nodes"], key=_node_sort_key
        )
        return report

    assert run_result is not None  # AVAILABLE implies a payload
    by_node = run_nodes(run_result)
    observed_ids = set(observed_side["nodes"])
    run_ids = set(by_node)

    compared_ids = sorted(observed_ids & run_ids, key=_node_sort_key)
    observed_only = sorted(observed_ids - run_ids, key=_node_sort_key)
    run_only = sorted(run_ids - observed_ids, key=_node_sort_key)
    unjoined_object_ids = [
        object_id for node_id in run_only for object_id in by_node.get(node_id, [])
    ]

    coverage = _empty_coverage()
    coverage["nodes_compared"] = compared_ids
    coverage["observed_nodes_absent_from_run"] = observed_only
    coverage["run_nodes_without_observed"] = run_only
    coverage["unjoined_run_object_ids"] = _bound(unjoined_object_ids)
    coverage["counts"].update(
        {
            "observed_nodes": len(observed_ids),
            "run_nodes": len(run_ids),
            "compared": len(compared_ids),
        }
    )

    if not compared_ids:
        report = not_checkable(
            NO_JOINABLE_NODES,
            observed=", ".join(observed_only) or "-",
            run=", ".join(run_only) or "-",
        )
        report["run_evidence"] = evidence_state
        report["coverage"] = coverage
        report["workbook_warnings"] = warnings
        report["period"] = period
        report["sources"] = sources
        return report

    nodes: list[dict[str, Any]] = []
    proposals: list[dict[str, Any]] = []
    refusals: list[dict[str, Any]] = []
    movements_compared = 0
    ungradable: list[dict[str, Any]] = []
    rejected_aliases: list[str] = []

    for node_id in compared_ids:
        node = observed_side["nodes"][node_id]
        evidence = _observed_side_only(fixes.node_evidence_from_real_counts(node))
        view, rejected = _aliased_run_view(run_result, node_id, movement_ids.get(node_id))
        rejected_aliases.extend(f"{node_id}: {item}" for item in rejected)
        merged = fixes.merge_modelled_from_run_result(evidence, view)

        for movement in merged.get("movements") or []:
            if movement.get("modelled") is None or movement.get("observed") is None:
                ungradable.append(
                    {
                        "node_id": node_id,
                        "movement_id": movement.get("id"),
                        "has_observed": movement.get("observed") is not None,
                        "has_modelled": movement.get("modelled") is not None,
                    }
                )
            else:
                movements_compared += 1

        outcome = fixes.map_node_counts(
            merged,
            targets=targets.get(node_id),
            geh_threshold=geh_threshold,
            registry=registry,
        ).to_dict()

        entry = {
            "node_id": node_id,
            "node_name": node.get("node_name"),
            "observed_total": merged.get("observed_total"),
            "modelled_total": merged.get("modelled_total"),
            "node_total_geh": outcome["evidence"].get("node_total_geh"),
            "outcome": outcome,
        }
        nodes.append(entry)
        (proposals if outcome["outcome"] == fixes.PROPOSAL else refusals).append(entry)

    coverage["ungradable_movements"] = _bound(ungradable)
    coverage["rejected_movement_aliases"] = _bound(rejected_aliases)
    coverage["counts"].update(
        {"movements_compared": movements_compared, "movements_ungradable": len(ungradable)}
    )

    return {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "roadmap_rows": ["2.1"],
        "requirement": "R2.3",
        "status": CHECKED,
        "reason_code": None,
        "message": _message(
            CHECKED,
            compared=len(compared_ids),
            proposals=len(proposals),
            refusals=len(refusals),
        ),
        "geh_threshold": geh_threshold,
        "period": period,
        "sources": sources,
        "run_evidence": evidence_state,
        "coverage": coverage,
        "nodes": nodes,
        "proposals": proposals,
        "refusals": refusals,
        "workbook_warnings": warnings,
        "detail": {},
    }


__all__ = [
    "CHECKED",
    "MOVEMENT_VOLUMES_KEY",
    "MULTI_SEED_VOLUMES_NOT_COMPARABLE",
    "NOT_CHECKABLE",
    "NO_JOINABLE_NODES",
    "NO_OBSERVED_COUNTS",
    "NO_RUN_RESULT",
    "OBSERVATION_NO_MAPPED_SITES",
    "OBSERVATION_SET_NOT_FOUND",
    "OBSERVATION_SET_PERIOD_ABSENT",
    "OBSERVATION_SITE_NODE_MAP_MISSING",
    "OBSERVED_PERIOD_ABSENT",
    "OBSERVED_WORKBOOK_UNREADABLE",
    "RUN_EVIDENCE_ARMED_BUT_EMPTY",
    "RUN_EVIDENCE_EMPTY",
    "RUN_EVIDENCE_KEY_OMITTED",
    "RUN_EVIDENCE_TABLE_ABSENT",
    "RUN_NOT_FOUND",
    "STATUSES",
    "check_run_against_counts",
    "not_checkable",
    "observed_nodes",
    "run_evidence_state",
    "run_nodes",
]
