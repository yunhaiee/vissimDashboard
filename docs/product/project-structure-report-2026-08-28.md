# Prism (vissimDashboard) — Project Structure & Transition Report

**Date:** 2026-08-28 · **Verified against:** commit `b81f695` (`main` == `origin/main`, nothing unpushed)
**Prepared at:** Yunha Lee's handoff of the project. Written for a maintainer who has never seen this codebase.
**Verification run this day:** backend `pytest` 6,322 passed / 14 skipped / 14 machine-local failures (explained in §10.2 — not code regressions) · frontend `tsc --noEmit` clean · dashboard contract suite 264/264 green.

---

## 0. How to use this document

This is the **orientation map**, not the live work state. Three documents remain the living authorities and win over this report wherever they disagree:

1. `docs/product/master-roadmap-v2-2026-08-11.md` — the master plan. Its §5 tables are maintained continuously (last touched 2026-08-27); its §2 prose snapshot is dated 2026-08-11 and *understates* the current state.
2. `docs/phase2/DECISIONS.md` — the narrative ledger (7,283 lines, ~200 dated entries). The roadmap says it explicitly: "서사는 DECISIONS.md가 갖는다" (the narrative belongs to DECISIONS; the roadmap is only an index).
3. `docs/product/handoff-2026-08-26b.md` — the newest doc in the handoff chain. Each handoff declares itself the successor's authority and wins over its predecessor on conflict.

Read §16 of this report for the recommended first-week reading order.

---

## 1. What Prism is

Prism is **Dochak's proprietary product**: a Vissim traffic-simulation platform that packages a traffic consultant's entire workflow — scoping → data intake → **AI network build ("Genesis")** → **AI calibration** → scenario sweep → **styled report** — behind a web dashboard, with an LLM agent that edits Vissim models through the COM interface.

The load-bearing thesis, stated everywhere from `CLAUDE.md` to the eval plan: **Vissim fails silently, so the validation layer is the product.** Vissim almost never refuses a bad edit — it reports confident wrong numbers (gridlocked movements report LOS A; conflicting greens *improve* reported delay; unmet demand silently evaporates). Prism substitutes the error channel Vissim never provides: a constraint-encoded validation layer, a nine-check generation gate, and an agent that **refuses with a named reason when it is likely to be wrong**. A refused edit with a clear reason beats a completed edit with wrong numbers.

Product decisions fixed in `docs/product/tk-requirements-2026-07-31.md` (v2.1): Korea/**Daejeon-first** via a format-neutral adapter layer (WA SCATS second), **micro** simulation (not meso), Vissim-focused, sold as consultant tooling. The client authority for validation numerics is **Main Roads WA (MRWA)** — Prism's Category-1 thresholds match MRWA Table 5-12 exactly, and the report engine's section spine is derived verbatim from MRWA reporting requirements. The TR-C research paper is **deferred** (post-US-move), but the sweep/calibration instrumentation deliberately keeps accumulating paper data (`docs/phase2/04-EVAL-PLAN.md`).

**Stack in one line:** Next.js 16 frontend (`:3000`) → same-origin `/api/*` proxy → FastAPI backend (`:8001`) → services layer → background worker threads → Vissim COM + a file-based store under `runtime/`. One small SQLite DB exists for auth/membership metadata only ("metadata only, files stay files" — `backend/services/db.py`). LLM calls go to Anthropic (`claude-sonnet-5` by default) with an optional local Qwen provider.

---

## 2. The consultant pipeline and where each stage stands

| Stage | What exists | Grade |
|---|---|---|
| Scoping / region | Lasso + bbox region picker → Overpass `.osm` fetch (`/genesis/region`) | Product UI |
| Data intake | Datasets (.inpx), counts workbooks, observation sets, Daejeon 현시도 (phase-diagram) Excel, WA SCATS adapter | Product UI + adapter layer |
| Network Genesis | OSM → geometry draft → signals/demand/fleet → compose → emit .inpx → **V1–V9 gate** → publish. Whole chain offline-by-construction (no COM imports) | Product UI (hub + job detail with gate rail); the from-map **full walk reaches the gate and is currently refused by V5/V9** — see #75 in §13 |
| Calibration | 13-step rail on `/calibration`, GEH ladder, parameter search jobs, reality-check refusals, signed step attestations, movement-level honest-refusal doctrine | Product UI |
| Scenario sweep | Multi-seed batch runs (`RunContinuous`), sweep tooling in agent toolkit | Agent tools + jobs; ranking/曲면 UI (roadmap E-2.2) still open |
| Report | Track R engine: MRWA-derived section spine → data extraction → style profile → `.docx` + SVG zip (`/report`) | Product UI; style-from-sample (E-2.6) open; **HWP excluded from v1** (decision) |

Measured milestones recorded in roadmap §2.1: live-verified edit/validate/approve/rollback (37 tools / 90 constraints at the 08-11 count; `constraints.py` has grown since), calibration loop GEH 7.53→4.19 on demo, first GEH vs **real observations** 2.79/3.51/6.38 (WA SCATS, 30,208 counts), Daejeon signal-sheet parsing 22/23 offline and 20/22 → fixed-time plans.

---

## 3. System architecture

```
Browser (ko primary; en/ja)
   │
   ▼
Next.js 16 dev/prod server  :3000
   │  next.config.ts rewrites()  /api/:path* ─────────────┐
   │  (proxy body limit 250 MB for .inpx/zip uploads)     │
   ▼                                                      ▼
frontend/lib/api.ts (6,542 lines — the ONLY door)   FastAPI  :8001  (backend/main.py)
   • apiFetch(): same-origin first, then direct-       │  middleware (outer→inner):
     base fallback ladder to 127.0.0.1:8001            │   license gate → PNA header shim → CORS
   • every request stamped X-Workspace-Id              │   → WorkspaceScopeMiddleware (innermost,
   • auth calls strip that header (pinned)             │     so its 403/404 carry CORS headers)
   • 1 WebSocket: /api/simulations/ws/{jobId}          ▼
                                                 21 routers (backend/routers/, 11,227 lines)
                                                       │
                                                       ▼
                                                 services/ (~150k lines)
                                                       │
        ┌──────────────────────────────┬───────────────┼──────────────────┐
        ▼                              ▼               ▼                  ▼
  services/agent/             services/genesis/   vissim_com.py      file store
  (42 modules, ~59k lines)    (21 modules, 34k;   (5,435 lines,      runtime/  (gitignored)
  planner → orchestrator →    offline BY          COM wrapper)        workspaces/ simulations/
  gates → toolkit dispatch →  CONSTRUCTION —          │               agent_sessions/
  guardrails/constraints →    nothing imports         ▼               parameter_search/
  trace JSONL                 COM) … gate.py     PTV Vissim GUI       db/app.db (auth metadata
                              (V1–V9, 3,978 ln)  (licensed, COM)      only) · rag index
```

Key structural facts:

- **No job broker.** Long work runs on daemon `threading.Thread`s inside the single uvicorn process, with durable JSON registries (`runtime/simulations/simulation_runs.json` etc.) written temp+fsync+`os.replace`; corrupt registries are quarantined as `.corrupt-*`. Boot reconciles orphaned `queued/running` jobs to `error` (lifespan in `main.py`, each step wrapped so failure never blocks startup).
- **COM never runs in a request handler.** Handlers only enqueue; the source `.inpx` path and `workspace_id` are **frozen at enqueue time** on the request thread (a deliberate TOCTOU fix); worker threads call `pythoncom.CoInitialize()` and re-bind the workspace explicitly because ContextVars do not cross threads.
- **Workspace scoping** (UI: 프로젝트) is a raw-ASGI middleware binding `X-Workspace-Id` → ContextVar; unknown id → 404 `workspace_not_found`, non-member → 403 `workspace_forbidden`; `/api/auth/` exempt; websockets degrade to the default workspace (browser WS API cannot carry the header — documented at the call site).
- **LLM provider:** `services/claude_provider.py` — `DEFAULT_CLAUDE_MODEL = "claude-sonnet-5"`, override via `ANTHROPIC_MODEL`; `timeout=90.0, max_retries=1`; `ANTHROPIC_MAX_TOKENS` default 2048. `ASSISTANT_PROVIDER=qwen` switches to the local Qwen worker (dev servers set `QWEN_WORKER_AUTO_START=0`). Structured-output ladder in `services/agent/structured.py`; phase-diagram vision reuses the same provider.

---

## 4. Repository map

| Path | Role |
|---|---|
| `CLAUDE.md` | Project instructions: ports, hard rules, conventions. Current (2026-08-18). |
| `README.md` | Korean customer-facing overview. **Stale** (2026-07-28): says port 8000, describes the v1 agent, lists as "future" much that has shipped. Do not trust for state. |
| `backend/` | FastAPI app. See §5. |
| `frontend/` | Next.js 16 app. See §6. |
| `docs/` | All plans, doctrine, handoffs, references. See §12. |
| `shared/` | Cross-language fixtures: `geh-conformance.json` (pinned by a backend test AND a frontend test so `frontend/lib/geh.ts` and `backend/services/validation.py` cannot drift), `mrwa-validation-criteria.json`. |
| `runtime/` | **Gitignored** file-based store: workspaces, simulation jobs, agent sessions, parameter search, RAG index, `db/app.db`. See §9. |
| `data/` | **Gitignored** demo Vissim data (`practice.inpx`, `practice.vissimpdb`, `Scenarios/`), regenerable via `backend/tools/make_demo_data.py`. |
| `client-data/` | **Gitignored** client drops: Daejeon signal zips, 대전-현시도-엑셀, MRWA workbooks + `operational-modelling-v2.pdf` (14.9 MB), `murdoch.osm`, report style samples, 유성구 traffic workbook. **Does not travel with a clone — must be handed over out-of-band** (§9.3). |
| `.claude/launch.json` | Dev-server configs, Windows + Mac variants (§11.1). `.claude/worktrees/` holds 12 stale `agent-*` worktrees (cleanup candidates, matching `worktree-agent-*` local branches). |
| `시퀀스 01.mp4`, `시퀀스 02.mp4` | Untracked demo recordings at repo root (391 MB + 149 MB). **Not gitignored** — an unguarded `git add -A` would stage 540 MB. Per handoff-08-24b §6: never commit, never delete (user's files). |

Git: branch `main`; remote **only** `https://github.com/yunhalee2008/vissimDashboard.git` (hard rule: private, no other remotes, no collaborators without the owner's say-so). Historical remote branches: `phase2`, `phase3-render`, `ui-dark-theme`, `v2-tools`. No CI, no git hooks — every quality gate is manual (§10).

---

## 5. Backend (`backend/`)

### 5.1 Entry point and routers

`backend/main.py` (411 lines) assembles `FastAPI(title="VISSIM Platform API")`. Port 8001 appears **only** in `.claude/launch.json` uvicorn args, nowhere in code. 21 routers under `backend/routers/`:

| Router | Prefix | Responsibility |
|---|---|---|
| `network.py`, `search.py`, `network_diff.py` | `/api/network` | Parsed .inpx reads; find-by-name/number; structural diff of session snapshots |
| `datasets.py` | `/api/datasets` | Workspace-scoped .inpx/workbook upload (current/staging) |
| `workspaces.py` | `/api/workspaces` | 프로젝트 list/create/describe |
| `scenarios.py` | `/api/scenarios` | Scenario list/results/compare |
| `simulations.py` (1,168) | `/api/simulations` | COM run jobs, WebSocket progress |
| `geojson.py`, `trajectories.py` | `/api/geojson`, `/api/trajectories` | WGS84 geometry; per-timestep vehicle positions (.fzp) |
| `validation.py` | `/api/validation` | Model vs real counts (GEH) |
| `assistant.py` (2,002) | `/api/assistant` | Chat + agent stream/approve (NDJSON) |
| `exports.py`, `report.py` | `/api/exports`, `/api/report` | Excel deliverables; .docx report assembly |
| `agent_downloads.py`, `agent_audit.py` | `/api/agent/*` | Session's edited .inpx; audit trail → .docx |
| `auth.py`, `admin.py` | `/api/auth`, `/api/admin` | login/logout/whoami; users + membership |
| `observations.py` | `/api/observations` | Ingested field counts |
| `genesis.py` (2,921) | `/api/genesis` | OSM→draft→emit→gate→publish jobs |
| `health.py` | `/api/health-check` | 모델 검진 integrity report |
| `calibration.py` (1,307) | `/api/calibration` | Calibration read side, parameter search, attestations |

### 5.2 The two "assistants" — don't confuse them

- `backend/routers/assistant.py` — the HTTP surface.
- `backend/services/assistant_router.py` — a **3,156-line monolith** (98 top-level defs): intent classification, `LocalQwenProvider`, ~20 `build_*_answer[_en|_ja]` deterministic answer builders, parsers, and `clean_generated_answer()`. **Hard rule: new agent code goes in `backend/services/agent/`, and grep this monolith for an existing helper before writing one — it has had duplicates.**

### 5.3 The agent (`backend/services/agent/`, 42 modules, ~59k lines)

The real control loop (no file is literally named planner/executor/verifier):

- `planner.py` (955) — assembles the per-turn prompt (system prompt is a file: `services/agent/prompts/v1_system.md`, mtime-cached) and returns one `PlannerDecision`.
- `orchestrator.py` (1,835) — the loop: plan → **tier gate** (`read` free; `write`/`run` gated) → `GuardrailEngine.preflight` → `toolkit.dispatch` → `postwrite` (with rollback) / `postrun` → trace JSONL.
- `gates.py` — `RefuseAllWriteGate` / `ProposeWriteGate` / `PreapprovedWriteGate`; `ApprovalRequired` pauses the turn until a human approves in the UI.
- `toolkit.py` — single tool registry; contract and dispatch generated from one `@tool` registration. Tools live in `tools_network/edit/run/metrics/check/calibration/sweep.py`.
- Verification: `constraints.py` (**11,589 lines** — the ko/en/ja constraint catalog) + `guardrails.py`. The doctrine: classify every capability by **reversibility × verifiability**; every setter needs a paired read-back; **no verification ⇒ the verb does not ship** (`docs/TOOL-CATALOG.md` is the authoritative verb matrix).
- State: `state.py` (`ArtifactManager` — versioned `edit_NNN.inpx`), `session_store.py`, `edit_session.py`, `rollback.py`, `trace.py`.
- Calibration/search: `parameter_search.py`, `search_runner.py`, `search_job.py`, `geh_ladder.py`, `reality_check*.py`, `saturation_flow.py`, `mrwa_reference.py`, `phase_diagram_vision.py`, `fixes.py`, `genesis_fixes.py`.

### 5.4 Genesis (`backend/services/genesis/`, 21 modules, 34k lines)

**Offline by construction — nothing in the package imports COM.** Pipeline order (per `genesis/__init__.py`): `osm` → `project` → `connectivity` → `draft` → `demand` / `signals` / `signals_daejeon` / `fleet` → `compose` → `side_inputs` → `emit` → `diff` / `benchmark` → `gate`.

**`gate.py` (3,978 lines)** implements requirement R1.5's V1–V9 (§7). `services/genesis_jobs.py` (8,586 lines) is the job layer around it.

### 5.5 Vissim COM layer

- `services/vissim_com.py` (5,435 lines) — `VissimController`. **Caution:** `connect()`'s signature default is `attach_to_running=True` (the unsafe path — GetActiveObject attaches to a human's open GUI and can destroy their work). Every automation call site passes `attach_to_running=False` explicitly (`routers/simulations.py`, `agent/edit_session.py`, `agent/search_runner.py`, `agent/tools_check.py`, `vissim_open_probe.py`, `vissim_tool.py`). Any **new** call site must do the same.
- `save_net_as()` **refuses to write over the loaded path** — the structural enforcement of "never edit the active dataset in place". Simulation jobs clone via `services/simulation_network_builder.py` → `<stem>.<job_id>.interactive.inpx` in the job dir; agent edits persist via `save_net_as(tmp)` + `os.replace` into the session's `working.inpx`.
- Batch runs use `RunContinuous` (~7.3× realtime); only the interactive live-progress path deliberately uses `run_single_step()`.
- ProgID ladder tries Vissim 2026 → 2025 → 2024 → generic. A licensed, logged-on session is required (`license_guard.py`).

### 5.6 i18n on the backend

There is **no backend `t()`** (that's a frontend function). Server-authored verdicts are `{ko, en, ja}` dicts (47 service modules), resolved by `services/agent/planner.py::pick_language_text` (unknown language → Korean). Deterministic prose uses the language-suffixed `build_*_answer` builders in the monolith. **`clean_generated_answer()` (`assistant_router.py:2922`) strips `<think>` blocks, reasoning-prefix lines, markdown — and JSON. Strict-JSON LLM paths must bypass it** (pinned by a source-level test over `phase_diagram_vision.py`).

### 5.7 Tools and eval

`backend/tools/` — 91 CLI scripts, mostly `probe_*` / `*_live_check.py`: one-shot COM harnesses that empirically verified each attribute id before it was trusted in `vissim_com.py` (this is why the wrapper can be trusted). Operationally important: `make_demo_data.py` (§11.3), `manage_users.py` (**the only way accounts are created** — no default credentials by policy), `build_rag_index.py`, `genesis_validation_gate.py` (CLI gate), `genesis_open_live_check.py` (produces V1's load evidence), `make_b52_demo_job.py` (a manufactured REFUSED genesis job for demos).

`backend/eval/` — the offline agent-eval harness (runner, scoring, injection, baselines; tasks `T001–T015`) that will feed the deferred TR-C paper.

---

## 6. Frontend (`frontend/`)

Next **16.2.4** / React **19.2.4** / TypeScript ^5 / Tailwind v4. Deps: d3, leaflet + react-leaflet, recharts. 21 pages (~20,119 lines) + 46 components (~30,379 lines).

### 6.1 Screens (sidebar groups from `components/AppShell.tsx`)

| Route | Nav (ko) | Purpose | Lines |
|---|---|---|---|
| `/` | — | Redirect → `/overview` | 12 |
| `/overview` | 개요 | Four-stage project summary; owns the "no project yet" branch | 537 |
| `/genesis` | 생성 | Genesis hub: history + primary action; polls jobs | 637 |
| `/genesis/jobs/[id]` | — | Job detail: map left / **gate-verdict rail** right | 3,012 |
| `/genesis/region` | — | bbox + polygon lasso → Overpass `.osm` → staged token | 1,270 |
| `/genesis/demand` | 수요 | Counts workbook → site→leg→link join → `genesis.demand.v1` token | 592 |
| `/genesis/phases` | 신호 (현시도) | Phase-diagram Excel upload, Φ strip, 네트워크 결박 attestation | 1,679 |
| `/network` (+`/network-diff` shim) | 네트워크 뷰 | Network preview; `?mode=diff` folds in `NetworkDiffView` | 813 |
| `/health` | 모델 검진 | Health-check report + download | 549 |
| `/observations` | 실측 관측 | Observation-set store, master-detail | 854 |
| `/simulation` | 시뮬레이션 | Run launch/monitor; WS + interval poll | 1,343 |
| `/dashboard` | 대시보드 | **Main dashboard**: scenario comparison, d3 `NetworkViewer`, trajectory playback, node-flow drilldown | **4,034** |
| `/calibration` | 보정 | Fixed context bar + 13-step/8-station rail (absorbed the retired `/base-model`, 308 redirect) | 1,639 |
| `/report` | 작성 | Track R builder: spine tree + preview + style profile → .docx + SVG zip | 1,401 |
| `/downloads` | 아티팩트 | Export catalogue (ko/en/ja labels keyed by backend export key) | 530 |
| `/admin`, `/login`, `/docs` | 관리 / — / 문서 | Users & membership; sign-in (deliberately no signup/reset); in-app doctrine reader | 792/202/128 |

Biggest components: `calibration/BaseModelStation.tsx` 2,710 · `FloatingAssistant.tsx` 2,615 (globally mounted agent drawer: streaming, approvals, step log) · `NetworkViewer.tsx` 2,326 · `NodeDirectionTable.tsx` 1,625 · `NodeFlowPanel.tsx` 1,592 · `agent/ApprovalCards.tsx` 1,472 (shared with `/calibration`) · map family on a CARTO dark basemap · `report/LegacyReportBuilder.tsx` 649 (pre-Track-R path, demoted not deleted).

`components/genesis/PhaseDiagramSketch.tsx` (437) is the type-enforced phase renderer: `Glyph` union routes `unknown`→chip and `pedestrian`→crossing with **no default arm**, and `Record<VehicleTurn, …>` over exactly `through|left|right|u_turn` means an unknown movement *cannot compile into an arrow*. Placement is index-order, never bearing, with a mandatory disclaimer. The closed seven-tag host vocabulary (`a, div, img, li, p, span, ul`) is pinned in the contract test over the reading column of `/genesis/phases`.

### 6.2 Backend wiring

`frontend/lib/api.ts` (**6,542 lines**) is the single binding layer. `apiFetch()` tries the same-origin relative path (proxied by `next.config.ts` `rewrites()` to `API_PROXY_TARGET ?? NEXT_PUBLIC_API_BASE ?? http://127.0.0.1:8001`), then on network-class errors walks a direct-base fallback ladder; base overridable at runtime via `localStorage["vissim-api-base"]`. **Every request carries `X-Workspace-Id`** (persisted in `localStorage["vissim-workspace-id"]`), injected centrally; auth calls use `noWorkspaceRequestInit`, which deletes the header — both behaviors are pinned. One WebSocket (`createSimulationWS`); agent streaming reads `response.body.getReader()`. Polling: genesis 1,000 ms (`lib/genesisArtifacts.ts`), parameter search 5,000 ms, assistant refresh 2,500 ms. Async loading standardized in `lib/asyncResource.ts`.

### 6.3 i18n

No i18n library, no locale routes. `LanguageProvider.tsx` supplies `t(ko, en, ja?)`; Korean literals **are** the keys, inline in JSX; server renders `ko`, preference read post-hydration from `localStorage["vissim-language"]`. Japanese: 3-arg `t()` wins, else `JA_UI[ko] ?? en` from the auto-generated dictionary `lib/i18n-ja.ts` (~574 entries). Server-authored `{ko,en,ja}` payloads resolve via `lib/serverText.ts` — passed through, never composed client-side.

### 6.4 The contract test — this project's constitution

`frontend/tests/dashboard-contract.test.ts` — **17,977 lines; 264 tests (252 `contractTest` + 12 behavioural); 2,218 pins** (1,788 `pin.match` + 430 `pin.doesNotMatch`) — regex-pins **raw source text** read straight off disk. Runner: `node --test --experimental-strip-types` (Node ≥ 22 in practice; plain `node:test`, no vitest/jest).

Why it looks the way it does:

- `contractTest` hands the body a `Pin` whose failures are **recorded, not thrown**, then fails once with the complete list — because on 2026-08-05 one stale pin hid 2 broken + 53 healthy pins behind the first abort.
- Helpers (`sliceBetween`, `functionSource`, `topLevelFunction`, `withoutComments` — itself pinned) are CRLF-tolerant, because a Windows rewrite once silently emptied a haystack and made `doesNotMatch` pins pass vacuously.
- Classes pinned: route/nav composition; API-binding shape (workspace header present/absent); refusal/verdict semantics ("never present a proposal as a fact"); ko/en/ja triples + ja-dictionary coverage; forbidden idioms; cross-file constants against `shared/*.json`; layout/style pins kept in their own tests; and non-frontend files — including `docs/product/calibration-doctrine.md` (doctrine sentences die if they reappear on-screen *and* if they vanish from the doctrine file).
- **Threat-model boundary (recorded in DECISIONS.md):** the suite defends against careless drift by well-meaning editors and against refuter-demonstrated named mutation classes. Deliberate sabotage that can also edit the tests is explicitly out of scope.
- **Trap:** strip-types erases types without checking them and can't resolve `@/` aliases — invariants "enforced by the type system" are only verified when `npm run typecheck` runs separately. Run both, always.

The only other frontend test: `tests/geh-conformance.test.ts`, sharing `shared/geh-conformance.json` with `backend/tests/test_geh_conformance.py` so the two GEH implementations cannot drift. `npm run lint` covers only ~9 hand-listed files (known gap: genesis screens are missing from the list — open chip). `frontend/AGENTS.md` demands reading the relevant guide in `node_modules/next/dist/docs/` before writing any Next code ("This is NOT the Next.js you know").

---

## 7. The validation layer

Three enforcement surfaces, one doctrine:

1. **Agent-edit constraints** — `services/agent/constraints.py` + `guardrails.py`: preflight before any write, read-back verification after, rollback on failure, honest refusal with a named reason. Verb inventory governed by `docs/TOOL-CATALOG.md` ("a verb ships only when its verification does").
2. **The Genesis gate** — `services/genesis/gate.py`, implementing requirement R1.5:
   - **Refuse tier (all must pass):** V1 load oracle (generated .inpx passes COM load+init, 0 critical errors — evidence produced by `tools/genesis_open_live_check.py`, and the gate verifies the **sha256 of the loaded file matches the file being gated**) · V2 reference integrity (zero dangling XML refs) · V3 connectivity (every input reaches an exit; no orphan zones) · V4 lane continuity · V5 signal–geometry consistency (every signalised approach has a head; every group maps to a head; no controller-less heads) · V6 demand coverage (structural refuse / capacity warn).
   - **Warn tier:** V7 geometry sanity · V8 conflict coverage (Passive count reported explicitly) · V9 measurement readiness (queue counters at stop lines, travel-time sections).
   - Every check resolves through one funnel with severity order **FAIL > NOT_CHECKABLE > PASS** — a check that cannot run never passes. `GateVerdict` = `PASS` only if every refuse-tier check truly passed; missing V1 evidence → `INCOMPLETE`; any refuse-tier FAIL → `REFUSED`. Thresholds derive from the emitter's own constants, so emitter and gate cannot drift apart.
3. **Calibration doctrine** — `docs/product/calibration-doctrine.md` is the **single source** for doctrine/guard sentences (evicted from the `/calibration` screen by user decision: "the screen owes the user action, not education"). Three consumers: the contract test pins the sentences, `/docs` renders them via `lib/help-content.tsx`, the RAG index ingests them. Calibration steps carry **signed attestations** with a machine-outranks-testimony status funnel and .inpx-sha staleness checks (`services/calibration_attestation.py`).

The proudest recent fact in the ledger: the first end-to-end from-map walk (2026-08-27) reached the gate and was **refused by the project's own V5/V9** — recorded in commit `841f807` as "every door that refused it, and why each refusal was right." That is the product behaving as designed; making generation satisfy its own gate is open item **#75**.

---

## 8. Storage model

**"File-based, no DB" with one scoped exception.** `backend/services/db.py` owns SQLite at `runtime/db/app.db` for `users`, `auth_sessions`, `workspace_members` — *metadata only, files stay files*. Everything else is files:

```
runtime/                                  (gitignored, resolved by backend/config.py)
├─ workspaces/registry.json               workspace registry (default: 기본 프로젝트)
├─ workspaces/<12-hex>/                   per-프로젝트:
│   ├─ uploads/current/{current.inpx, metadata.json, real_counts.xlsx}
│   ├─ uploads/staging/                   tokens from region/demand/phases screens
│   ├─ observations/registry.json         field-count sets
│   ├─ genesis/                           genesis artifacts per workspace
│   └─ mapping_memory.json
├─ simulations/<job_id>/                  cloned .inpx, results/*.db (Vissim SQLite), *.fzp
│   └─ simulation_runs.json               durable job registry
├─ agent_sessions/<session_id>/           state.json, trace_*.jsonl, pending_approval.json,
│                                         working.inpx, edit_NNN.inpx  (7-day TTL sweep at boot)
├─ parameter_search/<8-hex>/              search jobs
├─ db/app.db                              auth/membership metadata (SQLite)
├─ rag_tfidf.joblib (17.7 MB) + rag_pdf_cache/
└─ ~70 dated probe/live-check output dirs from backend/tools
```

`data/` holds the synthetic demo network (regenerable); `client-data/` holds the real client materials (not regenerable). Formats across the store: JSON registries, Vissim XML (`.inpx`/`.fzp`), SQLite results `.db`, `.xlsx`, `.docx`.

---

## 9. The data estate — what does NOT travel with git

This is the most transition-critical section. A fresh clone contains code and docs **only**. The following live outside version control and must be handed over deliberately:

### 9.1 Secrets
`backend/.env` — one key: `ANTHROPIC_API_KEY`. Gitignored (with `!.env.example` re-included; there is no backend `.env.example`). **The successor should issue their own key**, not inherit this one. Never commit, never print.

### 9.2 Accounts
Accounts live in `runtime/db/app.db` and are created **only** by `backend/tools/manage_users.py` (`create-admin`, `create-user`, `reset-password`, …). A successor on a fresh machine must create their own admin.

### 9.3 Client materials (`client-data/` — irreplaceable)
Daejeon signal zips + 대전-현시도-엑셀 (the 23 phase-diagram sheets), MRWA `operational-modelling-v2.pdf` + workbooks, `murdoch.osm`, report style samples, 유성구-교통량-워크북, 데모-교차로. Also **outside the repo entirely**: the corrected Daejeon phase-diagram delivery in `Desktop\대전현시도-정정본` (client deliverable, 08-24), the original client model package in `Downloads` (the byte-verified restore source from the 08-20 incident), and the two `시퀀스 *.mp4` demo recordings at repo root.

### 9.4 Runtime state worth preserving
`runtime/workspaces/` — 21 workspaces including 기본 프로젝트 (`bfc69251bd04`), the WA SCATS 실측 대조 pair (`22fef7afb149`, `026582e612c4` — these currently hold the intact Murdoch-scale network copies), the live-demo lineage, and `완주검증-claude` / `demo-full-run` (the 08-27 full-walk evidence). `runtime/agent_sessions/` traces and `runtime/simulations/` results are the raw material the deferred TR-C paper expects to mine. The RAG index (`rag_tfidf.joblib`) is regenerable via `build_rag_index.py` **only if the PDF corpus is present** — the corpus is itself gitignored.

### 9.5 Known state right now (2026-08-28)
기본 프로젝트's active dataset is currently `current_edited_4435a945_001.inpx` (uploaded 2026-08-27 04:26 UTC during the demo-recording session) — an agent-edited demo network, **not** the Murdoch client model. This is why 14 machine-local tests fail (§10.2). Same displacement class as the resolved 08-20 incident recorded in DECISIONS.md. **There is no backup/restore tooling in the repo** — treat `runtime/` and `client-data/` as production data and copy them wholesale during the machine handover.

---

## 10. Verification playbook

### 10.1 The gates (all manual — there is no CI)

```
# backend (from backend/, venv at backend/.venv, Python 3.11.2)
.venv\Scripts\python.exe -m pytest

# frontend (from frontend/)
npm run typecheck                  # tsc --noEmit — REQUIRED; the test runner does not typecheck
npm run test:dashboard-contract    # node --test, 264 tests / 2,218 source pins
npm run test:geh-conformance       # shared-fixture conformance vs backend
npm run lint                       # scoped to ~9 named files only
```

Plus the milestone's named acceptance check (see roadmap rows), and the standing rule: non-COM code must work against synthetic demo data (`python backend/tools/make_demo_data.py`) — never make it require live Vissim.

### 10.2 Verified state on 2026-08-28

- Frontend: `tsc` clean; contract **264/264**; suite runs in ~1 s.
- Backend: **6,322 passed / 14 skipped / 14 failed in 4m22s.** The 14 failures are all `@_requires_real_dataset` acceptance tests (Murdoch Drive queries in `test_network_search.py`, `test_agent_constraints.py`, `test_agent_read_tools.py`, congested-scene harness). They resolve `ACTIVE_REAL_INPX` to the **default workspace's current upload**, which is currently the swapped-in demo network (§9.5) — so the guard doesn't skip (file exists) and the content assertions fail. **Not code regressions.** On a fresh checkout these tests *skip* (file absent): expect **6,322 passed / 28 skipped**. To restore full green on this machine, re-upload the Murdoch client model into 기본 프로젝트 (intact copies: the WA SCATS workspaces' `current.inpx`, or the original package in Downloads).
- Suite conventions: a contract-test failure is real (pins are co-edited with the source they pin, in the same commit); `test_rag_retrieval_quality` passes or skips-with-reason by corpus presence — don't weaken its measured floors.

### 10.3 The refuter convention

Recent development ran builder → **refuter** (an adversarial pass that attacks the fresh change) → orchestrator re-verification, one unit = one commit; DECISIONS.md records refuter catches as first-class history (28/28 streak through late August). Whether the successor keeps the ritual is their call, but the ledger's defect classes (silent drops, fragment pins letting en/ja drift, vacuously-passing `doesNotMatch`) are worth reading before touching pinned code.

---

## 11. Development environment

### 11.1 Dev servers (`.claude/launch.json`)
Four configs — `backend` / `frontend` (Windows PowerShell) and `backend-mac` / `frontend-mac` (bash): uvicorn `main:app --app-dir backend --port 8001` with `QWEN_WORKER_AUTO_START=0`, and `npm run dev` on 3000 (`autoPort: true`). Keep scripts cross-platform — the project runs on a Windows desktop (with Vissim + COM) and a Mac laptop (everything non-COM).

### 11.2 Remote access
Tailscale serve exposes the dev frontend at `https://desktop-ck.tailf57fd1.ts.net`; Next 16 blocks unknown dev origins, hence `allowedDevOrigins` in `next.config.ts` (symptom when missing: browser hangs at "불러오는 중…", `/_next/webpack-hmr` blocked while curl works). `experimental.proxyClientMaxBodySize` is raised to 250 MB for `.inpx`/zip uploads.

### 11.3 Fresh-machine bootstrap
1. Clone; create `backend/.venv` (Python 3.11); `pip install -r backend/requirements.txt` — **first fix `requirements.txt:8`**: `python-dotenv==310; platform_system == "Windows"` is a broken pin (310 is a pywin32 version; the working venv has `python-dotenv 1.2.2` + `pywin32 310`). Optional RAG extras: `requirements-rag.txt` (chromadb, sentence-transformers).
2. `frontend`: `npm install` (Node ≥ 22 required in practice for `--experimental-strip-types`; unpinned — consider adding `engines`).
3. `backend/.env` with a fresh `ANTHROPIC_API_KEY`.
4. `python backend/tools/make_demo_data.py` for synthetic data. **Warning from its own docstring:** a default `--out` run `rmtree`s `data/Scenarios` and materialises `real_counts.xlsx`, flipping the demo validation surface from "workbook not found" to "observed counts present" — use `--out` elsewhere if that matters.
5. `python backend/tools/manage_users.py create-admin …`.
6. Vissim work additionally needs: PTV Vissim installed (2024–2026 ProgIDs), licensed, in a logged-on interactive session.

---

## 12. Documentation authority map

| Document | Status | What it's for |
|---|---|---|
| `CLAUDE.md` | AUTHORITATIVE | Hard rules + conventions; read first. |
| `docs/product/master-roadmap-v2-2026-08-11.md` | AUTHORITATIVE (living) | The plan. §5 tables current through 08-27; §2 prose snapshot stale (08-11). Done = one line + commit; remaining = detailed. |
| `docs/phase2/DECISIONS.md` | AUTHORITATIVE (living) | Interface-gap protocol + the narrative ledger (~200 entries). Contract pins cite it by line. |
| `docs/product/handoff-2026-08-26b.md` | AUTHORITATIVE (chain head) | Latest work-state handoff; each handoff supersedes its predecessor. |
| `docs/product/tk-requirements-2026-07-31.md` (v2.1) | AUTHORITATIVE | What TK is buying; EPICs 1–5; R1.5 defines V1–V9. |
| `docs/phase2/00-EXECUTION-GUIDE.md` → `01` → `02` → `03` → `04` | AUTHORITATIVE | Executor contract + verified COM landmines; architecture thesis; M0–M5 (done); research digests; eval plan (paper substrate). Read 00 before ANY agent/COM work. |
| `docs/TOOL-CATALOG.md` | AUTHORITATIVE | Agent verb matrix by verification tier. |
| `docs/product/calibration-doctrine.md` | AUTHORITATIVE (pinned) | Single source of doctrine sentences (contract-test-enforced). |
| `docs/product/report-engine-design-2026-08-24.md` | AUTHORITATIVE | Track R design: "style is skin, the spine is regulation." |
| `docs/product/adapter-contract.md`, `spec-c1b-*`, `data-intake-inventory-*` | AUTHORITATIVE | Format-neutral intake adapter layer; Daejeon C1-b spec. |
| `docs/product/reference/` (5 files) | AUTHORITATIVE | Verbatim MRWA extracts (reporting requirements = R-1 spine evidence; Appendix A signals; Appendix B saturation flow; LinSig tables; §5 Vissim guidelines). |
| `docs/product/master-roadmap-2026-07.md` | **SUPERSEDED** | v1 — kept ONLY because its rows carry per-unit measurements. Never plan from it. |
| `docs/ROADMAP.md`, `docs/meeting-brief-tk-mrwa.md`, demo scripts, ux-audit files | HISTORY | Legacy plan + deployment ladder; meeting prep; dated demo scripts (newest: `demo-script-2026-08-27.md` — uses the pre-baked workspace, since the from-map path is currently gate-refused). |
| `docs/ux-backlog.md` | DEFERRED | 26 surviving UX gaps + an intake section marked "do not spec yet." |
| `docs/product/hwp-feasibility.md` | MOOT | HWP excluded from v1 by decision. |
| `README.md`, `frontend/README.md` | **STALE** | Rewrite before showing anyone. |
| Presentation set (08-27/28): `talk-script-tupa-2026-08-28.md` (14 slides, ~16 min), `what-we-built-2026-08-27.md`, 4 deck prompts, `docs/product/diagrams/*.svg` (pipeline, agent-loop, validation-layer, gate-v1-v9) | CURRENT | The story as told to outsiders; `what-we-built` is the honest one-pager. |

---

## 13. Where the project stands and what remains

### 13.1 Honest position (roadmap §2 + ledger through 08-28)
Everything numbered **A1–A5, B1–B3, #34–#74, R-1–R-5c is shipped with commit hashes.** The system edits, validates, calibrates against real observations, generates networks from a map through a product UI, and writes MRWA-spined styled reports. The operating principles codified in §2.3 are the culture: an unmeasured blocker isn't a blocker; offline round-trip ≠ load proof; silent drops are the worst defect class; a check that can't run is NOT_CHECKABLE, never PASS; don't invent reality for a number.

### 13.2 The open queue (roadmap §5, live)
- **#75 ⛔** — the frontier: signal emission must satisfy the gate's own demands (V9 queue counters at all 4 stop lines; V5 head-placement/connector-membership signalising a neighbour node — live catch #5 from the first full walk, 08-27).
- **B4 ⬜** — the one-pipeline demo (generate→simulate→report) E2E on a small real Daejeon area; runbook exists; needs the user/COM lane.
- **#37 ⛔** — 표준노드링크 cross-check adapter; research done (GO-WITH-CAVEATS), awaiting a human download from ITS (its.go.kr, EPSG:5186).
- **C1 ⛔ / C3 ⛔** — 현시도 approach→phase mapping (UTIC open data covers Incheon/Daegu, not Daejeon → C1-b delegation stays the answer); 5 plans where YELLOW(RED) < the declared 3.0 s amber await client confirmation.
- **D1 ⛔ / D2 ⬜** — LMA/LMB drawings from client; 15-min profiled demand (unblocks on one measurement).
- **E** — sweep ranking UI (2.2), style-from-sample (2.6), onboarding (2.7).
- **F** — phase-plan editing (3.4), replay upgrade (3.5), deploy-as-service (3.6), P③ SQLite move (intentionally held), whole-app-behind-login (undecided).
- **G/H** — Phase 4; tech debt + 7 open chips (incl. "lint list misses genesis screens") + the 26-item UX backlog.

### 13.3 Waiting on humans (roadmap §6 — 5 rows)
(1) client LMA/LMB drawings (LM00398·LM00972·LM00973) → unblocks movement-level calibration; (2) client 현시도 as a table → Daejeon's 20 plans become real signal groups; (3) client confirmation of the 5 short-amber phases; (4) **PTV written licence confirmation** (node-locked/floating, server-side execution, COM) → sweep parallelism ceiling and deploy-as-service; (5) professor advisory question ④ (site-total GEH admissibility). The roadmap's closing line: waiting does not stop work — A/B/E/F proceed regardless.

---

## 14. Landmine catalog (verified the hard way)

**Vissim COM** (full detail in `docs/phase2/00-EXECUTION-GUIDE.md` §3):
- `SimSec` resets to 0 the instant a run ends, so `SimSec >= SimPeriod` never fires; further `RunSingleStep` calls silently start NEW runs (79 back-to-back observed). Don't remove the backwards-jump latch.
- `RunSingleStep` costs minutes per 60 sim-seconds; production uses `RunContinuous`.
- Evaluations are **not retroactive**, and reconfiguring them **deletes existing results** — configure before the run, never between compare runs.
- A short run writes a results .db with only the SIMULATIONRUN table — degrade gracefully.
- Conflict areas: no COM Add/Remove (geometry-derived); they default to **Passive** (vehicles drive through each other).
- Links have no lane-count and no speed-limit attributes (lanes baked into `AddLink` LaneWidths; speed via distributions across 3 object types; desired-speed decisions are permanent unless paired with a restoring decision).
- CC1 is a **reference to a Time Distribution object**, not a scalar. Driving behaviours are **global** — duplicate before editing or you change every link.
- Fixed-time signal programs live in an opaque blob; RBC/VAP/Econolite timings sit in external files COM cannot reach — check controller type first.
- Unmerged vehicles are deleted after 60 s (`DiffusTm`) and demand vanishes; excess demand queues invisibly as latent demand; gridlock reports LOS A. Intergreen violations are not blocked and *improve* reported delay — an optimizer will seek them.

**Codebase:**
- `clean_generated_answer()` strips JSON — strict-JSON paths must bypass it (pinned).
- Contract pins are co-edited with the source they pin, same commit; the runner strips types **without** typechecking (a RegExp-for-string bug once passed the contract while `tsc` failed) — always run both.
- CRLF: test helpers were hardened after a Windows rewrite emptied a haystack and made `doesNotMatch` pins pass vacuously; keep new helpers CRLF-tolerant.
- `assistant_router.py` has had duplicate helpers — grep before adding.
- `backend/tests/conftest.py` monkeypatches **five** by-name copies of `SESSIONS_ROOT`; miss one in a new module and tests leak into the developer's real runtime (the boot TTL sweep then deletes real agent sessions).
- `make_demo_data.py` default run destroys and rebuilds `data/Scenarios` (see §11.3).
- `.next` corruption trap (symptom seen as `--color-sky-50=#032042`) and stale dev-server chunks after source mutations — restart the frontend dev server before browser-verifying changes.
- PowerShell 5.1 has no `&&`; scout/agent summaries are not verbatim quotes (a recon once reported `straight`/`uturn` for source that says `through`/`u_turn`) — re-verify before pinning strings.
- `backend/requirements.txt:8` broken pin (§11.3). Two 540 MB mp4s at repo root are unignored (§4).
- Sidebar hazard: `connect()`'s default is the unsafe attach path — new COM call sites must pass `attach_to_running=False` explicitly (§5.5).

---

## 15. Hard rules (non-negotiable, from CLAUDE.md and the execution guide)

1. **Proprietary source.** Remote is ONLY the private `github.com/yunhalee2008/vissimDashboard`. Never public, never additional remotes, never collaborator grants — owner's call alone.
2. **Never commit secrets.** `ANTHROPIC_API_KEY` lives in gitignored `backend/.env`; never print it; don't weaken `.gitignore`.
3. **COM safety.** Automation always `attach_to_running=False`; never edit the active dataset's .inpx in place (clone into a job work dir); batch via `RunContinuous`; all COM through the job layer, never a request handler; one writer per Vissim instance.
4. **The refusal doctrine.** A refused edit with a clear reason beats a completed edit with wrong numbers. Don't ship a verb without its verification; don't present a proposal as a fact; NOT_CHECKABLE is never PASS.
5. **Verify before claiming done.** Backend pytest + frontend typecheck + contract suite + the milestone's named acceptance check; non-COM code runs on synthetic demo data.
6. **ko primary; en/ja through the existing plumbing.** Server text as `{ko,en,ja}`; client text through `t()`.

---

## 16. First-week reading order for the successor

1. `CLAUDE.md` — 5 minutes, the rules.
2. `docs/phase2/00-EXECUTION-GUIDE.md` — before touching anything agent/COM.
3. `docs/product/master-roadmap-v2-2026-08-11.md` §2 → §5 → §6 — the state and the queue.
4. `docs/product/tk-requirements-2026-07-31.md` — what is being built and for whom.
5. `docs/product/handoff-2026-08-26b.md` — the latest fine-grained work state.
6. `docs/phase2/DECISIONS.md` — skim the protocol at top, then read entries from 2026-08-20 onward; consult older entries whenever a design choice looks strange (it is probably recorded).
7. `docs/TOOL-CATALOG.md` + `docs/product/calibration-doctrine.md` — the doctrine surfaces.
8. `docs/product/what-we-built-2026-08-27.md` + `talk-script-tupa-2026-08-28.md` — the external story, useful for demos.
9. Then run the playbook in §10 and open the app: `overview → genesis → dashboard → calibration → report`.

*This report was compiled from a full-code survey and verification run on 2026-08-28 against commit `b81f695`. Corrections belong in DECISIONS.md like everything else.*
