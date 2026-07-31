# MRWA Appendix A — Signal Data Information for Modelling (extract, v2, 52pp)

Extracted 2026-07-31 from `App-A_signal-data-information-for-modelling-a.pdf`. **This is the
completion spec for the SCATS→fixed-time conversion module (roadmap 1.6 / R1.3b)** and the
signal-side reference for genesis (3.1) and phase-plan editing (EPIC 5). Page cites = Appendix-A.

## Source data (A.1, p.3-5) — what Main Roads provides
- **SCATS Phase History** (.xlsx/.csv, TrafficMap, 24-month retention): per record Date,
  Duration(s), Start, End — phase sequence + durations.
- **SCATS Event History** (.csv): `SGi=On/Off` (signal-group green bounds);
  `Walk j:Demand=On`, `Walk j:Demand=Off Active=On` (ped demand met), `Walk j:Active=Off`
  (clearance-1 start).
- **Signal Data spreadsheet** (4 tabs): Site (TCS no, region) / Timings (late start, min/max
  green, early cut-off, yellow, all-red, ped delay/walk/clearance, protection) / Link & Offset
  Plans (subsystem, coordinated phase, cycle bounds) / Phase History summary.
- Detector Volume Data; Phase Sequence Chart (.doc — late-start + protection markup);
  Strategic Monitor graph (24h link-plan occupancy → which plan was active in the window);
  LMA (signal arrangement: SG numbers/phases/detectors) + LMB (pavement/signage) drawings.

## Core derivation algorithms (A.2) — the 1.6 module's math
- **Stretch phase** (runs every cycle, usually A) defines cycle boundaries. Calculation
  period: first stretch-phase start in window → first stretch-phase start after window.
- **Avg cycle** c̄ = total duration ÷ n_cycles (n = stretch-phase starts).
- **Avg phase i** t̄ᵢ = total activation time of i (green + its intergreen) ÷ n_cycles;
  Σ t̄ᵢ = c̄. Non-every-cycle phases: t̄ᵢ = SCATS avg phase time × frequency fᵢ (fᵢ =
  occurrences/cycles; stretch = 1.0).
- **Coordinated sites with low-frequency phases**: recount cycles at the coordinated partner
  (Fig A-5, p.11).
- **Ped demand frequency** = count of `Demand=Off Active=On` events ÷ complete cycles (p.11).

## Time settings (A.3)
- **Yellow** (Austroads AGTM09-16): t_y = t_r + 0.5(V/3.6)/(a_d + 9.8G), min 3.0 s, round UP
  to 0.5 s; WA uses t_r=1.0, a_d=3.0; same yellow both approaches on a grade; Table A-2
  pre-calculated (p.15).
- **All-red**: t_ar = 3.6·L_c/V, min 1.0 s, round UP to 0.5 s; WA method = clearance distance
  ÷ posted-speed m/s (Table A-3: 40→11, 50→13, 60→16, 70→19, 80→22 m/s); Table A-4
  pre-calculated (p.17-18).
- **Intergreen** = early cut-off + yellow + all-red. Early cut-off is application-specific
  (AWFS ~7 s, staggered shutdown, rail sync) — site-justified.
- **Pedestrian**: walk = 6 s WA standard; clearance = crossing length A ÷ 1.2 m/s rounded up
  (Table A-5, 4–45 m → 4–38 s); Clearance-2 = intergreen − 1 s (WA rule). Delay time before
  walk when prior all-red can't clear the parallel conflict.
- **Protection types (6)** with distances measured from the pushbutton pole (A: full crossing,
  B: to median or 55%A, C: to exit-lane middle): exclusive phase / parallel+5s time control /
  parallel+3s+flashing yellow / timed red arrow (C÷1.2) / red arrow max(B,55%A)÷1.2 + flash /
  fully controlled. Existing sites: SCATS Special Times cell 11 holds protection time.

## Phasing library (A.4) — 9 standard sequences
Conventional / leading RT / lagging RT / lead-lag / repeat RT / split / diamond overlap
(3 variants) / aggro arrow (shared lane, 2 SGs) / **yellow-trap avoidance rule**: an opposed
filtering right turn must never overlap the next phase's intergreen (p.41-42) — a genesis/
EPIC-5 structural constraint candidate.

## Coordination & offsets (A.5)
Subsystem = intersections sharing a cycle; link plans 1–4 (night/PM/balanced/AM) chosen by
volume; coordinated-phase notation `a,b^c` (offsets a,b; ^=start-of-phase coordination,
no-^=end; optional TCS ref site). Offset derivation: identify active plan from Strategic
Monitor → thresholds from TrafficMap → linear interpolation (default) or hysteresis between
offset values by average cycle length. Worked example TCS 359 (p.44-49).

## Module implementation notes (1.6)
Count only complete stretch-to-stretch cycles; frequency-adjust non-every-cycle phases;
round yellow/all-red UP to 0.5 s, ped clearance UP to 1 s; SCATS fractions always round up
(main doc rule); use posted speed not design speed for all-red (WA practice); validate LMA vs
Phase Sequence Chart agreement; refuse yellow-trap sequences.
