# UX Backlog — deferred gaps from the 2026-07-22 audit gap-check

Source of truth for "what's still imperfect but demo-safe". Derived from
[ux-audit-gapcheck-2026-07-22.json](ux-audit-gapcheck-2026-07-22.json) (26 surviving gaps out of 38 audit
findings; verdicts re-verified against code by independent agents). After that report was generated,
commit `77b0dc6` closed the **demo-critical halves** of the five worst gaps — the "Status after 77b0dc6"
notes below record exactly what remains. Everything here is deferrable; nothing puts a fabricated number
on screen anymore.

Ranks refer to the original audit numbering. File refs may drift as `dashboard/page.tsx` evolves —
re-grep the quoted identifiers rather than trusting line numbers.

## High severity — residuals after 77b0dc6

- [ ] **#7 residual — on-screen per-direction delay still uses the invented 0.75–1.25 volume factor.**
  Fixed in 77b0dc6: synthetic "Estimated LOS" letters removed from the exported report; estimation
  disclaimer made always-visible on DirectionDelayChart. Remaining: the chart itself still weights
  intersection-average delay by `volumeFactor = 0.75 + (dirVol/maxVol) * 0.5` (search `volumeFactor` in
  `frontend/app/dashboard/page.tsx`) — now disclosed, but still an invented distribution. Proper fix:
  read real per-movement delay from node results (movement-level rows exist in the result DBs) or drop
  the per-direction delay chart entirely and show per-direction *volume* only.
- [ ] **#18 residual — decision content placement.** Fixed in 77b0dc6: busiest intersection auto-seeds
  (`autoSeededNodesRef`), so panels are never empty on load. Remaining: the overall
  narrative/scenario-ranking section is still 5th of 6 and the 520px map is still the first section;
  no summary strip above the map. Proper fix: headline KPI/verdict strip above the map, or move
  `overall-results-analysis` to position 1–2.
- [ ] **#2 residual — sim totals sourced only from `top_congested_nodes`; third GEH pipeline.**
  Fixed in 77b0dc6: "S000001" no longer leaks into user copy (resolved via `resolveBaseScenarioName`).
  Remaining: validation-matrix sim totals come from `summary.top_congested_nodes.find(...)` so nodes
  outside the top-N render "-"; `/base-model` still has its own `geh()` implementation with no
  cross-link to the dashboard's — three disjoint GEH pipelines total. Proper fix: a single backend GEH
  endpoint/util consumed by all surfaces.
- [ ] **#24 residual — total metrics units.** Fixed in 77b0dc6: "Best" tag suppressed on totals.
  Remaining: TRAVTMTOT/DELAYTOT still display as raw seconds with "51.5k"-style abbreviation instead of
  vehicle-hours. Proper fix: convert totals to veh·h with a unit badge.
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
