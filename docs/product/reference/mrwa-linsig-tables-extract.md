# MRWA OMG v2.0 §3 (LinSig) — demand dependency, bonus green, degree of saturation (extract)

Extracted 2026-08-05 from the per-page text dump of `Operational Modelling Guidelines
Version 2.0`. **Doc page = PDF page − 18.** All `p.NNN` citations are **doc pages** (the
number printed as "Page NNN of 271"), matching `mrwa-appendix-a-extract.md`.

**Why this file exists.** Unit 1.6 (SCATS → fixed-time conversion) deferred its
degree-of-saturation and bonus-green items because the LinSig chapter had not been extracted
(recorded in `docs/phase2/DECISIONS.md`). This file carries **Table 3-3 and Table 3-4 in
full**, the paragraphs that define how they are applied, the four demand-dependency methods
and Main Roads' stated preference among them, and the DoS/PRC definitions — so the deferred
1.6 items can be built without reopening the PDF.

**Page map:** Table 3-3 = doc p.105 (PDF 123). Table 3-4 = doc p.107 (PDF 125). Surrounding
material spans doc p.102–113 (PDF 120–131). DoS/PRC = doc p.121–123 (PDF 139–141).

---

## 3.5.15 Demand Dependency — why the adjustment is needed (p.102)

> "**LinSig models a single 'average' cycle in peak hour. However in reality the pedestrian and
> traffic demand may be different in each cycle, operating with different signal phase sequence
> and length in each cycle.**"

> "**When using history files, the number of occurrences of particular phases can be seen from
> the outputs. For phases that are demanded very infrequently, this may mean that the average
> phase time falls below the minimum being used in the model.**"

> "Some of the situations where demand dependency adjustments are required include:
>  infrequent phases (refer to Section 3.5.15.2)
>  where pedestrian demand affects the minimum phase lengths
>  where pedestrian demand affects the pedestrian protection delay to traffic (refer to
> Section 3.5.15.3), and
>  alternative phases e.g. diamond movements (refer to 3.5.15.4)."

> "Some of the criteria for determining a suitable adjustment method include:
>  nature of the infrequent phase and signal group
>  demand frequency in the existing situation
>  demand frequency to be modelled and analysed as set out in the modelling scope
>  signal set up, and
>  scheme assessment and options to be analysed."

> "**As each modelling project and the intersection setup are different, there is not a
> definitive method on making demand dependency adjustments for each case.**"

> "**While the guidelines discuss the common methods and Main Roads' preference, the modeller
> should make the engineering judgement on the most appropriate demand dependency adjustment
> method for each project, discuss the assumptions with Main Roads and document the assumptions
> in the modelling report.**"

> "**Mixing different methods within a modelling project is not recommended. The selection of
> the modelling method should also consider how the intersections will be modelled and optimised
> for the options and future year scenarios. The chosen method should be applied in all scenarios
> and all stages of modelling for consistency.**"

> "**It is recommended that modellers use the Add Notes function in LinSig's Network Layout View
> stating the demand dependency frequencies and the high level modelling method to make it clear
> for other modellers to understand the assumptions made when viewing the LinSig models.**"

---

## 3.5.15.1 Demand Dependency Methods — the four methods and the preference (p.103–104)

> "This section provides the general methodology for each demand dependency adjustment method
> and discusses Main Roads' preference."

### 3.5.15.1.1 Bonus Green Time Method — **Main Roads' preferred method** (p.103)

> "**Main Roads' preferred method for modelling demand dependency is the use of bonus green time
> in Lane Timings View. The general modelling method is:**
> **1. Calculates the total green time for each signal group in the modelled hour.**
> **2. Divides the total green time in step 1 by the number of cycles in the hour.**
> **3. Calculates the bonus green time for each signal group, which is the average green time in
> step 2 subtracts the modelled green time in Signal Timings View.**
> **4. Apply the signal group bonus green time to the relevant lanes in Lanes Timings View.**"

> "**This method has the flexibility of applying different demand dependency frequencies in each
> scenario. The bonus green times need to be estimated for each peak and applied to each scenario
> individually reflecting the different frequencies in each peak.**"

> "**When undertaking signal optimisation, it may be necessary to remove the bonus green time
> prior the optimisation process, but this depends on the purpose of applying bonus green time.**"

**Implementable form of the four steps** (restatement for 1.6, not doc text):
`bonus_green(SG) = ( Σ green(SG) over the modelled hour ÷ n_cycles ) − modelled_green(SG)`
where `modelled_green(SG)` is the green the SG receives in the single modelled cycle in Signal
Timings View, and `n_cycles` is the number of cycles in the hour. Bonus green may be **negative**
(see §3.5.15.3.2 and §3.5.15.4.1). It is applied per **lane**, not per signal group.

### 3.5.15.1.2 Dummy Phases Method (p.103)

> "This method is to add alternative dummy phases in the phase sequence replicating when the
> demand dependent signal group is not activated."

> "The general modelling method is:
> 1. Set up an alternative dummy phase in Phase View, with the same phase letter and signal
> timings as the original phase.
> 2. Activate the same signal groups as the original phase, with exception of the demand
> dependent signal groups which should be deactivated.
> 3. Model the dummy phase, replacing the original phase, in the Phase Sequence."

> "**Main Roads recommends not to modify the original phases as they may be needed for other
> assessments. The original signal setup should match the SCATS setup, therefore dummy phases are
> modelled for ease of checking and to avoid confusion.**"

> "This method may need to be combined with adding bonus green times and / or setting up multiple
> cycle sequences depending on the situation."

### 3.5.15.1.3 Multiple Sequences Method (p.104)

> "This method is used to create a new phase sequence that consist of multiple cycles replicating
> the demand frequency of a particular phase. The general modelling method is:
> 1. Create a new dummy phase as discussed in Section 3.5.15.1.2, if needed.
> 2. Double or triple the cycle time, depending on the frequency being modelled.
> 3. Create a new phase sequence that consists of the multiple cycles replicating the frequency
> of the dummy phase.
> 4. Adjust the phase lengths in Signal Timings View or in Timing Dials.
> 5. Double or triple the cycle time of the coordinated controllers, if modelling in a network
> (refer to Section 3.5.11.1). Select the Double or Triple set up in Signal Timings View of the
> neighbouring sites to avoid creating a multiple cycle sequence as discussed in Section 3.5.8."

> "**This method can be used if the phase sequence is simple and the frequency is straight forward
> such as one-in-two or one-in-three cycles, and is not be suitable for complex demand
> percentage.**"

> "**However, this method is not preferred as it significantly increases the signal optimisation
> and run-time, particularly when the phase sequence is complex or working in a large network. As
> a result manual adjustments to the phase time may be required following the signal
> optimisation.**"

### 3.5.15.1.4 Adjust signal setup — **not recommended** (p.104)

> "This method is used to adjust the signal input parameters proportionally based on the demand
> frequencies. The general modelling method is:
> 1. Estimate the weighted average signal timings based on the demand frequencies.
> 2. Create a new dummy phase as discussed in Section 3.5.15.1.2, if needed.
> 3. Reduce the signal timing inputs in the dummy phase based on the weighted average, such as:
>  pedestrian protection period in traffic signal groups
>  walk time, clearance 1 and clearance 2 time in pedestrian signal groups, and
>  late start, minimum green, early cut-off green, yellow & all red times in phases.
> 4. Model the phase sequence and phase lengths based on the average timings."

> "**While this method is quick to model, it is not recommended by Main Roads. Signal inputs
> should match the SCATS set up to avoid confusion. Moreover, the reduced timings are applied to
> all scenarios in the LinSig files and not specific to each scenario. This becomes problematic
> when using the model file for analysing other scenarios.**"

> "**This method is not generally recommended as the adjusted phase timings are applicable to all
> scenarios in the model and therefore, affect the timings for the other peaks. This method also
> does not provide an assessment for option modelling.**"

---

## 3.5.15.2 Demand Dependency Example: Infrequent Phases — **Table 3-3** (p.105)

> "In the example shown in Figure 3-51, C phase is a demand dependent phase, this phase is
> skipped in some cycles when there is no demand."

`[FIGURE p.105 — Figure 3-51 "Leading right turn example" — not extractable from text; content:
the phase/signal-group diagram of the worked example (phases A, B, C with a leading right turn;
signal groups SG1–SG4). All quantities used downstream are recovered in Tables 3-3 and 3-4.]`

### The four formulae that build Table 3-3 (verbatim, p.105)

> "**The SCATS history file can be used to determine the demand frequencies and to calculate the
> weighted average phase lengths and cycle time, as shown in Table 3-3 using the following
> formulae:**
>  **Total phase time is the sum of all phase lengths in an hour. Refer to Appendix A for
> further information.**
>  **Average phase time = total phase time in an hour ÷ number of occurrences.**
>  **Weighted average phase time = Total phase time in an hour ÷ number of cycles.**
>  **Minimum phase time is the minimum time when the phase is activated in a cycle.**"

> "**In this example, the weighted average phase time for C phase is shorter than minimum phase
> time.**"

### Table 3-3: Infrequent phase calculations (p.105)

| Phase | Demand Frequencies = Number of Occurrences / Number of Cycles | Total Phase Time (seconds) | Actual Average Phase Time (seconds) | Weighted Average Phase Time (seconds) |
|---|---|---|---|---|
| A | 100% = 36 / 36 | 2223 | 62 | 62 |
| B | 100% = 36 / 36 | 1113 | 31 | 31 |
| C | 61% = 22 / 36 | 264 | 12 | 7 |
| **Total** | | **3600** | **105** | **100** |

Arithmetic check (all consistent, no extraction doubt): 2223+1113+264 = 3600 s = one hour.
A: 2223/36 = 61.75 → 62. B: 1113/36 = 30.9 → 31. C actual: 264/22 = 12. C weighted:
264/36 = 7.33 → 7. Totals 62+31+12 = 105 (the *actual*-average cycle) and 62+31+7 = 100 (the
*weighted*-average cycle).

### The rule that follows the table (verbatim, p.105) — the load-bearing sentence

> "**The cycle time of 100 seconds, not 105 seconds, should be modelled replicating the correct
> number of cycles in an hour (36 cycles).**"

i.e. **model the weighted-average cycle, not the sum of actual averages.** The weighted-average
column is the one that reconciles with `3600 ÷ n_cycles`.

### 3.5.15.2.1 Bonus Green Time Method applied to the example (p.106)

> "**In Phase Sequence, the sequence that occurred most of the time should be modelled. In this
> example, A,B,C.**"

> "In Signal Timings View, the estimated phase time if C phase is demanded in modelled, as shown
> in Figure 3-52. In this example:
>  **The demand dependent phase (C phase) runs to its minimum phase length.**
>  **The stretch phase (A phase) length is reduced accordingly.**
>  **The unaffected phase (B phase) length remains the same as the average phase length
> calculated in Table 3-3.**"

> "Figure 3-53 shows an estimated phase split if C phase is not demanded in a cycle. **This figure
> is for demonstration only and does not need to be modelled in LinSig.** In this example:
>  **The unaffected phase (B phase) length remains the same as the average phase length
> calculated in Table 3-3.**
>  **The remaining time is assigned to the stretch phase (A phase).**"

`[FIGURE p.106 — Figure 3-52 "Assumed phase lengths if the infrequent phase is demanded in a
cycle" — not extractable from text; content: the LinSig Signal Timings View phase split for the
demanded case. This is the "modelled green" reference that Table 3-4's bonus-green subtraction
uses — its SG green times are recovered from Table 3-4 row "A,B,C (Figure 3-52)".]`
`[FIGURE p.106 — Figure 3-53 "Assumed phase lengths if the infrequent phase is not demanded in a
cycle" — not extractable from text; content: the phase split for the not-demanded case,
demonstration only. Its SG green times are recovered from Table 3-4 row "A,B (Figure 3-53)".]`

**Note:** although both figures' pictures are lost, **their numeric content survives** — Table
3-4's first two rows are exactly the per-SG green times of Figure 3-52 and Figure 3-53. Nothing
needed for the bonus-green calculation is missing.

---

## 3.5.15.2 (cont.) — **Table 3-4** and the bonus-green definition (p.107)

### How Table 3-4 is built (verbatim, p.107)

> "**The green time for each signal group can then be calculated as summarised in Table 3-4:**
>  **Weighted average green is calculated based on the % activation**
>  **Bonus green time = weighted average green − modelled green (Figure 3-52)**"

> "**It is important to separate SG3 to opposed and unopposed periods as it affects the capacity
> calculations during the give-way period.**"

### Table 3-4: Weighted average signal group green time calculations (p.107)

| Phase Sequence | % activation | SG1 Green Time | SG2 Green Time | SG3 Opposed Green Time | SG3 Unopposed Green Time | SG4 Green Time |
|---|---|---|---|---|---|---|
| A,B,C (Figure 3-52) | 61% | 51 | 63 | 47 | 6 | 25 |
| A,B (Figure 3-53) | 39% | 63 | 63 | 59 | 0 | 25 |
| **Weighted Average Green Time** | | **55.68** | **63** | **51.68** | **3.66** | **25** |
| **Bonus Green Time** | | **+4.68** | **0** | **+4.68** | **-2.34** | **0** |

Arithmetic check (all consistent, no extraction doubt):
- SG1: 0.61·51 + 0.39·63 = 31.11 + 24.57 = **55.68**; bonus = 55.68 − 51 = **+4.68** ✓
- SG2: 0.61·63 + 0.39·63 = **63**; bonus = 63 − 63 = **0** ✓
- SG3 opposed: 0.61·47 + 0.39·59 = 28.67 + 23.01 = **51.68**; bonus = 51.68 − 47 = **+4.68** ✓
- SG3 unopposed: 0.61·6 + 0.39·0 = **3.66**; bonus = 3.66 − 6 = **−2.34** ✓
- SG4: 0.61·25 + 0.39·25 = **25**; bonus = 25 − 25 = **0** ✓

The 61 % is Table 3-3's C-phase demand frequency (22/36); 39 % is its complement. **The bonus is
subtracted against the *demanded-case* (Figure 3-52) green, which is the green actually coded in
Signal Timings View** — this is what "modelled green" means in the formula. Bonus green is
**signed**; SG3-unopposed here is negative.

### Rules that follow the table (verbatim, p.107)

> "**The green time calculation spreadsheet should be included in the modelling submission for
> ease of reference.**"

> "In Lane Timings View, the bonus green times are added to the relevant traffic lanes as
> demonstrated in Figure 3-54."

> "**While this method can also be applied by reducing the B phase time instead of A phase time in
> Signal Timings View, modellers should ensure the SG3 times are calculated separately for the
> opposed and unopposed periods, as it affects the capacity calculations for the filtered
> right-turn movement.**"

`[FIGURE p.107 — Figure 3-54 "Lane Timings View with bonus green time to replicate infrequent
phase" — not extractable from text; content: the LinSig Lane Timings View with the bonus green
values from Table 3-4 entered against lanes. The values themselves are in Table 3-4.]`

### 3.5.15.2.2 Multiple Sequences Method — same example (p.108)

> "**In Phase Sequence, a new sequence with repeated phases is set up based on the demand
> frequency e.g. if the infrequent phase is activated approximately two out of three times, the
> sequence is A,B,C, A,B,C, A,B.**"

> "In Signal Timings View, multiply the cycle time accordingly and manually adjust the phase
> lengths."

### 3.5.15.2.3 Signal Set Up Adjustment Method — same example (p.108)

> "In Phase View, create a new dummy phase duplicating the same phase activations as the phase of
> interest (C phase)."

> "**In Phase View of C phase, reduce the late start, minimum green, early cut off and yellow and
> all red periods based on the activation frequency. For example if the phase is activated 61% of
> the time, the minimum green should be adjusted from the original 6 seconds to 4 seconds (61% x
> 6s = 3.66).**"

> "**This method assumes the activation frequency in all scenarios is the same in all scenarios.**"

*(Note the doc's own rounding here: 61 % × 6 s = 3.66 → stated as "4 seconds", i.e. rounded UP
to the whole second — consistent with the SCATS-fractions round-up rule quoted in
`mrwa-appendix-a-extract.md`. It is a worked example, not a stated rounding rule.)*

---

## 3.5.15.3 Demand Dependency Example: Pedestrian Protection (p.109–110)

> "**In this example, the timed red arrow pedestrian protection period is 10 seconds, as shown in
> Figure 3-55. If the pedestrian crossing is not activated in a particular cycle, the left-turn
> signal group would begin at the start of the phase. Note that in this example, the modelled
> phase length is affected by the crossing activation.**"

`[FIGURE p.109 — Figure 3-55 "Pedestrian protection example" — not extractable from text;
content: the phase diagram showing a 10-second timed-red-arrow protection period delaying the
left-turn signal group. The 10 s value is stated in the prose above.]`

### 3.5.15.3.1 Bonus Green Time Method — **Main Roads' preferred method** (p.109)

> "**This is Main Roads' preferred method as it provides flexibility on modelling a different
> demand frequency in each scenario without adjusting the standard signal setup.**"

> "**In Lane Timings View, a positive bonus green is applied to the relevant traffic lanes to
> replicate the average protection time in an hour. For example, if the crossing is activated 60%
> of the time (i.e. not activated 40% of the time), a positive bonus green time of 4 seconds (40%
> x 10s) is applied to Lane 1/1 to compensate for cycles when the green time begins at the start
> of the phase**, as shown in Figure 3-56."

> "**Different bonus green values can be applied to different peak scenarios depending on the
> demand frequency.**"

`[FIGURE p.109 — Figure 3-56 "Bonus green time for pedestrian protection demand dependency
adjustment" — not extractable from text; content: the Lane Timings View with the +4 s bonus green
on Lane 1/1. The value is in the prose above.]`

**Rule, generalised (for 1.6):** when the model codes the *protected* case (protection always
applied), `bonus_green = (1 − activation_frequency) × protection_period`, **positive**.

### 3.5.15.3.2 Dummy Phase Method (p.110)

> "**This method can be used if crossing activation frequency is assumed to be low (almost 0 per
> cent).**"

> "In Phases View, an alternative dummy phase to A phase is set up without the activation of the
> pedestrian signal group, as shown in Figure 3-57."

> "In Phase Sequence, the dummy phase A1 is used instead of A, i.e. A1,B."

> "**As SG2 green time begins at the start of the phase, it would overestimate capacity if the
> pedestrian crossings are still occasionally activated. In Lane Timings View, a negative bonus
> green time may need to be applied to the relevant traffic lanes to replicate the average
> protection period in an hour. For example, if the pedestrian crossing is activated 20% of the
> time, a negative bonus green time of 2 seconds (20% x 10 seconds protection period) is
> required.**"

`[FIGURE p.110 — Figure 3-57 "Alternative dummy phase without the pedestrian activation" — not
extractable from text; content: the LinSig Phases View showing dummy phase A1 with the pedestrian
SG deactivated.]`

**Rule, generalised (for 1.6):** when the model codes the *unprotected* case (dummy phase),
`bonus_green = − activation_frequency × protection_period`, **negative**. This is the mirror of
§3.5.15.3.1 — sign depends entirely on which case is coded in Signal Timings View.

### 3.5.15.3.3 Multiple Sequences Method (p.110)

> "In Phase View, dummy phase A1 is set up, as shown in Figure 3-57."

> "**In Phase Sequence, a new sequence with repeated phases is set up based on the demand
> frequency, e.g. if the crossing is activated two out of three times, the sequence is A,B, A,B,
> A1,B.**"

> "In Signal Timings View, multiply the cycle time accordingly and manually adjust the phase
> lengths."

### 3.5.15.3.4 Signal Set up Adjustment Method (p.110)

> "**In Edit SGroup window, reduce the Timed Protection Period based on the crossing activation
> frequency. For example if the pedestrian crossing is activated 40% of the time, then 4 seconds
> (40% x 10 seconds) is applied.**"

> "**This method assumes the activation frequency in the same in all scenarios.**"

---

## 3.5.15.4 Demand Dependency Example: Alternative Phases (p.111–112)

> "**Alternative phases is common in Western Australian signal setup. The frequency and the
> duration of the alternative phases can be estimated based on the traffic demand of each
> right-turn or determined from the signal group outputs such as SCATS Event History File.**"

> "In this example:
>  **The total duration of E phase (whether E or E1 or E2) is 18 seconds.**
>  **The westbound right-turn demand is less than the eastbound right-turn demand, therefore in
> some cycles, E1-phase is activated with SG4 terminates earlier and SG1 commences sooner.**
>  **On average, the green time for SG4 is five seconds, which is shorter than the minimum green
> time set out in E phase.**"

> "**Note that for a leading right-turn with filtered right-turn situation, the average
> calculation should also take into account the lost green time due to signal drop off and late
> start of the right-turn arrow. The intergreen time between SG1 and SG4 should be maintained.**"

`[FIGURE p.111 — Figure 3-58 "Alternative phase example and average signal group green time" —
partially extractable; the text layer recovers only the axis scaffolding: title "Average SG length
with alternative phases", column headers "E/E1 phases" and "A phase", a time axis
"0 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21", and row labels SG1, SG2, SG3, SG4.
**The green-time bars themselves — the actual per-SG durations — are graphics and are NOT
recoverable.** Do not infer SG green times for this example.]`

### 3.5.15.4.1 Bonus Green Method — **Main Roads' preferred method** (p.111)

> "**Main Roads' preferred method is to examine the green time for each signal group and calculate
> the weighted average and apply bonus green time to the relevant lanes as shown in Figure 3-59:**"

> "**In Lane Timings View, negative bonus green time is applied to the traffic lanes that get less
> green time due to the early termination.**"

> "**Similarly, positive bonus green time is applied to the traffic lanes that get extra green time
> due to operating the alternative phase.**"

`[FIGURE p.112 — Figure 3-59 "Positive and negative bonus green time added in Lane Timings View" —
not extractable from text; content: the Lane Timings View with both signs of bonus green. Callout
labels survive in the text layer: "Positive bonus green time", "Negative bonus green time". No
numeric values recoverable.]`

### 3.5.15.4.2 Alternative Phases in Phase Sequence (p.112)

> "In Phase Sequence View, a separate phase sequence is set up to include E and E1 phases."

> "In Signal Timings View, the phase lengths are manually adjusted to replicate the average signal
> group green time."

> "**Note that it may be necessary to reduce the minimum green time of the alternate phase in
> Phase View as shown in Figure 3-60, which may cause confusion when reviewing the model.**"

`[FIGURE p.112 — Figure 3-60 "Alternative Phases added in Phase Sequence" — not extractable from
text; content: the Phase Sequence with E,E1. Callout labels survive: "Minimum green time reduced",
"E,E1 phases as part of the phase sequence". No numeric values recoverable.]`

---

## 3.5.16 Demand Dependency for Proposed Modelling (p.113)

> "**Depending on the project requirements, demand dependency should be taken into consideration
> when optimising the signal timings for the option models. While it is usually acceptable to
> consider the demand dependency frequency for the option model when it remains unchanged from the
> existing situation, many factors in the future scenarios, such as land use, pedestrian demand or
> cycle time, could also affect the demand frequency.**"

> "**The following method can be used to consider the potential demand frequency, this is
> particularly useful for a diamond signal arrangement when alternative phases may run:**
> **1. For the alternative phases, reduce the restrictions of minimum green from the existing
> timing to one second.**
> **2. Optimise the signal timings for PRC and note the resultant phase lengths.**
> **3. Re-enter the minimum green as per the site condition.**
> **4. Apply the bonus green time to replicate the traffic lane green time as per point two
> above.**"

---

## Degree of Saturation — validation criterion (§3.10, p.121)

> "**Validating the LinSig base model ensures that the models developed accurately reflect the
> performance of the network or intersection being modelled.** Validated models can then form the
> basis of any future models and should provide a reliable measure of future performance following
> upgrades or changes to the network/intersection."

> "**Validation requirements should be agreed with Main Roads prior to undertaking any data
> collection or modelling. The validation comparisons should include:**
>  **degree of saturation, and**
>  **queue length at the start of green period.**"

### 3.10.1 Degree of Saturation (p.121) — **the deferred 1.6 criterion**

> "**Degree of saturation (DoS) is a LinSig output that represents the ratio of flow to capacity.
> As it is recognised as the most reliable indication of model validity, it is the primary
> validation method.**"

> "**For a base model, if the traffic data is collected using stop line turning counts and the
> signal timings are extracted from the SCATS system, the DoS should not exceed 100 per cent.**"

> "**In addition, modellers should review the magnitude of the modelled DoS is reflective to
> on-site conditions.**"

> "**Main Roads may request on-site recording the DoS at critical intersections to be compared
> with the modelled DoS results for validation purposes.**"

**This is the whole numeric DoS validation rule: `DoS ≤ 100 %`**, conditional on the base model
being built from stop-line turning counts + SCATS-extracted signal timings. There is no
category split and no tolerance band — unlike the Vissim Tables 5-12/5-13.

### 3.10.2 Queue Lengths (p.121)

> "**Queue lengths should be used for validation. The modeller should ensure that the queue at the
> start of the green period on each lane is captured, rather than recording the maximum back of
> the queue.**"

> "**Multiple queue cycles should be recorded from site visits or videos, spread over each peak
> period, to form an average queue representation of the traffic conditions evident over each peak
> period. As a minimum, the queue of each lane on the critical approaches should be recorded
> on-site and compared to the LinSig outputs.**"

> "**The average site queue at the end of the red period / the start of the green period should be
> compared to the Back of Uniform Queue at End of Red output in the Network Results window. The
> values should be similar with the same magnitude between the site measured queue and the LinSig
> output queues.**"

> "**In addition, modellers should review if the modelled queues can fit into the physical
> operating space, particularly at the internal links of interchanges and closely spaced
> intersections.**"

(No numeric queue tolerance — "similar with the same magnitude" is the whole criterion.)

### 3.11.2 Degree of Saturation — scheme-assessment reporting (p.122)

> "**Degree of saturation (DoS) is defined as the ratio of demand flow to the maximum flow which
> can be passed through the intersection from a particular lane. It is used as the primary
> performance indicator for determining the suitability of proposals for Main Roads projects.**
> Refer to Section 3.10.1 for DoS validation in a base model."

> "**For scheme assessments, the highest DoS results of each arm should be reported for the study
> intersections. Modellers should ensure the DoS results satisfy the performance requirements set
> out in Main Roads' Traffic Signal Approval Policy or in the modelling scope.**"

**Note the two different DoS definitions in the same document:** §3.10.1 "ratio of flow to
capacity" (validation) vs §3.11.2 "ratio of demand flow to the maximum flow which can be passed
through the intersection from a particular lane" (assessment). The §3.11.2 form is the per-lane
one. The numeric acceptance level for schemes is **not in this document** — it defers to the
*Traffic Signal Approval Policy* or the project scope.

### 3.11.2.1 Practical Reserve Capacity (p.123)

> "**Practical Reserve Capacity (PRC) is the amount by which traffic at an intersection can
> increase before any link becomes a practical operating level of 90%. It is calculated based on
> the highest degree of saturation in the intersection using the following formulae:**"

> PRC = (90% − Highest DoS) / Highest DoS   **(if highest DoS < 90%)**
> PRC = (90% − Highest DoS) / 90%   **(if highest DoS ≥ 90%)**

> "**For example, if the highest DoS in the network is 80.0%, the PRC is 12.5%.**"

> "**PRC is a function of cycle time and therefore the cycle time should be referenced.**"

> "**The PRC result displayed in the network results is calculated based on the highest DoS of the
> whole network.** This value is used when LinSig undertakes signal optimisation for PRC, refer to
> sections 3.5.12 and 3.5.13 on signal optimisation for further information."

`[EXTRACTION NOTE p.123]` The two formulae are rendered in the dump as
`PRC = \n90%−Highest DoS\nHighest DoS  (if highest DoS <90%)` and
`PRC = \n90%−Highest DoS\n90%  (if highest DoS ≥90%)` — the fraction bar is lost but numerator
and denominator are unambiguous, and the worked example verifies the first branch:
(90 − 80)/80 = 0.125 = **12.5 %** ✓. The second branch is a sign-flipped (negative PRC) form and
has no worked example in the doc.

### 3.11.3 Mean Maximum Queue (p.123) — the PCU→metre conversion

> "**Modellers should review the Mean Maximum Queue (MMQ) outputs from LinSig and consider if the
> modelled queues are realistic and whether it can fit into the physical operating space. If the
> modelled MMQ exceeds the physical lane length, LinSig highlights the MMQ value in red.**"

> "**For Traffic Signal Approval, the highest MMQ output of each intersection is usually required.
> The results should be converted to meters assuming 1 PCU = 7.35 m.**"

> "**Depending on the scheme requirements, the highest MMQ of each lane may need to be reported
> for assessments.**"

> "Occasionally sliver queues may appear at mid-block lanes causing unrealistic MMQ numerical
> outputs when the traffic should be free flowing. Queue graphs can be used to examine the
> formation of the queues, refer to Section 3.11.3.1."

---

## Related: DoS in cycle-time / optimisation decisions (§3.5.12.4.3 and §3.5.13.1)

### 3.5.12.4.3 Degree of Saturation Limit (p.99)

> "**The Apply DegSat Limit (%) option is used to influence the LinSig optimiser to ensure the DoS
> does not exceed this value (where possible) when undertaking signal optimisation. This function
> is particularly useful if the scheme has a specific upper limit DoS on certain lanes, for example
> if the DoS of the major road cannot be greater than 80 per cent in an option model.**"

### 3.5.13.1 Desired Cycle Time Selection (p.100) — the DoS↔PRC worked equivalence

> "**A desired cycle time may not necessarily provide the best results, but rather should be a
> minimum cycle time that achieves the accepted performance requirements.**"

> "**For example, if the performance requirement is to achieve Degree of Saturation (DoS) of 80 per
> cent, i.e. Practical Reserve Capacity (PRC) of 12.5 per cent (refer to Section 3.11.2 for
> definitions), Figure 3-48 shows while 120 second cycle time provides the best overall results,
> 105 second cycle time is sufficient for meeting the performance requirements. The lower cycle
> time should be chosen to avoid unnecessary delay time for traffic and pedestrians.**"

> "**Depending on the scheme requirements, the cycle time selection can be based on fixed intervals
> (for example every five seconds), and not all possible cycle times need to be analysed. This
> should be decided on a case-by-case basis depending on the nature of the analysis and the
> modelling results.**"

`[FIGURE p.99 — Figure 3-48 "Cycle time optimisation" — not extractable from text; content: the
LinSig cycle-time optimisation graph plotting vehicle delay and PRC against a range of cycle
times. The 105 s / 120 s comparison is stated in the prose above; the curve values are not
recoverable.]`

Also, §3.5.13 (p.99): "**Note that the blue value at the top of the graph (cycle time slider)
shows the initial cycle time, not an optimised cycle time.**" and "**Screenshots of the cycle time
optimisation graph should be included in the modelling report to justify the proposed cycle
time.**"

---

## What 1.6 can now build (implementation notes — not doc text)

1. **Bonus green, general form.** `bonus(SG or lane) = weighted_average_green − modelled_green`,
   where `modelled_green` is whatever case is coded in Signal Timings View. **Signed.** Verified
   against Table 3-4 to 2 dp on all five columns.
2. **Weighted average green** = Σ over cases of `p(case) × green(SG, case)`, with `p` from the
   SCATS history demand frequency (Table 3-3's "Number of Occurrences / Number of Cycles").
3. **Cycle time to model** = `3600 s ÷ n_cycles` (the weighted-average cycle), **not** the sum of
   actual average phase times (Table 3-3 note, p.105 — explicit: "100 seconds, not 105 seconds").
4. **Weighted average phase time** = total phase time in the hour ÷ **number of cycles**;
   **actual average phase time** = total ÷ **number of occurrences**. These differ for any phase
   with frequency < 100 %; using the wrong one is the classic error this section exists to prevent.
5. **Opposed/unopposed split is mandatory** for a filtered right-turn SG ("It is important to
   separate SG3 to opposed and unopposed periods as it affects the capacity calculations during
   the give-way period", p.107) — a single bonus figure for such an SG is wrong by construction.
6. **Pedestrian protection sign rule.** Protected case coded → `+(1−f)·protection`. Unprotected
   (dummy-phase) case coded → `−f·protection`. Both worked with a 10 s protection period.
7. **Method selection.** Bonus green is Main Roads' preferred method in all three worked examples
   (infrequent phase, ped protection, alternative phases). Multiple-sequences = allowed but "not
   preferred". Signal-setup-adjustment = "not recommended by Main Roads". Do not mix methods
   within a project. A refusal path is justified for the signal-setup-adjustment method.
8. **DoS validation** = `DoS ≤ 100 %` for a base model built from stop-line counts + SCATS
   timings (§3.10.1, p.121). No banding, no category split.
9. **PRC** = `(90% − maxDoS)/maxDoS` if maxDoS < 90 %, else `(90% − maxDoS)/90%`. Always report
   the cycle time alongside.
10. **MMQ → metres**: `1 PCU = 7.35 m` (§3.11.3, p.123).
11. **Deliverable obligation:** "The green time calculation spreadsheet should be included in the
    modelling submission" (p.107) — an export artefact 1.6 should be able to produce.

---

## INVENTORY

**Subsections covered:** 3.5.12.4.3 (p.99), 3.5.13 + 3.5.13.1 (p.99–100), 3.5.15 (p.102),
3.5.15.1 with .1.1/.1.2/.1.3/.1.4 (p.103–104), 3.5.15.2 with .2.1/.2.2/.2.3 (p.105–108),
3.5.15.3 with .3.1/.3.2/.3.3/.3.4 (p.109–110), 3.5.15.4 with .4.1/.4.2 (p.111–112), 3.5.16
(p.113), 3.10 + 3.10.1 + 3.10.2 (p.121), 3.11.2 + 3.11.2.1 (p.122–123), 3.11.3 + 3.11.3.1
(p.123). **= 27 subsections.** (§3.5.14 Offsets, p.100–101, is adjacent and was read but is
outside this extract's scope — it covers LinSig offset locking and reference-phase reordering.)

**Tables transcribed: 2 of 2** — Table 3-3 (p.105) and Table 3-4 (p.107), both in full, both
arithmetic-verified against the doc's own worked numbers. These are the only tables in the
covered range.

**Figures skipped: 10** — Figure 3-48 (p.99), 3-51 (p.105), 3-52 (p.106), 3-53 (p.106), 3-54
(p.107), 3-55 (p.109), 3-56 (p.109), 3-57 (p.110), 3-59 (p.112), 3-60 (p.112). **Two more are
partially recovered:** Figure 3-58 (p.111 — axis/labels only, bars lost) and the callout labels
of 3-59/3-60. Figures 3-49/3-50 (p.100–101, offset locking) are outside scope.

**Figures whose loss matters:** only **Figure 3-58 (p.111)** — the alternative-phase example's
per-SG green times are graphics-only and are NOT recoverable, so that example cannot be
numerically reproduced. Every number needed for the *infrequent-phase* worked example
(Figures 3-52/3-53) survives inside Table 3-4, and the *pedestrian-protection* example's numbers
(10 s protection, 60/40 %, 20 %, 40 %) are all in the prose. **The two deferred 1.6 items —
degree of saturation and bonus green — are fully specified by this extract; nothing they need
is lost to a figure.**

**Uncertain extractions: 1**
- `[EXTRACTION NOTE p.123]` PRC formulae — fraction bars lost in the text layer; numerator and
  denominator unambiguous and the first branch verified against the doc's own worked example
  (80 % DoS → 12.5 % PRC). Not treated as a full uncertainty flag for that reason.

**Doc defects noted:** §3.5.15.3.4 (p.110) "This method assumes the activation frequency in the
same in all scenarios" (missing word); §3.5.15.2.3 (p.108) "the activation frequency in all
scenarios is the same in all scenarios" (duplicated clause). Neither affects a number.
