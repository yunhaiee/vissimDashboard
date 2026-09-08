# Showing Prism to someone outside the team, without publishing anything

**Written 2026-09-08.** The occasion: the professor wanted to see the product,
does not use GitHub, and the repo must stay private (CLAUDE.md hard rule — the
remote is the private repo and nothing else, and collaborator access is the
user's call alone). This is the third option from that conversation: a
temporary public URL pointing at the dev stack on your own machine, behind a
password, that dies when you press Ctrl-C.

## The short version

One command, from the repo root, on the machine that will serve the demo:

```
./scripts/demo.sh
```

That is the whole procedure. It creates the venv if missing, installs backend
and frontend dependencies, seeds synthetic demo data if `data/` is empty, and
opens the tunnel. Every step is idempotent, so a second run reaches the tunnel
in seconds. Arguments pass through (`./scripts/demo.sh --user prof`).

One prerequisite it cannot install for you:

```
brew install cloudflared
```

No Cloudflare account or domain is needed. The script stops with that exact
line if the binary is missing.

It then prints three lines to send, and nothing else needs to be sent:

```
    URL       https://<random-words>.trycloudflare.com
    Username  professor
    Password  <freshly generated, printed once>
```

The viewer needs no account, no install and no GitHub. Ctrl-C ends it, and the
URL dies with it. Each run gets a new URL.

### Why a wrapper script exists at all

Because the by-hand version had four separate ways to fail, and on 2026-09-08
it hit all of them on the Mac laptop this doc recommends using:

- **macOS has no `python`, only `python3`** — the first two commands died at
  `command not found` before anything ran.
- **`backend/requirements.txt` could not install on macOS at all.** `pywin32`
  carried no platform marker, so `pip install -r requirements.txt` failed
  outright — which is why that laptop had no venv to activate. Fixed in that
  file; the marker there is load-bearing, not tidying.
- **`make_demo_data` must run from `backend/` and as `-m`.** A path run puts
  `backend/tools/` on `sys.path` instead of `backend/`, so its
  `from services.workspaces import ...` raises ModuleNotFoundError.
- **`python-dotenv==310; platform_system == "Windows"`** was two bugs in one
  line: pywin32's marker on the wrong package, and a version that has never
  existed. It could not install on either platform; `config.py`'s try/except
  swallowed the resulting ImportError, so `backend/.env` was silently never
  loaded off Windows.

Seeding is skipped when `data/Scenarios` already exists, deliberately: a
default `--out` run **`rmtree`s** that directory before rebuilding it. The
rebuild is byte-identical, so nothing the script wrote is lost — but anything a
*human* put there is gone. Re-seed by hand with `--out` if you need to.

## What is protecting it

`frontend/proxy.ts` — Next 16's proxy (the old `middleware.ts`) — refuses every
request that does not carry the right HTTP Basic credentials. It runs *before*
the `/api/*` rewrites in `next.config.ts`, so the FastAPI surface behind them is
covered too. That matters more than it sounds: the agent endpoints spend
`ANTHROPIC_API_KEY`, and an open tunnel URL is an open wallet.

The decision logic is `frontend/lib/demo-auth.ts`, kept separate from the Next
adapter so it can be tested without starting a server (`npm run test:demo-auth`,
8 cases). Constant-time comparison, no short-circuit between the username and
password checks, first-colon-only credential split.

**It is inert unless both `PRISM_DEMO_AUTH_USER` and `PRISM_DEMO_AUTH_PASSWORD`
are set.** A normal `npm run dev` is byte-for-byte what it always was. Only
`scripts/share_demo.py` sets them, and it sets them as plain env vars — *not*
`NEXT_PUBLIC_*`, which would inline the password into the client bundle and
hand the door key to everyone who loads the page. The contract suite pins that.

### This is deliberately NOT Prism's login

Prism has real auth — `services/auth.py`, `routers/auth.py`, `/login`, `/admin`,
scrypt hashes, row-backed revocable sessions. The demo gate does not touch it,
for two reasons:

1. The master roadmap's §F still lists **"앱 전체를 로그인 뒤로 — 현재 2개
   엔드포인트만 게이트됨. 의도적 미결정 사항"**. That decision should be made
   on its merits, not as a side effect of wanting to show someone the app.
2. Even if it were made, it would not fit. Prism's auth exists to put a *name*
   in the approval record and the audit DOCX's approver column. A demo needs
   one shared key that deliberately leaves no name — the opposite requirement.

One is the product's identity layer. The other is a door key for an afternoon.

## Why the tunnel URL gets fed back into the frontend

Most of `frontend/lib/api.ts` already tries same-origin `/api/...` first and
lets the Next rewrite reach :8001 — that survives a tunnel with no help, chat
and streaming included. But `apiUrl()` builds **absolute** URLs from
`getApiBase()`, which defaults to `http://127.0.0.1:8001`. Left alone, the
phase-diagram images (`api.ts:5818`), the xlsx exports (`:5920`–`:5930`) and the
health-check JSON (`:2458`) would resolve against *the viewer's* localhost and
break. `createSimulationWS` (`:3318`) derives its `ws://` base from the same
value.

So the script sets `NEXT_PUBLIC_API_BASE` to the tunnel origin. It is inlined at
compile time, which is why the tunnel has to come up *before* the dev server —
that ordering is the whole reason the script exists rather than a README
snippet. It also sets `ALLOWED_DEV_ORIGINS` to the tunnel host, the same knob
`next.config.ts` documents for the tailscale setup: without it Next 16 blocks
dev-resource requests from unrecognised origins and the page hangs at
"불러오는 중…" while `curl` sails through.

## Known gaps — read before a Windows demo

- **Live run progress.** The browser WebSocket API sends no `Authorization`
  header, so `/api/simulations/ws/<job>` gets a 401 through the gate and
  progress falls back to polling via `fetchSimulationJob`. Moot on a laptop
  (no Vissim, no COM, no live runs). It *would* bite a tunnelled demo from the
  Windows box — fix it there before relying on live progress.
- **The 5xx retry path.** `apiDirectMutationFetch(..., { sameOrigin: false })`
  retries against `127.0.0.1:8001` and will fail for a remote viewer. It only
  runs after a 500 from the same-origin attempt, so it degrades an already
  broken request rather than a working one.
- **Quick tunnels are ephemeral.** New URL every run, and Cloudflare does not
  promise uptime for them. Start it before the call, not the night before.

## Before you open it — what actually becomes reachable

The script makes you confirm, and warns if `client-data/` is non-empty. Take
that seriously. Anyone with the link *and* the password reaches every workspace
the running backend can see. `client-data/` is gitignored because it is the
**client's** data — the Daejeon SIS and Main Roads WA drops — not ours; the repo
being private is not the same as that data being shareable.

The recommended shape, and the one this was built for:

- Run it on the **Mac laptop**, not the Vissim box.
- Seed a clean workspace with `python backend/tools/make_demo_data.py`.
- Confirm no client dataset is loaded, then start the tunnel.

That shows the dashboard, network preview, `/health` 모델 건강검진, the
calibration workspace and the agent chat, with nothing confidential behind it.

## Troubleshooting

| Symptom | Cause |
|---|---|
| `cloudflared is not installed` | `brew install cloudflared` (macOS) / `winget install --id Cloudflare.cloudflared` (Windows). No account or domain needed. |
| No URL within 60s | Quick tunnels occasionally fail to allocate a hostname. Re-run. |
| Page hangs at "불러오는 중…" | `ALLOWED_DEV_ORIGINS` did not reach the dev server — check the tunnel came up *before* the frontend. |
| Login box reappears forever | Password mistyped, or the dev server was restarted without the env vars. |
| Images/exports 404 for the viewer only | `NEXT_PUBLIC_API_BASE` was not set to the tunnel URL — the dev server was started by hand instead of by the script. |

## The backend suite is not green off Windows — 22 tests

Making `pip install` work on macOS had a side effect worth stating plainly: the
suite can now RUN there, and 22 tests fail when it does. All of them in the
COM/agent-run files (`test_agent_run_num_runs.py`, `test_agent_run_trajectory.py`
and neighbours), all with the same cause:

```
AssertionError: pywin32 is not installed, so VISSIM COM cannot run.
ERROR routers.simulations:simulations.py:865 pythoncom not available
```

This is **not** a regression from the marker fix and not a fault in the demo
path. It was simply invisible before: `pip install -r requirements.txt` failed
outright on macOS, so nobody ever got far enough to run pytest there. These
tests mock the COM layer but still need `pywin32` importable, which cannot
happen off Windows.

The repo already has the right idiom for this — `test_rag_retrieval_quality`
passes-or-skips-with-reason on corpus presence, because client PDFs are
gitignored. These 22 want the same treatment: skip with a named reason when
`pywin32` is absent, so `pytest` on the Mac reports `0 failed` honestly rather
than 22 red lines that mean "you are not on Windows". **Not done here** — it is
a separate change with its own review, and it is not on the path to showing
somebody the product. Until then: run the backend suite on the Windows box, and
read a Mac run as "22 expected COM failures + everything else".

One consequence to note: `pytest` itself is not in `requirements.txt` (nor is
`httpx`, which `starlette.testclient` needs — without it 58 test modules fail at
collection). Installing them is a separate step from what `scripts/demo.sh`
does; the demo path does not need either.

## Verification performed (2026-09-08)

`npm run typecheck` clean · `test:dashboard-contract` 265/265 ·
`test:geh-conformance` 11/11 · `test:demo-auth` 8/8 · `npm run build` clean with
the proxy registered. Live against a running dev server with the gate armed:
anonymous `/` → 401 with `WWW-Authenticate` + `Cache-Control: no-store`;
correct credentials → 307 → `/overview` → 200; wrong password and wrong
username → 401; `/api/health-check` anonymous → 401 (from the gate, with no
backend running); `/favicon.ico` anonymous → 401, authenticated → 200; the
legacy `/intersections` and `/intersections/abc` redirects to `/dashboard`
still fire when authenticated and return 401 — not a redirect — when not, so
the gate does not leak which routes exist.

Then the whole stack, end to end, on a machine with no venv to start with:
`scripts/demo.sh` created `.venv`, installed `backend/requirements.txt` (the
marker fix's real test — it now installs off Windows), seeded demo data, and
stopped exactly at the `brew install cloudflared` line. With uvicorn and the
gated dev server up, `GET /api/scenarios` **through the Next rewrite** returned
the three demo scenarios (`Base (AM Peak)`, `Signal Optimization`,
`Demand +20%`) for an authenticated caller and 401 for an anonymous one —
which is what the professor's browser will do.

That last check is the one that found the `API_PROXY_TARGET` bug: with only
`NEXT_PUBLIC_API_BASE` set, the same request hung for 30s and returned zero
bytes with nothing in the dev log. Worth re-running after any change to how
those two variables are set.
