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
import secrets
import time
from collections import defaultdict, deque
from urllib.parse import urlsplit

import geoip2.database
import jwt

import db
from config import (
    ALLOWED_COUNTRIES, COOKIE_NAME, GEO_DB_PATH, JWT_SECRET,
    MAX_SOCKET_CONNECTS, SAFE_USERNAME, SESSION_HOURS, SITE_URL,
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

    One instance per thing being limited, so a viewer reconnecting chat does
    not eat into the allowance that protects password guessing.
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


# Password guessing and invite-code guessing share this one, so an attacker
# cannot get two budgets by alternating between them.
_LOGIN_LIMITER = RateLimiter(5, "login")


# Opening a chat socket. Its own budget again: a viewer reconnecting after a
# network blip must not spend the allowance that protects password guessing.
_SOCKET_LIMITER = RateLimiter(MAX_SOCKET_CONNECTS, "socket")


# Redeeming a highlight. Its own budget: a highlight spends points and posts to
# chat, so it is a write path a stranger should not be able to hammer, but it
# must not draw on the login allowance. Ten a minute is generous for a real
# viewer (a highlight costs roughly fifty minutes of watching, so nobody earns
# ten in a minute) and well below what an abuse loop wants.
_REDEEM_LIMITER = RateLimiter(10, "redeem")


# Changing a password. Its own, tighter budget: the point is to keep a stolen
# session from brute forcing the current-password check the change must clear.
# Five a minute per address caps that guessing regardless of how many valid
# sessions the attacker holds, since the limit is on the address, not the login.
_PASSWORD_LIMITER = RateLimiter(5, "password")


# Posting a comment on a recording or a clip. Its own budget: a comment is a
# write that lands in a thread everyone reads, so a script holding one valid
# session must not be able to fill it, and it must not draw on the login
# allowance. Ten a minute is far past a person typing replies.
_COMMENT_LIMITER = RateLimiter(10, "comment")


# Opening the projector socket. One machine reconnecting after a network blip
# needs a handful; ten a minute covers a backoff cycle and leaves nothing for
# someone guessing the key over that socket.
_PROJECTOR_LIMITER = RateLimiter(10, "projector")


# Turning the go-live push on or off for a device. A person does it a handful of
# times at most; the ceiling keeps a script from churning the table.
_PUSH_LIMITER = RateLimiter(20, "push")

# The dashboard's test push. Each one is a real message to every device the
# admin has, so it gets a small budget of its own.
_PUSH_TEST_LIMITER = RateLimiter(5, "push test")


def too_many_attempts(ip):
    return _LOGIN_LIMITER.hit(ip)


def too_many_socket_connects(ip):
    return _SOCKET_LIMITER.hit(ip)


def too_many_redeems(ip):
    return _REDEEM_LIMITER.hit(ip)


def too_many_password_changes(ip):
    return _PASSWORD_LIMITER.hit(ip)


def too_many_projector_connects(ip):
    return _PROJECTOR_LIMITER.hit(ip)


def too_many_comments(ip):
    return _COMMENT_LIMITER.hit(ip)


def too_many_push_changes(ip):
    return _PUSH_LIMITER.hit(ip)


def too_many_push_tests(ip):
    return _PUSH_TEST_LIMITER.hit(ip)


def reset_limiters():
    """Clear every limiter. For the tests, which share one process: state
    carried between cases makes them order-dependent. They are reset together
    rather than one by one, because adding a fourth limiter and forgetting to
    reset it is exactly the kind of thing that produces a test that passes
    alone and fails in the suite."""
    for limiter in (
        _LOGIN_LIMITER, _SOCKET_LIMITER, _REDEEM_LIMITER,
        _PASSWORD_LIMITER, _PROJECTOR_LIMITER, _COMMENT_LIMITER,
        _PUSH_LIMITER, _PUSH_TEST_LIMITER,
    ):
        limiter.clear()


# Kept for tests that reach in directly.
_ATTEMPTS = _LOGIN_LIMITER._hits


# ---- Sessions -------------------------------------------------------------

def issue_token(user):
    now = int(time.time())
    expires = now + SESSION_HOURS * 3600
    payload = {
        "sub": user["username"],
        "name": user["display_name"],
        "admin": bool(user["is_admin"]),
        "mod": bool(user["is_moderator"]),
        "iat": now,
        "exp": expires,
    }
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")


def key_matches(given, stored):
    """Constant-time compare of a key from a URL against the stored one. As bytes,
    because compare_digest raises on a str holding anything but ASCII, and a
    stranger chooses what is in the URL."""
    return secrets.compare_digest(str(given).encode(), str(stored).encode())


def origin_allowed(headers):
    """Whether a write, or a chat socket, came from this site's own pages.

    The session cookie is SameSite=Lax, which keeps another site's form from
    carrying it. But a page on a sibling subdomain of the same domain counts as
    the same site and gets the cookie anyway, so writes also have to come from
    this exact origin. Browsers name where a request came from in Sec-Fetch-Site
    and Origin. A client that sends neither (curl, the tests, MediaMTX posting
    to /mtx-auth) is not a browser anyone could be tricked into using, and
    passes."""
    if headers.get("sec-fetch-site", "") in ("cross-site", "same-site"):
        return False
    origin = headers.get("origin")
    if origin is None:
        return True
    netloc = urlsplit(origin).netloc.lower() if origin != "null" else ""
    allowed = {headers.get("host", "").lower()}
    if SITE_URL:
        allowed.add(urlsplit(SITE_URL).netloc.lower())
    return bool(netloc) and netloc in allowed


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


def session_user(request):
    """The signed-in account's row, or None.

    None covers both ways a cookie can be worthless: it does not verify, or the
    account behind it is gone. Read fresh from the database on every call, so
    deleting an account signs it out at once."""
    session = read_session(request.cookies.get(COOKIE_NAME, ""))
    if not session:
        return None
    return db.get_user(session["sub"])


def can_moderate(user):
    """Whether a user may run moderator actions. Admin and moderator are separate
    roles, but an admin keeps every moderator power (admin is a superset of mod in
    capability, never in identity, so the badges stay distinct)."""
    return bool(user and (user["is_admin"] or user["is_moderator"]))


def may_act_on(user, owner_username):
    """Whether `user` may edit or remove something `owner_username` made.

    The author always may and an admin always may. A moderator may too, except
    on an admin's things: the same line chat already draws, where a moderator
    cannot delete an admin's message or time an admin out."""
    if not user:
        return False
    if user["username"] == owner_username or user["is_admin"]:
        return True
    if not user["is_moderator"]:
        return False
    owner = db.get_user(owner_username) if owner_username else None
    return not (owner and owner["is_admin"])


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
# The Caddyfile now goes one step further and REPLACES the header on the way to
# the gate with its own resolved client address ({client_ip}), so the gate gets
# exactly one entry. That is what makes a second proxy in front of Caddy work (a
# host-level Caddy on a shared box, say): Caddy trusts it through
# "trusted_proxies static private_ranges", reads the visitor from its header,
# and hands the gate the visitor, where appending would have handed it the
# front proxy's private address and put every visitor on one rate-limit key.
# Note the alternative of walking left past addresses that look like
# infrastructure would be actively wrong here: on a LAN-only install every real
# viewer has a private address, and skipping those would collapse the whole
# house into a single rate-limit key.


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
    if not SAFE_USERNAME.match(name):
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
