# Prism (vissimDashboard) — project instructions

Vissim traffic-simulation validation dashboard (Dochak) + LLM agent that edits Vissim via COM.
FastAPI backend on **port 8001** + Next.js 16 frontend on 3000; file-based storage, no DB.
**Goal (re-set 2026-07-31): the full consultant pipeline as a product** — scoping → data →
AI network build → AI calibration → scenario sweep → styled report. Master plan:
`docs/product/master-roadmap-2026-07.md` (8 tracks / 5 phases); requirements:
`docs/product/tk-requirements-2026-07-31.md` (v2.1). The TR-C paper is DEFERRED (post-US-move);
sweep/calibration instrumentation still accumulates paper data by design. Target market:
Korea/Daejeon first via a format-neutral adapter layer; micro (not meso); Vissim-focused.

## Read this first for any agent/COM work

`docs/phase2/` in order: `00-EXECUTION-GUIDE.md` (rules + verified landmines — Vissim COM and
model semantics WILL bite you without it) → `01-ARCHITECTURE.md` → `02-ROADMAP.md` (find your
milestone; M0+M1 are done) → `03-RESEARCH-DIGEST.md` → `04-EVAL-PLAN.md`.
Record any interface-gap deviation in `docs/phase2/DECISIONS.md` (protocol + examples inside).
Deferred UX debt: `docs/ux-backlog.md`. Legacy plan + deployment ladder: `docs/ROADMAP.md`.
MRWA guideline mapping (client authority numbers): `docs/meeting-brief-tk-mrwa.md`.

## Hard rules

- **This is Dochak's proprietary source.** Remote: ONLY the private repo
  `github.com/yunhalee2008/vissimDashboard` (approved 2026-07-22). Never make it public, never
  add other remotes, never grant collaborator access — those are the user's calls alone.
- **Never commit secrets.** `ANTHROPIC_API_KEY` lives in git-ignored `backend/.env`. Never
  print it. `.gitignore` blocks `.env*` — do not weaken.
- **Vissim COM safety:** automation always `connect(attach_to_running=False)` (GetActiveObject
  attaches to a human's open GUI and can destroy their work); never edit the active dataset's
  .inpx in place — clone into a job work dir; batch runs use RunContinuous, never
  per-step loops; all COM work goes through the job queue, never a request handler.
- **A refused edit with a clear reason beats a completed edit with wrong numbers.** Vissim
  fails silently; the validation layer is the product.

## Working conventions

- `frontend/tests/dashboard-contract.test.ts` regex-pins raw source — co-edit it when moving
  pinned code. Pins go through `contractTest`/`pin.match`, which reports EVERY stale pin in a
  test instead of aborting at the first: plain `assert` let one stale base-model layout pin
  hide 2 broken + 53 healthy pins from the source import until 2026-08-05. Keep tests
  single-concern and keep layout/style pins in their own test. Suite is fully green — a
  failure here is real. (`test_rag_retrieval_quality` on the backend is still known-failing
  and environmental: it needs the full RAG corpus we don't have. Don't "fix" it in passing.)
- Verify before claiming done: backend `pytest` (from `backend/`, use the venv), frontend
  `npm run typecheck` + `npm run test:dashboard-contract`, plus the milestone's named
  acceptance check. Non-COM code must work against synthetic demo data
  (`python backend/tools/make_demo_data.py`) — never make it require live Vissim.
- New agent code goes in `backend/services/agent/`, not the `assistant_router.py` monolith
  (grep for existing helpers there before adding any — it has had duplicates).
- User-facing strings: ko primary, en/ja via the existing `t()`/answer-builder plumbing.
- `clean_generated_answer()` strips JSON — strict-JSON LLM paths must bypass it.
- Windows dev box + Mac laptop both used; keep scripts cross-platform (`.claude/launch.json`
  has per-OS dev-server configs).
