# C1-b — 현시도 제안+확인 (phase-diagram proposal + human confirmation)

Status: ⬜ QUEUED (dispatch after the 2026-08-12 in-flight units land: Track G fix, B3 fixes, V6 gate clause, commits).
Owner track: G (backend proposal pipeline) + X (confirm UI). Parent: roadmap v2 §5.C row C1.
This unit implements roadmap C1 **option (c)** — infer, then human-confirm — and yields
**option (b)** (manual-input UI) as a byproduct, because the confirm screen with an empty
proposal IS the manual entry form.

## Why now

C1 is ⛔ blocked on the client transcribing the 현시도 into tables. But all 23 대전 sheets are
already in `client-data/`, and the Track G unit (2026-08-12) gives the product a door that
accepts an approach→phase mapping as data (`signals` side-input, `approach_phases` per node).
The only missing piece is *producing* the mapping. A vision proposal + human confirmation
produces it without waiting on TK and without violating 추측 금지 — because nothing is ever
used unconfirmed.

## Pipeline (5 stages)

1. **Render.** For each 대전 sheet PDF, render the page region containing the 현시도 diagram to
   an image (offline tool, backend). Store image + sha256 + source page provenance.
2. **Propose (vision LLM).** Claude (the product's existing LLM; `ANTHROPIC_API_KEY` from
   `backend/.env`, never printed) reads the image and proposes, per 현시: which approaches/
   movements receive green (e.g. 현시 1 = 북↔남 직진, 현시 2 = 남북 좌회전). Output is a strict-
   JSON proposal artifact: per-phase movement list + per-phase ambiguity notes + model id +
   image sha + date. Strict-JSON path MUST bypass `clean_generated_answer()` (CLAUDE.md).
   A diagram too ambiguous/degraded to read → the proposal REFUSES with a named reason
   (never a low-confidence guess dressed as a reading).
3. **Cross-check (deterministic).** Validate every proposal against what the text layer already
   parsed: phase count and phase ids must match the sheet's parsed plan; proposed approaches
   must exist for that intersection. Mismatch → proposal flagged for the human, never
   auto-corrected.
4. **Confirm (human, UI).** A review screen: rendered 현시도 image side-by-side with the
   editable proposal. The human confirms or corrects per intersection, and binds the sheet's
   intersection to the network node (propose nearest/name-matched node; human confirms).
   The confirmation is recorded as an **attestation** — who, when, what changed vs the
   proposal, image sha — the same doctrine as V1's operator attestation. ko primary UI.
5. **Emit.** Confirmed mapping → exactly the Track G `genesis.signals.v1` side-input shape
   (`daejeon_sheet` plan route + `approach_phases`) → genesis job carries signals → gate V5
   becomes checkable.

## Hard rules

- **An unconfirmed proposal is inert.** The signals-document builder refuses any mapping whose
  attestation is absent. There is no flag to skip confirmation.
- **Attestations are provenance-bound.** Keyed to the sheet file + image sha; a re-parsed or
  changed sheet invalidates stale confirmations (they surface as "재확인 필요", never silently
  reused).
- The vision proposal is an artifact with its own provenance (model, date, image sha); the
  attestation is the load-bearing fact. UI/report language must never imply the machine
  verified the mapping.
- No COM anywhere. Proposal generation runs as an offline backend tool/job, never in a
  request handler.
- `gate.py` untouched. The existing `generate_node_signals` required-argument refusal stays
  the enforcement point.

## Acceptance

1. End-to-end on ≥1 real 대전 intersection: sheet → proposal → real human confirmation click →
   signals side-input document → genesis job with signals layer emitted → V5 runs (not
   NOT_CHECKABLE).
2. Refusal test: an unconfirmed mapping cannot reach the signals document builder.
3. Proposals generated + stored for review for all 20 plan-bearing intersections (batch run),
   each either a structured proposal or a named refusal — zero silent skips.
4. Backend suite, frontend typecheck + contract suite green.

## Honest limits (declared up front)

- Vision accuracy is UNKNOWN until measured; the first batch run doubles as the measurement
  (agreement rate proposal-vs-confirmed, recorded in DECISIONS). The unit never claims
  accuracy — the attestation carries the truth.
- Intersections whose diagrams refuse remain manual-entry via the same screen (option (b)).
- The site→leg→link join for detector-level work (D1, LMA/LMB) is OUT of scope — this unit
  binds intersection→node and approach→phase only.
