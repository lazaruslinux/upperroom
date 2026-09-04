"""
Configuration for the upperroom gate.

Every tunable the gate reads from the environment lives here, parsed once at
import so the rest of the package can import plain constants instead of touching
os.environ. No secrets are written in this file; sensitive values are read from
the environment and the account database.
"""

import logging
import os
import re

# Logging. A single "upperroom.*" logger hierarchy, configured once here so the
# rest of the package can just call logging.getLogger("upperroom.<area>"). The
# level comes from the environment (default INFO); nothing secret is ever logged.
LOG_LEVEL = os.environ.get("SELFSTREAM_LOG_LEVEL", "INFO").upper()


def _setup_logging():
    logger = logging.getLogger("upperroom")
    if logger.handlers:                       # idempotent, in case of re-import
        return
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    logger.addHandler(handler)
    logger.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))
    # Keep our lines off the root logger so they are not doubled by any handler
    # a host process (e.g. uvicorn) installs on the root.
    logger.propagate = False


class RedactQueryStrings(logging.Filter):
    """Keep query strings out of the HTTP access log.

    Two endpoints authenticate with a key in the URL: the projector's socket,
    and the overlay, which is an OBS browser source and so cannot send a header.
    uvicorn's access line records the whole request target, which means that
    without this an operator's own `docker logs` slowly fills with live keys,
    and logs travel: into backups, bug reports and screenshots. The path is kept
    because it is what makes the line useful; only the query is dropped."""

    def filter(self, record):
        # The target sits at a different index depending on the line: an HTTP
        # one logs (client, method, target, version, status), a WebSocket one
        # logs (client, target). Rather than track uvicorn's shapes, redact any
        # argument shaped like a request target, which is the only place a query
        # string appears. Anchored on the leading slash so ordinary log text
        # containing a question mark is left alone.
        args = record.args
        if not isinstance(args, tuple):
            return True
        redacted = []
        for arg in args:
            if isinstance(arg, str) and arg.startswith("/") and "?" in arg:
                arg = arg.partition("?")[0] + "?<redacted>"
            redacted.append(arg)
        record.args = tuple(redacted)
        return True


_setup_logging()
# Attached to the loggers rather than to a handler, so it survives uvicorn
# installing its own handlers whenever that happens relative to this import.
# Both names matter: uvicorn writes HTTP access lines to uvicorn.access, but
# its WebSocket "[accepted]" lines go to uvicorn.error, and the sockets are
# exactly where our keys ride.
for _access_logger in ("uvicorn.access", "uvicorn.error"):
    logging.getLogger(_access_logger).addFilter(RedactQueryStrings())

# The running release. Bumped by hand per tagged release (releases are annotated
# git tags), and surfaced in one place: /api/status reads it so the dashboard
# footer and any external check report the version without a number baked into
# the markup.
VERSION = "0.21.1"

JWT_SECRET = os.environ["SELFSTREAM_JWT_SECRET"]
SESSION_HOURS = int(os.environ.get("SELFSTREAM_SESSION_HOURS", "6"))
MEDIAMTX_API = os.environ.get("MEDIAMTX_API", "http://mediamtx:9997")
STREAM_PATH = os.environ.get("SELFSTREAM_PATH", "live")
GEO_DB_PATH = os.environ.get("SELFSTREAM_GEO_DB", "/app/dbip-country.mmdb")
ALLOWED_COUNTRIES = {
    c.strip().upper()
    for c in os.environ.get("SELFSTREAM_ALLOWED_COUNTRIES", "US").split(",")
    if c.strip()
}

COOKIE_NAME = "selfstream_session"
MAX_MESSAGE_LENGTH = 500
CHAT_HISTORY = 50                   # how many recent messages a joiner sees
AVATAR_DIR = os.environ.get("SELFSTREAM_AVATAR_DIR", "/data/avatars")
AVATAR_SIZE = 256                   # avatars are stored as this square, in px
MAX_AVATAR_BYTES = 2 * 1024 * 1024  # reject uploads larger than this
SAFE_USERNAME = re.compile(r"^[a-z0-9_.-]+$")
ALLOWED_FONTS = {"system", "jetbrains", "grotesk", "plex", "sora"}
MAX_BIO_LENGTH = 200

# Chat moderation knobs the admin sets on the dashboard.
MAX_SLOW_SECONDS = 3600         # cap on the slow-mode interval (a typo can't lock chat for a day)
# Length cap on the banned-words text. Raised from 2000 when the default list
# shipped: that list is ~650 characters on its own, and a third of the operator's
# budget going to the defaults left too little room to add their own. Still far
# below Caddy's 64KB cap on /api/* bodies, so nothing else has to move.
MAX_BANNED_WORDS_LEN = 4000


def sanitize_chat_color(value):
    """Validate a viewer-chosen chat color (for their name or their message text).

    Returns a normalized "#rrggbb" string to store, or "" to clear it and fall
    back to the theme default. Raises ValueError with a human-readable message
    when the color is malformed, too dim to read on the near-black chat panel, or
    inside the red range reserved for the LIVE tag and the host camera mark. The
    readability floor is deliberately strict: a color must clear roughly a 4.5:1
    contrast ratio against the dark panel so nobody can pick an invisible name."""
    text = str(value or "").strip().lower()
    if text == "":
        return ""
    if not re.fullmatch(r"#[0-9a-f]{6}", text):
        raise ValueError("Pick a color in #rrggbb form.")
    r, g, b = (int(text[i:i + 2], 16) for i in (1, 3, 5))
    # Reserved: a strong red would mimic the LIVE tag and the host camera badge.
    if r > 180 and g < 90 and b < 90:
        raise ValueError("That red is reserved for the live and host marks. Pick another color.")

    def _linear(channel):
        c = channel / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    luminance = 0.2126 * _linear(r) + 0.7152 * _linear(g) + 0.0722 * _linear(b)
    # (L + 0.05) / (0 + 0.05) >= 4.5  =>  L >= 0.175 against a ~black panel.
    if luminance < 0.18:
        raise ValueError("That color is too dark to read on the chat background. Pick a brighter one.")
    return text
MAX_DISPLAY_NAME = 40
MIN_PASSWORD = 8
MAX_SITE_NAME = 60
MAX_STREAM_TITLE = 100
MAX_STREAM_DESC = 500
# What the streamer is playing, shown in the link preview when the watch
# page is shared. A category label, not a sentence.
MAX_GAME_NAME = 80
MAX_CLIP_NAME = 80
# The lengths the watch page offers as chips, and the only values a client may
# ask for. The viewer's pick is the whole rule: there is no channel-wide cap.
CLIP_LENGTHS = (60, 45, 30)
# What a viewer takes when they send no length at all. The watch page always
# sends one, so this covers a client that does not: a script, or an older page.
DEFAULT_CLIP_LENGTH = CLIP_LENGTHS[0]

# Seconds between one person's clips: the same for viewers and moderators, and
# shorter for the host, who is running the broadcast. Environment variables so
# an operator can retune them without editing code.
CLIP_COOLDOWN_SECONDS = int(os.environ.get("SELFSTREAM_CLIP_COOLDOWN", "300"))
CLIP_COOLDOWN_HOST_SECONDS = int(
    os.environ.get("SELFSTREAM_CLIP_COOLDOWN_HOST", "60")
)
# Chat belongs to the night, not to one broadcast. It survives a stream ending,
# the switch to theater and the film after it, so an evening reads as one
# conversation. The wipe moves to the start of the next broadcast and only fires
# when the last air ended long enough ago to be a different night, so OBS
# crashing and coming back keeps the room. The idle sweep is the backstop for a
# night that never gets a sequel.
NIGHT_GAP_SECONDS = int(os.environ.get("SELFSTREAM_NIGHT_GAP", "21600"))
CHAT_IDLE_WIPE_SECONDS = int(
    os.environ.get("SELFSTREAM_CHAT_IDLE_WIPE", "86400")
)
# How long someone may be gone before the room is told. A phone that backgrounds
# its tab drops the socket and opens a new one on return, which announced a
# departure and an arrival every time somebody glanced at another app. Both
# lines wait this long now, so a flap is silent and a real departure still reads.
JOIN_GRACE_SECONDS = int(os.environ.get("SELFSTREAM_JOIN_GRACE", "60"))

# How many people may pull the video at once. The setting itself lives in
# channel_settings (0 means no limit) so it can be changed from the dashboard
# without a restart; this is only the ceiling a typo cannot get past. Every
# viewer pulls the full broadcast bitrate from this server, so the viewer count
# is what the bandwidth bill is made of.
MAX_VIEWER_LIMIT = 500
# How long after their last segment somebody still counts as watching. HLS
# players fetch about once a second, so this is generous: it forgives a slow
# network without holding a slot for somebody who closed the tab.
WATCHER_WINDOW_SECONDS = 30

MAX_EMAIL = 254
MAX_INVITE_LABEL = 60

# Channel points. Viewers earn this many points per minute while the stream is
# live and they are connected to the watch page. Deliberately a fixed constant,
# not an env var: the earn rate is part of the feature's shape, not a per-install
# knob.
POINTS_PER_MINUTE = 1

# The one thing points buy: highlighting a short message on stream. At one point
# per minute this is roughly fifty minutes of watching. A fixed constant, not an
# env var, for the same reason the earn rate is. The highlight text reuses the
# chat message length limit (MAX_MESSAGE_LENGTH) rather than a limit of its own.
HIGHLIGHT_COST = 50

# How long the admin-only chat log is kept before old lines are purged. Viewers
# always see chat as ephemeral; this only affects the history the admin reviews.
CHAT_RETENTION_SECONDS = int(
    os.environ.get("SELFSTREAM_CHAT_RETENTION_DAYS", "7")
) * 86400

# Live preview thumbnail. A background task pulls a single frame from the stream
# every few seconds while it is live, so the home card can show a real preview.
# The static site Caddy serves. The gate reads one file out of it, the watch
# page, so it can render that page's link-preview tags before handing it over.
WEB_DIR = os.environ.get("SELFSTREAM_WEB_DIR", "/srv/web")

THUMB_PATH = os.environ.get("SELFSTREAM_THUMB", "/data/thumb.jpg")
THUMB_TMP = THUMB_PATH + ".tmp"
THUMB_INTERVAL = int(os.environ.get("SELFSTREAM_THUMB_INTERVAL", "15"))
# Where the gate reads the live stream back from MediaMTX, for the recorder and
# the preview thumbnail. RTSP rather than RTMP: pulling these back over RTMP
# opens a second RTMP client session against our own server, and on a loaded
# machine that read dies mid-demux and leaves recording stalled at zero bytes
# while HLS keeps serving viewers normally. The RTSP read path does not. The port
# is internal to the compose network and never published.
# SELFSTREAM_RTMP_SOURCE is the old name for this setting, still honoured so an
# operator who overrode it does not silently lose the override on update.
MEDIA_SOURCE = os.environ.get("SELFSTREAM_MEDIA_SOURCE") or os.environ.get(
    "SELFSTREAM_RTMP_SOURCE", f"rtsp://mediamtx:8554/{STREAM_PATH}"
)

# Recordings (VODs) and viewer clips. Broadcasts are recorded to a node local
# scratch dir while live (a plain copy, no transcode, over the internal docker
# network, so it never touches the live stream's bandwidth), then archived to the
# media store once the stream ends. The operator points SELFSTREAM_MEDIA_DIR
# wherever they like (a big disk, a NAS, a ZFS mount); it defaults to a docker
# volume so a fresh checkout just works.
RECORD_TMP = os.environ.get("SELFSTREAM_RECORD_TMP", "/data/rec")
MEDIA_DIR = os.environ.get("SELFSTREAM_MEDIA_DIR", "/data/media")
VOD_DIR = os.path.join(MEDIA_DIR, "vods")
CLIP_DIR = os.path.join(MEDIA_DIR, "clips")
# Published clips. A clip in here is reachable without signing in, so nothing
# may be written to it except by the publish path. The files are hard links to
# the originals in CLIP_DIR, not copies: a hard link is a second name for the
# same bytes on disk, so publishing costs no space and the shared copy can never
# drift from the real one. The bytes go when the last name goes, which is why
# deleting a clip has to remove both.
SHARED_DIR = os.path.join(MEDIA_DIR, "shared")
# Poster art for whatever a theater session is showing. It sits under the media
# dir so Caddy serves it behind the same session check as the recordings, and
# deliberately NOT under VOD_DIR or CLIP_DIR: retention and the orphan sweeps
# walk those two folders, and a poster is not a recording to be pruned.
ART_DIR = os.path.join(MEDIA_DIR, "art")
# Retention lives in the dashboard now (channel_settings), not here: the limits
# are per-channel state the operator changes without a restart, and they ship at
# zero so a fresh install never deletes a recording on its own. The old
# SELFSTREAM_VOD_KEEP / _DAYS variables are read exactly once, by db.init_db, to
# seed the dashboard values on an install that predates the move.
# How often the retention sweep runs, so lowering a limit takes effect without
# waiting for the next broadcast to end.
RETENTION_INTERVAL = 3600

# How much of the live edge a clip captures is the viewer's pick, from
# CLIP_LENGTHS above.
#
# CLIP_LAG is only a fallback. The player normally sends the exact instant it
# was showing when Clip was pressed (MediaMTX stamps the HLS playlist with
# EXT-X-PROGRAM-DATE-TIME, hls.js exposes it as playingDate), and then no guess
# is needed at all. This is what a browser that cannot supply that falls back
# to, and it is a guess: the real delivery delay is 2 to 5 seconds and varies.
CLIP_LAG = 2
# A stream copy can only cut on a keyframe, so a clip starts up to one keyframe
# interval before the requested point and would otherwise end that much early,
# losing the moment that was being clipped. The cut asks for this much extra so
# it runs long rather than short. Two seconds matches a typical OBS keyframe
# interval; a source with longer keyframes just gets a slightly softer edge.
CLIP_KEYFRAME_SLACK = 2

# ---- Theater --------------------------------------------------------------
# A theater session plays titles from the operator's own library to the room.
# Search bounds keep a stray keystroke from asking the library for everything;
# the art cap bounds what the projector can push through the socket, which is
# the one place it hands the gate bytes rather than text.
MIN_THEATER_QUERY = 2
MAX_THEATER_QUERY = 64
MAX_THEATER_RESULTS = 25
# A show's whole run, not a page of it. Cutting an episode list at the search cap
# would hide a season with nothing on screen to say it had been hidden.
MAX_THEATER_EPISODES = 500
MAX_THEATER_ART_BYTES = 2 * 1024 * 1024
# Posters are shown at a few hundred pixels; anything larger is re-encoded down.
THEATER_ART_MAX = (600, 900)

# Comments on a recording or a clip. Longer than a chat line because this is
# written after the fact rather than in the moment, but still a comment and not
# an essay.
MAX_COMMENT_LENGTH = 1000

# ---- Chat socket limits ---------------------------------------------------
# One person legitimately has a couple of tabs open. Beyond that it is either a
# stuck reconnect loop or somebody leaning on the server, and the chat socket is
# an expensive thing to lean on: every join broadcasts presence AND a joined
# line to every open socket, so the work is quadratic in the number of them.
# Measured on the demo stack, one ordinary account: 10 sockets cost 120 frames,
# 25 cost 683, 50 cost 2,600. A thousand would be about a million.
MAX_SOCKETS_PER_USER = 6
# Connection attempts per address per minute. Generous next to a real person
# (who connects once and stays) and far below what a flood needs.
MAX_SOCKET_CONNECTS = 30

# ---- Guest passes ---------------------------------------------------------
# A guest pass is a single-use code that lets someone watch and chat without an
# account. Redeeming one creates a real users row flagged is_guest, because
# presence, watch sessions, bans and every moderator command resolve their
# target through that row: a guest with no row could talk in chat and could not
# be timed out, banned or purged.
GUEST_MINUTES = 30                 # the clock starts on redemption, not on issue
GUEST_REAP_INTERVAL = 300          # how often expired guest accounts are removed
MAX_GUEST_NAME = 24                # shorter than a member's; it is on screen only
MAX_GUEST_PASS_BATCH = 25          # how many passes one click may mint

# Recorder resilience. The stream watcher supervises the recording ffmpeg while
# the stream is live: if the process dies or its scratch file stops growing, the
# partial recording is finalized (when it holds usable content) or discarded, and
# a fresh recording is started while the broadcast is still on. Repeated failures
# back off so a persistently broken source cannot spin restarts in a tight loop.
RECORD_STARTUP_GRACE = 3           # seconds to confirm the recorder stayed up
RECORD_STALL_POLLS = 4             # no-growth polls (~5s each) before a stall call
RECORD_SURVIVAL_SECONDS = 60       # a recording alive this long clears the backoff
RECORD_BACKOFF = (0, 10, 30, 60)   # seconds between successive restart attempts

# ---- Go-live notifications ------------------------------------------------
# When a broadcast starts, announce it once over any channel the operator has
# configured: a Discord webhook and/or email through an SMTP relay (e.g. Brevo).
# Everything here is best effort and gated on configuration: with nothing set,
# notifications are simply skipped.
SITE_URL = os.environ.get("SELFSTREAM_SITE_URL", "").rstrip("/")
# Re-announcing is suppressed within this window, so a brief HLS blip (offline ->
# online flap) or a gate restart mid-broadcast cannot spam viewers.
NOTIFY_COOLDOWN = int(os.environ.get("SELFSTREAM_NOTIFY_COOLDOWN", "1800"))
SMTP_HOST = os.environ.get("SELFSTREAM_SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("SELFSTREAM_SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SELFSTREAM_SMTP_USER", "")
SMTP_PASS = os.environ.get("SELFSTREAM_SMTP_PASS", "")
SMTP_FROM = os.environ.get("SELFSTREAM_SMTP_FROM", "")
