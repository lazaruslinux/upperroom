"""
Channel points and the highlight redemption.

Viewers earn points by watching the stream live (the stream watcher credits them;
see media.py) and spend them on the one built-in redemption: highlighting a short
message on stream. There is no catalog and no admin configuration. The viewer's
balance and the redeem action both live here.
"""

import logging
import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

import db
import wordfilter
from auth import (
    GUEST_REFUSED, client_ip, member_user, session_user, too_many_redeems,
)
from config import HIGHLIGHT_COST, MAX_MESSAGE_LENGTH
from hub import hub

logger = logging.getLogger("upperroom.points")

router = APIRouter()


@router.get("/api/points")
def points(request: Request):
    # Any signed-in viewer: their own balance and the fixed highlight cost, so the
    # watch page can show the points chip and drive the highlight composer.
    # Guests never accrue points, so they have no balance to show and no
    # highlight to buy. Refused rather than shown as zero, which would read as a
    # thing they could earn.
    if not session_user(request):
        return JSONResponse({"error": "Sign in first."}, status_code=401)
    user = member_user(request)
    if not user:
        return JSONResponse({"error": GUEST_REFUSED}, status_code=403)
    return {"points": db.get_points(user["username"]), "cost": HIGHLIGHT_COST}


@router.post("/api/redeem")
async def redeem(request: Request):
    if not session_user(request):
        return JSONResponse({"error": "Sign in first."}, status_code=401)
    user = member_user(request)
    if not user:
        return JSONResponse({"error": GUEST_REFUSED}, status_code=403)
    username = user["username"]
    # A highlight spends points and posts to chat, so it is a write path and gets
    # its own per-address rate limit, checked before any work so a flood cannot
    # lean on the spend and broadcast machinery. Its own budget, not the login
    # one, so highlighting never eats the allowance that protects sign in.
    if too_many_redeems(client_ip(request)):
        return JSONResponse(
            {"error": "Too many highlights. Wait a minute and try again."},
            status_code=429,
        )
    body = await request.json()
    # The highlight text follows the same length rule the chat socket enforces:
    # strip, then truncate to the chat limit. An empty message after stripping is
    # nothing to highlight.
    message = str(body.get("message", "")).strip()[:MAX_MESSAGE_LENGTH]
    if not message:
        return JSONResponse({"error": "Say something first."}, status_code=400)
    # A highlight is a chat message with a spotlight, so it obeys the same
    # moderation the chat send path does: a banned or timed-out viewer cannot post
    # one. Reuse the hub's own checks (and their wording) rather than reimplement.
    if hub.is_banned(username):
        return JSONResponse(
            {"error": "You are banned from chat."}, status_code=403
        )
    remaining = hub.is_timed_out(username)
    if remaining:
        return JSONResponse(
            {"error": f"You are timed out for {remaining} more seconds."},
            status_code=403,
        )
    # A highlight runs through the same word filter chat does, read fresh so an
    # admin's change takes effect at once. Without this a highlight would be a
    # paid way around the filter chat enforces.
    settings = db.get_chat_moderation()
    if wordfilter.contains_banned(message.lower(), settings.get("banned_words", "")):
        return JSONResponse(
            {"error": "Your message was blocked by the word filter."},
            status_code=400,
        )
    # The spend is atomic: the balance is deducted only if it covers the cost, so
    # two redemptions racing on a balance that covers one can never both win.
    balance = db.spend_points(username, HIGHLIGHT_COST)
    if balance is None:
        return JSONResponse({"error": "Not enough points."}, status_code=400)
    user = db.get_user(username)
    display_name = user["display_name"] if user else username
    ts = int(time.time())
    # Log the highlight to the admin chat history and reuse the row id as the
    # message id, exactly as a normal chat line does (hub.chat calls db.log_chat
    # and carries the id back). A highlight is a chat message with a spotlight,
    # so it belongs in the log the admin reviews, and giving it an id is what
    # lets a moderator delete one: the hover-delete, /delete and /purge paths all
    # match on that id or on "user". Best effort: a logging failure must never
    # undo the spend that already went through, so the id is simply absent then.
    msg_id = None
    try:
        msg_id = db.log_chat(username, display_name, message, ts)
    except Exception:
        logger.debug("log_chat for highlight failed", exc_info=True)
    # Announce the highlight to every watch page and the overlay, and keep it in
    # the backlog so a viewer joining later still sees it. Best effort: a
    # broadcast failure must never undo a spend that already went through. The
    # payload carries the sender's identity in the same shape a chat line does,
    # so a highlight can render with their avatar, name, color, and role.
    try:
        await hub.highlight({
            "type": "highlight",
            "id": msg_id,
            "user": username,
            "name": display_name,
            "admin": bool(user["is_admin"]) if user else False,
            "mod": bool(user["is_moderator"]) if user else False,
            "avatar": user["avatar_version"] if user else 0,
            "name_color": user["name_color"] if user else "",
            "message": message,
            "cost": HIGHLIGHT_COST,
            "ts": ts,
        })
    except Exception:
        logger.debug("highlight broadcast failed", exc_info=True)
    return {"ok": True, "points": balance}
