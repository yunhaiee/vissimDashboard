# Prism (vissimDashboard) — project instructions

Vissim traffic-simulation validation dashboard (Dochak) + LLM agent that edits Vissim via COM.
FastAPI backend on **port 8001** + Next.js 16 frontend on 3000; file-based storage, no DB.
Goal: working system + TR-C paper substrate before ~2026-09-05.

## Read this first for any Phase 2 (agent) work

`docs/phase2/` in order: `00-EXECUTION-GUIDE.md` (rules + verified landmines — Vissim COM and
model semantics WILL bite you without it) → `01-ARCHITECTURE.md` → `02-ROADMAP.md` (find your
milestone; M0+M1 are done) → `03-RESEARCH-DIGEST.md` → `04-EVAL-PLAN.md`.
Record any interface-gap deviation in `docs/phase2/DECISIONS.md` (protocol + examples inside).
Deferred UX debt: `docs/ux-backlog.md`. Overall plan: `docs/ROADMAP.md`.

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
  pinned code. Two known-failing tests are environmental/pre-existing, don't "fix" in passing:
  `test_rag_retrieval_quality` (needs the full RAG corpus we don't have) and the base-model
  layout contract test.
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
