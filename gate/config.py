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
MAX_DISPLAY_NAME = 40
MIN_PASSWORD = 8
MAX_STREAM_TITLE = 100
MAX_STREAM_DESC = 500
MAX_CLIP_NAME = 80
MAX_EMAIL = 254

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
# Retention: keep at most this many VODs, and/or only this many days. 0 disables
# that limit. The oldest beyond the limit are pruned (files and rows) on stop.
VOD_KEEP = int(os.environ.get("SELFSTREAM_VOD_KEEP", "20"))
VOD_KEEP_DAYS = int(os.environ.get("SELFSTREAM_VOD_KEEP_DAYS", "0"))
CLIP_SECONDS = 30                  # how much of the live edge a clip captures
CLIP_LAG = 2                       # stay this far back from the very live edge

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
