# Phase 2 Roadmap — milestones, task sequence, acceptance criteria

Prerequisites for the executor: read [00-EXECUTION-GUIDE.md](00-EXECUTION-GUIDE.md) (rules +
landmines) and [01-ARCHITECTURE.md](01-ARCHITECTURE.md) (design) first. Each task below names
its files, its verification step, and its done-criteria. Work strictly in milestone order —
later milestones assume earlier interfaces exist. One commit per task where feasible; local
commits only, never push.

Working-time context: user is on-site daily until ~2026-09-05; the professor demo and the TR-C
evaluation both depend on M1–M4. M5 is the paper substrate and runs in parallel from M2 on.

---

## M0 — Foundations (no LLM involved) — est. 1–2 days

### M0.1 Trace module
`backend/services/agent/trace.py`: append-only JSONL writer. Every record:
`{ts, session_id, kind, payload}` with kinds `action_start | action_end | plan | preflight |
postwrite | postrun | refusal | warning | user_confirm | fallback_rung`. One file per agent
session under the job work dir.
**Accept:** unit test writes/reads a session trace; timestamps monotonic; file survives
process restart (append mode).

### M0.2 Shadow model + versioned artifacts
`backend/services/agent/state.py`: `AgentState` dataclass ⟨goal, artifacts, history:
list[StepRecord], context⟩ with JSON (de)serialization. `StepRecord(thought, action, params,
observation, kpi)`. Artifact manager: clone active dataset's .inpx into a session work dir,
`save_version()` → `edit_001.inpx`, `edit_002.inpx`…; refresh shadow model via existing
`inpx_parser.py` after each save; `rollback_to(version)`.
**Accept:** pytest — clone → mutate copy → save_version → rollback restores byte-identical
prior version; shadow model reflects the reparsed file. Runs against synthetic demo data (no
Vissim needed).

### M0.3 Constraint catalog skeleton
`backend/services/agent/constraints.py`: `Constraint` dataclass (id, applies_to, phase
preflight|postwrite|postrun, tier refuse|warn, check callable, message_ko/en/ja,
suggested_fix) + registry + `run_checks(phase, action, shadow_model, extra)` returning
`list[Finding]`. Seed with the first 8 constraints (the rest land in M3):
1. `object_not_found` (refuse, preflight) — target id absent from shadow model → difflib
   candidates in suggested_fix.
2. `volume_out_of_range` (refuse, preflight) — vehicle input volume <0 or >20000 veh/h.
3. `latent_demand_overflow` (warn, preflight) — requested volume exceeds rough capacity
   (lanes × 1900 veh/h) of the receiving link → "excess demand will queue invisibly as latent
   demand".
4. `signal_controller_type_unsupported` (refuse, preflight) — cycle/offset edit on a
   non-fixed-time controller (check via `signal_parser.py` controller type; RBC/VAP/Econolite
   are COM-unreachable).
5. `cycle_below_intergreen_sum` (refuse, preflight) — proposed cycle < sum of minimum
   green + intergreen times parsed for that controller.
6. `write_verification_failed` (refuse→abort, postwrite) — read-back ≠ written value.
7. `results_missing_kpi_tables` (warn, postrun) — result .db has only SIMULATIONRUN (run too
   short for evaluation intervals).
8. `vehicles_deleted_in_run` (warn, postrun) — deleted-vehicle count > 0 (DiffusTm
   disappearances) → "demand silently vanished; KPIs understate load".
**Accept:** pytest per constraint with synthetic shadow-model fixtures — each fires on its
trigger and stays silent otherwise; messages exist in ko/en/ja.

### M0.4 Tool registry core
`backend/services/agent/toolkit.py`: `@tool` decorator registering name, pydantic params
model, tier (read|write|run), provider group, docstring; `dispatch(name, raw_params)` →
validate (reject unknown/missing/mistyped — NOT key-filtering), execute, wrap return in
`{status, error_type, message, artifacts, suggested_fix, verification}`. `render_contract()`
→ the planner-prompt tool section generated from the registry (single source of truth).
**Accept:** pytest — bad params rejected with structured error; `render_contract()` output
contains every registered tool and no others (this test is the anti-drift guard; it must fail
if someone adds a tool without registry metadata).

---

## M1 — Read-only agent loop (safe end-to-end skeleton) — est. 2–3 days

### M1.1 Structured provider method
Extend `ClaudeProvider` with `generate_structured(messages, tools, schema)` using native
Anthropic tool-use (model `claude-sonnet-5`; enable prompt caching on system + tools blocks).
Give `LocalQwenProvider` the same method via strict-JSON prompting + `extract_json_object()`.
Duck-typed; assistant_router untouched except provider selection reuse.
**Accept:** integration test (Claude, mocked + one live smoke) and unit test (Qwen path with
canned output) both yield a validated action dict; malformed output exercises the fallback
ladder and logs `fallback_rung`.

### M1.2 Read tools
`tools_network.py`: `get_network_overview`, `resolve_object` (lexical top-k over shadow model
— port the network-RAG scoring: exact > substring > token-overlap, mtime-cached),
`get_vehicle_inputs`, `get_signal_controllers`. `tools_metrics.py`: `get_run_kpis`,
`compare_scenarios` (wrap `results_reader.py` / existing compare pipeline; return computed
summaries, never file paths to raw .db).
**Accept:** all work against the synthetic demo dataset with no Vissim/COM; `resolve_object`
returns ranked candidates for an ambiguous Korean name ("중앙로 교차로") and a structured
`object_not_found` with candidates for a miss.

### M1.3 Orchestrator + planner (read-only)
`orchestrator.py` + `planner.py`: the loop from architecture §3 with only read tools enabled.
System prompt (versioned file in `agent/prompts/v1_system.md`): role, thesis, lifecycle
rails, complexity-tier routing, parameter-sufficiency schema, refusal vocabulary, ko/en/ja
rules, `render_contract()` injection. State summary + last-K history per turn. K=6 iteration
cap. Planner output includes `summary` (≤80 chars) for UI narration.
**Accept:** scripted conversation against demo data: "이 네트워크에 뭐가 있어?" → overview
via tools; "107번 입력 교통량 알려줘" → resolve + read; a nonsense request → clarification
question, not hallucination. Full trace JSONL written. No write/run tool reachable (registry
tier gate test).

### M1.4 Router + frontend wiring (read-only)
New endpoint(s) in `backend/routers/assistant.py` dispatching to the orchestrator (thin);
FloatingAssistant streams step narration (`summary` per step) above the final answer.
**Accept:** browser verification against demo data — ask the two M1.3 questions in the UI,
see step narration + answer; zero console errors; contract tests still green.

---

## M2 — Write path with full verification (the product core) — est. 3–4 days

### M2.1 COM verb read-backs
Extend `vissim_com.py`: paired getters for every setter (`get_vehicle_input_volume`,
`get_signal_cycle`, `get_signal_offset`, `get_sim_duration`, `get_random_seed`) + `save_net_as
(path)`. Keep the existing latch/attach semantics untouched.
**Accept:** extend `tools/com_smoke_test.py`: for each verb — set on the example network →
get → assert equal → SaveNetAs → reparse via `inpx_parser` → assert the shadow model shows
the change. Run against live Vissim 2026 (this is the one milestone-critical live-COM task).

### M2.2 Guardrail engine
`guardrails.py`: `preflight(action, state)` / `postwrite(action, state, com_result)` /
`postrun(run_result, state)` orchestrating `constraints.py` checks; refusals and warnings as
structured tool returns (`error_type` = constraint id, candidates, `requires_user_input`
where applicable). Postwrite = read-back compare + shadow-model refresh + diff (nothing else
moved).
**Accept:** pytest with a mocked COM layer — a write that "lands" passes; a write where the
mock returns a different value produces `write_verification_failed` and the orchestrator
aborts the plan + rolls back to the last good version.

### M2.3 Write tools + approval integration
`tools_edit.py`: `set_vehicle_input_volume`, `set_signal_cycle`, `set_signal_offset`,
`set_sim_duration`, `set_random_seed`, `set_desired_speed_area` (existing XML-injection path,
versioned + verified via reparse). Tier gate: first write/run of a plan requires the existing
server-side propose→confirm→execute approval; the confirm card lists every proposed action +
preflight warnings + assumptions. Lane-closure requests → explicit refusal with explanation
(previously accepted-and-silently-dropped — that must never happen again).
**Accept:** end-to-end on live Vissim: "107번 vehicle input을 600으로 바꿔줘" → plan → confirm
card → approve → COM write → read-back verification evidence in the answer. Decline path
leaves the network untouched (verify via shadow model). Trace shows preflight → execute →
postwrite for each action.

### M2.4 Run gate + post-run lie detectors
`tools_run.py`: submit to the existing job queue (RunContinuous, multi-seed param,
`attach_to_running=False`, process-unique work dirs); delete/refuse stale result .dbs before
running; on completion run `postrun` checks (missing-KPI-tables, deleted-vehicles,
zero-throughput-good-LOS) and attach findings to the result.
**Accept:** live end-to-end: "그리고 5분 시뮬레이션 돌려줘" after M2.3's edit → job runs →
KPIs summarized with the short-run warning attached (5min run has no KPI tables → the warning
must fire, which proves the detector works). SIMULATIONRUN row is the completion check.

---

## M3 — Full constraint catalog + agentic hardening — est. 3–4 days

### M3.1 Encode the remaining catalog
Port the rest of the ~40-entry constraint catalog (the full catalog is committed at
[docs/reference/vissim-constraints.html](../reference/vissim-constraints.html) — work from
that file, not from memory) into `constraints.py`, each with tests. Priority order: (a) everything that fires on the M2 verb
set, (b) conflict-area / intergreen semantics checks, (c) post-run lie detectors (latent
demand residue, gridlock-LOS-A).
**Accept:** catalog coverage table in the module docstring (constraint id ↔ test id); pytest
green; every constraint has ko/en/ja messages + suggested_fix.

### M3.2 Complexity tiers live
Wire the three-tier routing: simple (single verified call, skip plan-preview when parameters
sufficient — but document assumptions in the answer), complex (CoT + mandatory preview),
agentic (extended template + strictest guardrail profile + mandatory post-run detectors +
explicit "the agent may not exploit reported-delay improvements that come from constraint
warnings" rule in the prompt).
**Accept:** three scripted requests, one per tier, produce the expected gating behavior;
trace records the classified tier; an agentic "reduce delay at node 1" run on demo data
never proposes a plan whose only improvement coexists with a fired lie-detector warning
(test with a rigged mock where the "best" config triggers `vehicles_deleted_in_run`).

### M3.3 Multi-turn session + clarification memory
Session persistence across messages (state reload by session id), accumulated warnings
surfaced in every subsequent answer, clarification answers merged into the parameter schema.
**Accept:** scripted 3-turn conversation: request with missing duration → clarification with
recommended default → user confirms → plan proceeds carrying the earlier context; warning
from turn 2 still visible in turn 3's report.

---

## M4 — Frontend polish for the demo — est. 2 days

- Plan-approval card: action list, per-action preflight status chips (pass/warn/refuse),
  assumptions block, Korean-first copy.
- Step progress streaming (plan → check → execute → verify), refusals rendered as explanatory
  blocks with the suggested alternative, verification evidence (read-back values) in the
  final answer.
- Post-run: link into the existing dashboard compare surfaces (scenario appears in the
  picker; curation bar can reference it).
**Accept:** browser walkthrough of the full M2.3+M2.4 scenario recorded as the demo script;
zero console errors; contract tests co-edited if pinned source moved; ja/en strings present.

---

## M5 — Evaluation harness (runs in parallel from M2) — see [04-EVAL-PLAN.md](04-EVAL-PLAN.md)

- M5.1 Benchmark task suite (YAML task specs + expected post-state assertions).
- M5.2 Runner: executes tasks against the agent, scores from traces + shadow-model diffs
  (success, silent-failure detection, refusal correctness, turns, latency, tokens).
- M5.3 Baselines: (a) the existing regex parser (`parse_prompt_simulation_command`) — the free
  baseline; (b) no-validation ablation (guardrails disabled flag — build the flag early, in
  M2.2, it is one boolean); (c) optional naive "LLM writes COM script" baseline.
- M5.4 Result tables + failure-injection protocol (adapted from ChatSUMO-Agent §4.4, with N
  and CIs they omitted).

---

## Sequencing notes for the executor

- M0 tasks are independent of each other and of Vissim — parallelize freely.
- M1 is fully testable on synthetic demo data; only M2.1/M2.3/M2.4 verification needs the
  live Vissim on this machine.
- The guardrails-disabled flag (M2.2) is a first-class requirement, not debug scaffolding —
  it IS the paper's ablation arm.
- If a task reveals that an interface defined here doesn't fit reality (e.g., a COM attribute
  read-back isn't possible for some verb), do not silently redesign: ship the closest
  verifiable subset, mark the gap in the task's commit body, and record it in
  `docs/phase2/DECISIONS.md` (create on first use).
- Defer everything in [ux-backlog.md](../ux-backlog.md) unless a Phase 2 task touches the same
  code anyway (then fix in passing and check it off).
