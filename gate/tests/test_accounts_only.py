"""
Accounts only: nobody reaches the room without an account.

Guest access (links, passes, the human challenge and the rows they made) is
gone. These pin the three things that keep it gone: its routes answer 404, the
rows an older install left behind are removed at startup before anything is
served, and no code reads the legacy guest columns again. That last one is the
security half: once nothing reads is_guest, a leftover guest row would sign in
as an ordinary account, so the purge is what keeps an old guest cookie dead.
"""

import inspect
import logging
import os
import re
import time

import pytest
from starlette.websockets import WebSocketDisconnect

import auth
import db
from config import COOKIE_NAME
from conftest import make_client

from test_api import add_user, login, setup_admin


GATE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB = os.path.join(os.path.dirname(GATE), "web")
LEGACY_NAME = "guest_1a2b3c4d"


def plant_legacy_guest(username=LEGACY_NAME):
    """Give the fresh database what an older install leaves behind: the legacy
    columns, a pass table with a redeemed pass, and one guest with a row in
    every table delete_user clears."""
    now = int(time.time())
    with db.connect() as conn:
        db._ensure_column(conn, "users", "is_guest", "INTEGER NOT NULL DEFAULT 0")
        db._ensure_column(conn, "users", "guest_expires_at", "INTEGER NOT NULL DEFAULT 0")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS guest_passes (code TEXT PRIMARY KEY, "
            "label TEXT DEFAULT '', created_by TEXT, created_at INTEGER, "
            "revoked_at INTEGER, redeemed_by TEXT, redeemed_at INTEGER)"
        )
        conn.execute(
            "INSERT INTO guest_passes (code, created_by, created_at, redeemed_by, "
            "redeemed_at) VALUES ('ember-quiet-harbor', 'owner', ?, ?, ?)",
            (now, username, now),
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS guest_link_guests (username TEXT PRIMARY KEY, "
            "code TEXT NOT NULL, display_name TEXT NOT NULL DEFAULT '', "
            "joined_at INTEGER NOT NULL, ended_at INTEGER)"
        )
        conn.execute(
            "INSERT INTO users (username, display_name, password_hash, created_at, "
            "is_guest, guest_expires_at) VALUES (?, 'Sam', 'no-password', ?, 1, ?)",
            (username, now, now + 1800),
        )
    db.start_watch_session(username, now)
    db.log_chat(username, "Sam", "hello", now)
    db.add_ban(username, "owner", "", now)
    db.set_like("clip", 1, username, True, now)
    db.add_comment("clip", 1, username, "nice", now)


def rows_for(username):
    with db.connect() as conn:
        return {
            table: conn.execute(
                f"SELECT COUNT(*) AS n FROM {table} WHERE username = ?", (username,)
            ).fetchone()["n"]
            for table in ("users", "watch_sessions", "chat_log", "bans",
                          "media_likes", "media_comments")
        }


def table_exists(name):
    with db.connect() as conn:
        return conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
        ).fetchone() is not None


# ---- the routes are gone ---------------------------------------------------

@pytest.mark.parametrize("method,path", [
    ("GET", "/api/guest/challenge"),
    ("POST", "/api/guest/peek"),
    ("POST", "/api/guest"),
    ("GET", "/api/admin/guest-links"),
    ("POST", "/api/admin/guest-links"),
    ("POST", "/api/admin/guest-links/revoke"),
    ("POST", "/api/admin/guest-links/kick"),
    ("POST", "/api/admin/guest-links/remove"),
    ("POST", "/api/admin/guest-links/clear"),
    ("GET", "/api/admin/guest-passes"),
    ("POST", "/api/admin/guest-passes"),
    ("DELETE", "/api/admin/guest-passes/ember-quiet-harbor"),
    ("POST", "/api/admin/guest-passes/ember-quiet-harbor/remove"),
    ("POST", "/api/admin/guest-passes/clear-used"),
])
def test_every_old_guest_route_answers_404(client, method, path):
    # As the admin, so a 403 from the admin gate cannot pass for "gone".
    setup_admin(client)
    kwargs = {"json": {}} if method == "POST" else {}
    assert client.request(method, path, **kwargs).status_code == 404


# ---- the startup purge -----------------------------------------------------

def test_the_purge_removes_guests_and_everything_they_left(client):
    setup_admin(client)
    add_user("nell")
    now = int(time.time())
    db.log_chat("nell", "Nell", "still here", now)
    db.start_watch_session("nell", now)
    plant_legacy_guest()
    assert rows_for(LEGACY_NAME)["users"] == 1

    db.init_db()

    assert set(rows_for(LEGACY_NAME).values()) == {0}
    assert not table_exists("guest_passes")
    assert not table_exists("guest_link_guests")
    # Members are untouched.
    assert db.get_user("nell") is not None
    assert rows_for("nell")["chat_log"] == 1
    assert rows_for("nell")["watch_sessions"] == 1
    assert db.get_user("owner")["is_admin"] == 1


def test_the_purge_is_idempotent_and_logs_counts_only(client, caplog):
    setup_admin(client)
    plant_legacy_guest()
    with caplog.at_level(logging.INFO, logger="upperroom.db"):
        db.init_db()
    said = [r.getMessage() for r in caplog.records if r.name == "upperroom.db"]
    assert said == ["guest access removed: 1 account(s) deleted, 2 table(s) dropped"]
    assert not any(LEGACY_NAME in line for line in said)

    caplog.clear()
    with caplog.at_level(logging.INFO, logger="upperroom.db"):
        db.init_db()
    assert [r for r in caplog.records if r.name == "upperroom.db"] == []


def test_a_fresh_install_has_no_guest_columns_or_tables(client):
    with db.connect() as conn:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(users)")}
    assert "is_guest" not in columns and "guest_expires_at" not in columns
    assert not table_exists("guest_passes")


def test_a_guest_cookie_from_before_the_purge_no_longer_signs_in(client):
    setup_admin(client)
    plant_legacy_guest()
    token = auth.issue_token(db.get_user(LEGACY_NAME))
    cookie = {COOKIE_NAME: token}
    old = make_client()
    # Nothing reads is_guest any more, so before the purge the row would pass
    # for an account. This is the case the purge exists for.
    assert old.get("/api/me", cookies=cookie).json()["authed"] is True

    db.init_db()

    assert old.get("/api/me", cookies=cookie).json() == {"authed": False}
    assert old.get("/api/verify?scope=live", cookies=cookie).status_code == 401
    assert old.get("/api/verify?scope=media", cookies=cookie).status_code == 401
    assert old.get("/api/channel", cookies=cookie).status_code == 401
    with old.websocket_connect("/ws", cookies=cookie) as ws:
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_json()
    assert closed.value.code == 4401


# ---- nothing reads the legacy columns again --------------------------------

# The one note at the schema, and the purge. Everything else is a regression.
_SCHEMA_NOTE = (
    "    -- Older databases also carry is_guest and guest_expires_at: legacy and\n"
    "    -- unused. init_db removes guest rows (_purge_guests); nothing reads them.\n"
)
_INIT_CALL = (
    "        # Accounts only: remove whatever an older install left of guest\n"
    "        # access, before anything can be served.\n"
    "        _purge_guests(conn)\n"
)
_GUEST = re.compile(r"guest", re.IGNORECASE)


def test_no_code_reads_guest_state_again():
    db_path = os.path.join(GATE, "db.py")
    with open(db_path, encoding="utf-8") as fh:
        db_source = fh.read()
    # The allowances must match exactly, or they would allow nothing.
    for allowed in (_SCHEMA_NOTE, _INIT_CALL, inspect.getsource(db._purge_guests)):
        assert db_source.count(allowed) == 1

    hits = []
    for base in (GATE, WEB):
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames
                           if d not in ("tests", "__pycache__", "vendor", "fonts", "icons")]
            for name in filenames:
                if not name.endswith((".py", ".js", ".html", ".css")):
                    continue
                path = os.path.join(dirpath, name)
                # Release notes tell viewers what changed, guests leaving included.
                if path == os.path.join(GATE, "changelog.py"):
                    continue
                with open(path, encoding="utf-8") as fh:
                    text = fh.read()
                if path == db_path:
                    for allowed in (_SCHEMA_NOTE, _INIT_CALL,
                                    inspect.getsource(db._purge_guests)):
                        text = text.replace(allowed, "")
                for number, line in enumerate(text.splitlines(), 1):
                    if _GUEST.search(line):
                        hits.append(f"{os.path.relpath(path, GATE)}:{number}: {line.strip()}")
    assert hits == []


# ---- invite codes are the public credential now ---------------------------

def test_the_invite_code_space_is_large_enough_to_be_public():
    """An invite code is guessable at a public endpoint (/api/register) and
    travels as a link, so the wordlist size is a security property. Three words
    from the old 48 word list was 110,592 combinations, which is walkable."""
    assert db.CODE_SPACE > 10_000_000
    assert len(set(db._CODE_WORDS)) == len(db._CODE_WORDS)
    code = db._new_code()
    assert len(code.split("-")) == db.CODE_WORD_COUNT
    assert all(part in db._CODE_WORDS for part in code.split("-"))


# ---- what a signed-in account may do ---------------------------------------

def test_any_account_reaches_the_library_and_its_own_profile(client):
    """With no second kind of session, the checks that used to tell the two
    apart are one check: a signed-in account."""
    setup_admin(client)
    add_user("nell")
    viewer = make_client()
    login(viewer, "nell")
    assert viewer.get("/api/verify?scope=media").status_code == 200
    assert viewer.get("/api/verify?scope=art").status_code == 200
    assert viewer.get("/api/points").status_code == 200
    assert viewer.post("/api/profile", json={"bio": "hi"}).status_code == 200
    stranger = make_client()
    assert stranger.get("/api/verify?scope=media").status_code == 401
    assert stranger.get("/api/points").status_code == 401
