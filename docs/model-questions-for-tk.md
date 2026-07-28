# Questions for TK — Murdoch Drive model (2026-07-28)

Findings from running Prism's signal reader against `SouthStreet_Base.inpx` and
cross-checking it with `Scenario Signal timing adjustment data.xlsx`. Every claim below is
reproducible with `backend/tools/probe_murdoch_signals.py` (pure XML, no COM, no licence).

**Read this distinction first.** Everything in "Provable" comes out of the files themselves
and is not open to interpretation. Everything in "Inferred" is our reading of intent, which
is exactly what only TK can settle. Do not present the inferred items as defects.

---

## The six controllers, as the model stores them

| SC | name | family | active program | model cycle | workbook measured |
|----|------|--------|----------------|-------------|-------------------|
| 6 | TCS398_MurdochDr | stage-based | 11 "BaseAM" | 131 s | 122.18 s |
| 7 | TCS972_Murdoch_Discovery | stage-based | 11 | 156 s | 148.74 s |
| 8 | TCS973_Murdoch_RobinW | stage-based | 11 | 159 s (offset 144 s) | 145.13 s |
| 9 | TCS1111 | signal-group | 11 "BaseAM" | **375 s** | — |
| 10 | TCS601 | stage-based | 11 | — | — |
| 99 | Murdoch-Farrington Link | stage-based | 41 | 120 s | — |

---

## 1. SC 9 "TCS1111" — 375 s cycle, 96% green. Stub or deliberate gate?

One signal group, green 0 → 357 s, 4 s amber, inside a 375 s cycle — roughly 14 s of red
per cycle. Its two heads sit on link 772 lanes 1–2 at position 619, downstream of SC 10's
SG 4 stop line at position 233 **on the same link**.

**The suggestive part:** the AM workbook has a literal **375** typed into the "Average Cycle
Adopted" cell for *both* the TCS972 and TCS973 blocks (N96, N150), while their computed
cycles are 148.74 s and 145.13 s. Did a stray 375 propagate into the model as well?

*Ask:* is this an intentional near-permanent-green gate, or a leftover placeholder?

## 2. SC 99 "Murdoch-Farrington Link" — complete programs, zero signal heads

Full stage programs (TMP2_AM_120s / TMP2_PM_120s), but **no `<signalHead>` anywhere in the
model references it**. It controls nothing.

*Ask:* deliberately disabled, or was it meant to control the link?

## 3. SC 10 "TCS601" — 3 of its 4 signal groups control nothing

Four signal groups defined; only SG 4 carries heads. SG 1/2/3 have none.

## 4. Model cycles run longer than the measured field cycles — confirm this is intentional

SC 6: 131 s vs 122.18 s · SC 7: 156 s vs 148.74 s · SC 8: 159 s vs 145.13 s.

This looks deliberate, not sloppy, and we want it confirmed rather than assumed. For SC 6 the
underlying agreement is very strong:

- Min greens match **exactly**: workbook A=8, D=6, E=8, G=6 → model SG 1,2=8, SG 7,8=6,
  SG 5,6=8, SG 3,4=6.
- Phase durations match to under half a second on three of four: D 20 s vs 19.50, E 35 s vs
  34.64, A 41 s vs 40.93. G is 29 s + G1 6 s = 35 s vs 33.93 — and the workbook itself notes
  *"Often runs G then G1."*
- Transition instants 37 / 57 / 92 s vs end-of-intergreen 37.93 / 57.43 / 92.07 s.

So the gap is mostly the extra G1 / F2 stages plus fitting a fixed-time cycle to
SCATS-actuated averages. **No program anywhere in the model carries a ~122 s cycle** — the
nearest 120 s program belongs to SC 99, which controls nothing.

## 5. Workbook cell N45 is `#REF!`

"Average Cycle Adopted" for TCS398. What should it be?

## 6. Field control is SCATS-actuated; the model is FIXEDTIME

The workbook carries Calls / Actual Av / MAX / MIN columns — actuated operation. Every
controller in the model is fixed-time. Confirm this is the intended approximation, because
Prism will describe these to users as fixed-time plans.

## 7. Minor: SC 7's name has a trailing space

`"TCS972_Murdoch_Discovery "` — breaks exact name matching.

---

## Also worth raising

- **We cannot find any observed traffic counts in the bundle — this is the big one.**
  We first assumed `Scenario TurningMovements_review.xlsx` held the field survey. It does not.
  Its ten sheets are `AM_DoMin` / `AM_TMP1_final` / `AM_TMP2_final` / `PM_*` plus four `_Diff`
  sheets — all *modelled scenarios*. Scanning every cell of all ten sheets for a header
  containing observed / survey / count / actual / field / 실측 / 조사 returns exactly one road
  name and one cell reference. The other two workbooks are the same story: `Travel Time
  Comparison` has Base / DoMin / TMP1 / TMP2 columns (all model), and the `TT_*` sheets are raw
  Vissim exports. The only genuine field data in the bundle is SCATS **signal timing** in
  `STP Cal_AM/PM` (Calls / Actual Av / MAX / MIN) — real, but it is timing, not volumes.

  *Ask, in this order:*
  1. Does a turning-movement **count survey** exist for this project at all? Murdoch may be a
     design/option study that was never calibrated against counts, in which case GEH-on-counts
     simply does not apply here and we should say so in the UI rather than show an empty tab.
  2. If it does exist, we need that file — whatever shape it is in.
  3. The model's travel-time measurements are named `TomTomTravelTime…`, so observed probe
     travel times almost certainly exist somewhere. Can we get the TomTom export? That alone
     would light up the travel-time half of Category 1 validation.

  We are NOT going to import modelled turning movements as if they were observed: GEH would
  compare the model against itself and pass every time, which is worse than showing nothing.
- **Stage-based programs are read-only in Prism by design.** We can report a stage program's
  cycle, offset, stage sequence and signal-group roster, but we deliberately do not
  synthesize per-group green windows, because `interstagesUsingMinDurations` compresses
  interstages by an amount that cannot be derived from the file. Worth asking how often
  Dochak needs to *edit* green splits on stage-based controllers — that answer decides
  whether this becomes a priority.
