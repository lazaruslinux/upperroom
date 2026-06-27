"""
selfstream gate and chat service.

This service is the brains of selfstream. It:

  - serves the public site config the login page needs (the Turnstile sitekey)
  - logs viewers in with a named account plus the Cloudflare Turnstile bot check
  - issues a signed session cookie that lasts a few hours
  - answers the check Caddy makes before it serves any video
  - runs the live chat and the watching list over a WebSocket
  - reports whether the stream is currently live

No accounts are hard coded here and no secrets are written in this file.
Accounts live in a SQLite database that the admin manages with manage.py, and
every sensitive value is read from the environment.
"""

import asyncio
import os
import time
from collections import defaultdict, deque

import geoip2.database
import httpx
import jwt
from fastapi import FastAPI, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

import db

JWT_SECRET = os.environ["SELFSTREAM_JWT_SECRET"]
SESSION_HOURS = int(os.environ.get("SELFSTREAM_SESSION_HOURS", "6"))
TURNSTILE_SITEKEY = os.environ.get("TURNSTILE_SITEKEY", "")
TURNSTILE_SECRET = os.environ.get("TURNSTILE_SECRET", "")
MEDIAMTX_API = os.environ.get("MEDIAMTX_API", "http://mediamtx:9997")
STREAM_PATH = os.environ.get("SELFSTREAM_PATH", "live")
GEO_DB_PATH = os.environ.get("SELFSTREAM_GEO_DB", "/app/dbip-country.mmdb")
ALLOWED_COUNTRIES = {
    c.strip().upper()
    for c in os.environ.get("SELFSTREAM_ALLOWED_COUNTRIES", "US").split(",")
    if c.strip()
}

COOKIE_NAME = "selfstream_session"
TURNSTILE_VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
MAX_MESSAGE_LENGTH = 500

app = FastAPI(title="selfstream", docs_url=None, redoc_url=None)
db.init_db()


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


async def turnstile_ok(token, ip):
    if not TURNSTILE_SECRET:
        # No secret set means the bot check is turned off. This is only for
        # local testing. Always set a secret before going public.
        return True
    # We deliberately do not send remoteip. Behind a shared VPN or proxy the
    # viewer's address can differ between solving the challenge and this
    # request, which makes Cloudflare reject an otherwise valid token.
    data = {"secret": TURNSTILE_SECRET, "response": token}
    async with httpx.AsyncClient(timeout=10) as http:
        reply = await http.post(TURNSTILE_VERIFY_URL, data=data)
    result = reply.json()
    if not result.get("success"):
        print("turnstile verify failed:", result.get("error-codes"),
              "token_len=", len(token or ""), flush=True)
    return result.get("success", False)


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
            seen[who["username"]] = who["name"]
        return [{"username": u, "name": n} for u, n in seen.items()]

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
        async with self._lock:
            self._sockets[socket] = who
            history = list(self._history)
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
            "text": text,
            "ts": int(time.time()),
        }
        self._history.append(message)
        await self.broadcast(message)


hub = Hub()


# ---- HTTP routes ----------------------------------------------------------

@app.get("/api/config")
def config():
    # The sitekey is public by design. It is safe to send to the browser.
    return {"turnstile_sitekey": TURNSTILE_SITEKEY}


@app.get("/api/me")
def me(request: Request):
    session = read_session(request.cookies.get(COOKIE_NAME, ""))
    if not session:
        return {"authed": False}
    return {
        "authed": True,
        "username": session["sub"],
        "name": session["name"],
        "admin": session["admin"],
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
    turnstile_token = body.get("turnstile_token", "")

    if not await turnstile_ok(turnstile_token, ip):
        return JSONResponse(
            {"error": "Bot check failed. Reload the page and try again."},
            status_code=403,
        )

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


@app.get("/api/status")
async def status():
    # Ask MediaMTX whether a publisher is connected to our stream path, so the
    # player can show an offline card instead of a broken video.
    url = f"{MEDIAMTX_API}/v3/paths/get/{STREAM_PATH}"
    try:
        async with httpx.AsyncClient(timeout=5) as http:
            reply = await http.get(url)
        if reply.status_code == 200 and reply.json().get("ready", False):
            return {"online": True}
    except httpx.HTTPError:
        pass
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

    who = {
        "username": session["sub"],
        "name": session["name"],
        "admin": session["admin"],
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
