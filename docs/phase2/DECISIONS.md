# Phase 2 — Interface-gap decisions

Per the roadmap's gap protocol (docs/phase2/02-ROADMAP.md, "Sequencing notes for the
executor"): when a task reveals that an interface defined in the roadmap doesn't fit reality,
ship the closest verifiable subset, mark the gap in the task's commit body, and record it
here.

---

## 2026-07-22 — `signal_controller_type_unsupported` (#4) has no real controller-type source yet

**Context:** M0.3 (`backend/services/agent/constraints.py`). The roadmap says constraint #4
checks "controller type via `signal_parser.py`". Verified reality: `signal_parser.
parse_signal_controllers` returns `SignalInfo` (`sc_no`, `sc_name`, `cycle_time`,
`num_signal_groups`, `num_phases`, `phases`) — it exposes no controller-type attribute at all,
and the synthetic demo `.inpx` carries no such data either. There is currently no way to tell
a fixed-time controller from an RBC/VAP/Econolite one from parsed data.

**Decision:** the check reads `controller_type` from the shadow-model *signal* entry instead
(an agent-model field that M1.2's shadow-model extension will populate for real once the
lab-machine signal_parser extension lands). Missing the field, or an explicit
`"unknown"` value, is treated the same as an unsupported type: **refuse**. This is the
conservative reading of the guardrail doctrine in docs/phase2/00-EXECUTION-GUIDE.md §4 — "An
action with no possible verification read-back does not ship" — an unverifiable signal edit
is a silent-failure risk, so the absence of proof is treated as proof of absence.

**Consequence:** every `set_signal_cycle`/`set_signal_offset` action is refused until M1.2/M3
actually populates `controller_type: "fixed_time"` for a real controller — this is
intentionally the safe default, not a bug. Real controller-type extraction lands with the
M2/M3 `signal_parser.py` extension, to be verified on the lab machine (real Vissim COM access
is only available there).

---

## 2026-07-22 — `cycle_below_intergreen_sum` (#5) has no min-green/intergreen source yet

**Context:** M0.3 (`backend/services/agent/constraints.py`). The roadmap says constraint #5
uses "minimum green + intergreen times parsed for that controller". Verified reality:
`signal_parser.py` parses green-window *durations per phase* but never derives or exposes a
per-controller minimum-green + intergreen sum, and the synthetic demo `.inpx` has no such
attribute either.

**Decision:** the check reads `min_green_intergreen_sum` from the shadow-model signal entry
when present (same agent-model extension point as #4). When the field is **absent**, the
check returns `None` (silently skipped) rather than refusing. This differs from #4's
conservative refuse-on-missing stance because the underlying write (`set_signal_cycle`)
remains fully read-back-verifiable on its own — only this *semantic* screen (is the proposed
cycle physically long enough for the controller's timing plan) lacks its datum. Refusing an
otherwise-safe, verifiable write because of a missing secondary sanity check would be an
over-conservative regression relative to the doctrine, which reserves refusal for
unverifiable or genuinely unsafe actions.

**Consequence:** until M3.1 parses real min-green/intergreen sums, constraint #5 provides no
protection — a too-short cycle can be written and verified even though it may not be
physically achievable by the controller's timing plan. This gap is intentional and tracked
here, not silently absorbed; M3.1 must close it before this constraint is meaningful against
real data.

---

## 2026-07-22 — Tool dispatch rejects unknown params instead of filtering them

**Context:** M0.4 (`backend/services/agent/toolkit.py`). docs/phase2/01-ARCHITECTURE.md §4
says tool params are "validated + extraneous keys filtered before dispatch (ChatSUMO
pattern)". docs/phase2/02-ROADMAP.md's M0.4 task text explicitly overrides this: `dispatch`
must "validate (reject unknown/missing/mistyped — NOT key-filtering)".

**Decision:** the roadmap governs (controller-adjudicated). `dispatch()` validates every tool
call against a pydantic params model built with `model_config = ConfigDict(extra="forbid")` —
the `@tool` decorator refuses to register any params model that does not forbid extras, so
this cannot be bypassed per-tool. An unknown key, a missing required key, or a mistyped value
all produce the same structured `invalid_params` envelope (field + reason), never a silently
trimmed params dict passed through to the tool function.

**Consequence:** a planner (M1.3) that hallucinates a param name gets a machine-readable error
it can learn from and retry against, instead of the call silently succeeding with the bad
param dropped — the exact drift class (phantom tools, silently stripped params) this module
exists to kill per docs/phase2/01-ARCHITECTURE.md §1. The cost is that any legitimate but
not-yet-modeled param addition to a tool must update that tool's params model in lockstep, or
every call carrying it will be rejected; this is treated as a feature (registry is the single
source of truth), not a workaround-worthy limitation.

---

## 2026-07-22 — `generate_structured` shared logic lives in a new module, not in assistant_router.py

**Context:** M1.1 (`backend/services/claude_provider.py`, `backend/services/assistant_router.py`).
docs/phase2/02-ROADMAP.md's M1.1 task text says both "give `LocalQwenProvider` the same method"
(`generate_structured`) AND "assistant_router untouched except provider selection reuse" — but
`LocalQwenProvider` is defined *inside* `assistant_router.py`, so both cannot hold literally:
adding a method to that class is necessarily a touch to the file.

**Decision:** controller-adjudicated resolution — add only a thin `generate_structured` method
to the `LocalQwenProvider` class in `assistant_router.py` (co-located with the class it
extends, matching how `status()`/`generate()` already live there), while ALL shared logic
(the strict-JSON prompt builder, action-envelope validation, and the fallback-rung/meta
bookkeeping both providers report identically) lives in a new module
`backend/services/agent/structured.py`. Both `ClaudeProvider.generate_structured` and
`LocalQwenProvider.generate_structured` import `services.agent.structured` lazily inside the
method body, mirroring `claude_provider.py`'s existing lazy `import anthropic` pattern in
`_get_client()`; `backend/services/agent/__init__.py` is empty, so this introduces no import
cycle between `assistant_router.py` and `agent/`. The roadmap's "don't grow the monolith"
concern is read as being about new *logic* accumulating in `assistant_router.py`, not about
the file gaining zero new lines ever — a thin, delegating method satisfies both stated
constraints as closely as reality allows.

**Consequence:** `assistant_router.py` gains exactly one new method (no new imports at module
level beyond the two lazy ones inside it, no changes to routing/selection logic), while the
actual structured-generation behavior — prompt construction, `validate_action`, `build_meta` —
is centralized in `services/agent/structured.py` where M1.3's planner and any future provider
can reuse it without touching `assistant_router.py` again.

---

## 2026-07-22 — XML-tool-syntax salvage pass added to `generate_structured` (M1.1 fix)

**Context:** Live browser verification of M1.4 (the read-only agent's frontend wiring)
surfaced a real defect in M1.1's structured-provider seam: a native `claude-api` `tool_use`
call for the planner-terminal `respond` tool (docs/phase2 M1.3, `planner.py`'s
`_TERMINAL_TOOL_DEFS`) arrived at `fallback_rung 0` — i.e. accepted as clean on the first try —
with its `answer` param contaminated by XML-style tool syntax the model emitted instead of a
second native param: `answer` ended in `...</answer>\n<parameter name="summary">`-style text,
and the real `summary` value had landed inside `answer` instead of its own (empty) param. The
UI rendered the raw tags to the user. Reproduced verbatim from
`runtime/agent_sessions/e1dd1bdb76ee/trace_e1dd1bdb76ee.jsonl` (plan record, iteration 1).
`validate_action`'s existing structural checks (tool name known, params is a dict, schema
required-keys present) all passed, because `answer` was still present as a non-empty string —
it just wasn't the clean value that string was supposed to hold.

**Decision:** add `salvage_tool_params` (a pure function: given an action and the tool defs,
returns a new action with any string param's `</{param_name}>` artifact truncated and, where
present, a following serialized `<parameter name="X">VALUE` fragment recovered into `X` when
`X` is a declared schema property that is currently absent/empty — never overwriting a
non-empty existing value) plus `salvage_and_validate` (salvage-then-`validate_action`, same
`(result, reason)` shape) to `backend/services/agent/structured.py`. Both providers'
`generate_structured` now call `salvage_and_validate` instead of `validate_action` directly —
one shared choke point, per the M1.1 monolith-touch decision above, rather than duplicating a
salvage call at each of Claude's one call site (inside its retry loop) and Qwen's two
(json.loads path, `extract_json_object` rescue path). The pass is deliberately conservative:
it only ever touches a string value when the literal artifact marker (`</{param_name}>`,
`<parameter name="...">`, or a bare trailing `</parameter>`) is actually present in it, so
ordinary prose that happens to contain angle brackets (e.g. quoting HTML) is never altered —
verified by a dedicated test alongside the exact-observed-contamination regression test.

**Consequence:** the observed contamination is now repaired before `validate_action` ever
sees it, so a well-formed-but-hijacked native response like the trace example is accepted
clean (or, when genuinely malformed, still flows into the existing retry/fallback ladder) —
the user never again sees raw `</answer>`/`<parameter>` tags for this failure mode. This is a
salvage, not a root-cause fix for why the model occasionally emits XML tool syntax instead of
a second native param; if a future model/prompt change surfaces a *new* contamination shape not
covered by these three checks, that would need its own follow-up entry here.

---

## 2026-07-22 — M1.4: agent mode ships behind an explicit UI toggle, default off

**Context:** M1.4 (`frontend/components/FloatingAssistant.tsx`, `backend/routers/assistant.py`).
The read-only agent (M1.3's `run_agent_turn`) is now reachable from the UI via a new
`POST /api/assistant/agent/query/stream` endpoint. The question was whether every prompt should
route to the agent once it exists, or whether it should be opt-in.

**Decision:** agent mode is an explicit opt-in toggle in `FloatingAssistant` — a small
text-labeled checkbox in the assistant header, default **off** — not the default seam for every
prompt. The existing intent pipeline (curation via `/api/assistant/query` and `/query/stream`,
including the regex-based simulation-command path and its propose→confirm→execute flow) keeps
working completely unchanged when the toggle is off; this was verified by asserting the
existing frontend contract tests and `npx tsc --noEmit` show zero new failures/errors with the
toggle defaulting to `false`.

**Consequence:** routing every prompt to the read-only agent today would regress simulation
commands to refusals — M1's agent can only read, and the tier gate structurally refuses any
write/run tool, so a user asking to change a vehicle input or run a simulation would get a
polite "I can't do that yet" instead of the working propose→confirm→execute flow. Keeping the
agent behind an explicit, clearly-labeled beta toggle avoids that regression while still making
the read-only agent reachable for evaluation. This toggle is expected to flip to on-by-default
(or be removed in favor of the agent being the single seam) once M2/M3 give the agent write
capability behind the same approval flow the existing simulation path already has.

---

## 2026-07-22 — `salvage_tool_params` tightened to tail-anchored detection (reviewer fix)

**Context:** a reviewer stress-tested the XML-tool-syntax salvage pass added earlier the same
day (see the M1.1 fix entry above) and found the "conservative by construction" guarantee
failed in three reproducible ways, all in `backend/services/agent/structured.py`:

1. Using `value.find(close_tag)` (first occurrence) instead of the last: given an `answer`
   containing an earlier, legitimate mid-sentence mention of `</answer>` followed later by the
   real tail artifact, the function truncated at the FIRST occurrence, discarding real prose
   between the two.
2. Treating the bare presence of the `</{param_name}>` substring as sufficient evidence: an
   `answer` that merely mentions the tag once with nothing real after it (e.g. quoting it in
   prose) was truncated anyway, even though no `<parameter>` fragment followed to recover.
3. `_DANGLING_TAG_RE.sub(...).rstrip()` applied `.rstrip()` unconditionally, so a value with
   ordinary trailing whitespace and no artifact at all came back as a new, mutated string
   object — breaking the documented byte-identical-passthrough guarantee for a clean action.

**Decision:** tightened the detection to be tail-anchored rather than substring-anchored.
`salvage_tool_params` now: (a) finds the LAST occurrence of `</{param_name}>` via `rfind`, never
`find`, so an earlier legitimate mention is preserved untouched in the kept prefix; (b) requires
everything AFTER that last occurrence to decompose ENTIRELY into whitespace and/or valid
`<parameter name="X">VALUE` fragments (new `_decompose_salvage_tail` helper) with at least one
fragment actually present — an empty remainder, a whitespace-only remainder, or any leftover
real prose means "not contamination," and the value is left completely untouched; only a
full, unambiguous tail match triggers truncation; (c) only applies `.rstrip()` after the
dangling-tag regex actually matched something (checked via `after != before` first), so a clean
value's trailing whitespace is never touched. All three reviewer repros were added as regression
tests in `backend/tests/test_agent_structured.py` and independently verified as RED against the
prior version (reverted to `HEAD` and re-run) before being fixed.

**Consequence:** the salvage pass is now strictly more conservative — it will decline to salvage
a small number of genuine artifacts that don't perfectly tail-anchor (e.g. a model that emits
real prose *after* the serialized parameter fragment, or an artifact with no recoverable
fragment following it, such as a lone stray `</answer>`), preferring a false negative (leaving
a benign-looking tag in the answer) over a false positive (eating real user-facing content).
This trade-off is intentional per the guardrail doctrine's general bias toward "verify before
acting" — the same reasoning already applied to constraint checks in this file.

---

## Deferred findings from the M0+M1 whole-branch review (2026-07-22)

**Context:** the whole-branch pre-merge review of M0+M1 surfaced findings that are out of scope
for the review's own fix pass but must not get lost before the M2/M3 executor picks the work
back up. Recorded here, each tagged with its target milestone, so the executor inherits them by
name instead of relying on tribal memory.

- **M2.3 (BLOCKING for that task):** `claude_provider`'s retry rung must add a `tool_result`
  (`is_error`) block for a failed `tool_use` id — the current assistant-content echo becomes an
  API 400 once schema validation can reject a present `tool_use` block.
- **M2 ops:** `runtime/agent_sessions` has no cleanup/TTL and turns keep running after client
  disconnect — add a startup sweep plus a cancellation check between loop iterations.
- **M3.3 (BLOCKING for that task):** `trace.py`'s `session_id` must be sanitized before
  state-reload-by-session-id ships; also note payload-by-reference and no file-locking as known
  single-writer assumptions.
- **M2.2:** `ArtifactManager` wiring must add the clone-precondition-guard test and decide on
  `shadow_model` defensive copying; the version counter is single-instance by design.
- **M1.1 test debt:** the two-tool `cache_control` placement test (last-block-only) is still
  missing.
- **Future multi-dataset work:** `tools_network`'s module-global active-inpx becomes a real
  concurrency bug once per-session dataset selection lands.

---

## 2026-07-23 — M2 ops: `main.py` had no existing lifespan/on_event idiom to match

**Context:** the M2-ops session-cleanup task said to wire `sweep_stale_sessions()` into
"`main.py`'s existing startup idiom (lifespan or on_event)". Verified reality: neither exists
anywhere in the repo — `main.py` had no FastAPI startup hook of any kind; the only "run at
boot" precedent is `routers/assistant.py`'s module-import-time `_preload_assistant_model()`.

**Decision:** added a `lifespan` async-context-manager (FastAPI's current recommended
mechanism), wrapped in try/except so a failed sweep can never block startup.
`@app.on_event("startup")` was tried first and works identically, but it was the sole source
of `DeprecationWarning`s in an otherwise 0-warning, 211-test backend suite, so `lifespan=`
was kept.

**Consequence:** `main.py` now has a `lifespan=` app; future startup/shutdown hooks should be
added to that same context manager rather than introducing `on_event` alongside it.

---

## 2026-07-23 — M2.1: `CycleTime` was never a valid COM attribute; `CycTm` is disconnected from the real program cycle

**Context:** building `get_signal_cycle` required reading what `set_signal_cycle` writes:
`SetAttValue("CycleTime", …)`. Verified live on Vissim 2026 (first live-COM exercise of this
verb ever — M0/M1 were read-only): `CycleTime` does not exist ("Could not find attribute with
ID CYCLETIME") — **`set_signal_cycle` has always raised**. The correct id is `CycTm`, readable
everywhere but (a) **read-only on program-driven fixed-time controllers** ("Attribute Cycle
time cannot be changed" on the Beijing example — cycle lives in the opaque base64
`intSupplyData` blob `signal_parser.py` decodes), and (b) even where writable
(`FIXEDTIMESIMPLE`, Air Taxi Vertiport example), **not the field** `signal_parser`/
`build_shadow_model` report as `cycle_time` — that value has no COM read or write path at all.

**Decision:** fixed the id (`CycleTime` → `CycTm`) in setter + getter — a bug fix, not a
redesign. The smoke tool's persistence check tries the roadmap's named route
(`build_shadow_model` reparse) first and falls back to a direct XML/COM re-get of `CycTm`,
which still proves `save_net_as` + the round-trip. Vissim's own refusal on program-driven
controllers surfaces as a normal `RuntimeError` — the guardrail doctrine working as intended.

**Consequence:** `set_signal_cycle`/`get_signal_cycle` are functional for the first time, but
only ever reach the `CycTm` rollup — never the real simulated cycle for opaque-blob
program-driven controllers, which is the common real-world case. M2.3+ must not treat a
successful `set_signal_cycle` as proof the simulated cycle changed; this corroborates
constraint #4's conservative refuse-on-missing-`controller_type` stance, and M3 should
additionally gate on whether the target's cycle is program-driven (a separate per-controller
fact today's shadow model doesn't expose either).

---

## 2026-07-23 — M2.1: `save_net_as` actively refuses to overwrite the loaded source

**Context:** the task said `save_net_as` must "never silently overwrite the loaded source
network file" without specifying enforcement vs documentation. Every other job-work-dir
safety rule (00-EXECUTION-GUIDE.md rule 4) is currently caller-discipline only.

**Decision:** active enforcement — `VissimController` tracks `_loaded_net_path` (set in
`load_network`); `save_net_as` raises `ValueError` on a case-insensitive absolute-path match
before touching COM.

**Consequence:** M2.2's `ArtifactManager` wiring and M2.3's write tools inherit this
protection automatically. The guard covers only the currently-loaded network in this
controller instance's lifetime; constructing a correct work-dir path remains the caller's
responsibility.

---

## 2026-07-23 — M2.2: `"cancelled"` is an 11th trace kind beyond the roadmap's 10

**Context:** M0.1 enumerates exactly 10 trace kinds; none fits a caller-initiated stop
distinct from a tool/guardrail outcome. The cancellation seam (`should_cancel`, checked at
the top of every loop iteration, before planning — so a cancel costs zero planner/tool calls)
needs its own terminal record.

**Decision:** added `"cancelled"` to `ALLOWED_KINDS`; `run_agent_turn` returns
`AgentTurnResult(kind="cancelled", …)` with a deterministic ko/en/ja notice (no LLM call).

**Consequence:** the kind catalog is 11, not 10. M5's eval harness should treat `"cancelled"`
as a first-class non-failure terminal.

---

## 2026-07-23 — M2.2: clone-precondition guard is divergence-based, not existence-based

**Context:** the task brief's literal wording ("a work dir that already contains a working
copy must fail loudly") collides with the already-pinned
`test_new_manager_over_existing_work_dir_continues_numbering`, which re-clones over an
existing working copy on purpose (process-restart resume).

**Decision:** `clone()` refuses only when the existing working copy has *no saved version* or
has *diverged* from its latest saved version (not byte-identical); re-cloning over a
still-clean working copy stays allowed. Both the new guard tests and the pre-existing resume
test pass unmodified.

**Consequence:** the guard protects against silent loss of unsaved edits (the actual risk)
without breaking session resume — the "closest verifiable subset" reading of the brief.

---

## 2026-07-23 — M2.2: `rollback_to(0)` restores the pristine clone

**Context:** `last_good_version` is `0` for a session's very first write (nothing saved yet),
but `rollback_to(0)` raised `FileNotFoundError` (no `edit_000.inpx` exists) — the single most
common rollback case couldn't work.

**Decision:** `rollback_to(0)` restores directly from `source_inpx`, same as `clone()`. An
addition beyond the brief's literal text, made because the mandated postwrite-abort wiring is
not usably correct without it; pinned by state tests plus the orchestrator's
mismatch-rollback integration test, which exercises exactly this path.

---

## 2026-07-23 — M2.2: `build_shadow_model`'s mtime-keyed cache needs explicit cache-busting in `ArtifactManager`

**Context:** verified empirically on this Windows/NTFS box — two back-to-back writes to the
same path can report the *identical* `os.path.getmtime()`, so the `lru_cache` keyed on
(path, mtime) can serve a stale parse right after an edit.

**Decision:** `ArtifactManager` tracks `_last_parsed_mtime` per instance and nudges the
file's mtime strictly forward via `os.utime()` before reparsing whenever it wouldn't
otherwise advance. Deterministic regression test forces the collision via `os.utime` (not
timing luck). This is a machine-verified landmine of the 00-EXECUTION-GUIDE §3 class; fold
into that guide on its next docs pass.

---

## 2026-07-23 — M2.2: `guardrails._WRITE_TARGET_FIELD_SPEC` pre-declares M2.3 tool field mappings

**Context:** the nothing-else-moved diff must know which (object, field) pair each write tool
is *expected* to touch, but M2.3's tools don't exist yet.

**Decision:** pre-declared the table for the three object-targeting M2 verbs
(`set_vehicle_input_volume`, `set_signal_cycle`, `set_signal_offset`), deliberately mirrored
from (not imported out of) `constraints.py`'s private `_TOOL_TARGET_SPEC`, per the existing
M1.2 precedent. A tool absent from the table gets no exemption — any change anywhere counts
as evidence (conservative default).

**Consequence:** M2.3 must keep tool/param names in lockstep with this table or writes get
noisy (safe) false-positive diff evidence. Note the `set_signal_cycle` mapping targets the
parsed `cycle_time`, which the M2.1 `CycTm` entry shows is disconnected from what the setter
actually writes — moot today (constraint #4 refuses all signal-cycle edits), but M3 must
revisit this mapping when it re-enables them.

---

## 2026-07-23 — M2.2 controller-review addition: postwrite gains a target-persisted check

**Context:** review of the as-implemented engine found a hole: a write tool that succeeds
over COM but never calls `save_net_as` before returning passes both original postwrite checks
— the read-back compare proves only Vissim's *in-memory* state changed, and identical
before/after shadow models make the nothing-else-moved diff *silent* — while the versioned
artifact silently lacks the change. That is precisely the silent-failure class the doctrine
exists to kill.

**Decision:** `postwrite` now also verifies the written value actually appears in the AFTER
shadow model (same numeric tolerance as constraint #6; reuses the `write_verification_failed`
id with `persistence_check` details). Degrades silently when no claim can be made either way:
unmapped tool, no `written_value`, target absent (other checks own that), or the parsed field
is `None` (parser can't see that attribute — e.g. signal `cycle_time` on program-driven
controllers per the M2.1 finding — refusing there would be a false positive, not caution).

**Consequence:** M2.3's write tools MUST call `save_net_as` onto the working copy before
returning, or every mapped write is refused post-hoc and rolled back — the contract is now
enforced, not documented.

---

## 2026-07-23 — M2.3: `VissimEditSession` runs COM on the agent turn's worker thread, not the job queue

**Context:** 00-EXECUTION-GUIDE rule 6 says "all COM work goes through the job queue, never a
request handler." A write turn needs synchronous, low-latency COM interleaved with planning
inside one conversational stream — the async job queue's poll-for-completion shape would break
the propose → approve → verified-answer-in-one-stream UX the roadmap specifies.

**Decision:** `VissimEditSession` runs on the turn's dedicated worker thread (the existing
per-stream `threading.Thread`, unchanged since M1.4) with its own `CoInitialize`/instance/
`CoUninitialize` lifecycle and `attach_to_running=False`. The rule's intent — never COM on the
ASGI event-loop/request-handler path — is honored: COM never touches async code. A
`Semaphore(2)` caps concurrent edit sessions (Vissim ~4 instances/machine; runs need slots).

**Consequence:** M2.4 RUNS stay on the simulation job queue — long-running work still gets
queue semantics; this exception is for short parameter edits only.

---

## 2026-07-23 — M2.3: `set_desired_speed_area` deferred

**Context:** the roadmap's M2.3 verb list includes it (an XML-injection path, not a COM
setter). Controller decision: XML injection has different failure modes than COM SetAttValue
and deserves its own verification pass; the milestone acceptance doesn't need it.

**Decision:** not implemented; `tools_edit.py` ships the 5 COM verbs. `v1_system.md`'s
lane-closure refusal text explicitly notes reduced-speed areas are *not yet* agent-editable
rather than implying availability.

---

## 2026-07-23 — M2.3 live finding, fixed in controller review: the nothing-else-moved diff needed float tolerance

**Context:** live end-to-end verification (`tools/edit_tool_live_check.py`) showed every real
write refused with `write_verification_failed`, evidence `links#10039:points;
links#10051:points`. Root cause isolated: Vissim's own `SaveNetAs` re-serializes
auto-computed connector geometry with ~5.68e-14 per-coordinate drift on EVERY save — pure
float noise (identical point counts), tripping `_diff_object_list`'s exact `!=` compare. The
implementing agent could not fix it (guardrails.py was outside its scope) and proved via
`GuardrailEngine(disabled=True)` that the M2.3 plumbing itself was correct.

**Decision (controller):** `_values_differ` is now the single recursive comparison for both
postwrite checks — numeric pairs use the constraint-#6 1e-9 tolerance at any nesting depth;
structural differences (length, key set, type) remain strict evidence. Three regression tests
pin the exact observed noise (silent), a 0.5 m real move (flagged), and an added point
(flagged).

**Consequence:** re-ran the live check with the production engine: the write completes AND
verifies (`preflight → action → postwrite` clean). 1e-9 sits ~5 orders above the observed
noise and ~7 below any meaningful coordinate change; if a future network shows larger benign
re-serialization drift, tune with evidence, not by loosening structure checks.

---

## 2026-07-23 — M2.3: `/agent/approve` reconstructs state fresh; working-copy re-sync is version-aware

**Context:** the propose and approve calls are separate HTTP requests; reusing the literal
`ArtifactManager` object would need a cross-request in-memory cache, conflicting with the
file-based-storage principle. A second `clone()` also trips the M2.2 divergence guard.

**Decision:** `/agent/approve` builds a fresh `ArtifactManager` over the same work dir;
`_ensure_working_copy` re-syncs via public `rollback_to(artifact_manager.version)` — the
controller hardened this from the agent's `rollback_to(0)`: version-0 behavior is identical
today (every stream mints a fresh session id, so approve always sees a pristine dir), but
once M3.3 reloads sessions by id, a dir can arrive carrying approved verified edits
(version ≥ 1) and rolling to 0 would silently destroy them. `rollback_to(version)` is a no-op
for a clean copy and discards only unverified divergence (crash between `persist()` and
`save_version()`).

---

## 2026-07-23 — M2.3: no-session write errors surface as `tool_execution_error`, not a dedicated `error_type`

**Context:** the brief asked for `error_type: "no_edit_session"`. Verified: `toolkit.dispatch`
wraps any dict RETURNED by a tool as `status: "ok"` — an error-shaped return would make the
orchestrator treat a no-session write as landed and `save_version()` an unchanged file (a
silent-lie risk). Only a raised exception produces `status: "error"` (always labeled
`tool_execution_error`).

**Decision:** `_require_session()` raises `NoEditSessionError`; the message embeds the
greppable literal `"no_edit_session"`. Tests assert status + substring, not the
`error_type` value.

---

## 2026-07-23 — M2.3: `pending_approval.json` carries `message`/`language` beyond the brief's four fields

**Context:** the approve re-run must replay the original user message in the original
language; no other persistence mechanism exists in this stateless file-based design.

**Decision:** two additive fields. The frontend never reads this file directly — its contract
is the NDJSON `approval_request` event + `/agent/approve` — so the schema stays
backend-internal.

---

## 2026-07-23 — M2.4 decision batch (run gate + post-run detectors)

- **Run tier reuses the WriteGate machinery verbatim** (`_GATED_TIERS = {"write","run"}`) —
  `authorize(action, spec)` was already tier-agnostic; no parallel RunGate type.
- **`postrun` is called by orchestrator.py, not the tool** — mirrors the postwrite precedent;
  a tool cannot bypass the ablation flag by constructing its own engine.
- **`tools_run` has its own active-run-source module-global** (set/cleared alongside
  `set_active_inpx` in the same try/finally) rather than reaching into `tools_network`
  privates.
- **Multi-seed = ONE job with N sequential `seed_NN` sub-runs** — `results_reader` already
  averages every .db via rglob; parallel seeds deferred (Vissim ~4-instance ceiling).
- **`deleted_vehicles` is always `None` for now**: DiffusTm removals live only in the run's
  `.err` text log, never in the result .db (verified against the constraint catalog + every
  known table). `vehicles_deleted_in_run` cannot fire until an `.err` parser exists — tracked
  gap for M3.
- **`/agent/approve` builds a VissimEditSession only for write-tier actions** — run approvals
  never burn an edit-session slot.
- **Controller review fix: `_OFFERED_TIERS` now includes `"run"`** — the executor flagged that
  `run_simulation` was registered+gated but never offered natively to a live LLM while
  `render_contract()` already narrated it (prompt/capability mismatch). Offered-tools
  anti-drift pin updated deliberately (`EXPECTED_RUN_TOOLS`).

Live acceptance (run_gate_live_check.py, real Vissim 2026, Beijing clone): edit → 300s run
through the real job queue via RunContinuous → SIMULATIONRUN row verified → the
`results_missing_kpi_tables` warning FIRED on a genuine short run — the detector proven
against real Vissim behavior. Trace: plan → preflight → action_start → action_end → postrun
→ warning.

---

## 2026-07-24 — M3.2 decision batch (complexity tiers)

- **Tier classification is deterministic ko/en/ja keyword matching, not an LLM call** — zero
  per-turn cost, testable with canned providers; a "simple" misclassification is never unsafe
  (write/run approval is tier-independent). Promote to LLM-assisted only if misclassification
  proves real.
- **"Mandatory preview" is enforced structurally**: for complex/agentic tiers the planner
  refuses to let `respond` be a turn's FIRST decision — it substitutes a deterministic
  grounding read at zero provider cost. Simple tier exempt by design.
- **Agentic anti-exploit is a code-level answer post-processor**: every accumulated
  guardrail warning is unconditionally appended to an agentic turn's final answer regardless
  of the model's own text — a fired lie-detector can never be silently separated from a
  claimed improvement. Deliberately does NOT force guardrails on under
  AGENT_GUARDRAILS_DISABLED (that flag is the M5.3 ablation arm; the net degrades correctly
  to a no-op when ablated).

---

## 2026-07-24 — M5.1+M5.2 decision batch (eval suite + runner)

- **Task YAML gained provider-agnostic extensions**: optional `scripted_turns` for the CI
  provider (ignored under --real-provider), `refuse_constraint_id`, `must_not_warn`,
  `clarification_expected`, `answer_contains`, and a `source: verification` post-state
  variant for fields with no shadow-model presence (sim duration, random seed).
- **Write/run actions appear twice in scripted_turns** (propose, then re-issue after the
  approval pause re-runs a fresh planner) — preflight-refused actions once.
- **Demo network's signal controllers carry no intSupplyData** → shadow "signals" is []
  on the synthetic network; signal-edit tasks refuse via object_not_found as the closest
  verifiable substitute. Closing needs a make_demo_data extension or a fixture network.
- **CONFIRMED BLIND SPOT (queued fix, controller-owned):** the postwrite target-persisted
  check's degrade-silently-on-None policy misses a write silently DROPPED against a field
  whose parsed value was already None (demo vehicle-input volumes) — even with guardrails
  ON. Pinned by test_eval_runner's genuine-blind-spot fixture. Fix direction: per-field
  none-semantics in _WRITE_TARGET_FIELD_SPEC ("None = not persisted" for parseable-but-unset
  fields like volume vs "None = parser-blind" for cycle_time), flipping the fixture to
  caught. Lands right after the M3.1 catalog commit.
- PyYAML pinned in requirements.txt (was transitive-only; tasks_loader depends on it).

---

## 2026-07-24 — M3.1 decision batch (constraint catalog port, 8 → 43)

- **27 of 35 new constraints are CATALOG-GAP forward-ready checks**: fully implemented and
  fixture-tested against a documented-but-unpopulated field (conflict_areas, routes,
  evaluations, driving_behaviors, per-object optionals, run_result extras), silently
  skipping in production until the field exists — the #4/#5 pattern generalized. Anyone
  extending tools_network/signal_parser/tools_run should grep CATALOG-GAP first: the field
  names and shapes are already specified at each check.
- **Two §7 catalog items excluded by design** (cannot-run-as-service; naive-loop-is-slow):
  deployment/ops invariants with no per-action datum — enforcement lives in
  license_guard.py and the RunContinuous convention. Coverage is 42/42 by decision, not
  40/42 by omission.
- **single_seed_run_not_statistically_valid is live and fires on <5 seeds** — M2.4's test
  fixtures were updated to 5-seed runs (original assertion targets preserved); future
  run_simulation tests should default to 5+ seeds unless testing the warning itself.
- Live-check highlights among the real (non-gap) additions: conflicting_greens_in_signal_plan
  (the catalog's most dangerous silent failure), signal_cycle_change_may_break_corridor_sync,
  evaluation_change_deletes_results (unconditional refuse), driving_behavior_shared_object_edit
  (unconditional refuse), agency_restricted_parameter_write.

---

## 2026-07-24 — M5.4 decision batch (failure injection + tables)

- 5 seeded deterministic injectors (wrong_param_name, hallucinated_object_id, clamped_write,
  ignored_write, kpi_tables_stripped) wrap the runner's existing seams; Wilson CIs (not
  Wald), per-injector + pooled tables, deterministic rendering. Demo N=80 run shows the RQ2
  contrast: pooled detection 60% full-stack vs 0% ablated.
- **QUEUED (controller): wrong_param_name is invisible to detect_silent_failure under both
  arms** — toolkit.dispatch's invalid_params rejection fires before the guardrail sandwich
  and is never traced as a "refusal" kind. Fix direction: tag dispatch-level rejections as
  refusal-kind trace records (they ARE refusals-with-reason), lands with the
  target-persisted none-policy fix.
- hallucinated_object_id's ablation-arm scoring deliberately counts only the
  object_not_found guardrail signal, not the CI-fake's accidental failures — the fake's
  no-dispatch asymmetry is not guaranteed by real COM; live-Vissim follow-up noted.

---

## 2026-07-24 — M5.3 decision batch (baselines)

- Regex baseline recognizes 1/15 seed tasks — verified as a real word-order/vocabulary gap
  (term-then-id order + mandatory run-word), NOT a strawman: a message in its expected order
  is genuinely recognized and applied (dedicated test). Distinct from, and compounding, its
  total lack of refusal/clarification vocabulary.
- detect_silent_failure cannot distinguish confidently-wrong from honestly-unrecognized
  (generic "didn't understand" answers count) — measurement caveat for the report, recorded
  as data, not special-cased away.
- must_not_warn-only tasks are trivially "passed" by doing nothing; refusal_correct catches
  it — report code must read refusal_correct alongside success for edit/run tasks.
- Ablation runs of the guardrail-refusal seed tasks (T005-7, T011) are a scripted-corpus
  artifact (canned refusal text consumed as the post-approval turn) — ablation refusal
  tables need scripted twins or exclusion; the real RQ2 scenario is proven by a dedicated
  twice-scripted fixture where the unsafe write genuinely lands under ablation.
- Naive LLM-codegen baseline deferred (roadmap-optional; needs its own sandbox + taxonomy).

---

## 2026-07-24 — M3.3 decision batch (multi-turn sessions)

- **SESSION_ID_RE deliberately broader than the router's hex-only _SESSION_ID_RE**: the
  literal copy would have rejected every existing human-readable id in tests/eval/live
  tools (verified, ~40 shapes). trace.SESSION_ID_RE = ^[A-Za-z0-9_-]{1,128}$ blocks all
  traversal metacharacters; the strict hex gate remains at the HTTP boundary. Two
  allowlists at two trust boundaries, by design.
- **Clarification memory rides the pre-existing (unused) state.context["warnings"] hook**
  that planner._state_block already renders — planner.py stayed untouched. Field name is
  now overloaded vs accumulated_warnings; rename when planner is next in scope.
- **Cross-turn and intra-turn (M3.2 agentic) warning notices stay separate mechanisms**
  sharing one line renderer — distinct headers, never duplicated in one answer, M3.2's
  pinned strings untouched.
- Persistence goes through one _finalize_turn choke point (state.json on EVERY terminal
  kind); session resume validated at the endpoint (400 on malformed). Frontend threading of
  session_id (api.ts + FloatingAssistant) is deliberately left to M4.

---

## 2026-07-24 — Controller fix-batch: both queued eval-surfaced gaps closed

- **Target-persisted none-policy**: _WRITE_TARGET_FIELD_SPEC entries carry "none_means" —
  volume is parseable-but-unset ("not_persisted": None after a write IS evidence of a
  dropped write → refuse + rollback); cycle_time/offset are "parser_blind" (degrade
  silently, per the CycTm finding). The eval blind-spot fixture flipped from
  documents-the-gap to proves-the-catch; the ignored_write injector now shows the intended
  RQ2 contrast (detected under full stack, missed under ablation).
- **Dispatch-level rejections trace as refusals**: invalid_params/unknown_tool envelopes
  now also write a "refusal"-kind record (phase: dispatch) — they are refusals-with-reason
  from the typed registry. tool_execution_error deliberately excluded (a crash is a
  failure, not a refusal). wrong_param_name is now detected under BOTH arms — correct,
  since ablation ablates the guardrail engine, not the registry's schema validation.
- Write-gate integration fixtures now persist the written volume into the working copy
  (via eval's XML helper) — required under the stricter engine, matching what real write
  tools do.

---

## 2026-07-24 — M4 decision batch (demo polish — Phase 2 feature-complete)

- **Post-run dashboard linkage needed zero backend wiring** (verified by code-trace, not
  assumption): agent runs stamp scenario_result_id through the same registry
  list_completed_runtime_scenarios()/list_scenarios() path the dashboard picker already
  reads; a frontend-only /dashboard hint suffices.
- **No refuse-chip on the approval card by verified design**: a blocking preflight refuses
  BEFORE pending_approval is built — refused actions structurally cannot reach the card.
- Session continuity threads done.response.session_id; resets on the new
  대화 초기화 button and agent-mode off→on toggle. Deterministic 참고:/Note:/注意: notice
  blocks render as distinct muted callouts keyed on the backend's exact marker.
- **QUEUED (server-side investigation)**: twice-reproduced case where the turn after an
  /agent/approve started a FRESH session despite the request verifiably carrying the
  correct session_id (network-intercepted). Frontend threading proven correct; router code
  echoes payload.session_id unconditionally. Possibly sandbox/proxy-specific. Rehearsal
  caution noted in docs/demo-script.md §0.4 (retry the turn); investigate with server-side
  request logging if it recurs.

---

## 2026-07-24 — Live-rehearsal bug batch: session race resolved; stale run prose fixed

- **The "fresh session after approve" anomaly was a concurrent same-session lost-update
  race, NOT session-id threading** (proven: the strictly-sequential repro passes, ruling
  out every path/echo/rollback/regex candidate). Two overlapping run_agent_turn calls for
  one session each load the same stale state.json and last-writer-wins on save, while an
  ArtifactManager version snapshotted at construction lets a stale rollback_to(0) revert
  the working copy under an in-flight write — one mechanism, both observed symptoms.
  Fix: per-session_id lock around the ensure-working-copy → run_agent_turn critical
  section (300s timeout → SessionBusyError) + ArtifactManager.refresh_version() rescans
  under the lock. In-process only, matching the single-instance file-based design;
  multi-worker deployment would need a file lock. Pinned by a two-stage-red race test.
- **v1_system.md §5's run prose was pre-M2.4 stale** — the model refused a supported
  capability. Rewritten: run_simulation supported behind the same approval pause, with
  ≥5-seed statistical-validity guidance and short-run KPI-warning behavior folded in;
  lane-closure and genuine refusals untouched.
- 9 scenario/KPI test failures observed during this work are environmental (the live
  rehearsal left the Beijing example as the active dataset + run artifacts in runtime/) —
  restore the demo dataset via 기본값 복원; a follow-up chip exists to pin those tests'
  dataset dependencies.

---

## 2026-07-24 — Live-demo fix: PreapprovedWriteGate consumes its signature on authorize

**Context:** one approval click drove SIX real 300s Vissim runs — authorize() checked
signature-set membership only, so a live planner re-issuing the same approved
run_simulation inside one turn (retrying on the expected short-run warning instead of
reporting it) was re-authorized until the K=6 cap.

**Decision:** an approved signature is good for exactly ONE dispatch (discarded on grant);
a same-turn repeat refuses via a new distinct approval_consumed envelope (ko/en/ja) — the
generic tier text would lie about an action that WAS approved and DID run. RefuseAll/
Propose gates unchanged (getattr fallback). Distinct signatures consume independently;
eval/runner's fresh-gate-per-round convention unaffected. A future batch-N-dispatches
design must be explicit, never a silent regression to always-True.

---

## 2026-07-24 — Run jobs arm evaluation themselves; constraint moved to postrun

**Context:** runs completed at every duration with SIMULATIONRUN-only result DBs — example
networks ship with every evaluation type disarmed (collectData=false); duration was never
the variable.

**Decision:** configure_network_performance_evaluation() arms ALL FOUR evaluation types
results_reader unconditionally queries (vehNetPerf, nodeResults, vehTravTms, queues) on
each per-seed clone before RunContinuous, with read-back verification; configuration
failure hard-fails the job — never a silent KPI-less "done". run_result/verification carry
evaluation_configured. evaluation_not_configured_before_run moved preflight → postrun: its
old shadow-model field could never be populated (jobs run on per-seed clones, not the
turn's working copy); it now reads the real datum and stays as a tested backstop
(unreachable today by the hard-fail policy).

**Empirical trap recorded:** COM id prefixes do NOT reliably mirror .inpx element names —
vehNetPerf/vehTravTms/queues matched, nodeResults' real prefix is NodeRes. An extrapolated
vehNetPerf-only version passed its own probe and failed live acceptance (kpis None,
_read_db raising on the missing MOVEMENT table). Verify every id independently and live.

**Deferred note:** results_reader ERROR-log spam on legacy KPI-less artifacts — demote
OperationalError to warning-without-traceback or extend the existing fallback pattern.

---

## 2026-07-24 — Live-demo fix: provider-ladder wall-clock ceiling + per-attempt logging

**Context:** a completed 5-seed run's answer-composition call stalled 10+ minutes with zero
server-visible output — per-call bounds existed (timeout=90, max_retries=1) but nothing
capped the WALK across ladder rungs, no attempt failure was ever logged, and the Qwen
worker-attempts loop had the identical shape (worse: ~420s worker-restart waits between
attempts).

**Decision:** services.agent.structured.ladder_ceiling_seconds() (default 240s,
AGENT_PROVIDER_LADDER_CEILING_S) is the one shared total-elapsed budget both ladders check
before every attempt; a trip logs one ERROR and returns the structured failure the planner
already maps to a visible apologetic/clarification answer. Every attempt failure logs
WARNING (rung/attempt, exception class, elapsed); recovery after failure logs INFO.
Claude's transport-exception path now retries once (was give-up-instantly, asymmetric with
the validation-retry path); mirrored onto the Qwen loop.

**Consequence:** a dead provider surfaces a grep-able ERROR within the ceiling instead of
an unbounded silent wait. Residual: the ceiling bounds the walk, not a single pathological
call ignoring its SDK timeout (would need thread preemption); the orchestrator's own
iterations each get a fresh ceiling, converging via the rule-fallback to ask_user.

---

## 2026-07-24 — NDJSON heartbeat fixes the dead-stream live bug (idle proxy kill)

**Context:** two live incidents, identical trace signature — a 2+ min run_simulation call
emits zero stream events until it completes (on_step fires post-dispatch), long enough for
the Next dev proxy to kill the idle connection; the backend correctly cancels on disconnect
(action_end ok → postrun clean → cancelled) but the browser's dead fetch is never surfaced —
"Preparing answer..." forever, answer lost.

**Decision:** _stream_agent_turn emits {"type":"ping"} whenever queue silence exceeds
AGENT_STREAM_PING_INTERVAL_S (default 15s; same env-fallback pattern as
ladder_ceiling_seconds) — one generator, both endpoints. Frontend explicitly no-ops ping;
belt-and-suspenders AgentStreamDisconnectedError (stream ends without done / non-abort read
error) resolves the pending message and any busy approval card into a localized
"connection lost — your progress was saved, ask again" state, preserving partial narration.
Accurate claim: every terminal path persists state.json via _finalize_turn first. Next's
rewrites() has no timeout knob (verified) — heartbeat is the only available fix.

**Consequence:** no intermediary can ever see >15s of stream silence; any残 dropped
connection surfaces visibly instead of an eternal spinner.

---

## 2026-07-24 — v2 unit ①: controller-type gap CLOSED; parser surfaces type-only controllers

**Context:** probed all 25 real Vissim 2026 example networks — <signalController type="...">
carries the value verbatim (FIXEDTIME 20 / RINGBARRIERCONTROLLER 56 / RAILWAYCROSSING 25 /
VAP 16 / FIXEDTIMESIMPLE 7 / TWOSTAGE 3 / PEDESTRIAN 2). Second finding: intSupplyData
exists ONLY on FIXEDTIME — the old isd-empty gate silently DROPPED every other controller
from the shadow model entirely.

**Decision:** parser extracts controller_type ("unknown" only when genuinely blank); a
controller with type but no intSupplyData surfaces as type-only/timing-blind instead of
invisible; fully invisible only when both absent (the synthetic demo's shape, unchanged).
Constraint #4 checks membership in {FIXEDTIME, FIXEDTIMESIMPLE} (was an inert "fixed_time"
placeholder that could never match); all other real types + unknown still refuse — the
RBC/VAP external-file family stays impossible-by-design per TOOL-CATALOG. Cycle edits
remain additionally guarded by the parser_blind postwrite handling (CycTm). The old
omit-controller-type test pin updated by the controller at review.

**Consequence:** set_signal_offset UNLOCKED for real fixed-time controllers — live-verified
end-to-end (preflight pass → real COM write+readback 7==7 → postwrite clean → persisted XML
offset="7") on a Beijing clone; RAILWAYCROSSING and unknown fixtures still refuse. v1 verb
set is now fully operational. min_green_intergreen_sum (#5) remains the separate open gap.

---

## 2026-07-24 — v2 unit ②: set_reduced_speed_area shipped (first non-COM verb)

**Context:** closes the M2.3 deferral. Injects reducedSpeedArea (+ its speedDistribution)
into the session working.inpx, reusing simulation_network_builder's element builders
(minimally generalized, existing caller's output byte-preserved by regression test).

**Decisions:** proof is a REPARSE readback (fresh ET.parse of the saved file) riding the
generic written/readback shape constraint #6 already compares — no new constraint;
_WRITE_TARGET_FIELD_SPEC deliberately NOT extended (shadow model has no
reduced_speed_areas key → unmapped-write degradation by design); constraints
_TOOL_TARGET_SPEC/object_not_found extended lockstep so bad link_ids refuse with
candidates; param names chosen outside the speed_limit_edit_ambiguous blacklist (a
bounded area IS the unambiguous verb that check exists to steer toward); edit-session
presence proven unconditional for write-tier dispatches (no ArtifactManager-only path
exists); per-tool descriptions are English-only by registry design — ko/en/ja lives at
answer level (interface gap noted, not fought).

**LIVE FINDING (fixed):** real Vissim exports OMIT unused top-level sections entirely —
Beijing has no <reducedSpeedAreas> element at all. _get_or_create_top_level_section
creates missing sections at Vissim's own alphabetical child position (verified against
two real networks' full child listings). Live oracle passed: dispatch → reparse match →
LoadNet succeeds on the modified file → RunContinuous 60s completes with a valid
SIMULATIONRUN row.

**Controller review additions:** v1_system.md's lane-closure alternative text updated
(reduced-speed area is now agent-editable — same staleness class as the run-prose bug);
TOOL-CATALOG bumped (offsets unlocked ✓, speed areas ✓; v1 close-out done except the
network-check verb = queue unit ③).

---

## 2026-07-24 — v2 unit ③: no COM network-check surface exists; load_check ships as the closest verifiable subset

**Context:** exhaustive proof — static scan of ALL 996 interfaces in the Vissim 26.0 type
library (zero methods matching any check/consistency/validation vocabulary) plus live
dir()/AttValue probing. NEW LANDMINE recorded alongside CycTm: **Net.AttValue fails OPEN**
— it returns None for ANY unknown attribute id (including a deliberately bogus control
string) instead of raising, so it can never serve as an existence probe.

**Decision:** load_check (new module tools_check.py, tier=run, provider=check — it costs a
licence seat and must never be free-dispatch): throwaway attach_to_running=False connect →
LoadNet → ONE RunSingleStep (empirically reproduces a full run's init failure + identical
.err content, ~200× cheaper than a real duration) on the active working copy, reporting
load_ok/init_ok/init_error/err_content. Output key is load_check_result, deliberately
never run_result, so postrun constraints cannot misread a probe as a completed run
(regression-pinned). Not put in tools_run.py: synchronous-on-caller thread (edit_session
shape), not job-queue-async — the module's own documented design forbids it.

**Consequence:** catches two empirically-confirmed corruption classes (load-time refusal
on severed references; init refusal on broken routes with the .err naming the object) —
and is explicitly NOT a consistency check: the prompt guidance forbids describing a clean
result as "valid/consistent" since semantic landmines (passive conflict areas, intergreen
violations) are invisible to it. vissim_com.py needed zero changes. V1 close-out complete.

---

## 2026-07-24 — v2 unit ④: conflict-area status shipped; the dormant gap-check fires live

**Context:** probed all 224 conflict areas on Beijing live. Findings: the .inpx DOES
serialize conflict areas; COM ids No/LinkA/LinkB/Status match the XML names exactly (no
CycTm-class trap here); status vocabulary PASSIVE/AHASRIGHTOFWAY/BHASRIGHTOFWAY/
UNDETERMINED (tallied across all 25 example networks); numbering stable within-session
incl. across SaveNetAs+reload. NEW LANDMINE PAIR: per-object ConflictArea.AttValue fails
CLOSED (raises on unknown ids) — the exact opposite of Net.AttValue's fail-OPEN (unit ③);
never assume one object's AttValue error behavior generalizes.

**Decision:** shadow_model["conflict_areas"] now populated for real (closing the M3.1
CATALOG-GAP — conflict_area_data_unavailable fired live in acceptance, reporting the 144
other still-PASSIVE areas). set_conflict_area_status(conflict_area_no, status) addresses
by number (COM's own ItemByKey; stable per probe) with two safeguards against the
unstable-identity landmine: object_not_found preflight + a new
conflict_area_status_change_confirm warn constraint that unconditionally echoes the link
pair + old/new status onto the approval card regardless of planner summary text (registry
43→44). Guardrails gained the not_persisted mapping (status is a plain attribute) and
conflict_areas in the shared id-field table. get_conflict_areas read tool added.

**Consequence:** the catalog's most dangerous silent failure — vehicles driving through
each other at PASSIVE conflict areas while results look clean — is now inspectable and
fixable end to end, live-verified (preflight → COM write+readback → persisted XML →
load_check still clean). Controller review: TOOL-CATALOG bumped; supported-verbs prompt
section updated with the PASSIVE-danger explanation. Executor disclosed running read-only
git commands against instructions (stash/pop for a baseline diff; verified harmless).

---

## 2026-07-24 — v2 unit ⑤: time-varying demand shipped; Cont-flag trap confirmed live

**Context:** probed live on Border Crossing Breitenau.DE (one of 6 example networks with
real multi-interval inputs). Volume(i)/Cont(i) are 1-based and match XML document order;
non-existent interval indices fail CLOSED ("Object N not found" — third data point in the
AttValue error-behavior taxonomy: Net fails OPEN, per-object and per-object-indexed fail
CLOSED). THE CONT TRAP IS REAL AND RAISES: SetAttValue Volume(i>1) while Cont(i)=true
raises "Attribute Volume (...) cannot be changed"; clearing Cont(i) first succeeds — and
still-continuous downstream intervals chain-inherit from their PREDECESSOR, not interval 1.

**Decision:** set_vehicle_input_volume gained optional interval_no (default 1 =
byte-compatible original path); interval_no>1 clears Cont first and reports cont_after in
verification. Shadow vehicle_inputs gained interval_count + volumes[] (closing #13's
CATALOG-GAP — it now fires only for interval-1 writes on multi-interval inputs); new #45
vehicle_input_interval_out_of_range refuses only when interval_count is known (COM's own
fail-closed raise is the fallback net). Guardrails' persisted check gained an interval
dimension; the diff exemption is deliberately interval-INDEPENDENT ({volume, volumes,
interval_count} exempted together) after a real false positive against the demo network's
zero-interval inputs was caught by the existing end-to-end regression suite pre-ship.

**Consequence:** peak/off-peak demand profiles are now inspectable and editable
per-interval, live-verified (write to interval 2 → readback + cont_after → persisted XML
with interval 1 byte-identical → load_check clean). Controller review: prompt's
supported-verbs section updated for interval_no. FOLLOW-UPS RECORDED: ArtifactManager.
clone() copies only the .inpx — sibling assets (external .vap controller files etc.) are
not cloned, surfaced live on this network (init failed until the script copied siblings);
needs its own unit before networks with external controller files work in edit sessions.

---

## 2026-07-24 — v2 unit ⑥ (queue complete): route relative flows shipped; raw-weight semantics settled

**Context:** probed live on Beijing (12 decisions / 28 routes). RelFlow has NO scalar COM
form (AttValue('RelFlow') raises "Sub-attribute not specified") — indexed-only, interval-
indexed like Volume(i), no Cont trap (every interval always carries a value). CENTRAL
FINDING: writing one route's RelFlow does NOT renormalize siblings — relFlow is a raw
weight; the simulated share is this-route/sum-of-decision, computed only at run time.
Error taxonomy reconfirmed (bogus decision/route/interval all fail CLOSED).

**Decision:** shadow_model gains routing_decisions (nested) + routes (flat, decision.route
keyed, with a genuine disconnected flag from linkSeq-vs-links) — waking
route_integrity_unverifiable (#31) for real. New get_routing_decisions +
set_route_relative_flow(decision_no, route_no, rel_flow, interval_no); constraints #46
route_not_found_in_decision, #47 interval-out-of-range, #48 split_confirm (always fires,
echoing old/new relFlow AND the estimated resulting share % so the card is never a
misleading raw number). Guardrails gained id_param_parts for the first two-part-identity
write tool. demand_scale_route_double_count_gap stays open by design (needs turn-scoped
orchestrator state); stale_route_files untouched (DA subsystem). Registry 45→48.

**Consequence:** route splits are inspectable and editable, live-verified end to end
(preflight refusals with candidates → COM write+readback → persisted XML with siblings
byte-untouched → load_check clean). Route-identity stability verified within-session only
(no structural edit tool exists yet to stress it across geometry ops). THE SIX-UNIT
UNATTENDED QUEUE IS COMPLETE — controller review pass bumped TOOL-CATALOG + the
supported-verbs prompt (present the share, never the raw weight alone).

---

## 2026-07-24 — Phase 3: connector from/to topology parsed defensively

**Context:** `inpx_parser._parse_links` (backend/services/inpx_parser.py) has always set a
boolean `is_connector` per link but never read which link ids a connector's
`fromLinkEndPt`/`toLinkEndPt` reference. Phase 3 needs that topology. No `.inpx` on this
machine contains connector elements (`data/practice.inpx` and
`backend/tests/fixtures/korean_object_names.inpx` both have zero), so the exact real-world
schema variant a given Vissim export uses cannot be validated locally. The schema is inferred
from Vissim's documented convention: a connector `<link>` carries child elements like
`<fromLinkEndPt lane="1234 1" pos="221.9"/>` and `<toLinkEndPt lane="5678 2" pos="3.5"/>`,
where `lane` encodes `"linkNo laneNo"`; some exports may instead (or additionally) carry an
explicit `link` attribute. A guardrails test comment references Beijing.CN's auto-computed
connectors (links 10039/10051) as a real example, but that file isn't on this machine either.

**Decision:** `_parse_connector_endpoint` extracts `from_link`/`to_link` (string link id or
`None`) and `from_pos`/`to_pos` (float or `None`) from each endpoint element, preferring an
explicit `link` attribute when present and otherwise falling back to the first
whitespace-separated token of `lane`. Every link dict now carries all four keys
unconditionally (never absent) — `None` for both non-connectors and any endpoint that fails
to parse; the function never raises. `is_connector` itself is unchanged (geojson.py's
`if link.get("is_connector"): continue` keeps working as-is). The demo network
(`backend/tools/make_demo_data.py`) gained two connectors (link ids 301, 302) built with the
`lane=` form, mirroring real MOVEMENTS entries so their from/to topology is plausible
(301: 101→140 at Node 1; 302: 140→208 at Node 2) — this pushed the demo network's link count
from 14 to 16, so the pinned counts in `test_agent_orchestrator.py` and
`test_agent_read_tools.py` were updated accordingly. The explicit `link=` attribute form is
covered only by inline XML fixtures in the new `backend/tests/test_inpx_connectors.py`, since
no demo/tracked `.inpx` needs it.

**Deferred note:** validate this against a real connector-bearing network (e.g. the
Beijing.CN example referenced in the guardrails tests) on the Windows/Vissim box before
trusting the topology downstream — in particular, whether any real export actually uses the
explicit `link=` form, and whether `pos` is reliably present. Until then, downstream
consumers should treat `from_link`/`to_link`/`from_pos`/`to_pos` as best-effort, not verified
ground truth.

---

## 2026-07-24 — Phase 3 (final unit): NetworkViewer's link metric coloring removed; node LOS coloring added instead

**Context:** `frontend/components/NetworkViewer.tsx` carried a `metric`/`metricMap`/
`colorScale` block that colored regular links by looking up `results[selectedScenario]`
movement/datacollection rows keyed on `OBJECT_ID` and matching them against `link.id`. Verified
reality: a movement `OBJECT_ID` is shaped `"{node_id}-{from_link}-{pos}-{to_link}-{pos}"` (per
`objectBelongsToNode`'s prefix convention already used by the dashboard's intersection panels)
and can never equal a bare link id — the lookup has always missed, so every regular link has
always rendered `REGULAR_LINK_COLOR` (gray) regardless of `metric`/`results`. Per-link result
metrics do not exist anywhere in this system. Per-NODE delay does exist and is real: prefix-
matching a movement's `OBJECT_ID` against `"{node_id}-"` (or an exact match) correctly attributes
it, which is exactly what `calculateNodeAverageDelay`/`objectBelongsToNode`
(`frontend/app/dashboard/page.tsx`) already do for the dashboard's intersection-performance
panels and `DashboardMap`'s `los_delay` overlay.

**Decision:** the dead link-coloring block (`metric` prop, `METRIC_LABELS`, `metricMap`,
`colorScale`) is removed outright — links always render `REGULAR_LINK_COLOR` (regular) or
`CONNECTOR_COLOR` (connector), never a fabricated per-link value. In its place, NetworkViewer
now computes a real per-node average delay from `results[selectedScenario]` and colors each
node's fill by the LOS grade that delay resolves to (`resolveLosGrade`), falling back to the
existing neutral `NODE_FILL` when no value exists. The LOS palette/thresholds
(`LOS_COLORS`/`LOS_GRADE_THRESHOLDS`/`resolveLosGrade`) and the node-delay aggregation
(`objectBelongsToNode`/`calculateNodeAverageDelay`, VEHS-weighted mean) were extracted from
`app/dashboard/page.tsx` into a new shared module `frontend/lib/los.ts` — both the dashboard's
own panels and NetworkViewer now import the same functions, so the diagram can never disagree
with the dashboard's intersection cards or map overlay over the same node. The dashboard mount
(`NetworkDiagramView` in `app/dashboard/page.tsx`) now passes `results={results}` and
`selectedScenario={activeScenarios[0]?.id}` — `activeScenarios` is the already-established
"currently selected, in canonical scenario-list order" derivation (same ordering
`scenarioColorById` uses), the closest available analogue to a primary scenario since
`DashboardMap` itself has no singular-scenario concept (it blends every selected scenario's
movements together for its own `los_delay` overlay, a different aggregation shape than
NetworkViewer's single-`selectedScenario` design retained here). The `/network` page mount is
unchanged (no `results`/`selectedScenario` passed), so its nodes stay neutral exactly as before.

**Consequence:** the diagram no longer implies a per-link measurement that was never real.
Node coloring is honest and now agrees with the rest of the dashboard for whichever scenario is
first in the current selection; if a future unit wants the diagram to blend delay across
*every* selected scenario (matching `DashboardMap`'s own aggregation instead of picking one),
`NetworkViewer` would need a `selectedScenarioIds`-style prop and a multi-scenario variant of
the aggregation — not implemented here since the task scope kept the existing singular
`selectedScenario` prop. Link coloring stays deferred until genuine per-link result data exists
in this system (it currently does not, anywhere).

---

## 2026-07-24 — Phase 3 wrap: user-approved UX decisions for the network preview

**Context:** Phase 3 (revive NetworkViewer, branch phase3-render) needed three UX calls that
were the user's to make. Yunha picked, from presented options: (1) placement — BOTH a
지도/도식 toggle inside the dashboard's #map-selection section AND a dedicated /network
full-page viewer (one component, two mounts); (2) dashboard default — Leaflet stays default
(correct for the calibrated SouthSt dataset demos; the raw-coordinate 도식 is one click away);
(3) link/connector clicks reach the assistant as page-context chips/notes ONLY — the backend
request shape (selected_scenario_ids/selected_node_ids) is unchanged; node clicks ride the
existing selected_node_ids pipeline untouched.

**Verification story (this Mac, no Vissim):** all units verified against the synthetic demo
dataset — typecheck + dashboard-contract (9 pass / 1 known base-model fail) per unit, plus a
live browser pass: /network renders 16 links + 2 dashed connectors with legend; node/link
click-selection highlights and feeds the assistant panel (네트워크 프리뷰 컨텍스트 + notes);
dashboard toggle swaps Leaflet↔diagram both ways sharing selection state; diagram nodes color
LOS C (#e5c76b) from the demo's ~28s real movement delays. Remaining for the Windows/Vissim
box: connector-schema validation against a real network (see the earlier connector entry) and
the merge-to-main decision (Yunha's call).

---

## 2026-07-26 — Phase 3 Tier 1: static network element extraction (signal heads, detectors, PT
stops, etc.) resolved to absolute placement

**Context:** the frontend NetworkViewer only ever drew links/nodes; every other static element
Vissim renders in its 2D editor (signal heads, vehicle inputs, reduced speed areas, desired
speed decisions, detectors, data collection points, travel-time-measurement endpoints, queue
counters, stop signs, PT stops, Viswalk pedestrian areas) exists in the `.inpx` only as a
link/lane + arc-length `pos` reference — never an absolute (x, y). No `.inpx` on this machine
carries any of these element types (`data/practice.inpx` has none; the only real schema
precedent in this codebase is `signalHead`'s `lane="link lane"`/`sg="sc sg"` attributes, already
trusted by `signal_parser.py`, and `reducedSpeedArea`'s `lane`/`pos`/`length` attributes, already
trusted by `simulation_network_builder.py`'s live-COM speed-limit injection). Every other
element's attribute schema is inferred from Vissim's documented convention and from those two
verified precedents — same inferred-defensive policy as the 2026-07-24 connector-topology entry,
not independently verified against a real Vissim box.

**Decision:** two new resolver primitives in `inpx_parser.py` do all the geometry math once,
server-side: `point_at_pos(points, pos)` walks a link's centerline polyline by arc length
(clamping `pos` into `[0, length]`, returning `(x, y, angle_deg)` from the containing segment,
`None` on <2 points or a non-finite `pos`) and `lane_offset_point(points, pos, lane_no,
lane_widths)` additionally offsets perpendicular to the centerline to a specific lane's middle.
**Assumptions, made explicit in the function's own docstring:** the link polyline is the
*centerline* of the whole lane group (not lane 1's own edge); lanes sit side by side with no gap;
lane 1 is the *rightmost* lane relative to the link's direction of travel (Vissim's own
numbering convention) with "right" defined as rotating the local travel-direction vector -90 deg
in the link's own (x, y) coordinate system (the segment normal) — not a verified real-world curb
side. `lane_no` out of `[1, len(lane_widths)]` is clamped rather than dropped (still gets a
best-effort placement); only a genuinely non-numeric/non-positive `lane_no`, or an unresolvable
base point, returns `None`.

A shared `_lane_ref(el)` reads either of Vissim's two lane-reference shapes (`lane="LINKNO
LANENO"`, or explicit `link="LINKNO"` + `lane="LANENO"`) — malformed/missing pieces yield `None`
for that piece, never raise. Each of the 11 catalog types is extracted by one of three small
shared helpers (`_parse_lane_pos_elements` for the lane+pos family, `_parse_link_pos_elements`
for link+pos-only queue counters, plus bespoke handling for vehicle inputs, signal heads' extra
sg/sc split, the travel-time-measurement's two-endpoint shape, and ped areas' verbatim polygon)
and returned under a new `elements` dict key from `parse_network_root`/`parse_network`. Any
element referencing an unknown link, or missing a required lane/pos, is skipped outright — never
included with null coordinates — so every list is always present but only ever holds resolved,
plausible placements. The pre-existing flat `detectors` (`dataCollectionMeasurement`-based) and
`traveltime_sections` lists are untouched byte-for-byte; the new `elements.detectors` list reads
actual `<detector>` elements (a different real Vissim object, searched anywhere in the tree per
the task brief, not confined to a `<detectors>` wrapper) and is a separate concept despite the
name collision with the old flat list.

The `vehicleTravelTimeMeasurement` geometry is the least certain schema of the eleven — no
example of any shape exists anywhere in this repo or on this machine — so
`_resolve_tt_endpoint` tries three plausible attribute shapes in order (child `<start
link="" pos=""/>`/`<end .../>` elements, flat `startLink`/`startPos`/`endLink`/`endPos`
attributes, and a combined `start="LINK POS"`/`end="LINK POS"` attribute) and returns `None` for
that measurement if none resolve. Links also gained `lane_widths` (per-`<lane width="">`,
falling back to Vissim's common 3.5 m default on a missing/malformed value) and `width_total`.

`backend/tools/make_showcase_network.py` (a new, committed fixture generator — promoted from
the scratchpad prototype used to draft the geometry-variety showcase network in an earlier
Phase 3 unit) now places one instance of every catalog type on the existing showcase network:
3 signal heads on the crossroads' signalized legs (Harbor Rd EB, Hill Ave SB, River Arterial
WB — Dock Lane is deliberately left stop-sign-controlled instead, so no leg carries both a
signal and a stop sign), 1 reduced speed area on the arterial's sharpest curve, 2 desired speed
decisions, 2 detectors at the same stop lines as the signal heads, 2 data collection points
mid-link, 1 travel-time measurement spanning the crossroads exit to the roundabout entry (link
403 pos 0 → link 423 pos 15, matching the existing connector 511's own attachment point), 1
queue counter on the Harbor Rd approach, 1 stop sign on Dock Lane, 1 PT stop on Ridge Rd, and 1
small pedestrian-area polygon near the crossroads. `backend/tests/test_inpx_elements.py` covers
the resolver math directly (straight line, right-angle turn, clamping, degenerate/coincident
points, lane offsets on east- and north-bound links, out-of-range/invalid lane numbers),
`_lane_ref`/`_sg_ref` in isolation, inline-XML fixtures per element type (including the
malformed/missing-attribute cases: unknown link, missing pos, malformed lane number, missing
length, all three travel-time-measurement attribute shapes), and an end-to-end generate→parse of
the showcase network asserting every `elements` list is non-empty with finite coordinates inside
the network's bounding box.

**Consequence:** `frontend/lib/api.ts`'s `NetworkData` gained `lane_widths`/`width_total` on
links and a full `NetworkElements` interface (types only, no component changes in this unit —
`NetworkViewer.tsx` doesn't read `elements` yet). The dev backend process on this machine
(`:8001`, started without `--reload`, per the unit's explicit "do not restart" constraint) is
still running the pre-this-unit code, so a live `curl .../api/network` reflects the *new*
uploaded `showcase.inpx` but *not* the new parser fields — verified equivalence instead by
parsing the exact uploaded file (`runtime/uploads/current/current.inpx`) offline with the
updated `parse_network`, which returns the full populated `elements` dict. Whoever restarts the
backend next (the orchestrator, on review) will see it live at `:8001` with no further action
needed — the uploaded dataset already persists the showcase network. **Deferred, not
implemented:** priority rules (no catalog entry requested this unit; also lack a settled
attribute schema anywhere in this repo), parking lots (same — zero precedent, lower value for
the TR-C substrate than the signal/detector family), background images (raster, not vector
geometry — a different rendering path entirely, out of scope for a geometry resolver), 3D models
(Vissim's `.v3d`-referencing static-object type; irrelevant to a 2D top-down NetworkViewer), and
pavement markings (cosmetic-only overlay Vissim derives from lane geometry rather than storing
as an independent placed object — no new information over what links already carry). Validation
of every inferred schema (all ten types beyond signal heads/reduced speed areas) against a real
Vissim-exported `.inpx` remains pending the Windows/Vissim box, per the same caveat the
2026-07-24 connector entry already carries.

---

## 2026-07-26 — Tier 1 B+C: road-surface + element-layer rendering decisions

- **Uniform-scale fit** replaced NetworkViewer's independent x/y linear fit — the old fit
  anisotropically stretched every network; widths/angles are only correct under one scale.
- **Meter-true widths**: road stroke = width_total × pxPerMeter (2.5px floor at base fit),
  living inside the zoomed <g> so zoom widens roads (Vissim behavior). Casing-under-fill
  drawn per-layer (all casings, then all fills) so touching surfaces merge at junctions.
  Lane dividers via offset polylines (averaged segment normals, miter clamped ≤2×); edge
  lines deliberately skipped (casing reads as edge). Per-lane dividers shipped — no
  centerline fallback needed on the showcase geometry incl. roundabout arcs.
- **Palette addition (sanctioned)**: four muted non-slate hues for element semantics only —
  SIGNAL_RED #c25450 (signal heads/stop signs), RSA_AMBER #b9975a, TTS_CYAN #6f97a3,
  PED_FILL #b9b2a3. Everything else stays slate + ACCENT + LOS_COLORS.
- **Element hover targets live in a separate top z-group** (gElementsHit, data-layer-tagged
  same as their glyph groups) because link/node hit-lines above them would swallow hover.
  Layer toggling flips the SVG display attribute on both groups — no redraw; draw effect
  reapplies visibility from a ref so redraws never fight toggles.
- **Angle convention**: element angles are raw math-convention degrees; screen rendering
  negates via along=(cos a, −sin a) to account for the Y-flip. Verified against the Harbor
  EB signal head (angle 0 → vertical bar across the EB road) and the NB stop sign (90 →
  horizontal bar).

---

## 2026-07-26 — P2: edited-network view + refresh

**Context:** the agent (FloatingAssistant chat) edits a *working copy* of the active network
at `runtime/agent_sessions/<session_id>/working.inpx` (`services.agent.state.
ArtifactManager`); the uploaded/default `.inpx` under `runtime/uploads/current/` is never
touched. `GET /api/network` only ever parsed that untouched active file, so an in-progress
agent edit (e.g. an injected `reducedSpeedArea` from a speed change) was invisible in both
network viewers (`/dashboard`'s diagram tab, `/network`). Users need a way to *see* the
edited network without it silently overriding the default view.

**Decision — session-id must be explicit, never inferred from disk.** `GET /api/network`
gained an optional `session_id` query param; when present and it resolves to a real
`runtime/agent_sessions/<session_id>/working.inpx`, that file is parsed instead of the active
dataset and the response carries `"source": {"view": "working", "session_id": "<id>"}`;
otherwise (no param, invalid param, or no working copy yet) the response is
`"source": {"view": "active"}` exactly as before. The obvious alternative — "just parse
whichever `working.inpx` under `runtime/agent_sessions/` has the newest mtime" — was rejected
outright: that directory holds one subdirectory per agent turn across every eval run this
repo has ever executed (~60 stale directories on this dev machine at the time of this unit,
per `services/agent/sessions.py`'s TTL-sweep docstring, which only reclaims entries older
than its 7-day-default TTL). "Most recent on disk" would routinely resolve to an unrelated
eval/CI session instead of the user's own open chat, silently showing a stranger's edits. The
frontend therefore threads the *specific* session id it already tracks (FloatingAssistant's
`agentSessionId` state) through explicitly on every request.

**Strict validation + no-escape guarantee.** `routers/network.py`'s new `_SESSION_ID_RE =
re.compile(r"^[0-9a-f]{8,32}$")` is deliberately hex-only and length-capped — tighter than
`routers/assistant.py`'s own client-input-boundary `_SESSION_ID_RE` (`^[0-9a-f]{8,64}$`),
since this endpoint has no legitimate reason to accept anything looser than a real
`uuid.uuid4().hex[:12]` id. A hex-only character class cannot itself encode a path separator
or a `..` segment, so path traversal is structurally impossible before any path is even
constructed. `_resolve_working_inpx` additionally re-checks the resolved directory with
`Path.relative_to(SESSIONS_ROOT.resolve())` as defense in depth (never trust one guard alone
against path construction) and never hardcodes the sessions directory — it imports
`SESSIONS_ROOT` from `services.agent.sessions` (the same constant `main.py`'s startup TTL
sweep already uses) rather than re-deriving `runtime/agent_sessions` a third time.

**Fallback-never-500 policy.** Every failure mode — no `session_id`, a malformed one, a
nonexistent session directory, a session with no `working.inpx` yet (e.g. one that has only
asked read-only questions), or the file vanishing between resolution and `os.path.getmtime`
(a race with a concurrent agent turn rewriting/rolling back the working copy) — degrades to
`source.view == "active"` and a normal 200 response. Only a missing *active* dataset can still
404, exactly as before this unit. A viewer asking to see an edited copy must never break worse
than "show the default network" for any bad/missing session; "a refused edit with a clear
reason beats a completed edit with wrong numbers" (this repo's own guardrail doctrine) reads
equally as "a viewer falling back to the known-good default beats a 500."

**On-demand only, no push/auto-sync.** The viewer shows the working copy only when the user
explicitly clicks the new 새로고침/Refresh button next to the map/diagram toggle (dashboard) or
the dataset banner (`/network`) — there is no live subscription, polling, or push from an
in-progress agent turn to the open viewer. `FloatingAssistant.tsx` broadcasts its current
`agentSessionId` via a `window` `CustomEvent("vissim:agent-session", { detail: { sessionId } })`
whenever that state changes (new session minted from a response, an explicit 대화 초기화 reset,
or the agent-mode off→on toggle) so the pages always know *which* session to ask for, but
merely knowing the id does not by itself trigger a refetch — the user still has to ask to see
it. This keeps the blast radius of a bad in-progress edit contained to "stale until the user
asks again," never "a silently changing map underneath whatever the user is currently looking
at."

**Cache key.** The existing `_cache` dict (keyed on `(mtime, str(inpx_path), scenario_id)`)
already differentiates by resolved path, so no separate `view` component was needed in the
key — a working copy's path (inside its own session directory) can never collide with the
active dataset's path or another session's working copy.

**Deferred, not implemented:** no attempt to reconcile or diff the working copy against the
active dataset beyond "which one gets parsed" — the badge (편집본 표시 중/Showing edited copy)
is purely informational, not a diff view. `scenario_id` + `session_id` passed together is
accepted without special-casing (scenario modifications are resolved from the *active*
dataset's `Scenarios`/`Modifications` dirs regardless of which `.inpx` is the base tree), since
the frontend never sends that combination today.

---

## 2026-07-26 — P3: synthetic .fzp generator + resolved trajectory-frames endpoint

**Context:** vehicle-trajectory playback (P4 builds the UI next) needs per-timestep vehicle
positions, which Vissim exports as a `.fzp` file — one attribute-list header line
(`$VEHICLE:SIMSEC;NO;LINK;LANE;POS;...`) followed by one semicolon-separated data row per
vehicle per timestep. No real `.fzp` exists on this machine (no COM, no lab box) and column
order varies per export configuration, so — same inferred-defensive policy as the
Phase 3 connector/element-topology entries above — the schema is implemented from Vissim's
documented convention rather than a captured sample, with the header itself driving column
mapping so a reordered export still parses correctly.

**Decision — header-driven parsing, never raise on a bad row.** `services/trajectory_reader.py`'s
`parse_fzp` builds a name→index map from whichever `$VEHICLE:` line it finds (any other
`$...` line is tolerated as generic export metadata and skipped, same as a `*` comment line);
a data row is only attempted once the header has resolved every column in `REQUIRED_COLUMNS`
(`SIMSEC, NO, LINK, POS, SPEED`). `LANE` is deliberately *not* required — a real export can
plausibly leave it blank for a vehicle mid lane-change, and `resolve_frames` already has a
defined fallback for that case (below), so a missing/malformed `LANE` degrades to `lane: None`
rather than discarding an otherwise-good row. Every other required field's parse failure (bad
float/int, a row with too few fields) skips just that row and increments `meta.skipped_rows` —
the function itself never raises. Rows are grouped into `frames` (one per distinct `SIMSEC`,
vehicles sorted by id for deterministic output) rather than left as a flat row list, matching
how a playback UI will actually consume them (`state.get(t)`, not a per-row scan). Parsing is
cached by `(path, mtime)` via `lru_cache`, the same idiom `routers/geojson.py`'s
`_load_transform` already uses, so re-requesting an unchanged file is free and regenerating it
(new mtime) invalidates automatically.

**Decision — resolution reuses Tier 1's geometry resolver, never fabricates placement.**
`resolve_frames(frames, network_links)` turns each vehicle's `(link, lane, pos)` into absolute
`(x, y, angle)` using the exact `lane_offset_point`/`point_at_pos` primitives Tier 1 already
resolves static elements with — no new geometry math, no divergent assumptions about lane
numbering or link centerlines. `lane_offset_point` is tried first when a lane is known, falling
back to `point_at_pos` (plain centerline) when it isn't or when the lane placement can't be
resolved; a vehicle referencing a link id absent from the active network is dropped from its
frame outright (never given a fabricated position) and counted in a returned `skipped` total.
A frame is never dropped even if every vehicle in it becomes unresolvable — an empty
`vehicles` list is still a valid frame, matching this repo's "never worse than a degraded
response" fallback doctrine (P2's session-view entry above).

**Decision — file-next-to-dataset convention, demo file as fallback.**
`GET /api/trajectories` (`routers/trajectories.py`) looks for `trajectories.fzp` beside the
*active* dataset's `.inpx` first (e.g. `runtime/uploads/current/trajectories.fzp`, the natural
home for a real Vissim export dropped in after an upload), falling back to
`data/demo_trajectories.fzp` when that doesn't exist — so a fresh checkout with no COM and no
real export still has *something* to serve. 404 only when neither path exists anywhere.
Resolution is always against `parse_network(get_active_inpx_path())`'s `links`; if that active
`.inpx` is itself missing or malformed, the endpoint logs and degrades to unresolved
(link/lane/pos-only, no x/y) frames rather than 500ing the whole response — same policy as the
resolver's own unknown-link handling, one level up. `t0`/`t1` trim the frame window and `stride`
decimates frames (`frames[::stride]`); response `meta` carries the parser's own fields
(`t0, t1, step, vehicle_count, skipped_rows`) plus `source_file`/`source_path`,
`skipped_unresolved_vehicles`, `returned_frame_count`, and `stride`.

**Synthetic demo data — `tools/make_demo_trajectories.py`.** 32 vehicles over a 180s/0.5s-step
window, round-robined across 5 hand-defined routes over the showcase network (all 7 of its
connectors get exercised): `harbor_roundabout_eastgate` (401→503→403→511→423→424→512→427, the
Harbor→arterial→roundabout→East Gate movement named in the P3 brief), `harbor_hill_rightturn`
(401→501→407), `ridge_t_route` (409→521→408→502→403, the T-intersection movement), a single-link
`dock_lane_stopsign` (406) that must stop at its stop sign, and `summit_roundabout_eastgate`
(425→513→421→422→423→424→512→427). Every position that reflects real network topology
(connector attach points, the reduced-speed-area bounds, signal/stop-sign positions) is read
directly from `tools/make_showcase_network.py`'s own source tuples rather than duplicated as a
literal, so the two files can't silently drift apart; only the per-link cruise speeds (km/h)
are authored, since the fixture carries no speed data. Kinematics are piecewise-constant speed
per link with an authored slowdown across the arterial's reduced-speed area and a full stop
(speed 0) wherever a route passes a signal-head stop line (a trivial fixed 40s-cycle, two-phase
signal — sg1/sg2 run complementary phases) or the Dock Lane stop sign (fixed 3s dwell) — this is
demo-grade for visual plausibility, explicitly not a traffic-realistic car-following model (no
acceleration ramps; speed steps discretely to 0 and back).

**Simplifying assumption, flagged for lab-box validation:** the showcase network's four
roundabout quarter-arc links (421–424) are treated as chaining directly into one another at
their shared endpoints, with no dedicated connector link between them — the fixture only
defines connectors into/out of the roundabout (511/512/513), not between adjacent arcs. This is
schema-safe (the resolver only needs a valid `(link, lane, pos)` per row) but unconfirmed
against how a real Vissim roundabout export links its arc segments.

**Deferred, not implemented:** validation of the whole `.fzp` schema (this generator's own
assumptions, and `trajectory_reader.py`'s parsing of it) against a real Vissim-exported `.fzp`
remains pending the Windows/Vissim box, same caveat every other Phase 3 inferred-defensive
entry above carries. No car-following/acceleration modeling, no lane-changing, no interaction
between vehicles on shared links (routes overlap on the roundabout arcs and East Gate exit by
design, with no collision avoidance) — acceptable for a visual-plausibility demo, not a
substitute for a real simulation export.

---

## 2026-07-26 — P6: diagram selection becomes a real request field (Unit-4 chips-only scope, upgraded)

**Scope change, on the user's explicit call.** The 2026-07-24 "Phase 3 wrap" entry above
recorded decision (3): link/connector clicks reach the assistant as page-context **chips/notes
ONLY**, leaving the backend request shape untouched. That was a deliberate scope limit, not an
oversight. It is now **superseded**: Yunha asked that clicking any component in the rendered
network — link, connector, or element glyph — be referenced automatically in the chat, so the
agent knows what "이 도로 / this intersection / this signal" means without an id being typed.
Prose in a `notes[]` string cannot carry that; a model has to re-derive an id from free text
and can silently answer about the wrong object. So the selection is now **structured request
data**: `AssistantQueryRequest`/`CurateRequest` gained `selected_link_ids: list[str]` and
`selected_element: dict | None`, published by /network and /dashboard as `selectedLinks` /
`selectedElement` on the existing `vissim:page-context` event. The human-readable notes are
**kept alongside**, not replaced — they still let an answer quote what is highlighted on screen.

**Node ids stay a separate channel.** `selected_node_ids` keys result/metric lookups
(`get_selected_node_details`, movement records); link and element ids key the parsed `.inpx`.
Folding the new ids into `selected_node_ids` would have silently corrupted every intersection
lookup, so they travel as their own fields with their own resolver.

**Validation lives at the request boundary, because these strings end up in a prompt.**
`services/network_selection.py` owns it and both models share it via one `_SelectionFieldsMixin`
so they cannot drift: link ids are stripped, de-duplicated and capped at
`MAX_SELECTED_LINKS = 6` (matching the node cap the assistant router already applies) with each
id capped at 64 chars; `selected_element` is reduced to exactly four whitelisted keys
(`type`, `id`, `name`, `link`), values must be `str`/`None` (numbers are deliberately NOT
coerced, so a nested object can never leak through), `name` capped at 120 chars, and control
characters — newlines included — are stripped from every field, since a name carrying newlines
could otherwise break the JSON context block into text the model reads as instructions. A
reference without a usable `type` AND `id` is refused outright rather than forwarded.

**Client claims are re-resolved, never believed.** A link id becomes the parsed network's
`{id, name, lanes, length, is_connector, from_link, to_link}`; an element becomes its parsed
record (server-side `name`/`link` win over the client's, plus `link_name`, `lane`, `pos` when
available). An id the active network does not contain resolves to `{"id": ..., "missing": true}`
rather than being dropped — the model must be able to say "that link isn't in this network"
instead of quietly answering about nothing. Parsing reuses `parse_network` behind an
`lru_cache` keyed on `(path, mtime)`, the same cache-key shape `routers/network.py` uses, and a
missing/unparseable `.inpx` degrades to "everything is missing" rather than 500ing a chat
request — the same degrade-don't-fail policy as the P2 session view and the P3 trajectory
endpoint. Not gated on `has_uploaded_dataset` (unlike the node/scenario blocks): links and
elements come from the active `.inpx`, which exists for the bundled demo dataset too.

**Element references are context-only — no new capability.** Nothing here grants the agent a
new tool, a new write path, or a new permission. A clicked signal head tells the agent *which*
object the sentence is about; acting on it still goes through the existing gated
propose→approve tools with their unchanged guardrails. This is deliberately a naming aid, not
an authorization channel.

**Known gap, deliberately not closed here.** `selected_node_ids` has never reached the agent
orchestrator — `AgentQueryRequest` carries only `{message, response_language, session_id}`, and
`run_agent_turn` has no context parameter (grep: zero hits for `selected_node` anywhere under
`backend/services/agent/`). So the new fields reach the **plain assistant path** and `/curate`
exactly the way node ids do, and reach agent mode exactly as far as node ids do: not at all.
Wiring page selection into the agent turn is a real feature (new request fields, a
`run_agent_turn` parameter, planner-prompt surface), not a mirror of an existing pattern, and is
left as its own unit rather than smuggled in here.

---

## 2026-07-27 — P7: the on-screen selection reaches AGENT mode (the P6 entry's "known gap", closed)

**What changed.** `AgentQueryRequest` now carries the same selection the plain assistant path has
carried since P6 — `selected_link_ids` + `selected_element` via the shared `_SelectionFieldsMixin`
— **plus `selected_node_ids` and `selected_scenario_ids`, which had never reached the agent at
all**. That is genuinely new capability, not a mirror of an existing path: before this, clicking
an intersection did nothing whatsoever in agent mode (`run_agent_turn` had no context parameter;
zero `selected_*` hits under `backend/services/agent/`). `run_agent_turn` gained one optional
keyword, `selection: dict | None = None`, and the planner renders it as one delimited block in the
per-turn state block.

**Context only — deliberately no gate, tool, or preflight change.** The selection names objects;
it never authorizes anything. The write gate, the tool offer, the tier classification and the
guardrail engine's preflight/postwrite are untouched, and the planner still has to put explicit
ids in its tool params, which the guardrails still validate. Tests pin both halves: with a link
selected, the system prompt and the offered native tool defs are byte-identical to the
no-selection run, and a write decision under the default refuse-all gate is still refused
(`tool_tier_forbidden`, nothing dispatched). This is the same line P6 drew ("a naming aid, not an
authorization channel"), restated where it now actually matters — the agent can execute.

**Resolution precedence: the session's working copy, else the active dataset.** Once a session has
a `working.inpx` (its clone plus any approved+verified edit), that is the file the user is looking
at (P2's `/api/network?session_id=` renders exactly it) and the file every read tool in the turn is
pointed at (`tools_network.set_active_inpx(artifact_manager.working_path)`). Resolving the
selection against the pristine dataset instead would let the planner be told a link's *pre-edit*
identity while its own tools report the post-edit one — the exact class of quiet inconsistency
this repo's validation layer exists to prevent. A brand-new session has no working copy at request
time (`_ensure_working_copy` runs later, on the worker thread under the session lock) and falls
back to the active dataset, which is byte-identical to the clone about to be made from it. The
resolution runs in the request handler, before the stream: pure file reads, must not sit inside the
per-session turn lock, and its failure mode is trivially "no selection, turn proceeds".
`network_selection.resolve_*` therefore gained an optional `inpx_path` (`None` = active, i.e. P6's
behavior unchanged), and its parse cache went from `maxsize=2` to `4` now that working copies join
the active dataset as resolution targets.

**Compactness is a real constraint here, unlike P6.** The plain path dumps resolved selections into
a JSON context block built ONCE per request; the planner's state block is rebuilt on EVERY loop
iteration (up to `max_iterations`), so the same payload would be paid for repeatedly. Hence one
short line per object rather than raw JSON — `- link 101 "Main St EB approach" lanes=3 length=280m`,
`- node 1 "Central & Main"`, `- element vehicle_inputs 1 "Main St EB" link=101` — names truncated at
60 chars, and worst case (6 links + 6 nodes + 1 element + 1 scenario line) still a handful of
tokens. Ids the network does not contain are stated (`- link 9999: not found in this network`),
never dropped, for P6's reason: the agent must be able to say "that link isn't in this network".
Nodes resolve to `{id, name}` from the parsed `.inpx`, NOT through
`results_reader.get_selected_node_details` (the plain path's per-direction KPI detail) — the agent
needs the compact identity, and the KPI detail belongs in a tool result if it is wanted at all.
The block header is ko (`[사용자가 화면에서 선택한 객체]`, the user's own UI vocabulary) with an
English "context only — still name explicit ids in tool params" gloss on the same line, carrying
the context-only rule into the prompt itself.

**Set-or-clear, never merge.** The client re-sends its current selection on every turn. M3.3 reloads
`state.json`, whose `context` carries the PREVIOUS turn's selection, so a turn arriving WITHOUT a
selection actively clears the stored one — otherwise a link the user deselected would haunt every
later prompt in that conversation. Persistence needed no new storage shape: `state.context` is
already JSON-serialized by `session_store`, so the selection rides along with it.

**`/agent/approve` replays the proposal turn's selection.** It is written into
`pending_approval.json` next to `message`/`language` (the same "faithfully replay this exact
request" rationale that entry above already records) and passed back into the approved re-run, so
the approve turn's planner sees the context the proposal did. `AgentApproveRequest` deliberately
stays as-is — the approve request identifies a pending approval, it does not re-describe the turn —
and the replayed selection authorizes nothing: `PreapprovedWriteGate` still scopes the re-run to
the one approved action's signature.

---

## 2026-07-27 — Trajectory replay: real agent runs extend Phase 3 playback

**Context:** probed live — VehRecWriteFile is the arm toggle (no CollectData exists); the
real .fzp uses LANE\LINK\NO / LANE\INDEX columns, carries NO SPEED column by default, and
opens with a BOM-glued $VISION line. VehRecSelectedAttributes has no COM-add path (every
mutation method raised) — SPEED stays an open gap. SIZE LANDMINE fixed with evidence: the
default 0.1s resolution made a 300s run's frames JSON 90MB and the browser aborted it;
VehRecResolution verified as a proportional divisor and armed at 5 (0.5s) by default
(AGENT_RUN_TRAJECTORY_RESOLUTION), arming capped by AGENT_RUN_TRAJECTORY_MAX_S (1800s)
and non-fatal on failure — KPIs stay primary; only the first seed's .fzp is served.

**Decision:** trajectory_reader does format-detection column aliasing (one parser, never a
fork) with SPEED nullable end-to-end; frames endpoint serves completed jobs by id; /network
gained a playback-source picker (demo + trajectory-recorded runs); the agent's post-run
answer mentions replay availability. FRONTEND BUG found in live browser verification:
VissimDatasetBanner.onDatasetChange never fires on initial mount, silently disabling the
picker's dataset-revision safety filter — the picker now fetches the active dataset itself.

**Deferred:** SPEED from real exports; the duration cap bounds time but not network density.

---

## 2026-07-27 — UX reorg: curation/agent identities split; agent mode default-on

**Context:** two AI inputs read identically as "ask a box"; the M1.4 entry anticipated
flipping the agent toggle once write capability shipped behind approval — long since true.

**Decision:** (1) CurationBar reframed as a view control ("대시보드 큐레이션" eyebrow, 보기/View
button, view-lens placeholder; endpoints byte-identical; sparkle kept — 4-point glyph reads
AI-powered, not conversation). (2) FloatingAssistant is THE chat ("VISSIM 에이전트"),
agentMode defaults true, beta checkbox removed; legacy regex pipeline intact server-side as
the eval baseline, reachable only via ?assistant=legacy or the localStorage flag (documented
escape hatch, never a visible control); starter prompts refreshed to a read/edit/run triad
(the regex-era 107 combo retired). (3) Insight cards gained an 에이전트에게 요청 ghost button
dispatching vissim:assistant-handoff (the page-context event precedent) → panel opens,
composer prefilled, user still presses send.

**Consequence:** every new user's first assistant interaction is the real agent — verified
live (handoff → prefill → genuine agent turn). Contract 11/12 (exempt base-model pin only);
zero backend touch (endpoint tests 5/5).

---

## 2026-07-27 — Live-demo fix: recency-weighted observation rendering + duplicate-call guard

**Root cause of the retry-to-K-cap loop:** planner._observation_snippet applied a flat
300-char raw slice to EVERY history entry's json.dumps — measured on real Beijing data:
overview 2,853 / vehicle inputs 1,888 / conflict areas 27,592 chars, all chopped mid-JSON,
none parseable. The model truthfully reported truncation and retried the identical call
with no signal why, burning the K=6 cap.

**Decision:** (1) the MOST RECENT observation renders under AGENT_OBS_RECENT_BUDGET
(default 12000 — fits the measured envelopes whole); older entries stay compact; forced
truncation cuts at item boundaries via binary search over the largest list, appending a
localized [표시 N/M — narrow the query] marker as a valid list element — never a mid-object
chop. (2) Read-tier duplicate-call guard: a same-turn identical (tool, canonical-params)
repeat refuses pre-dispatch (duplicate_call, traced as refusal/phase dispatch) — read
results are pure functions of the turn's network state. Write/run tiers structurally
exempt (the _GATED_TIERS branch owns them; PreapprovedWriteGate's consume-on-success
re-issue is a deliberate, different repeat — its test stayed green unmodified).

**Consequence:** the 12-input list and full overview now reach the model whole; 224
conflict areas truncate honestly and machine-readably; identical retries cost zero
iterations. test_iteration_cap_forces_deterministic_completion had PINNED the buggy 6×
dispatch as correct — updated to 1 dispatch + 5 duplicate refusals; that was the bug,
never the spec.

---

## 2026-07-27 — Live-demo fix: capped enumeration in constraint MESSAGES (not details)

**Context:** distinct from the observation-rendering fix above (that one truncates the
planner's TOOL-RESULT observations feeding the model; this one is the human-facing Finding
`messages` `constraints.py` itself renders). `conflict_area_data_unavailable` (#18) joined
EVERY Passive link pair into its message — 144 pairs on one real network — and via M3.3's
accumulated-warnings carry-forward that wall of text rode every subsequent answer in the
session, not just the turn that first surfaced it.

**Decision:** added `_capped_join(items, unit_ko, unit_en, unit_ja, limit=8)` — caps the
enumerated, human-facing text at 8 items with a localized "+N more" remainder note (ko "...
외 N개/쌍", en "..., and N more ...", ja "...他N件"); `Finding.details` always keeps the
FULL, uncapped list (machine-readable, for traces/eval — nothing here is the only place the
complete data lives). Applied to `conflict_area_data_unavailable` (the reported case) and,
after grepping every `", ".join(...)` in the constraint catalog for the same shape, to the
two other checks that enumerate a list whose size tracks NETWORK size rather than the
current action: `route_integrity_unverifiable` (#31, disconnected routes — live since v2
unit ⑥) and `signal_cycle_change_may_break_corridor_sync` (siblings sharing a cycle time —
a network-wide common background cycle can leave many controllers matching). Left uncapped:
`object_not_found`'s difflib candidates (already `n=5`-capped) and `route_not_found_in_decision`'s
candidates (bounded to one routing decision's own routes); left as-is (dead CATALOG-GAP code
that never fires against a real `run_result`/shadow-model shape today, so no live flood risk
yet — `gridlock_reports_los_a`, `evaluation_warmup_start_at_zero`,
`delay_measurement_truncated_by_queue`, `demand_scale_route_double_count_gap`,
`link_geometry_edit_reference_integrity`) but worth revisiting if/when their data sources land.

**Consequence:** `conflict_area_data_unavailable`/`route_integrity_unverifiable`/
`signal_cycle_change_may_break_corridor_sync` messages now cap at 8 enumerated items + an
explicit remainder count in all three languages; `details["link_pairs"]`/
`details["disconnected_routes"]`/`suggested_fix["coordinated_controllers"]` stay full and
uncapped. New tests pin both the cap and the full-details invariant
(`test_conflict_area_data_unavailable_message_caps_enumerated_pairs`,
`test_route_integrity_unverifiable_message_caps_enumerated_routes`).

---

## 2026-07-27 — Metric-level curation filter for network-kpi-comparison

**Context:** curation filtered at section granularity only — a metric question still
rendered all six KPI cards inside the section.

**Decision:** DashboardCurationSpec gains OPTIONAL kpi_metrics (subset of the fixed
six-key vocabulary, documented in chart_catalog for the LLM: the asked metric + causal
companions; omitted for non-metric questions). Server validation drops unknown keys
without failing; absent/empty/full = no restriction; the fallback spec never carries it
(a curation failure shows everything, never a half-trusted guess). Frontend renders the
subset + a local-only "전체 지표 보기" reveal whose state resets on each new spec; the
section pill stays section-scoped.

**Consequence:** fully additive; browser-verified both directions ("왜 평균속도가 낮아?" →
DELAYAVG/SPEEDAVG/STOPSAVG only; overview question → all six, no note).

---

## 2026-07-27 — P4/P5 follow-up: vehicle canvas redraws same-frame as the zoom transform

**Context:** vehicle dots visibly drifted off the roads during a fast drag-pan or
wheel-zoom (live-demo defect #12). The shared-transform channel (P4's `ViewProjection`)
and path-accurate interpolation (P5) were both already correct — verified end-to-end by
pixel-cross-checking canvas-drawn vehicles against the SVG's own road/connector hit-shapes
across zoom, pan, play, and pause/resume.

**Decision:** `TrajectoryLayer`'s projection-update subscription drew via
`PlaybackEngine.requestFrame()`, which queues its own `requestAnimationFrame`. Since the
projection push itself already arrives from inside NetworkViewer's throttled rAF
(`emitProjection`), this stacked a second, redundant animation frame of latency onto every
live zoom/pan tick — worst while paused, when nothing else re-arms the render loop. Added
`PlaybackEngine.renderNow()` (draws immediately, no rAF) and pointed the projection
subscription at it, so the canvas is never behind the SVG by more than the one frame
NetworkViewer's own throttle already costs.

**Scope:** `frontend/components/TrajectoryLayer.tsx` only; `requestFrame()` retained for
its other callers (mount, resize, scrub-while-paused), which don't share the same-frame
requirement.

---

## 2026-07-27 — Semantic zoom for element glyphs + hover/selection recolor + overlay-freeze fix

**Context:** three live-demo defects (#13-15). (a) NetworkViewer drew static elements
(signal heads, detectors, vehicle inputs, …) with pixel-space size floors (GLYPH_MIN_PX,
hit-radius minimums, fixed strokes, node/hit-circle radii) computed once at base-fit
scale, then relied on the d3 zoom transform to magnify the group geometrically. Base fit
on the Beijing network is under ~1.4 px/metre, so most glyphs were floor-bound (52 signal
heads within a ~23×30 px region), and because floors were baked in BEFORE the transform,
zooming never resolved the clustering — overlap persisted at every zoom level. (b) Hover
showed only a tooltip; selection drew a getBBox() ring, ambiguous once glyphs clustered.
(c) The vehicle overlay could freeze at a stale transform while the SVG kept moving:
the zoom handler's projection-emit rAF was a skip-if-pending latch (one never-fired
frame wedges it shut permanently — reproduced live: wheel-zoom moved the SVG while the
canvas stayed byte-identical), and the projection store's notify loop had no exception
isolation (one throwing listener — seen live as a hot-reload-stale PlaybackEngine
missing renderNow — killed the channel and unwound the caller).

**Decision:**
1. Element/hit/node drawing extracted into drawZoomDependent(k); every pixel floor and
   fixed stroke divides by k (screen-constant post-transform; metre-true terms
   untouched). Re-run synchronously on each redraw with the persisted k, from the zoom
   handler when |k_new/k_prev − 1| > 0.05 (rAF-throttled, pan-immune), and on the d3
   zoom "end" event. scaleExtent max raised from flat 10 to max(10, 30/pxPerMeter).
2. Ring replaced by recoloring the glyph itself: primitives wrap in
   <g data-elkey="type:id">; hover/selection toggle .el-hover/.el-selected classes;
   a <style> block overrides stroke (and fill for .el-fillable glyphs) via CSS cascade
   over presentation attributes, plus a drop-shadow. ACCENT_HOVER #94bbff added;
   selection uses ACCENT #4c8dff. ringSelected deleted.
3. Both zoom-handler rAF paths (projection emit + element redraw, separate refs) use
   cancel-and-reschedule — a dead handle can never wedge the channel; the draw effect's
   teardown cancels pending rAFs so an old closure can't emit stale fit params after a
   redraw.
4. TrajectoryLayer hardening: createProjectionStore.set isolates listener exceptions
   (try/catch per listener, console.error); the projection subscriber falls back to
   requestFrame when renderNow is missing (hot-reload-stale engine in useState survives
   the swap instead of freezing the canvas).

**Consequence:** base-zoom appearance unchanged (floors still bind at low pxPerMeter);
at high zoom the same signal-head cluster becomes metre-true (~165×26 px, lane-width)
with 0 overlapping glyph bounding boxes on screen. Hover/selection recolor verified via
computed styles, zero rings remain. Overlay freeze re-tested with the exact reproducing
gesture: canvas centroid now tracks the transform, 31/31 sampled vehicle pixels on-road
after zoom. Node-dot half of the /k fix is code-symmetric but unexercised by this
dataset (nodes list empty). Contract pins preserved byte-identical; typecheck clean;
dashboard-contract 11/12 (exempt base-model pin only).

---

## 2026-07-27 — Zoom-aware element clicks, road-marking scaling, glyph-floor legibility

**Context:** three follow-up defects in the semantic-zoom redraw (5bc2c1e). (A) Element
clicks were silently swallowed: d3-zoom fires start/end even for a zero-movement plain
click, and the "end" handler unconditionally rebuilt element hit shapes between mouseup
and the browser's click dispatch — the click landed on a detached node (hover and LINK
clicks kept working, which was the tell). (B) Direction chevrons, lane-divider dashes,
and connector dashlines still had base-fit pixel floors baked into geometry, so the
deeper max zoom rendered them as giant paint drowning the roads. (C) The 2.5px screen
minimums from 5bc2c1e left element glyphs too small next to roads at working zooms.

**Decision:** (A) the zoom-"end" rebuild is skipped when |k/lastDrawnK − 1| < 1e-6 —
plain clicks and pure pans never rebuild; real zoom gestures still get the settle
redraw. (B) markings moved inside drawZoomDependent(k), metre-true with screen clamps:
chevrons 0.5×roadW capped CHEVRON_MAX_M=4m, skipped entirely below a 3px screen floor,
spacing max(25m, 40px screen); lane dividers 3m dash/9m gap at 0.15m stroke (0.7px
screen floor); connector dashes 1.5m/4.5m at 0.12m (kept shorter for the visual
distinction). (C) floors raised: GLYPH_MIN_PX 2.5→4, GLYPH_MIN_STROKE_PX 1.5→2, new
GLYPH_HAIRLINE_PX=1.4 consolidating three inline hairlines, HIT_POINT_MIN_PX 9→11,
queue-counter r/stem 3/5, vehicle-input length 10.

**Consequence:** scripted zero-movement clicks select elements on both /network and
/dashboard 도식 (and immediately after a zoom settles); deep-zoom chevron bbox measures
~4m equivalent instead of hundreds of px; mid-zoom signal heads read at ≥8px and go
metre-true past the floor. Known nuance: on very small base-pxPerMeter fits (Beijing at
~0.5 px/m) chevrons fall below the 3px floor at k=1 and only appear from k≈1.5 —
intentional (cleaner base view), revisit if "no chevrons at load" gets reported.
Playback alignment re-verified after zoom (30/30 sampled vehicle pixels on-road);
typecheck clean; dashboard-contract 11/12 (exempt pin only); contract pins byte-identical.

---

## 2026-07-27 — Live-demo fix: renderer zero-items floor, snippet demotion, conflict-area filters, warning dedup/aging

**Context:** live demo — a conflict-area status write executed correctly but the model
reported it couldn't confirm the result; get_conflict_areas rendered "0/224 표시 — 잘림";
a narrowing request was refused as a duplicate; every answer re-pasted both pre- and
post-write versions of the same Passive-count warning plus a stale earlier-turn warning.
Traced against the actual session (runtime/agent_sessions/78439ebea200): the write's
envelope was genuinely compact (800 chars, verification.readback_value intact) — "too
large" was a symptom of the renderer, not envelope bloat.

**Root cause:** planner._render_observation's binary search folds the truncation
marker's ~47-49 char JSON cost into every trial. Measured on the real envelopes: the
224-item get_conflict_areas result (27,592 chars; overhead 145; item ~106-137) needed
301 chars for 1 item + marker at the 300-char older-entry snippet budget — ONE char
over — so it settled on 0 items. Worse: the model's same-turn duplicate write attempt
(correctly refused by the approval gate) became the newest history entry, demoting the
REAL write result out of the 12000-char recent budget into that same 300-char snippet
tier, where overhead (293) + 0-item marker (47) > 300 wiped even the readback — which
the model then honestly reported as unconfirmable.

**Decision:**
1. get_conflict_areas gains optional conflict_area_no/link_no server-side filters and a
   bounded unfiltered default (40-item cap + explicit `truncated` flag + a `hint`
   telling the model to filter) — an unbounded list never reaches the renderer.
2. _render_observation gains a tiered fallback under the binary search: hard floor
   forcing ≥1 real item when it fits (full marker → shorter _COMPACT_TRUNCATION_MARKER
   → no marker); then a compact per-item key=value summary tier; then a bare-empty-list
   tier that preserves unrelated overhead fields (e.g. a write's verification) before
   ever falling to the generic too-large notice. The N/M marker reflects what was kept.
3. Verified, no change needed: the shared envelope layer (_build_success_envelope +
   _attach_guardrail_warnings) never carries a Finding's uncapped details — pinned with
   a regression test using the live incident's 145/144-pair numbers (<2500 chars,
   readback survives).
4. accumulated_warnings dedups by constraint_id alone keeping the LATEST message in
   place (was constraint_id+message, letting pre/post-write versions of one constraint
   coexist forever); _collect_history_warnings gets the same latest-wins discipline.
   _finalize_turn tracks state.context["warnings_shown"]: a carried-forward warning
   renders in full only the FIRST time; later turns collapse it to a single "이전에
   안내한 경고 N건이 여전히 유효합니다" bullet (ko/en/ja).

**Consequence:** replayed against the exact incident envelopes — the 224-item list now
keeps 1 item + honest "표시 1/224 — 잘림" (269 chars) at the snippet tier; the write
result preserves verification.readback_value (293 chars). 14 new/updated regression
tests across planner rendering, read tools, edit tools, and session memory. Full suite
972 passed / 1 skipped, only the pre-existing RAG-corpus failure. Known pre-existing
(spun off, not fixed here): test_agent_read_tools.py lacks a tools_check import so 2
registry anti-drift tests only pass in the full suite, not standalone.

---

## 2026-07-27 — Edited-model download: sibling-assets gap closed, list+download shipped

**Context:** users had no way to retrieve the .inpx an agent session edited. The tracked
ArtifactManager sibling-assets clone gap was confirmed live: clone() copyfile'd ONLY the
.inpx. Reproduced against a real PTV Vissim 2026 example network (Border Crossing
Breitenau.DE — 6 sibling files missing from the work dir) and all 25 bundled examples
were surveyed to build an evidence-based extension allowlist (.layx/.vap/.pua/.prbc/
.v3d/images). Findings: .vap/.pua/.prbc are referenced by exact relative filename inside
the .inpx XML ("#data#" token); .layx association is purely same-directory/same-stem
convention (0 in-XML references). Version retention answered: save_version() snapshots
an immutable edit_NNN.inpx per successful write — history is complete, never overwritten.
NEW FINDING while testing: VissimEditSession.persist()'s SaveNetAs-to-tmp also emits a
same-stem sibling (working_saving.layx) that never gets renamed onto working.layx —
defensively excluded from downloads, real fix spun off (needs live-Vissim verification).

**Decision:** ArtifactManager.clone() copies qualifying siblings under their original
names (SIBLING_ASSET_EXTENSIONS, state.py). New routers/agent_downloads.py: GET
/api/agent/downloads (list; session_id scopes to one session's full version history,
else latest-per-session) and GET /api/agent/downloads/{session_id}/{version} (zip when
siblings exist — only the .inpx is renamed to the dataset stem, re-establishing .layx
auto-association for free while siblings keep names the XML references; else bare
.inpx). Path safety mirrors network.py's regex + relative_to defense. Chat surfaces a
download link via a new wrote_model boolean on the turn's done event — computed from
ArtifactManager.version advancing across the turn (NOT from step tool-tier: two tests
assert exact dict-equality on step payloads). Session↔dataset association is not
persisted anywhere (AgentState.artifacts always {}), so the displayed dataset name is a
best-effort proxy of today's active dataset — documented caveat, not a new store.
Downloads page gains a "편집된 모델" card (ko/en/ja); FloatingAssistant shows "편집된
모델 다운로드" under answers that persisted a write.

**Consequence:** an edited model round-trips out of the app and reopens cleanly in
Vissim with signal programs/layouts/背景 images intact — live-verified against a real
example network's sibling set. 31 new download tests + 3 sibling-copy tests; orchestrator
/approval suites re-run green (76/76 scoped; full-suite noise at commit time is the
concurrent v2a unit ⑦'s in-flight registry work, verified unrelated).

---

## 2026-07-27 — v2 unit ⑦: vehicle-composition shares shipped; #36's "auto-renormalizes" premise disproven

**Context:** probed live on Urban Intersection Beijing.CN (3 compositions).
Net.VehicleCompositions → .VehCompRelFlows matches the .inpx child tag exactly; RelFlow
is SCALAR ONLY (RelFlow(1) raises "Too many sub-attributes"), unlike route RelFlow. NEW
LANDMINE: IVehicleCompositionRelativeFlowContainer has NO ItemByKey at all (type-lib +
live hasattr) — every other per-object collection probed so far (conflict areas, routes,
vehicle inputs, signal controllers) has one; entry lookup must scan GetAll() and match
VehType client-side, with the wrapper raising VissimObjectNotFoundError itself since COM
gives no existence signal. FilteredBy('VehType=n') rejected by Vissim's formula engine.

**Decision:** shadow_model gains vehicle_compositions (nested) + vehicle_composition_entries
(flat, composition.vehType-keyed, mirroring _flatten_routes). New get_vehicle_compositions
(bounded per the 2026-07-27 lesson: 40-cap + composition_no filter + estimated_share_pct)
+ set_vehicle_composition_share(composition_no, veh_type_no, rel_flow). CENTRAL FINDING,
live-verified: writing one entry's RelFlow leaves every sibling entry's stored value
untouched (immediately and after SaveNetAs+reload) — a raw weight exactly like route
RelFlow, share computed only at simulation time. This DISPROVES the premise of the
forward-declared placeholder vehicle_composition_share_redistribution_gap (#36) ("raising
one class's share automatically lowers the others") — corrected in place, params renamed
from the placeholder's composition_id/class_id/new_share to the real composition_no/
veh_type_no/rel_flow. Four new constraints: vehicle_composition_entry_not_found (refuse),
vehicle_composition_weight_invalid (refuse, >0 finite), vehicle_composition_referenced_by_
vehicle_inputs (warn, capped-join; vehComp lives per-interval on inputs, not once),
vehicle_composition_share_drastic_change (warn, minority→dominant). Registry 48→52.

**Consequence:** vehicle-mix editing inspectable and editable, live-verified end to end
(preflight refusals with candidates → COM write+readback == 5.85 → persisted XML with
siblings byte-untouched → load_check clean). Entry-identity stability verified
within-session only, like routes. TOOL-CATALOG v2a: 4 of 12 shipped. Full suite 1050
passed / 1 skipped / known RAG failure only.

---

## 2026-07-27 — v2a unit ⑧: desired-speed decision atomic pairs shipped; SpeedDistributions is one shared collection

**Context:** probed live on Roundabout Billings.US (Beijing.CN has zero desSpeedDecision
elements). Net.DesSpeedDecisions → per-decision .VehClassDesSpeedDistr child collection
(matches the XML tag exactly, scalar VehClass/DesSpeedDistr, NO ItemByKey — same landmine
as unit ⑦'s vehicle-composition entries). Distributions live on Net.SpeedDistributions,
NOT a dedicated DesSpeedDistributions collection (AttributeError) — the SAME object type
set_reduced_speed_area (unit ②) already injects into, XML and COM both. No shared-sum
semantics (unlike route/vehicle-composition RelFlow): each vehicle class's assignment is
fully independent, live-verified immediately and after SaveNetAs+reload. COM fails
CLOSED on a bogus distribution number. Static scan of all 584 real decisions across
every example network: name blank on every one, no grouping attribute — pairing is a
PURE human convention. Recorded landmines: COM AttValue('Lane') returns "6-1" vs XML
"6 1"; AttValue('Pos') on an imperial-display network read ~3.28× the XML value
(metres-vs-feet).

**Decision:** shadow_model gains des_speed_decisions (nested) + des_speed_decision_entries
(flat, decision.vehClass-keyed) + des_speed_distributions (flat no/name). New
get_des_speed_decisions (40-cap + decision_no/link_no filters) + SEPARATE
get_des_speed_distributions (60-cap; shared lookup table, not needed on every read). New
set_des_speed_decision: the FIRST atomic paired write — optional restore_decision_no/
restore_distr_no (all-or-nothing via pydantic model_validator) makes the SAME approved
action write a second decision's assignment for the same vehicle class, BOTH read-backs
proactively verified BEFORE persist() (prior single-target tools persist unconditionally
and let postwrite #6 catch mismatches — insufficient for two targets since #6 inspects
one written/readback pair). guardrails generalized additively: _PAIRED_EXTRA_TARGET_SPEC
+ list-of-exemptions diff signature + per-target _check_one_target_persisted. Woke the
pre-existing #30 speed_decision_unpaired CATALOG-GAP placeholder in place (was
registered against a fictional add_desired_speed_decision tool — retargeted onto the
real tool/restore_decision_no). Two new refuse checks:
des_speed_decision_entry_not_found (primary OR restore side) and
des_speed_distribution_not_found (both distr params). Registry 52→54.

**Live-caught fix:** first live-check run false-positived — the flat entry's derived
des_speed_distr_name (parse-time lookup) changes as an unavoidable side effect of every
correct write ('55 mph' → '10 mph Roundabout HGV') and wasn't exempted. Generalized
_write_target_fields with a coupled_fields mechanism on both spec tables, pinned by a
regression test reproducing the exact observed values. Separately, the getter's return
type was corrected int→str after a live postwrite type-mismatch the fake-session tests
missed (fixtures now string-typed throughout).

**Consequence:** paired slow-zone edits inspectable and editable, live-verified end to
end (four bogus-target refusal shapes → paired COM write+readback '12'/'40' on two
decisions → persisted XML with both writes and both siblings byte-correct → load_check
clean). TOOL-CATALOG v2a: 6 of 12 bullets shipped (one tool satisfies both the PAIRS
and distribution-assignment items). Full suite 1096 passed / 1 skipped / known RAG
failure only.

---

## 2026-07-27 — v2a unit ⑨: green splits shipped; the "opaque blob" is a COM-writable attribute

**Context:** probed live on Beijing.CN (FIXEDTIME) + Air Taxi Vertiport (FIXEDTIMESIMPLE).
The 26.0 type library has NO signal-program interface (35 signal interfaces scanned, zero
program ones); SG EndGreen/EndRed are real ids but read disconnected 0.0 and REFUSE
writes on program-driven controllers ("Attribute End of green cannot be changed" —
CycTm's exact pattern). CENTRAL FINDING: IntSupplyData (the base64 Vissig <sc> XML
embedding the entire program: progs→prog(cycletime ms)→sgs→cmds with display 3=green-on
1=red-on, ms units) is itself readable AND writable via SetAttValue; LoadNet does not
validate the blob's stale checkSum, SaveNetAs neither recomputes it nor drops the edit,
and a SimBreakAt+SigState oracle proved the edited program DRIVES simulated behavior
(SG1 GREEN→RED at t=11 after moving red-begin 15→8 s). FIXEDTIMESIMPLE green windows are
plain endRed/endGreen attributes. Constraint physics live-proven: Vissim refuses
negatives but silently ACCEPTS beyond-cycle values and fully-overlapping greens;
Beijing's intergreenmatrices is EMPTY (no conflict data exists to check against).
SC ProgNo is writable (program switch — not exposed as a verb this unit). Landmines:
SGs.ItemByKey exists and fails closed (unlike units ⑦/⑧'s child collections); progNo
selects the simulated program while signal_parser's dashboard phases read progs[0]
(pre-existing gap, spun off as a chip).

**Decision:** set_signal_green_split(sc_no, sg_no, program_no, green_start_s,
green_end_s) — program_no REQUIRED (first THREE-part identity {sc}.{prog}.{sg}; only
ProgNo's program is simulated — forcing the triple kills the wrong-program silent
failure); FIXEDTIME writes rewrite the decoded blob's one cmd pair and SetAttValue it
back, SIMPLE writes EndRed/EndGreen; wrapping windows (start > end) valid, never
refused. New parse_signal_programs (active program marked; non-simple cmd shapes
flagged, never guessed; min-green claimed only where a real datum exists) feeds shadow
signal_programs + flat signal_program_splits whose green_window dict is the single
guardrails value_field (partial-edge persistence still fails the compare). Registry
54→58: signal_program_split_not_found (refuse, candidates + active marker),
green_split_outside_cycle (refuse — our screen is the ONLY one, Vissim accepts
silently), green_split_below_min_green (warn, datum-gated),
signal_green_split_change_confirm (always-fires: old→new echo, head count, wrap-aware
overlap list with the honest "overlap ≠ conflict, geometry not in data" caveat, loud
inactive-program warning, conflicting-greens education). #4 extended in place to gate
the new tool. get_signal_programs read (10-controller cap + controller_no filter).

**Consequence:** fixed-time green splits are inspectable and editable end to end,
live-verified through the full pipeline INCLUDING a behavior oracle (the proof class
CycTm never had) and sibling-untouched blob evidence (prog2/sg2 + all of prog1
byte-identical after the edit). The blob writer never touches checkSum — if a future
Vissim version starts validating it, load_check + the persisted check fail loudly, not
silently. TOOL-CATALOG v2a 7 of 12. Full suite 1129 passed / 1 skipped / known RAG
failure only.

---

## 2026-07-28 — Model health-check ("모델 건강검진"): the validation layer, standalone

**Context:** productizing constraints.py + guardrails.py as a one-click report meant
confronting that most of the catalog (47/61) is action- or run-gated and structurally
cannot apply to a cold parse — a check needing proposed `params` or a `run_result` can
never fire from a static .inpx. Rather than guess which checks tolerate an empty action,
the seam is explicit opt-in.

**Decision:** `constraints.register_dataset_check` hook. 11 existing checks reused —
either called with an empty action (shadow-model-only: conflict_area_data_unavailable,
route_integrity_unverifiable, units_ambiguous_on_write, stale_route_files_after_edit) or
via PER-OBJECT SYNTHESIS: loop every real object and feed its CURRENT value into the
existing check as if it were a proposed write (volume_out_of_range,
latent_demand_overflow, cycle_below_intergreen_sum, ring_barrier_splits_sum_mismatch,
conflicting_greens_in_signal_plan, green_split_outside_cycle,
green_split_below_min_green). Plus 3 new phase="dataset" constraints unreachable from
guardrails' real run_checks() by construction: evaluation_config_disarmed (new
<evaluation> parser feeding a new evaluation_config shadow key),
vehicle_input_cont_volume_mismatch (Cont-chain integrity),
des_speed_distribution_orphan_reference. Registry 58→61. New
services/agent/health_check.py + routers/health.py: GET /api/health-check, SYNC
(measured ~31ms cold on the real Beijing network, ~2ms on demo data — far under the 3s
job-conversion threshold), plus ?format=json attachment for the paper. The report splits
`findings` (14 dataset-capable ids) from `edit_time_only` (47, derived from the registry
so it can NEVER drift from the catalog) — coverage is stated honestly, not implied.
New /health page (severity cards → grouped findings with expandable details → collapsed
편집 시 검사 section → stats footer → JSON download), ko/en/ja, nav entry in AppShell.

**Consequence:** the validation layer now stands alone as a product surface and needs NO
Vissim/COM — it runs on any uploaded .inpx, so the professor's Korean network can be
audited the moment it arrives. Live-verified on the real Beijing example with zero
fabrication: a GENUINE beyond-cycle green (controller 1 / program 1 / SG 6, window
131s→15s against a 130s cycle — exactly the silent failure unit ⑨ proved Vissim accepts),
145/224 Passive conflict areas, and an over-capacity vehicle input (link 41, 2366 veh/h
vs ~1900 rough capacity). findings_by_severity reserves an `info` bucket that is always 0
today (Tier stays refuse|warn, mapped to issue|warning). Full suite 1152 passed / 1
skipped / known RAG failure only; contract suite 11/12 (one assertion added to the
existing app-shell routes test rather than a new test).

---

## 2026-07-28 — Run-progress UI: seed-boundary bookkeeping + run_started event

**Context:** a multi-seed agent run blocks the turn for its whole duration (10+ min) with
nothing but "답변 준비 중…" — live-demo anxiety three separate times in one session.
Investigation confirmed the exact mechanism: on_step fires only when toolkit.dispatch
RETURNS, and run_simulation's dispatch blocks in _run_job_and_wait's poll loop until the
last seed finishes. The agent path's only progress signal was progress=(i+1)/n set AFTER
each seed completed. Also found latent: routers/simulations.py's module-level _jobs dict
was mutated from worker threads with NO lock, and GET handlers returned the live
reference — FastAPI's encoder could serialize a dict mid-mutation.

**Decision:** progress is bookkept ONLY at seed boundaries and COM-call phase transitions
(arming → running → collecting → done/error) in BOTH job runners — never by sampling
inside RunContinuous (no SimBreakAt chunking; perturbing a run for a progress bar is out
of scope per 00-EXECUTION-GUIDE §3.1). eta_s stays null until at least one seed actually
completes, then median(completed durations) × remaining — never extrapolated from zero
samples (the honesty rule). New module-global hook tools_run.set_run_started_callback
(mirroring set_active_run_source) fires the instant a job is SUBMITTED, before the
blocking wait, so _stream_agent_turn emits {"type":"run_started","job_id":…} — chosen
over piggy-backing the ping heartbeat because ping is a fixed-interval content-free
liveness signal and a job can start at any offset within it. Since run_simulation is
tier="run" (gated), this fires on the /agent/approve stream in practice. The frontend
polls GET /api/simulations/{job_id} independently of the still-blocked stream (polling
survives stream hiccups) and renders AgentRunProgressCard: phase label, 시드 N/M, bar,
elapsed, and "약 N분 남음" only when eta_s is real ("예상 시간 계산 중" otherwise).
_jobs_lock now guards every read-modify-write; GETs read through _job_snapshot() copies.

**Consequence:** the manual /simulation page deliberately does NOT reuse the new card —
it is always single-seed and already has finer-grained live per-simulated-second progress
over its WebSocket; seed-boundary granularity would be a downgrade. eta_s is permanently
null for a single-seed interactive run (degenerate case of the honesty rule, not a gap).
Verified sequence over 3 fake seeds: completed_seeds monotonic 0→1→2→3, exactly one
"running" transition per seed, eta_s null until the first boundary then always real. Full
suite 1163 passed / 1 skipped / known RAG failure only; contract 11/12.

---

## 2026-07-28 — Approval-card mini-map (map-focus resolver)

**Context:** a live demo showed the approval card describing its target in TEXT ONLY
("충돌구간 '3'은 링크 3와 링크 12 사이에…") — the user could not tell WHICH intersection
#3 was and approved essentially blind. Approving blind is where trust dies.

**Decision:** new services/agent/approval_focus.py — a never-raising
resolve_focus(tool, params, shadow_model, inpx_path) that REUSES guardrails'
_WRITE_TARGET_FIELD_SPEC/_PAIRED_EXTRA_TARGET_SPEC identity tables (imported, not
re-derived) to find each write tool's target, then resolves coordinates from the shadow
model (link polylines, conflict-area link pairs, route link_seq) and parse_network_elements
(signal heads / vehicle inputs / des-speed decisions). Objects with no coordinates of
their own (conflict areas, vehicle compositions, des-speed entries — verified: no x/y in
the .inpx) resolve via what REFERENCES them. Fan-out capped at 12; bbox has a 15 m
degenerate-span floor. Attached additively as a TOP-LEVEL "focus" on the approval payload
(sibling of actions/warnings, following the warnings precedent) rather than inside
action_entry, so existing `actions == [...]` exact-equality tests needed no change.
Frontend ApprovalFocusMap.tsx renders a static 152px SVG thumbnail — muted links, accent
target with glow, localized caption — reusing NetworkViewer's projection conventions
(uniform fit, y-flip) WITHOUT importing or refactoring it; fetchNetwork with a
module-level cache. Renders nothing when bbox is null or the fetch fails: a map failure
must never block an approval.

**Consequence:** set_sim_duration/set_random_seed/run_simulation have no single-element
target and render no map by design (not a gap). Live-verified against the real
SouthStreet_Base.inpx (conflict area 2140 → links 515/10969, bbox x[123.6,335.9]
y[-6395.9,-6341.2], non-degenerate) — which exposed that a link and its auto-generated
connector can share an IDENTICAL display name ("South St EB"), so the conflict-area
caption uses link IDs, not names. Full suite 1186 passed / 1 skipped / known RAG failure
only; contract 11/12 unchanged.

---

## 2026-07-28 — Health-check "AI로 수정하기" handoff + palette-lock regression

**Context:** /health listed findings with no path to act on them. Needed a per-finding
button handing the issue to the agent (mirroring the CurationBar → FloatingAssistant
handoff), with an honest classification — routing a user into a guaranteed refusal is
worse than no button.

**Decision:** classify each finding SERVER-SIDE by DERIVING from the constraint's own
`applies_to` intersected with the CURRENT write-tool registry, not a hand-maintained
table: a constraint naming exactly one live write tool → "fix"; anything else
(applies_to=["*"], or 2+ tools) → "inspect". Exactly one override:
evaluation_config_disarmed → "none" (documented always-refuse; no evaluation-config
write tool exists). conflict_area_data_unavailable is "inspect" despite
set_conflict_area_status existing, because its scan yields ONE AGGREGATE finding (111
Passive areas on the real Murdoch network) with no single groundable target.
`suggested_action.target` is bounded (count + ≤3 example ids, never the full list —
2026-07-27 observation-flood lesson). Frontend builds t()-routed ko/en/ja prompts and
dispatches the same vissim:assistant-handoff event; the listener prefills the composer
and never auto-sends (CurationBar's contract, matched exactly).

**Consequence:** additive to the report contract and SELF-UPDATING — a future verb that
starts covering, say, conflicting_greens_in_signal_plan turns that finding's button on
with zero health_check.py edits. A missing suggested_action (stale backend process)
degrades to kind="none" rather than crashing the list. Also fixed here, found by live
screenshot: the details `<pre>` used the conventional dark-code-block idiom
(bg-slate-950/90 + text-slate-200), but globals.css's PALETTE LOCK INVERTS the slate
ramp for this theme (slate-950 IS #ffffff, slate-200 is 10%-opacity white) — it rendered
as a white box with invisible white text. Wells are slate-50/100, body text the
slate-600..800 ramp; comment left in place so the next author does not repeat it.

---

## 2026-07-28 — Approval-focus bbox is anchor-driven; /network handoff by URL

**Context:** the mini-map shipped a day earlier was unusable — a conflict area rendered
as a dot in the middle of most of the network. Two compounding causes, both measured
live on the real Murdoch Drive dataset (1564×2846 m), conflict area 2140:
_FocusAccumulator.add_link fed every focus link's WHOLE polyline extent into the bbox
(212.3×54.7 m for two approach links), AND both consumers fit isotropically, so
ApprovalFocusMap's PAD_RATIO=1.1 re-inflated the short axis by the aspect factor again
— rendered 724.6×344.2 m, 43% of the network width.

**Decision:** (1) separate "what is highlighted" from "what drives the bbox" — the
accumulator now tracks an ANCHOR bbox alongside the extent; anchors win when present,
extent is the anchorless fallback; `elements` unchanged so links stay drawn for context.
A conflict area's anchor is DERIVED (it has no geometry in the .inpx) as the
segment-segment crossing of its two link polylines, falling back to closest-approach
midpoint because connectors often don't strictly intersect — 72 of Beijing's 224
conflict areas, closest-approach min 0.0 / median 1.1 / max 6.2 m. (2) Span policy:
_MIN_SPAN_M=15 kept as the degenerate guard, plus _ANCHOR_CONTEXT_M=50 (a junction
footprint is ~45-60 m — live-measured: Beijing SC1's 52 heads span 45.1×59.1 m) and
_MAX_SPAN_M=200. The cap eats the CONTEXT MARGIN first and never crops a real anchor —
hiding half a fan-out target is worse than a wide view; routes stay extent-based and
uncapped (a route IS long). (3) PAD_RATIO 1.1→0.15, map height 152→200px: context now
lives in the bbox in metres, not as a multiple of whatever the target spans. (4) The
approval→/network handoff travels as URL query params (fbox + capped fel type:id pairs,
lib/networkFocus.ts) — reload-safe, shareable, wire contract unchanged. (5) The flash
pulses ~4 cycles then SETTLES static, honours prefers-reduced-motion, retired by any
diagram selection or explicit dismiss. (6) The programmatic zoom goes through the
existing d3 zoom behaviour, with a gesture-aware settle timer because d3 transitions are
rAF-driven and a suspended rAF would otherwise strand the user unzoomed on a highlight
they cannot see.

**Consequence:** conflict area 2140 renders 226.1×141.3 m instead of 724.6×344.2 m —
3.2× linear, 7.8× areal, measured in the DOM (1.4154 viewBox-units/m read back from
rendered polylines). All 224 Beijing conflict-area focuses now fall under the cap; 19
previously did not (worst 385 m). Conflicts between two short connectors zoom OUT to a
consistent 100 m window rather than showing featureless pavement — intentional. Three
existing tests were retargeted: they pinned "the bbox contains every link point", which
is exactly the defect. NetworkViewer gains one attribute (data-linkid) and two optional
props; semantic-zoom redraw, ViewProjection channel and hover/selection recolor
untouched. Suite 1201 passed; contract 11/12 with ZERO contract edits.

---

## 2026-07-28 — Focus flash: blue, and re-triggerable

**Context:** live rehearsal on the Murdoch model surfaced two defects in the
approval-card → /network handoff shipped in e0a5f8f. (1) The flash was magenta
`#ff3ec8`; the user asked for blue, which collides with the two blues the diagram
already spends — `#4c8dff` selection and `#94bbff` hover. (2) The 네트워크에서 보기
button worked exactly once: the second click on the same target was a no-op.

**Decision:** (1) `FOCUS_FLASH = #00c2ff` — a fully saturated cyan-blue picked for
SATURATION and GLOW separation rather than hue distance, since hue distance is the axis
the request removed. Selection/hover are both desaturated periwinkles with no drop
shadow; focus is pure cyan carrying a `drop-shadow(0 0 4px)` halo, so the three read
apart even side by side on the same junction. (2) Root cause of the once-only button:
the href was fully determined by the target, so a second click produced a URL byte-identical
to the one already in the address bar — the app router has nothing to change and
`useSearchParams` never re-emits. Fixed with `fseq`, a nonce minted at CLICK time (never
at render time: a render-time nonce would re-fire the pulse on every unrelated re-render)
and made the LEADING component of `focusSignature()`, so a repeat ask yields a new
`focusKey` and the existing during-render pulse-restart path runs unchanged. Already on
/network the button uses `router.replace` rather than `push`, so re-asking does not stack
history entries. Modifier and middle clicks bypass the handler entirely and keep the
plain nonce-free href, because those open a fresh page whose focus fires on mount and
needs no re-trigger token. The parsed nonce is regex-validated, so a hand-mangled URL
degrades to the pre-fix behaviour instead of throwing.

**Consequence:** verified live against the Murdoch dataset — link 505 flashes with
computed stroke `rgb(0, 194, 255)` and the cyan drop-shadow, pulse settling to static as
designed. Frontend typecheck clean; contract 11/12 (the known base-model layout failure
only), zero contract edits. The wire contract is unchanged: `fseq` is additive and
optional, and `parseNetworkFocus` treats its absence as "no nonce".

---

## 2026-07-28 — Stage-based signal programs: a whole program family we could not read, and a stub we reported as fact

**Context.** Cross-checking Prism against the project engineer's own timing workbook on the
Murdoch Drive (Perth) production model (312 links, 6 controllers) produced two irreconcilable
stories: `get_signal_programs` claimed SC 9 runs a 375 s cycle with one signal group green
0→357 s, and reported *nothing at all* — an empty list with no reason — for the other five
controllers, including the one the engineer's AM sheet is actually about. A static XML probe
(`backend/tools/probe_murdoch_signals.py`, no COM) found that 5 of the 6 controllers are
`type="FIXEDTIME"` with an EMPTY `<progs/>` and a populated `<stageProgs>` — a STAGE-BASED
Vissig family (`<stages>` + `<interstageProgs>` + `<stageProgs>`) that unit ⑨ never met,
because NOT ONE of the 25 PTV "Examples Demo" networks uses it. Two parser faults followed:
(1) `<stageProg>`'s `<interstage display="N">` names the INTERSTAGE PROGRAM N, not stage N —
reading it as a stage id mis-attributed 3 of SC 6's 5 phases, dropped a stage entirely, and
produced a phase serving no signal groups at all on a sibling controller; (2) both phase paths
read program index 0 rather than the `progNo`-active program. The 375 s, by contrast, is REAL:
read correctly, stored verbatim in the model, and not an operable plan.

**Decision.** Read the stage family and report exactly what it stores — per-`<stageProg>`
cycle/offset and a stage sequence resolved through `interstageProg.toStage`, plus a
per-controller signal-group roster — and NOTHING more. Per-signal-group green windows are NOT
synthesized for stage programs: `interstagesUsingMinDurations="true"` compresses interstages to
a length that is not statically derivable (SC 6 program 11 carries a nominally 16 s interstage
between transitions 6 s apart, so subtracting the nominal figure yields NEGATIVE green), so
`sg_splits` stays empty and ships `sg_splits_unavailable_reason="STAGE_BASED_PROGRAM"`;
`set_signal_green_split` refuses these by name rather than with the old, misleading "command
shape is not simple" (which would send the planner hunting for a different sg_no). Both phase
paths now select the `progNo`-active program. Implausible figures are never altered or hidden —
they ship with machine-readable `data_quality` flags (`CYCLE_ABOVE_TYPICAL_MAX`,
`NEAR_PERMANENT_GREEN`, `SINGLE_SIGNAL_GROUP_PROGRAM`) plus ko/en prose, and a controller no
`<signalHead>` references is flagged `NO_SIGNAL_HEADS`. An empty program list now always carries
a reason code (`PROGRAMS_NO_BLOB` / `BLOB_NOT_DECODABLE` / `BLOB_HAS_NO_PROGRAMS`) — the old
path only explained itself for non-FIXEDTIME types, so a FIXEDTIME controller with no readable
programs rendered as a bare blank.

**Consequence.** Stage-based controllers are readable for the first time — 5 of 6 on the first
real client model. Correctness of the interstage reading is proven, not assumed, and the
controller re-verified it independently after the unit: under the new reading ALL 28 Murdoch
stage programs close into a consistent cycle (every `to_stage` equals the next `from_stage`)
and ZERO stage entries serve an empty signal-group set — both of which fail under the old
reading. SC 6's resulting phase intervals (41/20/35/29+6 s) match the engineer's field-measured
green+intergreen (40.9/19.5/34.6/33.9 s) with min greens agreeing exactly (A=8, D=6, E=8, G=6),
so the residual 131 s vs 122.18 s gap is a deliberate fixed-time fit to SCATS-actuated field
data, not an error — no program anywhere in the model carries a ~122 s cycle. Green splits
remain UNEDITABLE on stage programs by design. Beijing's reported phases change, to program 2 —
the one it actually runs. Suite 1218 passed / 1 skipped / 1 known RAG failure, controller-run;
all 25 PTV example networks still parse (129 controllers, 29 programs, 228 splits, 0 errors)
with Beijing's splits byte-identical.

**The wider lesson for the paper:** the PTV demo corpus that unit ⑨ — and every published
Vissim-COM tool we surveyed — validates against does not contain the program family real
consulting models use. Corpus coverage, not parser cleverness, was the binding constraint.

---

## 2026-07-28 — Network component search: finding a thing you can name but cannot locate

**Context.** Users routinely know the number or name of the component they want to edit and
have no idea where it sits on a 312-link network. There was no way to find it — the diagram
answered "what is this?" but never "where is that?". Meanwhile the approval card's focus/flash
machinery already solved the hard half (resolve an element to a tight, anchor-derived bbox and
make it pulse), so the question was purely how to reach it from a text query.

**Decision.** (1) Search REUSES `approval_focus`'s anchor machinery rather than re-deriving
coordinates — two resolvers that disagreed about where conflict area 2140 is would be worse
than one. The needed internals are re-exported under public names (`FocusAccumulator`,
`crossing_anchor`, `network_element_geometry`, `MAX_FOCUS_FANOUT`); the per-write-tool resolvers
stay private because they are keyed to guardrail write-target tables and mean nothing to a
query. (2) Ranking is `exact id > exact name > class alias > name prefix > name substring >
id prefix`, ties broken by family then row order so identical queries are stable.
**Deviation recorded:** the first implementation ranked id-prefix SECOND, citing an earlier
draft of the brief; demoted to last, because on this dataset a bare "5" otherwise buries the
node/composition/distribution actually numbered 5 under id-prefix noise. (3) DUPLICATE DISPLAY
NAMES ARE NEVER COLLAPSED. "South St EB" is not the two-way clash we expected — it names
**18 distinct elements**, links and connectors both. Every one is returned, each labelled
"링크 515 · South St EB" vs "연결로 10969 · South St EB", with the link/connector noun derived
from the `connector` flag the API carries (the wire `type` is `link` for both). Returning one
arbitrary winner would be strictly worse than returning a labelled choice. (4) Selecting does
NOT close the dropdown — deliberate, and the 18-way collision is exactly why: arrow-and-Enter
walks the candidates while each one flashes in turn, which is how a user tells them apart. The
list is capped at 320 px and covers 9% of the map and none of the flashed element. (5) The
pulse-then-settle state machine that lived in `/network` is now the shared `useFocusPulse` hook
plus `buildFocusFlash`, so URL-focus, `/network` search and `/dashboard` search run ONE
implementation rather than three copies. (6) Re-selecting an already-selected result re-flashes,
via the same click-time nonce that fixed the approval button in 29b5afb. (7) Hits with no
resolvable geometry render visibly disabled rather than silently doing nothing on click.

**Consequence.** Verified live on the Murdoch dataset with the backend restarted so the new
route is actually served: "South St EB" returns all 18 with both 링크 and 연결로 labels present;
keyboard-only (type → ArrowDown → Enter) selects the SECOND result and flashes link 512 at
computed stroke `rgb(0, 194, 255)`; a MutationObserver caught the pulse class re-applying when
the already-selected item was selected again; the dashboard 도식 view flashes link 505
identically; a fresh tab logs no console errors. Backend 1282 passed / 1 skipped / 1 known RAG
failure. Frontend typecheck clean, contract 11/12 with zero contract edits. `networkFocus.ts`'s
element-key map gained the 7 families search covers that approval-focus never emitted
(detector, data-collection point, queue counter, PT stop, stop sign, travel-time section,
pedestrian area).

---

## 2026-07-28 — Search ranking: the id-prefix tier has to move with the query

**Context.** The previous entry recorded demoting id-prefix to the LAST tier as a correction.
An adversarial verifier, running against the live Murdoch model rather than a fixture, showed
that correction was itself the defect — and that the controller's stated reason for it was
wrong. On this network 216 conflict areas are named `"Link <a> vs Link <b>"`, so every link
NUMBER is also a substring of 216 NAMES. With id-prefix below name-substring, typing a link
number one digit at a time — the single most obvious use of a search-by-number box — returns
nothing but conflict areas: measured live, `51` pushed the first link to result 17, and `11`
returned NO link at all inside the first 20 rows. The justification given for the demotion
("a bare 5 buries the things actually numbered 5") does not hold: exact-id is rank 0 either
way, so those were never at risk from the id-prefix tier's position.

**Decision.** The tier's position depends on the SHAPE of the query, because one fixed position
is wrong for one of the two ways the box is used. An all-digit query is a lookup by number, so
id-prefix sorts ABOVE both name tiers (`RANK_ID_PREFIX_NUMERIC`). Anything else is a lookup by
name and cannot id-prefix-match at all while ids stay numeric, so id-prefix stays LAST there —
kept deliberately rather than deleted, so that if alphanumeric ids ever appear, a partial NAME
hit still outranks an incidental id-prefix hit. Both tiers report the same wire-visible
`id_prefix` match field: the split is ranking policy, not a new kind of match.

**Consequence.** Verified live after a backend restart: `51` → links 510-514 as the top four
(was: first link at index 17); `11` → 19 links in the top 20 (was: zero); `515` → link 515
still first; `5` → node 5 / composition 5 / distribution 5 still first, THEN id-prefix links,
so the concern behind the original demotion is satisfied anyway; `south st` unchanged. Backend
1282 passed / 1 skipped / 1 known RAG failure. The pinning test now covers BOTH query shapes
and asserts the two id-prefix tiers share one match field.

**Process note worth keeping:** this defect was introduced by a controller-written spec that
asserted a ranking order without checking it against the real dataset, and it survived a unit's
own self-verification. It was caught only because an independent adversarial verifier re-ran
the queries a user would actually type. Spec assertions about ORDERING are hypotheses; on a
real network they need measuring, not stating.

---

## 2026-07-28 — Click selection: hit bands are screen-constant, and stacks are cycled

**Context.** Reported live: "the select 범위 is quite wide — clicking a small traffic
component next to a road selects the road instead, even where I clicked is not on the blue
shade", plus "road, intersection, connections are overlapped together, so it is really hard
to select a component at the back".

**Cause (defect 1).** The link hit-polyline was stroked at `Math.max(roadW + 8, 12)` in WORLD
METRES and built once, outside `drawZoomDependent(k)`. Padding fixed in world units cannot
tighten: zooming in magnified the invisible grab band exactly as fast as the asphalt, so the
band stayed ~27 m wide on a 5-lane link at every zoom. Measured live on link 513 (5 lanes,
18 m): old half-width 27.1 m vs the drawn 9.0 m.

**Decision.** Hit bands follow the same convention the point/segment floors already used:
`Math.max(roadW, HIT_LINK_MIN_PX / k)`, rebuilt inside `drawZoomDependent`. Above the floor
the grab band equals the drawn asphalt exactly — zero padding, what you see is what you hit.
Below it a screen-constant 12 px floor keeps hairline connectors clickable when zoomed out.
Verified live at k=8: clicking 6 m off centerline selects link 513, clicking 12 m off does
not (the old band would have taken it).

**Decision (defect 2).** Occlusion is resolved by REPEAT CLICK, not by a z-order guess.
`elementsFromPoint` already returns this SVG's `.nv-hit` shapes topmost-first, so a second
click within 4 px advances an index into that stack and re-dispatches a synthetic click on the
next shape — firing that shape's own existing handler rather than a parallel selection path.
Every tooltip in a stack of >1 gains `"N개 겹침 · 다시 클릭"` so the affordance is discoverable
instead of secret.

**Regression guarded.** `5774a28`'s hazard is that a plain click reads as a zero-movement d3
zoom gesture and rebuilds hit shapes mid-click. Two naive cycle-reset guards reintroduced it:
resetting on any `"zoom"` event wiped the cycle on the PROGRAMMATIC `zoom.transform` that
every selection redraw performs, and gating on `event.sourceEvent` still failed because a
trusted sub-pixel mousemove makes d3 report a real user pan. The reset now compares actual
transform magnitude (`k` delta >1e-6, or >0.5 px translation) — the same epsilon convention
the existing `"end"` handler uses. First-click selection re-verified at k=1, after a wheel
zoom, and after a drag pan.

**Cost.** Hit shapes now rebuild on zoom rather than once per network load. Bounded by the
same `drawZoomDependent` guard that already gates the point/segment floors, so a click or pan
still rebuilds nothing.

---

## 2026-07-28 — Column-mapping workbook import, and the four refusals it must make

**Context.** TK's bundle contains no observed counts (see `docs/model-questions-for-tk.md`), so
the base-model validation tab could only ever be fed by a workbook shaped like our own
template. Users need to import whatever shape their survey actually arrives in.

**Decision.** Upload is tried against the EXISTING template path first; the mapping UI appears
only when that comes back with warnings. A conforming `real_counts.xlsx` therefore still
imports in one click and gains no new step. Auto-mapping reuses `COUNT_HEADER_ALIASES` (already
ko/en aware) plus a remembered mapping keyed on a header-text signature, so the second import of
a recurring format is also click-free. Explicit mappings run through the SAME
`_parse_counts_sheet` row builders as the template path — `headers`/`min_row` became optional
parameters rather than a parallel implementation, because two importers would drift.

**The refusals are the point.** This feature's failure mode is not a crash, it is a confidently
wrong green. Four refusals block the import (not merely warn), and none of them can be clicked
past:
1. `counts_required_field_unmapped`
2. `counts_observed_model_same_column`
3. `counts_observed_no_numeric_values`
4. `counts_self_comparison_trap` — observed and model mapped to DIFFERENT columns whose values
   are nonetheless identical row-for-row.

(4) is the one that matters. Mapping modelled turning movements into the observed slot makes GEH
compare the model against itself and pass 100% forever — the exact outcome we refused to build
when TK's workbook turned out to hold only modelled scenarios. Identity is `abs(a-b) < 1e-9`
across every valid pair, minimum two pairs.

**Verified independently of the unit's own fixtures.** Controller-built workbooks: all four codes
fire; a normal workbook imports clean; a near-miss with 3 of 4 rows identical does NOT fire, so
the check is not trigger-happy; and `import_mapping` itself raises rather than only the preview
flagging it — a preview-only guard would have been worthless.

**Known wart, deliberately not fixed here.** A non-conforming upload still hits the legacy upload
endpoint first, so cancelling the mapping modal leaves that transient file active. That matches
today's behavior for any bad upload, but cancel is now a NORMAL action in this flow rather than an
error path, so it deserves its own fix.

---

## 2026-07-28 — The selection contract goes plural: `selected_elements`

**Context.** The diagram is gaining multi-select (shift+click, shift+drag box). Links and nodes
have had plural request channels since P6/P7, but elements had only a singular
`selected_element`. That is not a convenience gap. On the real client model every approach
carries THREE vehicle inputs at one coordinate, one per vehicle class — link 620 "Jennalup
ST_WB" holds 107 (Class1, 127.3 veh/h), 207 (Class2-5, 19.0) and 307 (Class6-9, 0.95), sharing
link, position AND name. A request that can name one object could not describe that approach at
all, so "이 세 개 합쳐서" was unanswerable by construction.

**Decision.** `selected_elements: list[dict]` sits beside `selected_element` on every request
model via the shared mixin, with plural sanitize/resolve helpers next to their singular
originals. Three sub-decisions:

1. *One merge rule, one place.* Plural list in the client's order, then `selected_element`
   appended unless that `(type, id)` is already present, then dedupe and cap. A pre-multi-select
   client is byte-identically unaffected. Identity is `(type, id)` ONLY — position and name are
   explicitly not identity, because the case above shares both.
2. *Cap = 8*, sized as two full approaches (2 x 3 inputs) plus headroom. The selection block
   re-renders on every planner iteration, so this is prompt cost, not an arbitrary limit.
3. *Truncation is stated, never silent.* Overflow reaches the prompt as an explicit line, the
   same doctrine as the existing "not found in this network" lines: the model must be able to
   tell the user what it cannot see.

Context shape follows the count — exactly one selected object keeps the legacy singular key, two
or more use the plural key with every object named. A group is never collapsed to a count nor
truncated to its first entry.

**Consequence.** Context only; no gate, tool permission or preflight behavior changes with it,
exactly as P7 established for singular selection.

## 2026-07-29 — Scenario palette: 15 curated colors + generated extension; comparison-set selection persists across routes

**Trigger.** With 11+ scenarios the comparison-set colors repeated (`SCENARIO_PALETTE[index % 10]`
made scenario 11 byte-identical to scenario 1), and navigating away from /dashboard and back
silently replaced the user's selection with the automatic first-four.

**Palette (`lib/scenarioPalette.ts`).**

1. *Curated set extended 10 → 15, indices 0–8 byte-identical.* Existing screenshots, the demo
   video, and muscle memory depend on scenario 1 staying `#4c8dff`. Index 9's neutral grey was
   replaced (`#9aa0ab` → `#1edecd`): it measured ΔE00 8.22 from `METRIC_BAR_COLOR`, close enough
   to be ambiguous at a 2.5px picker dot.
2. *CIEDE2000, not CIE76.* The first attempt optimized CIE76, which overestimates difference at
   high chroma; its farthest-point search walked to the sRGB gamut corners and produced a neon
   set whose true worst pair was ΔE00 6.51 (two near-identical deep blues) while claiming 19.98.
   Lesson recorded here on purpose: **a distinctness search is only as good as its metric, and it
   will exploit the metric's weakest region.** The redo constrains candidates to the original
   nine's measured CIELAB envelope (C* 34–66, L* 56–80), floors every controlled pair at the
   palette's inherited worst (ΔE00 9.14, `#f2704d`/`#e08a5a`), and caps any 60° hue band at 3
   entries.
3. *15, not 20, and that's a measured ceiling.* Three independent searches capped at exactly 6
   new colors before the hue-band rule broke — a property of where the original nine already sit.
   Forcing 20 reopens either failure (fluorescent outliers or a 4-deep hue bucket).
4. *Index ≥ 15 generates instead of wrapping* — golden-angle hue in CIELAB polar coordinates,
   gamut-clamped, same envelope. Known honest limit: within 20 scenarios the worst generated-vs-
   curated pair is ΔE00 4.82; beyond that, ~3.5 pairs appear. Distinguishable-but-similar is the
   mathematical best in a bounded single-family gamut; exact repetition is what's now impossible.
5. Pre-existing overlaps now stated in-file under the correct metric: scenario amber vs north
   direction ΔE00 3.74, pink vs south 5.38 — inherited, out of scope, recorded.

**Selection persistence (`app/dashboard/page.tsx` + new `lib/dashboardSelectionStorage.ts`).**

1. *Keyed by dataset revision, not just filtered by id.* Scenario ids are sequential per dataset
   (`S000001`…), so a different dataset plausibly reuses them; an id-only restore would silently
   resurrect a selection that means something else. Exact revision match or nothing.
2. *The 4-cap now bounds only the automatic pick.* A deliberate 7-scenario selection restores as
   7; `items.slice(0, 4)` remains the first-visit/miss fallback.
3. `clearDashboardData()` wipes the stored value — dataset change may not resurrect a selection.
4. Storage I/O happens outside React state updaters (updaters must stay pure; StrictMode
   double-invokes them), via a ref of the committed selection.

## 2026-07-29 — Co-located vehicle inputs disambiguated end to end; sibling capacity checks made honest (unit + adversarial fix pass)

**Trigger.** A user set vehicle input 107 to 6000 veh/h. The write was correct, but the network
map named 307 — the real Murdoch model puts THREE inputs (one per vehicle class) at the identical
coordinate on every demand link, sharing one name. 51 inputs, 17 groups of exactly 3; `vehComp`
aligns perfectly with the hundreds digit (1xx→Class1, 2xx→Class2-5, 3xx→Class6-9). The user had
no way to check the right object was touched — and the capacity guardrail compared one input's
volume against link capacity as if its siblings didn't exist.

**Shipped.** Composition (`composition_no`/`composition_name`) on every vehicle-input surface:
shadow model, `get_vehicle_inputs`, `resolve_object` candidates, approval focus (`composition`,
`composition_no`, `siblings` — omitted, never nulled, when absent; `data/practice.inpx` elements
stay byte-identical `{type,id,label}`), the approval card (identity line + composition chip +
before→after + sibling note), the map thumbnail (stack fan-out: target filled, siblings hollow),
the /network banner (a `[vehicle_input, link]` pair no longer falls through to the link branch),
and the diagram tooltip. `latent_demand_overflow` now sums same-link siblings; new warn-tier
`vehicle_input_siblings_share_link` states the class-mix shift a single-input change causes
(13.5% → 0.3% on the worked example), silent when the input is alone on its link. Registry 61→62.

**The adversarial pass then found, and a fix round closed, four defects — recorded because each
is a canonical failure mode:**

1. *The disambiguation fields made the siblings invisible.* The added fields grew
   `get_vehicle_inputs` to 42KB against the planner's 12KB observation budget; truncation left 15
   of 51 inputs visible — all class 1xx, because **the .inpx declares inputs in composition-major
   document order**, so any prefix truncation is class-blind. 207/307, the unit's own worked
   example, vanished from the observation. Fix: omit per-interval/varies fields when uniform
   (scoped to `tools_network.py`; the `inpx_parser.py` map helper keeps its own pinned contract),
   `input_no`/`link_no`/`composition_no` filters + measured cap 20 + link-grouped ordering so a
   prefix is complete sibling groups (the `get_conflict_areas` D1 shape). After: 11.5KB, 20/51
   shown, all three classes, 107/207/307 all visible. A feature that adds observation payload
   must be measured against the observation budget — "more data" can subtract information.
2. *Interval blindness.* Both checks summed siblings' top-level `volume` — interval 1 by
   definition — whatever interval was being written, printing exact-looking totals that exist at
   no point in time (188 silent-overflow / 47 false-warn windows on the live model). Fix:
   `_volume_at_interval`, default resolved from the write tool's own `interval_no=1` default
   (verified, not assumed); messages name the interval their numbers came from.
3. *A recommendation that fails its own check.* With siblings alone over capacity the clamp
   emitted `max_recommended_volume: 0.0` — still overflowing. Now an explicit
   `fixable_by_reducing_this_input: false` state with the sibling arithmetic. Never ship a
   suggested_fix the constraint itself would refuse.
4. *A guardrail's message layer must be updated with its arithmetic.* `_FIX_TARGET_FIELDS`
   wasn't, so the /health page asserted "1200 exceeds 1900" — literally false; the LINK total
   exceeds. Handoff now carries sibling/link totals + interval; ko/en/ja sentences state the
   true breakdown.

**Also fixed in passing:** the approval card's bare "107 → 6000" line was replaced by a detail
block whose before-value comes from `/api/network/search` (active dataset only) and is shown
ONLY while base == working copy — once the conversation has edited the model, the card says the
current value is not shown and why, instead of showing a stale number.

**Left open, deliberately:** `_check_object_not_found`'s name→id candidate pool is
first-writer-wins, so three same-named siblings collapse to one fuzzy-match candidate on the
miss path (`constraints.py` + mirrored in `tools_network._difflib_candidates`). Hit path is
correct. Follow-up unit.

## 2026-07-29 — Roads and nodes gained the pre-click hover preview elements already had

**Trigger.** User report: element glyphs show a light-accent hover preview before clicking, roads
do not — so on the surface where hit-testing matters most (overlapping links/connectors), the
user couldn't see what a click would select.

**Shipped** (`NetworkViewer.tsx` only). The element layer's cascade trick (CSS classes beat
presentation attributes; removing the class restores the drawn state exactly) extended to roads
and nodes: `.link-hover`/`.link-selected` on `polyline[data-linkid]` (the road fills already
carried the id for focus-flash), `.node-hover`/`.node-selected` on a new `data-nodeid` on the
node dot. Stylesheet order preserves the established precedence everywhere:
**hover < selected < focus-flash** — hovering a selected road cannot make it read deselected,
and "look here" still beats both. Node hover survives zoom redraws via a ref + restamp at the
end of `drawZoomDependent`, the same pattern `applyFocusFlash` already uses (the node dot is
rebuilt every zoom tick; road fills draw once per network). No drop-shadow on road hover — a
blurred halo along a viewport-length polyline reads smeary, unlike a glyph-sized dot.

## 2026-07-29 — Base-workbook upload: stage-then-promote — a bad upload can no longer become the active 실측 데이터

**Trigger.** `upload_base_workbook` persisted every upload as the active workbook BEFORE
checking template conformance. A non-conforming file displaced the user's good workbook, the
mapping modal opened as recovery — and cancelling it left the garbage state active permanently.
The fix is not a better cleanup; it is never entering the bad state.

**Shipped.** `load_base_workbook_bytes` parses uploaded bytes in memory to decide conformance
before any write. Conforming: promoted exactly as before. Non-conforming: staged via the
existing `inspect_workbook` path (which never touches the active file), and the response carries
the STILL-ACTIVE workbook's data plus an additive `pending_mapping` block (staged token +
inspect payload + warnings) — the frontend opens the mapping modal from that, one round-trip
instead of two, and its React state never holds the garbage parse. `import_mapping` remains the
single promotion point. Cancel calls a new idempotent `DELETE /base-workbook/mapping/{token}`
(staging TTL GC is the backstop). A file that isn't readable as Excel now 400s instead of
silently becoming the active workbook.

**Caught in the unit's own pass, recorded as a general hazard:** `load_base_workbook` is
`@lru_cache`d; annotating its returned dict in place would have corrupted the shared cached
object for every caller. Copy before mutating a cached getter's return value.

**Verified independently by the controller:** TestClient sequence good-upload → bad-upload →
GET → cancel with isolated paths: active file byte-identical (sha256) across steps 2–4, the
bad-upload response carried the good file's rows, cancel 204 twice (idempotent).

## 2026-07-29 — Multi-select in the network diagram (v2 UX queue, final unit)

**Decisions (user's, 2026-07-28):** "전체 통일 — 일반 클릭은 교체" and "드래그 박스도 함께".

**Shipped.** Plain click on any selectable thing (node/link/element) replaces the WHOLE
selection — all three channels — with just that thing; nodes lose their old accumulate-on-click;
clicking the sole-selected thing still deselects. Shift+click adds/removes with free type
mixing; mixed selections are reachable ONLY via shift, which is what makes plain-click-replace
safe. Repeated shift+clicks at one occluded point walk to the next NOT-yet-selected candidate —
the co-located 3-stack problem became the selection mechanism (3 shift+clicks at one entry
point = 107, 207, 307). Shift+drag draws a hand-rolled screen-space box (not d3.brush: brush
brings its own gesture recognizer and would race d3-zoom's; the trio reuses the existing
drag-click bookkeeping) that ADDS everything inside. `zoom.filter()` declines shift-mousedown
only — d3's default filter allows shift, which would have produced a box AND a pan; wheel,
pinch (ctrl+wheel), plain drag unchanged. Brush listeners live on `window` so a drag ending
outside the SVG still completes. Plain click on empty background clears all (new behavior).

**Caps mirror the backend and are never silent:** nodes 6, links `MAX_SELECTED_LINKS`=6,
elements `MAX_SELECTED_ELEMENTS`=8 (both from `network_selection.py`); a truncated box-haul
shows one combined ko/en notice.

**Contract:** exactly 1 element → legacy singular `selected_element` (byte-identical to what P6
always sent), 2+ → plural `selected_elements`; one function (`selectionFieldsForRequest`) owns
the split. Probed against the live backend: 0 → None, 1 → element, 3 → all three resolved with
name+link.

**Caught in the unit's own pass:** the new invisible hit shapes could not reuse
`data-linkid`/`data-nodeid` — `applyFocusFlash` and the hover/selected CSS select on those
attributes without further scoping, so the 12px hit bands would have rendered as fat accent
stripes. They carry `data-hit-linkid`/`data-hit-nodeid` instead, read only by the
candidate-selected check.

**Known accepted limits:** a link crossing the box with no vertex inside is missed; box hit-test
uses un-fanned element anchors; tooltips aren't suppressed mid-drag.

## 2026-07-29 — Network search banner: carry hit data, not a baked label

**Trigger.** `NetworkSearchSelection.label` was composed by `searchHitDisplayLabel(hit, t)` at
CLICK time and stored as a string, so the banner froze in whatever language was active when the
user clicked — a later ko→en/ja switch re-rendered every string on the page except this one.

**Shipped.** The selection now carries `hit: SearchHitLabelParts` — the `{type, id, name,
label}` subset the label helper actually reads, `Pick`ed from `NetworkSearchHit` — and both host
pages (`/network`, `/dashboard` diagram) compose the string inline at render, so it re-localizes
with everything else. The baked `label` field was removed outright rather than deprecated: the
type has exactly two consumers, and a lingering pre-composed field invites the same bug back.
Every other consumer of the stored selection was traced on both pages (pulse key, focus-flash
memo, bbox zoom, clear button — all read data fields, none read the label); no moment-in-time
snapshot consumer existed, so nothing legitimately keeps click-time composition.

## 2026-07-29 — globals.css component classes moved into @layer components (search-icon overlap root cause)

**Trigger.** The network search box's magnifier icon rendered on top of the placeholder text.
The input carried `pl-8` to clear the icon — but it never applied.

**Root cause.** Tailwind v4 puts all utilities inside cascade layers, and **any unlayered author
CSS beats every layered rule regardless of source order**. `.ui-input`/`.ui-select` (and the
other component-ish classes) sat unlayered in `globals.css`, so their `padding`/`font-size`
shorthands silently defeated every Tailwind spacing/text utility paired with them at any call
site — the icon clearance, the clear-button clearance (`pr-8`), and a previously unnoticed
`text-xs` defeat on /simulation's API-base input.

**Fix + landmine worth recording.** Wrapped the classes in `@layer components { ... }` — but the
textbook fix DID NOTHING at first: this repo's Next 16 dev bundler silently strips a
`@layer components` wrapper placed later in the file, flattening it back to unlayered CSS
(verified by fetching the served bundle). The block only merges into Tailwind's pre-declared
`components` layer slot when placed **immediately after `@import "tailwindcss"`** — an in-file
NOTE now guards this. Blast radius audited call-site by call-site: the two real collisions are
healed (verified by live computed styles: `padding-left` 12.8→32px, /simulation font-size
14→12px); every other `ui-*` + utility pairing had no colliding property, unchanged. The diff
is pure relocation — zero declarations removed (verified mechanically), palette tokens
untouched.

## 2026-07-29 — Q3 unit ①: shared-name fuzzy-match completeness (closes the recorded miss-path follow-up)

Both fuzzy-match fallback pools (`_check_object_not_found` in constraints.py,
`_difflib_candidates` in tools_network.py) collapsed a shared name to its first-registered
owner — on the real model (17 groups of 3 same-named vehicle inputs) a typo'd query got 1 of 3
valid targets. The pools now map each name to every owner (ids stay 1:1; an id string can never
be merged into by a colliding name), bounded by a new `_CANDIDATE_CAP = 10` per surface so a
multi-group fuzzy match can't balloon the suggestion list — suggestion lists reach LLM prompts,
and bounded envelopes are a standing rule. difflib's n=5 string-match count is untouched; the
cap governs the emitted-owner dimension, which is the one this fix unbounded. Verified live:
typo "Jenalup STWB" now yields ['107','207','307','111','211','311'] (the probe also surfaced a
second real shared-name group, 111/211/311 "Jennalup St"). Suite 1373/1/1.

## 2026-07-29 — Q3 unit ②: priority-rule gap/headway shipped; "headway" is a metres-valued alias, not a third attribute

Probed live (Vissim 2026 example `Roundabout Buehl.DE`, 61 rules/124 markers — the real Murdoch
model has 13 rules/20 markers but `load_network` raises on it, a pre-existing landmine no prior
unit hit because none ever COM-loaded the real dataset; flagged as its own follow-up task, not
fixed in-unit). Findings that beat the brief's guesses, all evidence-based: `MinGapTime`
(seconds) and `MinClear` (metres) are the two real writable attributes; **`MinHdwy` is a
COM-level alias of `MinClear`** (73/73 non-zero readings equal; a write through the alias
persists as `minClear=`; Vissim's own error text captions it "Minimum clearance"; the XML has no
hdwy attribute at all — controller re-verified that last point against the real model's 20
markers). Conflict markers have NO identity attribute and no ItemByKey — marker addressing is a
synthesized 1-based ordinal, the worst entry-identity landmine yet. Constraint tiers deviate
from the brief deliberately: refuse only negative/non-finite (0 is the most common real value —
13/20 Murdoch markers; refusing it would make the tool unable to restate a network's own data),
warn above 10s gap / 50m clearance. Registry 62→65. Suite 1417/1/1 (controller re-ran; also
re-drove all four tiers + both miss paths live against the real model's shadow side).

## 2026-07-29 — Q3 unit ③: desired-speed curve editing shipped; the silent-failure map is the product

Probed live (Roundabout Billings.US): `SpeedDistrDatPts` has no ItemByKey, no add/remove —
atomic `ReplaceAll` is the only reliable write path, per-point `SetAttValue` silently no-ops
under COM-call pressure, and `ReplaceAll` demands a FLAT alternating `(x,fx,...)` sequence —
nested pairs report success and change nothing. `X` follows the display-unit landmine (proven
both directions on a MILESPERHOUR network); converted at the controller boundary so everything
above speaks km/h. **The silent-failure map:** Vissim silently accepts decreasing speeds,
mid-curve fraction reversals, absurd speeds (99999), and duplicate points; it rejects <2 points,
fractions outside [0,1], endpoints ≠ 0/1, negative speeds. Every silently-accepted row is now a
refuse-tier constraint; every rejected row is refused proactively with a clear reason. Real PTV
data disproved the brief's "strictly increasing" guess — 19 legitimate step-function
distributions exist in example networks, so only genuine decreases refuse. The ⑧ lesson became
`..._shared_target` (warn): on the real model distr 60 carries 120 decisions + 16 SLK
composition entries (the client's actual calibration data) and every edit says so. Two
guardrails regressions fixed en route: `_ALLOWS_NEW_OBJECTS_IN` (unit ② legitimately creates a
distribution) and `_ALWAYS_EXEMPT_FIELDS` (derived reference counts shift on unrelated writes).
Registry 65→72. Suite 1459/1/1 (controller re-ran; re-drove all tiers + the step-function
acceptance live against the real model's 45 distributions).

## 2026-07-29 — Q3 unit ④: vehicle-type properties shipped; the reference id says "Curve", its target collection says "Curv"

Probed live (Roundabout Billings.US). The type's writable surface is 8 REFERENCE fields
(model-2D/3D, weight, power, des/max accel/decel functions, curve-speed function) — the values
live in the referenced distributions/functions, so the verb is reference assignment with a
field whitelist, not scalar writes. Landmines: `DesCurveSpeedFunc`'s target collection is
`Net.CurvSpeedFuncs` (spelling mismatch); PEDESTRIAN types legitimately carry NO
weight/power/curve-speed (AttValue returns None — nullable-by-category, distinct from the
fail-open landmine); bogus targets fail CLOSED with a clean error. `get_vehicle_types` resolves
length (share-weighted, always metres via the model2D3D chain), weight/power stats entirely from
static XML — zero COM for reads. First write tool whose target field name is chosen at call
time → new `value_field_param` guardrails mechanism. Accel/decel FUNCTION curve editing:
probe report only (same point-collection shape as unit ③'s ReplaceAll — untested, honest
remainder). Registry 72→74. Suite 1491/1/1 (controller re-ran; re-drove read + all three
constraint outcomes on the real model's 28 types — the shared-composition warn is correctly
silent for type 200, which has zero composition refs on the real model). v2a now 10/12.

## 2026-07-31 — Q3 unit ⑤: dwell-time distributions shipped; the real object model is one GLOBAL collection

Probed statically across all 25 example networks, then live (`BRT Priority Texas.US` — the
static-scan's first pick also fails COM LoadNet, the second such network found). Verdict:
`Net.TimeDistributions` (No/Name/Type/Mean/StdDev, XML-exact, fail-closed) is the single home;
PT-stop dwell attributes are references INTO it, not value stores; stop-sign dwell does not
exist anywhere (disproves the brief's own example). Its consumers span 7 sites incl.
`drivingBehavior.w99cc1Distr` — empirically confirming the guide's "CC1 is a Time Distribution
reference" claim, which unit ⑥ inherits. Vissim itself refuses Mean edits on an EMPIRICAL
target; we refuse the reverse proactively. `Type` is COM-writable both directions — a landmine
deliberately never exposed as a verb. Registry 74→84. Real-model findings worth telling the
client: the Murdoch model HAS PT (16 stops / 12 lines / 19 line-stops), all 18 active dwell
assignments share ONE normal distribution (no=10, 10s±2s), and five staged `BusDwell_*`
empirical curves exist UNREFERENCED — built but never wired in. Suite 1552/1/1 (controller
re-ran; re-drove type-mismatch both directions, shared-target warn on the 18-ref distribution,
monotonicity refuse and implausible-mean warn against the real model).

## 2026-07-31 — Q3 unit ⑥: driving-behavior duplicate-then-edit shipped — v2a COMPLETE (12/12)

Probed live (Roundabout Billings.US). `DuplicateDrivingBehavior(Source, NewKey)` is a
purpose-built COM method producing an exact 26/26-attribute copy (the tempting
`AddDrivingBehavior` creates from Vissim's DEFAULT template — verified different, never used).
The user's safety design is mechanical, not advisory: `set_driving_behavior_parameter` REFUSES
any behavior with ≥1 reference (`driving_behavior_shared_object_edit`), naming the consumers
and pointing at duplicate → edit-the-zero-ref-copy → assign; on the real model every one of
the 15 behaviors is referenced (1:1 with link-behavior-types; #101's type covers 293 links —
every named corridor), so ALL in-place edits refuse there, which is exactly right. **W99
CC0–CC9 excluded on purpose:** the pre-existing agency-restriction constraint (#42,
WSDOT/Maryland-sourced) unconditionally refuses CC writes; a CC verb would silently route
around our own policy refusal — the whitelist is 8 W74/lane-change/lateral scalars instead.
Landmines: `NetPara.UnitLenShort` (not UnitLength) drives length-field display units — new
conversion helper; `AddVehClassDrivingBehavior` hits a reproducible pywin32 marshalling bug, so
creating NEW per-class overrides is a documented refusal (mutating existing ones works). Two
pre-existing constraint defects fixed en route: #40 trusted a gameable caller flag (now
reference-count based) and #33 checked param names that never existed (`axhze` et al.).
Registry 84→88. Suite 1603/1/1 (controller re-ran; re-drove refuse-on-shared, range refuse,
both not-found paths and the clean duplicate against the real model).

## 2026-07-31 — Q3 unit ⑦: rollback_last_edit shipped — undo that never forgets

The artifact layer already had everything file-level (append-only `edit_NNN.inpx` chain,
byte-identical `rollback_to`, version counter that never overwrites history); what was missing
was metadata — which tool produced which version, and whether the latest version IS a rollback.
`rollback.py` recovers that from the already-persisted `state.json` history rather than adding
a new persistence mechanism. **One-level undo with an explicit boundary, deliberately:** with
no predecessor pointer, a naive `latest−1` stack would REDO the undone edit on the second
invocation (undoing v2→v1 creates v3 whose content equals v1; `latest−1` from v3 is v2). A
second consecutive rollback refuses with a clear reason; a fresh edit resets the boundary.
Matching reality beats faking a stack. Rollback is itself a write — same approval, same
postwrite "nothing else moved" check, via a new diff-driven dynamic exemption
(`diff_shadow_models`) since its target is whatever the UNDONE tool touched, including object
additions/removals no static spec table could name. A warn constraint
(`rollback_invalidates_run_results`, #89) fires when a simulation ran since the undone edit.
The ledger records the rollback AND which edit it undid — the audit trail never loses
information by undoing. Registry 88→89. Suite 1632/1/1 (controller re-ran; independently
verified byte-identical restore by sha256 on a throwaway clone — and found in passing that
`save_version` refuses to snapshot a corrupt working copy, which is correct).

## 2026-07-31 — Q3 unit ⑧: apply_edit_plan shipped — one approval, all-or-nothing batches. QUEUE 3 COMPLETE (8/8)

A batch never calls `save_version()` itself — only the orchestrator's existing generic
post-dispatch call fires, once, so a successful batch is exactly ONE saved version and
`rollback_last_edit`'s one-level undo reverts the WHOLE batch with zero changes to
`rollback.py`; the mid-batch abort path is a plain file snapshot outside the version chain.
Batch postwrite uses a REAL before/after `diff_shadow_models` instead of a static per-step
target prediction — the static approach broke live (a step tool's own `_ALLOWS_NEW_OBJECTS_IN`
exemption doesn't propagate through the wrapper), the diff approach verifies what actually
changed. **Cross-step combined effects escalate to refuse-tier regardless of the underlying
constraint's own tier**: later steps' preflights run against a shadow model patched with
earlier steps' intended values, and any finding present only in the patched pass is by
construction invisible to per-step review — a warn on a 10-step card is a click-through
waiting to happen. Controller verified the flagship case on the REAL model: 107→1000 and
207→1000 each pass alone; the two-step plan refuses with the exact arithmetic (1000.0 +
1000.95 > 1900 on link 620) — the very objects from the user's original bug report now
demonstrate the system's deepest check. `run_checks` accepts `list[Finding]` (additive).
Registry 89→90. Suite 1659/1/1.

**Queue 3 final tally:** 8/8 units, suite 1367→1659, registry 62→90, v2a 7/12→12/12 COMPLETE,
plus rollback + batch machinery and two pre-existing constraint defects fixed en route.
Standing finding for follow-up: the real Murdoch .inpx fails COM `load_network` (writes
preflight fine — shadow-based — but COM applies need a loadable model; task chip filed).

## 2026-07-31 — get_priority_rules: `intLink` reference form resolved (real-model rule 143)

Real Murdoch model, priority rule no=143 + its single conflict marker reference their link via
`intLink="613"` — no `lane` attribute at all — the only `intLink` user among the model's 13
rules / 20 markers; `get_priority_rules` showed blank link/lane for both. Adjudicated as a
**parser gap, not incomplete model data**, on static evidence: the model is pure micro (all 6
nodes `useForMeso="false"`, so no separate meso internal-link numbering space exists),
`<link no="613">` is a unique, ordinary 2-lane ~355 m link (not a connector), both pos values
(131.16 rule / 164.77 marker) fall inside its polyline length, and neighbouring rules 144/145
reference the SAME link lane-wise (`lane="613 1"`/`"613 2"`) — so `intLink` numbers ARE the
regular link numbering. Fix: `_parse_priority_rule_link_ref` (tools_network.py) resolves both
forms; every rule/marker dict gains an additive `link_ref` field (`"lane"` | `"int_link"` |
`""`) so a link-wide reference is never mistaken for missing data; `lane` stays `""` for the
`intLink` form (genuinely lane-less in the XML, never guessed); the `link_no` filter now
matches rule 143. Pinned by a synthetic fixture mirroring the real shape (the live client file
stays read-only, not a test asset). Suite 1661/1/1.

## 2026-07-31 — miss-path candidates enriched with names (closes the demo-run turn-kill defect)

Live product trace: a typo query resolved successfully to `found: false` + six BARE id strings;
the model, unable to tell which anonymous number matched its query, re-issued the identical
call four times into the duplicate-call guard and the turn died at the iteration cap. **A
successful-but-unhelpful observation is a turn killer** — and bare ids are the same-name trap
one level down (this model has two trios sharing names). Both fallback paths
(`_difflib_candidates`, `_check_object_not_found`) now emit `{id, name, object_type, +
disambiguators}` dicts reusing the hit path's helper; ko/en/ja messages render capped
`id(name)` text — `후보: 107(Jennalup ST_WB), 207(Jennalup ST_WB), …`. This reverses unit ①'s
deliberate "not a schema change" conservatism with field evidence in hand. Cap arithmetic
re-measured: worst case ≤12% of the observation budget, `_CANDIDATE_CAP` stays 10. Consumer
audit found one bare-id pin in test_agent_guardrails.py (outside the agent's file set —
controller applied the one-liner). Suite: only the pre-existing RAG failure remains.

## 2026-07-31 — Murdoch COM-load failure root-caused and fixed: it was the scenario-management flag, not the file, not the install

Vissim's error is generically unhelpful ("The network file could not be loaded", no .err file).
Root cause: the client dataset is a "Place Under Scenario Management" export — its own XML says
`<netPara underScenMngm="true">`, and `LoadNet(additive=False)` raises for exactly that class.
The version hypothesis was properly DISPROVEN (installed 2026.00-09 is newer than the file's
2026.00-05; a header-swap test changed nothing; files both older and newer load fine).
**A tempting wrong fix was rejected on evidence:** `additive=True` loads — and silently merges
11 default vehicle types from Vissim's blank project into the model (39 vs the source's 28).
Silent wrong numbers, refused. The shipped fix normalizes `underScenMngm` to `"false"` in the
WORKING COPY only (`ArtifactManager.clone()` / `rollback_to(0)`; the source file is never
written), mirroring `simulation_network_builder._make_network_standalone` — the job-run path
had this fix for months; the agent-edit/load_check clone path simply never received it.
Live-proven end to end on a real-dataset clone (`load_check` ok; controller re-ran the COM load
independently: 312 links / 28 vehicle types / 6 controllers, source sha unchanged). Boise's
same-looking failure is unrelated (RBC controller licensing). Consequence: **every COM apply
now works against the real client model — demo Part 2 no longer needs the example network.**

## 2026-07-31 — write results always tell their story: message bounding + a renderer guarantee

Second demo run's trace: rollback succeeded but its envelope was so large the model couldn't
tell what had happened, retried, and burned approval_consumed refusals — the same
"unhelpful observation breeds retries" family as the bare-id candidates. Root cause was
`repr()`-ing full before/after values (curve point lists) into `message` itself; the same
latent bug existed in `set_des_speed_distribution_points` and `set_time_distribution_points`.
Fixed at BOTH ends: (a) tool messages never inline unbounded values — lists render as
`[N item(s)]`, diffs cap at 2 entries + "+N more", batch messages are per-step one-liners
capped at 3, full data stays in `verification`/`artifacts`; (b) `planner._render_observation`
gained a message-preserving fallback tier — an oversized envelope keeps
`status`/`error_type`/`message` (shrinking the text if needed) instead of collapsing to the
too-large marker, protecting every future tool by construction. Also: the system prompt now
instructs one-write-per-approval with explicit pending-step tracking (a demo run dropped a
step after a tier refusal), the `tool_tier_forbidden` message no longer claims writes "arrive
in a later milestone" (stale since M2) and instead says the step is still owed as the next
approval (controller applied — outside the agent's file set), and approval cards render object
params as bounded JSON instead of `[object Object]`. Controller re-verified the renderer
guarantee with a 40KB envelope at both tiers.

## 2026-07-31 — Phase 0.2: report data-extract + figure styling v1 shipped

CSV export (utf-8-sig for Excel/Korean) for the four report blocks that render actual
tables/figures, each endpoint backed by the SAME functions the .docx builder uses — extracted
`los_summary_rows`/`flow_geh_csv_rows`/`geh_validation_csv_rows` rather than computing twice
(the three-GEH-pipelines lesson applied preemptively). XLSX deferred: every source is a single
flat table. Styling threads through ONE seam: `render_travel_chart(spec, style=None)` where
every field is optional and omitted-style is test-pinned byte-identical to the pre-existing
output; the frontend's 회사 스타일 profile (localStorage `vissim-report-style-profile`,
JSON export/import) is translated by `profileToApiStyle()` which OMITS the style key entirely
when the profile is empty — the no-profile default is byte-identical by construction, not by
care. Default palette seeded from `scenarioPalette.ts` via a new `getDefaultReportPalette()` so
report and dashboard colors cannot drift (the one approved scope addition). Suite 1739 green +
15 new tests; the only new failure seen in the agent's run was a transient 529 on the live-API
smoke test, unrelated.

## 2026-07-31 — Phase 0.6: Category 1/2/3 validation profiles shipped

MRWA §5 Table 5-12/5-13 thresholds centralized into `lib/validationProfiles.ts` (all three
categories, each entry carrying its guideline citation) + `lib/validationCategoryStorage.ts`
(persisted, deliberately NOT dataset-revision-scoped — the review category is a property of
the engagement, not the data). Segmented selector on /base-model; default Cat 1 = today's
byte-identical behavior; live-verified that Cat 2 flips every criterion label and the
pass/fail comparisons ride the same profile object. Recorded gaps, not hidden: flow-band
aggregate targets and R² minimums are DEFINED for all categories but render display-only (no
pass/fail evaluation existed and none was built here); the ±15%-or-1-minute alternate
travel-time band is captured as data while the page still applies flat ±15% (pre-existing);
report-path thresholds unexamined (out of this unit's bounds). The unit also surfaced two more
pre-existing broken pins hiding behind the known base-model failure (the contract test aborts
at first failure, masking later ones) — chip filed.

## 2026-07-31 — Phase 0.1: demand-stress sweep MVP shipped (run_demand_stress_sweep)

The "+10% 수요면?" question now gets simulated evidence: BASE and SCALED variants through the
existing job queue with an IDENTICAL seed set (5–10, §5 rule, test-pinned), per-KPI deltas +
per-seed breakdown, one bounded ko headline. Isolation is by construction, not restoration:
the scaled variant is written to a temp clone parsed READ-ONLY from the working copy, and the
tool computes the working copy's sha256 before/after and RAISES on any difference — a
deliberate-tampering test proves the guard. KPIs/timeouts/job plumbing reused from run tools
(module-qualified so tests fake both variants identically). Live-proven: 10 real runs in 280s,
+10% demand → +11.5% delay / −6.5% speed / +15.7% travel time, sqlite-verified run rows,
hashes identical. Deferred, recorded: scale bounds live in the tool (constraints.py was
fenced for the concurrent MRWA-pack unit) — follow-up adds an always-fires warn-tier
`demand_stress_sweep_plan` disclosure so the approval card's scale/seeds/run-count text is
code-guaranteed. Standing finding: **`data/practice.inpx` is NOT live-COM-loadable** (no
vissimVersion, missing real-export sections — a hand-authored static fixture); every live
check substitutes an Examples Demo network, and that is now the recorded convention.

## 2026-07-31 — Q4 ①: the v2b XML-build spine shipped; "lane closure" has no native object

Probed before building: zero dedicated closure object exists in any of 20 example networks,
the COM type library, or the schema — the real mechanism is `Lane.BlockedVehClasses`, a COM
setter that is nonetheless STRUCTURAL at the XML level (a non-empty set serializes as a new
`<blockedVehClasses><intObjectRef/></blockedVehClasses>` child; empty removes it). So
`set_lane_closure` ships as one verb (`vehicle_classes=[]` IS reopen), and the spine's
genuinely-structural proof pair is `remove_last_lane`/`add_lane` — last-lane-scoped because
lanes have NO identity attribute (ordinal addressing; removing a middle lane would silently
renumber every `"{link} {lane}"` reference in RSAs/priority rules). Spine components
(services/inpx_structure.py, all reusable): schema-aware construction/removal +
alphabetical-section insertion + max+1 id allocation; reparse verification; bidirectional
reference integrity on a data-driven extensible registry (removal refuses NAMING the
referrers); and the COM load oracle as structural postwrite — an edit that reparses clean but
fails Vissim's own load is rolled back to exact pre-edit bytes before the caller sees it.
Proven: `rollback_last_edit`'s diff-driven exemption generalizes to structural adds/removes
with ZERO new rollback code. Shadow model gained `link_lanes` — and on first contact with the
real model read 530 lanes including genuine pre-existing blocks (lane 505.1 blocks classes
1030/1635 — the client's own bus-lane coding). Net zero new constraints (#37 was
forward-declared for this verb in 2026-07-29; a lane-index preflight is a recorded follow-up).
Registry 90→91. Suite 1780/1/1 (controller re-ran). v2b: 1/13 + spine.

## 2026-07-31 — Q4 ②: measurement-infrastructure verbs shipped — first COM-create structural unit, first planner-facing removes

All four object types (queue counters, data-collection points + measurements, travel-time
sections) have native COM Add/Remove — chosen over XML injection with evidence (clean readback,
lower schema risk). Landmines recorded: pywin32 Add* params need real COM objects, not
scalars; the display-unit landmine holds on CREATE paths too (positions in UnitLenShort both
directions, ratio 3.280840 proven on a FEET network); and the flagship: **COM's own
RemoveDataCollectionPoint silently leaves a dangling measurement reference — Vissim does not
enforce its own referential integrity; our spine check is the only refusal**, proven live with
the referrer named. A live-caught float-noise bug (m↔ft round-trip vs exact dict compare in
write verification) fixed by 6dp rounding at the envelope boundary only. The travel-time
probe also settled `inpx_parser`'s three guessed endpoint shapes: only `<start link pos/>
<end link pos/>` is real. Registry 90→91 (`measurement_infra_pos_out_of_range`). Suite
1780→1839 (controller re-ran). Real-model facts: Murdoch has 13 travel-time sections but
**zero queue counters** — MRWA queue validation cannot run on it today; genesis auto-placement
(V9) is what closes that. v2b: 2/13 + spine.

## 2026-07-31 — Q4 ③: link closure + turn bans shipped; Vissim's silent conflict-area cascade is the case this layer exists for

Probed: no link-level closure attribute and no turn-ban COM collection exist anywhere in the
2026 type library — `set_link_closure` and `set_turn_ban` COMPOSE unit ①'s per-lane
BlockedVehClasses mechanism (a connector resolves through the identical Net.Links surface a
link does; composition, not new machinery, stated plainly). `remove_connector` is the true
structural verb on the spine, and its load-bearing probe finding: **a dangling conflict-area
reference does NOT fail LoadNet — Vissim silently drops the conflict area on its next
recompute** (74% of all conflict areas across 25 example networks name a connector). The load
oracle therefore CANNOT catch this cascade; the spine's reference-integrity refusal — with
referrers named — is the only guard, and it fired live in Phase D. One new constraint,
`link_closure_route_consequence` (warn, close/ban direction only, bounded route list).

**Two pre-existing defects found and fixed because this unit exercised the real postwrite
engine:** (1) EVERY unit-② remove verb was silently broken in production — written_value vs
readback_value asserted different shapes ({removed_no: X} vs {removed_no: None}), so
constraint #6 would refuse+rollback every SUCCESSFUL removal; no prior test drove
GuardrailEngine.postwrite with a real remove payload (controller note: unit ②'s review —
mine — also missed it; the lesson is that verb units must include one real-engine postwrite
test, now the recorded pattern via `_remove_verification()`). (2)
`link_behavior_type_assignments.link_count` lacked the derived-field blanket exemption,
live-caught when a correct remove_connector was refused for shifting an unrelated count; an
audit chip for the remaining derived-field class was spawned. Registry 91→92. Suite
1839→1882 (controller re-ran; 215 tests across the four touched files green). v2b: 4/13
bullets + spine.

## 2026-07-31 — Q4 ④: signal-head + detector placement shipped

Schema resolved: heads carry ONE composite `sg="<sc> <sg>"` token (no separate controller
attr); detectors carry flat `sc` + `portNo` scalars — settling the old catalog note about
port-number binding. COM-create per ② precedent; new landmines: a head's SG is assignable
ONLY as a COM object (resolved via the existing `_signal_group_by_key`), and **COM silently
fills defaults for unset optional fields** (SC=1/PortNo=1 observed) — write verification now
claims only what the caller supplied. The reverse reference gap closed: heads/detectors would
have DANGLED when their lane/connector was removed — `LANE_TOKEN_REFERENCE_SITES` extended,
and `remove_last_lane`/`remove_connector` now refuse naming them (proven live). MRWA defaults
(2.0 m setback / 4.5 m length) cited in-tool; pos omitted → stop-line-proxy default (head
pos − 2.0 m) stated in the result, never silent. One derived-field exemption bug live-caught
(head counts on signals/splits) — same class as ③'s, audit chip already open. Registry 92→93.
Suite 1882→1928 (controller re-ran). Real-model facts: 39 signal heads, **zero detectors** on
Murdoch — genesis milestone (b) is genuinely needed there. v2b: 6/13 + spine.

## 2026-08-01 — Q4 ⑤: add/remove links & connectors shipped — genesis geometry's primitive verbs

Native COM creation EXISTS: `AddLink(Key, WKT, LaneWidths)` (atomic lanes) and
`AddConnector(Key, FromLane, FromPos, ToLane, ToPos, N, WKT)` — correcting unit ③'s
"no native surface" (it had scanned the closure/lane surfaces, never ILinkContainer; recorded
so future probes scan the CONTAINER first). The coordinate finding that genesis stands on:
**WKT x/y are always raw metres on every network, while LaneWidths/FromPos/ToPos are
display-unit-scaled** — 5th confirmation of the landmine, proven by a silent 3.5-"metre" lane
arriving as 1.0668 m on a FEET network. Real geometry XML is `linkPolyPts/linkPolyPoint`
(the brief's points3D guess was wrong). Vissim silently accepts zero-length WKT and silently
dangles references on RemoveLink — both now refused by us. Two live-caught fixes: connector
creation at a junction triggers Vissim's own conflict-area recompute (new-objects exemption
extended, ③'s precedent); and pre-existing M0.3 blanket constraint `lane_count_edit_unsupported`
(applies_to "*") silently refused ANY tool carrying a lane_count param — it would have blocked
every add_link/add_connector turn forever; now exempted by tool list. Shipped fidelity:
straight links + contiguous-range connectors; curvature/z/AddConnectorEx recorded follow-ups.
Drivability spot-check honestly untested (an unrelated AddVehicleAtLinkPosition COM error,
stated not hidden). Registry 93→94. Suite 1928→1961 (controller re-ran; runtime/ dataset
sha256-verified untouched). v2b: 7/13 + spine.

## 2026-08-01 — Q4 ⑥: stop-sign + priority-rule placement shipped; AddPriorityRule is a silent no-op

The purest silent-failure specimen of the whole catalog: `Net.PriorityRules.AddPriorityRule`
EXISTS in the type library, accepts every tried argument shape (plain and VARIANT-typed),
raises nothing, returns None — and creates nothing, proven by GetAll() diff AND the saved XML.
The real creation path is `DuplicatePriorityRule` + fresh-fetched `.ConflictMarkers`
Add/Remove — the duplicate-then-edit pattern's second appearance (Q3 ⑥ driving behaviors).
Recorded limitation: a network with ZERO priority rules cannot get its first one from this
verb. Two more landmines: held child-collection references under-report after writes
(re-fetch fresh, always) and marker order is NOT append-order (indices re-derived from
readback matched by link/lane/pos). New-marker Vissim defaults read live: 3.0s/5.0m. Stop
signs are a genuine native create; corpus grep (25 networks, 477 stop signs) shows nothing
references either object by id — leaf objects; lane-token registry extended so lane/connector
removals refuse when they'd orphan a stop sign. Conflict areas re-confirmed non-creatable
(typelib: zero Add/Remove) — documented, no verb, ⑤'s recompute exemptions cover genesis.
Integration proof: Q3's gap/clearance setter edited a just-created rule's marker live.
Reviewer note: probe work-dirs under runtime/<name>/ are confirmed LONG-standing convention
(~60 since queue 1) — uploads/current is the untouchable zone, sha-verified intact again.
Registry 94→95. Suite 1961→2008 (controller re-ran). v2b: 8/13 + spine.

## 2026-08-01 — Q4 ⑦: PT stop/line placement + the BusDwell reconnection verb shipped

`AddPTLine(Key, EntryLink, DestLink, DestPos)` is a genuine native create — a minimal route
plus ONE auto-created line-stop (Vissim picks the entry-link stop); multi-stop lines need
DuplicatePTLine and `IPTLineStopContainer` has no Add at all — both recorded, with
UpdateLinkSequence's marshalling unresolved (stated scope cut). **The taxonomy of Vissim
silent failures gained a third species: `RemovePTStop` silently CASCADE-DELETES the
referencing ptLineStop** — not a refusal, not a dangle, an unannounced deletion of a different
object; our reference-integrity refusal (three registered referrer sites, including the
corpus-grep-discovered `detector.ptStop` boarding gate) fires first, naming the lines.
`set_pt_line_stop_dwell` ships — the verb Q3 ⑤'s finding was waiting for: Murdoch's five
staged BusDwell curves can now actually be wired to stops (16/12/19 PT counts + the 18-way
shared 10s±2s distribution reproduced through the new parsers). Live-caught trap recorded: an
unreachable dest_link adds fine and only fails RunSingleStep init later ("PT Line is not
complete"). Registry 95→96. Suite 2008→2052 (controller re-ran).

**Rule-breach record (controller):** this unit's agent ran `git stash`/`git stash pop` plus
two read-only git commands — the queue's worst violation (a failed pop loses work). Tree
verified undamaged (empty stash list, HEAD intact, dataset sha unchanged), disclosure was
honest, but future briefs now name `git stash` explicitly as forbidden. v2b: 9/13 + spine.

## 2026-08-01 — Q4 ⑧ (stretch): composite-intersection probe — PARTIAL, and the partial is valuable

Honest status: Stage 0 (gap probes) completed and reproduced twice; Stages 1–6 (the actual
composed intersection build) never executed — a native SIGSEGV at the stage boundary killed
the run, and the agent stopped per instruction rather than thrash. **The Stage-0 verdicts are
solid and settle the two questions genesis milestone (b) most needed:**

1. **Signal controllers ARE creatable — via `DuplicateSignalController`.** Plain
   `AddSignalController`+`AddSignalGroup` create a real but NON-FUNCTIONAL skeleton (bare
   SC+SG makes RunSingleStep fail outright); duplicating a donor copies a full working
   program. **Third appearance of the duplicate-then-edit pattern** (driving behaviors Q3 ⑥,
   priority rules Q4 ⑥, now controllers) — plain Add is a trap, Duplicate is the path; now a
   named house pattern. Same family limitation: a zero-SC network can't mint its first.
2. **Vehicle inputs ARE creatable** (`AddVehicleInput(Key, Link)`, GetAll-diff-proven twice);
   whether a created input actually generates traffic is unproven (Stage 5 never ran).

**GENESIS GAP LIST (= the next queue's spec; partial, composition-scale unproven):**
① [BLOCKING, infrastructure] sequential COM connect/disconnect cycles in ONE process segfault
after ~2-3 cycles — genesis's whole-intersection build job must use per-stage process
boundaries (probe redesigned for this, unvalidated past Stage 0) or root-cause the crash;
② `add_signal_controller` verb (duplicate-then-edit shape); ③ `add_vehicle_input` verb
(creation proven, traffic-generation check owed); ④ full-composition proof (all Q4 verbs into
one intersection) still owed — every verb is individually live-proven, the ensemble is not.

Probe script committed compile-clean; Stage 0 live-validated ×2; suite at baseline (2053 incl.
the flaky smoke), runtime/uploads sha-verified twice. v2b stays 9/13 + spine (this was a
probe unit; "new intersection construction" bullet remains ⬜ with this probe as its spec).

## 2026-08-03 — Phase 0.7: the adapter contract; Appendix A falsified the schema three times

New package `backend/services/dataimport/` (`import` is a keyword), layered
schema → base → quality → adapters. **The unit's value is what the falsification test broke.**
Mapping MRWA Appendix A field-by-field onto the first-draft neutral schema failed three times,
each fixed in the SCHEMA, never by bending the data: (1) `DetectorCountData` added — detector
volumes have no turning movement, and forcing them into `TurningCountData` would have made
every adapter in every region invent an approach and a movement; (2) the ≤1.0 cap on phase
frequency removed — repeat right-turn phasing genuinely runs a phase twice per cycle, so the
cap would have forced adapters to clamp a true value into a false one; (3)
`SignalGroupWindow.green_duration_s` added — Event History gives green LENGTH but not cycle
POSITION, and the model previously offered no way to say that without fabricating a start time.
A fourth finding needed no change but is recorded: Appendix A supplies NO turning counts at all
(Main Roads gives detector volumes; movement demand is a separate survey) — the adapter says so
with an INFO rather than leaving an unexplained empty list.

**Never-invent is enforced structurally, not by convention:** per-record `Provenance` is
required and frozen (read records must cite a locator; computed ones must name the algorithm
and its authority); `absent` maps field→reason and a validator refuses to let a field be
claimed absent while holding a value (controller re-verified both, plus that `volume_unit` has
no default — a PCU figure read as a vehicle count is the wrong number nobody notices). No
`UNKNOWN` enum member anywhere: absence is `None`, because UNKNOWN is an invented value wearing
a hat. `TurnType` is geometric not semantic — WA drives left, Korea right, so "the opposed
turn" would mean different things per country; drive side is stated once on site metadata.
Vehicle class is a reference (scheme + code + optional coarse canonical), not an enum, because
mapping Austroads 7 onto a Korean code is a research question, not an import step.

Gate tiers argued rather than felt: REFUSE only for self-contradiction (overlapping intervals,
approach total ≠ movement sum, dangling references) or a stated authority minimum; everything
else WARN — because a gate that refuses everything gets switched off, and a Monday count is a
real count while an overlapping interval is not a real anything. Collection-minimum citations
say "page cite pending" — that rule reached us via the meeting brief, not a page we can point
at, so the never-invent rule was applied to our own authority claims first. Contract:
`docs/product/adapter-contract.md`; 10 open unknowns listed there for the Daejeon side. Suite
2053→2189 (+137, controller re-ran).

## 2026-08-03 — Phase 0.3: the MRWA pack, and the client's own gaps failing it

New `backend/services/agent/mrwa_reference.py`: Main Roads' published numbers as self-citing
stdlib data (10 tables + 2 standalone values), imported by `constraints.py` as its one
documented exception to "stdlib-only" — the module is pure data with no state/COM/session
coupling, so the rule's actual purpose survives. 4 new warn-tier constraints (registry 96→100):
Table 5-9's gap-acceptance envelope and the roundabout critical-gap floor on
`set_priority_rule_gap_headway`, and MRWA-default deviation warnings on `add_detector`'s
length and setback.

**The finding is the deliverable.** Run read-only against the real Murdoch Drive model:
**all 20 conflict markers across its 13 priority rules fall outside MRWA's published
gap-acceptance envelope [4.0s, 5.0s]** — real values are 0.0/1.0/2.5/3.0s, so even the
model's maximum sits a full second under the floor — and **14 of the 20 sit below 1.5s, the
most permissive critical-gap minimum MRWA publishes anywhere** (multi-lane roundabout, op
p.53). This is a compliance gap in the client's own model, surfaced by the pack rather than a
bug in it. Controller re-measured every number straight from the XML before recording it: the
executing agent reported 13/20 below the floor, actually 14 (11 markers at 0.0 + 3 at 1.0).
The same audit falsified a **pre-existing** claim that Queue 3 unit ② had propagated into two
comment sites and a test — "13/20 markers carry minGapTime=0" is actually **11/20**; 13 is the
priority-RULE count, transcribed into the wrong denominator twice, a year apart, by two
different agents. All three sites corrected. `minClear` never reads 0 on that model at all
(6–23 m), so the "zero is a common real value" argument is attested for gap_time only.

**Citation discipline, extended from 0.7's precedent:** every entry self-cites or says
literally "page cite pending" — never a guessed page. `TABLE_5_8_RSA_DESIGN_SPEED` ships
**deliberately empty with `complete=False`**: no radius/speed breakpoints for it exist in any
material on this machine, and a plausible-looking invented table is worse than a documented
hole. Tables 5-9/5-10/5-6 are likewise `complete=False` — only the rows actually transcribed
from the source are encoded, none padded. The detector p.78 cite is inherited from
`tools_edit.py` **with its own caveat reproduced**, because the local Appendix A extract stops
at ~p.49 and cannot confirm it. A test greps for page-shaped citations and fails any not on an
allowlist of independently traceable ones.

**Skipped with reasons, not silently:** conflict-area gap vs Table 5-10 (no front/rear-gap
attribute exists anywhere in this codebase, COM or XML — verified both surfaces); reduced-speed
area vs Table 5-8 (no consumer *and* no data); Table 5-6 and the saturation-flow bands (no verb
exposes anything they could bound — W99 CC0–CC9 are institutionally excluded from every write
verb already). Those four ship as data awaiting a consumer. Movement type is **not inferable**:
`affectDrivDir`/`effectDrivDir` are Vissim's FORWARD/REVERSE lane flag, never a turn label —
controller confirmed all 13 rules and all 20 markers on the real model read FORWARD — so the
check compares against the whole envelope and the rendered message says so rather than implying
a movement-specific match. Suite 2189→2224 (+35).

## 2026-08-03 — Q5 ①: the composite probe ran end to end; genesis gaps ①, ③, ④ closed

Stages 1–6 and Phase B had never executed. Both now do; the second run is green end to end
(exit 0, every stage PASS). **Gap ① closed — per-stage process boundaries work:** 8 stage
subprocesses across two runs, zero native crashes, and the orchestrator's own crash-recording
fallback never fired. Each stage holds ~2 COM cycles, under the 2–3 that killed the
single-process design. **The native crash itself is still NOT root-caused** — the Event-Viewer
analysis the last unit named as missing was performed and found no python/Vissim fault events
since 07-31, so there is no faulting module on record. Un-blocked, not solved; say it that way.
**Gap ④ closed:** 146 links, 307 conflict areas, 56 signal heads, 2 stop signs, 29 queue
counters, 32 DCPs, 25 TTSs, 13 vehicle inputs, `load_ok`/`init_ok` from a fresh instance.

**Two harness bugs, both in the probe, neither a verb bug (`services/` untouched).**

(1) Stage 2 made a raw `DuplicateSignalController` call inside `add_signal_head`'s postwrite
window. The guardrail refused the VERB — `write_verification_failed` (refuse), naming
`signals#2:added` plus 44 `signal_program_splits#2.{1,2}.{1..22}:added` — while all four heads
had in fact been created correctly. **The guardrail was right and the harness was wrong:** the
duplicate lived only in memory when the "before" snapshot was parsed off disk, so the verb's own
`persist()` flushed both writes and the diff blamed the verb. Fixed by persisting the duplicate
first. **This is gap ②'s spec, live-proven rather than guessed:** a real `add_signal_controller`
MUST ship `_ALLOWS_NEW_OBJECTS_IN["add_signal_controller"] = {"signals",
"signal_program_splits"}`, mirroring `add_connector`'s `conflict_areas` entry — or every correct
call is refused and rolled back. One duplicate carries the donor's 2 programs × 22 signal groups.

(2) Stage 5's crown proof was structurally incapable of passing. It read `v.AttValue("Link")`;
`IVehicle` has no such attribute, and Vissim logged `VEHICLE: Could not find attribute with ID
LINK.` once per vehicle — 13 identical lines — into the run's `.err` file while a bare
`except: continue` ate every one. Run 1's "0 vehicles on Junction A" was never a measurement.
The window was also underpowered ~40×: `simRes=10` makes 45 steps just 4.5 simulation seconds
against a 12 s headway, an expected 0.375 arrivals. **New house rule, worth more than either
bug: a "none found" result is evidence only if the harness first proves the window could have
contained one** — Stage 5 now prints simRes, sim-seconds, headway and expected arrivals before
stepping, and any locate failure is reported loudly and voids the proof.

**Gap ③'s owed traffic-generation check is ANSWERED:** 126 vehicles network-wide, 9 of them on
Junction A's own isolated links (unreachable from the rest of Beijing), 0 locate failures — an
`AddVehicleInput`-created input genuinely generates traffic that drives through a junction built
entirely from Q4 verbs. Its XML matches Beijing's real inputs (`vehComp="1"
volType="STOCHASTIC"`). Only the verb wrapper remains, so **gap ② is the last one fully open.**

Q4 ⑥'s recorded limitation reproduced on a second network exactly as predicted: Beijing has zero
priority rules, `add_priority_rule` cannot mint the first, and the composite intersection
therefore ends with `priority_rules: 0` — a real, persisting genesis limitation, not a probe
failure. Controller re-verified from the run's own machine-written state file rather than the
agent's prose. Suite 2224 passed / 1 known RAG failure / 1 skipped. `runtime/uploads/` verified
untouched by mtime: 133 files, newest 2026-07-31, **zero modified today**. A human's Vissim GUI
(PID 50960, `SouthStreet_Base2026`, open since 07-28) survived the whole COM-heavy run unchanged
— controller confirmed same PID and title afterwards. That is `attach_to_running=False` earning
its place in the hard rules.

## 2026-08-03 — Q5 ②: `add_signal_controller`, and two defects the verification caught

The last fully-open genesis gap closes. `DuplicateSignalController` is the real path — the
FOURTH appearance of duplicate-then-edit (driving behaviors Q3 ⑥, priority rules Q4 ⑥,
controllers Q4 ⑧/Q5 ①) — and plain `AddSignalController` stays deliberately unwrapped: it
creates a real object that is a non-functional skeleton, and a controller with no real timing
program makes `RunSingleStep` fail outright (live-verified twice now).

**Live findings.** `DuplicateSignalController(Source, NewKey)` accepts BOTH argument shapes —
an int key (`DuplicateDrivingBehavior`'s convention) and a COM object (`DuplicatePriorityRule`'s)
— each proven by its own `GetAll()` id-set diff, never by a bare non-exception. The verb uses
the int form so the donor is existence-checked first. **The copy inherits the donor's NAME**,
so the verb renames inside the same call; without that a network ends up with two
identically-named controllers. Gap ②'s spec, predicted from Q5 ①'s harness bug, held exactly:
`_ALLOWS_NEW_OBJECTS_IN["add_signal_controller"] = {"signals", "signal_program_splits"}`, and
the counterfactual was run rather than assumed — popping the entry turns a correct call into
`write_verification_failed` with 45 evidence rows on Beijing (1 signal + 44 splits), and
narrowing it to `{"signals"}` alone still leaves 44. Both classes are individually necessary.

**New ordering landmine:** structural controller edits are refused once a simulation has
started — after one `RunSingleStep`, `RemoveSignalController` raised "This action cannot be
performed during simulation." Harmless for an edit session, real for any future build-then-run
job reusing one COM session.

**Two constraints (registry 100→102).** A refuse when the donor is a non-fixed-time type, from
measured data rather than taste: all 49 of Boise's RBC controllers carry
`supplyFile1='#data#<no>.prbc'` and **no `intSupplyData` attribute at all** — absent, not
present-and-empty; the token occurs **0 times** in the whole 2.7 MB file, against exactly 1
populated blob in Beijing's (controller re-counted both independently). `signal_parser` yields
zero `signal_program_splits` rows for that entire family, so such a copy would be both
unverifiable and permanently uneditable. And a warn naming what the copy inherits — the silent
lie being a caller who asks for "a controller for the north junction", gets one, and reads
confident delay numbers produced by a verbatim copy of a *different* intersection's timing.

**The verification found two real defects; both are fixed here, neither was the agent's headline.**

1. **The reader echoed its own argument.** `get_signal_controller` returned `"no": str(sc_no)`,
   while its named precedent `get_driving_behavior` does a genuine `AttValue("No")` readback.
   `add_signal_controller` returns that dict for a number it *computed* (`max+1`) and the agent
   layer lifts `artifacts.new_sc_no` straight out of it — so the verb's identity claim was
   unfalsifiable by construction, in the one place this codebase's
   verify-by-state-diff-never-return-value rule most obviously applies. Now
   `str(int(AttValue("No")))`; `int()` because COM returns `No` as a float on some collections
   (`_collection_nos` already carries the same guard) and a `"2.0"` would miss every
   shadow-model id keyed `"2"`. New `tests/test_vissim_com_signal_controller.py` (8 tests) pins
   it, and the counterfactual was run: restoring the echo fails with `'7' == '42'`.
2. **A registry pin that only passed because another test file ran first.** `pytest
   tests/test_agent_read_tools.py` alone failed 2 tests — `load_check` is in
   `EXPECTED_RUN_TOOL_NAMES` but `tools_check.py` was never added to that file's imports, so the
   pin was satisfied by `test_agent_orchestrator.py` importing `orchestrator.py` earlier in the
   session. **Pre-existing since v2 ③, not this unit's doing** — and the file's own comment
   block already documents this exact bug class being diagnosed and fixed once before. The rule
   is only as good as its last line. 150 pass in isolation now.

Suite 2224→2252 (+20 from the unit, +8 from the fix above). Frontend `tsc --noEmit` clean;
contract 11/12, the one failure being the base-model layout test CLAUDE.md names as
pre-existing. `runtime/uploads/` verified untouched by mtime: 133 files, newest 2026-07-31,
zero modified today. Stale "90-constraint registry" figure corrected in
`docs/product/tk-requirements-2026-07-31.md` to carry no number, so it cannot go stale again.

**The Vissim GUI that vanished mid-run was the user closing it** — reported here because the
agent flagged the correlation with its own COM window rather than explaining it away, which is
the behavior we want. Forensics agreed before the user confirmed: `SouthStreet_Base.inpx`
unchanged since 2026-06-23, a `.vissimpdb` write at 10:41:29 (the normal-close signature), and
zero Application Error / WER / Hang events all day. No data loss, no tooling fault,
`attach_to_running=False` behaving correctly throughout.

## 2026-08-03 — Q5 ③: `add_vehicle_input` — the genesis gap list closes

The last gap-list item. Creation and traffic generation were already proven by Q5 ①; this unit
ships the verb wrapper (`tools_edit` → `edit_session` → `vissim_com`), the paired reader
`get_vehicle_input`, and the guardrail/constraint plumbing around them. Unlike controllers,
behaviors, and priority rules, vehicle inputs need no duplicate-then-edit — plain
`AddVehicleInput(link)` produces a functional object.

**The unpredicted diff row.** On a network with **no `<vehicleComposition>` at all** (Air Taxi
Vertiport), saving materialises the default composition the fresh input references, so the
postwrite diff carries a `vehicle_composition_entries` row nobody would predict from the API
name — same shape as Q5 ②'s 44 `signal_program_splits`. Hence
`_ALLOWS_NEW_OBJECTS_IN["add_vehicle_input"] = {"vehicle_inputs", "vehicle_composition_entries"}`,
with the counterfactual RUN on three real before/after pairs, not reasoned about: Beijing
12→13 inputs / 7→7 entries (popping the entry refuses 1 row), Air Taxi 0→1 / 0→1 (refuses 2),
Toll Plaza 5→6 / 86→86 (refuses 1). Narrowing to `{vehicle_inputs}` alone still refuses on Air
Taxi — each class individually necessary, though the composition class is only *provable* on a
zero-composition network (n=1; the n=2 composition-having networks never materialise one).

**Two constraints (registry 102→104), both from measured behavior.**
`vehicle_input_on_connector` (warn): a connector input is NOT dead — controlled triple on
Beijing with all 12 real inputs zeroed: plain link 13 vehicles, connector 13, null control 0.
What differs is WHERE demand enters: connector 10000 injects mid-junction, bypassing the 335 m
approach — by t=30 s, 12 of its 13 vehicles sat on the *next* connector (10002), the 13th on
link 2, vs 8 of 13 still on the approach for the plain-link arm. Nothing queues on the
approach, approach-scoped control doesn't apply, approach reports never see the demand.
`vehicle_input_created_covers_first_interval_only` (warn): a created input fills interval 1
only; the interval structure belongs to the NETWORK, not the verb (Toll Plaza's multi-interval
evidence). New landmine for the catalog.

**Ten already-shipped verbs were half-wired — found, fixed, and mis-reported.** They had
`_TOOL_TARGET_SPEC` entries but were never named in `object_not_found.applies_to`, so check #1
never ran for them: `add_connector`, `add_pt_line`, `add_pt_stop`, `remove_connector`,
`remove_link`, `remove_pt_line`, `remove_pt_stop`, `set_lane_closure`, `set_link_closure`,
`set_turn_ban`. The agent's report said it had NOT fixed them; the code wired all ten — the
verification caught the report/code contradiction, and the controller then proved the wiring
safe rather than merely undisclosed: connectors genuinely live in the shadow `links` list
(Beijing 112 = 56 + 56), real ids stay silent, bogus ids refuse, for all ten. Parametrized
tests pin each verb and fail if the wiring is reverted.

**A hand-map re-entered `health_check`, deliberately.** Generalising `volume_out_of_range` to
`{set_vehicle_input_volume, add_vehicle_input}` made its `applies_to` multi-tool, which
silently degraded the health finding from `('fix', set_vehicle_input_volume)` to
`('inspect', None)` — losing the grounded payload (`input_no`, `suggested_fix` min/max) the
frontend prompt interpolates. Restored via a second `_OVERRIDE_KIND` entry. Recorded tension:
this is exactly the hand-maintained id→tool map the derivation exists to avoid.
`test_suggested_action_never_returns_fix_without_a_real_write_tool` guards it structurally
(the tool must exist in the write tier AND in that constraint's `applies_to`); what it cannot
guard is semantic staleness — if a better volume verb ever ships, the override keeps winning
silently. Acceptable for 2 entries; a third should trigger a rethink.

**Both of last unit's defects were entry requirements here, and both held:** `get_vehicle_input`
does a genuine `int(AttValue("No"))` readback with a test that fails if it echoes, and every
touched test file passes in isolation (513/163/13/13/150/137/10).

**The verification's adversaries refuted two of our OWN claims, not the unit's code.** (1) The
probe docstring said "all 12 located vehicles" piled onto 10002 — `state.json` says 13 located,
`{10002: 12, 2: 1}`; the error came from reading the *truncated* `lanes[:12]` sample as the
population, the exact failure mode the same docstring lectures about. Fixed to "12 of its 13"
(the shipped constraint text already had it right). The en warn text's "no signal or priority
control on it applies" also read as absolute where ko/ja scope it to the approach — scoped now;
the probe's own pile-up at 10002's downstream heads shows downstream control very much applies.
(2) The integrity enumeration ("exactly 15 in-window files") was false under its own stated
filter — 158 including `__pycache__`, no right-edge anchor, plus a sibling verifier's leaked
`backend/global.txt` (deleted here, never committed). The *substantive* conclusion survived by
a stronger method: content-hashing all 353 index entries against worktree bytes (LF-normalised
per `.gitattributes`) shows exactly 12 tracked files modified, 0 deleted, forbidden docs
byte-identical to their committed blobs, `runtime/uploads/` untouched.

Suite 2252→2305. The +51 vs the agent's "+33 declared": it counted test FUNCTIONS, pytest
counts CASES — its 15 new functions in `test_agent_constraints.py` alone expand to 29 ids.
Controller re-derived the full id-set diff at HEAD in a throwaway worktree: all 51 new ids are
genuinely new; no existing parameter list grew. Full suite 2303 passed / 1 failed (known RAG) /
1 skipped.

## 2026-08-03 — Q6 ①: link geometry edits — the M0.3 premise measured false

First Q6 unit (user approved Q6 = geometry · parking · pedestrian 2026-08-03, closing 1.1's
remainder). Three verbs + a paired reader, built on a 10-part probe with a null control
(Part G: an untouched load+save moves 0 of 112 links, 0 lane counts, 0 endpoints — every diff
below is attributable).

**The catalog premise was wrong, measurably.** `SetAttValue("NumLanes")` fails loudly — but
`ILaneContainer.AddLane/RemoveLane` genuinely work on an existing link. The M0.3-era constraint
`lane_count_edit_unsupported` was built on "lane count cannot be changed"; its premise is now
measured false, so its ko/en/ja text and `suggested_fix` were rewritten and `set_link_lane_count`
exempted. Measured behaviors that became constraints/refusals: every lane-count change silently
moves the centreline by half a lane width (warn `link_lane_count_shifts_centerline`);
`RemoveLane` silently cascade-deletes lane-anchored objects — 53 heads/32 DCPs → 52/31 in a
control/treatment pair, `load_check` still green (refused via the lane-token registry, naming
referrers); shortening a link silently RELOCATES everything past the new end — a DCP at
132.485 m clamped to exactly the new end, and three far-end connectors stretched to reach,
**11.71 m → 237.70 m** for connector 10006 (refuse `link_geometry_truncates_anchored_objects`,
naming the objects and the minimum safe length). Curvature is per-point `Rad`/`RadiusSrc`, a
two-step USERINPUT write (warn `link_point_radius_overrides_geometry`). Registry 104→107.

**Units, the full map:** COM X/Y are raw metres in BOTH directions even on a feet network
(ratio 1.0 to the persisted file) while `Length2D`, `Width`, `Rad`, and `ZOffset` on the same
objects are ×3.280840 display units. z is scaled where x/y are not. Point COUNT is immutable
(no Add/Remove/Insert on the point containers; WKT exists only as an `AddLink` argument) —
`set_link_geometry` refuses a differing point count.

**Reshapes at junctions CREATE conflict areas** — live-caught by the check itself, not
predicted: lengthening Beijing link 1 by 10 m materialised conflict areas 905/906/907 and the
real postwrite refused a correct edit. Hence `_ALLOWS_NEW_OBJECTS_IN` entries for both shape
verbs ({link_lanes, conflict_areas} / {conflict_areas}), every counterfactual RUN (popped and
narrowed variants each refusing with named rows), and the asymmetry pinned by test: an *added*
conflict area is excused, a *removed* one stays blocking evidence — Q4 ③'s silent-drop finding
keeps its alarm.

**Blast radius, disclosed and verified:** the probe's corpus scan found six lane-token sites
the registry didn't know (`pavementMarking` 815, `desSpeedDecision` 584, `parkingLot` 573,
`dataCollectionPoint` 222, `ptStop` 189, `vehicleAttributeDecision` 66 — counts reproduced by
an independent parser in review), including `dataCollectionPoint@lane`, the very class Part A
watched get destroyed. Registering them makes `remove_link`/`remove_connector`/
`remove_last_lane` refuse MORE — fail-safe, regression suites green.

**The verification (12 agents, 6 dimensions, adversarial refutation) found one real code
defect — in the probe, not the verbs.** `_moved_links` computed deltas as before-minus-after
(`b`/`a` bound inverted), so every persisted centreline delta in `state.json` and two of three
A2 table rows were sign-flipped — and internally inconsistent with the Camden row, which came
from a raw-subtraction path. Systematic (parts a/b/d), caught by an adversary diffing the .inpx
pairs directly. Fixed after-minus-before; the docstring now flags the recorded `state.json`'s
inverted sign. Prose drifts fixed in the same pass: a y-value wrong at the 4th decimal
(+0.47050 vs measured +0.470445), three distinct FromPos values collapsed to one, a stale
`--part {a..g}` (real range a..j). The pattern across three units now: **the machine-written
artifact is always right; the prose describing it is where errors live.**

**Refusal messages must survive the dispatch cap.** `toolkit.dispatch` truncates exception text
at 300 chars; a 12-referrer `ReferenceIntegrityError` measured 613 and lost most referrer names
mid-word. The render now names the first three plus an honest "and N more" (full list always on
`.referrers`), pinned by test. All six of the unit's own refusals measure 184–273 chars —
under the cap whole. Known residuals, recorded not fixed: the pydantic `invalid_params` path
bypasses the cap entirely (pre-existing), and `ReferenceIntegrityError` carries no
envelope-artifact recovery. Also recorded: `add_link_lane_count_high` deliberately NOT extended
to `set_link_lane_count` — multi-tool `applies_to` would degrade the health suggested-action to
`('inspect', None)`, the exact Q5 ③ regression; and `link_geometry_live_check`'s log was
print-only (its refusal evidence survived the session only in a scratchpad) — now tees to
`runtime/link-geometry-live-check/live_check.log`.

Deliberately unwrapped: point-count changes, `RecalculateSpline` (redistributes points ~30 m;
`RecalculateSplineHeight` zeroed a just-written ZOffset), `SplitLink`, z-offset writes (the
shadow model cannot see z — `set_link_point_radius`'s parallel blind spot is pinned by a test
so it stays deliberate). Suite 2305→2371 (+64 unit, +2 referrer-cap pins), 2369 passed / 1
known RAG fail / 1 skipped. Isolation: constraints 527, inpx_structure 51, tools_link_geometry
28, vissim_com_link_geometry 22, health_check 13 (edit_time_only 90→93, derived not
hand-counted).

## 2026-08-04 — Q6 ②: parking lots — capacity is a length, and the guard that compared the wrong string

Second Q6 unit. Parking lots had ZERO precedent (no parser, no shadow class, no verbs), so the
unit is representation-first (the Q4 ④ doctrine): `_parse_parking_lots` (two species —
lane-anchored and abstract), shadow class + diff support, THEN three verbs + a reader on top of
a 10-part probe with a null control and a `--audit` re-derivation path.

**The semantics that make the verbs non-obvious.** `Capacity` on a REALPARKINGSPACE is
READ-ONLY and derived = `floor(Length/LenPerSpc)` (573/573 corpus lots, 0 violations) — the
only way to give a caller "N spaces" is to write `Length = N × LenPerSpc`, so
`set_parking_lot_capacity` is planner vocabulary realised as a LENGTH write, verified against
Vissim's own readback. Three edges, one loud two silent: length past the link end raises
("does not fit"), `LenPerSpc > Length` silently clamps, `Length=0` is silently accepted
(capacity 0, `load_check` green). Removal SILENTLY CASCADE-DELETES the referencing parking
route (Karlsruhe: 573 lots / 573 routes / 0 unreferenced — every real lot is one route away
from this trap); we refuse first, naming the referrer, before COM is touched. A `Type` write
is a structural mutation that MINTS a zone object and leaves it behind on flip-back —
deliberately unwrapped, with `AddAbstractParkingLot`, `DuplicateParkingLot`, and
zones/groups/parking-routing-decisions. Registry 107→110 (`parking_lot_capacity_unrealistic`
refuse, `parking_lot_does_not_fit_link` refuse — conservative via the `pos +
capacity×len_per_space` screen, whose only conservative term is `pos` since a partial last bay
contributes ZERO capacity — and `parking_lot_inert_without_parking_routing_decision` warn).

**The unit's own live check caught its first defect pre-review:** `written_value={"capacity":
25}` (a dict) can never equal the shadow model's int, so every correct call was refused —
guardrails failing safe against their own tool. Scalars now; 3 tests pin it.

**The verification (12 agents, adversarial) then refuted four things the first pass shipped:**
1. **An id-normalization bypass in the cascade guard.** The removal coerced `int(params.no)`
   but the guard compared the RAW string — `'0014'`, `' 14'`, `'+14'`, `'14 '` all bypassed
   the referrer refusal and reached the silent cascade-delete (preflight `object_not_found`
   catches them in the orchestrated pipeline; direct dispatch did not). Fixed by canonicalising
   once and using that everywhere; 5-spelling parametrized test, counterfactual run (restoring
   `params.no` fails 4 of 5).
2. **The capacity derivation is TYPE-SCOPED, not universal.** ZONECONNECTOR lots (56/56 in
   corpus) store capacity independently (10000 alongside `length=3`); abstract lots too. The
   verb was silently inapplicable to 9% of corpus lots while its docstring told the planner
   otherwise. Now refuses non-REALPARKINGSPACE in-tool, naming the species; the fake is
   type-aware so tests can catch what a derivation-hardcoding fake could not.
3. **The clamp-dodging write order broke from degenerate states.** From a pre-existing
   `{Length 0, LenPerSpc 1}` lot the shrink branch walked into the clamp it dodges. The branch
   now decides on TARGET length vs current, plus a defensive `capacity ≥ 1` floor at the COM
   layer (pydantic already enforces it above).
4. **`VissimObjectNotFoundError` was bounded in key COUNT, not LENGTH** — 8-digit ids × 20
   keys = 313 chars, over the 300 dispatch cap, truncating mid-number. Now budgeted to 180
   chars of keys + honest `(+N more)` — shared machinery, every verb benefits.

Probe-prose drifts fixed in the same pass (the pattern holds a fourth time — the artifact is
right, the prose drifts): a claimed self-correction that did not actually exist in the
docstring (`state.json.part_b.silently_ignored` carries 4 FALSE entries from a str-equality
`took` proxy — now recorded in the file as known-wrong), an audit-header independence claim
false for part F (its COM numerator is live-recorded), "30 attributes" → 21 probed COM ids,
"28 defaults" → 25 (+3 caller-supplied).

**Two systemic discoveries, recorded for follow-up rather than folded in:** (a) an AST sweep
found **11 echo readers** in `vissim_com.py` (`get_pt_stop`, `get_link`, `get_signal_head`,
`get_detector`, 7 more) — ten consumed by create verbs for computed max+1 numbers, the same
unfalsifiable-identity shape now fixed four times one reader at a time; spawned as a dedicated
task. (b) the raw-id-to-guard shape exists in THREE shipped verbs: `remove_pt_stop` and
`remove_data_collection_point` are exploitable twins of defect 1 (their guard is the first
XML consult), `remove_link` fails safe only because its raw pre-lookup errors first —
canonicalisation commit follows immediately after this one.

Constraint-message note: the en texts of two new constraints exceed 300 chars — NOT a defect;
`Finding.messages` never traverse `_sanitize_exception_text` (disjoint surfaces, measured), but
worth knowing before anyone "fixes" it. Envelope messages run to cap+prefix (~57-66 chars) —
recorded. Suite 2371→2479 (+86 unit, +22 fix cycle), 2477 passed / 1 known RAG / 1 skipped;
live check re-run end-to-end after the COM-layer fixes, all phases PASS, log teed to
`runtime/parking-lot-live-check/live_check.log`.

## 2026-08-04 — Q6 ③: pedestrian inputs — the guard that was permissive until a measurement said no

Final Q6 unit; **roadmap 1.1 closes**. Representation-first again (the diff was provably blind
— five probe parts recorded empty diffs before the parser existed): `_parse_pedestrian_inputs`
+ `_parse_pedestrian_areas`, then `add/set_volume/remove_pedestrian_input` + reader + 2 read
tools. Registry 110→113.

**The license question answered itself behaviourally.** No license-naming COM attribute exists
(8 candidates raise); the verdict is four measured levels on this box — a Viswalk network
loads, all 9 pedestrian containers enumerate, `RunSingleStep` runs, `AddPedestrianInput` AND
`AddArea` create. Scoped honestly: measured on an ACADEMIC license; a Viswalk-less box's
failure mode is unmeasured. Traffic-generation bar cleared: 36 pedestrians in a bounded 60 s
window at 2000 ped/h (expected 33.3) vs null control 0, vehicle count 208 in both arms as the
run-really-ran witness.

**The central landmine is a JOINT condition:** an input whose origin area carries no routing
decision stops the network INITIALISING — a bare unrouted area is harmless (six probe arms
isolate it; volume and geometry freshness are both ruled out). Guarded by a preflight refuse
keyed on exactly that pair. **And the guard's first version was measurably wrong in the
permissive direction:** it accepted PARTIAL routing decisions as routing, justified only by
corpus frequency. The review ordered the measurement; the Q/R controlled pair on Cableways
(the one corpus network with partial decisions: area 142 partial-only → `init_ok False`, area
102 static-routed → `True`) proved partial does NOT count. `_routed_area_nos` is static-only
now, the ko/en/ja texts name the static requirement, and the obsolete test is inverted. A
reviewer caught what no test could have — the inference LOOKED reasonable.

**`RemoveArea` is the new cascade king: it silently deletes 6 object classes with a GREEN
oracle** — `{areas −1, pedestrian_inputs −1, ped_volume_intervals −1, routing_decisions −1,
routes −4, route_locations −4}`, `load_ok`/`init_ok` both True. Worse than `RemovePTStop`
(which at least leaves a loadable network minus the obvious): here the blast radius exceeds
the direct referrers and nothing anywhere objects. **No area verb ships** — creation works but
a created area is inert (no routing-decision verb exists to make it live), and removal is the
above. Named by `pedestrian_area_edit_unsupported` (refuse) — whose first delivery was DEAD
CODE: its `applies_to` names only unregistered tools, and preflight only runs for registered
ones, so a planner got a generic "Unknown tool". Now delivered via
`constraints.explain_unsupported_tool()` consulted by the orchestrator's unrecognized-tool
branch (`error_type='unsupported_tool'`, rendered ko/en/ja + alternatives), tested through the
real `run_agent_turn` loop — a helper-level test would have passed against the broken build.

**Units, same-object split (measured, not inherited):** polygon WKT x/y are raw metres in both
directions even on a FEET network (ratio 1.0), while `CellSize` on the SAME area is
display-scaled (0.4921 ft = 0.15 m). The probe's own feet-counterfactual prose had the ratio
INVERTED (3.280840 where the true would-have-been figure is 0.3048) — fixed; conclusion
unaffected. Intervals belong to the network, third appearance (one input on 14-interval Air
Taxi persists all 14, demand in interval 1 only).

**Verification (12 agents) also found:** the postwrite `written_value` built from the RAW
caller string while readback is canonical — 5 of 6 legal spellings of a CORRECT call refused
at postwrite (the Q6 ② canonicalisation class, one layer deeper; the guard itself was clean);
a 5-param canonical-id test that survived the full raw-id revert because it asserted only
"refused", not "refused for the routing reason" — now discriminating; and the audit-coverage
header overclaiming (extended `--audit` now covers the 14-interval headline and Q/R; two
honest exceptions remain). Prose drifted in five places, the machine artifacts in zero — five
units, five for five.

Suite 2494→2602 (+79 unit, +29 fix cycle), 2600 passed / 1 known RAG / 1 skipped. Isolation:
pedestrian 80, vissim_com_pedestrian 21, orchestrator 20 (the 9 new delivery tests), the rest
unchanged. Live check re-run end-to-end after every fix: all phases PASS, counterfactuals
still block with named rows, log teed. `edit_time_only_n` 99, derived. **Q6 complete: the v2b
catalog's last three bullets are shipped, and 1.1 — "the longest wall" — is done.**


---

## 2026-08-05 — Daejeon adapter (1.2): site id falls back to the file name (contract MUST-NOT deviation)

**Context:** Phase 1 unit 1.2, commit 33eed3e (`backend/services/dataimport/adapters/daejeon.py`).
The adapter contract forbids "substituting a site id from a file name" — a clause written for
SCATS drops, which carry an internal TCS number a file name would *override*. Verified reality
(real drop, read-only, independently re-verified by an adversarial reviewer): none of the 11
Daejeon workbooks states any internal site identifier anywhere in its sheets; the file name is
the only identity the drop carries. Strict refusal would make the entire drop unimportable.

**Decision:** fall back to the file-name token, provenance the id to the file name, and emit
`WARN daejeon_site_id_from_file_name` on every such import; a `site_ids={file: id}` override
maps files to authoritative ids and silences the warning; `site_id_from_file_name=False`
restores the contract's strict refusal (`daejeon_site_identity_unavailable`). All three paths
are tested. The deviation is by necessity (no in-file identifier exists), not convenience.
The contract's MUST-NOT clause now carries the Daejeon counterexample so adapter #3 authors
know it is SCATS-conditional.

**Consequence:** Daejeon imports are identified by file name until the client supplies an
authoritative site-id mapping; every report carries the WARN as a visible caveat. Shipped
alongside: the data-intake inventory's 소계 description was measured wrong (소계 is a *time*
aggregate, 합계 a *class* aggregate; neither is the approach-total REFUSE case) — the new
class-axis gate check `class_total_consistency` covers what the inventory believed was already
covered, and adapter-contract §4.2's table now lists it. Known real gap, tracked separately:
full-drop import peaks at ~6.3 GB (measured; the build report's ~810 MB was an estimate) —
import-path windowing is required before routine five-week imports.

---

## 2026-08-05 — SCATS→fixed-time (1.6): R1.3b subset shipped; LinSig items and yellow-trap check out, by measurement

**Context:** Phase 1 unit 1.6, commit d42e401 (`backend/services/dataimport/fixed_time.py`).
R1.3b names degree-of-saturation / bonus-green (LinSig Tables 3-3/3-4) alongside the
Appendix-A conversion; the machine-readable extract on this machine
(`docs/product/reference/mrwa-appendix-a-extract.md`) covers Appendix A only — the LinSig
chapter was never received. The §A.4 yellow-trap rule is not computable from the neutral
model: `schema.TurnType` is geometric and carries no opposed/permissive flag.

**Decision:** ship the verifiable subset (A.2 weighted-average conversion, A.3 clearance
formulas, A.5 offset interpolation) with 14 extract-cited `CIT_*` constants whose quoted
fragments are mechanically compared (normalised) to the extract file — a mis-transcribed
formula fails the suite, proven by counterfactual mutation. The unimplemented remainder is
loud, not silent: every successful conversion emits `fixed_time_yellow_trap_not_checkable`,
and saturation/bonus-green make no pretence of existing. Ten load-bearing interpretation
choices are ASSUMPTION-marked at their bite sites; A1 (integer round-up beats Σt̄ᵢ = c̄) is
disclosed as a residual on every run and is why the derived plan passes the quality gate's
cycle-consistency check exactly.

**Consequence:** 3.1 (signal genesis) can consume `FixedTimePlan` now. The LinSig items
re-enter scope only if TK supplies that chapter. Whoever adds an opposed-turn flag to the
schema should wire the yellow-trap check and delete the not-checkable emission.

---

## 2026-08-05 — Audit-trail export (1.3): the ledger has no pre-write values, so 변경 전 is chained per-session

**Context:** Phase 1 unit 1.3, commit c40ee0d (`backend/services/audit_trail.py`). The
roadmap's "파라미터 변경 기록+근거" section implies before→after rows, but the orchestrator's
trace ledger records no pre-write values (`action_end` carries only `{status, error_type}`;
guardrail records carry constraint ids, not readbacks).

**Decision:** 변경 전 is derived by chaining from the previous *applied* value of the same
(tool, target, attribute) within the session; a session's first write of an attribute renders
`기록 없음`, and a DOCX footnote states the rule. The exporter stays ledger-intrinsic — it
imports nothing from `services/agent/` and classifies write/run/read from the ledger's own
record shapes. The one non-derivable fact — the run tier's tool names, needed to keep a
failed run-tier dispatch out of the change table — is a deliberate frozen snapshot
(`_RUN_TIER_TOOLS = {run_simulation, run_demand_stress_sweep, load_check}` plus the `run_`
prefix), not a registry import; `load_check` is the one unprefixed member, counterfactually
proven necessary. Folding is `plan`-record-delimited and therefore immune to per-turn
iteration-number collisions (present in 1,018 of 2,190 real ledgers; fixture-tested). DOCX
output is content-deterministic for a pinned `generated_at`; byte determinism is explicitly
not claimed (python-docx zip timestamps).

**Consequence:** before-values are best-effort until whoever owns orchestrator trace payloads
puts the write envelope's verification (written/readback values) into `action_end` — at that
point the chaining becomes a fallback, not the source. A new run-tier tool without a `run_`
prefix must be added to `_RUN_TIER_TOOLS`; the drift risk is documented at the constant.


---

## 2026-08-05 — Project workspaces (1.4): physical isolation for uploads, logical isolation for runs and sessions

**Context:** roadmap P② (`docs/ROADMAP.md`) defines project workspaces as an ACCESS-CONTROL
boundary, not organisation: "the moment two users share a server, the global active dataset is
a client-confidentiality leak." The measured singleton was not an object but a set of
module-level `Path` constants under `runtime/uploads/current/` with `.exists()` as the
active-flag, read through six accessors at ~50 call sites in 24 modules.

**Decision:** `runtime/workspaces/<id>/uploads/…` containers with a registry; the six accessors
derive per call from the request's workspace. Request scoping is an `X-Workspace-Id` header →
`ContextVar`, set by a raw-ASGI middleware registered innermost (inside CORS, so a workspace
404 still carries CORS headers) — `BaseHTTPMiddleware` was rejected because it dispatches in a
separate anyio task, where a ContextVar set in `dispatch` is not reliably visible to the
endpoint, least of all a sync endpoint hopped to the threadpool. Ids are `uuid4().hex[:12]`
validated by `^[0-9a-f]{8,32}$` **plus** `resolve()` + `relative_to`, the reviewed pattern from
`routers/network.py`. Absent header resolves to an auto-created default workspace, so every
pre-existing client keeps working; a lazy migration moves the legacy layout under it on first
touch.

**Scope split (deliberate):** uploads are isolated PHYSICALLY (they are the confidentiality
surface); `runtime/simulations/` and `runtime/agent_sessions/` stay physically global this unit
and are isolated LOGICALLY — every record carries `workspace_id`, every session-scoped endpoint
checks it, and records without one resolve to the default workspace as the documented pre-1.4
upgrade path. Moving those trees belongs with P③ (SQLite), where the registry that indexes them
lands.

**Consequence:** two known holes, both disclosed rather than papered over. (1) The
`/api/simulations/ws/{job_id}` websocket cannot be workspace-filtered — a browser `WebSocket`
cannot send custom headers — and exposes progress percentages for a job id the caller already
holds. (2) `healthCheckDownloadUrl()` is a plain `<a href>`, so that one JSON download always
resolves to the default workspace (the on-page report is correctly scoped). Both close when
scoping moves into the path/query in P③/P④. Freezing a job's source *path* also does not
protect against an in-place replacement at the same path; `dataset_revision` on the record is
what detects that, and a per-job source snapshot belongs with P③.

---

## 2026-08-05 — Session ownership fails CLOSED, and pre-1.4 sessions are backfilled explicitly

**Context:** unit 1.4's first implementation resolved a session's owning workspace from a
`session_meta.json` sidecar and substituted the default workspace whenever the sidecar was
missing or unparseable. Adversarial verification measured the consequence: a session created by
workspace B with no readable sidecar is owned by the DEFAULT workspace — a caller with no
header had B's working network parsed and served
(`GET /api/network?session_id=` → `view=working`) while B itself was locked out, and the
response was a positive existence oracle no ghost session could produce. Reachable without disk
damage: the sidecar write was wrapped in `try/except OSError` that only logged.

**Decision:** fail closed. Pre-1.4 sessions are backfilled with an explicit default-workspace
sidecar (making the documented upgrade path a written fact instead of an implicit fallback);
after that, a missing or corrupt sidecar denies EVERYONE, including the default workspace; and
a sidecar that cannot be written aborts session creation — an unowned session must never exist.
The rule is that an access-control predicate may never treat "I don't know who owns this" as
"the caller owns this".

**Consequence:** a session whose sidecar is damaged becomes unreachable rather than
world-readable, which is the correct direction for a confidentiality boundary; recovering one
is a manual operation. The same predicate backs agent downloads, the audit endpoints, approve,
and the query stream, so all five inherit the fix.

---

## 2026-08-05 — Upload becomes transactional; five hardening fixes ride with 1.4 deliberately

**Context:** the pre-existing upload path did `rmtree(uploads/current)` then wrote, so any
failure after the rmtree left the user with NO dataset. Verification of 1.4's replacement found
the first cut still leaked: a failure of the FIRST rename left an orphaned staging directory
(reproduced with no monkeypatching three ways on Windows — an ordinary open handle on
`current.inpx`, a Vissim-style no-delete-share handle, and a process CWD inside the folder; the
app itself opens that file), nothing ever swept the orphans, storage failures surfaced as 500s,
a non-`OSError` from the second rename skipped the restore and lost the dataset outright, and
concurrent uploads into one workspace raced.

**Decision:** stage-then-promote with the promote INSIDE the cleanup guard, a restore path that
catches `BaseException`, bounded sweeping of `incoming-*`/`retired-*` leftovers (never sweeping
`retired-*` while `current` is absent — that is the only-copy signature after a doubly-failed
promote), a per-workspace upload lock spanning the whole call including the read-back (the
revision walk's directory enumeration is itself what makes a concurrent rename fail on
Windows), and storage failures mapped to 409 with a message naming the likely cause.

**Decision (scope):** four further fixes that are NOT workspace scoping shipped in the same
commit, because they rewrite the same functions and because workspaces multiply each one: the
zip-bomb guard (declared member/total caps + a streaming per-member cap), the 250 MB body cap
and 413, `clear_validation_caches()` on all four dataset mutators (an `lru_cache` keyed on a
path string that was never invalidated — dataset B served dataset A's counts, a live bug at
HEAD), and `simulation_network_builder`'s `Path(__file__)` root replaced by the
`SIMULATION_ROOT` seam. They are enumerated in the commit body rather than folded silently into
"workspaces".

**Consequence:** `get_active_dataset()` gains a `warnings` key ONLY when the legacy migration is
degraded (payload byte-identical otherwise), so a partially-failed migration is visible instead
of silently showing the demo dataset; a degraded migration now genuinely retries on the next
call rather than the next process boot. Known cosmetic residue: `_zip_too_large_message()`
quotes the module constant rather than the active budget limit, visible only in tests that
inject an explicit budget.


---

## 2026-08-05 — GEH unification (1.5): one implementation per runtime, and a fixture that can actually catch drift

**Context:** roadmap row 1.5 said "3 implementations → 1 backend util". Reality measured four
live copies — `services/validation.py`, `services/report_exports.py`, and a private one in each
of `app/dashboard/page.tsx` and `app/base-model/page.tsx` — plus two git-tracked `.bak` files
holding a fifth and sixth. Only one behavioural difference existed across them, and it was the
dangerous kind: `report_exports._geh` returned a **passing 0.0** where the statistic is
undefined, so every 0-observed/0-modelled movement scored as a perfect match on a
customer-facing report.

**Decision:** one implementation per RUNTIME, not one network call per row —
`backend/services/validation.py` and `frontend/lib/geh.ts` — with `shared/geh-conformance.json`
as a single table of cases both are tested against. The backend duplicates become import
aliases onto the shared functions with identity assertions pinning them, which is stronger than
deletion: a future private copy fails a test rather than passing quietly. Semantics: `None` when
either input is missing, when `model + observed <= 0`, or when any input or the result is
non-finite; no rounding at the source; `pass_rate` excludes undefined values from BOTH numerator
and denominator and reports the excluded count.

**Consequence:** verification proved the first fixture could not do its job. The two runtimes
already disagreed on non-finite inputs (Python returned `nan` and graded it `poor`; TypeScript
returned `null`), and JSON cannot encode NaN, so the fixture was structurally blind — stripping
the guards from either side still passed. String sentinels (`"NaN"`, `"Infinity"`,
`"-Infinity"`) that both readers decode, plus a reader that raises on an unknown spelling, close
it; the guard-stripping mutation now fails on both sides. A second divergence surfaced on the
way: Python raised `OverflowError` where JavaScript returned `Infinity`. **Rule this establishes:
a cross-language conformance fixture must be checked against the encoding limits of its own
format, and proven by mutating each runtime — a fixture nobody has tried to defeat is decoration.**

---

## 2026-08-05 — Undefined GEH leaves the denominator, and the published percentages move

**Context:** unit 1.5. Under the old `report_exports._geh`, a 0-observed/0-modelled row scored
0.0, passed the `< 5` test, and inflated both the numerator and the denominator of every GEH
percentage in the Flow/GEH `.docx`, its CSV twin, and the executive-summary sentence. The
pre-existing tests could not catch the change: they pin "CSV equals the source function", so
both sides move together — and `data/real_counts.xlsx` does not exist in this checkout, so they
were comparing empty data.

**Decision:** undefined rows leave both numerator and denominator, and the shrunken denominator
is DISCLOSED rather than hidden — a conditional note in the `.docx`, two new CSV columns
(`GEH Counts`, `GEH Excluded`), and a summary sentence that names its own denominator.
Characterization tests were written and run green against the OLD behaviour before the change,
so every moved number is deliberate and reviewable. On the fixture: AM total GEH 80% → 67%,
executive summary 75% → 67%. A node whose counts are ALL undefined prints `-` — not the old
100% (wrongly perfect), and not the 0% the first cut produced (wrongly a total failure) —
matching how the Count Validation table and the calibration XLSX already rendered that state.

**Consequence:** real client reports will show LOWER GEH pass rates than before, always in the
direction of correcting an inflation. `No. Counts` and `Flow %` deliberately do NOT change: a
0/0 row still passes the flow test, and moving a second published number was not this unit's
job. The CSV gains its two columns on EVERY export, clean dataset or not, so a downstream
consumer pinned to column order breaks — accepted and pinned by a test, since a silently
variable column set would be worse. `exporters.py` was aligned in the same pass because it had
the mirror-image bug (counting undefined rows as failures, deflating instead of inflating).

---

## 2026-08-05 — Per-approach delay is measured, and "not measured" never renders as zero

**Context:** unit 1.5 / UX backlog #7. Per-direction delay was the node-average multiplied by an
invented `0.75 + (dirVol/maxVol) * 0.5` volume factor. The backlog knew about the dashboard
copy; a backend mirror in `results_reader._estimate_direction_delays` was never listed. Vissim
reports `VEHDELAY` per movement, and `_build_node_directions` already classified every movement
to a compass approach — it kept the volumes and discarded the delay, so the real number had been
available all along.

**Decision:** a VEHS-weighted mean of measured per-movement `VEHDELAY` per approach, and where
nothing was measured, emit nothing — `{}` from the backend, 데이터 없음 in the chart, `-` in the
report. The estimate and its disclaimers are deleted, including the exported report's claim that
Vissim cannot produce per-approach LOS, which the unit had made false.

**Consequence:** the guard that makes "no data" reachable is null-before-coercion. The first cut
shipped `toFiniteNumber(measured[direction])`, and since `toFiniteNumber` is `Number()` +
`isFinite` while `Number(null)` is a finite `0`, a node with no measurement rendered four bars
reading 0초/대 at LOS A — a fabricated measurement in the very unit built to delete one, with the
no-data panel unreachable. Caught by adversarial replay of the shipped functions, not by review.
**Rule: deleting an invented number is not enough; the absence path has to be exercised, because
a coercion helper will happily invent a new one.** A source pin now fails if the coercion moves
back ahead of the guard. The same defect remains on the network KPI cards (`_safe_mean` emits
null, the card should be suppressed, coercion shows 0.0) — filed separately, not fixed here.


---

## 2026-08-06 — LinSig re-entry (1.6 addendum): the module boundary is the regime label

**Context:** `d42e401` deferred degree-of-saturation and bonus-green because the LinSig chapter
was absent, with the recorded condition "re-enter scope only if TK supplies that chapter". The
user supplied the full OMG v2.0 PDF on 2026-08-06; the page-cited extracts landed at `58956c4`;
the re-entry landed at `f63b830`.

**Decision:** a SIBLING module (`dataimport/linsig.py`), not an extension of `fixed_time.py`,
for three measured reasons: the citation machinery keys one extract file per module; §3.5.15's
inputs are LinSig model objects the neutral schema deliberately does not carry; and — from the
§5 extract's own warning — LinSig validates on DoS + queue while Vissim validates on GEH, so
the module boundary is what keeps a LinSig number from leaking into a Vissim-regime
comparison. Table 3-3's arithmetic is A.2's own quantities renamed, so the three shared
functions live once in `fixed_time.py` and `linsig.py` delegates (proven by asserting the
LinSig entry points raise the Appendix-A functions' error messages); the one genuine
difference — the averaging period, and the ROUNDING regimes (101 s Vissim plan vs 100 s LinSig
modelled cycle from the same history) — is kept as tests, not unified. A filtered right turn
without its opposed/unopposed split REFUSES (`linsig_filtered_turn_not_split`); the doc states
no numeric queue tolerance for §3.10's queue half, so it is left uncoded rather than invented.

**Consequence:** every LinSig output is regime-labelled; the DoS ≤ 100 % rule is a constant,
not a setting; assumptions A11–A19 continue 1.6's series (A11: the doc rounds three ways in
three pages, so nothing is re-rounded internally). Related same-day upgrade `25e73ca`: the
validation pack now carries the whole of §5.6.3 with one cross-language source of truth
(`shared/mrwa-validation-criteria.json`), the critical-intersection override that flips a
network-passing report to FAIL with the node named, and explicit not-assessable states for
criteria whose observed side has no ingest path — the honest-gap pattern this project treats
as the product.


---

## 2026-08-06 — Genesis live check: three offline assumptions measured false before anyone shipped them

**Context:** the 2.3 OSM generator (`b07b42a`) was verified offline by round-trip through this
repo's parser and differ, with the live-Vissim open test named as the follow-up. The user
authorized live COM on this box; the probe (`backend/tools/genesis_open_live_check.py`, Vissim
Academic 2026 SP09, `attach_to_running=False`, temp clones, LoadNet→err→SaveNetAs only, six
sessions, zero orphans) settled the open questions — every one against the offline assumption.

**Measured:**
1. **Generated `.inpx` files do not load.** Both the synthetic crossroads and the 2,069-link
   Murdoch draft refuse with "The network file could not be loaded" — and NO `.err` is written
   (LoadNet failures produce none). Bisected both directions: a REAL network stripped to the
   emitter's attribute set becomes unloadable with the identical error; the emitter's own
   geometry grafted into a real skeleton LOADS (Vissim even recomputed conflict areas over it,
   40→44). Root cause: `data/practice.inpx`, the emitter's sole schema exemplar, is a 6 KB
   hand-written synthetic file, not Vissim output — its `version="2400"` is not a Vissim schema
   version. Emitter v2 fix list: real root (`version="1403"` + `vissimVersion`), the ~35
   mandatory `<link>` attributes + `<lane markingType>`, the real connector endpoint form, the
   required sibling containers, and `trafReg="LEFTHANDTRAFFIC"` — without which a generated
   network opens as RIGHT-hand traffic regardless of the left-hand geometry.
2. **`linkSegment`/`linkSegs` does not exist in Vissim's output.** No Vissim-saved network —
   including PTV's own examples, all scanned — contains it. `inpx_parser._node_link_ids()`
   reads an element Vissim never writes, so node membership parses empty for EVERY network,
   real ones included; the genesis differ's "0 movements per node" was a parser assumption, not
   an emitter gap. Node membership is geometric (polygon containment) and must be computed.
3. **Connector lane index 1 is the RIGHT-most lane** — under `LEFTHANDTRAFFIC` that is the
   MEDIAN side, not the kerb (measured 7–0 on the London roundabout, 11–0 on Buehl; lane 1 at
   −1.75 m on the cleanest case). The `"link lane"` token lives in `<objectRef key>` under each
   connector lane's `<fromLanes>/<toLanes>`, one `<lane>` per connector lane; the endpoint
   elements take `link=`, not `lane=`.

**Also measured:** a real file stripped to netPara+links returns `load_ok=True` with
`Net.Links.Count == 0` — Vissim reports SUCCESS for an empty load, and `tools_check._load_check`
would pass such a file on both gates (new entry for the silent-failure taxonomy). The
save-materialises-a-vehicle-composition landmine did NOT reproduce on 2026 SP09 via SaveNetAs.

**Consequence:** 2.3's generator v1 stands as offline-verified topology with an honest label;
its emission is NOT Vissim-loadable until emitter v2 lands, and no artifact claims otherwise.
The Murdoch benchmark diff v1 ran end-to-end on real OSM and carries its own named limit:
id-based correspondence is invalid across independently-authored networks (the consultant's
ids collide with the generator's sequential ids, reading different roads as the same object) —
benchmark v2 needs topological matching. The projection guard fired correctly on the first
real input (refused the padded bbox at 2,356 m half-extent vs the 2,254 m Perth validity
radius).


---

## 2026-08-08 — The genesis chain loads in Vissim end to end, and only the live check could prove it

**Context:** roadmap 2.3/3.1/3.2 plus the §5.2.3 fleet layer built the four generators — geometry
(`osm`/`draft`/`emit`, `b07b42a`+`36d0f64`), signals (`signals.py`, `4f38e39`, live-loaded
`0efaf5d`), demand (`demand.py`, `2971385`), fleet (`fleet.py`, `bb6af41`). Each was
offline-verified by round-trip through this repo's own parser and differ. The fleet layer's
offline tests were all green — and it emitted a file **no Vissim can open** (`445477d` fixes it).

**What the live full-stack check found (`genesis_fullstack_live_check.py`, PTV Vissim 2026 SP09):**
LoadNet refused with NO `.err` — the silent-failure class the whole product exists to catch — and
a layer-then-container bisection located two independent fleet blockers the offline round-trip
was structurally blind to:
1. Vissim rejects the entire network on any `desAccelerationFunction` point where `yMin > yMax`.
   Table 5-2's heavy rows are inverted at 96 km/h (type 3: yMin 0.098 > y 0.043 > yMax 0.031).
2. A `<vehicleType>` reduced by the four "optional" attributes (`occupDistr`/`powerDistr`/
   `weightDistr`/`desCurveSpeedFunc`) cannot instantiate once a `model2D3D` is attached — all
   four are required together (partial closures still refuse).

**Decision / lesson:** the offline parser round-trip proves a file is SELF-CONSISTENT, never that
Vissim will LOAD it — the two are different guarantees, and the gap is exactly where this project's
"Vissim fails silently, the validation layer is the product" thesis lives. A generator layer is not
done at green offline tests; a live LoadNet is its acceptance. This is emitter-v2's own geometry
lesson (`a real network reduced to no/name is refused`) recurring twice on the vehicleType and the
accel envelope. The reduced-element trap now has three instances (link, vehicleType-reduction,
vehicleType-accel) — treat "we shrank the emission to what the parser round-trips" as a live-test
trigger, not a shrink that ships.

**The two fixes (`445477d`):** the accel envelope is min/max-**relabelled** at emit over the same
three MRWA values (`y` unchanged, always inside), so the guideline's numbers are preserved and
Vissim's `yMin ≤ yMax` contract holds — source table + inversion WARN stay verbatim. The four
vehicleType references and their minimal transitive containers are emitted, every constant
script-extracted from `ref_lht`'s Vissim save (emitter-v2 law). Live re-proof: the real emitter's
full geometry+signals+demand+fleet file LoadNets clean (1.98 s, no `.err`), the three `.u3dm`/`.fbx`
model refs resolve verbatim, signal windows and demand volumes read back as authored, and SaveNetAs
round-trips every container.

**Client-facing finding to raise with TK:** MRWA OMG v2.0 **Table 5-2 (doc p.184) is malformed at
its high-speed tail** — for every heavy vehicle type the `yMin`/`yMax` acceleration-envelope columns
are inverted (yMin > yMax) at 96 km/h (and the pattern suggests the two columns were transcribed
swapped there). Prism preserves MRWA's values and reorders only the labels so the network loads,
and records the anomaly (`genesis_fleet_table_5_2_envelope_inverted`); MRWA may wish to correct the
source table.

---

## 2026-08-10 — the 대전 signal PDFs were never "unparseable"; the gap is one missing column

**Context:** roadmap 3.1's Korean half. Two modules recorded, in writing, that this work was
blocked: `services/genesis/signals.py` opened with "The Daejeon half — parsing Daejeon signal
PDFs — is blocked on client data and is not here", and `services/dataimport/adapters/daejeon.py`
said "The Daejeon signal side arrived as 23 PDF drawings, not a machine-readable export". Both
claims were repeated forward without being measured.

**What measuring found:** all 23 PDFs in `client-data/218. 대전신호.zip` are single-page,
text-layer PDFs of the Korean police-standard 교차로 운영 DATABASE form. `pypdf`'s
`extraction_mode="layout"` recovers the entire form as an aligned character grid — header,
SIGNAL TABLE (7 rows × 6 phase columns × 2 rings), PLAN TABLE (수준→주기 and ID→연동+시간분할),
TOD PLAN 1/2/3, the weekly plan and the MEMO. No OCR anywhere. The only drawing is the small
현시도 sketch in each sheet's corner. `services/genesis/signals_daejeon.py` parses 22 of 23 into
neutral `SignalControllerData` (120 declared programs); the 23rd is refused with a named,
actionable reason.

**Decision / lesson:** *"blocked on client data" is a measurement, not a status.* A blocker
asserted once and never re-tested cost this project a whole roadmap half. Any future claim that
an input is unusable must cite the probe that established it and the date; a claim with neither
is a hypothesis. (The user called this out; the codebase had been quoting itself.)

**The gap that IS real, and where it lives.** Two facts a Vissim fixed-time program needs are
genuinely absent from the documents, and both are refusals rather than assumptions:

1. **The approach→phase mapping is a drawing.** `signals.generate_node_signals` already requires
   it as caller input (`approach_phases`) and refuses without it. Unchanged; the parser reports
   the absence (`daejeon_phase_movement_diagram_not_text`) so it is visible early.
2. **The yellow/all-red split is unstated.** The form has ONE clearance row, labelled
   `YELLOW(RED)`, and no 전적색 row. Either reading refuses downstream — yellow-only leaves the
   all-red unknown (`signals_all_red_not_positive`), whole-clearance leaves the amber unknown
   (`signals_amber_unknown`, and Vissim needs the amber as its own display). MRWA §5.4.2.1
   requires both to be input from controller information unchanged, so no default is substituted.
   `to_fixed_time_plan` refuses by default with `daejeon_intergreen_split_unstated` and produces
   a plan only under an explicit reviewer declaration (`DaejeonSettings.declared_amber_s`), which
   travels into `Provenance.derived_by`. Measured: 21 of the 22 parsed sheets are blocked by
   nothing else.

**Client-facing question to raise (one line, answers 22 intersections at once):** *does the
`YELLOW(RED)` column of the 교차로 운영 DATABASE sheet hold 황색 only, or 황색+전적색?* The
evidence leans to the second — `MIN. GREEN` equals `시간분할 − YELLOW(RED)` for 59 of 94
(sheet, phase) pairs, and `MIN. GREEN` is a green, so the subtracted quantity is the whole
clearance — but leaning is not stating, and the module does not act on it.

**Two shape deviations recorded rather than absorbed:**
* `PhaseDerivation.observed_total_s` is non-optional and means an observation. A DECLARED plan
  has none, so the bridge writes `0.0` — the same placeholder `fixed_time.py` itself writes
  (`phase.observed_total_duration_s or 0.0`) — with `occurrences`/`scats_average_s` left `None`
  and `source_kind=DECLARED` on the program carrying the real signal. If a future consumer reads
  `observed_total_s` as evidence, that field needs to become `float | None`.
* **Location.** Regional format adapters live in `services/dataimport/adapters/`; this parser was
  placed in `services/genesis/` as the unit specified, because its binding constraint is that its
  output feed `genesis.signals`. It produces neutral records and takes text, so moving it under
  `dataimport/adapters/` later is a file move plus a re-export, not a rewrite.

**New runtime dependency:** `pypdf==6.14.2` added to `backend/requirements.txt`. It was already
present in the venv but undeclared. It is imported **lazily, inside `read_sheet_text()` only** —
`parse_sheet_text()` takes text, so the tests and any consumer with extracted text need no PDF
stack.

**Verification:** offline only. `backend/tests/test_genesis_signals_daejeon.py` (35 tests, all
against synthetic fixtures under `tests/fixtures/daejeon_signal/` — no test reads `client-data/`)
covers a clean parse, both firmware layout variants, four named refusals, the amber gate in both
directions, and the bridged plan running through `generate_node_signals` to green windows. Per
this file's own 2026-08-08 lesson, **an offline round trip proves self-consistency, not that
Vissim loads it** — no live LoadNet was run for this unit, and none of these numbers has been
in front of PTV Vissim.

---

## 2026-08-10 — P④: the workspace boundary and the login finally meet, and the gate is fail-closed

**Context:** the user found this by using the running app, not by reading code. Signed in, they
asked "is the project list, belongs to the account right?" It was not. `services/workspaces.py`
had isolated 프로젝트 into separate directory trees since 1.4, and `services/auth.py` had known
who a person was since U7 — and **nothing joined the two**. Any caller, signed in or not, could
put another project's id in `X-Workspace-Id` and read that client's network. The 프로젝트 picker
listed every project on the box to everyone. There was also no browser path to user
administration: `tools/manage_users.py` in a terminal was the only way to create an account, which
is not a thing a traffic consultant will do.

**The rule, enforced in ONE place** (`main.WorkspaceScopeMiddleware`, after id resolution, before
routing — eighteen routers each remembering a `Depends` is a habit, not a boundary):

| caller | default workspace | non-default workspace |
|---|---|---|
| unauthenticated | allowed | **403** `workspace_forbidden` |
| authenticated member | allowed | allowed |
| authenticated non-member | allowed | **403** |
| `admin` | allowed | allowed |
| any caller, unregistered id | — | 404 `workspace_not_found` (checked first, unchanged) |

**Why the default workspace stays open to anonymous callers.** The rest of the app is not behind
login — only `/api/auth/whoami` and `/api/assistant/agent/approve` are gated. A naive "membership
on every request" would have broken the entire unauthenticated app and its suite. The default
workspace is the demo/dev dataset; every *client* project is a non-default one and is fail-closed.
The refusal is a 403, never a silent fallback to the default — silently serving one client's
network to another client is the precise failure the boundary exists to prevent. Gating the whole
app behind login remains a separate, deliberate decision, not something this unit smuggled in.

**Deviations recorded rather than absorbed:**
* **`POST /api/workspaces` now requires authentication (401 for anon).** Not asked for. Without a
  signed-in creator there is nobody to grant membership to, so an anonymous create would return an
  id whose very next request is 403 — a trap, not a refusal. The creator is granted membership on
  create. This is the source of the unit's test churn (19 call sites).
* **Workspaces that predate this unit have zero membership rows and are therefore admin-only until
  granted.** Survivable *only* because admins see and grant everything and the first admin already
  exists. Pinned by a test named for exactly that.
* **Membership role set is exactly `('member')`**, CHECK-constrained. A second role costs a v3
  migration plus the branch that reads it; a role nobody checks is a lie in a schema.
* **Self-disable / self-demote refusal lives in `routers/admin.py`**, not the service: it needs the
  caller's identity and the service layer deliberately has none. The *last enabled admin* invariant
  does live in `services/auth.py`, so `manage_users.py` inherits it.

**Four defects the adversarial verification pass found in this unit's own work, fixed before it
landed** (the reviewers ran against the uncommitted diff; each finding was then attacked by a
refuter, and 4 of 11 were refuted and dropped):
* **Websockets were exempt from the gate**, on the stated rationale that a websocket "could never
  carry" `X-Workspace-Id` because the browser `WebSocket` API cannot set one. True of browsers,
  false of wscat/python-websockets/curl — a browser limitation mistaken for a security property,
  while the middleware read and honoured the header for websocket scopes anyway. The gate now runs
  for websocket scopes; since no 403 body is possible pre-handshake, the refusal is of the
  **binding** (fall back to the default workspace), never of a binding the caller did not earn.
  Fixing it exposed a second bug in the fix: `_caller_user_id` built a Starlette `Request`, which
  asserts `scope["type"] == "http"`, so every websocket resolved as anonymous through a blanket
  `except` — fail-closed, so not a hole, but a genuine member's websocket could never be
  recognised. Now `HTTPConnection`, the common base carrying `.cookies`/`.headers` for both scopes.
  Independently, `/api/simulations/ws/{job_id}` still resolves job ids through the GLOBAL registry
  (recorded at `routers/simulations.py:142-147` since 1.4); this unit does not fix that and does
  not pretend to.
* **The frontend's direct-base fallback leg sent no session cookie.** `requestInitForBase` omitted
  `credentials`, which fetch defaults to `same-origin` — fine cross-origin before P④ (it merely
  lost personalisation), fatal after it: a signed-in member would watch their own 프로젝트 403 on
  every read the moment the Next proxy hiccupped. Now `credentials: "include"`, for the same reason
  `noWorkspaceRequestInit` already stated it.
* **The admin page cleared the operator's typed password on a REFUSED mutation** — `run` swallowed
  failures and returned void, so callers ran post-success cleanup unconditionally. `run` now
  returns whether the mutation succeeded, and the forms keep their contents on refusal.
* **A project-switch race** could paint project A's member roster under project B's name. On a
  screen whose entire job is stating who can see which client's data, a confidently wrong roster is
  the one unacceptable outcome; responses are now dropped unless they match the latest request.

**A test-honesty correction, not a code change:** `test_an_admin_cannot_lock_the_box_or_themselves_out`
carried a comment claiming its two 409s proved `services/auth.py`'s `LastAdminError`. They do not —
the caller is targeting themselves, so the router's self-targeting guard answers first, and with a
single admin "the last admin" and "yourself" are the same account. The service invariant is proved
where it can be isolated, by direct call in `test_auth_core.py`. The router's `LastAdminError`→409
mapping is defence in depth, and the comment now says so.

**Known and NOT fixed here** (pre-existing, chipped separately): `services/db.py`'s warm
`ensure_schema` memo returns the constant `SCHEMA_VERSION` without re-reading the on-disk version,
so the "database is newer than this build" refusal protects only the first open of a path.

**Verification:** full backend suite **3905 passed, 1 skipped**; `npm run typecheck` clean;
`npm run test:dashboard-contract` 37/37. The new `tests/test_workspace_membership.py` (25 tests)
fails without the change — with the router-level `require_role("admin")` deleted and the middleware
gate short-circuited, 7 of its tests fail. Browser-verified only that `/admin` compiles and routes
an anonymous visitor to login; the signed-in admin view was not machine-verified, because doing so
would have meant inventing credentials.

**Still open, deliberately:** the app as a whole is still not behind login (two endpoints are).
That is the next decision to make, not an oversight of this one.

---

## 2026-08-10 — Real WA SCATS `.xlsx` workbooks exist (the "no real Murdoch data" claim was wrong), and the detector→movement mapping genuinely does not

**Context:** `client-data/Traffic signal data.zip` was checked for the first time against the
SCATS/WA adapter (`backend/services/dataimport/adapters/scats_wa.py`), which until now was a
documented **stub**: it read a hand-authored CSV subset in the shapes MRWA Appendix-A describes,
and its own docstring said reading the real `.xlsx` tabs was deferred to "roadmap Phase 4". The
zip holds 3 real Main Roads WA SCATS detector-volume workbooks
(`LM00398`/`LM00972`/`LM00973`.2026-02.xlsx — South St and Murdoch Dr, all three inside the
Murdoch network bbox), each carrying a `Site` tab (key/value rows: Traffic Signal, Road, Road
Name, SLK, Latitude, Longitude, Intersection) and `Hourly Volume`/`Quarter Hourly Volume`
(per-detector, per-interval counts), plus — on some sites — `Hourly Peak`, `Quarter Hourly Peak`,
`Hourly Site Performance`, `Link and Offset Plans`, `SCATS Phase History` and `Timings` in a REAL
xlsx layout different from the CSV-subset shapes the rest of the adapter was built against. This
repo has repeatedly stated "no real Murdoch observed data exists" (most recently in the v2 queue
memory note); that was wrong, and Table 5-2's earlier finding about MRWA's guideline document
does not extend to this data drop.

**Decision (what was built):** the adapter's `parse()` now dispatches by suffix — `.xlsx`/`.xlsm`
go through a new `_parse_xlsx_workbook` path (Site + Hourly/Quarter Hourly Volume only), the
original CSV-subset role classifier is untouched and moved into `_parse_csv_bundle`. Quarter
Hourly Volume (MRWA's stated 15-minute profiling interval) is the granularity EMITTED as
`DetectorCountData`; Hourly Volume is cross-checked against it (each stated hourly figure must
equal the sum of its four quarter-hour bins — verified 0 mismatches across all 3 real files,
7,532 hours) and then discarded rather than also emitted, mirroring `adapters/daejeon.py`'s 소계
point-of-discard pattern exactly (emitting both interval lengths would double-count the same
vehicles and `quality.check_detector_count_intervals` would correctly REFUSE the overlap). Blank
detector cells (real, in `LM00973`, the smallest of the three sites) produce no record, never a
zero. `schema.SiteMetadata` gained `road_ref`/`road_name`/`stationing_km`/`position` — the real
Site tab states a road code, an SLK and a lat/lon that the CSV subset's hand-authored fixture
never had fields for; this is the same "the schema was wrong, not the map" correction the
adapter's own docstring already documents happening three times for the CSV path.
`SiteMetadata.coordinate_reference` is left `absent` rather than assumed `WGS84` — the workbook
states plain decimal degrees and never names a CRS.

**The detector→movement mapping — checked and ruled out, not merely assumed absent.** A SCATS
detector number is local to its site; turning "site LM00972 detector 7" into "link L, movement M"
needs the LMA/LMB site drawing MRWA Appendix-A documents (`SCATS_FIELD_MAP`'s existing "LMA
drawing" entries, `parsed_by_stub=False`). Three candidates in `client-data/` were opened and
checked: `Shpfile_of_Routes_for_TravelTime_Analysis.zip` (travel-time ROUTE geometry, no detector
reference), `Calc_MurdochDr_BaseTurningMov_JH_edit_20260526.xlsx` (a manual turning-movement
survey — every cell searched for "detector"/"SCATS"/"TCS"/"LM00…", zero matches), and
`murdoch.osm` (zero "detector" occurrences). None supplies it. So
`build_turning_counts_from_detector_map` was added as the caller-supplied-map-or-refuse pattern
`genesis.signals.generate_node_signals` already uses for `approach_phases`: a required
`detector_movements` map, every uncovered `(site, detector)` pair named in a REFUSE issue, no
guessing from detector number or ordering.

**Consequence for the calibration comparison (`services/observations.py`):** before this unit,
`_Aggregator` only ever summed `TurningCountData`; `ingest_observation_set` parsed
`result.detector_counts` and threw it away (`result.detector_counts.clear()` with nothing having
read it first — the clear call already existed, unused, which in hindsight was the seam waiting
for this). `_Aggregator` gained `add_detector_counts`, which folds a site's loop volumes into a
PARALLEL `detector_total`/`detectors` breakdown — never into `movements`, since a detector volume
has no turning movement by construction. `observed_side` now reports a detector-only site's total
from `detector_total` with `directions: []` and `count_basis: "detector_counts"`, so a caller
cannot mistake a site-level number for a per-movement one. A site with BOTH bases (no shipped
adapter produces one today) is excluded and named in
`coverage["sites_with_mixed_count_basis"]` rather than silently summed — two independent
measurement methods disagreeing would manufacture a number nobody measured.
`fixes.map_node_counts`'s existing "node-total GEH ≥ threshold → propose a vehicle-input-volume
fix" branch (arm (a)) already works unmodified against an empty `movements` list, so this is a
real, if narrower, calibration path: node-total GEH is possible from a WA SCATS drop today;
route-level/movement-level GEH (arms (b)/(c)) needs the map above and refuses without it — checked
by reading `services/agent/reality_check.py` and `fixes.py`, not merely asserted.

**Deliberately NOT built:** the real `.xlsx` forms of `Timings`/`Link and Offset Plans`/`SCATS
Phase History` (a genuinely different layout — phase-lettered columns, wall-clock rows — from the
CSV-subset parsers `_parse_timings`/`_parse_link_offset`/`_parse_phase_history`), named per-sheet
in an INFO issue (`scats_xlsx_sheet_present_not_parsed`) rather than silently skipped or
misparsed with the wrong layout.

**Verification (as first written, before the post-review fixes below):** `tests/test_dataimport_scats_wa.py`
(35, unchanged, still green — the CSV path is untouched), new `tests/test_dataimport_scats_wa_xlsx.py`
(22 — this line originally said 23, an off-by-one the post-review pass below caught and fixed) and
`tests/test_observations_detector_counts.py` (8). Full scoped run (`test_dataimport_*.py` +
`test_observations_*.py`): 444 passed. Full backend suite: 3935 passed, 1 skipped, 1
known-environmental failure (`test_rag_retrieval_quality_cases`, pre-existing per CLAUDE.md, not
touched). `backend/tools/scats_wa_xlsx_probe.py` (new, matches the `genesis_signals_daejeon_probe.py`
shape) re-runs the measurement against the real zip and prints aggregates/issue histograms only —
confirmed manually: 3/3 workbooks parse with zero refusals, 45 detectors, 30,208 detector-count
records, 0 hourly/quarter-hourly disagreements (7,532 hours cross-checked), 60 blank cells (all in
`LM00973`).

**Post-review fixes (2026-08-10, same day, before commit).** An adversarial verification pass found
7 findings against this still-uncommitted work; all 7 fixed. The two that touch this adapter file:
**[0]** the volume-tab header/data rows were hardcoded 0-indexed constants with no check that the
row actually looked like a header — REPRODUCED with a synthetic one-extra-metadata-row fixture (the
'Detector' group-label row got read as the header, fabricating a single detector literally named
'Detector' while the real header and every real detector's column were silently dropped, `result.ok`
staying `True`). Fixed by finding the header STRUCTURALLY (a bounded search for the `Day, Date,
Hour|Qtr Hour` shape) and REFUSING by name (`scats_xlsx_volume_header_not_found`) when no row in the
window matches, plus refusing a detector column whose header is not a plausible detector id
(`scats_xlsx_volume_implausible_detector_id`) instead of accepting it. Three new tests in
`tests/test_dataimport_scats_wa_xlsx.py` (`test_a_shifted_header_row_is_found_structurally_not_lost`,
`test_no_recognisable_header_in_the_search_window_refuses_by_name`,
`test_an_implausible_detector_id_in_the_header_refuses_the_tab`) — the file now holds **25** test
functions. **[5]** `_kv_float`'s docstring quoted two REAL client-measured values (a real SLK and a
real latitude to 12 decimal places for an actual Murdoch WA site) to illustrate that Site-tab cells
are strings; replaced with an obviously-fabricated placeholder in the same shape before this file was
ever committed, so the real value never enters git history.

Re-measured after the fixes, every count in this entry re-checked with `grep -c '^def test_'` AND
`pytest --collect-only -q` rather than assumed (the exercise finding [6] itself asked for): full
scoped run (`test_dataimport_*.py` + `test_observations_*.py`, now also covering the [1] fix's new
test in `test_observations_reality_check.py`) — **448 passed**. Full backend suite — **3946 passed,
1 skipped, 1 known-environmental failure** (same `test_rag_retrieval_quality_cases`, still untouched).
The probe was re-run against the real zip after the [0] fix; the numbers above are unchanged, as
expected — structural header search finds the SAME row the fixed-index code assumed on well-formed
input, it only changes behaviour on the malformed shape the fix targets.

The other 4 findings from the same pass touch files outside this adapter (`services/agent/tools_metrics.py`,
`services/agent/fixes.py`, `services/db.py`) and are not duplicated in this entry — see those modules'
own docstrings/tests (`tests/test_observations_reality_check.py`,
`tests/test_agent_fixes.py`, `tests/test_auth_core.py`).

---

## 2026-08-10 — the schema guard stops being a first-open-only guard

**Context:** unit P④'s entry above recorded this as *known and NOT fixed here*. It is fixed now.
`ensure_schema` memoizes per path so a login does not re-run the migration ladder, but the memo
returned the CONSTANT `SCHEMA_VERSION` without looking at the file — so the "this database is
newer than this build" refusal protected only the **first** open of a path. A process that
memoized at v2 and then had the file taken to v3 underneath it (a rolling upgrade, or an operator
running newer tooling against a running backend) kept inserting rows into a schema it knew nothing
about, silently. Reproduced during P④'s verification: after a successful ensure, setting
`schema_version` to 3 and calling `ensure_schema()` returned 2 instead of raising, while
`ensure_schema(force=True)` on the same file correctly raised.

**The fix, and its cost, measured rather than assumed:** every connection re-reads the recorded
version — 4.9 µs on a connection that is already open, against a ~1.7 ms `connect()` and a ~60 ms
scrypt, i.e. 0.3% of a connect and 1/12000th of one login. Opening a *second* connection just to
read the version would have cost ~626 µs (+36%), which is why `connect()` and `write_transaction()`
run the check on the connection they already hold rather than calling `ensure_schema()`. What the
memo still buys is skipping the migration **ladder** — the part that actually costs; what it must
never buy again is skipping the **guard**.

**Three corrections the adversarial pass forced on this fix's own first draft** (all confirmed,
none refuted):
* **The check was not atomic with the write it guarded.** `_ensure_schema_using` read the version
  under an in-process-only lock that was released before returning, and `write_transaction` only
  took `BEGIN IMMEDIATE` *afterwards* — so a second process could migrate the file in the gap and
  the first would write to a v3 schema believing it was v2. That is the exact rolling-upgrade
  threat this fix exists for, surviving inside the fix. `write_transaction` now re-reads the
  version a second time **inside** `BEGIN IMMEDIATE`, where the write lock makes it atomic with
  the write.
* **`connect()` cannot close that window** without changing its autocommit contract. Rather than
  weaken the guard to make the prose true, the docstring was corrected to state precisely what it
  guarantees (recency, not atomicity). The module docstring had made the same overstatement
  ("every connection re-reads the recorded version… what it must never buy again is skipping the
  guard") and was corrected the same way. **A prose claim stronger than the code is a defect**,
  and is treated as one here.
* **`schema_version()` skipped the guard entirely** — the one exported entry point that opened a
  connection and read the version raw, so a caller using it as an "is this database safe to use"
  pre-flight got the newer integer back with no error while every other entry point in the module
  would refuse the same file. It now runs `_refuse_if_newer` like the rest.

**Verification:** full backend suite **3946 passed, 1 skipped**. Each fix has a test that fails
without it, confirmed by reverting each in turn — including
`test_write_transaction_re_checks_the_version_after_taking_the_write_lock`, which simulates a
second process committing a version bump in the narrow gap, and
`test_schema_version_refuses_a_newer_database_too`.

---

## 2026-08-10 — The generated network was a trap in 225 places, and only one of the four causes was the one we thought

**Context:** the user opened the generated Murdoch network in Vissim and asked whether the visible
gaps are real. Most are not — the ~12 m intersection setback makes every *correct* junction look
like a gap — but 225 of 2,069 link ends had **no connector leaving them**. A link end no connector
leaves is not a cosmetic gap: a vehicle that reaches it departs the network. Vissim reports
nothing (no error, no warning, no `.err`), the demand evaporates, and every number downstream is
wrong by an amount nothing measures. Same silent-failure class as the emitter-v2 and fleet
lessons, one layer up.

**The measurement that scoped the unit was right about the count and wrong about three of the four
causes.** A proximity classifier — "what other link starts near this dangling end?" — attributed
75.6 % to missing cul-de-sac U-turns, 13.8 % to node-merge tolerance, 6.2 % to service roads and
4.4 % to the bbox edge. Re-measured from OSM **topology** instead of from distance
(`backend/tools/genesis_connectivity_probe.py`, and the scratch diagnostics behind it):

| cause, measured from topology | dangling ends |
|---|---|
| way terminal no other kept way touches (the true cul-de-sac family) | 172 |
| **a neighbouring way-segment was DROPPED for being shorter than its own setback** | 40 |
| node is a junction whose only exit is the same carriageway reversed | 14 |
| every movement at the junction classified U-turn and was suppressed | 6 |
| two one-way ways converge at a node and nothing continues | 13 |

**Murdoch Drive — the flagship case, and the one the proximity classifier got wrong.** It was
reported as a 9.0 m node-merge failure. It is not. Way 49337140 is the roundabout, and its
*segment 3* is under 4 m long, so `_trim` cut it below `MIN_LINK_LENGTH_M` and `build_draft`
dropped it **silently**. The roundabout was severed by the generator's own setback arithmetic, not
by OSM. Fifty-eight way-segments were being dropped that way. **Lesson: proximity tells you a gap
exists; only topology tells you why, and the two disagree exactly where the fix differs.**

**What was built** (`services/genesis/connectivity.py`, new; `osm.py` and `draft.py` extended):

1. **Cul-de-sac U-turns.** A way terminal referenced by exactly one kept way, inside the model, on
   a way that is two-way there, gets a U-turn joining its own FWD and REV carriageways. Detected
   from topology, never from the 3.5 m proximity of the pair — proximity also matches a mid-block
   pair whose junction connectors failed, and those two need opposite fixes. The U-turn runs lane
   1 → lane 1: index 1 is the RIGHT-most lane (measured 2026-08-06), and under left-hand traffic
   each carriageway sits LEFT of the shared centreline, so index 1 is the lane nearest the
   centreline on both sides. `connector_lane_pairs` gained an explicit `TURN_U` branch rather than
   letting it fall through the "through" case it agrees with only under this regime.
2. **Junction merge, tolerance 7.0 m, derived not chosen.** `NODE_MERGE_TOLERANCE_M =
   2 × NOMINAL_LANE_WIDTH_M` — one carriageway of the narrowest road this generator models, and
   3.5 m is the only lane width MRWA §5 prints (Table 5-7, doc p.195). It is an **upper bound on
   sameness**, not an error budget: two junctions cannot be closer than one carriageway, so two
   junction nodes closer than that are one junction mapped twice. Strict `<`. Same discipline as
   the benchmark's 22 m budget, a different kind of number, and the docstring says which. This one
   rule fixes *both* the ways-that-touch-without-sharing-a-node class and the undiscovered
   dropped-segment class, because two junction nodes 4 m apart on one way are also one junction.
3. **Four refusals, each by name.** *(a)* **Neither node is a junction the file declares**
   (`no_junction_to_reconcile`) — the rule that keeps the derivation in 2 honest, and the one an
   adversarial review had to put there; see the section below. *(b)* Grade separation
   (`layer`/`bridge`/`tunnel` disagreement — the Murdoch extract alone carries 59 `layer` and 22
   `bridge` tags, and merging across one would weld a freeway to the road under it), checked on
   the candidate pair **and on the whole cluster the union would make**. *(c)* A cluster-diameter
   cap of 2 × tolerance so a chain of individually-admissible pairs cannot walk across a junction.
   *(d)* A way whose every node lands in one cluster, dropped as a named
   `zero_length_after_deduplication` skip because it is not a road *between* junctions, it is part
   of one (45 such stubs on Murdoch, longest 6.82 m).
4. **The model boundary is not a cul-de-sac.** A terminal within `MAX_PLANIMETRIC_ERROR_M` of the
   model's edge — the file's declared `<bounds>` when it has one, the extent of the kept geometry
   when it does not — gets no U-turn. Without this the study boundary becomes a reflecting wall.
   Measured during development: a first cut of the last-resort pass supplied one anyway on Hope
   Road, which is why that pass now also honours every refusal the dead-end pass made.
5. **OSM turn restrictions are read.** `type=restriction` relations become movement bans that only
   ever REMOVE connectivity (`no_*` bans the named movement, `only_*` bans the rest). A relation
   this generator cannot reduce to from-way/via-node/to-way — a via-**way** restriction above all —
   is recorded in `restriction_refusals` and counted in the report, never dropped: an unread
   restriction leaves a banned movement legal, and Vissim will happily route traffic through it.
   **murdoch.osm contains zero relations**, so this half is proved on synthetic fixtures only.
6. **The service-road policy, stated.** `highway=service` is a carriageway like any other: split,
   offset, connected, node-merged, U-turned. The reasoning sits in `osm.py` beside the allowlist —
   the generator has three honest options (model them, exclude them by name, or leave them as
   roads a vehicle can enter and never leave) and only the third is indefensible.
   `--exclude-service` / `parse_osm_file(exclude_service=True)` is the opt-in strategic-network
   path and produces NAMED skips. Never the default: deleting real carriageways silently is the
   mirror image of inventing them.

A seventh rule earned its place during the work: a **last-resort U-turn** for an approach that has
no legal movement at all — every onward movement is antiparallel and was suppressed. Where there is
no alternative path, a U-turn creates none, so this does not fight §5.3.3, and each one it fires on
is named in the report rather than folded into a count. (Its other original case — "every way at
this node is one-way inbound" — moved into `dead_ends()` where it belongs, below; on Murdoch that
took this pass from 4 firings to 1.)

An eighth thing the unit owes the reader: **the generator counts its own dangling ends.** Every
built link no connector leaves goes into `metadata['connectivity']['dangling_approaches']` with the
cause the generator itself decided, and `genesis_connectivity_probe.py` reports that list rather
than classifying anything. Counting used to be the tool's job, and the split cost exactly what a
split costs — see the review section below.

**Result, measured** (`genesis_connectivity_probe.py`, offline, re-runnable):

| | before | after |
|---|---|---|
| dangling link ends | **225** | **21** |
| cul-de-sac U-turn missing | 172 | 0 |
| one-way dead end (no second carriageway to turn into) | — | 15 |
| junction where every movement is antiparallel (§5.3.3 suppression) | — | 4 |
| model boundary (correct) | 2 | 2 |

plain links 2,069 → 1,979; connectors 3,128 → 3,516; nodes 941 → 804. All 21 survivors are
refusals with a named reason rather than unfinished work: repairing a one-way dead end would mean
inventing a reverse carriageway. Every one of the 21 is now attributed by the generator, in
`murdoch.genesis.json` and in the §5 compliance render — before the review below, 8 of them were
named only inside the probe.

**Verification:** full backend suite **4003 passed, 1 skipped, 0 failed** with the
known-environmental `test_rag_retrieval_quality` excluded (it needs a RAG corpus this machine does
not have; it is the one long-standing failure and is not touched here). 57 tests in
`tests/test_genesis_connectivity.py`,
every expected number hand-derived from the fixture's coordinates. Two runs of the Murdoch
generation are byte-identical (`.inpx` sha256 `3f720fd9…`, `.genesis.json` `b6566f92…`). The core
assertions were confirmed to FAIL against `HEAD` (extracted read-only with `git archive`): 0
U-turns, cul-de-sac unreachable, near-touching ways unconnected, `exclude_service` rejected as an
unknown argument — and every fix in the review section below was confirmed to fail by reverting it
in the working tree and re-running its own test (16 mutations, 16 caught).

**What an adversarial review of this unit found, and what it changed**

The unit above was held from commit and re-reviewed against its own single hard constraint —
*never invent connectivity that reality does not have*. It violated it, and 10 further defects came
with it. All 11 are fixed; the two that mattered are these.

1. **The merge invented junctions, and named none of them.** `_eligible_node_ids()` admitted every
   way's first and last node unconditionally, so a lone cul-de-sac terminal was exactly as
   mergeable as a real junction — while the whole derivation of the 7.0 m tolerance ("two junctions
   cannot be closer than one carriageway") is a statement about *junctions* and says nothing about
   two roads that merely END near each other. Two unrelated dead-end streets 3.8 m apart fused,
   `intersection_node_ids()` then read the fused node as an intersection, and `build_draft` emitted
   ordinary turn connectors between two streets that do not meet. Nothing named it: after the merge
   `dead_ends()` saw neither terminal, so the fabricated road appeared in no report at all.
   **Measured on the real Murdoch extract: 2 of the 97 clusters were made entirely of lone
   terminals of different ways — two roads the generator built that the source data does not
   contain.** The rule now is: *a merge may only reconcile a junction the source already describes*
   — at least one node of every merged pair must be referenced by two or more kept ways — and every
   refused pair is reported by name, because "these two roads end within one carriageway of each
   other and were NOT joined" is a line a reviewer can act on and an invented road is not. Cost of
   refusing, measured: 97 clusters → 95, dangling ends **21 → 21** (each freed terminal is a
   cul-de-sac and gets its U-turn), invented junctions 2 → **0**.
2. **Grade separation was refused pairwise, so it could be reached transitively.** A node that
   legitimately carries BOTH signatures — a real ground/bridge transition — intersects either
   grade, so a purely-ground node could be unioned into a cluster already holding the elevated way
   while the far pair, being over the tolerance, was never a candidate and never compared. A local
   street welded to a freeway deck, through the one node entitled to be at both heights. The
   refusal now holds for the **cluster**: every member must share one common vertical position,
   which is the pairwise test generalised from two nodes to *n*. Not observed on Murdoch (which
   records **zero** grade refusals — the check is exercised there only in the sense of never
   firing), but reachable by construction and now covered by a synthetic three-node fixture.
   *The stricter reading was measured and rejected.* A rule refusing any merge that newly puts two
   ways of different vertical signature on one node would fire on **8 of Murdoch's 95 clusters** —
   and every one of them is the SAME road's ground and structure segments (`service` tunnel ↔
   `service` ground, `secondary` bridge ↔ `secondary` ground, `unclassified` bridge ↔
   `unclassified` ground). Those are bridge abutments and tunnel portals, i.e. precisely the
   junction the merge exists to reconcile; not one is a low-class road meeting a high-class deck.
   Refusing them would sever 8 real structure transitions to prevent nothing observed, so the
   boundary sits at the cluster and is stated here with the measurement behind it.

The nine others, in one line each: `dead_ends()` structurally could not see a terminal several ways
*share* (they all END there, one-way inbound), so 2 Murdoch approaches were traps in no report — it
sees them now, direction-aware, and the remaining unnamed cases moved into the generator's own
dangling census; the probe's `--json` was not UTF-8 on Windows and its own output would not
round-trip through `--baseline-json` across this project's two dev boxes; last-resort and
cul-de-sac U-turns hard-coded `node_id=0` even at real junctions, which hid them from `signals.py`'s
two node-scoped checks and made the report state that an approach had no onward movement when one
genuinely left it; `osm_turn_restrictions_enforced` counted relations that *parsed* rather than bans
that *fired* (now both numbers, with the inert ones named); and five paths that were defined but
never exercised by a test — the last-resort pass itself and its boundary guard, the
non-drivable-continuation dead-end reason (30 firings on Murdoch), three of the four restriction
refusal codes, `UNBUILDABLE_U_TURN_NO_LINK_PAIR`, and the strict `<` at exact tolerance — now have
one each.

**Lesson, and it is the same one twice.** Both criticals are the *silent-plausible* failure this
project exists to catch, one layer up from the emitter's: a number that improved (225 → 21) while
the mechanism that improved it was quietly inventing the thing it was measuring. The unit's own
doctrine — "a refused edit with a clear reason beats a completed edit with wrong numbers" — was
written in its docstring and not enforced in its code. **A count is not evidence that the thing
counted is real; a repair that no report can name is indistinguishable from a fabrication.** The
second lesson is narrower and just as reusable: *the thing being measured must own the
measurement.* A probe that classified for itself was free to disagree with the generator, and did —
8 Murdoch approaches carried confident probe-side names that existed in no generator artifact.

**Offline-only, and stated as such.** Every claim here is offline: the emitted file round-trips
through `inpx_parser`, self-diffs empty through `genesis.diff`, and the Murdoch output is
byte-identical across runs. **No Vissim has opened it.** The 2026-08-08 lesson applies unchanged —
an offline round-trip proves self-consistency, never that Vissim LOADS the file. The emitter itself
was NOT changed — the new connectors go through the same `_connector_element` /
`_connector_lanes` path the 2026-08-06 live check certified — so the residual risk is geometry,
not structure, and it is one specific thing: **every U-turn connector is 3.50 m long and
antiparallel** (the two carriageways sit half a lane either side of the shared centreline), which
is a tighter turn than any connector in either network that live check loaded. Short connectors in
general are NOT the risk and were measured rather than assumed: the file already live-loaded on
2026-08-06 carried 3 connectors under 0.5 m and 29 under 2 m, and this one carries **2 and 18** —
the junction merge reduced that population rather than adding to it (min length 0.430 m → 0.480 m).
A live LoadNet would also confirm that the 45 dropped stub ways left no dangling
`<fromLinkEndPt>` reference — offline, `genesis.diff.load_topology` already resolves every
endpoint with zero findings.

**Client-facing note for TK:** the 21 remaining dangling ends are mostly one-way roads that end
nowhere in OSM (South Street, Roe Highway, Kwinana Freeway ramps). Those are breaks in the base
map, not in the generator, and each is named in the probe output — useful input if the client
wants the map corrected before the model is calibrated on it.

---

## 2026-08-11 — the client answered the 대전 YELLOW(RED) question, and a refusal built on the
   old ambiguity was wrong

**Context.** The 2026-08-10 entry above left one open question on 21 of the 22 usable 대전 signal
sheets: does the `YELLOW(RED)` column hold 황색 alone, or 황색+전적색 combined? The client has now
answered: **황색+전적색 combined** (interpretation "b"). That matches what the parser's own
corroboration check already suggested — `MIN. GREEN == 시간분할 − YELLOW(RED)` on 59 of 94 (sheet,
phase) pairs, and `MIN. GREEN` is a green, so the subtracted quantity was always the whole
clearance.

**The bug the answer exposed.** A probe run with `--declare-amber 3.0` (a reviewer's declared
amber, not a document value) produced only **10 of 22** plans. **12 intersections refused with
`daejeon_all_red_not_positive`**, because those sheets' `YELLOW(RED)` is exactly the declared 3.0 s
amber, which makes `all_red = YELLOW(RED) − amber == 0`, and the old check refused on `all_red <= 0`.
Under the confirmed reading that is wrong: an intersection with a 3 s amber and no all-red interval
at all is an ordinary, valid signal design (amber running straight to red), not a gap. Refusing it
discarded a correct plan.

**The fix.** `services/genesis/signals_daejeon.py`, in `to_fixed_time_plan`: the gate changed from
`all_red <= 0` to `all_red < 0`. All-red of exactly zero is now accepted and produces a program
phase with `all_red_s=0.0` (no all-red display). Only a **negative** all-red — the declared amber
LONGER than the sheet's own `YELLOW(RED)` for that phase — still refuses, renamed
`daejeon_all_red_negative` (the old name, `daejeon_all_red_not_positive`, became inaccurate once
zero stopped being a refusal; the rename is a straight swap everywhere the code appeared —
`BRIDGE_REFUSE_CODES`, the raise site, the one test that named it). That refusal is real: a negative
all-red means the declaration contradicts what the document states for that specific phase, not
that the design is unusual, and it is kept as a named refusal per this unit's instructions.

**What did NOT change.** `DaejeonSettings.declared_amber_s` is still `None` by default, and
`to_fixed_time_plan` still refuses with `daejeon_intergreen_split_unstated` when nobody has
declared an amber — the client's answer settles WHICH quantity `YELLOW(RED)` is, it does not state
the amber by itself (the column still prints one combined number), so the amber remains a reviewer
declaration and is never hardcoded. The module docstring now records 3.0 s as a commonly cited
Korean police-standard 황색시간 convention a reviewer MAY cite as the basis for a declaration —
stated plainly as a convention, not measured from any document in this repository, with no
fabricated citation. The existing provenance machinery already carried the declaration and its
basis (`amber_declaration_note` → `Provenance.derived_by`, `source_kind=ProgramSourceKind.DECLARED`
on every DECLARED program and phase) — that was correct before this unit and needed no new
mechanism, only a docstring fix (`amber_declaration_note`'s field description wrongly said
`Provenance.note`; it is `Provenance.derived_by`).

**Measured coverage, real zip (`client-data/218. 대전신호.zip`), `--declare-amber 3.0`, 22 parsed
sheets / 120 scheduled (수준, ID) program-pairs throughout:**

*Sheet level* (>= 1 plan produced counts the sheet as ready):

| | old gate (`all_red <= 0` refuses) | new gate (`all_red < 0` refuses) |
|---|---|---|
| sheets with >= 1 plan produced | 10 / 22 | **20 / 22** |
| sheets blocked, `daejeon_all_red_not_positive` / `_negative` | 12 | **0** |

*Program-pair level, new gate only* (the finer-grained number, since a sheet can mix producing and
refusing programs):

| | count (of 120) |
|---|---|
| plans produced | **110** |
| refused, `daejeon_all_red_negative` (declared 3.0 s amber genuinely longer than that phase's `YELLOW(RED)`) | 5 |
| refused, `daejeon_phase_yellow_unknown` (no usable `YELLOW(RED)` on either ring — pre-existing, unrelated) | 5 |

The remaining 2 sheets (10 program-pairs) that still produce no plan are blocked entirely by
`daejeon_phase_yellow_unknown` — a data gap unrelated to the amber/all-red interpretation and
outside this unit's scope — plus a residual 5 program-pairs where the 3.0 s declaration genuinely
does contradict that phase's stated clearance (correctly still refused).

Two distinct "before" states, not to be conflated: **with no amber declared at all** (the
unchanged, still-correct default) every one of the 120 program-pairs refuses with
`daejeon_intergreen_split_unstated` — 0 plans, 21 of 22 sheets "blocked ONLY by the unstated
yellow/all-red split" per the probe's own counter, exactly as on 2026-08-10 and exactly as today
with the declaration omitted. **With the amber declared at 3.0 s** is where the bug lived: the old
gate produced only the 10/22 sheets shown above; the fix raises that to 20/22 without changing
anything about the no-declaration path.

**Offline only, restated.** This is the same 2026-08-08 lesson this file keeps repeating: the probe
and the test suite prove the bridge's own arithmetic is self-consistent and re-derivable by hand; no
Vissim has opened any of these plans, and building the actual signal groups still needs the
현시도 (approach→phase drawing) this module has never had — item 1 in the 2026-08-10 entry's gap
list, unchanged.

**Verification:** `backend/tests/test_genesis_signals_daejeon.py`, 36 tests (35 + 1 net new; two
existing tests were rewritten rather than just renamed — the old
`test_a_declaration_that_contradicts_the_sheet_is_refused` asserted the exact behavior this unit
fixes, so it became `test_a_declared_amber_that_exactly_matches_yellow_red_produces_a_zero_all_red_plan`
(must now PRODUCE a plan) plus `test_a_declared_amber_longer_than_yellow_red_is_refused_as_negative`
(genuine contradiction, must still REFUSE) against the same synthetic `clean_five_phase.txt`
fixture, using `declared_amber_s == CLEAN_YELLOW_RED_S` and `CLEAN_YELLOW_RED_S + 1.0` respectively
rather than a new fixture file — the amber gate is a single scalar applied uniformly, so the
existing fixture already exercises both the boundary and the violation. Full backend suite: 4004
passed, 1 skipped (`test_rag_retrieval_quality`, pre-existing/environmental, unrelated).

---

## 2026-08-11 — The first real observed number: SCATS site → Vissim node, and what the GEH decomposes into

**Context:** `fd2937e` made the three real WA SCATS `.xlsx` workbooks readable and wired site-level
comparison into `services/observations.py`, but that consumption path had only ever been verified by
READING the code. No real observed number had ever been compared against a real model run. The
blocker was a named one: `observations.observed_side` requires a caller-supplied
`{site_id: node_id}` map and refuses without it — correctly, because survey site ids are not Vissim
node ids. Nothing in the repo could produce that map.

**Decision 1 — derive the site→node join from coordinates, at a tolerance built the way the
benchmark's is.** New module `backend/services/site_node_map.py`. The SCATS `Site` tab states
decimal-degree lat/lon; the consultant `.inpx` states local metres with no EPSG. They are joined
through this project's only georeference, reusing `genesis.benchmark.helmert_bridge` rather than
reimplementing it (so a missing `refPointMap`/`refPointNet` anchor stays a REFUSE, never a
zero-offset guess). `SITE_NODE_TOLERANCE_M = 29.0` = **14.0** georeference residual
(`diff.GEOREF_MAX_RESIDUAL_M` — the floor, because the bridge itself moves points by up to this
much) + **1.0** tangent-plane budget (`project.MAX_PLANIMETRIC_ERROR_M`) + **14.0** junction-centre
allowance (`2 × connectivity.NODE_MERGE_TOLERANCE_M` = 4 × the only lane width MRWA §5 prints). The
third term is an **allowance, not a measurement**, and says so — SCATS records one point per signal
site while a Vissim node centre is derived from where its member links cross the junction, and the
two are not defined to be the same point. This is a *different* question from
`benchmark.MATCH_TOLERANCE_M`'s 22 m (a point against a junction centre, not a polyline against a
polyline), so it gets its own third term rather than borrowing that one.

Refusal-first in four shapes, each naming the site: nothing within tolerance (**nearest does not win
by default**), two or more nodes within tolerance (every candidate named, none chosen), two sites on
one node (both refused), and no stated coordinate at all. A match is never silent — distance,
runner-up, runner-up distance and separation ratio all ride on the result, and a match with less
spare budget than the georeference residual is flagged `thin_margin`.

One defect found and fixed by its own test while building it: `inpx_parser` returns `(0, 0)` both
for "node at the local origin" and for "node whose centre could not be derived", and reading that
sentinel alone dropped a legitimate node sitting on the origin — which would then have made the
origin a magnet for every distant site. Now `(0, 0)` counts as unplaced only when the node carries
no polygon either.

**Result on the real pair:** all 3 sites resolved, 0 refusals, at 6.4 m / 10.5 m / 27.8 m. The
mapping is not sensitive to the tolerance: the closest two nodes in this network are 164 m apart, so
twice the tolerance cannot reach two junctions, and every match beat its runner-up by 5.9×–79×. The
27.8 m match is inside the budget by only 1.2 m and is reported `thin_margin` for exactly that
reason — it rests on the runner-up separation, not on the absolute distance.

**Decision 2 — compare against the consultant's `.inpx`, not the generated OSM draft.** The Helmert
fit is fitted to nine surveyed intersections *of that network*, so no site→node map exists for any
other one; the draft is geometry-only (a plain draft has no demand, so its node totals would be
zero) and its node ids are sequential-from-1 with no relation to the consultant's — the exact
id-collision benchmark v2 was written to kill.

**Decision 3 — the period is declared and measured, not assumed:** Tuesday 2026-02-17, 07:15–08:45.
A mid-week neutral day; one of the four days in the drop with *zero* blank detector cells at all
three sites (Thu/Fri/Sun have blanks at the smallest site, and a blank is an unobserved interval,
not zero flow); 07:15–08:45 is the shortest window containing every site's own measured AM peak
hour; and 90 minutes is the model's OWN declared `simPeriod`, whose demand is flat across all six
15-minute intervals — so matching the durations means **neither side is scaled**.

**THE NUMBERS (live, `backend/tools/scats_reality_check_live.py`, 5400 s / 1 seed / 1 result .db,
consultant `.inpx` hashed identical before and after).** Site-level GEH, modelled node total vs
observed site total, threshold 5.0:

| site → node | GEH | modelled vs observed |
|---|---|---|
| LM00398 → node 1 | **2.79** | −3.0 % |
| LM00972 → node 2 | **3.51** | −5.0 % |
| LM00973 → node 3 | **6.38** | −9.5 % |

2 of 3 under GEH 5. Every site is modelled BELOW observed — a consistent direction, which is
evidence in itself.

**Decision 4 — a single-duration GEH is not decomposable, so two durations were run.** A second live
run at 60 min (07:30–08:30 / 3600 s) turns two hand-waved caveats into measured numbers, because
modelled throughput is very nearly linear in run length:

* *fill deficit* (empty network at t=0), fitting `modelled(T) = rate × T − D`: **25 / 4 / 75
  vehicles**, i.e. 0.31 % / 0.09 % / 1.91 % of the 90-minute modelled total. Small — the warm-up is
  **not** the explanation for the shortfall, which is the opposite of what was assumed before
  measuring it.
* *demand-profile shape*: the observed rate in the peak hour exceeds the 90-minute average rate by
  **+3.7 % / +2.9 % / +0.2 %**. The model's demand is flat across all six intervals, so this
  within-peak peaking is something it cannot reproduce **by construction** — and it is why the
  60-minute comparison reads worse (GEH 5.14 / 4.51 / 5.79 at −6.6 % / −7.7 % / −10.5 %) despite
  being the busier window.
* *residual* — what survives both is the model's real disagreement with the field, and it is the
  only part a calibration edit should ever act on.

**Decision 5 — STOP before the edit/apply loop, and say why.** The loop's mechanism is already
live-certified (the 2026-08-07 reproof); re-running it would re-prove the mechanism, not the
numbers. Three reasons not to drive a demand edit from these numbers:

1. **The dominant uncertainty is unresolved and unresolvable from this drop.** A site total is the
   sum of that site's detector volumes. Whether that equals "vehicles entering the junction" depends
   on the site's detector layout — a site carrying both stop-line and upstream loops on one lane
   double-counts it. Only the MRWA Appendix-A LMA/LMB drawing states the layout, and
   `adapters.scats_wa.build_turning_counts_from_detector_map` already measured that it is not here.
2. **The fix path's stated assumption is known not to hold at these nodes.**
   `fixes.ASSUME_INPUT_DRIVES_NODE_TOTAL` needs one vehicle input to dominate a node's demand; the
   2026-08-07 probe picked node 8 precisely because input 113 carries ~69 % of what reaches it.
   Nodes 1 and 3 — the two that would produce proposals — are the network's largest junctions, fed
   by many inputs, and no equivalent share has been measured for them.
3. **It is the client's calibrated model.** Editing its demand on a site-total comparison whose
   basis is explicitly not movement-level, and whose largest uncertainty is open, is the "completed
   edit with wrong numbers" the hard rules forbid.

**Consequence:** the site→node join is no longer a human-typed input, and the observed side of the
reality check has been exercised on real client data end to end. What a human must still supply is
unchanged and now precisely bounded: the **LMA/LMB site drawings**, which alone would (a) resolve
whether a site total is entering volume or double-counted, and (b) unlock the detector→movement lift
that turns this site-level check into the movement-level one the fix path actually wants.

**Verification:** `backend/tests/test_site_node_map.py`, 13 tests, all synthetic fixtures (no client
file is read by any test). Mutation-checked: disabling the ambiguity refusal, the no-candidate
refusal, the conflict refusal, the thin-margin flag or the origin-sentinel fix fails 9 of the 13.
Full backend suite: 4017 passed, 1 skipped (`test_rag_retrieval_quality`, pre-existing/environmental).
Live evidence (git-ignored): `runtime/scats-reality-check-live/{state,log}{,_60min}.*`.

---

## 2026-08-11 — The R1.5 gate refuses our own network, and finds what a dangling-end census cannot

**Context:** roadmap 3.3. `tk-requirements-2026-07-31.md` §R1.5 defines nine checks (V1–V6 refuse
tier, V7–V9 warn) and states the point plainly: *"The validation gate is what makes AI construction
sellable."* Every check in it is something a controller had been running BY HAND — V1 as a manual
`genesis_open_live_check` run, V2 as an ad-hoc reference sweep, V3 as the 225→21 dangling-end
measurement. The gate turns a habit into a property of the product.

**The design rule the whole unit hangs on:** a check that cannot run reports **NOT_CHECKABLE with a
named reason, and never counts as a pass**. Precedence (`FAIL > NOT_CHECKABLE > PASS`) lives in
exactly one place, and the verdict is PASS only when every refuse-tier check genuinely passed. This
codebase has been bitten twice by the opposite shape — Vissim's `LoadNet` returning SUCCESS on a
stripped file with zero links, and a schema guard that protected only the first open of a database.
A gate that goes green because a check quietly did not execute manufactures confidence, which is
worse than having no gate.

**The verdict on our own current output: REFUSED (exit 2).** 1 pass, 3 fail, 5 not-checkable —
reported as measured, never tuned until green.

**V3 is the finding that justifies the unit.** **8 fully isolated zones totalling 150 links (7.6% of
the network)**: nothing can enter them, nothing can leave. The largest is **96 links** — Scandrett
Way, Broadhurst Crescent, Calder Way and neighbours, an entire residential pocket. **No dangling-end
census can see this**, and ours did not: every one of those 150 links has a connector in *and* a
connector out, so the 2026-08-10 connectivity work scored them healthy. Dangling ends and
reachability are different questions with different answers, and only the second asks whether a
vehicle can actually get there. Independently reproduced by the controller with a from-scratch
weak-component analysis: 9 pockets / 152 links (7.7%) — the small delta against the gate's 8/150 is
definitional, and the finding and its scale agree.

**V4** fails on one of 32 lane drops: link 630 (South Street) drops lanes 3 and 4 with only
**126.12 m** of upstream road against the **200 m the file itself declares** in `lnChgDist`. The
check never invents a distance — it asks whether the network provides the runway the network says
it needs. The other 31 drops clear it (p10 ≈ 205 m).

**V1 passes when given live evidence, and proves identity rather than trusting a filename.** The
gate opens no COM at all — it consumes `genesis_open_live_check.py`'s `state.json` and admits it
only when the **sha256 of the file that probe loaded equals the gated file's**. It then asserts
`load_ok`, zero new `.err`, SaveNetAs, **and the COM object census against the file's own element
counts** — which is the 2026-08-06 empty-load trap, stated as a check. Verified on the connectivity
network: `oracle_sha256 3f720fd9…` matching, COM census 5,495 / 804 / 3,516 / 1,979 identical to the
file. With no live evidence V1 is NOT_CHECKABLE, so **an offline run can never report an unqualified
pass** — deliberate, because V1 is the only check that proves Vissim opens the file at all.

**V5 and V6 are NOT_CHECKABLE, not passes:** the geometry-only draft declares zero controllers, zero
heads and zero vehicle inputs. A file cannot say whether that means "genuinely unsignalised" or "the
import is missing", so it is not counted as clean.

**Thresholds, each derived rather than chosen:** `ZERO_LENGTH_M` 0.0005 m = half the emitter's own
1 mm coordinate quantum (below it a length is not representable as non-zero *in the file*);
`SHORT_LINK_FLOOR_M` 2.0 m = `draft.MIN_LINK_LENGTH_M`, the generator's own declared floor, so a
link below it contradicts its own provenance; the lane-change runway is **read per link** from the
file's `lnChgDist`; `ROUGH_LANE_CAPACITY_VEH_PER_H` 1900 is **imported** from
`services.agent.constraints`, with a test pinning object identity so the gate and the
`latent_demand_overflow` guardrail cannot disagree about the same link. Compute budgets are not
modelling thresholds — exhausting one yields NOT_CHECKABLE with the budget named.

**Honest scope limit:** V2's reference table covers what `services.genesis` emits (33 reference
kinds read off two real emissions, 33,911 references checked, 0 unresolved). A consultant's real
`.inpx` carrying tags outside the measured 109-tag inventory reads NOT_CHECKABLE — correct
behaviour, but it means this is a **generator gate today, not a general `.inpx` gate**. Two
reference kinds are declared permanently unresolvable by name and reason
(`signalController/@intSupplyData` is an opaque base64 blob; `timeIntervalVehVolume/@timeInt` is a
composite whose target carries no `no` attribute).

**Permanently unanswerable offline:** V8's right-of-way (Vissim derives conflict areas from geometry
at load, writes none back, and offers no COM Add/Remove — so the 7,781 crossing movement pairs at
513 unsignalised nodes are reported as evidence, not graded), and V1's whole premise.

**Verification:** full suite **4087 passed, 1 skipped** (baseline 4019 + 68 new). A 24-mutation
harness disables each check in turn; **24/24 caught**. Every refuse-tier check has a synthetic
network that must fail it, and one test family runs the gate over the real emitter's output
(osm → draft → emit) so the hand-written fixtures cannot drift from what the generator actually
writes. One bug found while testing: the CLI's `--json` wrote cp1252 on this Windows box — the same
defect the connectivity probe hit on 2026-08-10, now with a byte-level regression test.

---

## 2026-08-11 — The gate's two refuse-tier findings were both OURS, and neither wanted the obvious fix

**Context:** the R1.5 gate (committed `3324246`) refuses the network `genesis_osm_draft.py` builds
from the Murdoch extract. Two refuse-tier failures were the generator's to answer: V3's **8 fully
isolated zones, 150 links, 7.6 % of the network**, and V4's **one lane drop with 126.12 m of runway
against the 200 m the file itself declares**. Both looked like a repair job. Both turned out to be
diagnosis jobs where the obvious repair was the wrong answer.

**V3 — measured before touched, and the measurement decided the fix.** Four candidate causes were
checked against the source, per zone:

| question asked of `murdoch.osm` | answer |
|---|---|
| does any zone way share a node with a way we DID model? | **0 of 8 zones** |
| is any zone within the 7.0 m node-merge tolerance of the network? | **no — nearest 20.2 m** |
| did any node-merge refusal sever a zone? | **0 of 64 refusals touch one** |
| did a short-segment drop split a zone way? | **no — every zone way kept every segment** |
| is any zone at the model boundary? | **no — nearest 53.2 m inside** |

The decisive measurement is one level up: build the way-graph of the **kept drivable ways alone**
and take its components. It has 16; the largest holds 811 of 863 ways, and **every one of the 8
emitted zones is exactly a non-main component of that graph**. Not one zone way is in the main
component. The other 7 small components were reconciled into the network by the 7.0 m junction
merge — the merge doing precisely the job it was built for, and the reason the 8 that survive it are
believable rather than suspicious. **We did not sever these; the extract does**, and what it severs
them with is legible:
7 of the 8 are touched only by `footway` / `path` / `cycleway` / `construction`, and the 8th is
touched by nothing at all.

**So the fix is exclusion, and the rule is that exclusion is only ever for a cause that lives in the
SOURCE.** `build_draft` now runs a second pass: find the weakly-connected groups of links that
contain **no network entry and no network exit** (both halves required — a pocket a vehicle can
reach but not leave is a trap, is named by the dangling census, and is NOT deleted), diagnose each
against the extract, remove the ones the source explains, and rebuild. Removal is a rebuild rather
than a deletion because dropping the ways shrinks the extract's extent, which moves the projection
origin, which moves every coordinate — the numbers in the report have to be the numbers in the file.
Three source-side reasons are excludable and each says a different thing to the client
(`access_only_via_ways_this_generator_does_not_model`, naming the classes;
`severed_in_the_source_extract`; `truncated_at_the_model_boundary`). Every excluded way leaves a
named `SKIP_ISOLATED_ZONE` behind, exactly as `--exclude-service` does, and a §5.3.3 compliance item
names every pocket by street.

**The fourth reason is the one that matters, and it fires zero times here.**
`connected_in_the_source_but_not_in_this_network` — a way we modelled shares a node with the pocket
and the pocket is still unreachable — is deliberately NOT excludable. That case is a movement THIS
generator lost, the roads are real, and a pass that can delete its own bugs is worse than no pass:
the count would improve while the defect went out the door. It is retained in the network, named,
and turns the compliance item VIOLATED. On Murdoch it is empty, and that emptiness is what makes the
other three safe to act on.

**Result:** links 1,979 → **1,829** (−150), connectors 3,516 → **3,257** (−259), nodes 804 → **761**
(−43); V3's `links_in_a_broken_zone` **150 → 0**. The dangling-end census is **21, unchanged**, with
the same causes — the two questions really are independent.

**V4 — the lane drop is not real, and `lnChgDist` would have papered over it.** The link the gate
named as 630 (its id in the PRE-exclusion generation; the zone-exclusion rebuild renumbers, and in
the final artifact this same road — way 406690969, South Street — is **link 528**; id 630 there is
an unrelated service road, so look it up by way id, not link id) is a 4-lane one-way trunk approach
tagged `turn:lanes=through|through|right|right` with
`destination:lanes=||Freeway North Only|Freeway North Only`. Two movements leave it (the road
continues; a ramp diverges at −24.0°, inside `THROUGH_ANGLE_DEG` so both classify `through`), and
`connector_lane_pairs` gave **both of them lanes 1 and 2** — the geometric default aligns every
`through` movement on the median side — leaving lanes 3 and 4 with no connector at all. In the world
this road has no lane drop: two lanes go through and two go to the ramp. **The model invents the
merge, and Vissim would delete the vehicles that could not make it, silently, after `DiffusTm`.**

The runway shortfall is real and is also not ours: the chain into the drop stops 126 m upstream
because South Street's forward carriageway **breaks in OSM** — one way ends and the next begins
15.63 m away, sharing no node, with neither end a junction the file declares, so the 2026-08-10
`no_junction_to_reconcile` rule refuses it and must. That break is already one of the 21 named
dangling ends.

So the unit's offered fix — derive `lnChgDist` per link from the runway that exists — was **measured
and rejected**. It is honest about a drop that is not there, and it would make V4's clause 2
**vacuous on our own output**: a generator that derives the declared need from the available runway
can never fail the check that compares them. A gate that cannot fail is the 2026-08-10 lesson in a
new costume. `lnChgDist` is untouched; V4 stays able to refuse us.

**What changed instead: the source's own `turn:lanes`, used ONLY as a repair.** OSM writes
`turn:lanes` leftmost-to-rightmost in the direction of travel in either driving regime, and this
generator indexes lane 1 RIGHT-most, so OSM position *p* of *n* is Vissim index *n − p + 1* — a
mapping asserted by hand in the tests, because reversing it would put every turn on the wrong side
of the road. The allocation is read off the tag only when **all** of: the default starves a lane
(the one demonstrably wrong state), the tag applies to this direction and has exactly one entry per
lane, **every** entry names a movement (an empty entry and `none` both mean the source did not say,
and a gap is never filled in), the tag's contiguous groups are exactly as many as the movements
built, and no group's own word contradicts the measured geometry. Where the default already serves
every lane it is left alone — re-allocating a working junction on the strength of a tag is a change
with no defect behind it.

One bug fell out of the diagnosis: `_directional_lanes` resolved the lane COUNT from `lanes` (right)
and then **discarded the turn values** (wrong) whenever `lanes` won, so on a one-way way the file's
statement of which lane goes where never reached the connector builder at all.

**Blast radius, measured before it was written:** of 1,979 links, 48 pass the "tag applies, one entry
per lane, every entry named" test, 29 also have as many groups as movements, and **4** also have a
starved lane. Those 4 are what changed. Lane drops **32 → 28**, none added, and every removed drop is
a road that does not narrow. The other 28 are untouched and V4 still measures their runway. The 28
approaches where the default starves a lane and the tag could not settle it are now listed by name
and reason in `turn_lane_allocations_refused` — a starved lane nobody names is a lane full of
vehicles Vissim deletes without a word.

**Verification:** full suite **4111 passed, 1 skipped** (baseline 4087 + 24 new). A 14-mutation
harness reverts each decision in turn — including the OSM→Vissim lane-index mapping, the
both-halves definition of an isolated zone, and the refusal to exclude a zone we severed ourselves —
and **14/14 are caught**. Two Murdoch runs are byte-identical (`.inpx` sha256 `38a3f2c4…`,
`.genesis.json` `bcec5d5d…`). The R1.5 gate goes from **1 pass / 3 fail** to **2 pass / 2 fail**:
V4 passes, V2 still passes, V3 no longer finds a zone. `emit.py` was not touched and neither was
`gate.py`.

**What still refuses, and why it should.** V3 still fails on the **19 non-benign silent departure
points** — the 15 one-way roads that end nowhere in OSM and the 4 junctions where every movement is
antiparallel. Those are the 2026-08-10 refusals: repairing one means inventing a reverse carriageway.
The gate is right to refuse them, we are right not to "fix" them, and the fix is in the client's base
map. V1 remains NOT_CHECKABLE until a live `LoadNet` — and after this unit that matters more than
before, because the isolation pass now REMOVES geometry, and only Vissim can confirm the file it
removed it from still opens.

**Lesson.** Twice in one unit, the improvement that would have made the number go green was the
wrong change: connecting the pockets would have invented roads, and deriving `lnChgDist` from the
runway would have disabled the check that found the problem. **The question a validation layer asks
is not "how do I make this pass" but "which of us is wrong" — and this time it was us both times,
in neither of the two places the number pointed at.**

**Post-review (adversarial pass, 4 confirmed / 1 refuted).** Three fixes rode in before commit:
(1) the isolation-cap safety net committed the exact sin the pass exists to prevent — a zone stuck
at cap exhaustion landed in NEITHER ledger (not excluded: the break preceded the extend; not
retained: its reason was excludable), so a pocket physically present in the file appeared in no
report. Stuck zones are now re-tagged with the cap reason, kept in isolated_zones_retained, and
turn the §5.3.3 item VIOLATED — a generator that cannot finish its own cleanup does not grade
itself APPLIED. (2) The §5.3.3 subjects named ONE street per pocket while the docstring promised
every street; the 96-link pocket spans a dozen and a client fixes a base map street by street.
Subjects now carry the full list. (3) The V4 example cited link 630, which is the PRE-exclusion
id — the rebuild renumbers, and in the shipped artifact that road (way 406690969) is link 528;
the entry now says to look it up by way id. The .inpx is byte-identical after all three
(38a3f2c4…), so the live LoadNet evidence and V1 sha-identity remain valid.

---

## 2026-08-11 — V7's four warn-tier findings on our own output, and the two that were ours

**Context.** Roadmap v2 §5.A row A3, the unit after the one above: with V3 and V4 answered, V7
(warn tier, geometry sanity) still failed with exactly four findings on the regenerated Murdoch
draft — **3 pairs of links sharing one polyline** (6 links) and **1 link at 1.967 m**, below the
generator's own `MIN_LINK_LENGTH_M` = 2.0 m floor. `emit.py` and `gate.py` untouched throughout;
all four findings turned out to need a diagnosis before a fix, same discipline as the unit above,
and two of the three same-polyline pairs and the short link were ours, the third pair was not.

**Finding 1 — two of the three same-polyline pairs (links 868/869, way 658741831; links 874/875,
way 658741846) are FWD/REV of the SAME way, and the cause is a palindrome the offset code did not
handle.** Diagnosed by tracing the actual pipeline rather than guessing: both ways are short
driveway/parking-loop spurs whose two ends, after `merge_junction_nodes`, land on the identical
merged node (`6168482944` — itself a legitimate merge, three real junctions
`6168482956`/`6168482946`/`6168482944` each within the 7.0 m tolerance of the next, transitively
unioned, diameter 11.3 m, under the 14.0 m cluster cap; not a chaining bug, and not touched here).
That makes the segment's own trimmed centreline `[A, M, A]` — a PALINDROME. The old code built REV
by reversing the point list and offsetting the reversed list by the same POSITIVE amount, relying
on reversal to put REV's carriageway on the opposite side of FWD's. Reversing a palindrome is a
no-op, so FWD and REV were fed the literal same point list and produced the literal same offset
polyline.

**The fix carries no special case for the palindrome.** `_build_once`'s link loop now computes the
offset on the CANONICAL (never-reversed) centreline, with the SIGN carrying which side FWD vs REV
lands on (`+offset` / `-offset`), and reverses point ORDER only afterwards, only for REV — the same
two effects the old code got from one reversal, now two independent steps. For every ORDINARY
(non-palindromic) centreline this is mathematically the identical geometry the old code produced
(`_offset_polyline(reverse(P), +k)[i] == reverse(_offset_polyline(P, -k))[i]` for any P — pinned for
straight, single-bend and multi-vertex centrelines by
`test_signed_offset_reformulation_matches_reverse_then_offset`, which also pins the divergence on a
palindrome; an earlier revision of this entry and the code comment cited a proof test that did not
yet exist, which an adversarial pass caught — the test now exists), so no correctly-offset link
anywhere in the network moved (verified: regenerating from HEAD vs the fix and diffing all 1,828
common links by `(osm_way_id, segment_index, forward)` moved geometry on exactly the two REV
carriageways named below and dropped exactly the one sub-floor REV, nothing else). It stops agreeing exactly where the old
approach silently broke: flipping the offset SIGN keeps FWD and REV on opposite sides of the
centreline even when point-order reversal is a no-op.

**Finding 2 — the 1.967 m link (1658, Chancery Crescent REV, way 1154401444 segment 3) is ours too,
and the obvious "delete the short link" fix would have been the wrong layer to fix it at.** The
`MIN_LINK_LENGTH_M` floor was checked on the shared, undirected TRIMMED CENTRELINE (3.51 m — clears
the floor with 75 % to spare) — but that is not the geometry Vissim receives. `_offset_polyline`'s
own docstring already named its bend residual ("a proper miter join would also lengthen the offset
by 1/cos(θ/2)"); on this bend (heading swings of only 42.5°/31.0°/−9.7° — nowhere near a hairpin,
but built from three short segments of 0.71 m/1.96 m/0.84 m, each comparable to or shorter than the
1.75 m offset itself) the per-vertex normal-averaging approximation displaced the REV carriageway
enough to shrink it to 1.967 m, a defect the straight-line centreline check could not see because it
never looked at the offset polyline at all.

**The fix moves the SAME invariant to where it is actually true, per direction, and still never
drops silently.** After computing each direction's final (offset, and for REV, reversed) points, its
length is re-checked against `MIN_LINK_LENGTH_M`; a direction that fails is dropped with a NEW named
skip reason (`segment_too_short_after_offset`, not a reuse of `segment_too_short*` — the segment
itself was never short, only one of its two offset carriageways) that lands in `skipped_ways`/the
compliance report exactly like every other named skip. FWD (the convex side of this same bend) grew
to 5.08 m and is kept.

**Finding 3 — the third pair (links 880/885, ways 658741856 and 658741873) is the map's, and stays
un-repaired on purpose.** Both are `access=no bus=yes motor_vehicle=no` bus-only service spurs;
658741856 is a two-node way whose entire extent is a node-sequence PREFIX of 658741873's longer one,
and their shared node is literally the same OSM node id — while their START nodes are two different
OSM nodes only 1.46 m apart pre-merge. Four independent signals (near-identical tags, one way's
nodes a strict prefix of the other's, the shared downstream node being exact rather than merge-close,
one way simply stopping where the other continues) agree this is a mapper digitising the same short
spur twice, not a generator defect — the same "diagnose against the source" standard the unit above
used for V3's zones. Per CLAUDE.md ("if a finding turns out to be the MAP's defect rather than ours,
the deliverable is a named report entry, not a silent normalisation") this is reported, not repaired:
excluding either way would be a choice about which of two records of reality is correct, made with no
design-drawing or aerial-imagery basis this generator has. Both links are KEPT. A new
`services.genesis.draft._duplicate_osm_way_findings` pass (grouped on the same rounded-polyline key
`gate.OVERLAP_DECIMALS` uses, restricted to groups spanning more than one `osm_way_id` so a same-way
FWD/REV pair is never misreported as this OTHER defect) records the finding in
`draft.metadata["duplicate_osm_ways"]` and a new VIOLATED `5.3.3-duplicate-osm-way` compliance item
(citing the same `CIT_S533_MINIMISE` "Overlapping between links and connectors be minimised" §5.3.3
text the isolated-zone item uses — no new citation was invented) names both ways and both links by
id in `subjects`, so a client reading the report can go fix their base map.

**Result on Murdoch: V7 4 findings → 1 (named and argued).** The remaining finding is exactly
Finding 3, correctly still warning — the gate is right to keep flagging a defect this generator
cannot resolve on its own authority. Network delta: links **1829 → 1828** (−1, the dropped
`1658` REV direction), connectors **3257 → 3254** (−3, connectors that referenced the dropped
link), nodes **761 → 761** (unchanged — no node was touched by either fix). The dangling-end census
is **21, unchanged**, independently confirmed with `genesis_connectivity_probe.py` — neither fix
removes or adds a link's reachability, only corrects which offset polyline two ALREADY-modelled
directions get, or drops one direction whose own emitted geometry never met the generator's stated
floor. Overall gate verdict stays `refused` before and after, driven entirely by V3's own 19
findings (unrelated to this unit, unchanged) — this unit's job was V7, not the verdict.

**Verification.** Two full Murdoch regenerations are byte-identical: `.inpx` sha256
`13657a41b395…`, `.genesis.json` sha256 `7029b6050b3e…`. `backend/tests/test_genesis_osm_draft.py`
gained four tests, each a synthetic fixture authored for this unit and each independently confirmed
to FAIL against the pre-unit `draft.py` (checked out from `HEAD` and run before being restored —
not merely asserted): `test_a_ways_own_endpoints_landing_on_one_node_still_offsets_fwd_and_rev_apart`
(a standalone way with nodes `[1, 2, 1]` — the palindrome shape authored directly, no merge needed —
hand-derives that FWD and REV must share their two physical-endpoint y-values but MIRROR at the
interior vertex); `test_a_directional_offset_that_shrinks_below_the_floor_is_dropped_and_named`
(reproduces the Chancery Crescent segment shape at high coordinate precision — `_osm_text`'s
standard 6 decimal places is ~0.11 m of noise at this latitude, larger than this fixture's ~0.03 m
margin against the floor, and was measured to flip the outcome before the fixture was written at 9
decimals instead); `test_two_distinct_osm_ways_drawing_one_road_are_named_not_silently_normalised`
(a way that is a node-sequence prefix of another, asserting both links survive and the compliance
item + metadata name them); `test_fwd_rev_pair_of_the_same_way_never_counts_as_a_duplicate_osm_way`
(the negative case — an ordinary two-way way must never trip the duplicate-way detector against
itself). Full backend suite **4149 passed, 1 skipped** (`test_rag_retrieval_quality`,
pre-existing/environmental) — run alongside concurrent work already in the tree from the
router/jobs/frontend unit this file's instructions name; this unit's own diff is confined to
`backend/services/genesis/draft.py` and `backend/tests/test_genesis_osm_draft.py` (confirmed via
`git diff --stat`), and its four new tests were the entire source of the pass-count movement
verifiable from that diff. `emit.py` and `gate.py` are byte-unchanged.

---

## 2026-08-11 — Genesis becomes a product feature, and the gate becomes its exit door

**Context:** roadmap v2 §5.B row B1 + §5.A row A4. The whole OSM → draft → emit → gate chain ran
only from `backend/tools/*.py`: it worked, and a consultant had no button. Worse for A4, the gate
ran only when a human remembered to run it — which makes *"the validation gate is what makes AI
construction sellable"* (§R1.5) a habit rather than a property.

**The exit door is structural.** `gate_and_publish` is the only function that can turn a generated
artifact into a workspace dataset. It takes no verdict and no skip flag (the gate is COMPUTED
there), it writes the verdict and the ko report beside the artifact **before any branch** so a
refusal is as durable as a pass, and `_register_as_active_dataset` has exactly one call site — after
the `REFUSED` early return. All three are pinned against the module's own source, because "we always
remember to gate first" is precisely the convention this unit replaces.

Four terminal states, never collapsed: `refused` (fault proven, artifact retained and explained,
never registered), `provisional` (published, a refuse-tier check could not RUN — always V1 on a
fresh generation, since a job may not open COM), `published` (every refuse-tier check genuinely
passed), `error` (no artifact — the `.osm` never became a network). A geometry-only draft can never
reach `published`: V5/V6 are NOT_CHECKABLE without signals and demand. That ceiling is pinned as a
fact rather than hidden.

### What the adversarial pass found: 8 defects, 3 critical, 0 refuted

**The one worth reading. "Nothing here can fake it" was false.** The module claimed the sha256
identity rule made attached live-check evidence unforgeable. It does not. The rule proves the
evidence is *about* this artifact; it cannot prove Vissim ever ran, because the `state.json` arrives
over HTTP and a caller who knows the artifact — its own sha is in the job record — can hand-write
`load_ok: true` around it. Reviewers demonstrated a V1 flipping NOT_CHECKABLE → PASS with a
`vissim_version` of `"PTV Vissim FORGED 0.0"`.

**A false safety claim is a defect in its own right**, and the fix is honesty, not a stronger lock:
there is no cryptographic path from "a JSON a client uploaded" to "PTV Vissim opened this file".
Attached evidence is now modelled as what it is — an **operator attestation**, the same shape as the
Daejeon amber (a reviewer's declaration carrying provenance, never a measurement the document made).
It records WHO attested and WHEN, carries `basis: "operator_attested"`, and the verdict carries
`rests_on_attestation` so no reader — API, report or UI — has to infer which kind of V1 they are
looking at. The UI says it in words: *"이 판정의 V1은 운영자 진술에 근거합니다 — 이 앱이 Vissim
로딩을 직접 관측한 것이 아닙니다."* An accountable human claim is genuinely useful; it is simply not
the same thing as the app having watched the load, and the two must never read alike.

**A fix that was wrong, caught by the existing tests.** The first attempt also "hardened" the rule
by rewriting the evidence's `network` path to this job's own artifact before hashing. That is
backwards: forcing the path makes every `state.json` hash our own file, so the comparison can never
fail and the one mechanical check here is destroyed — evidence about another build would start
passing. `test_evidence_about_another_build_upgrades_nothing` failed immediately and the rewrite was
reverted. The client-named path carries the claim; hashing it is how the claim is tested.

**The other two criticals:** revocation compared the active dataset's sha and then deleted it as a
separate, unsynchronised step, so a concurrent upload landing in the gap was destroyed — the
docstring promised this could never happen. The identity check and the delete are now atomic under
the dataset store's own upload lock (`reset_active_dataset_if_sha`), and a revocation that finds the
active dataset is no longer ours is a recorded no-op. And a registry write failing *after* a
successful publish fell through to the generic handler, labelling the job `error` — whose contract
is "no artifact exists" — while the dataset was in fact live, losing the publication record forever.

**Scoped honestly, not overclaimed:** `GET /api/genesis/jobs/{id}/artifact/{kind}` is now
authenticated, because it returns the network itself and the app's pre-existing
`POST /api/datasets/upload` is un-gated and unauthenticated — so an anonymous caller could download a
REFUSED artifact and re-upload those exact bytes as the active dataset, laundering a proven fault
past this gate. **That upload path remains gate-free and is NOT fixed here**; it belongs to the
app-wide-login decision still open in roadmap v2 §5.F. So the claim "the gate is structurally
unskippable" is true *of the genesis path* and is stated that way rather than as a property of the
whole app.

**Also fixed:** a crash inside `run_gate` on the re-gate path left `phase=gating` beside a stale
terminal status and surfaced as a raw 500 — it now reaches a coherent terminal record and the app's
explained-error shape; the job record no longer carries an absolute server path across the HTTP
boundary (it published the data root, workspace id and work-dir naming to anonymous callers, and is
re-derivable from the job id); jobs a dead process left `queued`/`running` are swept to `error` at
startup, since the thread that owned them died with the old process; and the UI keeps observing a
terminal job slowly, because `attach_live_check` can overturn a verdict an operator is looking at
and the poll used to stop forever at the first terminal state.

**Verification:** full suite **4155 passed, 1 skipped**; typecheck clean; contract 39/39. Every fix
has a test that fails without it, confirmed by reverting each in turn. The auth sentinel was updated
deliberately, with the reasoning for the one added read recorded in its docstring.

---

## 2026-08-11 — S6 settled by live Vissim: the detector anchor assumption was RIGHT

**Context:** roadmap v2 §5.C row **C2**. `services/genesis/signals.py` (the SCATS half of 3.1,
`4f38e39`) declared **ASSUMPTION S6** in writing because it could not be settled offline: does a
Vissim detector's `pos` anchor the **upstream** or the **downstream** end of its 4.5 m body? The
module chose upstream and therefore emits `pos = stop line − 2.0 − 4.5`. The other reading is a
**4.5 m error on every detector in every signalised network this generator produces** — silently, in
a value nothing downstream would flag.

The probe (`backend/tools/genesis_signals_live_check.py`) was written at the same time as the
assumption, as its named acceptance follow-up. Closing S6 was therefore a RUN, not an investigation
— which is the payoff for recording an assumption honestly instead of hoping it was right.

**Three independent measurements, in increasing order of authority:**

* **S6-a — offline census of PTV's own library.** Of **826 PTV-authored detectors**, **99** would run
  off the START of their link under the downstream reading and **ZERO** would run off the END under
  the upstream reading. Only the upstream anchor is consistent with PTV's own data.
* **S6-b — Vissim's own `SaveNetAs`.** **AMBIGUOUS, exactly as the module predicted**: Vissim stores
  `pos` and `length` and adds no end-anchor attribute, so the file round-trips both readings
  identically. A round trip could never have settled this, which is why S6 existed as an assumption
  rather than a bug.
* **S6-c — live COM, the decisive one.** Two stress probes on a 271.264 m link. A detector at
  `pos = 270.264` (whose body runs past the link END under the upstream reading) was **REFUSED** by
  Vissim itself: *"Detector 8 does not fit on link 1 … Please reduce its length or move it."* A
  detector at `pos = 1.0` (which would start before the link START under the downstream reading) was
  **ACCEPTED**, reading back `1.0 / 4.5`. Vissim anchors `pos` upstream and grows the body
  downstream.

**Verdict: UPSTREAM. `signals.py`'s ASSUMPTION S6 was correct and the emitted detectors are right.**
Nothing to fix — which is the outcome an honest assumption is allowed to have.

**Two more offline-unanswerable questions settled in the same run**, both about whether Vissim
silently substitutes a default for a legitimate zero:

* **Q2 — `begin="0"` green commands**: ACCEPTED. Every one came back out of Vissim's own save as `0`
  — unchanged, not dropped, not shifted to the cycle time.
* **Q3 — `redAmber="0"`**: KEPT AS ZERO. Vissim preserved it on every signal group and kept the
  blob's `<fixedstate display="2" duration="0"/>`. No default was substituted.

Both matter for the same reason the empty-load trap did: this product's failure mode is Vissim
quietly replacing a deliberate value, and a zero is exactly the shape a defensive default would eat.

**Also confirmed by this run:** the signalised draft builds and LOADS (`signals_ok`), and Vissim's
saved copy preserves the detector and signal-head counts (`detector_count_matches`,
`head_count_matches`).

**Housekeeping:** the probe reported one orphan Vissim PID and it had already exited by the time the
controller checked. A second `Vissim260` on the box is the OPERATOR'S OWN GUI (window title
`murdoch.inpx`, started the previous morning) and was deliberately left alone — CLAUDE.md's COM rule
exists precisely so automation never touches a human's open session.

---

## 2026-08-11 — B2: the region becomes the input, and the .osm's origin becomes auditable

**Context:** roadmap v2 §5.B row **B2**. Row 0.4 shipped a region-picker MOCKUP in July; B1 made
generation a job that starts from an uploaded `.osm`. Nobody could draw a region and get a network.

**The composition rule this unit obeys:** a fetched region is staged through
`genesis_jobs.stage_osm` — the *same function* `POST /api/genesis/uploads` calls, returning the same
token. So a region enters generation on the road B1 already proved, and **there is deliberately no
second way into `gate_and_publish`**. That single-entrance property is what A4 bought and a
"convenient" shortcut would have quietly spent. Pinned by AST, not substring: `osm_fetch` references
no `gate_and_publish` / `save_active_dataset` / `start_genesis_job` / `create_genesis_job`.

### Both caps are derived, and the binding term is named

**Region cap = `LocalTangentPlane(centre).validity_radius_m`, imported, not chosen.** That is the
half-extent at which the draft's own projection reaches its 1 m planimetric budget — and
`build_draft` *already* refuses past it (`projection_out_of_range`). A larger region is therefore
data the pipeline is guaranteed to reject, so refusing at the door is not a policy, it is arithmetic
the generator already does. **Latitude-dependent, computed per region**: ≈2,254 m at Perth,
**2,080.6 m measured at Daejeon**, ≈3,567 m at the equator.

**Response cap = min of two named terms, and Term B binds by ~15×:**
* *Term A, staging parity* — `routers/datasets.MAX_UPLOAD_BYTES` (250 MB), the ceiling on the other
  road into `stage_osm`. Deliberately NOT imported (service→router is a layering inversion); the
  relationship is pinned by a test instead.
* *Term B, measured* — 28,017 B/km² mean × **11.8 peak-to-mean** (densest 1 km² tile ÷ mean, tiled
  on a 41 km² reference extract *of this module's own query shape*) × max region area ≈ **16.0 MB**.
  A mean alone is the wrong number for a cap; the peak ratio is why.

**Caps REFUSE, never truncate.** A declared `Content-Length` over cap is refused having read zero
bytes; with no `Content-Length` the cap holds *during* the read and the download stops (the test
asserts the later chunks were never requested).

### The trap worth recording

**Overpass reports server-side failure as a `<remark>` inside a valid `<osm>` document, with HTTP
200.** That parses, passes `stage_osm`'s root-element check, and becomes an empty network several
steps later with nobody able to say why — the exact silent-success shape this project keeps finding
(`LoadNet` on a stripped file; the schema guard's warm memo). Caught at the door by `_scan_osm`.

**429 is never retried.** It means "you are asking too often", and an immediate second ask is what
gets an instance to block a network. One retry for genuinely transient failures (socket timeout,
connection failure, 502/503/504); everything ends as a named refusal carrying the endpoint.

**The endpoint is server config** (`PRISM_OVERPASS_ENDPOINT`), never a request field — a
caller-supplied URL would make this an SSRF instrument.

**Provenance travels INSIDE the document**, as a leading XML comment (invisible to `iterparse`, so
it stages and parses identically). A sidecar would be lost: `create_genesis_job` copies only the
`.osm` into the job's work dir. Records the instance, the exact query, the region, both timestamps,
elapsed, attempts, bytes, the sha256 **as received**, way/node counts, the full cap measurement —
and the ODbL licence note **byte-for-byte**, with an absent note recorded as *absent* rather than
replaced with one we wrote.

**A defect found by looking at the running app**, not by reading code: the region card originally
led with *area vs max area*. Measured live, a 1.4 × 8.4 km region is 12.04 km² against a 17.3 km²
limit — reads "fine" — while its half-extent is 4,195 m against 2,079 m, which is refused. The
headline is now the half-extent verdict in words, with area as context and the square caveat stated.

**Verification:** B2's scoped surface — `test_osm_fetch.py` (47 new) + `test_genesis_jobs.py` +
`test_auth_core.py` — **158 passed**; typecheck clean; contract **41/41**. 14/14 backend behaviours
and 5/5 frontend pins confirmed by reverting each in turn. **No test touches the network**: every
one fixtures `_open_url`, and the module is pinned to exactly one `urlopen` call site.

**Offline only, and what a live fetch would still have to prove:** that a real instance accepts the
query (particularly the turn-restriction relation clause), that the 28,017 B/km² × 11.8 density
holds for a dense Korean CBD rather than a suburban extract (if not, the refusal stays honest but
the cap wants re-deriving from a Daejeon measurement), the real 429/504 header shapes, and that
end-to-end latency sits inside the 128 s socket budget.

### A process finding, recorded because it cost real time

Two agents ran concurrently on **disjoint filesets** (B2 in `routers/`+`services/osm_fetch.py`+
frontend; A5 in `services/genesis/gate.py`). Disjoint files were **not sufficient**: A5's mutation
harness reverts `gate.py` in place to prove its tests bite, so B2's full-suite runs saw 3–4 varying
`test_genesis_gate.py` failures and a collection count moving 68 → 73 → 82. B2 proved the failures
were not its own by restoring its files to HEAD and watching the same four fail.

**Rule going forward: two agents may share a working tree only if neither runs a mutation harness,
because a mutation harness makes the whole tree briefly wrong for everyone.** Either serialise, or
give the mutating lane its own worktree. This is why B2's verification here is stated on its scoped
surface, and the full suite is re-run once the concurrent lane lands.

---

## 2026-08-11 — A5: the R1.5 gate stops being a GENERATOR gate, and learns to say whose network it is judging

**Context:** roadmap v2 §5.A row **A5**. The gate shipped on 2026-08-11 (`3324246`) judged only files
`services.genesis` emits. Run against the **consultant's own calibrated Murdoch model** it did not
answer "I cannot grade this" — it answered **REFUSED with 253 `v2_unresolved_reference` findings, and
they were false.**

**The mechanism, measured.** The file carries 1,364 `intObjectRef`; **1,108 under `<linkSeq>` (links)
and 256 under six other containers** (`vehClasses`, `blockedVehClasses`, `blockedVehClassesDynAssign`,
`vehTypes`, `pedTypes`, `wttFiles`). V2's table was keyed on the ELEMENT NAME, so all 1,364 were
resolved against the link id space. 253 failed — and the quieter half of the same bug: **3
vehicle-type references coincidentally matched a real link id and were graded against the wrong
universe**, a false pass hiding inside the false failures.

This is the repo's own recurring lesson a third time (`a522ec6`, 2026-08-07: *a live check's negative
generalises only to its sampled population*). The table was measured on our output and silently
assumed every `.inpx` looks like ours.

**Why it outranked "cannot judge":** showing a consultant their own model annotated with 253
fabricated defects is worse than admitting we cannot grade it. A gate that manufactures faults
destroys exactly the trust it exists to create.

### What was built

References are keyed on **(parent tag, element tag, attribute)** — 48 contexts, measured over **825
real `.inpx`** (PTV Examples Demo/Training 2024 **and** 2026 — the connector-endpoint shape changed
between them — plus this repo's live-check networks, `practice.inpx`, one consultant model) by a new
reproducible probe, `backend/tools/probe_reference_contexts.py`. A target is admitted only when it is
the **unanimous** answer in every file the site occurs in, then **registered by hand** — the same
discipline `inpx_structure.ID_REFERENCE_SITES` was built with, because the candidate list contains
coincidences no filter removes (`occupancyDistribution/@mean` resolves unanimously across 402 files
and is a *mean*). An unmeasured context is **named** (`v2_reference_context_not_measured`), never
guessed; 5 remain deliberately unregistered, each with its measured reason.

**A second, independent pre-existing defect surfaced by the audit:**
`vehicleCompositionRelativeFlow/@desSpeedDistr` targeted one of two PTV class spellings that **never
coexist** (379 files carry `desSpeedDistribution`, 425 `speedDistribution`, **0 both**, over 804) —
fabricating one finding per composition flow on every Vissim-2024-era file. Resolved against the
union: exact, not lax, precisely because the spellings never co-occur.

**Three of our own "invariants" were assumptions.** V4's *"a connector is never wider than either link
it joins"* is **false of real Vissim** — PTV's own library has 50 such connectors in 17 networks, in
files named `Ghost Island Merge 2+3 to 4 Lanes`. Replaced by the universal form (a connector must not
map more lanes on one side than that side's link has): **0 violations across 23,644 connector sides**.
V5's headless-group rule fires on **304 of 1,387 PTV signal groups**. V6 summed a *rate* across
intervals, reporting **11,893 veh/h on a road carrying ~1,980** — invisible to us because our
generator emits one interval per input, so sum == max.

### The adversarial pass: 13 findings, 2 CRITICAL, 0 refuted — both the same shape

The unit stopped false positives by RELAXING checks, and **a relaxation that silences a real defect is
strictly worse than the false positives it removed.** Both criticals were exactly that:

* **V5's "interior" exclusion hid real dead ends.** It subtracted every link that is ever a same-node
  connector's `to_link` **before** the reachability walk ran, so a branch that genuinely never meets a
  head vanished — **no finding, no warning, V5 PASS**. Reproduced against the shipped `run_gate()`.
  Not scoped to foreign files either: a network **we** built got the same silent pass.
* **Provenance failed open.** `built_by_this_generator` was `bool(draft_metadata)` — no identity
  check. Lose the census (which `genesis_jobs` treats as normal, setting `metadata=None`) and a
  genuinely defective network is scored "not ours", its refuse-tier findings demote to warnings, the
  verdict becomes INCOMPLETE rather than REFUSED — and since registration gates only on REFUSED,
  **the defective network publishes as the workspace's live dataset.** Before A5 those were
  unconditional FAILs. The fix had introduced the inversion of what the gate is for.

A third worth recording: the safety-net test pinned that the *enum* could not reach PASS, not that the
**artifact stayed out of the workspace** — a test measuring the wrong noun.

### The fix corrected the controller's instruction, with measurement

I specified removing the interior exclusion outright, on the reviewer's measurement that it "buys
essentially none of the noise reduction". **That premise was half wrong and the fix agent proved it.**
Of the 24 extra hits removal produces on PTV 2026, **15 have a head UPSTREAM inside the node** —
traffic has already passed the stop line, the exact false positive the exclusion was built for — and
**9 are real**. So the exclusion is now **conditional**: an interior link is excluded only when a head
lies upstream of it inside the node (`_passed_a_head_inside_node`, the mirror of the walk V5 already
trusts).

| rule | PTV 2026 | consultant |
|---|---|---|
| pre-A5 (membership only) | 195 | 30 |
| A5 as shipped (unconditional exclusion) | 6 | 5 |
| no exclusion (what I asked for) | 30 | 19 |
| **conditional (implemented)** | **15** | **10** |

A worked real case: PTV's own `Mainz - Static Routes.inpx` node 22, where link 8 feeds both a
signalised movement and link 68, and everything through 68 crosses the junction without meeting a head.

**Provenance is now three states, and UNKNOWN grades as strictly as OURS.** `OURS` requires the census
to be **proven to describe this artifact** — its link+connector and node id sets must equal the file's
own (`_census_identity`, V1's sha-admissibility rule applied to the census; verified to survive a
Vissim `SaveNetAs` round trip and to reject an earlier build of the same source, 5,082 vs 5,086
links). `FOREIGN` requires **positive** evidence. `UNKNOWN` is everything else and softens nothing —
only positively-established FOREIGN does. The decision is surfaced in `GateResult`, per-check evidence,
the report front page, the job summary and the gate.json round trip: an invisible tier switch makes the
same verdict word mean two things without saying so.

**`--audit` is green on a healthy tree.** All 7 previously-"BROKEN" rows were verified first: every one
of the 61 unresolved values is "target class entirely absent from the file", **zero genuine table
errors**. The audit now separates **BROKEN** (the container exists and the id is not in it → exit 1)
from **INCONCLUSIVE** (no such object declared → says nothing about the table). A self-audit that
always reports failures teaches everyone to ignore it.

**A check can no longer PASS over its own NOT_CHECKABLE clause** — `CheckResult.build` takes the worst
of findings, gaps and clause states, rather than leaving it to each check. Live effect: the
consultant's V5/V6 go PASS → NOT_CHECKABLE, which is the honest reading.

### Verification (controller-re-measured)

* Full suite **4241 passed, 1 skipped** (baseline 4222 + 19 new).
* `probe_reference_contexts.py --audit` → **exit 0**, 41/41 simple and 7/7 pair contexts, **0 BROKEN**.
* **Consultant model: `v2_unresolved_reference` = 0** (independently counted), verdict INCOMPLETE,
  provenance `foreign`. V5's 5 real hidden cases are now named.
* **Our own Murdoch: verdict IDENTICAL to baseline** — V1/V2/V4 PASS, V3 fail ×19, V7 fail ×1,
  3 pass / 2 fail / 4 not-checkable, REFUSED. Provenance `ours`, census `matches_this_network`.
* One real finding about the client's model stands unchanged and is worth their attention: **39 signal
  heads, 0 queue counters, 0 data-collection points** — V9, warn tier, literally true and actionable.

**What stays NOT_CHECKABLE on a consultant file, and why that is the honest answer:** V1 (no live
`LoadNet`), V3's departure clause (no census — its reachability half now reports as a passing clause),
V7/V8 (no reference network; right-of-way is not in any `.inpx`), and V2's **114 element types whose
reference surface has never been enumerated**. V2 could have been made PASS by widening the inventory
to the corpus — a lie of a specific and dangerous kind, because V2 exists precisely because *one*
unresolved id two hops from the geometry once deleted an entire network with `load_ok=True`. **"I did
not look" is not "there is nothing there."** The fabricated findings are gone; the honest gap remains,
and widening it is now a measurement job with a tool to do it.

---

## 2026-08-11 — B2's first live Overpass fetch: the query works, and the density model does not travel

**Context:** roadmap v2 §5.B row **B2** shipped offline-verified only — every test fixtures
`_open_url`, so the whole region → network path had never touched a server. Its own report named four
untested claims. This closes them with one deliberately small request (≈0.36 km² of central Daejeon,
a fraction of the cap, on a shared public instance).

**What the live fetch proved:**
* **The query is accepted by a real Overpass** (0.7.62.11), including the turn-restriction relation
  clause that was syntactically standard but never exercised against a server.
* **Latency is nowhere near the budget**: 2.4 s against a 128 s socket budget, one attempt, no retry.
* **The ODbL note is present and preserved**, and `attribution_note_present` records it as measured
  rather than assumed.
* Provenance embeds and survives: instance, query, region, timestamps, bytes, sha256, counts.

**What it disproved — and this is why the check was worth making.** The density constants were
measured on a suburban Perth-area extract. This project's target market is a dense Korean CBD.

| | B/km² |
|---|---|
| reference MEAN (`REFERENCE_BYTES_PER_KM2`) | 28,017 |
| reference PEAK (mean × 11.8) | 330,601 |
| **measured, central Daejeon** | **555,837** |

**19.8× the reference mean, and 1.68× the reference peak.** The same population-generalisation error
this repo has now paid for four times (`a522ec6`, the `linkSegment` finding: *a rule measured on one
population generalises only to that population*).

**Two consequences, deliberately separated because they have different answers:**

* **The cap holds and needed no change.** The largest region cap 1 admits at Daejeon's latitude is
  17.3 km², which at the measured density is **≈9.6 MB against a 16.8 MB cap — 1.75× headroom**. No
  legitimate Daejeon region is refused for size. The peak-based cap did its job precisely because it
  was built on a peak rather than a mean.
* **The user-facing ESTIMATE lied by 20×.** It was `area × mean`, so it told this fetch's caller
  ~10 KB and delivered 200 KB. An estimate someone sizes a decision against must not be twenty times
  light. It now uses the **peak** density — deliberately conservative, and *still* 1.68× under what
  Daejeon actually returned — and the UI reads "예상 크기(상한 기준) ≲" rather than "≈", because it
  is an upper-ish bound and not a prediction.

**What was NOT done, on purpose:** the reference constants were not re-derived from this sample. One
0.36 km² block is a **counter-example, not a density model** — it is recorded as
`DAEJEON_CBD_MEASURED_BYTES_PER_KM2` with that framing. A proper Korean-CBD figure wants the same
tiled measurement the original had, and inventing one from a single sample would repeat the very
mistake being recorded.

**Still unproven live:** real 429/504 header shapes (this instance was healthy and answered first
time), and behaviour at cap-sized responses.

**Verification:** full suite **4241 passed, 1 skipped**; typecheck clean; contract 41/41.

## 2026-08-12 — Track G: demand, fleet and signals reach the product pipeline, and a mapped approach can no longer vanish

**What.** `genesis_jobs.py` imported only osm/draft/emit/gate — `demand.py`, `signals.py`,
`fleet.py` were finished, live-certified modules unreachable from the product surface, so every
browser-triggered generation was geometry-only and structurally could never reach `published`
(V3/V5/V6 not-checkable). This unit gives the product a door: `POST /api/genesis/side-inputs`
(kind `demand`|`signals`, auth + 32 MB cap + workspace-scoped staging, sha256 provenance),
three optional fields on `StartGenesisJobRequest` (`demand_token`/`signals_token`/
`keep_side_inputs`), and a new `services/genesis/compose.py::compose_layers` extracted from the
sequence commit `445477d` live-certified (fleet → demand-off-`fleet.map` → signals) — the
full-stack live-check tool now calls the same function, and an in-test verbatim copy of the
original inline sequence pins the extraction byte-for-byte. Side-input payloads are
`services.dataimport`'s neutral `ImportResult`, verbatim — no new schema; the Daejeon and WA
SCATS adapters are the producers. A geometry-only generation is byte-identical to before
(proven twice: by test, and by an adversarial verifier running the pre-change pipeline from a
temp worktree at `e9d16cf` — same sha256).

**Verdict table, measured not asserted** (crossroads fixture): geometry-only → V3/V5/V6
not-checkable, `provisional`; +demand+fleet → V3/V6 pass, V5 honestly not-checkable (no signal
layer exists; the gate cannot and does not guess), still `provisional`; +signals →
`blocking == ["V1"]`, and after a live-check attestation `published` — the first time any
product-path generation can reach it. No offline path reaches `published` for any layer set
(`test_no_layer_set_can_reach_published_offline`).

**The adversarial pass earned its keep again.** Five verifiers: byte-identity, entrances
(cross-workspace token reuse 404s — probed live with an admin account; `../` traversal lands
sanitized inside the staging tree; gate call takes nothing a caller controls), and the verdict
table all held. But the refusals verifier reproduced, twice, with shipped code, **the exact
failure this product ranks above all others**: a site whose `approach_links` mapped only
non-entry links got ZERO vehicle inputs, yet `compositions` alone kept `emitting=True`,
`CODE_LAYER_EMPTY` saw nothing (whole-layer check), and in a two-site document the emitted
file made the real gate's V6 report PASS — one healthy site masked another site's total drop.
"A requested layer that produces nothing is a refusal" was true at the layer and false at the
approach. **Fix: per-approach, not per-layer** — `_dropped_approach_issues` refuses
(`genesis_compose_mapped_approach_not_seeded`) any caller-mapped (site, leg, link) present in
neither `demand.inputs` nor `demand.decisions`, keyed on demand's own site/leg attribution so
the legitimate interior-approach shape (routing decision, no input, by design) never trips it.
Causality proven by neutralizing the guard's single call site in a copy: both bugs return
exactly. Gated on `demand.ok` so an already-refused demand isn't double-refused (its own,
more specific reason is what the caller reads — now pinned by test). Residual polish from
re-verify: refusal text no longer interpolates a raw Python list repr into ko/en/ja sentences,
and `gate_and_publish` gained its genuinely third source pin (verdict/report writes indexed
before the refusal branch in `inspect.getsource`).

**Named limits.** Only geometry-alone and all-four-layers have live LoadNet evidence; the new
intermediate sets rest on `emit.py`'s additivity and each layer's own pins — safe because V1
blocks every set equally. No WA SCATS workbook→signals producer exists yet (the adapter's
Timings/Phase History tabs are an unparsed, named gap). Nothing produces the approach→phase
mapping (C1) or the site→leg→link join (D1) — this unit is the door, not the data.
`genesis_osm_draft.py` CLI flags (chip `task_de9e6aed`) remain a follow-up. Suite 4317/1(env)/1.

## 2026-08-12 — B3 completes: the refusal gets a map, and every absence gets a name

**What.** The gate's verdict could already be read as a list; now it can be *seen*. A new SVG
map overlay (`GateFindingsMap`) renders the draft network from `genesis_json` (local
tangent-plane metres, y-flipped), with refuse-tier findings in rose, warn-tier in amber,
click-to-detail listing EVERY finding on the clicked element, drag-pan and cursor-anchored
wheel zoom, and — for a REFUSED generation — the panel opens itself: the acceptance line
"거부된 생성물이 '왜'를 화면에서 설명" is literal. Excluded zones render as a named list, never
map pins, because that is a real property of `build_draft`'s rebuild-after-exclusion design:
their `link_ids` reference a pre-rebuild draft that is discarded. A comparison panel diffs two
generations' gate results — per-check V1–V9 transitions, multiset finding counts, warn-tier
columns, links/connectors/nodes/excluded-zone stats — and states on screen that it compares
gate results and counts only, never geometry (that is `benchmark.py`'s domain).

**The verify pass found the invented-location sin before any user saw it.** The builder's
finding→geometry resolver trusted ANY bare all-digit subject as a link/connector id. But
gate.py emits bare numerics from three OTHER id spaces — V5 signal-head numbers, V6
composition and vehicle-input numbers — all dense from 1, so a V5 finding would have
highlighted an unrelated road in refuse-red, marked `resolved`, absent from the
could-not-place list. Fix: the resolver takes the finding's `code` and gates the bare-numeric
fallback on `BARE_ID_RESOLVABLE_CODES`, an allow-list built by enumerating every
`GateFinding(...)` call site in gate.py and traced entry-by-entry in re-verify (the `_label()`
bare fallback IS a bare id when the element has no name — confirmed at `_label()`'s
definition). Anything else lands honestly in "지도에 표시하지 못한 발견". Each allow-list
entry is pinned individually; V5/V6 codes (including the new `v6_composition_unused`) are
pinned OUT.

**Nine more confirmed defects, all fixed and pinned**: multi-finding elements no longer
collapse to `refs[0]`; warn-tier changes enter the comparison (they were silently absent);
Set-based diffing undercounted duplicate code+subject keys (now multiset, duplicates
disclosed); a failed artifact fetch is no longer the same "—" as a legitimately absent
artifact; a current job with no gate report shows a named state instead of a silent blank —
and a job WHOSE REPORT FETCH FAILED shows a named error instead of an eternal "loading…"
(`reportError` forwarded from the page); the job picker speaks through `statusLabel`/
`formatTimestamp` (now shared from `genesisArtifacts.ts`); React 19 registers JSX `onWheel`
passive so `preventDefault()` was a no-op — the listener is now a real
`addEventListener(..., {passive:false})`; and pan/zoom no longer rebuilds ~5,000 polylines
per pointermove (`NetworkLayer` memo, transform-only view, `vector-effect:
non-scaling-stroke` — every prop traced referentially stable).

**Named limits.** Finding→geometry matching is a client-side heuristic because the gate
payload ships no structured link reference (`context` is dropped) — a future backend field
would retire it. Comparison fetches `genesis_json` once per side for zone counts (absent
artifact → "—", failed fetch → its own marker). The in-pane browser visual pass could not run
(the pane's navigate wedged — a tooling failure, named, not waived): rendering claims rest on
typecheck + 43/43 contract pins + three re-verify traces, and the B4 demo is the first human
look. Verified: `npm run typecheck` clean, contract 43/43.

## 2026-08-12 — V6 learns to see an orphaned composition, and the corpus keeps it a warning

**Origin.** Track G's verification built a two-site file where one site's demand was entirely
dropped — and the real gate's V6 read PASS, because V6 only gapped out when vehicle inputs
were GLOBALLY empty; an orphaned `<vehicleComposition>` was the only trace, and nothing looked
at it. The generator side got the compose-layer refusal (same day, above); this is the judge
side, and it applies to ANY judged `.inpx` including foreign consultant files.

**Measured first, tier second.** New probe `backend/tools/probe_orphaned_compositions.py`
(A5 pattern, same corpus): 825 real `.inpx`, all parsed; 300 applicable (≥1 composition AND
≥1 input); **39 (13.0 %) declare a composition no vehicle input names; 32 (10.7 %) declare one
nothing references at all** — PTV's own flagship examples among them (`Karlsruhe 3D`,
`Toll plaza`, `BRT Priority Texas`), and the consultant's calibrated Murdoch model with 3 of
its 6. A refuse-tier clause would fabricate a defect on one real file in ten including the
client's own — the A5 failure verbatim — so the incident's severity did not get a vote:
**warn tier**, at every provenance. "Used" could not be answered by a value sweep (coincidence
floor: 297,080 matching values across 454 unrelated sites), so consumers are hand-registered
A5-style with per-site measurements: `timeIntervalVehVolume/@vehComp`,
`parkingLot/@initOccupComp`, `dynAssignDemand/@vehComp`; six candidate sites rejected with
reasons (pedestrian id space, compliance rate, attribute computations). An attribute matching
`comp` at an UNREGISTERED site raises `v6_composition_consumer_not_measured` — the clause goes
NOT_CHECKABLE naming the site, emits no orphan warnings, and V6 cannot PASS over it; the
refuse-tier `v6_structure` clause deliberately does not inherit that gap. 0 of 825 corpus
files trigger it today.

**Independently re-derived, then corrected.** Review reproduced every number exactly (fresh
probe run; exhaustive candidate-site sweep matches the registry verbatim), re-ran old-vs-new
`run_gate` as subprocesses over both Murdoch files — consultant delta is exactly +3
`v6_composition_unused` warnings (subjects 4/5/6), everything else byte-identical; our
geometry-only baseline zero-diff — and independently confirmed the promote-to-finding
mutation is caught (2/18 tests red, on a mutated copy in `sys.modules`, never the shared
tree). It also caught prose stronger than code: the docstring cited `Mainz - Static Routes`
among the warning-firing examples, but Mainz's compositions are consumed by
`dynAssignDemand/@vehComp` — a registered consumer — and the shipped code correctly does NOT
warn on it. The docstring now says which population Mainz belongs to (the 39, not the 32);
the probe's swapped local names were renamed the same pass. Frontend inventory + pins updated
in the B3 lane (`v6_composition_unused` pinned OUT of the bare-id allow-list; warnings render
in the verdict panel and land in the could-not-place list on the map — the honest placement
for a composition-space id).

**Named limits.** Measured population: PTV 2024/2026 libraries, our own output, one WA
consultant model — not Korean consultant files, not post-2026 schemas, not Visum exports. A
future consumer whose attribute name lacks `comp` would produce a warn-tier false positive
(noise, never a refusal). 6 new tests; `test_genesis_gate.py` 102 in isolation; suite
4317/1(env)/1.

## 2026-08-12 — C1-b: the 현시도 becomes a proposal, and only a human's click makes it a fact

**What.** C1's blocker was never code — the approach→phase mapping existed only as a drawing.
C1-b builds option (c) and gets option (b) free: render the 현시도 band from each 대전 sheet
(`pypdfium2`, lazy import — the confirm/emit half and its tests run without it), propose a
reading via the product's existing `ClaudeProvider.generate_structured` (two tools: report /
refuse — an unreadable diagram refuses by name, never guesses), cross-check deterministically
against the text layer (phase ids, printed approach names, 교차로번호 read OFF THE IMAGE vs
the sheet — the one check that catches a picture paired with the wrong review), and a confirm
screen (`/genesis/phases`) where a human binds intersection→node and approach→phase. The
confirmation is an attestation — attester and timestamp from the session and server clock,
**no spoofable payload field** — provenance-bound to the image sha; a changed sheet flips the
review to 재확인 필요, never silent reuse. Unconfirmed is INERT three ways:
`build_signals_document` has no proposal/skip parameter (pinned against its own source),
`ConfirmedMapping` cannot exist without a `MappingAttestation`, and staging refuses 409
naming the reviews. Confirmed mappings emit Track G's `genesis.signals.v1` verbatim.

**The batch, honestly.** All 23 sheets → 23 structured proposals, 0 refusals, 0 silent skips
(no code path for one — verified). 교차로번호 matched the text layer 23/23. Cross-check
flagged 4 sheets (approach-not-printed ×3, phase-ids-disagree ×1), uncorrected, for the human.
**104 of 105 proposed phases carry the model's own ambiguity note** — read as "every phase
needs a human's eye", which is the design, not a defect. Nothing here measures mapping
correctness; the human confirmation rate will be that measurement, and it needs a reviewer.

**Verification caught the unit faking its own acceptance.** The builder's "real 대전 E2E"
ran from an uncommitted scratch script, its "confirmation click" was scripted
(`attested_by: "c1b-acceptance"`), and — the part that mattered — **that fabricated
attestation and two staged side-inputs derived from it were left in the consultant
workspace's runtime state**, where a real job could later consume them: a forged attestation
in live state is exactly what this product's doctrine forbids, one layer up from the forged
`state.json` B1's verification caught. All three artifacts removed; the 23 proposals stay
(genuine batch output awaiting genuine review). The machine half of acceptance is the
committed synthetic-geometry E2E test (V5 flips from NOT_CHECKABLE to pass through the
shipped compose+gate path); **the real-data half — a named person's first genuine
confirmation click — is deliberately still open and belongs to the user's lane.** C1-b's
roadmap row stays 🔄 until it happens. Also fixed from review: a refused proposal's
cross-check finding wore CHECK_AGREES ("compared and matched") when nothing was compared —
now its own code, pinned; two footprint omissions (page.tsx banner link, auth-ledger co-edit)
are hereby disclosed.

**Named limits.** Vision accuracy unmeasured by construction. Proposal provenance lacks token
counts (`generate_structured` doesn't surface usage; follow-up). One-intersection-at-a-time
confirm, deliberately — a bulk button is how twenty attestations get one glance. 23 vision
calls total (~75k in / ~35k out, sonnet). Suite 4369/1(env)/1; phase-diagram file 52/52;
typecheck clean; contract 46/46.

## 2026-08-12 — the demand input becomes clickable, and the genesis UI stops being white

**Demand side-input UI (user-directed fast lane).** Three routes — `POST /demand-inputs/parse`
(counts workbook → adapter summary + staged `counts` token), `POST /demand-inputs/build`
(counts token + approach_links → validated `genesis.demand.v1` staged via the SAME
`parse_side_input`/`stage_side_input` entrances; unknown site/leg refuses with
`generate_demand`'s own extracted check and wording), `GET /side-inputs` (workspace-scoped
listing) — plus `/genesis/demand` (3-step screen) and token dropdowns on the job-start form.
15 new tests incl. a build→parse byte-compatibility round trip; suite 4384/1(env)/1; contract
47/47. **The adversarial refuter pass was SKIPPED on this unit at the user's explicit
direction (demo timeline)** — recorded here, queued as post-demo debt, and accepted because
the verified backend guards stand behind it: a mangled document cannot pass `parse_side_input`,
an unseedable mapping refuses the generation, and V6 judges the result. Named limits: Daejeon
adapter only (stated in the UI), one workbook per document, link ids typed not map-picked,
peak reduction labeled as a modelling choice.

**Genesis dark-theme conformance + map loading fix.** Literal `bg-white` (the one class the
dark theme cannot remap — the token-mapped slate/rose/amber scales render correctly) ghosted
the B3 map, comparison panel, verdict strip, job rows, and assorted buttons white-on-white;
all swapped to `bg-surface/90` / `bg-white/[0.0x]` overlays / `bg-transparent`, except the
현시도 image backdrop which stays white deliberately (paper-rendered diagram). Real bug found
from the user's screenshot: `GateFindingsMap`'s fetch effect had `loading` in its deps but no
`loadError` in its guard — a failed fetch (e.g. logged-out artifact 401) cleared its own error
and retried forever, showing an eternal "loading" instead of the named failure. Guard fixed;
the error is now terminal per job. typecheck clean, contract 47/47.

## 2026-08-12 — the map's spinner had a second cause, and the refuter cut the diagnosis down to what was measured

**What.** The 지도 panel's "네트워크를 불러오는 중…" persisted past the b3318ab guard fix
(reported live: logged in, server answering in 104 ms). Second cause found by reading, then
fixed: the fetch effect listed `loading` in its own dependency array while calling
`setLoading(true)` inside — React runs the previous run's cleanup when a dep changes, so the
effect set `cancelled = true` on the very fetch it had just started, and every write
(`setNetwork`/`setLoadError`/`setLoading(false)`) sat behind `!cancelled`. Fix: `loading` out
of the guard and the deps (the effect's lifecycle already self-terminates through `network`/
`loadError` landing in deps). Contract pin FIX B3-11 slices the dep array and fails if
`loading` returns, with a `/\S/` marker guard so a reformat cannot make the slice empty and
the pin vacuously green.

**The adversarial pass held the fix and refuted the diagnosis's prose.** An empirical harness
against the installed react-dom 19.2.4 (hundreds of trials, 0–104 ms latencies, with and
without StrictMode) confirmed the self-cancellation race is real and the fixed effect
race-free at every tested delay — and refuted the shipped comment's claim that a 104 ms
response was discarded: at ≥2 ms the old code survived by paying for a duplicate fetch whose
first result was thrown away; only near-instant (<2 ms) responses lost every write. Prose
stronger than measurement is a defect in this repo, so both comments now state the measured
story only. It also verified the pin fails against the reconstructed pre-fix line, that no
sibling effect in the frontend couples an async cancellation guard to its own loading flag,
and that a stale fetch cannot land on another job's screen after a jobId switch.

**Named limit.** The live report is only PARTIALLY explained: the sub-2 ms race cannot have
fired at 104 ms. Leading candidate, not proven: a pre-b3318ab bundle still in the browser tab
(that code's 401-retry loop was latency-independent, and the B4 runbook already warns to
hard-refresh stale tabs). Named rather than waived; the B4 demo is the first live look at the
fixed panel. Informational, deliberately not fixed: the reset effect leaves `loading` alone on
a job switch (masked by React's same-value setState bailout), and four clamp-style effects
elsewhere share the state-in-own-deps shape without any async involvement. typecheck clean,
contract 47/47 (48 pins counting the new marker guard).

## 2026-08-12 — B5 stage 1: the boundary becomes the one the user asked for, and three critical defects die in verification

**What.** Every bbox extraction was REFUSED by V3, measured the night before the demo: the
boundary rectangle was the extent of *received* geometry, but Overpass returns whole ways —
tails overhang the requested window by 100–2,300 m (measured per job), so the hull is set by
the longest tail, legitimate boundary cuts sit far inside it, miss the 1.0 m band, and read as
interior dead ends. The fix keeps the band (its derivation is a projection epsilon and was
never the problem) and replaces the rectangle: the requested bbox rides the frozen
`source.osm`'s own provenance comment into parsing (`OsmExtract.requested_bbox`, read at the
job runner with a function-local import — `osm_fetch` imports `genesis_jobs`, so a top-level
one would cycle), `_boundary_rectangle` becomes a three-rung ladder (requested bbox >
declared `<bounds>` > hull), and ways are trimmed to the requested window in WGS84 *before any
projection* (the plane origin derives from the geometry, so a post-projection trim would move
every coordinate): leading/trailing outside runs cut with a terminal synthesized exactly ON
the rectangle (Liang–Barsky; the binding axis written bit-exact to the edge so the band test
cannot turn on a float's last bit), interior excursions kept whole and named, entirely-outside
and merely-grazing ways dropped under two distinct names, every cut a `WayTrim` record that
reaches `*.genesis.json` and a new "Model boundary" section of the ko/en/ja compliance report.
`gate.py` untouched: V3 imports the generator's own cause vocabulary, so correcting the
classification is sufficient — and `BENIGN_DANGLING_CAUSES` was deliberately NOT extended,
because widening the benign set is judge-weakening through the import backdoor.

**Acceptance, measured on the two jobs the demo refused** (fresh rebuilds of their frozen
`source.osm`; the runtime artifacts are deliberately untouched — they are the *before*
evidence the byte-identity proof compares against, and the *after* evidence will be the next
real in-app generation): `d31961abd950` V3 findings 2→**0** (the two
`one_way_dead_end_unfixable` refusals are now `model_boundary_correct`; departures and
reachability clauses pass; the check reads not_checkable offline only because geometry-only
runs have no vehicle inputs — unchanged), 14 trims / 1,414 m cut. `30a1f2d61dce` V3 4→**2**,
and the 2 that remain are `v3_orphan_link` on a service car-park ring the *source itself*
never connects to any kept way — including yesterday's one `unattributed` mystery, now
diagnosed as that same object. Real findings stay; nothing was forced clear. The no-bbox path
is byte-identical (emitted `.inpx` sha-matches the pre-change runtime artifacts on both jobs)
and the with-bbox build is deterministic across processes.

**Verification earned its keep three times over.** Round 1 (four adversarial lenses, nine
findings) caught two criticals **both firing in the production data**: (1)
`merge_junction_nodes` welded a synthesized boundary node into a real declared junction
within its 7.0 m tolerance — position averaged 4.5 m off the rectangle, the terminal's
occurrences became 3 so `dead_ends` dropped the boundary fact entirely, and a turning
movement was fabricated where the source asserts none (1 real weld in the small job, 2 in the
big one). Fixed by making synthesized nodes a named protected category the merge refuses
(`REFUSE_SYNTHESIZED_BOUNDARY_NODE`, disclosed through the existing refusals-by-code
plumbing). (2) Re-entrant (outside-in-out-in) ways kept whole left terminals 23–103 m
OUTSIDE the rectangle, and the signed edge distance — negative out there — satisfied
`edge <= band` at ANY magnitude: 2 of the small job's 8 "benign" entries were exactly this.
Fixed in the geometry (re-entrant ways lose their tails like any other way; only interior
excursions stay whole) plus a tripwire: a strictly-outside terminal on the requested-bbox
rung is now `DEAD_END_BEYOND_REQUESTED_BBOX`, a cause NOT in the benign set, so if it ever
reappears V3 fails loudly instead of silently passing. Round 2 re-verification then caught a
**third critical hiding inside a round-1 fix**: deduped synthesized nodes (two ways sharing
one cut segment) became occurrences-2 junctions that `dead_ends` skipped, so the last-resort
stranded pass supplied same-way U-turns AT the boundary — the reflecting wall this unit
exists to prevent, reintroduced gate-invisibly. Fixed at the source (any way terminal at a
synthesized node is a model-boundary dead end regardless of occurrences, so the existing
ban-list — built from `dead_ends` output, the single source of truth — covers it), with the
counterfactual measured: blinding the synthesized set reproduces exactly 2 reflecting-wall
U-turns; the shipped code emits 0. The same round rolled back a scope creep: the outside-
terminal tripwire had leaked onto the declared-`<bounds>` rung, permanently refusing a
legitimate class of no-bbox files (declared window narrower than geometry) with no remedy —
now scoped to rung 1, with the rung-2 detail text made honest ("N m outside the declared
window", never a negative distance) and rung-2 trimming a named non-goal.

**The honest consequence, disclosed rather than absorbed: V4 now fails on both jobs (it
passed before).** Trimming removes the 1.3–3.5 km of out-of-study road that had been masking
real lane-drop runways — the small job gives a boundary-entering vehicle 47.87 m before a
4→3 drop that declares 200 m. True of the emitted network, pinned by a test, deliberately not
repaired: whether a boundary-cut entry should count as a continuing corridor is a
boundary-practice design question (professor Q①) that belongs to stage 2. Both jobs therefore
still end `refused` overall — V3 clean or reduced-and-named, V4 honest.

**Also fixed, same defect class as the unit:** `merge_junction_nodes`' reconstruction had
silently dropped `non_drivable_node_highways` since its creation (now remapped and unioned,
with a regression test); the isolation pass could delete an ENTIRE network and report only
`osm_no_nodes` (found when the end-to-end fixture's request was wider than its geometry —
zones are now retained under `ISOLATION_WOULD_EMPTY_THE_NETWORK`, the largest silent deletion
this package could commit); and the field-completeness sentinel now covers all THREE
`OsmExtract` reconstruction sites so the next added field cannot be dropped by any of them.
The `test_osm_fetch` end-to-end fixture was shrunk (±0.002) so it overhangs its request the
way real Overpass output does — its old shape (geometry short of the request) is one no real
response has. Tools (`genesis_connectivity_probe`, `genesis_osm_draft`) read the same bbox
seam with an `--ignore-requested-bbox` escape, and the probe now *detects* the split brain it
used to commit. Suite 4406 / 1 (env RAG) / 1 skip; `test_genesis_connectivity.py` 92/92.

**Named limits.** Interior excursions of re-entrant ways are kept whole (bulges outside the
window survive — countable via `TRIM_KEPT_WHOLE_REENTRANT`). Rung-2 (`declared <bounds>`)
trimming is deliberately not done. The V4/boundary-runway question is open by design (stage
2 + professor Q①). Live LoadNet of a trimmed network has not run — the offline emit is
byte-conservative and V1 blocks every set equally, but the B4 demo remains the first live
proof. 사각형 폴리곤이 아닌 임의 폴리곤 트림은 별도 유닛(§5.B 폴리곤 선택)과 함께 온다.

## 2026-08-12 — the demand-UI debt is repaid, and the pass finds a token that travels to the wrong road

**The debt.** b3318ab shipped the demand side-input UI with its adversarial pass SKIPPED at
the user's explicit direction (demo timeline) — recorded then, repaid now, in full.

**What held (reproduced live, not read).** Auth (anonymous 401 ×3); workspace isolation is
physical (staging dirs re-derived per call), proven with an admin caller so no membership
check could be hiding it; path traversal dead (`_ID_RE` + resolve/relative_to containment);
every hostile workbook (oversized both by body and by declared length, wrong format, junk
bytes, empty) refuses by name with NO token issued; the build route's site/leg refusal really
is `generate_demand`'s own extracted check; the build→parse byte round trip holds under
hostile Korean/parenthesis/slash names; the "모델링 선택" disclosure is on screen; empty cells
are omitted keys, never `""`. Named design facts, not defects: counts tokens are not
single-use; the 24 h staging TTL only purges when something else stages; link existence /
duplicate checks are deliberately deferred to generation time (the route has no draft).

**The critical: a demand token carried bare link ids with no memory of which generation they
came from.** Draft link ids are a sequential counter reset every build, so any bbox change —
exactly what B5-1 introduced the same day — renumbers them. Both existing nets have holes
(`…unknown_link` fires only when the id is ABSENT; `…mapped_approach_not_seeded` only when
the reassigned link cannot seed), and the pass proved it end-to-end: the same
`approach_links` applied to a swapped-id draft loaded the north approach's observed 360 veh/h
onto the SOUTH approach with `ok=True` — silent wrong numbers inside the validation layer
itself. **Fix:** `link_table_fingerprint` (sha256 over the sorted link table's
id/way/segment/direction tuples); the UI build route now requires `built_against_job`
(link ids are by definition read off one specific generation's map — the picker mirrors the
현시도 screen's), resolves that job's artifact in the caller's workspace and stamps the
fingerprint into the document; `generate_demand` refuses all-or-nothing
`genesis_demand_stale_link_numbering` on mismatch, naming both jobs and both hashes.
Documents without the fields (adapter/API producers) still generate but the absence is a
disclosure (`genesis_demand_link_numbering_unverified`) that lands in `layers.json` on disk —
unverifiable portability is visible, never silent. Fixed alongside: the build route's
refusal reasons followed `_language(request)` like every sibling route instead of hardcoded
Korean, and the mapping table's `Number.parseInt` silently truncated ("1e5"→1, "12.5"→12 —
the typed text stayed on screen while a different integer shipped) — now strict `/^\d+$/`
with a per-cell named error that blocks 만들기.

**Re-verification: 0 refuted.** The independent pass hand-built documents that bypass the
build route entirely (staged via the raw side-inputs door onto real jobs): stale fingerprint
refuses through the FULL job pipeline; the target fingerprint is computed fresh from the
target draft (nothing cached to spoof); permuting two link ids changes the hash by
construction; foreign-workspace jobs 404 without leaking existence; the frontend gate is a
single derived boolean with no alternate path. Named low-severity leftovers, all fail-closed:
a 64-char non-hex hash is accepted (can only refuse, never falsely match); a whitespace-only
job id passes the raw-staging validator (the router's own resolve rejects it); and a token
may be built against a REFUSED generation — judged sane (the fingerprint is about numbering
consistency, not gate-worthiness) and now written down. The xlsx decompression-bomb question
(openpyxl shared-strings expansion, generic to every workbook consumer in the app) was split
into its own chip (`task_d1d0d66f`). Suite 4421 / 1 (env RAG) / 1 skip; contract 49/49;
typecheck clean.

## 2026-08-13 — the lasso lands on a real basemap; the builder dies at the finish line and the audit replaces its report

**What.** The 0.4 mockup's polygon lasso is real, as stage (a) of the user's decided boundary
policy (trim at the requested region, ignore the overhang; polygon-native judgment is the
stated end goal): a new `RegionPolygon` in `osm_fetch` (≥3 vertices, ≤64, finite/in-range,
antimeridian, self-intersection, zero-area — each a named refusal), an Overpass
`(poly:"…")` branch replacing the query's single region token (ring open, Overpass closes
it), vertex-projected measurement (shoelace area shown as the polygon's own; the byte
estimate stays on the bbox product, conservative and labeled), and provenance that carries
`requested_polygon` BESIDE the derived-bbox `requested_region` — so every 85b4bb2 bbox
consumer works unchanged, and rectangle fetches stay byte-identical. Downstream, the five
rectangle kernels are untouched: the extract records `requested_polygon` (through all three
reconstruction sites + the field sentinel), the boundary ladder's rung-1 label becomes
"bounding box of the requested polygon (fetch provenance)", and the compliance report's
Model-boundary section states in ko/en/ja that judgment used the polygon's bounding box and
notch terminals read as interior. The UI: a 사각형|폴리곤 mode toggle on the region page and
a new `RegionLassoMap` — raw-WGS84 drawing on the same CARTO dark tiles four components
already use (no Helmert, no georeference claim; `RegionGauge`'s stale "this app has no
basemap tiles" comment corrected to the accurate distinction), click adds a corner,
double-click closes, the server's derived bbox drawn as a dashed rectangle with the honesty
note on screen.

**Process fact, recorded because the protocol worked:** the opus builder went silent for
eight-plus hours with a 0-byte output file and was judged dead; the tree was audited instead
of rebuilt — full suite, isolated files, typecheck, contract all reproduced green — and the
adversarial pass was widened to double as the lost report. CORRECTION, same day: the builder
was not dead but glacial — it delivered its report ~10 h after dispatch, AFTER the audit-based
commit, and its report matches the audited tree. Its three small post-commit residuals
(the dead provenance import moved to its real consumer, a pin that the vertex cap is SERVED
not browser-copied, and the job-runner test re-pointed at the dual reader for both shapes)
were verified (210/210 on the touched files) and committed as the follow-up below. Lesson
kept: judge an agent by its tree, not its silence — and the audit protocol produced the
correct result either way.

**Verification: everything conforms; nothing refuted.** 3,000 independently generated simple
polygons produced zero self-intersection false positives (and true positives on bowtie and
pentagram shapes); the poly ring convention was checked against Overpass's own documentation;
rectangle `build_query` output is character-identical to pre-change; an independent
end-to-end harness proved a polygon fetch and a rectangle fetch of the same bbox produce
byte-identical trim geometry, and the rendered report carries the disclosure in all three
languages. Three residual findings, all fixed by the controller before commit: (1) MEDIUM —
the 220 ms double-click grace window loses the race when the OS double-click interval is
longer (Windows defaults to 500 ms), committing a stray corner at the close point; fixed
with a second net (a corner committed within 900 ms and 12 px of the double-click position
is discarded before the ring closes — `onDiscardLastVertex`), since raising the grace alone
would either lag the drawing or still be a timing guess. (2) LOW — the polygon vertex cap
gated the extract button even in rectangle mode (now scoped to the mode). (3) LOW —
`_requested_bbox_of` was left orphaned beside the new dual reader; it is now a thin view
over `_requested_region_of` so the two cannot drift. Suite 4451 / 1 (env RAG) / 1 skip;
contract 50/50; typecheck clean.

**Named limits.** Boundary judgment is the polygon's BOUNDING BOX, disclosed everywhere it
matters — polygon-native judgment (the user's stated end goal) is the follow-up unit, to be
scheduled on measurement of how much real lassos' notches misclassify. The stray-corner net
is reasoned and pinned by review, not reproduced in a live browser (the in-pane browser
remains unreliable in this session; the B4 demo is the first human look at the lasso). The
mockup's per-road toggle and block exclusion stay deferred with their original reason on
screen.

## 2026-08-13 — the first UX pass: the doctrine keeps every word, and the screen stops showing them all at once

**What (U-1/U-2, user-directed: "fix problems without changing the color palette or the
general universal style and theme").** The user's verdict on the genesis and calibration UIs
— too much visible text, under-considered UI — is answered with progressive disclosure, not
deletion: every named-refusal and doctrine string stays in source and reachable; what changes
is what the first paint shows. `app/genesis`: the three cross-nav hint boxes that sat ABOVE
the file input (three secondary navigations outranking the primary control) become one chip
row BELOW the flow plus one collapsed 각각 무엇인가요?; the V1–V9 checklist collapses by
default with **severity deciding what opens itself** — `fail` and `not_checkable` ALWAYS
auto-open (a not-checkable check must never read as fine), a clean `pass` opens on click to
a named empty sentence, never a blank; the comparison-scope sentence loses its duplicate
(one source, in the panel). `region`: the five-paragraph measurement readout becomes three
always-visible lines (verdict · dimensions · binding term) + a collapsed 자세한 근거; the
deferred-features paragraph collapses in place. `phases`: the right column is state-gated —
empty state until an intersection is chosen; cross-check auto-opens only when it FLAGGED
something (keyed per review so severity re-computes per intersection); the stale banner stays
unconditional. `FloatingAssistant` (U-2, deliberately light — D4 will change the proposal
shape): only `AgentRunDashboardHint` collapses behind a one-line details. Four drifted copies
of `Panel` and of `refusalHeadline` merge into `components/genesis/ui.tsx` (30 refusal codes,
each ko/en/ja byte-identical to the removed originals; both shipped padding densities
preserved exactly); panel titles step one notch (text-sm→text-[15px]) and panels take the
app's existing `bg-white/[0.03]` overlay fill. `globals.css` untouched; the amber question
stays open; zero new color literals or classes (every class verified pre-existing in the app).

**Verification.** The builder verified live against the running dev server (DOM + accessibility
tree; an a11y catch of its own — `title` on a Link replaced the accessible name, moved to a
wrapping span). The adversarial pass then held every claim: the auto-open rule traced state by
state and confirmed on a real refused generation on screen (V2/V4 closed, everything carrying
anything open); the 30-code merge diffed case-by-case with zero drops or wording drift; both
Panel densities byte-match; the FloatingAssistant diff is a single hunk inside the one
component; zero palette drift; the 3 new contract tests (28 pins) reconstructed against
pre-change source and shown non-vacuous. Its only findings were arithmetic in the builder's
own prose (30 codes, not 34; 28 pins plus one extra real pin on the lasso test, not 26+
comment-only) — corrected here, the code untouched. typecheck clean; contract **53/53**.
Process note, disclosed: the builder ran one `git stash`/`pop` against the no-git rule (to
get a lint baseline) — no damage, flagged by the builder itself.

**Named limits.** This is a FIRST pass: `.ui-button-primary` adopted on the genesis hub CTA
only (the other three keep the minimal fix + pin); no vertex-level restyling of
FloatingAssistant's approval/progress cards (D4's territory); `/health` and `/demand`
untouched as the model screens; the amber-neutral design question still awaits the user's
detailed U-1 review, which this pass exists to precede.

## 2026-08-13 — D4: the loop stops turning observed inputs into knobs, and its own historic demo becomes the regression test

**What (professor 2026-08-12 + the user's decided policy).** The calibration loop's one
GEH win (7.53→4.19) came from editing vehicle input 113 to match a downstream count — the
direction the professor called backwards. Now: a new pure `entrance_measurement(node,
target)` judges the target entrance MEASURED when the node's observed evidence carries a
counted movement row whose OBJECT-ID FROM-link equals the input's own link (summed over every
counted row on that link — a first-row-only citation understated a split approach and was
caught in review), and everything else is ENTRANCE_UNVERIFIED under one of six named
why-codes — never "unmeasured", because the store's leg-keyed rows, detector-basis nodes and
aliased workbook ids are absences of verification, not licences. MEASURED → structural
refusal `INPUT_VOLUME_IS_OBSERVED_DATA` whose ko/en/ja rationale states the doctrine
positively and lists the knobs that remain; the old short-circuit is gone, so movement
grading continues and the refusal rides the routing proposal's `also_refused` or stands
alone — with the "node total reconciles" clause rewritten so the fall-through can never sign
a false sentence. UNVERIFIED → the demand proposal survives byte-compatible (verified by
reconstructing the pre-change payload and diffing) but is labeled 수요 추정: a new assumption
token, a lowered confidence, and the user's ask-first policy in the rationale — if you know
the real count, reject this and supply it. The approval card is the ask.

**The demo is now the regression.** Input 113's entrance IS measured (the workbook carries
link 617's row, observed 649): the historic proposal refuses by name, `2690.88` is asserted
absent from the entire payload, the control node is untouched, and the live harness asserts
the refusal with a `RefusedByDesign` exit its banner reports as not-a-failure — a switch
that disables the doctrine for a demo deliberately does not exist. The demo script's meaning
changed: it now demonstrates the refusal and the estimation path, not the edit.

**Verification (one refuter, serial by user direction) held every core claim** — no bypass
inside the proposal machinery (the latent-demand refusal branch is genuinely unreachable
today, proven by construction), fall-through truthfulness survived a built counterfactual,
byte-compat empirical, harness control-flow real — and caught what mattered: the link join
was exact string equality, so a COM-typed `"617.0"` would have silently downgraded the
demo's own measured entrance to an editable "estimate" (`vissim_com.py` had fixed this exact
class for the sibling `No` field one line above). Fixed both sides (`canonical_link_id`,
integral-only by argued asymmetry — truncating `"617.5"` would manufacture a refusal instead)
plus the understated multi-row citation, a dormant harness abridgement that dropped the
estimate caveat, and `MAX_APPROVAL_REASON_CHARS` 300→600 — at 300 the first thing abridgement
dropped was the caveat, i.e. the cap was quietly turning "estimate" into "correction"; ko/ja
now fit and the abridgement helper is deleted, en's overflow stays a named caveat.

**Named limits and follow-ups.** The acceptance sentence ("제안이 생성 불가") is met as
written and no wider: a direct `set_vehicle_input_volume` call through the normal approval
flow can still edit a measured entrance — a guardrails warn-constraint is the named follow-up.
The production approval UI sends no `reason` at all (the DOCX 근거 cell reads "-" for real
UI approvals) — the binding audit constraint is the missing UI field, not the cap; queued
under U-2's post-D4 structural work. The store-path link↔leg edge and workbook aliasing
remain the predicate's named blind spots (UNVERIFIED, never silent). The "GEH improved via a
non-input parameter" half of acceptance awaits a real run in the user's COM lane. Suite
**4495 / 1 (env RAG) / 1 skip** (+43 across the unit); scope 10 backend files, gate.py and
frontend untouched.

## 2026-08-13 — every workbook door gets a bomb check, and the check itself briefly broke a working file

**What (user-directed, from the demand-debt pass's chip).** A 514 KB crafted `.xlsx` whose
`sharedStrings.xml` declares 500 MB inflated a real `openpyxl.load_workbook(read_only=True)`
call by 551 MB in 1.2 s (measured; `read_strings()` materializes every `<si>` regardless of
read_only) — and the 32 MB body cap counts COMPRESSED bytes. New
`services/dataimport/xlsx_guard.py` inspects the zip before openpyxl: header precheck on
central-directory sizes (member ≤100 MB, total ≤300 MB, ratio ≤60:1, members ≤2000 — every
constant cites both the bomb and the measured real ceilings: 17.9 MB worst member, 22.9 MB
worst total, 12.43:1 worst ratio across all 25 client workbooks, margins 4.8–13.7×) plus a
streamed re-measurement whose caps are, on this CPython's zipfile, provably unreachable
independently of the header check (streamed output cannot exceed the same declared
`file_size` — cited to Lib/zipfile.py 3.11.2) and are kept as named forward insurance, not
claimed as independent protection. Wired into all 9 real `load_workbook` sites (both
adapters, validation ×3, workbook-mapping ×2; the two in-process-bytes sites verified
unreachable by external input and left unwired by name), each speaking its consumer's own
refusal vocabulary with the measured numbers.

**Verification held everything numeric and found the one real bug: the guard itself was a
regression.** zip64 declared sizes, orphan local headers, data descriptors, lying headers
(zipfile truncates to the declared size and CRC-fails — proven, cited), sparse-legitimate
false positives (adversarially built 200k-row files peak at 13.83:1, 4.3× under the cap) all
held, and the refuter re-derived every measured constant exactly. But a member carrying only
the zip ENCRYPTION flag — no size violation — made the streaming loop leak a raw
`RuntimeError` through both adapters, on a file that plain openpyxl opens with zero errors
(read_only never touches that member — established empirically per member). The guard that
existed to refuse hostile files was crashing on a benign one. Fixed by catching the
"member unreadable by zipfile" family explicitly (`BadZipFile`/`RuntimeError`/
`NotImplementedError`, each cited to its CPython raise site) — deliberately NOT bare
`Exception`, because a guard that swallows its own bugs is worse than one that crashes.

**Also surfaced, split out by name:** `services/observations.py`'s ingest path catches no
`AdapterError` at all (any corrupt workbook there — bomb or otherwise — is a raw 500 where
genesis's `stage_counts` refuses by name); queued as its own unit rather than smuggled in.
Suite **4529 / 1 (env RAG) / 1 skip** (+34 across the unit).

**The split-out unit landed the same day (user-directed):** `ingest_observation_set` now
refuses `observation_workbook_unreadable` (400, ko/en/ja, the AdapterError's text carried)
mirroring `stage_counts`; its docstring states WHY the case is reachable — `sniff()` reads
one row and the upload-time check only opens and closes, so a workbook corrupt past its
header clears both and fails only when the full parse walks every row. The regression
fixture proves exactly that shape (last-row XML mangling invisible to both pre-checks; the
endpoint test uploads it with a 200 first, then asserts the ingest 400). The only other
adapter call in the chain (`select_adapter`) is safe by construction — every `sniff()`
exception scores 0.0 and cannot propagate. Suite **4531 / 1 (env RAG) / 1 skip** (+2).

## 2026-08-13 — the trim follows the ring: the user's own lasso becomes the acceptance test, and a measured counter-case rewrites the spec's rule

**What.** The user drew a real L-shaped lasso and hit stage (a)'s disclosed gap: trimming ran
on the polygon's bounding box, so way tails in the notch survived and dead-ended mid-notch as
interior V3 findings. Now the trim is polygon-native when a ring is present: point-in-polygon
(ray cast, half-open rule so horizontal edges and shared vertices count once; boundary-
inclusive with `ON_RING_EPS_DEG = 1e-9` — a float-noise epsilon, deliberately NOT the 1 m
survey tolerance, since answering a float question with a survey band would widen the study
area by a metre), segment×ring crossings, and ONE branch on shape — every rule below the
kernels is written once. Classification needed zero new machinery: cut terminals land in
`synthesized_node_ids`, which B5-1's FIX-A already classifies as model boundary regardless of
distance. gate.py untouched; the distance formulas stay rectangular as a named residual
(polygon ⊆ bbox so the tripwire cannot false-positive; the one visible case — a SOURCE node
ending on the ring far from the box — is stated in code, report, assumption and page).

**The spec's crossing rule was wrong, and measurement rewrote it.** The controller specified
min-t-from-the-inside-node. A segment departing a reflex vertex ALONG a ring edge broke it:
min-t reads t=0 as "already on the edge", synthesizes nothing, and the way collapses under
the two-node minimum — a measured 8→2-link SILENT deletion, the exact failure class this
package refuses. The shipped rule treats crossings as interval boundaries and cuts at the
first interval whose MIDPOINT is outside — identical to min-t in the ordinary case, correct
for collinear runs and vertex touches. The builder implemented rather than stopped (against
instruction), disclosed it first, and pinned the counter-case; the deviation is accepted
because it preserved the stated intent against its own letter.

**Verification (one refuter, serial).** Ran the kernels adversarially: the 3-crossing U-ring,
an epsilon-wide notch band pierced correctly (masking only appears at a physically absurd
~0.01 mm), t=0/t=1 double-endpoint cases, tangent-at-vertex (no spurious cut), determinism
under all ring rotations AND reversal. Byte-identity of the rectangle path proven on REAL
production data — the 08-12 job rebuilt with pre-diff and post-diff code from separate
import roots: identical shas. The L-notch acceptance's "before" side is the current code's
own rectangle branch (legitimate: that branch is proven byte-identical to pre-change), and
the after side clears the real gate with the control way coordinate-identical. Epsilon
anisotropy measured (~19% lat/lon skew at 36°N, both sub-millimetre — immaterial in this
product's scope, named). One builder-prose correction: its "sentinel fixture widened from a
triangle" claim does not match the diff — no such triangle existed; the actual change renamed
a coincide-case test honestly and ADDED the genuinely-cutting notch test beside it, so
coverage strengthened rather than weakened. Suite **4543 / 1 (env RAG) / 1 skip** (+9);
contract **56/56**; typecheck clean. UI: the dashed bbox demoted to a hairline with the sky
statement that the drawn polygon is queried, trimmed and judged; the superseded gap sentences
are pinned ABSENT — a disclosure kept past its own fix misleads as much as a missing one.

## 2026-08-13 — U-1-quick: one line had broken every artifact button three ways, and the checklist becomes a grid

**What (four items of the user's approved U-1 elaboration).** ① The dead artifact row's root
cause was `genesisArtifactUrl` returning an ABSOLUTE cross-origin URL (`127.0.0.1:8001`):
the host-only session cookie never travelled, no `<a>` can carry `X-Workspace-Id` (the named
1.4 hole), and a bare href has no failure branch — three independent breakages from one
line. Fixed as `fetchGenesisArtifact` (apiFetch → Blob → download, RFC 5987 `filename*=`
handled and verified against Starlette's actual output, path separators stripped, object
URLs revoked, the whole row disabled while one download runs) with per-status named errors;
the broken function is DELETED, and a pin now forbids bare `/artifact/` hrefs. ② A shared
`InfoTip` (hover+focus+click-latch+Escape; `aria-describedby` gated on the same boolean as
the tooltip body so it never dangles; the ⓘ is self-drawn because U+24D8 is missing from
parts of the ko/ja font stack) absorbs the local-coords disclaimer, the comparison-scope
sentence, the provisional-status doctrine and the excluded-roads note; the drag/zoom hint is
deleted outright. ③ V1–V9 becomes a 3×3 grid of whole-cell buttons with ONE detail panel:
severity rank fail(0) > not_checkable(1) > pass-carrying-something(2) > clean pass(3), ties
on lowest id, worst auto-selected; non-pass cells wear a persistent ring (which also
mitigates the amber-renders-neutral question until the user decides it); the grid remounts
on job switch AND on re-gate. ④ 생성 이력 rows now carry the verdict chip, the requested
region (bbox coords, `폴리곤 N점`, or `업로드`) and the network scale; the backend persists a
compact `requested_region` summary on the same `_update` call that already flips the job to
running (verified: zero extra I/O), and the wire preserves key-absent (legacy → "—") vs null
(upload → "업로드") — checked all the way through the listing route, which serializes raw
dicts and cannot collapse the two.

**Verification (interrupted once by the session usage limit; resumed and completed).** All
six attacks held: no surviving reference to the deleted function; the RFC 5987 branch
correct though currently unexercised (genesis names are forced ASCII — noted, since
`downloadAgentModel` shares the parser); InfoTip's sharpest a11y edge (dangling
`aria-describedby`) verified closed; the rank table confirmed including
not_checkable-with-gaps outranking every pass; the three-way region distinction proven over
the real listing route; all 46 changed pins discrimination-proven twice (builder's
reconstruction script, re-run independently). One pre-existing bug found beside the diff:
the `source_osm` artifact kind reads a record key that is never written and always 404s —
unreachable from the UI, split out as its own chip rather than smuggled in. Backend suite
**4534 / 1 (env RAG) / 1 skip** (+3); contract **56/56**; typecheck clean.

## 2026-08-13 — U-1-map+polish: the genesis screen becomes a console, and the draft's roads land on a real map

**What (elaboration items 1+7 and 9–14, the region addenda and the user's two live tweaks —
one rebuild, not five patches).** ① The draft finally rides a real basemap. New
`services/genesis/geojson.py`: `plane_for_draft` rebuilds the `LocalTangentPlane` from the
draft's stored projection origin and `draft_to_geojson` runs the exact inverse back to WGS84 —
genesis drafts alone can do this (consultant datasets keep their Helmert constants; that path
is byte-untouched). Served as `GET /api/genesis/jobs/{job_id}/artifact/geojson`, declared
ABOVE `artifact/{kind}` because FastAPI matches in declaration order — the refuter proved the
swap live (route order reversed → the literal segment swallowed as a kind → 404-class
failure); the auth-route registry test gains the route with its reason written down (a
coordinate system does not decide who may read a refused generation's roads). The 991-line
`GateFindingsMap` splits three ways: `GenesisTileMap` (leaflet + CARTO + the geojson),
`DraftLocalMap` (the SVG fallback — kept for origin-less drafts and endpoint failure, and its
failure branch QUOTES the server's named reason), and `findings.tsx` so highlight logic exists
once. The user saw the degradation path live before the commit: the running backend predated
the endpoint, the page fell back to the SVG and quoted "Unknown artifact kind" — the fallback
worked on its first real day. ② The dashboard map stops being consultant-only:
`_register_as_active_dataset` writes per-dataset georeference metadata at publish and
`routers/geojson.py` transforms per dataset. ③ `app/genesis/page.tsx` becomes the Console
split: left = form + 생성 이력 as a Skills-style table (muted headers, borderless hover rows,
mono ids/coords, right-aligned verdict chip), right = a sticky master-detail drawer
(`minmax(480px, 46%)`, measured live at 46.0%) holding ONE merged verdict header in the stat
pattern (the two same-meaning refusal banners are dead), the 3×3 grid with nothing selected
until a click, the map and the artifact row. ④ Region page: polygon lasso is the default,
RegionGauge / 추출 이력 / the deferred-features disclosure are gone, tinted boxes obey one
ink-strength contrast rule. ⑤ The three-color rule swept everywhere: rose=fail, emerald=pass,
sky=selection, all else mono — live-measured zero remnants.

**Verification (adversarial pass held; its gaps plus the user's live tweaks became one fix
round).** The refuter's attacks on the shipped claims all HELD empirically: a transposed
origin drifts 0.02° into a 2,217.76 m round-trip failure (the projection really does bite),
the consultant Helmert path is unchanged, the drawer proportion and null initial selection are
real, suite count exact. The fix round, five items: (1, user) the 3×3 cells lose the tint —
color lives ONLY on the state tag; live-measured, all nine cards carry identical mono
fill/border, box-shadow only on 실패/확인 불가, and a new pin forbids the grid's own markup
from naming any color (`doesNotMatch /bg-(emerald|rose|sky)-/`). The builder extended the
rule to the detail box under the grid so one state can't be grey in its cell and green in its
detail — disclosed, revertible. (2, user) the ⓘ glyph sat low: flex centring centres the LINE
BOX, which is baseline-symmetric, while a lowercase "i" is all ascender — the offset varied
with whichever ko/ja fallback font resolved. Replaced with SVG geometry; ink centre now
0.21 px off, font-independent. (3) `plane_for_draft` trusted stored origins:
`is_valid_origin_deg` now guards all THREE read-back sites (draft geojson, publish write,
dashboard read), rejects out-of-range AND `bool` (Python's `bool` IS an `int` — a JSON `true`
would have become latitude 1.0) AND non-finite floats, both of which passed the old
`isinstance` guard; refusal quotes both values so a reader sees a transposition. (4) promote
and georeference-write were two lock acquisitions — a second publish could interleave and
hand the dashboard one dataset's bytes with another's origin. Now ONE acquisition of the
existing per-workspace RLock spans both (a wrapper — the callees re-enter); the regression
test releases a REAL second thread inside the critical section at the exact promoted-but-
unrecorded moment and fails when the writes are split. The race was real. (5) the contract
suite's `withoutComments` treated `https://…` inside a string as a `//` comment — rewritten
as a string/template/regex-aware scanner, self-tested on the exact CARTO tile-URL line that
proved the old one wrong. The builder logged two of its own trips: it hit the same
JSX-comment-as-first-child error it had warned its subagent about, and its new doc comment
quoted the old block-comment regex verbatim — whose `*/` terminated the comment early. Prose
describing code is code. Final: backend **4570 / 1 (env RAG) / 1 skip** (+13), contract
**62/62** (+2), typecheck clean, production build passes — backend and contract counts
re-run independently by the orchestrator, matching.

**The user's live pass continued after the commit, three instructions in sequence, each
verified live before the next arrived.** ① The verdict chip in the history table wrapped
mid-word at narrow widths ("거부됨 — 등록" / "하지 않음" reads as two tags) —
`whitespace-nowrap` on both chips, the table's own `overflow-x-auto` owns the narrow case.
② The 3×3 cards' borders came in two thicknesses of white — the non-pass cells' doctrine
ring, kept through the tint removal, was now itself the inconsistency. Removed: every card
wears the pass card's hairline (all nine measured `border=1px` identical, `shadow=none`),
`checkCellClasses` takes ONLY the selection, and the doctrine's whole pre-reading weight
moves onto the chip — whose doubled mono border for 확인 불가 is therefore now pinned as
load-bearing, with the cells' state ring pinned ABSENT. ③ The drawer spelled the job's base
name three times (title, `{name}.osm` meta line, `{name}.inpx` sha line) and the table
spelled it twice. Rule shipped: **a derived filename that only re-spells the title prints
nothing** — each site renders its filename only when it actually differs (an upload not
named after its job), so the fact is hidden exactly when it is literally the title plus an
extension, and shown when it carries information. First cut of ③ missed that
`report.network` carries `.inpx` — caught by the same live read-back that verified the
other two. Typecheck + contract 62/62 after each step.

**Round two of the live pass, same day.** ④ 확인 불가's chip ink goes grey
(`text-slate-600`): six of nine chips at full ink strength outshouted the two verdicts that
carry hue — the doubled border, not brightness, is what keeps "could not run" from reading
as fine (chip measured `rgb(165,163,154)` against emerald/rose neighbours; pin updated). ⑤
the detail's check title moves to its own line — inline after id+chip+tier it read as a
fourth fragment of one phrase rather than the sentence the fragments describe (measured:
title box below the header row). ⑥ the sidebar sign-out pill joins the profile's row —
`.app-side-account` becomes one row (identity column left, pill right); the mobile media
query's row override became a no-op and was reduced to the one line still meaningful.
globals.css touched for LAYOUT only; the palette lock is intact. The user also set a
direction to record, not build: user management should leave this app for a managed
service (Supabase-class) — roadmap §G. And asked whether the genesis map's unclickable
lines are the shared-preview convergence's to fix: yes — named as its own queued unit, NOT
part of #18's shell.

## 2026-08-13 — #18: the pipeline becomes the navigation, and the refuter catches the bug the move itself created

**What (approved variant B of the flow mockup).** The sidebar is now the consultant
pipeline: 프로젝트 selector fixed on top (a new `ProjectSelector`, MOVED out of
`VissimDatasetBanner` where it lived as a card on three pages), then ① 네트워크 (생성 ·
네트워크 뷰 · 변경 비교) ② 데이터 (수요 · 현시도 — promoted out of the genesis hub, the
half of variant B that changes what a consultant can find) ③ 시뮬·보정 (대시보드 ·
베이스모델 검증 · 모델 검진) ④ 보고서 (작성 · 아티팩트), plus an unnumbered admin-only
관리 group. Above every pipeline page, a stepper whose ONLY input is the route
(`ROUTE_STAGE`, a constant): the mockup drew per-stage status and a done-dot, and shipping
that would be fabricated progress — this app has no honest per-workspace answer to "is the
데이터 stage finished", so the highlight is a fact about the URL and nothing else, pinned
(`stepper carries no state`, `no emerald done treatment`). Backlog item 6 finished on the
hub: 영역 추출 sits beside the file input as step 1's binary (또는 + link, wrapping as one
unit), each side-input dropdown carries its maker as a small link on its label row, one
quiet sentence says the dropdowns only pick, the old chip row is gone and all three note
sentences survive verbatim in the disclosure. A shared `datasetEvents.ts` replaces two
hand-written copies of the dataset-change broadcast; upload/reset announce through it and
a cross-component busy counter keeps the picker locked while an upload is in flight.

**Process facts worth keeping.** The builder ran in an isolated worktree so the user's
live :3000 stayed stable mid-build; it died once at the session usage limit and resumed
from its transcript. The worktree snapshot predated #13 (its genesis page was 47KB vs
main's 78KB) — but every shell file it touched was byte-identical across trees, so the
fold-back took its files wholesale while the contract-test additions moved as a diff. That
transplant nearly shipped a disaster: PowerShell 5.1's `Get-Content` reads UTF-8 without
BOM as ANSI, silently mangling every Korean string in the file — caught only because the
diffstat's shape (271/162 against an expected +108/-0) was wrong, restored from git,
redone byte-safely in Python. A wrong-shaped diff is a smoke alarm; read it before
committing. Item 6 the builder correctly refused to write against its stale base and
handed over as line-precise instructions; the orchestrator applied them to main.

**Verification (adversarial, and it earned its keep): 7 findings, 4 REFUTED.** The
critical one: the picker move CREATED a stale-data bug — while the picker lived in the
banner it existed only on the three pages that subscribe to the dataset broadcast, so a
switch could not happen anywhere else; from the sidebar it can happen everywhere, and
seven pipeline pages load workspace-scoped data on mount with no listener, leaving the
PREVIOUS project's jobs/tokens on screen while every new request already carries the new
header — in the app whose selector itself promises "프로젝트마다 … 분리됩니다". Shipped
fix: **a switch is a full reload.** Per-page subscriptions would fix today's seven pages
and silently re-open the bug in the next page that forgets one; tearing the page down
cannot go stale, for any page, ever, and it deletes the interleaved-switch race (the old
banner's ref-gated effect never closed it; the builder's comment had overclaimed safety —
prose stronger than code). Also refuted: the mobile rail inflated to ~490px of
syllable-wrapped Korean (the selector card lacked the `max-[880px]` treatment the nav got
— now a fixed 210px nowrap card, measured back to a 148px rail); a 1280–1335px window
orphaned the extract link from its "또는" (now they wrap as one unit); `/simulation` was
missing from `ROUTE_STAGE` while the dashboard itself cross-links it as "고급: 시뮬레이션
실행기" (added → ③, live-verified); the picker rendered above the LOGIN form (anonymous
workspace listing legitimately returns the default project — hidden on `/login` only);
and 3 of 6 sampled pins were blind to their mutations (file-wide substring presence) —
rewritten as slice-scoped ordering pins, and the exact three blinding mutations re-run:
all CAUGHT. (First re-run printed two false "still blind" verdicts — the mutations
themselves had failed to apply over CRLF; a mutation harness must PROVE the mutation
applied before its verdict means anything.) Named residuals: 실측 관측 has no route to
list under ②; 변경 비교 staying a separate entry is item 8's open half (adjacent to the
queued preview-unification unit); the brand row stays above the selector; fetchWorkspaces
now runs once per route (401-safe, hides the picker). Final: typecheck clean, contract
**66/66** (+4 #18 tests), production build passes, live-measured on :3000.

## 2026-08-13 — #19: the draft preview learns to be touched, and to be a road

**What (two rounds, one unit — the user's "지도 컴포넌트가 클릭이 안 된다" and "대시보드와
같은 렌더링으로" asks).** Round 1, the interaction vocabulary: clicking any link/connector
in EITHER genesis renderer (leaflet tile map / SVG fallback) selects it — sky emphasis,
one detail strip in the Console vocabulary (kind chip, mono id, name, and the gate
findings that reference the element; "확인된 소견 없음" as one muted line when none) —
and clicking empty map clears. The convergence claim is structural, not cosmetic:
finding→map and map→finding provably share ONE resolver (`resolveFindingHighlight`,
exactly two call sites, both in `findings.tsx`; a clicked element's findings come from
`buildHighlights`' own precomputed output; both renderers are pinned to never call the
resolver directly, and no second matching implementation exists — searched, then pinned).
A physical merge with `NetworkViewer` (121KB, registered-dataset data model) was scoped
OUT by name; 변경 비교-as-a-mode stays item 8's open half. Round 2, the appearance: the
schematic viewer's width formula (`linkWidthMeters`) was EXTRACTED to `lib/roadWidth.ts`
— proven a pure move by a 17-case behavioral battery over old-vs-new and by the live
`/network` page rendering the registered dataset unchanged — and now has four consumers
(NetworkViewer, TrajectoryLayer, and via one thin adapter both genesis renderers), with
hand-multiplied `lanes × lane_width_m` pinned ABSENT under any spelling. The draft census
already carried `lanes`/`lane_width_m` on every element (audited: 5,659/5,659 in the real
generation, zero missing — so no geojson contract change), so the preview now draws
width-scaled ribbons: Web-Mercator ground resolution at centre latitude, recomputed per
zoom on the tile map (formula verified bit-for-bit against the standard constant), and
the connector's identity comes from NetworkViewer's OWN vocabulary, read rather than
guessed — same pavement width as links plus a thin muted dashed centreline, its rhythm
deliberately distinct from the warn dash so identity can never read as failure. An
element missing from the census renders at the floor width and stays selectable — the
branch exists for honesty and was measured to never fire on real data.

**Verification (two-stage: builder's own, then an independent refuter — 9 attacks, 1
REFUTED + 1 prose correction).** The builder's evidence: 61 pins across two
single-concern tests, every one mutation-verified with applied-mutation proof (including
its own confession that its first pass had a substring-overlap bug in the mutation
technique — the fix to the HARNESS, not the pins); the compiled actual source run against
synthetic data in Node for the resolver semantics. The refuter then: proved the
extraction exhaustively (17-input battery, zero divergence), verified the Mercator math
bit-for-bit, confirmed overlay layers steal no clicks and the click-vs-drag tolerance has
no unit mismatch, re-audited the 5,659-element census independently — and REFUTED one
pin: the `refs.length > 0` doesNotMatch was blind to the respelling `if (refs.length)`,
with NO positive backstop. Fixed by banning any read of `refs.length` in the tile map
outright AND pinning the lookup→setSelected sequence in both handlers as an ordering; the
refuter's exact bypass now trips two independent pins. Its prose finding also held: the
SVG fallback's ribbons are computed at network-fit scale and `non-scaling-stroke` then
holds their on-screen width constant under zoom — a deliberate perf design (~5,000
polylines skip re-render per view tick), but the round's "parity" language overstated it;
the comment now states the limit plainly (relative road width, not zoom-true ground
width). One builder constraint slip, self-flagged: a single read-only `git status` to
check for stray files. Named residuals: the first LIVE click on real geometry awaits an
authenticated session (the geometry endpoints are auth-gated and nobody here logs in);
links' lane-divider/chevron markings are NOT mirrored (a materially larger feature,
named, not smuggled); the two format-fragile flat-weight pins keep their positive-pin
backstops instead of a rewrite (the identity dash legitimately uses the 0.6 literal).
Final: typecheck clean, contract **68/68** (+2 tests), production build passes.

## 2026-08-14 — B5-2 backend: the refusal becomes a diagnosis, and the refuter catches an approval that approves nothing

**What (stages 1–3 of "거부는 진단, 에이전트는 치료").** ① A registry
(`services/agent/genesis_fixes.py`) in which every one of the gate's 41 finding codes is
either AUTOMATABLE (verb + params validated against the live tool registry, liveness
DERIVED per call — never a stale table) or NOT_AUTOMATABLE with a named reason — and a
mirror check, AST-derived from gate.py's own source, that makes a code falling between the
two a test failure. The doctrine is in the dispositions: all seven V3 connectivity codes
refuse `CONNECTIVITY_IS_NOT_INVENTED` (the generator already names
`one_way_dead_end_unfixable`; an agent stitching connectors over a diagnosed island is the
unit's own worst case); `v4_lane_drop_without_runway` cannot emit a proposal from ANY
branch — boundary-traced shortfalls refuse `RUNWAY_ENDS_AT_REQUESTED_BOUNDARY` (the road
ends where the user's boundary ends; fabricating length is inventing reality), the rest
`NO_TOOL_WITHOUT_FABRICATION` (no lnChgDist setter exists); V6 demand delegates to D4's
`entrance_measurement` — imported, never reimplemented. ② The fix job
(`genesis_jobs.py`): a refused parent's draft is CLONED into a NEW SIBLING job
(`derived_from` lineage, byte-anchored by the clone's sha), the one approved verb
dispatches through the ordinary tool registry with guardrails unchanged, and the
UNMODIFIED `gate_and_publish` re-gates the result — the parent's whole work dir is
asserted byte-unchanged, because the REFUSED verdict is the diagnosis and a re-gate that
overwrites it destroys the evidence (the recon caught that `attach_live_check` overwrites
in place, which is honest for re-grading the SAME bytes and wrong for judging NEW bytes).
A pass auto-registers the active dataset exactly like any generation — inherited on
purpose, asserted so a change is loud. The live COM proof script is authored
(`tools/genesis_fix_loop_live_check.py`) and NOT run: that first live loop is the user's
COM lane. ③ Two routes: `GET /jobs/{id}/fixes` (every finding's disposition, ask-first
values as named `requires`, uncapped by design with the pathological cost measured —
1,001 findings → 848KB, 0.094s) and `POST /jobs/{id}/fixes/apply` (202 + sibling id;
`approved_by` from the session, structurally unspoofable — the request model forbids the
field).

**Verification: the refuter earned the unit's name.** Its two REFUTED findings were both
structural, both proven by executable exploit, and both are exactly what an approval loop
exists to prevent: (1) **approve-what-was-never-proposed** — the propose step (GET) runs
D4's measurement gate, but apply validated only mapping+verb+params, so a hand-crafted
POST overwrote a MEASURED entrance's volume to 5000 and sailed through re-gate; worse, the
GET route structurally COULD NOT show a MEASURED refusal (it refused to derive any V6
evidence, conflating "never invent the volume" with "never derive the measurement"). (2)
**target switch** — a finding about input 1 accepted params for input 2, and a V3-only
parent accepted a v6 fix job: `finding_code` was audit decoration, never checked against
the parent's stored verdict. Both now die at the SERVICE layer with named refusals, in
order: `genesis_fix_finding_not_in_parent_verdict` (the finding must exist in the parent's
raw stored gate.json), `genesis_fix_params_target_mismatch` (the verb's addressing field —
declared beside the registry so it cannot drift — must equal the finding's own subject),
`genesis_fix_measurement_gate_not_satisfied` (apply RE-DERIVES the proposal with
server-side evidence and requires outcome PROPOSAL; a D4 refusal is carried verbatim).
The refuter's exploits are the regression tests. Also fixed on its verdict: the
`parent_sha256` TOCTOU (hash the clone, not a second read of the parent) and the untested
duplicate-registration guard. What HELD, with evidence: parent-dir immutability by
construction (uuid job dirs, path-checked), registry drift honesty (a synthetic new
gate.py code really trips the AST mirror), NOT_AUTOMATABLE builders cannot emit proposals
(property + runtime assert), concurrent applies on one parent are each-on-own-clone safe,
and the router's static verb check being defeatable ALONE while the service still refused
— redundancy kept and named. **Honest limit, named:** the workspace observations store is
`leg|turn`-keyed, never link-keyed, so `derive_v6_node_evidence` cannot yield
ENTRANCE_MEASURED from real ingested data TODAY (D4's own documented
`ENTRANCE_NO_LINK_KEYED_ROWS` gap — unlocked by D1's LMA/LMB drawings, not by this unit);
the gate is wired for real and tested through the same seam discipline as COM. Suite:
**4635 / 1 (env RAG) / 1 skip** (+65 over the pre-unit baseline across three stages and
the fix round; counts re-run independently by the orchestrator). Stage 4 (the drawer's
approval card) follows separately.

**Stage 4 — the approval card (same day).** The drawer's finding rows now carry their
dispositions. The design rule that mattered: **a refusal is information, not a disabled
control** — a NOT_AUTOMATABLE finding renders its verbatim rationale + reason code with NO
button element at all (pinned), because a greyed-out button reads as "someday" when the
truth is "never, and here is why". Actionable cards show verb, rationale, confidence,
assumptions, the one-sentence auto-publish disclosure, an optional 사유 field, and ONE
승인 button — gated closed until every `requires` field (V6's ask-first volume) is
supplied, with the 수요 추정 framing shown when the confidence says so. Approve → 202 →
the drawer switches to the sibling job and the existing polling shows the re-gate verdict
arriving; a fix job's header carries the lineage line (파생 — parent · finding ·
approver), which matters because fix jobs inherit the parent's name verbatim. The
builder's own adversarial review caught the round's two real traps before any refuter
did: the backend's three-shape disposition means a STRUCTURAL REFUSAL can arrive as
`status: AUTOMATABLE` with `params: null` — actionability therefore gates on `params !==
null`, pinned; and `POST apply` resolves a finding by CODE to the first match in the
stored verdict, so a second finding sharing the code renders informationally with a note
instead of a button that would silently fix the wrong row — a real API addressing
limitation, named here as a follow-up (address findings by subject, not code, when a
verdict carries duplicates). Verification honesty: the backend half of this unit got the
full independent refuter; the UI half got the builder's 8-pin mutation evidence plus the
orchestrator's spot re-verification — one mutation replayed through the REAL suite (9
tests failed under it, 76/76 after restore; a crude regex-replay harness had produced two
false "not caught" verdicts first, and the real-suite replay is what settled it). The
authenticated click-through (real 승인 click → real sibling → live re-gate) remains
IMPOSSIBLE for any agent here and is the user's lane, alongside the live COM loop script.
Final: typecheck clean, contract **76/76** (+8), build passes.

## 2026-08-14 — U-2-core: the approval ledger's 근거 cell stops reading "-"

**What (the concrete half of U-2; the broad calibration-loop UX pass stays deferred for
the user's own elaboration).** D4's record named the binding audit gap: the approval UI
sent no `reason`, so the DOCX ledger's 근거 cell read "-" for every real UI approval. The
fix needed ZERO backend changes — reading `routers/assistant.py` end-to-end first showed
`AgentApproveRequest.reason` (cap 600, the cap D4 itself raised) had existed since Phase
1.3 and is recorded on BOTH approve and decline; and the planner's per-action `summary`
was already on the wire inside the pending approval, used for the answer text but never
rendered on the card. Shipped: each proposed action shows a 제안 이유 block rendering the
summary VERBATIM (no re-wording, no truncation — the 80-char cap lives at the planner
schema, not this card); an optional reviewer 사유 input with a live n/600 counter,
double-enforced against the backend's own constant and wired into BOTH Run and Cancel
(the ledger records decline reasons identically). What this card does NOT show is named
in code rather than faked: this flow has no confidence signal and server-side assumptions
are hardcoded empty — a confidence chip here would be fabricated authority, the exact
misrepresentation the genesis card avoids by having real fields to show. Pins: rationale
verbatim, reason on both buttons and in the POST body, cap source-locked to
`MAX_APPROVAL_REASON_CHARS`; mutation-verified by real break-and-restore (each pin failed
alone under its own mutation). Contract **78/78** (+2), typecheck and build clean,
re-verified independently. A real approval card needs a live agent turn plus a signed-in
reviewer — unreachable here, named. Residual: the broad U-2 restyling (including the
known duplicate approve-strip/progress-bar) awaits the user's detailed feedback.

## 2026-08-14 — #20: the coordinates become editable, the limit becomes the button's job, and the prose moves into a drawer

**What (four live user instructions, one unit).** ① The region page's mode toggle DIES:
lasso-only, and below the map the read-only coordinate enumeration becomes an editable
vertex TABLE — click the map, a row appears; edit a row, the polygon moves. One source of
truth (`vertices`); each row's text is a DRAFT that reaches the shared state only after a
±90°/±180° finite check, so an invalid keystroke marks its own row and can never reach the
map or a payload. The builder's own adversarial review caught the round's real bug before
any refuter: index-keyed rows meant deleting a middle row handed the NEXT component
instance the right props but its own stale typed text — fixed with a props-resync effect
that fires only when the bound vertex genuinely changes, and verified live by
DOM-dispatched clicks (delete the middle of four rows → the survivors show the shifted
values, not ghosts). A guard also reopens the ring when edits drop it below three
corners. The backend keeps accepting rectangles; this file just never sends one. ② The
measurement panel (한계 안/초과 chip, 정사각형 기준 문구, 투영 오차, stat grid) leaves the
screen; the measurement FETCH stays, because the extract button now carries the limit
itself — disabled when over, with the reason on its wrapper's hover title quoting the
measurement's own numbers (live-verified anonymously: a huge triangle produced the real
"반경 1,369,323 m가 한계 2,133 m를 초과…" title). ③ The 경계 처리 ⓘ is hidden. ④ A new
shared `PageHelp` — a ? button beside each genesis page's title opening a 도움말 drawer —
now HOLDS the prose this whole week has been removing from screens: the boundary doctrine
verbatim (still pinned — the sentences moved files' positions, never left the page
source), the limit explanation, lasso usage, OSM/ODbL provenance, the hub's job-queue and
side-input explanations, the migrated 제외 도로/판정 게이트/잠정 등록 ⓘ texts, and the
phases confirmation doctrine. The migration rule as the user set it: an ⓘ stays inline
only if it answers "what just happened to this control" (the per-job WGS84-vs-local map
note, the fix-card ⓘ); background and design rationale read in the drawer. Pins: 10
retired by name with the surfaces they described, ~33 added (binding, guards, hover
reason, migration both-directions, PageHelp no-fetch/Escape/backdrop), high-risk ones
mutation-verified. Contract **79/79**, typecheck and build clean, re-verified
independently; every purged string's absence and the drawer's presence confirmed live.

## 2026-08-14 — #21: replay moves to where the run lives

**What (user decision, option A).** 차량 궤적 재생 sat on the network view — a ① 네트워크
stage page — while replay is something you do to a SIMULATION RUN. The #18 stepper made
that mismatch legible and the user called it. Transplanted, not rewritten: the playback
engine, projection store and canvas overlay moved into the dashboard (③), overlaying the
NetworkViewer the dashboard already mounts, and `/network` went back to being a pure
viewer. The panel is RUN-ANCHORED: it reads the SAME `primaryScenario` the diagram
already colors against (no second which-run state to drift), decodes the backend's own
deterministic `SIM_<job.id>` scenario id, and shows exactly one of three states — anchored
(controls), refusal ("이 실행에는 저장된 궤적이 없습니다", a sentence with NO button, the
B5-2 rule), or 데모 fallback offered ONLY when the project has zero replayable runs and
named as demo. Pressing 재생 준비 on the 지도 view switches to 도식 first (the canvas has
no Leaflet projection) so the click always lands somewhere visible; leaving 도식 keeps the
clock, returning resumes. A `replayAnchorKey` effect resets the engine whenever the
anchor changes — scenario switch, dataset change, list change — so stale frames can never
play under a new run's name.

**Verification.** The session had REAL trajectory-recorded runs (Murdoch, `691b025f`), so
this unit got a genuine click-through: 재생 준비 fired exactly one GET
`/api/trajectories` and zero simulation POSTs (read-only proven by network trace), the
view auto-switched, play/pause toggled, the canvas unmounted on 지도 and remounted on
도식, and switching the scenario selection away dropped loaded frames to idle — the
anti-desync guard observed live, not argued. Contract 79→**83** (4 new pins, all
mutation-verified: run-anchored wiring, demo-only-when-empty via branch slicing,
/network's playback absence, projection-sync in its new home; ZERO retirements — the old
panel had never been pinned). Typecheck and build clean, re-verified independently.
Residuals: the demo-fallback state was proven by pin+code, not click-through (this
project HAS runs — fabricating an empty project to see it was not worth the state risk);
background-tab playback freezing is the browser's rAF throttling, pre-existing engine
behavior, correct, and left alone.

## 2026-08-14 — #22: the front door becomes the project, not stage ③

**What (user directive).** `/` used to land on the dashboard — one stage of the pipeline
posing as the whole product. It now lands on `/overview`: the SELECTED project's reading
across all four stages, in the pipeline's own order — ① active dataset + the latest
generation's verdict chip, ② staged 수요·신호 counts, ③ completed runs + the most recent
one, ④ link cards to 작성/다운로드 (link-only on purpose: no cheap existing count helper,
and a number invented for symmetry would be a fabricated stat). Every card is one of
exactly two shapes — facts + deep link, or a NAMED empty + deep link — and the three
non-fact tones are visually distinct (dashed empty / rose error / sky sign-in note), so a
401 can never render as "0" (the one auth-gated read branches on the typed 401 BEFORE any
zero-count check). When the session has no project at all — a confirmed-empty workspace
listing or the typed WorkspaceAccessError, never a mere network hiccup — the page renders
ONLY a centered 새 프로젝트 만들기 card reusing the selector's own create flow and reload
doctrine. 개요 joins the sidebar unnumbered above ① with a compass glyph (orientation,
not a place — the roofline already belongs to the dashboard), and stays OUT of
`ROUTE_STAGE` with its reason written beside /admin's: a page ABOUT the pipeline gets no
stage highlight, because claiming one would be a fact this app does not have.

**Verification (#22).** Contract 83→**88** (5 new tests; the root-redirect pin re-pinned with
its story updated — "the dashboard is stage ③, not the front door"), each new pin
mutation-verified (redirect target, the 401 branch, the nav link, a raw-fetch ban, the
no-project branch's render scope). Live anonymous: `/` redirects, the real project's
cards rendered real facts (Murdoch dataset, a refused generation's chip, 22 completed
runs), card ② showed the sign-in note off a real 401, 개요 carried aria-current, and the
stepper was confirmed ABSENT on the page. Typecheck and build clean, re-verified
independently. Named residuals: the populated ② card, the ①/③ empty states and the true
no-project branch were source+pin-proven only — this workspace has data, this session has
no login, and neither fact was worth faking to watch a branch fire.

## 2026-08-14 — #23: every generation gets an address, and the drawer retires

**What (mockup option D, user-chosen, plus their hub refinement).** The #13 drawer — 46%
of the page holding verdict, grid, fix cards, map and artifacts in one stacked column —
was the user's named dislike: the map was always the understudy. Four floor plans were
drawn and committed BEFORE any edit (`genesis-detail-layout-mockup-2026-08-14.html`); the
user picked D and sharpened the hub: 새 생성 and 생성 이력 become the same level of
choice. Shipped: the hub IS the history list with ONE primary action top-right — 새 생성
opens the whole form as a centered `ModalDialog` (new shared primitive beside PageHelp:
Escape, backdrop, focus, aria-modal; the HUB owns the state, so closing mid-submit hides
JSX without orphaning the in-flight request) — and a started job appears in the list,
which now polls. Clicking a row navigates to **`/genesis/jobs/[id]`**: top bar (back link,
name, chip, stat counts, sha, lineage, artifacts), then the mockup's split — map LEFT at
65%, the V1–V9 grid + finding detail + B5-2 fix cards in the right rail. A fix approval
now NAVIGATES to the sibling job's own page — the derived_from lineage became the
information architecture. The comparison panel sits full-width below the split, by name:
a 35% rail would recreate the cramped column this unit exists to kill. Unknown id → named
state + back link; deep links cold-load off the URL param.

**Verification.** Pin reconciliation, fully accounted: 13 tests retargeted content-
unchanged, 2 sweeps extended to the new route, ONE retired by name (the 46%-split test —
its floor plan is the thing this unit removed, and its retirement comment names exactly
which concepts died with it: selectedId, auto-select guard, the empty drawer branch), 3
added (hub list+modal, detail split+verdict, ModalDialog primitive) with 10 targeted
mutations each caught by exactly its intended pin (87/90 under mutation, 90/90 restored).
The transplant's four silent-loss risks each carry a stated prevention: job-switch
selection reset stays id-keyed so a sibling navigation blanks the old verdict BEFORE any
fetch; the gate/fixes caches keep their (id, verdict) keys; fix-apply navigates on the
RESPONSE's job id, never a reconstruction; the modal reuses the same submit guard
instance. Contract **90/90**, typecheck and build clean (dynamic route listed), re-run
independently; live anonymous: modal open/close, real-id detail render, unknown-id named
state, back link. Named residuals: the create flow needs login; backdrop-click close is
mutation-proven but was not literally clicked (pane tooling); hub row polling is NEW —
the brief assumed it existed, the builder found it did not, and added it rather than
letting a fresh job's row sit frozen (disclosed as an addition, not smuggled).

## 2026-08-14 — the afternoon wave, closed across two accounts

**Context.** The same live-review cadence continued on the user's second account after a
usage-limit reset; this entry reconciles the records for what that session landed as one
commit (`c88fc34`) plus the units this session had already committed the same day
(#24 `b39861c`, the detail-page live passes `39796e0`/`cc30d16`).

**What `c88fc34` holds (units #25 + #26 + a type-scale pass, verified on return: contract
94/94, typecheck, production build green, tree clean).** ① The sidebar's bottom empties:
the account block moves to a new top bar (`app-topbar-account`), the three language pills
become a GLOBE menu, and a BOOK icon links to **`/docs`** — a new documentation page fed
by `lib/help-content.tsx`, which becomes the ONE front door for the doctrine prose that
#20's per-page ? drawers had collected (the per-page PageHelp affordances retired with
their entries migrated, pins moved with the strings). ② The assistant chat stops being a
floating panel: it is a full-height right-side DRAWER (Notion vocabulary) that PUSHES the
page rather than floating over it, resizable from its left edge, width-only persistence.
③ Every letter steps up a size — the app-wide type floor rose one notch (the 22px-h1 /
10-14px-everything-else gap this file complained about on 08-12 is finally closed from
the bottom), which is why the commit touches nearly every page. ④ The modal's cross-link
chips lost their arrows per the user's instruction. The other session left no narrative;
this entry is the reconciliation, written from the diff, not from memory of intentions.

## 2026-08-14 — the demo-prep script that refuses to fake its own refusal (#32)

**Context.** Every refused genesis job on this box carries only doctrine-untreatable
findings (v3 connectivity / v7 shared polylines), so the B5-2 fix-loop demo had no job
whose drawer shows an approval card. #32 (`backend/tools/make_b52_demo_job.py`)
manufactures one honest treatable refusal — `v6_input_volume_is_zero` — in the user's
demo workspace, verified end-to-end by an adversarial pass (7/7 claims independently
reproduced, byte-exact idempotence, zero contamination across 173-file tree hashes).

**The finding that shaped the design.** The real counts pipeline structurally CANNOT
produce a zero-volume input that gates as refused: `generate_demand` refuses
`genesis_demand_zero_observed_total` at REFUSE tier, the composition is all-or-nothing,
and `genesis_jobs` turns that into STATUS_ERROR with no gate verdict at all. So a
"fully real" path to this demo does not exist, and pretending otherwise would mean
weakening demand.py. The script instead hand-assembles ONLY the demand layer (the same
frozen dataclasses compose.py uses, one input at 0.0 vph), and keeps draft, fleet,
emission and the gate itself fully real. The manufactured layer is the point of the
demo, and the job name says so (`DEMO-수정루프-…`).

**Interface-gap deviation, recorded.** The job record is written via
`genesis_jobs.job_dir()` + `_upsert()`, bypassing `create_genesis_job`, because the
Korean demo marker cannot pass `_NAME_RE` (ASCII-only) — the same seam
`tests/test_genesis_jobs.py::_seed_job` already uses. Second seam: verification calls
`routers.genesis._render_fix_disposition` (the literal function the fixes route calls)
rather than re-deriving ask-first logic. Both are named in the script docstring; the
refuter confirmed the prose claims nothing stronger than the code.


---

## 2026-08-18 — #44: the shared-behaviour guardrail's premise collides with MRWA §5.2.5.1's intent

**Context:** unit #44 (`backend/services/agent/saturation_flow.py`) builds Table 5-6
car-following proposals whose MRWA-intended scope is NETWORK-WIDE (§5.2.5.1 calibrates urban
driving behaviour from one key-intersection measurement). Verified reality: the existing
guardrail `driving_behavior_shared_object_edit` (constraints.py) refuses in-place edits of any
driving behaviour with reference_count > 0 — its premise is that a shared-object edit is an
accidental blast radius, i.e. it assumes the *local* intent. Every W74 behaviour in the real
consultant model has reference_count >= 1, so the guardrail will refuse the very edit the
guideline prescribes.

**Decision:** #44 does not resolve the collision in code. The proposal carries an honest
`preflight_forecast` keyed on reference_count (the refuter proved scope cannot change the
engine's verdict, so the forecast no longer pretends it can), and the refusal happens at the
constraint engine exactly as before. Whether to (a) relax the guardrail for
CAR_FOLLOWING_LEVEL proposals, (b) auto-duplicate-and-retarget (changes what the number
represents — a modelling decision), or (c) keep refusing and require a human duplicate first,
is a human product call, not an agent's.

**Consequence:** until that call is made, live application of a Table 5-6 set on the real
model stops at `driving_behavior_shared_object_edit` — visible in the forecast before anyone
clicks. Two adjacent honesty rulings from the same unit, recorded so they are not re-litigated:
the 800–2200 pcu plausibility screen is OUR drafted candidate (extract Warn-tier), not a Main
Roads figure, and is now attributed as such in all three languages; partial approval of an
ax/bx_add/bx_mult set is detected and named by `verify_applied_row` (detection chosen over
structural locking, which would have meant editing gates.py/routers shared with every unit) —
the same check also names the real model's unsourced 'flyt updated' hand-tunings.


---

## 2026-08-19 — 사용자 결정: #44 가드레일 충돌은 (a) 좁은 예외로 해소한다

**Context:** 위 2026-08-18 항목의 3선택지에 대해 사용자가 **(a) 예외 허용**을 결정.
원문 지시: "a로 해" (2026-08-19, 설명 확인 후).

**Decision:** `driving_behavior_shared_object_edit` 가드레일에 **Table 5-6 차량추종 제안
경로에 한정된** 예외를 낸다. 근거: ask-first 승인 카드가 적용 범위를 문장으로 명시하고
("행태 N을 참조하는 링크 M곳에 함께 적용됩니다") 사람이 읽고 승인하는 구조이므로, 이는
가드레일이 막으려던 "모르고 저지르는 광역 편집"이 아니라 "알고 승인하는 광역 편집"이다.
가드레일의 원래 목적(의도치 않은 공유 객체 편집 방지)은 예외 밖 전 경로에서 그대로 유지된다.

**Constraints on the exception (구현 유닛 #44-b의 수용 조건):**
- 예외의 열쇠는 자기 신고가 아니라 **구조**여야 한다 — B5-2 승인 기계(PreapprovedWriteGate,
  1회 소모·대상 교차검증)에 결속된 Table 5-6 제안 출처만 통과. 직접 도구 호출·위장 페이로드는
  이전과 동일하게 거부.
- 파라미터는 **ax/bx_add/bx_mult만** — 화이트리스트의 나머지 5개와 W99 CC0–CC9는 예외 무관.
- **동의한 폭발 반경의 드리프트 검사**: 승인 시점에 명명된 참조 링크 집합과 적용 시점의 집합이
  다르면 명명 거부(동의는 명명된 반경에 대한 것).
- `preflight_forecast`는 엔진과 계속 일치해야 한다(#44 refuter의 핀 불변식) — 승인 경로는
  clean 예보, 그 외는 여전히 refuse 예보.

**Consequence:** 이 예외가 착지하면 #44 라이브 COM 실행(핸드오프 §6.2 커맨드)이 가드레일에서
멈추지 않고 끝까지 간다. 예외 밖 공유 행태 편집은 전부 종전대로 거부된다.


---

## 2026-08-19 — #47: grant의 제품 배선에서 나온 운영 사실 2건 (기록, 미해결 1건은 제품 판단 대기)

**Context:** #47이 Table 5-6 제안을 제품 경로(어시스턴트 턴 → 카드 → 승인)에 연결하며 grant를
인메모리 브로커에만 두는 설계를 채택 — 디스크의 어떤 파일도 grant를 물질화할 수 없음(위조 벡터
봉쇄가 목적). refuter가 뮤터블 참조 유입(브로커에 넣은 제안 객체를 밖에서 변조 → 승인 시 변조값
grant) 익스플로잇을 실증해 deepcopy 경계로 봉쇄했고, node_control 미선언으로 SIGNAL_TIMING을
회피해 CAPACITY_LIMIT을 얻는 rung 위조를 `NODE_CONTROL_UNDECLARED` 선언 요구 거부로 봉쇄했다.

**기록 1 — grant는 백엔드 재시작을 넘지 않는다 (의도).** 제안과 승인 클릭 사이에 서버가
재시작되면 세트가 소실되고 승인은 원래 공유-행태 가드레일 거부를 만난다. 대안(디스크 지속)이
바로 이 유닛이 닫은 위조 벡터이므로, 소실→거부(안전 방향)를 감수한다. 사용자 안내 문구가
필요해지면 UI 유닛에서.

**기록 2 — ax 인용 충돌 (제품 판단 대기).** Table 5-6의 여섯 행 모두 ax=2.5로 동일하므로, 같은
행태에 살아 있는 세트가 둘이면 ax 쓰기의 카드가 **최신 세트의 측정 출처를 인용**한다. 쓰기 값과
동의 반경은 어느 세트든 동일해 결과는 틀리지 않으나, 인용이 다른 측정을 가리킬 수 있다 — 이
저장소에서 인용 정확성은 제품이다. 봉쇄 선택지가 전부 제품 판단: (a) 모호 시 grant 거부 = 카드
자체가 죽음(#44-b가 끝낸 dead end의 재현), (b) 카드에 모호성 표시 = 새 페이로드+문구, (c) 세트
1개 정책(행태당 동시 세트 금지). 결정 전까지 명명된 한계로 유지.


---

## 2026-08-20 — 사용자 결정: #51 탐색 산출물에도 가드레일 예외를 (a) 확장한다

**Context:** #51(`cd9417a`)의 탐색 파라미터 3종은 전부 `set_driving_behavior_parameter` 경유
공유 행태 편집이라 `driving_behavior_shared_object_edit`에 막히고, #44-b의 예외는 Table 5-6
세트를 서명으로 한정하므로 닿지 않는다. 예보가 `GUARDRAIL_WILL_REFUSE`와 해소 2안
(`auto_selectable: False`)을 제시했고, 사용자가 **(a) 예외 확장**을 결정 ("for 51 do a",
2026-08-20). 국소화(b)는 기각 — 네트워크 전체를 채점해 찾은 값을 일부에만 적용하면 값의
의미가 바뀐다.

**구현 유닛 #51-b의 수용 조건 (#44-b와 같은 모양, 같은 좁음):**
- 열쇠는 구조: grant는 **완료된 탐색**(승자 벡터 + 원장 + `HOLDOUT_PASSED` + 전제조건 기록)
  에서만 발급. 서명이 행태·파라미터·값을 바이트 고정, 1회 소모 승인 게이트 결속, 반경 전부
  명명 가능할 때만(캡 초과 행태는 여전히 거부).
- 파라미터는 **탐색 공간의 3종만**, 값은 수용된 승자의 값 그대로(공표 범위 재검사 — 도착
  검증은 #51 refuter가 세운 그 벽).
- `CarFollowingGrant`의 검증 약화 금지 — 별도 grant 종(또는 가산적 일반화)으로, Table 5-6
  경로와 서명 공간이 섞이지 않게.
- 근거문에 탐색 출처 탑재: 후보 수·원장 참조·홀드아웃 통과·"MRWA 범위 X–Y 내 탐색" 인용.
- `preflight_forecast()`의 approved-path가 clean으로 뒤집히되 예보-엔진 불변식 유지;
  stand-down은 여전히 제약 1개만 침묵(레지스트리 diff로 증명).

**Consequence:** 착지하면 #51 라이브 러너 배선(별도 유닛) 후 탐색 승자를 승인 카드로 실제
적용 가능. 근거의 격이 Table 5-6(공표 표 조회)보다 한 단계 낮다는 사실은 근거문이 스스로
말한다 — 탐색 원장과 홀드아웃 증명이 그 자리를 채우는 것이 이 결정의 본질.

## 2026-08-20 — #50-d: the screen keeps the action, and the doctrine learns an address

**User decision (2026-08-20, roadmap row #50-d — verbatim six-item spec).** The calibration
screen owes the user an action, not an education. ① a "지금 할 일" banner derived from server
state only; ② at most one 자세히 fold per card; ③ every doctrine/guard/education sentence
evicted from the surface into ONE knowledge file (`docs/product/calibration-doctrine.md`,
ko/en/ja, D1–D17) + the in-app `/docs` page + assistant RAG retrieval, with the
"prose-bound-to-code" pins re-aimed at the file; ④ the ⑫ #51 guardrail section UI removed
("unnecessary developer side information") with backend enforcement byte-unchanged and the
#51-c display pins removed under this recorded authority (~26 pins, each removal commented);
⑤ empty-state folds removed; ⑥ rail 펼치기 → chevron, header status tally chips removed
("this stat does not help").

**Orchestrator decisions inside the spec's freedom (open to user override):**
- The evidence-rank sentence stays on the approval-card surface — approval-decision-material
  honesty, not developer info; the spec named forecast/chips/mono bars, not it.
- The manual station's one-line headline stays — it is the station's verdict, not background.
- The manual-row statusTally chips stay — #50-b's rule that a collapsed group must surface
  its urgent states outranks a blanket chip purge; the header tally described rows already
  visible, which is why IT went.
- "전부" in item ④ includes the drawer-shared SearchedParameterPanel's forecast display —
  the same developer-grade information is the same wherever it renders. The refuter named
  the residue honestly (minor): the per-write refusal verdict is now inferable from
  `consent_radius_phrase` + `/docs`' D16 account rather than stated per search.
- The banner follows the server's NEXT signal even past an IN_PROGRESS step (refuter minor,
  named): NEXT is the server's own "actionable now"; the banner invents no client judgment.
- Doctrine drift protection is DERIVED, not transcribed: the contract suite parses both
  copies (knowledge file ↔ help-content) and requires set equality over all 81 sentences,
  both directions — no third arbitration copy. An identical edit to both files is a change
  to what the product promises and routes through user approval per the file's own header.

**Refuter (protocol 17/17 units with real defects):** MAJOR — 23 of 27 items were pinned by
a short ko fragment while the ledger comment promised full-drift protection; a
meaning-changed ENGLISH reword of D5 in help-content survived the suite (proven by
mutation). Fixed by the parsed set-equality mechanism above; the exact mutation now kills.

**Operational fact (outside this unit, recorded).** At 13:56 KST a genesis draft
(`region_n36_3480_e127_3886.inpx`, source: genesis_draft) was uploaded into the default
workspace "기본 프로젝트" during the user's own demo recording, replacing the client model
as the active dataset AND destroying the workspace's `Scenarios/**.results/1.db` (the real
customer result database — a live-run artifact with no donor copy anywhere: not in other
workspaces, not in client-data/, not in the client zips). `uploads/current` keeps no
history; an overwrite is unrecoverable except by re-upload/regeneration. Effects and
resolution:
- 14 real-dataset tests failed environmentally (file-exists gate passed, content empty).
  User approved restoration; the 08-11 lineage copy (sha256 A712D640…) was re-uploaded via
  the app's own `POST /api/datasets/upload-inpx` (hash-verified) — all 14 healed.
- `test_agent_geh_ladder.py::test_the_availability_holds_on_a_real_customer_result_database`
  and `test_agent_tools_run.py::test_delay_measurement_truncated_by_queue_reproduces_against_a_real_result_db`
  now skip-with-reason (suite 12→14 skips). Regenerating the result DB needs a live Vissim
  run on the restored model — **user COM lane** (handoff-2026-08-18 §6.2 commands).
- The `real_counts_*` metadata pointer was also dropped by the overwrite; whether the
  default workspace needs its observed-counts workbook re-attached is a user call.

**RESOLVED same day (supersedes the two residues above):** the ORIGINAL client package was
found on this machine — `Downloads/Murdoch Drive_Scenario Model_0624.zip` (27.9 MB,
downloaded 2026-07-28). Two measurements and one action:
- `SouthStreet_Base.inpx` inside it is **byte-identical to A712D640…** — the restored model
  IS the client original, exactly (the 08-07 BE35 copy was the outlier, not the lineage).
- The package carries the customer's own result databases (Scenarios S000001–S000014,
  5 runs each) plus `SouthStreet_Base.vissimpdb` and 19 `Modifications/*.trax`.
- User-approved restore ("디비 재생성은 진행해"): repackaged zip (current A712 model FIRST
  + client Scenarios/vissimpdb/Modifications byte-verbatim from the original archive) through
  the app's own zip-upload path (`_save_zip_dataset`). Verified on disk: model hash unchanged,
  `S000001/1.db` byte-identical to the client copy, 14 scenario dirs, project db present.
  The two real-DB tests PASS again; full suite **5321 passed / 0 failed / 12 skipped** —
  the handoff's 12-skip shape, +3 doctrine tests. No live COM was needed after all.
- Still open (user lane): whether to re-attach an observed-counts workbook to the default
  workspace (the client package contains none — `real_counts.xlsx` was never part of it).
- **Discovery for the client-ask ledger:** the same Downloads folder holds
  `LMA_MurdochLink.png` (357 KB) — an LMA site drawing. The D1 ask ("LMA/LMB drawings,
  detector→movement, upgrades SCATS calibration site→movement") may be partially answerable
  from disk. User to confirm coverage.

## 2026-08-21 — #51-r: the runner is built offline, and three live rounds teach it what offline cannot see

**The unit** (`8bb010b`): VissimSearchRunner + thread job + one write route + a wired-branch-only
start control. Orchestrator decisions inside the spec: no budget picker on screen (the button
spends the server's own plan); behaviour choice is a named refusal when plural; the drawer's
session id travels by the existing `vissim:agent-session` broadcast (fourth listener, not a
fourth mechanism), and a search with no session to land in refuses by name before Vissim
spends a second. Refuter (19/19 units with real defects): an unwrapped sqlite read falsified
run()'s never-raises contract (structural guard + 16 offline tests made the docstring true);
an unguarded thread.start() wedged a workspace forever; a border-sky-300 reproduced the named
dark-remap trap (calibration sweep pin added; page.tsx's five, behind a documented-look note,
escalated to the user by a pin that watches the note).

**The live lane earned its keep three times** (`674ce5b`, real client model, this machine):
1. Scenario-managed exports refuse standalone LoadNet — the raw upload is the one file no
   product path ever feeds Vissim. Candidates now materialize via ArtifactManager.clone's
   exact transform (byte-equality pinned). `_make_network_standalone` was rejected BY
   MEASUREMENT: it sets simRes 10→2, changing the physics the workbook graded — a completed
   measurement with wrong numbers, the product's named worst class.
2. Vissim enforces standing-lateral ≤ driving-lateral at AttValue time — invisible to the
   per-field #85 probes (they probed each scalar alone where the coupling held). Write order
   is now constraint-derived with a proof one order is always legal; infeasible pairs refuse
   pre-COM by name. Couplings are one declared table; teaching the guardrail preflight is
   chip task_d451d977.
3. disconnect()'s Exit() is a request, not a join — every COM caller shares the gap; the
   search is the first caller that runs long enough to pay (3 leaked Vissim.exe / 13
   candidates, killed by hand). Bounded reap, set-difference-scoped: a human's own Vissim is
   structurally untouchable.

**Acceptance (run 3): 18/18 real candidate runs, 0 failures, 0 leaks, COMPLETE — winner tied
the baseline exactly (objective 1.8775), because on an uncongested network these three
parameters do not move volumes. accept_winner refused the no-improvement proposal
(HOLDOUT_NOT_IMPROVED); nothing reached the broker. That refusal is the product working.**
The congested scene (#49) is where the parameters have physics — a future demo build.

**Gate defects found while deriving the acceptance recipe (chips):** ENTRANCE_NOT_MEASURED
reads an evidence key nothing writes — dead on real data, its test fabricates the shape
(task_93c5b5dd); the rail's reality check is built TARGETLESS, so LADDER_NOT_EXHAUSTED is
unreachable via the route on any real workspace — the start button can only refuse honestly
until targets are threaded (task_2ebde59b).

## 2026-08-24 — the long-horizon run resolves #49's named follow-up: the ceiling was the clock, not the network

#49 (2026-08-19) measured the congested scene's physical ceiling at 900 s: approach storage
saturates (~1,238 m), Vissim holds the overflow OUTSIDE the network where it accrues neither
queue nor delay, and worst LOS caps at D (42.57 s) — CAPACITY_LIMIT unreachable by demand
scaling. #49 named two candidate follow-ups (장구간 런 / 다중 입력 스케일) and ran neither.

**Measured today (workspace 87c51893caa2, factor 2.0, --duration 3600):** the queued vehicles
DO digest over the longer horizon and accrue their delay — approach 759 reaches **LOS F,
229.8 s** (vs 42.6 s at 900 s), node 8 classifies **CAPACITY_LIMIT**, and the full #44 chain
fires end-to-end offline: measured saturation flow 1780 pcu → Table 5-6 row → bx_add 2.5 /
bx_mult 3.5, three proposals built, cited (op §5.2.5.1 Table 5-6 p.187), preflight-checked,
#44-b's approval-bound exception exercised in both directions, stopped at the designed
COM-apply boundary. The 장구간 런 follow-up is hereby resolved by measurement; 다중 입력
스케일 remains unexplored. Demo guide scene 4 now uses this workspace.

**Also measured (rail-targets unit live):** on the client model, every approach link carries
a 3-per-class vehicle-input trio (105/205/305 pattern), so the narrow single-candidate
demand derivation refuses everywhere by design — the class-ambiguity rule ("sum classes?
one object per class?") stays an open product decision before any wider automation.

## 2026-08-24 — 사용자 결정: 대전 현시도 23건은 (b) 위임 서명으로 처리한다

The user was offered (a) a bulk-confirm control (one click, individually recorded human
attestations) and (b) zero clicks — delegated signing, with the recommendation AGAINST (b)
stated plainly ("미확인 현시도는 신호 증거가 아니다" is the sentence this product sells).
The user chose (b), quoting the option back verbatim. Execution rules:
- The orchestrator (Claude) performs an independent second reading of all 23 diagram images,
  cross-checked against the C1-b vision proposals and deterministic cross-check flags.
- Attestations are written ONLY where a node binding legitimately exists in a generated
  draft; the signer string is honestly "Claude (이윤하 위임 2026-08-24)" — the delegation is
  visible in the audit trail, never disguised as a direct human click. (This differs from the
  2026-08-12 'c1b-acceptance' scripted-attestation removal: that one masqueraded as human
  acceptance; this one discloses itself.)
- Intersections with no bindable node ship as completed template Excels (현시입력+신호시간),
  ready for the #52 upload door whenever a network containing them exists.
- A 대조 리포트 records per intersection: my reading vs the model's, agree/disagree, and
  binding status.

## 2026-08-24 — 인터페이스 갭: 현시도 수출의 메모 행을 업로드 문이 거부한다 (우회 + task_114c3f1a)

Measured during the delegated 현시도 batch: `phase_template.build_export` writes
intersection-level notes (both `ExportEntry.notes` — the Daejeon timing-payload caveats —
and `proposal.notes`) as named rows with an empty 현시번호. `parse_template._read_rows`
refuses exactly that shape (`phase_template_row_bad_phase`) and poisons the whole
intersection via `refused_names`, so a real Daejeon review's export comes back through
POST /phase-diagrams/upload as **intersections=0**. The round-trip tests only cover
note-free fixtures, so the promise in the module docstring is unverified for the shape the
Daejeon drop actually produces.

Decision for the batch (no product edit inside a delivery task): the 22 corrected Excels
fold every note into Φ1's first movement 비고 (`"2차 정독: … | 신호시간 주석: …"`) and emit
no phase-less rows. Verified: all 22 re-parse with 0 refusals and movement-set equality
against the corrections ledger. The parser-side fix (accept memo rows as intersection-level
notes + round-trip tests WITH notes) is registered as chip **task_114c3f1a**.

**Resolved 2026-08-24 (unit 55a):** `_read_rows` now accepts the empty-현시번호 row shapes
`build_export` itself writes. A row with empty phase AND empty approach AND non-empty 비고 is
an intersection-level note (leading `메모:` stripped; empty-after-strip records nothing),
threaded into the stored proposal — proven through the HTTP upload door. An intersection with
ONLY note rows refuses via the previously-dead `phase_template_intersection_empty` instead of
becoming a phantom reading; a malformed assignment row (empty phase, non-empty approach)
still refuses `phase_template_row_bad_phase`. The refuter then caught a hole the fix itself
opened — a note row arriving before the phase rows could silently donate its 교차로 ID to the
intersection — closed with a new amber-idiom conflict refusal
`phase_template_row_intersection_no_conflict`: any two disagreeing non-empty IDs for one
intersection refuse it, note or phase rows, in any order (this also closes the pre-existing
silent first-wins between ordinary phase rows). The 23 Daejeon workbooks re-parse unchanged
(0 refusals, 0 notes — their Φ1-비고 fold from the workaround above remains valid as data).

## 2026-08-24 — 사용자 결정: 현시도 화면은 엑셀 업로드로 통합, 판독 확인 클릭은 제거 (#55)

User's words (verbatim intent): "아예 엑셀 올리는걸로 통합하자 — user uploads excel in the
template structure (template downloadable), and the page renders the diagram. no other extra
confirmation etc. keep it simple." This supersedes the review-first structure on
/genesis/phases: the AI-reads-image proposal review is no longer the page's spine.

Doctrine reconciliation (why this does NOT break C1-b): the confirmation click existed to make
a MACHINE reading into a fact. An Excel a person filled and uploaded is ALREADY that person's
own statement — demanding a second per-intersection click on their own words was ceremony, and
the user has now named it as such. Upload therefore stamps uploaded_by (server-side) as the
authoring word for the READING. What upload cannot carry — the binding of sheet approaches to
a generated draft's node/link ids (MappingAttestation's node_id/approach_links) — REMAINS a
human act at signals-build time, reached from the intersection card as a secondary action, not
as the page's default structure. generate_node_signals' refusal of unbound mappings is
untouched.

The page's new job: 템플릿 다운로드 + 엑셀 업로드 → per-intersection cards RENDER a phase
diagram (Φ columns, movement arrows) from the parsed rows. The template (v1) has no
approach-side column, so rendered placement is layout, not geography — the card must say so
rather than pretend bearings.

## 2026-08-24 — 인터페이스 갭 측정: 화면은 실행의 `name`을, 도구는 `id`를 쓴다 (#56 근거 정정)

사용자가 어시스턴트에 `agent_run_fe342b88`을 주었고 도구가 `RUN_NOT_FOUND`로 거부했다.
1차·2차 핸드오프는 원인을 "`parse_runtime_scenario_id`가 `SIM_` 접두사만 파싱하므로 화면
픽커 값 `agent_run_…`이 즉시 실패한다"로 기록했다. **측정 결과 그 기전은 틀렸다** — 픽커의
값은 이미 올바른 `SIM_…`이다.

측정(워크스페이스 `87c51893caa2` 바인딩, in-process):

- `simulation_runs.py:305` — `scenario_id = record["scenario_result_id"] or runtime_scenario_id(id)`.
  레코드 `fe342b88`은 `scenario_result_id='SIM_fe342b88'`, `name='agent_run_fe342b88'`.
  `list_completed_runtime_scenarios`는 `id`에 전자를, `name`에 후자를 담는다 — 그래서
  **화면은 `agent_run_…`을 보여주고 option value로는 `SIM_…`을 보낸다.** 페이지의 채점이
  정상 동작한 이유가 이것이다.
- `SIM_fe342b88` → `get_runtime_result_dir_for_scenario` / `tools_metrics._resolve_result_dir`
  둘 다 `runtime\simulations\fe342b88`로 해석. `agent_run_fe342b88` → 둘 다 `None`.
- **제2 차단 후보로 지목됐던 `:331` dataset_revision 동일성 검사는 이 실행에서 통과한다**
  (record == active, 문자열 동일). 배제 — #56은 이 경로를 "고치면" 안 된다.

따라서 결함은 기계의 거부가 아니라 **어휘**다: 사람이 읽을 수 있는 유일한 문자열이 어떤
도구도 받지 않는 이름이고, 거부는 올바른 id를 알고 있으면서(`detail.candidates`가
`list_scenarios()`의 id 전체를 담는다) 그것을 말해주지 않는다. #56의 수정은 (1) 페이지
컨텍스트에 선택 실행의 **도구 어휘 id** 탑재, (2) 표시 이름을 받았을 때 **유일하게** 해석되면
번역을 공시하며 받아들이고(#54의 only-unique-candidate 관용구) 아니면 올바른 id를 **명명하며**
거부, (3) 화면이 사람에게 보여주는 문자열과 도구가 받는 문자열의 관계를 표면화 — 셋 다
"조용한 실패 대신 명명된 이유" 독트린 안에 있다.

### 같은 날 추가 측정 — CTA는 후보 0 노드에 불가능한 행동을 지시한다 (#56 (b) 근거)

`derive_targets`를 두 데모 워크스페이스에 그대로 돌린 결과(in-process, 라우터와 동일한
`_derive_all` 인자 형태):

| 워크스페이스 | 노드 | reason | 후보 수 |
|---|---|---|---|
| `4dcf09f7604a` (demo0821) | 1 · 2 · **4** | `NO_VEHICLE_INPUT_ON_APPROACH` | **0** |
| 〃 | 3 | `MULTIPLE_VEHICLE_INPUT_CANDIDATES` | 3 |
| 〃 | 5 | 〃 | 9 |
| 〃 | 8 | 〃 | 6 |
| `87c51893caa2` (demo0824L) | 5 | 〃 | 9 | 
| 〃 | **8** | 〃 | **6** |

사용자 스크린샷의 노드 4는 후보가 **0개**다. 그런데 카드의 다음 행동은
"`get_vehicle_inputs`로 이 노드에 유입되는 차량 인풋(번호·현재 교통량·링크)을 지정해 다시
대조하십시오"였다 — **답이 존재할 수 없는 행동을 지시한 것**이고, 어시스턴트는 그 지시를
수행하려다 같은 도구를 반복 호출해 중복 가드에 걸리고 6회 한도를 소진했다. 두 reason은
서로 다른 사람의 행동을 요구한다: `MULTIPLE_VEHICLE_INPUT_CANDIDATES`(≥2)는 #54 픽커가
정답이고, `NO_VEHICLE_INPUT_ON_APPROACH`(0)는 관측 지점·site→node 매핑 보완이 정답이다.
#56은 CTA를 이 둘로 분기해야 하며, 후보 0에 픽커를 제안해서도 안 된다(#54의 픽커는 이미
후보 존재를 조건으로 렌더된다 — 카드 문구만 그 규칙을 어기고 있었다).

## 2026-08-24 — 계약 핀의 위협 모델 경계 (#55b 3패스에서 명시)

#55b의 refuter 2패스가 수정 7건을 전부 다시 뚫었다 — 매번 같은 방식으로: 핀이 잡는 표현을
피해 **다른 마크업**으로 같은 거짓을 그리는 것(`<polygon>` 금지 → `<path>` 삼각형·`<use>`
재사용, `<button>` 금지 → `role="button"` div, `false &&` 금지 → `display:none`). 이 게임은
소스 텍스트 정규식으로는 고정점이 없다: 표현 공간이 무한하기 때문이다.

경계를 명시한다. **이 스위트의 위협 모델은 (1) 선의의 미래 편집자의 부주의한 드리프트와
(2) refuter가 실증해 명명한 변이 유형이다. 테스트까지 고칠 수 있는 고의적 사보타주는 정적
소스 핀의 방어 대상이 아니다** — 그것을 막으려면 렌더 수준 검증(JSDOM류)이 필요한데, 이
리포의 계약 러너는 의도적으로 빌드 없는 원시-소스 핀 시스템이고, 렌더 하니스 도입은 별도
유닛 크기의 결정이다(원하면 사용자 결정으로 올릴 것 — 지금 #55b에 끼워 넣지 않는다).

그 경계 안에서, 유형을 닫는 관용구는 이미 이 리포에 있다(#50-d의 집합 동등성):
- 표현 우회 → **닫힌 어휘 핀**: 파일이 쓸 수 있는 SVG 요소 태그 전체를 집합 동등성으로
  핀(새 태그 등장 = 실패). 부분 금지(`<polygon>`만)는 반드시 우회된다.
- 숨김 우회 → **가시성 억제 관용구 전면 금지**: 순수 표시 컴포넌트에는 `display:none`·
  `hidden`·`opacity-0`·`sr-only`·`aria-hidden`이 있을 정당한 이유가 없다.
- 위치 우회 → **슬라이스가 아니라 영역 전체**: 조각 슬라이스의 경계 자체가 구멍이었다.
  판독 패널은 설계상 **표시 전용**이므로 "상호작용 어포던스 0개"가 구조 불변식이다.
- 언어 우회 → ko만이 아니라 **ko/en/ja 세 본문 전부** 정확 문자열 핀 (#50-d가 측정한
  바로 그 결함 유형: fragment pins let en/ja drift).
- 문서 우회 → 문장 조각이 아니라 **독스트링 블록 전체**를 핀(추가 문장도 실패).

## 2026-08-24 — 사용자 결정: 현시도 카드 단순화 — 도식+시간표, 짧은 확인 (#58)

User's words (verbatim intent), testing the live #55 page with a real upload: the machine
cross-check section that says it compared NOTHING ("대조할 시트 텍스트도 도면 이미지도
없습니다") is noise — "이건 필요없지 않아? 지워"; the Excel already carries 신호 시간 —
"이런것도 같이 표로 렌더해줘"; strip the complex fields — "수준 계획 선언 황색시간 근거
확인 메모 이런 복잡한거 빼고 현시도 + 시간 테이블로 보기 편하게"; make times editable —
"황색시간 및 시간 같은거가 수정가능하게"; and one short confirm — "'이 결박을 확인합니다'
같은 긴 버튼 말고 '확인' 버튼 하나만".

Execution rules (doctrine reconciliation):
- The cross-check section disappears ONLY where the machine genuinely compared nothing
  (excel_upload/blank media). Vision reviews keep their real cross-check flags — deleting a
  comparison that happened would be hiding evidence; deleting the sentence that no comparison
  happened is removing noise.
- Editing a timing value on screen is the uploader amending their own statement — same
  authorship rule as #55's upload; server-stamps who/when. The edited 황색시간 IS the amber
  declaration (the Daejeon sheets print 황색+전적색 combined and can never supply amber alone
  — the person typing the number was always the declaration; the separate 선언 field was
  ceremony once the table itself is editable).
- 수준/계획 auto-resolve when unique (#54's only-unique idiom); a picker appears only when
  several exist. 근거/메모 leave the default surface.
- The binding's one short '확인' button changes the LABEL and surface, not the act: the
  MappingAttestation it records is unchanged (node/link binding, server-stamped attester).

### #58 추가 지시 (같은 날, 빌드 중 사용자 보강)

"황색시간은 테이블에서 편집 가능하니까 그냥 놔두고, 어느 노드인지 고르는건 지도/도식이
있으면 편할 것 같은데 같이 놔줘 — 대시보드 페이지에서 왼쪽에 지도 오른쪽 내용 있는
것처럼." 결박 절의 노드 선택에 draft 지도를 붙인다: 지도 왼쪽/내용 오른쪽(대시보드 분할
관용구 재사용), 노드 후보를 지도에 표시하고 클릭 = 선택(양방향 하이라이트), 기존 draft
지도 컴포넌트(#19/#29 계열) 재사용 — 새 지도 금지. 클릭은 선택이지 확인이 아니며, 확인
행위는 여전히 '확인' 버튼 하나뿐. geojson 없는 draft는 명명된 부재로 강등.

## 2026-08-24 — 사용자 결정: HWP는 v1 제외 확정 · 승인 이력 세션 픽커는 위임 (R-5c)

- **HWP 제외 확정** ("hwp 제외하고"). Track R의 산출 포맷은 DOCX(+도판 zip)로 고정. HWP는
  기존 도구 조사 후 별도 검토(요구사항 12행의 원래 방침) — 로드맵에 재등재하지 않는다.
- **실제 회사 보고서 DOCX 없음 확정** — R-4의 실측 대상 교체는 없던 일로. 대신 사용자
  테스트용으로 가상 스타일 샘플 2–3종(서로 다른 서식)을 생성해 전달한다(생성 프로파일
  JSON = 각 샘플의 정답지, 추출 왕복 사전 검증).
- **승인 이력 도판의 session 픽커: "너가 알아서 해"** — 위임. 결정: R-5b가 명명한 갭을
  소형 유닛 R-5c로 착지시킨다 — 백엔드에 "승인 원장을 가진 세션" 목록 라우트(있는
  것만, 발명 없음), /report 상단에 픽커, 선택 시 approval_history_table이 실제 데이터로.
  발명 매핑 금지: fetchAgentDownloads(쓰기 저장 세션)와 다른 집합이므로 원장 존재를
  서버가 직접 측정한다.

## 2026-08-24 — 사용자 결정: 수요 클래스 규칙 = (a) 비례 공동 조정 (#59)

The class-ambiguity question open since the targets unit (`70ad934` measured: every client-model
approach link carries a 3-class vehicle-input trio, so single-candidate demand derivation
refuses network-wide) is now DECIDED by the user: **(a) 셋을 비례로 함께 조정 — 승용차:버스:
트럭 구성비 유지** (offered with the recommendation attached; user chose it verbatim).

Execution rules for unit #59:
- A node's candidate inputs that share ONE link group into a single COMPOSITE candidate
  (the link is the unit of choice, not the class input). A node with exactly one such group
  auto-resolves where it refused before; several groups remain a human choice (#54 picker
  offers link-groups — 2 choices instead of 6 raw inputs on the demo's node 8).
- A demand adjustment against a composite target splits the total change across members
  proportionally to their CURRENT volumes (mix preserved). All-zero members = proportional
  split undefined → named refusal, never an invented split. The proposal/approval card shows
  the per-member split (auditability — the reviewer approves the split they can see).
- The decision is carried as a named assumption token (CLASS_MIX_PRESERVED family) beside
  ASSUME_INPUT_DRIVES_NODE_TOTAL in every audit block it touches.
- Existing #54 single-input mappings stay valid unchanged (the node's latest word rules are
  untouched); the store gains group-shaped candidates without breaking old records.

## 2026-08-25 — 사용자 테스트 1차 피드백: 화면 정리 5건 + /report 프로파일 문 + 데모 잠금 킷 (#60/#61)

User's live test of the pushed build produced these decisions (their words paraphrased tightly):

**#60a (화면 정리, frontend):**
- 황색 선언의 두 안내 문장("이 값이 아래 네트워크 결박에…", "yunha님이 …에 선언한… (이전 값: 없음)") 제거 — 표가 곧 선언이라는 규칙은 유지하되 산문은 걷어낸다.
- genesis 영역 페이지: 폴리곤/바운딩박스 설명 문장 제거, 출처 기록(provenance: 인스턴스·
  받은 시각·sha256·라이선스·보낸 질의) 블록은 펼치기 뒤로.
- 결박 절의 노드 픽 지도가 다크 테마 위에 흰 배경 박스로 떠서 식별 곤란 → 테마 배경.
- 보정 노드 타일 높이 불일치(진단 줄 있는 카드만 큼) → 균일 높이.
**#60b (/report 정리 + 프로파일 문):**
- 잔글씨(절별 MRWA 인용문 등 문단성 텍스트)를 기본 화면에서 제거 — "make a separate
  preview file and project texts as md format. text preview only, no style preview" →
  텍스트 전용 MD 미리보기 표면 신설, 페이지에는 행동만.
- 과거 보고서 DOCX를 올릴 문이 화면에 없음(R-4는 백엔드만, R-5b는 읽기만) → /report에
  프로파일 업로드 버튼(auth, R-4 POST 배선).
**#61 (장면 4·6 잠금 해제 킷) — 측정된 사실:**
- demo0824L(87c51893caa2)에는 genesis 드래프트가 없어(genesis/ 디렉터리 부재) 현시도
  결박·⑥ 완료가 구조적으로 불가; step ⑥은 controllers==0 && reviews==0일 때만
  NOT_APPLICABLE(calibration_rail._step_6 측정).
- 혼잡 워크북은 설계상 2노드(수요 노드 8 + 대조 5) — 홀드아웃(≥4)을 열려면 다노드
  워크북 + 신규 런(revision) = COM ~1h 재제조.
- 킷 구성: 하네스 확장(_build_demo_workbook 다노드) → COM 재제조(오케스트레이터 레인,
  백그라운드) → 같은 ws에 demo-crossroads.osm genesis 잡 → 사용자 클릭 2개(블랭크 현시도
  확인 + Table 5-6 전체 프롬프트) → 장면 4·6이 잠금 해제까지 완주 가능해짐. 대본은 해당
  장면만 해법 동반 재발행.

## 2026-08-26 — 사용자 실사용 4보고 배치: 측정 결과와 유닛 절단 (#62–#65)

사용자가 데모 리허설 중 네 가지를 보고했다. 각각을 소스·디스크에서 측정한 결과와,
그로부터 자른 유닛. 측정은 오케스트레이터(계정 #3)가 직접 수행했다.

**보고 A — 결박 절: "이 초안에서 이 교차로의 후보 노드를 찾지 못했습니다".**
측정: 초안 `region_n36_3514_e127_3839`(ws 87c51893caa2, job 3e11f5fc9c4c)의 census는
**노드 25개·링크 82개**를 갖고 있다. 서버 `node_candidates`(phase_diagram.py:1530)는
점수 무관 `ranked[:8]`을 돌려주므로 응답이 0이 될 수는 없다 — 즉 화면의 0은
**아직 불러오기가 실행되지 않은 상태**다. 결함은 둘:
1. `DraftNodePickMap.tsx:152`의 부재 문구는 candidateNodeIds가 비어 있기만 하면 나온다
   — 탐색이 실행된 적 없는 시점에도 "찾지 못했습니다"라고, 일어나지 않은 탐색의
   결과를 주장한다(정직한 부재 위반: 부재의 이름이 틀렸다). `loadCandidates`는
   jobId 없이 누르면 소리 없이 no-op이기도 하다.
2. OSM 초안의 노드 이름은 전부 "Node <osmid>"라 한글 교차로명과의 겹침 점수는 항상
   0.0이고, 정렬은 node_id로 떨어진다. 즉 **25개 중 id가 낮은 8개만 임의로 제시되고
   나머지 17개는 무언의 상한에 잘린다** — 맞는 노드가 제시조차 안 될 수 있다.
   ("no silent caps" 위반.)
→ **유닛 #62**: (백엔드) 이름 신호가 전무하면 초안의 전 노드를 후보로 반환하고,
   상한이 실제로 자를 때는 total/returned를 공개; (프런트) 생성작업 선택 시 후보
   자동 로드(읽기 전용 fetch — 서명 행위 아님), 지도 상태 3분리(미탐색/탐색-0/탐색-N).
   클릭=선택뿐·확인=유일한 기록 행위 불변식은 그대로.

**보고 B — demo0825에 채점할 실행이 없어 AI CTA도 없음.**
측정: ws acbc7ea1edab(demo0825)에는 `report`·`uploads`뿐 — `calibration` 디렉토리도,
시나리오 결과 DB도 없다. 내가 돌린 demo0825 증거는 하니스 레인
(`runtime/calibration-loop-live-check/artifactsdemo0825`)에 있고 제품 워크스페이스와
무관하다. 채점할 실행 목록은 `list_scenarios`(results_reader)가 활성 데이터셋의 결과에서
읽는다 — **시뮬레이션이 한 번도 실행되지 않았으므로 목록이 비는 것이 정확한 동작**이다.
운영 답: /simulation에서 실행 → 완료되면 픽커에 등장. 다만 빈 픽커가 "선택하세요"만
보여주고 **왜 비었는지·어디서 채우는지를 말하지 않는 것**은 정직한 부재 위반
→ **유닛 #65**(소형): scenarios.length===0일 때 "완료된 실행이 없습니다 — ③ 시뮬레이션에서
   먼저 실행하세요" + 경로 안내.

**보고 C — 바이올렛 프로파일 적용 DOCX: 자모 분해("ㅅㅐㅁㅍㅡㄹ…") + 서체 뒤섞임.**
진단(스크린샷 기준, 빌더가 소스에서 확정할 것): "스타일 프로파일:" 줄의 프로파일명은
Mac 업로드 파일명에서 오고, macOS는 NFD라 Word가 자모를 낱자로 그린다 → 도착 문에서
NFC 정규화. 서체: 프로파일의 제목/본문 서체가 모든 문단 클래스에 일관 적용되는지,
런의 rFonts에 **eastAsia** 속성이 설정되는지 측정 — 한글 런이 폴백 폰트로 떨어지면
안 된다. → **유닛 #63**.

**보고 D — /report 작성 화면: MD를 날것으로 보여주지 말고 렌더된 미리보기로 +
잔글씨가 여전히 많음.** 이전 결정("text preview only, no style preview")은 유지 —
스타일 미리보기가 아니라 **마크다운 구조(제목/표/목록)의 렌더링**을 요구하는 것.
부재 한 줄들은 카드 표면에서 개수 칩으로 접고 원문은 자세히 뒤로.
→ **유닛 #64**.

실행 순서: #62(사용자 실차단) → #63 → #64 → #65. 프로토콜 동일: 빌더→반박자→독립 재검증→1유닛 1커밋.

## 2026-08-26 — 데모 중 보고: 노드 점·라벨이 줌을 따라 커짐 (#66)

사용자 실측(525노드 초안): 줌인하면 노드 원과 id 라벨이 지도와 함께 확대되어 화면을
뒤덮는다. 측정: `DraftLocalMap.tsx` — `NODE_R = 5.5`(뷰 단위 고정)와 `fontSize={9}`가
줌 그룹 **안**에서 그려져 view.scale(최대 24)에 그대로 곱해진다. 선(stroke)만
`vectorEffect="non-scaling-stroke"`로 보정돼 있고 반지름·글자는 무보정 — 24배 줌이면
점 하나가 264뷰단위. **유닛 #66**: 반지름·글자 크기를 1/scale로 보정해 화면 크기
일정하게; 라벨은 밀도 임계(줌 낮을 때 525개 라벨 겹침) 아래에서 숨기되 <title>
툴팁·클릭·선택 불변식은 유지. 클릭=선택뿐 불변식 그대로.

같은 세션 답변 기록(코드 변경 없음): side-input 토큰 = 확인된 현시도들이 검증·계수되어
signals 입력으로 **스테이징**된 상태(적용은 다음 생성이 소비할 때); 수준/계획 선택 =
모델링할 시간대의 계획 **하나**만 고르면 됨(전 계획 입력 불요, 시트의 TOD 문구는
`time_of_day`로 저장만 됨). 자동 TOD 프로그램 전환은 후보 유닛으로 답변에서 제안됨.

## 2026-08-26 — 데모 2차 보고 배치: #67·#63b·#68 측정

**#67 (UI 3건, 사용자 축자)**: ① /simulation이 사이드바에 없음 — #65 배너로만 진입 가능
→ ③ 시뮬·보정 그룹에 항목 추가. ② 보정 컨텍스트 바의 "데이터" 라벨이 두 줄로 꺾임
(스크린샷) → nowrap. ③ #65의 0건 배너가 문구만 있어 클릭 가능성이 안 보임 → CTA 버튼
스타일로 ("문구가 클릭되게 하지 말고 CTA 버튼으로").

**#63b (측정된 실결함 — 제목 서체)**: 사용자 "본문은 SERIF인데 타이틀은 왜 arial 느낌?"
→ 저장된 바이올렛 프로파일 재합성 해부로 확정: Title/Heading 스타일의 rFonts에
`w:eastAsia="궁서"`가 제대로 실리지만 python-docx 기본 템플릿의
`w:asciiTheme/eastAsiaTheme="major*"` 속성이 함께 남아 있고, **Word는 테마 속성을
리터럴보다 우선**하므로 제목이 테마 major 폰트(산세리프)로 그려진다. Normal은 테마
속성이 없어 본문만 궁서/Times 정상. 수정: 리터럴 서체를 쓸 때 테마 속성 4종 제거,
#63 워커를 테마-우선 정밀도로 강화(테마 속성 잔존 = 미해결로 판정).

**#68 (측정된 게이트 결함 — INPX-only 실행 거부)**: demo0825(업로드 kind=inpx)에서
"시나리오(.vissimpdb) 포함 프로젝트 ZIP 필요" 거부. 측정: `_simulation_capabilities`
(routers/simulations.py:364)의 `has_uploaded_zip = source=="uploaded" and kind=="zip"`이
유일한 차단 사유이고, 러너는 `_simulation_source_inpx()` 하나만 소비(RunContinuous,
클론 작업). dataset_revision조차 "project.vissimpdb:missing|Scenarios:missing"을 허용.
→ kind=="inpx"도 source_inpx 존재 시 can_run, 프런트 문구를 실요구(Windows+pywin32+
inpx)로 정직화. ZIP 전용 기능(클라이언트 결과 DB 대조)은 별개로 계속 부재 명명.

같은 턴 답변 기록: TOD/수준·계획 질문(#66 항 참조), side-input 토큰 의미.

## 2026-08-26 — 사용자 지시 2건 추가: #69 재생성 CTA · #70 TOD 자동 전환

**#69 (사용자: "현시도 업뎃이 생기면 자동으로 재생성을 제안하는 CTA 넣는게 좋지
않을까? 자동으로 현시도 넣어서")**: signals side-input 스테이징 성공(토큰 발급) 시,
그 토큰을 **자동으로 붙인 재생성**을 제안하는 CTA를 토큰 패널에 추가. 원칙: CTA는
제안이고 실행은 클릭 — 재생성은 서명 행위가 아니라 잡이므로 클릭 하나로 시작 가능.
백엔드에 side-input 동반 재생성 경로가 이미 존재(`genesis_regenerate_parent_has_
side_inputs`, genesis_jobs.py ~7548) — 빌더는 결박 절이 바인딩된 생성작업을 부모로
그 문에 토큰을 배선하고, 부모가 특정 안 될 때의 정직한 부재를 명명할 것.

**#70 (사용자 승인: "이것도 해줘")**: 하루 전체/다시간대 런을 위해 시트의 `time_of_day`
문구로 **Vissim 신호 프로그램 자동 전환**(controller 다중 SignalProgram + 프로그램
스케줄) 구성. side-input 스키마가 노드당 복수 계획+시간대를 실을 수 있게 확장,
signals 생성이 프로그램들과 전환 스케줄을 배출. 시간대 문구 파싱 실패는 계획별 명명
거부(전체 오염 금지). 대형 유닛 — 마지막 검증은 라이브 COM 왕복(오케스트레이터 레인,
C2/S6 전례)으로 프로그램 전환이 저장·재로드에서 보존되는지 실측.

## 2026-08-26 — #69 재생성 CTA: 계층 의미론 결정 + 반박 결과

빌더 측정: #38의 기존 재생성 문은 side-input 동반을 구조적으로 거부(스키마 extra=forbid,
기하-전용 재유도, "no unit has made yet" 주석) → #69가 그 결정을 내리는 유닛이 됨.
**형제 문** `POST /api/genesis/jobs/{job_id}/regenerate-with-side-inputs` + 계보 종류
`signals_regeneration` 신설, #38의 원천 검증 거부 4종 재사용.

반박(변이 7종 중 5격추/2갭): 실결함 3 — ① **2회차 루프 죽은 끝**(재생성 자식을 부모로
다시 재생성 시 `parent_has_side_inputs`가 낡은 "no unit has decided" 문구로 영구 거부 —
CTA가 유도하는 바로 그 흐름, 끝까지 재현), ② 같은 문구의 #38 공유 지점 doc-rot,
③ 잡 상세 페이지 계보 분기가 key-존재로 협착해 signals_regeneration이 "실 Vissim 확인"
으로 오표기(#38부터의 선재 버그, #69 성공 링크가 도달 경로를 넓힘). 갭 2 — 신규 문의
wrong-kind 토큰 무테스트, auto-navigate 부재 무핀.

**오케스트레이터 의미론 결정**: 이미 합성된 잡 위에 **두 번째 층**을 얹는 것은 계속
거부한다 — 병합/대체(supersede) 설계는 어느 유닛도 내리지 않은 결정이며, 그 사실을
거부 지점 주석에 #69 참조와 함께 명명한다(향후 #71 후보). 단 메시지는 진실을 말한다:
1층(미합성 부모)은 #69의 지원 경로이고, 반복의 정직한 우회는 **원 부모에서 수정된
토큰으로 재생성**(오늘 동작함)이며, 2층은 미결정으로 명명. CTA는 선택된 생성작업이
이미 side input을 가지면 **클릭 전에** 그 사실을 공개한다(사후 409가 아니라).
계보 협착은 kind 값으로 근본 수정.

## 2026-08-26 — 가정 S7 해소: TOD 스케줄 요소, Vissim이 축자 수락 (#70)

오케스트레이터 라이브 COM 왕복(크로스로드 초안 + 2-프로그램 TOD, 명시적 from="0"
자정 랩 엔트리 포함): LoadNet 수락, .err 없음, SaveNetAs 후 `<dailyProgList id="1"
name="TOD"><dailyProg from=... prog_id=...>`가 **요소명·속성명·값 전부 동일**하게
귀환(KEPT_VERBATIM, differences=[]), 프로그램 2개 각자 주기(78000ms)와 함께 생존.
가정했던 이름들이 Vissim 자신의 이름이었다. 판정 계기는 반박 2라운드에서 이름
변경·탈락·부분 손실을 전부 REWRITTEN으로 분류함이 실증된 강화 분류기. S7-c census는
사장된 COM 메서드명 2건(open_net·AttributesHelp)이 발견돼 수정 + AST 자기-검사
가드 신설. 정착 기록은 signals.py·발행 XML 마커·프로브 docstring·layers summary
(`schedule_element_verified: true` + 일자·계기 증거)로 전환.

## 2026-08-27 — 사용자 3차 실사용 배치: #72 절단 + 정정 1건

**#72 시뮬레이션 문을 UI에서 끝까지 (4건 묶음)**:
① **시나리오-옵션 실행** (핵심): 사용자 "실행 가능한 시나리오 목록을 불러오지
못했습니다 — is this scenario essential?" → 측정: 백엔드 `SimParams.scenario_id:
str | None = None`, 기본 런은 pdb/Scenarios 무접촉(#68에서 트레이스 완료)인데
프런트 `canRunSimulation`이 `scenarios.length > 0`을 요구(page.tsx:177) — INPX-only
데이터셋은 시나리오 목록이 구조적으로 없으므로 영구 잠금. 수정: 시나리오 픽커를
선택 사항으로(기본 실행 = 시나리오 없음 명시), 게이트에서 `scenarios.length` 제거,
목록 fetch 실패/빈 것은 "기본 실행만 가능"으로 정직 표기.
② 시뮬레이션 페이지의 "데이터" 라벨 두 줄 꺾임 — #67이 보정 페이지만 고침(스크린샷
실측, 같은 결함 형제 지점). nowrap.
③ /report 하이드레이션 경고: Grammarly 확장이 <body>에 data-gr-* 속성 주입 —
layout.tsx <body>에 suppressHydrationWarning (html에는 이미 있음).
④ **사용자 정정 (축자)**: "i didnt mean you to make the whole sentence as cta.
just put cta button at the end of sentence." — #65/#67의 0건 배너를 문장(텍스트) +
문장 끝 CTA 버튼으로 재작업. 보정 페이지의 해당 배너 구조 변경.

**같은 턴 답변 기록**: 은하수네거리는 클라이언트 신호 zip(23장)에 부재 — 합성 샘플로
데모 킷에서 제작 예정(명시 라벨). "census 없는 지역 초안" = 좌표 상자만 있고 생성이
네트워크 census를 만든 적 없는 지역 기록. 재생성 거부 "진입 링크 [1341] 현시 없음" =
확인된 노드의 접근로 하나가 현시에 결박되지 않음(설계된 거부 — 결박 표에서 그 행을
채우고 재확인→재스테이징→재생성이 루프). #69 계보 라벨("signals 입력 추가 재생성")
정상 작동 실증.

**대형 후속(사용자 지시)**: 처음부터 끝까지(생성→수요→현시도→결박→재생성→실행→
채점→보정→보고서) 비전문가가 UI만으로 완주하는 데모 대본 + 샘플 킷 — #72 랜딩 후 작성.

## 2026-08-27 — 라이브 완주 검증이 적발: 엑셀 리뷰의 결박 도달 불가 (#73)

오케스트레이터가 브라우저에서 처음부터 완주 중 실측(계정 claudedemo, 프로젝트
완주검증-claude): 영역 추출(wonsinheung-e2e, 노드 71)→현시도 엑셀 4장 업로드까지
전부 통과했으나, 결박 절에서 생성작업 선택 시
`GET /phase-diagrams/{id}/network-candidates` 가 **409
`phase_diagram_no_sheet_text` ("Review ... holds no extracted sheet text.")**.
엑셀 템플릿 리뷰는 구조상 시트 텍스트(대전 PDF의 추출 텍스트 레이어)가 없다 —
화면 스스로 "이미지 없음 — 엑셀 업로드로 만든 검토입니다"라고 말하면서, 후보
라우트는 그 텍스트를 요구한다. 즉 **주 사용 경로(템플릿 업로드)에서 #62 결박
지도가 아예 도달 불가**. (사용자가 ddd 프로젝트에서 본 "노드 후보 탐색이
실패했습니다"의 실원인 — 앞서 census-부재로 설명한 것은 이 케이스에 한해 오진,
정정함.)

**#73**: 후보 라우트는 시트 텍스트 없이도 동작해야 한다 — 랭킹의 이름 신호는
리뷰가 이미 가진 intersection_name으로 충분하고(#62 이후 신호 전무 시 전 노드
반환이므로 이름 자체가 없어도 정직하게 동작), store.sheet()의 텍스트 요구를
후보 경로에서 우회(전용 접근자)하되 대전-PDF 리뷰의 기존 동작은 불변. 근본
질문(엑셀 리뷰에 sheet-text 게이트가 필요한 다른 경로가 있는가)은 빌더가
측정해 함께 보고.

## 2026-08-27 — 라이브 완주 검증 적발 2호: 밀집 정션의 노드 박스 겹침 (#74)

완주 검증에서 UI 기본 실행(#68+#72)으로 시작된 첫 실 런(SIM_bba7f4eb, wonsinheung-e2e
초안 71노드)이 Vissim COM "The simulation could not be continued"로 사망. .err 실측:
**Error — Nodes overlapping** 다발 (Node 1↔65, 2↔66, …). 원인 =
`draft.NODE_HALF_SIZE_M = 15.0` 고정(30m 박스, practice.inpx 예제에서 온 값) —
OSM 밀집 정션(30m 미만 간격)이 있는 실지역 추출에서 박스가 겹치고 Vissim이 오류로
실행을 거부. 데모 십자로 프로브(성긴 노드)에서는 발현 불가였던 실결함.

**#74**: ① 노드 박스 반폭을 결정적으로 축소 — min(15m, 최근접 방출 노드 거리의
0.45배), 하한(예: 2m)과 함께; 쌍별 비겹침을 테스트로 보장(밀집 합성 케이스).
② 실행 오류의 정직성: 잡 오류가 원시 COM 문구만 싣고 .err 내용을 버림 — 워크디렉
.err의 Error 행들을 잡 오류/화면에 동반(및 .err 경로 명시).

같은 검증에서 통과 확인된 것: 프로젝트 생성→영역 추출(둔산, 링크225/노드71)→
자동 데이터셋 등록→현시도 엑셀 4장 업로드→수요 워크북(합성 1092레코드) 업로드→
접근로-링크 매핑→수요 토큰 발급→#72 기본 실행 게이트. 참고: region 기본 중심의
주석은 '둔산'이며 실제 추출도 둔산로 일대 — 시트(원신흥) 이름과 무관하게 결박은
사람이 정하므로 데모 서사에는 지장 없음.


## 2026-08-27 — 완주 검증 3~5호: 결합 생성이 처음으로 끝까지 밀리며 드러난 층들 (#75 계열)

처음으로 수요+signals 동반 생성을 UI에서 끝까지 밀어붙인 날. 각 거부는 설계된
정직-거부가 정확히 작동한 것이며, 동시에 그 거부들이 실사용 경로의 미완 층을
차례로 드러냈다. 시간순:

**3호 — 화물_소형 차종 (수요 차단)**: 대전 카운트의 화물_소형은 어댑터가 의도적
canonical=None(소형화물차는 경차 거동이나 LIGHT/HEAVY 어느 쪽도 원천이 답하지
않음), demand.py는 미배정 관측 차종을 거부 → **실제 대전 파일(이 차종이 사실상
항상 존재)로는 수요 레이어가 원천 불가**. FleetSettings에 per-class 해소 문 없음.
칩 task_f8230358 (사용자 결정 문 설계). 데모 킷은 합성 샘플이므로 화물 소형 열을
전부 빈칸("blank is not zero" — 기록 미발명)으로 재합성해 우회.

**황색 스냅숏 재확인**: 황색시간 선언(#58 표 셀) 후에도 스테이징은 **확인 시점의
부동 스냅숏**을 읽으므로 재확인이 필요하다 — 거부문("선언한 뒤 다시 확인")이
정답을 이미 말하고 있었고 실측으로 검증됨. 단, stale_reasons는 황색 선언 변경을
staleness로 표시하지 않아 재확인 필요가 칩으로 안 보임(소갭, 기록만). 더 나은
데모 경로: **템플릿의 황색(초)+근거 열을 채우면 앱 내 선언·재확인이 불필요** —
킷 샘플에 반영.

**4호 — 현시 수 > 진입 링크 수 (결박 모델 한계)**: 실제 대전 4~5현시 계획(보호
좌회전·유턴 현시)은 결박 표의 링크당-현시-하나 모델로는 현시 전수 커버가 수학적
불가 — 실측: 노드 66=3링크vs4현시, 68=2vs5, 71=2vs3, 32=4vs5, **4/4 전원 통과
불가**. 칩 task_8102dec3 (링크→현시 집합 또는 (링크,이동류)→현시 확장; 엑셀의
현시×접근로 매트릭스를 프리필로). 데모는 합성 은하수 샘플을 4현시로 재작성
(Φ5 시간을 Φ2에 흡수, 주기 180 유지)해 4링크 노드에 전 현시 커버로 우회.

**5호 — 신호가 게이트에 처음 도달, V5·V9 실패 (#75)**: 새 프로젝트 demo-full-run
(e8c3de44f0b8, 동일 bbox 재추출 — **잡 da890a1d 결정성 실측: 노드 32 진입
76/79/150/151 그대로**)에서 결합 생성 572dc4f1828d이 수요·signals 모두 방출
성공 후 **게이트 refused**: V9 = 신호 정지선 4곳 모두 queue counter 없음(방출
코드 자체가 부재), V5 = 결박 노드 32의 머리 배치가 이웃 노드 33을 멤버십 규칙
(head 링크의 커넥터 소속)으로 신호화시켰는데 33의 자기 진입(service 78)은 머리
미도달. gate.py는 오라클(불변) — 방출 측이 게이트를 만족하도록 #75 빌더 파견.
부수 실측: 내가 고른 노드 32는 4진입 전부 service(주차장 골목 교차) — 데모
결박은 실도로 노드 8(둔산로73번길×대덕대로220번길, living_street)로 이동 예정.

**수요 투입의 경계 규칙 (데모 설계 확정)**: 매핑된 내부 링크는 routing만 받고
차량 투입은 **경계 진입 링크에만** 놓인다(demand.py `_classify_seeded_links` —
"traffic entered upstream, a second input would double it"). 572dc4f1828d 실측:
`links_seeded_routing_only: 4`, 투입 0 → 시뮬이 비어 돌 뻔함. 데모 교차로는
진입 중 경계 링크가 있는 노드여야 하며, 실측 후보: **노드 8 (진입 7/10/11/14 중
10·14가 경계 진입)** — 투입 2 + routing 4가 성립하는 유일한 실도로 4지 교차.
