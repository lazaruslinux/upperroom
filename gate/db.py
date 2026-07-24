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

# The accent flavors the admin can pick for the whole channel. green is the
# default and the historical color; the others are alternate brand tints. Every
# visitor sees the chosen one; validated server side so only these four persist.
ACCENTS = ("green", "amber", "blue", "ghost")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    username TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    is_admin INTEGER NOT NULL DEFAULT 0,
    is_moderator INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL,
    avatar_version INTEGER NOT NULL DEFAULT 0,
    chat_font TEXT NOT NULL DEFAULT 'system',
    bio TEXT NOT NULL DEFAULT '',
    -- Optional address for the "channel is live" email. notify_live is the
    -- viewer's own opt-out; it defaults on since the admin only makes accounts
    -- for known people, and an email is sent only when both are set.
    email TEXT NOT NULL DEFAULT '',
    notify_live INTEGER NOT NULL DEFAULT 1,
    -- Channel points, earned by watching the stream live and spent to highlight
    -- a short message on stream.
    points INTEGER NOT NULL DEFAULT 0,
    -- Optional per-viewer chat colors: the display-name color and the message
    -- text color, each a validated "#rrggbb" or empty for the theme default.
    -- They ride along on the viewer's messages so everyone sees them.
    name_color TEXT NOT NULL DEFAULT '',
    msg_color TEXT NOT NULL DEFAULT ''
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
    ts INTEGER NOT NULL,
    deleted_by TEXT
);
CREATE INDEX IF NOT EXISTS idx_chat_ts ON chat_log (ts);

-- Channel level settings the streamer edits: the title and description shown on
-- the home card and stamped onto each VOD when a broadcast begins. A single row.
CREATE TABLE IF NOT EXISTS channel_settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    -- The operator's own brand, shown leading the visitor pages next to
    -- "powered by upperroom". Distinct from stream_title (the per-broadcast
    -- title): this is the permanent site identity. Defaults to the platform
    -- name until the operator sets their own.
    site_name TEXT NOT NULL DEFAULT 'upperroom',
    stream_title TEXT NOT NULL DEFAULT 'Live Stream',
    stream_description TEXT NOT NULL DEFAULT '',
    -- Per-role minimum minutes between clips (0 disables the cooldown for that
    -- role). Replaces the old per-day clip caps.
    clip_cooldown_user INTEGER NOT NULL DEFAULT 15,
    clip_cooldown_mod INTEGER NOT NULL DEFAULT 5,
    clip_cooldown_admin INTEGER NOT NULL DEFAULT 1,
    -- Go-live notifications. discord_webhook is an optional Discord incoming
    -- webhook URL; last_notified_at guards against re-announcing on a brief
    -- stream blip or a gate restart mid-broadcast.
    discord_webhook TEXT NOT NULL DEFAULT '',
    last_notified_at INTEGER NOT NULL DEFAULT 0,
    -- The channel-wide accent flavor every visitor sees (the brand color). One
    -- of the presets in ACCENTS; the per-user dark/light toggle is separate.
    accent TEXT NOT NULL DEFAULT 'green',
    -- A long random bearer key for the OBS chat overlay. Anyone who has it can
    -- open the read-only overlay socket, since OBS cannot sign in. Nullable
    -- until first generated; regenerating it revokes the old URL.
    overlay_key TEXT,
    -- The RTMP publish key OBS sends to go live. MediaMTX delegates publish auth
    -- to the gate, which checks this value. Nullable until first generated (or
    -- seeded from PUBLISH_PASS on an upgrade); regenerating it takes effect from
    -- the next connect and never kicks a live broadcast.
    stream_key TEXT,
    -- Chat moderation. slow_mode_seconds is the minimum gap between messages for
    -- a plain viewer (0 = off; mods and admins are exempt). banned_words is a
    -- newline/comma separated list; a message containing any of them is dropped.
    slow_mode_seconds INTEGER NOT NULL DEFAULT 0,
    banned_words TEXT NOT NULL DEFAULT ''
);

-- One row per broadcast we record. The file is written to local scratch while
-- live, then archived to the media store and marked ready when the stream ends.
CREATE TABLE IF NOT EXISTS vods (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    filename TEXT,
    started_at INTEGER NOT NULL,
    ended_at INTEGER,
    duration INTEGER,
    views INTEGER NOT NULL DEFAULT 0,
    ready INTEGER NOT NULL DEFAULT 0
);

-- A viewer made clip: a short cut of the last 30 seconds of the live stream.
CREATE TABLE IF NOT EXISTS clips (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    filename TEXT NOT NULL,
    creator TEXT NOT NULL,
    vod_id INTEGER,
    start_ts INTEGER NOT NULL,
    end_ts INTEGER NOT NULL,
    duration INTEGER NOT NULL,
    views INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL
);

-- Chat snapshotted at the moment a VOD or clip is finalized, with each line's
-- offset in seconds from the start of the media, so playback can replay chat in
-- sync. Kept apart from chat_log, which is purged on the retention schedule.
CREATE TABLE IF NOT EXISTS replay_chat (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    ref_id INTEGER NOT NULL,
    username TEXT NOT NULL,
    display_name TEXT NOT NULL,
    avatar_version INTEGER NOT NULL DEFAULT 0,
    font TEXT NOT NULL DEFAULT 'system',
    admin INTEGER NOT NULL DEFAULT 0,
    moderator INTEGER NOT NULL DEFAULT 0,
    text TEXT NOT NULL,
    offset_s INTEGER NOT NULL,
    deleted INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_replay ON replay_chat (kind, ref_id, offset_s);

-- One row per (item, viewer) so a refresh or a seek cannot inflate a view
-- count: the parent's views is bumped only when a new row is inserted here.
CREATE TABLE IF NOT EXISTS media_views (
    kind TEXT NOT NULL,
    ref_id INTEGER NOT NULL,
    username TEXT NOT NULL,
    ts INTEGER NOT NULL,
    PRIMARY KEY (kind, ref_id, username)
);

-- A persistent chat ban (unlike a timeout, it survives restarts). banned_by
-- records who issued it, so a moderator can later lift only their own bans,
-- while an admin can lift any.
CREATE TABLE IF NOT EXISTS bans (
    username TEXT PRIMARY KEY,
    banned_by TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL
);

-- Single-use invite codes. An admin generates a code; someone redeems it once to
-- create a viewer account. A revoked or redeemed code keeps its row so the
-- history stays visible on the dashboard: revoked_at and redeemed_at record when
-- each thing happened, redeemed_by records which account the code created.
CREATE TABLE IF NOT EXISTS invites (
    code TEXT PRIMARY KEY,
    label TEXT DEFAULT '',
    created_by TEXT,
    created_at INTEGER,
    revoked_at INTEGER,
    redeemed_by TEXT,
    redeemed_at INTEGER
);
"""


@contextmanager
def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    # Wait up to five seconds for a lock rather than failing at once, so a burst
    # of concurrent chat writes and dashboard reads does not raise "database is
    # locked". synchronous=NORMAL is the durable, faster pairing for WAL.
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA synchronous = NORMAL")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with connect() as conn:
        # WAL lets readers and a writer work at the same time instead of taking
        # turns on one global lock. It is a persistent property of the database
        # file, so setting it once here is enough.
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(SCHEMA)
        # Older databases predate these columns. Add them in place so existing
        # accounts keep working after an update.
        _ensure_column(conn, "users", "avatar_version", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "users", "chat_font", "TEXT NOT NULL DEFAULT 'system'")
        _ensure_column(conn, "users", "bio", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "users", "is_moderator", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "users", "email", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "users", "notify_live", "INTEGER NOT NULL DEFAULT 1")
        _ensure_column(conn, "users", "points", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "users", "name_color", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "users", "msg_color", "TEXT NOT NULL DEFAULT ''")
        # Provenance: which invite code (if any) this account was created from.
        _ensure_column(conn, "users", "invite_code", "TEXT")
        _ensure_column(conn, "chat_log", "deleted_by", "TEXT")
        _ensure_column(
            conn, "channel_settings", "site_name", "TEXT NOT NULL DEFAULT 'upperroom'"
        )
        _ensure_column(
            conn, "channel_settings", "clip_cooldown_user", "INTEGER NOT NULL DEFAULT 15"
        )
        _ensure_column(
            conn, "channel_settings", "clip_cooldown_mod", "INTEGER NOT NULL DEFAULT 5"
        )
        _ensure_column(
            conn, "channel_settings", "clip_cooldown_admin", "INTEGER NOT NULL DEFAULT 1"
        )
        _ensure_column(
            conn, "channel_settings", "discord_webhook", "TEXT NOT NULL DEFAULT ''"
        )
        _ensure_column(
            conn, "channel_settings", "last_notified_at", "INTEGER NOT NULL DEFAULT 0"
        )
        _ensure_column(
            conn, "channel_settings", "accent", "TEXT NOT NULL DEFAULT 'green'"
        )
        _ensure_column(conn, "channel_settings", "overlay_key", "TEXT")
        _ensure_column(conn, "channel_settings", "stream_key", "TEXT")
        _ensure_column(
            conn, "channel_settings", "slow_mode_seconds", "INTEGER NOT NULL DEFAULT 0"
        )
        _ensure_column(
            conn, "channel_settings", "banned_words", "TEXT NOT NULL DEFAULT ''"
        )
        # The admin-defined rewards catalog was replaced by a single built-in
        # redemption (highlight a message), so its table is dropped in place, the
        # same lightweight in-init migration as the column adds above.
        conn.execute("DROP TABLE IF EXISTS rewards")
        # Ensure the single channel_settings row exists so getters always find it.
        conn.execute(
            "INSERT OR IGNORE INTO channel_settings (id, stream_title) "
            "VALUES (1, 'Live Stream')"
        )
        # Seed the publish key from PUBLISH_PASS on an existing install so OBS and
        # the demo keep working across the switch to gate-delegated auth. Only
        # while it is still NULL: once a key exists (generated or already seeded),
        # changing the env never overwrites it. Read from the environment here,
        # not config.py, because demo_seed imports db without config and config
        # hard-requires the JWT secret.
        publish_pass = os.environ.get("PUBLISH_PASS", "")
        if publish_pass:
            conn.execute(
                "UPDATE channel_settings SET stream_key = ? "
                "WHERE id = 1 AND stream_key IS NULL",
                (publish_pass,),
            )
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
            "SELECT username, display_name, is_admin, is_moderator, created_at "
            "FROM users ORDER BY username"
        ).fetchall()
        return [dict(r) for r in rows]


def add_user(username, display_name, password, is_admin=False, email=""):
    with connect() as conn:
        conn.execute(
            "INSERT INTO users "
            "(username, display_name, password_hash, is_admin, created_at, email) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (username, display_name, hash_password(password),
             1 if is_admin else 0, int(time.time()), email or ""),
        )


def set_password(username, password):
    with connect() as conn:
        cur = conn.execute(
            "UPDATE users SET password_hash = ? WHERE username = ?",
            (hash_password(password), username),
        )
        return cur.rowcount > 0


def update_user(username, display_name=None, is_admin=None, is_moderator=None):
    """Change a display name, admin flag, and/or moderator flag. The two role
    flags are independent; setting one never touches the other. Returns True if a
    row matched."""
    sets = []
    values = []
    if display_name is not None:
        sets.append("display_name = ?")
        values.append(display_name)
    if is_admin is not None:
        sets.append("is_admin = ?")
        values.append(1 if is_admin else 0)
    if is_moderator is not None:
        sets.append("is_moderator = ?")
        values.append(1 if is_moderator else 0)
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


def count_users():
    """Total accounts. The first-run setup wizard is offered only while this is
    zero, and closes permanently once anyone exists."""
    with connect() as conn:
        row = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()
        return row["n"]


def create_first_user(username, display_name, password, when):
    """Create the very first account, as admin, but only if no account exists yet.
    The emptiness check and the insert are one atomic statement, so two racing
    setup requests can never both create an owner. Returns True if it made the
    account, False if the table was not empty (the wizard is already spent)."""
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO users "
            "(username, display_name, password_hash, is_admin, created_at) "
            "SELECT ?, ?, ?, 1, ? WHERE NOT EXISTS (SELECT 1 FROM users)",
            (username, display_name, hash_password(password), when),
        )
        return cur.rowcount > 0


def channel_owner():
    """The streamer shown on the home card: the longest-standing admin."""
    with connect() as conn:
        row = conn.execute(
            "SELECT username, display_name, avatar_version FROM users "
            "WHERE is_admin = 1 ORDER BY created_at ASC, rowid ASC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None


def get_stream_info():
    """The channel settings the streamer controls: the title and description
    shown on the home card (and stamped onto each VOD), plus the per role clip
    cooldowns in minutes."""
    with connect() as conn:
        row = conn.execute(
            "SELECT site_name, stream_title, stream_description, clip_cooldown_user, "
            "clip_cooldown_mod, clip_cooldown_admin, accent "
            "FROM channel_settings WHERE id = 1"
        ).fetchone()
        if not row:
            return {
                "site_name": "upperroom",
                "stream_title": "Live Stream",
                "stream_description": "",
                "clip_cooldown_user": 15,
                "clip_cooldown_mod": 5,
                "clip_cooldown_admin": 1,
                "accent": "green",
            }
        return dict(row)


def set_stream_info(site_name=None, title=None, description=None,
                    clip_cooldown_user=None, clip_cooldown_mod=None,
                    clip_cooldown_admin=None, accent=None):
    sets = []
    values = []
    if site_name is not None:
        sets.append("site_name = ?")
        values.append(site_name)
    if title is not None:
        sets.append("stream_title = ?")
        values.append(title)
    if description is not None:
        sets.append("stream_description = ?")
        values.append(description)
    if clip_cooldown_user is not None:
        sets.append("clip_cooldown_user = ?")
        values.append(int(clip_cooldown_user))
    if clip_cooldown_mod is not None:
        sets.append("clip_cooldown_mod = ?")
        values.append(int(clip_cooldown_mod))
    if clip_cooldown_admin is not None:
        sets.append("clip_cooldown_admin = ?")
        values.append(int(clip_cooldown_admin))
    if accent is not None:
        # Guard here too, so a bad value can never reach the column even if a
        # caller skips the route-level check.
        if accent not in ACCENTS:
            raise ValueError(f"unknown accent: {accent!r}")
        sets.append("accent = ?")
        values.append(accent)
    if not sets:
        return
    with connect() as conn:
        conn.execute(
            f"UPDATE channel_settings SET {', '.join(sets)} WHERE id = 1", values
        )


def get_chat_moderation():
    """The admin-only chat moderation settings: the slow-mode interval in seconds
    (0 = off) and the banned-words list. Kept out of get_stream_info so the
    banned-words list never rides a public endpoint."""
    with connect() as conn:
        row = conn.execute(
            "SELECT slow_mode_seconds, banned_words FROM channel_settings WHERE id = 1"
        ).fetchone()
        if not row:
            return {"slow_mode_seconds": 0, "banned_words": ""}
        return dict(row)


def set_chat_moderation(slow_mode_seconds=None, banned_words=None):
    sets = []
    values = []
    if slow_mode_seconds is not None:
        sets.append("slow_mode_seconds = ?")
        values.append(int(slow_mode_seconds))
    if banned_words is not None:
        sets.append("banned_words = ?")
        values.append(banned_words)
    if not sets:
        return
    with connect() as conn:
        conn.execute(
            f"UPDATE channel_settings SET {', '.join(sets)} WHERE id = 1", values
        )


def get_notify_settings():
    """The channel's go-live notification settings: the Discord webhook URL and
    the epoch of the last announcement (used for the cooldown)."""
    with connect() as conn:
        row = conn.execute(
            "SELECT discord_webhook, last_notified_at FROM channel_settings "
            "WHERE id = 1"
        ).fetchone()
        if not row:
            return {"discord_webhook": "", "last_notified_at": 0}
        return dict(row)


def set_discord_webhook(url):
    with connect() as conn:
        conn.execute(
            "UPDATE channel_settings SET discord_webhook = ? WHERE id = 1",
            (url or "",),
        )


def mark_notified(when):
    with connect() as conn:
        conn.execute(
            "UPDATE channel_settings SET last_notified_at = ? WHERE id = 1", (when,)
        )


# ---- Overlay key ----------------------------------------------------------
# The OBS chat overlay authenticates with a long random key in its URL, since a
# browser source cannot sign in. The key is a bearer secret: whoever holds it
# can read chat over the overlay socket, so it is shown only on the admin
# dashboard and can be regenerated to revoke an old URL.

def get_overlay_key():
    """The current overlay key, or None if one has never been generated."""
    with connect() as conn:
        row = conn.execute(
            "SELECT overlay_key FROM channel_settings WHERE id = 1"
        ).fetchone()
        return row["overlay_key"] if row else None


def regenerate_overlay_key():
    """Mint a fresh overlay key, replacing any previous one, and return it. This
    revokes the old URL: an overlay connected with the old key stops matching."""
    key = secrets.token_urlsafe(32)
    with connect() as conn:
        conn.execute(
            "UPDATE channel_settings SET overlay_key = ? WHERE id = 1", (key,)
        )
    return key


# ---- Stream key -----------------------------------------------------------
# The RTMP publish key OBS sends to go live. MediaMTX delegates publish auth to
# the gate (see routes/auth.py), which compares the connecting password against
# this value. It is a bearer secret shown only on the admin dashboard, and can
# be regenerated to rotate it; rotation applies from the next connect, so it
# never interrupts a broadcast already in progress.

def get_stream_key():
    """The current publish key, or None if one has never been generated."""
    with connect() as conn:
        row = conn.execute(
            "SELECT stream_key FROM channel_settings WHERE id = 1"
        ).fetchone()
        return row["stream_key"] if row else None


def regenerate_stream_key():
    """Mint a fresh publish key, replacing any previous one, and return it. The
    old key stops being accepted, but a live broadcast keeps going: auth is
    per-connection, so the change only bites on the next publish."""
    key = secrets.token_urlsafe(32)
    with connect() as conn:
        conn.execute(
            "UPDATE channel_settings SET stream_key = ? WHERE id = 1", (key,)
        )
    return key


def count_user_clips_since(username, since):
    """How many clips a user has made since a given epoch."""
    with connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM clips WHERE creator = ? AND created_at >= ?",
            (username, since),
        ).fetchone()
        return row["n"]


def last_clip_at(username):
    """Epoch of a user's most recent clip, or 0 if they have never clipped. Used
    to enforce the per-role clip cooldown."""
    with connect() as conn:
        row = conn.execute(
            "SELECT MAX(created_at) AS last FROM clips WHERE creator = ?", (username,)
        ).fetchone()
        return row["last"] or 0


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


def set_chat_colors(username, name_color=None, msg_color=None):
    """Store a viewer's chosen chat colors. Each argument is a validated
    "#rrggbb" string or "" to clear it; None leaves that column unchanged."""
    sets = []
    values = []
    if name_color is not None:
        sets.append("name_color = ?")
        values.append(name_color)
    if msg_color is not None:
        sets.append("msg_color = ?")
        values.append(msg_color)
    if not sets:
        return
    values.append(username)
    with connect() as conn:
        conn.execute(
            f"UPDATE users SET {', '.join(sets)} WHERE username = ?", values
        )


def set_bio(username, bio):
    with connect() as conn:
        conn.execute("UPDATE users SET bio = ? WHERE username = ?", (bio, username))


def set_email(username, email):
    with connect() as conn:
        conn.execute(
            "UPDATE users SET email = ? WHERE username = ?", (email or "", username)
        )


def set_notify_live(username, on):
    with connect() as conn:
        conn.execute(
            "UPDATE users SET notify_live = ? WHERE username = ?",
            (1 if on else 0, username),
        )


def list_live_recipients():
    """Email addresses to notify when the channel goes live: non-admin accounts
    that have an address and have not opted out. Admins run the broadcast, so
    they are never emailed that their own stream is live. Returns a list of
    (display_name, email)."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT display_name, email FROM users "
            "WHERE notify_live = 1 AND email != '' AND is_admin = 0"
        ).fetchall()
        return [(r["display_name"], r["email"]) for r in rows]


def delete_user(username):
    with connect() as conn:
        # Remove the account and everything tied to it, so a deleted user leaves
        # no orphaned watch history, chat, or ban behind.
        conn.execute("DELETE FROM watch_sessions WHERE username = ?", (username,))
        conn.execute("DELETE FROM chat_log WHERE username = ?", (username,))
        conn.execute("DELETE FROM bans WHERE username = ?", (username,))
        cur = conn.execute("DELETE FROM users WHERE username = ?", (username,))
        return cur.rowcount > 0


# ---- Bans -----------------------------------------------------------------

def add_ban(username, by, reason, when):
    with connect() as conn:
        conn.execute(
            "INSERT INTO bans (username, banned_by, reason, created_at) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(username) DO UPDATE SET "
            "banned_by = excluded.banned_by, reason = excluded.reason, "
            "created_at = excluded.created_at",
            (username, by, reason, when),
        )


def remove_ban(username):
    with connect() as conn:
        cur = conn.execute("DELETE FROM bans WHERE username = ?", (username,))
        return cur.rowcount > 0


def get_ban(username):
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM bans WHERE username = ?", (username,)
        ).fetchone()
        return dict(row) if row else None


def banned_usernames():
    with connect() as conn:
        rows = conn.execute("SELECT username FROM bans").fetchall()
        return [r["username"] for r in rows]


def list_bans():
    """Every active ban with display names, newest first. Used by the mod and
    admin dashboards. No admin is ever banned, so none appear here."""
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT b.username, b.banned_by, b.reason, b.created_at,
                   u.display_name AS display_name,
                   bu.display_name AS banned_by_name
            FROM bans b
            LEFT JOIN users u ON u.username = b.username
            LEFT JOIN users bu ON bu.username = b.banned_by
            ORDER BY b.created_at DESC
            """
        ).fetchall()
        return [dict(r) for r in rows]


# ---- Invites --------------------------------------------------------------
# Readable, single-use codes: three short words joined by dashes, e.g.
# "ember-quiet-harbor". The words are picked with the secrets module so a code is
# not guessable, and the small wordlist stays easy to read out or type in.

_INVITE_WORDS = (
    "amber", "anchor", "aspen", "basil", "beacon", "birch", "cedar", "cinder",
    "cobalt", "comet", "coral", "cove", "delta", "dune", "ember", "fable",
    "fern", "flint", "garnet", "grove", "harbor", "hazel", "indigo", "ivory",
    "jade", "juniper", "kelp", "lark", "lotus", "maple", "meadow", "onyx",
    "opal", "pebble", "pine", "quartz", "quiet", "raven", "reed", "river",
    "sage", "slate", "spruce", "thistle", "tide", "umber", "violet", "willow",
)


def _new_invite_code():
    return "-".join(secrets.choice(_INVITE_WORDS) for _ in range(3))


def create_invite(label, created_by, created_at):
    """Generate a fresh single-use invite code and store it. Retries on the rare
    chance the random words collide with an existing code. Returns the code."""
    with connect() as conn:
        for _ in range(20):
            code = _new_invite_code()
            try:
                conn.execute(
                    "INSERT INTO invites (code, label, created_by, created_at) "
                    "VALUES (?, ?, ?, ?)",
                    (code, label or "", created_by, created_at),
                )
                return code
            except sqlite3.IntegrityError:
                continue
        raise RuntimeError("could not generate a unique invite code")


def get_invite(code):
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM invites WHERE code = ?", (code,)
        ).fetchone()
        return dict(row) if row else None


def list_invites():
    """Every invite with its redeemer's display name (if redeemed), newest first.
    The route/UI derives active/revoked/redeemed status from the timestamps."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT i.code, i.label, i.created_by, i.created_at, i.revoked_at, "
            "i.redeemed_by, i.redeemed_at, u.display_name AS redeemed_by_name "
            "FROM invites i LEFT JOIN users u ON u.username = i.redeemed_by "
            "ORDER BY i.created_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def revoke_invite(code, when):
    """Revoke an invite so it can no longer be redeemed, keeping the row. Only an
    active code (not already redeemed, not already revoked) changes; returns True
    if one did."""
    with connect() as conn:
        cur = conn.execute(
            "UPDATE invites SET revoked_at = ? "
            "WHERE code = ? AND revoked_at IS NULL AND redeemed_at IS NULL",
            (when, code),
        )
        return cur.rowcount > 0


def register_via_invite(code, username, display_name, password, when):
    """Atomically claim a single-use invite and create a viewer account from it.
    The claim and the insert share one transaction, so a code can never mint two
    accounts, even under a race. Returns one of:

      'ok'          - account created and the invite marked redeemed
      'used'        - the code was missing, revoked, or already redeemed
      'user_exists' - that username is taken (the claim is rolled back)
    """
    with connect() as conn:
        # Claim the code first. This guarded UPDATE is the single point where
        # single-use is enforced: only one caller's statement can match a code
        # that is still active, so a second redeemer matches zero rows.
        cur = conn.execute(
            "UPDATE invites SET redeemed_by = ?, redeemed_at = ? "
            "WHERE code = ? AND redeemed_at IS NULL AND revoked_at IS NULL",
            (username, when, code),
        )
        if cur.rowcount == 0:
            return "used"
        taken = conn.execute(
            "SELECT 1 FROM users WHERE username = ?", (username,)
        ).fetchone()
        if taken:
            # Undo the claim so a taken username does not burn the code.
            conn.execute(
                "UPDATE invites SET redeemed_by = NULL, redeemed_at = NULL "
                "WHERE code = ?",
                (code,),
            )
            return "user_exists"
        conn.execute(
            "INSERT INTO users "
            "(username, display_name, password_hash, is_admin, created_at, invite_code) "
            "VALUES (?, ?, ?, 0, ?, ?)",
            (username, display_name, hash_password(password), when, code),
        )
        return "ok"


# ---- Channel points -------------------------------------------------------
# Viewers earn points by watching the stream live and spend them to highlight a
# short message on stream. Balances live on the users row; there is no ledger or
# redemption history in this version, only the current balance.

def get_points(username):
    """A user's current points balance, or 0 if the account does not exist."""
    with connect() as conn:
        row = conn.execute(
            "SELECT points FROM users WHERE username = ?", (username,)
        ).fetchone()
        return row["points"] if row else 0


def credit_points(usernames, amount):
    """Add `amount` points to each of the given usernames in one statement. The
    list is de-duplicated first, so a viewer with several tabs open is credited
    only once per round. Usernames without an account are silently ignored.
    Returns the number of accounts updated."""
    names = list(set(usernames))
    if not names or amount == 0:
        return 0
    placeholders = ",".join("?" for _ in names)
    with connect() as conn:
        cur = conn.execute(
            f"UPDATE users SET points = points + ? WHERE username IN ({placeholders})",
            (amount, *names),
        )
        return cur.rowcount


def spend_points(username, cost):
    """Atomically deduct `cost` points from a user, but only if they can afford
    it. Returns the new balance on success, or None if the balance was too low
    (no row changed). This one guarded UPDATE is the sole point that enforces the
    balance, so two redemptions racing on a balance that covers only one can
    never both succeed."""
    with connect() as conn:
        cur = conn.execute(
            "UPDATE users SET points = points - ? WHERE username = ? AND points >= ?",
            (cost, username, cost),
        )
        if cur.rowcount == 0:
            return None
        row = conn.execute(
            "SELECT points FROM users WHERE username = ?", (username,)
        ).fetchone()
        return row["points"] if row else None


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
    """Record a chat line and return its row id, which doubles as the message id
    the client uses so a moderator can later delete a specific message."""
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO chat_log (username, display_name, text, ts) "
            "VALUES (?, ?, ?, ?)",
            (username, display_name, text, ts),
        )
        return cur.lastrowid


def mark_chat_deleted(msg_id, by):
    """Flag a logged message as removed by a moderator. The row stays in the log
    so the admin can still see it, labeled as deleted."""
    with connect() as conn:
        conn.execute(
            "UPDATE chat_log SET deleted_by = ? WHERE id = ?", (by, msg_id)
        )


def mark_all_chat_deleted(username, by):
    """Flag every not-yet-deleted logged message from a user as removed, for a
    moderator's /purge. Rows stay in the log, labeled as deleted. Returns the
    number of rows affected."""
    with connect() as conn:
        cur = conn.execute(
            "UPDATE chat_log SET deleted_by = ? "
            "WHERE username = ? AND deleted_by IS NULL",
            (by, username),
        )
        return cur.rowcount


def purge_old_chat(cutoff):
    """Delete chat messages older than the cutoff epoch. Returns rows removed."""
    with connect() as conn:
        cur = conn.execute("DELETE FROM chat_log WHERE ts < ?", (cutoff,))
        return cur.rowcount


def recent_chat(limit=200, exclude_admins=False):
    """Recent chat for the dashboards. The mod dashboard passes
    exclude_admins=True so messages from admin accounts are hidden there."""
    where = ""
    if exclude_admins:
        where = (
            "WHERE username NOT IN (SELECT username FROM users WHERE is_admin = 1) "
        )
    with connect() as conn:
        rows = conn.execute(
            "SELECT username, display_name, text, ts, deleted_by FROM chat_log "
            f"{where}ORDER BY ts DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


# ---- VODs, clips, chat replay, and views ----------------------------------

def _media_table(kind):
    # Guard the kind so it can never be used to build an unexpected table name.
    if kind not in ("vod", "clip"):
        raise ValueError("kind must be 'vod' or 'clip'")
    return "vods" if kind == "vod" else "clips"


def create_vod(title, description, started_at):
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO vods (title, description, started_at, ready) "
            "VALUES (?, ?, ?, 0)",
            (title, description, started_at),
        )
        return cur.lastrowid


def finalize_vod(vod_id, ended_at, duration, filename):
    with connect() as conn:
        conn.execute(
            "UPDATE vods SET ended_at = ?, duration = ?, filename = ?, ready = 1 "
            "WHERE id = ?",
            (ended_at, duration, filename, vod_id),
        )


def list_vods():
    """Finished VODs for the landing page, most recent first."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, title, description, filename, started_at, duration, views "
            "FROM vods WHERE ready = 1 ORDER BY started_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def get_vod(vod_id):
    with connect() as conn:
        row = conn.execute("SELECT * FROM vods WHERE id = ?", (vod_id,)).fetchone()
        return dict(row) if row else None


def create_clip(name, filename, creator, vod_id, start_ts, end_ts, duration, created_at):
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO clips "
            "(name, filename, creator, vod_id, start_ts, end_ts, duration, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (name, filename, creator, vod_id, start_ts, end_ts, duration, created_at),
        )
        return cur.lastrowid


def set_clip_filename(clip_id, filename):
    with connect() as conn:
        conn.execute(
            "UPDATE clips SET filename = ? WHERE id = ?", (filename, clip_id)
        )


def clear_unfinished_vods():
    """Remove VOD rows whose recording never finished (ready = 0), e.g. if the
    gate restarted mid broadcast. Called at startup so no half rows linger."""
    with connect() as conn:
        rows = conn.execute("SELECT id FROM vods WHERE ready = 0").fetchall()
        for row in rows:
            conn.execute(
                "DELETE FROM replay_chat WHERE kind = 'vod' AND ref_id = ?", (row["id"],)
            )
            conn.execute(
                "DELETE FROM media_views WHERE kind = 'vod' AND ref_id = ?", (row["id"],)
            )
        conn.execute("DELETE FROM vods WHERE ready = 0")
        return [r["id"] for r in rows]


def list_clips():
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, name, filename, creator, start_ts, duration, views, created_at "
            "FROM clips ORDER BY created_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def get_clip(clip_id):
    with connect() as conn:
        row = conn.execute("SELECT * FROM clips WHERE id = ?", (clip_id,)).fetchone()
        return dict(row) if row else None


def delete_media(kind, ref_id):
    """Delete a VOD or clip row and everything tied to it, returning the deleted
    row (so the caller can remove the files on disk). None if it did not exist."""
    table = _media_table(kind)
    with connect() as conn:
        row = conn.execute(
            f"SELECT * FROM {table} WHERE id = ?", (ref_id,)
        ).fetchone()
        if not row:
            return None
        conn.execute(
            "DELETE FROM replay_chat WHERE kind = ? AND ref_id = ?", (kind, ref_id)
        )
        conn.execute(
            "DELETE FROM media_views WHERE kind = ? AND ref_id = ?", (kind, ref_id)
        )
        conn.execute(f"DELETE FROM {table} WHERE id = ?", (ref_id,))
        return dict(row)


def add_view(kind, ref_id, username):
    """Record a view and return the item's view count. The (item, viewer) row is
    unique, so a refresh or a seek never inflates the count."""
    table = _media_table(kind)
    with connect() as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO media_views (kind, ref_id, username, ts) "
            "VALUES (?, ?, ?, ?)",
            (kind, ref_id, username, int(time.time())),
        )
        if cur.rowcount:
            conn.execute(
                f"UPDATE {table} SET views = views + 1 WHERE id = ?", (ref_id,)
            )
        row = conn.execute(
            f"SELECT views FROM {table} WHERE id = ?", (ref_id,)
        ).fetchone()
        return row["views"] if row else 0


def snapshot_chat(kind, ref_id, start_ts, end_ts):
    """Copy the chat said during a media window into replay_chat, each line tagged
    with its offset in seconds from the start, so playback can replay it in sync.
    Avatars, fonts, and role flags are taken from the author's account as it
    stands now (a good enough likeness for replay)."""
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO replay_chat
                (kind, ref_id, username, display_name, avatar_version, font,
                 admin, moderator, text, offset_s, deleted)
            SELECT ?, ?, c.username, c.display_name,
                   COALESCE(u.avatar_version, 0), COALESCE(u.chat_font, 'system'),
                   COALESCE(u.is_admin, 0), COALESCE(u.is_moderator, 0),
                   c.text, MAX(0, c.ts - ?),
                   CASE WHEN c.deleted_by IS NOT NULL THEN 1 ELSE 0 END
            FROM chat_log c LEFT JOIN users u ON u.username = c.username
            WHERE c.ts >= ? AND c.ts <= ?
            """,
            (kind, ref_id, start_ts, start_ts, end_ts),
        )


def get_replay(kind, ref_id):
    with connect() as conn:
        rows = conn.execute(
            "SELECT username, display_name, avatar_version, font, admin, moderator, "
            "text, offset_s, deleted FROM replay_chat "
            "WHERE kind = ? AND ref_id = ? ORDER BY offset_s, id",
            (kind, ref_id),
        ).fetchall()
        return [dict(r) for r in rows]


def prune_vods(keep_count, keep_days, now):
    """Drop VODs past the retention limits, newest kept. Returns the deleted rows
    so the caller can remove the files (and posters) from disk."""
    with connect() as conn:
        ready = conn.execute(
            "SELECT id, filename, started_at FROM vods WHERE ready = 1 "
            "ORDER BY started_at DESC"
        ).fetchall()
        doomed = []
        for idx, row in enumerate(ready):
            too_many = keep_count > 0 and idx >= keep_count
            too_old = keep_days > 0 and row["started_at"] < now - keep_days * 86400
            if too_many or too_old:
                doomed.append(dict(row))
        for d in doomed:
            conn.execute(
                "DELETE FROM replay_chat WHERE kind = 'vod' AND ref_id = ?", (d["id"],)
            )
            conn.execute(
                "DELETE FROM media_views WHERE kind = 'vod' AND ref_id = ?", (d["id"],)
            )
            conn.execute("DELETE FROM vods WHERE id = ?", (d["id"],))
        return doomed


# ---- Admin views ----------------------------------------------------------

def admin_list_users(include_admins=True):
    """Every account with rolled-up activity stats, for the dashboards. The mod
    dashboard passes include_admins=False so admin accounts never appear there."""
    where = "" if include_admins else "WHERE u.is_admin = 0"
    with connect() as conn:
        rows = conn.execute(
            f"""
            SELECT
                u.username,
                u.display_name,
                u.is_admin,
                u.is_moderator,
                u.created_at,
                u.avatar_version,
                u.email,
                u.notify_live,
                (SELECT MAX(COALESCE(left_at, joined_at))
                   FROM watch_sessions w WHERE w.username = u.username) AS last_seen,
                (SELECT COUNT(*)
                   FROM watch_sessions w WHERE w.username = u.username) AS sessions,
                (SELECT COALESCE(SUM(COALESCE(left_at, joined_at) - joined_at), 0)
                   FROM watch_sessions w WHERE w.username = u.username) AS watch_seconds,
                (SELECT COUNT(*)
                   FROM chat_log c WHERE c.username = u.username) AS messages
            FROM users u
            {where}
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
            "SELECT text, ts, deleted_by FROM chat_log WHERE username = ? "
            "ORDER BY ts DESC LIMIT ?",
            (username, chat_limit),
        ).fetchall()
        return {
            "watch_sessions": [dict(r) for r in watches],
            "chat": [dict(r) for r in chats],
        }
