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
