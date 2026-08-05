# Data Adapter Contract — the format-neutral import layer

**Roadmap item 0.7 (Track D). Status: contract + neutral schema + quality gate shipped;
SCATS/WA adapter is a mapping stub; Daejeon adapter (1.2) waits on TK's files.**

Code: `backend/services/dataimport/` — `schema.py` (neutral model), `base.py` (adapter
protocol + result transport), `quality.py` (R2.5 gate), `adapters/scats_wa.py` (adapter #2,
stub tier). Tests: `backend/tests/test_dataimport_{schema,base,quality,scats_wa}.py`.

> **한 줄 요약.** 코어는 포맷 중립 내부 모델이고, 지역 포맷은 전부 플러그인 어댑터다
> (R1.3). 어댑터의 유일한 절대 규칙: **원본에 없는 값을 지어내지 않는다.** 없으면 `None`,
> 이유는 `absent`에, 사람이 볼 설명은 `ImportIssue`에. Vissim이 조용히 틀린 숫자를
> 돌려주는 것을 막는 검증 계층을, 한 단계 위(데이터 반입)에서 똑같이 적용한 것.

This document is written for **whoever writes adapter #3** — a region we have not seen,
in a format we have not seen. If something here is only true for SCATS or only true for
Korea, that is a bug in the contract, not a fact about the world.

---

## 0. Why the contract exists before the data does

The product goal is "any site, any country's data format" (TK requirements v2.1). That
goal is not achieved by writing two parsers; it is achieved by the *quality of the
contract between them*. Korea/Daejeon is adapter #1, WA/SCATS is adapter #2, and the test
of the design is that #3 needs a new module and nothing else.

We wrote the contract against what we **know** (MRWA Appendix A, in hand, field-level) and
we are explicit about what we are **guessing** (the Daejeon 스마트교차로 export, unseen —
see §7). Appendix A was used as a falsification test, not as a template: three schema
decisions were wrong and Appendix A proved it (§5).

---

## 1. The one rule everything else follows from

> **An adapter must never invent a value.**

Vissim's defining failure mode is that it accepts a bad edit, runs, and reports confident
wrong numbers. Prism's answer is a validation layer that manufactures the error signal
Vissim never emits. Data import is the same failure mode one stage earlier: a source that
does not state a yellow time, an adapter that helpfully computes one from a formula whose
inputs it does not have, and a model that is now wrong in a way no downstream check can
detect — because by the time the number reaches Vissim it looks exactly like a measurement.

So: **if the source did not say it, the field is `None`.** Three mechanisms make that
absence useful rather than merely blank:

| Mechanism | Where | For whom |
|---|---|---|
| `None` field value | any neutral record | code — `None` means "not in the source", never zero, never default |
| `record.absent[field] = reason` | `NeutralRecord.absent` | machines + auditors — *why* it is missing, enforced truthful |
| `ImportIssue` | `ImportResult.issues` | humans — a reviewable finding with severity, code and citation |

`absent` is validated: you cannot claim a field is absent while it holds a value, and you
cannot name a field that does not exist. A lie about missing data fails at parse time.

---

## 2. The neutral domain model (`schema.py`)

### 2.1 Provenance — every record cites its source

```python
Provenance(source_file="phase_history.csv", locator="row 42", native_id="A")
Provenance(source_file="phase_history.csv", derived_by="MRWA Appendix-A A.2 avg cycle")
```

Required on **every** record. Granularity is **per record, not per batch**: a batch-level
"these came from file X" cannot answer "which row said the cycle was 100 seconds?", and
that question gets asked in report review months later.

Enforced rule: a record that was **read** must set `locator` and/or `native_id`; a record
that was **computed** must set `derived_by` naming the algorithm *and its authority*. A
provenance that says neither is rejected. `Provenance` is frozen — a citation cannot be
rewritten by a later stage.

`source_file` is the name the import job knows the input by, never a machine-specific
absolute path, so provenance survives the job directory.

### 2.2 Record inventory

| Model | Identity | Notes |
|---|---|---|
| `SiteMetadata` | `site_id`, `source_system` | site + extraction facts; the only place `drive_side` and `coordinate_reference` live |
| `SignalControllerData` | `controller_id` | cycle **bounds**; owns programs / groups / detectors / ped phases |
| `SignalProgramData` | `program_id`, `source_kind` | one plan or one observation window; carries **both** views (§2.4) |
| `PhaseData` | `phase_id` | SCATS-native phase with durations, frequency, stretch flag |
| `SignalGroupWindow` | `signal_group_id` | Vissim-native green; position and length are independent (§5) |
| `SignalGroupData` | `signal_group_id` | phase membership, late start / early cut-off, movements |
| `PedestrianPhaseData` | `pedestrian_phase_id` | walk / clearance-1 / clearance-2 / delay / protection / demand frequency |
| `DetectorData` | `detector_id` | controller + channel, function, position *hint* |
| `TurningCountData` | (site, approach, movement, class, interval) | the 15-min shape R1.4 consumes |
| `DetectorCountData` | (site, detector, interval) | loop volumes — **no movement** (§5) |

Container lists (`programs`, `phases`, `detectors`, …) default to `[]` because a partial
import is normal. Semantic lists where empty and unknown differ (`phase_ids`, `movements`,
`offsets_s`) are `list[X] | None`: `[]` means the source says *none*, `None` means the
source never said.

### 2.3 What is Optional, and why

Everything except identity and the few fields whose absence would make the record
meaningless:

| Required | Why it cannot be Optional |
|---|---|
| every `*_id` | a record you cannot name is a record you cannot cite or join |
| `provenance` | the audit trail is the point |
| `SignalProgramData.source_kind` | a declared plan and an averaged observation are different objects with different error bars — see below |
| `TurningCountData.volume` | a count with no count is not a count |
| `TurningCountData.volume_unit` | **no default.** A PCU-weighted figure silently read as a vehicle count is a wrong number nobody would ever notice |
| `TurningCountData.interval_start/end` | an interval stored as "start + assumed 15 min" cannot represent a 5-minute source honestly |
| `SiteMetadata.source_system` | "which system produced this" is the first question of every data dispute |

`source_kind` deserves its own paragraph. `DECLARED` = the source states these timings as
configuration. `OBSERVED_HISTORY` = these are observations of what an adaptive controller
actually did. `DERIVED` = we computed them. A fixed-time program declared by a controller
and a fixed-time program averaged out of six weeks of SCATS history will both run in
Vissim without a murmur; the flag is what stops a report calling the second one the first.

Everything else is Optional *because a real source may legitimately lack it*, and the list
of what a source may lack is not knowable in advance — which is exactly the problem this
layer exists to absorb.

### 2.4 Two views of a signal program, neither derived from the other

- **Phase view** (`SignalProgramData.phases`) — SCATS-native: a sequence of phases with
  durations. What Phase History gives you.
- **Signal-group window view** (`SignalProgramData.signal_group_windows`) — Vissim-native:
  per-group green windows inside the cycle. What Vissim ultimately needs.

Sources supply one, the other, or both. **The adapter never synthesises one from the
other.** Converting the phase view into a Vissim fixed-time plan is roadmap 1.6's
SCATS→fixed-time module, which does it with the full A.2 treatment (frequency-adjusted
phase times, coordinated-site cycle recount per Fig A-5) and records `derived_by`. An
adapter doing half of that quietly would be the worst of both worlds.

### 2.5 Enum vs free string

The rule: **enum where the domain is closed *in the domain*, not merely closed in the
region in front of you.** Every normalised enum is paired with a verbatim string so an
unmappable token survives.

| Closed → enum | Open → enum + raw string | Never an enum |
|---|---|---|
| `TurnType`, `CompassDirection`, `DriveSide` | `ControlType` + `control_type_raw` | approach / leg names |
| `IssueSeverity`, `ProgramSourceKind`, `CoordinationPoint` | `PedestrianProtectionType` + `protection_type_raw` | street names, plan descriptions |
| `VolumeUnit`, `SignalGroupKind`, `DetectorFunction` | `VehicleClassRef.scheme` + `code` + optional `canonical` | subsystem ids, time-of-day labels |

Three calls worth defending:

1. **No `UNKNOWN` member anywhere.** "The source did not say" is `None`. An `UNKNOWN`
   enum member is an invented value wearing a modest hat.
2. **`TurnType` is geometric, not semantic.** WA drives on the left, so the opposed turn
   is the right turn; Korea drives on the right, so it is the left turn. If the enum
   encoded "the opposed turn", every neutral record would mean something different per
   country and cross-region code would be silently wrong. Side of road is stated once, on
   `SiteMetadata.drive_side`, and consumers combine the two.
3. **Vehicle class is a *reference*, not an enum.** Austroads 12-class and Korea's
   12-class scheme are different taxonomies; mapping class 7 to a Korean code is a
   research question, not an import step. `VehicleClassRef` keeps `scheme` + `code`
   losslessly and fills `canonical` only with the coarse buckets that *are* safely
   mappable (`LIGHT_VEHICLE`, `HEAVY_VEHICLE`, `BUS`, `MOTORCYCLE`, `BICYCLE`,
   `PEDESTRIAN`, `ALL_VEHICLES`). Unsure ⇒ leave `canonical` `None`; the gate reports
   reduced class coverage rather than the adapter guessing.

`PedestrianProtectionType`'s six members come from Appendix-A A.3 and are expected to gain
members as regions land. An adapter that cannot map its source's arrangement leaves the
enum `None`, keeps the raw string, and emits an `INFO`. It must not pick the closest.

### 2.6 Regional constants are not inventions

The SCATS adapter sets `drive_side=LEFT` even though no file says so. That is legitimate:
it is a documented, citable fact about the region the adapter serves, declared by the
adapter and reviewable in one place. Inventing is *filling a per-record field the source
did not carry*. Keep the distinction sharp, and keep regional constants few, named, and in
the adapter's own module — never scattered through parsing code as fallback defaults.

---

## 3. The adapter protocol (`base.py`)

```python
class DataAdapter(ABC):
    info: AdapterInfo                                   # name, region, source_system, version
    def sniff(self, files: Sequence[Path]) -> SniffResult:   ...
    def parse(self, files: Sequence[Path]) -> ImportResult:  ...
```

**MUST**

- Return an `ImportResult` from `parse()` **always** — including when nothing usable was
  found. A parse that found nothing returns with `REFUSE` issues explaining why.
- Report every *expected* data problem as an `ImportIssue`. Missing tab, malformed row,
  unmappable code, contradictory values: all issues, never exceptions.
- Attach `Provenance` to every record, and `derived_by` to every computed value.
- Record material absences in `record.absent` alongside the issue.
- Bump `info.version` whenever the field mapping changes. Reports cite it.
- Make `sniff()` cheap, read-only, and non-raising. "Not mine" is confidence `0.0`.

**MUST NOT**

- Invent, default, interpolate, round-to-plausible, or "helpfully" derive a value whose
  inputs are not present. This includes: substituting a site id from a file name;
  computing yellow/all-red from Austroads formulae without speed and grade; assuming
  15-minute bins because most sources use them; assuming vehicles when the unit is unstated.
  (The site-id clause is SCATS-conditional: a SCATS drop carries a TCS number a file name
  would *override*. Where a source verifiably states no identifier anywhere — the Daejeon
  drop — the sanctioned fallback is file-name id + provenance + WARN
  `daejeon_site_id_from_file_name` + a `site_ids` override, with
  `site_id_from_file_name=False` restoring strict refusal. Recorded in
  `docs/phase2/DECISIONS.md`, 2026-08-05.)
- Raise for a data problem. `AdapterError` is for genuinely unreadable inputs and bugs
  only. (A Pydantic `ValidationError` escaping `parse()` is the same violation wearing a
  different hat — catch it and report a `REFUSE`. `scats_wa.py` does exactly this where a
  source can violate a cross-field invariant.)
- Re-bin, re-scale, re-number or otherwise normalise the *numbers*. Normalise the
  *shape*; preserve the values. MRWA §5 is explicit that signal-group numbering must match
  SCATS — the general rule is that the source's identifiers are the source's.
- Register itself at import time. Registration is explicit in
  `services/dataimport/__init__.py` so a half-finished adapter cannot start winning sniffs.

**`ImportIssue`** carries `severity` / `code` (stable, snake_case, asserted on by tests) /
`message` (English for the reviewer) / `messages` (the `{lang: text}` i18n hook, same shape
as `constraints.Finding`) / `subject` / `provenance` / `citation` / `context` (numeric
evidence). Rules with no written authority leave `citation` `None` rather than borrowing one.

**Selection.** `select_adapter(files)` sniffs every registered adapter and returns the
highest scorer at or above `SNIFF_CONFIDENCE_THRESHOLD` (0.5), plus all scores. Below the
threshold it returns `None` — telling the user which formats we recognise beats picking
the least-bad option, because a wrong adapter produces confidently mis-mapped fields. An
adapter whose `sniff` raises is scored `0.0` and the failure recorded, so one broken
adapter cannot break intake.

---

## 4. The quality gate (`quality.py`) — R2.5

MRWA's collection standards, expressed as checks over **neutral records only**. The gate
never sees a file format, so it protects adapter #3 exactly as well as adapter #1.

### 4.1 Refuse vs warn — the argument

Two tiers, one rule.

**REFUSE** when either

- **(a) the data contradicts itself**, so any number derived from it is wrong — overlapping
  intervals double-count; a stated approach total that disagrees with its own movement
  rows; a signal group pointing at a phase no program declares; phase durations that do not
  add up to the program's own stated cycle; or
- **(b) it fails an explicit numeric minimum stated by the governing authority** — the ≥3 h
  peak coverage rule being the one we hold today.

**WARN** when the data is coherent and meets the minimums but is **unrepresentative,
unusual, or missing a recommended property** — a Monday instead of a neutral Tue–Thu,
5-minute or hourly bins instead of the required 15, holes inside the collection period,
totals-only class coverage, an implausible flow rate.

**INFO** carries no judgement: what was parsed, what was absent and why, and — critically —
*what we could not check*.

Why this line and not "how bad does it feel": (a) and (b) are exactly the two cases where
proceeding silently yields a model that is **wrong** rather than merely **thin**. A Monday
count is a real count of a real Monday; the modeller may knowingly use it and write down
why. An overlapping interval is not a real anything. Refusing the second and warning about
the first keeps refusals credible — a gate that refuses everything gets switched off, and
then it protects nobody.

A REFUSE **blocks by default and is overridable only by a recorded human decision**, the
same doctrine as an edit refusal: the override is a logged act with a reason, not a flag.

Two distinct uses of REFUSE coexist and are compatible:

- **gate-level REFUSE** — the (a)/(b) rule above.
- **adapter-level REFUSE** — "this input cannot be turned into neutral records without
  guessing" (no site identity; an empty required structure). Same severity, same blocking
  behaviour, narrower question.

### 4.2 The checks

| id | tiers | what it catches | citation |
|---|---|---|---|
| `counts_present` | INFO | signal-only import; says the demand checks were skipped | — |
| `interval_overlap` | REFUSE | double-counted intervals in one movement series | op p.59; meso p.62 |
| `interval_regularity` | WARN | non-15-min bins, mixed bin lengths, gaps ("not zero demand") | op p.59; meso p.62 |
| `peak_coverage` | REFUSE + INFO | longest contiguous block per site-day < 3 h; INFO that we cannot verify the block *is* the peak | MRWA collection minimums † |
| `neutral_day` | WARN + INFO | survey outside Tue–Thu; INFO that holiday/school-term status is unverifiable | MRWA collection minimums † |
| `minimum_observations` | WARN | a movement series thinner than 3 h at 15 min (12 intervals) | MRWA collection minimums † |
| `approach_total_consistency` | REFUSE + WARN | stated approach total ≠ sum of its movements (REFUSE beyond tolerance, WARN inside) | — |
| `class_total_consistency` | REFUSE + WARN | stated all-class total (합계) ≠ sum of its per-class parts in the same movement/interval bucket — the class-axis complement of `approach_total_consistency` (REFUSE beyond max(2 veh, 2%), WARN inside; silent when only a total or only parts exist) | — |
| `class_coverage` | WARN + INFO | totals-only counts cannot seed composition; unmapped class codes reported, not guessed | op p.19 |
| `volume_sanity` | WARN | flow rate above a deliberately generous band (decimal-point / unit slips) | — |
| `detector_count_intervals` | REFUSE + WARN | same overlap/gap rules for loop series | op p.59; meso p.62 |
| `signal_reference_integrity` | REFUSE | dangling group→phase, window→group, detector→group, walk→group | op §5 signal coding |
| `program_cycle_consistency` | REFUSE + WARN + INFO | phase durations vs stated cycle (WARN inside the SCATS round-up band); INFO when the program is incomplete | Appendix-A A.2 p.11 |
| `controller_completeness` | WARN | controller with no timings; observed history with no observation window | Appendix-A A.2 |

† **Honest citation.** The ≥3 h peak / ≥6 travel-time observations / ≥10 queue samples /
neutral-day minimums reached us via `docs/meeting-brief-tk-mrwa.md` §4, not from a page we
can point at. The citation strings say "page cite pending" rather than inventing a page
number — the never-invent rule applies to our own authority claims first.

Thresholds live in `QualityThresholds` and are overridable per jurisdiction; a region
whose authority states different minimums changes numbers, not code. `run_quality_gate()`
returns findings without mutating; `apply_quality_gate()` appends them and flips
`result.ok`.

A meta-test (`test_no_check_emits_a_severity_it_did_not_declare`) pins the tier column of
the table above against the code, so this document cannot drift from `QUALITY_CHECKS`.

### 4.3 Not yet covered

R2.5 also names ≥6 travel-time observations and ≥10 queue samples per hour. The neutral
model carries no travel-time or queue observation records today, so those checks do not
exist rather than existing and quietly passing. They arrive with the calibration
observation store (2.1), as `TravelTimeObservation` / `QueueObservation` records plus two
minimum-count checks — the shape is a direct copy of `minimum_observations`.

---

## 5. What Appendix A changed — the falsification result

The SCATS field map (`SCATS_FIELD_MAP`, 38 entries) exists to be *checked*: every entry
declares a dotted path into `schema.py`, and
`test_every_documented_appendix_a_field_lands_on_a_real_schema_field` resolves all of them
against the real models. Writing that map broke the first schema three times. All three
were fixed in the schema, not papered over in the adapter:

1. **`DetectorCountData` did not exist.** Appendix-A A.1 lists "Detector Volume Data"
   among what Main Roads supplies. A loop volume has **no turning movement** — a stop-line
   detector counts what crossed it, not where it went. Forcing those records into
   `TurningCountData` would have required every adapter, in every region, to invent an
   approach and a movement. A new record type was the only honest answer, and `ImportResult`
   grew a `detector_counts` list beside `counts`.
2. **`PhaseData.frequency` was capped at 1.0.** Appendix-A A.4's repeat right-turn phasing
   runs a phase **twice per cycle**, so A.2's f_i = occurrences ÷ cycles genuinely exceeds
   1. The cap would have forced an adapter to clamp — i.e. to report a wrong number to
   satisfy a validator. Cap removed (same for `PedestrianPhaseData.demand_frequency`).
3. **`SignalGroupWindow` could only express a *positioned* green.** Event History gives
   `SGi=On`/`SGi=Off` wall-clock timestamps: the green's **length** falls straight out, its
   **position in the cycle** does not (that needs the stretch-phase timeline from A.2). The
   model could say "green from 12 s to 42 s" but had no way to say "green lasted 30 s,
   position unknown" — so an adapter would have had to fabricate a start. Added
   `green_duration_s` alongside `start_s`/`end_s`.

A fourth finding needed no schema change but is worth recording: **Appendix A supplies no
turning counts at all.** Main Roads provides detector volumes; movement-level demand comes
from a separate turning-count survey. The adapter says so in an INFO rather than leaving a
reviewer to wonder why `counts` is empty.

---

## 6. Worked example — SCATS/WA (`adapters/scats_wa.py`)

Adapter #2 at **stub tier**: it maps every Appendix-A structure and parses a CSV subset in
the shapes Appendix A describes (the real deliverables are .xlsx workbooks, .csv event logs
and .doc charts; reading .xlsx tabs and drawings is deferred with the rest of adapter #2 to
Phase 4). Unparsed sources still appear in the field map with `parsed_by_stub=False`, so
the gap is documented rather than forgotten.

### 6.1 Mapping highlights

| Appendix-A source | field | neutral path |
|---|---|---|
| Site tab | TCS number | `SignalControllerData.controller_id` (+ `SiteMetadata.site_id`) |
| Timings tab | late start / early cut-off | `SignalGroupData.late_start_s` / `.early_cut_off_s` |
| Timings tab | yellow / all-red / min / max green | `PhaseData.amber_s` / `.all_red_s` / `.min_green_s` / `.max_green_s` |
| Timings tab | ped delay / walk / clearance / protection | `PedestrianPhaseData.delay_s` / `.walk_s` / `.clearance_1_s` / `.protection_type` |
| Link & Offset Plans | subsystem / `a,b^c` / cycle bounds | `…coordination.subsystem` / `.coordinated_phase_id` + `.coordination_point` / `SignalControllerData.cycle_min_s`,`cycle_max_s` |
| Phase History | phase, duration, start/end | `SignalProgramData(OBSERVED_HISTORY).phases.{phase_id, observed_total_duration_s, observation_count}` + `.observation_window` |
| Phase History (A.2 derived) | stretch phase, f_i, n cycles, c̄ | `.is_stretch_phase`, `.frequency`, `.observed_cycle_count`, `.cycle_time_s` |
| Event History | `SGi=On/Off` | `SignalGroupWindow.green_duration_s` (+ `observation_count`) |
| Event History | `Walk j:Demand=Off Active=On` | `PedestrianPhaseData.demand_frequency` |
| Detector Volume Data | volume per interval | `DetectorCountData.volume` |
| Phase Sequence Chart / Strategic Monitor / LMA | — | mapped, `parsed_by_stub=False` |

### 6.2 The refusals, and what each one prevents

| Missing input | What a careless adapter would do | What this one does |
|---|---|---|
| Site tab | derive a site id from the file name | `REFUSE scats_site_identity_unavailable` |
| Site tab present, no TCS number | use the row index | `REFUSE scats_tcs_number_missing` |
| Timings tab | compute yellow from A.3's Austroads formula | `WARN intergreen_split_unavailable`; `amber_s`/`all_red_s` stay `None` with `absent` reasons naming the inputs it lacks (approach speed, grade, clearance distance) |
| Phase History | assume one ped demand per cycle | `WARN ped_demand_frequency_not_computable`; `demand_frequency` `None` |
| two phases tied for most frequent | pick the alphabetically first as the stretch phase | `WARN stretch_phase_ambiguous`; cycle count, f_i and c̄ all left unset |
| Timings tab states two yellows for one phase | take the first | `WARN timings_value_conflict`; field `None` with "will not choose between them" |
| cycle min > cycle max | swap them | `REFUSE cycle_bounds_contradictory`; neither bound recorded |
| protection type we do not recognise | map to the nearest of the six | `INFO`; enum `None`, raw string kept |

What it *does* compute, it labels: the average cycle c̄ = total duration ÷ n_cycles carries
`Provenance.derived_by = "MRWA Appendix-A A.2 …"`, and an INFO says the full A.2 conversion
to a fixed-time plan belongs to roadmap 1.6.

---

## 7. Unknowns — what adapter #1 (Daejeon) may still need

We have not seen a Daejeon 스마트교차로 file. Everything below is a known gap, not a
prediction, and each one is a *contract* question rather than a parsing question.

1. **Vehicle classification scheme.** `ClassificationScheme` has a `KOREA_12_CLASS`
   member placed on the strength of the national survey convention, unverified against a
   real export. If Daejeon's classes do not match it, add a member — do **not** reuse
   `AUSTROADS` codes. Whether any Korean class maps onto `CanonicalVehicleClass` is a
   question for whoever holds the file, and `None` is the correct answer until then.
   **Answered 2026-08-05 (Daejeon adapter):** the real drop is a **6**-class scheme —
   new member `KOREA_SMART_INTERSECTION_6`, kept separate from `KOREA_12_CLASS` (their
   correspondence stays open question 4 to the client); 대형/중형→HEAVY, 버스→BUS,
   승용→LIGHT, `화물_소형` stays `canonical=None` for the gate to report.
2. **Turning counts vs approach counts.** 스마트교차로 devices are movement-aware in
   principle. If an export gives approach totals only, `MovementRef.is_approach_total`
   carries it and R1.4's routing will need a separate source — the gate will say so.
   **Answered 2026-08-05:** the drop is fully movement-aware including U-turns; per-class
   movements plus a 합계 class total (emitted, gate-checked by `class_total_consistency`)
   and a 소계 *time* subtotal (cross-checked then discarded at the adapter).
3. **Interval length.** If Korean data arrives in 5-minute bins, that is a WARN, not an
   error, and re-binning is the *consumer's* explicit decision, never the adapter's.
   **Answered 2026-08-05:** 15-minute bins throughout; a declared-vs-observed bin-length
   contradiction is a REFUSE, an inferred length is a WARN.
4. **Control-type vocabulary.** 정주기 / 감응 / 스마트 map plausibly onto `FIXED_TIME` /
   `VEHICLE_ACTUATED` / (?). The third is genuinely uncertain — `control_type_raw` holds
   the token until someone who knows decides.
5. **Signal group and phase numbering.** Korean controllers commonly use the 8-SG
   convention (`signal_parser.py` already encodes N/E/S/W = 1–8). Whether the export's
   numbering matches the .inpx's is a *joining* problem the neutral model does not solve;
   it will need an explicit, reviewable cross-reference, not a heuristic.
6. **Pedestrian protection.** Appendix-A A.3's six arrangements are WA's. Korea's are
   likely different; expect `PedestrianProtectionType` to gain members and expect the
   first Daejeon import to emit `INFO` for every walk until it does.
7. **Coordinate reference.** Korean data may carry EPSG:5186/5179 coordinates; WA data
   carries none. `SiteMetadata.coordinate_reference` is the single place it goes, and an
   adapter that finds coordinates without a stated CRS must leave the CRS `None` rather
   than assuming.
   **Answered 2026-08-05:** the count export carries no coordinates and states no CRS —
   recorded as an absence, CRS stays `None`.
8. **Time zone and DST.** Interval timestamps are stored as given. If a source's local
   times are ambiguous, `SiteMetadata.timezone` is the only place to say so — and if the
   source does not state one, it stays `None`.
   **Answered 2026-08-05:** the source states no timezone; it stays `None` with an
   `absent` reason (`drive_side=RIGHT` *is* declared as a regional constant per the §2.6
   precedent — the two clauses conflict for timezone and the more conservative one wins).
9. **Licensing / export restrictions on the source files** (open question 1 in the TK
   requirements). Provenance records file names; if a jurisdiction forbids retaining source
   identifiers, that is a contract change, not an adapter change.
10. **Multi-site drops.** `ImportResult` holds lists of sites and controllers, but the
    SCATS stub only exercises the single-site path. A Korean city-wide export will be the
    first real multi-site test; `site_id` linkage is already in the model
    (`SignalControllerData.site_id`), untested at scale.

---

## 8. Checklist for adapter #3

1. New module under `services/dataimport/adapters/`. Nothing outside it knows your format.
2. Declare `AdapterInfo` (name, region, source_system, version).
3. Write the **field map first**, as data, with a dotted neutral path per source field, and
   a test that resolves every path via `schema.neutral_field_path_exists`. If a path does
   not exist, stop: either you are mapping to the wrong field, or the schema is missing a
   concept and *the schema changes* (see §5 for three precedents).
4. Implement `sniff()` — cheap, read-only, never raises, honest confidence.
5. Implement `parse()` — always returns; issues not exceptions; provenance on everything;
   `absent` on every material gap.
6. Write the missing-input tests **before** the happy path. For each input your format can
   lack, assert the adapter produces an issue and a `None`, not a number.
7. Register in `services/dataimport/__init__.py`.
8. Run the gate over your output and read the INFO issues — they tell you what your format
   does not carry.

---

## 9. Related

- `docs/product/tk-requirements-2026-07-31.md` — R1.3 (adapter architecture), R1.4 (demand
  seeding), R2.5 (data-quality gate)
- `docs/product/master-roadmap-2026-07.md` — item 0.7, Track D, dependency graph
- `docs/product/reference/mrwa-appendix-a-extract.md` — the WA/SCATS field-level ground truth
- `docs/product/reference/mrwa-appendix-b-extract.md` — saturation flow (PCU factors, out of
  scope for import; belongs to the 0.3 constraint pack)
- `docs/meeting-brief-tk-mrwa.md` §4 — the collection minimums whose page cites are pending
- `backend/services/agent/constraints.py` — the downstream validation layer this one mirrors
