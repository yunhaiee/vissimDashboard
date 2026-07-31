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
