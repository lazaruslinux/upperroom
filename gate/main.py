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
HISTORY_TTL_SECONDS = 60            # chat messages disappear after this long
AVATAR_DIR = os.environ.get("SELFSTREAM_AVATAR_DIR", "/data/avatars")
AVATAR_SIZE = 256                   # avatars are stored as this square, in px
MAX_AVATAR_BYTES = 2 * 1024 * 1024  # reject uploads larger than this
SAFE_USERNAME = re.compile(r"^[a-z0-9_.-]+$")


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
            if was_online and not online:
                await hub.wipe()
            was_online = online
        except Exception:
            pass
        await asyncio.sleep(5)


@asynccontextmanager
async def lifespan(_app):
    watcher = asyncio.create_task(stream_watcher())
    try:
        yield
    finally:
        watcher.cancel()


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
        self._history = deque(maxlen=50)
        self._lock = asyncio.Lock()

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
        # Only replay messages from the last minute, since older ones have
        # already disappeared for everyone else.
        cutoff = int(time.time()) - HISTORY_TTL_SECONDS
        async with self._lock:
            self._sockets[socket] = who
            history = [m for m in self._history if m.get("ts", 0) >= cutoff]
        await socket.send_json({"type": "hello", "you": who, "history": history})
        await self.broadcast(self.presence_message())
        await self.broadcast(
            {"type": "system", "text": f"{who['name']} joined", "ts": int(time.time())}
        )

    async def leave(self, socket):
        who = self._sockets.pop(socket, None)
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
            "text": text,
            "ts": int(time.time()),
        }
        self._history.append(message)
        await self.broadcast(message)

    async def wipe(self):
        # Called when a broadcast ends. Clear the backlog and tell every open
        # page to empty its chat, so the next stream starts fresh.
        async with self._lock:
            self._history.clear()
        await self.broadcast({"type": "wipe"})

    async def update_avatar(self, username, version):
        # A viewer changed their avatar mid-session. Point their open sockets at
        # the new version so their next messages and the watching list show it.
        for who in self._sockets.values():
            if who["username"] == username:
                who["avatar"] = version
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
    await hub.update_avatar(username, version)
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
    if data and data.get("ready", False):
        return {"online": True, "since": ready_epoch(data.get("readyTime"))}
    return {"online": False}


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
