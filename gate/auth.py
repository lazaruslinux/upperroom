"""
Sessions, rate limiting, and the geo gate for the upperroom gate.

This module answers the two questions the rest of the service keeps asking: who
is this request (a valid session cookie whose account still exists), and are
they allowed in at all (login attempt rate and country). No accounts are hard
coded here; identities come from the account database, keyed by the signed
session cookie.
"""

import ipaddress
import logging
import time
from collections import defaultdict, deque

import geoip2.database
import jwt

import db
from config import (
    ALLOWED_COUNTRIES, COOKIE_NAME, GEO_DB_PATH, JWT_SECRET, SAFE_USERNAME,
    SESSION_HOURS,
)

logger = logging.getLogger("upperroom.auth")


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


def _clean_username(raw):
    name = (raw or "").strip().lower()
    return name if SAFE_USERNAME.match(name) else None


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
