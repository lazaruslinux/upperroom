"""
Admin routes.

The channel's stream title and cooldowns, full account management, chat review,
and go-live notification config. Every route here is admin only; the admin flag
is read fresh from the database on each call.
"""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

import db
from auth import _clean_username, admin_user
from config import (
    MAX_DISPLAY_NAME, MAX_EMAIL, MAX_STREAM_DESC, MAX_STREAM_TITLE, MIN_PASSWORD,
    SITE_URL, SMTP_FROM, SMTP_HOST,
)
from hub import hub
from notify import notify_live

router = APIRouter()


@router.post("/api/stream-info")
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


@router.delete("/api/admin/users/{username}")
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
