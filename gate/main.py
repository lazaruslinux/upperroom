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
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from datetime import datetime, timezone

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
            if was_online and not online:
                await hub.wipe()
            was_online = online
        except Exception:
            pass
        await asyncio.sleep(5)


async def capture_thumbnail():
    """Pull one frame from the live stream into THUMB_PATH. Best effort."""
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y", "-loglevel", "error",
        "-rw_timeout", "5000000",          # give up on a stalled read after 5s
        "-i", RTMP_SOURCE,
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
os.makedirs(AVATAR_DIR, exist_ok=True)


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
        self._sockets = {}            # websocket -> {"username", "name", "admin"}
        self._history = deque(maxlen=CHAT_HISTORY)
        self._lock = asyncio.Lock()
        self._live = False            # whether the stream is currently live

    def viewers(self):
        # One entry per person, even if they have several tabs open.
        seen = {}
        for who in self._sockets.values():
            seen[who["username"]] = who
        return [
            {"username": w["username"], "name": w["name"], "avatar": w.get("avatar", 0)}
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
        message = {
            "type": "chat",
            "user": who["username"],
            "name": who["name"],
            "admin": who["admin"],
            "avatar": who.get("avatar", 0),
            "font": who.get("font", "system"),
            "text": text,
            "ts": int(time.time()),
        }
        self._history.append(message)
        # Keep an admin-only copy on disk. Viewers still see chat as ephemeral;
        # this log is purged after the retention window (see chat_purge_worker).
        try:
            db.log_chat(who["username"], who["name"], text, message["ts"])
        except Exception:
            pass
        await self.broadcast(message)

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
        "admin": session["admin"],
        "avatar": user["avatar_version"] if user else 0,
        "font": user["chat_font"] if user else "system",
        "bio": user["bio"] if user else "",
    }


@app.get("/api/channel")
def channel(request: Request):
    # Identity of the streamer (the channel owner) shown on the home card. Only
    # for signed in viewers, like the rest of the lobby.
    if not read_session(request.cookies.get(COOKIE_NAME, "")):
        return Response(status_code=401)
    owner = db.channel_owner()
    if not owner:
        return {"username": None, "name": "Lazarus Labs", "avatar": 0}
    return {
        "username": owner["username"],
        "name": owner["display_name"],
        "avatar": owner["avatar_version"],
    }


@app.get("/api/verify")
def verify(request: Request):
    # Caddy calls this before serving any video segment. A valid cookie returns
    # 200 and the request continues. Anything else returns 401 and Caddy refuses
    # to serve the video.
    if read_session(request.cookies.get(COOKIE_NAME, "")):
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
    db.add_user(username, display_name, password, is_admin=is_admin)
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

    if display_name is not None or is_admin is not None:
        db.update_user(username, display_name=display_name, is_admin=is_admin)

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
    who = {
        "username": session["sub"],
        "name": session["name"],
        "admin": session["admin"],
        "avatar": user["avatar_version"] if user else 0,
        "font": user["chat_font"] if user else "system",
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
            # Flood guard: drop anything past five messages in three seconds.
            now = time.time()
            sent_times.append(now)
            if len(sent_times) == sent_times.maxlen and now - sent_times[0] < 3:
                continue
            await hub.say(who, text)
    finally:
        await hub.leave(websocket)
