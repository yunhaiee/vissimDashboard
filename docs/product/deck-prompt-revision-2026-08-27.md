# Deck revision prompt — background, limits, future work (2026-08-27)

> Paste everything below the horizontal rule into the same Claude Design conversation
> that produced the deck. It edits the existing deck; it does not start a new one.

---

Please make four changes to the deck. Keep the existing visual style exactly as it is.

## 1. Insert two new slides directly after "The problem in one sentence"

### New slide A — "Prior work: LLM agents that drive traffic simulators"

Three entries, set as a compact reference list in the muted text color with the venue in
the accent color. Keep each description to one line.

- **ChatSUMO Agent** — Li, Ma, Azfar, Ke (Rensselaer Polytechnic Institute).
  *An LLM-Based Agent for Conversational Traffic Simulation in SUMO.*
  **Transportation Research Part C 190 (2026) 105759.** A closed-loop planner that emits one
  JSON action per step from a fixed vocabulary (build network, build routes, run, analyze,
  modify, stop); recovery is additional loop iterations. Code is public and MIT-licensed.
- **AgentSUMO** — Jeong, Chang, Yoon (KAIST GSDS).
  *An Agentic Framework for Interactive Simulation Scenario Generation in SUMO via LLMs.*
  **arXiv:2511.06804 (Nov 2025), under review.** Its contribution sits *before* execution:
  requests are complexity-classified, checked against a parameter-sufficiency schema, and any
  missing critical parameter pauses for a clarification question with a recommended default.
- **Related** — ChatSUMO (arXiv:2409.09040, IEEE OJVT 2024), SUMO-MCP (arXiv:2506.03548),
  TrafficSimAgent (arXiv:2512.20996).

One-line closing note beneath the list, in muted text: every one of these targets **SUMO**.

### New slide B — "The gap this work addresses"

Lead statement, set large:

**Open simulators emit the error signals that make closed-loop agents work. A silent-failure
simulator does not — so the error channel has to be built.**

Then a three-column comparison table (hairline borders, no fill; column headers in accent):

| | ChatSUMO Agent | AgentSUMO | This work |
|---|---|---|---|
| Simulator | SUMO | SUMO | **PTV Vissim** |
| Error signal | CLI fails loudly | CLI fails loudly | **Silent — the validation layer supplies it** |
| Pre-execution gating | Lifecycle rails | Complexity tiers, parameter schema | Both, plus a constraint catalogue |
| Verifies the edit took effect | No | No | **Read back after every write** |
| Human approval before a write | — | Plan confirmation | Per-edit, with the reason recorded |

Below the table, one muted line: the "no" entries are observations from the published paper
and its public repository, not a judgement of those systems — SUMO simply does not require
the check, and Vissim does.

Closing line in accent: in Vissim, an unverified edit means confidently reporting numbers
from a simulation that did something else.

## 2. Edit the gate slide ("The generation gate")

Delete the final bullet entirely — the one beginning "Honest limit on the same slide" and
mentioning queue counters, signal-binding membership, and "the gate is the oracle". Remove
that whole sentence and its follow-on. Keep every other bullet on that slide unchanged.

## 3. Replace the "Limits and what comes next" slide

Retitle it **"Current scope"** and reduce it to a single statement plus one supporting line:

- The scoring logic, the refusal logic, and the approval and audit trail are in place.
- A calibration **validated against real-world field counts** is the next milestone and is
  not claimed today.

Delete the other three items entirely (phase-diagram binding, vehicle-class mapping, gate
families). Do not replace them with anything.

## 4. Add a new "Future work" slide immediately after "Current scope"

Four items, each a heading line in white with one muted line beneath.

- **A Vissim MCP server**
  No public Model Context Protocol server for Vissim exists today. Exposing the same
  validated tool surface over MCP would let any MCP client drive Vissim while the constraint
  catalogue, the read-back verification, and the approval gate stay in force. The safety
  layer travels with the tools rather than living in one application.
- **A no-validation ablation**
  Disable the constraint layer and measure how many silently wrong edits reach the model.
  This turns "validation is the product" from a design claim into a measured one, and it is
  the evaluation neither prior system reports.
- **Validated calibration on field data**
  Score against observed counts collected in the field, and report the result with sample
  sizes and confidence intervals.
- **More regions through the adapter layer**
  Data intake is a format-neutral adapter, so a new city is a new adapter rather than a
  rewrite. Daejeon signal sheets and WA SCATS counts are the two that exist.

Keep the closing slide and the Q&A slide as they are.
