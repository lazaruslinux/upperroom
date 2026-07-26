"""
Sessions, rate limiting, and the geo gate for the upperroom gate.

This module answers the two questions the rest of the service keeps asking: who
is this request (a valid session cookie whose account still exists), and are
they allowed in at all (login attempt rate and country). No accounts are hard
coded here; identities come from the account database, keyed by the signed
session cookie.
"""

import asyncio
import ipaddress
import logging
import time
from collections import defaultdict, deque

import geoip2.database
import jwt

import db
from config import (
    ALLOWED_COUNTRIES, COOKIE_NAME, GEO_DB_PATH, GUEST_REAP_INTERVAL,
    JWT_SECRET, SAFE_USERNAME, SESSION_HOURS,
)

logger = logging.getLogger("upperroom.auth")


# ---- Rate limiting --------------------------------------------------------
# A short per address history of attempts. Still simple, and it still resets if
# the service restarts, which is fine for a single operator.
#
# The eviction is not incidental. This used to be a bare defaultdict that grew a
# permanent entry per address it ever saw: the timestamps inside each entry
# aged out, but the entry itself never did. Roughly 880 bytes each, never
# returned. That is a slow memory leak under ordinary traffic and a way to push
# a small box over on purpose, so entries whose window has fully passed are now
# swept, and there is a hard ceiling as a backstop.

_WINDOW_SECONDS = 60
# Sweep when the table has grown past this. Well above any real audience, so a
# normal install never pays for the sweep at all.
_SWEEP_THRESHOLD = 2048
# An absolute cap, in case something arrives faster than the sweep can clear.
# Reaching this means the limiter starts refusing everyone, which is the correct
# failure for a flood: the alternative is running out of memory.
_MAX_TRACKED = 20000


class RateLimiter:
    """Per-address attempt counting over a sliding window.

    One instance per thing being limited, so a viewer fetching a new challenge
    question does not eat into the allowance that protects password guessing.
    """

    def __init__(self, max_attempts, name):
        self.max_attempts = max_attempts
        self.name = name
        self._hits = defaultdict(deque)

    def _sweep(self, now):
        """Drop addresses with nothing left inside the window."""
        stale = [
            ip for ip, hits in self._hits.items()
            if not hits or now - hits[-1] > _WINDOW_SECONDS
        ]
        for ip in stale:
            del self._hits[ip]
        if stale:
            logger.debug(
                "%s limiter swept %d idle addresses (%d tracked)",
                self.name, len(stale), len(self._hits),
            )

    def hit(self, ip):
        """Record an attempt from `ip`. True if it should be refused."""
        now = time.time()
        if len(self._hits) >= _SWEEP_THRESHOLD:
            self._sweep(now)
        if ip not in self._hits and len(self._hits) >= _MAX_TRACKED:
            # Under a flood from many addresses, refuse rather than keep
            # allocating. Addresses already being tracked still get their normal
            # allowance, so a real viewer mid-session is not thrown out.
            logger.warning(
                "%s limiter is at its address ceiling (%d); refusing new ones",
                self.name, _MAX_TRACKED,
            )
            return True
        history = self._hits[ip]
        while history and now - history[0] > _WINDOW_SECONDS:
            history.popleft()
        if len(history) >= self.max_attempts:
            return True
        history.append(now)
        return False

    def clear(self):
        self._hits.clear()

    def tracked(self):
        return len(self._hits)


# Password guessing and pass-code guessing share this one, so an attacker cannot
# get two budgets by alternating between them.
_LOGIN_LIMITER = RateLimiter(5, "login")

# Fetching a challenge question is not an attempt at anything, so it gets its own
# and a far higher ceiling: the page asks for one on load and again after every
# wrong answer, and a household behind one address might do that a few times a
# minute quite legitimately. It is limited at all because each one costs a
# signature, and this box has one core.
_CHALLENGE_LIMITER = RateLimiter(60, "challenge")


def too_many_attempts(ip):
    return _LOGIN_LIMITER.hit(ip)


def too_many_challenges(ip):
    return _CHALLENGE_LIMITER.hit(ip)


# The tests reach for this to reset state between cases.
_ATTEMPTS = _LOGIN_LIMITER._hits


# ---- Sessions -------------------------------------------------------------

def issue_token(user):
    now = int(time.time())
    payload = {
        "sub": user["username"],
        "name": user["display_name"],
        "admin": bool(user["is_admin"]),
        "mod": bool(user["is_moderator"]),
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


def mod_actor(request):
    """The signed in user's row if they may use the moderator dashboard (admin or
    moderator), read fresh from the database. None otherwise."""
    session = read_session(request.cookies.get(COOKIE_NAME, ""))
    if not session:
        return None
    user = db.get_user(session["sub"])
    if not user or not (user["is_admin"] or user["is_moderator"]):
        return None
    return user


def guest_expired(user, now=None):
    """Whether this row is a guest whose time is up.

    Read from the row, never from the session cookie, for the same reason
    is_admin is: the cookie outlives the decision. This runs on /api/verify,
    which Caddy calls once per video segment, so it stays a comparison on data
    the caller has already loaded rather than anything that touches the database
    again."""
    if not user or not user["is_guest"]:
        return False
    expires = user["guest_expires_at"] or 0
    return expires > 0 and expires <= (time.time() if now is None else now)


# What a guest is told when they reach something that needs an account. One
# wording, in one place, so every refusal reads the same.
GUEST_REFUSED = "Guests can watch and chat. Sign in or use an invite code to do that."


def session_user(request):
    """The signed-in account's row, or None.

    None covers all three ways a cookie can be worthless: it does not verify, the
    account behind it is gone, or it is a guest whose pass has run out. Routes
    get expiry enforcement by using this rather than reading the session
    themselves, which is the point of having it."""
    session = read_session(request.cookies.get(COOKIE_NAME, ""))
    if not session:
        return None
    user = db.get_user(session["sub"])
    if not user or guest_expired(user):
        return None
    return user


def member_user(request):
    """As session_user, but None for guests as well.

    The line this draws: a guest may watch and chat, because those are the two
    things the pass is for. Everything that leaves a mark on the channel or on
    an account (clipping, points, the library, editing a profile) needs an
    account somebody actually owns. is_guest is read from the row, never from
    the session cookie, exactly as is_admin is."""
    user = session_user(request)
    if not user or user["is_guest"]:
        return None
    return user


def can_moderate(user):
    """Whether a user may run moderator actions. Admin and moderator are separate
    roles, but an admin keeps every moderator power (admin is a superset of mod in
    capability, never in identity, so the badges stay distinct)."""
    return bool(user and (user["is_admin"] or user["is_moderator"]))


# ---- Caller address -------------------------------------------------------
# X-Forwarded-For is a list the caller gets to start writing, and Caddy appends
# the address it actually saw rather than replacing what arrived. A request sent
# with "X-Forwarded-For: 1.2.3.4" therefore reaches the gate as
# "1.2.3.4, <the real caller>". Reading the left-most entry reads the value the
# caller chose, which let anyone pick their own address and walk past both the
# login rate limiter and the country gate. That was the bug.
#
# The right-most entry is the one Caddy wrote itself, so it is the only entry
# nobody upstream could have forged. Take exactly that, and never look further
# left: everything to the left arrived from outside and is decoration.
#
# This trusts one hop, which is the topology this ships with (caller -> Caddy ->
# gate) and the one the Caddyfile's "trusted_proxies static private_ranges"
# describes. Note the alternative of walking left past addresses that look like
# infrastructure would be actively wrong here: on a LAN-only install every real
# viewer has a private address, and skipping those would collapse the whole
# house into a single rate-limit key. If you put another proxy in front of
# Caddy, this needs to skip that many extra entries, and the Caddyfile needs to
# trust it too.


def resolve_client_ip(forwarded, peer):
    """The caller's address, given the X-Forwarded-For header and the socket peer.

    Returns the right-most X-Forwarded-For entry, which is the address our own
    proxy observed. Falls back to the socket peer when the header is absent or
    unusable, which on the compose network means the request reached the gate
    without crossing Caddy at all."""
    for entry in reversed([e.strip() for e in (forwarded or "").split(",")]):
        if not entry:
            continue
        try:
            ipaddress.ip_address(entry)
        except ValueError:
            # Caddy writes a bare address here. Anything else did not come from
            # Caddy, so stop rather than reading further left into whatever the
            # caller supplied.
            break
        return entry
    return peer or "unknown"


def client_ip(request):
    return resolve_client_ip(
        request.headers.get("X-Forwarded-For", ""),
        request.client.host if request.client else "",
    )


# Guest accounts are named guest_<random>. Reserve the prefix so a member can
# never register a name that reads as a guest in chat, or vice versa.
GUEST_USERNAME_PREFIX = "guest_"


def _clean_username(raw):
    name = (raw or "").strip().lower()
    if not SAFE_USERNAME.match(name):
        return None
    if name.startswith(GUEST_USERNAME_PREFIX):
        return None
    return name


# Geo lookup. The database is baked into the image at build time. If it is
# missing, or an address is not in it, we allow the request, so a database
# problem can never lock everyone out of the stream.
try:
    _geo_reader = geoip2.database.Reader(GEO_DB_PATH)
except Exception as exc:
    # Logged once, here at startup, rather than on every request the geo gate
    # then waves through.
    _geo_reader = None
    logger.warning(
        "geo database unavailable at %s (%r); the country gate is open",
        GEO_DB_PATH, exc,
    )


async def guest_reaper():
    """Delete guest accounts whose passes have run out, on a timer.

    Expiry is already enforced on every request, so this is housekeeping rather
    than a gate: without it the users table would keep a row per guest who ever
    visited. It reuses db.delete_user(), which already clears watch sessions,
    chat log and bans, so a reaped guest leaves nothing orphaned behind.

    It also closes the guest's chat socket. That is the part that is not merely
    tidying: a guest sitting in chat when their time runs out would otherwise
    keep the socket open indefinitely, since the connect-time check only catches
    guests who arrive already expired.
    """
    # Imported here rather than at module scope: hub imports nothing from auth
    # today, and a module level import would make that a cycle waiting to
    # happen the first time it does.
    from hub import hub

    while True:
        try:
            now = int(time.time())
            for username in db.expired_guests(now):
                await hub.disconnect_user(username)
                if db.delete_user(username):
                    logger.info("guest account expired and removed: %s", username)
        except Exception:
            # A failed sweep must not kill the worker; the next one retries.
            logger.warning("guest reaper pass failed", exc_info=True)
        await asyncio.sleep(GUEST_REAP_INTERVAL)


def country_allowed(ip):
    if not _geo_reader or not ALLOWED_COUNTRIES:
        return True
    try:
        code = _geo_reader.country(ip).country.iso_code
    except Exception:
        # An address not in the database (or an odd lookup) is allowed through,
        # same as before; this is a chatty per-request path, so debug only.
        logger.debug("geo lookup miss for %s", ip)
        return True
    return code in ALLOWED_COUNTRIES
