"""
The viewer limit, and who is counted against it.

Every viewer pulls their own copy of the broadcast off this server, so the
number of people watching is what the bandwidth bill is made of. The limit is
enforced where Caddy already asks permission for each segment, which means it
also guards the saved recordings and clips: those are files on disk, they cost
nothing per viewer, and they must not be caught by it.
"""

import sys
import threading
import time

import pytest

import db
import watchers
from conftest import make_client
from test_api import add_user, login, setup_admin

LIVE = {"X-Forwarded-Uri": "/live/index.m3u8"}
VOD = {"X-Forwarded-Uri": "/vods/2026-08-30.mp4"}


@pytest.fixture
def room(client):
    """An admin owner, two viewers, and a limit of one."""
    setup_admin(client, username="owner")
    add_user("nell")
    add_user("rafe")
    db.set_max_viewers(1)
    return client


def viewer(name):
    session = make_client()
    login(session, name)
    return session


# ---- the module that counts ----


def test_a_watcher_falls_out_of_the_count_when_they_stop_asking():
    watchers.reset()
    now = time.time()
    watchers.note("nell", now)
    assert watchers.count(now) == 1
    assert watchers.active("nell", now)
    assert watchers.count(now + 31) == 0
    assert not watchers.active("nell", now + 31)


def test_the_count_survives_concurrent_segment_checks(monkeypatch):
    """The route that feeds this is a sync def, so FastAPI runs it in its
    threadpool and a full room hits this module genuinely in parallel. The prune
    loop deleting from the same dict another thread was writing to raised a
    RuntimeError or a KeyError out of an authorization check, which Caddy turns
    into a refused segment: the video stops for a viewer who did nothing wrong,
    and only when enough people are watching to make it worth caring about."""
    watchers.reset()
    # Nothing outlives a tick, so every call prunes while the others write, and a
    # short switch interval makes the interpreter change threads inside the loop
    # instead of finishing it first. Both are needed to provoke it reliably.
    monkeypatch.setattr(watchers, "WATCHER_WINDOW_SECONDS", 0)
    interval = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    errors = []

    def hammer(tag):
        try:
            for i in range(300):
                watchers.note(f"{tag}-{i % 200}")
                watchers.active(f"{tag}-{i % 200}")
                watchers.count()
        except Exception as exc:
            errors.append(repr(exc))

    threads = [threading.Thread(target=hammer, args=(f"t{n}",)) for n in range(8)]
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
    finally:
        sys.setswitchinterval(interval)
        watchers.reset()

    assert errors == []


def test_one_person_with_several_tabs_is_one_watcher():
    watchers.reset()
    now = time.time()
    watchers.note("nell", now)
    watchers.note("nell", now)
    watchers.note("rafe", now)
    assert watchers.count(now) == 2


# ---- the limit, through the route Caddy calls ----


def test_the_first_viewer_is_let_in_and_the_second_is_not(room):
    nell, rafe = viewer("nell"), viewer("rafe")
    assert nell.get("/api/verify", headers=LIVE).status_code == 200
    assert rafe.get("/api/verify", headers=LIVE).status_code == 403


def test_somebody_already_watching_is_never_thrown_out(room):
    # Their next segment must not be the one that refuses them: they were
    # admitted, and the limit is about who starts, not who is mid-title.
    nell = viewer("nell")
    assert nell.get("/api/verify", headers=LIVE).status_code == 200
    assert nell.get("/api/verify", headers=LIVE).status_code == 200


def test_a_place_opens_up_when_somebody_stops_watching(room, monkeypatch):
    nell, rafe = viewer("nell"), viewer("rafe")
    assert nell.get("/api/verify", headers=LIVE).status_code == 200
    assert rafe.get("/api/verify", headers=LIVE).status_code == 403
    # Nell closes the tab: no more segment requests, so the window runs out.
    monkeypatch.setattr(watchers, "WATCHER_WINDOW_SECONDS", 0)
    assert rafe.get("/api/verify", headers=LIVE).status_code == 200


def test_the_operator_is_never_refused_their_own_broadcast(room):
    nell = viewer("nell")
    assert nell.get("/api/verify", headers=LIVE).status_code == 200
    # Full for everyone else, and still open to the admin.
    assert viewer("rafe").get("/api/verify", headers=LIVE).status_code == 403
    assert room.get("/api/verify", headers=LIVE).status_code == 200


def test_the_operator_does_not_take_up_a_place(room):
    assert room.get("/api/verify", headers=LIVE).status_code == 200
    assert watchers.count() == 0
    assert viewer("nell").get("/api/verify", headers=LIVE).status_code == 200


def test_recordings_and_clips_are_not_capped(room):
    nell, rafe = viewer("nell"), viewer("rafe")
    assert nell.get("/api/verify", headers=LIVE).status_code == 200
    # The live room is full, but a saved file costs nothing per viewer.
    assert rafe.get("/api/verify", headers=VOD).status_code == 200
    # And watching a recording does not take a place in the live room either.
    assert watchers.count() == 1


def test_a_proxy_that_sends_no_path_refuses_nobody(room):
    # An older or hand-edited Caddy config. A limit that locks the whole channel
    # out of its own video is worse than no limit, so this fails open.
    nell, rafe = viewer("nell"), viewer("rafe")
    assert nell.get("/api/verify", headers=LIVE).status_code == 200
    assert rafe.get("/api/verify").status_code == 200


def test_no_limit_lets_everybody_in(client):
    setup_admin(client, username="owner")
    add_user("nell")
    add_user("rafe")
    assert db.get_max_viewers() == 0
    assert viewer("nell").get("/api/verify", headers=LIVE).status_code == 200
    assert viewer("rafe").get("/api/verify", headers=LIVE).status_code == 200


def test_a_signed_out_visitor_is_still_a_401_not_a_403(room):
    # The limit must not turn "you are not signed in" into "the room is full".
    assert make_client().get("/api/verify", headers=LIVE).status_code == 401


# ---- the setting ----


def test_the_limit_saves_and_reads_back(client):
    setup_admin(client, username="owner")
    assert client.post("/api/stream-info", json={"max_viewers": 12}).status_code == 200
    assert db.get_max_viewers() == 12
    # Zero is a real value here: it turns the limit off.
    assert client.post("/api/stream-info", json={"max_viewers": 0}).status_code == 200
    assert db.get_max_viewers() == 0


def test_the_limit_is_clamped_and_a_word_is_refused(client):
    setup_admin(client, username="owner")
    client.post("/api/stream-info", json={"max_viewers": 99999})
    assert db.get_max_viewers() == 500
    client.post("/api/stream-info", json={"max_viewers": -4})
    assert db.get_max_viewers() == 0
    assert client.post(
        "/api/stream-info", json={"max_viewers": "lots"}
    ).status_code == 400


def test_saving_the_title_alone_leaves_the_limit_where_it_was(client):
    setup_admin(client, username="owner")
    client.post("/api/stream-info", json={"max_viewers": 7})
    client.post("/api/stream-info", json={"title": "Tonight"})
    assert db.get_max_viewers() == 7


def test_a_viewer_cannot_read_or_set_the_limit(client):
    setup_admin(client, username="owner")
    add_user("nell")
    nell = viewer("nell")
    assert nell.post("/api/stream-info", json={"max_viewers": 0}).status_code == 403
    # And it is not on the endpoint any signed-in viewer can read.
    assert "max_viewers" not in nell.get("/api/channel").json()


# ---- what the dashboard reads ----


def test_the_dashboard_is_told_the_limit_and_who_is_watching(room):
    viewer("nell").get("/api/verify", headers=LIVE)
    data = room.get("/api/admin/stream").json()
    assert data["max_viewers"] == 1
    assert data["video_watchers"] == 1
    # Offline, so there is nothing sent to report yet.
    assert data["sent_bytes"] == 0
