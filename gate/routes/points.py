"""
Channel points and the highlight redemption.

Viewers earn points by watching the stream live (the stream watcher credits them;
see media.py) and spend them on the one built-in redemption: highlighting a short
message on stream. There is no catalog and no admin configuration. The viewer's
balance and the redeem action both live here.
"""

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

import db
from auth import GUEST_REFUSED, member_user, session_user
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
    # The spend is atomic: the balance is deducted only if it covers the cost, so
    # two redemptions racing on a balance that covers one can never both win.
    balance = db.spend_points(username, HIGHLIGHT_COST)
    if balance is None:
        return JSONResponse({"detail": "not enough points"}, status_code=400)
    # Announce the highlight to every watch page and the overlay, the same hub
    # broadcast path the old redeem event took. Best effort: a broadcast failure
    # must never undo a spend that already went through.
    user = db.get_user(username)
    try:
        await hub.broadcast({
            "type": "highlight",
            "user": user["display_name"] if user else username,
            "message": message,
            "cost": HIGHLIGHT_COST,
        })
    except Exception:
        logger.debug("highlight broadcast failed", exc_info=True)
    return {"ok": True, "points": balance}
