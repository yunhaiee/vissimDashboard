# TK 요구사항 명세 — 2026-07-31 미팅 (raw notes → spec)

Source: 미팅 직후 사용자 메모. This document restates them as testable requirements, maps each
to what exists today, and sequences them. **The big reveal: TK wants the pipeline to start one
stage earlier than our product does** — today Prism *edits and validates an existing network*;
TK wants AI to *build the network from a map*, then calibrate, then sweep scenarios, then write
the styled report. That is the consultant's entire workflow, end to end.

The two motivating examples he gave (keep these as acceptance narratives):
- **다리 건설**: how many lanes should the bridge have? (cost-dominated → pre-compute alternatives)
- **Phase plan 변경**: what happens if the signal phase plan changes?

---

## EPIC 1 — AI Network Genesis (지도에서 네트워크 자동 구축)

> "select map region and AI build … not simply rectangular selection … block/street-by-street
> … AI builds it and AI validates it and finishes building the network"

**R1.1 Region selection UI.** User selects the model extent on a map. NOT a rectangle: street-
and block-granular include/exclude (e.g., polygon lasso + per-street toggle), so irrelevant
side streets can be excluded. Output: a bounded set of ways/intersections to model.
**R1.2 Geometry synthesis.** From map data (OSM as baseline; aerial/RIMS where available),
generate Vissim links, connectors, lane counts, turn pockets, and node footprints — written as
.inpx XML (our v2b "XML-build + reparse + reference-integrity" tier, currently 0/13 shipped).
**R1.3 Signal import.** Build signal controllers + phasing from real signal data. Two source
formats, both to be supplied by TK: **대전 스마트교차로 시스템** and **Western Australia
(SCATS)**. Requirement: format-specific parsers → our controller/program model.
**R1.4 Demand seeding.** Vehicle inputs + compositions from detector/count data (Daejeon smart
intersections give per-approach classified counts; WA gives SCATS detector flows); routing
seeded from turning counts.
**R1.5 AI network validation.** The build is not done until it passes machine checks:
connectivity (every input reaches an exit), lane continuity across connectors, signal-geometry
consistency (every signalized approach has heads/detectors), orphan objects, capacity sanity.
This is our constraint registry extended to STRUCTURAL constraints — the same
refuse-with-reason doctrine applied to construction. **The validation layer is what makes AI
construction sellable; without it this feature is a demo trick.**

**Have today:** nothing structural — but the .inpx parser, shadow model, load_check oracle,
and the constraint machinery are the foundation R1.5 needs, and the COM-load/save round trip
is proven.
**Blockers:** v2b tier does not exist; TK's data formats not yet received; OSM fidelity for
lane counts is known-poor in places (needs aerial-assisted correction workflow).

## EPIC 2 — AI Calibration Loop (보정 자동화)

> "catch unusual pattern/unrealistic traffic and how should it be fixed (lane change distance,
> or connector 1→2? etc) … then try again and see if fixed. But it might be real behavior …
> then compare with past traffic data"

**R2.1 Anomaly detection on run results.** Detect unrealistic patterns: phantom queues,
blocked connectors, starved links, unused lanes, teleport/removal counts, unmet latent demand.
(Post-run lie detectors exist; extend from "did the write lie" to "does the traffic look real".)
**R2.2 Fix proposal.** Each anomaly maps to candidate parameter fixes (lane-change distance,
connector geometry/routing, priority rule gaps, reduced-speed areas) — proposed as normal
approval-gated edits, applied, re-run, re-checked. Loop until clean or budget exhausted.
**R2.3 Reality check (TK's own caveat — make it a first-class feature).** Before "fixing" an
anomaly, compare against historical/observed data: if the weird pattern exists in the field
data too, it is BEHAVIOR, not a bug — record the finding instead of calibrating it away. This
is exactly our refusal doctrine applied to calibration, and a paper-grade differentiator.
**R2.4 GEH-scored convergence.** The loop's objective function is the MRWA-style criteria we
already compute (GEH pass rates, travel time ±15%) — category-aware profiles per the meeting
brief.

**Have today:** run gate + post-run detectors, GEH dashboard + real-counts import, batch
edits + rollback (the loop's apply/undo machinery), health check. Missing: the anomaly
catalog, the fix-mapping table, loop orchestration, historical-data comparison store.

## EPIC 3 — Scenario Intelligence (시나리오 스윕 + 최적안 결론)

> "what are possible solutions … +10% vehicles then what happens … AI do simulation for all
> possible fixes and concludes what is the best solution and analysis"

**R3.1 Demand stress ("+10% 차량").** One command: scale demand N%, run, compare against base
— delta report. (Multi-interval inputs + batch + compare dashboard nearly compose this today.)
**R3.2 Alternative enumeration.** For a stated problem, enumerate candidate interventions
(signal retiming, lane add, turn ban, phase change …) — bounded, user-editable list before
anything runs.
**R3.3 Sweep execution.** Simulate all candidates (scenario matrix: N alternatives × M demand
levels), multi-seed per MRWA (≥5 seeds, median), overnight-capable via the job queue.
**R3.4 Ranked conclusion.** Compare KPIs, rank, and write the recommendation WITH the
analysis trail (every candidate's numbers visible — never a bare "best is X").
**R3.5 Bridge-lane example shape:** lane-count alternatives × cost input → performance-vs-cost
table. (Cost model = user-supplied unit costs; we do not invent costs.)

**Have today:** scenarios/compare dashboard, batch, run gate, rollback, the 14-scenario real
project structure (Base/DoMin/TMP×OptSig is literally an alternatives matrix). Missing: sweep
orchestrator, ranking/synthesis layer, cost input.

## EPIC 4 — Report Engine (보고서 자동 생성 + 스타일)

> "report automatically written, but different formatting … data extract … image/figure size
> customizable, graph/figure style (color etc) customizable to company style … upload their
> sample/past report and new report generated customized/fitted to the inputted file style"

**R4.1 Data extraction.** Every figure/table in the report exportable as data (CSV/XLSX).
**R4.2 Figure customization.** Size, colors, fonts per figure; a saved "company style" profile
(회사 스타일 프리셋).
**R4.3 Style-from-sample.** Upload a past report (DOCX/HWP/PDF) → extract layout/typography/
figure conventions → generate the new report fitted to that style. (Phase it: v1 = extract
palette/fonts/logo/heading structure from DOCX; HWP is its own beast — scope explicitly.)
**R4.4 Content requirements.** The MRWA mapping already tells us the mandatory sections:
calibration/validation with parameter-change log (our action ledger IS this), GEH tables,
scenario comparison. Korean 교통영향평가 formatting as the second target format.

**Have today:** report page + exports, action ledger, GEH tables. Missing: style system,
template engine, sample-style extraction.

## EPIC 5 — Full Phase-Plan Editing (신호 phase plan 변경)

TK's second example needs more than our green-splits-within-a-program verb: adding/removing
phases, changing sequences, intergreen-matrix-verified redesign (v2c "full signal plan
redesign" bullet). Gated behind the signal-data import work in R1.3 (same data structures).

---

## Sequencing (dependency-honest)

**Phase A — now → 논문 마감(~09-05). The paper still comes first; these ride along:**
1. **#9 Phase 4 eval harness** (unchanged priority — and EPIC 2's loop telemetry will feed it).
2. **R3.1 demand stress + R3.3-lite scenario sweep MVP** — composes existing machinery; the
   "+10%면 어떻게 되나" demo TK asked for verbatim, cheap and high-impact.
3. **R4.1/R4.2 report data-extract + figure styling v1** — low risk, visible daily value.
4. **Data intake spike**: receive TK's Daejeon + WA signal/count samples, write read-only
   parsers + a format spec doc. De-risks EPIC 1 before any commitment, zero product surface.

**Phase B — post-paper autumn:**
5. **EPIC 2 calibration loop v1** (anomaly catalog → supervised fix-loop → reality-check
   against historical data). Builds directly on paper-phase instrumentation.
6. **R4.3 style-from-sample v1** (DOCX first; HWP scoped separately).
7. **v2b foundation**: XML-build machinery + structural verification, starting with the small
   catalog bullets (lane closure, measurement infra) — the load-bearing wall for EPIC 1.
8. **R3.2–R3.4 full sweep + ranked conclusion.**

**Phase C — the headline (needs B's wall):**
9. **EPIC 1 network genesis**: region UI → OSM geometry → signal import (parsers from A4) →
   demand seeding → structural validation. Milestone-sliced: (a) geometry-only draft network,
   (b) +signals, (c) +demand/routing, (d) validation gate closes the loop.
10. **EPIC 5 phase-plan redesign** (rides on the same signal model).

## Open questions to resolve in planning (TK / 우리 쪽 결정)

1. **Data**: Daejeon 스마트교차로 + WA signal/count 샘플 파일과 포맷 명세 — 언제, 어떤 형태로?
   (라이선스/반출 제약 포함. TK가 주기로 함.)
2. **Map source**: OSM 기반으로 시작하는 것에 동의하는지; MRWA RIMS/항공사진 접근 가능성.
3. **Report 대상 포맷 우선순위**: DOCX vs HWP vs PDF; MRWA 스타일 vs 교통영향평가 스타일 중
   첫 타깃.
4. **"AI validates the network"의 합격선**: 구조 검증 항목 목록에 TK 합의 필요 (우리 초안:
   R1.5 목록).
5. **Cost model** (다리 차선 예시): 단가 입력을 누가/어떤 형식으로 주는가.
6. **Region selection UX**: street-by-street 선택의 인터랙션 초안을 우리가 목업으로 먼저
   제시할지.
7. **범위 방어선**: Phase A 동안 EPIC 1에 착수하지 않는다는 합의 — 논문이 우선이고, EPIC 1은
   데이터 스파이크(A4)로만 진전.
