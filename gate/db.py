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
    created_at INTEGER NOT NULL
);
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


def delete_user(username):
    with connect() as conn:
        cur = conn.execute("DELETE FROM users WHERE username = ?", (username,))
        return cur.rowcount > 0
