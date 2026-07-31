# TK 요구사항 명세 — 2026-07-31 미팅 (v2, 2026-07-31 재편)

**v2 변경점 (사용자 결정):** ① 논문은 뒤로 — 미국행 이후에도 쓸 수 있음. **제품 구축이
우선순위 1.** ② 비용 모델 삭제 — TK의 "다리 차선" 비용 언급은 시뮬레이션을 *왜* 쓰는지에
대한 설명이었지 기능 요구가 아님. 예시는 동기 서사로만 유지.

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
**R1.3 Signal import.** Controllers + phasing from real signal data: **대전 스마트교차로** and
**WA SCATS** (TK will supply both).
**R1.3b SCATS→fixed-time conversion module.** ★MRWA 가이드라인 발견: 적응식(SCATS) 이력을
모델용 고정시간 계획으로 바꾸는 알고리즘이 **이미 받은 PDF의 LinSig 장에 명세되어 있음**
(가중평균 phase 시간, demand frequency, bonus green — Tables 3-3/3-4 + 부록 A). "파일 파싱"이
아니라 이 변환이 signal import의 실체. 가이드라인 자체가 이 방식을 승인함(op p.31: 피크
시간대 SCATS 평균 사용).
**R1.4 Demand seeding.** Vehicle inputs + compositions from detector/count data; routing from
turning counts. 15-min interval profiling (guideline-required, our multi-interval verbs ready).
**R1.5 AI network validation gate.** Build isn't done until machine checks pass: connectivity,
lane continuity, signal-geometry consistency, orphan objects, capacity sanity, **plus
measurement infrastructure auto-placement** (queue counters, travel-time sections — validation
later needs them; a v2b bullet). ★가이드라인이 이 게이트의 체크리스트를 사실상 제공함(op §2.12
검증 항목). **The validation gate is what makes AI construction sellable.**
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

## Open questions — planning phase에서 답할 것

**TK에게:**
1. 대전 + WA 신호/카운트 샘플 파일과 포맷 명세 — 언제, 어떤 형태, 라이선스/반출 제약?
2. **가이드라인 §5(Vissim)·§4(SIDRA)·부록 A(신호 데이터)·B(포화류)** — 보내준 PDF에 없음.
3. **Genesis의 첫 스케일**: 교차로/corridor(micro)인가 sub-regional 20-25km²(meso)인가?
   meso 문서를 보낸 의도 확인 — 두 스케일은 사실상 다른 제품.
4. "AI validates the network"의 합격선 — R1.5 체크리스트 초안에 합의.
5. 보고서 첫 타깃: MRWA 스타일(영문 DOCX) vs 교통영향평가(HWP)? **HWP는 별도 기술 과제라
   우선순위가 로드맵을 바꿈.**
6. Region selection UX — 우리가 목업 먼저 제시할지.

**우리 쪽(유나/Inhi 교수):**
7. **첫 시장**: 한국(대전)과 WA(MRWA) 중 어디 먼저? 신호 체계·보고서 양식·언어가 다 달라
   동시 타깃은 비쌈.
8. **Genesis의 사용자**: Dochak 내부 엔지니어용 도구인가, 외부 컨설턴트 판매 제품인가?
   (UX 다듬기 수준이 달라짐.)
9. **Nexus와의 경계**: base-model 관리는 Nexus의 영역 — genesis가 만든 네트워크는 어느
   제품에 속하나? (교수님과 제품 경계 정리.)
10. **Vissim 라이선스 수**: 스윕은 병렬 실행 수 = 라이선스 수. 1개면 야간 직렬 큐가 상한 —
    스윕 처리량 약속에 직결.
11. **OSM 라이선스(ODbL)**: 상용 제품에서의 고지/공유 의무 확인. 대전 데이터 사용권,
    MRWA 데이터 기밀성도.
12. **미국행 일정과 인수인계**: 언제 가는지, 부재 중 개발 주체는 누구인지 — 자동화/문서화
    수준을 여기 맞춰야 함.
13. **다음 TK 데모의 정의**: genesis MVP 데모의 합격 서사를 미리 합의 (예: "Murdoch 절반을
    지도에서 재구축, 구조 diff 90% 일치" 같은 측정 가능한 문장).
14. **보정 루프의 성공 지표**: GEH 개선폭? 엔지니어 시간 절감? — EPIC 2를 측정할 잣대.

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
