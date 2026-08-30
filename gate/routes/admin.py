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
    GUEST_MINUTES, MAX_BANNED_WORDS_LEN, MAX_DISPLAY_NAME, MAX_EMAIL,
    MAX_GAME_NAME, MAX_GUEST_PASS_BATCH, MAX_INVITE_LABEL, MAX_OVERLAY_TICKER,
    MAX_SCHEDULE_NOTE, MAX_SITE_NAME, MAX_SLOW_SECONDS, MAX_STREAM_DESC,
    MAX_STREAM_TITLE, MAX_VIEWER_LIMIT, MIN_PASSWORD, SITE_URL, SMTP_FROM,
    SMTP_HOST,
)
import watchers
from hub import hub
from media import (
    enforce_retention, fetch_path, media_usage, ready_epoch, recording_status,
)
from notify import notify_live

router = APIRouter()


def _clean_ticker(raw):
    """Reduce an overlay ticker to a single line of safe plain text: control
    characters (newlines, tabs, and the like) become single spaces so pasted
    multi-line text keeps its word breaks instead of gluing words together, runs
    of whitespace collapse to one space, then trimmed and length capped. The
    overlay renders it with textContent, so this is about shape, not escaping."""
    text = "".join(
        ch if ord(ch) >= 32 and ord(ch) != 127 else " " for ch in str(raw or "")
    )
    return " ".join(text.split())[:MAX_OVERLAY_TICKER]


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

    accent = None
    if "accent" in body:
        accent = str(body.get("accent") or "")
        if accent not in db.ACCENTS:
            return JSONResponse(
                {"error": "Unknown accent flavor."}, status_code=400
            )

    db.set_stream_info(
        site_name=site_name, title=title, description=description, accent=accent,
    )
    # What is being played rides this route too, but is stored on its own: an
    # empty string is a real value here (it clears the label), which is exactly
    # what the fields above refuse, so it cannot share their None-means-unset
    # handling.
    if "game" in body:
        db.set_now_playing(str(body.get("game") or "").strip()[:MAX_GAME_NAME])
    # So does the viewer limit, and for the same reason: 0 is a real value here
    # (it turns the limit off), which is exactly what the text fields above
    # refuse, so it cannot share their None-means-unset handling.
    if "max_viewers" in body:
        try:
            limit = max(0, min(MAX_VIEWER_LIMIT, int(body["max_viewers"])))
        except (TypeError, ValueError):
            return JSONResponse(
                {"error": "Viewer limit must be a whole number."}, status_code=400
            )
        db.set_max_viewers(limit)
    # The overlay ticker rides this channel-settings route too, but is saved and
    # pushed separately: cleaned to a single safe line, stored, then broadcast to
    # the overlay sockets ONLY so a live source updates without a reconnect. It is
    # never returned by /api/status or any public payload.
    if "overlay_ticker" in body:
        ticker = _clean_ticker(body.get("overlay_ticker"))
        db.set_overlay_ticker(ticker)
        await hub.send_overlays({"type": "ticker", "text": ticker})
    return {"ok": True}


@router.get("/api/admin/stream")
async def admin_stream(request: Request):
    # The live broadcast at a glance for the dashboard's stream strip: whether it
    # is live, since when, how many are watching, and whether the recorder is
    # healthy. It polls MediaMTX and parses readyTime exactly as /api/status does,
    # reusing the same helpers so the dashboard and the watch page can never
    # disagree about "live". The point is that a streamer never has to read the
    # container logs to know their broadcast is being recorded.
    if not admin_user(request):
        return JSONResponse({"error": "Admins only."}, status_code=403)
    data = await fetch_path()
    live = bool(data and data.get("ready", False))
    return {
        "live": live,
        "since": ready_epoch(data.get("readyTime")) if live else None,
        "watching": len(hub.viewers()),
        "recording": recording_status(),
        # What the broadcast is costing. MediaMTX counts the bytes it has sent
        # since the path went live, so this is tonight rather than all time,
        # which is the number a streamer actually wants. watchers.count() is
        # people pulling video, which is not the same as people in chat.
        "sent_bytes": int((data or {}).get("bytesSent") or 0) if live else 0,
        "video_watchers": watchers.count(),
        "max_viewers": db.get_max_viewers(),
        # The console's Playing row rides this poll rather than a second
        # endpoint: it is dashboard-only state and this already runs every ten
        # seconds while the page is open.
        "game": db.get_now_playing(),
        "recent_games": db.recent_games(),
    }


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


@router.get("/api/admin/activity")
def admin_activity_over_time(request: Request, days: int = 30):
    # Daily buckets for the analytics charts: watch minutes, unique viewers, and
    # chat messages per day. Admin only, like the rest of /api/admin/*. The chat
    # series only goes back as far as the chat retention keeps rows, which the
    # page labels honestly rather than pretending older days were silent.
    if not admin_user(request):
        return JSONResponse({"error": "Admins only."}, status_code=403)
    return {"days": db.activity_by_day(days)}


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
    """Revoke an active invite. Note this only stamps revoked_at and keeps the
    row; removing it is the separate route below, and deliberately so."""
    if not admin_user(request):
        return JSONResponse({"error": "Admins only."}, status_code=403)
    if not db.revoke_invite(code.strip().lower(), int(time.time())):
        return JSONResponse(
            {"error": "That invite is not active."}, status_code=400
        )
    return {"ok": True}


@router.post("/api/admin/invites/{code}/remove")
def admin_invites_remove(code: str, request: Request):
    """Delete a spent invite row for good. Only a revoked or redeemed code goes:
    an active one has to be revoked first, so removing can never quietly
    un-issue a code somebody is holding."""
    if not admin_user(request):
        return JSONResponse({"error": "Admins only."}, status_code=403)
    if not db.delete_invite(code.strip().lower()):
        return JSONResponse(
            {"error": "Revoke that invite before removing it."}, status_code=400
        )
    return {"ok": True}


@router.post("/api/admin/invites/clear-used")
def admin_invites_clear_used(request: Request):
    """Sweep every redeemed and revoked invite at once, which is the thing that
    actually gets asked for: they pile up and there was no way to clear them."""
    if not admin_user(request):
        return JSONResponse({"error": "Admins only."}, status_code=403)
    return {"ok": True, "removed": db.clear_used_invites()}


# ---- Guest passes ---------------------------------------------------------
# The same shape as invites above, deliberately: he generates a handful, pastes
# them into a group text, and each person redeems one. Unlike invites, these are
# built with a real delete from the start rather than revoke-only, because the
# complaint about invites piling up applies here twice over: a pass is spent
# within the hour and its row has nothing to say afterwards.

@router.get("/api/admin/guest-passes")
def admin_guest_passes_list(request: Request):
    if not admin_user(request):
        return JSONResponse({"error": "Admins only."}, status_code=403)
    return {
        "passes": db.list_guest_passes(),
        "minutes": GUEST_MINUTES,
        "now": int(time.time()),
    }


@router.post("/api/admin/guest-passes")
async def admin_guest_passes_create(request: Request):
    actor = admin_user(request)
    if not actor:
        return JSONResponse({"error": "Admins only."}, status_code=403)
    body = await request.json()
    label = (body.get("label") or "").strip()[:MAX_INVITE_LABEL]
    # Making several at once is the actual workflow: one text message, one code
    # each. Capped so a slip on the number field cannot mint thousands.
    try:
        count = int(body.get("count") or 1)
    except (TypeError, ValueError):
        count = 1
    count = max(1, min(count, MAX_GUEST_PASS_BATCH))
    now = int(time.time())
    codes = [
        db.create_guest_pass(label, actor["username"], now) for _ in range(count)
    ]
    return {"ok": True, "codes": codes}


@router.delete("/api/admin/guest-passes/{code}")
def admin_guest_passes_revoke(code: str, request: Request):
    """Revoke an unused pass. Spent passes are removed with the route below;
    this one only ever stops a code that could still be redeemed."""
    if not admin_user(request):
        return JSONResponse({"error": "Admins only."}, status_code=403)
    if not db.revoke_guest_pass(code.strip().lower(), int(time.time())):
        return JSONResponse(
            {"error": "That guest pass is not active."}, status_code=400
        )
    return {"ok": True}


@router.post("/api/admin/guest-passes/{code}/remove")
def admin_guest_passes_remove(code: str, request: Request):
    """Delete a spent pass row for good. Only revoked or redeemed passes go: an
    active code has to be revoked first, so removing can never be a quiet way to
    un-issue a code somebody is still holding."""
    if not admin_user(request):
        return JSONResponse({"error": "Admins only."}, status_code=403)
    if not db.delete_guest_pass(code.strip().lower()):
        return JSONResponse(
            {"error": "Revoke that pass before removing it."}, status_code=400
        )
    return {"ok": True}


@router.post("/api/admin/guest-passes/clear-used")
def admin_guest_passes_clear_used(request: Request):
    """Sweep every redeemed and revoked pass at once, which is the thing that
    actually gets asked for once a few broadcasts have gone by."""
    if not admin_user(request):
        return JSONResponse({"error": "Admins only."}, status_code=403)
    return {"ok": True, "removed": db.clear_used_guest_passes()}


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
    # The ticker rides back here (admin only) so the dashboard's overlay panel can
    # show the current message; it is never on a public endpoint.
    return {"key": key, "ticker": db.get_overlay_ticker()}


@router.post("/api/admin/overlay/regenerate")
def admin_overlay_regenerate(request: Request):
    if not admin_user(request):
        return JSONResponse({"error": "Admins only."}, status_code=403)
    return {"key": db.regenerate_overlay_key()}


# The synthetic events the test-fire buttons send. Each is clearly labelled as a
# test and carries no real account: the point is to let an operator confirm their
# OBS browser source is wired up without waiting for a real viewer to act. They go
# to the overlay sockets only (hub.send_overlays), so a test can never appear in
# real chat or the chat log.
def _test_overlay_event(kind, now):
    if kind == "chat":
        return {
            "type": "chat", "id": None, "user": "test", "name": "test",
            "admin": False, "mod": False, "avatar": 0,
            "name_color": "", "msg_color": "",
            "text": "This is a test chat line.", "ts": now,
        }
    if kind == "join":
        # The overlay surfaces a join from a system line ending in " joined".
        return {"type": "system", "text": "test joined", "ts": now}
    if kind == "clip":
        return {"type": "clip", "by": "test", "name": "a test clip"}
    if kind == "highlight":
        return {
            "type": "highlight", "id": None, "user": "test", "name": "test",
            "admin": False, "mod": False, "avatar": 0, "name_color": "",
            "message": "This is a test highlight.", "cost": 0, "ts": now,
        }
    if kind == "ticker":
        return {"type": "ticker", "text": "This is a test ticker message."}
    return None


@router.post("/api/admin/overlay/test")
async def admin_overlay_test(request: Request):
    # Fire one synthetic event at the overlay so the operator can see their OBS
    # browser source is working. Admin only, like the rest of /api/admin/*.
    if not admin_user(request):
        return JSONResponse({"error": "Admins only."}, status_code=403)
    body = await request.json()
    kind = str(body.get("kind") or "")
    event = _test_overlay_event(kind, int(time.time()))
    if event is None:
        return JSONResponse(
            {"error": "Unknown test kind."}, status_code=400
        )
    # Overlay sockets only: never a real chat socket, never the chat log.
    await hub.send_overlays(event)
    return {"ok": True, "kind": kind}


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
        "email_on_live": bool(settings["email_on_live"]),
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
    if "email_on_live" in body:
        db.set_email_on_live(bool(body.get("email_on_live")))
    if body.get("test"):
        # Fire a one-off announcement now, ignoring the cooldown, so the operator
        # can confirm Discord and email are actually wired up.
        await notify_live(force=True)
        return {"ok": True, "tested": True}
    return {"ok": True}
