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

import os
import time

import pytest
from starlette.websockets import WebSocketDisconnect

import auth
import db
import notify
from config import (
    COOKIE_NAME, HIGHLIGHT_COST, MAX_MESSAGE_LENGTH, MAX_SCHEDULE_NOTE,
)
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


def test_setup_creates_admin_names_site_and_signs_in(client):
    resp = setup_admin(client, username="owner", channel="Room One")
    assert COOKIE_NAME in resp.cookies
    row = db.get_user("owner")
    assert row["is_admin"] == 1
    # The wizard's one name field is the site name (the operator's brand), not the
    # per-broadcast stream title, which keeps its default until an admin edits it.
    assert db.get_stream_info()["site_name"] == "Room One"
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
        ("GET", "/api/admin/retention", None),
        ("POST", "/api/admin/retention", {"vod_keep_count": 1}),
        ("POST", "/api/vods/1/keep", {"keep": True}),
        ("POST", "/api/clips/1/keep", {"keep": True}),
        ("GET", "/api/admin/schedule", None),
        ("POST", "/api/admin/schedule", {"next_stream_at": 0}),
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
    # Confirmed properly, so the 400 is about being the last admin and not
    # about the confirmation step.
    delete = client.request("DELETE", "/api/admin/users/owner?confirm=owner")
    assert delete.status_code == 400
    assert "only admin" in delete.json()["error"].lower()
    assert db.get_user("owner") is not None


def test_delete_needs_the_username_typed_back(client):
    setup_admin(client, username="owner")
    add_user("leaving")
    bare = client.request("DELETE", "/api/admin/users/leaving")
    assert bare.status_code == 400
    assert db.get_user("leaving") is not None
    wrong = client.request("DELETE", "/api/admin/users/leaving?confirm=leavin")
    assert wrong.status_code == 400
    assert db.get_user("leaving") is not None
    ok = client.request("DELETE", "/api/admin/users/leaving?confirm=LEAVING")
    assert ok.status_code == 200          # same normalising as a username field
    assert db.get_user("leaving") is None


def test_admin_cannot_rename_someone(client):
    setup_admin(client, username="owner")
    add_user("viewer")                       # display name defaults to "Viewer"
    resp = client.patch(
        "/api/admin/users/viewer",
        json={"display_name": "Renamed By Admin", "is_moderator": True},
    )
    assert resp.status_code == 403
    after = db.get_user("viewer")
    assert after["display_name"] == "Viewer"
    # The refusal is total: nothing else in the same request took effect.
    assert after["is_moderator"] == 0


def test_admin_still_names_an_account_when_creating_it(client):
    setup_admin(client, username="owner")
    resp = client.post(
        "/api/admin/users",
        json={"username": "newbie", "password": "longenough", "display_name": "Newbie"},
    )
    assert resp.status_code == 200
    assert db.get_user("newbie")["display_name"] == "Newbie"


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


# ---- 7c. Site name (the operator's brand) ---------------------------------

def test_stream_info_accepts_and_validates_site_name(client):
    setup_admin(client, channel="Old Name")
    ok = client.post("/api/stream-info", json={"site_name": "Northwind Live"})
    assert ok.status_code == 200
    assert db.get_stream_info()["site_name"] == "Northwind Live"
    # An empty site name is refused and the stored value is left as it was.
    bad = client.post("/api/stream-info", json={"site_name": "   "})
    assert bad.status_code == 400
    assert db.get_stream_info()["site_name"] == "Northwind Live"


def test_site_name_is_clamped_to_max_length(client):
    from config import MAX_SITE_NAME
    setup_admin(client)
    client.post("/api/stream-info", json={"site_name": "z" * (MAX_SITE_NAME + 50)})
    assert len(db.get_stream_info()["site_name"]) == MAX_SITE_NAME


def test_site_name_surfaces_on_public_status_and_channel(client):
    # The login page (pre-auth) reads it from /api/status; signed-in pages read it
    # from /api/channel. Both must carry the operator's brand.
    setup_admin(client)
    client.post("/api/stream-info", json={"site_name": "Northwind Live"})
    assert client.get("/api/status").json()["site_name"] == "Northwind Live"
    assert client.get("/api/channel").json()["site_name"] == "Northwind Live"


# ---- 7d. Chat colors (per-user name + message color) ----------------------

def test_chat_color_accepts_readable_and_rejects_dim_and_reserved_red(client):
    setup_admin(client, username="owner")
    # A bright, readable color is stored.
    ok = client.post("/api/profile", json={"name_color": "#8ab4ff"})
    assert ok.status_code == 200
    assert db.get_user("owner")["name_color"] == "#8ab4ff"
    # Too dark to read on the near-black panel: rejected, prior value kept.
    dim = client.post("/api/profile", json={"name_color": "#111111"})
    assert dim.status_code == 400
    assert db.get_user("owner")["name_color"] == "#8ab4ff"
    # The reserved live/host red is refused.
    assert client.post("/api/profile", json={"msg_color": "#ff4d4d"}).status_code == 400
    # Malformed input is refused.
    assert client.post("/api/profile", json={"name_color": "not-a-color"}).status_code == 400
    # An empty value clears back to the theme default.
    clear = client.post("/api/profile", json={"name_color": ""})
    assert clear.status_code == 200
    assert db.get_user("owner")["name_color"] == ""


def test_chat_color_rides_on_the_message_payload(client):
    setup_admin(client, username="owner")
    client.post("/api/profile", json={"name_color": "#8ab4ff", "msg_color": "#c8f0d8"})
    with ws_connect(client) as ws:
        drain_join(ws)
        ws.send_json({"type": "chat", "text": "hello"})
        frame = ws.receive_json()
    assert frame["type"] == "chat"
    assert frame["name_color"] == "#8ab4ff"
    assert frame["msg_color"] == "#c8f0d8"


# ---- 7e. Moderation: purge, hover-delete, slow mode, banned words ---------

def test_ws_purge_deletes_all_of_a_users_messages(client):
    setup_admin(client, username="owner")
    add_user("chatty")
    # This is about purge, not pacing, so take the default slow mode out of it.
    db.set_chat_moderation(slow_mode_seconds=0)
    chatty = make_client()
    login(chatty, "chatty")
    with ws_connect(chatty) as cw:
        drain_join(cw)
        cw.send_json({"type": "chat", "text": "one"})
        cw.receive_json()
        cw.send_json({"type": "chat", "text": "two"})
        cw.receive_json()
    # After chatty leaves, the owner purges their backlog.
    with ws_connect(client) as ow:
        drain_join(ow)
        ow.send_json({"type": "chat", "text": "/purge chatty"})
        reply = recv_system(ow)
    assert "purged 2" in reply.lower()


def test_ws_moddelete_removes_one_message_by_id(client):
    setup_admin(client, username="owner")
    add_user("viewer")
    viewer = make_client()
    login(viewer, "viewer")
    with ws_connect(viewer) as vw:
        drain_join(vw)
        vw.send_json({"type": "chat", "text": "hi"})
        msg_id = vw.receive_json()["id"]
    with ws_connect(client) as ow:
        drain_join(ow)
        ow.send_json({"type": "moddelete", "id": msg_id})
        deleted = None
        for _ in range(5):
            frame = ow.receive_json()
            if frame.get("type") == "delete":
                deleted = frame
                break
    assert deleted is not None and deleted["id"] == msg_id


def test_ws_moddelete_mod_cannot_delete_an_admin_message(client):
    setup_admin(client, username="owner")   # owner is the admin
    add_user("mod1", is_moderator=True)
    with ws_connect(client) as ow:
        drain_join(ow)
        ow.send_json({"type": "chat", "text": "admin speaking"})
        admin_msg_id = ow.receive_json()["id"]
    mod = make_client()
    login(mod, "mod1")
    with ws_connect(mod) as mw:
        drain_join(mw)
        mw.send_json({"type": "moddelete", "id": admin_msg_id})
        reply = recv_system(mw)
    assert "admin" in reply.lower()


def test_slow_mode_blocks_fast_repeats_but_exempts_admin(client):
    setup_admin(client, username="owner")
    add_user("viewer")
    db.set_chat_moderation(slow_mode_seconds=30)
    viewer = make_client()
    login(viewer, "viewer")
    with ws_connect(viewer) as vw:
        drain_join(vw)
        vw.send_json({"type": "chat", "text": "first"})
        assert vw.receive_json()["type"] == "chat"
        vw.send_json({"type": "chat", "text": "too soon"})
        blocked = vw.receive_json()
    assert blocked["type"] == "system"
    assert "wait" in blocked["text"].lower()
    # The admin is exempt: two quick messages both broadcast as chat.
    with ws_connect(client) as ow:
        drain_join(ow)
        ow.send_json({"type": "chat", "text": "a"})
        first = ow.receive_json()
        ow.send_json({"type": "chat", "text": "b"})
        second = ow.receive_json()
    assert first["type"] == "chat" and second["type"] == "chat"


def test_banned_word_message_is_dropped(client):
    setup_admin(client, username="owner")
    add_user("viewer")
    db.set_chat_moderation(banned_words="pineapple\nbadword")
    viewer = make_client()
    login(viewer, "viewer")
    with ws_connect(viewer) as vw:
        drain_join(vw)
        vw.send_json({"type": "chat", "text": "I love PINEAPPLE pizza"})
        frame = vw.receive_json()
    assert frame["type"] == "system"
    assert "blocked" in frame["text"].lower()


def test_admin_is_excluded_from_go_live_email_recipients(client):
    setup_admin(client, username="owner")
    db.set_email("owner", "owner@example.com")
    add_user("viewer")
    db.set_email("viewer", "viewer@example.com")
    emails = [email for _, email in db.list_live_recipients()]
    assert "viewer@example.com" in emails
    assert "owner@example.com" not in emails


def test_channel_email_defaults_on(client):
    # A fresh channel, and an upgraded one, both send email: the switch only
    # matters once an operator turns it off.
    setup_admin(client, username="owner")
    assert db.get_notify_settings()["email_on_live"] == 1


def test_channel_email_switch_gates_go_live_email(client, monkeypatch):
    # With a relay configured, the switch alone decides whether email goes out.
    setup_admin(client, username="owner")
    monkeypatch.setattr(notify, "SMTP_HOST", "smtp.example.com")
    monkeypatch.setattr(notify, "SMTP_FROM", "bot@example.com")
    assert notify.email_enabled() is True
    db.set_email_on_live(False)
    assert notify.email_enabled() is False
    db.set_email_on_live(True)
    assert notify.email_enabled() is True


def test_channel_email_switch_cannot_send_without_a_relay(client):
    # No SMTP configured, so the switch being on changes nothing.
    setup_admin(client, username="owner")
    db.set_email_on_live(True)
    assert notify.email_enabled() is False


def test_admin_notify_endpoint_round_trips_the_email_switch(client):
    setup_admin(client, username="owner")
    assert client.get("/api/admin/notify").json()["email_on_live"] is True
    assert client.post("/api/admin/notify", json={"email_on_live": False}).status_code == 200
    assert client.get("/api/admin/notify").json()["email_on_live"] is False


# ---- 8. Session-gated media endpoints -------------------------------------

@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/api/thumbnail"),
        ("GET", "/api/vods"),
        ("GET", "/api/clips"),
        ("POST", "/api/clip"),
        ("GET", "/api/vods/1/chat"),
        ("GET", "/api/clips/1/chat"),
        ("GET", "/api/points"),
        ("POST", "/api/redeem"),
    ],
)
def test_media_endpoints_require_a_session(client, method, path):
    # No cookie: the gate should turn these away cleanly, never error out.
    resp = client.request(method, path, json={} if method == "POST" else None)
    assert resp.status_code in (401, 403)


# ---- 9. Channel points and the highlight redemption -----------------------

class _CaptureSocket:
    """A stand-in overlay watcher that records the broadcasts it receives, so a
    test can assert the exact event shape a redeem sends over the hub."""

    def __init__(self):
        self.sent = []

    async def send_json(self, message):
        self.sent.append(message)


def _viewer_with_points(client, points):
    """Sign a fresh viewer client in and give the account a starting balance."""
    setup_admin(client, username="owner")
    add_user("viewer")
    db.credit_points(["viewer"], points)
    viewer = make_client()
    login(viewer, "viewer")
    return viewer


def test_points_endpoint_returns_balance_and_cost(client):
    viewer = _viewer_with_points(client, 120)
    body = viewer.get("/api/points").json()
    assert body["points"] == 120
    assert body["cost"] == HIGHLIGHT_COST


def test_redeem_requires_a_message(client):
    viewer = _viewer_with_points(client, 120)
    for body in ({}, {"message": ""}, {"message": "   "}):
        resp = viewer.post("/api/redeem", json=body)
        assert resp.status_code == 400
    assert db.get_points("viewer") == 120          # nothing spent on an empty say


def test_redeem_highlights_and_broadcasts_the_event(client):
    # The success path spends the cost, returns the new balance, and broadcasts a
    # highlight event of the documented shape to every connected watcher (the
    # overlay). Mirror the WS event test: seat a fake watcher, then drive redeem.
    viewer = _viewer_with_points(client, 120)
    sock = _CaptureSocket()
    hub.add_watcher(sock)
    try:
        resp = viewer.post("/api/redeem", json={"message": "hello stream"})
    finally:
        hub.remove_watcher(sock)
    assert resp.status_code == 200
    assert resp.json()["points"] == 120 - HIGHLIGHT_COST
    assert db.get_points("viewer") == 120 - HIGHLIGHT_COST
    assert sock.sent == [
        {"type": "highlight", "user": "Viewer", "message": "hello stream",
         "cost": HIGHLIGHT_COST}
    ]


def test_redeem_truncates_over_length_message_like_chat(client):
    # The chat socket truncates to MAX_MESSAGE_LENGTH; the highlight text must obey
    # the same limit, so an over-length message is accepted and clipped, not
    # rejected.
    viewer = _viewer_with_points(client, 120)
    sock = _CaptureSocket()
    hub.add_watcher(sock)
    try:
        resp = viewer.post(
            "/api/redeem", json={"message": "x" * (MAX_MESSAGE_LENGTH + 50)}
        )
    finally:
        hub.remove_watcher(sock)
    assert resp.status_code == 200
    assert len(sock.sent[0]["message"]) == MAX_MESSAGE_LENGTH


def test_redeem_insufficient_balance_is_rejected_unchanged(client):
    viewer = _viewer_with_points(client, HIGHLIGHT_COST - 1)
    resp = viewer.post("/api/redeem", json={"message": "hi"})
    assert resp.status_code == 400
    assert resp.json()["detail"] == "not enough points"
    assert db.get_points("viewer") == HIGHLIGHT_COST - 1    # balance untouched


def test_redeem_timed_out_viewer_is_forbidden(client):
    # A timed-out viewer cannot highlight, checked through the same hub timeout
    # the chat send path consults, and the spend never runs.
    viewer = _viewer_with_points(client, 120)
    hub.set_timeout("viewer", 300)
    resp = viewer.post("/api/redeem", json={"message": "hi"})
    assert resp.status_code == 403
    assert db.get_points("viewer") == 120


def test_redeem_banned_viewer_is_forbidden(client):
    # A banned viewer is likewise refused, through the same hub ban set the chat
    # send path consults.
    viewer = _viewer_with_points(client, 120)
    hub.add_ban_local("viewer")
    resp = viewer.post("/api/redeem", json={"message": "hi"})
    assert resp.status_code == 403
    assert db.get_points("viewer") == 120


def test_profile_reports_points(client):
    setup_admin(client, username="owner")
    add_user("viewer")
    db.credit_points(["viewer"], 7)
    login(client, "owner")
    assert client.get("/api/profile/viewer").json()["points"] == 7


def test_watch_points_accrue_once_per_user_and_only_when_live(client):
    # The credit function is what the stream watcher calls once a minute. It
    # credits each distinct present viewer exactly once, and only while live.
    from media import credit_watch_points

    add_user("alice")
    add_user("bob")
    # Two tabs for alice, one for bob (fake sockets keyed by identity).
    hub._sockets[object()] = {"username": "alice"}
    hub._sockets[object()] = {"username": "alice"}
    hub._sockets[object()] = {"username": "bob"}
    try:
        hub._live = False
        assert credit_watch_points() == 0          # offline: nothing accrues
        assert db.get_points("alice") == 0
        hub._live = True
        assert credit_watch_points() == 2          # two distinct viewers credited
        assert db.get_points("alice") == 1         # once, despite two tabs
        assert db.get_points("bob") == 1
    finally:
        hub._live = False
        hub._sockets.clear()


# ---- 10. Retention and storage --------------------------------------------

def _finished_vod(started_at, title="Show"):
    vod_id = db.create_vod(title, "", started_at)
    db.finalize_vod(vod_id, started_at + 60, 60, f"{vod_id}.mp4")
    return vod_id


def test_retention_reports_zero_limits_and_real_usage(client):
    # What a fresh install must look like on the dashboard: every limit off, and
    # the storage numbers present so the operator can see usage before deciding.
    setup_admin(client)
    body = client.get("/api/admin/retention").json()
    assert all(body[field] == 0 for field in db.RETENTION_FIELDS)
    assert body["counts"] == {"vods": 0, "clips": 0, "pinned": 0}
    assert set(body["usage"]) == {
        "vods_bytes", "clips_bytes", "total_bytes", "free_bytes", "fs_total_bytes",
    }


@pytest.mark.parametrize("value", [-1, "lots", None, 1.5])
def test_retention_rejects_a_value_that_is_not_a_whole_count(client, value):
    setup_admin(client)
    resp = client.post("/api/admin/retention", json={"vod_keep_count": value})
    assert resp.status_code == 400
    assert db.get_retention()["vod_keep_count"] == 0      # nothing was written


def test_retention_saves_partially_and_clamps_absurd_values(client):
    setup_admin(client)
    client.post("/api/admin/retention", json={"vod_keep_count": 5})
    client.post("/api/admin/retention", json={"vod_keep_days": 99999})
    limits = db.get_retention()
    assert limits["vod_keep_count"] == 5                  # untouched by the second save
    assert limits["vod_keep_days"] == 3650                # clamped, not rejected


def test_saving_a_lower_limit_prunes_at_once_and_reports_the_cost(client):
    # Retention has to bite on save; waiting an hour for the sweep would leave
    # the operator unsure whether the setting did anything.
    setup_admin(client)
    now = int(time.time())
    ids = [_finished_vod(now - (4 - i) * 100) for i in range(4)]
    body = client.post("/api/admin/retention", json={"vod_keep_count": 2}).json()
    assert body["removed"] == 2
    assert body["counts"]["vods"] == 2
    assert {v["id"] for v in db.list_vods()} == {ids[-1], ids[-2]}


def test_a_pinned_vod_survives_a_lowered_limit(client):
    # The headline safety promise, end to end through the API.
    setup_admin(client)
    now = int(time.time())
    ids = [_finished_vod(now - (3 - i) * 100) for i in range(3)]
    assert client.post(f"/api/vods/{ids[0]}/keep", json={"keep": True}).json()["keep"]
    client.post("/api/admin/retention", json={"vod_keep_count": 1})
    remaining = {v["id"] for v in db.list_vods()}
    assert ids[0] in remaining and ids[-1] in remaining
    assert len(remaining) == 2


def test_pin_state_rides_the_content_listing(client):
    setup_admin(client)
    vod_id = _finished_vod(int(time.time()))
    assert client.get("/api/vods").json()["vods"][0]["keep"] is False
    client.post(f"/api/vods/{vod_id}/keep", json={"keep": True})
    assert client.get("/api/vods").json()["vods"][0]["keep"] is True
    client.post(f"/api/vods/{vod_id}/keep", json={"keep": False})
    assert client.get("/api/vods").json()["vods"][0]["keep"] is False


def test_pinning_something_that_does_not_exist_is_a_404(client):
    setup_admin(client)
    assert client.post("/api/vods/999/keep", json={"keep": True}).status_code == 404
    assert client.post("/api/clips/999/keep", json={"keep": True}).status_code == 404


# ---- 11. The next scheduled stream ----------------------------------------

def test_schedule_round_trips_through_the_admin_api(client):
    setup_admin(client)
    when = int(time.time()) + 7200
    resp = client.post(
        "/api/admin/schedule",
        json={"next_stream_at": when, "next_stream_note": "Week four"},
    )
    assert resp.status_code == 200
    assert resp.json()["next_stream_at"] == when
    assert client.get("/api/admin/schedule").json()["next_stream_note"] == "Week four"


@pytest.mark.parametrize(
    "when,why",
    [
        (int(time.time()) - 86400, "long past"),
        (int(time.time()) + 400 * 86400, "beyond a year"),
        ("soon", "not a number"),
    ],
)
def test_schedule_rejects_an_unusable_time(client, when, why):
    setup_admin(client)
    resp = client.post("/api/admin/schedule", json={"next_stream_at": when})
    assert resp.status_code == 400, why
    assert db.get_schedule()["next_stream_at"] == 0


def test_the_countdown_is_public_but_the_note_is_not(client):
    # The whole point of the split: someone who finds the sign-in page learns
    # when the next stream is, and nothing about what it is.
    setup_admin(client)
    when = int(time.time()) + 3600
    client.post(
        "/api/admin/schedule",
        json={"next_stream_at": when, "next_stream_note": "prayer night"},
    )
    anon = make_client()
    status = anon.get("/api/status").json()
    assert status["next_stream_at"] == when
    assert "next_stream_note" not in status
    assert "prayer night" not in str(status)
    # Signed in, the note is there.
    channel = client.get("/api/channel").json()
    assert channel["next_stream_note"] == "prayer night"


def test_the_countdown_disappears_once_the_stream_is_well_past(client):
    setup_admin(client)
    # Set it legitimately, then move it into the past behind the API's back, the
    # way time passing would.
    client.post("/api/admin/schedule", json={"next_stream_at": int(time.time()) + 60})
    db.set_schedule(int(time.time()) - 3 * 3600, "")
    assert "next_stream_at" not in client.get("/api/status").json()


def test_a_schedule_just_past_still_shows_during_the_grace_window(client):
    # A stream that starts late must not vanish exactly when people look for it.
    setup_admin(client)
    client.post("/api/admin/schedule", json={"next_stream_at": int(time.time()) + 60})
    db.set_schedule(int(time.time()) - 600, "")
    assert "next_stream_at" in client.get("/api/status").json()


def test_clearing_the_schedule_removes_it_everywhere(client):
    setup_admin(client)
    client.post(
        "/api/admin/schedule",
        json={"next_stream_at": int(time.time()) + 3600, "next_stream_note": "x"},
    )
    client.post("/api/admin/schedule", json={"next_stream_at": 0})
    assert "next_stream_at" not in client.get("/api/status").json()
    assert client.get("/api/channel").json()["next_stream_note"] == ""


def test_a_long_schedule_note_is_clamped_not_rejected(client):
    setup_admin(client)
    resp = client.post(
        "/api/admin/schedule",
        json={"next_stream_at": int(time.time()) + 3600, "next_stream_note": "x" * 500},
    )
    assert resp.status_code == 200
    assert len(db.get_schedule()["next_stream_note"]) == MAX_SCHEDULE_NOTE


# ---- 12. Web app manifest -------------------------------------------------

def test_the_manifest_is_public_and_carries_the_operators_name(client):
    # A browser asks for the manifest before anyone signs in, and the installed
    # app should appear under the operator's brand, not the software's.
    setup_admin(client, channel="Northwind Live")
    anon = make_client()
    resp = anon.get("/api/manifest.webmanifest")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/manifest+json")
    body = resp.json()
    assert body["name"] == "Northwind Live"
    assert body["start_url"] == "/home"
    assert body["display"] == "standalone"


def test_the_manifest_theme_color_follows_the_channel_accent(client):
    setup_admin(client)
    client.post("/api/stream-info", json={"accent": "blue"})
    assert client.get("/api/manifest.webmanifest").json()["theme_color"] == "#7aa3c0"
    client.post("/api/stream-info", json={"accent": "amber"})
    assert client.get("/api/manifest.webmanifest").json()["theme_color"] == "#c2a05c"


def test_every_icon_the_manifest_names_is_actually_committed(client):
    # The one check that catches shipping a manifest that points at a file
    # nobody committed, which is otherwise invisible until a phone fails to
    # install the app.
    web = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "web",
    )
    icons = client.get("/api/manifest.webmanifest").json()["icons"]
    assert icons
    for icon in icons:
        relative = icon["src"].split("?")[0].lstrip("/")
        assert os.path.exists(os.path.join(web, relative)), icon["src"]
    # And the pieces the HTML head points at directly.
    for name in ("favicon.ico", "sw.js", "assets/icons/icon.svg",
                 "assets/icons/apple-touch-icon.png", "assets/icons/og-default.png"):
        assert os.path.exists(os.path.join(web, name)), name
