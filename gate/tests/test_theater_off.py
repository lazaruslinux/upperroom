"""
Theater switched off (SELFSTREAM_THEATER unset, the default for a new install).

The rest of the suite runs with theater on. These pin what off means: the routes
are gone, the projector socket refuses, and a session somebody left open before
switching it off can no longer suppress recording or clips.
"""

import time

import pytest
from starlette.websockets import WebSocketDisconnect

import config
import db
import theater
from projector import link

from test_api import setup_admin


@pytest.fixture
def theater_off(monkeypatch):
    monkeypatch.setattr(config, "THEATER_ENABLED", False)


@pytest.mark.parametrize("method,path", [
    ("get", "/api/theater"),
    ("post", "/api/admin/theater/session"),
    ("get", "/api/admin/theater/projector"),
    ("post", "/api/admin/theater/projector/key"),
    ("get", "/api/admin/theater/search?q=long"),
    ("get", "/media/art/demo-one.jpg"),
])
def test_every_theater_route_is_a_404(client, theater_off, method, path):
    setup_admin(client, username="owner")
    assert getattr(client, method)(path).status_code == 404


def test_the_projector_socket_refuses_even_the_right_key(client, theater_off):
    setup_admin(client, username="owner")
    key = db.regenerate_projector_key()
    with client.websocket_connect(f"/ws/projector?key={key}") as ws:
        with pytest.raises(WebSocketDisconnect) as excinfo:
            ws.receive_json()
    assert excinfo.value.code == 4404
    assert link.connected() is False


def test_a_session_left_open_is_ignored(client, theater_off):
    db.create_theater_session(int(time.time()))
    assert db.get_active_theater_session() is not None
    # What the stream watcher and the clip path ask before suppressing anything.
    assert theater.is_active() is False
    assert theater.public_state() == {"active": False, "state": "off", "now": None}


def test_the_routes_come_back_when_it_is_on(client, monkeypatch):
    monkeypatch.setattr(config, "THEATER_ENABLED", True)
    setup_admin(client, username="owner")
    assert client.get("/api/theater").status_code == 200
