"""Unit U7 — ``backend/tools/manage_users.py``, the operator bootstrap CLI.

The approved policy (user, 2026-08-06) is that this script is the ONLY way an
account is created, and that the operator supplies the password. So the tests
that matter most here are the refusals: an empty password, a mismatched
confirmation, and a second ``create-admin`` on a box that already has one.

Every test drives ``main([...])`` in-process against its own tmp database, so
nothing here spawns a shell, and no test can see another test's accounts.
"""
from __future__ import annotations

import ast
import io
from pathlib import Path

import pytest

from services import auth, db
from tools import manage_users

GOOD_PASSWORD = "operator-chosen-secret"


@pytest.fixture(autouse=True)
def cli_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "db" / "app.db"
    monkeypatch.setattr(db, "DB_PATH", path)
    monkeypatch.setattr(auth, "SCRYPT_N", 1 << 8)
    monkeypatch.setattr(auth, "_dummy_hash_cache", {})
    return path


@pytest.fixture()
def stdin_password(monkeypatch: pytest.MonkeyPatch):
    def _feed(text: str) -> None:
        monkeypatch.setattr("sys.stdin", io.StringIO(text))

    return _feed


@pytest.fixture()
def no_prompt(monkeypatch: pytest.MonkeyPatch):
    """Fail loudly if a command reaches an interactive prompt it should not."""
    def _boom(prompt: str = "") -> str:
        raise AssertionError(f"unexpected interactive password prompt: {prompt!r}")

    monkeypatch.setattr(manage_users.getpass, "getpass", _boom)


# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------

def test_create_admin_happy_path_via_password_stdin(stdin_password, capsys):
    stdin_password(GOOD_PASSWORD + "\n")
    code = manage_users.main(
        ["create-admin", "--username", "yunha", "--display-name", "이윤하", "--password-stdin"]
    )
    assert code == 0
    assert "yunha" in capsys.readouterr().out

    user = auth.get_user_by_username("yunha")
    assert user["role"] == "admin" and user["display_name"] == "이윤하" and user["disabled"] is False
    assert auth.authenticate("yunha", GOOD_PASSWORD) is not None


def test_password_stdin_accepts_a_stream_without_a_trailing_newline(stdin_password):
    stdin_password(GOOD_PASSWORD)
    assert manage_users.main(["create-admin", "--username", "yunha", "--password-stdin"]) == 0
    assert auth.authenticate("yunha", GOOD_PASSWORD) is not None


def test_create_admin_prompts_twice_when_interactive(monkeypatch: pytest.MonkeyPatch):
    typed = iter([GOOD_PASSWORD, GOOD_PASSWORD])
    prompts: list[str] = []

    def fake_getpass(prompt: str = "") -> str:
        prompts.append(prompt)
        return next(typed)

    monkeypatch.setattr(manage_users.getpass, "getpass", fake_getpass)
    assert manage_users.main(["create-admin", "--username", "yunha"]) == 0
    assert len(prompts) == 2, "the password must be typed twice"
    assert auth.authenticate("yunha", GOOD_PASSWORD) is not None


def test_a_mismatched_confirmation_creates_nothing(monkeypatch: pytest.MonkeyPatch, capsys):
    typed = iter([GOOD_PASSWORD, "something-else-entirely"])
    monkeypatch.setattr(manage_users.getpass, "getpass", lambda prompt="": next(typed))
    assert manage_users.main(["create-admin", "--username", "yunha"]) == manage_users.EXIT_REFUSED
    assert "다릅니다" in capsys.readouterr().err
    assert auth.list_users() == []


@pytest.mark.parametrize("stdin_text", ["", "\n", "\r\n"])
def test_an_empty_password_is_refused(stdin_password, capsys, stdin_text: str):
    stdin_password(stdin_text)
    code = manage_users.main(["create-admin", "--username", "yunha", "--password-stdin"])
    assert code == manage_users.EXIT_REFUSED
    assert "비밀번호가 비어" in capsys.readouterr().err
    assert auth.list_users() == [], "a refused bootstrap must leave no account behind"


def test_a_short_password_is_refused(stdin_password, capsys):
    stdin_password("short\n")
    code = manage_users.main(["create-admin", "--username", "yunha", "--password-stdin"])
    assert code == manage_users.EXIT_REFUSED
    assert str(auth.MIN_PASSWORD_CHARS) in capsys.readouterr().err
    assert auth.list_users() == []


def test_a_second_admin_is_refused_without_force_additional(stdin_password, capsys, no_prompt):
    stdin_password(GOOD_PASSWORD)
    assert manage_users.main(["create-admin", "--username", "yunha", "--password-stdin"]) == 0

    # No stdin is fed and no prompt is allowed: the guard must fire BEFORE the
    # operator is asked to type a secret that was always going to be discarded.
    code = manage_users.main(["create-admin", "--username", "second", "--password-stdin"])
    assert code == manage_users.EXIT_REFUSED
    assert "--force-additional" in capsys.readouterr().err
    assert [user["username"] for user in auth.list_users()] == ["yunha"]


def test_force_additional_allows_a_second_admin(stdin_password):
    stdin_password(GOOD_PASSWORD)
    assert manage_users.main(["create-admin", "--username", "yunha", "--password-stdin"]) == 0
    stdin_password(GOOD_PASSWORD)
    assert (
        manage_users.main(
            ["create-admin", "--username", "second", "--password-stdin", "--force-additional"]
        )
        == 0
    )
    assert auth.count_admins() == 2


def test_a_duplicate_username_is_refused(stdin_password, capsys):
    stdin_password(GOOD_PASSWORD)
    assert manage_users.main(["create-user", "--username", "kim", "--role", "modeler", "--password-stdin"]) == 0
    stdin_password(GOOD_PASSWORD)
    code = manage_users.main(
        ["create-user", "--username", "KIM", "--role", "reviewer", "--password-stdin"]
    )
    assert code == manage_users.EXIT_REFUSED
    assert "KIM" in capsys.readouterr().err
    assert len(auth.list_users()) == 1


def test_an_invalid_username_is_refused(stdin_password, capsys):
    stdin_password(GOOD_PASSWORD)
    code = manage_users.main(
        ["create-user", "--username", "한글", "--role", "modeler", "--password-stdin"]
    )
    assert code == manage_users.EXIT_REFUSED
    assert "사용자 ID" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# The other commands
# ---------------------------------------------------------------------------

def test_create_user_binds_the_requested_role(stdin_password):
    for username, role in (("kim", "modeler"), ("park", "reviewer"), ("choi", "admin")):
        stdin_password(GOOD_PASSWORD)
        assert manage_users.main(
            ["create-user", "--username", username, "--role", role, "--password-stdin"]
        ) == 0
    assert {user["username"]: user["role"] for user in auth.list_users()} == {
        "kim": "modeler",
        "park": "reviewer",
        "choi": "admin",
    }


def test_create_user_rejects_a_role_the_product_does_not_have(capsys):
    with pytest.raises(SystemExit):  # argparse choices
        manage_users.main(["create-user", "--username", "kim", "--role", "superuser"])


def test_reset_password_replaces_the_password_and_revokes_sessions(stdin_password, capsys):
    stdin_password(GOOD_PASSWORD)
    manage_users.main(["create-user", "--username", "kim", "--role", "modeler", "--password-stdin"])
    user = auth.get_user_by_username("kim")
    token, _ = auth.create_session(user["id"])

    stdin_password("a-brand-new-secret")
    assert manage_users.main(["reset-password", "--username", "kim", "--password-stdin"]) == 0
    assert "kim" in capsys.readouterr().out
    assert auth.authenticate("kim", GOOD_PASSWORD) is None
    assert auth.authenticate("kim", "a-brand-new-secret") is not None
    assert auth.resolve_session(token) is None


def test_disable_and_re_enable(stdin_password, capsys):
    stdin_password(GOOD_PASSWORD)
    manage_users.main(["create-user", "--username", "kim", "--role", "modeler", "--password-stdin"])
    user = auth.get_user_by_username("kim")
    token, _ = auth.create_session(user["id"])

    assert manage_users.main(["disable-user", "--username", "kim"]) == 0
    assert auth.get_user_by_username("kim")["disabled"] is True
    assert auth.resolve_session(token) is None

    assert manage_users.main(["disable-user", "--username", "kim", "--enable"]) == 0
    assert auth.get_user_by_username("kim")["disabled"] is False


def test_commands_on_an_unknown_user_are_refused(stdin_password, capsys):
    stdin_password(GOOD_PASSWORD)
    assert manage_users.main(["reset-password", "--username", "ghost", "--password-stdin"]) == manage_users.EXIT_REFUSED
    assert manage_users.main(["disable-user", "--username", "ghost"]) == manage_users.EXIT_REFUSED
    assert "ghost" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# list-users
# ---------------------------------------------------------------------------

def test_list_users_never_prints_a_hash_fragment(stdin_password, capsys):
    stdin_password(GOOD_PASSWORD)
    manage_users.main(
        ["create-admin", "--username", "yunha", "--display-name", "이윤하", "--password-stdin"]
    )
    capsys.readouterr()

    assert manage_users.main(["list-users"]) == 0
    out = capsys.readouterr().out
    assert "yunha" in out and "admin" in out and "이윤하" in out
    assert "$" not in out, "a '$' in the output means an encoded hash leaked"
    assert "scrypt" not in out
    assert GOOD_PASSWORD not in out

    encoded = auth.hash_password(GOOD_PASSWORD)
    assert encoded.split("$")[-1] not in out


def test_the_korean_status_column_stays_aligned(stdin_password, capsys):
    """"사용" is two characters and four terminal columns — ``ljust`` would
    misalign every column after it in the product's primary language."""
    assert manage_users._display_width("사용") == 4
    assert manage_users._display_width("active") == 6
    assert manage_users._display_width(manage_users._pad("사용", 8)) == 8

    for username in ("yunha", "kim"):
        stdin_password(GOOD_PASSWORD)
        manage_users.main(["create-user", "--username", username, "--role", "modeler", "--password-stdin"])
    capsys.readouterr()
    manage_users.main(["list-users"])

    rows = [line for line in capsys.readouterr().out.splitlines() if "T" in line and "+00:00" in line]
    assert len(rows) == 2
    starts = {line.index("2026") if "2026" in line else None for line in rows}
    assert len(starts) == 1 and None not in starts, "timestamp column drifted between rows"


def test_list_users_on_an_empty_install_says_so(capsys):
    assert manage_users.main(["list-users"]) == 0
    out = capsys.readouterr().out
    assert "create-admin" in out
    assert auth.list_users() == []


def test_output_language_follows_the_flag(stdin_password, capsys):
    stdin_password(GOOD_PASSWORD)
    manage_users.main(["--language", "en", "create-admin", "--username", "yunha", "--password-stdin"])
    assert "Created account" in capsys.readouterr().out

    manage_users.main(["--language", "ja", "list-users"])
    assert "データベース" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Where it writes
# ---------------------------------------------------------------------------

def test_the_db_flag_redirects_the_whole_database(tmp_path: Path, stdin_password, cli_db: Path):
    elsewhere = tmp_path / "elsewhere" / "other.db"
    stdin_password(GOOD_PASSWORD)
    assert manage_users.main(
        ["--db", str(elsewhere), "create-admin", "--username", "yunha", "--password-stdin"]
    ) == 0
    assert elsewhere.exists()
    assert not cli_db.exists(), "the default database must not have been created"


def test_the_cli_creates_nothing_but_the_database(tmp_path: Path, stdin_password, cli_db: Path):
    """It deliberately does NOT bind the default workspace the way the probe
    scripts do: ``default_workspace_id()`` creates workspace directories and
    runs the legacy dataset migration, and creating an account must not
    relocate a consultant's model. See the module docstring.

    Asserted against the parse tree, not the text — the module docstring and
    the ``__main__`` comment both NAME ``bind_workspace`` while explaining its
    absence, and a grep would read those as the thing they warn about.
    """
    tree = ast.parse(Path(manage_users.__file__).read_text(encoding="utf-8"))
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("services")
        for alias in node.names
    } | {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    called = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "bind_workspace" not in imported and "bind_workspace" not in called
    assert "default_workspace_id" not in imported and "default_workspace_id" not in called
    assert "services.workspaces" not in imported and "services.datasets" not in imported

    before = {path.name for path in tmp_path.iterdir()}
    stdin_password(GOOD_PASSWORD)
    manage_users.main(["create-admin", "--username", "yunha", "--password-stdin"])
    after = {path.name for path in tmp_path.iterdir()}

    # The ONLY new thing under the tmp root is the database's own directory.
    assert after - before == {"db"}
    assert cli_db.exists()
    assert {path.name for path in cli_db.parent.iterdir()} <= {"app.db", "app.db-wal", "app.db-shm"}


def test_no_default_credential_anywhere_in_the_shipped_source():
    """Scans this unit's source for a hardcoded credential.

    Parsed rather than grepped: any assignment (or argument default) whose NAME
    looks like a secret and whose VALUE is a literal string is a default
    credential, and the policy is that an empty install has no way in. A
    fallback password added later would make every audit record U8 writes
    meaningless.
    """
    root = Path(manage_users.__file__).resolve().parents[1]
    sources = [
        root / "tools" / "manage_users.py",
        root / "services" / "auth.py",
        root / "services" / "db.py",
        root / "routers" / "auth.py",
    ]
    secretish = ("password", "passwd", "secret", "credential", "token")
    offenders: list[str] = []

    for path in sources:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            targets: list[tuple[str, ast.expr]] = []
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        targets.append((target.id, node.value))
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                if node.value is not None:
                    targets.append((node.target.id, node.value))
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                args = node.args
                positional = args.posonlyargs + args.args
                for arg, default in zip(positional[len(positional) - len(args.defaults):], args.defaults):
                    targets.append((arg.arg, default))
                for arg, default in zip(args.kwonlyargs, args.kw_defaults):
                    if default is not None:
                        targets.append((arg.arg, default))

            for name, value in targets:
                if not any(word in name.lower() for word in secretish):
                    continue
                if isinstance(value, ast.Constant) and isinstance(value.value, str) and value.value:
                    offenders.append(f"{path.name}:{node.lineno} {name} = {value.value!r}")

    assert not offenders, f"default credential(s) found: {offenders}"
