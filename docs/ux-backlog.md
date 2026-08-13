# UX Backlog — deferred gaps from the 2026-07-22 audit gap-check

Source of truth for "what's still imperfect but demo-safe". Derived from
[ux-audit-gapcheck-2026-07-22.json](ux-audit-gapcheck-2026-07-22.json) (26 surviving gaps out of 38 audit
findings; verdicts re-verified against code by independent agents). After that report was generated,
commit `77b0dc6` closed the **demo-critical halves** of the five worst gaps — the "Status after 77b0dc6"
notes below record exactly what remains. Everything here is deferrable; nothing puts a fabricated number
on screen anymore.

Ranks refer to the original audit numbering. File refs may drift as `dashboard/page.tsx` evolves —
re-grep the quoted identifiers rather than trusting line numbers.

## 2026-08-12 user intake — awaiting elaboration (do not spec yet)

Direct user feedback, recorded verbatim in intent, details to follow from the user:

- [ ] **U-1 — Genesis UI/UX enhancement.** "The UI is so confusing right now. Too much text visible,
  lack of UX consideration as well as UI." Scope when elaborated: the 네트워크 생성 page family
  (`app/genesis/**` — job form, gate verdict panel, findings map, comparison, demand builder, 현시도
  review). Known contributing fact: these screens were built unit-by-unit with disclosure-first copy
  (every refusal named on screen), so text density is a *doctrine artifact* — the enhancement must
  reduce visible text without deleting the named-refusal guarantees (progressive disclosure, not
  removal).
  **FIRST PASS DONE 2026-08-13 (`cb27da1`), user-directed, palette/theme frozen:** hint boxes
  demoted below the primary flow; V1–V9 collapsed with severity auto-open; region measurement
  3 lines + disclosure; phases state-gated; Panel/refusalHeadline ×4 merged into
  `components/genesis/ui.tsx`; assistant run-hint folded (U-2 light only — D4 changes the
  proposal shape). REMAINING for the user's detailed review: the amber-neutral question below,
  `.ui-button-primary` adoption on the other three CTAs, FloatingAssistant structural work
  (post-D4), and whatever the elaboration surfaces.

  **THE USER'S DETAILED ELABORATION ARRIVED 2026-08-13 (screenshots of live use). Eight
  problems, all APPROVED with solutions and order (user answer: "전부 승인, 제안 순서대로";
  flow direction: mockup first, then decide):**
  1. GateFindingsMap ↔ dashboard-map parity: bare local-coords SVG is illegible, no basemap.
     Solution: server-computed GeoJSON endpoint (genesis drafts are WGS84-derived — exact
     inverse exists, unlike consultant Helmert), leaflet+CARTO with finding overlays. [U-1-map]
  2. ALL artifact buttons dead (.inpx / §5 / gate report / gate.json / manifest) — BUG,
     suspected `<a href>`-cannot-carry-workspace-header class (the named 1.4 hole). [U-1-quick]
  3. Residual always-visible text (drag/zoom hint, comparison scope, local-coords note,
     provisional explanation, excluded-roads note) → new shared ⓘ tooltip component; doctrine
     strings into tooltips (reachable), operation hints deleted. [U-1-quick]
  4. V1–V9: whole-card click (not just arrow) + 3×3 grid of state chips with a single detail
     panel below; severity auto-select stays. [U-1-quick]
  5. 생성 이력 rows carry only title+date → add verdict chip, requested region (coords/area),
     link/node counts; backend job-listing gains a region summary. [U-1-quick]
  6. Choice hierarchy broken on the hub: step-1 (upload|extract) and step-2 (demand/현시)
     share a row; "수요 입력 만들기" page + job-form "수요 입력 (선택)" dropdown read as
     duplicates. → absorbed into the flow restructure. [U-1-flow]
  7. Dashboard MAP dead for genesis-published datasets (도식 works): the map is pinned to the
     consultant Helmert constants. Solution: per-dataset georeference metadata written at
     genesis publish; geojson router uses per-dataset transform. [U-1-map]
  8. Function overlap (network preview ×3) + user proposes a survey-like flow. Direction:
     the product's own six stages (스코핑→데이터→생성→보정→분석→리포트) as a persistent
     stepper; ONE shared network-preview component. User wants a MOCKUP first. [U-1-flow]
  **Approved order (serial): D4 → U-1-quick (#2,#3,#4,#5) → polygon-native trim → U-1-map
  (#1,#7) → U-1-flow mockup → decision → B5-2.**

  **SECOND ELABORATION 2026-08-13 (after testing U-1-quick live; reference design = the
  Claude Console dashboard screenshots):** (9) card-in-card-in-card with repeated titles —
  생성 비교 stacks THREE same-meaning titles (panel → inner → label); flatten to one title +
  one control, sweep all screens for the same disease. (10) the two refusal banners
  ("거부됨 — 등록하지 않음" + "거부 — 결함이 입증되었습니다") say the same thing — merge into
  ONE verdict header (status + counts + sha + downloads), demote the rest. (11) green-on-green:
  pass cells render green text on a green card — contrast bug, fix within the palette.
  (12) 3×3 grid — USER REFINED 2026-08-13: NO microcopy, NO checkmark, NO auto-select.
  ALL cells start closed (detail renders only after a click; severity stays readable on the
  cells themselves via state chips + accent rings, which satisfies the doctrine without an
  auto-expanded panel); wider cell gaps; active cell = one-step fill + 2px ring + brighter
  title, ~100ms transition. The cb27da1/78e52d1 auto-select pins get rewritten accordingly.
  (13) body type one step larger; keep trimming residual prose. Console STYLE transfer
  specifics (palette frozen — spacing/size/weight/structure only): one-layer cards with
  20-24px padding and 32-40px section rhythm instead of nested boxes; section titles
  15-16px semibold / body 13 / secondary 12 muted, nothing below 12; the Console stat
  pattern (small muted label above, large bold value below) for the verdict counts, region
  measurement and history scale numbers; 생성 이력 as a clean table (muted column headers,
  borderless hover rows, right-aligned verdict chips); pill-style filter chips from the
  existing .ui-button-ghost vocabulary. LIST REFERENCE (user, 2026-08-13, Console Skills
  page — better than API keys since it has many rows): muted column headers (이름/생성일/
  영역/규모/판정), monospace ids and coordinates, no row borders — row height carries the
  separation, verdict as a small right-side tag chip (the "Anthropic" chip style),
  pagination arrows when the list grows, ONE primary action top-right of the section.
  MASTER-DETAIL reference (second Skills screenshot, row clicked): clicking a list row
  highlights it and opens a RIGHT-SIDE detail drawer — header = name + tag chip + one muted
  meta line, then plain titled sections (Description / Versions ↔ our 판정 / 소견 / 아티팩트),
  close X top-right. This IS the #13 split: 생성 이력 row click → the right pane shows that
  generation's verdict grid, map and artifact row; the left column stays the form + table.
  LIVE-REVIEW TWEAKS on the #13 build (2026-08-13, pre-commit): 3×3 cells lose the
  whole-card tint — color lives ONLY on the state tag (통과/실패/확인 불가), card body mono;
  the ⓘ glyph sits slightly below vertical center — align it. (Screenshot also showed the
  old backend process rejecting /artifact/geojson — not a defect; the degradation path drew
  the SVG fallback and quoted the server's reason exactly as designed.)
  MOCKUP DECISIONS (2026-08-13, flow-stepper-mockup reviewed): **variant B approved** —
  the five-group pipeline sidebar (① 네트워크 ② 데이터 ③ 시뮬·보정 ④ 보고서, project
  selector fixed on top) becomes the implementation target after #13. Right drawer must
  hold a BIGGER proportion (~45-50% of content width, map ≥360px tall). **THREE-COLOR
  RULE (this CLOSES the amber-neutral question — user decided):** rose=fail,
  emerald=pass, sky=selection/info; everything else MONO — not_checkable/warn/provisional
  render ink + heavier ring/border, distinct by weight and shape, never a fourth color.
  Matches globals.css's own "no yellow" philosophy; doctrine (not-checkable must not read
  as fine) is carried by the ring, pinned.
  REGION-PAGE ADDENDA (user live-testing 2026-08-13, folded into #13 mid-build): tinted
  info boxes were blue-on-blue — ONE contrast rule, ink-strength text on every tinted box;
  RegionGauge removed in polygon mode (the map + the 한계 안 line already say it; the little
  dashed square read as a stray drawn rectangle); polygon lasso becomes the DEFAULT mode;
  the 추출 이력 list REMOVED (every generation consumes a fresh extract, so past staged
  extracts carry no action; generation history lives on the hub) and the deferred-features
  disclosure REMOVED (named-absence doctrine governs dropped DATA, not unbuilt features —
  the deferral record's home is this file and the roadmap).
  (14) LAYOUT: adopt the Console's split — left rail = controls/history/verdict list, right =
  sticky map/network + detail. ALL of 9–14 fold into the #13 U-1-map unit (one rebuild of
  app/genesis/page.tsx rather than two); choice-hierarchy/stepper stays in the #14 mockup.
  **#13 LANDED `5ba7210` (2026-08-13):** elaboration items 1+7 (tile map via server-side
  WGS84 geojson + per-dataset dashboard georeference) and 9–14 (Console split, merged verdict
  header, Skills table, three-color sweep), the region addenda and both live tweaks (tag-only
  color, SVG ⓘ) shipped as one rebuild — narrative in DECISIONS 2026-08-13. Backend restart
  required to activate the geojson endpoint (the SVG fallback covers until then, by design).
  Remaining from the elaborations: items 6+8 = the approved **variant B** flow build (next).
  POST-COMMIT LIVE PASS (user, 2026-08-13, three instructions, all landed same day): verdict
  chips nowrap; 3×3 card borders ALL at pass thickness — the non-pass doctrine ring removed,
  the chip (확인 불가's doubled mono border, now pinned) carries the whole pre-reading
  signal; repeated fine prints removed — a filename that only re-spells the job name prints
  nothing (table 2nd line, drawer meta, verdict sha line), shown only when it differs.
- [ ] **U-2 — Calibration-loop UI/UX enhancement.** Same feedback applied to the calibration loop
  surfaces (approval flow, anomaly→proposal→approve→re-run). Note: D4 (roadmap §5.D, professor's
  direction reversal) will change what the loop's proposals even look like — sequence the UX pass
  after D4's proposal-shape change lands, or design them together.

Pre-audit facts (read-only terrain pass, 2026-08-12 — measured, not designed):

- [x] **Objective defect — FIXED 2026-08-12 (`de0a667`):** the five genesis primary CTAs were the ONLY
  `bg-slate-900 … text-white` pairs in the app (`app/genesis/page.tsx:535`, `region:552,676`,
  `demand:247`, `phases:435`); the dark palette remaps slate-900 to ivory (`globals.css:184`) and its
  own lock comment says call sites must pair it with `text-ink` — every non-genesis call site does.
  Fixed minimally (`text-ink`), reintroduction pinned by contract test "genesis primary CTAs stay
  legible on the inverted dark palette"; adopting `.ui-button-primary` remains a U-1 design option.
- **Doctrine-relevant defect:** `globals.css:199-211` remaps the amber family to neutral, so
  NOT_CHECKABLE / 재확인 필요 / rests-on-attestation — the states that must NOT read as neutral —
  render grey while PASS/FAIL keep real color.
- Worst text volume: `/genesis` (17 always-visible prose blocks; 3 cross-nav hint boxes BEFORE the
  file input; V1–V9 always fully expanded), `/genesis/region` (5 stacked paragraphs per measurement),
  `/genesis/phases` (6 panels open at once, no state gating). `/genesis/demand` and `/health` are the
  well-behaved models (state-gated sections; per-row 상세/접기).
- Redundancies: comparison scope line stated twice in one card (`genesis/page.tsx:889` +
  `GenesisComparisonPanel.tsx:207`); `Panel` copy-pasted ×4 with drift; `refusalHeadline` ×4;
  3 hand-rolled `<details>`; two approve-strips and two progress bars in `FloatingAssistant.tsx`.
- Existing primitives to build on: severity-driven auto-open (`GateFindingsMap` `autoOpen`),
  plain/expert toggle (`CurationBar.tsx:200`, consumed nowhere in genesis), `MapShell` 펼치기/접기,
  truncation-with-disclosure convention. Absent: tabs/accordion/popover/stepper — no `components/ui/`.
- Type hierarchy: two effective sizes (h1 22px → everything else 10–14px); panels have no fill
  contrast vs page (`bg-surface/90` == page background, hairline borders only).

## High severity — residuals after 77b0dc6

- [x] **#7 — CLOSED 2026-08-05 (7cfe8eb, unit 1.5).** Per-approach delay is now a VEHS-weighted mean of
  measured per-movement `VEHDELAY`, not the node average times an invented factor. The factor lived in
  TWO places — `calculateDirectionDelays` in `frontend/app/dashboard/page.tsx` and a backend mirror,
  `results_reader._estimate_direction_delays`, that this entry never knew about; both are deleted. Where
  nothing was measured the chart shows 데이터 없음 and the report prints `-`, never a zero (the guard is
  null-before-coercion: `toFiniteNumber` is `Number()` + `isFinite` and `Number(null)` is a finite `0`,
  which briefly turned "not measured" into four bars at LOS A — a source pin now fails if the coercion
  moves back ahead of the guard). The exported report's claim that Vissim cannot produce per-approach LOS
  went with the estimate, and the report now carries the measured delay and its LOS. See
  `docs/phase2/DECISIONS.md`.
- [ ] **#18 residual — decision content placement.** Fixed in 77b0dc6: busiest intersection auto-seeds
  (`autoSeededNodesRef`), so panels are never empty on load. Remaining: the overall
  narrative/scenario-ranking section is still 5th of 6 and the 520px map is still the first section;
  no summary strip above the map. Proper fix: headline KPI/verdict strip above the map, or move
  `overall-results-analysis` to position 1–2.
- [ ] **#2 residual — sim totals sourced only from `top_congested_nodes`; third GEH pipeline.**
  Fixed in 77b0dc6: "S000001" no longer leaks into user copy (resolved via `resolveBaseScenarioName`).
  Remaining: validation-matrix sim totals come from `summary.top_congested_nodes.find(...)` so nodes
  outside the top-N render "-". **The GEH half is CLOSED 2026-08-05 (7cfe8eb, unit 1.5)**: there were
  FOUR live implementations, not three (the backend carried two disjoint copies, and `report_exports`'
  returned a passing `0.0` where the statistic is undefined). Now one per runtime —
  `backend/services/validation.py` and `frontend/lib/geh.ts` — held in step by
  `shared/geh-conformance.json`, a case table both are tested against. Remaining under this ticket: the
  sim-totals sourcing from `summary.top_congested_nodes`.
- [x] **#24 — CLOSED 2026-08-05 (7cfe8eb, unit 1.5).** TRAVTMTOT/DELAYTOT render in vehicle-hours
  (`displayDivisor`, value and stdev scaled together) instead of raw seconds with a "51.5k" abbreviation;
  units and labels route through `t()` with ja entries added. `KpiCards.tsx` converted alongside the
  dashboard. Note the sibling ticket **#28** (hardcoded `sec`/`veh` axis labels elsewhere, hover-only
  volume values) is a DIFFERENT ticket and stays open — it was deliberately out of 1.5's scope.
- [x] **#31 — closed in 77b0dc6.** `calculateNodeMaxQueue` no longer falls back to QLEN under the
  "Max Q Length" header; absent QLENMAX now renders honestly empty.

## Medium-high

- [ ] **#11 — plain mode hides stochastic-noise context.** ±stdev and the "within noise" softening of
  the Best tag are gated behind expert mode; default (plain) audience sees bare point values with an
  unqualified Best tag. Decide: either show a lightweight "difference within run-to-run noise" hint in
  plain mode, or de-gate the stdev display.

## Medium

- [ ] **#9 — backend-down still shows contradictory upload UI.** When the server is unreachable the
  error banner renders *alongside* the "Upload required" pill, the dataset gate, and the upload panel
  (which would also fail). Distinguish `backendDown` from `noDataset` in state and suppress upload
  affordances in the former.
- [ ] **#14 — curation lacks server-side data-availability filter.** `validate_spec()` in
  `backend/services/dashboard_curator.py` filters only against `VALID_CHART_IDS`; context passed from
  `backend/routers/assistant.py` carries no `has_real_counts`/`has_data` flags, so the LLM can select
  `field-count-validation` with no counts loaded. Client shows a text notice only. Add availability
  flags to the curation context + filter chosen ids server-side.
- [ ] **#21 — audience toggle reaches only 1 of 3 chart surfaces.** In plain mode the LOS section still
  shows raw A–F letters and the validation section the full GEH table; `IntersectionPerformanceGrid`
  and `ValidationMatrix` take no audience prop. Thread audience through both + plain-mode subtitles.
- [ ] **#33 — scenario cap inconsistency.** Picker toggle appends uncapped while reload silently slices
  to 4; no "auto-selected" badge on the default set. Enforce one max in both paths.
- [ ] **#6 — report is English-only, three disconnected pipelines.** Client-side markdown report
  hardcodes English titles and `en-AU` dates behind a Korean-labeled button; no canonical backend
  report endpoint. Longer-term: single backend .docx pipeline with locale support.
- [ ] **#13 — silent 5xx auto-retry.** `api.ts` retries once on any 5xx with no "retrying…" feedback,
  so a failing curation query takes ~40s while the status line promises 10–20s. Surface retry state.
- [ ] **#25 — uploads/resets have no timeout or cancel.** `apiMutationFetch` uses bare `fetch` with no
  AbortSignal; a stalled backend leaves the banner disabled forever (elapsed counter ticking). Wire
  `fetchWithTimeout` + a Cancel button into the mutation path.
- [ ] **#17 — no cross-handoff between curation bar and chat.** Execution-intent questions typed into
  the read-only curation bar go to the curation endpoint; no event exists to open/prefill
  FloatingAssistant. Add execution-intent detection + an `assistant-open` custom event.
  *(Note: Phase 2's agent work will likely subsume this — revisit after the edit-agent lands.)*
- [ ] **#30 — Word export is HTML-as-.doc.** Word fires a format-mismatch prompt on open; ja locale
  lacks the inline warning. Real fix is a backend .docx (pairs with #6).

## Low-medium

- [ ] **#19 — stale jobs not flagged.** JobRow never compares `job.dataset_filename` to the active
  dataset; jobs from a replaced dataset look identical to current ones.
- [ ] **#12 — travel-time series distinguished by hue alone.** No per-series marker shapes or
  end-of-line labels; near hues indistinguishable (accessibility).
- [ ] **#15 — hidden sections unmount abruptly.** No placeholder stub, count badge lives in the
  non-sticky curation bar, no `aria-live` announcement of section changes (focus mode).
- [ ] **#29 — jargon still primary text in assistant.** "Qwen model path"/"worker" status note,
  "COM offline/connected/ready" pill, raw worker-error "ID:" fragments.
- [ ] **#27 — nav labels lack descriptors.** No one-line descriptions; "베이스모델 검증" not renamed
  toward a user task.

## Low

- [ ] **#16 — section jump-index embedded in map.** Absolutely positioned inside the map section:
  scrolls away, vanishes when map is focus-hidden, CSS-hover-only (no touch/onClick/aria-expanded).
- [ ] **#28 — hardcoded 'sec'/'veh' axis labels; hover-only volume values.** Not routed through `t()`;
  per-direction volumes and full disclaimer remain title-attribute-only in places.
- [ ] **#36 — no "what should the ZIP contain" hint** beside either upload button.
- [ ] **#34 — changelog-voice copy leaks.** Map subtitle "기존 대시보드 지도를 그대로 사용…", upload
  panel referencing removed sample-data behavior users never saw.
- [ ] **#38 — vestigial `SimulationControl.tsx`.** WebSocket-only monitor with no polling fallback;
  zero real importers; warned by comment only. Delete it or rewrite on the WS+polling pattern.

## Unresolved bookkeeping

- **Rank 8 was never verified** — its gap-check verifier hit a structured-output retry cap. Re-check it
  against the original audit ([ux-audit-2026-07-22.json](ux-audit-2026-07-22.json)) whenever the next
  UX pass happens.

## Added after the audit

- [ ] **2026-08-05 - approval card: optional one-line 근거 input + 감사추적 download action.** Backend
  shipped in c40ee0d: `POST /api/assistant/agent/approve` accepts optional `reason` (<=300 chars,
  recorded to the trace ledger); `GET /api/agent/audit/{session}/export.docx` serves the DOCX and
  `GET /api/agent/audit/{session}/summary` returns counts (use it to decide whether to show the
  button). Frontend work: reason field on the approval card, a download action, and
  `AgentApproveRequest` in `frontend/lib/api.ts` gains `reason?: string`. ko primary, en/ja via `t()`.

- [ ] **2026-08-05 - workspace scoping cannot reach two surfaces.** Unit 1.4 (56f7f5c) scopes every
  request by an `X-Workspace-Id` header, which two callers cannot send: the browser `WebSocket` API
  (`/api/simulations/ws/{job_id}` — progress percentages only, for a job id the caller already holds)
  and a plain `<a href>` download (`healthCheckDownloadUrl()` in `app/health/page.tsx`, so that JSON
  always resolves to the default project; the on-page report itself is correctly scoped). Both close
  when scoping moves into the path or query string with P③/P④. Until then, do not add new
  workspace-sensitive data to either surface.
