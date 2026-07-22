# Phase 2 Execution Guide — read this before touching any code

**Audience:** the AI model (or human) executing Phase 2 tasks. This guide exists so that any
competent executor — Sonnet, Opus, or a new contributor — produces the same quality of work a
stronger model would, by following the encoded judgment below instead of re-deriving it.

Read order for a fresh session: this file → [01-ARCHITECTURE.md](01-ARCHITECTURE.md) →
[02-ROADMAP.md](02-ROADMAP.md) (find your milestone) → [03-RESEARCH-DIGEST.md](03-RESEARCH-DIGEST.md)
(skim; deep-read the section your task cites) → [04-EVAL-PLAN.md](04-EVAL-PLAN.md) (only if your
task touches the evaluation harness).

---

## 1. Non-negotiable rules

1. **Local commits only. NEVER push, never add a remote.** The repo contains Dochak's proprietary
   source; pushing requires explicit Dochak team approval that has not been given.
2. **Never commit secrets.** The Anthropic key lives in git-ignored `backend/.env`
   (`ANTHROPIC_API_KEY`). `.gitignore` blocks `.env*`; do not weaken it. Never print the key in
   logs, test output, or error messages.
3. **Never let automation attach to a human's Vissim GUI session.** Always
   `VissimController.connect(attach_to_running=False)` in agent/job code. `GetActiveObject`
   attaches to an *arbitrary* registered instance, including the user's open GUI — loading a
   network into it destroys their unsaved work.
4. **Never edit the active dataset's .inpx in place.** All agent edits happen on a cloned network
   file in a job-scoped work directory (the pattern `scenario_project.py` and
   `tools/com_smoke_test.py` already use). The user's uploaded dataset is read-only input.
5. **The validation layer is the product.** When a task trades off "agent can do more" against
   "agent can silently do wrong", wrong loses. A refused edit with a clear explanation is a
   feature; a completed edit with wrong numbers is the worst possible outcome. See §4.
6. **One writer per Vissim instance.** Vissim COM is an out-of-process EXE server,
   single-threaded per instance, ~4 instances max per machine, needs a logged-on session (cannot
   run as a Windows service). All COM work goes through the job queue — never call COM directly
   from a request handler.

## 2. Repo primer (what exists — do not rebuild these)

Stack: FastAPI backend on **port 8001** (README's 8000 is stale) + Next.js 16 frontend on 3000.
File-based storage, no DB. Frontend dev server via `.claude/launch.json` (`& npm.cmd run dev` —
plain `npm` is blocked by PowerShell execution policy).

| Surface | File | What it does |
|---|---|---|
| COM wrapper | `backend/services/vissim_com.py` | `VissimController`: connect/load_network/set_vehicle_input/set_sim_duration/set_random_seed/set_signal_cycle/set_signal_offset/run_single_step/is_sim_finished/available_*_nos/disconnect. Contains the SimSec-reset latch (see §3.1). |
| COM status/snapshot | `backend/services/vissim_tool.py` | capabilities payload, pywin32 check, live-object snapshot sampling. |
| Assistant monolith | `backend/services/assistant_router.py` (~2960 lines) | intent classification (regex), the v0 regex prompt→SimParams parser (`parse_prompt_simulation_command`), RAG doc search, answer builders (ko/en/ja), provider selection (`_default_provider`). Phase 2 *replaces the regex parser with a real agent* but keeps the router as the entry seam. |
| LLM providers | `backend/services/claude_provider.py`, `qwen_worker.py` | duck-typed `status()` / `generate(prompt, on_token, think)`. `ClaudeProvider` used when `ANTHROPIC_API_KEY` is set; `ASSISTANT_PROVIDER=qwen` forces local. **Note: `generate()` is text-only — Phase 2 must add a `messages`+`tools` method (M1).** |
| Curation | `backend/services/dashboard_curator.py`, `chart_catalog.py` | strict-JSON dashboard curation; `extract_json_object()` (brace-depth parser) is reusable for any strict-JSON LLM output. |
| Results | `backend/services/results_reader.py` (1547 lines) | reads Vissim result SQLite DBs; exact schema that `tools/make_demo_data.py` replicates. |
| Validation math | `backend/services/validation.py` | GEH etc. — reuse, don't duplicate (there are already 3 GEH pipelines; do not add a 4th). |
| Jobs | `backend/services/simulation_runs.py`, `backend/routers/simulations.py` | file-based job store + endpoints; the propose→confirm→execute approval flow lands here. |
| Approval flow | `backend/routers/assistant.py` + `frontend/components/FloatingAssistant.tsx` | server-side propose→confirm→execute (`allow_tool_execution=false` first pass; Run/Cancel via `confirmPromptExecution`/`declinePromptExecution`). Phase 2 extends this, never bypasses it. |
| Smoke tools | `backend/tools/com_smoke_test.py`, `smoke_parse_examples.py`, `make_demo_data.py` | proven COM run pattern; 25/25 example-network parse; synthetic demo dataset generator. |

Vissim 2026 is installed at `C:\Program Files\PTV Vision\PTV Vissim 2026` (ProgID
`VISSIM.Vissim.2026`); its `Examples Demo/` networks are the test corpus.

## 3. Landmines (verified empirically on this machine — believe them)

### 3.1 Vissim COM behavior
- **SimSec resets to 0 the moment a run ends**, so `SimSec >= SimPeriod` NEVER fires; each further
  `RunSingleStep` silently starts a NEW run (observed 79 back-to-back runs). The backwards-jump
  latch in `vissim_com.py` (`_max_sim_sec`/`_sim_run_ended`) handles this — don't remove it.
- **`RunSingleStep` over COM costs minutes for 60 sim-seconds** (1–2 COM round-trips per step).
  Production runs use `RunContinuous` (~7.3× realtime observed). Per-step control is only for
  future live-streaming features, never for batch runs.
- **A short run writes a results .db containing ONLY the SIMULATIONRUN table** (evaluation
  intervals never elapsed) — no KPI tables at all. Treat SIMULATIONRUN
  (RANDSEED/SIMPERIOD/SIMEND/VISSIMVER) as the run-completed artifact; degrade gracefully when
  KPI tables are missing.
- **Evaluations are not retroactive, and reconfiguring them deletes existing results.** Configure
  evaluation before the run; never reconfigure between compare runs.
- **Vissim needs a licence + logged-on session**; `license_guard.py` exists for a reason.

### 3.2 Vissim model semantics (why naive edits produce lies)
- Vissim almost never refuses a bad edit — it runs to completion and reports confident wrong
  numbers. Examples: conflict areas default to Passive right-of-way (vehicles drive through each
  other); intergreen violations aren't blocked and conflicting greens *improve* reported delay —
  an optimizing agent will actively seek that "solution"; vehicles that can't merge are deleted
  after 60s (`DiffusTm`) and demand silently vanishes; excess demand queues invisibly as latent
  demand; gridlocked zero-throughput movements report LOS A.
- Conflict areas have **no Add/Remove via COM** (derived from geometry).
- Links have **no lane-count and no speed-limit attribute** (lane count baked into AddLink's
  LaneWidths; speed via distributions through 3 object types; desired-speed decisions are
  permanent unless paired with a restoring decision).
- CC1 is a **reference to a Time Distribution object**, not a scalar.
- Fixed-time signal programs live in an opaque blob; RBC/VAP/Econolite timings are in external
  files COM cannot reach. Our `set_signal_cycle`/`set_signal_offset` work only for the signal
  types `signal_parser.py` can read — the guardrail layer must check controller type first.
- Driving behaviours are **global objects** — duplicate before editing or you change every link.

### 3.3 Codebase traps
- `clean_generated_answer()` in `assistant_router.py` **strips JSON** — any strict-JSON LLM path
  must bypass it (the curation path already does; copy that pattern).
- `frontend/tests/dashboard-contract.test.ts` **regex-pins raw source text** of `page.tsx` — when
  you refactor pinned code, co-edit the test in the same commit. One pre-existing failure
  (base-model layout drift) is known and unrelated; do not "fix" it in passing.
- `assistant_router.py` had duplicated function definitions before; grep for an existing
  implementation before adding any helper there.
- react-hooks lint: `set-state-in-effect` — defer with `Promise.resolve().then(...)` (pattern
  already in `page.tsx`).
- Windows: paths with spaces everywhere; PowerShell 5.1 has no `&&`; prefer the repo's existing
  script patterns.

## 4. The guardrail doctrine (how to think about every agent capability)

Every action the agent can take gets classified along two axes before implementation:

- **Reversibility**: does it mutate the cloned work file only (fine), or shared state
  (evaluation config, global driving behaviours, the active dataset) — which needs a stronger
  gate or a refusal?
- **Verifiability**: can we *prove post-hoc* that the edit took effect and didn't create a lie?
  Every setter must have a paired reader used for post-write verification
  (`set_vehicle_input` → read back the volume; run → check SIMULATIONRUN row). An action with no
  possible verification read-back does not ship.

Three-tier response to a requested edit:
1. **Allowed** — in the verb catalog, parameters in range: execute on the clone, verify, report.
2. **Allowed-with-warning** — legal but suspicious (e.g. demand increase beyond capacity →
   latent-demand lie): execute AND attach the specific constraint warning to the result.
3. **Refused** — out of catalog, unverifiable, or a known silent-failure trigger: return the
   refusal *with the reason and the nearest allowed alternative*. Refusals are first-class
   results, logged and evaluated like successes.

## 5. Working conventions

- **Verification is part of every task.** Backend: run the relevant smoke tool or pytest; agent
  loop changes: run the eval harness subset named in the task. Frontend: typecheck + contract
  tests + live check in the browser preview. A task claiming completion without its named
  verification step is not complete.
- **COM tasks need the real Vissim** on this machine; everything else must work against the
  synthetic demo dataset (`python backend/tools/make_demo_data.py`). Keep it that way — never
  make a non-COM code path require live Vissim.
- **Commit style**: imperative subject, body explains why, `Co-Authored-By: Claude` trailer as in
  history. One milestone-task per commit where feasible.
- **i18n**: user-facing strings go through the existing `t()`/language plumbing (ko primary,
  en/ja). Answer builders come in ko/en/ja triplets — extend all three or route through a
  language-neutral path.
- **Don't grow the monolith**: new Phase 2 code goes in new modules (`backend/services/agent/…`),
  not into `assistant_router.py`. The router only gains thin dispatch calls.
- **Ask-vs-act**: if a task's acceptance criteria conflict with something you discover in the
  code, stop and surface it rather than silently reinterpreting the task.

## 6. Definition of done (every Phase 2 task)

1. Code + tests written; named verification step run and passing (paste the evidence).
2. No new lint/typecheck errors; contract tests co-edited if pinned source moved.
3. Guardrail doctrine applied to any new agent capability (tier classification stated in the PR
   description/commit body).
4. Docs touched if behavior changed (`docs/phase2/` for design deltas, `docs/ux-backlog.md` if a
   deferred item was addressed).
5. Local commit made; **no push**.
