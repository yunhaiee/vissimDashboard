# MRWA OMG v2.0 §5 "Vissim Guidelines" — verbatim extract (doc p.179–221)

Extracted 2026-08-05 from the per-page text dump of `Operational Modelling Guidelines
Version 2.0` (271 doc pages / 289 PDF pages). **Doc page = PDF page − 18.** All `p.NNN`
citations below are **doc pages** (the number printed as "Page NNN of 271"), matching the
convention of `mrwa-appendix-a-extract.md`.

**Section boundary determined by reading:** §5 starts doc p.179 (PDF 197) and ends doc
p.221 (PDF 239, last subsection §5.8.1.4). §6 "Aimsun Guidelines" starts doc p.222 (PDF 240).

This is the source-of-truth reference for: the OSM network generator (roadmap 2.3) via
§5.2/§5.3, intersection-control verbs via §5.4, and the base-model validation thresholds via
§5.6.3 (Tables 5-12/5-13/5-14). Quotes are verbatim; the text dump's stray intra-word spaces
(e.g. "rou nd up") have been closed where the word is unambiguous, and nothing else altered.

---

## 5.1 Introduction (p.179)

> "Vissim is a mesoscopic and microscopic multi-modal traffic flow simulation software that
> is generally used as a tool to simulate real-world transport systems."

> "This section is designed to complement the PTV Vissim User Manual. It provides detail on
> key parameters to be adopted when undertaking Vissim mesoscopic, microscopic and hybrid
> simulation modelling in Western Australia."

### 5.1.1 Appropriate Use of Vissim (p.179)

> "Vissim can assess the performance of any transport networks, however, it is typically data
> and labour intensive to achieve accurate modelling of the complex and adaptive operations,
> compared to micro-analytical software such as LinSig and SIDRA. It is therefore recommended
> to use Vissim when evaluating the performance of detailed operations that exhibit:"

Bulleted list (verbatim): "Network-wide traffic capacity implications when part of the
network becomes over-saturated." / "Closely spaced intersections or interchanges." /
"Advanced traffic management schemes (for example priority signals, congestion management)."
/ "Complex intersection or interchange layouts." / "Complex or dynamic (non-cyclic or
demand-based) signal operations." / "Feasibility studies for high occupancy vehicle and high
occupancy toll lanes." / "Impact analysis of infrastructure design such as highway corridor
improvement." / "Impact of heavy vehicles to network capacity." / "Evaluation of variable
speed policies and other intelligent transportation systems." / "A high level of pedestrian
interaction."

### 5.1.2 Software Version (p.179)

> "This guideline is based on version 20 of Vissim. While the Operational Modelling Guidelines
> will need to be updated for future versions of the software, the general principles outlined
> will continue to apply."

---

## 5.2 Model Base Data (p.180–190)

> "The base data for simulation models should be defined prior to building the models. The
> defined base data includes network settings, model input time periods, various vehicle
> types, vehicle classes and driving behaviours." (§5.2, p.180)

### 5.2.1 Network Settings

#### 5.2.1.1 Vehicle Behaviour (p.180)

> "**Left-hand traffic should be adopted for Traffic regulations.**"

> "If the model includes network components where link gradients will play an important part
> (for example the impacts of heavy vehicle performance), these should be included in the
> Vehicle Behaviour tab in the Network Settings window (as shown in Figure 5-1). The modeller
> can either use the gradient attribute as a direct input or use Z-coordinates with the model
> calculating the gradient based on link lengths:"

> " Gradient attribute – is recommended where the number of locations is minimal and
> gradients/elevations are unlikely to change across models.
>  Z-coordinate approach – is more appropriate for use across a larger network or for
> networks with significant elevation changes."

`[FIGURE p.180 — Figure 5-1 "Recommended vehicle behaviours" — not extractable from text;
content: screenshot of the Vissim Network Settings > Vehicle Behaviour tab showing the
recommended settings.]`

#### 5.2.1.2 Units (p.181)

> "In the Units tab (as shown in Figure 5-2), **the metric system should be adopted.**"

`[FIGURE p.181 — Figure 5-2 "Recommended units" — not extractable from text; content:
screenshot of the Vissim Network Settings > Units tab.]`

### 5.2.2 Time Period Settings (p.181)

> "The modelling should not only include the simulation assessment period, but also the
> warm-up and cool-down periods. The warm-up period is the time allowed for the traffic demands
> and queues to reflect a realistic level of congestion as observed on-site at the beginning of
> the analysis period. The cool-down period is the time that allows vehicles to complete their
> trips and replicate the congestion observed at end of the analysis period."

> "The duration of the warm-up and cool-down periods should be sufficient for the traffic
> volumes and queues in the network to reflect the traffic conditions observed on-site.
> **Depending on the model network size, a minimum of two times of the longest travel time or
> 15 minutes (whichever is greater) should be defined for each of the warm-up and cool-down
> periods.** The duration for the warm-up and cool-down periods should be agreed with Main
> Roads."

`[FIGURE p.181 — Figure 5-3 "Vehicle input time intervals" — not extractable from text;
content: per the surrounding text, "time intervals defined for a static assignment model for
a one hour assessment period, and 15 minute warm-up and cool-down periods".]`

### 5.2.3 Vehicle Types (p.182)

> "Traffic surveys should contain information on the vehicle compositions to be modelled.
> Vehicles in Western Australia can be classified using the Austroads Vehicle Classification
> System. The model should include vehicle types which are critical to the performance of the
> network or are a primary component of the project's objectives. Figure 5-1 shows the
> breakdown of the recommended minimum vehicle types to be defined in the model."

> "For projects that include the evaluation of public transport facilities, schemes or
> operation efficiencies, the Vissim default vehicle types for public transport can be used."

**Table 5-1: Recommended vehicle types** (p.182)

| Vehicle type | Axles | Axle groups | Length |
|---|---|---|---|
| 1 Short | *[see note]* | 1 or 2 | Short (up to 5.5 m) |
| 2 Short-towing | 3, 4 or 5 | 3 | Medium (5.5 m to 14.5 m) |
| 3 Two axle truck | 2 | 2 | Medium (5.5 m to 14.5 m) |
| 4 Three axle truck | 3 | 2 | Medium (5.5 m to 14.5 m) |
| 5 Four axle truck | > 3 | 2 | Medium (5.5 m to 14.5 m) |
| 6 Three axle articulated | 3 | 3 | Long (11.5 m to 19.0 m) |
| 7 Four axle articulated | 4 | > 2 | Long (11.5 m to 19.0 m) |
| 8 Five axle articulated | 5 | > 2 | Long (11.5 m to 19.0 m) |
| 9 Six axle articulated | ≥6 | > 2 | Long (11.5 m to 19.0 m) |
| 10 B-double | >6 | 4 | Medium combination (17.5 m to 36.5 m) |
| 11 Double road train | >6 | 5 or 6 | Medium combination (17.5 m to 36.5 m) |
| 12 Triple road train | >6 | > 6 | Large combination (over 33.0 m) |

`[EXTRACTION UNCERTAIN p.182]` Row "1 Short" is short one value in the dump — the raw
fragment is `1 Short  1 or 2 Short  \n(up to 5.5 m)`, i.e. only ONE numeric value between the
type name and the Length group where every other row has two. Reading it as
Axles = *(missing)* / Axle groups = "1 or 2" matches the Austroads classification (Class 1 =
2 axles, 1–2 axle groups) but the Axles value is **not present in the source text** — do not
assume "2". Also, the Length column is a merged/spanning column in the original: the dump
gives the group label only on the first row of each span (rows 2/6/10/12); the repetitions
above are the transcriber's expansion of that span, not separate printed cells.
`[EXTRACTION UNCERTAIN p.182]` "6≥" and "6>" appear literally in the dump for rows 9–12;
transcribed above as "≥6" / ">6" (glyph-order artifact of the text extraction).

> **Note (doc-internal cross-reference error):** the body text says "Figure 5-1 shows the
> breakdown of the recommended minimum vehicle types" but the object that follows is
> **Table 5-1**. The same Figure/Table mix-up recurs at §5.2.3.2, §5.2.3.3, §5.2.3.4, §5.2.4,
> §5.2.5.1, §5.3.5, §5.6.2.2 and §5.8.1.2 — see the "Doc defects found" block at the end.

#### 5.2.3.1 2D/3D Model (p.183)

> "For all vehicle types, 2D and 3D models should be defined based on the vehicle model
> profiles and connected with the corresponding 2D and 3D model distributions and vehicle
> types."

`[FIGURE p.183 — Figure 5-4 "2D and 3D models defined in Vissim" — not extractable from text;
content: an example of 2D/3D models and model distributions defined in a Vissim model.]`

#### 5.2.3.2 Acceleration and Deceleration Attributes (p.183–184)

> "In the base model build, the default desired and maximum deceleration attributes can be
> adopted as a starting point for the vehicle types. For the desired heavy vehicle acceleration
> the values in Figure 5-2 should be adopted."  *(→ means Table 5-2)*

> "The desired acceleration values were estimated by the Main Roads' Operational Modelling and
> Visualisation team, based on
>  acceleration rates and power usages from Typical maximum acceleration rates for
> tractor-semitrailer combination trucks documented in the ITE traffic engineering handbook
> (Pline, 1999), and
>  power and mass ranges stipulated in Heavy Vehicle Dynamics and Microsimulation Modelling
> (by Graham Jacoby, MRWA)."

**Table 5-2: Desired acceleration attributes for heavy vehicle types** (p.184)
Speed axis X (km/h): 0, 32, 40, 48, 56, 64, 72, 80, 88, 96, 120. Values in m/s².

| Vehicle Type | series | 0 | 32 | 40 | 48 | 56 | 64 | 72 | 80 | 88 | 96 | 120 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 3 | yMin | 0.664 | 0.664 | 0.597 | 0.530 | 0.419 | 0.309 | 0.278 | 0.247 | 0.172 | 0.098 | 0.000 |
| 3 | Y | 1.053 | 1.053 | 1.041 | 1.029 | 0.809 | 0.588 | 0.556 | 0.524 | 0.283 | 0.043 | 0.000 |
| 3 | yMax | 1.930 | 1.930 | 1.930 | 1.930 | 1.587 | 1.243 | 1.112 | 0.980 | 0.506 | 0.031 | 0.000 |
| 4 | yMin | 0.552 | 0.552 | 0.470 | 0.387 | 0.308 | 0.230 | 0.199 | 0.167 | 0.140 | 0.113 | 0.000 |
| 4 | Y | 0.761 | 0.761 | 0.708 | 0.655 | 0.517 | 0.379 | 0.347 | 0.316 | 0.200 | 0.084 | 0.000 |
| 4 | yMax | 1.004 | 1.004 | 0.986 | 0.967 | 0.760 | 0.553 | 0.521 | 0.489 | 0.269 | 0.050 | 0.000 |
| 5 | yMin | 0.482 | 0.482 | 0.390 | 0.298 | 0.240 | 0.181 | 0.148 | 0.115 | 0.114 | 0.112 | 0.000 |
| 5 | Y | 0.618 | 0.618 | 0.544 | 0.471 | 0.374 | 0.277 | 0.245 | 0.214 | 0.159 | 0.104 | 0.000 |
| 5 | yMax | 0.761 | 0.761 | 0.708 | 0.655 | 0.517 | 0.379 | 0.347 | 0.316 | 0.200 | 0.084 | 0.000 |
| 6 | yMin | 0.599 | 0.599 | 0.523 | 0.446 | 0.355 | 0.263 | 0.232 | 0.200 | 0.154 | 0.107 | 0.000 |
| 6 | Y | 0.761 | 0.761 | 0.708 | 0.655 | 0.517 | 0.379 | 0.347 | 0.316 | 0.200 | 0.084 | 0.000 |
| 6 | yMax | 1.819 | 1.819 | 1.819 | 1.819 | 1.489 | 1.160 | 1.042 | 0.925 | 0.478 | 0.031 | 0.000 |
| 7 | yMin | 0.499 | 0.499 | 0.409 | 0.318 | 0.255 | 0.191 | 0.16 | 0.129 | 0.125 | 0.121 | 0.000 |
| 7 | Y | 0.761 | 0.761 | 0.708 | 0.655 | 0.517 | 0.379 | 0.347 | 0.316 | 0.200 | 0.084 | 0.000 |
| 7 | yMax | 1.819 | 1.819 | 1.819 | 1.819 | 1.489 | 1.160 | 1.042 | 0.925 | 0.478 | 0.031 | 0.000 |
| 8 | yMin | 0.491 | 0.491 | 0.399 | 0.308 | 0.247 | 0.186 | 0.154 | 0.123 | 0.123 | 0.122 | 0.000 |
| 8 | Y | 0.664 | 0.664 | 0.597 | 0.530 | 0.419 | 0.309 | 0.278 | 0.247 | 0.172 | 0.098 | 0.000 |
| 8 | yMax | 0.831 | 0.831 | 0.787 | 0.744 | 0.586 | 0.429 | 0.397 | 0.365 | 0.220 | 0.074 | 0.000 |
| 9 | yMin | 0.501 | 0.501 | 0.411 | 0.321 | 0.257 | 0.193 | 0.162 | 0.130 | 0.126 | 0.121 | 0.000 |
| 9 | Y | 0.649 | 0.649 | 0.580 | 0.510 | 0.405 | 0.299 | 0.267 | 0.236 | 0.168 | 0.100 | 0.000 |
| 9 | yMax | 0.883 | 0.883 | 0.847 | 0.811 | 0.638 | 0.466 | 0.434 | 0.403 | 0.235 | 0.067 | 0.000 |
| 10 | yMin | 0.409 | 0.409 | 0.317 | 0.225 | 0.191 | 0.157 | 0.100 | 0.043 | 0.017 | 0.000 | 0.000 |
| 10 | Y | 0.518 | 0.518 | 0.430 | 0.342 | 0.274 | 0.205 | 0.174 | 0.143 | 0.130 | 0.118 | 0.000 |
| 10 | yMax | 0.717 | 0.717 | 0.657 | 0.598 | 0.473 | 0.347 | 0.316 | 0.284 | 0.187 | 0.090 | 0.000 |
| 11 | yMin | 0.368 | 0.368 | 0.276 | 0.185 | 0.126 | 0.068 | 0.007 | 0.000 | 0.000 | 0.000 | 0.000 |
| 11 | Y | 0.409 | 0.409 | 0.317 | 0.225 | 0.191 | 0.157 | 0.100 | 0.043 | 0.017 | 0.000 | 0.000 |
| 11 | yMax | 0.622 | 0.622 | 0.549 | 0.476 | 0.378 | 0.279 | 0.248 | 0.217 | 0.160 | 0.104 | 0.000 |
| 12 | yMin | 0.346 | 0.346 | 0.254 | 0.162 | 0.082 | 0.002 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| 12 | Y | 0.464 | 0.464 | 0.372 | 0.280 | 0.228 | 0.175 | 0.136 | 0.097 | 0.090 | 0.082 | 0.000 |
| 12 | yMax | 0.582 | 0.582 | 0.503 | 0.425 | 0.338 | 0.251 | 0.219 | 0.188 | 0.149 | 0.109 | 0.000 |

Transcription notes (not uncertainty flags — the row/column counts are self-consistent at 11
values per row):
- The speed axis jumps 96 → 120 with no 104/112 point. The lower points are mph conversions
  (32≈20, 48≈30, 64≈40, 80≈50, 96≈60 mph); 120 is the terminal anchor where all series = 0.
- Vehicle types 1, 2 (light vehicles) are **not** in this table — §5.2.3.2 scopes it to
  "the desired heavy vehicle acceleration".
- Type 7 at 72 km/h is printed "0.16" (2 dp) where every neighbour is 3 dp — transcribed as
  printed.

#### 5.2.3.3 Power and Weight Distributions (p.185)

> "In the base model, the default power and weight distributions can be adopted for vehicle
> types including cars, buses, coaches, motorcycles and bicycles. In addition, appropriate
> power and weight distributions should be identified and defined for the heavy vehicle types."

> "Figure 5-3 shows recommended power and weight distributions for heavy vehicle types that
> should be adopted in the base model and referenced at the calibration stage."  *(→ Table 5-3)*

**Table 5-3: Recommended Power and Weight Distributions for Heavy Vehicles** (p.185)

| Name | Power distribution (kW) | Weight distribution (t) — Minimum | Medium | Maximum |
|---|---|---|---|---|
| 240 hp | 180 | 6.0 | 9.7 | 25.7 |
| 300 hp | 225 | 7.5 | 12.2 | 32.1 |
| 400 hp | 300 | 10.0 | 16.2 | 42.9 |
| 500 hp | 375 | 12.5 | 20.3 | 53.6 |
| 600 hp | 450 | 15.0 | 24.3 | 64.3 |

#### 5.2.3.4 Colour Distributions (p.185)

> "In the base model, colour distributions should be defined and adopted for the vehicle types.
> Different colours for each vehicle type enables modellers and model auditors to check the
> model's efficiently by easily identifying different vehicle type while running Vissim
> simulation."

> "Figure 5-4 demonstrates the recommended colour distributions that are categorized based on
> vehicle classes stipulated in Section 5.2.4. When the scope of modelling is to assess bus
> operations, the modellers should define a separate vehicle type and colour distribution for
> buses accordingly. **The recommended colour for buses is green.**"  *(→ Table 5-4)*

**Table 5-4: Recommended colour distribution** (p.185)

| Name | Colour (RGB) | Share (%) | Vehicle Type |
|---|---|---|---|
| 1 Short | Aqua (0,255,255) | 100% | 1 Short, 2 Short-towing |
| 2 Medium | Lime Green (182,255,0) | 100% | 3 Two axle truck, 4 Three axle truck, 5 Four axle truck |
| 3 Long | Pink (255,127,237) | 100% | 6 Three axle articulated, 7 Four axle articulated, 8 Five axle articulated, 9 Six axle articulated |
| 4 Medium combination | Orange (255,106,0) | 100% | 10 B-double, 11 Double road train |
| 5 Large combination | Red (255,0,0) | 100% | 12 Triple road train |
| 6 Bus | Green (38,127,0) | 100% | 30 Bus |

#### 5.2.3.5 Vehicle Type Attributes (p.186)

> "Figure 5-5 shows an example of the recommended vehicle types and selected attributes in a
> Vissim model."

`[FIGURE p.186 — Figure 5-5 "Recommended Attributes for Vehicle Types" — not extractable from
text; content: a Vissim vehicle-types list with the recommended attribute columns filled in.
No attribute values are recoverable from the dump.]`

### 5.2.4 Vehicle Classes (p.186)

> "The defined vehicle types should be grouped into vehicle classes based on vehicle
> characteristics such as vehicle length and turning speed. The recommended vehicle classes for
> the vehicle types defined (based on the Austroads Vehicle Classification System) are set out
> in Figure 5-5. The vehicle classes are grouped together based on vehicle length."  *(→ Table 5-5)*

**Table 5-5: Recommended vehicle classes** (p.186)

| Vehicle class | Vehicle types |
|---|---|
| 1 Short | 1 Short, 2 Short-towing |
| 2 Medium | 3 Two axle truck, 4 Three axle truck, 5 Four axle truck |
| 3 Long | 6 Three axle articulated, 7 Four axle articulated, 8 Five axle articulated, 9 Six axle articulated |
| 4 Medium combination | 10 B-double, 11 Double road train |
| 5 Large combination | 12 Triple road train |

> "When the project area includes specific vehicle facilities (for example an exclusive bus
> lane, bicycle lane, pedestrian footpaths), appropriate vehicle types and classes should be
> defined to reflect the vehicle movements associated with user characteristics."

**Consistency note (not an extraction error):** Table 5-5 puts vehicle type **2 Short-towing**
in vehicle *class* "1 Short", whereas Table 5-1 assigns type 2 the *length* group "Medium
(5.5 m to 14.5 m)". Table 5-4 follows Table 5-5. Class grouping ≠ Austroads length grouping.

### 5.2.5 Driving Behaviours (p.187–190)

> "Various link types represent the different roadway classes in Western Australia, such as
> urban roads, freeways and merging areas."

#### 5.2.5.1 General (p.187–189)

> "Driving behaviours should be defined for each of the link types:
>  Urban – for all urban roads including collectors, distributors and arterial networks.
>  Freeway – for use on motorways with no controls on lane choice.
>  Merging areas – the modeller should define additional driving behaviours and link type to
> reflect the merging behaviour observed on-site."

> "**Default driving behaviours should generally be adopted, with the exception of car
> following model parameters, and waiting time before diffusion to reflect observed driving
> behaviours.**"

> "Car following model parameters to be updated are:
>  **Standstill distance – 2.5 metres.**
>  Additive part of safety distance and Multiplic. part of safety distance values should be
> determined based on measured straight movement saturation flow at key intersection(s).
> Figure 5-6 demonstrates recommended values for the parameters."  *(→ Table 5-6)*

**Table 5-6: Recommended car following model parameters** (p.187)

| Average saturation flow (pcu) | Additive part of safety distance | Multiplic. part of safety distance |
|---|---|---|
| 1,630 | 2.75 | 3.75 |
| 1,780 | 2.50 | 3.50 |
| 1,950 | 2.25 | 3.25 |
| 2,050 | 2.00 | 3.00 |
| 2,100 | 1.75 | 2.75 |
| 2,200 | 1.50 | 2.50 |

> "The recommended car following parameter values are based on the saturation flow chart
> stipulated in the PTV Vissim 2020 User Manual."

`[FIGURE p.188 — Figure 5-6 "Car following model parameters for urban driving behaviours" —
not extractable from text; content: the Vissim Driving Behaviour > Car Following tab with the
parameters that must be amended highlighted.]`

> "**Waiting time before diffusion in the urban driving behaviour parameters should be
> increased to the maximum observed signal cycle time to avoid waiting vehicles being removed
> during simulation.**" (p.188)

> "Driving behaviour for a merging area should be defined based on the updated urban
> (motorized) car following model parameters, with changes made to the lane-change and the
> lateral parameters
>  Average standstill distance (2.5 m)
>  Updated additive part of safety distance and multiplic. part of safety distance
>  Safety distance reduction factor – 0.25 to 0.45
>  Maximum deceleration for cooperative braking – from -5.00 m/s2 to -9.00 m/s2
>  Cooperative lane change to be ticked
>  Observe adjacent lane(s) to be ticked
>  Minimum lateral distance standing at 0 km/h – 0.1 m to 0.4 m, and
>  Minimum lateral distance driving at 50 km/h – 0.2 m to 1.0 m." (p.188)

> "The factors should be determined based on driving behaviours observed on-site." (p.188)

`[FIGURE p.189 — Figure 5-7 "Driving Behaviour (merging) - Lane Change and Lateral tabs" —
not extractable from text; content: the two Vissim tabs with the merging parameters
highlighted. The numeric ranges are already given in the bullet list above.]`

> "For specific locations where the observed driving behaviours are different from the
> simulation, modellers can adjust the driving behaviour parameters to better reflect the
> observed behaviours. This should be documented in the modelling report." (p.189)

> "At locations where vehicles on adjacent lanes are observed to be affected by the lateral
> clearance between vehicles, the modeller should apply appropriate lateral driving behaviour
> settings for the relevant links and connectors." (p.189)

> "**The number of driving behaviour types should be minimised, and the modeller should ensure
> that all similar driving behaviour types are consolidated unless they need to be modelled
> separately.**" (p.189)

#### 5.2.5.2 Display Types (p.190)

> "Display types can be used when specifying the representation of network objects in a Vissim
> model. Using display types for different network object types (e.g. links with different link
> behaviour types), modellers and model auditors can review Vissim models efficiently by easily
> recognising network object types. **Therefore, it is recommended that modellers define
> different display types and use them representing different link behaviour or lane types.**"

`[FIGURE p.190 — Figure 5-8 "Recommended display types" — not extractable from text; content:
a table/screenshot of recommended display types for link behaviour and lane types. The
specific colour-to-behaviour mapping is NOT recoverable from the dump.]`

#### 5.2.5.3 Desired Speed Profile (p.190)

> "**Default speed profiles (linear) should be adopted for all speed limits up to and including
> 60 km/h. For speed limits of 70 km/h or greater, the default values should be adjusted to
> align with prevailing driver behaviour.**"

> "**Modellers should consult Main Roads for the speed distributions for roads with speed
> limits of 70km/h or greater prior to commencing modelling.**"

---

## 5.3 Network Data (p.191–197)

> "Network data is used when building transport networks in Vissim. This section outlines
> recommended ways for Western Australian transport network development in Vissim." (p.191)

### 5.3.1 Background Information (p.191)

> "Main Roads recommends uploading background images for the network coding and verification.
> Modellers can also import network data from existing Visum and Vissim models."

#### 5.3.1.1 Background Image (p.191)

> "When developing the existing network the modeller should ensure up-to-date background aerial
> imagery is used. Changes to the existing network that may not be reflected in the background
> imagery **must** be incorporated into the network. Scaling of the background imagery should be
> undertaken using the project areas' coordinates and dimensions. It is the modeller's
> responsibility to ensure that the aerial imagery coordinate and proposed design coordinate
> systems are aligned."

#### 5.3.1.2 Import Network Data from VISUM (p.191)

> "Whilst a VISUM network can be exported to create a Vissim network, the modeller should
> review and refine the network outputs and all associated network parameters and objects."

(Footnote 22, p.191: "PTV VISUM software is used to model transport networks and travel demand,
to analyse expected traffic flows, to plan public transport services and to develop advanced
transport strategies and solutions.")

#### 5.3.1.3 Import Network Data from Other Vissim Models (p.191)

> "If the model is comprised of any elements from another model, all parameters should be
> checked to ensure there is consistency across all network objects including the base data. The
> details of the duplicated model components and the process should be provided in the modelling
> report."

### 5.3.2 Model Boundaries (p.191–192)

> "To help confirm the model time period and boundaries, site visits should be undertaken in the
> relevant peak periods to confirm the geographic extent of congestion within the project area.
> This should be done prior to undertaking data collection exercises."

> "The model boundary should also consider the location of adjacent intersections surrounding
> key intersections. The inclusion of upstream and downstream intersections to control arrival
> and departure rates is often necessary."

#### 5.3.2.1 Upstream Congestion (p.192)

> "**Links should be long enough so that all queuing traffic is within the model boundary.**
> Vehicles queuing beyond the boundary may affect the number of vehicles within the model and
> the performance of the network modelled. As a result of certain existing traffic conditions,
> the back of queue may not always be contained. Where the road link extension is considered to
> be impractical, the modeller should report the un-captured condition as a limitation of the
> developed model. **If the links have been extended and no longer represent the modelled
> network, visualisation and evaluation should be turned off in the Link Display and Others tabs
> respectively** (as shown in Figure 5-9)."

`[FIGURE p.192 — Figure 5-9 "Link Display and Others tabs" — not extractable from text;
content: the two Vissim link-property tabs showing where visualisation and evaluation are
turned off.]`

#### 5.3.2.2 Downstream Congestion (p.192)

> "Downstream congestion such as exit blocking, which may exist beyond the boundary of the
> initial modelling area, and has an impact on traffic in the project area, should be included
> in the final modelling area to reflect the existing traffic conditions on-site. The modeller
> should illustrate the induced delays and congestion through the calibration and validation
> process."

### 5.3.3 Links and Connectors (p.193–196)

> "Links and connectors are used for roads and paths that vehicles and pedestrians can move on.
> When coding links and connectors, it is recommended that:
>  The number of links and connectors be minimised.
>  Overlapping between links and connectors be minimised.
>  **Lane gains and lane drops (refer to Section 5.3.3.2) should be modelled using one
> connector** to ensure merging and diverging movements are smoother and occur within the link.
> This will also minimise the number of alternative paths." (p.193)

#### 5.3.3.1 Connector Route Parameters (p.193)

> "Connectors have attributes and options that affect vehicle behaviour in model simulation.
> Among the connector's attributes emergency stop and lane change parameters are used to model
> the lane change role of vehicles that follow their route or path."

> "Modellers should adjust the parameters to better reflect the driving behaviour and lane
> discipline observed on-site, however, **500 meters is recommended for the value of the lane
> change parameter at the diverging section, as a starting point.**"

> "**Emergency stop position of a turning lane connector should be at least the length of
> approach lane's solid line.**"

`[FIGURE p.193 — Figure 5-10 "Emergency stop parameter update" — not extractable from text;
content: an example of updated emergency stop and lane change parameters on a connector.]`

#### 5.3.3.2 Merging Section Coding (p.194–195)

> "For merging, modellers should code the sections in detail to better reflect the on-site
> observed behaviour, capacity and queue space."

> "**The lane change distance parameter of the downstream connector should be updated based on
> the vehicle lane change behaviour and upstream lane utilisation observed on site. The
> predefined behaviour type of merging (refer to Section 5.2.5.1) is selected for the merging
> connector.**"

`[FIGURE p.194 — Figure 5-11 "Merging section – behaviour type and lane change distance" —
not extractable from text; content: per the surrounding text, "the link coding, lane change
distance parameter, and link behaviour type adopted for a two-lane section merging into one
lane". Table 5-7 below transcribes the accompanying attribute values.]`

> "The merging area in Figure 5-11 consists of multiple links and connectors. Table 5-7 shows
> the links and attributes adopted for the merging section." (p.195)

**Table 5-7: Merging section links** (p.195)

| | Link 19 | Link 10009 | Link 18 | Link 10010 | Link 17 |
|---|---|---|---|---|---|
| Lane 1 width | 3.5 m | 3.5 m | 3.5 m | 3.5 m | 3.5 m |
| Lane 2 width | - | - | 0.5 m | 0.5 to 3.5 m | 3.5 m |
| Link behaviour type | 1 Urban (motorized) | 1 Urban (motorized) | 1 Urban (motorized) | 6 Urban (merging) | 1 Urban (motorized) |
| Lane change distance | - | Merging distance | - | 500 m | - |

(Header row in the source is "Link number  19  10009  18  10010  17"; links 10009/10010 are
connectors by Vissim's numbering convention.)

#### 5.3.3.3 Diverging Section Coding (p.195)

> "The intersection approach diverging areas should be coded in a similar manner with merging
> area link coding when the exit lane(s) is shared by multiple movements, and lane utilisation
> is critical to the approach's capacity, as shown in Figure 5-12."

> "**When exit lanes are dedicated to a movement, separated link coding for the pocket lane is
> recommended** as shown in Figure 5-13."

`[FIGURE p.195 — Figure 5-12 "Diverging section link coding for shared movements lane" — not
extractable from text; content: link/connector layout for a diverging area whose exit lane is
shared by multiple movements.]`
`[FIGURE p.195 — Figure 5-13 "Diverging section link coding for dedicated movement lane" — not
extractable from text; content: link/connector layout using a separate link for a dedicated
pocket lane.]`

#### 5.3.3.4 Dedicated Bus and Heavy Vehicle Lane (p.196)

> "**Where heavy vehicles are observed to only use a specific lane(s), for example corners with
> tight radii, lane closures should be used to ensure correct lane use.**"

> "**To enable interaction between buses and general traffic, bus lanes should be coded as one
> lane of a link using lane closures rather than as a separate link.** Bus lanes modelled using
> a separate link will not allow interaction between vehicles, especially at merge points or
> locations where general traffic must cross the bus lane to turn left (as shown in Figure
> 5-14)."

> "Along with the default link behaviour types, any additional link behaviour types with
> adjusted parameters can be defined in the model to reflect the traffic behaviours observed
> on-site. **Additional link behaviour types and associated driving behaviour must be described
> and explained in the modelling report. User-defined driving behaviour links should be coded in
> a different display colour.**"

`[FIGURE p.196 — Figure 5-14 "Bus lane coding" — not extractable from text; content: bus-lane
coding shown as a lane of a link with lane closures vs. a separate link.]`

#### 5.3.3.5 Pedestrian Crossings (p.196)

> "**Where pedestrian crossings are observed to impact the performance of the network (for
> example intersections in the CBD), Use as pedestrian area (as shown in Figure 5-15) should be
> selected and conflict areas between pedestrian and vehicular links should be defined to
> simulate the interaction between vehicle and pedestrian movements.**"

`[FIGURE p.196 — Figure 5-15 "Link for pedestrian crossing" — not extractable from text;
content: the Vissim link dialog with the "Use as pedestrian area" option selected.]`

**This is the whole of §5.3.3.5** — the guideline gives no crossing width, no crossing-link
length, and no ped-speed default here. (Ped walk/clearance timing lives in Appendix A, not §5.)

### 5.3.4 Desired Speed Decisions (p.197)

> "**Desired speed decision markers should be set where vehicles are observed to change speed.
> This is generally at posted speed limit sign locations. Modellers should generally follow the
> speed limit published in the road information mapping system available from Main Roads'
> website.**"

### 5.3.5 Reduced Speed Areas (p.197)

> "**Reduced speed areas should be set at any location at which the road geometry prevents
> traffic from travelling at the posted speed safely (for example, tight bends, all turning
> movements or areas of poor visibility).** In the base model build, the vehicle speed
> distributions for different turn types defined in Figure 5-8 can be adopted as a starting
> point."  *(→ Table 5-8)*

**Table 5-8: Vehicle reduced speed range** (p.197)

| Turn type / Radius (Approx.) | Typical example | Short – desired speed | HV – desired speed |
|---|---|---|---|
| Very tight turn — 12.5 m | Turns entering/leaving car parks, driveways or other narrow roads/lanes | 20 km/h (20-25 km/h) | 5 km/h (4-6 km/h) |
| Tight turn — 15.0 m | Most left-turn operations and small single lane roundabouts | 25 km/h (25-30 km/h) | 12 km/h (12-15 km/h) |
| Moderate turn — 20.0 m | Most right-turn operations and medium-sized roundabouts | 30 km/h (30-35 km/h) | 15 km/h (15-20 km/h) |
| Gentle turn — 30.0 m | Large multi-lane intersections and large (multi-lane) roundabouts | 40 km/h (40-45 km/h) | 25 km/h (25-30 km/h) |

(The bracketed value is the distribution range; the leading value is the desired speed.
"Short" = vehicle class 1; "HV" = heavy vehicles. Note WA is left-hand traffic, so "left-turn"
is the near-side/tight turn and "right-turn" is the across-traffic turn.)

> "**When defining reduced speed areas approximately two to five metre areas are generally
> recommended, considering that vehicles decelerate to match driving speed with the defined
> reduced speed.**"

> "**Default speed distribution profiles should be used unless the desired speed range does not
> match the default distribution, and site observations show that these are not valid for the
> specific location modelled. Any change in the default speed distributions should be justified
> in the modelling report.**"

`[FIGURE p.197 — Figure 5-16 "Reduced speed areas coding" — not extractable from text;
content: an example of reduced speed areas defined at an intersection.]`

---

## 5.4 Intersection Control Data (p.198–203)

> "Intersections can be categorised into two types that are priority controlled and traffic
> signal controlled intersections. This section discusses the recommended network objects and
> parameters for each intersection type in Vissim." (p.198)

### 5.4.1 Priority Controlled Intersections (p.198–200)

> "**Priority controlled intersections can be managed by conflict areas or priority rules. In
> general, conflict areas are recommended to be used, however, for complex intersections
> priority rules should be used.** When using conflict areas or priority rules, gap acceptance
> parameters in Table 5-9 should be considered as a starting point." (p.198)

**Table 5-9: Critical acceptance gaps and follow-up headways from Austroads** (p.198)

| Movement | Description | Critical acceptance gap | Follow up headway |
|---|---|---|---|
| Left-hand turn | "Requiring a to slow" `[see uncertainty note]` | 5 seconds | 2-3 seconds |
| Crossing | One-way — two-lane | 4 seconds | 2 seconds |
| Crossing | One-way — three-lane | 6 seconds | 3 seconds |
| Crossing | One-way — four-lane | 8 seconds | 4 seconds |
| Crossing | Two-way — two-lane | 5 seconds | 3 seconds |
| Crossing | Two-way — four-lane | 8 seconds | 5 seconds |
| Crossing | Two-way — six-lane | 8 seconds | 5 seconds |
| Right-hand turn from major road | Across 1 lane | 4 seconds | 2 seconds |
| Right-hand turn from major road | Across 2 lanes | 5 seconds | 3 seconds |
| Right-hand turn from major road | Across 3 lanes | 6 seconds | 4 seconds |
| Right-hand turn from minor road | One-way | 3 seconds | 3 seconds |
| Right-hand turn from minor road | Two-way — two-lane | 5 seconds | 3 seconds |
| Right-hand turn from minor road | Two-way — four-lane | 8 seconds | 5 seconds |
| Right-hand turn from minor road | Two-way — six-lane | 8 seconds | 5 seconds |
| Merge | Acceleration lane | 3 seconds | 2 seconds |

The printed table has a "Diagram" column between Movement and Description:
`[FIGURE p.198 — Table 5-9's "Diagram" column — not extractable from text; content: a small
movement schematic per row (left-hand turn, crossing, right-turn from major, right-turn from
minor, merge). Carries no numbers beyond what the Description column already states.]`
`[EXTRACTION UNCERTAIN p.198]` The Left-hand-turn Description cell reads literally
`Requiring a to slow` in the dump — a word is missing (most plausibly "Requiring a
[through vehicle / major road vehicle] to slow"). Quoted raw rather than repaired.

> "These timings should be compared to the existing operation of the project location and
> amended accordingly. For example, the default parameters may need to be changed as a result of
> geometry. **To accommodate variations in acceleration, different gap acceptance parameters are
> recommended for different vehicle types. The exact gap acceptance will need to be determined
> during the calibration process.**" (p.199)

#### 5.4.1.1 Conflict Areas (p.199–200)

> "Typically, conflict areas are a more streamlined way of identifying vehicle priority between
> two conflicting streams. **Conflict areas are the preferred approach for non-critical
> intersections with no obvious unusual priorities and no overlapping and contradicting
> priorities.**"

> "When using conflict areas, the default gap acceptance parameters such as front gap, rear gap,
> avoid blocking the minor flow, and avoid blocking major flow should be refined based on
> intersection and movement types."

Definitions (verbatim, p.199):
> " Front gap is the minimum gap time in seconds between the rear end of a vehicle in the main
> traffic stream and the front end of a vehicle in the minor traffic stream.
>  Rear gap is the minimum gap time in seconds between the rear end of a vehicle in the minor
> traffic stream and the front end of a vehicle in the main traffic stream. This is the time that
> must be provided, after a yielding vehicle has left the conflict area and before a vehicle with
> the right of way enters it.
>  Avoid blocking minor flow makes a vehicle with the right-of-way check the space available
> downstream of the conflict area, and does not drive into the conflict area when the space
> available downstream is less than the total of the individual vehicle length + 0.5 metres and
> the blocking vehicle is slower than 5m/s and slower than 75 per cent of its desired speed.
>  Avoid blocking major flow makes sure a yielding vehicle does not enter or stop within the
> conflict area, unless it can drive through it in one go."

> "When using conflict areas, the critical gap and follow up headway gap defined in Table 5-9
> should be taken into account and the recommended parameter values in Table 5-10 can be adopted
> as a starting point for calibration." (p.199)

**Table 5-10: Recommended Conflict Area Parameters** (p.199)

| Movement | Front Gap | Rear Gap |
|---|---|---|
| Left-hand turn | 0.5 - 1.0 second | 2.0 – 4.0 seconds |
| Crossing | 0.5 - 1.0 second | 2.0 – 4.0 seconds |
| Right-hand turn from major road | 0.5 - 1.0 second | 2.0 – 4.0 seconds |
| Right-hand turn from minor road | 0.5 - 1.0 second | 2.0 – 4.0 seconds |
| Merge | 0.5 - 1.0 second | 1.5 – 3.0 seconds |

Conflict-area colour semantics (verbatim, p.200):
> "The status of conflict areas can be indicated by its colour:
>  Green main flow (right of way).
>  Red minor flow (yield).
>  Both red for branching conflicts, so that vehicles can "see" each other. There is no
> right-of-way, as vehicles simply remain in their original sequence.
>  Both yellow passive conflict area without right-of-way."

`[FIGURE p.200 — Figure 5-17 "Conflict area and updated parameters" — not extractable from
text; content: examples of left-hand and right-hand turns with updated conflict area
parameters. Numeric guidance is already in Table 5-10.]`

#### 5.4.1.2 Priority Rules (p.200)

> "**For complex intersections, locations with a high heavy vehicle percentage and intersections
> which are deemed critical to the performance of the overall network, priority rules should be
> used**, as illustrated in Figure 5-18."

> "When using priority rules, gap acceptance parameters in Table 5-9 should be adopted as a
> starting point."

`[FIGURE p.200 — Figure 5-18 "Priority rules" — not extractable from text; content: an example
of priority rules (stop line + conflict marker) placed at an intersection.]`

### 5.4.2 Signalised Intersections (p.201–202)

> "**The type of signal control used for the modelling should be agreed with Main Roads.** The
> accuracy of the signal operation will generally depend on the complexity of the project
> location, type of assessment and its impact on network performance. **Signal offsets should be
> correctly modelled using the information from SCATS (where applicable).**" (p.201)

#### 5.4.2.1 Traffic Signal Groups (p.201)

> "**Signal groups in the model must be defined in accordance with the site LM plan and SCATS
> information for existing signals (refer to Appendix A). Signal group numbers in Vissim models
> must match the signal groups defined in SCATS.**"

> "**The signal group/phase timings (minimum greens and yellow + all reds) should be directly
> input from controller/SCATS information and should not be changed. If the SCATS system shows a
> value as a fraction (for example, 0.5), the modeller should always round up the value for the
> inputs.** For pedestrian protection, late starts and early cut-offs, it may be necessary to
> obtain the relevant signal documents from the Main Roads' trafficmap or observe on-site to
> identify the signal groups associated with these."

#### 5.4.2.2 Signal Heads (p.201)

**Full text of the subsection — two sentences, no numbers:**

> "**Signal heads should generally be set on links or on connectors where partially controlled
> turns exist. To ensure that signal heads are not mismanaging traffic or being ignored, care
> should be taken in the setting of signal heads relative to the adjacent lanes and
> link/connectors.**"

**IMPORTANT — no "2.0 m / 4.5 m" appears in §5.4.2.2.** §5.4.2.2 prescribes **no** signal-head
position offset at all. The 2.0 m / 4.5 m numbers are in §5.4.2.6 Detectors (below) and are
**detector** defaults, not signal-head placement defaults.

#### 5.4.2.3 Fixed Time Signals (p.201)

> "**Fixed time signals should generally be used where there is minimal variation of phase
> sequences and timing throughout the assessment period. IDM data or SCATS history files should
> be used to determine the average phase lengths for the intersection(s) during the modelled
> time periods.**"

> "**Pedestrians should be modelled where there are high levels of pedestrian movements which
> may result in delays to the corridor due to crossing movements or signal controls. SCATS
> signal time settings can be used as the base data for pedestrian signals. If the pedestrian
> crossing is not fully protected, priority rules can be set to reflect vehicle movements during
> pedestrian signal clearance times.**"

#### 5.4.2.4 SCATSIM (p.201)

> "**The use of SCATSIM should be considered if high level accuracy of signal operations is
> required or if the signal operations are of high complexity. To ensure all required SCATSIM
> input files are accessible and available for modelling purposes, modellers should consult Main
> Roads.**"

> "**The use of SCATSIM may require the development of personality files (.sft) to model
> proposed intersections or phasing arrangement operations. Mixing SCATSIM with other forms of
> signal control is not generally recommended.**"

#### 5.4.2.5 Vehicle Actuated Programming (VAP) (p.202)

> "**VAP signal controls should be considered to mimic the SCATS operations for complex signal
> operations, such as infrequent demand dependant phases that have severe impact to signal
> operations (e.g. bus priority, alternative and pedestrian phases). Prior to using VAP in
> Vissim, two files should be defined:**
>  **Inter-stage file** – a text file which details all stages, intergreens, phase delays and
> interstage timings, with the detailed information derived from SCATS data.
>  **Logic file** – the actual control logic to be executed by the program file."

> "**The recommended VAP (.vv file) structure is:**
>  Program initiation – initiation of the program.
>  Program schedule – various phase proportions or time settings throughout the modelling
> period.
>  Signal linking module – for signal coordination purposes.
>  Recording functions – to enable the recording of the modelled phase and cycle frequency,
> time checks or validation process.
>  Stage logic module – used for phase transition purposes and should consider gap-out, minimum
> green, maximum phase time and phase call / detection operations.
>  Other transport priority and pedestrian operations that may apply to more than one phase
> should be kept separate to the stage logic module (where applicable).
>  **All numerical values (such as minimum green time, phase splits settings) should be stalled
> in the parameters section and not within the main logic module (flow chart).**
>  Expressions should be used where possible for the simplification of the logic code and to
> streamline review processes. The expressions module should contain gap-out and phase call
> conditions."

*(the dump reads "should be stalled in the parameters section"; almost certainly "stored" in
the printed doc, but quoted as-extracted.)*

> "**While direct coding of VAP may be appropriate, it is recommended modellers use VisVAP to
> code a VAP program. The use of VisVAP should be noted in the modelling report.**"
(Footnote 23: "VisVAP is a tool for defining the program logic of actuated signal controllers
as flow charts.")

#### 5.4.2.6 Detectors (p.202) — the 2.0 m / 4.5 m rule

> "Signal detectors generally need to be set when SCATSIM or VAP is adopted for signal control
> in the model. **Signal detectors in the model should reflect existing or design information
> (for example, traffic signal plans or operation sheets).**"

> "**If there is no information provided to indicate the detector size and location, signal
> detectors for vehicle detections should be modelled 2.0m back from signal heads and 4.5m in
> length at intersection approaches.**"

Reading: 2.0 m = setback of the detector's downstream end from the signal head; 4.5 m =
detector length. Applies to **vehicle** detectors at intersection approaches, only when plan
data is absent. It is a fallback, not a preferred value — plans/operation sheets win.

### 5.4.3 Ramp Metering (p.202–203)

> "Ramp metering is used to improve freeway efficiency by regulating flows at the on-ramps onto
> the freeway. There are several methods of modelling that can be used, but it will depend on
> the purpose of the study." (p.202)

#### 5.4.3.1 Fixed-Time Metering (p.203)

> "**Fixed-time controls should generally be used unless the purpose of the model is to assess
> the operations of the freeway and on-ramps.** This simplified method allows modellers to
> simulate the operations of the ramps by inputting the observed data. The required data may vary
> depending on the type of metering option selected in Aimsun. The fixed metering options are as
> follows:
>  green-time metering
>  green-time by lane metering
>  flow metering, and
>  delay metering."

> "**The fixed-time ramp meters will have to be validated against the observed on-ramp and
> freeway operations**, however this should be confirmed during the project scope meeting as the
> requirements will vary depending on the purpose of the project."

#### 5.4.3.2 ALINEA Ramp Metering (p.203)

> "The ALINEA algorithm is a closed-loop dynamic system that improves the efficiency of the
> freeway by dynamically controlling the on-ramp flows based on the mainline downstream
> occupancy rate. The algorithm runs in isolation and queues cannot be managed on the on-ramps."

#### 5.4.3.3 Adaptive Ramp Metering (p.203)

> "Adaptive Ramp Metering (ARM) is an AImsun algorithm comparable to HEuristic Ramp metering
> cOordination (HERO). It is an extended version of ALINEA with a feedback system that
> coordinates and controls local ramp meters under specific conditions. When ARM is activated,
> the ramp that triggered ARM will become the master ramp, while any upstream ramps will become
> slaves to reduce flows resulting in improved efficiency on the mainline."

> "ARM will deactivate once the specific conditions are satisfied and the trigger conditions are
> as follows:
>  Activation trigger:
>  the mainline occupancy is over a threshold of the target occupancy, and
>  the queue on the on-ramp (which becomes the master ramp) is over the threshold of the
> maximum queue length.
>  Deactivation trigger:
>  the mainline occupancy drops below the threshold of the target occupancy, or
>  the queue on the master ramp is lower than the threshold of the maximum queue length."

> "**Main Roads recommends ARM as the preferred option when the purpose of the model is to
> assess the operation of the freeway and fixed timing for other assessments. The preferred
> method should be confirmed at the scope meeting.**"

**DOC DEFECT — §5.4.3 is Aimsun text pasted into the Vissim chapter.** §5.4.3.1 says the data
"may vary depending on the type of metering option selected in **Aimsun**" and §5.4.3.3 calls
ARM "an **AImsun** algorithm". No Vissim-specific ramp-metering guidance is given anywhere in
§5. Do not build Vissim ramp-metering behaviour off this subsection without confirming with
the client; the numeric thresholds ("target occupancy", "maximum queue length") are named but
never valued.

---

## 5.5 Traffic Data (p.204–209)

> "Once a transport network is built in Vissim, modellers should define traffic data such as
> demands and routes in the model. This section discusses recommended traffic input types."
(p.204)

### 5.5.1 Vehicle Composition (p.204)

> "**Vehicle composition used within the model should be determined using recent classified
> traffic counts and aligned with specific vehicle types (refer to Section 5.2.3).**"

### 5.5.2 Public Transport Stops and Lines (p.204–205)

> "**Public transport operations should be modelled where they may have a significant impact on
> the performance of the project area, such as bus only lanes and intersections with public
> transport priority operations. Public transport coding should be based on information obtained
> from Transperth, including public transport routes, stop locations, timetables and dwell
> time.**"

> "If there is no dwell time data available, and the model does not require detailed public
> transport operations, the parameters shown in Table 5-11 may be used as a guide depending on
> the purpose of the model and the study area."

**Table 5-11: Bus dwell time parameters if bus dwell time data not available** (p.204)

| Parameter | Suggested value |
|---|---|
| Transfer Station – Mean | 20 seconds |
| Bus Stop – Mean | 10 seconds |
| Deviation | 5 seconds |

> "**A bus dwell time of 10 seconds is sufficient in most cases, however bus dwell times may be
> higher within the CBD, around schools and universities and bus stations. On-site observations
> of dwell time should be used to help determine bus dwell time parameters**"

> "**Modellers should include school buses in the public transport coding.** The routes and
> timetables of school buses can be obtained from Transperth's website."

> "**The layout of the public transport bays should reflect existing or design configurations.**
> When observations indicate general traffic overtakes a bus dwelling at an on-street bus stop on
> a single lane road and the behaviour impacts the network performance, **it is recommended to
> model public transport overtaking lane with Overtake Only PT activation.**"

`[FIGURE p.205 — Figure 5-19 "Bus overtaking lane coding with Overtake only public transport
active" — not extractable from text; content: an example of bus overtaking lane coding.]`

### 5.5.3 Parking (p.205)

> "**Vehicle parking spaces and parking manoeuvres should be modelled where they may have a
> significant effect on the operation and capacity of the network within the project area. The
> layout and vehicle movements associated with parking should reflect the existing or design
> conditions. Parking delays should not be induced by setting reduced speed areas in the
> model.**"

### 5.5.4 Static Assignment (p.205–207)

> "**While the use of static vehicle routes is generally recommended, the model assignment
> method should be agreed with Main Roads.**"

#### 5.5.4.1 Vehicle Inputs (p.205–206)

> "**Traffic demands should generally be introduced using the vehicle inputs in network objects.
> Modellers should define peak period demands and include warm-up and cool-down periods.**"

> "**Main Roads recommends that modellers use at least 15 minutes or two times of the longest
> travel time for warm-up and cool-down periods with a minimum of 85 per cent of peak hour
> volumes unless modellers have justifications to use other values.**"

> "**It is recommended for model category 1 (refer to Section 2.12.2.1) that traffic count data
> is analysed for 15 minute intervals and vehicle inputs are set for peak periods to enable a
> model to replicate observed on-site traffic flow accurately.**"

> "**Modellers can use hourly volumes for model categories 2 and 3 and set at 15 minute intervals
> using continued attribute in vehicle input.**"

> "**It should be noted that vehicle inputs in Vissim are expressed in hourly volumes. Therefore,
> the analysed 15 minute interval traffic flows must be recalculated to one-hour traffic flows
> (for example, an analysed 15 minute flow is 200 vehicles, then the value for the vehicle input
> must be 800).**"

> "Traffic volumes can be assigned a vehicle composition and either set to generate exact volumes
> or, if variability is required, to a stochastic loading. **However, it is recommended that
> modellers use exact volumes to minimise the discrepancies of model outputs between simulation
> runs with different seed numbers.**" (p.206)

> "It is recommended for model category 1 (refer to Section 2.12.2.1) that traffic count data is
> analysed for 15 minute intervals and demand profiles are set for peak periods to enable a model
> to replicate observed on-site traffic flow accurately. Modellers can use hourly volumes for
> model categories 2 and 3 and set at 15 minute intervals with the observed demand profiles or a
> flat demand profile. **The demand development methodology will require agreement with Main
> Roads during the scope meeting.**" (p.206 — near-duplicate of the earlier paragraph, with
> "demand profiles" substituted for "vehicle inputs"; both are printed on p.205/206.)

> "**Where peak fluctuations due to schools and shopping centres may occur, and therefore
> depending on the purpose of the model, traffic data analysis for 15 minutes or 30 minutes
> intervals may be required for categories 2 or 3.**" (p.206)

`[FIGURE p.206 — Figure 5-20 "Example of vehicle input defined for model category 1" — not
extractable from text; content: a Vissim vehicle-input table for a Cat-1 model (15-minute
profiled inputs).]`
`[FIGURE p.206 — Figure 5-21 "Example of vehicle input defined for model category 2 or 3" —
not extractable from text; content: a Vissim vehicle-input table for a Cat-2/3 model using the
"continued" attribute.]`

#### 5.5.4.2 Vehicle Routes (p.207)

> "**Vehicle route decision markers should be set to mimic the point where motorists would likely
> begin to perform lane changes to reach their desired destination. To manage demands for trip
> purposes or vehicle types, the use of vehicle routes for individual vehicle classes is
> recommended.**"

> "**It is generally recommended to activate Combine static routing decisions when static
> assignment is used.**"

`[FIGURE p.207 — Figure 5-22 "Example of static routing decisions" — not extractable from text;
content: static vehicle routing decisions defined for different vehicle classes.]`

### 5.5.5 Dynamic Assignment (p.207–209)

> "If route choice exists, vehicles can be loaded into the network by using OD matrices and
> dynamic assignment. Dynamic assignment provides drivers with choices on routes to a destination
> based on different traffic conditions. **Assignment should be set according to the user-defined
> route selection criteria for distance and travel time.**"

> "**The use of dynamic assignment should be agreed with Main Roads before modelling begins.**"

> "When dynamic assignment is used, **the modeller needs to ensure that traffic flows are
> correctly specified in the OD matrix files and that the method used to estimate OD matrices is
> documented in the modelling report. The modeller must also ensure that the modelled network is
> stable during the assignment process to enable the model to reach a converged state.**"

> "**It is recommended that traffic is initially loaded at a reduced level of demand (typically
> 50 per cent) and then progressively increased.**"

> "**If equilibrium assignment is used in place of stochastic user equilibrium, the modelling
> report should contain details on why this assignment method was considered more appropriate.**"

#### 5.5.5.1 Nodes (p.208)

> "Nodes are primarily used for dynamic assignment, intersection evaluation or mesoscopic
> modelling purposes."

> "**For dynamic assignment, nodes should be set at locations where a route decision needs to be
> made by a vehicle**, as shown in Figure 5-23."

> "To limit the links that the node is assigned to, the modeller may convert the node. This is
> particularly relevant for situations where the primary highway should be excluded from the
> interchange above / below the highway (for example, at multi-level single point interchanges as
> shown in Figure 5-24."

> "**Nodes can also be used to evaluate intersection performance. Particular care should be taken
> with node structure if node evaluation is being used.**"

`[FIGURE p.208 — Figure 5-23 "Node placement" — not extractable from text; content: node
placement at a route-decision location.]`
`[FIGURE p.208 — Figure 5-24 "Example of node convert" — not extractable from text; content: a
converted node at a multi-level single point interchange.]`

#### 5.5.5.2 Convergence (p.209)

> "**Main Roads recommends modellers use the dynamic assignment convergence criteria:**
>  **95% of travel times on all paths and edges change by less than 20% for at least four
> consecutive iterations** as shown in Figure 5-25."

> "**Once convergence has been achieved, the convergence evaluation file (*.CVA), path file
> (*.WEG) and cost file (*.WGA) for each calibrated model should be stored for use during all
> subsequent modelling and provided to Main Roads as part of the base model submission.**"

`[FIGURE p.209 — Figure 5-25 "Dynamic assignment parameters" — not extractable from text;
content: the Vissim dynamic-assignment convergence settings dialog. Numeric criteria already
given verbatim above.]`

---

## 5.6 Model Calibration and Validation (p.210–217)

> "In general, calibration and validation require an iterative process of adjusting parameters
> and analysing model results until the model has achieved an accepted level of confidence when
> compared to the on-street conditions." (p.210)

> "**The calibration and validation requirements can be defined based on the importance of a
> specific project and its significance to the region. It is recommended that modellers discuss
> the requirements with Main Roads.**" (p.210)

### 5.6.1 Model Calibration Requirements (p.210–211)

> "Base model calibration is the process of employing verifiable observed data into a model to
> replicate on-street conditions."

#### 5.6.1.1 Data Collection (p.210)

> "The type of data required for the modelling should be determined specifically for each
> project. **It is the modeller's responsibility to ensure appropriate data is collected to
> enable the development of the model.** The format of output should be specified for modelling
> purposes. **For consistency and accuracy of the modelling, it is recommended that all data is
> collected on the same day.** The following list of data to be collected is not exhaustive but
> should be considered:"

> " design layout information /  SCATS data /  traffic counts /  OD surveys /  public
> transport route and timetable information /  travel time data /  queue length /  saturation
> flow /  vehicle speed, and /  video footage."

#### 5.6.1.2 Site Observations (p.210)

> "**It is important for modellers to observe the existing site conditions. Driving behaviours,
> network configuration, signal operations and other events which may impact on the performance
> should be captured and reported as part of the modelling. The operating conditions and site
> findings should be captured in the form of photographs and videos.**"

#### 5.6.1.3 Model Input Check (p.211)

> "**Modellers should check network coding and network object's attributes to confirm that the
> model network replicates the existing or the proposed transport network, and that the
> simulation is consistent with the observed on-street vehicle behaviour. The key inputs to check
> include:**
>  number of lanes /  lane width /  lane closure /  link behaviour type /  elevation
> (Z-height) /  feasible movements /  vehicle speed (desired speed decisions and reduced speed
> areas) /  priority control (conflict areas and priority rules) /  signalised intersection
> control (signal timing plans, signal groups, signal heads) /  traffic demands (vehicle inputs
> or OD matrices) /  vehicle routes, and /  dynamic assignment cost parameters (cost and
> surcharges)."

*(This 12-item list is the closest thing §5 has to a machine-checkable model-input audit
checklist — a natural target for a Prism validation rule set.)*

#### 5.6.1.4 Vehicle Behaviour (p.211)

> "As it is difficult to set up a standard for vehicle behaviour, **modellers should undertake a
> visual check to confirm the observed on-street vehicle behaviour is consistent with that
> observed in the model. Vehicle behaviour such as speed / flow performances, bottlenecks, queue
> formation and discharging need to be carefully compared and calibrated.**"

> "A correctly coded network should reflect on-street vehicle behaviour, especially during
> congested peak times. **Main Roads recommends modellers drive through the network and capture
> videos of critical locations to familiarise themselves with driving behaviour. Modellers should
> include notes and changes to the network in the modelling report.**"

#### 5.6.1.5 Error Files (p.211)

> "**Ideally, there should be no error messages for a calibrated and validated base model.**"

> "**If error messages are generated and persistent errors (e.g. latent traffic demands and
> diffused vehicles) occur at certain location(s), modellers must review network coding and model
> parameters to ensure all model deficiencies are addressed.**"

### 5.6.2 Simulation Parameters (p.211–213)

#### 5.6.2.1 Key Parameters (p.212)

> "**The simulation period is defined as the total simulation time which includes warm-up,
> cool-down and the assessment period. The simulation period and start time should be reflected
> in the simulation parameters prior to simulations being run.**"

> "**Simulation resolution should be set to the default 10 time steps per second, as shown in
> Figure 5-26. This should not be changed, especially when modelling different scenarios. If time
> steps need to be modified for specific operational modelling purposes, consistency should be
> maintained across all compared scenarios.**"

`[FIGURE p.212 — Figure 5-26 "Simulation parameters general tab" — not extractable from text;
content: the Vissim simulation-parameters General tab. The one prescriptive number (10 time
steps/s) is given verbatim above.]`

#### 5.6.2.2 Seed Values (p.212–213)

> "There is variability in traffic conditions as a result of random driver behaviour and
> different daily events. Vissim attempts to replicate this random variability by altering
> individual driver decisions based on random numbers. The set of random numbers is determined by
> Vissim random seed value (refer to Figure 5-24) at the start of a simulation run."
*(cross-reference error: Figure 5-24 is "Example of node convert"; Figure 5-26 is meant.)*

> "**Modellers should run a minimum of five and a maximum of 10 simulations with different random
> seed values and present average model outputs. The modeller should list the seed numbers used
> in the modelling report.**" (p.213)

> "**For dynamic assignment models, one seed value should be used to search for paths and to
> achieve convergence. Once the model has met convergence criteria, the paths should be locked
> and the multi-runs undertaken. Model calibration and validation results should be based on the
> average of the seed runs and not on the single seed model used to search for the paths.**"

> "**Seed values must remain consistent across all scenarios.**"

### 5.6.3 Model Validation Requirement (p.213–217)

> "**Main Roads defines the three model categories for model validation, based on the network
> size and modelling purposes (Refer to Section 2.12.2.1). The following sub-sections discuss the
> validation criteria for Vissim models.**"

#### 5.6.3.1 Traffic Volumes (p.213)

> "**Main Roads requires hourly turning movement and directional link volumes to be validated for
> each major vehicle type.** Table 5-12 demonstrates the model validation criteria for individual
> model categories."

**Table 5-12: Traffic flow validation criteria** (p.213)

| Criteria | Category 1 | Category 2 | Category 3 |
|---|---|---|---|
| GEH < 5 | 95% | 85% | 80% |
| GEH < 10 | 100% | 95% | 90% |
| < 700 vph within 100 vph | 95% | 90% | 85% |
| 700 – 2,700 vph within 15% | 95% | 90% | 85% |
| > 2,700 vph within 400 vph | 95% | 90% | 85% |
| R squared value | >0.95 | >0.95 | >0.9 |

> "**Even if the model outputs meet the traffic volume validation criteria for the entire
> network, the modeller should check and ensure that turning volumes at critical intersection(s)
> achieve GEH less than 5.**"

> "**Where throughput between upstream and downstream intersections do not match due to survey
> error, the flow discrepancy should be identified and detailed in the modelling report.** While
> it is recommended that in these circumstances the most appropriate traffic flow be used is
> based on engineering judgement, this will also need to be explained in the modelling report."

#### 5.6.3.2 Travel Times (p.214–215)

> "Travel time is a common technique used to assess the accuracy of a model by comparing surveyed
> and modelled travel times along key routes in the study area. **It is important to ensure that
> a sufficient number of travel time surveys have been undertaken during the data collection
> stage of the project. When collecting data the route should be disaggregated into smaller
> sections so that the location of key delays within the overall travel time can be easily
> identified.**"

> "**Main Roads recommends modellers measure travel times between intersection stop lines along
> key routes in the study area. The sections to compare should be agreed with Main Roads during
> the scope meeting. The extracted travel time for the model validation should be average values
> over several simulation runs (refer to 5.6.2.2).**"

> "The use of sections allows a cumulative graph of travel time along the route to be developed.
> This type of graph can provide an accurate analysis that demonstrates how the model performs in
> each key section, rather than simply providing a total travel time."

> "**Modellers should not only compare cumulative travel time, but also need to analyse each
> section's travel time as well and identify any issue causing the travel time discrepancy.**"

> "**The modelled travel times should be within 15 per cent or one minute (whichever is greater)
> of average observed travel time.** Table 5-13 demonstrates the model validation criteria for
> individual model categories." (p.215)

**Table 5-13: Travel time validation criteria** (p.215)

| Criteria | Category 1 | Category 2 | Category 3 |
|---|---|---|---|
| 15 % or one minute of average observed travel time | 100% | 90% | 85% |

**Table 5-13 has exactly one criteria row.** It carries the travel-time pass RATE only; the band
("15 % or one minute … whichever is greater") is stated in the body text above the table.

> "**Where there are insufficient observed sample sizes to compare with the modelled average
> travel time, the minimum and maximum observed travel time should be shown.**"

`[FIGURE p.214 — Figure 5-27 "Example of cumulative travel time comparison" — not extractable
from text; content: a cumulative observed-vs-modelled travel-time graph along a route.]`
`[FIGURE p.215 — Figure 5-28 "Example of each section travel time comparison" — not extractable
from text; content: a per-section observed-vs-modelled travel-time comparison chart.]`

#### 5.6.3.3 Queue Length (p.215)

> "**Queue length data should be collected on-site and compared with modelled outputs to provide
> an indication of how accurately the model replicates congestion on approaches to key
> intersections in the model.**"

> "**Modelled queues should correlate reasonably with on-site observations of queuing behaviour,
> with any significant discrepancies indicating that areas of the model may require further
> calibration including link driving behaviour, vehicle inputs and traffic controller updates.**"

> "**It is recommended that the modelled average maximum queue length be compared to observed
> queue length data. The queue data should be surveyed and recorded for five minute intervals
> over the peak period.**"

**No numeric queue tolerance is given for Vissim.** §5.6.3.3 is qualitative ("correlate
reasonably") plus the 5-minute survey interval. (The LinSig chapter's queue validation —
"Back of Uniform Queue at End of Red", §3.10.2 p.121 — is likewise qualitative.)

#### 5.6.3.4 Signal Timing (p.215–216)

> "**An evaluation of model output and surveyed data should provide confidence that signal cycles
> and timings are comparable in the model to the recorded average SCATS operation.**"

> "**The modeller should compare selected signalised intersections in the core area of the model
> with the recorded average SCATS history data. The nominated intersections should be agreed with
> Main Roads during the scope meeting.**"

**Table 5-14: Microsimulation signal timing validation criteria** (p.216)

| Signal operation | Criteria |
|---|---|
| Fixed time control - cycle time | Within 3 seconds or 5% (whichever is smaller) of recorded average of SCATS history data for same one hour period |
| Fixed time control - green time | Within 3 seconds or 10% (whichever is smaller) of recorded average phase of SCATS history data for same one hour period |
| Vehicle actuated control - Call frequency | Call frequency of demand-dependent phases (including pedestrian phases) to be compared with recorded average phase of SCATS history data for same one hour period |

**Table 5-14 gives no pass rate and no category split** — unlike 5-12/5-13 it is a flat
criteria table. Note the direction: signal timing uses "**whichever is smaller**" (tighter of
the two), the opposite of travel time's "whichever is greater". The VAP/actuated row states a
comparison obligation with **no numeric tolerance at all**.

#### 5.6.3.5 Saturation Flow (p.216)

> "**Lane saturation flow model outputs should be validated against saturation flow measured
> on-site for critical lanes. The critical lanes and key intersections to be compared should be
> agreed with Main Roads during the scope meeting.**"

> "**The preferred method to extract saturation flow outputs from Vissim is using the discharge
> record. The modeller should select discharge record active in the Signal Head window and
> discharge record in the Evaluation Configuration window.** Figure 5-29 shows an example of
> discharge record configuration selection. **Once the discharge record is produced, modellers
> should calculate the saturation flow for the desired link according to the saturation flow
> measurement method outlined in Section 2.10.3.**"

> "**All observed and modelled saturation flow values should be tabulated and the percentage
> error between the two values provided in the modelling report.**"

> "**Modelled saturation flow values should be within 10 per cent of observed values.**"

`[FIGURE p.216 — Figure 5-29 "Example of discharge record configuration" — not extractable from
text; content: the Vissim Signal Head / Evaluation Configuration windows with discharge record
enabled.]`

#### 5.6.3.6 Vehicle Speed (p.217)

> "**For all freeway modelling projects, speed plots (heat maps) must be produced to compare the
> modelled and observed freeway segment condition. Critical bottlenecks may require lane-by-lane
> comparisons.**"

`[FIGURE p.217 — Figure 5-30 "Example of speed plots" — partially extractable; content: an
observed-vs-modelled heat map, "Kwinana Freeway Northbound Heat Map Plot", 6:00–8:30, VDS rows
from "Mounts Bay Off" to "Russell Off". Legend bands recovered verbatim: "< 30 km/h",
"30 - 50 km/h", "50 - 70 km/h", "70 - 90 km/h", "> 90 km/h". The plot itself is colour data and
carries no further numbers.]`

The five legend bands (<30 / 30–50 / 50–70 / 70–90 / >90 km/h) are the only quantitative
content in §5.6.3.6 and are an example legend, **not** stated as a prescribed banding.

---

## 5.7 Scenario Management (p.218)

> "**Modellers are required to maintain consistency in network settings between scenarios (e.g.
> AM and PM peaks). For consistency, it is recommended that modellers use scenario management to
> establish scenarios using developed modifications.**"

> "**The naming and descriptions of each modification should be easily comprehended by other
> users, and the number of modifications should be kept minimum. It is recommended that modellers
> create individual modification files for each peak, and geometry update scenario.**"

> "**The order of the scenarios should be in sequence and a consistent naming convention should
> be adopted to assist users in identifying the relevant scenarios more effectively.**"

> "**The structure and scope of the modifications developed should be provided in the modelling
> report.** It is important that modellers pay extra attention to the modifications that should
> be amended."

> "**To prevent unnecessary changes being made to the original external files, modellers should
> ensure that external files (such as signal program, cost and path files) are duplicated and
> renamed for each scenario.**"

`[FIGURE p.218 — Figure 5-31 "Example of modification files for peak and design scenarios" —
not extractable from text; content: a list of Vissim modification files named per peak and per
geometry-update scenario. The naming convention itself is NOT recoverable.]`
`[FIGURE p.218 — Figure 5-32 "Example of scenario naming and descriptions" — not extractable
from text; content: the Vissim scenario manager with example scenario names and descriptions.
The naming convention itself is NOT recoverable.]`

§5.7 is entirely prose — **no tables, no numbers.**

---

## 5.8 Model Output (p.219–221)

> "**Modellers are required to report model assessment results to Main Roads for review when the
> base model is validated and when proposed option models are completed.** This section outlines
> model evaluation outputs to be reported and recommends ways to extract the data from Vissim
> models." (p.219)

### 5.8.1 Model Evaluation Outputs (p.219)

> "**Main Roads will require the model evaluation results to be reported as the final model
> outcomes in the modelling report. The model outputs include, but are not limited to those shown
> in Table 5-15. The results extracted from the model should be clearly presented in tabular or
> graphical forms.**"

**Table 5-15: Model evaluation outputs** (p.219)

| Type | Model output | Evaluation method | Time period |
|---|---|---|---|
| Network-wide | Number of vehicle served; Total travel distance; Total travel time; Average speed; Total delay; Average delay per vehicle; Total latent traffic demand | Vehicle network performance | Total analysis period (peak) |
| Intersection | Turning movement volume; Approach queue length; Average delay for each approach and intersection; LoS for each turning movement and intersection | Node; Queue counter | 1 hour interval analysis period |
| Route | Volume; Average travel time; Travel distance; Average delay time | Vehicle travel time; Delay | 1 hour interval analysis period |
| Critical section (for example, bottle neck) | Traffic volume; Density; LoS; Heat map; Vehicle speed | Link; Data collection | 15 minute interval analysis period |

(The "Model output" and "Evaluation method" cells are multi-line lists in the printed table;
semicolons above are the transcriber's separators. Row/column alignment is unambiguous in the
dump.)

#### 5.8.1.1 Network-wide Performance Assessment (p.220)

> "**Network-wide model performance outputs should be presented for the scoped evaluation time
> period with appropriate time intervals.**"

**Table 5-16: Example of network performance outputs** (p.220) — *an example, not a criterion*

| Scenario | Base AM | Base PM |
|---|---|---|
| Time Interval | 07:45 – 08:45 | 16:30 – 17:30 |
| Vehicles arrived (veh) | 15,969 | 22260 |
| Total travel distance (km) | 51,906 | 72,223 |
| Total travel time (hh:mm) | 1217:39 | 1567:34 |
| Total delay (hh:mm) | 501:30 | 567:14 |
| Average vehicle speed (km/h) | 42.6 | 46.1 |
| Average delay (s) | 101 | 86 |
| Demand latent (veh) | 0 | 1 |

("22260" is printed without a thousands separator in the source; transcribed as printed.)

#### 5.8.1.2 Intersection Assessment (p.220)

> "**Intersection performance outputs should be generated for the analysis of the existing
> condition or proposed traffic management scheme. The intersection performance outputs should
> include directional traffic volume, vehicle delay, level of service and queue length.**"

> "Table 5-16 demonstrates an example of a signalised intersection performance based on the node
> evaluation outputs."  *(cross-reference error: the table that follows is **Table 5-17**.)*

**Table 5-17: Example of intersection performance outputs** (p.220) — *an example, not a criterion*

| Movement | Volume (veh) | Delay (s) | LOS | Queue (m) |
|---|---|---|---|---|
| Westbound Right-turn | 3 | 127.1 | F | 130.9 |
| Westbound Thru | 991 | 20.1 | C | 130.9 |
| Westbound Left-turn | 191 | 2.12 | A | 35.5 |
| Eastbound Right-turn | 19 | 96.4 | F | 317.1 |
| Eastbound Thru | 1,759 | 27.2 | C | 317.1 |
| Eastbound Left-turn | 12 | 22.6 | C | 283.1 |
| Northbound Right-turn | 650 | 82.6 | F | 169.2 |
| Northbound Thru | 23 | 103.2 | F | 169.2 |
| Northbound Left-turn | 2 | 2.7 | A | 0.0 |
| Southbound Right-turn | 49 | 83.5 | F | 35.4 |
| Southbound Thru | 39 | 90.4 | F | 35.4 |
| Southbound Left-turn | 69 | 9.8 | A | 25.7 |
| **Intersection** | **3,807** | **35.44** | **D** | **317.1** |

#### 5.8.1.3 Route Assessment (p.221)

> "**Critical route assessment should be presented for travel time, volumes and delays, to
> confirm that the developed model reflect existing traffic condition within the study area, and
> to compare traffic management schemes.**"

#### 5.8.1.4 Critical Section Assessment (p.221)

> "**Detailed performance assessment should be carried out for critical sections of the study
> area such as bottle necks and freeway on and off-ramps.**"

> "**Link evaluation enables detailed analysis of the link performance including vehicle volume,
> speed and density.**"

**END OF §5** — doc p.221. §6 "Aimsun Guidelines" begins doc p.222.

---

---

# CROSS-CHECK — `frontend/lib/validationProfiles.ts` vs Tables 5-12 / 5-13 / 5-14

**This block is analysis, NOT part of the verbatim extract above.**

## Verdict: every number in `validationProfiles.ts` is CORRECT. Four documentation/coverage defects, zero numeric disagreements.

### Numeric agreement — Table 5-12 (p.213) vs `VALIDATION_PROFILES`

| Table 5-12 criterion | Doc Cat1/2/3 | `validationProfiles.ts` field | ts Cat1/2/3 | |
|---|---|---|---|---|
| GEH < 5 | 95 / 85 / 80 % | `gehUnder5Pct` | 95 / 85 / 80 | AGREE |
| GEH < 10 | 100 / 95 / 90 % | `gehUnder10Pct` | 100 / 95 / 90 | AGREE |
| < 700 vph within 100 vph | 95 / 90 / 85 % | `flowLowWithinPct` + `FLOW_LOW_TOLERANCE_VPH=100` | 95 / 90 / 85, 100 | AGREE |
| 700 – 2,700 vph within 15% | 95 / 90 / 85 % | `flowMidWithinPct` + `FLOW_MID_TOLERANCE_PCT=15` | 95 / 90 / 85, 15 | AGREE |
| > 2,700 vph within 400 vph | 95 / 90 / 85 % | `flowHighWithinPct` + `FLOW_HIGH_TOLERANCE_VPH=400` | 95 / 90 / 85, 400 | AGREE |
| R squared value | >0.95 / >0.95 / >0.9 | `rSquaredMin` | 0.95 / 0.95 / 0.9 | AGREE (ts doc-comment correctly reads it as strict `>`) |

### Numeric agreement — Table 5-13 (p.215) vs `VALIDATION_PROFILES`

| Table 5-13 criterion | Doc Cat1/2/3 | ts field | ts Cat1/2/3 | |
|---|---|---|---|---|
| "15 % or one minute of average observed travel time" | 100 / 90 / 85 % | `travelWithinPct` | 100 / 90 / 85 | AGREE |
| band = 15 % (body text p.215) | 15 % | `TRAVEL_TOLERANCE_PCT` | 15 | AGREE |
| band floor = one minute, **whichever is greater** (body text p.215) | 1 min | `TRAVEL_TOLERANCE_MIN_MINUTES` | 1 | AGREE — and ts already flags this floor as not yet applied by the base-model page |

The ts module's structural reading is also right: the **tolerance widths** (100 vph / 15 % /
400 vph / 15 % / 1 min) do not vary by category; only the **pass rates** do.

### DISAGREEMENT 1 — the citation's page numbers are PDF pages, not doc pages

`validationProfiles.ts` header: `Tables 5-12/5-13 (p.231-234)`. Doc pages are **213** (Table
5-12) and **215** (Table 5-13); 231/234 are the PDF page indices (213+18, 216+18). Every
other MRWA reference in the repo (e.g. `mrwa-appendix-a-extract.md`) cites doc pages, so this
citation is off-by-18 relative to house convention and will not match a reader's PDF page
footer. Suggested replacement: `Tables 5-12/5-13 (OMG v2.0 §5.6.3.1/§5.6.3.2, p.213/p.215)`.

### DISAGREEMENT 2 — the section title in the header comment is wrong

`validationProfiles.ts` says `Source: MRWA Operational Modelling Guidelines, §5 "Model
Validation" (micro-simulation)`. §5's actual title is **"Vissim Guidelines"**. The validation
material is §5.6 "Model Calibration and Validation" → §5.6.3 "Model Validation Requirement".
Also worth noting: these thresholds are the **Vissim** chapter's; §6 (Aimsun) and §3 (LinSig)
have their own criteria — LinSig validates on degree of saturation + queue length (§3.10,
p.121), not on GEH at all. Naming the profile "§5" without "Vissim" invites mis-application.

### DISAGREEMENT 3 — the flow bands are misattributed to Table 5-13

ts comment: *"Flow-band tolerances (±100 veh/h … ±400 veh/h above 2700 veh/h) and the
travel-time tolerance band … are the SAME across all three categories in **Table 5-13**"*.
The flow bands are in **Table 5-12**; Table 5-13 contains **one row only** (travel time). The
numbers are right, the table attribution is not.

### GAP 4 — Table 5-14 (signal timing) has no representation in `validationProfiles.ts`

Table 5-14 (p.216) is a §5.6.3 validation criterion of the same standing as 5-12/5-13 and is
absent from the profile module. It is also **category-invariant** (no Cat 1/2/3 split), so it
does not fit the existing per-category shape and would need its own constant:

- Fixed time control — cycle time: within **3 s or 5 %, whichever is SMALLER**
- Fixed time control — green time: within **3 s or 10 %, whichever is SMALLER**
- Vehicle actuated — call frequency: comparison required, **no numeric tolerance stated**

Landmine: "whichever is **smaller**" here is the *opposite* of travel time's "whichever is
**greater**". Any shared helper that computes "tolerance = max(pct·x, floor)" will silently
produce a too-loose signal-timing check.

### GAP 5 — other §5.6.3 numeric criteria not carried anywhere

- §5.6.3.5 (p.216): "**Modelled saturation flow values should be within 10 per cent of observed
  values.**" — a hard numeric criterion, category-invariant, currently unmodelled in the repo.
- §5.6.3.1 (p.213): the per-intersection override — "**turning volumes at critical
  intersection(s) achieve GEH less than 5**" — is stricter than the network-wide 95/85/80 %
  rates and applies *even when the network-wide criteria pass*. A base-model page that only
  reports network-wide GEH rates can show PASS on a model that MRWA would reject.
- §5.6.2.2 (p.213): validation results must be the **average of 5–10 seed runs** (min 5, max
  10), with seed numbers listed in the report and held constant across scenarios. This is a
  precondition on the inputs to every threshold above.
- §5.6.3.3 (p.215) queue length and §5.6.3.6 (p.217) speed heat maps carry **no** numeric
  criteria — correctly absent from the profile module.

### Note on Category definitions (outside §5)

§5.6.3 defers the Cat 1/2/3 definitions to **§2.12.2.1 (p.43–44)**:
- **Category 1**: "Single intersection or short corridor model (up to four full movement
  intersections)."
- **Category 2**: "Small area network or long corridor model with limited route choices."
- **Category 3**: "Large area networks including multiple long corridors with various routes
  between origin and destination zones, and use dynamic traffic assignment."

GEH definition (§2.12.2.2, p.44): `GEH = √( 2(M-C)² / (M+C) )`, "M is the modelled flow and C
is the observed flow." `[EXTRACTION UNCERTAIN p.44]` — the dump renders the formula as
`GEH = √2(M-C)2\nM+C`, i.e. the fraction bar and the exponent are lost; the standard GEH
formula is assumed above. Also `[EXTRACTION LOST p.44]` — **Table 2-3 "Model validation
requirements based on model category"** (which outputs are required per category per software)
extracts with **all its cells empty**: the content was tick marks, which the text layer does
not carry. That table would be needed to say *which* of the §5.6.3 criteria apply to a given
category; it is not recoverable from the text dump and needs the PDF or a rendered image.

---

## Doc defects found (report these upstream; they affect mechanical citation matching)

Figure/Table cross-reference errors — the body text says "Figure N" where the object is
"Table N":
- §5.2.3 p.182 → "Figure 5-1" means Table 5-1
- §5.2.3.2 p.183 → "Figure 5-2" means Table 5-2
- §5.2.3.3 p.185 → "Figure 5-3" means Table 5-3
- §5.2.3.4 p.185 → "Figure 5-4" means Table 5-4
- §5.2.4 p.186 → "Figure 5-5" means Table 5-5
- §5.2.5.1 p.187 → "Figure 5-6" means Table 5-6
- §5.3.5 p.197 → "Figure 5-8" means Table 5-8
- §5.6.2.2 p.212 → "refer to Figure 5-24" means Figure 5-26
- §5.8.1.2 p.220 → "Table 5-16 demonstrates … intersection performance" means Table 5-17

Content defects:
- §5.4.3 (p.203) is **Aimsun** text in the Vissim chapter ("selected in Aimsun", "ARM is an
  AImsun algorithm"). §5 gives no Vissim-specific ramp-metering method.
- §5.5.4.1 (p.205–206) prints two near-duplicate paragraphs of the Cat-1 15-minute-interval
  rule, one about "vehicle inputs" and one about "demand profiles".
- §5.2.5.3 (p.190) and §5.3.4 (p.197) both govern desired speed but neither cross-references
  the other; a generator must apply both (profile shape by speed limit; marker placement at
  posted-limit signs).
- §5.4.2.5 (p.202) "should be **stalled** in the parameters section" — presumed typo for
  "stored".
- §5.4.1 Table 5-9 (p.198) Left-hand-turn description "Requiring a to slow" is missing a word.

---

## INVENTORY

**Subsections covered (all of §5, doc p.179–221):** 5.1, 5.1.1, 5.1.2, 5.2, 5.2.1, 5.2.1.1,
5.2.1.2, 5.2.2, 5.2.3, 5.2.3.1, 5.2.3.2, 5.2.3.3, 5.2.3.4, 5.2.3.5, 5.2.4, 5.2.5, 5.2.5.1,
5.2.5.2, 5.2.5.3, 5.3, 5.3.1, 5.3.1.1, 5.3.1.2, 5.3.1.3, 5.3.2, 5.3.2.1, 5.3.2.2, 5.3.3,
5.3.3.1, 5.3.3.2, 5.3.3.3, 5.3.3.4, 5.3.3.5, 5.3.4, 5.3.5, 5.4, 5.4.1, 5.4.1.1, 5.4.1.2,
5.4.2, 5.4.2.1, 5.4.2.2, 5.4.2.3, 5.4.2.4, 5.4.2.5, 5.4.2.6, 5.4.3, 5.4.3.1, 5.4.3.2, 5.4.3.3,
5.5, 5.5.1, 5.5.2, 5.5.3, 5.5.4, 5.5.4.1, 5.5.4.2, 5.5.5, 5.5.5.1, 5.5.5.2, 5.6, 5.6.1,
5.6.1.1, 5.6.1.2, 5.6.1.3, 5.6.1.4, 5.6.1.5, 5.6.2, 5.6.2.1, 5.6.2.2, 5.6.3, 5.6.3.1, 5.6.3.2,
5.6.3.3, 5.6.3.4, 5.6.3.5, 5.6.3.6, 5.7, 5.8, 5.8.1, 5.8.1.1, 5.8.1.2, 5.8.1.3, 5.8.1.4.
**= 84 subsections, complete — no gaps.**

**Tables transcribed: 17 of 17** (Tables 5-1 … 5-17). Every table printed in §5 is transcribed
in full. Plus, in the cross-check block only: §2.12.2.1 category definitions (prose) and the
§2.12.2.2 GEH formula.

**Figures skipped (content not in the text layer): 32** — Figures 5-1 … 5-32, on doc pages
180, 181, 181, 183, 186, 188, 189, 190, 192, 193, 194, 195, 195, 196, 196, 197, 200, 200, 205,
206, 206, 207, 208, 208, 209, 212, 214, 215, 216, 217, 218, 218. Plus 1 figure-column inside a
table (Table 5-9's "Diagram" column, p.198). **Figure 5-30 (p.217) is partially recovered** —
its axis labels and 5-band km/h legend survived in the text layer.

Figures whose loss is **materially damaging** (they carry content the prose does not restate),
flagged for a future image pass:
1. **Figure 5-8 (p.190) "Recommended display types"** — the recommended colour/display mapping
   for link behaviour and lane types exists nowhere else in the text.
2. **Figure 5-5 (p.186) "Recommended Attributes for Vehicle Types"** — the recommended
   attribute values for the 12 vehicle types.
3. **Figures 5-31/5-32 (p.218)** — the scenario/modification **naming convention**, which §5.7
   requires be "consistent" but never spells out.
4. **Figure 5-1 (p.180) "Recommended vehicle behaviours"** — the Network Settings values beyond
   "left-hand traffic".
5. **Figure 5-11 (p.194)** — merging-section geometry (Table 5-7 recovers the attributes but
   not the layout).

**Uncertain extractions: 3** (plus 2 in the cross-check block, from §2, out of §5 scope)
- `[EXTRACTION UNCERTAIN p.182]` Table 5-1 row "1 Short" — one value missing; Axles column
  unrecoverable. Raw: `1 Short  1 or 2 Short  (up to 5.5 m)`
- `[EXTRACTION UNCERTAIN p.182]` Table 5-1 "6≥" / "6>" glyph-order artifacts (rows 9–12)
- `[EXTRACTION UNCERTAIN p.198]` Table 5-9 Left-hand turn description — raw:
  `Requiring a to slow` (a word is missing)
- (§2) `[EXTRACTION UNCERTAIN p.44]` GEH formula fraction bar / exponent lost
- (§2) `[EXTRACTION LOST p.44]` Table 2-3 tick marks entirely absent
