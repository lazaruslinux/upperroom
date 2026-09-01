"""
Account storage for upperroom.

Accounts live in a small SQLite file on a docker volume. There are no public
sign ups. The admin creates every account with manage.py. Passwords are never
stored directly, only a scrypt hash with a per account salt.
"""

import datetime
import hashlib
import hmac
import os
import secrets
import sqlite3
import time
from contextlib import contextmanager

import wordfilter
# Only for stamping a new account with the release it was made on, so nobody
# is welcomed by a list of things that changed before they arrived.
from config import VERSION

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
    -- How much of the live edge a clip captures, in seconds. A dashboard
    -- setting rather than a constant, because its neighbours (the cooldowns
    -- above, retention below) already are and there was no reason for this one
    -- to be different.
    clip_seconds INTEGER NOT NULL DEFAULT 60,
    -- Go-live notifications. discord_webhook is an optional Discord incoming
    -- webhook URL; last_notified_at guards against re-announcing on a brief
    -- stream blip or a gate restart mid-broadcast. email_on_live is the
    -- channel's master switch for the go-live email: viewers each have their own
    -- opt-in, this decides whether the channel sends any at all. It does not
    -- affect Discord.
    discord_webhook TEXT NOT NULL DEFAULT '',
    last_notified_at INTEGER NOT NULL DEFAULT 0,
    email_on_live INTEGER NOT NULL DEFAULT 1,
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
    -- A new install starts at 2 seconds, which is slow enough to take the edge
    -- off a flood and short enough that nobody having a conversation notices.
    -- The migration below deliberately keeps 0, so an existing channel never
    -- has a delay appear under it: raising it there is the operator's choice.
    slow_mode_seconds INTEGER NOT NULL DEFAULT 2,
    banned_words TEXT NOT NULL DEFAULT '',
    -- Retention limits for the media store. 0 means "no limit on this axis".
    -- An item that is pinned (vods.keep / clips.keep) is exempt from all of them.
    --
    -- clip_keep_days is the one deliberate exception to "a fresh install never
    -- deletes anything on its own". Clips are the shareable unit, and once a
    -- thing can be handed to someone outside the channel, a short life stops
    -- being a limitation and starts being a safety property: a mistake expires
    -- instead of standing forever. Two days is long enough to watch something
    -- and short enough to bound the mistake. Pin a clip to keep it.
    vod_keep_count INTEGER NOT NULL DEFAULT 0,
    vod_keep_days INTEGER NOT NULL DEFAULT 0,
    clip_keep_count INTEGER NOT NULL DEFAULT 0,
    clip_keep_days INTEGER NOT NULL DEFAULT 2,
    -- A ceiling on the whole media store in gigabytes. Enforced oldest first
    -- across both kinds once the per-kind limits above have had their say.
    media_cap_gb INTEGER NOT NULL DEFAULT 0,
    -- When the channel last stopped being on air, whether that was a broadcast
    -- going offline or a theater session closing. What decides whether the next
    -- broadcast is the same night carrying on or a new one starting.
    last_air_ended_at INTEGER NOT NULL DEFAULT 0
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
    ready INTEGER NOT NULL DEFAULT 0,
    -- Pinned by the operator: retention never removes this recording, and it
    -- does not use up a slot in the count limit either. Deleting it by hand
    -- still works; an explicit action always beats the pin.
    keep INTEGER NOT NULL DEFAULT 0,
    -- Set when the recording finished but could not be archived, e.g. the media
    -- store is a network mount and it was unreachable. Holds the scratch path
    -- still waiting to be moved. A row with this set is NOT an unfinished
    -- recording and must survive the startup sweeps, or the file is lost.
    pending_path TEXT
);

-- A viewer made clip: a short cut of the recent live stream. How much is a
-- channel setting (channel_settings.clip_seconds).
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
    created_at INTEGER NOT NULL,
    -- Same pin as on vods: retention never removes a pinned clip.
    keep INTEGER NOT NULL DEFAULT 0
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
    deleted INTEGER NOT NULL DEFAULT 0,
    -- The author's chat colors as they stood when the snapshot was taken, frozen
    -- alongside their name and avatar so the replay looks like the live chat did.
    -- Each is a validated "#rrggbb" or empty for the theme default.
    name_color TEXT NOT NULL DEFAULT '',
    msg_color TEXT NOT NULL DEFAULT ''
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
-- A like on a recording or a clip. Exactly the shape of media_views, and for
-- the same reason: the primary key makes one person count once, so liking twice
-- is not two likes and unliking is a plain delete. Accounts only; a guest has
-- nothing to leave behind.
CREATE TABLE IF NOT EXISTS media_likes (
    kind TEXT NOT NULL,
    ref_id INTEGER NOT NULL,
    username TEXT NOT NULL,
    ts INTEGER NOT NULL,
    PRIMARY KEY (kind, ref_id, username)
);

-- A comment on a recording or a clip. Deliberately NOT the chat replay: the
-- replay is what was said live, frozen, and it stays exactly as it is. This is
-- what people say afterwards, and it sits beside the replay rather than mixed
-- into it.
--
-- deleted_by mirrors chat_log: a removed comment keeps its row and gains the
-- name of whoever removed it, so moderation reads the same way in both places
-- and a deletion can be accounted for.
CREATE TABLE IF NOT EXISTS media_comments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    ref_id INTEGER NOT NULL,
    username TEXT NOT NULL,
    text TEXT NOT NULL,
    ts INTEGER NOT NULL,
    deleted_by TEXT
);
CREATE INDEX IF NOT EXISTS idx_media_comments_ref
    ON media_comments(kind, ref_id, ts);

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

-- Guest passes: single-use codes that let someone watch and chat for a while
-- without making an account. Deliberately the same shape as invites, because
-- they are the same idea with a different outcome: an invite creates a
-- permanent account, a guest pass creates a temporary one. The account a pass
-- creates is recorded in redeemed_by exactly as it is for invites, so a pass
-- can be traced to whoever used it even after the account is reaped.
CREATE TABLE IF NOT EXISTS guest_passes (
    code TEXT PRIMARY KEY,
    label TEXT DEFAULT '',
    created_by TEXT,
    created_at INTEGER,
    revoked_at INTEGER,
    redeemed_by TEXT,
    redeemed_at INTEGER
);

-- A theater session: the operator is playing titles from their own library to
-- the room rather than broadcasting themselves. While one is open the gate does
-- not record, refuses clips, holds the chat wipe, and announces going live once
-- at the start instead of once per title.
--
-- state is 'intermission' (between titles), 'playing' (a title is on air) or
-- 'ended'. Anything that is not 'ended' is the active session, and there is at
-- most one of those (create_theater_session enforces it in SQL). The now_*
-- columns hold whatever is on air, cleared when it stops.
CREATE TABLE IF NOT EXISTS theater_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    state TEXT NOT NULL,
    started_at INTEGER,
    ended_at INTEGER,
    notified INTEGER NOT NULL DEFAULT 0,
    now_jf_id TEXT,
    now_title TEXT,
    now_year INTEGER,
    now_runtime INTEGER,
    now_synopsis TEXT,
    now_art TEXT,
    -- An episode is not identified by its own name: "Freedom Day" says nothing
    -- without the show it belongs to and its place in the run. A film leaves
    -- all three null. now_year holds the SHOW's year for an episode, because
    -- that is the year anybody knows the show by.
    now_series TEXT,
    now_season INTEGER,
    now_episode INTEGER
);

-- What the streamer has said they are playing, kept so the dashboard can offer
-- it again instead of making them retype it. Only the label is stored, capped
-- to a short list by set_now_playing; the current one lives on
-- channel_settings.now_playing_game.
CREATE TABLE IF NOT EXISTS recent_games (
    name TEXT PRIMARY KEY,
    last_used INTEGER NOT NULL
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
        # A guest is a real account with a short life. Keeping it a row rather
        # than a rowless session is what lets presence, watch sessions, bans and
        # every moderator command keep working on guests unchanged; all of those
        # resolve their target through a users row. is_guest is read fresh from
        # the row wherever it matters, never trusted from the session cookie.
        _ensure_column(conn, "users", "is_guest", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "users", "guest_expires_at", "INTEGER NOT NULL DEFAULT 0")
        # The release whose "what changed" notice this person has already seen.
        # Empty on an existing account, which is what makes the notice appear
        # once after an upgrade; a new account is stamped with the running
        # version at sign-up, so nobody is welcomed with a changelog.
        _ensure_column(conn, "users", "last_seen_version", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "chat_log", "deleted_by", "TEXT")
        # Older databases snapshotted replay chat before per-viewer colors rode
        # along, so their replay rows have no color to carry. Add the columns in
        # place; old snapshots simply read as the theme default, which is exactly
        # how they already looked.
        _ensure_column(conn, "replay_chat", "name_color", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "replay_chat", "msg_color", "TEXT NOT NULL DEFAULT ''")
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
        # Where an unarchived recording is parked. Null on every existing row,
        # which is right: anything already archived has nothing pending.
        _ensure_column(conn, "vods", "pending_path", "TEXT")
        _ensure_column(conn, "channel_settings", "overlay_key", "TEXT")
        _ensure_column(conn, "channel_settings", "stream_key", "TEXT")
        # The projector's bearer key. Nullable until an operator generates one on
        # the dashboard, and deliberately never seeded from the environment: with
        # no key, the projector socket cannot authenticate at all, which is the
        # right default for a machine that reaches out to this server.
        _ensure_column(conn, "channel_settings", "projector_key", "TEXT")
        # Theater grew from films to shows. A session that predates this has no
        # episode on air, so null is exactly right for every existing row.
        _ensure_column(conn, "theater_sessions", "now_series", "TEXT")
        _ensure_column(conn, "theater_sessions", "now_season", "INTEGER")
        _ensure_column(conn, "theater_sessions", "now_episode", "INTEGER")
        # 1, matching a new install: before this switch existed the channel sent
        # go-live email whenever SMTP was configured, so defaulting it on is what
        # keeps an existing channel behaving exactly as it did yesterday.
        _ensure_column(
            conn, "channel_settings", "email_on_live", "INTEGER NOT NULL DEFAULT 1"
        )
        # 0, not the 2 a new install gets: this branch runs on a channel that
        # already exists, and its chat should carry on behaving exactly as it
        # did yesterday until the operator says otherwise.
        _ensure_column(
            conn, "channel_settings", "slow_mode_seconds", "INTEGER NOT NULL DEFAULT 0"
        )
        _ensure_column(
            conn, "channel_settings", "banned_words", "TEXT NOT NULL DEFAULT ''"
        )
        # Retention moved out of the environment and into the dashboard. The
        # first of these tells us whether this database predates that move.
        upgraded = _ensure_column(
            conn, "channel_settings", "vod_keep_count", "INTEGER NOT NULL DEFAULT 0"
        )
        _ensure_column(
            conn, "channel_settings", "vod_keep_days", "INTEGER NOT NULL DEFAULT 0"
        )
        _ensure_column(
            conn, "channel_settings", "clip_keep_count", "INTEGER NOT NULL DEFAULT 0"
        )
        _ensure_column(
            conn, "channel_settings", "clip_keep_days", "INTEGER NOT NULL DEFAULT 0"
        )
        _ensure_column(
            conn, "channel_settings", "media_cap_gb", "INTEGER NOT NULL DEFAULT 0"
        )
        _ensure_column(conn, "vods", "keep", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "clips", "keep", "INTEGER NOT NULL DEFAULT 0")
        # A clip is private until an admin publishes it. The token is the whole
        # of the public URL, so it has to be unguessable, and it is null rather
        # than blank when unpublished so the uniqueness index below does not
        # treat every private clip as a duplicate of every other one.
        _ensure_column(conn, "clips", "share_token", "TEXT")
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_clips_share_token "
            "ON clips(share_token) WHERE share_token IS NOT NULL"
        )
        # Clip length moved out of config.py and onto the channel. An existing
        # channel picks up 60 here, which is the change this shipped for.
        _ensure_column(
            conn, "channel_settings", "clip_seconds", "INTEGER NOT NULL DEFAULT 60"
        )
        # NOTE: clip_keep_days now defaults to 2 in the schema above, but that
        # only reaches a FRESH install. An existing channel keeps whatever it
        # has, deliberately, and the same way slow_mode_seconds does: an update
        # must never start deleting somebody's clips out from under them. An
        # operator who wants the two day life turns it on in the dashboard.
        _ensure_column(
            conn, "channel_settings", "last_air_ended_at", "INTEGER NOT NULL DEFAULT 0"
        )
        _ensure_column(
            conn, "channel_settings", "now_playing_game", "TEXT NOT NULL DEFAULT ''"
        )
        # How many people may pull the video at once, 0 for no limit. Every
        # viewer costs the server a full copy of the broadcast, so this is the
        # one setting that bounds the bandwidth bill. Off by default: an
        # existing channel should not start refusing people on an upgrade.
        _ensure_column(
            conn, "channel_settings", "max_viewers", "INTEGER NOT NULL DEFAULT 0"
        )
        # Whether a theater play burns in subtitles when the host does not say.
        # Off on purpose, on an existing channel as well as a fresh one: the burn
        # is only as good as the library's subtitle timing, and a film that is
        # seconds out is seconds out for the whole room.
        _ensure_column(
            conn, "channel_settings", "theater_subtitles", "INTEGER NOT NULL DEFAULT 0"
        )
        # The admin-defined rewards catalog was replaced by a single built-in
        # redemption (highlight a message), so its table is dropped in place, the
        # same lightweight in-init migration as the column adds above.
        conn.execute("DROP TABLE IF EXISTS rewards")
        # Settings whose features were removed outright, so their columns go the
        # same way: the announced "next stream", and the overlay ticker that went
        # when the overlay was cut back to chat alone. Guarded because DROP COLUMN
        # wants SQLite 3.35, and an older library should leave a few unused
        # columns behind rather than refuse to open the database at all.
        for gone in ("next_stream_at", "next_stream_note", "next_reminded_for",
                     "overlay_ticker"):
            try:
                conn.execute(f"ALTER TABLE channel_settings DROP COLUMN {gone}")
            except sqlite3.OperationalError:
                pass
        # Ensure the single channel_settings row exists so getters always find it.
        # A FRESH install starts with the default banned-words list; OR IGNORE is
        # what keeps that from reaching an existing channel, which owns its own
        # list and may have emptied it on purpose. Same reasoning as
        # slow_mode_seconds and clip_keep_days above.
        conn.execute(
            "INSERT OR IGNORE INTO channel_settings (id, stream_title, banned_words) "
            "VALUES (1, 'Live Stream', ?)",
            (wordfilter.DEFAULT_BANNED_WORDS,),
        )
        # This database predates dashboard-managed retention, so until now it has
        # been pruning by the old environment variables. Carry those values over
        # once, so an update never silently changes how many recordings the
        # operator keeps. From here the dashboard owns the limits and the
        # environment is ignored. Read the environment directly for the same
        # reason as PUBLISH_PASS below: db is imported without config.
        if upgraded:
            conn.execute(
                "UPDATE channel_settings SET vod_keep_count = ?, vod_keep_days = ? "
                "WHERE id = 1",
                (_env_int("SELFSTREAM_VOD_KEEP", 20), _env_int("SELFSTREAM_VOD_KEEP_DAYS", 0)),
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


def backup_to(path):
    """Write a consistent, compacted copy of the database to `path`.

    VACUUM INTO rather than copying the file: the gate runs in WAL mode, so a
    plain copy can catch a page mid-write or miss the write-ahead log entirely,
    and the result would restore as a corrupt or stale database. This is safe to
    run while the service is live and writing. SQLite refuses to overwrite an
    existing destination, which is the check we want anyway."""
    conn = sqlite3.connect(DB_PATH, isolation_level=None)
    try:
        conn.execute("VACUUM INTO ?", (str(path),))
    finally:
        conn.close()


def table_names():
    """Every table in the database, for the backup manifest and for checking a
    restored file is not from something else entirely."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        return [row["name"] for row in rows]


def _env_int(name, fallback):
    """An integer from the environment, falling back on anything unusable. Only
    the one-time retention seeding needs this; everything else reads config."""
    try:
        return int(os.environ.get(name, "").strip() or fallback)
    except ValueError:
        return fallback


def _ensure_column(conn, table, column, decl):
    """Add a column if the table does not have it yet. Returns True when the
    column was actually added, which is the one moment a caller can tell an
    upgrade from a fresh install and run a one-time backfill."""
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
        return True
    return False


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
            "(username, display_name, password_hash, is_admin, created_at, "
            "email, last_seen_version) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (username, display_name, hash_password(password),
             1 if is_admin else 0, int(time.time()), email or "", VERSION),
        )


def mark_version_seen(username, version):
    """Remember that this person has read the notice for this release."""
    with connect() as conn:
        conn.execute(
            "UPDATE users SET last_seen_version = ? WHERE username = ?",
            (version, username),
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
            "(username, display_name, password_hash, is_admin, created_at, "
            " last_seen_version) "
            "SELECT ?, ?, ?, 1, ?, ? WHERE NOT EXISTS (SELECT 1 FROM users)",
            (username, display_name, hash_password(password), when, VERSION),
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
    """The channel settings the streamer controls: the site name, the title and
    description shown on the home card (and stamped onto each VOD), and the
    accent. Clip lengths and cooldowns are no longer among them; both are fixed
    in config, so the older columns here are left alone rather than read."""
    with connect() as conn:
        row = conn.execute(
            "SELECT site_name, stream_title, stream_description, accent "
            "FROM channel_settings WHERE id = 1"
        ).fetchone()
        if not row:
            return {
                "site_name": "upperroom",
                "stream_title": "Live Stream",
                "stream_description": "",
                "accent": "green",
            }
        return dict(row)


def set_stream_info(site_name=None, title=None, description=None, accent=None):
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


def get_max_viewers():
    """How many people may pull the video at once, 0 for no limit. Kept out of
    get_stream_info for the same reason now_playing_game is: that one feeds the
    public status payload, and how full the room is allowed to get is the
    operator's business, not a visitor's."""
    with connect() as conn:
        row = conn.execute(
            "SELECT max_viewers FROM channel_settings WHERE id = 1"
        ).fetchone()
        return int(row["max_viewers"]) if row else 0


def set_max_viewers(count):
    """Set the viewer limit. 0 clears it, which is a real value here rather than
    an unset one, so this cannot ride set_stream_info's None-means-unset keys."""
    with connect() as conn:
        conn.execute(
            "UPDATE channel_settings SET max_viewers = ? WHERE id = 1",
            (max(0, int(count)),),
        )


def get_theater_subtitles():
    """Whether a theater play burns in subtitles when the request does not say.
    The per-play box overrides it for one showing; this is what it starts from."""
    with connect() as conn:
        row = conn.execute(
            "SELECT theater_subtitles FROM channel_settings WHERE id = 1"
        ).fetchone()
        return bool(row["theater_subtitles"]) if row else False


def set_theater_subtitles(on):
    """Set the subtitle default. False is a real value here rather than an unset
    one, so this cannot ride set_stream_info's None-means-unset keys either."""
    with connect() as conn:
        conn.execute(
            "UPDATE channel_settings SET theater_subtitles = ? WHERE id = 1",
            (1 if on else 0,),
        )


# How many past games the dashboard offers back. Long enough to cover a rotation,
# short enough that the list stays pickable.
RECENT_GAMES_KEPT = 12


def get_now_playing():
    """What the streamer has said they are playing, or "" for nothing. Kept out
    of get_stream_info because that one feeds the public status payload and this
    is read on its own, by the link preview and the dashboard."""
    with connect() as conn:
        row = conn.execute(
            "SELECT now_playing_game FROM channel_settings WHERE id = 1"
        ).fetchone()
        return (row["now_playing_game"] if row else "") or ""


def set_now_playing(name):
    """Set (or clear, with "") what is being played. A real name is also
    remembered, so the dashboard can offer it back instead of asking the
    streamer to type a title they have already typed."""
    name = (name or "").strip()
    with connect() as conn:
        conn.execute(
            "UPDATE channel_settings SET now_playing_game = ? WHERE id = 1", (name,)
        )
        if not name:
            return
        # The stamp is a clock reading, but forced above every existing one:
        # two games set inside the same second would otherwise tie, and the tie
        # decides both which one reads as most recent and which one the trim
        # below throws away.
        conn.execute(
            "INSERT INTO recent_games (name, last_used) VALUES (?, "
            "MAX(?, (SELECT COALESCE(MAX(last_used), 0) + 1 FROM recent_games))) "
            "ON CONFLICT(name) DO UPDATE SET last_used = excluded.last_used",
            (name, int(time.time())),
        )
        # Trim the tail rather than let the list grow forever. Deleting by a
        # subquery of the ones to keep, so the newest RECENT_GAMES_KEPT survive
        # whatever order they were added in.
        conn.execute(
            "DELETE FROM recent_games WHERE name NOT IN ("
            "SELECT name FROM recent_games ORDER BY last_used DESC LIMIT ?)",
            (RECENT_GAMES_KEPT,),
        )


def recent_games(limit=RECENT_GAMES_KEPT):
    """The games most recently played, newest first."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT name FROM recent_games ORDER BY last_used DESC LIMIT ?", (limit,)
        ).fetchall()
        return [r["name"] for r in rows]


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
    """The channel's go-live notification settings: the Discord webhook URL, the
    epoch of the last announcement (used for the cooldown), and whether the
    channel sends go-live email at all."""
    with connect() as conn:
        row = conn.execute(
            "SELECT discord_webhook, last_notified_at, email_on_live "
            "FROM channel_settings WHERE id = 1"
        ).fetchone()
        if not row:
            return {"discord_webhook": "", "last_notified_at": 0, "email_on_live": 1}
        return dict(row)


def set_discord_webhook(url):
    with connect() as conn:
        conn.execute(
            "UPDATE channel_settings SET discord_webhook = ? WHERE id = 1",
            (url or "",),
        )


def set_email_on_live(on):
    """Turn the channel's go-live email on or off. Viewers keep their own
    per-account opt-in; this is the switch above all of them."""
    with connect() as conn:
        conn.execute(
            "UPDATE channel_settings SET email_on_live = ? WHERE id = 1",
            (1 if on else 0,),
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


# ---- Projector key --------------------------------------------------------
# The bearer key the projector service authenticates its socket with. Same shape
# as the overlay key, and for the same reason: the projector runs on the
# operator's own media machine and cannot sign in. It is never seeded from the
# environment, so a channel that has not generated one refuses every projector.

def get_projector_key():
    """The current projector key, or None if one has never been generated."""
    with connect() as conn:
        row = conn.execute(
            "SELECT projector_key FROM channel_settings WHERE id = 1"
        ).fetchone()
        return row["projector_key"] if row else None


def regenerate_projector_key():
    """Mint a fresh projector key, replacing any previous one, and return it.
    This revokes the old one: a projector connected with it stops matching."""
    key = secrets.token_urlsafe(32)
    with connect() as conn:
        conn.execute(
            "UPDATE channel_settings SET projector_key = ? WHERE id = 1", (key,)
        )
    return key


def seed_projector_key(key):
    """Set the projector key only while it is still unset. For the demo stack,
    which pairs its own projector container without a dashboard step. Returns
    True if it was written. Same rule as the PUBLISH_PASS seed: once a key
    exists, this never overwrites it."""
    if not key:
        return False
    with connect() as conn:
        cur = conn.execute(
            "UPDATE channel_settings SET projector_key = ? "
            "WHERE id = 1 AND projector_key IS NULL",
            (key,),
        )
        return cur.rowcount > 0


# ---- Theater sessions -----------------------------------------------------
# One session at a time, enforced in SQL rather than by reading first and then
# inserting: two admins pressing start at the same moment would both read "none
# active" and both insert. "Active" means state is not 'ended'.

def create_theater_session(started_at):
    """Open a theater session, unless one is already open. Returns its id, or
    None when there was already an active session (nothing was written)."""
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO theater_sessions (state, started_at) "
            "SELECT 'intermission', ? WHERE NOT EXISTS "
            "(SELECT 1 FROM theater_sessions WHERE state != 'ended')",
            (started_at,),
        )
        if cur.rowcount <= 0:
            return None
        return cur.lastrowid


def get_active_theater_session():
    """The open session, or None. Newest first, so a database that somehow holds
    two answers with the current one rather than an ancient row."""
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM theater_sessions WHERE state != 'ended' "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None


def set_theater_state(session_id, state):
    """Move an open session between 'intermission' and 'playing'. Ending is
    end_theater_session's job, and an already ended session never moves back."""
    with connect() as conn:
        conn.execute(
            "UPDATE theater_sessions SET state = ? WHERE id = ? AND state != 'ended'",
            (state, session_id),
        )


def set_theater_now(session_id, title=None, year=None, runtime=None,
                    synopsis=None, art=None, jf_id=None, series=None,
                    season=None, episode=None):
    """Record what is on air, or clear it when called with nothing (the title
    stopped). Written as one statement so a half-set title cannot be read."""
    with connect() as conn:
        conn.execute(
            "UPDATE theater_sessions SET now_jf_id = ?, now_title = ?, "
            "now_year = ?, now_runtime = ?, now_synopsis = ?, now_art = ?, "
            "now_series = ?, now_season = ?, now_episode = ? "
            "WHERE id = ?",
            (jf_id, title, year, runtime, synopsis, art, series, season,
             episode, session_id),
        )


def mark_theater_notified(session_id):
    """Stamp that this session's go-live announcement has gone out, so it fires
    once for the session rather than once per title."""
    with connect() as conn:
        conn.execute(
            "UPDATE theater_sessions SET notified = 1 WHERE id = ?", (session_id,)
        )


def end_theater_session(session_id, ended_at):
    """Close a session for good and clear whatever it was showing. Returns
    whether this call is the one that closed it.

    An already ended session is left alone rather than closed again, which is
    what lets two paths race to end the same night (the host pressing end while
    the projector reports its title finished) and only one of them narrate it."""
    with connect() as conn:
        cur = conn.execute(
            "UPDATE theater_sessions SET state = 'ended', ended_at = ?, "
            "now_jf_id = NULL, now_title = NULL, now_year = NULL, "
            "now_runtime = NULL, now_synopsis = NULL, now_art = NULL, "
            "now_series = NULL, now_season = NULL, now_episode = NULL "
            "WHERE id = ? AND state != 'ended'",
            (ended_at, session_id),
        )
        return cur.rowcount > 0


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
    they are never emailed that their own stream is live. Guests are excluded
    outright: they have no address and a thirty minute account has no business
    receiving mail about future broadcasts. Returns a list of
    (display_name, email)."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT display_name, email FROM users "
            "WHERE notify_live = 1 AND email != '' AND is_admin = 0 AND is_guest = 0"
        ).fetchall()
        return [(r["display_name"], r["email"]) for r in rows]


def delete_user(username):
    with connect() as conn:
        # Remove the account and everything tied to it, so a deleted user leaves
        # no orphaned watch history, chat, or ban behind.
        conn.execute("DELETE FROM watch_sessions WHERE username = ?", (username,))
        conn.execute("DELETE FROM chat_log WHERE username = ?", (username,))
        conn.execute("DELETE FROM bans WHERE username = ?", (username,))
        # Their likes and comments go with them, the same as their chat. A
        # comment naming a deleted account would be a ghost in the thread, and
        # the guest reaper runs this every few minutes.
        conn.execute("DELETE FROM media_likes WHERE username = ?", (username,))
        conn.execute("DELETE FROM media_comments WHERE username = ?", (username,))
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


# ---- Codes ----------------------------------------------------------------
# Readable, single-use codes: three short words joined by dashes, e.g.
# "ember-quiet-harbor". The words are picked with the secrets module, and the
# words themselves stay easy to read out loud, type on a phone, and tell apart.
#
# The size of this list is a security property, not a matter of taste. An invite
# code only reaches a private sign-up form, but a guest pass reaches a public
# redemption endpoint that anyone can post to, so the list has to be big enough
# that guessing is hopeless on its own. Three words from 48 is 110,592
# combinations, which a script would walk through in minutes. This list is sized
# so that three words clear 10 million, and redemption is rate limited per
# address on top of that.
#
# Keep them: lowercase a-z only, 3-8 letters, no plurals, no two words that
# sound alike when read down a phone.

_CODE_WORDS = (
    "acorn", "alder", "almond", "amber", "anchor", "anvil", "apple", "arbor",
    "arch", "arrow", "ash", "aspen", "astro", "atlas", "autumn", "azure",
    "badge", "balsa", "bamboo", "banjo", "barley", "basil", "basin", "bay",
    "beacon", "beetle", "bellow", "birch", "bison", "blossom", "bluff", "bolt",
    "boulder", "bramble", "branch", "brass", "brick", "bridge", "brook", "buckle",
    "bugle", "burrow", "cabin", "cactus", "camber", "candle", "canoe", "canyon",
    "cargo", "carrot", "cascade", "cedar", "chalk", "charm", "cherry", "chime",
    "cinder", "cirrus", "citron", "clay", "cliff", "clover", "cobalt", "comet",
    "compass", "copper", "coral", "cove", "cricket", "crocus", "crystal", "cypress",
    "dagger", "daisy", "dapple", "dawn", "delta", "denim", "dew", "domino",
    "dune", "dusk", "eagle", "echo", "elder", "elm", "ember", "emerald",
    "fable", "falcon", "fathom", "fennel", "fern", "fig", "finch", "flax",
    "flint", "flute", "forest", "fossil", "fox", "frost", "gable", "galley",
    "garnet", "gecko", "geode", "ginger", "glacier", "glade", "granite", "gravel",
    "grotto", "grove", "gully", "gypsum", "halo", "harbor", "harvest", "hawk",
    "hazel", "heather", "hedge", "helm", "hemlock", "heron", "hickory", "hollow",
    "honey", "hopper", "husk", "indigo", "inlet", "iris", "ironwood", "island",
    "ivory", "jade", "jasper", "jetty", "juniper", "kelp", "kestrel", "kettle",
    "keystone", "lagoon", "lantern", "lark", "laurel", "lavender", "ledge", "lemon",
    "lichen", "lilac", "linen", "lotus", "lumber", "lupine", "magnet", "mahogany",
    "mallow", "mango", "manor", "maple", "marble", "marsh", "meadow", "mesa",
    "mica", "millet", "mint", "mirror", "mist", "moss", "mulberry", "nectar",
    "nettle", "nimbus", "nutmeg", "oak", "oasis", "obsidian", "ochre", "olive",
    "onyx", "opal", "orchard", "orchid", "osprey", "otter", "oxbow", "paddle",
    "pampas", "pantry", "papyrus", "parcel", "pasture", "pebble", "pelican", "pennant",
    "pepper", "pewter", "pigment", "pine", "pinion", "piper", "plateau", "plum",
    "pollen", "pond", "poplar", "porch", "portage", "prairie", "puffin", "pumice",
    "quarry", "quartz", "quill", "quiet", "radish", "rafter", "rapids", "raven",
    "reed", "reef", "relay", "ribbon", "ridge", "rill", "rimrock", "river",
    "rosemary", "rowan", "rudder", "ripple", "russet", "rye", "saffron", "sage",
    "sandbar", "sapling", "sapphire", "satchel", "sedge", "sequoia", "shale", "shamrock",
    "shelter", "shingle", "sierra", "silo", "silver", "solstice", "skiff", "slate",
    "sleet", "sorrel", "spindle", "spire", "spruce", "starling", "steppe", "sumac",
    "summit", "sundial", "sycamore", "tamarind", "tandem", "tanager", "teak", "tempo",
    "thicket", "thistle", "thorn", "thyme", "tide", "timber", "topaz", "torrent",
    "trellis", "trillium", "trout", "tulip", "tundra", "turret", "umber", "valley",
    "velvet", "verbena", "vessel", "vine", "violet", "vireo", "walnut", "wander",
    "warbler", "wattle", "wax", "wedge", "whistle", "wicker", "willow", "windmill",
    "wisteria", "wren", "yarrow", "yew", "yucca", "zephyr", "zinnia", "zircon",
)

# Three words drawn from the list above. Kept as a named number so a test can
# assert the space rather than trusting the list to stay large by accident.
CODE_WORD_COUNT = 3
CODE_SPACE = len(_CODE_WORDS) ** CODE_WORD_COUNT


def _new_code():
    return "-".join(secrets.choice(_CODE_WORDS) for _ in range(CODE_WORD_COUNT))


# ---- Invites --------------------------------------------------------------

def create_invite(label, created_by, created_at):
    """Generate a fresh single-use invite code and store it. Retries on the rare
    chance the random words collide with an existing code. Returns the code."""
    with connect() as conn:
        for _ in range(20):
            code = _new_code()
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


def delete_invite(code):
    """Remove a spent invite row for good.

    Invite rows are kept after use on purpose: redeemed_by is the record of
    which account a code created, and that is worth having. But the list only
    ever grows, and a code that is already spent has nothing left to say beyond
    that, so it can go when the operator says so.

    Only a revoked or redeemed code can be removed. An active one must be
    revoked first, so deleting can never become a quiet way to un-issue a code
    somebody is still holding, which is the property that made keeping the rows
    deliberate in the first place.

    The audit trail is not entirely lost either way: users.invite_code records
    which code created each account, on the account's own row, and that is
    untouched by this. Returns True if a row was removed."""
    with connect() as conn:
        cur = conn.execute(
            "DELETE FROM invites WHERE code = ? "
            "AND (revoked_at IS NOT NULL OR redeemed_at IS NOT NULL)",
            (code,),
        )
        return cur.rowcount > 0


def clear_used_invites():
    """Remove every redeemed or revoked invite at once. Returns how many went.
    This is the actual complaint: they pile up and there was no way to sweep
    them."""
    with connect() as conn:
        cur = conn.execute(
            "DELETE FROM invites "
            "WHERE revoked_at IS NOT NULL OR redeemed_at IS NOT NULL"
        )
        return cur.rowcount


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
            "(username, display_name, password_hash, is_admin, created_at, "
            " invite_code, last_seen_version) "
            "VALUES (?, ?, ?, 0, ?, ?, ?)",
            (username, display_name, hash_password(password), when, code, VERSION),
        )
        return "ok"


# ---- Guest passes ---------------------------------------------------------
# Same single-use, race-safe, revocable shape as invites, but redeeming one
# creates a temporary account instead of a permanent one.

# Stored in place of a password hash on a guest row. verify_password() splits on
# "$" and needs three parts, so this can never match any password: a guest
# cannot sign in through /api/auth at all, whatever they type.
GUEST_PASSWORD_SENTINEL = "guest-account-no-password"


def create_guest_pass(label, created_by, created_at):
    """Generate a fresh single-use guest pass and store it. Returns the code."""
    with connect() as conn:
        for _ in range(20):
            code = _new_code()
            try:
                conn.execute(
                    "INSERT INTO guest_passes (code, label, created_by, created_at) "
                    "VALUES (?, ?, ?, ?)",
                    (code, label or "", created_by, created_at),
                )
                return code
            except sqlite3.IntegrityError:
                continue
        raise RuntimeError("could not generate a unique guest pass code")


def get_guest_pass(code):
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM guest_passes WHERE code = ?", (code,)
        ).fetchone()
        return dict(row) if row else None


def list_guest_passes():
    """Every guest pass, newest first, with the display name of the guest it
    created. The name survives in guest_passes.redeemed_by after the reaper has
    deleted the account, so a spent pass still shows what it was used for."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT g.code, g.label, g.created_by, g.created_at, g.revoked_at, "
            "g.redeemed_by, g.redeemed_at, u.display_name AS redeemed_by_name, "
            "u.guest_expires_at "
            "FROM guest_passes g LEFT JOIN users u ON u.username = g.redeemed_by "
            "ORDER BY g.created_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def revoke_guest_pass(code, when):
    """Revoke an unused pass. Returns True if one changed. Revoking does not end
    a session already redeemed from it; that expires on its own."""
    with connect() as conn:
        cur = conn.execute(
            "UPDATE guest_passes SET revoked_at = ? "
            "WHERE code = ? AND revoked_at IS NULL AND redeemed_at IS NULL",
            (when, code),
        )
        return cur.rowcount > 0


def delete_guest_pass(code):
    """Remove a spent pass row for good. Only a pass that is already revoked or
    redeemed can go: an active code must be revoked first, so deleting can never
    become a quiet way to un-issue a code somebody is still holding. Returns
    True if a row was removed."""
    with connect() as conn:
        cur = conn.execute(
            "DELETE FROM guest_passes WHERE code = ? "
            "AND (revoked_at IS NOT NULL OR redeemed_at IS NOT NULL)",
            (code,),
        )
        return cur.rowcount > 0


def clear_used_guest_passes():
    """Remove every redeemed or revoked pass at once. Returns how many went."""
    with connect() as conn:
        cur = conn.execute(
            "DELETE FROM guest_passes "
            "WHERE revoked_at IS NOT NULL OR redeemed_at IS NOT NULL"
        )
        return cur.rowcount


def redeem_guest_pass(code, username, display_name, when, expires_at):
    """Atomically claim a single-use guest pass and create the guest account.

    The claim and the insert share one transaction, mirroring
    register_via_invite(), so two people racing on one code cannot both win.
    Returns 'ok', 'used' (missing, revoked or already redeemed), or
    'user_exists' (the generated username collided; the claim is rolled back so
    the pass is not burned).
    """
    with connect() as conn:
        cur = conn.execute(
            "UPDATE guest_passes SET redeemed_by = ?, redeemed_at = ? "
            "WHERE code = ? AND redeemed_at IS NULL AND revoked_at IS NULL",
            (username, when, code),
        )
        if cur.rowcount == 0:
            return "used"
        taken = conn.execute(
            "SELECT 1 FROM users WHERE username = ?", (username,)
        ).fetchone()
        if taken:
            conn.execute(
                "UPDATE guest_passes SET redeemed_by = NULL, redeemed_at = NULL "
                "WHERE code = ?",
                (code,),
            )
            return "user_exists"
        conn.execute(
            "INSERT INTO users "
            "(username, display_name, password_hash, is_admin, created_at, "
            " is_guest, guest_expires_at, notify_live, last_seen_version) "
            "VALUES (?, ?, ?, 0, ?, 1, ?, 0, ?)",
            (username, display_name, GUEST_PASSWORD_SENTINEL, when, expires_at,
             VERSION),
        )
        return "ok"


def expired_guests(now):
    """Usernames of guest accounts whose time is up. Separate from the delete so
    the reaper can log what it removed and so this is testable on its own."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT username FROM users "
            "WHERE is_guest = 1 AND guest_expires_at > 0 AND guest_expires_at <= ?",
            (now,),
        ).fetchall()
        return [r["username"] for r in rows]


def count_guests(now):
    """How many guest accounts are currently live, for the analytics page. Guests
    are filtered out of the account list, so without this they would be invisible
    rather than merely separate."""
    with connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM users "
            "WHERE is_guest = 1 AND guest_expires_at > ?", (now,)
        ).fetchone()
        return row["n"] if row else 0


# ---- Likes and comments ---------------------------------------------------
# Accounts only, and deliberately separate from the chat replay. The replay is
# what was said live and it stays frozen; this is what people say afterwards.

def set_like(kind, ref_id, username, liked, when):
    """Like or unlike. The primary key makes one person count once, so liking
    twice is not two likes. Returns the new total."""
    with connect() as conn:
        if liked:
            conn.execute(
                "INSERT OR IGNORE INTO media_likes (kind, ref_id, username, ts) "
                "VALUES (?, ?, ?, ?)",
                (kind, ref_id, username, when),
            )
        else:
            conn.execute(
                "DELETE FROM media_likes WHERE kind = ? AND ref_id = ? AND username = ?",
                (kind, ref_id, username),
            )
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM media_likes WHERE kind = ? AND ref_id = ?",
            (kind, ref_id),
        ).fetchone()
        return row["n"]


def like_state(kind, ref_id, username):
    """(total, whether this person liked it)."""
    with connect() as conn:
        total = conn.execute(
            "SELECT COUNT(*) AS n FROM media_likes WHERE kind = ? AND ref_id = ?",
            (kind, ref_id),
        ).fetchone()["n"]
        mine = conn.execute(
            "SELECT 1 FROM media_likes WHERE kind = ? AND ref_id = ? AND username = ?",
            (kind, ref_id, username),
        ).fetchone()
        return total, bool(mine)


def add_comment(kind, ref_id, username, text, when):
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO media_comments (kind, ref_id, username, text, ts) "
            "VALUES (?, ?, ?, ?, ?)",
            (kind, ref_id, username, text, when),
        )
        return cur.lastrowid


def list_comments(kind, ref_id):
    """Comments oldest first, with the author's current display name and avatar
    so the thread reflects a rename rather than freezing the name at the time.

    Deleted ones are included but carry no text: the thread should show that
    something was removed rather than silently closing the gap, which is how the
    chat replay behaves too."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT c.id, c.username, c.text, c.ts, c.deleted_by, "
            "u.display_name, u.avatar_version, u.is_admin, u.is_moderator, "
            "u.name_color "
            "FROM media_comments c LEFT JOIN users u ON u.username = c.username "
            "WHERE c.kind = ? AND c.ref_id = ? ORDER BY c.ts ASC, c.id ASC",
            (kind, ref_id),
        ).fetchall()
        out = []
        for r in rows:
            row = dict(r)
            if row["deleted_by"]:
                row["text"] = ""
            out.append(row)
        return out


def get_comment(comment_id):
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM media_comments WHERE id = ?", (comment_id,)
        ).fetchone()
        return dict(row) if row else None


def delete_comment(comment_id, by):
    """Soft delete, the same way chat_log does it: the row stays and gains the
    name of whoever removed it, so a deletion can be accounted for later."""
    with connect() as conn:
        cur = conn.execute(
            "UPDATE media_comments SET deleted_by = ? "
            "WHERE id = ? AND deleted_by IS NULL",
            (by, comment_id),
        )
        return cur.rowcount > 0


def comment_counts(kind, ref_ids):
    """How many live comments each item has, for the listings. One query rather
    than one per card."""
    if not ref_ids:
        return {}
    marks = ",".join("?" for _ in ref_ids)
    with connect() as conn:
        rows = conn.execute(
            f"SELECT ref_id, COUNT(*) AS n FROM media_comments "
            f"WHERE kind = ? AND deleted_by IS NULL AND ref_id IN ({marks}) "
            "GROUP BY ref_id",
            (kind, *ref_ids),
        ).fetchall()
        return {r["ref_id"]: r["n"] for r in rows}


def like_counts(kind, ref_ids):
    if not ref_ids:
        return {}
    marks = ",".join("?" for _ in ref_ids)
    with connect() as conn:
        rows = conn.execute(
            f"SELECT ref_id, COUNT(*) AS n FROM media_likes "
            f"WHERE kind = ? AND ref_id IN ({marks}) GROUP BY ref_id",
            (kind, *ref_ids),
        ).fetchall()
        return {r["ref_id"]: r["n"] for r in rows}


# ---- Public clip sharing --------------------------------------------------
# A published clip is reachable without an account. Everything else on the site
# sits behind the session check, so this is the one deliberate hole in it and
# the rules are kept narrow: admin only, one clip at a time, private by default,
# and revocable.

def publish_clip(clip_id, token):
    """Mark a clip public under `token`. Returns True if a clip changed.

    Only sets the token when there is not one already, so publishing twice
    cannot quietly rotate the link out from under somebody who already has it."""
    with connect() as conn:
        cur = conn.execute(
            "UPDATE clips SET share_token = ? WHERE id = ? AND share_token IS NULL",
            (token, clip_id),
        )
        return cur.rowcount > 0


def unpublish_clip(clip_id):
    """Take a clip back out of public reach. Returns the token it had, so the
    caller can remove the matching file, or None if it was not published."""
    with connect() as conn:
        row = conn.execute(
            "SELECT share_token FROM clips WHERE id = ?", (clip_id,)
        ).fetchone()
        if not row or not row["share_token"]:
            return None
        conn.execute(
            "UPDATE clips SET share_token = NULL WHERE id = ?", (clip_id,)
        )
        return row["share_token"]


def get_clip_by_token(token):
    """The clip behind a share link, or None. Used by the public page, so the
    caller must be careful which fields it passes on: the row carries the
    creator's username and this is the one place it must not travel."""
    if not token:
        return None
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM clips WHERE share_token = ?", (token,)
        ).fetchone()
        return dict(row) if row else None


def published_clip_tokens():
    """Every live share token. The sweep that removes orphaned public files
    needs the full set, so a token whose clip vanished leaves nothing behind."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT share_token FROM clips WHERE share_token IS NOT NULL"
        ).fetchall()
        return {r["share_token"] for r in rows}


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


def activity_by_day(days=30, now=None):
    """Daily activity buckets for the analytics charts, oldest first.

    One entry per day over the last `days` days, each carrying:
      date          - "YYYY-MM-DD" (UTC)
      watch_minutes - total watch time that day, summing every session's overlap
                      with the day. A session still open (left_at NULL) is capped
                      at `now`, so an in-progress watch counts up to the present.
      viewers       - distinct accounts with a session touching that day
      messages      - chat_log rows stamped that day. chat_log is purged on the
                      retention schedule, so days before the kept window read 0
                      honestly rather than being reported wrong.

    Buckets are UTC calendar days. `now` is injectable so the bucketing math can
    be tested without waiting on the clock."""
    days = max(1, min(365, int(days)))
    if now is None:
        now = int(time.time())
    # Midnight UTC of the current day, then one day boundary per bucket back.
    midnight = int(
        datetime.datetime.fromtimestamp(now, datetime.timezone.utc)
        .replace(hour=0, minute=0, second=0, microsecond=0)
        .timestamp()
    )
    day_starts = [midnight - i * 86400 for i in range(days - 1, -1, -1)]
    window_start = day_starts[0]
    window_end = midnight + 86400   # the end of today

    watch_seconds = [0] * days
    viewer_sets = [set() for _ in range(days)]
    messages = [0] * days

    with connect() as conn:
        # Every session that touches the window, split across the days it spans.
        sessions = conn.execute(
            "SELECT username, joined_at, left_at FROM watch_sessions "
            "WHERE joined_at < ? AND (left_at IS NULL OR left_at > ?)",
            (window_end, window_start),
        ).fetchall()
        for s in sessions:
            start = s["joined_at"]
            end = s["left_at"] if s["left_at"] is not None else now
            if end <= start:
                continue
            for i, day_start in enumerate(day_starts):
                lo = max(start, day_start)
                hi = min(end, day_start + 86400)
                if hi > lo:
                    watch_seconds[i] += hi - lo
                    viewer_sets[i].add(s["username"])
        # chat_log rows land in the bucket their timestamp falls in. The day
        # starts are consecutive, so the index is a plain division.
        for row in conn.execute(
            "SELECT ts FROM chat_log WHERE ts >= ? AND ts < ?",
            (window_start, window_end),
        ):
            i = int((row["ts"] - window_start) // 86400)
            if 0 <= i < days:
                messages[i] += 1

    return [
        {
            "date": datetime.datetime.fromtimestamp(
                day_start, datetime.timezone.utc
            ).strftime("%Y-%m-%d"),
            "watch_minutes": watch_seconds[i] // 60,
            "viewers": len(viewer_sets[i]),
            "messages": messages[i],
        }
        for i, day_start in enumerate(day_starts)
    ]


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
    """Mark a recording archived and playable. Clears pending_path in the same
    statement, so an archive that succeeded on a retry stops being pending."""
    with connect() as conn:
        conn.execute(
            "UPDATE vods SET ended_at = ?, duration = ?, filename = ?, ready = 1, "
            "pending_path = NULL WHERE id = ?",
            (ended_at, duration, filename, vod_id),
        )


def list_vods():
    """Finished VODs for the landing page, most recent first."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, title, description, filename, started_at, duration, views, keep "
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


def rename_clip(clip_id, name):
    with connect() as conn:
        conn.execute("UPDATE clips SET name = ? WHERE id = ?", (name, clip_id))


def set_clip_filename(clip_id, filename):
    with connect() as conn:
        conn.execute(
            "UPDATE clips SET filename = ? WHERE id = ?", (filename, clip_id)
        )


def _delete_media_children(conn, kind, ref_id):
    """Remove everything hanging off one recording or clip.

    There are three separate paths that delete media (the admin delete, the
    retention sweep, and the startup cleanup of unfinished recordings) and each
    used to carry its own copy of this list. Adding likes and comments meant
    touching all three, which is exactly how one gets missed and leaves orphaned
    rows nobody notices. One list, one place."""
    conn.execute("DELETE FROM replay_chat WHERE kind = ? AND ref_id = ?", (kind, ref_id))
    conn.execute("DELETE FROM media_views WHERE kind = ? AND ref_id = ?", (kind, ref_id))
    conn.execute("DELETE FROM media_likes WHERE kind = ? AND ref_id = ?", (kind, ref_id))
    conn.execute("DELETE FROM media_comments WHERE kind = ? AND ref_id = ?", (kind, ref_id))


def clear_unfinished_vods():
    """Remove VOD rows whose recording never finished (ready = 0), e.g. if the
    gate restarted mid broadcast. Called at startup so no half rows linger.

    A row with pending_path set is deliberately spared. That recording DID
    finish; only the archive move failed, and its scratch file is still on disk
    waiting for a retry. Deleting the row here would orphan the file, and
    cleanup_record_scratch would then delete the file too."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT id FROM vods WHERE ready = 0 AND pending_path IS NULL"
        ).fetchall()
        for row in rows:
            _delete_media_children(conn, "vod", row["id"])
        conn.execute("DELETE FROM vods WHERE ready = 0 AND pending_path IS NULL")
        return [r["id"] for r in rows]


def mark_vod_pending(vod_id, path, ended_at):
    """Park a finished-but-unarchived recording. Records where the bytes are and
    when the broadcast ended, so a later retry has everything it needs.

    Clears ready and filename in the same statement. A parked recording is not
    playable, and an archive that got as far as marking the row finished before
    failing must not leave the landing page offering a file that is not there."""
    with connect() as conn:
        conn.execute(
            "UPDATE vods SET pending_path = ?, ended_at = ?, ready = 0, "
            "filename = NULL WHERE id = ?",
            (path, ended_at, vod_id),
        )


def pending_vods():
    """Recordings waiting to be archived, oldest first."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, pending_path, started_at, ended_at FROM vods "
            "WHERE pending_path IS NOT NULL ORDER BY started_at"
        ).fetchall()
        return [dict(row) for row in rows]


def clear_vod_pending(vod_id):
    """Drop the pending mark, once the recording is archived or given up on."""
    with connect() as conn:
        conn.execute(
            "UPDATE vods SET pending_path = NULL WHERE id = ?", (vod_id,)
        )


def count_pending_vods():
    with connect() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM vods WHERE pending_path IS NOT NULL"
        ).fetchone()[0]


def list_clips():
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, name, filename, creator, start_ts, duration, views, created_at, "
            "keep, share_token FROM clips ORDER BY created_at DESC"
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
        _delete_media_children(conn, kind, ref_id)
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
        # Clear first so this is safe to run twice. An archive that failed and was
        # retried can reach here a second time, and duplicated replay lines would
        # be the only trace of it.
        conn.execute(
            "DELETE FROM replay_chat WHERE kind = ? AND ref_id = ?", (kind, ref_id)
        )
        conn.execute(
            """
            INSERT INTO replay_chat
                (kind, ref_id, username, display_name, avatar_version, font,
                 admin, moderator, text, offset_s, deleted, name_color, msg_color)
            SELECT ?, ?, c.username, c.display_name,
                   COALESCE(u.avatar_version, 0), COALESCE(u.chat_font, 'system'),
                   COALESCE(u.is_admin, 0), COALESCE(u.is_moderator, 0),
                   c.text, MAX(0, c.ts - ?),
                   CASE WHEN c.deleted_by IS NOT NULL THEN 1 ELSE 0 END,
                   COALESCE(u.name_color, ''), COALESCE(u.msg_color, '')
            FROM chat_log c LEFT JOIN users u ON u.username = c.username
            WHERE c.ts >= ? AND c.ts <= ?
            """,
            (kind, ref_id, start_ts, start_ts, end_ts),
        )


def get_replay(kind, ref_id):
    with connect() as conn:
        rows = conn.execute(
            "SELECT username, display_name, avatar_version, font, admin, moderator, "
            "text, offset_s, deleted, name_color, msg_color FROM replay_chat "
            "WHERE kind = ? AND ref_id = ? ORDER BY offset_s, id",
            (kind, ref_id),
        ).fetchall()
        return [dict(r) for r in rows]


# ---- When the channel was last on air --------------------------------------

def get_last_air_ended_at():
    """When the channel last went off air, or 0 on a channel that never has."""
    with connect() as conn:
        row = conn.execute(
            "SELECT last_air_ended_at FROM channel_settings WHERE id = 1"
        ).fetchone()
        return int(row["last_air_ended_at"]) if row else 0


def set_last_air_ended_at(when):
    with connect() as conn:
        conn.execute(
            "UPDATE channel_settings SET last_air_ended_at = ? WHERE id = 1",
            (int(when),),
        )


# ---- Retention ------------------------------------------------------------
# Every limit is 0 by default and 0 means "no limit on this axis", so all five
# at zero is retention switched off: nothing is ever deleted on its own. There
# is deliberately no separate on/off flag, which would be a second source of
# truth able to disagree with the numbers.

RETENTION_FIELDS = (
    "vod_keep_count",
    "vod_keep_days",
    "clip_keep_count",
    "clip_keep_days",
    "media_cap_gb",
)

# The two kinds, and the column each one is ordered and aged by.
_MEDIA_KINDS = {"vod": ("vods", "started_at"), "clip": ("clips", "created_at")}


def get_retention():
    """The channel's retention limits. All zero means retention is off."""
    with connect() as conn:
        row = conn.execute(
            f"SELECT {', '.join(RETENTION_FIELDS)} FROM channel_settings WHERE id = 1"
        ).fetchone()
        if not row:
            return {field: 0 for field in RETENTION_FIELDS}
        return {field: int(row[field]) for field in RETENTION_FIELDS}


def set_retention(**limits):
    """Update whichever retention limits were passed, leaving the rest alone.
    Unknown names are a programming error, not silently ignored."""
    sets = []
    values = []
    for field, value in limits.items():
        if field not in RETENTION_FIELDS:
            raise ValueError(f"unknown retention limit: {field!r}")
        if value is None:
            continue
        sets.append(f"{field} = ?")
        values.append(max(0, int(value)))
    if not sets:
        return
    with connect() as conn:
        conn.execute(
            f"UPDATE channel_settings SET {', '.join(sets)} WHERE id = 1", values
        )


def set_media_keep(kind, ref_id, keep):
    """Pin or unpin one VOD or clip. A pinned item is never removed by
    retention. Returns True when a row changed."""
    table = _media_table(kind)
    with connect() as conn:
        cur = conn.execute(
            f"UPDATE {table} SET keep = ? WHERE id = ?", (1 if keep else 0, ref_id)
        )
        return cur.rowcount > 0


def _rows_for_retention(conn, kind):
    """Every unpinned, finished item of one kind, newest first. Pinned rows are
    left out entirely, so a pin does not use up a slot in the count limit. So
    are rows without a filename yet: a clip's row exists while ffmpeg is still
    cutting it, and deleting it mid-cut would strand the file it is writing."""
    table, ts_column = _MEDIA_KINDS[kind]
    ready = " AND ready = 1" if kind == "vod" else ""
    # share_token comes along for clips because the sweep has to remove the
    # public copy of a published clip as well as the private one. Without it a
    # clip deleted by retention would stay playable for anyone holding the link,
    # forever, and nothing would look wrong from the inside.
    share = "share_token" if kind == "clip" else "NULL AS share_token"
    return conn.execute(
        f"SELECT id, filename, {share}, {ts_column} AS ts FROM {table} "
        f"WHERE keep = 0{ready} AND filename IS NOT NULL AND filename != '' "
        "ORDER BY ts DESC, id DESC"
    ).fetchall()


def _delete_media_row(conn, kind, ref_id):
    table = _media_table(kind)
    _delete_media_children(conn, kind, ref_id)
    conn.execute(f"DELETE FROM {table} WHERE id = ?", (ref_id,))


def prune_candidates(limits, now):
    """The VODs and clips past the count and age limits, newest kept and pinned
    items always kept, as {kind, id, filename}. This only reads: the caller
    removes the files first and deletes the rows for the ones that actually
    went, so a file that cannot be removed never loses its row."""
    doomed = []
    with connect() as conn:
        for kind in _MEDIA_KINDS:
            keep_count = int(limits.get(f"{kind}_keep_count", 0) or 0)
            keep_days = int(limits.get(f"{kind}_keep_days", 0) or 0)
            if keep_count <= 0 and keep_days <= 0:
                continue
            for index, row in enumerate(_rows_for_retention(conn, kind)):
                too_many = keep_count > 0 and index >= keep_count
                too_old = keep_days > 0 and row["ts"] < now - keep_days * 86400
                if too_many or too_old:
                    doomed.append(
                        {"kind": kind, "id": row["id"], "filename": row["filename"],
                         "share_token": row["share_token"]}
                    )
    return doomed


def media_filenames():
    """Every filename and poster the database expects to exist, by kind, so a
    sweep can tell a real file from one nothing points at any more."""
    known = {"vod": set(), "clip": set()}
    with connect() as conn:
        for kind, (table, _ts) in _MEDIA_KINDS.items():
            for row in conn.execute(f"SELECT id, filename FROM {table}"):
                if row["filename"]:
                    known[kind].add(os.path.basename(row["filename"]))
                known[kind].add(f"{row['id']}.jpg")
        # A parked recording still points at its archive: the retry finishes the
        # same file, named by the recording's id, so sweeping it would only make
        # the retry redo work it had already done.
        for row in conn.execute("SELECT id FROM vods WHERE pending_path IS NOT NULL"):
            known["vod"].add(f"{row['id']}.mp4")
    return known


def retention_candidates():
    """Every unpinned, finished VOD and clip, oldest first, as
    {kind, id, filename, ts}. The size cap walks this from the oldest until the
    media store is back under its limit."""
    rows = []
    with connect() as conn:
        for kind in _MEDIA_KINDS:
            for row in _rows_for_retention(conn, kind):
                rows.append(
                    {
                        "kind": kind,
                        "id": row["id"],
                        "filename": row["filename"],
                        "ts": row["ts"],
                    }
                )
    rows.sort(key=lambda r: (r["ts"], r["kind"], r["id"]))
    return rows


def delete_media_rows(items):
    """Delete a batch of {kind, id} rows and everything tied to them. Used by the
    size cap, which decides what goes by looking at the files on disk."""
    with connect() as conn:
        for item in items:
            _delete_media_row(conn, item["kind"], item["id"])


def count_media():
    """How many recordings and clips there are, and how many are pinned. For the
    storage panel, which reports counts next to the bytes."""
    with connect() as conn:
        vods = conn.execute("SELECT COUNT(*) AS n FROM vods WHERE ready = 1").fetchone()
        clips = conn.execute("SELECT COUNT(*) AS n FROM clips").fetchone()
        pinned = conn.execute(
            "SELECT (SELECT COUNT(*) FROM vods WHERE keep = 1 AND ready = 1) + "
            "(SELECT COUNT(*) FROM clips WHERE keep = 1) AS n"
        ).fetchone()
        return {"vods": vods["n"], "clips": clips["n"], "pinned": pinned["n"]}


# ---- Admin views ----------------------------------------------------------

def admin_list_users(include_admins=True):
    """Every real account with rolled-up activity stats, for the dashboards. The
    mod dashboard passes include_admins=False so admin accounts never appear
    there.

    Guests are always excluded. They are accounts only so that moderation and
    presence keep working on them; they are not people who signed up, and a busy
    broadcast would otherwise bury the real account list under expiring rows.
    The analytics page counts them separately instead (count_guests)."""
    where = "WHERE u.is_guest = 0" + ("" if include_admins else " AND u.is_admin = 0")
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
