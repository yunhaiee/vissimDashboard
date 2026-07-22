# Research Digest — the two prior systems and what we take from each

Self-contained: an executor does not need the source PDFs. Full digests were produced from the
actual papers + repo on 2026-07-22 (Vissim/Prism context). Sources:

- **[A] ChatSUMO-Agent** — Li, Ma, Azfar, Ke (Rensselaer Polytechnic Institute). *ChatSUMO
  Agent: An LLM-Based Agent for Conversational Traffic Simulation in SUMO.* Transportation
  Research Part C 190 (2026) 105759, DOI 10.1016/j.trc.2026.105759. **Published TR-C** —
  our closest template and the paper the professor referenced. Code: MIT-licensed at
  github.com/ChrisLi1221/ChatSUMO-Agent (~4.5k LOC Python, Streamlit + FastMCP; local clone
  studied file-by-file).
- **[B] AgentSUMO** — Jeong, Chang, Yoon (KAIST GSDS). *AgentSUMO: An Agentic Framework for
  Interactive Simulation Scenario Generation in SUMO via LLMs.* arXiv:2511.06804 (Nov 2025),
  under review at an Elsevier journal (TR-C-style formatting). Uses Claude Sonnet 4.5 + real
  Anthropic MCP. Extends their own GeoSim '25 workshop paper. No public code.

## 1. What each system is, in one paragraph

**[A]** frames simulation engineering as a **closed-loop decision process**: an LLM planner
emits exactly ONE JSON action per step `{action, params, reason, decision.summary}` from a
fixed vocabulary (BUILD_NET / BUILD_ROUTES / RUN / ANALYZE / MODIFY / STOP); tools execute and
return structured observations; state ⟨goal, artifacts, history, context⟩ feeds the next plan.
Failure recovery is just more loop iterations (bounded at K), driven by structured error
payloads. Evaluated with a failure-injection protocol (3 network scales × 3 failure modes,
recovery success 0.78–0.95) and a time-to-insight comparison (~80% human-effort reduction).

**[B]** puts its innovation **before execution**: an Interactive Planning Protocol embedded in
the system prompt. Every request is complexity-classified (*simple* → direct tool call /
*complex* → CoT template / *agentic* → extended multi-phase CoT), mapped onto a parameter-
sufficiency schema (spatial scope, demand, intervention, experimental design, metrics), and any
missing critical parameter pauses execution for structured clarification questions *with
recommended defaults* ("MSG capacity ≈ 20,000 seats; realistic egress 60/40 — confirm?"). A
plan preview (inputs, steps, expected outputs, validation checks) requires explicit user
confirmation before running. State is a session dictionary serialized into the prompt each
turn; static prompt sections are cached. Evaluation is case-study-only on real OD data
(Gangnam, Manhattan).

## 2. The comparison that defines our position

| Dimension | [A] ChatSUMO-Agent | [B] AgentSUMO | **Prism Phase 2** |
|---|---|---|---|
| Loop | Closed (replan from observations) | Open after confirm (no described recovery) | Closed |
| Pre-execution gating | Lifecycle rails only | **Complexity tiers + parameter schema + clarify-before-execute** | Both + constraint catalog pre-flight |
| Error signal source | SUMO CLI fails loudly → tool wraps stderr | SUMO CLI (implicitly) | **Vissim is silent → validation layer manufactures the signal** |
| Post-edit verification | None (worst gap) | None | **Read-back after every write; shadow-model diff; post-run lie detectors** |
| Tool transport | FastMCP stdio subprocesses ×5 | Real Anthropic MCP servers | In-process registry (COM = single STA thread) |
| Grounding NL→object | Lexical top-k RAG over net.xml edges | GeoPy + OD map-matching | Lexical top-k over .inpx shadow model |
| Eval rigor | 1 quantitative table, N unstated, no ablations | Case studies only, no agent metrics | Benchmark suite + N + CIs + **no-validation ablation** |
| LLM disclosure | Never named (!) | Claude Sonnet 4.5 | Named + versioned prompts |

**Our novelty dichotomy (the paper's spine):** open simulators emit the error signals that make
closed-loop agents work; closed, silent-failure simulators do not — so a constraint-encoded
validation layer must act as the substitute error channel. Neither [A] nor [B] verifies that an
edit actually took effect. In Vissim, not verifying means confidently reporting numbers from a
simulation that did something else entirely.

## 3. Patterns we copy (with source)

1. **State tuple** ⟨goal, artifacts, history, context⟩ + `StepRecord(thought, action, params,
   observation, kpi)` serialized into every planner call. [A, Eq. 1 + repo `agent/state.py`]
2. **Tool return schema** `{status, error_type, message, artifacts, suggested_fix}`; error_type
   from a taxonomy — ours is the constraint catalog. [A, Eq. 3]
3. **Structured self-healing errors**: unknown object → difflib `candidates` list +
   `requires_user_input: true` + prompt rule "pick a candidate, retry once". The best code in
   [A]'s repo (`provider_sumo_modifier.py` remove_edge). Every Prism constraint violation
   returns this shape.
4. **One-JSON-action-per-step + fixed action vocabulary + params contract in the system
   prompt** — works without native function calling (matters for local Qwen). [A repo,
   `llm_planner.py` — its ~80-line system prompt is the entire prompt surface and is quoted in
   the agent-a study; the lesson is that structure beats prompt sophistication.]
5. **Layered fallback ladder** (JSON parse → regex extract → auto-correct action → rule-based
   planner). [A repo]
6. **Lifecycle/artifact-presence rails** (can't run before network+demand; modification defers
   execution). [A]
7. **Complexity-tier routing** (simple/complex/agentic → reasoning depth + confirmation
   requirements). [B]
8. **Parameter-sufficiency schema + clarify-before-execute with recommended defaults and
   explicit assumptions**, plan preview = our existing propose→confirm→execute card. [B]
9. **State-block injection per turn + prompt caching** of static sections. [B]
10. **Network-RAG grounding**: lexical top-k retrieval of candidate objects (id, name, geometry
    hints) into the prompt, mtime-cached. [A repo `network_rag.py`]
11. **Stale-output deletion before every run** so a failed run can't serve the previous run's
    results. [A repo runner — the most Vissim-relevant guardrail there.]
12. **TTI event logging** (`action_start`/`action_end` JSONL) from day one. [A repo]
13. **Paired-scenario methodology** — baseline vs. intervention under identical configs as the
    canonical experiment the agent constructs. [B]
14. **Failure-injection evaluation protocol** + metrics (recovery success rate, iterations,
    time-to-recovery). [A §4.4] We add N and CIs, which [A] omitted.

## 4. Anti-patterns we explicitly avoid (with source)

1. **Prompt/schema/dispatch drift** — [A]'s repo maintains the tool contract in three places by
   hand; result: a tool in the prompt that no provider implements (`edge_set_speed`), calls
   missing required args, params silently stripped by key-whitelisting. **Rule: the prompt
   contract is generated from the tool registry; params validated by enforced pydantic models.**
2. **No post-edit verification** — both systems. Our read-back layer is non-negotiable.
3. **In-place mutation with ignored return codes** — [A]'s `remove_edge` netconverts the net
   file in place, `subprocess.call` rc ignored, no backup. We version every mutation
   (`edit_NNN.inpx`) and check everything.
4. **Hidden magic stop conditions** — [A] auto-stops when KPI delta < 0.5, invisible to the
   user. Convergence criteria must be explicit and surfaced.
5. **Unlogged fast-paths** — [A]'s demo regex planner silently bypasses the LLM. Log which
   planner rung fired, always.
6. **Undisclosed LLM/eval params** — [A] never names its model; no N, no variance. We name
   model, temperature, prompt version, N, and CIs.
7. **Agentic-tier blind spot** — an optimizing agent told to "reduce delay" will exploit
   Vissim's silent failures (conflicting greens *improve* reported delay; deleted vehicles
   *reduce* reported demand). Neither paper faces this because SUMO errors loudly. Agentic
   tasks get the strictest guardrail profile + mandatory post-run lie detectors.

## 5. Paper-writing intel (TR-C template, from [A] published + [B] submitted)

- **Structure that got [A] accepted (~14 typeset pages):** Intro (pain point → gap dichotomy →
  numbered contributions) → Related Work (3 subsections, each ending "in contrast, our
  work…") → Methodology (framework figure; formal state tuple Eq.; planner logic; **the tool
  table** — the paper's skeleton; failure-recovery formalism + return schema Eq.; implementation
  + reproducibility paragraph) → Experiments (3 questions; walkthrough → grounded task with
  automatic success criteria → ONE quantitative table → formula-defined efficiency metric;
  **Limitations as subsection 4.6 inside Experiments**) → Conclusion + insights.
- **The bar is beatable:** [A] passed TR-C with one 9-cell table, no N, no ablations, no named
  LLM. Our eval plan ([04-EVAL-PLAN.md](04-EVAL-PLAN.md)) clears that bar with a benchmark
  suite, ablation of the validation layer, and full disclosure.
- **Framing moves to copy:** a single repeated dichotomy (theirs: open-loop vs closed-loop;
  ours: loud vs silent simulators / validation as substitute error channel); metrics defined
  with formulas even when simple; public-code link in the abstract (ours must stay private —
  address via "available on request" like [A]'s data); qualitative claims pre-mapped to
  measurable outputs.
- **[B]'s credibility lever:** real corridors + real OD data (Gangnam!, NYC TLC). Dochak/KAIST
  connections make a real Korean-intersection case study very achievable — and [B] being a
  KAIST GSDS paper means our related-work section must position against it carefully and
  charitably.
- **Timeline evidence:** [A] went received→accepted in ~4.5 months; [B] seeded with a workshop
  paper then extended. With the ~2026-09-05 internship end, target: working system + benchmark
  by late August, paper drafting into the fall semester, possibly a workshop paper first.
