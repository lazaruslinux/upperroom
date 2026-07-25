"""
Admin routes.

The channel's stream title and cooldowns, full account management, chat review,
and go-live notification config. Every route here is admin only; the admin flag
is read fresh from the database on each call.
"""

import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

import db
from auth import _clean_username, admin_user
from config import (
    MAX_BANNED_WORDS_LEN, MAX_DISPLAY_NAME, MAX_EMAIL, MAX_INVITE_LABEL,
    MAX_SCHEDULE_NOTE, MAX_SITE_NAME, MAX_SLOW_SECONDS, MAX_STREAM_DESC,
    MAX_STREAM_TITLE, MIN_PASSWORD, SITE_URL, SMTP_FROM, SMTP_HOST,
)
from hub import hub
from media import enforce_retention, media_usage
from notify import notify_live

router = APIRouter()


@router.post("/api/stream-info")
async def set_stream_info(request: Request):
    # The streamer's title and description for the next/current broadcast. Admin
    # only: this is channel level, not a per viewer setting.
    if not admin_user(request):
        return JSONResponse({"error": "Admins only."}, status_code=403)
    body = await request.json()
    site_name = None
    if "site_name" in body:
        site_name = str(body.get("site_name") or "").strip()[:MAX_SITE_NAME]
        if not site_name:
            return JSONResponse(
                {"error": "Site name cannot be empty."}, status_code=400
            )
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

    accent = None
    if "accent" in body:
        accent = str(body.get("accent") or "")
        if accent not in db.ACCENTS:
            return JSONResponse(
                {"error": "Unknown accent flavor."}, status_code=400
            )

    db.set_stream_info(
        site_name=site_name, title=title, description=description,
        clip_cooldown_user=cd_user, clip_cooldown_mod=cd_mod,
        clip_cooldown_admin=cd_admin, accent=accent,
    )
    return {"ok": True}


@router.get("/api/admin/moderation")
def get_moderation(request: Request):
    # The chat moderation settings for the admin panel. Kept off the public
    # endpoints so the banned-words list is never exposed to viewers.
    if not admin_user(request):
        return JSONResponse({"error": "Admins only."}, status_code=403)
    return db.get_chat_moderation()


@router.post("/api/admin/moderation")
async def set_moderation(request: Request):
    if not admin_user(request):
        return JSONResponse({"error": "Admins only."}, status_code=403)
    body = await request.json()
    slow = None
    if "slow_mode_seconds" in body:
        try:
            slow = max(0, min(MAX_SLOW_SECONDS, int(body["slow_mode_seconds"])))
        except (TypeError, ValueError):
            return JSONResponse(
                {"error": "Slow mode must be a whole number of seconds."},
                status_code=400,
            )
    words = None
    if "banned_words" in body:
        words = str(body.get("banned_words") or "")[:MAX_BANNED_WORDS_LEN]
    db.set_chat_moderation(slow_mode_seconds=slow, banned_words=words)
    return {"ok": True}


@router.get("/api/admin/schedule")
def get_schedule(request: Request):
    if not admin_user(request):
        return JSONResponse({"error": "Admins only."}, status_code=403)
    return db.get_schedule()


@router.post("/api/admin/schedule")
async def set_schedule(request: Request):
    # The next broadcast: a time (epoch seconds, sent by the browser from a
    # local date field) and a short note. 0 clears it.
    if not admin_user(request):
        return JSONResponse({"error": "Admins only."}, status_code=403)
    body = await request.json()
    try:
        when = int(body.get("next_stream_at") or 0)
    except (TypeError, ValueError):
        return JSONResponse({"error": "That is not a valid time."}, status_code=400)
    now = int(time.time())
    if when:
        # A little slack backwards, so setting "in five minutes" cannot fail on
        # a clock skew, but not so much that a mistyped year sticks around.
        if when < now - 3600:
            return JSONResponse(
                {"error": "That time has already passed."}, status_code=400
            )
        if when > now + 365 * 86400:
            return JSONResponse(
                {"error": "Schedule something within the next year."}, status_code=400
            )
    note = str(body.get("next_stream_note") or "").strip()[:MAX_SCHEDULE_NOTE]
    db.set_schedule(when, note)
    return {"ok": True, **db.get_schedule()}


# Ceilings on the retention limits, so a typo cannot ask for something absurd.
# 0 always means "no limit on this axis", which is how retention stays off.
_RETENTION_MAX = {
    "vod_keep_count": 10000,
    "vod_keep_days": 3650,
    "clip_keep_count": 10000,
    "clip_keep_days": 3650,
    "media_cap_gb": 1000000,
}


def _retention_payload():
    return {
        **db.get_retention(),
        "usage": media_usage(),
        "counts": db.count_media(),
    }


@router.get("/api/admin/retention")
def get_retention(request: Request):
    # The retention limits plus what the media store is actually using, so the
    # dashboard can show the numbers and their effect in one place.
    if not admin_user(request):
        return JSONResponse({"error": "Admins only."}, status_code=403)
    return _retention_payload()


@router.post("/api/admin/retention")
async def set_retention(request: Request):
    if not admin_user(request):
        return JSONResponse({"error": "Admins only."}, status_code=403)
    body = await request.json()
    limits = {}
    for field, ceiling in _RETENTION_MAX.items():
        if field not in body:
            continue
        raw = body[field]
        # Strict about what counts as a whole number: rounding 1.5 down to 1
        # would quietly keep less than the operator asked for, and this setting
        # deletes recordings.
        if isinstance(raw, bool) or (isinstance(raw, float) and not raw.is_integer()):
            return JSONResponse(
                {"error": "Retention limits must be whole numbers."}, status_code=400
            )
        try:
            value = int(raw)
        except (TypeError, ValueError):
            return JSONResponse(
                {"error": "Retention limits must be whole numbers."}, status_code=400
            )
        if value < 0:
            return JSONResponse(
                {"error": "Retention limits cannot be negative."}, status_code=400
            )
        limits[field] = min(ceiling, value)
    db.set_retention(**limits)
    # Apply the new limits at once, so lowering one takes effect on save rather
    # than at the next sweep, and the response can report what that cost.
    removed = await enforce_retention()
    return {"ok": True, "removed": removed, **_retention_payload()}


@router.get("/api/admin/users")
def admin_list(request: Request):
    if not admin_user(request):
        return JSONResponse({"error": "Admins only."}, status_code=403)
    return {"users": db.admin_list_users()}


@router.post("/api/admin/users")
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
    notify_live_opt = bool(body.get("notify_live", True))
    db.add_user(username, display_name, password, is_admin=is_admin, email=email)
    if is_moderator:
        db.update_user(username, is_moderator=True)
    if not notify_live_opt:
        db.set_notify_live(username, False)
    return {"ok": True, "username": username}


@router.patch("/api/admin/users/{username}")
async def admin_update(username: str, request: Request):
    if not admin_user(request):
        return JSONResponse({"error": "Admins only."}, status_code=403)
    username = _clean_username(username)
    target = db.get_user(username) if username else None
    if not target:
        return JSONResponse({"error": "No such user."}, status_code=404)
    body = await request.json()

    # An admin picks the starting display name when creating an account, and
    # that is the end of it: from then on the name belongs to the person, who
    # changes it from their own settings. Refused rather than ignored, so a
    # caller is never told a rename succeeded when it did not.
    if "display_name" in body:
        return JSONResponse(
            {"error": "Only the account holder can change their display name."},
            status_code=403,
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

    if is_admin is not None or is_moderator is not None:
        db.update_user(
            username,
            display_name=None,
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


@router.delete("/api/admin/users/{username}")
def admin_delete(username: str, request: Request, confirm: str = ""):
    # Deleting takes the account, its watch history and its chat, and there is
    # no undo. The caller has to echo the username back in ?confirm=, so the
    # dashboard's "type the username" step is enforced here and not only in the
    # browser, and a stray DELETE can never land on anyone.
    actor = admin_user(request)
    if not actor:
        return JSONResponse({"error": "Admins only."}, status_code=403)
    username = _clean_username(username)
    target = db.get_user(username) if username else None
    if not target:
        return JSONResponse({"error": "No such user."}, status_code=404)
    if _clean_username(confirm) != username:
        return JSONResponse(
            {"error": "Type the username to confirm the deletion."}, status_code=400
        )
    if target["is_admin"] and db.count_admins() <= 1:
        return JSONResponse(
            {"error": "You cannot delete the only admin account."}, status_code=400
        )
    db.delete_user(username)
    return {"ok": True}


@router.get("/api/admin/users/{username}/activity")
def admin_activity(username: str, request: Request):
    if not admin_user(request):
        return JSONResponse({"error": "Admins only."}, status_code=403)
    username = _clean_username(username)
    if not username or not db.get_user(username):
        return JSONResponse({"error": "No such user."}, status_code=404)
    return db.user_activity(username)


@router.get("/api/admin/chat")
def admin_chat(request: Request):
    if not admin_user(request):
        return JSONResponse({"error": "Admins only."}, status_code=403)
    return {"messages": db.recent_chat()}


# ---- Invites --------------------------------------------------------------

@router.get("/api/admin/invites")
def admin_invites_list(request: Request):
    if not admin_user(request):
        return JSONResponse({"error": "Admins only."}, status_code=403)
    return {"invites": db.list_invites()}


@router.post("/api/admin/invites")
async def admin_invites_create(request: Request):
    actor = admin_user(request)
    if not actor:
        return JSONResponse({"error": "Admins only."}, status_code=403)
    body = await request.json()
    label = (body.get("label") or "").strip()[:MAX_INVITE_LABEL]
    code = db.create_invite(label, actor["username"], int(time.time()))
    return {"ok": True, "code": code}


@router.delete("/api/admin/invites/{code}")
def admin_invites_revoke(code: str, request: Request):
    if not admin_user(request):
        return JSONResponse({"error": "Admins only."}, status_code=403)
    if not db.revoke_invite(code.strip().lower(), int(time.time())):
        return JSONResponse(
            {"error": "That invite is not active."}, status_code=400
        )
    return {"ok": True}


# ---- Overlay key ----------------------------------------------------------

@router.get("/api/admin/overlay")
def admin_overlay_get(request: Request):
    # The bearer key for the OBS chat overlay. Generated on first read so the
    # panel always has a URL to show; regenerate to revoke the old one.
    if not admin_user(request):
        return JSONResponse({"error": "Admins only."}, status_code=403)
    key = db.get_overlay_key()
    if not key:
        key = db.regenerate_overlay_key()
    return {"key": key}


@router.post("/api/admin/overlay/regenerate")
def admin_overlay_regenerate(request: Request):
    if not admin_user(request):
        return JSONResponse({"error": "Admins only."}, status_code=403)
    return {"key": db.regenerate_overlay_key()}


# ---- Stream key -----------------------------------------------------------

@router.get("/api/admin/stream-key")
def admin_stream_key_get(request: Request):
    # The RTMP publish key OBS uses to go live. Generated on first read so the
    # panel always has a key to show (a fresh install needs no PUBLISH_PASS);
    # regenerate to rotate it. Rotation applies from the next connect only.
    if not admin_user(request):
        return JSONResponse({"error": "Admins only."}, status_code=403)
    key = db.get_stream_key()
    if not key:
        key = db.regenerate_stream_key()
    return {"key": key}


@router.post("/api/admin/stream-key/regenerate")
def admin_stream_key_regenerate(request: Request):
    if not admin_user(request):
        return JSONResponse({"error": "Admins only."}, status_code=403)
    return {"key": db.regenerate_stream_key()}


@router.get("/api/admin/notify")
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


@router.post("/api/admin/notify")
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
