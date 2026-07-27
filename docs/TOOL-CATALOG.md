# Prism Agent Tool Catalog — v1 / v2 capability matrix

The definitive list of what the AI agent can do in Vissim, by verification tier.
Doctrine: **a verb ships only when its verification method does** (00-EXECUTION-GUIDE §4).
Updated 2026-07-27 (post-Phase-2 + v2 queue units ①–⑨ + conflict-area read filters).

Legend: ✅ shipped · ⚠️ shipped with a by-design limitation · ⬜ planned · 🚫 permanent refusal

---

## V1 — parameter tier (read-back verification) — **SHIPPED, close-out complete**

### Reads
- ✅ Network overview
- ✅ Object resolve (fuzzy, Korean-aware)
- ✅ Vehicle inputs — including per-interval volume / from_s / continuous profile (v2 unit ⑤)
- ✅ Signal controllers
- ✅ Run KPIs
- ✅ Scenario comparison
- ✅ Routing decisions (`get_routing_decisions`, v2 unit ⑥)
- ✅ Conflict areas (`get_conflict_areas`, v2 unit ④)
  - 2026-07-27: gained `conflict_area_no` / `link_no` filters + a 40-item default cap
    with an explicit `truncated` flag and filter hint (observation-flood fix)
- ✅ Vehicle compositions (`get_vehicle_compositions`, v2 unit ⑦)
  - 40-composition default cap + `composition_no` filter + per-entry estimated share %
- ✅ Desired-speed decisions + distributions (`get_des_speed_decisions` /
  `get_des_speed_distributions`, v2a unit ⑧)
  - 40-cap + `decision_no`/`link_no` filters; distributions as a separate 60-cap
    lookup read (shared table, not needed on every decisions read)
- ✅ Signal programs (`get_signal_programs`, v2a unit ⑨)
  - Per controller: programs (active marked), per-SG green windows/amber/min-green;
    10-controller cap + `controller_no` filter

### Edits
- ✅ Vehicle input volume
- ✅ Vehicle input volume **per time interval** (v2 unit ⑤)
  - `set_vehicle_input_volume` takes optional `interval_no` — addresses any interval of
    a multi-interval input, not just the first
  - Verified live against the Cont-flag trap: `SetAttValue Volume(i)` for `i>1` RAISES
    while `Cont(i)` is true; the setter clears the flag first and reports `cont_after`
    in the verification payload
  - Closes the `vehicle_input_multi_interval_write_gap` CATALOG-GAP
- ✅ Simulation duration
- ✅ Random seed
- ✅ Signal offset
  - Unlocked 2026-07-24: controller types now parsed; FIXEDTIME / FIXEDTIMESIMPLE
    allowed, all external controller types refuse by design
- ⚠️ Signal cycle
  - `CycTm` only — disconnected from a program-driven controller's real cycle by
    Vissim design; refusal message explains the limitation
  - Real per-program cycle/green data now readable via `get_signal_programs` (unit ⑨)
- ✅ Reduced-speed area (`set_reduced_speed_area`, v2 unit ②)
  - First non-COM verb: XML injection + reparse read-back + live LoadNet/run oracle
- ✅ Conflict-area status (`set_conflict_area_status`, v2 unit ④)
  - Number-addressed with link-pair echo on the approval card
  - `conflict_area_data_unavailable` gap-check live
- ✅ Route relative flow (`set_route_relative_flow`, v2 unit ⑥)
  - Raw-weight semantics confirmed live; approval card echoes the estimated resulting
    share %; `route_integrity_unverifiable` check live
- ✅ Vehicle composition share (`set_vehicle_composition_share`, v2 unit ⑦)
  - Raw-weight semantics confirmed live — Vissim does NOT renormalize siblings on
    write (disproved the earlier placeholder constraint's premise); approval card
    echoes estimated resulting share % + referencing vehicle inputs
  - Landmine: the entries container has NO `ItemByKey` (unique among probed
    collections) — lookup scans `GetAll()` and matches `VehType` client-side
- ✅ Desired-speed decision distribution assignment (`set_des_speed_decision`,
  v2a unit ⑧)
  - The FIRST atomic paired write: optional `restore_decision_no`/`restore_distr_no`
    makes one approved action write the slow-zone start AND its restore decision,
    both read-backs verified BEFORE persist (all-or-nothing)
  - No shared-sum semantics — each vehicle class's assignment fully independent
    (live-verified); unpaired edits warn about the stay-slow-forever risk
  - Pairing is a PURE human convention: 584 real decisions scanned, zero names,
    no grouping attribute — the tool never infers pairs from geometry
  - Distributions live on `Net.SpeedDistributions` (shared with reduced-speed
    areas); no `DesSpeedDistributions` collection exists
- ✅ Signal green splits (`set_signal_green_split`, v2a unit ⑨)
  - The "blob writer": FIXEDTIME programs are edited through the COM-writable
    `IntSupplyData` Vissig XML (no COM program interface exists — verified against
    the whole type library); FIXEDTIMESIMPLE via plain EndRed/EndGreen
  - `program_no` REQUIRED — only the ProgNo-selected program is simulated; editing
    an inactive program warns loudly
  - Behavior-oracle-verified (SimBreakAt + SigState flip), not just readback/XML
  - Vissim silently accepts beyond-cycle values AND conflicting greens (live-proven)
    — `green_split_outside_cycle` refuses, the always-on confirm educates

### Runs
- ✅ Multi-seed simulation with auto-armed evaluation (all four evaluation types)
- ✅ Post-run lie detection + KPI summary
- ✅ Vehicle-trajectory recording (.fzp) for network-tab replay
- ✅ Load-validity check (`load_check`, tier=run, v2 unit ③)
  - LoadNet + one init step ONLY — **not** a full consistency check: no COM
    consistency-check surface exists anywhere in the type library (verified
    exhaustively 2026-07-24); semantic landmines stay invisible to it and the system
    prompt forbids overclaiming

### Refusals-with-reason (shipped as features, not gaps)
- Lane closure (v1 scope; structural — see V2b)
- Evaluation-config edits
- Shared driving-behavior edits
- CC1-as-scalar
- Agency-restricted calibration parameters
- Unknown objects
- Out-of-range values

---

## V2a — more parameters (same machinery; new plumbing + live probing per verb)

- ✅ Time-varying demand profiles (multi-interval inputs) — shipped, see V1 edits
- ✅ Static route relative flows — shipped, see V1 edits
- ✅ Conflict-area status — shipped, see V1 edits
- ✅ Vehicle composition shares — shipped, see V1 reads/edits
- ✅ Desired-speed decision PAIRS (set + restore, atomic) — shipped, see V1 edits
- ✅ Desired-speed distribution assignment — shipped (same tool as PAIRS)
- ⬜ Desired-speed/time DISTRIBUTION curve editing (the legitimate CC1 route)
- ⬜ Priority-rule gap/headway parameters
- ⬜ Driving-behavior params via duplicate-then-edit (expert-gated)
- ⬜ Dwell-time distributions
- ✅ Signal green-splits within a program — shipped, see V1 edits (the blob writer
  turned out to be a COM-writable attribute, not an offline XML rewrite)
- ⬜ Vehicle type properties (accel/decel curves, lengths, weight/power)

**Status: 7 of 12 bullets shipped (one tool, `set_des_speed_decision`, satisfies both
the PAIRS and distribution-assignment line items).**

---

## V2b — structural/physical tier (XML-build + reparse + reference-integrity verification)

- ⬜ Lane closure / reopen
- ⬜ Full link closure
- ⬜ Add / remove / reroute links & connectors
- ⬜ Link geometry edits (lanes / length / curvature)
- ⬜ Turn bans / movement removal
- ⬜ Signal heads (placement)
- ⬜ Detectors (placement / length)
- ⬜ Stop signs & priority rules (placement)
- ⬜ Measurement infrastructure — travel-time sections, queue counters,
  data-collection points, nodes
- ⬜ Parking lots / zones
- ⬜ PT lines / stops / timetables
- ⬜ Pedestrian inputs & areas
- ⬜ New intersection construction (composite of the above)

---

## V2c — semantic/expert tier (outcome verification + engineer-in-the-loop)

- ⬜ Full signal plan redesign (intergreen / conflict-matrix verified)
- ⬜ Calibration parameter search (GEH-scored against field data)
- ⬜ OD matrix editing + dynamic-assignment machinery (cost/path files, convergence)
- ⬜ Evaluation-config management (results snapshotted first)
- ⬜ Scenario management (.trax create / branch / merge)
- ⬜ Agentic compare workflows ("close a lane and compare")
- ⬜ Run video export (presentation)

---

## 🚫 Impossible by design (permanent, documented refusals)

- **External signal-controller logic (RBC / VAP / Econolite)** — timing lives in
  external files Vissim's own API cannot reach; correct behavior is refusal +
  "edit in the controller software" guidance
- **Conflict-area add/remove** — conflict areas are derived from geometry; no COM
  verbs exist

## Excluded (niche, out of scope)

- Meso/hybrid settings
- Managed-lane pricing
- Rail level crossings
- 3D / camera aesthetics
