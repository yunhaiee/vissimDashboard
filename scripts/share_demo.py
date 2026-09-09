#!/usr/bin/env python3
"""Put the running Prism dev stack on a temporary public URL, behind a password.

Why this exists (2026-09-08): showing Prism to somebody outside the team — a
professor, a client — used to mean either a screen share or handing them the
repo. This script gives them a link instead: a Cloudflare quick tunnel to the
Next dev server, with ``frontend/middleware.ts``'s Basic-auth gate switched on
by the two env vars set here.

WHAT IS AND IS NOT PROTECTED
----------------------------
The gate is at the EDGE, in front of everything, including the ``/api/*``
rewrites — so the FastAPI surface behind them is covered too. It is NOT
Prism's own auth (``services/auth.py`` / ``/login``): that is the product's
named-user layer, and the master roadmap still lists "앱 전체를 로그인 뒤로" as
an open decision. One shared door key is the right shape for a demo and the
wrong shape for an audit record; these two things stay separate on purpose.

WHY THE TUNNEL URL IS FED BACK INTO THE FRONTEND
------------------------------------------------
Most of ``frontend/lib/api.ts`` already tries same-origin ``/api/...`` first,
which the Next rewrite proxies to :8001 — that part needs no help. But
``apiUrl()`` builds ABSOLUTE urls from ``getApiBase()``, whose default is
``http://127.0.0.1:8001``: phase-diagram images, the xlsx exports and the
health-check JSON would all resolve against the VIEWER's localhost and fail.
So ``NEXT_PUBLIC_API_BASE`` is set to the tunnel origin before the dev server
starts (it is inlined at compile time, which is why the tunnel has to come up
first). ``createSimulationWS`` derives its ws:// base from the same value.

Usage, from the repo root::

    python scripts/share_demo.py                 # backend + tunnel + frontend
    python scripts/share_demo.py --no-backend    # backend already running
    python scripts/share_demo.py --user prof --password hunter2

Ctrl-C shuts everything down, and the URL dies with it.
"""
from __future__ import annotations

import argparse
import os
import queue
import re
import secrets
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FRONTEND_DIR = REPO_ROOT / "frontend"
BACKEND_DIR = REPO_ROOT / "backend"

FRONTEND_PORT = 3000
BACKEND_PORT = 8001

#: cloudflared prints the assigned hostname on stderr, boxed in ASCII art. The
#: subdomain is words-and-digits joined by hyphens; anchoring on the fixed
#: suffix is what makes this safe to scrape.
TUNNEL_URL_RE = re.compile(r"https://[a-z0-9][a-z0-9-]*\.trycloudflare\.com")
TUNNEL_TIMEOUT_SECONDS = 60

#: How often the public URL is probed from this machine, and how many probes
#: must fail before the alarm. Measured 2026-09-09: cloudflared ran for 3h54m
#: with ZERO established edge connections while the hostname had gone NXDOMAIN
#: — the process never exited, so the supervision loop's `poll()` check saw a
#: healthy child and said nothing. The viewer got "This site can't be reached"
#: and the operator got no signal at all. A tunnel is only up if the URL
#: answers, so that is what gets measured.
TUNNEL_HEALTH_EVERY_SECONDS = 30
TUNNEL_HEALTH_FAILURES_BEFORE_ALARM = 2

#: cloudflared lines worth repeating. Everything else is per-request noise.
TUNNEL_NOTABLE_RE = re.compile(
    r"\b(err|error|failed|failure|warn|unauthorized|disconnect|reconnect|"
    r"retry|unregistered|terminat|shutdown|expired)",
    re.IGNORECASE,
)

CLOUDFLARED_INSTALL_HINT = """
cloudflared is not installed (or not on PATH). Install it:

  macOS     brew install cloudflared
  Windows   winget install --id Cloudflare.cloudflared
  Linux     see https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/

No Cloudflare account or domain is needed for a quick tunnel.
""".strip()


def log(message: str) -> None:
    print(f"[share-demo] {message}", flush=True)


def find_backend_python() -> str:
    """The backend venv interpreter, or this one as a fallback.

    Both dev boxes are supported (CLAUDE.md: "Windows dev box + Mac laptop
    both used"), so both venv layouts are probed.
    """
    candidates = [
        BACKEND_DIR / ".venv" / "bin" / "python",
        BACKEND_DIR / ".venv" / "Scripts" / "python.exe",
        REPO_ROOT / ".venv" / "bin" / "python",
        REPO_ROOT / ".venv" / "Scripts" / "python.exe",
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    log("WARNING: no backend venv found; falling back to the current interpreter.")
    return sys.executable


def pump_stderr(stream, sink: "queue.Queue[str]", echo_prefix: str | None) -> None:
    """Forward a child's stderr line-by-line, optionally echoing it.

    cloudflared's URL only appears on stderr, and it appears once, so the
    stream cannot simply be inherited — it has to be read. Everything read is
    pushed to `sink` for the URL scan and echoed if asked, so nothing the
    child says is silently swallowed.
    """
    try:
        for raw in iter(stream.readline, ""):
            line = raw.rstrip("\n")
            sink.put(line)
            if echo_prefix is not None:
                print(f"{echo_prefix} {line}", flush=True)
    finally:
        stream.close()


def tunnel_is_reachable(url: str, timeout: float = 8.0) -> bool:
    """True when the public URL still answers from the open internet.

    ANY http status counts as up, 401 included — the demo gate refusing an
    unauthenticated probe is proof the request reached this machine, which is
    exactly what is being measured. Only DNS and transport failures mean down,
    and NXDOMAIN on the quick-tunnel hostname is the observed shape of a
    cloudflared that is still running but no longer registered.
    """
    request = urllib.request.Request(url, method="GET", headers={"User-Agent": "prism-share-demo-healthcheck"})
    try:
        urllib.request.urlopen(request, timeout=timeout).close()
        return True
    except urllib.error.HTTPError:
        return True
    except Exception:
        return False


def drain_tunnel_output(lines: "queue.Queue[str]", current_url: str) -> str | None:
    """Echo anything notable cloudflared said; return a NEW url if it moved.

    A quick tunnel that re-registers gets a different hostname, and the old
    link is dead the moment it does.
    """
    new_url = None
    while True:
        try:
            line = lines.get_nowait()
        except queue.Empty:
            break
        found = TUNNEL_URL_RE.search(line)
        if found and found.group(0) != current_url:
            new_url = found.group(0)
        elif TUNNEL_NOTABLE_RE.search(line):
            log(f"cloudflared: {line}")
    return new_url


def start_backend(env: dict[str, str]) -> subprocess.Popen:
    python = find_backend_python()
    log(f"starting backend on :{BACKEND_PORT} ({python})")
    return subprocess.Popen(
        [
            python,
            "-m",
            "uvicorn",
            "main:app",
            "--app-dir",
            str(BACKEND_DIR),
            "--port",
            str(BACKEND_PORT),
        ],
        cwd=str(BACKEND_DIR),
        env=env,
    )


def start_tunnel() -> tuple[subprocess.Popen, str, "queue.Queue[str]"]:
    """Open a quick tunnel to the frontend port and return its public URL.

    The stderr queue comes back with it: cloudflared keeps talking after the
    URL appears — including when it loses the edge — and the supervision loop
    has to keep reading, or those lines pile up unread and the operator learns
    the tunnel is dead from whoever they sent the link to.
    """
    cloudflared = shutil.which("cloudflared")
    if not cloudflared:
        raise SystemExit(CLOUDFLARED_INSTALL_HINT)

    log(f"opening a Cloudflare quick tunnel to :{FRONTEND_PORT}")
    process = subprocess.Popen(
        [
            cloudflared,
            "tunnel",
            "--no-autoupdate",
            "--url",
            f"http://127.0.0.1:{FRONTEND_PORT}",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )

    lines: "queue.Queue[str]" = queue.Queue()
    threading.Thread(
        target=pump_stderr, args=(process.stderr, lines, None), daemon=True
    ).start()

    deadline = time.monotonic() + TUNNEL_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise SystemExit("cloudflared exited before publishing a URL.")
        try:
            line = lines.get(timeout=1.0)
        except queue.Empty:
            continue
        match = TUNNEL_URL_RE.search(line)
        if match:
            return process, match.group(0), lines

    process.terminate()
    raise SystemExit(
        f"cloudflared published no URL within {TUNNEL_TIMEOUT_SECONDS}s. "
        "Re-run; quick tunnels occasionally fail to allocate a hostname."
    )


def start_frontend(env: dict[str, str]) -> subprocess.Popen:
    npm = shutil.which("npm")
    if not npm:
        raise SystemExit("npm is not on PATH.")
    log(f"starting frontend on :{FRONTEND_PORT}")
    return subprocess.Popen([npm, "run", "dev"], cwd=str(FRONTEND_DIR), env=env)


def confirm_exposure(url_note: str, assume_yes: bool) -> None:
    """Make the operator look at what is about to become reachable.

    Opening a tunnel is outward-facing and easy to do without thinking about
    which workspaces happen to be loaded. `client-data/` is gitignored
    precisely because it is the CLIENT's data, not ours — it is exactly the
    thing that must not ride out over a public URL by accident.
    """
    client_data = REPO_ROOT / "client-data"
    has_client_data = client_data.is_dir() and any(client_data.iterdir())

    print()
    print("  " + "=" * 68)
    print("  A PUBLIC URL IS ABOUT TO POINT AT THIS MACHINE")
    print("  " + "=" * 68)
    print(f"  {url_note}")
    print("  Anyone with the link AND the password reaches every workspace")
    print("  this backend can see — datasets, reports, agent endpoints.")
    if has_client_data:
        print()
        print("  !! client-data/ is present and NOT empty on this machine.")
        print("  !! If any of it is loaded into a workspace, it goes out too.")
    print("  " + "=" * 68)
    print()

    if assume_yes:
        log("--yes given; skipping confirmation.")
        return
    answer = input("  Type 'yes' to open the tunnel: ").strip().lower()
    if answer != "yes":
        raise SystemExit("Aborted.")


def print_banner(url: str, user: str, password: str) -> None:
    width = 68
    print()
    print("  " + "=" * width)
    print("  PRISM IS LIVE — send these three lines and nothing else")
    print("  " + "=" * width)
    print(f"    URL       {url}")
    print(f"    Username  {user}")
    print(f"    Password  {password}")
    print("  " + "-" * width)
    print("  The link dies when you press Ctrl-C. A new run gets a new URL.")
    print("  " + "=" * width)
    print()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--user", default="professor", help="demo username (default: professor)")
    parser.add_argument(
        "--password",
        default=None,
        help="demo password (default: a fresh random one, printed once)",
    )
    parser.add_argument(
        "--no-backend",
        action="store_true",
        help="do not start uvicorn; assume :8001 is already serving",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="skip the 'this becomes public' confirmation",
    )
    args = parser.parse_args()

    password = args.password or secrets.token_urlsafe(9)

    confirm_exposure(
        "Target: the Next dev server on :%d, proxying the API on :%d."
        % (FRONTEND_PORT, BACKEND_PORT),
        args.yes,
    )

    children: list[subprocess.Popen] = []
    try:
        if not args.no_backend:
            backend_env = dict(os.environ)
            # Matches .claude/launch.json's dev configs: the Qwen worker is a
            # heavy optional dependency and a demo has no use for it.
            backend_env["QWEN_WORKER_AUTO_START"] = "0"
            children.append(start_backend(backend_env))

        tunnel_process, url, tunnel_lines = start_tunnel()
        children.append(tunnel_process)
        log(f"tunnel is up at {url}")

        host = url.removeprefix("https://")
        frontend_env = dict(os.environ)
        frontend_env.update(
            {
                # Absolute-URL builders (apiUrl, createSimulationWS) must point
                # at the tunnel, not at the viewer's own localhost.
                "NEXT_PUBLIC_API_BASE": url,
                # MUST be set alongside it. next.config.ts resolves its rewrite
                # target as `API_PROXY_TARGET ?? NEXT_PUBLIC_API_BASE ??
                # http://127.0.0.1:8001`, so setting only the line above would
                # ALSO repoint the /api/* rewrite at the tunnel -- and the
                # tunnel points back at this very dev server. Every API call
                # then loops frontend -> tunnel -> frontend and hangs until the
                # client times out, with nothing in the log to say why.
                # (Measured 2026-09-08: 30s timeout, 0 bytes, no request line.)
                "API_PROXY_TARGET": f"http://127.0.0.1:{BACKEND_PORT}",
                # next.config.ts reads this; Next 16 blocks dev-resource
                # requests from unrecognised origins, which is what made the
                # tailscale setup hang at "불러오는 중…" until it was added.
                "ALLOWED_DEV_ORIGINS": host,
                # Switches on frontend/middleware.ts. Server-side only — not
                # NEXT_PUBLIC_*, so it is never inlined into the bundle.
                "PRISM_DEMO_AUTH_USER": args.user,
                "PRISM_DEMO_AUTH_PASSWORD": password,
            }
        )
        # Belt and braces: same-origin requests are not CORS-checked at all, so
        # this changes nothing today. It costs one env var and saves an
        # afternoon if a future call goes cross-origin.
        existing_cors = os.environ.get("CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000")
        frontend_env["CORS_ORIGINS"] = f"{existing_cors},{url}"

        children.append(start_frontend(frontend_env))
        print_banner(url, args.user, password)

        # A dead tunnel is NOT a dead process, so `poll()` alone is blind to it
        # (see TUNNEL_HEALTH_EVERY_SECONDS). The URL itself is the thing under
        # supervision from here on.
        next_health_check = time.monotonic() + TUNNEL_HEALTH_EVERY_SECONDS
        failures = 0
        alarm_raised = False

        while True:
            for child in children:
                if child.poll() is not None:
                    log(f"a child process exited with {child.returncode}; shutting down.")
                    return child.returncode or 1

            moved_to = drain_tunnel_output(tunnel_lines, url)
            if moved_to:
                log("=" * 68)
                log(f"cloudflared re-registered at a NEW url: {moved_to}")
                log(f"the link you sent ({url}) is dead.")
                log("RESTART this script rather than sending the new one: the")
                log("frontend baked the old url into NEXT_PUBLIC_API_BASE and")
                log("ALLOWED_DEV_ORIGINS at startup, so the new host would be")
                log("refused as an unrecognised dev origin.")
                log("=" * 68)
                url = moved_to

            now = time.monotonic()
            if now >= next_health_check:
                next_health_check = now + TUNNEL_HEALTH_EVERY_SECONDS
                if tunnel_is_reachable(url):
                    if alarm_raised:
                        log(f"tunnel is answering again: {url}")
                        alarm_raised = False
                    failures = 0
                else:
                    failures += 1
                    if failures >= TUNNEL_HEALTH_FAILURES_BEFORE_ALARM and not alarm_raised:
                        alarm_raised = True
                        log("=" * 68)
                        log(f"TUNNEL IS DOWN — {url} no longer answers.")
                        log("Anyone you sent the link to is seeing a browser error.")
                        log("cloudflared may still be running; a quick tunnel can lose")
                        log("its edge registration without the process exiting.")
                        log("Press Ctrl-C and re-run this script to get a fresh url.")
                        log("=" * 68)

            time.sleep(1.0)
    except KeyboardInterrupt:
        log("Ctrl-C — closing the tunnel.")
        return 0
    finally:
        for child in reversed(children):
            if child.poll() is None:
                child.terminate()
        deadline = time.monotonic() + 10
        for child in reversed(children):
            remaining = max(0.0, deadline - time.monotonic())
            try:
                child.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                child.kill()
        log("all child processes stopped.")


if __name__ == "__main__":
    if hasattr(signal, "SIGPIPE"):
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    raise SystemExit(main())
