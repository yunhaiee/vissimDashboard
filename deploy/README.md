# Hosting Prism continuously (Hetzner + Caddy + Docker)

A private, always-on instance that does not depend on a laptop being awake.
Replaces `scripts/share_demo.py`, whose Cloudflare quick tunnel rotates its
hostname and expires after a few hours.

**Data policy for this deployment: synthetic demo data only.** The seed step
below builds `data/` from `backend/tools/make_demo_data.py` and creates an empty
workspace. Do not upload `client-data/` or copy `runtime/workspaces/` from a
development machine — the workspace named `기본 프로젝트` is where client real
data lives (see `docs/product/demo-guide-2026-08-21.md`), and this box is
rented infrastructure that stays up unattended.

---

## 1. Server

A CX22 (2 vCPU / 4GB / 40GB, ~€4.5/month) is enough. 4GB matters: `next build`
is the memory peak, not serving.

Disk is the thing that grows. `docs/ROADMAP.md` flags it: simulation results
accumulate, so watch `docker system df` and the `prism-runtime` volume rather
than assuming 40GB lasts forever.

Create it with Ubuntu 24.04 and your SSH key, then:

```bash
ssh root@<server-ip>
apt update && apt install -y docker.io docker-compose-v2 git
```

## 2. DNS

Point an A record at the server **before** the first `up` — Caddy asks Let's
Encrypt for the certificate on boot and the name has to resolve:

```
prism.yourdomain.com.   A   <server-ip>
```

Confirm from the server: `dig +short prism.yourdomain.com`

## 3. Code and secrets

The repo is private, so clone over SSH with a deploy key, or push from your
laptop. Then:

```bash
cd vissimDashboard
cp deploy/env.example deploy/.env
$EDITOR deploy/.env          # PRISM_DOMAIN, the password, the API key
```

Generate the door password rather than inventing one:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(12))"
```

`deploy/.env` is git-ignored by the repo-root `.env*` rule. Keep it that way.

## 4. Build and start

```bash
cd deploy
docker compose up -d --build
```

First build takes a few minutes (pip wheels, `npm ci`, `next build`). Watch
Caddy get its certificate:

```bash
docker compose logs -f caddy
```

## 5. Seed — demo data and your account

The image ships no data and **no default account**. `manage_users.py` is
explicit that an empty `users` table is the correct state for a fresh install,
so nobody can sign in until you create someone:

```bash
docker compose exec backend python backend/tools/make_demo_data.py
docker compose exec -it backend python backend/tools/manage_users.py \
    create-admin --username yunha --display-name "이윤하"
```

The password is prompted twice and never echoed. Your display name is what
appears in the report's approver column, so use the one you want on camera.

Restart the backend so it picks up the seeded data:

```bash
docker compose restart backend
```

## 6. Check it

```bash
curl -so /dev/null -w '%{http_code}\n' https://prism.yourdomain.com/overview   # 401
curl -so /dev/null -w '%{http_code}\n' -u "professor:<password>" \
     https://prism.yourdomain.com/overview                                     # 200
```

A 401 without credentials is the gate working. If you get 200 without them,
**stop** — `PRISM_DEMO_AUTH_USER`/`PASSWORD` did not reach the frontend
container, and the API is exposed.

---

## What the viewer does

Two doors, deliberately separate:

1. **The browser password box** — one shared key (`professor` + the generated
   password) in front of the whole origin. Answer it once; the session cookie
   carries the rest of the visit.
2. **The app's own 로그인** — named users with roles, whose names land in the
   audit record. Give your professor a viewer account of their own if you want
   their actions attributed; otherwise they can browse without signing in and
   simply see less.

## Updating

```bash
git pull && cd deploy && docker compose up -d --build
```

Rebuild the frontend whenever `PRISM_DOMAIN` changes — `NEXT_PUBLIC_API_BASE`
is compile-time, so a restart alone leaves the old origin baked into the bundle.

## What this does NOT do

**No Vissim.** COM automation is Windows-only, so on this Linux box: no
simulation runs, and the genesis gate's V1 load oracle reports `NOT_CHECKABLE`
with the verdict capped at `INCOMPLETE` rather than `PASS`. That is the
designed refusal, not a deployment fault (`services/genesis/gate.py`). Network
generation, phase-diagram upload, calibration scoring against pre-baked runs,
the search gate's refusals, and report assembly all work.

**No client PDFs**, so the assistant's page-cited answers degrade to
uncited ones — `docs/pdf_knowledge/` is git-ignored and stays empty here.
