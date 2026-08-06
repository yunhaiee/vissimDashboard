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
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from .osm import (
    EXTRACT,
    CIT_S5211_LEFT_HAND,
    CIT_S5212_UNITS,
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
    turn: str, approach_lanes: int, exit_lanes: int
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
    """
    count = max(1, min(approach_lanes, exit_lanes))
    approach_total = max(1, approach_lanes)
    exit_total = max(1, exit_lanes)

    if turn == TURN_LEFT:
        keep_left = True
    elif turn == TURN_RIGHT:
        keep_left = False
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


def build_draft(extract: OsmExtract, *, name: str) -> DraftNetwork:
    """OSM extract → draft network + §5 compliance report. Reports, never raises."""
    draft = DraftNetwork(name=name, source_osm=extract.source)

    if extract.refused:
        draft.refused = True
        draft.refusals = [f"{r.code}: {r.message}" for r in extract.refusals]
        return draft

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
    draft.skipped_ways = [skip.as_dict() for skip in extract.skipped]
    draft.assumptions = _assumptions()

    intersections = extract.intersection_node_ids()
    ways = sorted(extract.ways, key=lambda w: w.id)

    # --- links -------------------------------------------------------------
    links: list[DraftLink] = []
    naive_link_count = 0
    next_link_id = 1
    for way in ways:
        naive_link_count += (len(way.node_ids) - 1) * len(way.directions)
        for segment_index, segment in enumerate(_way_segments(way, intersections)):
            centreline = [projected[node_id] for node_id in segment]
            base_length = _polyline_length(centreline)
            if base_length < MIN_LINK_LENGTH_M:
                continue
            setback = min(INTERSECTION_SETBACK_M, base_length / 4.0)
            clamped = setback < INTERSECTION_SETBACK_M
            start_cut = setback if segment[0] in intersections else 0.0
            end_cut = setback if segment[-1] in intersections else 0.0
            trimmed = _trim(centreline, start_cut, end_cut)
            if _polyline_length(trimmed) < MIN_LINK_LENGTH_M:
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
    for osm_node_id in node_ids:
        approaches = sorted(
            (l for l in links if l.to_node_osm_id == osm_node_id), key=lambda l: l.id
        )
        exits = sorted(
            (l for l in links if l.from_node_osm_id == osm_node_id), key=lambda l: l.id
        )
        for approach in approaches:
            in_dx, in_dy = _heading(approach.points, at_end=True)
            for exit_link in exits:
                if approach.segment_key == exit_link.segment_key:
                    # The same carriageway reversed: a U-turn, suppressed under
                    # §5.3.3's "minimise the number of alternative paths".
                    continue
                out_dx, out_dy = _heading(exit_link.points, at_end=False)
                turn, angle = classify_turn(in_dx, in_dy, out_dx, out_dy)
                if turn == TURN_U:
                    continue
                lanes = min(approach.lanes, exit_link.lanes)
                lane_pairs = connector_lane_pairs(turn, approach.lanes, exit_link.lanes)
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
                        lanes=lanes,
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
    draft.connectors = connectors

    draft.compliance = _compliance(draft, extract, ways, naive_link_count, check)
    return draft


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
