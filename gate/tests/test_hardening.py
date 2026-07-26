"""
Abuse resistance for the public surface.

These pin properties that are silent when they break: a limiter that leaks does
not fail any functional test, and an endpoint with no ceiling looks perfectly
healthy until somebody leans on it. The app's only unauthenticated write
endpoint (/api/guest) is what makes any of this load bearing.
"""

import time

import pytest

import auth
from auth import RateLimiter

from test_api import setup_admin


# ---- the limiter must not grow without bound ------------------------------

def test_the_limiter_forgets_addresses_once_their_window_has_passed():
    """It used to keep an entry per address it had ever seen, forever. The
    timestamps inside aged out; the entry did not. Roughly 880 bytes each,
    which is a leak under ordinary traffic and a lever under a flood."""
    limiter = RateLimiter(5, "test")
    past = time.time() - (auth._WINDOW_SECONDS + 10)
    # Fill it past the sweep threshold with addresses that are all long idle.
    for i in range(auth._SWEEP_THRESHOLD + 50):
        limiter._hits[f"198.51.100.{i}"].append(past)
    assert limiter.tracked() > auth._SWEEP_THRESHOLD

    # The next call sweeps them.
    limiter.hit("203.0.113.1")
    assert limiter.tracked() < 10, "idle addresses were not released"


def test_a_busy_address_is_not_swept_out_from_under_itself():
    """The sweep must only drop the idle. Dropping an address mid-window would
    hand an attacker a fresh allowance every time the table filled."""
    limiter = RateLimiter(5, "test")
    # A real attacker, actively hitting it.
    for _ in range(5):
        limiter.hit("203.0.113.99")
    assert limiter.hit("203.0.113.99") is True     # already blocked

    # Now flood the table with idle addresses to force a sweep.
    past = time.time() - (auth._WINDOW_SECONDS + 10)
    for i in range(auth._SWEEP_THRESHOLD + 5):
        limiter._hits[f"198.51.100.{i % 250}.{i}"].append(past)
    limiter.hit("192.0.2.7")

    # The attacker is still blocked; the sweep did not reset them.
    assert limiter.hit("203.0.113.99") is True, "a flood reset an attacker's allowance"


def test_the_limiter_refuses_rather_than_allocating_without_end():
    """A hard ceiling behind the sweep. Refusing is the right failure for a
    flood; the alternative is running out of memory."""
    limiter = RateLimiter(5, "test")
    now = time.time()
    # Fill to the ceiling with addresses that are all live, so the sweep cannot
    # help and only the ceiling is left.
    for i in range(auth._MAX_TRACKED):
        limiter._hits[f"10.{i >> 16 & 255}.{i >> 8 & 255}.{i & 255}"].append(now)
    assert limiter.hit("203.0.113.200") is True
    assert limiter.tracked() <= auth._MAX_TRACKED + 1


# ---- the two budgets are separate -----------------------------------------

def test_fetching_challenges_does_not_spend_the_code_guessing_allowance(client):
    """The guest page asks for a question on load and after every wrong answer.
    If that drew on the login limiter, ordinary use would lock people out and
    the limiter that matters would be exhausted by noise."""
    setup_admin(client)
    ip = {"X-Forwarded-For": "203.0.113.77"}
    for _ in range(20):
        assert client.get("/api/guest/challenge", headers=ip).status_code == 200
    # The login allowance is untouched: a wrong password still gets 401, not 429.
    assert client.post(
        "/api/auth", json={"username": "ghost", "password": "wrong"}, headers=ip
    ).status_code == 401


def test_the_challenge_endpoint_has_a_ceiling_of_its_own(client):
    setup_admin(client)
    ip = {"X-Forwarded-For": "203.0.113.78"}
    codes = {
        client.get("/api/guest/challenge", headers=ip).status_code
        for _ in range(auth._CHALLENGE_LIMITER.max_attempts + 5)
    }
    assert 429 in codes, "challenge issuing has no ceiling at all"


def test_guessing_pass_codes_draws_on_the_same_budget_as_guessing_passwords(client):
    """Otherwise an attacker gets two budgets by alternating endpoints."""
    setup_admin(client)
    ip = {"X-Forwarded-For": "203.0.113.79"}
    for _ in range(5):
        client.post("/api/guest", json={"code": "nope"}, headers=ip)
    assert client.post(
        "/api/auth", json={"username": "ghost", "password": "wrong"}, headers=ip
    ).status_code == 429


# ---- the guest endpoint refuses before it works ---------------------------

def test_the_rate_limit_is_checked_before_any_real_work(client):
    """Order matters: the limiter has to come before the challenge check and the
    database lookup, or a blocked caller still costs an HMAC and a query."""
    import inspect
    import routes.guest as guest_routes
    src = inspect.getsource(guest_routes.redeem_guest)
    assert src.index("too_many_attempts") < src.index("check_challenge")
    assert src.index("check_challenge") < src.index("get_guest_pass")


def test_an_unknown_code_gives_nothing_away(client):
    """The refusal must not distinguish 'no such code' from 'already used', or
    the endpoint becomes an oracle for testing codes."""
    setup_admin(client)
    import db
    used = db.create_guest_pass("x", "owner", int(time.time()))
    db.revoke_guest_pass(used, int(time.time()))
    a = client.post("/api/guest", json={"code": "definitely-not-a-code"})
    b = client.post("/api/guest", json={"code": used})
    # Both fail at the challenge first, which is itself the point: nothing about
    # the code is revealed until a human check has been passed.
    assert a.status_code == b.status_code == 400
    assert a.json()["error"] == b.json()["error"]


# ---- one account cannot take over the room --------------------------------

def test_one_account_is_capped_to_a_few_chat_sockets(client):
    """Joining broadcasts to every open socket, so the cost of one account
    opening sockets is quadratic for everyone else. Measured on the demo stack:
    10 sockets cost 120 frames, 25 cost 683, 50 cost 2,600.

    The cap is what stops a single viewer, or a single thirty minute guest pass,
    from doing that."""
    from config import MAX_SOCKETS_PER_USER
    from starlette.websockets import WebSocketDisconnect

    setup_admin(client, username="owner")
    open_sockets = []
    try:
        for i in range(MAX_SOCKETS_PER_USER):
            ws = client.websocket_connect(
                "/ws", cookies={auth.COOKIE_NAME: client.cookies.get(auth.COOKIE_NAME)}
            ).__enter__()
            ws.receive_json()          # hello
            open_sockets.append(ws)
        # The next one is refused, with a code of its own so the page can tell
        # it apart from being signed out.
        with client.websocket_connect(
            "/ws", cookies={auth.COOKIE_NAME: client.cookies.get(auth.COOKIE_NAME)}
        ) as extra:
            with pytest.raises(WebSocketDisconnect) as excinfo:
                extra.receive_json()
        assert excinfo.value.code == 4429
    finally:
        for ws in open_sockets:
            try:
                ws.__exit__(None, None, None)
            except Exception:
                pass


def test_closing_a_socket_gives_the_slot_back(client):
    """The cap counts what is open now, not what has ever been opened, or a
    viewer who reloads six times would lock themselves out."""
    from config import MAX_SOCKETS_PER_USER
    setup_admin(client, username="owner")
    cookie = {auth.COOKIE_NAME: client.cookies.get(auth.COOKIE_NAME)}
    for _ in range(MAX_SOCKETS_PER_USER + 3):
        with client.websocket_connect("/ws", cookies=cookie) as ws:
            ws.receive_json()
    # All of those closed, so the room is empty again and a fresh one is fine.
    with client.websocket_connect("/ws", cookies=cookie) as ws:
        assert ws.receive_json()["type"] == "hello"


def test_socket_connects_have_their_own_budget(client):
    """Reconnecting after a blip must not spend the password-guessing
    allowance, and vice versa."""
    setup_admin(client, username="owner")
    cookie = {auth.COOKIE_NAME: client.cookies.get(auth.COOKIE_NAME)}
    for _ in range(10):
        with client.websocket_connect("/ws", cookies=cookie) as ws:
            ws.receive_json()
    # The login allowance is untouched.
    assert client.post(
        "/api/auth", json={"username": "ghost", "password": "wrong"}
    ).status_code == 401
