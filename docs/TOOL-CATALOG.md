# Prism Agent Tool Catalog — v1 / v2 capability matrix

The definitive list of what the AI agent can do in Vissim, by verification tier.
Doctrine: a verb ships only when its verification method does (00-EXECUTION-GUIDE §4).
Updated 2026-07-24 (post-Phase-2 completion + completeness audit).

## V1 — parameter tier (read-back verification) — SHIPPED

**Reads:** network overview · object resolve (fuzzy ko) · vehicle inputs · signal
controllers · run KPIs · scenario comparison — all ✅

**Edits:** vehicle input volume ✅ · sim duration ✅ · random seed ✅ ·
signal offset ✅(unlocked 2026-07-24: controller types now parsed; FIXEDTIME/FIXEDTIMESIMPLE
allowed, all external types refuse by design) ·
signal cycle ⚠️(CycTm only; disconnected from program-driven real cycle by Vissim design) ·
reduced-speed area ✅(XML-injection verb set_reduced_speed_area; reparse readback + live
LoadNet/run oracle — shipped 2026-07-24)

**Runs:** multi-seed simulation with auto-armed evaluation ✅ · post-run lie detection +
KPI summary ✅

**Refusals-with-reason (shipped features):** lane closure · evaluation-config edits ·
shared driving-behavior edits · CC1-as-scalar · agency-restricted calibration params ·
unknown objects · out-of-range values

**V1 close-out:** controller-type parsing ✅ · reduced-speed area ✅ (both 2026-07-24) ·
network-check read verb 🔜 (v2 queue unit ③).

## V2a — more parameters (same machinery, new plumbing + live probing per verb)

time-varying demand profiles (multi-interval inputs) · vehicle composition shares ·
static route relative flows · desired-speed decision PAIRS (set+restore atomic) ·
desired-speed distribution assignment · desired-speed/time DISTRIBUTION curve editing
(the legitimate CC1 route) · conflict-area status (passive↔active) · priority-rule
gap/headway params · driving-behavior params via duplicate-then-edit (expert-gated) ·
dwell-time distributions · signal green-splits within a program (needs blob writer) ·
vehicle type properties (accel/decel curves, lengths, weight/power)

## V2b — structural/physical tier (XML-build + reparse + reference-integrity verification)

lane closure/reopen · full link closure · add/remove/reroute links & connectors ·
link geometry edits (lanes/length/curvature) · turn bans / movement removal ·
signal heads · detectors (placement/length) · stop signs & priority rules ·
measurement infrastructure (travel-time sections, queue counters, data-collection
points, nodes) · parking lots/zones · PT lines/stops/timetables · pedestrian inputs &
areas · new intersection construction (composite)

## V2c — semantic/expert tier (outcome verification + engineer-in-the-loop)

full signal plan redesign (intergreen/conflict-matrix verified) · calibration parameter
search (GEH-scored vs field data) · OD matrix editing + DA machinery (cost/path files,
convergence) · evaluation-config management (results snapshotted first) · scenario
management (.trax create/branch/merge) · agentic compare workflows ("close a lane and
compare") · run video export (presentation)

## Impossible by design (permanent, documented refusals)

External signal-controller logic (RBC/VAP/Econolite) — timing lives in external files
Vissim's own API cannot reach; correct behavior is refusal + "edit in controller
software". Conflict-area add/remove — derived from geometry, no COM verbs.

## Excluded (niche, out of scope)

meso/hybrid settings · managed-lane pricing · rail level crossings · 3D/camera aesthetics
