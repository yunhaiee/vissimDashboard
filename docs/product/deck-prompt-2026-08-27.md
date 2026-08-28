# Claude Design prompt — Prism lab presentation deck (2026-08-27)

> Paste everything below the horizontal rule into Claude Design.

---

Create a presentation deck (16:9 slides) for a research-lab talk. Audience: graduate lab
members and a professor in transportation engineering. Language: **English**. Tone:
precise, technical, understated. No marketing adjectives, no exclamation marks. Every
claim must be one the presenter can defend under questioning — where a claim has a limit,
state the limit on the same slide rather than omitting it.

## Visual style — match the product's own UI exactly

The product is a dark-theme web platform whose palette is locked to these tokens. Use
them literally; do not substitute a generic "dark deck" theme.

- Page background `#1a1a19`; deep panel background `#0d0d0d`
- Cards: **same background as the page**, separated only by a 1px hairline
  `rgba(255,255,255,0.10)`, 12px corner radius, **no drop shadow, no fill**
- Strong text `#ffffff`; muted text `#898781`; faint text `#6a6862`
- Hairline dividers `rgba(255,255,255,0.07)`
- Accent (links, active states, key numbers) `#4c8dff`; soft accent fill
  `rgba(76,141,255,0.16)`
- Alert / refusal accent `#e49787` on a `rgba(224,133,119,0.10)` fill with a
  `rgba(224,133,119,0.35)` border
- Chip / tag fill `rgba(255,255,255,0.05)`
- UI font stack: `"Pretendard Variable", "Pretendard", "Segoe UI", "Noto Sans KR", sans-serif`
- Serif accent for slide titles only: `"Iowan Old Style", Palatino, Georgia, serif`
- Monospace for code, identifiers, and reason codes:
  `"JetBrains Mono", "Fira Code", Consolas, monospace`

Layout rules: generous whitespace, one idea per slide, left-aligned text, no gradients,
no glow, no 3D. Numbers carry the visual emphasis — set key figures large in the accent
color with a small muted caption beneath. Diagrams are line drawings in hairline white
with accent highlights.

## Placeholders

Wherever visual evidence belongs, insert a clearly marked placeholder box (dashed
hairline border, muted label centered) instead of inventing an image. Use these labels:

- `[DEMO VIDEO — network generation from map selection]`
- `[SCREENSHOT — generation job detail with gate verdict]`
- `[DIAGRAM — pipeline: Scoping to Data to Build to Calibrate to Analyze to Report]`
- `[DIAGRAM — agent loop: plan, tool call, constraint check, approval, apply, verify]`
- `[SCREENSHOT — data intake page]`
- `[DEMO VIDEO — Vissim launching and running]`
- `[SCREENSHOT — dashboard curation view]`
- `[SCREENSHOT — calibration node grid]`
- `[SCREENSHOT — uploaded style sample and generated report, side by side]`
- `[PHOTO / TEAM SLIDE]`

Size each placeholder for its final asset — video placeholders wide, screenshots roughly
16:10.

## Deck structure

### 1. Title

"Prism — an LLM agent that builds, checks, and refuses". Subtitle: traffic
microsimulation for PTV Vissim. Presenter name placeholder, date, lab name.

### 2. The problem in one sentence

Vissim fails silently: a wrong edit still produces a model that runs and prints numbers.
The real risk is not a crash — it is a plausible wrong answer. Design consequence, stated
plainly: **the validation layer is the product.**

### 3. What the system does — pipeline

`[DIAGRAM — pipeline]`. Six stages: Scoping, Data, Build, Calibrate, Analyze, Report.
Mark which stages this demo covers, and leave the uncovered ones visibly uncovered.

### 4. Demo scene 1 — network generation from a map

`[DEMO VIDEO — network generation from map selection]`

- Draw an area on a map, and the system produces an OpenStreetMap extract, a geometry
  draft, a connectivity layer, and a single `.inpx` that **PTV Vissim actually loads** —
  verified live, with zero entries in Vissim's error file and the COM object census
  matching the emitted file.
- The agent then proposes edits, and a human accepts or rejects each one.

### 5. How an edit actually happens — the agent loop

`[DIAGRAM — agent loop]`

Five stages, each a real mechanism rather than a description:

1. **Tool registry** — 87 registered tools with strict dispatch and typed schemas. Read
   tools (`get_network_overview`, `get_vehicle_inputs`, `get_signal_programs`,
   `get_conflict_areas`, `get_routing_decisions`, `get_driving_behaviors`) and write tools
   (`add_link`, `add_connector`, `set_signal_green_split`, `set_vehicle_input_volume`,
   `set_link_lane_count`, `remove_link`).
2. **Constraint catalogue** — every write is validated before it reaches Vissim. 117 named
   constraint messages, each in Korean, English, and Japanese, each carrying a
   machine-readable reason code rather than a generic error string.
3. **Human approval** — no write executes without an explicit approval that records who
   approved it and the reason they gave. That reason is carried into the audit document.
4. **Apply and verify** — the edit is applied through COM and then **read back**, and the
   written value is compared against what Vissim itself reports.
5. **Reversibility** — an append-only JSONL trace, a shadow model, versioned artifacts,
   and a `rollback_last_edit` tool.

Caption: this loop is why refusing is cheap. A refusal costs one message; a wrong edit
costs a whole study.

### 6. COM safety — four rules

Present as a compact list. Each exists because the corresponding failure mode is real.

- Automation never attaches to a running Vissim GUI, which would take over a person's open
  work; it always owns its own instance.
- The active dataset's `.inpx` is never edited in place — every job clones into its own
  working directory.
- Batch runs use `RunContinuous`, never per-step loops.
- All COM work goes through a job queue, never inside a request handler.

### 7. Demo scene 2 — data intake

`[SCREENSHOT — data intake page]`

State honestly that the demo showed the intake surfaces, not a live ingestion. Then give
the measured adapter results that do exist, labelled as offline results:

- Daejeon signal-timing sheets: 22 of 23 sheets parsed into structured phase plans.
- WA SCATS observed counts: 30,208 count records read.
- A format-neutral adapter layer, so a new region is an adapter rather than a rewrite.

### 8. Demo scene 3 — the simulation is real

`[DEMO VIDEO — Vissim launching and running]`

This is not a mock. PTV Vissim 2026 is driven over COM, the run is queued, and the result
databases are read back. One design decision worth defending: results are read from
Vissim's own result database and never re-derived by us.

### 9. Demo scene 4 — dashboard

`[SCREENSHOT — dashboard curation view]`

An LLM selects which charts answer the current question instead of printing every chart.
Keep one honest note on the slide: charts that could not be computed are named as absent —
nothing is silently dropped.

### 10. Demo scene 5 — the validation and calibration surface

`[SCREENSHOT — calibration node grid]`

- Model volumes are scored against observed counts using **GEH**, with a threshold of **5**.
- Scoring runs per movement and per node total.
- The client authority's operational-modelling ladder (13 steps) sits alongside; a step
  with no evidence is displayed as **"no evidence"** and can never be marked complete.
- State the limit plainly: this demo showed the surface and the scoring logic. A
  calibration validated against real-world field counts is **not yet claimed**.

### 11. The idea that makes this defensible — a refusal is a result

The intellectual core of the talk. Give it a full slide.

- When the system cannot justify a change, it neither guesses nor stays silent. It refuses
  with a **named reason code**.
- Two refusals from the same screen, side by side, in monospace:
  - `NO_TARGET_OBJECT` — the node misses the observed count, but no vehicle input was named
    as the target, so a demand change with no object to change is not proposed.
  - `MODEL_REPRODUCES_OBSERVED` — the model already reproduces observed behaviour, so no
    parameter change is proposed.
- The strongest example: a request to edit an entrance volume that is itself a measured
  observation is refused **structurally**, not by threshold —
  `INPUT_VOLUME_IS_OBSERVED_DATA`. A measured input is the target, not a knob.
- The honest counterpart: when a check cannot run, the system reports `NOT_CHECKABLE` with
  the reason. A check that did not execute is never a pass. Quote verbatim: *"the table was
  created but the run collected zero rows — that is measured absence of data, not absence
  of an anomaly."*

### 12. The generation gate — the system refuses its own output

- Generated networks pass a nine-family validation gate (V1 to V9) covering connectivity,
  references, signals, demand, and vehicle composition, with 78 distinct finding codes.
- The gate refused networks **we** generated — isolated regions that traffic could neither
  enter nor leave. That result is reported, not hidden.
- Reference integrity is enforced on deletion: a link still referenced by connectors cannot
  be removed.
- Honest limit on the same slide: a freshly generated network still fails two gate families
  today (queue counters are not emitted, and one signal-binding membership case). The gate
  is the oracle; the generator is what must catch up.

### 13. Demo scene 6 — reports inherit a firm's own format

`[SCREENSHOT — uploaded style sample and generated report, side by side]`

- Upload a past report `.docx`, and the system extracts a style profile — palette, font
  ladder, page size and margins, heading numbering scheme, table header fill and border,
  and header/footer treatment — then applies it to a generated report.
- The extractor reads named styles that are **actually used**, not merely defined, so a
  leftover template style is not mistaken for the firm's format.
- Every field it could not extract is **named** with its reason, for example "the header
  carries no image", rather than silently defaulted.
- The report's spine is the client authority's required section structure. A section
  without evidence is printed as missing, with the reason, never quietly dropped.

### 14. Engineering scale — numbers only

87 agent tools · 117 named constraint messages in three languages · a V1–V9 gate with 78
finding codes · 187 backend test modules · six weeks of development. Every refusal and
rationale exists in Korean, English, and Japanese.

### 15. Limits and what comes next — place this BEFORE questions

Four lines, each phrased as a named next step:

1. No calibration validated against real-world field counts yet. The scoring and refusal
   logic are in place; the validated result is not.
2. Real Daejeon phase diagrams cannot yet be bound end to end. The binding model is one
   phase per approach link, so a plan with more phases than a node has approach links is
   mathematically uncoverable. Extension: link to phase **set**.
3. One observed vehicle class in the Daejeon counts has no canonical mapping, and the
   demand layer correctly refuses unmapped classes. A per-class human decision surface is
   needed.
4. A freshly generated network does not yet pass the full gate (slide 12).

Closing line: these are named, measured, and queued — not unknowns.

### 16. Closing

One sentence, large and centered:

**"A refused edit with a clear reason beats a completed edit with wrong numbers."**

Then `[PHOTO / TEAM SLIDE]` and a Q&A slide.

## Deliberately excluded

Do not include any claimed GEH improvement figure, any claim of end-to-end operation on
real client data, capacity-limit diagnosis or saturation-flow prescription results,
trajectory replay, or any performance or accuracy number not listed above. If a slide
feels thin without such a claim, keep it thin.
