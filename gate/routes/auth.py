"""
Auth, profile, and avatar routes.

Signing in, signing out, the session checks Caddy makes before it serves video,
and the self-service profile and avatar endpoints a viewer uses to manage their
own account.
"""

import io
import ipaddress
import os
import secrets
import time

from fastapi import APIRouter, File, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from PIL import Image, ImageOps

import db
from auth import (
    GUEST_REFUSED, _clean_username, client_ip, country_allowed, guest_expired,
    issue_token, member_user, read_session, session_user, too_many_attempts,
    too_many_password_changes,
)
from config import (
    ALLOWED_FONTS, AVATAR_DIR, AVATAR_SIZE, COOKIE_NAME, MAX_AVATAR_BYTES,
    MAX_BIO_LENGTH, MAX_DISPLAY_NAME, MAX_EMAIL, MAX_SITE_NAME, MIN_PASSWORD,
    SAFE_USERNAME, SESSION_HOURS, sanitize_chat_color,
)
from hub import hub

router = APIRouter()

# The networks that may read/record from MediaMTX: loopback and the docker
# private range. Mirrors the old mediamtx `ips` allowlist. The gate's own ffmpeg
# pulls and Caddy's HLS reads all arrive from these, so allowing them keeps
# recording, thumbnails, and playback working.
_INTERNAL_NETS = tuple(
    ipaddress.ip_network(n) for n in ("127.0.0.1/32", "::1/128", "172.16.0.0/12")
)


def _signed_in_response(user, payload=None, max_age=None):
    """A JSON response that also sets the session cookie for `user`, exactly like
    a successful sign in. Used by the login, setup, and register endpoints so all
    three log the account in the moment it is created or authenticated.

    max_age overrides how long the cookie lives, which guest redemption uses so
    the cookie dies with the pass instead of lingering for the normal session
    length. It is belt and braces, not the enforcement: the expiry that matters
    is checked against the row on every /api/verify and on the chat socket, so a
    guest who keeps a cookie alive by hand still stops watching on time."""
    response = JSONResponse(payload or {"ok": True})
    response.set_cookie(
        key=COOKIE_NAME,
        value=issue_token(user),
        max_age=SESSION_HOURS * 3600 if max_age is None else max_age,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/",
    )
    return response


@router.get("/api/me")
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
        "name_color": user["name_color"] if user else "",
        "msg_color": user["msg_color"] if user else "",
        # Read from the row, not the token: the session cookie has no idea what
        # a guest is, which is deliberate. A token minted before guests existed,
        # or one belonging to a deleted row, is simply not a guest.
        "guest": bool(user["is_guest"]) if user else False,
        # Absolute, so the countdown does not drift with a slow page load and
        # does not care about the visitor's clock being wrong by minutes.
        "guest_expires_at": user["guest_expires_at"] if user else 0,
    }


@router.get("/api/channel")
def channel(request: Request):
    # Identity of the streamer (the channel owner) shown on the home card. Only
    # for signed in viewers, like the rest of the lobby.
    if not read_session(request.cookies.get(COOKIE_NAME, "")):
        return Response(status_code=401)
    info = db.get_stream_info()
    schedule = db.get_schedule()
    owner = db.channel_owner()
    base = {
        "site_name": info["site_name"],
        "title": info["stream_title"],
        "description": info["stream_description"],
        "clip_cooldown_user": info["clip_cooldown_user"],
        "clip_cooldown_mod": info["clip_cooldown_mod"],
        "clip_cooldown_admin": info["clip_cooldown_admin"],
        # How much stream a clip captures. The dashboard edits it from here, and
        # the watch page labels its clip button from the copy on /api/status.
        "clip_seconds": info["clip_seconds"],
        "accent": info["accent"],
        # The schedule note only goes to signed-in viewers; the public status
        # endpoint carries the time alone.
        "next_stream_at": schedule["next_stream_at"],
        "next_stream_note": schedule["next_stream_note"],
    }
    if not owner:
        return {**base, "username": None, "name": "upperroom", "avatar": 0}
    return {
        **base,
        "username": owner["username"],
        "name": owner["display_name"],
        "avatar": owner["avatar_version"],
    }


@router.get("/api/verify")
def verify(request: Request):
    # Caddy calls this before serving any video segment. A valid cookie whose
    # account still exists returns 200 and the request continues. Anything else
    # returns 401 and Caddy refuses to serve the video. Checking the account
    # exists (not just that the token is valid) means deleting a user cuts off
    # their video at once, rather than waiting for the token to expire.
    # A guest whose pass has run out is refused here too, which is what actually
    # stops the video: the player's next segment request gets a 401 from Caddy.
    session = read_session(request.cookies.get(COOKIE_NAME, ""))
    if not session:
        return Response(status_code=401)
    user = db.get_user(session["sub"])
    if not user or guest_expired(user):
        return Response(status_code=401)
    return Response(status_code=200)


@router.get("/api/geo")
def geo(request: Request):
    # Caddy calls this for every request. Allow only the configured countries.
    if country_allowed(client_ip(request)):
        return Response(status_code=200)
    return Response(status_code=403)


@router.post("/mtx-auth")
async def mtx_auth(request: Request):
    # MediaMTX delegates every publish/read to the gate over HTTP (authMethod:
    # http). It POSTs one JSON payload per new connection; 2xx allows it, anything
    # else denies. This route is deliberately not under /api/, so Caddy never
    # proxies it: only MediaMTX, inside the docker network, calls it directly.
    body = await request.json()
    action = body.get("action")

    if action == "publish":
        # Only OBS (or the demo publisher) may publish, and only with the current
        # stream key. The user field is ignored: old URLs send user=publisher and
        # keep working. Refuse until a key exists so a blank one never authorizes.
        stored = db.get_stream_key()
        password = str(body.get("password") or "")
        if stored and secrets.compare_digest(password, stored):
            return Response(status_code=200)
        return Response(status_code=401)

    # Every other action (read from Caddy/ffmpeg, the control API) is allowed only
    # from inside the container network. An unparseable IP is denied.
    try:
        ip = ipaddress.ip_address(str(body.get("ip") or ""))
    except ValueError:
        return Response(status_code=401)
    if any(ip in net for net in _INTERNAL_NETS):
        return Response(status_code=200)
    return Response(status_code=401)


@router.post("/api/auth")
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


@router.get("/api/setup")
def setup_status():
    # Public. The login page and the wizard both ask this: the wizard is offered
    # only on a brand new install, before any account exists.
    return {"needs_setup": db.count_users() == 0}


@router.post("/api/setup")
async def setup(request: Request):
    # First-run wizard: create the very first account, as admin, and name the
    # channel. This closes forever the instant any account exists. That server
    # side check is the security boundary, re-run on every call, not merely a hint
    # the page uses to hide itself.
    ip = client_ip(request)
    if too_many_attempts(ip):
        return JSONResponse(
            {"error": "Too many attempts. Wait a minute and try again."},
            status_code=429,
        )
    if db.count_users() > 0:
        return JSONResponse(
            {"error": "Setup is already complete."}, status_code=403
        )

    body = await request.json()
    username = _clean_username(body.get("username"))
    if not username:
        return JSONResponse(
            {"error": "Username may use only a-z, 0-9, dot, dash, underscore."},
            status_code=400,
        )
    password = body.get("password", "")
    if len(password) < MIN_PASSWORD:
        return JSONResponse(
            {"error": f"Password needs at least {MIN_PASSWORD} characters."},
            status_code=400,
        )
    display_name = (body.get("display_name") or username).strip()[:MAX_DISPLAY_NAME]
    # The wizard's one name field is the site name (the operator's brand); older
    # clients sent it as channel_name, so accept either.
    site_name = (body.get("site_name") or body.get("channel_name") or "").strip()[:MAX_SITE_NAME]
    if not site_name:
        return JSONResponse(
            {"error": "Site name cannot be empty."}, status_code=400
        )

    # The insert is guarded to fire only on an empty users table, so even a race
    # between two setup requests can create at most one owner.
    if not db.create_first_user(username, display_name, password, int(time.time())):
        return JSONResponse(
            {"error": "Setup is already complete."}, status_code=403
        )
    db.set_stream_info(site_name=site_name)
    return _signed_in_response(db.get_user(username))


@router.post("/api/register")
async def register(request: Request):
    # Public sign up with a single-use invite code. Creates a viewer account only:
    # an invite can never mint an admin or moderator. No email is involved.
    ip = client_ip(request)
    if too_many_attempts(ip):
        return JSONResponse(
            {"error": "Too many attempts. Wait a minute and try again."},
            status_code=429,
        )

    body = await request.json()
    code = (body.get("code") or "").strip().lower()
    invite = db.get_invite(code) if code else None
    if not invite:
        return JSONResponse(
            {"error": "That invite code is not valid."}, status_code=400
        )
    if invite["revoked_at"]:
        return JSONResponse(
            {"error": "That invite code is no longer valid."}, status_code=400
        )
    if invite["redeemed_at"]:
        return JSONResponse(
            {"error": "That invite code has already been used."}, status_code=400
        )

    username = _clean_username(body.get("username"))
    if not username:
        return JSONResponse(
            {"error": "Username may use only a-z, 0-9, dot, dash, underscore."},
            status_code=400,
        )
    password = body.get("password", "")
    if len(password) < MIN_PASSWORD:
        return JSONResponse(
            {"error": f"Password needs at least {MIN_PASSWORD} characters."},
            status_code=400,
        )
    display_name = (body.get("display_name") or username).strip()[:MAX_DISPLAY_NAME]

    result = db.register_via_invite(
        code, username, display_name, password, int(time.time())
    )
    if result == "user_exists":
        return JSONResponse(
            {"error": f"User {username} already exists."}, status_code=409
        )
    if result != "ok":
        # Lost the race to another redeemer, or the code was just spent.
        return JSONResponse(
            {"error": "That invite code has already been used."}, status_code=409
        )
    return _signed_in_response(db.get_user(username))


@router.post("/api/logout")
def logout():
    response = JSONResponse({"ok": True})
    response.delete_cookie(COOKIE_NAME, path="/")
    return response


@router.post("/api/password")
async def change_password(request: Request):
    # Lets a signed in viewer change their own password. They must prove they
    # know the current one, so a borrowed session cannot lock the owner out.
    if not session_user(request):
        return JSONResponse({"error": "Sign in first."}, status_code=401)
    # A guest row has no usable password hash to change, and no owner to lock
    # out. Refused explicitly rather than left to fail the current-password
    # check, which would read as "you typed it wrong".
    member = member_user(request)
    if not member:
        return JSONResponse({"error": GUEST_REFUSED}, status_code=403)
    username = member["username"]
    # Rate limit per address before the current-password check runs. A valid
    # session is required to reach here, but that is exactly the case worth
    # guarding: a borrowed session should not get unlimited guesses at the
    # current password on its way to setting a new one. Its own budget, so this
    # never touches the login allowance.
    if too_many_password_changes(client_ip(request)):
        return JSONResponse(
            {"error": "Too many attempts. Wait a minute and try again."},
            status_code=429,
        )
    body = await request.json()
    current = body.get("current_password", "")
    new = body.get("new_password", "")

    user = db.get_user(username)
    if not user or not db.verify_password(current, user["password_hash"]):
        return JSONResponse(
            {"error": "Your current password is wrong."}, status_code=403
        )
    if len(new) < MIN_PASSWORD:
        return JSONResponse(
            {"error": f"Use at least {MIN_PASSWORD} characters."}, status_code=400
        )
    db.set_password(username, new)
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


@router.post("/api/avatar")
async def set_avatar(request: Request, image: UploadFile = File(...)):
    if not session_user(request):
        return JSONResponse({"error": "Sign in first."}, status_code=401)
    # An avatar is a file that outlives a thirty minute pass.
    member = member_user(request)
    if not member:
        return JSONResponse({"error": GUEST_REFUSED}, status_code=403)
    username = member["username"]

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


@router.get("/api/avatar/{username}")
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

@router.post("/api/profile")
async def set_profile(request: Request):
    if not session_user(request):
        return JSONResponse({"error": "Sign in first."}, status_code=401)
    # Guests pick their display name once, when they redeem the pass. There is
    # no settings modal behind this for them and nothing here to persist.
    member = member_user(request)
    if not member:
        return JSONResponse({"error": GUEST_REFUSED}, status_code=403)
    username = member["username"]
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

    # Chat colors: each is a validated "#rrggbb" or "" to clear. The guard rejects
    # unreadable or reserved-red picks with a clear message; a rejected color
    # leaves the stored value unchanged.
    name_color = None
    msg_color = None
    try:
        if "name_color" in body:
            name_color = sanitize_chat_color(body.get("name_color"))
        if "msg_color" in body:
            msg_color = sanitize_chat_color(body.get("msg_color"))
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    if name_color is not None or msg_color is not None:
        db.set_chat_colors(username, name_color=name_color, msg_color=msg_color)

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

    # Push font/name/color changes to the live chat so others see them at once.
    if font is not None or name is not None or name_color is not None or msg_color is not None:
        await hub.update_member(
            username, font=font, name=name,
            name_color=name_color, msg_color=msg_color,
        )

    response = JSONResponse({
        "ok": True, "font": font, "bio": bio, "name": name,
        "name_color": name_color, "msg_color": msg_color,
    })
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


@router.get("/api/profile/{username}")
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
        "joined": user["created_at"],
        "points": user["points"],
    }
