# Prism evaluation benchmark

How Prism's agent is measured, what the numbers are, and what they do and don't show.

## Why this benchmark exists

PTV Vissim fails silently. It accepts many invalid edits without an error and then reports results as if they were correct: a write can be clamped or ignored, a short run can produce empty KPI tables, and a missing object can look like a successful no-op. An LLM agent that only "sounds right" will pass these silently through to the engineer.

So the question this benchmark asks is not "did the agent finish the task?" but **"when something goes wrong, does Prism catch it?"** Prism's claim is that the validation layer, not the model, is the product. The benchmark is built to measure that claim directly, by running the same tasks with the validation layer on and off.

## What is measured

The harness lives in [`backend/eval/`](backend/eval/).

### 1. Task suite (15 tasks)

Each task is a YAML spec in [`backend/eval/tasks/`](backend/eval/tasks/) with a Korean and English prompt, a target network, and expected outcomes: post-state assertions on the model, constraint warnings that must (or must not) fire, and whether the agent must refuse or ask for clarification.

| ID | Task | Expected behavior |
|---|---|---|
| T001 | Network overview (read) | Correct read, no write |
| T002 | Vehicle input (read) | Correct read, no write |
| T003 | Signal controllers on a model with none (read) | Honest "none found", no invented data |
| T004 | Change a vehicle input volume | Write lands and is verified |
| T005 | Volume above the allowed range | Refuse with reason |
| T006 | Edit a vehicle input that doesn't exist | Refuse, no hallucinated object |
| T007 | Negative volume | Refuse with reason |
| T008 | Volume that exceeds capacity | Write allowed, capacity warning must fire |
| T009 | Change simulation duration | Write lands |
| T010 | Change random seed | Write lands |
| T011 | Edit a signal controller that doesn't exist | Refuse |
| T012 | Run that is too short to produce KPIs | Run allowed, KPI warning must fire |
| T013 | Normal-length run | No false warning |
| T014 | Lane closure request (unsupported) | Refuse, never silently drop |
| T015 | Ambiguous object reference | Ask for clarification, don't guess |

### 2. Fault injection (5 injectors)

To test silent failures on purpose, [`backend/eval/injection.py`](backend/eval/injection.py) wraps eligible tasks with deterministic, seeded faults:

| Injector | Fault it simulates |
|---|---|
| `wrong_param_name` | The agent calls a tool with a wrong parameter name |
| `hallucinated_object_id` | The agent targets an object id that doesn't exist |
| `clamped_write` | Vissim silently clamps the written value |
| `ignored_write` | Vissim silently ignores the write |
| `kpi_tables_stripped` | The run finishes but its KPI tables are empty |

### 3. Conditions (arms)

| Arm | What it is |
|---|---|
| **Full stack** | Agent with guardrails before the edit, after the edit, and after the run |
| **Ablation** | Same agent, guardrails turned off (`--guardrails-disabled`) |
| **Regex baseline** | The pre-agent rule-based command parser, run on the same suite |

### 4. Scoring

Scoring ([`backend/eval/scoring.py`](backend/eval/scoring.py)) reads the agent's **trace records** (action start and end, plan, the three guardrail checks, refusals, warnings, user confirmations), not its final answer, so an agent cannot pass by sounding confident. Per task it records:

- **Task success:** all expected post-state assertions hold.
- **Warning checks:** required warnings fired, and no forbidden warnings fired.
- **Refusal correctness:** refused when it should, and didn't refuse when it shouldn't.
- **Clarification:** asked when the request was ambiguous.
- **Silent-failure detection:** in injected trials, whether the stack refused, warned, or rolled back instead of reporting a wrong result.

Rates are reported per injector and pooled, with **Wilson 95% confidence intervals** ([`backend/eval/report.py`](backend/eval/report.py)). Wilson is used instead of the normal approximation because trial counts are small and rates near 0% or 100% are common.

## Results

From the run recorded in the project decision log (2026-07-24):

| Measure | Result |
|---|---|
| Silent-failure detection, pooled over 5 injectors (N = 80 trials) | **60% full stack vs. 0% ablation** |
| Regex baseline, tasks recognized | **1 of 15** |

What these show: with the validation layer off, every injected silent failure went through undetected. With it on, most were caught. The regex baseline's 1/15 was checked by hand and reflects a real word-order and vocabulary gap, not a strawman (a message in its expected form is recognized correctly).

Findings the benchmark surfaced, and that were then fixed:

- `wrong_param_name` was invisible to silent-failure detection in both arms, because schema-level rejections were not traced as refusals. These rejections are now traced as refusals, so this injector is detected in both arms (correct, since the ablation turns off the guardrail engine, not schema validation).
- `ignored_write` was a documented blind spot for some parameters. After a fix that treats a missing written value as evidence of a dropped write, it now shows the intended contrast: detected with guardrails, missed without.

Because both fixes landed after the N = 80 run, the pooled 60% is a lower bound for the current code. **Re-run before quoting a current number** (see below).

## Limitations

- **Scripted runs.** By default each task uses scripted agent turns and a simulated Vissim backend, not a live LLM or live Vissim COM. This isolates the validation layer, but it is not a field result. Live runs are possible (`--real-provider`) but are not part of this result.
- **Small suite.** 15 tasks and 5 injectors. The plan is about 60 tasks, including multi-turn and "trap" tasks.
- **Single seed per run.** Each condition should be repeated 5+ times with different seeds and reported as mean with CI.
- **Measurement caveats.** Silent-failure detection cannot yet tell a confidently wrong answer from an honest "didn't understand". Tasks that only check "must not warn" pass trivially by doing nothing, so they must be read together with refusal correctness.
- **No cost metric yet.** Action start and end times are traced, but tokens per task are not, and neither is reported. Adding cost per task (tokens and wall-clock time) would measure value, not only accuracy.

## How to reproduce

Requires Python 3.11+. From `backend/` with dependencies installed (`pip install -r requirements.txt`):

```bash
# Task suite, full stack
python -m eval.runner

# Task suite, guardrails off (ablation)
python -m eval.runner --guardrails-disabled

# Regex baseline on the same suite
python -m eval.runner --baseline regex

# Fault injection: 8 trials per injector, both arms (80 trials total)
python -m eval.injection --n 8 --out-dir eval/results/injection/full
python -m eval.injection --n 8 --guardrails-disabled --out-dir eval/results/injection/ablation

# Combined tables with Wilson intervals
python -m eval.report eval/results/injection/full/results.jsonl eval/results/injection/ablation/results.jsonl
```

Runs are deterministic for a given seed (default `20260724`), so the same inputs produce byte-identical tables.
