# Phase 2 Evaluation Plan — the TR-C paper substrate

Everything the agent does must leave evidence the paper can score. The trace module
(M0.1) is the substrate; this doc defines what we measure, against what, and how it becomes
the Experiments section. Template: ChatSUMO-Agent (TR-C 190, 2026) — see
[03-RESEARCH-DIGEST.md](03-RESEARCH-DIGEST.md) §5 — whose bar we deliberately clear on rigor.

## 1. Research questions (the Experiments section's spine)

- **RQ1 (validity):** Does the agent perform requested Vissim edits correctly — and *provably*
  (read-back verified), not just plausibly?
- **RQ2 (the thesis):** How often would unvalidated LLM edits produce silently wrong
  simulations, and what fraction does the validation layer catch? (The no-validation ablation
  makes "the validation layer is the product" a measured claim.)
- **RQ3 (interaction):** Does clarify-before-execute + complexity routing reduce user effort
  vs. the imperative baseline (time-to-insight, clarification turns)?

## 2. Benchmark task suite (M5.1)

YAML task specs in `backend/eval/tasks/`, each:

```yaml
id: T012_vehicle_input_simple
tier: simple            # simple | complex | agentic
prompt_ko: "107번 vehicle input을 600으로 바꿔줘"
prompt_en: "Change vehicle input 107 to 600"
network: demo_two_intersection   # or a Vissim example network
expected:
  post_state:            # assertions against the shadow model after completion
    - object: vehicle_input:107
      attr: volume
      value: 600
  must_warn: []          # constraint ids that MUST fire
  must_refuse: false
notes: ""
```

Target composition (~60 tasks; grow from 20):
- **Happy-path edits** (~20): every M2 verb × parameter variants, ko + en phrasings.
- **Grounding challenges** (~10): ambiguous names, Korean/English mixed references, objects
  that don't exist (`must_refuse` with candidates).
- **Constraint triggers** (~15): one per major catalog entry — over-capacity demand
  (`must_warn: [latent_demand_overflow]`), cycle below intergreen (`must_refuse`),
  unsupported controller type, short-run missing KPIs, deleted-vehicle runs.
- **Trap tasks** (~10): edits that *succeed* in raw COM but produce lies — the agentic
  "reduce delay" task where the tempting config triggers a lie detector; lane-closure
  requests (must refuse, never silently drop).
- **Multi-turn** (~5): under-specified requests requiring clarification; scripted user
  replies in the task spec.

Networks: synthetic demo dataset (fast, CI-safe) + 2–3 PTV example networks (real COM) + —
if Dochak provides one — a real Korean intersection .inpx as the case-study network (the
credibility lever both prior papers used; [B] used Gangnam).

## 3. Metrics (report all with N and 95% CIs; both papers omitted them)

Per task, computed from trace + shadow-model diffs by the M5.2 runner:

- **Task success rate** — expected post-state assertions all hold AND no unexpected diffs.
- **Verified-write rate** — fraction of writes whose read-back matched (RQ1's "provably").
- **Silent-failure detection rate** — of tasks where the no-validation arm produced a wrong
  post-state or a lying run, the fraction where the full stack refused/warned/corrected (RQ2's
  headline number).
- **Refusal correctness** — precision/recall on `must_refuse` tasks (false refusals are a
  product failure too).
- **Constraint yield** — which catalog entries fired, how often, true/false positive per
  entry (this table doubles as the catalog's empirical justification).
- **Recovery metrics** (failure-injection protocol, §4): recovery success rate, recovery
  iterations, time-to-recovery — same three as ChatSUMO-Agent §4.4, plus N per cell.
- **Interaction cost:** clarification turns, wall-clock per task (action_start/action_end),
  LLM tokens per task, planner-fallback-rung distribution.
- **Time-to-insight** (RQ3): T = Σ(T_compute + T_human) per step, agent vs. baseline, on a
  scripted subset (T_human = scripted-reply reading/answer time or measured with the user).

Disclosure standard (differentiator): model name + version, temperature, prompt file version
(prompts are versioned in `agent/prompts/`), seeds, N per condition, variance. Run every
condition with ≥5 repetitions (LLM stochasticity); report mean ± CI.

## 4. Conditions

| Condition | What it is | Purpose |
|---|---|---|
| **Full stack** | planner + guardrails (preflight+postwrite+postrun) | the system |
| **No-validation ablation** | same planner, guardrails flag off (M2.2 boolean) | RQ2 — the paper's key table |
| **Regex baseline** | existing `parse_prompt_simulation_command` path | free imperative baseline (it exists today) |
| **Naive codegen** (optional) | LLM writes a win32com script per request, executed on the clone | the "anyone can wire an LLM to COM" strawman, made concrete |
| **Provider swap** (optional) | local Qwen vs Claude on the simple tier | robustness/on-prem story Dochak cares about |

Failure-injection protocol (adapted from [A] §4.4): inject controlled faults —
(a) wrong-object grounding (prompt names an absent object), (b) COM write clamped/ignored
(mock layer forces a silent no-op), (c) run producing lie conditions (short run / deleted
vehicles) — × 2 network scales; measure the recovery metrics. Our (b) is the Vissim-specific
novelty: [A] could rely on SUMO to error; we synthesize the no-op and test whether the
postwrite layer catches it.

## 5. Paper skeleton (mapping results → sections)

1. **Intro** — silent-failure motivation (validated examples: conflicting greens improve
   reported delay; DiffusTm deletions; latent demand; gridlock LOS A) → dichotomy: loud vs.
   silent simulators; validation as the substitute error channel → contributions (agent for a
   closed commercial simulator via COM; machine-encoded constraint catalog as pre/post
   validation; benchmark + ablation quantifying it).
2. **Related work** — 3 subsections ending with contrasts: LLM×simulation agents ([A], [B],
   SUMO-MCP, TrafficGPT); LLM tool-use/validation; Vissim COM automation. Position [B]
   charitably (KAIST neighbors; it contributes the interaction protocol we adopt).
3. **Methodology** — framework figure; state tuple; planner + tiers + clarify-before-execute;
   **the tool table**; the constraint catalog (categorized excerpt table — the full catalog is
   the artifact); tool-return schema + verification formalism.
4. **Experiments** — RQ1/RQ2/RQ3; walkthrough figure (Korean dialogue, plan card, verification
   evidence); benchmark results table; **the ablation table (RQ2) is the headline**;
   failure-injection table; time-to-insight; limitations as subsection 4.6.
5. **Conclusion** — insights: validation as error channel; catalog as encoded expert
   knowledge (Dochak's tacit knowledge, defensible asset); closed-simulator agents differ
   qualitatively from open-simulator agents.

Case study: one real Korean intersection scenario end-to-end (needs a real .inpx + ideally
observed counts from Dochak — **user's manual task to request**).

## 6. Timeline reality check

On-site ends ~2026-09-05. [A] went received→accepted in ~4.5 months; [B] seeded with a
workshop paper first. Plan: system + benchmark done by ~08-25; harvest all tables from CI-able
runs; draft the methodology/experiments skeleton before on-site ends (while Vissim access is
guaranteed); consider a workshop-paper first submission ([B]'s path) with the TR-C extension
following. Decision point with the professor at the end of M3.
