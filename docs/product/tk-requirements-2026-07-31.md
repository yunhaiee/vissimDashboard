# TK 요구사항 명세 — 2026-07-31 미팅 (v2.1)

**v2 변경점 (사용자 결정):** ① 논문은 뒤로 — 미국행 이후에도 쓸 수 있음. **제품 구축이
우선순위 1.** ② 비용 모델 삭제 — TK의 "다리 차선" 비용 언급은 시뮬레이션을 *왜* 쓰는지에
대한 설명이었지 기능 요구가 아님. 예시는 동기 서사로만 유지.

**v2.1 결정 반영 (사용자, 2026-07-31 저녁):**
- **타깃: 한국/대전 먼저.** 단, 최종 목표는 **어느 사이트·어느 포맷에서든** 동작 — 지역/국가마다
  데이터 포맷이 전부 다르므로, 데이터 임포트는 처음부터 **어댑터(플러그인) 아키텍처**로: 코어는
  포맷 중립 내부 모델, 대전/SCATS는 각각 첫 번째·두 번째 어댑터일 뿐.
- **Genesis 스케일: microsimulation 먼저.** meso는 당분간 보류 (질문 3 답변됨).
- **보고서: 다중 포맷 지원.** HWP 등 어려운 포맷은 **기존 도구/라이브러리 우선 활용**; 기존
  도구가 없거나 포맷 하나 추가에 과도한 시간이 들면 **중단하고 사용자에게 보고 → 사용자가 결정**
  (에스컬레이션 규칙, 질문 5 답변됨).
- **AI 검증 합격선: 우리(Claude)가 초안 설정** — R1.5에 구체화함 (질문 4 답변됨).
- **부록 A/B: 사용자가 TK에게 요청해 수령 예정.** §5 Vissim은 착오였음 — **전체 PDF(289쪽)에
  이미 포함**되어 있고(197–239쪽) 정독 완료/진행.
- **Region selection UX: 우리가 목업 먼저 제시** (디자인 주도권 우리 쪽).
- **Genesis 사용자: 외부 컨설턴트 판매 제품** (질문 8 답변됨).
- **Nexus 경계: 최종적으로는 모든 제품 통합이 궁극 목표.** 당분간은 **Vissim 중심으로 적절한
  경계** — genesis 산출물은 Prism의 Vissim 워크플로 안에 속하고, 제품 간 통합은 뒤로 (질문 9
  답변됨).

Source: 미팅 직후 사용자 메모. **The big reveal: TK wants the pipeline to start one stage
earlier than our product does** — today Prism *edits and validates an existing network*; TK
wants AI to *build the network from a map*, then calibrate, then sweep scenarios, then write
the styled report. That is the consultant's entire workflow, end to end.

Motivating examples (acceptance narratives, not feature specs):
- **다리 건설**: 차선 수 대안별로 미리 시뮬레이션 (건설은 비싸므로 — 시뮬레이션의 존재 이유)
- **Phase plan 변경**: 신호 phase plan을 바꾸면 어떻게 되는가

---

## EPIC 1 — AI Network Genesis (지도에서 네트워크 자동 구축)

> "select map region and AI build … not simply rectangular selection … block/street-by-street
> … AI builds it and AI validates it and finishes building the network"

**R1.1 Region selection UI.** Street/block-granular include·exclude (polygon lasso + per-street
toggle) — 명시적으로 "사각형 아님". Output: bounded set of ways/intersections to model.
**R1.2 Geometry synthesis.** From map data (OSM baseline; aerial/RIMS-assisted correction),
generate Vissim links, connectors, lane counts, turn pockets, node footprints as .inpx XML —
the v2b "XML-build + reparse + reference-integrity" tier (currently 0/13 shipped).
**R1.3 Signal import — 어댑터 아키텍처 (v2.1).** Controllers + phasing from real signal data.
**한국/대전 어댑터가 1번**, WA SCATS가 2번 — 그러나 코어는 포맷 중립 내부 모델(controller/
program/phase/detector 스키마)이고 각 지역 포맷은 플러그인 어댑터. "어느 사이트, 어느
포맷이든"이라는 최종 목표는 어댑터 계약(contract)의 품질에 달려 있으므로, 첫 두 어댑터를
만들며 계약을 다듬는 것이 곧 세계화 작업.
**R1.3b SCATS→fixed-time conversion module.** ★MRWA 가이드라인 발견: 적응식(SCATS) 이력을
모델용 고정시간 계획으로 바꾸는 알고리즘이 **이미 받은 PDF의 LinSig 장에 명세되어 있음**
(가중평균 phase 시간, demand frequency, bonus green — Tables 3-3/3-4 + 부록 A). "파일 파싱"이
아니라 이 변환이 signal import의 실체. 가이드라인 자체가 이 방식을 승인함(op p.31: 피크
시간대 SCATS 평균 사용).
**R1.4 Demand seeding.** Vehicle inputs + compositions from detector/count data; routing from
turning counts. 15-min interval profiling (guideline-required, our multi-interval verbs ready).
**R1.5 AI network validation gate — 합격선 초안 (v2.1: 사용자 위임으로 우리가 설정).**
Build isn't done until every REFUSE-tier check passes; WARN-tier findings ride on the result.
Same refuse-with-reason doctrine as the 90-constraint registry, applied to construction:

| # | check | tier |
|---|---|---|
| V1 | **Load oracle**: 생성된 .inpx가 COM load + init 통과, Vissim critical error 0건, .err 무결 또는 전건 설명됨 | refuse |
| V2 | **Reference integrity**: XML 수준 dangling 참조 0건 (없는 링크/차로/분포/컨트롤러 참조 금지) | refuse |
| V3 | **Connectivity**: 모든 vehicle input에서 ≥1 네트워크 출구까지 도달 가능; 고아 링크/도달 불능 구역 0건 | refuse |
| V4 | **Lane continuity**: 커넥터 차로 매핑 유효(존재하는 차로만), 차로 수 불연속엔 차로변경 거리 확보 | refuse |
| V5 | **Signal-geometry consistency**: 임포트된 phasing의 모든 신호화 접근로에 signal head ≥1, 모든 signal group이 head에 매핑, 컨트롤러 없는 head 0건 | refuse |
| V6 | **Demand coverage**: 모든 input에 routing decision 커버리지; composition relFlow 합 정상; input 볼륨 vs 링크 용량(기존 latent_demand 제약 재사용) | refuse(구조)/warn(용량) |
| V7 | **Geometry sanity**: 링크 길이 vs 지도 거리 오차 허용치 내, 0-길이/중첩 링크 0건, 도로 등급별 희망속도분포 배정 | warn |
| V8 | **Conflict coverage**: 비신호 상충점에 conflict area 또는 priority rule 존재; Passive 개수 명시 보고 (기존 111-Passive 경고의 일반화) | warn |
| V9 | **Measurement readiness**: 정지선마다 queue counter, 주요 corridor에 travel-time section 자동 배치 완료 | warn |

★가이드라인이 이 게이트의 상위 체크리스트를 제공(op §2.12). 임계값 세부(오차 허용치 등)는
구현 시 측정으로 확정하되 표의 tier 구조는 확정안. **The validation gate is what makes AI
construction sellable.**
**R1.6 Genesis benchmark (acceptance test).** ★핵심 아이디어: **Murdoch 모델을 벤치마크로** —
같은 지역을 지도+신호+카운트 데이터에서 AI로 재구축하고 TK의 수작업 모델과 구조적으로
diff. 정답지가 이미 손에 있는 유일한 지역. 재현율/오류 유형이 곧 genesis의 품질 지표.

**Have today:** .inpx parser, shadow model, load_check oracle, constraint machinery,
COM round-trip — R1.5's foundation. **Missing:** entire v2b tier, both data formats, R1.3b.

## EPIC 2 — AI Calibration Loop (보정 자동화)

**R2.1 Anomaly detection** on run results: phantom queues, blocked connectors, starved links,
teleports/removals, latent demand (★가이드라인 수치 채택: 미해소 수요 <1%, meso p.74).
**R2.2 Fix proposal loop.** Anomaly → candidate parameter fixes (lane-change distance,
connector config, priority gaps, RSAs) → approval-gated apply → re-run → re-check.
**R2.3 Reality check — first-class feature.** TK's own caveat: before "fixing," compare
against field/historical data; if the pattern is real, it's BEHAVIOR — record the finding,
refuse to calibrate it away. (Refusal doctrine extended upstream.)
**R2.4 GEH-scored convergence** with category-aware profiles (Cat 1/2/3 × total/core — meso
tables in hand; micro thresholds arrive with guideline §5).
**R2.5 Data quality gate (신규, 가이드라인 유래).** Observed data itself gets validated against
the collection standards before use: neutral-day rule, ≥3h peak coverage, ≥6 travel-time
observations, ≥10 queue samples, 15-min intervals. Bad calibration data refused with reasons —
same doctrine, one layer earlier.

**Have today:** run gate + post-run lie detectors, GEH dashboard + real-counts import,
batch + rollback (the loop's apply/undo), health check. **Missing:** anomaly catalog,
fix-mapping, loop orchestration, historical comparison store, R2.5.

## EPIC 3 — Scenario Intelligence (시나리오 스윕 + 최적안 결론)

**R3.1 Demand stress ("+10% 차량").** One command: scale demand N%, run, delta report.
**R3.2 Alternative enumeration.** Bounded, user-editable candidate list before anything runs.
**R3.3 Sweep execution.** N alternatives × M demand levels via job queue, overnight-capable.
★가이드라인 요구를 MVP부터 내장: **후보당 ≥5 시드, 중앙값/평균 보고** — 1-시드 스윕의 순위는
노이즈 순위이므로 처음부터 다중 시드가 아니면 결론 기능 자체가 성립 안 함.
**R3.4 Ranked conclusion** with the full analysis trail (every candidate's numbers visible).
**R3.5 (삭제됨 — v2)** 비용 모델 없음. 다리 예시는 R3.2/R3.3의 대안 나열("2차선/3차선/4차선
케이스")로 자연 표현되며, 비용 판단은 사용자 몫.

**Have today:** compare dashboard, batch, run gate, rollback, the real project's
Base/DoMin/TMP×OptSig structure. **Missing:** sweep orchestrator, multi-seed automation,
ranking/synthesis layer.

## EPIC 4 — Report Engine (보고서 자동 생성 + 스타일)

**R4.0 Content spine first (신규 인식).** ★스타일은 피부, 뼈대는 규정: MRWA가 요구하는 보고서
구성(보정/검증 분리 서술, 데이터 문서화, **파라미터 변경 기록+근거** — 우리 액션 레저가 이미
생산, GEH 표, 체크리스트)이 템플릿의 스파인. 한국 교통영향평가 양식이 제2 타깃.
**R4.1 Data extraction.** Every figure/table exportable (CSV/XLSX).
**R4.2 Figure customization.** Size/color/font per figure + 회사 스타일 프리셋.
**R4.3 Style-from-sample.** Upload past report → extract layout/typography/figure conventions
→ new report fitted to it. Phase: v1 = DOCX (palette/fonts/logo/heading structure); **HWP는
별도 스코프** (기술적으로 독립 과제 — 우선순위 질문 참조).

## EPIC 5 — Full Phase-Plan Editing (신호 phase plan 변경)

Beyond green-splits: add/remove phases, sequences, intergreen-matrix-verified redesign (v2c
bullet). Rides on R1.3/R1.3b's signal data structures. TK's second example lives here.

---

## Sequencing v2 — 제품 우선 (논문은 미국행 이후)

**Phase 1 — 지금 (foundation sprint):**
1. **R3.1+R3.3 sweep MVP, 다중 시드 내장** — TK의 "+10%" 질문에 그대로 답하는 데모. 기존
   기계(구간별 입력 + 배치 + 런 게이트 + 비교)의 조합.
2. **R4.1/R4.2 report data-extract + styling v1** — 저위험, 매일 보이는 가치.
3. **v2b 착수** — XML-build + 구조 검증 + 측정 인프라 배치. genesis와 검증 게이트 둘 다가
   딛고 설 벽. 카탈로그의 작은 불릿부터 (lane closure, measurement infra).
4. **R1.3b SCATS 변환 모듈** — 알고리즘 명세는 PDF에 있음; TK 데이터 없이도 가이드라인
   수식으로 선행 구현 + 단위 테스트 가능.
5. **A4 데이터 인테이크 스파이크** — TK 파일 도착 즉시 (대전 + WA 파서, 포맷 명세 문서).
6. **§5 Vissim 가이드라인 + 부록 A/B 확보** — TK에게 요청 (부록 A가 R1.3b의 완결 명세).

**Phase 2:**
7. **EPIC 2 calibration loop v1** (anomaly catalog → supervised loop → R2.3 reality check →
   R2.5 data quality gate).
8. **R3.2–R3.4 full sweep + ranked conclusion.**
9. **R4.3 style-from-sample v1 (DOCX).**
10. **Genesis milestone (a):** geometry-only draft from map, small corridor — **benchmark =
    Murdoch 재구축 diff (R1.6).**

**Phase 3:**
11. **Genesis (b)(c)(d):** +signals (R1.3/R1.3b), +demand (R1.4), validation gate closes (R1.5).
12. **EPIC 5 phase-plan redesign.**
13. **Meso/hybrid 여부 결정** (아래 질문) — genesis를 sub-regional 스케일로 확장할지.

**논문:** Phase 4 eval harness(#9)는 보류. 단, EPIC 2/3의 계측(액션 로그, 스윕 텔레메트리)은
설계상 논문 데이터를 자연 축적하도록 유지 — 나중에 논문 쓸 때 공짜 데이터가 되게.

## Open questions — v2.1 이후 남은 것

**답변 완료 (v2.1):** ~~첫 시장~~ → 한국/대전 먼저, 최종은 어댑터로 전 세계.
~~genesis 스케일~~ → micro 먼저. ~~합격선~~ → R1.5 초안 확정(우리 설정). ~~§5/부록~~ →
§5는 PDF에 있었음(정독 완료), 부록은 사용자가 TK에게 요청. ~~Region UX~~ → 우리가 목업
먼저. ~~genesis 사용자~~ → 외부 컨설턴트 판매 제품. ~~Nexus 경계~~ → 당분간 Vissim 중심,
궁극적으로 전 제품 통합. ~~HWP~~ → 기존 도구 우선, 과도하면 보고 후 사용자 결정.

**남은 질문:**
1. **대전 신호/카운트 샘플 파일** — 언제, 어떤 형태, 라이선스/반출 제약? (WA SCATS 샘플도.)
2. **부록 A(신호 데이터)·B(포화류)·C(장래수요)** — 사용자가 TK에게 요청, 수령 대기.
3. **Vissim 라이선스 수** — 스윕 병렬도 = 라이선스 수. 1개면 야간 직렬 큐가 상한.
4. **OSM ODbL 라이선스** — 상용 제품 고지/공유 의무; 대전 데이터 사용권.
5. **미국행 일정과 인수인계** — 자동화/문서화 수준을 여기 맞춰야 함.
6. **다음 TK 데모의 정의** — genesis MVP 합격 서사를 측정 가능한 문장으로 미리 합의
   (제안: "Murdoch 일부를 지도+데이터에서 재구축, R1.5 게이트 통과 + 수작업 모델과 구조 diff
   일치율 N%").
7. **보정 루프 성공 지표** — GEH 개선폭? 엔지니어 시간 절감?
8. **HWP 도구 현황 조사 결과 보고** — python 생태계(pyhwp/hwpx 등) + Hancom COM 자동화
   조사 후, "가능/비용" 보고서를 사용자에게 (에스컬레이션 규칙의 1회차 실행).

## §5 Vissim Guidelines 정독 결과 (v2.1 — 289쪽 전체 확인 후)

**정정: §5는 PDF 안에 있었음** (197–239쪽; 이전 "146쪽에서 잘림" 판단은 파일 메타데이터
착오). 부록 A/B/C만 별도 문서 — 사용자가 TK에게 요청 예정. §5가 준 것:

1. **Vissim micro 검증 수치 확보 (Table 5-12/13/14, p.231-234):** GEH<5 합격률
   Cat1/2/3 = 95/85/80%, GEH<10 = 100/95/90%, R²>0.95/0.95/0.9, 통행시간 ±15% 또는 1분
   (100/90/85%), 신호 주기 ±3초 또는 5%, 녹색 ±3초 또는 10%, 포화류 ±10%.
   ★**우리 대시보드의 Category 1 기준(≥95% GEH<5, 100% GEH<10)이 Table 5-12 Cat 1과
   정확히 일치** — 이미 규정 그대로 구현되어 있음이 확인됨. Cat 2/3 프로파일만 추가하면 됨.
2. **런 규율:** 시드 5–10개, 전 시나리오 동일 시드, 평균 보고, 시뮬레이션 해상도 10 steps/s
   고정(시나리오 간 변경 금지), warm-up = max(15분, 최장 통행시간×2), cool-down 동일.
   → 스윕 MVP(R3.3)의 스펙이 그대로 확정됨.
3. **★MRWA 디폴트 팩 — genesis의 공짜 자산:** §5의 표들이 사실상 "규정 준수 네트워크
   초기화 라이브러리": 중차량 12종 가감속 곡선(Table 5-2), 포화류→safety distance 매핑
   (Table 5-6), 이동류별 critical gap/follow-up(Table 5-9 — 우리 priority-rule verb의 경고
   범위로도 즉시 인용 가능), conflict area front/rear gap(Table 5-10), 회전반경별 감속구간
   설계속도(Table 5-8), 검지기 배치 기본값(정지선 2.0m 후방, 길이 4.5m), 버스 정차 기본값
   (정류장 10s/환승 20s ±5s — **Murdoch 모델의 공유 분포 10s±2s와 근사, BusDwell 곡선의
   맥락 확인**), 차종별 색상 분포. genesis는 이 팩으로 인스턴스화하면 "MRWA 기본값으로
   생성됨"이라고 보고서에 쓸 수 있음.
4. **신호 코딩 규칙:** 신호그룹 번호는 SCATS와 일치 필수, SCATS 소수값은 항상 올림,
   min green/yellow+allred는 컨트롤러 값 그대로(변경 금지). VAP(.vv) 구조 상세(§5.4.2.5)는
   EPIC 5(phase plan 편집)의 설계 참고자료.
5. **경계/코딩 관행(R1.2에 반영):** 링크/커넥터 최소화, diverge 차로변경거리 500m 시작점,
   차로폭 3.5m 표준, 버스전용은 별도 링크가 아니라 lane closure로, 대기행렬이 모델 경계를
   넘으면 한계로 보고.

## 가이드라인(두 PDF)이 이 계획에 주는 통찰 — 요약

**우려/변경:** ① 규정은 현장 요소를 요구(현장 방문, 포화류 실측, 경험자 데이터 수집) — "AI가
다 만든다"가 아니라 "AI가 초안을 만들고 규정의 검증 절차가 그것을 단련한다"로 포지셔닝; 제품은
인간 단계를 체크리스트로 오케스트레이션. ② 모델 수용 결정권은 Main Roads에 있고 카테고리·
기준은 **모델링 시작 전 합의** 필요 — TK의 "project scoping" 단계는 잡담이 아니라 컴플라이언스
게이트: **scoping document 생성기**가 파이프라인의 첫 산출물이 되어야 함. ③ WA 신호 현실은
SCATS(적응식) — 고정시간 근사 변환(R1.3b)이 필수 관문. ④ 스윕은 다중 시드 아니면 무의미
(≥5, 중앙값). ⑤ 대기행렬 검증을 위해 측정 인프라(카운터) 자동 배치가 R1.5에 포함되어야 함.

**좋은 것:** ① 파라미터 변경 기록+근거 요구 = 액션 레저가 이미 하는 일. ② R1.3b 알고리즘
명세가 PDF 안에 공짜로 있음. ③ 잠재수요 <1% 등 권위 있는 수치들을 제약에 인용 가능. ④
"비규범적, 대안 방법론 수용"(op p.5) — AI 워크플로의 규정상 근거. ⑤ Murdoch 모델이 genesis의
정답지 벤치마크. ⑥ LinSig Model Audit View 요구가 우리 network-diff 로드맵을 검증해 줌.
