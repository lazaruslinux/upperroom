"""
The 2026-09 review round: writes and the chat socket must come from this site's
own pages, a socket whose send failed must end rather than spin the event loop,
and a person who is deleted, demoted or whose guest time is up must stop having
powers on sockets and routes that only read the token.
"""

import time

import jwt
import pytest
from starlette.websockets import WebSocketDisconnect, WebSocketState

import auth
import db
from config import COOKIE_NAME, JWT_SECRET
from conftest import make_client
from hub import hub

from test_api import add_user, drain_join, login, setup_admin, ws_connect
from test_guest import make_pass, redeem
from test_theater import wait_until


EVIL = "https://evil.example"


# ---- Cross-site writes ----------------------------------------------------

def test_a_write_from_another_origin_is_refused(client):
    add_user("nell")
    resp = client.post(
        "/api/auth",
        json={"username": "nell", "password": "password1"},
        headers={"Origin": EVIL},
    )
    assert resp.status_code == 403
    assert COOKIE_NAME not in resp.cookies


def test_a_sibling_subdomain_counts_as_another_site(client):
    add_user("nell")
    resp = client.post(
        "/api/auth",
        json={"username": "nell", "password": "password1"},
        headers={"Sec-Fetch-Site": "same-site"},
    )
    assert resp.status_code == 403


def test_a_write_from_this_origin_goes_through(client):
    add_user("nell")
    resp = client.post(
        "/api/auth",
        json={"username": "nell", "password": "password1"},
        headers={"Origin": "https://testserver", "Sec-Fetch-Site": "same-origin"},
    )
    assert resp.status_code == 200


def test_reads_are_not_checked(client):
    assert client.get("/api/status", headers={"Origin": EVIL}).status_code == 200


def test_the_chat_socket_refuses_another_origin(client):
    add_user("nell")
    login(client, "nell")
    token = client.cookies.get(COOKIE_NAME)
    with client.websocket_connect(
        "/ws", cookies={COOKIE_NAME: token}, headers={"Origin": EVIL},
    ) as ws:
        with pytest.raises(WebSocketDisconnect) as excinfo:
            ws.receive_json()
    assert excinfo.value.code == 4403


# ---- The chat loop --------------------------------------------------------

def test_a_socket_whose_send_failed_ends_instead_of_spinning(client):
    """Starlette marks a socket disconnected when a send to it fails, and every
    receive after that raises at once without awaiting. The loop used to
    `continue` on that and spin, which froze the whole gate."""
    setup_admin(client, username="owner")
    add_user("nell")
    viewer = make_client()
    login(viewer, "nell")
    with ws_connect(viewer) as ws, ws_connect(client) as owner:
        drain_join(ws)
        drain_join(owner)
        assert wait_until(lambda: "nell" in hub.present_usernames())
        server_side = next(
            sock for sock, who in hub._sockets.items() if who["username"] == "nell"
        )

        async def broken_send(message):
            raise OSError("connection reset")

        server_side._send = broken_send
        # A broadcast now fails on nell's socket, which is what flips it.
        owner.send_json({"type": "chat", "text": "hello room"})
        assert wait_until(
            lambda: server_side.application_state == WebSocketState.DISCONNECTED
        )
        # The receive already waiting returns this frame; the next one is the
        # one that raises without awaiting.
        ws.send_json({"type": "ping"})
        assert wait_until(lambda: "nell" not in hub.present_usernames(), timeout=3)
        # And the gate is still answering.
        owner.send_json({"type": "chat", "text": "still here"})
        while owner.receive_json().get("text") != "still here":
            pass


def test_an_oversized_frame_is_dropped_unread(client):
    setup_admin(client, username="owner")
    with ws_connect(client) as ws:
        drain_join(ws)
        ws.send_text('{"type": "chat", "text": "' + "x" * 20000 + '"}')
        ws.send_json({"type": "chat", "text": "still here"})
        while True:
            frame = ws.receive_json()
            if frame.get("type") == "chat":
                break
    assert frame["text"] == "still here"


# ---- Deleted, demoted, expired --------------------------------------------

def test_deleting_an_account_closes_its_chat(client):
    setup_admin(client, username="owner")
    add_user("nell")
    viewer = make_client()
    login(viewer, "nell")
    with ws_connect(viewer) as ws:
        drain_join(ws)
        assert wait_until(lambda: "nell" in hub.present_usernames())
        resp = client.delete("/api/admin/users/nell?confirm=nell")
        assert resp.status_code == 200
        with pytest.raises(WebSocketDisconnect):
            while True:
                ws.receive_json()


def test_demoting_an_admin_reaches_their_open_socket(client):
    setup_admin(client, username="owner")
    add_user("second", is_admin=True)
    other = make_client()
    login(other, "second")
    with ws_connect(other) as ws:
        drain_join(ws)
        assert wait_until(lambda: "second" in hub.present_usernames())
        resp = client.patch("/api/admin/users/second", json={"is_admin": False})
        assert resp.status_code == 200
        assert wait_until(lambda: not any(
            who["admin"] for who in hub._sockets.values()
            if who["username"] == "second"
        ))


def test_a_deleted_accounts_token_is_signed_out(client):
    add_user("nell")
    login(client, "nell")
    db.delete_user("nell")
    assert client.get("/api/me").json() == {"authed": False}
    assert client.get("/api/channel").status_code == 401


def test_a_guest_token_ends_with_the_guest(client):
    setup_admin(client)
    visitor = make_client()
    assert redeem(visitor, make_pass()).status_code == 200
    claims = jwt.decode(
        visitor.cookies.get(COOKIE_NAME), JWT_SECRET, algorithms=["HS256"],
    )
    row = db.get_user(claims["sub"])
    assert claims["exp"] <= row["guest_expires_at"]


def test_a_foreign_character_in_a_key_is_a_refusal_not_a_crash(client):
    setup_admin(client, username="owner")
    db.regenerate_overlay_key()
    with client.websocket_connect("/ws?overlay=%E2%9C%93") as ws:
        with pytest.raises(WebSocketDisconnect) as excinfo:
            ws.receive_json()
    assert excinfo.value.code == 4401
    assert auth.key_matches("✓", "abc") is False


def test_regenerating_the_overlay_key_closes_connected_overlays(client):
    setup_admin(client, username="owner")
    key = db.regenerate_overlay_key()
    with client.websocket_connect(f"/ws?overlay={key}") as ws:
        assert wait_until(lambda: len(hub._watchers) == 1)
        assert client.post("/api/admin/overlay/regenerate").status_code == 200
        with pytest.raises(WebSocketDisconnect) as excinfo:
            ws.receive_json()
    assert excinfo.value.code == 4401
