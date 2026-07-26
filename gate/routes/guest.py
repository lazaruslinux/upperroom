"""
Guest passes: watching and chatting for a while without an account.

This is the only public, unauthenticated write endpoint in the app, so it is
also the only place where per-address rate limiting is load bearing rather than
a courtesy. Everything else behind /api needs a session first.

Redeeming a pass creates a real users row flagged is_guest with an expiry, not a
parallel rowless identity. That choice is what lets the rest of the app stay
unchanged: presence, watch sessions, the chat socket and above all every
moderator command resolve their target through a users row, so a guest can be
timed out, banned, /del'ed and /purge'd exactly like anyone else. A guest who
could talk in chat and could not be moderated would be a worse feature than no
guests at all.
"""

import logging
import secrets
import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

import db
from auth import (
    GUEST_USERNAME_PREFIX, client_ip, country_allowed, too_many_attempts,
)
from challenge import check_challenge, new_challenge
from config import GUEST_MINUTES, MAX_GUEST_NAME

from .auth import _signed_in_response

logger = logging.getLogger("upperroom.guest")

router = APIRouter()


def _guest_username():
    """A username no member could already hold and no member could later take.

    The prefix is reserved by the registration path, and the suffix is random
    rather than sequential so the number of guests who have ever visited is not
    on show in chat. Satisfies SAFE_USERNAME (lowercase, digits)."""
    return f"{GUEST_USERNAME_PREFIX}{secrets.token_hex(4)}"


@router.get("/api/guest/challenge")
def guest_challenge(request: Request):
    """Issue a question for the guest form. Public, and cheap on purpose: it
    creates no server-side state, so hammering it costs a signature and nothing
    else. The country gate still applies, so somewhere we do not serve cannot
    even collect a question."""
    if not country_allowed(client_ip(request)):
        return JSONResponse(
            {"error": "This channel is not available in your area."},
            status_code=403,
        )
    question, token = new_challenge()
    return {"question": question, "token": token}


@router.post("/api/guest")
async def redeem_guest(request: Request):
    """Redeem a single-use guest pass and sign the visitor in as a guest."""
    ip = client_ip(request)
    if not country_allowed(ip):
        return JSONResponse(
            {"error": "This channel is not available in your area."},
            status_code=403,
        )
    # Shares the login limiter, so guessing codes here and guessing passwords at
    # /api/auth draw down one allowance per address rather than two.
    if too_many_attempts(ip):
        return JSONResponse(
            {"error": "Too many attempts. Wait a minute and try again."},
            status_code=429,
        )

    body = await request.json()

    # The challenge is checked before the code is even looked up, so a script
    # that cannot answer it never gets to test whether a code exists.
    if not check_challenge(body.get("challenge_token"), body.get("challenge")):
        return JSONResponse(
            {"error": "That answer was not right. Here is a new question."},
            status_code=400,
        )

    code = (body.get("code") or "").strip().lower()
    display_name = (body.get("name") or "").strip()[:MAX_GUEST_NAME]
    if not display_name:
        return JSONResponse(
            {"error": "Enter the name you want to appear as in chat."},
            status_code=400,
        )

    pass_row = db.get_guest_pass(code) if code else None
    # One message for every kind of no. Saying which codes exist would turn this
    # endpoint into a way to test codes one at a time.
    if not pass_row or pass_row["revoked_at"] or pass_row["redeemed_at"]:
        return JSONResponse(
            {"error": "That guest pass is not valid."}, status_code=400
        )

    now = int(time.time())
    expires_at = now + GUEST_MINUTES * 60
    username = _guest_username()
    result = db.redeem_guest_pass(code, username, display_name, now, expires_at)
    if result != "ok":
        # Either somebody else claimed the code first, or the random username
        # collided. Both are "try again", and neither burned the pass.
        return JSONResponse(
            {"error": "That guest pass is not valid."}, status_code=400
        )

    logger.info(
        "guest pass redeemed: code=%s as %s (%s), expires in %d min",
        code, username, display_name, GUEST_MINUTES,
    )
    user = db.get_user(username)
    return _signed_in_response(
        user,
        payload={"ok": True, "expires_at": expires_at},
        max_age=expires_at - now,
    )
