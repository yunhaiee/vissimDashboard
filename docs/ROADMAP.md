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

**Done overnight 07-27 → 07-28** (v2a queue 2 + the top three purchase-drivers):

- **v2a queue units ⑦⑧⑨ shipped**, each live-verified against real Vissim:
  vehicle-composition shares (RelFlow proven a RAW WEIGHT — Vissim never renormalizes
  siblings, disproving placeholder constraint #36); desired-speed decision PAIRS (the
  first atomic two-target write; guardrails gained paired-target + coupled-fields
  machinery); signal GREEN SPLITS — the feared "blob writer" turned out to be the
  COM-writable `IntSupplyData` attribute, verified with a SimBreakAt+SigState
  BEHAVIOR ORACLE (the proof class CycTm never had). v2a 3/12 → 7/12; registry 48→58.
- **Edited-model download**: per-version list + zip export (.inpx + sibling assets),
  downloads-page card + in-chat link; closed the ArtifactManager sibling-assets gap
  (allowlist built by surveying all 25 PTV example networks).
- **모델 건강검진** (`/health`): the validation layer as a standalone parse-only
  product surface — 14 constraints run dataset-wide, 47 declared edit-time-only
  (derived from the registry so coverage can never overstate itself), ~31ms on the
  real Beijing network, JSON export for the paper. First run found a GENUINE
  beyond-cycle green in a shipped PTV example (131s window in a 130s cycle).
  Registry 58→61.
- **Run-progress card**: seed-boundary phase/ETA bookkeeping + a `run_started` stream
  event; ETA stays null until a seed actually completes (honesty rule). Fixed a
  latent race — job records were mutated by worker threads with no lock while GET
  handlers returned the live dict.
- **Approval-card mini-map**: `approval_focus.py` resolves each write tool's target to
  coordinates (reusing guardrails' identity tables) and the card renders an oriented
  thumbnail with the affected links highlighted — no more approving blind.

## To-do (forward plan, updated 2026-07-28)

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
- v2a remaining 5 bullets (priority-rule gap/headway params, driving behavior via
  duplicate-then-edit, dwell-time distributions, vehicle type properties) — the
  overnight-queue pattern works; queue them a few at a time
- v2b structural tier (lane/link closure, links & connectors, geometry, signal
  heads/detectors placement, measurement infra, PT, pedestrians)
- v2c semantic tier (signal plan redesign, calibration search, OD/DA,
  compare workflows)
- Batch/compound edits (one approval for a validated multi-edit plan); edit
  rollback verb (artifact versions already exist)

**Purchase-driver features** (product review 2026-07-27, ranked by buying power;
top three SHIPPED 2026-07-28):
- ✅ 모델 건강검진 — shipped 07-28 (`/health`)
- ✅ Approval-card mini-map — shipped 07-28
- ✅ Live run-progress UI — shipped 07-28
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

**Network-view interaction precision** (live-rehearsal defects, 2026-07-28)

The 도식 is now legible (semantic zoom, metre-true markings) but not yet *precise to
click*. Two separate defects, both in `NetworkViewer.tsx`:

- ⬜ **Link hit bands overhang the drawn asphalt.** The invisible link hit polyline is
  `stroke-width = max(roadW + 8, 12)` in **world metres**, drawn ONCE outside
  `drawZoomDependent`. So every road is clickable ~4 m beyond each visible edge, and a
  narrow connector claims a 12 m band — and because the padding is metre-true, zooming
  in never tightens it. Symptom: clicking a small element *beside* a road selects the
  road, on ground the user can see is outside the blue selection shade.
  Fix: make the band screen-constant — `max(roadW, HIT_LINK_MIN_PX / k)` — which moves
  it into `drawZoomDependent`. **Regression risk:** that is the exact code path of the
  5774a28 click bug (d3-zoom fires `end` for a zero-movement click and rebuilds hit
  shapes between mouseup and click dispatch). The `|k/lastDrawnK − 1| < 1e-6` guard must
  be proven to still hold with links inside the redraw, by test, not by reading.
- ⬜ **No way to reach an occluded element.** Roads, junction polygons, connectors and
  glyphs stack; the topmost hit shape in DOM order always wins and there is no second
  path to the ones underneath. Fix: repeat-click cycling — a click within ~4 px of the
  previous one, with no intervening pointer move, advances through the full candidate
  stack from `document.elementsFromPoint()` instead of reselecting the same top hit —
  plus a "N개 겹침 · 다시 클릭" hint in the tooltip so the affordance is discoverable.
  Stretch: shift-click opens a small labelled picker listing every candidate.

**Deployment & persistence** (direction 2026-07-27, detailed 2026-07-28)

Target model: ON-PREM TEAM SERVER inside the customer's network; engineers connect by
browser over LAN. Cloud SaaS rejected near-term (Vissim licensing + confidential .inpx).
The LLM API is the only external call; on-prem LLM swap available via the provider seam.

*Hard constraint (do not design around it):* **COM automation is local-only** — the
process calling COM must sit on the same Windows machine as the Vissim install. A
FLOATING license changes only where the *seat* is checked out (a CodeMeter license
server), NOT where Vissim executes. Therefore the backend host is Windows; no Linux, no
containers for the backend. Independently of licensing, the Vissim worker eventually
wants its own box anyway — simulations are CPU-hungry and will make the shared web UI
sluggish while running. That is why the worker abstraction is worth building BEFORE the
deployment ladder, not after.

Build order:
- ✅ ① Edited-model download — shipped 2026-07-28 (per-version zip + sibling assets).
- ② Project workspaces: replace the global active-dataset singleton (upload currently
  rmtree's the previous one) with projects/<id>/ containers holding datasets, runs,
  chat sessions, artifact versions, audit log. **This is an ACCESS-CONTROL boundary,
  not just organization** — the moment two users share a server, the global active
  dataset is a client-confidentiality leak. Hence it precedes login.
- ③ Metadata layer: SQLite in WAL mode — users, projects, memberships, datasets, runs,
  sessions, messages, artifact versions, audit log. Heavyweight artifacts stay FILES
  (never blob a .inpx into the DB); the DB stores paths + metadata. Threading care
  required: the job runner mutates from worker threads → connection-per-thread (or a
  small pool), WAL, and a busy_timeout, or expect sporadic "database is locked".
  Postgres only when multiple backend processes/hosts are needed.
- ④ Login + roles: session-cookie auth, argon2/bcrypt hashes, server-side sessions,
  cookies HttpOnly + Secure + SameSite=Lax. Build this FIRST rather than SSO (zero
  external dependency — waiting on customer IT to provision an app registration stalls
  pilots), but keep the auth layer a seam so OIDC/SSO drops in later (MFA and, more
  importantly, instant offboarding). Roles: modeler / reviewer / admin, mapped onto
  the existing approval gate — approvals bound to a named user is what turns the gate
  into a compliance control.
- ⑤ Optional later: cloud view-only tier (dashboards, replay, health-check report —
  none need Vissim) with the runner staying on-prem.

Serving shape: uvicorn + `next start` as supervised Windows Services (NSSM/sc.exe) —
never dev servers, never --reload. One reverse proxy (IIS or Caddy) terminating TLS and
serving frontend + API under a SINGLE ORIGIN — this removes CORS entirely and lets the
frontend's multi-base fallback URL logic be deleted. HTTPS even on LAN (session cookies
over plain HTTP inside a corporate network leak sideways).

Security, in priority order:
1. Authorization (project membership checks on every endpoint) — the real work; see ②.
2. Append-only audit log: who proposed, who approved, what changed, which version
   resulted. Doubles as the 변경 이력 export customers want (compliance = feature).
3. Uploads are the hostile surface: extension/size validation, zip-bomb guard, and the
   Vissim worker runs under a RESTRICTED Windows account with write access only to the
   work dirs — never Administrator.
4. Secrets + budget: ANTHROPIC_API_KEY server-side only with locked file permissions;
   per-user LLM usage tracking/caps and per-user job caps so one person cannot burn the
   month's budget or starve the queue.
5. Extend the existing path-traversal discipline (regex + relative_to, already in the
   download endpoints) to every new project-scoped file endpoint.

Operational gotcha to solve BEFORE any real deployment: **disk**. One 600s/5-seed
trajectory measured 44MB; multiplied by users and runs the drive fills in weeks and
Vissim fails ugly on a full disk. Need a retention policy (auto-expire .fzp after N
days, keep results + version snapshots) plus free-space monitoring.

Staged rollout: pilot (3-5 users, one Windows box, session auth, SQLite, internal TLS,
retention policy from day one) → hardening (roles + audit log, automated & TESTED
backups, OIDC if IT wants, quotas) → scale-out (worker on its own machine).

OPEN — must be answered before committing to variant A (ask TK / PTV Korea, get it in
writing): node-locked vs floating and how many seats; whether terms permit server-side
/ automated execution or restrict use to a named user at a licensed machine; whether COM
is included in Dochak's license tier or is an add-on; whether a batch/server SKU exists;
and whether customer sites have their own Vissim licenses (determines if the
local-worker fallback is viable there). If the answer is node-locked-only, the fallback
is a worker installed on each engineer's own PC pulling jobs from the central server —
each person uses their own seat, and the licensing question stops being load-bearing.

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
