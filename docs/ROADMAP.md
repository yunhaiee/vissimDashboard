# Prism LLM Upgrade — Roadmap & Architecture

Project: upgrade Dochak's Prism (Vissim validation dashboard) with (A) an
LLM-curated dashboard, (B) an LLM agent that edits Vissim simulations via COM,
(C) an .inpx-rendered network preview. Target: working product by ~2026-09-05
plus a TR Part C paper candidate (per Prof. Inhi Kim, 2026-07-21 meeting).

Decisions locked in (2026-07-21):

- **Modify the existing Prism codebase, not from scratch.** All three features
  land on existing seams; a rewrite would forfeit months of Vissim-specific
  work (results reader, signal decoder, .trax merge engine, inpx parser, COM
  wrapper, tri-lingual UI).
- **Repo is local-only — do not push** without Dochak team approval.
- Start directly on Vissim (skip the ChatSUMO warm-up); keep the existing
  regex prompt-parser as the paper's baseline condition.
- Select-area-then-prompt UX approved as stretch goal (Phase 3 seeds it).

## Phases

| Phase | When | What |
|---|---|---|
| 0. Foundation | 7/21 (done) | Repo import, venv/npm, both servers, synthetic demo data generator, Claude provider, pre-existing bug fixes |
| 1. LLM-curated dashboard | 7/22–8/1 | Chart catalog registry → structured-output path (bypasses `clean_generated_answer`) → focus-mode rendering → insight cards with 전문가/쉬운 설명 audience toggle → fix two fabricated visuals. Demo to prof ~8/1 |
| 2. Edit agent v1 | 8/4–8/15 | **Prerequisite: study the ChatSUMO-Agent repo (github.com/ChrisLi1221/ChatSUMO-Agent) + the papers in docs/reference/prism-llm-briefing.html — tool loop, prompt design, eval setup — before writing agent code.** |
| | | Tool schemas over 5 COM verbs + speed edit (XML injection) + run/results/compare; conversation history; server-side propose→clarify→approve→execute (AgentSUMO-style) with diff-card UI; `services/guardrails.py` (constraint catalog in code — NOT named "validation", that means GEH calibration here); job queue + registry locking + multi-seed runs |
| 3. Network preview | 8/18–8/22 | Revive unmounted d3 NetworkViewer at /network, refetch on dataset change, connector from/to topology, click-selection → `vissim:page-context` → assistant, result-metric coloring |
| 4. Evaluation | 8/25–8/29 (+buffer to 9/5) | Benchmark task suite (NL edit tasks w/ ground truth), conditions: regex baseline / agent / agent−guardrails; metrics: task success, unsafe-edit catch rate, clarification quality; proposed/approved/executed action logging |
| Later (Sept+) | | MCP adapter, real-user feedback, Vista/live preview tier, on-premise LLM swap, box-select UX |

## Architecture (3 layers)

1. **Vissim worker** — `services/vissim_com.py` VissimController (win32com,
   ProgID probe 2024–2026). Existing verbs: vehicle input volume, SimPeriod,
   RandSeed, signal CycleTime, signal Offset. Speed edits are NOT COM: cloned
   .inpx + injected reducedSpeedArea (`simulation_network_builder.py`).
   Needs: dialog suppression, persistent session, queue (one instance),
   multi-seed (interactive runs hardcode numRuns=1 today).
2. **Bounded-verbs API** — existing SimParams → job → results pipeline
   (`routers/simulations.py`), already ships LLM-oriented error messages.
   Guardrails module wraps every mutation.
3. **Agent loop** — server-side, Claude tool calling (provider swappable for
   on-prem Qwen later; the duck-typed provider seam already exists and is
   test-injectable). Approval gate MUST be server-side (today's
   `allow_tool_execution` is client-side keyword matching — fake).

## Key codebase landmines (from 2026-07-21 full map)

- `clean_generated_answer()` destroys JSON — structured output needs a bypass.
- `assistant_router.py` (2,948 lines) defines `classify_intent`/`tokenize`
  twice; second shadows first.
- Streaming endpoint re-runs the query if the threaded run yields nothing →
  can double-start simulation jobs.
- `lane_closure`/`full_closure` accepted by schema, silently dropped.
- Two dashboard visuals fabricate data: `stabilizeTravelTimeProfile()` and
  estimated per-direction delays — must fix/exclude before paper figures.
- `frontend/tests/dashboard-contract.test.ts` regex-pins raw source text —
  co-edit on any change to FloatingAssistant/dashboard/api.ts.
- Frontend expects backend on **:8001** (root README's 8000 is stale).
- Dataset upload rmtree's the previous upload; results hide on revision change.
- Geojson/Leaflet map georeference is Helmert-fit to SouthSt Perth only;
  generic preview must use raw-coordinate NetworkViewer instead.

## Reference documents

- **`docs/phase2/` — the Phase 2 master plan (2026-07-22).** Executor-ready:
  [00-EXECUTION-GUIDE](phase2/00-EXECUTION-GUIDE.md) (rules + landmines) →
  [01-ARCHITECTURE](phase2/01-ARCHITECTURE.md) → [02-ROADMAP](phase2/02-ROADMAP.md)
  (milestones M0–M5 with acceptance criteria) →
  [03-RESEARCH-DIGEST](phase2/03-RESEARCH-DIGEST.md) (ChatSUMO-Agent paper+repo,
  AgentSUMO — the study-papers-first prerequisite, fulfilled) →
  [04-EVAL-PLAN](phase2/04-EVAL-PLAN.md) (TR-C evaluation design). Any Phase 2
  work starts by reading these in order.
- `docs/ux-backlog.md` — deferred UX gaps from the 2026-07-22 audit gap-check.
- `docs/reference/prism-llm-briefing.html` — meeting briefing: landscape,
  papers (ChatSUMO Agent TR-C 2026 = the paper prof cited; AgentSUMO KAIST),
  architecture, on-prem, select-then-prompt UX.
- `docs/reference/vissim-constraints.html` — **the full ~40-entry Vissim
  constraint catalog** (silent failures, non-editable attributes, fragile
  references, domain semantics, results pitfalls, calibration, operations) +
  the 7-item validation-layer spec. Phase 2 guardrails must be built against
  this, not from memory.

## Division of labor

Claude codes; Yunha handles: Vissim install/license, running COM smoke tests
(watch for modal dialogs), obtaining real example networks + full working tree
from the Dochak team, API keys via `.env` (never committed), meetings/elicitation
with Dochak's traffic engineers (Dr. Chun/Dr. Oh) for guardrail rules and
eval-task ground truth.

## Status update — 2026-07-24

Phases 0, 1, 2 (all of docs/phase2 M0–M5) COMPLETE and live-demo-verified end to end
(edit → approve → verified COM write → 5-seed run with auto-armed evaluation → real KPIs
in chat + dashboard). Six live-rehearsal defects found and fixed same-day — see
docs/phase2/DECISIONS.md 2026-07-24 entries. Eval headline: 60% vs 0% silent-failure
detection (full stack vs ablation).

Phase 3 code-complete on branch phase3-render (2026-07-24, laptop): NetworkViewer revived —
dashboard 지도/도식 toggle + /network page, dataset-change refetch, connector from/to
topology, click-selection → vissim:page-context, node LOS coloring from real per-node delay.
Browser-verified against synthetic demo data; connector XML schema still needs validation
against a real network on the Vissim box (see DECISIONS.md), merge to main pending Yunha's
review.

## Status update — 2026-07-27

**Done since 07-24** (detail per entry in docs/phase2/DECISIONS.md):

- **v2 tool queue ①–⑥ complete** (unattended serial run on branch v2-tools):
  controller-type parsing/signal offset unlock, set_reduced_speed_area (first non-COM
  verb), load_check (+ proof no COM consistency-check exists), conflict-area
  read/status verbs, per-interval vehicle-input volumes (Cont-flag trap defeated),
  route relative-flow read/write. v1 close-out COMPLETE; v2a 3/12 shipped.
- **v2-tools + phase3-render merged to main**; both machines now share one trunk.
- **Trajectory replay**: AI-run simulations record .fzp per seed; network tab replays
  vehicle movement with speed controls, playback-source picker per job.
- **Chat reorganization**: VISSIM 에이전트 default-on everywhere; dashboard curation
  reframed as view control with agent handoff.
- **Metric-level curation**: kpi_metrics six-key vocabulary — metric questions show
  only relevant KPI cards + "전체 지표 보기" reveal.
- **~19 live-rehearsal defects found and fixed** (the demo-as-validation method
  continued): session race, approval consumed once, evaluation auto-arming,
  dead-stream detection + heartbeat, 300-char observation cap (M1-era), warning
  floods, replay download abort — and on 07-27 alone: vehicle-overlay freeze
  (three stacked causes: frame lag, rAF latch, listener isolation), semantic zoom
  for element glyphs (root cause of permanent clustering), hover/selection recolor
  replacing the ring, element-click swallow, metre-true road markings, and the
  agent observation-flood cluster (renderer zero-items bug traced to a 1-char
  budget overflow, conflict-area filters, warning dedup/aging).
- **Docs**: customer-facing README rewrite; TOOL-CATALOG reorganized into a
  scannable per-verb matrix.

## To-do (forward plan, updated 2026-07-27)

### 1. Paper-critical path (deadline ~09-05)

- Obtain real Korean intersection .inpx (professor emailed 07-24 — waiting)
- Ground-truth eval tasks with Dochak engineers (Dr. Chun/Dr. Oh)
- Live eval runs on real Vissim for final paper numbers (current 60% vs 0%
  silent-failure detection is demo-scale)
- Action-log analysis + TR-C paper writing
- Yunha: full demo video re-shoot (18-beat script); regular GitHub Desktop pushes

### 2. Product roadmap — ACTIVE, interleaved with paper work (decision 2026-07-27:
v2 and service upgrades are NOT deferred to post-paper; run as queued units)

**Tool expansion** (docs/TOOL-CATALOG.md is the definitive matrix):
- v2a remaining 9 verbs (compositions, desired-speed pairs/distributions,
  priority-rule params, driving behavior duplicate-then-edit, dwell times,
  green-splits, vehicle type properties)
- v2b structural tier (lane/link closure, links & connectors, geometry, signal
  heads/detectors placement, measurement infra, PT, pedestrians)
- v2c semantic tier (signal plan redesign, calibration search, OD/DA,
  compare workflows)
- Batch/compound edits (one approval for a validated multi-edit plan); edit
  rollback verb (artifact versions already exist)

**Purchase-driver features** (product review 2026-07-27, ranked by buying power):
- 모델 건강검진 — one-click health-check report running the full constraint catalog
  on an uploaded .inpx (sells the validation layer standalone; near-free to build)
- Approval-card mini-map — visualize the affected element/location before approving
- Live run-progress UI — per-seed status + ETA (recurring demo pain)
- Audit-trail export — the agent action ledger as a 변경 이력 report (HWP/DOCX,
  교통영향평가-style formatting)
- Scenario matrix — N alternatives × M demand levels overnight → comparison report
- Calibration assistant — GEH-scored parameter search (v2c's flagship)
- Replay upgrades — signal-state playback (.lsa), follow-a-vehicle, queue highlight
- Network diff view — two model versions side-by-side, changes highlighted
- Data-boundary statement in UI (files never leave the machine; prompts only)
- Onboarding — bundled sample project + guided tour
- Share links (read-only dashboard/replay) + reviewer/modeler roles on the
  approval gate

**Deployment & persistence** (direction set 2026-07-27):
- Target model: ON-PREM TEAM SERVER — one Windows box with Vissim + Prism inside the
  customer's network, engineers connect via browser over LAN. Cloud SaaS rejected
  near-term (Vissim licensing + confidential .inpx). LLM API stays the only external
  call; on-prem LLM swap via the existing provider seam if security demands.
- ① Edited-model download (FIRST — real user-facing gap today): per-version download
  + "export current edited model" (.inpx + sibling assets like .layx — forces the
  ArtifactManager sibling-assets fix), surfaced on the downloads page and in chat
  after an edit.
- ② Project workspaces: replace the global active-dataset singleton (upload currently
  rmtree's the previous one) with projects/<id>/ containers holding datasets, runs,
  chat sessions, artifact versions, audit log — "save my project and come back to my
  history" becomes structural.
- ③ Metadata layer: SQLite (WAL) for users/projects/runs/sessions/version-timeline
  indexes; heavyweight artifacts stay files. Zero-ops, one-file backup, Postgres
  migration path open if a cloud tier ever lands.
- ④ Login + roles: session auth with hashed passwords (OIDC/SSO later if customer IT
  requires; no social OAuth); modeler/reviewer/admin roles mapped onto the existing
  approval gate.
- ⑤ Optional later: cloud view-only tier (dashboards, replay, health-check report —
  none need Vissim) with the runner staying on-prem.

**Platform/service upgrades:**
- Live rendering during a run (step-streaming mode alongside batch RunContinuous)
- 3D view (three.js/deck.gl), satellite/OSM map underlay
- OSM/GIS network import; Korean traffic data ingestion (TOPIS/공공데이터 API)
- External controller files via file-level editing (RBC/VAP — COM-impossible, proven)
- Deployment: auth, multi-user, dedicated Vissim job-runner
- RAG corpus completion (turns the known-failing retrieval test green)
- Proactive diagnosis (agent flags gridlock/anomalies unprompted)

### 3. Known small debts (fix opportunistically; ledger-tracked)

- .fzp SPEED column absent → stopped-vehicle coloring inactive in replay
- .err-log parser for deleted_vehicles; ArtifactManager sibling-assets clone gap
- Deferred constraints: demand_scale turn-state, min_green_intergreen_sum
- Chevrons invisible at base zoom on low-px/m networks (intentional; revisit on report)
- test_agent_read_tools.py standalone-run import gap (task chip spawned)
- docs/ux-backlog.md items
