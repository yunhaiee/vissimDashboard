# Prism Agent Tool Catalog — v1 / v2 capability matrix

The definitive list of what the AI agent can do in Vissim, by verification tier.
Doctrine: **a verb ships only when its verification method does** (00-EXECUTION-GUIDE §4).
Updated 2026-07-28 (post-Phase-2 + v2 queue units ①–⑨ + health-check surface +
stage-based signal-program reader).

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
  - 2026-07-28: reads BOTH Vissig program families. `program_kind="signal_group"`
    is the `<progs>` shape unit ⑨ shipped; `program_kind="stage_based"` is the
    `<stages>` + `<interstageProgs>` + `<stageProgs>` shape found on 5 of the 6
    controllers in the first real client model — and present in NONE of the 25 PTV
    example networks
  - Stage programs report cycle/offset/stage sequence + a signal-group roster, but
    `sg_splits` stays empty with `sg_splits_unavailable_reason="STAGE_BASED_PROGRAM"`:
    `interstagesUsingMinDurations` compresses interstages by an amount that is not
    statically derivable, so a synthesized green window would be a guess
  - Never presents an implausible figure as fact: `data_quality` flags
    (`CYCLE_ABOVE_TYPICAL_MAX`, `NEAR_PERMANENT_GREEN`, `SINGLE_SIGNAL_GROUP_PROGRAM`,
    `NO_SIGNAL_HEADS`) ride along with ko/en prose, and an empty program list always
    carries a reason code instead of rendering as a bare blank

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
  - ⚠️ STAGE-BASED programs are read-only by design (2026-07-28): the verb refuses
    with `stage_based_program` rather than the old, misleading "command shape is not
    simple", which sent the planner hunting for a different `sg_no` that did not exist

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
- ✅ Desired-speed/time DISTRIBUTION curve editing — shipped (Q3 unit ③,
  `set_des_speed_distribution_points`, atomic ReplaceAll; the silent-failure map —
  decreasing speeds, mid-curve fraction reversals, absurd speeds all silently
  accepted by COM — is closed by refuse-tier constraints)
- ✅ Priority-rule gap/headway parameters — shipped (Q3 unit ②, `get_priority_rules` +
  `set_priority_rule_gap_headway`; live-proven: `MinHdwy` is a COM alias of `MinClear`,
  metres — one underlying "minimum clearance" value, not a third attribute)
- ✅ Driving-behavior params via duplicate-then-edit — shipped (Q3 unit ⑥,
  `duplicate_driving_behavior` / `set_driving_behavior_parameter` /
  `set_link_behavior_type_driving_behavior`; a referenced behavior REFUSES in-place
  edits — duplicate → edit the zero-ref copy → assign. W99 CC0–CC9 deliberately
  excluded: the pre-existing agency-restriction constraint refuses all CC writes and
  a verb would route around it)
- ✅ Dwell-time distributions — shipped (Q3 unit ⑤, `get_time_distributions` +
  `set_time_distribution_normal`/`_points`; the real object model is the GLOBAL
  TimeDistributions collection shared by PT dwell, CC1, zipper-overtake, parking
  and area waits — stop-sign dwell disproven)
- ✅ Signal green-splits within a program — shipped, see V1 edits (the blob writer
  turned out to be a COM-writable attribute, not an offline XML rewrite)
- ✅ Vehicle type properties — shipped (Q3 unit ④, `get_vehicle_types` +
  `set_vehicle_type_reference`, 8-field whitelist; accel/decel FUNCTION curve
  editing remains probe-report-only — same ReplaceAll shape as unit ③, untested)

**Status: 12 of 12 bullets shipped — V2a COMPLETE (one tool, `set_des_speed_decision`, satisfies both
the PAIRS and distribution-assignment line items).**

---

## V2b — structural/physical tier (XML-build + reparse + reference-integrity verification)

- ✅ Lane closure / reopen — shipped (Q4 ①, `set_lane_closure`; attribute-level via COM
  BlockedVehClasses, structural at XML; the v2b SPINE landed with it: services/
  inpx_structure.py — construction/removal, reparse verification, bidirectional
  reference integrity, COM-load-oracle postwrite; structural proof pair
  remove_last_lane/add_lane, not yet planner-registered)
- ✅ Full link closure — shipped (Q4 ③, `set_link_closure`: composes per-lane
  BlockedVehClasses across every lane; route-consequence warn names affected routes)
- ✅ Add / remove links & connectors — shipped (Q4 ⑤: native COM AddLink/AddConnector —
  WKT is always raw metres while LaneWidths/Pos are display-units; straight-line
  fidelity, curvature/z/non-contiguous mappings recorded follow-ups; removes refuse
  naming referrers; reroute = route tools' territory)
- ✅ Link geometry edits (lanes / length / curvature) — shipped (Q6 ①:
  `set_link_lane_count` via ILaneContainer AddLane/RemoveLane — the M0.3 "not
  editable" premise was NumLanes-the-attribute only, measured false for the
  container; every count change shifts the centreline half a lane width (warn);
  `set_link_geometry` moves existing points raw-metres, point COUNT immutable,
  refuses shortening past anchored objects — Vissim silently relocates them and
  stretches far-end connectors 11.7→237.7 m; `set_link_point_radius` two-step
  USERINPUT/Rad; junction reshapes CREATE conflict areas — exempted for :added
  rows only, removals stay loud; z-offset/RecalculateSpline/SplitLink
  deliberately unwrapped)
- ✅ Turn bans / movement removal — shipped (Q4 ③, `set_turn_ban` reversible on the
  connector's lanes + `remove_connector` structural, refuses-when-referenced naming
  referrers — Vissim itself silently DROPS dangling conflict areas on recompute)
- ✅ Signal heads (placement) — shipped (Q4 ④, add/remove; composite `sg` token schema;
  SG assignment is COM-object-only; lane-token registry now refuses lane/connector
  removals that would dangle heads)
- ✅ Detectors (placement/length) — shipped (Q4 ④, add/remove; MRWA defaults 2.0 m
  setback / 4.5 m length cited in-tool; sc/portNo wiring resolved; COM silently
  defaults unset fields — verification claims only what the caller supplied)
- ✅ Stop signs & priority rules (placement) — shipped (Q4 ⑥: add/remove stop signs
  native; **AddPriorityRule is a confirmed silent no-op** — creation goes through
  DuplicatePriorityRule + marker replacement, so a zero-rule network cannot get its
  first rule; conflict areas re-confirmed non-creatable, status-only)
- ✅ Measurement infrastructure — shipped (Q4 ②, 8 verbs: add/remove queue counters,
  travel-time sections, data-collection points + measurements; COM-create with the
  spine's reference-integrity guard — COM itself does NOT refuse dangling removals;
  nodes deferred)
- ⬜ Parking lots / zones
- ✅ PT lines / stops (timetables deferred) — shipped (Q4 ⑦: add/remove PT stops and
  lines — AddPTLine natively creates a minimal route + one auto line-stop; multi-stop
  needs DuplicatePTLine, departure-time creation tractable but unwired, both recorded;
  `set_pt_line_stop_dwell` wires dwell distributions — the BusDwell reconnection verb.
  RemovePTStop natively CASCADE-DELETES referencing line-stops silently — we refuse
  first, naming the lines)
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

## Related surface — model health check (not an agent verb)

`GET /api/health-check` + the `/health` page run the SAME constraint catalog
dataset-wide, parse-only (no COM, no Vissim install needed). Shipped 2026-07-28.

- **14 of 61 constraints** are dataset-capable — either shadow-model-only or evaluated
  per-object by feeding each object's CURRENT value in as if it were a proposed write
- **47 are edit-time-only** (need proposed params or a run result) and are listed as
  such in the report, derived from the registry so coverage can never be overstated
- 3 constraints are health-check-exclusive (`phase="dataset"`): evaluation-config
  disarmed, vehicle-input Cont-chain contradictions, orphan desired-speed-distribution
  references
- ~31ms on the real Beijing example; JSON export for paper artifacts

## Related surface — network component search (not an agent verb)

`GET /api/network/search?q=&limit=` + the search box on `/network` and the dashboard
도식 view. Parse-only (no COM). Shipped 2026-07-28.

- Covers every drawable element family; matches on id, name and Korean class alias
  ("신호" → signal controllers)
- Rank order: exact id > exact name > class alias > name prefix > name substring >
  id prefix, ties broken by family then row order so repeat queries are stable
- Reuses `approval_focus`'s anchor machinery, so a search hit and an approval card
  agree on where an element is
- Bounded like every list read: default 20, explicit `truncated` flag + total count
- Duplicate display names are never collapsed — 18 real elements share the name
  "South St EB", and each is labelled by family + id

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
