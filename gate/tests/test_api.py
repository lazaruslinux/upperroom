"""
HTTP and WebSocket tests for the upperroom gate.

These drive the assembled FastAPI app through a TestClient, one throwaway SQLite
database per test (see conftest.py). They cover the security-relevant surface:
who may sign in, the first-run setup gate, invite registration, the admin and
moderator gates, session integrity when an account changes underneath a live
cookie, and the chat moderation commands over the WebSocket.

Network and disk side effects are avoided by construction: the app's background
workers live in its lifespan, which these tests never enter, and no handler
covered here reaches MediaMTX or ffmpeg.
"""

import time

import pytest
from starlette.websockets import WebSocketDisconnect

import auth
import db
from config import COOKIE_NAME
from conftest import make_client
from hub import hub


# ---- helpers --------------------------------------------------------------

def setup_admin(client, username="owner", password="password1", channel="My Channel"):
    """Run the first-run wizard through the real endpoint; leaves `client` signed
    in as the new admin."""
    resp = client.post(
        "/api/setup",
        json={"username": username, "password": password, "channel_name": channel},
    )
    assert resp.status_code == 200
    return resp


def add_user(username, password="password1", is_admin=False, is_moderator=False):
    db.add_user(username, username.title(), password, is_admin=is_admin)
    if is_moderator:
        db.update_user(username, is_moderator=True)


def login(client, username, password="password1", ip="10.0.0.9"):
    resp = client.post(
        "/api/auth",
        json={"username": username, "password": password},
        headers={"X-Forwarded-For": ip},
    )
    assert resp.status_code == 200
    return resp


def ws_connect(client):
    """Open the chat socket, sending the client's session cookie explicitly (the
    test websocket transport does not carry the secure cookie jar on its own)."""
    token = client.cookies.get(COOKIE_NAME)
    return client.websocket_connect("/ws", cookies={COOKIE_NAME: token})


def drain_join(ws):
    """Consume the three frames every socket gets on join: hello, the presence
    list, and the 'joined' system line."""
    for _ in range(3):
        ws.receive_json()


def recv_system(ws):
    """Return the text of the next system frame, skipping any presence frames a
    command's role update may broadcast first."""
    while True:
        frame = ws.receive_json()
        if frame.get("type") == "system":
            return frame["text"]


# ---- 1. Auth and the login rate limiter -----------------------------------

def test_login_success_sets_cookie(client):
    add_user("viewer")
    resp = login(client, "viewer")
    assert resp.json() == {"ok": True}
    assert COOKIE_NAME in resp.cookies


def test_login_wrong_password_is_rejected_without_cookie(client):
    add_user("viewer")
    resp = client.post("/api/auth", json={"username": "viewer", "password": "nope-nope-nope"})
    assert resp.status_code == 401
    assert COOKIE_NAME not in resp.cookies


def test_login_unknown_user_is_rejected(client):
    resp = client.post("/api/auth", json={"username": "ghost", "password": "password1"})
    assert resp.status_code == 401
    assert COOKIE_NAME not in resp.cookies


def test_rate_limiter_is_shared_across_auth_setup_and_register(client):
    # Five failed logins from one address exhaust that address's allowance.
    ip = "203.0.113.7"
    for _ in range(5):
        r = client.post(
            "/api/auth",
            json={"username": "ghost", "password": "wrong"},
            headers={"X-Forwarded-For": ip},
        )
        assert r.status_code == 401
    # The sixth request is blocked, and the same limiter guards setup and
    # register: both refuse the same address without doing their own work.
    for path, body in (
        ("/api/auth", {"username": "ghost", "password": "wrong"}),
        ("/api/setup", {"username": "owner", "password": "password1", "channel_name": "c"}),
        ("/api/register", {"code": "x", "username": "u", "password": "password1"}),
    ):
        blocked = client.post(path, json=body, headers={"X-Forwarded-For": ip})
        assert blocked.status_code == 429
    # A different address is unaffected (proving it is per-address, not global).
    other = client.post(
        "/api/register",
        json={"code": "not-a-code", "username": "u", "password": "password1"},
        headers={"X-Forwarded-For": "203.0.113.8"},
    )
    assert other.status_code == 400


# ---- 2. First-run setup gate ----------------------------------------------

def test_setup_status_flips_once_an_account_exists(client):
    assert client.get("/api/setup").json() == {"needs_setup": True}
    setup_admin(client)
    assert client.get("/api/setup").json() == {"needs_setup": False}


def test_setup_creates_admin_names_channel_and_signs_in(client):
    resp = setup_admin(client, username="owner", channel="Room One")
    assert COOKIE_NAME in resp.cookies
    row = db.get_user("owner")
    assert row["is_admin"] == 1
    assert db.get_stream_info()["stream_title"] == "Room One"
    # The returned cookie is a working session straight away.
    assert client.get("/api/verify").status_code == 200


def test_setup_is_closed_once_any_user_exists(client):
    add_user("someone")
    resp = client.post(
        "/api/setup",
        json={"username": "owner", "password": "password1", "channel_name": "c"},
    )
    assert resp.status_code == 403
    assert db.get_user("owner") is None


@pytest.mark.parametrize(
    "body",
    [
        {"username": "Bad Name", "password": "password1", "channel_name": "c"},
        {"username": "owner", "password": "short", "channel_name": "c"},
        {"username": "owner", "password": "password1", "channel_name": "  "},
    ],
    ids=["bad-username", "short-password", "empty-channel"],
)
def test_setup_rejects_invalid_input(client, body):
    resp = client.post("/api/setup", json=body)
    assert resp.status_code == 400
    assert db.count_users() == 0


# ---- 3. Invites and invite registration -----------------------------------

def test_admin_can_create_list_and_revoke_invites(client):
    setup_admin(client)
    created = client.post("/api/admin/invites", json={"label": "for a friend"})
    assert created.status_code == 200
    code = created.json()["code"]
    listed = client.get("/api/admin/invites").json()["invites"]
    assert any(i["code"] == code for i in listed)
    revoked = client.request("DELETE", f"/api/admin/invites/{code}")
    assert revoked.status_code == 200
    # A second revoke reports no change, confirming the first one landed.
    assert client.request("DELETE", f"/api/admin/invites/{code}").status_code == 400


def test_non_admin_is_blocked_from_every_invite_route(client):
    setup_admin(client)
    code = client.post("/api/admin/invites", json={}).json()["code"]
    viewer = make_client()
    add_user("viewer")
    login(viewer, "viewer")
    assert viewer.get("/api/admin/invites").status_code == 403
    assert viewer.post("/api/admin/invites", json={}).status_code == 403
    assert viewer.request("DELETE", f"/api/admin/invites/{code}").status_code == 403
    # And an anonymous caller is refused too.
    anon = make_client()
    assert anon.get("/api/admin/invites").status_code == 403


def test_register_with_valid_code_creates_a_signed_in_viewer(client):
    setup_admin(client)
    code = client.post("/api/admin/invites", json={}).json()["code"]
    guest = make_client()
    resp = guest.post(
        "/api/register",
        json={"code": code, "username": "grandma", "password": "password1"},
    )
    assert resp.status_code == 200
    assert COOKIE_NAME in resp.cookies
    row = db.get_user("grandma")
    assert row["is_admin"] == 0 and row["is_moderator"] == 0   # viewer only
    assert row["invite_code"] == code                          # provenance stamped
    assert guest.get("/api/verify").status_code == 200


@pytest.mark.parametrize("state", ["invalid", "revoked", "redeemed"])
def test_register_rejects_unusable_codes(client, state):
    setup_admin(client)
    if state == "invalid":
        code = "not-a-real-code"
    else:
        code = client.post("/api/admin/invites", json={}).json()["code"]
    if state == "revoked":
        client.request("DELETE", f"/api/admin/invites/{code}")
    if state == "redeemed":
        assert make_client().post(
            "/api/register",
            json={"code": code, "username": "first", "password": "password1"},
        ).status_code == 200
    resp = make_client().post(
        "/api/register",
        json={"code": code, "username": "second", "password": "password1"},
    )
    assert resp.status_code == 400
    assert db.get_user("second") is None


def test_register_taken_username_conflicts_and_keeps_code_unredeemed(client):
    setup_admin(client)
    add_user("taken")
    code = client.post("/api/admin/invites", json={}).json()["code"]
    resp = make_client().post(
        "/api/register",
        json={"code": code, "username": "taken", "password": "password1"},
    )
    assert resp.status_code == 409
    # The code was not burned by the failed claim, so it is still redeemable.
    assert db.get_invite(code)["redeemed_at"] is None


# ---- 4. Admin and moderator gates -----------------------------------------

@pytest.mark.parametrize(
    "method,path,body",
    [
        ("GET", "/api/admin/users", None),
        ("POST", "/api/stream-info", {"title": "x"}),
        ("GET", "/api/admin/chat", None),
        ("GET", "/api/admin/notify", None),
        ("GET", "/api/admin/overlay", None),
        ("POST", "/api/admin/overlay/regenerate", None),
        ("GET", "/api/admin/stream-key", None),
        ("POST", "/api/admin/stream-key/regenerate", None),
    ],
)
def test_viewer_is_refused_admin_endpoints(client, method, path, body):
    setup_admin(client)          # a real admin exists, so 403 is about the caller
    viewer = make_client()
    add_user("viewer")
    login(viewer, "viewer")
    resp = viewer.request(method, path, json=body)
    assert resp.status_code == 403


def test_only_admin_cannot_be_deleted_or_demoted(client):
    setup_admin(client, username="owner")
    demote = client.patch("/api/admin/users/owner", json={"is_admin": False})
    assert demote.status_code == 400
    assert db.get_user("owner")["is_admin"] == 1
    delete = client.request("DELETE", "/api/admin/users/owner")
    assert delete.status_code == 400
    assert db.get_user("owner") is not None


def test_mod_dashboard_hides_admin_accounts(client):
    setup_admin(client, username="owner")            # an admin
    add_user("mod1", is_moderator=True)
    add_user("viewer")
    mod = make_client()
    login(mod, "mod1")
    usernames = [u["username"] for u in mod.get("/api/mod/users").json()["users"]]
    assert "viewer" in usernames
    assert "owner" not in usernames                  # admins are invisible here
    # And an admin cannot be inspected through the moderator area either.
    assert mod.get("/api/mod/users/owner/activity").status_code == 404


# ---- 5. Session integrity against a fresh role check ----------------------

def test_deleted_users_cookie_stops_verifying(client):
    setup_admin(client, username="owner")
    add_user("viewer")
    viewer = make_client()
    login(viewer, "viewer")
    assert viewer.get("/api/verify").status_code == 200
    db.delete_user("viewer")
    # The cookie still decodes, but the account is gone, so video access is cut.
    assert viewer.get("/api/verify").status_code == 401


def test_revoked_admin_loses_admin_endpoints_at_once(client):
    setup_admin(client, username="owner")
    add_user("second", is_admin=True)
    second = make_client()
    login(second, "second")
    assert second.get("/api/admin/users").status_code == 200
    db.update_user("second", is_admin=False)
    # No re-login: the admin flag is read fresh from the database each call.
    assert second.get("/api/admin/users").status_code == 403


# ---- 6. Self-service profile ----------------------------------------------

def test_profile_reports_join_time(client):
    setup_admin(client, username="owner")
    add_user("viewer")
    login(client, "owner")
    body = client.get("/api/profile/viewer").json()
    assert body["username"] == "viewer"
    assert body["joined"] == db.get_user("viewer")["created_at"]


def test_profile_enforces_bio_and_font_limits(client):
    setup_admin(client, username="owner")
    # A valid font is accepted.
    assert client.post("/api/profile", json={"font": "mono"}).status_code == 200
    assert db.get_user("owner")["chat_font"] == "mono"
    # An unknown font is rejected.
    assert client.post("/api/profile", json={"font": "wingdings"}).status_code == 400
    # An over-long bio is clamped to the configured limit, not stored whole.
    from config import MAX_BIO_LENGTH
    client.post("/api/profile", json={"bio": "a" * (MAX_BIO_LENGTH + 50)})
    assert len(db.get_user("owner")["bio"]) == MAX_BIO_LENGTH


# ---- 7. Chat moderation over the WebSocket --------------------------------

def test_ws_refuses_an_unauthenticated_socket(client):
    with pytest.raises(WebSocketDisconnect) as excinfo:
        with client.websocket_connect("/ws"):
            pass
    assert excinfo.value.code == 4401


def test_ws_admin_promotes_a_viewer_with_mod_command(client):
    setup_admin(client, username="owner")
    add_user("viewer")
    with ws_connect(client) as ws:
        drain_join(ws)
        ws.send_json({"type": "chat", "text": "/mod viewer"})
        reply = recv_system(ws)
    assert "moderator" in reply.lower()
    assert db.get_user("viewer")["is_moderator"] == 1


def test_ws_viewer_cannot_run_a_mod_command(client):
    setup_admin(client, username="owner")
    add_user("viewer")
    viewer = make_client()
    login(viewer, "viewer")
    with ws_connect(viewer) as ws:
        drain_join(ws)
        ws.send_json({"type": "chat", "text": "/mod owner"})
        reply = recv_system(ws)
    assert "permission" in reply.lower()
    assert db.get_user("owner")["is_moderator"] == 0


def test_ws_ban_command_blocks_the_targets_messages(client):
    setup_admin(client, username="owner")
    add_user("troll")
    # The admin bans the target over the socket.
    with ws_connect(client) as ws:
        drain_join(ws)
        ws.send_json({"type": "chat", "text": "/ban troll"})
        confirm = recv_system(ws)
    assert "banned" in confirm.lower()
    assert hub.is_banned("troll")
    assert db.get_ban("troll") is not None
    # The banned viewer's own messages are dropped with a private notice, never
    # echoed to chat.
    troll = make_client()
    login(troll, "troll")
    with ws_connect(troll) as ws:
        drain_join(ws)
        ws.send_json({"type": "chat", "text": "hello everyone"})
        frame = ws.receive_json()
    assert frame["type"] == "system"
    assert "banned" in frame["text"].lower()


# ---- 6b. OBS chat overlay -------------------------------------------------

def test_ws_overlay_receives_chat_but_is_not_a_viewer(client):
    setup_admin(client, username="owner")
    key = db.regenerate_overlay_key()
    with client.websocket_connect(f"/ws?overlay={key}") as overlay:
        with ws_connect(client) as ws:
            drain_join(ws)
            # The overlay is a watcher, not a viewer: it never shows up in the
            # presence list or the watching count.
            assert [v["username"] for v in hub.viewers()] == ["owner"]
            assert len(hub._watchers) == 1
            ws.send_json({"type": "chat", "text": "hi overlay"})
        # It still receives the chat broadcast (presence/system frames arrive
        # first from the viewer joining and chatting).
        chat = None
        for _ in range(12):
            frame = overlay.receive_json()
            if frame.get("type") == "chat":
                chat = frame
                break
        assert chat is not None
        assert chat["text"] == "hi overlay"
        assert chat["user"] == "owner"


def test_ws_overlay_bad_key_is_refused(client):
    setup_admin(client, username="owner")
    db.regenerate_overlay_key()
    with pytest.raises(WebSocketDisconnect) as excinfo:
        with client.websocket_connect("/ws?overlay=not-the-real-key"):
            pass
    assert excinfo.value.code == 4401


# ---- 6c. MediaMTX publish/read auth callback ------------------------------

def test_mtx_auth_publish_requires_the_current_key(client):
    key = db.regenerate_stream_key()
    anon = make_client()
    # The right key publishes.
    ok = anon.post("/mtx-auth", json={"action": "publish", "password": key})
    assert ok.status_code == 200
    # A wrong key is refused, and the user field is ignored (legacy OBS/demo URLs
    # send user=publisher and still work).
    bad = anon.post(
        "/mtx-auth",
        json={"action": "publish", "user": "publisher", "password": "nope"},
    )
    assert bad.status_code == 401


def test_mtx_auth_publish_refused_before_any_key_exists(client):
    # No key has been generated yet, so nothing may publish: a blank password must
    # never authenticate.
    anon = make_client()
    resp = anon.post("/mtx-auth", json={"action": "publish", "password": ""})
    assert resp.status_code == 401


def test_mtx_auth_read_allows_only_internal_ips(client):
    anon = make_client()
    # A read from inside the docker network (Caddy, the gate's own ffmpeg) is fine.
    inside = anon.post("/mtx-auth", json={"action": "read", "ip": "172.20.0.5"})
    assert inside.status_code == 200
    # A read from a public IP is refused.
    outside = anon.post("/mtx-auth", json={"action": "read", "ip": "203.0.113.7"})
    assert outside.status_code == 401
    # An unparseable IP is refused rather than erroring.
    garbage = anon.post("/mtx-auth", json={"action": "read", "ip": "not-an-ip"})
    assert garbage.status_code == 401


def test_mtx_auth_internal_ip_never_grants_publish(client):
    # Being on the docker network is enough to read, but a publish still needs the
    # key: an attacker inside the network must not publish keyless.
    db.regenerate_stream_key()
    anon = make_client()
    resp = anon.post(
        "/mtx-auth",
        json={"action": "publish", "ip": "172.20.0.5", "password": "wrong"},
    )
    assert resp.status_code == 401


def test_mtx_auth_regenerate_rotates_the_accepted_key(client):
    old = db.regenerate_stream_key()
    anon = make_client()
    assert anon.post(
        "/mtx-auth", json={"action": "publish", "password": old}
    ).status_code == 200
    new = db.regenerate_stream_key()
    # The old key stops being accepted; the freshly minted one is.
    assert anon.post(
        "/mtx-auth", json={"action": "publish", "password": old}
    ).status_code == 401
    assert anon.post(
        "/mtx-auth", json={"action": "publish", "password": new}
    ).status_code == 200


def test_clip_event_broadcast_reaches_a_watcher(client):
    # The clip announcement is a plain hub broadcast, so it can be exercised at
    # the hub layer without ffmpeg. A watcher (the overlay) must receive it.
    import asyncio

    class FakeSocket:
        def __init__(self):
            self.sent = []

        async def send_json(self, message):
            self.sent.append(message)

    sock = FakeSocket()
    hub.add_watcher(sock)
    try:
        asyncio.run(hub.broadcast({"type": "clip", "name": "Big play", "by": "Owner"}))
    finally:
        hub.remove_watcher(sock)
    assert sock.sent == [{"type": "clip", "name": "Big play", "by": "Owner"}]


# ---- 7b. Channel accent flavor --------------------------------------------

def test_stream_info_accepts_and_validates_accent(client):
    setup_admin(client)
    ok = client.post("/api/stream-info", json={"title": "x", "accent": "blue"})
    assert ok.status_code == 200
    assert db.get_stream_info()["accent"] == "blue"
    # An unknown flavor is refused and the stored value is left as it was.
    bad = client.post("/api/stream-info", json={"title": "x", "accent": "rainbow"})
    assert bad.status_code == 400
    assert db.get_stream_info()["accent"] == "blue"


def test_status_exposes_accent(client):
    # /api/status is public and carries the accent so pre-login pages can paint
    # the brand color. MediaMTX is unreachable in tests, so this reads offline.
    setup_admin(client)
    client.post("/api/stream-info", json={"title": "x", "accent": "ghost"})
    body = client.get("/api/status").json()
    assert body["accent"] == "ghost"


# ---- 8. Session-gated media endpoints -------------------------------------

@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/api/thumbnail"),
        ("GET", "/api/vods"),
        ("GET", "/api/clips"),
        ("POST", "/api/clip"),
    ],
)
def test_media_endpoints_require_a_session(client, method, path):
    # No cookie: the gate should turn these away cleanly, never error out.
    resp = client.request(method, path, json={} if method == "POST" else None)
    assert resp.status_code in (401, 403)
