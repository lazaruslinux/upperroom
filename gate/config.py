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


_setup_logging()

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
ALLOWED_FONTS = {"system", "mono", "comic", "retro", "caveat"}
MAX_BIO_LENGTH = 200

# Chat moderation knobs the admin sets on the dashboard.
MAX_SLOW_SECONDS = 3600         # cap on the slow-mode interval (a typo can't lock chat for a day)
MAX_BANNED_WORDS_LEN = 2000     # length cap on the banned-words text


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
MAX_CLIP_NAME = 80
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
THUMB_PATH = os.environ.get("SELFSTREAM_THUMB", "/data/thumb.jpg")
THUMB_TMP = THUMB_PATH + ".tmp"
THUMB_INTERVAL = int(os.environ.get("SELFSTREAM_THUMB_INTERVAL", "15"))
RTMP_SOURCE = os.environ.get(
    "SELFSTREAM_RTMP_SOURCE", f"rtmp://mediamtx:1935/{STREAM_PATH}"
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
# Retention lives in the dashboard now (channel_settings), not here: the limits
# are per-channel state the operator changes without a restart, and they ship at
# zero so a fresh install never deletes a recording on its own. The old
# SELFSTREAM_VOD_KEEP / _DAYS variables are read exactly once, by db.init_db, to
# seed the dashboard values on an install that predates the move.
# How often the retention sweep runs, so lowering a limit takes effect without
# waiting for the next broadcast to end.
RETENTION_INTERVAL = 3600

# ---- The next scheduled stream --------------------------------------------
# The operator announces one upcoming broadcast; the login page counts down to
# it and everyone with go-live email on gets one reminder beforehand. Fixed
# rather than configurable for the same reason as the points rate below: the
# lead time is part of the feature's shape, not a per-install knob.
SCHEDULE_REMIND_LEAD = 3600        # remind this long before the start time
# How long past the start time a schedule keeps showing before it clears itself.
# Two hours, so a broadcast that starts late still reads "starting soon" rather
# than vanishing exactly when viewers are looking for it.
SCHEDULE_GRACE = 7200
SCHEDULE_CHECK_INTERVAL = 60       # how often the reminder worker looks
MAX_SCHEDULE_NOTE = 120
CLIP_SECONDS = 30                  # how much of the live edge a clip captures
CLIP_LAG = 2                       # stay this far back from the very live edge

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
