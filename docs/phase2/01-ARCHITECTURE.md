# Phase 2 Architecture — the Vissim edit agent

**Goal:** a closed-loop LLM agent that edits and runs Vissim simulations from natural language
(Korean primary), inside Prism, with a validation layer that manufactures the error signals
Vissim never gives. Design is adapted from ChatSUMO-Agent (TR-C 190, 2026, DOI
10.1016/j.trc.2026.105759) — see [03-RESEARCH-DIGEST.md](03-RESEARCH-DIGEST.md) — with the
central inversion: *they externalize simulator errors; we must synthesize them*.

## 0. The one-sentence thesis (also the paper's thesis)

> Open simulators (SUMO) emit rich error signals an agent can close the loop on; closed,
> silent-failure simulators (Vissim) do not — so a constraint-encoded validation layer must act
> as the substitute error channel, and that layer, not the LLM plumbing, determines whether the
> agent's edits can be trusted.

Every design decision below serves that thesis. When in doubt, choose the option that
strengthens the validation layer's role.

## 1. Component map

```
frontend/components/FloatingAssistant.tsx        (existing propose→confirm→execute UI, extended)
        │  POST /api/assistant/*  (existing router seam)
        ▼
backend/routers/assistant.py                      (thin dispatch — no logic added here)
        ▼
backend/services/agent/                           (ALL new Phase 2 code lives here)
 ├─ orchestrator.py      Agent loop: state → plan → validate → execute → observe → repeat
 ├─ state.py             AgentState ⟨goal, artifacts, history, context⟩ + persistence
 ├─ planner.py           LLM planner: one JSON action per step + rule-based fallback
 ├─ toolkit.py           Tool registry: in-process dict of name→typed function (NOT MCP
 │                       subprocesses — COM wants one STA thread owning Vissim, and the
 │                       reference repo's 5-subprocess MCP setup bought only name routing).
 │                       Pydantic param models, ACTUALLY ENFORCED; the planner prompt's tool
 │                       contract is GENERATED from this registry — single source of truth
 │                       (the reference repo's #1 bug class was hand-maintained prompt/schema/
 │                       dispatch drift: phantom tools, silently stripped params).
 ├─ tools_network.py     Read/ground tools (query objects, resolve names→ids via lexical
 │                       top-k retrieval over the shadow model — the network-RAG pattern)
 ├─ tools_edit.py        Write tools (vehicle input, signal, sim params, …)
 ├─ tools_run.py         The single run gate (job-queue submission only)
 ├─ tools_metrics.py     Post-run KPI summaries (wraps results_reader.py)
 ├─ guardrails.py        THE PRODUCT: pre-flight constraint checks + post-write verification
 ├─ constraints.py       Machine-readable constraint catalog (~40 entries, see §5)
 └─ trace.py             Append-only action-observation log (JSONL) with action_start/
                         action_end timestamps — the eval substrate AND paper-ready
                         latency/time-to-insight numbers from day one
        │
        ▼
backend/services/vissim_com.py                    (existing VissimController, extended verbs)
backend/services/simulation_runs.py               (existing job store — all runs go through it)
```

Existing seams reused unchanged: provider selection (`_default_provider`), the server-side
propose→confirm→execute approval flow, `extract_json_object()` for strict-JSON parsing (bypasses
`clean_generated_answer`, which strips JSON), `results_reader.py`, `validation.py` (GEH).

## 2. Agent state (adapted from ChatSUMO-Agent Eq. 1)

`AgentState = ⟨goal, artifacts, history, context⟩`, persisted as JSON per agent session under the
job-scoped work directory.

- **goal** — the user's request, normalized (language-detected, with the resolved dataset id).
- **artifacts** — SUMO state lives in files; Vissim state lives in a live COM object graph, so we
  make it file-like: the cloned `.inpx` work copy is versioned (`edit_001.inpx`, `edit_002.inpx`…
  saved via COM SaveNetAs after each accepted mutation batch), plus the parsed shadow model
  (`inpx_parser.py` output) refreshed after each save. Artifacts also include result `.db` paths
  after runs. This restores ChatSUMO's inspect/replay/audit property that raw COM lacks.
- **history** — ordered step records: `{action, params, tier, preflight_result, com_result,
  postwrite_verification, kpis?, timestamp}`. Full trace to `trace.py` JSONL; a *summarized*
  window (last N steps + all warnings) goes into the planner prompt.
- **context** — dataset id, scenario id, COM session status, pending-approval flag, cached
  latest KPIs, accumulated constraint warnings (warnings persist across steps — a warning issued
  at step 2 must still be attached to the final report at step 9).

## 3. The loop (orchestrator.py)

```
user message
  → planner: state summary + tool schemas → ONE JSON action {tool, params, rationale}
      · invalid JSON / unknown tool / bad params → 1 retry with error appended → rule-based
        fallback (safe next action from artifact-state rules, or "ask user")
  → tier gate: read tools auto-execute; write/run tools require the EXISTING server-side
    propose→confirm→execute approval before first execution of the plan (one approval covers
    the proposed plan, not a blanket grant)
  → guardrails.preflight(action): constraint catalog checks against the shadow model
      · REFUSE → structured refusal into history; planner replans (may ask user)
      · WARN   → attach warning, proceed
      · PASS   → proceed
  → execute via toolkit (COM call on the work clone, inside the job worker — never in the
    request handler)
  → guardrails.postwrite(action): read back what was written; diff shadow model; compare
      · mismatch → synthesized error observation {status:"failure", error_type, message,
        suggested_fix} — THIS is the manufactured error signal
  → observe: structured tool return appended to history → loop until goal satisfied,
    K=6 recovery iterations exhausted, or user input needed
  → final answer: what changed, verification evidence, accumulated warnings, next-step offer
```

Tool return schema (ChatSUMO Eq. 3, extended):
`{status, error_type, message, artifacts, suggested_fix, verification}` where `error_type` is an
id from our constraint catalog (their coarse taxonomy, upgraded to the catalog) and
`verification` records the post-write read-back evidence.

## 4. Tool catalog v1 (provider-grouped; single run gate)

Signatures are JSON-schema'd in `toolkit.py`; params validated + extraneous keys filtered before
dispatch (ChatSUMO pattern). **v1 ships only verbs with a working read-back pair.**

| Provider | Tool | Maps to | Tier |
|---|---|---|---|
| Inspect | `get_network_overview` | shadow model summary (links, inputs, signals, nodes) | read |
| Inspect | `resolve_object` | NL name/desc → object id via shadow model; ambiguity → candidates list back to planner | read |
| Inspect | `get_vehicle_inputs` / `get_signal_controllers` | COM/shadow reads | read |
| Edit | `set_vehicle_input_volume` | `VissimController.set_vehicle_input` | write |
| Edit | `set_signal_cycle` / `set_signal_offset` | existing verbs (guardrail: fixed-time controllers only — check controller type via `signal_parser.py` first; RBC/VAP are COM-unreachable) | write |
| Edit | `set_sim_duration` / `set_random_seed` | existing verbs | write |
| Edit | `set_desired_speed_area` | existing reducedSpeedArea XML-injection path (documented as XML-not-COM; still versioned + verified via shadow-model reparse) | write |
| Run | `run_simulation` | job-queue submission → RunContinuous, multi-seed capable. **Deletes/refuses stale result .dbs before running** so a silently failed run can never serve the previous run's numbers (the single most Vissim-relevant guardrail in the reference repo) | run |
| Metrics | `get_run_kpis` | `results_reader.py` summaries (never raw .db) | read |
| Metrics | `compare_scenarios` | existing compare pipeline | read |

Deferred to v2+ (no safe read-back or known silent-failure trap today): link add/remove
(conflict-area config destroyed by geometry edits, no COM Add/Remove for conflict areas),
lane closures (previously accepted-and-silently-dropped — must be *refused* in v1, not dropped),
driving-behaviour params (global objects; require duplicate-before-edit machinery), speed limits
via distributions (3-object indirection). The planner knows these as named refusals with
explanations, not as absent tools.

## 5. Guardrails (`guardrails.py` + `constraints.py`)

`constraints.py` encodes the catalog (source: the full ~40-entry catalog committed at
[docs/reference/vissim-constraints.html](../reference/vissim-constraints.html) — build against
that file, not from memory) as data:

```python
Constraint(
  id="latent_demand_overflow",
  applies_to=["set_vehicle_input_volume"],
  phase="preflight",              # preflight | postwrite | postrun
  tier="warn",                    # refuse | warn
  check=...,                      # callable(action, shadow_model) -> Finding | None
  message_ko=..., message_en=..., message_ja=...,
  suggested_fix=...,
)
```

Three check phases:
1. **Pre-flight** — before the COM call: range/type checks, object existence (grounded against
   the shadow model), controller-type compatibility, intergreen arithmetic on proposed cycle
   changes, demand-vs-capacity screens, "edit destroys conflict-area config" screens.
2. **Post-write** — after the COM call: read back the attribute; reparse/refresh shadow model;
   assert the write landed and nothing else moved.
3. **Post-run** — after results land: silent-lie detectors on the output — deleted-vehicle count
   (`DiffusTm` disappearances), latent-demand residue, zero-throughput movements reporting good
   LOS, results .db missing KPI tables (short-run artifact). Findings attach to the run report.

Every finding carries the constraint id → the trace becomes machine-scoreable for the paper's
evaluation ("which constraint fired, how often, would the naive baseline have shipped the lie").

## 6. Planner (`planner.py`)

- Provider seam: extend the duck-typed provider with a `generate_structured(messages, tools,
  schema)` method — Claude native tool-use when `ClaudeProvider` is active; for local Qwen,
  strict-JSON prompting + `extract_json_object()` (curation path pattern). Planner code is
  provider-agnostic. Enable Anthropic prompt caching on the static system spec + tool
  definitions (AgentSUMO pattern; free latency/cost win on multi-turn sessions).
- **Task-complexity routing (AgentSUMO's three tiers):** every request is classified
  *simple* (single verified tool call — "set input 107 to 600"), *complex* (multi-step
  compare/apply plan — "close a lane on Teheran-ro and compare"), or *agentic* (open-ended
  optimization — "reduce delay at this intersection"), selecting reasoning depth and whether a
  plan-preview confirmation is mandatory. Note for agentic tier: an optimizer WILL exploit
  Vissim's silent failures (conflicting greens improve reported delay) — agentic tasks run with
  the strictest guardrail profile, and post-run lie detectors are non-optional there.
- **Parameter-sufficiency gate (AgentSUMO's clarify-before-execute):** each request maps onto a
  scenario schema — target objects, new values, run design (duration/seeds/scenarios), success
  metrics. Missing critical fields pause the loop and produce structured clarification questions
  *with recommended defaults and explicit assumptions* (e.g. "duration not given — propose 3600s
  as in the base scenario"). This merges naturally with our existing approval flow: the
  plan-preview confirm card IS the clarify-before-execute step, showing inputs, steps, expected
  outputs, preflight warnings, and assumptions.
- Prompt = system spec (role, domain rules, tool contracts, ordering constraints, refusal
  vocabulary, ko/en/ja response rules) + serialized state block (AgentSUMO injects state as a
  context block each turn — same here) + last-K history + accumulated warnings. Prompts live in
  `backend/services/agent/prompts/` as versioned files (paper needs to cite them; the eval
  harness pins prompt versions).
- Lifecycle rails (ChatSUMO's artifact-presence rules, translated to COM): no dataset → refuse
  with guidance; dataset but no COM session → connect+load first; edits pending → runs deferred
  until edits verified; run in progress → no concurrent edits (single-writer rule).
- **Layered fallback ladder** (proven in the reference repo to make even a weak local model
  workable — matters for our Qwen path): native tool-use / strict JSON → `extract_json_object()`
  regex rescue → auto-correct an invalid action from lifecycle state → rule-based planner picks
  the conservative next action or surfaces a clarification question. Guarantees the loop never
  wedges. Log which rung fired (the repo's regex pre-planner silently shadowed the LLM — never
  let a fast-path fire unlogged).
- Planner output carries a `summary` field (≤80 chars) used verbatim as the step narration in
  the FloatingAssistant progress UI — one field serving both trace and UX.

## 7. Concurrency + safety model

- All COM work inside the existing job worker (one Vissim instance, `attach_to_running=False`,
  process-unique work dirs — the `com_smoke_test.py` pattern). The orchestrator's execute step
  *submits* to the queue and awaits; it never touches COM in-process.
- Approval: the existing server-side propose→confirm→execute flow gates the first write/run of
  each plan. The confirm payload shows the full proposed action list + any preflight warnings,
  so the user approves the *actual* plan, not a paraphrase.
- Evaluation config is never reconfigured between compare runs (reconfiguring deletes results).
  Job setup configures evaluation once, before the first run.

## 8. What the frontend gains (kept minimal in Phase 2)

- FloatingAssistant renders the plan-approval card (action list + warnings), streaming step
  progress (plan → check → execute → verify per step), and the final verification-evidence
  answer. Constraint warnings render as first-class amber blocks, refusals as explanatory
  blocks with the suggested alternative.
- The dashboard's existing curation/insight surfaces pick up post-run results through the
  existing dataset/scenario plumbing — no new chart work in Phase 2 (Phase 3 owns preview UX).
