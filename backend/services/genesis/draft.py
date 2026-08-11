"""The draft network, and the MRWA §5 compliance report that ships with it.

Roadmap row 2.3, Genesis (a), second stage. :mod:`services.genesis.osm` says
what the OSM file contains; this module turns that into the objects Vissim
knows about — **directional links** (lane count + provenance + a polyline in
local metres) and **connectors** at intersections (which approach reaches which
exit, with the turn geometry left-hand traffic implies) — and then grades its
own output against MRWA OMG v2.0 §5.

Three states, and a draft that is generated anyway
--------------------------------------------------
Every §5 rule this generator touches lands in exactly one of:

* ``applied`` — implemented, and this draft honours it. Carries the count.
* ``violated`` — the draft demonstrably does not satisfy it, and the offending
  objects are **named**. A rule the generator cannot honour does not block
  generation: the draft is produced, the violation is listed, and the consumer
  decides. A silent pass would be the only unacceptable outcome.
* ``not_checkable`` — the rule cannot be evaluated offline, with a
  machine-readable reason: :data:`REASON_FIGURE_LOST` (the rule lived in a
  figure the text extraction could not recover — the extract's own inventory
  lists 32 such figures, five of them materially damaging),
  :data:`REASON_DATA_ABSENT_FROM_OSM`, :data:`REASON_OUTSIDE_EMITTED_SUBSET`,
  :data:`REASON_REQUIRES_SITE_OBSERVATION`, :data:`REASON_REQUIRES_VISSIM`.

``not_checkable(figure_lost)`` is never rounded up to ``applied``. Where §5's
actual instruction survives only as a screenshot of a Vissim dialog — Figure 5-1
"Recommended vehicle behaviours" is the canonical case, and only its
left-hand-traffic sentence made it into the text — this report says the rule is
NOT CHECKABLE and names the figure. Guessing the rest of that dialog would be
inventing a client's guideline.

Citations
---------
Same discipline as ``services/dataimport/fixed_time.py``: every ``CIT_*``
constant names the §/Table AND the **doc page** in
``docs/product/reference/mrwa-section5-extract.md`` and quotes it verbatim, so
``test_genesis_osm_draft.py`` §5 can mechanically prove each quotation is really
in the source. Locators name sections and TABLES, never the guideline's in-text
figure labels: the extract records nine measured cross-reference errors where
the body says "Figure N" and the object is "Table N".

Generator assumptions
---------------------
§5 is written for a network coded from survey and aerial imagery. It states no
intersection setback, no node box size and no general default lane width. Where
this generator needs such a number it uses a named constant, flags it as a
generator assumption (:class:`GeneratorAssumption`), and says which §5 text — if
any — the number was taken from. :data:`LANE_WIDTH_M` is the only one with a §5
number behind it, and even that is Table 5-7's *merging example*, not a stated
default. All of them are listed in the report.
"""
from __future__ import annotations

import math
from dataclasses import replace
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from .connectivity import (
    BOUNDARY_BAND_M,
    DANGLING_ALL_MOVEMENTS_U,
    DANGLING_NO_EXIT_AT_JUNCTION,
    DANGLING_RESTRICTED,
    DANGLING_UNATTRIBUTED,
    DANGLING_UNMERGED_NEIGHBOUR,
    DEAD_END_REASON_TO_DANGLING_CAUSE,
    NODE_MERGE_TOLERANCE_M,
    TOLERANCE_BASIS as NODE_MERGE_TOLERANCE_BASIS,
    DeadEnd,
    EXCLUDABLE_ZONE_REASONS,
    IsolatedZone,
    MergeReport,
    dead_ends,
    diagnose_isolated_zones,
    merge_junction_nodes,
    suppressed_by_restriction,
)
from .osm import (
    EXTRACT,
    CIT_S5211_LEFT_HAND,
    CIT_S5212_UNITS,
    SKIP_ISOLATED_ZONE,
    OsmExtract,
    OsmWay,
    left_normal,
)
from .project import (
    MAX_PLANIMETRIC_ERROR_M,
    ExtentCheck,
    LocalTangentPlane,
    NOT_GEOREFERENCED_NOTE,
    origin_for_bounds,
)

# ---------------------------------------------------------------------------
# Citations — §5.1 / §5.3 / §5.6.1.3
# ---------------------------------------------------------------------------

CIT_S512_SOFTWARE_VERSION = (
    f"MRWA OMG v2.0 §5.1.2 Software Version, doc p.179 ({EXTRACT}) — "
    "'This guideline is based on version 20 of Vissim.'"
)

CIT_S5311_COORDINATE_ALIGNMENT = (
    f"MRWA OMG v2.0 §5.3.1.1 Background Image, doc p.191 ({EXTRACT}) — "
    "'Scaling of the background imagery should be undertaken using the project areas' "
    "coordinates and dimensions. It is the modeller's responsibility to ensure that the "
    "aerial imagery coordinate and proposed design coordinate systems are aligned.'"
)

CIT_S5321_QUEUE_CONTAINMENT = (
    f"MRWA OMG v2.0 §5.3.2.1 Upstream Congestion, doc p.192 ({EXTRACT}) — "
    "'Links should be long enough so that all queuing traffic is within the model boundary'"
)

CIT_S533_MINIMISE = (
    f"MRWA OMG v2.0 §5.3.3 Links and Connectors, doc p.193 ({EXTRACT}) — "
    "'The number of links and connectors be minimised. ... Overlapping between links and "
    "connectors be minimised.'"
)

CIT_S533_ONE_CONNECTOR = (
    f"MRWA OMG v2.0 §5.3.3 Links and Connectors, doc p.193 ({EXTRACT}) — "
    "'Lane gains and lane drops (refer to Section 5.3.3.2) should be modelled using one "
    "connector to ensure merging and diverging movements are smoother and occur within the "
    "link. ... This will also minimise the number of alternative paths.'"
)

CIT_S5331_LANE_CHANGE_DISTANCE = (
    f"MRWA OMG v2.0 §5.3.3.1 Connector Route Parameters, doc p.193 ({EXTRACT}) — "
    "'500 meters is recommended for the value of the lane change parameter at the diverging "
    "section, as a starting point.'"
)

CIT_S5331_EMERGENCY_STOP = (
    f"MRWA OMG v2.0 §5.3.3.1 Connector Route Parameters, doc p.193 ({EXTRACT}) — "
    "'Emergency stop position of a turning lane connector should be at least the length of "
    "approach lane's solid line.'"
)

CIT_S5332_MERGING = (
    f"MRWA OMG v2.0 §5.3.3.2 Merging Section Coding, doc p.194 ({EXTRACT}) — "
    "'The lane change distance parameter of the downstream connector should be updated based "
    "on the vehicle lane change behaviour and upstream lane utilisation observed on site. The "
    "predefined behaviour type of merging (refer to Section 5.2.5.1) is selected for the "
    "merging connector.'"
)

CIT_T57_LANE_WIDTH = (
    f"MRWA OMG v2.0 Table 5-7 'Merging section links', doc p.195 ({EXTRACT}) — "
    "'Table 5-7: Merging section links ... | Lane 1 width | 3.5 m | 3.5 m | 3.5 m | 3.5 m | 3.5 m |'"
)

CIT_S5333_DEDICATED_POCKET = (
    f"MRWA OMG v2.0 §5.3.3.3 Diverging Section Coding, doc p.195 ({EXTRACT}) — "
    "'When exit lanes are dedicated to a movement, separated link coding for the pocket lane "
    "is recommended'"
)

CIT_S5334_BUS_AND_HV_LANE = (
    f"MRWA OMG v2.0 §5.3.3.4 Dedicated Bus and Heavy Vehicle Lane, doc p.196 ({EXTRACT}) — "
    "'Where heavy vehicles are observed to only use a specific lane(s), for example corners "
    "with tight radii, lane closures should be used to ensure correct lane use. ... To enable "
    "interaction between buses and general traffic, bus lanes should be coded as one lane of a "
    "link using lane closures rather than as a separate link.'"
)

CIT_S5335_PEDESTRIAN_CROSSINGS = (
    f"MRWA OMG v2.0 §5.3.3.5 Pedestrian Crossings, doc p.196 ({EXTRACT}) — "
    "'Where pedestrian crossings are observed to impact the performance of the network (for "
    "example intersections in the CBD) ... conflict areas between pedestrian and vehicular "
    "links should be defined to simulate the interaction between vehicle and pedestrian "
    "movements.'"
)

CIT_S534_DESIRED_SPEED = (
    f"MRWA OMG v2.0 §5.3.4 Desired Speed Decisions, doc p.197 ({EXTRACT}) — "
    "'Desired speed decision markers should be set where vehicles are observed to change speed. "
    "This is generally at posted speed limit sign locations.'"
)

CIT_S535_REDUCED_SPEED = (
    f"MRWA OMG v2.0 §5.3.5 Reduced Speed Areas, doc p.197 ({EXTRACT}) — "
    "'Reduced speed areas should be set at any location at which the road geometry prevents "
    "traffic from travelling at the posted speed safely (for example, tight bends, all turning "
    "movements or areas of poor visibility).'"
)

CIT_T58_TURN_SPEEDS = (
    f"MRWA OMG v2.0 Table 5-8 'Vehicle reduced speed range', doc p.197 ({EXTRACT}) — "
    "'Table 5-8: Vehicle reduced speed range ... Most left-turn operations and small single "
    "lane roundabouts ... Most right-turn operations and medium-sized roundabouts'"
)

CIT_S5613_MODEL_INPUT_CHECK = (
    f"MRWA OMG v2.0 §5.6.1.3 Model Input Check, doc p.211 ({EXTRACT}) — "
    "'number of lanes / lane width / lane closure / link behaviour type / elevation'"
)


# ---------------------------------------------------------------------------
# Generator constants
# ---------------------------------------------------------------------------

#: Lane width, metres. The ONLY lane width §5 prints is Table 5-7's, where every
#: lane of the worked merging example is 3.5 m (:data:`CIT_T57_LANE_WIDTH`).
#: §5 states no general default, so this is a generator assumption anchored on
#: that table — and it is also ``inpx_parser``'s own fallback width and
#: ``genesis.diff.NOMINAL_LANE_WIDTH_M``, so the three agree by construction.
LANE_WIDTH_M = 3.5

#: How far short of an intersection node a link stops, metres, so a connector
#: has somewhere to live. §5 states NO setback distance — this is a pure
#: generator assumption, listed as one in every report. Clamped to a quarter of
#: the segment length so a short block can never produce a zero-length or
#: reversed link.
INTERSECTION_SETBACK_M = 12.0

#: Half-width of the square node polygon written for each intersection, metres.
#: §5 states no node box size; ``data/practice.inpx`` — the only .inpx exemplar
#: on this machine — uses a 30 m box, which is where this number comes from.
NODE_HALF_SIZE_M = 15.0

#: Below this a link is not worth emitting at all, metres.
MIN_LINK_LENGTH_M = 2.0

#: Heading change magnitude, degrees, at or under which a movement is "through".
THROUGH_ANGLE_DEG = 30.0

#: Heading change magnitude, degrees, over which a movement is a U-turn.
UTURN_ANGLE_DEG = 150.0

#: Coordinates are rounded here, once, so every later stage is trivially
#: deterministic. 1 mm is four orders of magnitude below the projection's own
#: one-metre budget, so the rounding cannot be confused with a real difference.
COORD_DECIMALS = 3


class ComplianceState(str, Enum):
    APPLIED = "applied"
    VIOLATED = "violated"
    NOT_CHECKABLE = "not_checkable"


REASON_FIGURE_LOST = "figure_lost"
REASON_DATA_ABSENT_FROM_OSM = "data_absent_from_osm"
REASON_OUTSIDE_EMITTED_SUBSET = "outside_emitted_subset"
REASON_REQUIRES_SITE_OBSERVATION = "requires_site_observation"
REASON_REQUIRES_VISSIM = "requires_vissim"
REASON_NO_GEOREFERENCE = "no_georeference"
#: Added by roadmap 3.1 Genesis (b) (``services.genesis.signals``). Distinct
#: from ``REASON_DATA_ABSENT_FROM_OSM`` because the missing document is not OSM
#: and never could be: §5.4.2.1's LM plan and §5.4.2.6's operation sheets are
#: site drawings, and a generated draft has no access to either at any quality.
REASON_NO_SIGNAL_PLAN = "no_signal_plan"


TURN_THROUGH = "through"
TURN_LEFT = "left"
TURN_RIGHT = "right"
TURN_U = "u_turn"

#: Which physical lane Vissim's lane index 1 is. MEASURED, not assumed: the
#: 2026-08-06 live check compared each connector-lane endpoint's signed lateral
#: offset from its host link's centreline (positive = LEFT of travel) on two
#: networks Vissim itself saved — 7 links to 0 on the LEFT-hand London
#: roundabout, 11 to 0 on the right-hand Buehl one, with lane 1 sitting at
#: −1.75 m (i.e. half a lane to the RIGHT of centre) on the cleanest two-lane
#: case. Index 1 is the RIGHT-most lane in BOTH driving regimes; indices grow
#: leftwards.
#:
#: v1 wrote lane index 1 for every connector endpoint and said in writing that
#: it could not know what 1 meant. It means the right-most lane, so under
#: left-hand traffic — where the kerb is on the LEFT and the median on the
#: RIGHT — v1 attached every kerb-side (near-side) turn to the MEDIAN lane.
LANE_INDEX_1_IS_RIGHTMOST = True

#: The traffic regulation this generator builds for, and the value written to
#: ``netPara trafReg``. §5.2.1.1 is a left-hand-traffic document
#: (:data:`CIT_S5211_LEFT_HAND`) and every offset in :func:`build_draft` is
#: derived from it. MEASURED consequence of omitting it: a network with
#: left-hand geometry opens as RIGHT-hand traffic, because the attribute decides
#: the regime and the geometry does not.
TRAFFIC_REGULATION = "LEFTHANDTRAFFIC"

#: Table 5-8 (doc p.197), the two rows whose "Typical example" column names an
#: ordinary turning movement, keyed by the turn type this generator derives.
#: Carried as ADVISORY data only — this emission writes no reduced speed areas
#: (see the §5.3.5 report item), so nothing here is applied to the .inpx.
TABLE_5_8_TURN_ADVISORY: dict[str, dict[str, object]] = {
    TURN_LEFT: {
        "turn_type": "Tight turn",
        "radius_m": 15.0,
        "typical_example": "Most left-turn operations and small single lane roundabouts",
        "short_desired_speed_kmh": 25,
        "short_range_kmh": (25, 30),
        "hv_desired_speed_kmh": 12,
        "hv_range_kmh": (12, 15),
    },
    TURN_RIGHT: {
        "turn_type": "Moderate turn",
        "radius_m": 20.0,
        "typical_example": "Most right-turn operations and medium-sized roundabouts",
        "short_desired_speed_kmh": 30,
        "short_range_kmh": (30, 35),
        "hv_desired_speed_kmh": 15,
        "hv_range_kmh": (15, 20),
    },
}


# ---------------------------------------------------------------------------
# i18n. ko primary; unknown language codes fall back to Korean, the same
# contract as ``services.agent.planner.pick_language_text``.
# ---------------------------------------------------------------------------

_STATE_LABEL: dict[str, dict[str, str]] = {
    ComplianceState.APPLIED.value: {
        "ko": "적용됨",
        "en": "applied",
        "ja": "適用",
    },
    ComplianceState.VIOLATED.value: {
        "ko": "위반(명시)",
        "en": "violated (named)",
        "ja": "違反(明示)",
    },
    ComplianceState.NOT_CHECKABLE.value: {
        "ko": "확인 불가",
        "en": "NOT CHECKABLE",
        "ja": "確認不可",
    },
}

_REASON_LABEL: dict[str, dict[str, str]] = {
    REASON_FIGURE_LOST: {
        "ko": "규칙이 그림에만 있었고 텍스트 추출에서 그림이 유실됨 — 추정하지 않음",
        "en": "the rule lived only in a figure, and the figure was lost in text extraction — not guessed",
        "ja": "規則が図のみに存在し、テキスト抽出で図が失われた — 推測しない",
    },
    REASON_DATA_ABSENT_FROM_OSM: {
        "ko": "판정에 필요한 값이 OSM 데이터에 없음",
        "en": "the value the rule needs is not present in OSM data",
        "ja": "判定に必要な値がOSMデータに存在しない",
    },
    REASON_OUTSIDE_EMITTED_SUBSET: {
        "ko": "이 초안이 쓰지 않는 속성/객체 (기하 전용 범위)",
        "en": "an attribute or object this draft does not write (geometry-only scope)",
        "ja": "この草案が書き出さない属性/オブジェクト (幾何のみの範囲)",
    },
    REASON_REQUIRES_SITE_OBSERVATION: {
        "ko": "규칙 자체가 현장 관측을 전제로 함",
        "en": "the rule is conditioned on on-site observation",
        "ja": "規則自体が現地観測を前提とする",
    },
    REASON_REQUIRES_VISSIM: {
        "ko": "실제 Vissim에서 열어야 확인 가능 (이 장비에는 라이선스 없음)",
        "en": "only checkable by opening the file in a real Vissim (no licence on this machine)",
        "ja": "実際のVissimで開かないと確認できない (この端末にライセンスなし)",
    },
    REASON_NO_SIGNAL_PLAN: {
        "ko": "규칙이 현장 신호도면(LM 도면·운영표)을 전제로 함 — 생성 초안에는 없음",
        "en": "the rule presumes a site signal plan (LM plan / operation sheet), which a "
        "generated draft does not have",
        "ja": "規則が現地の信号図面(LM図面・運用表)を前提とする — 生成草案には存在しない",
    },
    REASON_NO_GEOREFERENCE: {
        "ko": "이 초안 좌표에는 측지 기준이 없음 — 상대 기하 전용",
        "en": "these coordinates carry no datum — relative geometry only",
        "ja": "この草案の座標に測地基準はない — 相対幾何のみ",
    },
}

_RULE_TITLE: dict[str, dict[str, str]] = {
    "5.1.2-software-version": {
        "ko": "§5.1.2 소프트웨어 버전 (Vissim 20 기준)",
        "en": "§5.1.2 software version (guideline is written for Vissim 20)",
        "ja": "§5.1.2 ソフトウェアバージョン (Vissim 20 前提)",
    },
    "5.2.1.1-left-hand-traffic": {
        "ko": "§5.2.1.1 좌측통행 — 방향별 링크의 좌/우 배치",
        "en": "§5.2.1.1 left-hand traffic — which side each directional link sits on",
        "ja": "§5.2.1.1 左側通行 — 方向別リンクの配置側",
    },
    "5.2.1.1-network-settings-figure": {
        "ko": "§5.2.1.1 Network Settings 나머지 항목 (Figure 5-1)",
        "en": "§5.2.1.1 the rest of the Network Settings dialog (Figure 5-1)",
        "ja": "§5.2.1.1 Network Settings の残り項目 (Figure 5-1)",
    },
    "5.2.1.2-metric-units": {
        "ko": "§5.2.1.2 미터법 단위",
        "en": "§5.2.1.2 metric units",
        "ja": "§5.2.1.2 メートル法",
    },
    "5.2.1.2-units-tab-figure": {
        "ko": "§5.2.1.2 Units 탭 나머지 항목 (Figure 5-2)",
        "en": "§5.2.1.2 the rest of the Units tab (Figure 5-2)",
        "ja": "§5.2.1.2 Units タブの残り項目 (Figure 5-2)",
    },
    "5.3.1.1-coordinate-alignment": {
        "ko": "§5.3.1.1 항공사진/설계 좌표계 정합",
        "en": "§5.3.1.1 aerial imagery and design coordinate alignment",
        "ja": "§5.3.1.1 航空写真と設計座標系の整合",
    },
    "5.3.2.1-queue-containment": {
        "ko": "§5.3.2.1 대기행렬이 경계 안에 들어오도록 링크 길이 확보",
        "en": "§5.3.2.1 links long enough to contain the queue",
        "ja": "§5.3.2.1 待ち行列が境界内に収まるリンク長",
    },
    "5.3.3-minimise-links-and-connectors": {
        "ko": "§5.3.3 링크·커넥터 수 최소화",
        "en": "§5.3.3 minimise the number of links and connectors",
        "ja": "§5.3.3 リンク・コネクタ数の最小化",
    },
    "5.3.3-isolated-zone-excluded": {
        "ko": "§5.3.3 도달 불가 구역 제외",
        "en": "§5.3.3 unreachable pockets excluded from the network",
        "ja": "§5.3.3 到達不能ゾーンの除外",
    },
    "5.3.3-lane-gain-drop-one-connector": {
        "ko": "§5.3.3 차로 증감은 커넥터 1개로",
        "en": "§5.3.3 lane gains and drops use one connector",
        "ja": "§5.3.3 車線の増減はコネクタ1本で",
    },
    "5.3.3-lane-width": {
        "ko": "Table 5-7 차로폭 3.5 m",
        "en": "Table 5-7 lane width 3.5 m",
        "ja": "Table 5-7 車線幅 3.5 m",
    },
    "5.3.3.1-lane-change-distance": {
        "ko": "§5.3.3.1 분류부 커넥터 차로변경 거리 500 m",
        "en": "§5.3.3.1 500 m lane-change distance at diverging connectors",
        "ja": "§5.3.3.1 分流部コネクタの車線変更距離 500 m",
    },
    "5.3.3.1-emergency-stop-position": {
        "ko": "§5.3.3.1 회전차로 커넥터의 emergency stop 위치",
        "en": "§5.3.3.1 emergency stop position on turning-lane connectors",
        "ja": "§5.3.3.1 右左折コネクタの emergency stop 位置",
    },
    "5.3.3.2-merging-behaviour-type": {
        "ko": "§5.3.3.2 합류부 거동유형·차로변경 거리",
        "en": "§5.3.3.2 merging behaviour type and lane-change distance",
        "ja": "§5.3.3.2 合流部の挙動タイプと車線変更距離",
    },
    "5.3.3.2-merging-layout-figure": {
        "ko": "§5.3.3.2 합류부 링크 배치 (Figure 5-11)",
        "en": "§5.3.3.2 merging-section link layout (Figure 5-11)",
        "ja": "§5.3.3.2 合流部のリンク配置 (Figure 5-11)",
    },
    "5.3.3.3-dedicated-pocket-lane": {
        "ko": "§5.3.3.3 전용 회전차로는 별도 링크(포켓)로 코딩",
        "en": "§5.3.3.3 a dedicated turn lane is coded as a separate pocket link",
        "ja": "§5.3.3.3 専用右左折車線は別リンク(ポケット)で",
    },
    "5.3.3.4-bus-and-hv-lane": {
        "ko": "§5.3.3.4 버스/대형차 전용차로는 lane closure로",
        "en": "§5.3.3.4 bus and heavy-vehicle lanes use lane closures",
        "ja": "§5.3.3.4 バス/大型車専用車線は lane closure で",
    },
    "5.3.3.5-pedestrian-crossings": {
        "ko": "§5.3.3.5 보행자 횡단 (pedestrian area + conflict area)",
        "en": "§5.3.3.5 pedestrian crossings (pedestrian area + conflict areas)",
        "ja": "§5.3.3.5 歩行者横断 (pedestrian area + conflict area)",
    },
    "5.3.4-desired-speed-decisions": {
        "ko": "§5.3.4 희망속도 결정지점",
        "en": "§5.3.4 desired speed decisions",
        "ja": "§5.3.4 希望速度決定地点",
    },
    "5.3.5-reduced-speed-areas": {
        "ko": "§5.3.5 감속구간 (내보내지 않음)",
        "en": "§5.3.5 reduced speed areas (not emitted)",
        "ja": "§5.3.5 減速区間 (出力しない)",
    },
    "5.3.5-reduced-speed-areas-table": {
        "ko": "Table 5-8 회전유형별 권장 감속속도 (참고값)",
        "en": "Table 5-8 per-turn-type reduced speeds (advisory)",
        "ja": "Table 5-8 回転種別ごとの推奨減速速度 (参考値)",
    },
    "5.6.1.3-model-input-check": {
        "ko": "§5.6.1.3 모델 입력 점검 12항목",
        "en": "§5.6.1.3 the 12-item model input check",
        "ja": "§5.6.1.3 モデル入力チェック12項目",
    },
    # Roadmap 3.1 Genesis (b) — §5.4.2, produced by ``services.genesis.signals``
    # and appended to this report. Titles live here so one renderer and one
    # title table serve the whole draft, signalised or not.
    "5.4.2-control-type-agreed": {
        "ko": "§5.4.2 신호 제어 방식은 Main Roads와 협의",
        "en": "§5.4.2 the signal control type is agreed with Main Roads",
        "ja": "§5.4.2 信号制御方式は Main Roads と協議",
    },
    "5.4.2-generation-warnings": {
        "ko": "§5.4.2 신호 생성 중 발생한 경고 (명시)",
        "en": "§5.4.2 warnings raised while generating the signals (named)",
        "ja": "§5.4.2 信号生成中に発生した警告 (明示)",
    },
    "5.4.2.1-signal-groups-match-scats": {
        "ko": "§5.4.2.1 신호그룹은 LM 도면·SCATS 번호와 일치",
        "en": "§5.4.2.1 signal groups follow the LM plan and SCATS numbering",
        "ja": "§5.4.2.1 信号グループは LM図面・SCATS番号と一致",
    },
    "5.4.2.1-timings-input-unchanged": {
        "ko": "§5.4.2.1 최소녹색·황색·전적색을 제어기 값 그대로 입력",
        "en": "§5.4.2.1 minimum greens and yellow + all reds input unchanged",
        "ja": "§5.4.2.1 最小緑・黄・全赤を制御機の値のまま入力",
    },
    "5.4.2.2-signal-heads-on-links": {
        "ko": "§5.4.2.2 신호등은 링크(접근로) 위 차로마다 배치",
        "en": "§5.4.2.2 signal heads on links, one per approach lane",
        "ja": "§5.4.2.2 信号機はリンク(流入路)上に車線ごとに配置",
    },
    "5.4.2.2-partially-controlled-turns": {
        "ko": "§5.4.2.2 부분 제어 회전은 커넥터 위 신호등",
        "en": "§5.4.2.2 partially controlled turns take heads on connectors",
        "ja": "§5.4.2.2 部分制御の回転はコネクタ上に信号機",
    },
    "5.4.2.3-fixed-time-from-history": {
        "ko": "§5.4.2.3 SCATS 이력에서 평균 현시시간 산출 (고정주기)",
        "en": "§5.4.2.3 average phase lengths from SCATS history (fixed time)",
        "ja": "§5.4.2.3 SCATS履歴から平均現示時間を算出 (固定時間)",
    },
    "5.4.2.3-pedestrian-signals": {
        "ko": "§5.4.2.3 보행자 신호 (내보내지 않음)",
        "en": "§5.4.2.3 pedestrian signals (not emitted)",
        "ja": "§5.4.2.3 歩行者信号 (出力しない)",
    },
    "5.4.2.6-detector-fallback-geometry": {
        "ko": "§5.4.2.6 검지기 대체 기하 (신호등 2.0 m 뒤 · 길이 4.5 m)",
        "en": "§5.4.2.6 detector fallback geometry (2.0 m back, 4.5 m long)",
        "ja": "§5.4.2.6 検知器の代替寸法 (信号機の2.0m手前・長さ4.5m)",
    },
    "5.4.2.6-detectors-reflect-plans": {
        "ko": "§5.4.2.6 검지기는 실제/설계 신호도면을 반영",
        "en": "§5.4.2.6 detectors reflect existing or design signal plans",
        "ja": "§5.4.2.6 検知器は既存/設計の信号図面を反映",
    },
    # Roadmap 3.2 Genesis (c) — §5.2.2 / §5.2.3 / §5.5 / §5.6.1.3, produced by
    # ``services.genesis.demand`` and appended to this report. Same reason the
    # 3.1 block sits here: one renderer and one title table serve the whole
    # draft, with or without a demand layer.
    "5.2.2-time-period-settings": {
        "ko": "§5.2.2 해석 기간 · 웜업/쿨다운 설정",
        "en": "§5.2.2 assessment period, warm-up and cool-down settings",
        "ja": "§5.2.2 解析期間・ウォームアップ/クールダウン設定",
    },
    "5.2.3-vehicle-types-declared": {
        "ko": "§5.2.3 차종 정의 (Austroads 분류)",
        "en": "§5.2.3 vehicle types defined (Austroads classification)",
        "ja": "§5.2.3 車種定義 (Austroads 分類)",
    },
    # The §5.2.3 vehicle-type layer titles are produced by
    # ``services.genesis.fleet`` and appended to this report — same one-renderer,
    # one-title-table reason as the demand and signals blocks. ``fleet`` DECLARES
    # the types the demand layer above only referenced.
    "5.2.3-vehicle-types-defined": {
        "ko": "§5.2.3 차종 선언 (Table 5-1 · 참조된 차종을 <vehicleTypes>로)",
        "en": "§5.2.3 vehicle types declared (Table 5-1, referenced types in <vehicleTypes>)",
        "ja": "§5.2.3 車種宣言 (Table 5-1 · 参照車種を <vehicleTypes> に)",
    },
    "5.2.3.1-2d3d-model": {
        "ko": "§5.2.3.1 2D/3D 모델 (Figure 5-4 유실 · Vissim 기본 모델 복사)",
        "en": "§5.2.3.1 2D/3D model (Figure 5-4 lost; a Vissim-default model per category)",
        "ja": "§5.2.3.1 2D/3D モデル (Figure 5-4 消失 · Vissim 既定モデルを複製)",
    },
    "5.2.3.2-heavy-desired-acceleration": {
        "ko": "§5.2.3.2 중차량 희망가속도 (Table 5-2) · 그 외는 기본값",
        "en": "§5.2.3.2 heavy desired acceleration (Table 5-2); other attributes default",
        "ja": "§5.2.3.2 重車両の希望加速度 (Table 5-2) · その他は既定値",
    },
    "5.2.3.3-power-weight": {
        "ko": "§5.2.3.3 출력·중량 분포 (Table 5-3 · 보정 단계로 이연)",
        "en": "§5.2.3.3 power and weight distributions (Table 5-3; deferred to calibration)",
        "ja": "§5.2.3.3 出力・重量分布 (Table 5-3 · 校正段階へ)",
    },
    "5.2.3.4-colour-distributions": {
        "ko": "§5.2.3.4 색상 분포 (Table 5-4 · 버스는 초록)",
        "en": "§5.2.3.4 colour distributions (Table 5-4; buses green)",
        "ja": "§5.2.3.4 カラー分布 (Table 5-4 · バスは緑)",
    },
    "5.2.4-vehicle-classes": {
        "ko": "§5.2.4 차종 클래스 (Table 5-5 · 미참조로 미출력)",
        "en": "§5.2.4 vehicle classes (Table 5-5; not referenced, not emitted)",
        "ja": "§5.2.4 車種クラス (Table 5-5 · 未参照のため未出力)",
    },
    "5.2.5.3-desired-speed-profile": {
        "ko": "§5.2.5.3 희망속도 프로파일 (≤60 km/h 기본 선형)",
        "en": "§5.2.5.3 desired-speed profile (default linear ≤60 km/h)",
        "ja": "§5.2.5.3 希望速度プロファイル (≤60 km/h 既定線形)",
    },
    "5.5-demand-generation-warnings": {
        "ko": "§5.5 수요 생성 중 발생한 경고 (명시)",
        "en": "§5.5 warnings raised while generating the demand (named)",
        "ja": "§5.5 需要生成中に発生した警告 (明示)",
    },
    "5.5.1-composition-from-classified-counts": {
        "ko": "§5.5.1 차량 구성은 분류 교통량 조사에서 산출",
        "en": "§5.5.1 vehicle composition determined from classified counts",
        "ja": "§5.5.1 車両構成は分類済み交通量調査から算出",
    },
    "5.5.4.1-hourly-volume-recalculation": {
        "ko": "§5.5.4.1 15분 집계값을 시간당 교통량으로 환산",
        "en": "§5.5.4.1 15-minute flows recalculated to hourly volumes",
        "ja": "§5.5.4.1 15分集計値を時間当たり交通量に換算",
    },
    "5.5.4.1-exact-volumes": {
        "ko": "§5.5.4.1 정확 볼륨(exact) 사용 권고",
        "en": "§5.5.4.1 exact volumes recommended over stochastic",
        "ja": "§5.5.4.1 exact ボリュームの使用推奨",
    },
    "5.5.4.1-fifteen-minute-profiles": {
        "ko": "§5.5.4.1 15분 수요 프로파일 (한 구간만 출력)",
        "en": "§5.5.4.1 15-minute demand profiles (one interval written)",
        "ja": "§5.5.4.1 15分需要プロファイル (1区間のみ出力)",
    },
    "5.5.4.2-routing-decision-position": {
        "ko": "§5.5.4.2 경로 결정 표지 위치 (차로 변경 시작점)",
        "en": "§5.5.4.2 routing decision marker position (where lane changes begin)",
        "ja": "§5.5.4.2 経路決定マーカー位置 (車線変更開始点)",
    },
    "5.5.4.2-combine-static-routing-decisions": {
        "ko": "§5.5.4.2 정적 경로 결정 결합(combine) 활성화",
        "en": "§5.5.4.2 combine static routing decisions activated",
        "ja": "§5.5.4.2 静的経路決定の結合(combine)を有効化",
    },
    "5.5.4.2-routes-per-vehicle-class": {
        "ko": "§5.5.4.2 차종별 경로 (전 차종 공통으로 출력)",
        "en": "§5.5.4.2 routes for individual vehicle classes (written for all types)",
        "ja": "§5.5.4.2 車種別経路 (全車種共通で出力)",
    },
    "5.6.1.3-demand-input-check": {
        "ko": "§5.6.1.3 입력 점검 — 교통 수요 · 차량 경로",
        "en": "§5.6.1.3 model input check — traffic demands and vehicle routes",
        "ja": "§5.6.1.3 入力チェック — 交通需要・車両経路",
    },
}

_REPORT_TEXT: dict[str, dict[str, str]] = {
    "heading": {
        "ko": "MRWA OMG v2.0 §5 준수 리포트 — Genesis (a) 기하 초안",
        "en": "MRWA OMG v2.0 §5 compliance report — Genesis (a) geometry draft",
        "ja": "MRWA OMG v2.0 §5 準拠レポート — Genesis (a) 幾何草案",
    },
    "summary": {
        "ko": "적용 {applied}건 / 위반 {violated}건 / 확인 불가 {not_checkable}건",
        "en": "{applied} applied / {violated} violated / {not_checkable} NOT CHECKABLE",
        "ja": "適用 {applied}件 / 違反 {violated}件 / 確認不可 {not_checkable}件",
    },
    "network": {
        "ko": "네트워크",
        "en": "Network",
        "ja": "ネットワーク",
    },
    "projection": {
        "ko": "투영 (절대 좌표 주장 없음)",
        "en": "Projection (no absolute-position claim)",
        "ja": "投影 (絶対座標の主張なし)",
    },
    "assumptions": {
        "ko": "생성기 가정 (§5에 값이 없어 이 생성기가 정한 수치)",
        "en": "Generator assumptions (numbers §5 does not state, chosen by this generator)",
        "ja": "生成器の仮定 (§5に値がなく本生成器が決めた数値)",
    },
    "skipped": {
        "ko": "건너뛴 way (사유별) — 조용히 버린 것은 없음",
        "en": "Skipped ways by reason — nothing was dropped silently",
        "ja": "スキップした way (理由別) — 黙って捨てたものはない",
    },
    "assumed_lanes": {
        "ko": "차로수를 태그가 아니라 생성기 기본값으로 채운 방향 링크: {count}개",
        "en": "directional links whose lane count came from a generator default, not a tag: {count}",
        "ja": "車線数をタグではなく生成器の既定値で埋めた方向リンク: {count}件",
    },
    "connectivity": {
        "ko": "연결성 (같은 교차로 합치기 · 막다른 길 U턴 · 회전 금지)",
        "en": "Connectivity (junction merge, cul-de-sac U-turns, turn bans)",
        "ja": "接続性 (同一交差点の統合・行き止まりUターン・右左折禁止)",
    },
    "connectivity_dangling": {
        "ko": (
            "커넥터가 나가지 않는 링크 끝(차량이 조용히 네트워크를 이탈하는 지점) — "
            "각 항목은 사유와 함께 genesis.json 에 이름이 남습니다"
        ),
        "en": (
            "link ends no connector leaves (where a vehicle silently departs the network) "
            "— each one is named with its cause in the genesis.json"
        ),
        "ja": (
            "コネクタが出ていないリンク端(車両が黙ってネットワークを離脱する地点) — "
            "各項目は理由とともに genesis.json に記録されます"
        ),
    },
    "connectivity_arterial_warning": {
        "ko": (
            "간선도로급 way가 막다른 길로 판정되었습니다. U턴이 가능한 경우에는 생성했지만"
            "(차량이 갇히지 않도록), 간선도로가 막다른 길인 경우는 실제 회차로보다 지도 단절인 "
            "경우가 많으므로 각 항목의 사유와 함께 검토가 필요합니다"
        ),
        "en": (
            "arterial-class ways were read as dead ends. Where a U-turn was possible it IS "
            "emitted (so vehicles are not stranded), but an arterial that dead-ends is more "
            "often a break in the map than a real turning head — review these, each with its "
            "own reason"
        ),
        "ja": (
            "幹線道路級の way が行き止まりと判定されました。U ターンが可能な場合は生成しています"
            "(車両が閉じ込められないように)が、幹線道路の行き止まりは実際の回転場所より地図の断絶"
            "であることが多く、各項目の理由とあわせて確認が必要です"
        ),
    },
    "rules": {
        "ko": "§5 규칙",
        "en": "§5 rules",
        "ja": "§5 規則",
    },
    "subjects": {
        "ko": "대상",
        "en": "subjects",
        "ja": "対象",
    },
    "citation": {
        "ko": "근거",
        "en": "citation",
        "ja": "根拠",
    },
    "not_opened_by_vissim": {
        "ko": (
            "이 파일은 이 저장소의 파서(inpx_parser)와 구조 diff(genesis.diff)로 왕복 검증되었을 뿐, "
            "실제 Vissim으로 열어본 적이 없습니다. 라이선스 장비에서의 open 스모크 테스트는 별도 후속 작업입니다."
        ),
        "en": (
            "This file has been round-tripped through THIS repo's parser (inpx_parser) and "
            "structural diff (genesis.diff) only. It has NOT been opened by a real Vissim; that "
            "smoke test on the licensed box is a named follow-up."
        ),
        "ja": (
            "このファイルは本リポジトリのパーサ(inpx_parser)と構造diff(genesis.diff)で往復検証しただけで、"
            "実際のVissimで開いたことはありません。ライセンス端末でのopenスモークテストは別途の後続作業です。"
        ),
    },
}


def pick_text(table: dict[str, str], language: str) -> str:
    """ko primary; an unknown code falls back to Korean."""
    return table.get(language, table["ko"])


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class GeneratorAssumption(BaseModel):
    """A number §5 does not state, that this generator had to choose."""

    model_config = ConfigDict(frozen=True)

    id: str
    value: float
    unit: str = "m"
    basis: str
    citation: str | None = None


class ComplianceItem(BaseModel):
    """One §5 rule, in one of the three states."""

    model_config = ConfigDict(frozen=True)

    rule_id: str
    section: str
    doc_page: int
    state: ComplianceState
    citation: str
    reason: str | None = None
    instances: int = 0
    subjects: list[str] = Field(default_factory=list)
    evidence: dict = Field(default_factory=dict)
    detail: dict[str, str] = Field(default_factory=dict)

    def title(self, language: str) -> str:
        return pick_text(_RULE_TITLE.get(self.rule_id, {"ko": self.rule_id}), language)

    def render(self, language: str) -> str:
        state_label = pick_text(_STATE_LABEL[self.state.value], language)
        head = f"- [{state_label}] {self.title(language)} (doc p.{self.doc_page})"
        lines = [head]
        if self.reason:
            lines.append(f"    - {pick_text(_REASON_LABEL[self.reason], language)}")
        if self.detail:
            lines.append(f"    - {pick_text(self.detail, language)}")
        if self.subjects:
            shown = ", ".join(self.subjects[:12])
            more = f" (+{len(self.subjects) - 12})" if len(self.subjects) > 12 else ""
            lines.append(f"    - {pick_text(_REPORT_TEXT['subjects'], language)}: {shown}{more}")
        if self.evidence:
            rendered = ", ".join(f"{k}={v}" for k, v in sorted(self.evidence.items()))
            lines.append(f"    - {rendered}")
        lines.append(f"    - {pick_text(_REPORT_TEXT['citation'], language)}: {self.citation}")
        return "\n".join(lines)


class ComplianceReport(BaseModel):
    """The three-state grade of one draft against §5."""

    items: list[ComplianceItem] = Field(default_factory=list)

    def by_state(self, state: ComplianceState) -> list[ComplianceItem]:
        return [item for item in self.items if item.state is state]

    @property
    def applied(self) -> list[ComplianceItem]:
        return self.by_state(ComplianceState.APPLIED)

    @property
    def violated(self) -> list[ComplianceItem]:
        return self.by_state(ComplianceState.VIOLATED)

    @property
    def not_checkable(self) -> list[ComplianceItem]:
        return self.by_state(ComplianceState.NOT_CHECKABLE)

    def counts(self) -> dict[str, int]:
        return {
            ComplianceState.APPLIED.value: len(self.applied),
            ComplianceState.VIOLATED.value: len(self.violated),
            ComplianceState.NOT_CHECKABLE.value: len(self.not_checkable),
        }


class DraftLink(BaseModel):
    """One directional carriageway — the thing that becomes a Vissim link."""

    model_config = ConfigDict(frozen=True)

    id: int
    name: str
    osm_way_id: int
    segment_index: int
    forward: bool
    lanes: int
    lane_width_m: float
    points: list[tuple[float, float]]
    lane_provenance: dict
    highway: str
    from_node_osm_id: int
    to_node_osm_id: int
    side_offset_m: float
    setback_clamped: bool = False

    @property
    def segment_key(self) -> tuple[int, int]:
        return self.osm_way_id, self.segment_index

    @property
    def length_m(self) -> float:
        return _polyline_length(self.points)


class DraftConnector(BaseModel):
    """One movement through one intersection."""

    model_config = ConfigDict(frozen=True)

    id: int
    name: str
    node_osm_id: int
    node_id: int
    from_link: int
    to_link: int
    from_pos_m: float
    to_pos_m: float
    lanes: int
    lane_width_m: float
    points: list[tuple[float, float]]
    turn: str
    heading_change_deg: float
    lane_change: str  # "same" | "gain" | "drop"
    #: Vissim lane indices on the approach and on the exit, one entry per
    #: connector lane, in connector-lane order. Derived by
    #: :func:`connector_lane_pairs` from the MEASURED convention that index 1 is
    #: the right-most lane; see that function for the hand derivation. Both
    #: lists always have :attr:`lanes` entries.
    from_lanes: list[int] = Field(default_factory=list)
    to_lanes: list[int] = Field(default_factory=list)
    table_5_8_advisory: dict | None = None


class DraftNode(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: int
    name: str
    osm_id: int
    x: float
    y: float
    polygon: list[tuple[float, float]]


class DraftNetwork(BaseModel):
    """A draft network, its provenance, and its §5 grade.

    ``refused`` drafts carry the reasons and **no** links/connectors — an empty
    network and a refused one are different answers, the same distinction
    ``genesis.diff.load_topology`` makes.
    """

    name: str
    source_osm: str
    #: The driving regime this draft's geometry was built for, and the value the
    #: emitter writes to ``netPara trafReg``. The emitter reads it here rather
    #: than hard-coding it, so the file can never disagree with the geometry.
    traffic_regulation: str = TRAFFIC_REGULATION
    refused: bool = False
    refusals: list[str] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)
    links: list[DraftLink] = Field(default_factory=list)
    connectors: list[DraftConnector] = Field(default_factory=list)
    nodes: list[DraftNode] = Field(default_factory=list)
    assumptions: list[GeneratorAssumption] = Field(default_factory=list)
    skipped_ways: list[dict] = Field(default_factory=list)
    compliance: ComplianceReport = Field(default_factory=ComplianceReport)

    def link_by_id(self, link_id: int) -> DraftLink | None:
        for link in self.links:
            if link.id == link_id:
                return link
        return None

    def render_compliance(self, language: str = "ko") -> str:
        text = _REPORT_TEXT
        counts = self.compliance.counts()
        out: list[str] = [
            f"# {pick_text(text['heading'], language)}",
            "",
            f"**{self.name}** — {self.source_osm}",
            "",
        ]
        if self.refused:
            out.append("REFUSED:")
            out.extend(f"- {reason}" for reason in self.refusals)
            return "\n".join(out) + "\n"

        out.append(pick_text(text["summary"], language).format(**counts))
        out.append("")
        out.append(f"## {pick_text(text['network'], language)}")
        out.append(
            f"- links={len(self.links)}, connectors={len(self.connectors)}, "
            f"nodes={len(self.nodes)}"
        )
        out.append(
            "- "
            + pick_text(text["assumed_lanes"], language).format(
                count=sum(1 for link in self.links if link.lane_provenance.get("assumed"))
            )
        )
        out.append("")
        out.append(f"## {pick_text(text['projection'], language)}")
        for key in (
            "kind",
            "origin_lat_deg",
            "origin_lon_deg",
            "validity_radius_m",
            "half_extent_m",
            "planimetric_error_bound_m",
            "binding_term",
            "georeferenced",
        ):
            if key in self.metadata.get("projection", {}):
                out.append(f"- {key}: {self.metadata['projection'][key]}")
            elif key in self.metadata:
                out.append(f"- {key}: {self.metadata[key]}")
        out.append(f"- {NOT_GEOREFERENCED_NOTE}")
        out.append("")
        out.append(f"## {pick_text(text['assumptions'], language)}")
        for assumption in self.assumptions:
            citation = f" — {assumption.citation}" if assumption.citation else ""
            out.append(
                f"- {assumption.id} = {assumption.value} {assumption.unit}: "
                f"{assumption.basis}{citation}"
            )
        out.append("")
        out.append(f"## {pick_text(text['connectivity'], language)}")
        connectivity = self.metadata.get("connectivity", {})
        merge = connectivity.get("node_merge", {})
        out.append(
            f"- node merge: {len(merge.get('clusters', []))} junction(s) joined from "
            f"{merge.get('nodes_merged', 0)} OSM node(s), "
            f"{len(merge.get('refusals', []))} refused"
        )
        # BY CODE, not just a count. A refusal a reader cannot find is the same
        # to them as a silent merge, and "two roads end within one carriageway of
        # each other and were NOT joined" is a line a reviewer can act on.
        refusals_by_code: dict[str, int] = {}
        for refusal in merge.get("refusals", []):
            code = refusal.get("code", "")
            refusals_by_code[code] = refusals_by_code.get(code, 0) + 1
        for code, count in sorted(refusals_by_code.items()):
            out.append(f"  - refused / {code}: {count}")
        out.append(f"- tolerance: {merge.get('basis', '')}")
        for reason, count in sorted(connectivity.get("dead_ends_by_reason", {}).items()):
            out.append(f"- dead end / {reason}: {count}")
        out.append(
            f"- cul-de-sac U-turn connectors emitted: "
            f"{connectivity.get('u_turn_connectors_emitted', 0)}"
        )
        stranded = connectivity.get("stranded_approach_u_turns", [])
        if stranded:
            shown = "; ".join(stranded[:6])
            more = f" (+{len(stranded) - 6})" if len(stranded) > 6 else ""
            out.append(
                f"- last-resort U-turns on approaches with no other legal movement: "
                f"{len(stranded)} — {shown}{more}"
            )
        arterials = connectivity.get("arterial_dead_ends_named", [])
        if arterials:
            shown = "; ".join(arterials[:12])
            more = f" (+{len(arterials) - 12})" if len(arterials) > 12 else ""
            out.append(
                f"- {pick_text(text['connectivity_arterial_warning'], language)}: "
                f"{shown}{more}"
            )
        dangling_by_cause = connectivity.get("dangling_approaches_by_cause", {})
        out.append(
            f"- {pick_text(text['connectivity_dangling'], language)}: "
            f"{len(connectivity.get('dangling_approaches', []))}"
        )
        for cause, count in sorted(dangling_by_cause.items()):
            out.append(f"  - {cause}: {count}")
        inert = connectivity.get("osm_turn_restrictions_inert", [])
        out.append(
            f"- OSM turn restrictions parsed: "
            f"{connectivity.get('osm_turn_restrictions_parsed', 0)}, of which ENFORCED "
            f"(a movement was actually removed): "
            f"{connectivity.get('osm_turn_restrictions_enforced', 0)}"
            + (
                f", inert (parsed, suppressed nothing — relation(s) {inert}): {len(inert)}"
                if inert
                else ""
            )
            + f"; movements suppressed: "
            f"{len(connectivity.get('movements_suppressed_by_restriction', []))}; "
            f"restrictions this generator could not apply: "
            f"{len(connectivity.get('osm_turn_restrictions_refused', []))}"
        )
        out.append("")
        out.append(f"## {pick_text(text['skipped'], language)}")
        if self.skipped_ways:
            counts_by_reason: dict[str, int] = {}
            for skip in self.skipped_ways:
                counts_by_reason[skip["reason"]] = counts_by_reason.get(skip["reason"], 0) + 1
            for reason, count in sorted(counts_by_reason.items()):
                ids = [str(s["way_id"]) for s in self.skipped_ways if s["reason"] == reason]
                shown = ", ".join(ids[:12]) + (f" (+{len(ids) - 12})" if len(ids) > 12 else "")
                out.append(f"- {reason}: {count} — way {shown}")
        else:
            out.append("- (0)")
        out.append("")
        out.append(f"## {pick_text(text['rules'], language)}")
        for item in self.compliance.items:
            out.append(item.render(language))
        out.append("")
        out.append(f"> {pick_text(text['not_opened_by_vissim'], language)}")
        return "\n".join(out) + "\n"


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------


def _polyline_length(points: list[tuple[float, float]]) -> float:
    total = 0.0
    for i in range(1, len(points)):
        total += math.hypot(points[i][0] - points[i - 1][0], points[i][1] - points[i - 1][1])
    return total


def _segment_skip(way, segment_index: int, length_m: float, *, trimmed: bool) -> dict:
    """One dropped way-segment, in the same shape as ``osm.SkippedWay``.

    Same keys as every other entry in ``skipped_ways`` so the compliance
    report's existing group-by-reason rendering picks these up with no special
    case. The two reasons are kept APART because they mean different things to
    whoever reads the report: ``too_short`` is a genuinely tiny piece of road,
    while ``too_short_after_setback`` is a segment the §5.3.1 intersection
    setback consumed — that one is the generator's own doing, and if it ever
    grows it means the setback is eating real geometry rather than trimming it.
    """
    return {
        "way_id": way.id,
        "reason": (
            "segment_too_short_after_setback_trim" if trimmed else "segment_too_short"
        ),
        "highway": getattr(way, "highway", "") or "",
        "detail": (
            f"segment {segment_index}: {length_m:.2f} m < MIN_LINK_LENGTH_M "
            f"{MIN_LINK_LENGTH_M:g} m"
            + (" (after the intersection setback was trimmed off)" if trimmed else "")
        ),
    }


def _round_point(point: tuple[float, float]) -> tuple[float, float]:
    return round(point[0], COORD_DECIMALS), round(point[1], COORD_DECIMALS)


def _offset_polyline(
    points: list[tuple[float, float]], offset_m: float
) -> list[tuple[float, float]]:
    """Shift a polyline ``offset_m`` to the LEFT of its own direction.

    Each vertex moves along the mean of the left normals of its incident
    segments. That is an approximation at a bend — a proper miter join would
    also lengthen the offset by ``1/cos(θ/2)`` — and it is deliberately the
    simple one: for the two-point segments this generator produces from OSM it
    is exact, and at a bend the residual is well under the projection's own
    one-metre budget for the angles a road actually turns through. Named here
    rather than hidden, because "the offset is exact" is the kind of claim that
    later turns out to have been the bug.
    """
    if len(points) < 2 or offset_m == 0.0:
        return [tuple(p) for p in points]

    normals: list[tuple[float, float]] = []
    for i in range(len(points) - 1):
        dx = points[i + 1][0] - points[i][0]
        dy = points[i + 1][1] - points[i][1]
        normals.append(left_normal(dx, dy))

    out: list[tuple[float, float]] = []
    for i, (x, y) in enumerate(points):
        if i == 0:
            nx, ny = normals[0]
        elif i == len(points) - 1:
            nx, ny = normals[-1]
        else:
            ax, ay = normals[i - 1]
            bx, by = normals[i]
            nx, ny = ax + bx, ay + by
            norm = math.hypot(nx, ny)
            if norm == 0.0:
                nx, ny = normals[i]
            else:
                nx, ny = nx / norm, ny / norm
        out.append((x + nx * offset_m, y + ny * offset_m))
    return out


def _point_at_distance(
    points: list[tuple[float, float]], distance: float
) -> tuple[float, float]:
    if distance <= 0:
        return points[0]
    acc = 0.0
    for i in range(1, len(points)):
        x0, y0 = points[i - 1]
        x1, y1 = points[i]
        seg = math.hypot(x1 - x0, y1 - y0)
        if seg <= 0:
            continue
        if acc + seg >= distance:
            t = (distance - acc) / seg
            return x0 + (x1 - x0) * t, y0 + (y1 - y0) * t
        acc += seg
    return points[-1]


def _trim(
    points: list[tuple[float, float]], start_cut: float, end_cut: float
) -> list[tuple[float, float]]:
    """Drop ``start_cut`` metres off the head and ``end_cut`` off the tail."""
    length = _polyline_length(points)
    lo, hi = start_cut, length - end_cut
    if hi <= lo:
        return [points[0], points[-1]]
    out = [_point_at_distance(points, lo)]
    acc = 0.0
    for i in range(1, len(points)):
        acc += math.hypot(points[i][0] - points[i - 1][0], points[i][1] - points[i - 1][1])
        if lo < acc < hi:
            out.append(points[i])
    out.append(_point_at_distance(points, hi))
    return out


def _heading(points: list[tuple[float, float]], *, at_end: bool) -> tuple[float, float]:
    if at_end:
        (x0, y0), (x1, y1) = points[-2], points[-1]
    else:
        (x0, y0), (x1, y1) = points[0], points[1]
    return x1 - x0, y1 - y0


def classify_turn(
    in_dx: float, in_dy: float, out_dx: float, out_dy: float
) -> tuple[str, float]:
    """(turn type, signed heading change in degrees).

    Positive = counter-clockwise in the +x-east/+y-north frame = a geometric
    LEFT turn. Under left-hand traffic that is the near-side turn — the extract
    says so under Table 5-8 (p.197): "left-turn is the near-side/tight turn and
    right-turn is the across-traffic turn". The sign convention is geometric and
    therefore the same in both driving regimes; what changes with the regime is
    which of the two crosses opposing traffic, and that is a §5.4 question this
    unit does not answer.
    """
    dot = in_dx * out_dx + in_dy * out_dy
    cross = in_dx * out_dy - in_dy * out_dx
    angle = math.degrees(math.atan2(cross, dot))
    magnitude = abs(angle)
    if magnitude <= THROUGH_ANGLE_DEG:
        return TURN_THROUGH, angle
    if magnitude > UTURN_ANGLE_DEG:
        return TURN_U, angle
    return (TURN_LEFT if angle > 0 else TURN_RIGHT), angle


def connector_lane_pairs(
    turn: str,
    approach_lanes: int,
    exit_lanes: int,
    approach_block: tuple[int, ...] | None = None,
) -> list[tuple[int, int]]:
    """Which approach lane each connector lane leaves, and which exit lane it joins.

    Returns ``[(from_lane, to_lane), …]`` in **connector-lane order**, i.e. the
    connector's own lane 1 first. Both index spaces are Vissim's, where
    :data:`LANE_INDEX_1_IS_RIGHTMOST` — index 1 is the right-most lane of its
    link and indices grow leftwards, in either driving regime.

    The rule, and why it is the rule
    --------------------------------
    A connector carries ``k = min(approach, exit)`` lanes. Which ``k`` of each
    link's lanes it takes is decided by where the movement GOES, which is
    geometry and therefore regime-independent:

    * a **left** turn leaves to the left, so it is made from the ``k``
      LEFT-most lanes — the HIGHEST indices — into the exit's left-most lanes;
    * a **right** turn is the mirror image: the ``k`` RIGHT-most lanes, indices
      ``1..k``, into the exit's right-most lanes;
    * a **through** movement has no such tell. It is aligned on the MEDIAN side,
      because that is the side turn pockets are not added to: under left-hand
      traffic the median is on the right, so through movements align on indices
      ``1..k``. This one branch — and only this one — depends on
      :data:`TRAFFIC_REGULATION`.

    Under left-hand traffic that makes the near-side (kerb) turn — the left turn
    — attach to the HIGHEST lane index, not to 1. Emitter v1 wrote 1 for
    everything, which put every left turn on the median lane.

    Hand-derived example (left-hand traffic, the case the crossroads fixture
    actually produces)
    -------------------------------------------------------------------------
    A two-lane approach ``A`` (lane 1 = right-most = median side; lane 2 =
    left-most = kerb side) meets a one-lane exit ``E``. ``k = min(2, 1) = 1``.

    ======== ================= ============= =========================
    movement connector lane 1  physical lane meaning under LHT
    ======== ================= ============= =========================
    left     ``A 2 → E 1``     kerb-most     near-side turn from the kerb lane
    right    ``A 1 → E 1``     median-most   across-traffic turn from the median
    through  ``A 1 → E 1``     median-most   median-aligned
    ======== ================= ============= =========================

    And with a three-lane approach into a two-lane exit (``k = 2``), a left turn
    gives ``[(2, 1), (3, 2)]``: connector lane 1 is the right-most of the
    connector, so it takes the right-most of the selected block on both sides.

    ``approach_block``: when the SOURCE says which lanes
    -------------------------------------------------------
    Everything above is a derivation from geometry, used because OSM usually
    says nothing about lane allocation. When it does — a fully specified
    ``turn:lanes`` (see :func:`turn_lane_allocation`) — the approach side is
    read from the file instead of derived, and only the EXIT side keeps the
    geometric rule, because ``turn:lanes`` describes the approach and is silent
    about which lane of the exit a movement lands in.

    The block is a set of approach lane indices. If the exit is narrower than
    the block, the ``k`` lanes of the block nearest the side the movement is
    heading are taken — the same "which side does this movement hug" question
    the derivation above answers, asked inside a smaller pool. Those of the
    block's lanes that do not fit are then a genuine lane drop, which is what
    the road actually does, and V4 of the R1.5 gate is left free to measure it.
    """
    if approach_block:
        return _pairs_from_block(turn, approach_block, exit_lanes)
    count = max(1, min(approach_lanes, exit_lanes))
    approach_total = max(1, approach_lanes)
    exit_total = max(1, exit_lanes)

    if turn == TURN_LEFT:
        keep_left = True
    elif turn == TURN_RIGHT:
        keep_left = False
    elif turn == TURN_U:
        # A U-turn crosses the centreline, so it is made from the lane nearest
        # the centreline and joins the lane nearest the centreline on the other
        # carriageway. Under left-hand traffic each direction's carriageway sits
        # to the LEFT of the shared centreline (``osm.left_normal``), so the
        # lane nearest the centreline is each link's RIGHT-most — index 1 — on
        # both sides. Stated as its own branch rather than left to fall through
        # the "through" case it happens to agree with: the two agree by
        # coincidence under this regime and would not under the other.
        keep_left = TRAFFIC_REGULATION != "LEFTHANDTRAFFIC"
    else:
        # The only regime-dependent branch: align on the median side, which is
        # the right (index 1) under left-hand traffic and the left under
        # right-hand traffic. Written as a comparison rather than a constant so
        # that a future right-hand generator flips it by changing the regime.
        keep_left = TRAFFIC_REGULATION != "LEFTHANDTRAFFIC"

    def block(total: int) -> list[int]:
        if keep_left:
            # left-most `count` lanes = the highest indices
            return list(range(total - count + 1, total + 1))
        return list(range(1, count + 1))

    return list(zip(block(approach_total), block(exit_total)))


def _keeps_left(turn: str) -> bool:
    """Does this movement hug the LEFT (highest-index) side of its lane pool?"""
    if turn == TURN_LEFT:
        return True
    if turn == TURN_RIGHT:
        return False
    # Through and U-turn both align on the median side — see the branches in
    # ``connector_lane_pairs``, of which this is the extracted predicate.
    return TRAFFIC_REGULATION != "LEFTHANDTRAFFIC"


def _pairs_from_block(
    turn: str, approach_block: tuple[int, ...], exit_lanes: int
) -> list[tuple[int, int]]:
    """``connector_lane_pairs`` with the approach lanes given, not derived."""
    pool = sorted(approach_block)
    exit_total = max(1, exit_lanes)
    count = max(1, min(len(pool), exit_total))
    chosen = pool[-count:] if _keeps_left(turn) else pool[:count]
    if _keeps_left(turn):
        exit_side = list(range(exit_total - count + 1, exit_total + 1))
    else:
        exit_side = list(range(1, count + 1))
    return list(zip(chosen, exit_side))


#: A ``turn:lanes`` entry that names no movement. Both spellings mean "the
#: source does not say what this lane does": an empty entry is an unfilled slot
#: and ``none`` is an explicit "no turn arrow painted here". Either way the file
#: has not stated an allocation, and guessing one would be an invention — so a
#: tag containing either is not used at all.
UNSPECIFIED_TURN_LANE_VALUES = frozenset({"", "none"})

#: Why a link's ``turn:lanes`` was not used to allocate its lanes. Counted in
#: the connectivity metadata so "the tag was there and we ignored it" is never
#: a silence.
TURN_LANES_NO_TAG = "no_turn_lanes_tag_for_this_direction"
TURN_LANES_COUNT_MISMATCH = "turn_lanes_entry_count_differs_from_lane_count"
TURN_LANES_UNSPECIFIED_ENTRY = "turn_lanes_has_an_unspecified_entry"
TURN_LANES_BLOCKS_NE_MOVEMENTS = "turn_lanes_groups_do_not_match_the_movements_built"
TURN_LANES_CONTRADICTS_GEOMETRY = "turn_lanes_order_contradicts_the_turn_geometry"
TURN_LANES_NO_STARVED_LANE = "default_allocation_already_serves_every_lane"


def turn_lane_allocation(
    turn_lanes: tuple[str, ...], lanes: int, movements: list[tuple[str, float]]
) -> tuple[dict[int, tuple[int, ...]], str]:
    """Which approach lanes each movement leaves, read from ``turn:lanes``.

    Returns ``({movement index: lane indices}, "")`` when the source states the
    allocation completely, and ``({}, reason)`` when it does not.

    **What this repairs.** The geometric default in
    :func:`connector_lane_pairs` gives every movement ``min(approach, exit)``
    lanes measured from one side, so two movements out of one approach are
    routinely given the SAME lanes while the approach's other lanes are left
    with no connector at all. Vissim does not complain: the vehicles in those
    lanes fail to merge, wait, and are deleted after ``DiffusTm`` with the
    demand vanishing silently. Measured on a real 4-lane trunk approach tagged
    ``through|through|right|right``: both movements were allocated lanes 1 and 2
    and half the road was unusable — a lane drop the road does not have.

    **The mapping, and the one place it can go wrong.** OSM orders ``turn:lanes``
    entries **leftmost to rightmost in the direction of travel**, in either
    driving regime. This generator indexes lanes with
    :data:`LANE_INDEX_1_IS_RIGHTMOST`. So OSM position ``p`` of ``n`` lanes is
    Vissim index ``n - p + 1``, and getting that backwards would put every
    turn on the wrong side of the road — which is why it is asserted by hand in
    the tests rather than trusted.

    **Preconditions, all required, because a partial read is worse than none:**

    1. the tag applies to this direction and has exactly ``lanes`` entries;
    2. every entry names a movement — an empty or ``none`` entry means the
       source did not say, and this function never fills in a gap;
    3. the tag's contiguous groups are exactly as many as the movements built,
       so the correspondence is forced rather than chosen;
    4. taken in order (leftmost group ↔ leftmost movement, ordered by signed
       heading change), no group's own word contradicts the geometry — a group
       saying ``left`` may not land on a movement classified as a right turn.
    """
    if not turn_lanes:
        return {}, TURN_LANES_NO_TAG
    if len(turn_lanes) != lanes or lanes < 1:
        return {}, TURN_LANES_COUNT_MISMATCH
    if any(value.strip().lower() in UNSPECIFIED_TURN_LANE_VALUES for value in turn_lanes):
        return {}, TURN_LANES_UNSPECIFIED_ENTRY

    groups: list[tuple[str, list[int]]] = []
    for position, value in enumerate(turn_lanes, start=1):
        index = lanes - position + 1
        if groups and groups[-1][0] == value:
            groups[-1][1].append(index)
        else:
            groups.append((value, [index]))
    if len(groups) != len(movements):
        return {}, TURN_LANES_BLOCKS_NE_MOVEMENTS

    # Movements sorted leftmost-first: a positive heading change is a geometric
    # left turn (``classify_turn``), so descending angle is left → right, the
    # same order the tag is written in.
    order = sorted(range(len(movements)), key=lambda i: (-movements[i][1], i))
    allocation: dict[int, tuple[int, ...]] = {}
    for (value, indices), movement_index in zip(groups, order):
        turn = movements[movement_index][0]
        words = {word.strip().lower() for word in value.split(";")}
        if (turn == TURN_LEFT and words <= {"right", "merge_to_right"}) or (
            turn == TURN_RIGHT and words <= {"left", "merge_to_left"}
        ):
            return {}, TURN_LANES_CONTRADICTS_GEOMETRY
        allocation[movement_index] = tuple(sorted(indices))
    return allocation, ""


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------


def _way_segments(way: OsmWay, intersections: set[int]) -> list[tuple[int, ...]]:
    """Split a way at its INTERIOR intersection nodes.

    Splitting only at junctions (rather than at every OSM vertex, which is the
    naive translation) is this generator's §5.3.3 "minimise the number of links"
    behaviour, and the report states both counts so the claim is checkable.
    """
    node_ids = way.node_ids
    cuts = [0]
    for index in range(1, len(node_ids) - 1):
        if node_ids[index] in intersections:
            cuts.append(index)
    cuts.append(len(node_ids) - 1)
    return [tuple(node_ids[cuts[i] : cuts[i + 1] + 1]) for i in range(len(cuts) - 1)]


#: How many times :func:`build_draft` may remove isolated zones and rebuild.
#: Removing a component that nothing can reach cannot isolate anything else, so
#: one extra round is enough in theory and the cap exists to make that claim
#: falsifiable rather than assumed: exhausting it is reported as a named gap and
#: the surviving zones stay in the network for the gate to refuse. A silent
#: `while True` that quietly stopped converging is exactly the failure this
#: package keeps finding in other people's code.
MAX_ISOLATION_ROUNDS = 3

#: Recorded when the cap above is hit. Never a silence.
ISOLATION_ROUNDS_EXHAUSTED = "isolated_zone_removal_did_not_converge"


def build_draft(extract: OsmExtract, *, name: str) -> DraftNetwork:
    """OSM extract → draft network + §5 compliance report. Reports, never raises.

    Two passes over :func:`_build_once`, because one of the questions can only
    be asked of a finished network: **is there a pocket of streets nothing can
    enter and nothing can leave?** A vehicle cannot be in one, so the pocket
    simulates nothing while inflating every count in every report — and the
    reason it is unreachable is always a property of the source (a footway for
    an access road, a severance in the base map, the edge of the extract), never
    something a connector may repair. So the zone is REMOVED and named; see
    :class:`~services.genesis.connectivity.IsolatedZone` for the full argument
    and :data:`~services.genesis.osm.SKIP_ISOLATED_ZONE` for the skip each
    excluded way leaves behind.

    Removing the ways shrinks the extract's extent, which moves the projection
    origin, which moves every coordinate — so the second pass is a full rebuild
    rather than a deletion from the first pass's output. That is the only way
    the numbers in the report can be the numbers in the file.
    """
    excluded: list[IsolatedZone] = []
    retained: list[IsolatedZone] = []
    current = extract
    unconverged = False
    for round_index in range(MAX_ISOLATION_ROUNDS):
        draft = _build_once(current, name=name)
        if draft.refused:
            return draft
        zones = diagnose_isolated_zones(draft.links, draft.connectors, current)
        # A zone the SOURCE connects and this generator did not is our bug, not
        # the map's, and it stays in the network with its name on it. Excluding
        # it would delete the evidence and turn the gate green over a defect
        # nobody ever saw.
        retained = [
            zone for zone in zones if zone.reason not in EXCLUDABLE_ZONE_REASONS
        ]
        removable = [zone for zone in zones if zone.reason in EXCLUDABLE_ZONE_REASONS]
        if not removable:
            break
        if round_index == MAX_ISOLATION_ROUNDS - 1:
            # Cap exhausted with zones still removable. They were NOT removed,
            # so they are retained in the emitted network — and they must be
            # NAMED as such. The first cut of this branch dropped them from
            # both lists (not excluded because the break ran before the
            # extend; not retained because their reason is excludable), so a
            # stuck pocket appeared in no report at all: present in the file,
            # absent from every ledger. An exclusion pass whose failure mode
            # is an unnameable zone commits the exact sin the pass exists to
            # prevent. Re-tag them with the cap reason and keep them in the
            # retained list, which is factually what they are.
            unconverged = True
            retained = retained + [
                replace(zone, reason=ISOLATION_ROUNDS_EXHAUSTED)
                for zone in removable
            ]
            break
        excluded.extend(removable)
        reason_of = {
            way_id: zone for zone in removable for way_id in zone.way_ids
        }
        current = current.without_ways(
            sorted(reason_of),
            reason=SKIP_ISOLATED_ZONE,
            detail_for=lambda way_id: (
                f"{reason_of[way_id].reason}: {reason_of[way_id].detail}"
            ),
        )
    connectivity = draft.metadata.setdefault("connectivity", {})
    connectivity["excluded_zones"] = [zone.as_dict() for zone in excluded]
    connectivity["excluded_zones_by_reason"] = _count_by(
        zone.reason for zone in excluded
    )
    connectivity["excluded_zone_links"] = sum(len(zone.link_ids) for zone in excluded)
    connectivity["isolated_zones_retained"] = [zone.as_dict() for zone in retained]
    if unconverged:
        connectivity["excluded_zones_unresolved"] = ISOLATION_ROUNDS_EXHAUSTED
    if excluded or retained:
        draft.compliance = _compliance_with_exclusions(draft, excluded, retained)
    return draft


def _count_by(values) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


def _compliance_with_exclusions(
    draft: DraftNetwork,
    excluded: list[IsolatedZone],
    retained: list[IsolatedZone],
) -> ComplianceReport:
    """The §5 report plus one item naming every pocket this build removed.

    Its state is APPLIED, and that is the honest grade rather than a generous
    one: §5.3.3 asks for the smallest network that represents the road system,
    and a pocket no vehicle can reach represents nothing. But the item carries
    every excluded pocket by street name in ``subjects``, because the *reason*
    it was unreachable is a fact about the client's base map, and a client who
    reads "96 links removed, access is a footway" can go and fix the map. A
    count with no names would be indistinguishable from the generator quietly
    losing roads — which is the failure mode this whole package is built
    against.
    """
    items = list(draft.compliance.items)
    links = sum(len(zone.link_ids) for zone in excluded)
    items.append(
        ComplianceItem(
            rule_id="5.3.3-isolated-zone-excluded",
            section="§5.3.3",
            doc_page=193,
            # VIOLATED the moment a zone had to be RETAINED: that one is a
            # movement this generator lost on roads the source says meet, and
            # grading it APPLIED would let our own defect read as tidiness.
            state=(
                ComplianceState.VIOLATED if retained else ComplianceState.APPLIED
            ),
            citation=CIT_S533_MINIMISE,
            instances=len(excluded) + len(retained),
            # EVERY street of the pocket, not a representative-plus-count. The
            # docstring above promises "every excluded pocket by street name",
            # and a client fixing their base map needs the whole list — the
            # 96-link pocket spans a dozen named streets, and "Scandrett Way
            # +95 link(s)" names one of them. A subject a reader cannot act on
            # is a count wearing a name.
            subjects=[
                f"{', '.join(zone.street_names) or zone.representative} "
                f"— {len(zone.link_ids)} link(s) [{zone.reason}]"
                for zone in excluded + retained
            ],
            evidence={
                "excluded_zones": len(excluded),
                "excluded_links": links,
                "excluded_osm_ways": sum(len(zone.way_ids) for zone in excluded),
                "by_reason": _count_by(zone.reason for zone in excluded),
                "retained_zones_this_generator_severed": len(retained),
                "retained_links": sum(len(zone.link_ids) for zone in retained),
            },
            detail={
                "ko": (
                    f"차량이 진입할 수도 이탈할 수도 없는 구역 {len(excluded)}곳(링크 {links}개)을 "
                    "네트워크에서 제외했습니다. 원인은 모두 원본 지도 쪽이며(비차량 way로만 접근, "
                    "원본 자체의 단절, 추출 경계 절단), 커넥터를 추가해 잇는 것은 없는 도로를 "
                    "만들어내는 일이므로 하지 않았습니다. 각 구역과 사유는 위에 이름으로 남깁니다."
                ),
                "en": (
                    f"{len(excluded)} pocket(s) totalling {links} link(s) were removed: nothing "
                    "can enter them and nothing can leave. Every cause is in the source map "
                    "(access only via a way this generator does not model, a severance in the "
                    "base data, or truncation at the extract's edge), so connecting them would "
                    "have meant inventing a road that does not exist. Each pocket and its reason "
                    "is named above."
                ),
                "ja": (
                    f"車両が進入も退出もできないゾーン{len(excluded)}箇所(リンク{links}本)を"
                    "ネットワークから除外しました。原因はいずれも元データ側にあり、"
                    "コネクタで接続することは存在しない道路の捏造にあたるため行いません。"
                ),
            },
        )
    )
    return ComplianceReport(items=items)


def _build_once(extract: OsmExtract, *, name: str) -> DraftNetwork:
    """One OSM extract → one draft network. See :func:`build_draft` for the loop.

    The connectivity pass (:mod:`services.genesis.connectivity`) runs FIRST and
    in one fixed order, because the three questions it answers are not
    independent:

    1. :func:`~.connectivity.merge_junction_nodes` decides which nodes are the
       same junction. This can only run before anything else, because it changes
       what ``intersection_node_ids()`` returns and therefore where links are
       split and where connectors are built.
    2. links, nodes and junction connectors are built from the MERGED topology,
       with any movement an OSM turn restriction bans left out.
    3. :func:`~.connectivity.dead_ends` then reads the merged topology, so a
       terminal the merge turned into a junction is no longer a dead end.
       Running it first would emit a U-turn in the middle of a junction.
    """
    draft = DraftNetwork(name=name, source_osm=extract.source)

    if extract.refused:
        draft.refused = True
        draft.refusals = [f"{r.code}: {r.message}" for r in extract.refusals]
        return draft

    source_extract = extract
    extract, merge_report = merge_junction_nodes(extract)

    bounds = extract.bounds()
    if bounds is None:  # pragma: no cover - a non-refused extract always has nodes
        draft.refused = True
        draft.refusals = ["osm_no_nodes: no node coordinates to project"]
        return draft

    plane = origin_for_bounds(*bounds)
    projected = {
        node_id: _round_point(plane.forward(node.lat, node.lon))
        for node_id, node in extract.nodes.items()
    }
    half_extent = max(
        (max(abs(x), abs(y)) for x, y in projected.values()),
        default=0.0,
    )
    check = plane.check_extent(half_extent)
    if not check.ok:
        draft.refused = True
        draft.refusals = [f"projection_out_of_range: {check.message}"]
        draft.metadata = _metadata(extract, plane, check, bounds)
        return draft

    draft.metadata = _metadata(extract, plane, check, bounds)
    draft.metadata["source_osm_ways_before_node_merge"] = len(source_extract.ways)
    draft.skipped_ways = [skip.as_dict() for skip in extract.skipped]
    draft.assumptions = _assumptions()

    intersections = extract.intersection_node_ids()
    ways = sorted(extract.ways, key=lambda w: w.id)

    # --- links -------------------------------------------------------------
    links: list[DraftLink] = []
    dropped_segments: list[dict] = []
    naive_link_count = 0
    next_link_id = 1
    for way in ways:
        naive_link_count += (len(way.node_ids) - 1) * len(way.directions)
        for segment_index, segment in enumerate(_way_segments(way, intersections)):
            centreline = [projected[node_id] for node_id in segment]
            base_length = _polyline_length(centreline)
            # A segment too short to be worth a link is DROPPED — but never
            # silently. Both of these used to be bare `continue`s, and that cost
            # real money: the 2026-08-10 connectivity measurement traced 40 of
            # the network's 225 dangling ends to segments that vanished here,
            # including Murdoch Drive's roundabout arm, and no report anywhere
            # said a link had gone missing. The reader of a compliance report
            # cannot notice an absence; they can notice a named skip. This is
            # the same doctrine the emitter learned from Vissim itself — silent
            # success on a network that lost pieces is the failure this product
            # exists to catch.
            if base_length < MIN_LINK_LENGTH_M:
                dropped_segments.append(
                    _segment_skip(way, segment_index, base_length, trimmed=False)
                )
                continue
            setback = min(INTERSECTION_SETBACK_M, base_length / 4.0)
            clamped = setback < INTERSECTION_SETBACK_M
            start_cut = setback if segment[0] in intersections else 0.0
            end_cut = setback if segment[-1] in intersections else 0.0
            trimmed = _trim(centreline, start_cut, end_cut)
            trimmed_length = _polyline_length(trimmed)
            if trimmed_length < MIN_LINK_LENGTH_M:
                dropped_segments.append(
                    _segment_skip(way, segment_index, trimmed_length, trimmed=True)
                )
                continue

            for direction in sorted(way.directions, key=lambda d: not d.forward):
                travel = trimmed if direction.forward else list(reversed(trimmed))
                # A TWO-WAY OSM way is the road's shared centreline, so each
                # direction's carriageway centre sits half its own width to the
                # LEFT of it (§5.2.1.1, CIT_S5211_LEFT_HAND). A ONE-WAY way is
                # already the centreline of the carriageway it describes — a
                # couplet or a dual carriageway is mapped as two separate ways
                # drawn where the asphalt is — so offsetting it would shift the
                # road sideways by half its width for no reason. Getting this
                # backwards is the other half of the silent-mirror family of
                # bugs, and it is asserted by sign in the tests.
                offset = 0.0 if way.is_oneway else direction.lanes * LANE_WIDTH_M / 2.0
                points = [
                    _round_point(p) for p in _offset_polyline(travel, offset)
                ]
                node_from = segment[0] if direction.forward else segment[-1]
                node_to = segment[-1] if direction.forward else segment[0]
                links.append(
                    DraftLink(
                        id=next_link_id,
                        name=_link_name(way, segment_index, direction.forward),
                        osm_way_id=way.id,
                        segment_index=segment_index,
                        forward=direction.forward,
                        lanes=direction.lanes,
                        lane_width_m=LANE_WIDTH_M,
                        points=points,
                        lane_provenance=direction.provenance.as_dict(),
                        highway=way.highway,
                        from_node_osm_id=node_from,
                        to_node_osm_id=node_to,
                        side_offset_m=round(offset, COORD_DECIMALS),
                        setback_clamped=clamped,
                    )
                )
                next_link_id += 1
    draft.links = links
    # Deterministic order: the loop above walks `ways` sorted by id, so these
    # are already in (way id, segment index) order — sorting again would be a
    # no-op that hid a future ordering regression rather than catching it.
    draft.skipped_ways.extend(dropped_segments)

    # --- nodes -------------------------------------------------------------
    touched = {link.from_node_osm_id for link in links} | {link.to_node_osm_id for link in links}
    node_ids = sorted(nid for nid in intersections if nid in touched)
    draft.nodes = [
        DraftNode(
            id=index + 1,
            name=f"Node {osm_id}",
            osm_id=osm_id,
            x=projected[osm_id][0],
            y=projected[osm_id][1],
            polygon=[
                _round_point((projected[osm_id][0] + dx, projected[osm_id][1] + dy))
                for dx, dy in (
                    (-NODE_HALF_SIZE_M, -NODE_HALF_SIZE_M),
                    (NODE_HALF_SIZE_M, -NODE_HALF_SIZE_M),
                    (NODE_HALF_SIZE_M, NODE_HALF_SIZE_M),
                    (-NODE_HALF_SIZE_M, NODE_HALF_SIZE_M),
                )
            ],
        )
        for index, osm_id in enumerate(node_ids)
    ]
    node_index = {node.osm_id: node.id for node in draft.nodes}

    # --- connectors --------------------------------------------------------
    connectors: list[DraftConnector] = []
    next_connector_id = 10001
    restriction_suppressed: list[str] = []
    #: Relation ids that actually removed a movement. NOT the ids that parsed —
    #: see ``_connectivity_metadata``.
    restrictions_enforced: set[int] = set()
    #: Why each approach lost each of its candidate movements, so a link that
    #: ends up with none can be attributed without re-deriving the geometry in
    #: a second tool. Keys are link ids; values are the codes below.
    suppression_of: dict[int, set[str]] = {}
    #: Approaches whose lane allocation came from the file's own ``turn:lanes``
    #: instead of from the geometric default, and — for every approach where the
    #: default starved a lane and the tag could NOT be used — why not. Both
    #: halves are reported: a repair nobody can name is indistinguishable from a
    #: fabrication, and a repair that did not happen is a lane full of vehicles
    #: Vissim will delete without saying so.
    turn_lane_allocations: list[dict] = []
    turn_lane_refusals: list[dict] = []
    way_by_id = {way.id: way for way in ways}
    for osm_node_id in node_ids:
        approaches = sorted(
            (l for l in links if l.to_node_osm_id == osm_node_id), key=lambda l: l.id
        )
        exits = sorted(
            (l for l in links if l.from_node_osm_id == osm_node_id), key=lambda l: l.id
        )
        for approach in approaches:
            in_dx, in_dy = _heading(approach.points, at_end=True)
            movements: list[tuple[DraftLink, str, float]] = []
            for exit_link in exits:
                if approach.segment_key == exit_link.segment_key:
                    # The same carriageway reversed: a U-turn, suppressed under
                    # §5.3.3's "minimise the number of alternative paths".
                    suppression_of.setdefault(approach.id, set()).add(SUPPRESSED_OWN_CARRIAGEWAY)
                    continue
                out_dx, out_dy = _heading(exit_link.points, at_end=False)
                turn, angle = classify_turn(in_dx, in_dy, out_dx, out_dy)
                if turn == TURN_U:
                    suppression_of.setdefault(approach.id, set()).add(SUPPRESSED_ANTIPARALLEL)
                    continue
                # The map's own movement bans. This can only REMOVE a movement,
                # never add one, so a misread restriction costs a legal turn
                # (visible as a routing failure) rather than legalising a banned
                # one (invisible, and exactly the confident-wrong-number class
                # this project exists to catch).
                banned = suppressed_by_restriction(
                    extract.restrictions,
                    osm_node_id,
                    approach.osm_way_id,
                    exit_link.osm_way_id,
                )
                if banned is not None:
                    restriction_suppressed.append(
                        f"{approach.name} -> {exit_link.name} "
                        f"({banned.kind}, relation {banned.id})"
                    )
                    restrictions_enforced.add(banned.id)
                    suppression_of.setdefault(approach.id, set()).add(SUPPRESSED_RESTRICTION)
                    continue
                movements.append((exit_link, turn, angle))

            blocks = _approach_lane_blocks(
                approach,
                movements,
                way_by_id.get(approach.osm_way_id),
                turn_lane_allocations,
                turn_lane_refusals,
            )
            for index, (exit_link, turn, angle) in enumerate(movements):
                lane_pairs = connector_lane_pairs(
                    turn, approach.lanes, exit_link.lanes, blocks.get(index)
                )
                if approach.lanes == exit_link.lanes:
                    lane_change = "same"
                elif exit_link.lanes > approach.lanes:
                    lane_change = "gain"
                else:
                    lane_change = "drop"
                connectors.append(
                    DraftConnector(
                        id=next_connector_id,
                        name=f"{approach.name} -> {exit_link.name} ({turn})",
                        node_osm_id=osm_node_id,
                        node_id=node_index[osm_node_id],
                        from_link=approach.id,
                        to_link=exit_link.id,
                        from_pos_m=round(approach.length_m, COORD_DECIMALS),
                        to_pos_m=0.0,
                        lanes=len(lane_pairs),
                        lane_width_m=LANE_WIDTH_M,
                        points=[approach.points[-1], exit_link.points[0]],
                        turn=turn,
                        heading_change_deg=round(angle, 3),
                        lane_change=lane_change,
                        from_lanes=[pair[0] for pair in lane_pairs],
                        to_lanes=[pair[1] for pair in lane_pairs],
                        table_5_8_advisory=TABLE_5_8_TURN_ADVISORY.get(turn),
                    )
                )
                next_connector_id += 1

    # --- cul-de-sac U-turns ------------------------------------------------
    ends = dead_ends(extract)
    u_turns, unbuildable = _cul_de_sac_connectors(
        links, ends, next_connector_id, node_index
    )
    connectors.extend(u_turns)
    stranded = _stranded_approach_connectors(
        links, connectors, extract, ends, next_connector_id + len(u_turns), node_index
    )
    connectors.extend(stranded)
    draft.connectors = connectors

    # A restriction that banned a U-turn the dead-end pass would otherwise have
    # emitted is enforced just as much as one that removed a junction movement.
    restrictions_enforced |= {
        restriction.id
        for restriction in extract.restrictions
        if restriction.kind == "no_u_turn" and restriction.from_way == restriction.to_way
        and (restriction.from_way, restriction.via_node)
        in {(end.way_id, end.node_id) for end in ends}
    }

    dangling = _dangling_approaches(links, connectors, ends, suppression_of)
    draft.metadata["connectivity"] = _connectivity_metadata(
        merge_report,
        ends,
        u_turns,
        stranded,
        unbuildable,
        restriction_suppressed,
        restrictions_enforced,
        dangling,
        extract,
        turn_lane_allocations,
        turn_lane_refusals,
    )
    draft.compliance = _compliance(draft, extract, ways, naive_link_count, check)
    return draft


def _approach_lane_blocks(
    approach: "DraftLink",
    movements: list[tuple["DraftLink", str, float]],
    way: OsmWay | None,
    applied: list[dict],
    refused: list[dict],
) -> dict[int, tuple[int, ...]]:
    """Per-movement approach lanes, from ``turn:lanes``, ONLY as a repair.

    The geometric default in :func:`connector_lane_pairs` is the rule; this is
    the exception, and it is deliberately narrow. It runs only when the default
    **starves a lane** — leaves one of the approach's lanes with no connector at
    all — because that is the one state that is demonstrably wrong rather than
    merely less specific than the source. A lane no connector leaves is a lane
    whose vehicles cannot follow their route, wait, and are deleted after
    ``DiffusTm`` with the demand vanishing and no error anywhere.

    Where the default already serves every lane it is left alone even if
    ``turn:lanes`` would say something different: the default is the rule this
    package's live check certified, and re-allocating a working junction on the
    strength of a tag would be a change with no defect behind it.

    Both outcomes are recorded — ``applied`` gets the repair, ``refused`` gets
    the approach and the reason the tag could not be used — so a reader can see
    the lanes this generator knows are starved and could not fix.
    """
    if not movements:
        return {}
    default = {
        lane
        for index, (exit_link, turn, _angle) in enumerate(movements)
        for lane, _to in connector_lane_pairs(turn, approach.lanes, exit_link.lanes)
    }
    starved = sorted(set(range(1, approach.lanes + 1)) - default)
    if not starved:
        return {}

    turn_lanes: tuple[str, ...] = ()
    if way is not None:
        for direction in way.directions:
            if direction.forward == approach.forward:
                turn_lanes = direction.turn_lanes
                break
    allocation, reason = turn_lane_allocation(
        turn_lanes,
        approach.lanes,
        [(turn, angle) for _exit, turn, angle in movements],
    )
    if reason:
        refused.append(
            {
                "link_id": approach.id,
                "link": approach.name,
                "lanes": approach.lanes,
                "starved_lanes": starved,
                "reason": reason,
                "turn_lanes": list(turn_lanes),
            }
        )
        return {}
    applied.append(
        {
            "link_id": approach.id,
            "link": approach.name,
            "lanes": approach.lanes,
            "starved_lanes_before": starved,
            "turn_lanes": list(turn_lanes),
            "allocation": {
                movements[index][0].id: list(block)
                for index, block in sorted(allocation.items())
            },
        }
    )
    return allocation


#: A dead end whose U-turn could not be built because one of the two
#: carriageways is not in the draft (its segment was shorter than
#: :data:`MIN_LINK_LENGTH_M`). Counted, never silently skipped.
UNBUILDABLE_U_TURN_NO_LINK_PAIR = "carriageway_pair_absent_from_draft"

# Why a candidate movement out of an approach was not built. Recorded per
# approach so a link that ends up with NO movement can be attributed from what
# the generator actually decided rather than from a second tool's re-derivation.
SUPPRESSED_OWN_CARRIAGEWAY = "same_carriageway_reversed"
SUPPRESSED_ANTIPARALLEL = "classified_u_turn"
SUPPRESSED_RESTRICTION = "banned_by_osm_restriction"


def _cul_de_sac_connectors(
    links: list[DraftLink],
    ends: list[DeadEnd],
    next_id: int,
    node_index: dict[int, int],
) -> tuple[list[DraftConnector], list[str]]:
    """A U-turn at every dead end :mod:`~.connectivity` says needs one.

    Without this a two-way street modelled as a FWD/REV pair is a **trap**: the
    two carriageways end half a road apart with nothing joining them, so a
    vehicle that drives to the end of the street cannot drive back out. Vissim
    does not report that — the vehicle simply leaves the network at the end of
    the link — so the street's outbound flow silently disappears and every
    number downstream of it is wrong by an amount nothing measures.

    The pair is found by ``(osm_way_id, segment_index)``: the connector joins
    the terminal segment's own two directions, never two different roads. The
    turn is :data:`TURN_U` and the lane mapping comes from
    :func:`connector_lane_pairs`, whose U-turn branch puts it on the lanes
    either side of the centreline.
    """
    by_end: dict[tuple[int, int], DraftLink] = {}
    by_start: dict[tuple[int, int], DraftLink] = {}
    for link in links:
        by_end[(link.osm_way_id, link.to_node_osm_id)] = link
        by_start[(link.osm_way_id, link.from_node_osm_id)] = link

    out: list[DraftConnector] = []
    unbuildable: list[str] = []
    connector_id = next_id
    for end in sorted(ends, key=lambda e: (e.way_id, e.node_id)):
        if not end.emit_u_turn:
            continue
        approach = by_end.get((end.way_id, end.node_id))
        exit_link = by_start.get((end.way_id, end.node_id))
        if approach is None or exit_link is None or approach.id == exit_link.id:
            unbuildable.append(
                f"way {end.way_id} at node {end.node_id}: "
                f"{UNBUILDABLE_U_TURN_NO_LINK_PAIR}"
            )
            continue
        in_dx, in_dy = _heading(approach.points, at_end=True)
        out_dx, out_dy = _heading(exit_link.points, at_end=False)
        _turn, angle = classify_turn(in_dx, in_dy, out_dx, out_dy)
        lanes = min(approach.lanes, exit_link.lanes)
        lane_pairs = connector_lane_pairs(TURN_U, approach.lanes, exit_link.lanes)
        if approach.lanes == exit_link.lanes:
            lane_change = "same"
        elif exit_link.lanes > approach.lanes:
            lane_change = "gain"
        else:
            lane_change = "drop"
        out.append(
            DraftConnector(
                id=connector_id,
                name=f"{approach.name} -> {exit_link.name} ({TURN_U})",
                node_osm_id=end.node_id,
                # 0 means "no node object", and it is the RIGHT answer for a
                # true cul-de-sac: it is not an intersection, no <node> polygon
                # is written for it, and nothing in the signal layer may treat
                # this connector as a node movement. It is the wrong answer
                # anywhere a <node> IS written — a dead end can now be a node
                # several ways share (every one of them one-way inbound), and
                # `signals.py`'s two node-scoped checks are keyed on this field,
                # so a 0 there makes a real movement invisible to them and the
                # report then states an approach has no onward movement when one
                # genuinely leaves it.
                node_id=node_index.get(end.node_id, 0),
                from_link=approach.id,
                to_link=exit_link.id,
                from_pos_m=round(approach.length_m, COORD_DECIMALS),
                to_pos_m=0.0,
                lanes=lanes,
                lane_width_m=LANE_WIDTH_M,
                points=[approach.points[-1], exit_link.points[0]],
                turn=TURN_U,
                heading_change_deg=round(angle, 3),
                lane_change=lane_change,
                from_lanes=[pair[0] for pair in lane_pairs],
                to_lanes=[pair[1] for pair in lane_pairs],
                table_5_8_advisory=TABLE_5_8_TURN_ADVISORY.get(TURN_U),
            )
        )
        connector_id += 1
    return out, unbuildable


def _stranded_approach_connectors(
    links: list[DraftLink],
    connectors: list[DraftConnector],
    extract: OsmExtract,
    ends: list[DeadEnd],
    next_id: int,
    node_index: dict[int, int],
) -> list[DraftConnector]:
    """Last resort: a U-turn for an approach that has NO legal movement at all.

    A link with no outgoing connector is a place vehicles leave the network in
    silence. Most of those are answered above — the junction merge gives a
    movement back, the cul-de-sac pass gives a U-turn. What is left is an
    approach that reaches a node other ways do touch and still has nowhere to
    go: every way at that node is one-way *inbound*, or every onward movement is
    antiparallel and was suppressed. On Murdoch this is a handful of service
    aisles that another aisle only feeds into.

    Turning around is then the ONLY physically available action, which is why
    this does not conflict with §5.3.3's "minimise the number of alternative
    paths": where there is no alternative, a U-turn creates none. The rule is
    deliberately last-resort — it can only ever fire on a link that would
    otherwise be a trap — and every one it fires on is NAMED in
    ``metadata['connectivity']['stranded_approach_u_turns']`` so a reviewer sees
    the list rather than a number that quietly went to zero.
    """
    leaving = {connector.from_link for connector in connectors}
    starts_at: dict[tuple[int, int, int], DraftLink] = {
        (link.osm_way_id, link.segment_index, link.from_node_osm_id): link
        for link in links
    }
    banned = {
        (r.from_way, r.via_node)
        for r in extract.restrictions
        if r.kind == "no_u_turn" and r.from_way == r.to_way
    }
    # Wherever the dead-end pass already REFUSED a U-turn — the model boundary
    # above all — this last-resort pass must not quietly supply one. Measured:
    # without this line a boundary link on Hope Road gained a U-turn and the
    # study edge became a reflecting wall, which is the exact failure the
    # boundary band exists to prevent.
    banned |= {(end.way_id, end.node_id) for end in ends if not end.emit_u_turn}

    out: list[DraftConnector] = []
    connector_id = next_id
    for approach in sorted(links, key=lambda l: l.id):
        if approach.id in leaving:
            continue
        if (approach.osm_way_id, approach.to_node_osm_id) in banned:
            continue
        exit_link = starts_at.get(
            (approach.osm_way_id, approach.segment_index, approach.to_node_osm_id)
        )
        if exit_link is None or exit_link.id == approach.id:
            continue
        in_dx, in_dy = _heading(approach.points, at_end=True)
        out_dx, out_dy = _heading(exit_link.points, at_end=False)
        _turn, angle = classify_turn(in_dx, in_dy, out_dx, out_dy)
        lane_pairs = connector_lane_pairs(TURN_U, approach.lanes, exit_link.lanes)
        out.append(
            DraftConnector(
                id=connector_id,
                name=f"{approach.name} -> {exit_link.name} ({TURN_U})",
                node_osm_id=approach.to_node_osm_id,
                # This pass exists for "an approach that reaches a node OTHER
                # WAYS DO TOUCH", so its node is normally a real DraftNode with
                # a real polygon — the opposite of the cul-de-sac case the 0
                # sentinel was copied from. Writing 0 here hid every one of
                # these connectors from `signals.py`'s node-scoped checks, which
                # then reported the approach as having no onward movement.
                node_id=node_index.get(approach.to_node_osm_id, 0),
                from_link=approach.id,
                to_link=exit_link.id,
                from_pos_m=round(approach.length_m, COORD_DECIMALS),
                to_pos_m=0.0,
                lanes=min(approach.lanes, exit_link.lanes),
                lane_width_m=LANE_WIDTH_M,
                points=[approach.points[-1], exit_link.points[0]],
                turn=TURN_U,
                heading_change_deg=round(angle, 3),
                lane_change=(
                    "same"
                    if approach.lanes == exit_link.lanes
                    else ("gain" if exit_link.lanes > approach.lanes else "drop")
                ),
                from_lanes=[pair[0] for pair in lane_pairs],
                to_lanes=[pair[1] for pair in lane_pairs],
                table_5_8_advisory=TABLE_5_8_TURN_ADVISORY.get(TURN_U),
            )
        )
        connector_id += 1
    return out


def _dangling_approaches(
    links: list[DraftLink],
    connectors: list[DraftConnector],
    ends: list[DeadEnd],
    suppression_of: dict[int, set[str]],
) -> list[dict]:
    """Every built link no connector leaves, attributed — the generator's own census.

    A link end no connector leaves is where a vehicle departs the network in
    silence: Vissim writes no error, the demand evaporates, and every number
    downstream is wrong by an amount nothing measures. Counting them was a
    separate tool's job, and the split cost exactly what a split costs — the
    tool re-derived causes the generator had never named, gave them confident
    names, and 8 of the Murdoch network's approaches were silent traps that
    appeared in NO generator artifact. The count belongs to whoever built the
    network, so this is it, and ``genesis_connectivity_probe.py`` reads it.

    Causes come from what the generator DECIDED, never from proximity: the
    dead-end pass's own reason where there is one, and otherwise the record of
    which candidate movements were suppressed and why.
    """
    reason_at = {(end.way_id, end.node_id): end.reason for end in ends}
    exits_at: set[int] = {link.from_node_osm_id for link in links}
    leaving = {connector.from_link for connector in connectors}

    out: list[dict] = []
    for link in sorted(links, key=lambda l: l.id):
        if link.id in leaving:
            continue
        node = link.to_node_osm_id
        reason = reason_at.get((link.osm_way_id, node))
        suppressed = suppression_of.get(link.id, set())
        if reason is not None:
            cause = DEAD_END_REASON_TO_DANGLING_CAUSE.get(reason, DANGLING_UNATTRIBUTED)
        elif node not in exits_at:
            cause = DANGLING_NO_EXIT_AT_JUNCTION
        elif SUPPRESSED_ANTIPARALLEL in suppressed:
            cause = DANGLING_ALL_MOVEMENTS_U
        elif suppressed == {SUPPRESSED_RESTRICTION}:
            cause = DANGLING_RESTRICTED
        elif not suppressed:
            # Nothing was even considered here, so the node is not a junction
            # this draft built connectors at.
            cause = DANGLING_UNMERGED_NEIGHBOUR
        else:
            cause = DANGLING_UNATTRIBUTED
        out.append(
            {
                "link_id": link.id,
                "link": link.name,
                "node_osm_id": node,
                "cause": cause,
                "dead_end_reason": reason,
            }
        )
    return out


def _connectivity_metadata(
    merge_report: MergeReport,
    ends: list[DeadEnd],
    u_turns: list[DraftConnector],
    stranded: list[DraftConnector],
    unbuildable: list[str],
    restriction_suppressed: list[str],
    restrictions_enforced: set[int],
    dangling: list[dict],
    extract: OsmExtract,
    turn_lane_allocations: list[dict],
    turn_lane_refusals: list[dict],
) -> dict:
    """Everything the connectivity pass decided, as data.

    Written into ``draft.metadata`` (and therefore into ``*.genesis.json``) so
    ``backend/tools/genesis_connectivity_probe.py``, the compliance report and a
    reviewer all read the same numbers instead of three re-derivations.
    """
    by_reason: dict[str, int] = {}
    for end in ends:
        by_reason[end.reason] = by_reason.get(end.reason, 0) + 1
    by_cause: dict[str, int] = {}
    for entry in dangling:
        by_cause[entry["cause"]] = by_cause.get(entry["cause"], 0) + 1
    inert = sorted(
        {restriction.id for restriction in extract.restrictions} - restrictions_enforced
    )
    return {
        "node_merge": merge_report.as_dict(),
        "dead_ends_by_reason": dict(sorted(by_reason.items())),
        # The generator's own dangling-end census. Whoever built the network
        # counts the places it strands vehicles; a tool that re-derived them
        # separately would be free to disagree, and did.
        "dangling_approaches": dangling,
        "dangling_approaches_by_cause": dict(sorted(by_cause.items())),
        "u_turn_connectors_emitted": len(u_turns),
        "u_turns_unbuildable": sorted(unbuildable),
        "stranded_approach_u_turns": sorted(connector.name for connector in stranded),
        # One list, and each entry carries its OWN reason: an arterial dead end
        # that got a U-turn and one that could not are both worth a reviewer's
        # eye, and collapsing them into an unlabelled list would make the
        # heading's claim wrong for half its contents.
        "arterial_dead_ends_named": sorted(
            f"way {end.way_id} ({end.highway}) at node {end.node_id}: {end.reason}"
            for end in ends
            if end.warn_arterial
        ),
        # ENFORCED means a movement was actually removed, not that a relation
        # parsed. The two are different numbers and the difference is the whole
        # point: a well-formed relation whose via node is not where its from-way
        # and to-way actually meet suppresses nothing, and reporting it as
        # "enforced" tells a reader a ban took effect when it did not. Both are
        # published, and the ones that did nothing are named.
        "osm_turn_restrictions_parsed": len(extract.restrictions),
        "osm_turn_restrictions_enforced": len(restrictions_enforced),
        "osm_turn_restrictions_inert": inert,
        "osm_turn_restrictions_refused": [
            refusal.as_dict() for refusal in extract.restriction_refusals
        ],
        "movements_suppressed_by_restriction": sorted(restriction_suppressed),
        # Lane allocation read from the file rather than derived — and, just as
        # importantly, every approach where the derivation starved a lane and
        # the file could NOT settle it. The second list is the honest half: it
        # names lanes whose vehicles Vissim will delete without a word.
        "turn_lane_allocations_applied": turn_lane_allocations,
        "turn_lane_allocations_refused": turn_lane_refusals,
        "turn_lane_allocations_refused_by_reason": _count_by(
            entry["reason"] for entry in turn_lane_refusals
        ),
    }


def _link_name(way: OsmWay, segment_index: int, forward: bool) -> str:
    base = way.name or way.highway
    return f"{base} [{'FWD' if forward else 'REV'}] w{way.id}s{segment_index}"


def _metadata(
    extract: OsmExtract,
    plane: LocalTangentPlane,
    check: ExtentCheck,
    bounds: tuple[float, float, float, float],
) -> dict:
    return {
        "generator": "services.genesis (roadmap 2.3 Genesis (a))",
        "source_osm": extract.source,
        "osm_ways_seen": extract.ways_seen,
        "osm_ways_kept": len(extract.ways),
        "osm_ways_skipped": len(extract.skipped),
        "osm_nodes_kept": len(extract.nodes),
        "bounds_wgs84": {
            "min_lat": bounds[0],
            "min_lon": bounds[1],
            "max_lat": bounds[2],
            "max_lon": bounds[3],
        },
        "projection": {**plane.metadata(), **check.as_dict()},
        "georeferenced": False,
        "verified_against": [
            "services.inpx_parser.parse_network",
            "services.genesis.diff.load_topology",
        ],
        "not_verified_against": ["a real PTV Vissim installation"],
    }


def _assumptions() -> list[GeneratorAssumption]:
    return [
        GeneratorAssumption(
            id="LANE_WIDTH_M",
            value=LANE_WIDTH_M,
            basis=(
                "§5 states no general default lane width. 3.5 m is the width every lane of "
                "Table 5-7's worked merging example carries, and is also inpx_parser's own "
                "fallback and genesis.diff.NOMINAL_LANE_WIDTH_M."
            ),
            citation=CIT_T57_LANE_WIDTH,
        ),
        GeneratorAssumption(
            id="INTERSECTION_SETBACK_M",
            value=INTERSECTION_SETBACK_M,
            basis=(
                "§5 states no setback. Links stop this far short of a junction node so a "
                "connector has somewhere to live; clamped to a quarter of the segment length "
                "so a short block cannot produce a degenerate link."
            ),
        ),
        GeneratorAssumption(
            id="NODE_HALF_SIZE_M",
            value=NODE_HALF_SIZE_M,
            basis=(
                "§5 states no node box size. data/practice.inpx — the only .inpx exemplar on "
                "this machine — uses a 30 m square, i.e. this half-size."
            ),
        ),
        GeneratorAssumption(
            id="NODE_MERGE_TOLERANCE_M",
            value=NODE_MERGE_TOLERANCE_M,
            basis=NODE_MERGE_TOLERANCE_BASIS,
            citation=CIT_T57_LANE_WIDTH,
        ),
        GeneratorAssumption(
            id="BOUNDARY_BAND_M",
            value=BOUNDARY_BAND_M,
            basis=(
                "§5 states no model-boundary rule. A way terminal this close to the model's "
                "edge — the file's declared <bounds> when it has one, otherwise the extent of "
                "the kept geometry — is treated as a road LEAVING the model rather than a "
                "cul-de-sac, so no U-turn is emitted there and the study boundary does not "
                "become a reflecting wall. The band is project.MAX_PLANIMETRIC_ERROR_M, the "
                "projection's own budget, i.e. the smallest distance distinguishable in this "
                "frame at all."
            ),
        ),
        GeneratorAssumption(
            id="THROUGH_ANGLE_DEG",
            value=THROUGH_ANGLE_DEG,
            unit="deg",
            basis=(
                "§5 states no through/turn threshold. A heading change at or under this is "
                "classified 'through'; Table 5-8's advisory speeds are attached only to the "
                "turning classes."
            ),
        ),
    ]


def _origin_lat(draft: DraftNetwork) -> float:
    return draft.metadata.get("projection", {}).get("origin_lat_deg", 0.0)


def _compliance(
    draft: DraftNetwork,
    extract: OsmExtract,
    ways: list[OsmWay],
    naive_link_count: int,
    check: ExtentCheck,
) -> ComplianceReport:
    items: list[ComplianceItem] = []

    # --- §5.1.2 -------------------------------------------------------------
    items.append(
        ComplianceItem(
            rule_id="5.1.2-software-version",
            section="§5.1.2",
            doc_page=179,
            state=ComplianceState.NOT_CHECKABLE,
            reason=REASON_REQUIRES_VISSIM,
            citation=CIT_S512_SOFTWARE_VERSION,
            evidence={"emitted_network_version_attr": "2400 (copied from data/practice.inpx)"},
            detail={
                "ko": "이 장비에는 Vissim이 없어 어떤 버전이 이 파일을 여는지 확인할 수 없습니다.",
                "en": "No Vissim on this machine, so which version opens this file is unverified.",
                "ja": "この端末にVissimがなく、どのバージョンで開けるか確認できません。",
            },
        )
    )

    # --- §5.2.1.1 left-hand traffic ----------------------------------------
    two_way = [w for w in ways if not w.is_oneway]
    items.append(
        ComplianceItem(
            rule_id="5.2.1.1-left-hand-traffic",
            section="§5.2.1.1",
            doc_page=180,
            state=ComplianceState.APPLIED,
            citation=CIT_S5211_LEFT_HAND,
            instances=len(draft.links),
            evidence={
                "two_way_osm_ways_split": len(two_way),
                "one_way_osm_ways_unoffset": len(ways) - len(two_way),
                "directional_links": len(draft.links),
                "side_rule": "carriageway offset to the LEFT normal of its own travel direction",
            },
            detail={
                "ko": "양방향 way는 방향별 링크 2개로 분할하고, 각 링크를 자기 진행방향 기준 좌측으로 (차로수x3.5)/2 만큼 이격했습니다. 일방통행 way의 중심선은 이미 그 차도의 중심선이므로 이격하지 않습니다.",
                "en": "Each two-way way became two directional links, each offset to the LEFT of its own direction of travel by (lanes x 3.5)/2 m. A one-way way is already its carriageway's own centreline, so it is not offset.",
                "ja": "双方向wayは方向別リンク2本に分割し、各リンクを自らの進行方向の左側へ (車線数x3.5)/2 だけオフセットしました。一方通行wayの中心線は既に当該車道の中心線であるためオフセットしません。",
            },
        )
    )
    items.append(
        ComplianceItem(
            rule_id="5.2.1.1-network-settings-figure",
            section="§5.2.1.1",
            doc_page=180,
            state=ComplianceState.NOT_CHECKABLE,
            reason=REASON_FIGURE_LOST,
            citation=CIT_S5211_LEFT_HAND,
            evidence={"lost_figure": "Figure 5-1 'Recommended vehicle behaviours' (doc p.180)"},
            detail={
                "ko": "좌측통행 한 문장만 본문에 남았고, Network Settings 나머지 권장값은 그림에만 있어 추정하지 않습니다.",
                "en": "Only the left-hand-traffic sentence survived in the text; the rest of the recommended Network Settings existed only in the figure and is not guessed.",
                "ja": "左側通行の一文だけが本文に残り、Network Settingsの他の推奨値は図のみに存在したため推測しません。",
            },
        )
    )

    # --- §5.2.1.2 units -----------------------------------------------------
    items.append(
        ComplianceItem(
            rule_id="5.2.1.2-metric-units",
            section="§5.2.1.2",
            doc_page=181,
            state=ComplianceState.APPLIED,
            citation=CIT_S5212_UNITS,
            instances=len(draft.links) + len(draft.connectors),
            evidence={"coordinate_unit": "m", "lane_width_unit": "m"},
            detail={
                "ko": "모든 좌표·차로폭이 미터입니다.",
                "en": "Every coordinate and lane width is in metres.",
                "ja": "すべての座標・車線幅はメートルです。",
            },
        )
    )
    items.append(
        ComplianceItem(
            rule_id="5.2.1.2-units-tab-figure",
            section="§5.2.1.2",
            doc_page=181,
            state=ComplianceState.NOT_CHECKABLE,
            reason=REASON_FIGURE_LOST,
            citation=CIT_S5212_UNITS,
            evidence={"lost_figure": "Figure 5-2 'Recommended units' (doc p.181)"},
        )
    )

    # --- §5.3.1.1 georeference (a named violation) --------------------------
    items.append(
        ComplianceItem(
            rule_id="5.3.1.1-coordinate-alignment",
            section="§5.3.1.1",
            doc_page=191,
            state=ComplianceState.VIOLATED,
            reason=REASON_NO_GEOREFERENCE,
            citation=CIT_S5311_COORDINATE_ALIGNMENT,
            instances=1,
            subjects=["<whole draft>"],
            evidence={
                "origin_lat_deg": _origin_lat(draft),
                "planimetric_error_bound_m": round(check.bound_m, 3),
                "budget_m": MAX_PLANIMETRIC_ERROR_M,
            },
            detail={
                "ko": "이 초안 좌표는 기록된 원점 기준 상대 미터일 뿐 측지 기준이 없습니다. 항공사진과의 정합은 모델러가 직접 해야 하며, 이 생성기는 그 규칙을 충족하지 못했음을 명시합니다.",
                "en": "These coordinates are relative metres about a recorded origin with no datum. Aligning them to aerial imagery is the modeller's step; this generator states plainly that it has not satisfied the rule.",
                "ja": "この草案の座標は記録された原点基準の相対メートルであり測地基準がありません。航空写真との整合はモデラーの作業であり、本生成器はこの規則を満たしていないことを明示します。",
            },
        )
    )

    # --- §5.3.2.1 queue containment ----------------------------------------
    boundary_links = [
        link
        for link in draft.links
        if not any(c.from_link == link.id for c in draft.connectors)
        or not any(c.to_link == link.id for c in draft.connectors)
    ]
    shortest = min((link.length_m for link in boundary_links), default=0.0)
    items.append(
        ComplianceItem(
            rule_id="5.3.2.1-queue-containment",
            section="§5.3.2.1",
            doc_page=192,
            state=ComplianceState.NOT_CHECKABLE,
            reason=REASON_REQUIRES_SITE_OBSERVATION,
            citation=CIT_S5321_QUEUE_CONTAINMENT,
            evidence={
                "boundary_links": len(boundary_links),
                "shortest_boundary_link_m": round(shortest, 1),
            },
            detail={
                "ko": "관측 대기행렬 길이가 있어야 판정할 수 있습니다. 참고로 경계 링크 중 최단 길이만 제시합니다.",
                "en": "Deciding this needs an observed queue length; the shortest boundary link is reported as a starting point only.",
                "ja": "判定には観測された待ち行列長が必要です。参考として境界リンクの最短長のみ示します。",
            },
        )
    )

    # --- §5.3.3 minimise ----------------------------------------------------
    items.append(
        ComplianceItem(
            rule_id="5.3.3-minimise-links-and-connectors",
            section="§5.3.3",
            doc_page=193,
            state=ComplianceState.APPLIED,
            citation=CIT_S533_MINIMISE,
            instances=len(draft.links) + len(draft.connectors),
            evidence={
                "links": len(draft.links),
                "connectors": len(draft.connectors),
                "links_if_split_at_every_osm_vertex": naive_link_count,
                "u_turn_connectors_suppressed": True,
            },
            detail={
                "ko": "way를 교차 노드에서만 분할했습니다(모든 정점에서 분할할 때 대비 링크 수 비교값 제시). U턴 커넥터는 생성하지 않습니다.",
                "en": "Ways are split only at junction nodes (the count for splitting at every vertex is given for comparison), and no U-turn connectors are generated.",
                "ja": "wayは交差ノードでのみ分割しました(全頂点で分割した場合の本数も併記)。Uターンコネクタは生成しません。",
            },
        )
    )
    lane_change_connectors = [c for c in draft.connectors if c.lane_change != "same"]
    items.append(
        ComplianceItem(
            rule_id="5.3.3-lane-gain-drop-one-connector",
            section="§5.3.3",
            doc_page=193,
            state=ComplianceState.APPLIED,
            citation=CIT_S533_ONE_CONNECTOR,
            instances=len(lane_change_connectors),
            evidence={
                "lane_gain_or_drop_movements": len(lane_change_connectors),
                "connectors_per_movement": 1,
                "connector_lanes": "min(approach, exit)",
            },
            detail={
                "ko": "차로 증감이 있는 이동도 커넥터 1개로만 코딩했습니다.",
                "en": "Every lane gain or drop is coded with exactly one connector.",
                "ja": "車線の増減がある移動もコネクタ1本のみで符号化しています。",
            },
        )
    )
    items.append(
        ComplianceItem(
            rule_id="5.3.3-lane-width",
            section="Table 5-7",
            doc_page=195,
            state=ComplianceState.APPLIED,
            citation=CIT_T57_LANE_WIDTH,
            instances=len(draft.links),
            evidence={"lane_width_m": LANE_WIDTH_M},
            detail={
                "ko": "§5에는 일반 기본 차로폭이 없습니다. Table 5-7의 3.5 m를 모든 차로에 적용했고, 이는 생성기 가정으로 기록됩니다.",
                "en": "§5 states no general default lane width; Table 5-7's 3.5 m is applied to every lane and recorded as a generator assumption.",
                "ja": "§5に一般的な既定車線幅はありません。Table 5-7の3.5 mを全車線に適用し、生成器の仮定として記録します。",
            },
        )
    )

    # --- §5.3.3.1 / §5.3.3.2 (outside the emitted subset) -------------------
    items.append(
        ComplianceItem(
            rule_id="5.3.3.1-lane-change-distance",
            section="§5.3.3.1",
            doc_page=193,
            state=ComplianceState.NOT_CHECKABLE,
            reason=REASON_OUTSIDE_EMITTED_SUBSET,
            citation=CIT_S5331_LANE_CHANGE_DISTANCE,
            evidence={"recommended_start_m": 500},
            detail={
                "ko": "커넥터의 lane change distance 속성은 이 초안이 쓰지 않습니다(파서가 읽지 않는 속성은 내보내지 않는다는 규칙).",
                "en": "The connector lane-change-distance attribute is not written by this draft (nothing is emitted that this repo's parser cannot read back).",
                "ja": "コネクタのlane change distance属性はこの草案では書き出しません(パーサが読めない属性は出力しない方針)。",
            },
        )
    )
    items.append(
        ComplianceItem(
            rule_id="5.3.3.1-emergency-stop-position",
            section="§5.3.3.1",
            doc_page=193,
            state=ComplianceState.NOT_CHECKABLE,
            reason=REASON_DATA_ABSENT_FROM_OSM,
            citation=CIT_S5331_EMERGENCY_STOP,
            detail={
                "ko": "접근로 실선 길이는 OSM에 없는 값입니다.",
                "en": "The length of the approach lane's solid line is not an OSM attribute.",
                "ja": "流入部の実線長はOSMに存在しない値です。",
            },
        )
    )
    items.append(
        ComplianceItem(
            rule_id="5.3.3.2-merging-behaviour-type",
            section="§5.3.3.2",
            doc_page=194,
            state=ComplianceState.NOT_CHECKABLE,
            reason=REASON_OUTSIDE_EMITTED_SUBSET,
            citation=CIT_S5332_MERGING,
            detail={
                "ko": "link behaviour type과 lane change distance는 이 기하 전용 범위 밖입니다.",
                "en": "Link behaviour type and lane-change distance are outside this geometry-only scope.",
                "ja": "link behaviour typeとlane change distanceは幾何のみの範囲外です。",
            },
        )
    )
    items.append(
        ComplianceItem(
            rule_id="5.3.3.2-merging-layout-figure",
            section="§5.3.3.2",
            doc_page=194,
            state=ComplianceState.NOT_CHECKABLE,
            reason=REASON_FIGURE_LOST,
            citation=CIT_S5332_MERGING,
            evidence={
                "lost_figure": "Figure 5-11 'Merging section' (doc p.194)",
                "recovered": "Table 5-7 attributes only, not the layout",
            },
        )
    )

    # --- §5.3.3.3 dedicated pocket lane (data-driven violation) -------------
    pocket_ways = sorted(
        {way.id for way in ways if way.dedicated_turn_lanes}
    )
    if pocket_ways:
        items.append(
            ComplianceItem(
                rule_id="5.3.3.3-dedicated-pocket-lane",
                section="§5.3.3.3",
                doc_page=195,
                state=ComplianceState.VIOLATED,
                citation=CIT_S5333_DEDICATED_POCKET,
                instances=len(pocket_ways),
                subjects=[f"osm way {way_id}" for way_id in pocket_ways],
                evidence={"detected_from": "turn:lanes entries naming a single movement"},
                detail={
                    "ko": "turn:lanes가 전용 회전차로를 선언한 way입니다. 이 초안은 해당 차로를 접근 링크의 한 차로로만 코딩했고 별도 포켓 링크를 만들지 않았습니다.",
                    "en": "turn:lanes on these ways declares a dedicated turn lane; this draft codes it as a lane of the approach link, not as the separate pocket link §5.3.3.3 recommends.",
                    "ja": "turn:lanesが専用右左折車線を宣言したwayです。本草案はそれを流入リンクの1車線として符号化し、別ポケットリンクは作成していません。",
                },
            )
        )
    else:
        items.append(
            ComplianceItem(
                rule_id="5.3.3.3-dedicated-pocket-lane",
                section="§5.3.3.3",
                doc_page=195,
                state=ComplianceState.APPLIED,
                citation=CIT_S5333_DEDICATED_POCKET,
                instances=0,
                evidence={"ways_declaring_a_dedicated_turn_lane": 0},
                detail={
                    "ko": "입력에 전용 회전차로 선언이 없어 별도 포켓 링크를 만들 대상이 없었습니다(공허하게 충족).",
                    "en": "No way in the input declares a dedicated turn lane, so there was nothing to code as a pocket link — honoured vacuously.",
                    "ja": "入力に専用右左折車線の宣言がなく、ポケットリンク化の対象がありませんでした(空虚に充足)。",
                },
            )
        )

    # --- §5.3.3.4 bus / HV lane (data-driven violation) ---------------------
    bus_ways = sorted({way.id for way in ways if way.declares_bus_lane})
    if bus_ways:
        items.append(
            ComplianceItem(
                rule_id="5.3.3.4-bus-and-hv-lane",
                section="§5.3.3.4",
                doc_page=196,
                state=ComplianceState.VIOLATED,
                citation=CIT_S5334_BUS_AND_HV_LANE,
                instances=len(bus_ways),
                subjects=[f"osm way {way_id}" for way_id in bus_ways],
                evidence={"lane_closures_emitted": 0},
                detail={
                    "ko": "버스/PSV 차로를 선언한 way입니다. 이 초안은 lane closure를 쓰지 않으므로 규칙을 충족하지 못합니다 — 별도 링크로 만들지도 않았으니 조용한 오코딩은 아닙니다.",
                    "en": "These ways declare a bus/PSV lane. This draft writes no lane closures, so the rule is not satisfied — it also did not code them as separate links, so nothing was silently mis-coded.",
                    "ja": "バス/PSV車線を宣言したwayです。本草案はlane closureを出力しないため規則を満たしません — 別リンク化もしていないので黙った誤符号化ではありません。",
                },
            )
        )
    else:
        items.append(
            ComplianceItem(
                rule_id="5.3.3.4-bus-and-hv-lane",
                section="§5.3.3.4",
                doc_page=196,
                state=ComplianceState.APPLIED,
                citation=CIT_S5334_BUS_AND_HV_LANE,
                instances=0,
                evidence={"ways_declaring_a_bus_or_psv_lane": 0},
                detail={
                    "ko": "입력에 버스/PSV 차로 선언이 없어 별도 링크로 코딩된 버스차로가 없습니다(공허하게 충족).",
                    "en": "No way declares a bus/PSV lane, so no bus lane was coded as a separate link — honoured vacuously.",
                    "ja": "入力にバス/PSV車線の宣言がなく、別リンク化されたバス車線はありません(空虚に充足)。",
                },
            )
        )

    # --- §5.3.3.5 pedestrian crossings --------------------------------------
    items.append(
        ComplianceItem(
            rule_id="5.3.3.5-pedestrian-crossings",
            section="§5.3.3.5",
            doc_page=196,
            state=ComplianceState.NOT_CHECKABLE,
            reason=REASON_REQUIRES_SITE_OBSERVATION,
            citation=CIT_S5335_PEDESTRIAN_CROSSINGS,
            detail={
                "ko": "규칙이 '보행 횡단이 성능에 영향을 준다고 관측되는 곳'을 전제로 하며, §5.3.3.5는 횡단 폭·길이·보행속도 기본값을 전혀 제시하지 않습니다.",
                "en": "The rule is conditioned on crossings being observed to affect performance, and §5.3.3.5 gives no crossing width, length or pedestrian-speed default at all.",
                "ja": "規則は「横断が性能に影響すると観測される場所」を前提とし、§5.3.3.5は横断幅・長さ・歩行速度の既定値を一切示していません。",
            },
        )
    )

    # --- §5.3.4 / §5.3.5 ----------------------------------------------------
    with_maxspeed = sorted({way.id for way in ways if way.maxspeed})
    items.append(
        ComplianceItem(
            rule_id="5.3.4-desired-speed-decisions",
            section="§5.3.4",
            doc_page=197,
            state=ComplianceState.NOT_CHECKABLE,
            reason=REASON_OUTSIDE_EMITTED_SUBSET,
            citation=CIT_S534_DESIRED_SPEED,
            evidence={"osm_ways_carrying_maxspeed": len(with_maxspeed)},
            detail={
                "ko": "희망속도 결정지점은 내보내지 않습니다. maxspeed 태그가 있는 way 수만 후속 단계용으로 보고합니다.",
                "en": "No desired speed decisions are emitted; the count of ways carrying a maxspeed tag is reported for a later stage only.",
                "ja": "希望速度決定地点は出力しません。maxspeedタグを持つway数のみ後続工程向けに報告します。",
            },
        )
    )
    advisory = {
        turn: len([c for c in draft.connectors if c.turn == turn])
        for turn in (TURN_LEFT, TURN_RIGHT)
    }
    items.append(
        ComplianceItem(
            rule_id="5.3.5-reduced-speed-areas",
            section="§5.3.5",
            doc_page=197,
            state=ComplianceState.NOT_CHECKABLE,
            reason=REASON_OUTSIDE_EMITTED_SUBSET,
            citation=CIT_S535_REDUCED_SPEED,
            evidence={
                "left_turn_movements": advisory[TURN_LEFT],
                "right_turn_movements": advisory[TURN_RIGHT],
                "table_5_8_advisory": "attached to each turning connector, applied to nothing",
            },
            detail={
                "ko": "감속구간은 내보내지 않습니다. Table 5-8의 회전유형별 권장속도는 각 회전 커넥터에 참고값으로만 붙여 두었습니다.",
                "en": "No reduced speed areas are emitted; Table 5-8's per-turn-type speeds ride along on each turning connector as advisory data only.",
                "ja": "減速区間は出力しません。Table 5-8の回転種別ごとの推奨速度は各回転コネクタに参考値として付与するのみです。",
            },
        )
    )
    items.append(
        ComplianceItem(
            rule_id="5.3.5-reduced-speed-areas-table",
            section="Table 5-8",
            doc_page=197,
            state=ComplianceState.NOT_CHECKABLE,
            reason=REASON_REQUIRES_SITE_OBSERVATION,
            citation=CIT_T58_TURN_SPEEDS,
            evidence={
                "left_turn_advisory_kmh": TABLE_5_8_TURN_ADVISORY[TURN_LEFT][
                    "short_desired_speed_kmh"
                ],
                "right_turn_advisory_kmh": TABLE_5_8_TURN_ADVISORY[TURN_RIGHT][
                    "short_desired_speed_kmh"
                ],
            },
            detail={
                "ko": "Table 5-8은 '출발점'으로만 제시된 값이고, 실제 반경·시거는 현장 확인이 필요합니다.",
                "en": "Table 5-8's values are offered as a starting point only; the actual radii and sight distances need site confirmation.",
                "ja": "Table 5-8の値は「出発点」に過ぎず、実際の半径・視距は現地確認が必要です。",
            },
        )
    )

    # --- §5.6.1.3 the 12-item input check -----------------------------------
    items.append(
        ComplianceItem(
            rule_id="5.6.1.3-model-input-check",
            section="§5.6.1.3",
            doc_page=211,
            state=ComplianceState.NOT_CHECKABLE,
            reason=REASON_OUTSIDE_EMITTED_SUBSET,
            citation=CIT_S5613_MODEL_INPUT_CHECK,
            evidence={
                "items_this_draft_writes": "number of lanes, lane width, feasible movements",
                "items_outside_this_draft": (
                    "lane closure, link behaviour type, elevation (Z-height), vehicle speed, "
                    "priority control, signalised control, traffic demands, vehicle routes, "
                    "dynamic assignment cost parameters"
                ),
            },
            detail={
                "ko": "§5.6.1.3의 12개 점검 항목 중 이 초안이 쓰는 것은 3개뿐입니다. 나머지는 후속 행(3.1 신호, 3.2 수요)에서 채워집니다.",
                "en": "Of §5.6.1.3's 12 input-check items this draft writes three; the rest belong to later rows (3.1 signals, 3.2 demand).",
                "ja": "§5.6.1.3の12点検項目のうち本草案が書くのは3項目のみで、残りは後続行(3.1 信号、3.2 需要)で埋まります。",
            },
        )
    )

    return ComplianceReport(items=items)
