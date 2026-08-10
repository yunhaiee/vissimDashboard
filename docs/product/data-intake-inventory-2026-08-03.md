# TK data drop — read-only inventory (2026-08-03)

Structural inventory of the seven files TK Kim sent 2026-08-02. **The files themselves live in
`client-data/`, which is gitignored** — Daejeon city and Main Roads WA client data, not ours.
This document records only *shape*: sheet names, header structure, units, coverage. It is the
input spec for roadmap **1.2 (대전 어댑터 v1)** and the first real test of the neutral schema
`backend/services/dataimport/` shipped blind in 0.7.

Nothing was extracted to disk; every archive was read in memory.

---

## 1. Daejeon — 유성구 일원 교통량 데이터.zip (12.5 MB)

11 workbooks, one per intersection, all named
`[지역 자치단체 제출용] 2026-04-06 00_00_2026-05-10 23_45_<교차로명>_15분_집계 주기.xlsx`.

**Coverage: 2026-04-06 00:00 → 2026-05-10 23:45 — five continuous weeks at 15-minute
resolution.** One `Sheet1` per file, ~16,800 rows.

Three-row hierarchical header:

| header row | carries |
|---|---|
| r1 | 유입부 · 시간대 · **좌회전 · 직진 · 우회전 · 유턴** (7 columns each) |
| r2 | 화물 · 버스 · 승용 · 합계 (within each movement) |
| r3 | 대형 · 중형 · 소형 (within 화물/버스) |

So each movement is broken into **six vehicle classes** — 화물 대형/중형/소형, 버스 대형/소형,
승용 — plus a 합계. Approaches (`유입부`) are named by destination, e.g. `수목토네거리방면`.

**This answers all four questions the adapter contract had open for the Daejeon side:**

1. Turning movements or approach totals? → **full turning movements**, and with a **U-turn**
   the Western four-movement convention often omits. `TurnType.U_TURN` already exists in the
   0.7 schema, so this lands without a change.
2. Vehicle classification? → a **6-class Korean scheme**, unmappable onto Austroads 1–12 by
   inspection. 0.7's decision to model vehicle class as a *reference* (scheme + code + optional
   coarse canonical) rather than an enum is what makes this importable at all.
3. Interval length? → **15 minutes**, uniform, five weeks.
4. Signal-group/phase numbering ↔ .inpx? → **still open, and now sharper** — see §2.

**Parser trap, found immediately:** data rows and `소계` (subtotal) rows are interleaved in the
same column layout. A naive read double-counts every hour. This is exactly the
"approach total ≠ movement sum" REFUSE case 0.7's quality gate already specifies — the gate
should catch it, and unit 1.2 must prove it does rather than assume.

## 2. Daejeon — 218. 대전신호.zip (4.5 MB)

23 files, **all PDF**, one per intersection: `<번호>. <교차로명>.pdf` (e.g. `319. 도안중네거리.pdf`).

**This is the significant finding: the Daejeon signal side is not a machine-readable export.**
The count side is clean tabular xlsx; the signal side is drawings. Either we parse PDF signal
diagrams (fragile, and the 0.7 contract's never-invent rule makes a low-confidence OCR read
unusable as authority), or we ask TK for the underlying SIS export. **Ask first** — this is a
question, not an engineering problem, and TK has already offered a walkthrough.

> ### CORRECTION 2026-08-10 — the paragraph above is wrong, and cost us roadmap 3.1's Korean half
>
> The 23 files were never inspected past their extension. Measured: every one is a **single-page
> text-layer PDF** of the Korean police-standard 교차로 운영 DATABASE form, and `pypdf`'s layout
> extractor recovers the whole form — header, SIGNAL TABLE (7 rows × 6 phase columns × 2 rings),
> PLAN TABLE (수준→주기 and ID→연동+시간분할), TOD PLAN 1/2/3, weekly plan, MEMO — as an aligned
> character grid. **No OCR is involved anywhere.** The only drawing on the sheet is the small
> 현시도 sketch in the corner.
>
> `backend/services/genesis/signals_daejeon.py` parses **22 of the 23** into neutral
> `SignalControllerData` (120 declared programs); the 23rd is refused with a named reason. Re-run
> the measurement yourself: `python tools/genesis_signals_daejeon_probe.py --zip "../client-data/218. 대전신호.zip"`.
>
> The provenance worry in the original paragraph was aimed at OCR and does not apply: these are
> exact character reads with a file-and-row locator on every record, and the parse is
> cross-checked against the documents' own arithmetic (Σ시간분할 must equal the level's 주기 for
> every scheduled 수준/ID pair). What the sheets genuinely do NOT carry is listed under Question 1
> below. Full record: `docs/phase2/DECISIONS.md` 2026-08-10.

**The two sets overlap almost perfectly.** All 11 intersections with count data have a matching
signal PDF (도안중 · 수목토 · 신흥들삼거리 · 옥녀봉네거리 · 옥녀봉삼거리 · 용반 · 용소삼거리 ·
원신흥 · 유성 · 계룡대교 · 도안2단지삼거리). That is a contiguous 유성구 corridor with **both
demand and control for every node** — the Daejeon pilot network, already assembled, without
having to ask for anything more.

---

## 3. Main Roads WA — Traffic signal data.zip (2.5 MB)

Three workbooks: `LM00398` (South St), `LM00972`, `LM00973` (both Murdoch Dr), period
2026-02-16 → 2026-02-22. Site tabs carry Road / Road Name / SLK / latitude — **these are the
Murdoch Drive corridor, i.e. the same network already in `runtime/uploads/current/`.**

Tabs: `Site` · `Hourly Volume` · `Hourly Peak` · `Hourly Site Performance` ·
`Quarter Hourly Volume` · `Quarter Hourly Peak` · `SCATS Phase History` · `Timings`, plus
`Link and Offset Plans` **on LM00398 only**.

**First real test of the 0.7 adapter, and it finds two gaps in Appendix A's own spec:**

- Appendix A describes a 4-tab spreadsheet (Site / Timings / Link & Offset Plans / Phase
  History). Reality has **8–9 tabs**: the five volume tabs are not in the published spec at all.
  `SCATS_FIELD_MAP` was written from the spec and will not cover them.
- `Link and Offset Plans` is present only on the coordinated site. An adapter that treats it as
  required refuses two of three real files. Absence here means "not coordinated", not "missing
  data" — precisely the distinction 0.7's `absent` map exists to record.

Detector volumes at 15-minute resolution: 15 detectors on LM00398 and LM00972, 9 on LM00973.
**`DetectorCountData` — the record type the 0.7 falsification pass *added* because detector
volumes have no turning movement — is what this data actually is.** That call was made blind
against the spec; the real files confirm it.

## 4. Main Roads WA — Calc_MurdochDr_BaseTurningMov_JH_edit_20260526.xlsx (3.9 MB)

26 sheets: `South01`–`South16` (per intersection-approach-movement), then `FarmRd`,
`WandooPrison`, `Bramanti`, `Caltex`, `Farrington`, `Parking`, `Veh Composition`, `Summary`,
`AM2026_Base`, `PM2026_Base`.

Each count sheet carries project/intersection/date/weather metadata, then 15-minute bins
(`0000 - 0015`, 120 rows = 24 h) across **CL 1 … CL 12 + BIKE**, with aggregate columns
**1 / 2-5 / 6-9 / 10-11 / 12** — exactly the Austroads grouping `mrwa_reference`'s PCU table
encodes. Survey date **Wed 25 March 2026**.

Note `Parking` and `Veh Composition` sheets: parking is one of the v2b verbs still unbuilt, and
vehicle composition is a shipped verb — both have real client data waiting.

## 5. Main Roads WA — travel time (TravelTime_Analysis_Results.xlsx + Shpfile zip)

Not Bluetooth or ANPR: **TomTom Traffic Stats — Route Analysis**, job created 2026-05-21.
Two routes (Roe Hwy/Murdoch Dr NB and SB), one day — **2026-03-25, the same Wednesday as the
turning-movement survey** — in two windows, 07:30–08:30 and 15:30–16:30. Segment-level, ~50
segments per route, with sample sizes in the hundreds to low thousands per window.

The shapefile gives route geometry (`.shp`/`.prj`) plus per-segment length, FRC, speed limit and
street name, keyed by `Id` to the per-window `.dbf` sidefiles. **So modelled sections can be
matched to surveyed segments geometrically rather than by name.**

**Methodological finding worth raising with TK.** The workbook reports harmonic-average,
arithmetic-average and median speeds as separate sheets, and on real segments they diverge
sharply — one Murdoch Drive segment reads 40.5 / 52.5 / 60.8 kph for the same hour under the
three definitions. MRWA's ±15%-or-1-minute travel-time criterion (Table 5-13) does not say
which. **Choosing the definition decides whether a model passes**, so it has to be agreed and
then pinned in the validation profile, not left to whoever exports the comparison. For speed
averaged over a fixed distance the defensible choice is the **harmonic** mean — it is the one
that corresponds to total travel time — and it is also the most conservative here.

---

## What this unblocks

- **1.2 Daejeon adapter**: demand side is fully specified above and can start now. Signal side
  is blocked on a question to TK, not on engineering.
- **Travel-time validation**: the dashboard has shipped MRWA's travel-time thresholds with
  nothing to test them against. These two routes are that missing half.
- **Task #35 (data-intake spike)**: this document is its read-only deliverable.

## Questions for TK

His own message offered a walkthrough (*"아무래도 설명이 필요하겠죠? 필요할때 연락줘요"*). The
four that actually block work:

1. **Daejeon signal data** — ~~is there an SIS export behind those 23 PDFs (phase/split/offset as
   data)? Parsing the drawings is possible but the numbers would carry far weaker provenance.~~
   **Superseded 2026-08-10** (see the correction under §2): the PDFs are machine-readable and
   parsed, so no SIS export is needed for cycle, split, offset or TOD. Two narrower questions
   replace it, and the first one alone unblocks 22 intersections:

   1a. **Does the sheet's `YELLOW(RED)` column hold 황색 only, or 황색+전적색?** The form has one
   clearance row and no 전적색 row, and Vissim needs the amber as its own display, so neither
   reading can be assumed and every intersection is held on this one word. (Our evidence leans to
   황색+전적색: `MIN. GREEN` equals `시간분할 − YELLOW(RED)` for 59 of 94 (sheet, phase) pairs, and
   `MIN. GREEN` is a green — but leaning is not stating and we do not act on it.)

   1b. **Is the 현시도 available as data, or only as the drawing on the sheet?** Which approach
   moves in which phase is the remaining input; without it the phase→approach assignment must be
   supplied by hand per intersection.

   1c. Minor, one intersection: one sheet prints a 시간분할 vector with one more field than its
   SIGNAL TABLE has populated phase columns (an empty field — stray comma, or a sixth phase not
   shown in the table?). We refuse it rather than guess.
2. **Travel time** — which speed definition do Main Roads' own assessments use for the Table
   5-13 comparison? (Recommend harmonic; want it confirmed, not assumed.)
3. **One day of travel time vs five weeks of counts** — is 2026-03-25 the agreed calibration
   day for both, or should the Daejeon window be narrowed to match a chosen WA day?
4. **Korean vehicle classes → Austroads** — is there a published correspondence Dochak already
   uses, or do we define one and have him review it?
