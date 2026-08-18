import os
from pathlib import Path
import sys

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

# Importing ``routers.assistant`` (which the fixture below must do, to patch the
# sessions-root seam that module binds by name) runs its module-level
# ``_preload_assistant_model()``. Under test that would spawn a local-LLM warmup
# thread nothing here uses. Set BEFORE any router import — conftest.py is
# imported before collection, so this reaches the module-level call. ``setdefault``
# so a developer who deliberately exports QWEN_PRELOAD still wins.
os.environ.setdefault("QWEN_PRELOAD", "0")


@pytest.fixture(scope="session", autouse=True)
def _isolate_ambient_machine_state(tmp_path_factory: pytest.TempPathFactory):
    """Pin the whole suite to the default ``data/`` demo dataset, regardless of
    this machine's persistent app state.

    ``services.datasets.get_active_*`` switch every reader to the current
    WORKSPACE's ``uploads/current/`` the moment a dataset has been uploaded
    through the dashboard UI, and ``services.simulation_runs`` lists completed
    COM runs from ``runtime/simulations/``. Both survive across sessions on a
    dev machine — a leftover manual upload (e.g. a live-demo rehearsal) once
    made every scenario-dependent test fail with empty scenario lists even
    though ``make_demo_data.py`` had seeded ``data/`` correctly. Redirecting
    the roots at the source keeps all tests hermetic.

    Unit 1.4 replaced the seven per-path ``services.datasets`` constants this
    fixture used to patch with ONE seam: ``services.workspaces.WORKSPACES_ROOT``.
    Every dataset path is now derived per call from the workspace bound to the
    request/job, so redirecting the root redirects all of them at once.

    ``LEGACY_UPLOADS_ROOT`` **must** be patched together with it. It is the
    source side of the one-time pre-workspace migration; leaving it pointed at
    the real ``runtime/uploads`` would make the first test that touches a
    workspace MOVE the developer's live dataset into a pytest tmp dir.

    ``SESSIONS_ROOT`` is redirected for the same reason, and it is the one this
    fixture was MISSING: a full suite run created ~13 agent-session directories
    (~56 files) inside the developer's real ``runtime/agent_sessions/`` — which
    has accumulated 2281 of them — because every agent endpoint test mints a
    real session on disk. Worse in the other direction: ``main.py``'s lifespan
    calls ``sweep_stale_sessions()``, so every ``with TestClient(main.app)`` in
    the suite was running the 7-day TTL DELETION sweep over that real
    directory. Tests must neither add to nor delete from a developer's runtime.

    There is no single sessions-root seam to patch: ``services.agent.sessions``
    owns the constant, and four modules bind it BY NAME at import
    (``from services.agent.sessions import SESSIONS_ROOT``), so each of those
    module attributes is its own independent copy. All five are redirected
    together, exactly as ``tests/test_workspaces.py`` already does per-test for
    the subset it exercises. Per-test fixtures that repoint a subset onto their
    own tmp dirs compose fine — their function-scoped monkeypatch wins for the
    test, then restores these values.

    A workspace is also BOUND here for the whole session. Production code gets
    its binding from ``main.WorkspaceScopeMiddleware`` on every request, but a
    test that calls a service function directly has no request — and
    ``current_workspace_id()`` deliberately raises rather than guessing. This
    binds the tmp root's default workspace once, so direct-call tests behave
    exactly like a request with no ``X-Workspace-Id`` header.

    Session-scoped deliberately: module-scoped fixtures (test_api_smoke.py's
    ``client``/``scenarios``) run before any function-scoped autouse patch
    could apply. Tests that exercise upload/run behaviour re-patch these same
    attributes onto their own tmp dirs, which composes fine — their
    function-scoped monkeypatch wins for the test, then restores these values.
    """
    from routers import agent_downloads as agent_downloads_router
    from routers import assistant as assistant_router_module
    from routers import network as network_router
    from services import audit_trail, db, simulation_runs, workspaces
    from services.agent import sessions as agent_sessions

    mp = pytest.MonkeyPatch()
    ambient_root = tmp_path_factory.mktemp("ambient-state")

    mp.setattr(workspaces, "WORKSPACES_ROOT", ambient_root / "workspaces")
    # Source side of the legacy migration -- see the docstring. Pointed at an
    # empty tmp dir so the migration is a guaranteed no-op under test.
    mp.setattr(workspaces, "LEGACY_UPLOADS_ROOT", ambient_root / "uploads")

    simulation_root = ambient_root / "simulations"
    mp.setattr(simulation_runs, "SIMULATION_ROOT", simulation_root)
    mp.setattr(simulation_runs, "REGISTRY_PATH", simulation_root / "simulation_runs.json")

    # THE definition plus every by-name copy of it. Missing any one of these
    # leaks that endpoint's session writes into the real runtime.
    sessions_root = ambient_root / "agent_sessions"
    sessions_root.mkdir(parents=True, exist_ok=True)
    mp.setattr(agent_sessions, "SESSIONS_ROOT", sessions_root)
    mp.setattr(assistant_router_module, "SESSIONS_ROOT", sessions_root)
    mp.setattr(network_router, "SESSIONS_ROOT", sessions_root)
    mp.setattr(agent_downloads_router, "SESSIONS_ROOT", sessions_root)
    mp.setattr(audit_trail, "SESSIONS_ROOT", sessions_root)

    # Unit U7 adds a THIRD root outside the workspace tree: the SQLite
    # metadata/auth database. It is deliberately NOT under WORKSPACES_ROOT
    # (auth is global, not per-project -- services/db.py), so the patch above
    # does not reach it and it needs its own line. Without this, any test that
    # touched a user or a session would create/modify the developer's real
    # ``runtime/db/app.db`` -- and once U8 puts approver names in it, a test
    # run could silently add accounts to a live install.
    mp.setattr(db, "DB_PATH", ambient_root / "db" / "app.db")

    # services/workbook_mapping.py stages uploaded workbooks and remembers
    # confirmed mappings; since unit 1.4 both live INSIDE the workspace
    # (``<workspace>/uploads/{staging,mapping_memory.json}``), so the
    # WORKSPACES_ROOT patch above already isolates them -- no separate seam.

    default_id = workspaces.default_workspace_id()
    token = workspaces.set_current_workspace_id(default_id)
    try:
        yield
    finally:
        workspaces.reset_current_workspace_id(token)
        mp.undo()


@pytest.fixture(scope="session", autouse=True)
def _isolate_rag_pdf_cache(tmp_path_factory: pytest.TempPathFactory):
    """Keep the PDF ingest cache out of the developer's real ``runtime/``.

    ``services/rag_pdf.py`` extracts each source PDF once and caches the chunks
    under ``runtime/rag_pdf_cache/`` so retrieval does not pay seconds of pypdf
    per request. Under test that default would write into the same real
    ``runtime/`` the fixture above exists to protect — and on the dev box the
    PDF root is ``client-data/``, so a suite run would quietly drop a few MB of
    derived client data into the developer's working tree.

    The PDF ROOTS are deliberately NOT redirected. Whether the client PDFs are
    on this machine is exactly the condition the retrieval tests are written to
    branch on: with them present the citation cases run for real, without them
    those cases skip with a stated reason. Forcing an empty root here would make
    the suite pass identically on both, which is the failure mode this unit was
    created to end.
    """
    from services import rag_pdf

    cache_dir = tmp_path_factory.mktemp("rag-pdf-cache")
    mp = pytest.MonkeyPatch()
    mp.setenv("RAG_PDF_CACHE_DIR", str(cache_dir))
    rag_pdf.reset_pdf_corpus_cache()
    try:
        yield
    finally:
        mp.undo()
        rag_pdf.reset_pdf_corpus_cache()


# ---------------------------------------------------------------------------
# Unit U8: authenticating as a role, for the ONE endpoint that now requires it
# ---------------------------------------------------------------------------

#: Every account these helpers mint uses it. Above ``auth.MIN_PASSWORD_CHARS``
#: and obviously fake — it exists only inside the session-scoped tmp database
#: the fixture above points ``services.db.DB_PATH`` at.
TEST_PASSWORD = "prism-test-password"


def bearer_headers(
    username: str, role: str, *, display_name: str | None = None
) -> dict[str, str]:
    """``Authorization`` headers for a user of ``role``, created on demand.

    ONE shared helper rather than a copy per test file: U8 gates
    ``POST /api/assistant/agent/approve`` behind ``reviewer``/``admin``, and
    seven existing test modules call that endpoint. The bearer channel is used
    rather than the cookie because it needs no cookie jar and is the same
    session token either way (routers/auth.py's module docstring).

    The account is REUSED when it already exists: ``services.db.DB_PATH`` is
    redirected once per pytest session, so a fixed username would otherwise
    collide with the previous test's account. The role is asserted rather than
    silently accepted, so two tests cannot quietly share one username under
    different roles and produce a green run for the wrong reason.
    """
    from services import auth

    user = auth.get_user_by_username(username)
    if user is None:
        user = auth.create_user(username, TEST_PASSWORD, role, display_name=display_name)
    elif user["role"] != role:
        raise AssertionError(
            f"test user {username!r} already exists with role {user['role']!r}, not {role!r}"
        )
    token, _session = auth.create_session(user["id"])
    return {"Authorization": f"Bearer {token}"}


#: The account every workspace-scoped test in this suite authenticates as.
#: ``admin`` specifically, because unit P④ closed the workspace boundary: a
#: NON-default workspace now needs an authenticated member (or an admin), and
#: an admin is the one identity that works for a workspace the test just
#: created without every test also having to grant itself membership.
SUITE_ADMIN_USERNAME = "suite-admin"


def admin_headers() -> dict[str, str]:
    """``Authorization`` headers for the suite's admin account.

    Imported by name (``from conftest import admin_headers``) rather than
    injected as a fixture because its callers are the module-level
    ``_headers(workspace_id)`` helpers four test files already build every
    scoped request through — turning those into fixtures would mean threading
    an argument through several hundred call sites to express one fact.

    Not memoized: ``tests/test_auth_core.py`` repoints ``services.db.DB_PATH``
    per test, so a cached token would be a token for a database that is no
    longer there. Minting a session is a token + a SHA-256 + one INSERT; the
    scrypt is paid once, when the account is first created.
    """
    return bearer_headers(SUITE_ADMIN_USERNAME, "admin", display_name="관리자")


@pytest.fixture()
def auth_headers():
    """The :func:`bearer_headers` factory, injected — so a test never has to
    import this conftest by name. ``auth_headers("someone", "modeler")``."""
    return bearer_headers


@pytest.fixture()
def admin_auth_headers() -> dict[str, str]:
    """Headers for the suite's admin — the fixture form of :func:`admin_headers`."""
    return admin_headers()


@pytest.fixture()
def approver_headers() -> dict[str, str]:
    """Headers for an authenticated REVIEWER — the role U8's approval gate
    exists for. Use this in any test that drives ``/agent/approve`` and is not
    itself about authorization."""
    return bearer_headers("suite-reviewer", "reviewer", display_name="검토 담당자")
