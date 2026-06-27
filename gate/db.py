"""
Account storage for selfstream.

Accounts live in a small SQLite file on a docker volume. There are no public
sign ups. The admin creates every account with manage.py. Passwords are never
stored directly, only a scrypt hash with a per account salt.
"""

import hashlib
import hmac
import os
import secrets
import sqlite3
import time
from contextlib import contextmanager

DB_PATH = os.environ.get("SELFSTREAM_DB", "/data/selfstream.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    username TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    is_admin INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL,
    avatar_version INTEGER NOT NULL DEFAULT 0,
    chat_font TEXT NOT NULL DEFAULT 'system',
    bio TEXT NOT NULL DEFAULT ''
);

-- One row per time someone opened the watch page. left_at is filled in when
-- they leave, so the admin can see who watched, when, and for how long.
CREATE TABLE IF NOT EXISTS watch_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL,
    joined_at INTEGER NOT NULL,
    left_at INTEGER
);
CREATE INDEX IF NOT EXISTS idx_watch_user ON watch_sessions (username, joined_at);

-- A rolling log of chat messages, kept only so the admin can review history.
-- Old rows are purged on a schedule (see CHAT_RETENTION_SECONDS in main.py),
-- so this never grows without bound. Viewers still see chat as ephemeral.
CREATE TABLE IF NOT EXISTS chat_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL,
    display_name TEXT NOT NULL,
    text TEXT NOT NULL,
    ts INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chat_ts ON chat_log (ts);
"""


@contextmanager
def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with connect() as conn:
        conn.executescript(SCHEMA)
        # Older databases predate these columns. Add them in place so existing
        # accounts keep working after an update.
        _ensure_column(conn, "users", "avatar_version", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "users", "chat_font", "TEXT NOT NULL DEFAULT 'system'")
        _ensure_column(conn, "users", "bio", "TEXT NOT NULL DEFAULT ''")
        # If the gate restarted while people were watching, their sessions never
        # got a left_at. Close them at their start so they do not count as one
        # endless session, and so the next start is clean.
        conn.execute(
            "UPDATE watch_sessions SET left_at = joined_at WHERE left_at IS NULL"
        )


def _ensure_column(conn, table, column, decl):
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


def hash_password(password):
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1, dklen=32)
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_password(password, stored):
    try:
        algo, salt_hex, digest_hex = stored.split("$")
        if algo != "scrypt":
            return False
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
        actual = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1, dklen=32)
        return hmac.compare_digest(actual, expected)
    except (ValueError, AttributeError):
        return False


# A throwaway hash used to keep login timing steady when a username does not
# exist, so the response time does not reveal which usernames are real.
DUMMY_HASH = hash_password(secrets.token_hex(16))


def get_user(username):
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE username = ?", (username,)
        ).fetchone()
        return dict(row) if row else None


def list_users():
    with connect() as conn:
        rows = conn.execute(
            "SELECT username, display_name, is_admin, created_at "
            "FROM users ORDER BY username"
        ).fetchall()
        return [dict(r) for r in rows]


def add_user(username, display_name, password, is_admin=False):
    with connect() as conn:
        conn.execute(
            "INSERT INTO users "
            "(username, display_name, password_hash, is_admin, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (username, display_name, hash_password(password),
             1 if is_admin else 0, int(time.time())),
        )


def set_password(username, password):
    with connect() as conn:
        cur = conn.execute(
            "UPDATE users SET password_hash = ? WHERE username = ?",
            (hash_password(password), username),
        )
        return cur.rowcount > 0


def update_user(username, display_name=None, is_admin=None):
    """Change a display name and/or the admin flag. Returns True if a row matched."""
    sets = []
    values = []
    if display_name is not None:
        sets.append("display_name = ?")
        values.append(display_name)
    if is_admin is not None:
        sets.append("is_admin = ?")
        values.append(1 if is_admin else 0)
    if not sets:
        return False
    values.append(username)
    with connect() as conn:
        cur = conn.execute(
            f"UPDATE users SET {', '.join(sets)} WHERE username = ?", values
        )
        return cur.rowcount > 0


def count_admins():
    with connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM users WHERE is_admin = 1"
        ).fetchone()
        return row["n"]


def channel_owner():
    """The streamer shown on the home card: the longest-standing admin."""
    with connect() as conn:
        row = conn.execute(
            "SELECT username, display_name, avatar_version FROM users "
            "WHERE is_admin = 1 ORDER BY created_at ASC, rowid ASC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None


def bump_avatar_version(username):
    # Each change bumps the version so the browser fetches the new image instead
    # of a cached one. Returns the new version (0 means the user is unknown).
    with connect() as conn:
        conn.execute(
            "UPDATE users SET avatar_version = avatar_version + 1 WHERE username = ?",
            (username,),
        )
        row = conn.execute(
            "SELECT avatar_version FROM users WHERE username = ?", (username,)
        ).fetchone()
        return row["avatar_version"] if row else 0


def set_chat_font(username, font):
    with connect() as conn:
        conn.execute(
            "UPDATE users SET chat_font = ? WHERE username = ?", (font, username)
        )


def set_bio(username, bio):
    with connect() as conn:
        conn.execute("UPDATE users SET bio = ? WHERE username = ?", (bio, username))


def delete_user(username):
    with connect() as conn:
        # Remove the account and everything tied to it, so a deleted user leaves
        # no orphaned watch history or chat behind.
        conn.execute("DELETE FROM watch_sessions WHERE username = ?", (username,))
        conn.execute("DELETE FROM chat_log WHERE username = ?", (username,))
        cur = conn.execute("DELETE FROM users WHERE username = ?", (username,))
        return cur.rowcount > 0


# ---- Watch activity -------------------------------------------------------

def start_watch_session(username, when):
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO watch_sessions (username, joined_at) VALUES (?, ?)",
            (username, when),
        )
        return cur.lastrowid


def end_watch_session(session_id, when):
    with connect() as conn:
        conn.execute(
            "UPDATE watch_sessions SET left_at = ? WHERE id = ? AND left_at IS NULL",
            (when, session_id),
        )


# ---- Chat log -------------------------------------------------------------

def log_chat(username, display_name, text, ts):
    with connect() as conn:
        conn.execute(
            "INSERT INTO chat_log (username, display_name, text, ts) "
            "VALUES (?, ?, ?, ?)",
            (username, display_name, text, ts),
        )


def purge_old_chat(cutoff):
    """Delete chat messages older than the cutoff epoch. Returns rows removed."""
    with connect() as conn:
        cur = conn.execute("DELETE FROM chat_log WHERE ts < ?", (cutoff,))
        return cur.rowcount


def recent_chat(limit=200):
    with connect() as conn:
        rows = conn.execute(
            "SELECT username, display_name, text, ts FROM chat_log "
            "ORDER BY ts DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


# ---- Admin views ----------------------------------------------------------

def admin_list_users():
    """Every account with rolled-up activity stats, for the admin dashboard."""
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT
                u.username,
                u.display_name,
                u.is_admin,
                u.created_at,
                u.avatar_version,
                (SELECT MAX(COALESCE(left_at, joined_at))
                   FROM watch_sessions w WHERE w.username = u.username) AS last_seen,
                (SELECT COUNT(*)
                   FROM watch_sessions w WHERE w.username = u.username) AS sessions,
                (SELECT COALESCE(SUM(COALESCE(left_at, joined_at) - joined_at), 0)
                   FROM watch_sessions w WHERE w.username = u.username) AS watch_seconds,
                (SELECT COUNT(*)
                   FROM chat_log c WHERE c.username = u.username) AS messages
            FROM users u
            ORDER BY u.is_admin DESC, u.username
            """
        ).fetchall()
        return [dict(r) for r in rows]


def user_activity(username, watch_limit=50, chat_limit=100):
    """Recent watch sessions and recent chat lines for one user."""
    with connect() as conn:
        watches = conn.execute(
            "SELECT joined_at, left_at FROM watch_sessions WHERE username = ? "
            "ORDER BY joined_at DESC LIMIT ?",
            (username, watch_limit),
        ).fetchall()
        chats = conn.execute(
            "SELECT text, ts FROM chat_log WHERE username = ? "
            "ORDER BY ts DESC LIMIT ?",
            (username, chat_limit),
        ).fetchall()
        return {
            "watch_sessions": [dict(r) for r in watches],
            "chat": [dict(r) for r in chats],
        }
