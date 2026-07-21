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
| 2. Edit agent v1 | 8/4–8/15 | Tool schemas over 5 COM verbs + speed edit (XML injection) + run/results/compare; conversation history; server-side propose→clarify→approve→execute (AgentSUMO-style) with diff-card UI; `services/guardrails.py` (constraint catalog in code — NOT named "validation", that means GEH calibration here); job queue + registry locking + multi-seed runs |
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
