"""
selfstream gate and chat service.

This service is the brains of selfstream. It:

  - logs viewers in with a named account and password
  - issues a signed session cookie that lasts a few hours
  - answers the check Caddy makes before it serves any video
  - runs the live chat and the watching list over a WebSocket
  - reports whether the stream is currently live

No accounts are hard coded here and no secrets are written in this file.
Accounts live in a SQLite database that the admin manages with manage.py, and
every sensitive value is read from the environment.
"""

import asyncio
import io
import os
import re
import shutil
import signal
import smtplib
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from email.message import EmailMessage

import geoip2.database
import httpx
import jwt
from fastapi import (
    FastAPI, File, Request, Response, UploadFile, WebSocket, WebSocketDisconnect,
)
from fastapi.responses import FileResponse, JSONResponse
from PIL import Image, ImageOps

import db

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


async def fetch_path():
    """Return the MediaMTX path JSON for our stream, or None on any error."""
    url = f"{MEDIAMTX_API}/v3/paths/get/{STREAM_PATH}"
    try:
        async with httpx.AsyncClient(timeout=5) as http:
            reply = await http.get(url)
        if reply.status_code == 200:
            return reply.json()
    except httpx.HTTPError:
        pass
    return None


async def stream_watcher():
    """Wipe the chat when a broadcast ends, so the next stream starts clean."""
    was_online = False
    while True:
        try:
            data = await fetch_path()
            online = bool(data and data.get("ready", False))
            # Open/close watch sessions on the live<->offline transition so
            # watch time only counts while the stream is live.
            await hub.set_live(online)
            if online and not was_online:
                await start_recording()
                # Announce in the background so a slow webhook or mail relay never
                # delays the status poll. notify_live enforces its own cooldown.
                asyncio.create_task(notify_live())
            if was_online and not online:
                await hub.wipe()
                await stop_recording()
            was_online = online
        except Exception:
            pass
        await asyncio.sleep(5)


async def capture_thumbnail():
    """Save one frame from the live stream into THUMB_PATH. Best effort.

    While a broadcast is recording, the stream is already being written to local
    disk, so we grab the freshest frame from that file instead of opening a
    second full RTMP pull just for a thumbnail. When no recording is in progress
    (a brief window right at go-live), fall back to a short RTMP read."""
    rec_path = _rec["tmp_path"] if _rec["active"] else None
    if rec_path and os.path.exists(rec_path):
        # -sseof -1 seeks to one second before the end of the file, reading the
        # most recent frame without decoding the whole recording.
        source_args = ["-sseof", "-1", "-i", rec_path]
    else:
        source_args = ["-rw_timeout", "5000000", "-i", RTMP_SOURCE]
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y", "-loglevel", "error",
        *source_args,
        "-frames:v", "1",
        "-vf", "scale=640:-2",             # 640px wide, height kept even
        "-q:v", "5",
        THUMB_TMP,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        await asyncio.wait_for(proc.wait(), timeout=12)
    except asyncio.TimeoutError:
        proc.kill()
        return
    # Swap in atomically so a half-written file is never served.
    if proc.returncode == 0 and os.path.exists(THUMB_TMP):
        os.replace(THUMB_TMP, THUMB_PATH)


async def thumbnail_worker():
    """While the stream is live, refresh the preview thumbnail on an interval.
    When it goes offline, drop the stale frame so the card shows offline."""
    while True:
        try:
            data = await fetch_path()
            if data and data.get("ready", False):
                await capture_thumbnail()
            elif os.path.exists(THUMB_PATH):
                os.remove(THUMB_PATH)
        except Exception:
            pass
        await asyncio.sleep(THUMB_INTERVAL)


# ---- Recording (VODs) and clips -------------------------------------------
# A broadcast is recorded with a plain stream copy (no transcode) to local
# scratch while live, then archived to the media store when it ends. Clips are
# cut from that in-progress file on demand.

_rec = {
    "active": False, "vod_id": None, "tmp_path": None,
    "started_at": None, "proc": None,
}


async def _run_ffmpeg(args, timeout):
    """Run an ffmpeg/ffprobe command to completion, returning (returncode, stdout).
    Best effort: a timeout or failure returns (None, b'')."""
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return proc.returncode, out
    except Exception:
        if proc and proc.returncode is None:
            try:
                proc.kill()
            except Exception:
                pass
        return None, b""


async def _make_poster(src, dst, seek=2):
    """Save a single frame as the card poster for a VOD or clip."""
    await _run_ffmpeg(
        ["ffmpeg", "-y", "-loglevel", "error", "-ss", str(seek), "-i", src,
         "-frames:v", "1", "-vf", "scale=640:-2", "-q:v", "5", dst],
        timeout=20,
    )


async def _probe_duration(path):
    code, out = await _run_ffmpeg(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", path],
        timeout=20,
    )
    try:
        return int(float(out.decode().strip()))
    except Exception:
        return 0


def _remove_media_files(folder, filename, item_id):
    for name in (filename, f"{item_id}.jpg"):
        if not name:
            continue
        path = os.path.join(folder, os.path.basename(name))
        try:
            if os.path.exists(path):
                os.remove(path)
        except Exception:
            pass


async def start_recording():
    if _rec["active"]:
        return
    started_at = int(time.time())
    info = db.get_stream_info()
    try:
        vod_id = db.create_vod(
            info["stream_title"], info["stream_description"], started_at
        )
    except Exception:
        return
    tmp_path = os.path.join(RECORD_TMP, f"{vod_id}.mp4")
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", "-loglevel", "error",
            "-i", RTMP_SOURCE,
            "-c", "copy",
            "-f", "mp4",
            # A fragmented MP4 is web playable and survives an abrupt stop, which
            # matters because we cut clips from it while it is still being written.
            "-movflags", "+frag_keyframe+empty_moov+default_base_moof",
            tmp_path,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except Exception:
        try:
            db.delete_media("vod", vod_id)
        except Exception:
            pass
        return
    _rec.update(
        active=True, vod_id=vod_id, tmp_path=tmp_path,
        started_at=started_at, proc=proc,
    )


async def stop_recording():
    if not _rec["active"]:
        return
    vod_id, tmp_path = _rec["vod_id"], _rec["tmp_path"]
    started_at, proc = _rec["started_at"], _rec["proc"]
    ended_at = int(time.time())
    # Mark inactive at once so clips stop and a quick re-go-live starts clean.
    _rec.update(active=False, vod_id=None, tmp_path=None, started_at=None, proc=None)
    if proc and proc.returncode is None:
        try:
            proc.send_signal(signal.SIGINT)   # let ffmpeg write the trailer
            await asyncio.wait_for(proc.wait(), timeout=15)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
    # Archive and finalize in the background so a slow transfer to the media
    # store (which may be a network mount) never blocks the stream watcher.
    asyncio.create_task(_finalize_recording(vod_id, tmp_path, started_at, ended_at))


async def _finalize_recording(vod_id, tmp_path, started_at, ended_at):
    try:
        if not (tmp_path and os.path.exists(tmp_path)
                and os.path.getsize(tmp_path) > 100_000):
            db.delete_media("vod", vod_id)
            if tmp_path and os.path.exists(tmp_path):
                os.remove(tmp_path)
            return
        filename = f"{vod_id}.mp4"
        # Poster first, while the file is still on fast local scratch.
        await _make_poster(tmp_path, os.path.join(VOD_DIR, f"{vod_id}.jpg"))
        dst = os.path.join(VOD_DIR, filename)
        # Remux the recording into a regular, faststart MP4. The live recording is
        # a fragmented MP4 (empty_moov + keyframe fragments) so it survives an
        # abrupt stop, but fragmented files load slowly and break some mobile
        # players. A plain stream copy with +faststart rewrites it to a single
        # moov-at-front file that seeks and plays everywhere; no re-encode, so it
        # stays quick. If the remux fails, fall back to moving the raw recording
        # so the VOD is never lost, even if playback is degraded.
        code, _ = await _run_ffmpeg(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", tmp_path,
             "-c", "copy", "-movflags", "+faststart", dst],
            timeout=600,
        )
        if code == 0 and os.path.exists(dst) and os.path.getsize(dst) > 100_000:
            try:
                os.remove(tmp_path)
            except OSError:
                pass
        else:
            await asyncio.to_thread(shutil.move, tmp_path, dst)
        duration = await _probe_duration(dst) or max(0, ended_at - started_at)
        db.finalize_vod(vod_id, ended_at, duration, filename)
        db.snapshot_chat("vod", vod_id, started_at, ended_at)
        # Enforce retention, removing the oldest VODs (rows and files) over the cap.
        try:
            for doomed in db.prune_vods(VOD_KEEP, VOD_KEEP_DAYS, int(time.time())):
                _remove_media_files(VOD_DIR, doomed.get("filename"), doomed["id"])
        except Exception:
            pass
    except Exception:
        pass


def cooldown_for(user):
    """A user's clip cooldown in seconds (0 disables it). Per-role and
    admin-configured: admins get the shortest, then moderators, then viewers."""
    info = db.get_stream_info()
    if user and user["is_admin"]:
        minutes = int(info["clip_cooldown_admin"])
    elif user and user["is_moderator"]:
        minutes = int(info["clip_cooldown_mod"])
    else:
        minutes = int(info["clip_cooldown_user"])
    return max(0, minutes) * 60


def format_remaining(seconds):
    """A short 'Xm Ys' / 'Ys' label for the cooldown message."""
    seconds = max(0, int(seconds))
    minutes, secs = divmod(seconds, 60)
    if minutes and secs:
        return f"{minutes}m {secs}s"
    if minutes:
        return f"{minutes}m"
    return f"{secs}s"


async def make_clip(user, name):
    """Cut the last CLIP_SECONDS of the live stream into a named clip. Returns
    (clip_id, None) on success or (None, error_message)."""
    if not _rec["active"]:
        return None, "The stream is not live."
    username = user["username"]
    cooldown = cooldown_for(user)
    if cooldown:
        elapsed = int(time.time()) - db.last_clip_at(username)
        if elapsed < cooldown:
            return None, (
                f"Clip cooldown — you can clip again in "
                f"{format_remaining(cooldown - elapsed)}."
            )
    started_at, src, vod_id = _rec["started_at"], _rec["tmp_path"], _rec["vod_id"]
    end = int(time.time()) - CLIP_LAG
    start = max(started_at, end - CLIP_SECONDS)
    duration = end - start
    if duration < 3:
        return None, "The stream just started; nothing to clip yet."
    name = (name or "").strip()[:MAX_CLIP_NAME] or "Clip"
    # Create the row first so the file can be named by its id.
    clip_id = db.create_clip(
        name, "", username, vod_id, start, end, duration, int(time.time())
    )
    filename = f"{clip_id}.mp4"
    dst = os.path.join(CLIP_DIR, filename)
    code, _ = await _run_ffmpeg(
        ["ffmpeg", "-y", "-loglevel", "error",
         "-ss", str(start - started_at), "-i", src, "-t", str(duration),
         "-c", "copy", "-movflags", "+faststart", dst],
        timeout=40,
    )
    if code != 0 or not os.path.exists(dst) or os.path.getsize(dst) < 1000:
        db.delete_media("clip", clip_id)
        _remove_media_files(CLIP_DIR, filename, clip_id)
        return None, "Could not make the clip. Try again in a moment."
    db.set_clip_filename(clip_id, filename)
    await _make_poster(dst, os.path.join(CLIP_DIR, f"{clip_id}.jpg"), seek=1)
    db.snapshot_chat("clip", clip_id, start, end)
    return clip_id, None


async def send_discord(webhook, content):
    """Post a message to a Discord incoming webhook. Best effort."""
    if not webhook:
        return
    try:
        async with httpx.AsyncClient(timeout=10) as http:
            await http.post(webhook, json={"content": content})
    except httpx.HTTPError:
        pass


def _send_emails_blocking(recipients, subject, body):
    """Send the go-live email to each recipient over the SMTP relay. Blocking, so
    it is called from a thread. One bad address never stops the rest."""
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as server:
            server.starttls()
            if SMTP_USER:
                server.login(SMTP_USER, SMTP_PASS)
            for _name, address in recipients:
                message = EmailMessage()
                message["Subject"] = subject
                message["From"] = SMTP_FROM
                message["To"] = address
                message.set_content(body)
                try:
                    server.send_message(message)
                except smtplib.SMTPException:
                    continue
    except (smtplib.SMTPException, OSError):
        pass


async def send_live_emails(title):
    """Email everyone who opted in that the channel is live. No-op unless an SMTP
    relay is configured and at least one account has an address."""
    if not (SMTP_HOST and SMTP_FROM):
        return
    recipients = db.list_live_recipients()
    if not recipients:
        return
    lines = [title, "", "The stream just went live."]
    if SITE_URL:
        lines += ["", f"Watch: {SITE_URL}/home"]
    body = "\n".join(lines)
    await asyncio.to_thread(
        _send_emails_blocking, recipients, "Lazarus Labs is live", body
    )


async def notify_live(force=False):
    """Announce that the channel went live, over every configured channel. Sends
    at most once per cooldown window unless force=True (a manual test). Best
    effort throughout: a failure on any channel never touches the stream."""
    now = int(time.time())
    settings = db.get_notify_settings()
    if not force:
        if now - settings["last_notified_at"] < NOTIFY_COOLDOWN:
            return
        # Stamp the cooldown before sending so a slow relay cannot let a second
        # transition slip through and double-announce. A test send leaves the
        # real cooldown untouched.
        db.mark_notified(now)
    info = db.get_stream_info()
    title = info["stream_title"] or "Live Stream"
    discord_text = f"**{title}** is live now."
    if SITE_URL:
        discord_text += f"\n{SITE_URL}/home"
    await send_discord(settings["discord_webhook"], discord_text)
    await send_live_emails(title)


async def chat_purge_worker():
    """Once a day, drop chat messages older than the retention window."""
    while True:
        try:
            db.purge_old_chat(int(time.time()) - CHAT_RETENTION_SECONDS)
        except Exception:
            pass
        await asyncio.sleep(86400)


@asynccontextmanager
async def lifespan(_app):
    hub.load_bans()
    # Any VOD still marked unfinished is from a recording the previous run never
    # got to close out; drop those rows so they do not linger.
    try:
        db.clear_unfinished_vods()
    except Exception:
        pass
    tasks = [
        asyncio.create_task(stream_watcher()),
        asyncio.create_task(thumbnail_worker()),
        asyncio.create_task(chat_purge_worker()),
    ]
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()


app = FastAPI(title="selfstream", docs_url=None, redoc_url=None, lifespan=lifespan)
db.init_db()
for _dir in (AVATAR_DIR, RECORD_TMP, VOD_DIR, CLIP_DIR):
    os.makedirs(_dir, exist_ok=True)


# ---- Login rate limiting --------------------------------------------------
# A short per address history of login attempts. Simple on purpose, and it
# resets if the service restarts, which is fine for a single operator.

_ATTEMPTS = defaultdict(deque)
_MAX_ATTEMPTS = 5
_WINDOW_SECONDS = 60


def too_many_attempts(ip):
    now = time.time()
    history = _ATTEMPTS[ip]
    while history and now - history[0] > _WINDOW_SECONDS:
        history.popleft()
    if len(history) >= _MAX_ATTEMPTS:
        return True
    history.append(now)
    return False


# ---- Sessions -------------------------------------------------------------

def issue_token(user):
    now = int(time.time())
    payload = {
        "sub": user["username"],
        "name": user["display_name"],
        "admin": bool(user["is_admin"]),
        "mod": bool(user["is_moderator"]),
        "iat": now,
        "exp": now + SESSION_HOURS * 3600,
    }
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")


def read_session(token):
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None


def admin_user(request):
    """Return the signed in admin's user row, or None. The admin flag is read
    fresh from the database, not the cookie, so revoking admin takes effect at
    once rather than waiting for the session to expire."""
    session = read_session(request.cookies.get(COOKIE_NAME, ""))
    if not session:
        return None
    user = db.get_user(session["sub"])
    if not user or not user["is_admin"]:
        return None
    return user


def mod_actor(request):
    """The signed in user's row if they may use the moderator dashboard (admin or
    moderator), read fresh from the database. None otherwise."""
    session = read_session(request.cookies.get(COOKIE_NAME, ""))
    if not session:
        return None
    user = db.get_user(session["sub"])
    if not user or not (user["is_admin"] or user["is_moderator"]):
        return None
    return user


def can_moderate(user):
    """Whether a user may run moderator actions. Admin and moderator are separate
    roles, but an admin keeps every moderator power (admin is a superset of mod in
    capability, never in identity, so the badges stay distinct)."""
    return bool(user and (user["is_admin"] or user["is_moderator"]))


def client_ip(request):
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


# Geo lookup. The database is baked into the image at build time. If it is
# missing, or an address is not in it, we allow the request, so a database
# problem can never lock everyone out of the stream.
try:
    _geo_reader = geoip2.database.Reader(GEO_DB_PATH)
except Exception:
    _geo_reader = None


def country_allowed(ip):
    if not _geo_reader or not ALLOWED_COUNTRIES:
        return True
    try:
        code = _geo_reader.country(ip).country.iso_code
    except Exception:
        return True
    return code in ALLOWED_COUNTRIES


# ---- Chat and presence ----------------------------------------------------

class Hub:
    """Tracks who is connected and relays chat messages to everyone."""

    def __init__(self):
        self._sockets = {}            # websocket -> {"username", "name", "admin", "mod"}
        self._history = deque(maxlen=CHAT_HISTORY)
        self._lock = asyncio.Lock()
        self._live = False            # whether the stream is currently live
        self._timeouts = {}           # username -> epoch until which they are muted
        self._banned = set()          # usernames with a persistent chat ban

    def viewers(self):
        # One entry per person, even if they have several tabs open.
        seen = {}
        for who in self._sockets.values():
            seen[who["username"]] = who
        return [
            {
                "username": w["username"],
                "name": w["name"],
                "avatar": w.get("avatar", 0),
                "admin": bool(w.get("admin")),
                "mod": bool(w.get("mod")),
            }
            for w in seen.values()
        ]

    def presence_message(self):
        viewers = self.viewers()
        return {"type": "presence", "viewers": viewers, "count": len(viewers)}

    async def broadcast(self, message):
        dead = []
        for socket in list(self._sockets):
            try:
                await socket.send_json(message)
            except Exception:
                dead.append(socket)
        for socket in dead:
            self._sockets.pop(socket, None)

    async def join(self, socket, who):
        # Replay the recent backlog so someone joining mid stream sees the last
        # messages. The backlog is kept until the stream ends, then wiped.
        async with self._lock:
            self._sockets[socket] = who
            history = list(self._history)
            # Only accrue watch time while the stream is actually live. Joining
            # while offline just puts you in chat; no watch session is opened.
            who["watch_id"] = None
            if self._live:
                try:
                    who["watch_id"] = db.start_watch_session(
                        who["username"], int(time.time())
                    )
                except Exception:
                    who["watch_id"] = None
        await socket.send_json({"type": "hello", "you": who, "history": history})
        await self.broadcast(self.presence_message())
        await self.broadcast(
            {"type": "system", "text": f"{who['name']} joined", "ts": int(time.time())}
        )

    async def leave(self, socket):
        who = self._sockets.pop(socket, None)
        if who and who.get("watch_id"):
            try:
                db.end_watch_session(who["watch_id"], int(time.time()))
            except Exception:
                pass
        await self.broadcast(self.presence_message())
        if who:
            await self.broadcast(
                {"type": "system", "text": f"{who['name']} left", "ts": int(time.time())}
            )

    async def say(self, who, text):
        ts = int(time.time())
        # Log first so the message carries its database row id. A moderator's
        # /del command uses that id to remove a specific line for everyone.
        msg_id = None
        try:
            msg_id = db.log_chat(who["username"], who["name"], text, ts)
        except Exception:
            pass
        message = {
            "type": "chat",
            "id": msg_id,
            "user": who["username"],
            "name": who["name"],
            "admin": who["admin"],
            "mod": who.get("mod", False),
            "avatar": who.get("avatar", 0),
            "font": who.get("font", "system"),
            "text": text,
            "ts": ts,
        }
        self._history.append(message)
        await self.broadcast(message)

    # ---- moderation -------------------------------------------------------

    def is_timed_out(self, username):
        until = self._timeouts.get(username)
        if not until:
            return 0
        remaining = until - int(time.time())
        if remaining <= 0:
            self._timeouts.pop(username, None)
            return 0
        return remaining

    def set_timeout(self, username, seconds):
        self._timeouts[username] = int(time.time()) + seconds

    def clear_timeout(self, username):
        self._timeouts.pop(username, None)

    def load_bans(self):
        try:
            self._banned = set(db.banned_usernames())
        except Exception:
            self._banned = set()

    def is_banned(self, username):
        return username in self._banned

    def add_ban_local(self, username):
        self._banned.add(username)

    def remove_ban_local(self, username):
        self._banned.discard(username)

    async def delete_last_by(self, username, by):
        """Mark the most recent visible message from a user as deleted, in the
        backlog and on every open page. Returns the message dict, or None."""
        target = None
        for message in reversed(self._history):
            if message.get("user") == username and not message.get("deleted"):
                target = message
                break
        if not target:
            return None
        target["deleted"] = True
        if target.get("id"):
            try:
                db.mark_chat_deleted(target["id"], by)
            except Exception:
                pass
        await self.broadcast({"type": "delete", "id": target.get("id")})
        return target

    async def update_role(self, username, mod=None):
        """Reflect a role change on a user's open sockets so their next message
        and the watching list show (or drop) the badge without a reconnect."""
        for who in self._sockets.values():
            if who["username"] == username and mod is not None:
                who["mod"] = bool(mod)
        await self.broadcast(self.presence_message())

    async def notify_user(self, username, text):
        """Send a private system line to one user's open sockets (e.g. to tell
        them they have been timed out). Others do not see it."""
        note = {"type": "system", "text": text, "ts": int(time.time())}
        for socket, who in list(self._sockets.items()):
            if who["username"] == username:
                try:
                    await socket.send_json(note)
                except Exception:
                    pass

    async def set_live(self, online):
        # Called by the stream watcher. Watch time only counts while live, so on
        # the transitions we open or close a watch session for everyone who is
        # already connected.
        if online == self._live:
            return
        now = int(time.time())
        async with self._lock:
            self._live = online
            for who in self._sockets.values():
                if online and not who.get("watch_id"):
                    try:
                        who["watch_id"] = db.start_watch_session(who["username"], now)
                    except Exception:
                        who["watch_id"] = None
                elif not online and who.get("watch_id"):
                    try:
                        db.end_watch_session(who["watch_id"], now)
                    except Exception:
                        pass
                    who["watch_id"] = None

    async def wipe(self):
        # Called when a broadcast ends. Clear the backlog and tell every open
        # page to empty its chat, so the next stream starts fresh.
        async with self._lock:
            self._history.clear()
        await self.broadcast({"type": "wipe"})

    async def update_member(self, username, avatar=None, font=None, name=None):
        # A viewer changed their avatar, chat font, or display name mid-session.
        # Point their open sockets at the new value so their next messages and
        # the watching list reflect it, without making them reconnect.
        for who in self._sockets.values():
            if who["username"] == username:
                if avatar is not None:
                    who["avatar"] = avatar
                if font is not None:
                    who["font"] = font
                if name is not None:
                    who["name"] = name
        await self.broadcast(self.presence_message())


hub = Hub()


# ---- Chat slash commands --------------------------------------------------
# Moderation is driven entirely by commands typed into chat, not buttons. The
# chat socket intercepts any message starting with "/", authorizes it against a
# fresh database read of the sender's role, and never echoes it to other people.

DEFAULT_TIMEOUT_SECONDS = 300
MAX_TIMEOUT_SECONDS = 86400


async def system_reply(websocket, text):
    """Send a private system line back to the person who ran a command."""
    try:
        await websocket.send_json(
            {"type": "system", "text": text, "ts": int(time.time())}
        )
    except Exception:
        pass


def _target_name(arg):
    return arg.strip().lower().lstrip("@")


async def handle_command(websocket, who, text):
    parts = text[1:].split()
    if not parts:
        return
    cmd = parts[0].lower()
    args = parts[1:]

    # Role is read fresh from the database, never trusted from the cookie, so a
    # demotion takes effect immediately.
    actor = db.get_user(who["username"])
    is_admin = bool(actor and actor["is_admin"])
    is_mod = bool(actor and actor["is_moderator"])

    if cmd == "help":
        lines = ["Chat commands:"]
        if is_admin or is_mod:
            lines += [
                "/timeout <user> [seconds] — mute a viewer (default 300)",
                "/untimeout <user> — lift a timeout",
                "/del <user> — delete that viewer's last message",
                "/ban <user> [reason] — ban a viewer from chat",
                "/unban <user> — lift a ban (yours, or any if admin)",
            ]
        if is_admin:
            lines += [
                "/mod <user> — make someone a chat moderator",
                "/unmod <user> — remove a chat moderator",
            ]
        if len(lines) == 1:
            lines.append("Your account has no chat commands.")
        await system_reply(websocket, "\n".join(lines))
        return

    if not (is_admin or is_mod):
        await system_reply(websocket, "You do not have permission for chat commands.")
        return

    # Granting and removing moderators is admin only: moderators cannot mint more
    # moderators.
    if cmd in ("mod", "unmod"):
        if not is_admin:
            await system_reply(websocket, "Only an admin can change moderators.")
            return
        if not args:
            await system_reply(websocket, f"Usage: /{cmd} <username>")
            return
        target = db.get_user(_target_name(args[0]))
        if not target:
            await system_reply(websocket, f"No account named {_target_name(args[0])}.")
            return
        make = cmd == "mod"
        if bool(target["is_moderator"]) == make:
            state = "already" if make else "not"
            await system_reply(
                websocket, f"{target['display_name']} is {state} a moderator."
            )
            return
        db.update_user(target["username"], is_moderator=make)
        await hub.update_role(target["username"], mod=make)
        word = "now" if make else "no longer"
        await system_reply(
            websocket, f"{target['display_name']} is {word} a moderator."
        )
        await hub.notify_user(
            target["username"],
            "You are now a chat moderator." if make
            else "You are no longer a chat moderator.",
        )
        return

    # timeout / untimeout / del all act on a target, and a moderator may not act
    # on an admin (only another admin can).
    if not args:
        await system_reply(websocket, f"Usage: /{cmd} <username>")
        return
    target = db.get_user(_target_name(args[0]))
    if not target:
        await system_reply(websocket, f"No account named {_target_name(args[0])}.")
        return
    if target["is_admin"] and not is_admin:
        await system_reply(websocket, "You can't moderate an admin.")
        return

    if cmd == "timeout":
        seconds = DEFAULT_TIMEOUT_SECONDS
        if len(args) > 1:
            try:
                seconds = max(1, min(MAX_TIMEOUT_SECONDS, int(args[1])))
            except ValueError:
                await system_reply(websocket, "Seconds must be a whole number.")
                return
        hub.set_timeout(target["username"], seconds)
        await system_reply(
            websocket, f"{target['display_name']} is timed out for {seconds}s."
        )
        await hub.notify_user(
            target["username"],
            f"A moderator has timed you out for {seconds} seconds.",
        )
        return

    if cmd == "untimeout":
        hub.clear_timeout(target["username"])
        await system_reply(websocket, f"Timeout lifted for {target['display_name']}.")
        await hub.notify_user(target["username"], "Your timeout has been lifted.")
        return

    if cmd == "del":
        removed = await hub.delete_last_by(target["username"], who["username"])
        if removed:
            await system_reply(
                websocket, f"Deleted {target['display_name']}'s last message."
            )
        else:
            await system_reply(
                websocket, f"No recent message from {target['display_name']}."
            )
        return

    if cmd == "ban":
        reason = " ".join(args[1:])[:200]
        try:
            db.add_ban(target["username"], who["username"], reason, int(time.time()))
        except Exception:
            await system_reply(websocket, "Could not save the ban.")
            return
        hub.add_ban_local(target["username"])
        await system_reply(
            websocket, f"{target['display_name']} is banned from chat."
        )
        await hub.notify_user(
            target["username"], "You have been banned from chat."
        )
        return

    if cmd == "unban":
        existing = db.get_ban(target["username"])
        if not existing:
            await system_reply(
                websocket, f"{target['display_name']} is not banned."
            )
            return
        # A moderator may lift only a ban they issued; an admin may lift any.
        if not is_admin and existing["banned_by"] != who["username"]:
            await system_reply(
                websocket, "Only the moderator who set this ban, or an admin, can lift it."
            )
            return
        db.remove_ban(target["username"])
        hub.remove_ban_local(target["username"])
        await system_reply(websocket, f"{target['display_name']} is unbanned.")
        await hub.notify_user(
            target["username"], "Your chat ban has been lifted."
        )
        return

    await system_reply(websocket, f"Unknown command /{cmd}. Try /help.")


# ---- HTTP routes ----------------------------------------------------------

@app.get("/api/me")
def me(request: Request):
    session = read_session(request.cookies.get(COOKIE_NAME, ""))
    if not session:
        return {"authed": False}
    user = db.get_user(session["sub"])
    return {
        "authed": True,
        "username": session["sub"],
        "name": session["name"],
        "admin": bool(user["is_admin"]) if user else bool(session.get("admin")),
        "mod": bool(user["is_moderator"]) if user else False,
        "avatar": user["avatar_version"] if user else 0,
        "font": user["chat_font"] if user else "system",
        "bio": user["bio"] if user else "",
        "notify_live": bool(user["notify_live"]) if user else True,
        "email": user["email"] if user else "",
    }


@app.get("/api/channel")
def channel(request: Request):
    # Identity of the streamer (the channel owner) shown on the home card. Only
    # for signed in viewers, like the rest of the lobby.
    if not read_session(request.cookies.get(COOKIE_NAME, "")):
        return Response(status_code=401)
    info = db.get_stream_info()
    owner = db.channel_owner()
    base = {
        "title": info["stream_title"],
        "description": info["stream_description"],
        "clip_cooldown_user": info["clip_cooldown_user"],
        "clip_cooldown_mod": info["clip_cooldown_mod"],
        "clip_cooldown_admin": info["clip_cooldown_admin"],
    }
    if not owner:
        return {**base, "username": None, "name": "Lazarus Labs", "avatar": 0}
    return {
        **base,
        "username": owner["username"],
        "name": owner["display_name"],
        "avatar": owner["avatar_version"],
    }


@app.post("/api/stream-info")
async def set_stream_info(request: Request):
    # The streamer's title and description for the next/current broadcast. Admin
    # only: this is channel level, not a per viewer setting.
    if not admin_user(request):
        return JSONResponse({"error": "Admins only."}, status_code=403)
    body = await request.json()
    title = None
    if "title" in body:
        title = str(body.get("title") or "").strip()[:MAX_STREAM_TITLE]
        if not title:
            return JSONResponse(
                {"error": "Stream title cannot be empty."}, status_code=400
            )
    description = None
    if "description" in body:
        description = str(body.get("description") or "").strip()[:MAX_STREAM_DESC]

    def clamp_minutes(value):
        # 0 disables the cooldown; cap at a day so a typo can't lock clips forever.
        try:
            return max(0, min(1440, int(value)))
        except (TypeError, ValueError):
            return None

    cd_user = clamp_minutes(body["clip_cooldown_user"]) if "clip_cooldown_user" in body else None
    cd_mod = clamp_minutes(body["clip_cooldown_mod"]) if "clip_cooldown_mod" in body else None
    cd_admin = clamp_minutes(body["clip_cooldown_admin"]) if "clip_cooldown_admin" in body else None

    db.set_stream_info(
        title=title, description=description,
        clip_cooldown_user=cd_user, clip_cooldown_mod=cd_mod,
        clip_cooldown_admin=cd_admin,
    )
    return {"ok": True}


@app.get("/api/verify")
def verify(request: Request):
    # Caddy calls this before serving any video segment. A valid cookie whose
    # account still exists returns 200 and the request continues. Anything else
    # returns 401 and Caddy refuses to serve the video. Checking the account
    # exists (not just that the token is valid) means deleting a user cuts off
    # their video at once, rather than waiting for the token to expire.
    session = read_session(request.cookies.get(COOKIE_NAME, ""))
    if session and db.get_user(session["sub"]):
        return Response(status_code=200)
    return Response(status_code=401)


@app.get("/api/geo")
def geo(request: Request):
    # Caddy calls this for every request. Allow only the configured countries.
    if country_allowed(client_ip(request)):
        return Response(status_code=200)
    return Response(status_code=403)


@app.post("/api/auth")
async def auth(request: Request):
    ip = client_ip(request)
    if too_many_attempts(ip):
        return JSONResponse(
            {"error": "Too many attempts. Wait a minute and try again."},
            status_code=429,
        )

    body = await request.json()
    username = body.get("username", "").strip().lower()
    password = body.get("password", "")

    user = db.get_user(username)
    # Always run a hash check, even for an unknown user, so the response time
    # does not reveal which usernames exist.
    stored = user["password_hash"] if user else db.DUMMY_HASH
    password_ok = db.verify_password(password, stored)
    if not user or not password_ok:
        return JSONResponse({"error": "Wrong username or password."}, status_code=401)

    response = JSONResponse({"ok": True})
    response.set_cookie(
        key=COOKIE_NAME,
        value=issue_token(user),
        max_age=SESSION_HOURS * 3600,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/",
    )
    return response


@app.post("/api/logout")
def logout():
    response = JSONResponse({"ok": True})
    response.delete_cookie(COOKIE_NAME, path="/")
    return response


@app.post("/api/password")
async def change_password(request: Request):
    # Lets a signed in viewer change their own password. They must prove they
    # know the current one, so a borrowed session cannot lock the owner out.
    session = read_session(request.cookies.get(COOKIE_NAME, ""))
    if not session:
        return JSONResponse({"error": "Sign in first."}, status_code=401)
    body = await request.json()
    current = body.get("current_password", "")
    new = body.get("new_password", "")

    user = db.get_user(session["sub"])
    if not user or not db.verify_password(current, user["password_hash"]):
        return JSONResponse(
            {"error": "Your current password is wrong."}, status_code=403
        )
    if len(new) < MIN_PASSWORD:
        return JSONResponse(
            {"error": f"Use at least {MIN_PASSWORD} characters."}, status_code=400
        )
    db.set_password(session["sub"], new)
    return {"ok": True}


# ---- Thumbnail ------------------------------------------------------------

@app.get("/api/thumbnail")
def thumbnail(request: Request):
    # The home card preview. Signed in viewers only, and never cached so the
    # frame stays current. 404 means the stream is offline (no fresh frame).
    if not read_session(request.cookies.get(COOKIE_NAME, "")):
        return Response(status_code=401)
    if not os.path.exists(THUMB_PATH):
        return Response(status_code=404)
    return FileResponse(
        THUMB_PATH,
        media_type="image/jpeg",
        headers={"Cache-Control": "no-store"},
    )


# ---- VODs and clips -------------------------------------------------------
# Metadata and view counting live here; the media files themselves are served
# straight from disk by Caddy at /media/* (behind the same session check as the
# live video), so large files never pass through this Python service.

def _signed_in(request):
    return read_session(request.cookies.get(COOKIE_NAME, "")) is not None


def _media_summary(row, kind):
    """Shape a VOD or clip row for a listing, including whether a poster exists."""
    folder = VOD_DIR if kind == "vod" else CLIP_DIR
    has_poster = bool(row.get("id")) and os.path.exists(
        os.path.join(folder, f"{row['id']}.jpg")
    )
    out = {
        "id": row["id"],
        "filename": row.get("filename"),
        "duration": row.get("duration") or 0,
        "views": row.get("views") or 0,
        "poster": has_poster,
    }
    if kind == "vod":
        out.update(
            title=row["title"], description=row.get("description") or "",
            started_at=row["started_at"],
        )
    else:
        out.update(
            name=row["name"], creator=row.get("creator"),
            created_at=row["created_at"],
        )
    return out


@app.get("/api/vods")
def list_vods(request: Request):
    if not _signed_in(request):
        return Response(status_code=401)
    return {"vods": [_media_summary(v, "vod") for v in db.list_vods()]}


@app.get("/api/clips")
def list_clips(request: Request):
    if not _signed_in(request):
        return Response(status_code=401)
    return {"clips": [_media_summary(c, "clip") for c in db.list_clips()]}


@app.get("/api/vods/{vod_id}")
def get_vod(vod_id: int, request: Request):
    if not _signed_in(request):
        return Response(status_code=401)
    vod = db.get_vod(vod_id)
    if not vod or not vod["ready"]:
        return JSONResponse({"error": "No such VOD."}, status_code=404)
    return _media_summary(vod, "vod")


@app.get("/api/clips/{clip_id}")
def get_clip(clip_id: int, request: Request):
    if not _signed_in(request):
        return Response(status_code=401)
    clip = db.get_clip(clip_id)
    if not clip:
        return JSONResponse({"error": "No such clip."}, status_code=404)
    return _media_summary(clip, "clip")


@app.post("/api/vods/{vod_id}/view")
def view_vod(vod_id: int, request: Request):
    session = read_session(request.cookies.get(COOKIE_NAME, ""))
    if not session:
        return Response(status_code=401)
    if not db.get_vod(vod_id):
        return JSONResponse({"error": "No such VOD."}, status_code=404)
    return {"views": db.add_view("vod", vod_id, session["sub"])}


@app.post("/api/clips/{clip_id}/view")
def view_clip(clip_id: int, request: Request):
    session = read_session(request.cookies.get(COOKIE_NAME, ""))
    if not session:
        return Response(status_code=401)
    if not db.get_clip(clip_id):
        return JSONResponse({"error": "No such clip."}, status_code=404)
    return {"views": db.add_view("clip", clip_id, session["sub"])}


@app.get("/api/vods/{vod_id}/chat")
def vod_chat(vod_id: int, request: Request):
    if not _signed_in(request):
        return Response(status_code=401)
    return {"messages": db.get_replay("vod", vod_id)}


@app.get("/api/clips/{clip_id}/chat")
def clip_chat(clip_id: int, request: Request):
    if not _signed_in(request):
        return Response(status_code=401)
    return {"messages": db.get_replay("clip", clip_id)}


@app.post("/api/clip")
async def create_clip_endpoint(request: Request):
    session = read_session(request.cookies.get(COOKIE_NAME, ""))
    if not session:
        return JSONResponse({"error": "Sign in first."}, status_code=401)
    user = db.get_user(session["sub"])
    if not user:
        return JSONResponse({"error": "Sign in first."}, status_code=401)
    body = await request.json()
    clip_id, error = await make_clip(user, body.get("name"))
    if error:
        return JSONResponse({"error": error}, status_code=400)
    return {"ok": True, "id": clip_id}


@app.delete("/api/vods/{vod_id}")
def delete_vod(vod_id: int, request: Request):
    if not admin_user(request):
        return JSONResponse({"error": "Admins only."}, status_code=403)
    row = db.delete_media("vod", vod_id)
    if not row:
        return JSONResponse({"error": "No such VOD."}, status_code=404)
    _remove_media_files(VOD_DIR, row.get("filename"), vod_id)
    return {"ok": True}


@app.delete("/api/clips/{clip_id}")
def delete_clip(clip_id: int, request: Request):
    if not admin_user(request):
        return JSONResponse({"error": "Admins only."}, status_code=403)
    row = db.delete_media("clip", clip_id)
    if not row:
        return JSONResponse({"error": "No such clip."}, status_code=404)
    _remove_media_files(CLIP_DIR, row.get("filename"), clip_id)
    return {"ok": True}


# ---- Admin ----------------------------------------------------------------

def _clean_username(raw):
    name = (raw or "").strip().lower()
    return name if SAFE_USERNAME.match(name) else None


@app.get("/api/admin/users")
def admin_list(request: Request):
    if not admin_user(request):
        return JSONResponse({"error": "Admins only."}, status_code=403)
    return {"users": db.admin_list_users()}


@app.post("/api/admin/users")
async def admin_create(request: Request):
    if not admin_user(request):
        return JSONResponse({"error": "Admins only."}, status_code=403)
    body = await request.json()
    username = _clean_username(body.get("username"))
    if not username:
        return JSONResponse(
            {"error": "Username may use only a-z, 0-9, dot, dash, underscore."},
            status_code=400,
        )
    if db.get_user(username):
        return JSONResponse(
            {"error": f"User {username} already exists."}, status_code=409
        )
    password = body.get("password", "")
    if len(password) < MIN_PASSWORD:
        return JSONResponse(
            {"error": f"Password needs at least {MIN_PASSWORD} characters."},
            status_code=400,
        )
    display_name = (body.get("display_name") or username).strip()[:MAX_DISPLAY_NAME]
    is_admin = bool(body.get("is_admin"))
    is_moderator = bool(body.get("is_moderator"))
    email = (body.get("email") or "").strip()[:MAX_EMAIL]
    if email and "@" not in email:
        return JSONResponse(
            {"error": "That email address looks invalid."}, status_code=400
        )
    notify_live = bool(body.get("notify_live", True))
    db.add_user(username, display_name, password, is_admin=is_admin, email=email)
    if is_moderator:
        db.update_user(username, is_moderator=True)
    if not notify_live:
        db.set_notify_live(username, False)
    return {"ok": True, "username": username}


@app.patch("/api/admin/users/{username}")
async def admin_update(username: str, request: Request):
    if not admin_user(request):
        return JSONResponse({"error": "Admins only."}, status_code=403)
    username = _clean_username(username)
    target = db.get_user(username) if username else None
    if not target:
        return JSONResponse({"error": "No such user."}, status_code=404)
    body = await request.json()

    display_name = None
    if "display_name" in body:
        display_name = (body.get("display_name") or "").strip()[:MAX_DISPLAY_NAME]
        if not display_name:
            return JSONResponse(
                {"error": "Display name cannot be empty."}, status_code=400
            )

    is_admin = None
    if "is_admin" in body:
        is_admin = bool(body.get("is_admin"))
        # Never let the last admin lose the badge, or the dashboard becomes
        # unreachable for everyone.
        if target["is_admin"] and not is_admin and db.count_admins() <= 1:
            return JSONResponse(
                {"error": "This is the only admin. Promote someone else first."},
                status_code=400,
            )

    is_moderator = None
    if "is_moderator" in body:
        is_moderator = bool(body.get("is_moderator"))

    if display_name is not None or is_admin is not None or is_moderator is not None:
        db.update_user(
            username,
            display_name=display_name,
            is_admin=is_admin,
            is_moderator=is_moderator,
        )
        if is_moderator is not None:
            await hub.update_role(username, mod=is_moderator)

    if "email" in body:
        email = (body.get("email") or "").strip()[:MAX_EMAIL]
        if email and "@" not in email:
            return JSONResponse(
                {"error": "That email address looks invalid."}, status_code=400
            )
        db.set_email(username, email)

    if "notify_live" in body:
        db.set_notify_live(username, bool(body.get("notify_live")))

    if "password" in body:
        password = body.get("password", "")
        if len(password) < MIN_PASSWORD:
            return JSONResponse(
                {"error": f"Password needs at least {MIN_PASSWORD} characters."},
                status_code=400,
            )
        db.set_password(username, password)

    return {"ok": True}


@app.delete("/api/admin/users/{username}")
def admin_delete(username: str, request: Request):
    actor = admin_user(request)
    if not actor:
        return JSONResponse({"error": "Admins only."}, status_code=403)
    username = _clean_username(username)
    target = db.get_user(username) if username else None
    if not target:
        return JSONResponse({"error": "No such user."}, status_code=404)
    if target["is_admin"] and db.count_admins() <= 1:
        return JSONResponse(
            {"error": "You cannot delete the only admin account."}, status_code=400
        )
    db.delete_user(username)
    return {"ok": True}


@app.get("/api/admin/users/{username}/activity")
def admin_activity(username: str, request: Request):
    if not admin_user(request):
        return JSONResponse({"error": "Admins only."}, status_code=403)
    username = _clean_username(username)
    if not username or not db.get_user(username):
        return JSONResponse({"error": "No such user."}, status_code=404)
    return db.user_activity(username)


@app.get("/api/admin/chat")
def admin_chat(request: Request):
    if not admin_user(request):
        return JSONResponse({"error": "Admins only."}, status_code=403)
    return {"messages": db.recent_chat()}


@app.get("/api/admin/notify")
def admin_notify_get(request: Request):
    # Current go-live notification config, plus what is wired up, so the dashboard
    # can show whether email is available and how many people would be emailed.
    if not admin_user(request):
        return JSONResponse({"error": "Admins only."}, status_code=403)
    settings = db.get_notify_settings()
    return {
        "discord_webhook": settings["discord_webhook"],
        "last_notified_at": settings["last_notified_at"],
        "smtp_configured": bool(SMTP_HOST and SMTP_FROM),
        "site_url": SITE_URL,
        "recipients": len(db.list_live_recipients()),
    }


@app.post("/api/admin/notify")
async def admin_notify_set(request: Request):
    if not admin_user(request):
        return JSONResponse({"error": "Admins only."}, status_code=403)
    body = await request.json()
    if "discord_webhook" in body:
        url = (body.get("discord_webhook") or "").strip()
        if url and not url.startswith("https://"):
            return JSONResponse(
                {"error": "A webhook URL must start with https://."}, status_code=400
            )
        db.set_discord_webhook(url)
    if body.get("test"):
        # Fire a one-off announcement now, ignoring the cooldown, so the operator
        # can confirm Discord and email are actually wired up.
        await notify_live(force=True)
        return {"ok": True, "tested": True}
    return {"ok": True}


# ---- Moderator dashboard --------------------------------------------------
# A moderator can review watch and chat history and lift bans they set, but
# cannot add or edit accounts and never sees admin accounts. Admins pass these
# same checks (admin is a superset of mod), so the admin dashboard can reuse the
# ban endpoints; the admin's own listing uses the fuller /api/admin/* routes.

@app.get("/api/mod/users")
def mod_users(request: Request):
    if not mod_actor(request):
        return JSONResponse({"error": "Moderators only."}, status_code=403)
    return {"users": db.admin_list_users(include_admins=False)}


@app.get("/api/mod/users/{username}/activity")
def mod_activity(username: str, request: Request):
    if not mod_actor(request):
        return JSONResponse({"error": "Moderators only."}, status_code=403)
    username = _clean_username(username)
    target = db.get_user(username) if username else None
    if not target:
        return JSONResponse({"error": "No such user."}, status_code=404)
    # Admin accounts are invisible in the moderator area.
    if target["is_admin"]:
        return JSONResponse({"error": "No such user."}, status_code=404)
    return db.user_activity(username)


@app.get("/api/mod/chat")
def mod_chat(request: Request):
    if not mod_actor(request):
        return JSONResponse({"error": "Moderators only."}, status_code=403)
    return {"messages": db.recent_chat(exclude_admins=True)}


@app.get("/api/mod/bans")
def mod_bans(request: Request):
    actor = mod_actor(request)
    if not actor:
        return JSONResponse({"error": "Moderators only."}, status_code=403)
    # Tell the page whether the viewer may lift each ban: an admin may lift any,
    # a moderator only the ones they set.
    bans = db.list_bans()
    is_admin = bool(actor["is_admin"])
    for ban in bans:
        ban["can_lift"] = is_admin or ban["banned_by"] == actor["username"]
    return {"bans": bans, "is_admin": is_admin}


@app.post("/api/mod/unban")
async def mod_unban(request: Request):
    actor = mod_actor(request)
    if not actor:
        return JSONResponse({"error": "Moderators only."}, status_code=403)
    body = await request.json()
    username = _clean_username(body.get("username"))
    existing = db.get_ban(username) if username else None
    if not existing:
        return JSONResponse({"error": "That user is not banned."}, status_code=404)
    if not actor["is_admin"] and existing["banned_by"] != actor["username"]:
        return JSONResponse(
            {"error": "Only the moderator who set this ban, or an admin, can lift it."},
            status_code=403,
        )
    db.remove_ban(username)
    hub.remove_ban_local(username)
    return {"ok": True}


# ---- Avatars --------------------------------------------------------------

def avatar_path(username):
    # basename strips any directory tricks hidden in the name.
    return os.path.join(AVATAR_DIR, os.path.basename(f"{username}.png"))


def crop_to_square(picture):
    width, height = picture.size
    side = min(width, height)
    left = (width - side) // 2
    top = (height - side) // 2
    return picture.crop((left, top, left + side, top + side))


@app.post("/api/avatar")
async def set_avatar(request: Request, image: UploadFile = File(...)):
    session = read_session(request.cookies.get(COOKIE_NAME, ""))
    if not session:
        return JSONResponse({"error": "Sign in first."}, status_code=401)
    username = session["sub"]

    length = request.headers.get("content-length", "")
    if length.isdigit() and int(length) > MAX_AVATAR_BYTES + 4096:
        return JSONResponse(
            {"error": "Image is too large. The limit is 2 MB."}, status_code=413
        )

    raw = await image.read(MAX_AVATAR_BYTES + 1)
    if len(raw) > MAX_AVATAR_BYTES:
        return JSONResponse(
            {"error": "Image is too large. The limit is 2 MB."}, status_code=413
        )

    # Re-encode through Pillow. This proves the upload is really an image and
    # drops any metadata or hidden payload: only clean pixels get written back.
    try:
        picture = Image.open(io.BytesIO(raw))
        picture.load()
    except Exception:
        return JSONResponse({"error": "That file is not an image."}, status_code=400)

    # Honor the photo's EXIF orientation before cropping, so phone uploads are
    # not rotated or flipped.
    picture = ImageOps.exif_transpose(picture)
    square = crop_to_square(picture).convert("RGB").resize(
        (AVATAR_SIZE, AVATAR_SIZE), Image.Resampling.LANCZOS
    )
    square.save(avatar_path(username), format="PNG")
    version = db.bump_avatar_version(username)
    await hub.update_member(username, avatar=version)
    return {"ok": True, "avatar": version}


@app.get("/api/avatar/{username}")
def get_avatar(username: str, request: Request):
    # Avatars appear in chat, so only signed in viewers may load them.
    if not read_session(request.cookies.get(COOKIE_NAME, "")):
        return Response(status_code=401)
    username = username.strip().lower()
    if not SAFE_USERNAME.match(username):
        return Response(status_code=404)
    path = avatar_path(username)
    if not os.path.exists(path):
        return Response(status_code=404)
    return FileResponse(path, media_type="image/png")


# ---- Profiles -------------------------------------------------------------

@app.post("/api/profile")
async def set_profile(request: Request):
    session = read_session(request.cookies.get(COOKIE_NAME, ""))
    if not session:
        return JSONResponse({"error": "Sign in first."}, status_code=401)
    username = session["sub"]
    body = await request.json()

    font = None
    if "font" in body:
        font = str(body.get("font") or "system")
        if font not in ALLOWED_FONTS:
            return JSONResponse({"error": "Unknown font."}, status_code=400)
        db.set_chat_font(username, font)

    bio = None
    if "bio" in body:
        bio = str(body.get("bio") or "").strip()[:MAX_BIO_LENGTH]
        db.set_bio(username, bio)

    if "notify_live" in body:
        db.set_notify_live(username, bool(body.get("notify_live")))

    if "email" in body:
        email = str(body.get("email") or "").strip()[:MAX_EMAIL]
        if email and "@" not in email:
            return JSONResponse(
                {"error": "That email address looks invalid."}, status_code=400
            )
        db.set_email(username, email)

    name = None
    if "display_name" in body:
        name = str(body.get("display_name") or "").strip()[:MAX_DISPLAY_NAME]
        if not name:
            return JSONResponse(
                {"error": "Display name cannot be empty."}, status_code=400
            )
        db.update_user(username, display_name=name)

    # Push font/name changes to the live chat so others see them at once.
    if font is not None or name is not None:
        await hub.update_member(username, font=font, name=name)

    response = JSONResponse({"ok": True, "font": font, "bio": bio, "name": name})
    # The display name is baked into the session token (used for chat and the
    # greeting), so re-issue the cookie when it changes or it would look stale
    # until the next sign in.
    if name is not None:
        user = db.get_user(username)
        response.set_cookie(
            key=COOKIE_NAME,
            value=issue_token(user),
            max_age=SESSION_HOURS * 3600,
            httponly=True,
            secure=True,
            samesite="lax",
            path="/",
        )
    return response


@app.get("/api/profile/{username}")
def get_profile(username: str, request: Request):
    if not read_session(request.cookies.get(COOKIE_NAME, "")):
        return Response(status_code=401)
    username = username.strip().lower()
    if not SAFE_USERNAME.match(username):
        return Response(status_code=404)
    user = db.get_user(username)
    if not user:
        return Response(status_code=404)
    return {
        "username": user["username"],
        "name": user["display_name"],
        "admin": bool(user["is_admin"]),
        "mod": bool(user["is_moderator"]),
        "avatar": user["avatar_version"],
        "bio": user["bio"],
    }


def ready_epoch(ready_time):
    # MediaMTX reports readyTime as an RFC3339 string, with nanosecond precision
    # and a trailing Z, e.g. "2026-06-27T12:34:56.789012345Z". Turn it into a
    # plain Unix timestamp the browser can use to show how long the stream has
    # been live. Anything unparseable returns None, and the page simply omits
    # the duration rather than breaking.
    if not ready_time:
        return None
    text = ready_time.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    # datetime.fromisoformat only accepts 3 or 6 fractional digits, so trim the
    # nanoseconds down to microseconds while leaving any timezone offset intact.
    if "." in text:
        head, _, tail = text.partition(".")
        frac = ""
        rest = ""
        for i, ch in enumerate(tail):
            if ch.isdigit():
                frac += ch
            else:
                rest = tail[i:]
                break
        text = head + "." + frac[:6] + rest
    try:
        when = datetime.fromisoformat(text)
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return int(when.timestamp())


@app.get("/api/status")
async def status():
    # Ask MediaMTX whether a publisher is connected to our stream path, so the
    # player can show an offline card instead of a broken video. When live we
    # also return when the stream started, so the landing page can show how
    # long it has been running.
    data = await fetch_path()
    watching = len(hub.viewers())
    if data and data.get("ready", False):
        return {
            "online": True,
            "since": ready_epoch(data.get("readyTime")),
            "watching": watching,
        }
    return {"online": False, "watching": watching}


@app.websocket("/ws")
async def chat_socket(websocket: WebSocket):
    session = read_session(websocket.cookies.get(COOKIE_NAME, ""))
    if not session:
        await websocket.close(code=4401)
        return

    forwarded = websocket.headers.get("x-forwarded-for", "")
    ws_ip = forwarded.split(",")[0].strip() if forwarded else (
        websocket.client.host if websocket.client else ""
    )
    if not country_allowed(ws_ip):
        await websocket.close(code=4403)
        return

    user = db.get_user(session["sub"])
    # If the account was deleted, the token may still be valid but there is no
    # one to be: refuse the socket rather than seating a ghost in chat.
    if not user:
        await websocket.close(code=4401)
        return
    who = {
        "username": session["sub"],
        "name": user["display_name"],
        "admin": bool(user["is_admin"]),
        "mod": bool(user["is_moderator"]),
        "avatar": user["avatar_version"],
        "font": user["chat_font"],
    }
    await websocket.accept()
    await hub.join(websocket, who)

    sent_times = deque(maxlen=5)
    try:
        while True:
            try:
                data = await websocket.receive_json()
            except WebSocketDisconnect:
                break
            except Exception:
                continue
            if data.get("type") != "chat":
                continue
            text = str(data.get("text", "")).strip()[:MAX_MESSAGE_LENGTH]
            if not text:
                continue
            # A leading slash is a moderation command, handled and answered
            # privately, never shown to other viewers.
            if text.startswith("/"):
                await handle_command(websocket, who, text)
                continue
            # A banned viewer cannot chat at all (a persistent timeout).
            if hub.is_banned(who["username"]):
                await system_reply(websocket, "You are banned from chat.")
                continue
            # A timed-out viewer's messages are dropped, with a private notice.
            remaining = hub.is_timed_out(who["username"])
            if remaining:
                await system_reply(
                    websocket, f"You are timed out for {remaining} more seconds."
                )
                continue
            # Flood guard: drop anything past five messages in three seconds.
            now = time.time()
            sent_times.append(now)
            if len(sent_times) == sent_times.maxlen and now - sent_times[0] < 3:
                continue
            await hub.say(who, text)
    finally:
        await hub.leave(websocket)
