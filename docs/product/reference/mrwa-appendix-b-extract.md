# MRWA Appendix B — Saturation Flow Information (extract, v1.1)

Extracted 2026-07-31 from `saturation-flow-information-b.pdf` (15pp) for the MRWA
constraint/defaults pack (Phase 0.3) and genesis validation. Page cites are Appendix-B pages.

## Method hierarchy (B.3) — Main Roads preference order
1. On-site measurement (critical lanes first)
2. Similar-intersection measurement
3. RR67 + local factor (from measured lanes at the same site)
4. RR67 standalone
5. SCATS MF data (last resort; **not recommended** — only valid if fully saturated in the
   preceding 24h; indication only, p.B-9)

## On-site measurement rules (B.4, Table B-3)
- Per-LANE, never grouped. Timing starts when the REAR of the 4th vehicle passes the stop
  line; counting starts from the 5th vehicle; stop at the rear of the last queued vehicle.
- Queue-end heuristic: gap > 2 s ⇒ cycle over (heavy vehicles: judgement).
- **Minimum 5 vehicles per sample (≥9 in queue); minimum 6 samples per lane, ideally 10.**
- Never measure during exit blocking. Timing to 0.1 s (1 s rounding materially biases:
  14.49 s → 1739 vs 14 s → 1800 PCU/h). Rear-of-vehicle reference, consistent.
- Outliers (rule-violating samples) removed before averaging (worked example: sample of
  4 PCU/1175 PCU/h excluded; average 1830 PCU/h).

## Formulas
- **SF = (PCU count ÷ time_s) × 3600.**
- **RR67 (single unopposed lane):** `SF = 2080 − 42·δG·G − 100(3.25 − wl) − 140·δn`,
  divided by `[1 + 1.5f/r]` for turning — δG uphill flag, G %grade (uphill only), wl lane
  width m, δn nearside flag, f turning proportion 0–1, r turn radius m. Worked example:
  wl=4.2, offside, f=1.0, r=22 → **2036 PCU/h** (p.B-8).
- **Local factor = measured ÷ RR67**, applied to unmeasured lanes; worked example 93% (p.B-9).
- PCU: Austroads 1=1.0 / 2–5=2.0 / 6–9=3.0 / 10–11=4.0 / 12=5.0; cycle 0.2, motorcycle 0.4,
  rigid bus 2.0, artic bus 3.0 (Table B-1).

## Reconciliation / acceptance
- Measured vs RR67 diverging **>5%** ⇒ apply local site factor (B.7).
- Geometric evidence (marked plans) required in submissions; deviations from Table B-3
  justified in the report; local factor carried into option models.

## Constraint-engine candidates (drafted from the rules above — 0.3 implements/curates)
Refuse: sample <5 vehicles; SF ≤ 0; measured during exit blocking (if metadata says so).
Warn: SF outside ~800–2200 PCU/h; >5% RR67 divergence without a local factor; lane width
outside 2.5–5.5 m; gradient >10%; turn radius <12 m or >100 m; SCATS-MF-sourced SF without a
measured comparison; 1-second-rounded timing with small counts; local factor outside 85–110%.

Sources cited by the appendix: TRL RR67 (Kimber et al. 1986), Austroads GTM Part 3 (2013),
ARR340 (Akçelik et al. 1999).
