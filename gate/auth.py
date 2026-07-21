"""
Sessions, rate limiting, and the geo gate for the upperroom gate.

This module answers the two questions the rest of the service keeps asking: who
is this request (a valid session cookie whose account still exists), and are
they allowed in at all (login attempt rate and country). No accounts are hard
coded here; identities come from the account database, keyed by the signed
session cookie.
"""

import time
from collections import defaultdict, deque

import geoip2.database
import jwt

import db
from config import (
    ALLOWED_COUNTRIES, COOKIE_NAME, GEO_DB_PATH, JWT_SECRET, SAFE_USERNAME,
    SESSION_HOURS,
)


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


def client_ip(request):
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _clean_username(raw):
    name = (raw or "").strip().lower()
    return name if SAFE_USERNAME.match(name) else None


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
