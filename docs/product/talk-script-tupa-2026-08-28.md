# TUPA 발표 대본 + Q&A 대비 — 2026-08-28

> 슬라이드 14장 · 발표 약 16분 + Q&A. 말은 영어(구어체 그대로 읽어도 되게 씀),
> 지문은 한국어. ⏱ 는 누적 시간 가이드. 덱에서 빠진 세 가지(거부 사례 · 숫자 요약 ·
> 클로징 인용문)는 말로 싣도록 대본에 박아 두었다 — 슬라이드를 복원했다면 그 장에서
> 그대로 읽으면 된다.

---

## 발표 대본

### 1 — Title ⏱ 0:00–0:45

> Hi everyone. I'm Yunha Lee, and today I want to show you a system whose best
> feature is that it says **no**.
>
> It's an LLM agent that builds and edits traffic microsimulation models for PTV
> Vissim — and the reason it's interesting is not the building part. It's that
> every layer of it is allowed to refuse, and to tell you exactly why.

[지문: 여기서 멈추지 말고 바로 다음 장으로. 첫 45초는 논지 선언만.]

### 2 — The problem: Vissim fails silently ⏱ 0:45–2:15

> Here's the core problem. In Vissim, a wrong edit still produces a model that
> runs, and prints numbers. There's no crash. No error. The real risk is never
> a crash — it's a **plausible wrong answer**, and the simulator does not mark
> the difference. So the check has to come from outside the simulator.
>
> There is prior work on LLM agents driving traffic simulators — ChatSUMO Agent,
> published in Transportation Research Part C this year, and AgentSUMO from
> KAIST. Both drive SUMO. And SUMO's command line **fails loudly** — the agent
> gets an error signal for free. Vissim gives you nothing. So the error channel
> itself has to be built. Neither of those systems verifies that an edit
> actually took effect, because SUMO never made them need to.
>
> That's the design consequence in the box: **the validation layer is the
> product.** Everything else in this talk follows from that one sentence.

[지문: 표의 "Read back after every write" 행을 가리키기. 이 장이 논문 프레이밍의 핵심.]

### 3 — The pipeline ⏱ 2:15–3:15

> This is the whole consultant pipeline: scoping, data, build, calibrate,
> analyze, report. The colored scenes are what I'll actually show you live
> today. One stage is dimmed — scoping — because we don't claim it yet. And
> calibration has a warm outline, because I'll show you the surface, but the
> field-validated result doesn't exist yet. I'll say that again when we get
> there — the deck marks its own limits on purpose.

[지문: 다이어그램의 흐림/웜 표시를 손으로 짚기 — "정직한 부재"를 시각으로 먼저 각인.]

### 4 — Demo scene 1: network generation from a map ⏱ 3:15–5:00

[지문: 시퀀스 영상 재생. 영상 위에 말 얹기 — 아래 문장을 영상 진행에 맞춰.]

> You draw an area on a map. The system pulls an OpenStreetMap extract, builds a
> geometry draft and a connectivity layer, and emits a single `.inpx` file —
> the native Vissim network format.
>
> And here's the part I care about: **PTV Vissim actually loads it.** We verify
> that live — a real Vissim instance opens the file, we check its error file is
> empty, and we take a census of the COM objects and compare it against what we
> emitted. Loading is not assumed. It's proven, per build.
>
> After the build, the agent can propose edits — and a human accepts or rejects
> each one. No edit happens on its own.

### 5 — The agent loop ⏱ 5:00–6:45

> So how does one edit actually happen? Six gates sit between an intention and
> a write.
>
> The agent plans an edit and states its reason. It calls one of **87
> registered tools** — every tool has a typed schema, unknown parameters are
> rejected at dispatch. Before anything touches Vissim, the proposed edit goes
> through a **constraint catalogue — 117 named constraints**, each one carrying
> a machine-readable reason code, in Korean, English, and Japanese.
>
> Then a human approves — no write executes without a recorded approval. The
> edit is applied over COM, and then — this is the step the SUMO agents don't
> have — the value is **read back** from Vissim and compared against what we
> wrote. If it doesn't match, we roll back, and the append-only trace keeps the
> whole history.
>
> This loop is why refusing is cheap. **A refusal costs one message. A wrong
> edit costs a whole study.**

[지문: 다이어그램의 웜 색 탈출구 3개(REFUSE/REJECTED/MISMATCH)를 가리키며 마지막 문장.]

### 6 — Four validation surfaces ⏱ 6:45–8:00

> Stepping back — the validation layer is actually four different surfaces, and
> each one guards a different moment in time.
>
> The constraint catalogue checks a proposed edit **before** Vissim is touched.
> Read-back verification checks **after** a write. The generation gate judges a
> **built network** before it's published. And the reality check compares model
> volumes against observed counts **after a run**, with the standard GEH
> threshold of five.
>
> Four different questions: is this edit legal, did it actually take effect, is
> this network sound, does the model match reality. And each surface is allowed
> to answer **no** — with a name attached.

### 7 — The generation gate, V1 to V9 ⏱ 8:00–9:30

> The gate is nine checks in a fixed order, in two tiers. Six can refuse a
> network outright — the load oracle, reference integrity, connectivity, lane
> continuity, signal consistency, demand coverage. Three can only annotate —
> they warn, with the reason written down, but never block.
>
> V1, the load oracle, is my favorite: a **real** PTV Vissim opens the file.
> It's the only check that can prove loadability, so nothing else is allowed to
> claim it.
>
> And the verdict has three values, not two. Pass, refused — and **incomplete**.
> A check that could not run is never counted as a pass. If V1 had no live
> Vissim available, the verdict says INCOMPLETE with V1 named. That distinction
> — between "checked and fine" and "not checked" — runs through the entire
> system.
>
> Across the nine checks there are 78 distinct coded messages, so when the gate
> refuses, it refuses **specifically**.

### 8 — Demo scene 2: data intake ⏱ 9:30–10:15

> Real data comes in through format adapters. What you see here is the intake
> surface for Daejeon smart-intersection traffic counts — fifteen-minute
> vehicle counts by movement and vehicle class — and the same layer has an
> adapter for WA SCATS. A new city is a new adapter, not a rewrite.
>
> The adapter behaves like everything else: a blank cell is "not observed",
> never zero. It refuses to invent a record that isn't in the sheet.

### 9 — Demo scene 3: the simulation is real ⏱ 10:15–11:30

[지문: 두 번째 영상. Vissim 창이 뜨는 장면에서 첫 문장.]

> I want to be explicit about this: **this is not a mock.** PTV Vissim 2026 is
> being driven over COM, the run goes through a job queue, and the result
> databases are read back after the run.
>
> Working with COM taught us four safety rules the hard way — the automation
> never attaches to a running Vissim GUI, because that would take over a
> person's open work. Every job clones the network into its own working
> directory — the active dataset is never edited in place. Batch runs use
> Vissim's continuous mode. And all COM work goes through the queue, never
> inside a web request.

### 10 — Demo scene 4: dashboard curation ⏱ 11:30–12:15

> After a run, we don't print every chart we can compute. An LLM selects which
> charts actually answer the current question. The full data is always there —
> curation chooses what's **on top**, it never deletes. That's the same
> honesty rule, applied to attention instead of data.

### 11 — Demo scene 5: calibration surface ⏱ 12:15–13:45

> This is the calibration surface. The thirteen steps you see on the rail are
> the client authority's own operational-modelling ladder — Main Roads Western
> Australia's guideline — present in the UI, step by step. Seven of them carry
> machine evidence; six are signed off by a human, and the UI keeps those two
> kinds of confirmation visibly distinct.
>
> I want to show you my favorite behavior of the whole system here. When you
> ask the AI for a calibration proposal, there are cases where it refuses. If a
> node misses the observed count but no vehicle input was ever named as a
> target, the answer is `NO_TARGET_OBJECT` — it won't invent a knob to turn. If
> the model already reproduces the observed behaviour, the answer is
> `MODEL_REPRODUCES_OBSERVED` — no change proposed. And if you ask it to edit
> an entrance volume that is itself a **measured observation**, it refuses
> structurally: measured data is the target, not a knob.
>
> **A refusal is a result.** That's the intellectual core of this work.
>
> And the honest limit, on the slide: this surface has not yet been validated
> against a real-world field study. The scoring logic and the refusal logic are
> in place. The validated calibration is the next milestone — it is not claimed
> today.

### 12 — Demo scene 6: reports ⏱ 13:45–14:45

> At the end, reports. Consulting firms have house formats, so the system
> inherits them: upload one past report as a `.docx`, and it extracts a style
> profile — palette, font ladder, page geometry, heading numbering, table
> styling — and applies it to the generated report. It reads styles that are
> actually **used** in the document, not just defined, so a leftover template
> style doesn't get mistaken for the firm's format.
>
> And the report spine is the authority's required section structure. A section
> without evidence is **printed as missing, with the reason** — never quietly
> dropped. The report tells you what it doesn't know.

### 13 — Future work ⏱ 14:45–15:45

> Where this goes next. A Vissim MCP server — there is no public one today, and
> it would let any agent client drive Vissim while the constraint catalogue,
> read-back, and approval gate stay in force; the safety layer travels with the
> tools. A no-validation ablation — turn the constraint layer off and **measure**
> how many silently wrong edits get through; that turns "validation is the
> product" from a design claim into a number, and it's the evaluation neither
> prior system reports. Validated calibration on field data. And more regions
> through the adapter layer.
>
> For scale: this is 87 tools, 117 constraints, a nine-check gate with 78
> finding codes, everything trilingual — built in six weeks. The refusals were
> the majority of that work, and they were the point.

### 14 — Thank you ⏱ 15:45–16:15

> I'll close with the design rule the whole system is built on:
>
> **"A refused edit with a clear reason beats a completed edit with wrong
> numbers."**
>
> Thank you — happy to take questions.

---

## Q&A 대비

> 4개 관점(교통 교수·LLM 연구자·시스템 엔지니어·회의적 심사자)에서 29문을 생성해
> 코드 실측으로 검증한 뒤 선별한 것. 답은 그대로 말해도 되는 영어 구어체.
> 모든 답의 공통 원칙: **한계를 먼저 스스로 말하면 공격이 무력화된다.**

### 0 — 손바닥 카드: 절대 흔들리면 안 되는 세 문장

1. **"Validated machinery exists; a validated model does not."** 검증 *기계*는 있고
   테스트돼 있다 — 실측 데이터로 *검증된 모델*은 아직 없다. 이 둘을 섞는 순간 전체
   신뢰가 무너진다.
2. **"Our own gate refused our own first full build — and both findings were real."**
   부끄러운 일이 아니라 논지가 작동한 증거. 단, 생성기의 성공담으로 포장하지 말 것.
3. **"Where the machine can't verify, it refuses or asks a named human — it never
   pretends."** NOT_CHECKABLE, 사람 서명 6단계, 명명 거부 — 전부 이 한 문장의 변주.

### 1 — 최다 출제 예상 Top 8

**Q1. "Have you calibrated a single model against real field counts? Seed policy?
Convergence criterion?"** (교수형 — 가장 위험)

> Honest answer: no. The calibration surface and its refusal logic exist and are
> tested; calibration against real field counts has not happened — that's a named
> limit, not a footnote. What does exist: GEH is one shared implementation with the
> MRWA/DMRB bands — good under 5, fair under 10 — and it returns "undefined", never
> a number, when the statistic can't be computed. Seed discipline is explicit: one
> RunContinuous per seed with NumRuns pinned to 1, because we measured that a network
> shipping NumRuns=5 silently turns one seed into five result databases. And the
> reality check refuses to compare a multi-seed aggregate against a single period's
> counts. There is no convergence criterion yet — that's honest future work.

**Q2. "Your gate refused your own build. Isn't that just a broken builder?"**
(4관점 전원이 다른 문장으로 물음 — 반드시 나옴)

> The two refusals are specific, real defects, both manually confirmed: V9 — queue
> counters were genuinely not emitted at the signal stop lines, so any LOS claim from
> that model would have been unmeasurable exactly where it matters; V5 — a signal
> head placement signalised a neighboring node whose own entry meets no head. Both
> are silent in Vissim itself: the file loads, the run completes, plausible numbers
> come out. That's the thesis demonstrated on ourselves. The fix goes into the
> generator, not the gate — and I'd rather present a refusal with two named fault
> codes than a green demo I can't defend.
>
> [금지: 이 거부를 "예정된 데모"였다고 말하지 말 것 — 진짜 첫 빌드 실패였고, 그렇게
> 말해야 강하다. 생성기가 게이트를 통과한다고도 말하지 말 것 — 아직 못 한다.]

**Q3. "Strip the LLM out — everything you showed is deterministic Python. What does
the LLM actually do?"** (ML 연구자형)

> I accept the premise — it's the design. The LLM has deliberately zero authority
> over correctness: all 87 tools take typed schemas where unknown parameters are
> rejected at dispatch, so it can only choose among vetted operations. What it
> contributes is what deterministic code can't: turning an underspecified consultant
> request in Korean into a multi-step tool plan over live model state, and explaining
> a refusal in the user's own language. The research object is really the boundary
> placement: how much authority can you give an LLM over a simulator that fails
> silently? Our answer: propose and sequence — never assert.

**Q4. "ChatSUMO and AgentSUMO already exist. What's new here besides guardrails?"**

> I claim no novelty in the agent loop — typed tool-use is standard. The novelty is
> what the substrate forces. SUMO's CLI fails loudly, so those systems get an error
> signal for free and can treat "it ran" as weak evidence — and notably, neither
> reads back its own edits. Vissim accepts wrong input silently, so "it ran" is
> evidence of nothing, and the contribution is the verification architecture that
> substitutes for the missing error channel: per-write read-back, a shadow model,
> a tiered gate whose verdict distinguishes REFUSED from INCOMPLETE, and
> NOT_CHECKABLE as a first-class answer. Plus one thing neither paper does: the
> domain's own standard — MRWA's 13-step calibration ladder — rendered as machine
> state, with machine evidence explicitly outranking human testimony.

**Q5. "OSM has no turn pockets, no storage lanes, one centreline for a dual
carriageway. Why is an OSM network more than a toy?"** (교수형)

> We agree — and the system says so in code. There's a benchmark module that compares
> an OSM draft against a real consultant model and documents exactly those failure
> modes: missing turn pockets, single centrelines, corridor cut at every node. The
> gate is the enforcement: refuse-tier checks for connectivity, lane continuity and
> signal-geometry consistency, and only a live Vissim load can ever produce a PASS.
> The claim is narrower than "OSM is good enough": when the geometry is wrong, you
> get a named fault code instead of a plausible-looking wrong model.

**Q6. "Can you model a real Daejeon intersection end to end, today?"** (한계 2연타
— 현시>링크 + 화물_소형. 먼저 스스로 말하는 게 최선)

> Not yet, and I'll name both blockers before you find them. One: the binding model
> is one phase per approach link, so any plan with more phases than the node has
> entry links — which real 4-to-5-phase Daejeon plans are — is mathematically
> uncoverable; the system refuses rather than mis-binds. Two: one Daejeon vehicle
> class, small freight, is deliberately unmapped because calling it light or heavy
> would decide a question the data doesn't answer — and the demand layer refuses
> unmapped classes rather than silently reweighting the composition. Both failures
> are loud, named refusals instead of silent mis-mappings — which is the thesis —
> but the honest generalization status is: zero regions pass end-to-end today.

**Q7. "Where's the evaluation? What's N? What would falsify the thesis?"**

> There is no task-success benchmark yet — that's the first thing to say. What
> exists: about 5,800 deterministic backend tests pinning behavior including refusal
> wording in three languages, an adversarial refuter protocol where every feature
> ships only after a separate pass tries to break it, and exactly two end-to-end
> datapoints, both reported: a live 3600-second run producing LOS F at 230 seconds
> of delay with the capacity-limit refusal firing correctly, and the first full
> build, which our gate refused. The thesis is falsifiable two ways: if refused
> networks turn out to run fine and match counts, the layer is friction, not
> product; if passed networks carry the same silent wrong-numbers defects, it adds
> nothing. The decisive experiment is fault injection measuring both rates — it's
> designed, and not yet run.

**Q8. "Is the LLM free to twiddle Wiedemann parameters until GEH goes under 5?"**
(교수형 — 이 답이 제일 세다)

> No — structurally, not by prompt. The parameter search space **excludes** the
> Wiedemann car-following set; the table in the code marks those parameters
> "EXCLUDED — a measured saturation flow is required." The only path that opens them
> is a car-following grant: measure stop-line saturation flow first, then apply the
> published Table 5-6 values — and the refusal message says exactly that, in three
> languages. The agent can't calibrate by knob-twiddling; it can only walk the
> ladder, and the steps a machine can't verify require a named human to attest.

### 2 — 관점별 심화

**교통 교수가 더 파면:**
- *"Node-level GEH is easy; do you validate per movement?"* → Per-movement exists
  and runs when the caller supplies the movement mapping — workbook turn tokens
  are never guessed onto Vissim objects. When a comparison can't be made, the
  answer is NOT_CHECKABLE with exactly one reason code, never a silent pass.
- *"A designer, six weeks, an LLM — why trust it? Isn't this deskilling?"* → The
  premise of the design is that neither I nor the LLM should be trusted — that's
  why every write is verified against Vissim itself or refused. And the refusals
  are the anti-deskilling mechanism: it will not let a non-engineer push an
  unverifiable model through. Our Daejeon phase-diagram delivery shipped with zero
  attestations and *said so* — because there was no verifiable binding to attest.
  Open modeling judgments are parked as named human decisions, not automated.

**ML 연구자가 더 파면:**
- *"Read-back catches transport errors. What about plausible-but-wrong values —
  the actual hallucination mode?"* → Three layers: read-back catches silent write
  drops; the constraint catalogue and gate catch structurally wrong values; and
  the only true semantic defense is the reality check against counts — which is
  exactly the layer not yet validated on field data. The claim is not
  "hallucinations can't reach the file"; it's "a hallucinated edit cannot land
  silently — it lands traced, attested, and subject to a check that names its own
  blind spots."
- *"Reproducibility? Prompt sensitivity?"* → Below the LLM: deterministic by
  construction, ambiguity refuses rather than samples. Above it: plan-level
  variance is real and unmeasured — no seed-controlled repeat study, and I won't
  dress up anecdotal stability as a result. What I offer instead is auditability —
  append-only trace, SHA-tied attestations — which is not the same thing, and I
  keep the two words separate.
- *"Why the Claude API, not a fine-tuned open model?"* → No training corpus of
  expert Vissim-COM trajectories exists; the architecture deliberately moves
  domain knowledge out of weights into checkable, testable code; and the tool
  layer is model-agnostic, so the API is a dependency, not a commitment. Cost and
  latency are not benchmarked — I won't improvise numbers.
- *"False-refusal rate of the gate?"* → Not measured — N is one build, refused,
  both findings confirmed true by hand. What the design guarantees is that a false
  refusal, when it happens, arrives as a specific falsifiable claim against the
  file — a named code you can check — not a vibe.

**시스템 엔지니어가 더 파면:**
- *"Read-back reads the same in-memory COM object. What does it prove?"* → Exactly
  one thing, and the thing Vissim is worst at: silent clamp-or-ignore on setter
  calls. Persistence is a separate layer — there's a file-level check and a shadow
  model diff for cross-object side effects, and for the one path where disk
  persistence was the real risk, TOD program switching, we verified with a live
  SaveNetAs roundtrip, not a COM re-read.
- *"Vissim 2027 ships — what breaks?"* → Honest two-parter: a version-pin
  constraint exists in the catalogue, but the runtime field that feeds it isn't
  wired yet — that gap is marked in the code itself. Every behavioral threshold is
  probe-derived with the probe script named next to it, so re-qualifying on a new
  version means re-running named scripts, not archaeology.
- *"What stops two requests touching Vissim concurrently?"* → A serialization
  discipline enforced by structure: all COM goes through one job-queue worker,
  request handlers read the shadow model or files, every job clones its network
  into an isolated directory, and we never attach to a running GUI. I'll own that
  it's discipline, not a type-system guarantee — one machine, one Vissim, jobs
  run serially, accepted deliberately.
- *"How many of your 5,800 tests touch real Vissim?"* → In CI: zero, by rule —
  non-COM code must run on synthetic data. Live Vissim evidence is a separate
  manual lane of probe scripts and live checks, and the CI fixtures are
  transcribed from those probes. So CI tests "does the code still match what
  Vissim 2026 measurably did" — which is also exactly the seam that breaks on an
  upgrade.
- *"Scaling story for a real scenario sweep?"* → There isn't a horizontal one
  built, and I won't invent it on stage. Runs use continuous mode so each
  simulation runs at native speed; the serial queue is the correctness price of
  one COM instance. The isolation invariants make N-machines-N-workers an
  additive change — but that's a design with zero lines written, and parallel
  Vissim licensing hasn't been costed.

**회의적 심사자가 더 파면:**
- *"Research or product engineering?"* → Today: product engineering that
  generates a research question — closed-loop verified actuation against an API
  that fails silently, which the two prior systems never needed to solve. The
  architecture-forced-by-the-substrate argument is defensible; a validated result
  is not, and the paper is deferred until the evaluation runs.
- *"117, 87, 78 — inventory, not evaluation."* → Correct, and I'll say so before
  you do. Those numbers measure effort, not detection rates. The
  evidence-of-function so far: every refusal we've investigated was a true
  defect. The measured-rates study is the next deliverable.
- *"Who verifies the verifier? Your LLM wrote the tests too."* → I won't pretend
  the regress terminates cleanly. Three things break the worst of it: the ground
  truth for "did the edit land" is PTV's own binary, not our code; every unit
  passes a separate adversarial pass that attacks the just-written tests, and it
  has repeatedly broken "green" work; and none of that equals an independent
  audit — which this hasn't had.

### 3 — 절대 주장 금지 체크리스트 (하나라도 말하면 잡힌다)

- ~~"the system calibrates models" / "GEH-validated on Daejeon data"~~ → 기계는
  있고, 검증된 결과는 없다.
- ~~"freshly generated real-map networks pass the gate"~~ → 첫 풀 빌드는 거부됐고
  수정 진행 중.
- ~~"real Daejeon signals bind end-to-end" / overlap·lead-lag 모델링~~
- ~~"Daejeon counts become a demand layer today"~~ (화물_소형 미결)
- ~~task success rate · 게이트 precision/recall · 비용/지연 수치~~ (전부 미측정 —
  즉석에서 숫자 만들지 말 것)
- ~~"CI가 라이브 Vissim을 돌린다"~~ (CI는 0건 — 라이브는 수동 프로브 레인)
- ~~크로스 버전 재현성 · 런타임 버전 감지가 살아있다~~ (감지 상수는 있으나 미배선)
- ~~수평 확장 존재~~ (설계만, 코드 0줄)
- ~~"LLM이 검증을 수행/개선한다"~~ (LLM은 정확성에 권한 0)
- ~~외부 감사~~ (없음)

### 4 — 즉답용 숫자 카드

| 숫자 | 뜻 | 찔리면 |
|---|---|---|
| **117** | 제약 메시지, ko/en/ja 전부, reason code 동반 | "링크 삭제" 예시는 단순화 — 실제로는 참조 안전을 증명 못 해 삭제 전면 거부 |
| **87** | @tool 등록 도구, typed schema(extra 금지)+strict dispatch | — |
| **9 / 6+3** | V1–V9 · 거부 6 + 경고 3 | 경고 계열도 내부적으로 fail은 하되 verdict를 막지 않음 |
| **78** | V1–V9에 걸친 코드화 메시지 | "결함 코드"만 세면 41 — "78 coded messages"가 정확한 말 |
| **GEH 5 / 10** | good/fair 경계, MRWA·DMRB 준거, 단일 상수 | per-movement는 movement 매핑 제공 시 |
| **13 / 7+6** | MRWA §2.12.1 사다리 · 기계증거 7 + 사람서명 6 | 서명은 여섯 번째 상태로 구분 렌더 — 증거가 증언을 이김 |
| **~5,800** | 백엔드 테스트 (전부 그린) | CI에서 라이브 COM은 0건 — 합성 데이터 규칙 |
| **2** | 라이브 E2E 데이터포인트: 3600s LOS F 229.8s + 거부된 첫 풀 빌드 | 둘 다 있는 그대로 보고 |
| **6주** | 개발 기간 | "6주가 산 것은 아키텍처와 거부 기계 — 증거가 아니다" |
| 선행 | ChatSUMO Agent: *Transp. Res. C* 190 (2026) 105759 · AgentSUMO: arXiv:2511.06804 | 둘 다 SUMO(시끄러운 실패)·read-back 없음 |

