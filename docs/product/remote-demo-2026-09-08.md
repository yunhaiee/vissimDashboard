# Showing Prism to someone outside the team, without publishing anything

**Written 2026-09-08.** The occasion: the professor wanted to see the product,
does not use GitHub, and the repo must stay private (CLAUDE.md hard rule — the
remote is the private repo and nothing else, and collaborator access is the
user's call alone). This is the third option from that conversation: a
temporary public URL pointing at the dev stack on your own machine, behind a
password, that dies when you press Ctrl-C.

## The short version

From the repo root, on the machine that will serve the demo:

```
python scripts/share_demo.py
```

It starts uvicorn on :8001, opens a Cloudflare quick tunnel, starts the Next
dev server with the tunnel URL wired in, and prints three lines to send:

```
    URL       https://<random-words>.trycloudflare.com
    Username  professor
    Password  <freshly generated, printed once>
```

That is all the viewer needs — no account, no install, no GitHub. The browser
shows a standard login box on first load. `--user` and `--password` override
the defaults; `--no-backend` skips uvicorn if :8001 is already serving.

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
