"""Operator CLI for Prism accounts — the ONLY way a user comes into existence.

Roadmap 2.5 / unit U7. **User-approved policy, 2026-08-06:** bootstrap is an
operator running this script and supplying a password. There is no default
credential anywhere in Prism — no ``admin/admin``, no seeded demo account, no
"first request creates an owner" branch. An empty ``users`` table means nobody
can log in, and that is the correct state for a fresh install: the next unit
(U8) puts these names in the audit document's approver column, and a name that
anyone could have typed is not an approval record.

Usage (run from ``backend/``, with the venv python)::

    python tools/manage_users.py create-admin --username yunha --display-name "이윤하"
    python tools/manage_users.py create-user --username kim --role reviewer
    python tools/manage_users.py reset-password --username kim
    python tools/manage_users.py disable-user --username kim          # --enable to undo
    python tools/manage_users.py list-users

Passwords are read interactively with ``getpass`` (typed twice, never echoed),
or from stdin with ``--password-stdin`` for an unattended install::

    printf '%s' "$PRISM_ADMIN_PASSWORD" | python tools/manage_users.py \\
        create-admin --username yunha --password-stdin

``create-admin`` REFUSES when an enabled admin already exists, unless
``--force-additional`` is given: the common way a bootstrap CLI becomes a
backdoor is somebody re-running it on a live box.

``--db PATH`` (or ``PRISM_DB_PATH``) points at a different database file; the
default is ``runtime/db/app.db``.

WHAT THIS SCRIPT TOUCHES
------------------------
The SQLite file, and nothing else. It deliberately does NOT do what the other
``backend/tools`` scripts' ``__main__`` blocks do — ``with
bind_workspace(default_workspace_id())`` — because that call is not the
read-only "say which project I mean" it looks like: ``default_workspace_id()``
goes through ``ensure_default_workspace()``, which CREATES
``runtime/workspaces/<id>/``, writes the workspace registry, and runs the
one-time legacy migration that MOVES ``runtime/uploads/{current,staging,
mapping_memory.json}`` onto disk elsewhere. Creating an account must not
relocate a consultant's dataset as a side effect. It is also unnecessary:
auth is global by design (``services/auth.py``), so nothing in this script's
call graph resolves a workspace-scoped path — verified by the fact that no
command here imports ``services.datasets``, ``services.workspaces`` or any
module that does. No Vissim COM is touched either.
"""
from __future__ import annotations

import argparse
import getpass
import sys
import unicodedata
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from services import auth, db  # noqa: E402  (path bootstrap must run first)
from services.agent.planner import pick_language_text  # noqa: E402

EXIT_OK = 0
EXIT_REFUSED = 2

#: ko primary, en/ja through the same ``pick_language_text`` lookup the rest of
#: the product uses. Operator-facing text lives here rather than in
#: ``services/auth.py``, whose exception strings are developer/log text.
_TEXT: dict[str, dict[str, str]] = {
    "admin_exists": {
        "ko": "이미 관리자 계정이 있습니다. 추가로 만들려면 --force-additional 을 사용하세요.",
        "en": "An admin account already exists. Use --force-additional to create another.",
        "ja": "管理者アカウントが既に存在します。追加で作成するには --force-additional を使用してください。",
    },
    "user_exists": {
        "ko": "이미 있는 사용자 ID입니다: {username}",
        "en": "That username already exists: {username}",
        "ja": "既に存在するユーザーIDです: {username}",
    },
    "unknown_user": {
        "ko": "사용자를 찾을 수 없습니다: {username}",
        "en": "No such user: {username}",
        "ja": "ユーザーが見つかりません: {username}",
    },
    "bad_username": {
        "ko": "사용자 ID는 영문/숫자로 시작하는 2~64자여야 합니다 (. _ - @ 사용 가능).",
        "en": "Username must be 2-64 ASCII characters starting with a letter or digit (. _ - @ allowed).",
        "ja": "ユーザーIDは英数字で始まる2~64文字にしてください (. _ - @ が使用可能)。",
    },
    "weak_password": {
        "ko": "비밀번호는 {minimum}자 이상이어야 합니다. 계정을 만들지 않았습니다.",
        "en": "Password must be at least {minimum} characters. No account was created.",
        "ja": "パスワードは{minimum}文字以上にしてください。アカウントは作成されていません。",
    },
    "empty_password": {
        "ko": "비밀번호가 비어 있습니다. 아무것도 변경하지 않았습니다.",
        "en": "The password was empty. Nothing was changed.",
        "ja": "パスワードが空です。何も変更していません。",
    },
    "password_mismatch": {
        "ko": "두 번 입력한 비밀번호가 다릅니다. 아무것도 변경하지 않았습니다.",
        "en": "The two passwords did not match. Nothing was changed.",
        "ja": "2回入力したパスワードが一致しません。何も変更していません。",
    },
    "prompt_password": {
        "ko": "새 비밀번호: ",
        "en": "New password: ",
        "ja": "新しいパスワード: ",
    },
    "prompt_confirm": {
        "ko": "비밀번호 확인: ",
        "en": "Confirm password: ",
        "ja": "パスワードの確認: ",
    },
    "created": {
        "ko": "계정을 만들었습니다: {username} (권한: {role}, 표시 이름: {display_name})",
        "en": "Created account: {username} (role: {role}, display name: {display_name})",
        "ja": "アカウントを作成しました: {username} (権限: {role}, 表示名: {display_name})",
    },
    "password_reset": {
        "ko": "{username} 의 비밀번호를 변경했습니다. 기존 로그인 세션은 모두 만료되었습니다.",
        "en": "Password changed for {username}. All existing sessions were revoked.",
        "ja": "{username} のパスワードを変更しました。既存のセッションはすべて無効化されました。",
    },
    "disabled": {
        "ko": "{username} 계정을 비활성화했습니다. 기존 로그인 세션은 모두 만료되었습니다.",
        "en": "Disabled {username}. All existing sessions were revoked.",
        "ja": "{username} を無効化しました。既存のセッションはすべて無効化されました。",
    },
    "enabled": {
        "ko": "{username} 계정을 다시 활성화했습니다.",
        "en": "Re-enabled {username}.",
        "ja": "{username} を再度有効化しました。",
    },
    "no_users": {
        "ko": "등록된 계정이 없습니다. 'create-admin' 으로 첫 관리자를 만드세요.",
        "en": "No accounts exist yet. Run 'create-admin' to create the first administrator.",
        "ja": "アカウントがまだありません。'create-admin' で最初の管理者を作成してください。",
    },
    "db_line": {
        "ko": "데이터베이스: {path}",
        "en": "Database: {path}",
        "ja": "データベース: {path}",
    },
    "status_active": {"ko": "사용", "en": "active", "ja": "有効"},
    "status_disabled": {"ko": "비활성", "en": "disabled", "ja": "無効"},
}


def _say(key: str, language: str, **fields: object) -> str:
    return pick_language_text(_TEXT[key], language).format(**fields)


def _display_width(text: str) -> int:
    """Terminal columns ``text`` occupies.

    ``str.ljust`` counts characters, but "사용" is two characters and FOUR
    columns — so padding the localized STATUS column with ljust misaligns every
    column after it in exactly the language this product ships first.
    """
    return sum(2 if unicodedata.east_asian_width(char) in ("W", "F") else 1 for char in text)


def _pad(text: str, width: int) -> str:
    return text + " " * max(0, width - _display_width(text))


def _read_password(*, from_stdin: bool, language: str) -> str | None:
    """The operator's password, or ``None`` when they failed to supply one.

    ``--password-stdin`` reads the WHOLE stream and strips at most one trailing
    newline (``printf '%s'`` and ``echo`` both work, the way ``docker login
    --password-stdin`` behaves). Interactive mode asks twice through
    ``getpass``, which does not echo and does not land in shell history.
    """
    if from_stdin:
        raw = sys.stdin.read()
        if raw.endswith("\n"):
            raw = raw[:-1]
        if raw.endswith("\r"):
            raw = raw[:-1]
        if not raw:
            print(_say("empty_password", language), file=sys.stderr)
            return None
        return raw

    first = getpass.getpass(_say("prompt_password", language))
    if not first:
        print(_say("empty_password", language), file=sys.stderr)
        return None
    second = getpass.getpass(_say("prompt_confirm", language))
    if first != second:
        print(_say("password_mismatch", language), file=sys.stderr)
        return None
    return first


def _refuse_auth_error(exc: auth.AuthError, language: str, *, username: str = "") -> int:
    """Render a service-layer refusal in the operator's language.

    Mapped by exception TYPE, so the localized text lives here and the service
    keeps one English message per failure instead of three.
    """
    if isinstance(exc, auth.WeakPasswordError):
        print(_say("weak_password", language, minimum=auth.MIN_PASSWORD_CHARS), file=sys.stderr)
    elif isinstance(exc, auth.UserExistsError):
        print(_say("user_exists", language, username=username), file=sys.stderr)
    elif isinstance(exc, auth.UnknownUserError):
        print(_say("unknown_user", language, username=username), file=sys.stderr)
    elif isinstance(exc, auth.InvalidUsernameError):
        print(_say("bad_username", language), file=sys.stderr)
    else:
        print(str(exc), file=sys.stderr)
    return EXIT_REFUSED


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def _cmd_create(args: argparse.Namespace, *, role: str) -> int:
    language = args.language
    if role == "admin" and not args.force_additional and auth.count_admins() > 0:
        # Checked BEFORE the password prompt so a refused run never asks the
        # operator to type a secret it was always going to throw away.
        print(_say("admin_exists", language), file=sys.stderr)
        return EXIT_REFUSED

    password = _read_password(from_stdin=args.password_stdin, language=language)
    if password is None:
        return EXIT_REFUSED

    try:
        user = auth.create_user(
            args.username, password, role, display_name=args.display_name
        )
    except auth.AuthError as exc:
        return _refuse_auth_error(exc, language, username=args.username)

    print(
        _say(
            "created",
            language,
            username=user["username"],
            role=user["role"],
            display_name=user["display_name"],
        )
    )
    return EXIT_OK


def _cmd_create_admin(args: argparse.Namespace) -> int:
    return _cmd_create(args, role="admin")


def _cmd_create_user(args: argparse.Namespace) -> int:
    return _cmd_create(args, role=args.role)


def _cmd_reset_password(args: argparse.Namespace) -> int:
    language = args.language
    password = _read_password(from_stdin=args.password_stdin, language=language)
    if password is None:
        return EXIT_REFUSED
    try:
        user = auth.set_password(args.username, password)
    except auth.AuthError as exc:
        return _refuse_auth_error(exc, language, username=args.username)
    print(_say("password_reset", language, username=user["username"]))
    return EXIT_OK


def _cmd_disable_user(args: argparse.Namespace) -> int:
    language = args.language
    disabled = not args.enable
    try:
        user = auth.set_disabled(args.username, disabled)
    except auth.AuthError as exc:
        return _refuse_auth_error(exc, language, username=args.username)
    print(_say("disabled" if disabled else "enabled", language, username=user["username"]))
    return EXIT_OK


def _cmd_list_users(args: argparse.Namespace) -> int:
    """Every account, one per line. NEVER a password hash — the rendered
    columns are chosen explicitly, and ``services.auth.list_users`` does not
    even carry the hash out of the database."""
    language = args.language
    print(_say("db_line", language, path=db.database_path()))
    users = auth.list_users()
    if not users:
        print(_say("no_users", language))
        return EXIT_OK

    # The free-form UTF-8 display name goes LAST so nothing has to be padded
    # after it; the columns before it are padded by DISPLAY width, because the
    # localized status ("사용" / "無効") is double-width in a terminal.
    statuses = {
        index: _say("status_disabled" if user["disabled"] else "status_active", language)
        for index, user in enumerate(users)
    }
    name_width = max([_display_width(str(user["username"])) for user in users] + [len("USERNAME")])
    role_width = max([_display_width(str(user["role"])) for user in users] + [len("ROLE")])
    status_width = max([_display_width(text) for text in statuses.values()] + [len("STATUS")])

    print(
        f"{_pad('USERNAME', name_width)}  {_pad('ROLE', role_width)}  "
        f"{_pad('STATUS', status_width)}  CREATED / NAME"
    )
    for index, user in enumerate(users):
        print(
            f"{_pad(str(user['username']), name_width)}  {_pad(str(user['role']), role_width)}  "
            f"{_pad(statuses[index], status_width)}  {user['created_at']}  {user['display_name']}"
        )
    return EXIT_OK


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def _add_password_flag(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--password-stdin",
        action="store_true",
        help="Read the password from stdin instead of prompting (unattended install).",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="manage_users.py",
        description="Prism account management. No default credential exists; every "
        "account is created here by an operator.",
    )
    parser.add_argument("--language", choices=("ko", "en", "ja"), default="ko")
    parser.add_argument(
        "--db",
        default=None,
        help="Database file to operate on (default: runtime/db/app.db, or $PRISM_DB_PATH).",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    create_admin = subparsers.add_parser(
        "create-admin", help="Create the first administrator account (the bootstrap)."
    )
    create_admin.add_argument("--username", required=True)
    create_admin.add_argument("--display-name", default=None, help="The human name reports show.")
    create_admin.add_argument(
        "--force-additional",
        action="store_true",
        help="Create another admin even though one already exists.",
    )
    _add_password_flag(create_admin)
    create_admin.set_defaults(func=_cmd_create_admin)

    create_user = subparsers.add_parser("create-user", help="Create a non-bootstrap account.")
    create_user.add_argument("--username", required=True)
    create_user.add_argument("--role", required=True, choices=auth.ROLES)
    create_user.add_argument("--display-name", default=None)
    create_user.add_argument("--force-additional", action="store_true", help=argparse.SUPPRESS)
    _add_password_flag(create_user)
    create_user.set_defaults(func=_cmd_create_user)

    reset_password = subparsers.add_parser(
        "reset-password", help="Set a new password (and revoke that user's sessions)."
    )
    reset_password.add_argument("--username", required=True)
    _add_password_flag(reset_password)
    reset_password.set_defaults(func=_cmd_reset_password)

    disable_user = subparsers.add_parser(
        "disable-user", help="Disable an account (and revoke its sessions)."
    )
    disable_user.add_argument("--username", required=True)
    disable_user.add_argument(
        "--enable", action="store_true", help="Re-enable instead of disabling."
    )
    disable_user.set_defaults(func=_cmd_disable_user)

    list_users = subparsers.add_parser("list-users", help="List accounts. Never prints hashes.")
    list_users.set_defaults(func=_cmd_list_users)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.db:
        # The same module-constant seam tests use. Set before any command runs,
        # so the schema is created at the requested path and nowhere else.
        db.DB_PATH = Path(args.db)
    return int(args.func(args))


if __name__ == "__main__":
    # No ``bind_workspace`` here, unlike the probe/live-check scripts in this
    # directory — see the module docstring: binding would create workspace
    # directories and run the legacy dataset migration, and account management
    # has no business doing either.
    raise SystemExit(main())
