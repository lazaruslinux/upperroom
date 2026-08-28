"""
Theater sessions: the state machine, the projector socket, and the four things a
session suppresses.

The suppressions are the part worth pinning hardest. A session that recorded
anyway would quietly build a library of somebody else's films; a session that
wiped the chat between titles would empty the room in the gap the intermission
exists to cover; a session that announced every title would mail the channel
once a film. None of those fail loudly, which is exactly why they are asserted
here rather than left to be noticed.
"""

import asyncio
import base64
import io
import os
import time

import pytest
from PIL import Image
from starlette.websockets import WebSocketDisconnect

import auth
import db
import media
import projector
import theater
from config import ART_DIR, CLIP_DIR, VOD_DIR
from conftest import make_client
from projector import ProjectorError, link

from test_api import add_user, login, setup_admin
from test_guest import make_pass, redeem


# ---- helpers --------------------------------------------------------------

def a_jpeg(size=(10, 15)):
    """A real, tiny JPEG, so the art path is exercised rather than mocked."""
    buffer = io.BytesIO()
    Image.new("RGB", size, (20, 40, 30)).save(buffer, format="JPEG")
    return buffer.getvalue()


PLAY_DETAIL = {
    "jf_id": "abc123",
    "title": "The Long Afternoon",
    "year": 2019,
    "runtime_min": 95,
    "synopsis": "A synopsis.",
    "has_subtitles": True,
}


class StubProjector:
    """A projector's socket, without a projector.

    It answers in the same envelope the real one does and does it inline, so a
    route's rpc call resolves without a second process, a socket, or a clock."""

    def __init__(self, replies=None, errors=()):
        self.replies = replies or {}
        self.errors = set(errors)
        self.sent = []
        self.closed = None

    async def send_json(self, message):
        self.sent.append(message)
        if message["method"] in self.errors:
            await link.handle({"id": message["id"], "error": "the library said no"})
            return
        await link.handle(
            {"id": message["id"], "result": self.replies.get(message["method"])}
        )

    async def close(self, code=1000):
        self.closed = code

    def methods(self):
        return [m["method"] for m in self.sent]


def attach(stub):
    asyncio.run(link.attach(stub))
    return stub


def wait_until(predicate, timeout=2.0):
    """Wait for something the app's own thread does.

    The TestClient runs the app in a portal thread, so a socket the test has
    just opened is seated a moment after the handshake returns. Waiting for the
    state rather than assuming it is what keeps this from passing on a fast
    machine and failing on a loaded one."""
    deadline = time.time() + timeout
    while time.time() < deadline and not predicate():
        time.sleep(0.01)
    return predicate()


def working_projector():
    return attach(StubProjector(replies={
        "art": {"jpeg_b64": base64.b64encode(a_jpeg()).decode("ascii")},
        "play": PLAY_DETAIL,
        "stop": {"ok": True},
        "search": [PLAY_DETAIL],
    }))


def start(client):
    resp = client.post("/api/admin/theater/session")
    assert resp.status_code == 200
    return resp.json()


# ---- 1. The state machine -------------------------------------------------

def test_a_session_starts_in_intermission_and_is_the_active_one(client):
    setup_admin(client, username="owner")
    assert start(client) == {"active": True, "state": "intermission", "now": None}
    session = db.get_active_theater_session()
    assert session["state"] == "intermission"
    assert session["notified"] == 1        # announced once, at the start


def test_only_one_session_can_be_open_at_a_time(client):
    setup_admin(client, username="owner")
    start(client)
    second = client.post("/api/admin/theater/session")
    assert second.status_code == 409
    assert "already running" in second.json()["error"]
    # And the exclusivity is in SQL, not in the route: the db helper refuses too.
    assert db.create_theater_session(int(time.time())) is None


def test_ending_a_session_closes_it_and_a_new_one_may_then_open(client):
    setup_admin(client, username="owner")
    start(client)
    assert client.post("/api/admin/theater/end").json() == {
        "active": False, "state": "off", "now": None,
    }
    assert db.get_active_theater_session() is None
    assert client.post("/api/admin/theater/session").status_code == 200


def test_ending_with_no_session_is_refused_rather_than_silently_fine(client):
    setup_admin(client, username="owner")
    assert client.post("/api/admin/theater/end").status_code == 409


def test_the_stage_moves_between_intermission_and_playing_but_never_back(client):
    setup_admin(client, username="owner")
    start(client)
    session = db.get_active_theater_session()
    db.set_theater_state(session["id"], "playing")
    assert db.get_active_theater_session()["state"] == "playing"
    db.end_theater_session(session["id"], int(time.time()))
    # An ended session is ended: set_theater_state must not resurrect it.
    db.set_theater_state(session["id"], "playing")
    assert db.get_active_theater_session() is None


def test_ending_a_session_clears_whatever_it_was_showing(client):
    setup_admin(client, username="owner")
    start(client)
    session = db.get_active_theater_session()
    db.set_theater_now(session["id"], title="A Film", art="abc.jpg")
    asyncio.run(theater.end_session())
    with db.connect() as conn:
        row = conn.execute(
            "SELECT * FROM theater_sessions WHERE id = ?", (session["id"],)
        ).fetchone()
    assert row["state"] == "ended" and row["ended_at"]
    assert row["now_title"] is None and row["now_art"] is None


def test_ending_a_session_wipes_the_chat_it_has_been_holding(client):
    setup_admin(client, username="owner")
    start(client)
    with client.websocket_connect(
        "/ws", cookies={"selfstream_session": client.cookies.get("selfstream_session")}
    ) as ws:
        for _ in range(3):
            ws.receive_json()
        client.post("/api/admin/theater/end")
        # The wipe the session held back, then the state frame saying it is over.
        wipe = ws.receive_json()
        ended = ws.receive_json()
    assert wipe == {"type": "wipe", "reason": "stream_ended"}
    assert ended == {"type": "theater", "active": False, "state": "off", "now": None}


# ---- 2. What a session suppresses -----------------------------------------

def test_going_live_during_a_session_neither_records_nor_announces():
    # The pure seam the stream watcher decides through, asserted directly: the
    # alternative is a poll loop that talks to MediaMTX and ffmpeg, which is a
    # stage set, not a test.
    plan = theater.stream_transition(True, theater_active=True)
    assert plan["record"] is False
    assert plan["notify"] is False
    assert plan["state"] == "playing"


def test_going_offline_during_a_session_holds_the_chat_wipe():
    plan = theater.stream_transition(False, theater_active=True)
    assert plan["wipe"] is False
    assert plan["state"] == "intermission"


def test_without_a_session_every_transition_behaves_exactly_as_before():
    assert theater.stream_transition(True, theater_active=False) == {
        "record": True, "notify": True, "state": None,
    }
    assert theater.stream_transition(False, theater_active=False) == {
        "wipe": True, "state": None,
    }


def test_is_active_follows_the_session(client):
    setup_admin(client, username="owner")
    assert not theater.is_active()
    start(client)
    assert theater.is_active()
    client.post("/api/admin/theater/end")
    assert not theater.is_active()


def test_clips_are_refused_during_a_session_and_say_why(client):
    setup_admin(client, username="owner")
    start(client)
    resp = client.post("/api/clip", json={"name": "a clip"})
    assert resp.status_code == 400
    assert resp.json()["error"] == "Clips are off during theater."
    # And at the source, before anything about the recorder is consulted.
    user = db.get_user("owner")
    assert asyncio.run(media.make_clip(user, "a clip")) == (
        None, "Clips are off during theater."
    )


def test_a_requested_length_does_not_get_a_clip_past_the_refusal(client):
    # The length is picked before the request, so it must not read as a
    # different, allowed shape of request.
    setup_admin(client, username="owner")
    start(client)
    resp = client.post("/api/clip", json={"seconds": 30})
    assert resp.status_code == 400
    assert resp.json()["error"] == "Clips are off during theater."


def test_clips_come_back_when_the_session_ends(client):
    setup_admin(client, username="owner")
    start(client)
    client.post("/api/admin/theater/end")
    resp = client.post("/api/clip", json={"name": "a clip"})
    # Refused for the ordinary reason again, not the theater one.
    assert resp.json()["error"] == "The stream is not live."


# ---- 3. What viewers can read ---------------------------------------------

def test_api_theater_needs_a_session(client):
    assert client.get("/api/theater").status_code == 401


def test_api_theater_is_off_when_nothing_is_running(client):
    setup_admin(client, username="owner")
    assert client.get("/api/theater").json() == {
        "active": False, "state": "off", "now": None,
    }


def test_a_guest_may_read_the_theater_state(client):
    # Watching is the whole of what a guest pass buys, and between titles the
    # intermission card is what there is to watch.
    setup_admin(client, username="owner")
    start(client)
    code = make_pass()
    guest = make_client()
    assert redeem(guest, code).status_code == 200
    body = guest.get("/api/theater").json()
    assert body["active"] is True and body["state"] == "intermission"


def test_the_now_showing_payload_names_the_title_but_never_the_library_id(client):
    setup_admin(client, username="owner")
    start(client)
    session = db.get_active_theater_session()
    db.set_theater_now(
        session["id"], jf_id="abc123", title="The Long Afternoon", year=2019,
        runtime=95, synopsis="A synopsis.", art="abc123.jpg",
    )
    now = client.get("/api/theater").json()["now"]
    assert now == {
        "title": "The Long Afternoon", "year": 2019, "runtime_min": 95,
        "synopsis": "A synopsis.", "art": "/media/art/abc123.jpg",
    }
    assert "abc123" not in str(now["title"])


def test_api_status_is_unchanged_by_a_session(client):
    # The watch page's status poll is the one payload every visitor reads; a
    # session must not add a field to it.
    setup_admin(client, username="owner")
    before = set(client.get("/api/status").json())
    start(client)
    assert set(client.get("/api/status").json()) == before
    assert "theater" not in client.get("/api/status").json()


# ---- 4. The admin routes --------------------------------------------------

@pytest.mark.parametrize("method,path,body", [
    ("POST", "/api/admin/theater/session", None),
    ("POST", "/api/admin/theater/end", None),
    ("POST", "/api/admin/theater/play", {"jf_id": "abc123"}),
    ("POST", "/api/admin/theater/stop", None),
    ("GET", "/api/admin/theater/search?q=afternoon", None),
    ("GET", "/api/admin/theater/projector", None),
    ("POST", "/api/admin/theater/projector/key", None),
])
def test_the_theater_controls_are_admin_only(client, method, path, body):
    setup_admin(client, username="owner")
    add_user("viewer")
    viewer = make_client()
    login(viewer, "viewer")
    resp = viewer.request(method, path, json=body)
    assert resp.status_code == 403
    anon = make_client()
    assert anon.request(method, path, json=body).status_code in (401, 403)


@pytest.mark.parametrize("query", ["", "a", "x" * 65])
def test_search_refuses_a_query_that_is_too_short_or_too_long(client, query):
    setup_admin(client, username="owner")
    working_projector()
    assert client.get(f"/api/admin/theater/search?q={query}").status_code == 400


def test_search_proxies_the_projector_and_trims_what_comes_back(client):
    setup_admin(client, username="owner")
    stub = working_projector()
    results = client.get("/api/admin/theater/search?q=afternoon").json()["results"]
    assert stub.sent[0]["method"] == "search"
    assert stub.sent[0]["params"] == {"query": "afternoon"}
    assert results == [PLAY_DETAIL]


def test_a_search_result_with_an_unusable_id_is_dropped(client):
    # The id becomes a filename on this machine, so it is checked rather than
    # trusted, even though the projector is the operator's own service.
    setup_admin(client, username="owner")
    attach(StubProjector(replies={"search": [
        {"jf_id": "../../etc/passwd", "title": "Nice try"},
        {"jf_id": "ok123", "title": "A Film"},
    ]}))
    results = client.get("/api/admin/theater/search?q=film").json()["results"]
    assert [r["jf_id"] for r in results] == ["ok123"]


def test_playing_a_title_stores_it_writes_its_poster_and_tells_the_room(client):
    setup_admin(client, username="owner")
    start(client)
    stub = working_projector()
    art_path = os.path.join(ART_DIR, "abc123.jpg")
    if os.path.exists(art_path):
        os.remove(art_path)
    resp = client.post(
        "/api/admin/theater/play", json={"jf_id": "abc123", "subtitles": True}
    )
    assert resp.status_code == 200
    assert stub.methods() == ["art", "play"]
    assert stub.sent[1]["params"] == {"jf_id": "abc123", "subtitles": True}
    assert resp.json()["now"]["title"] == "The Long Afternoon"
    assert resp.json()["now"]["art"] == "/media/art/abc123.jpg"
    assert os.path.exists(art_path)
    # Still intermission: the stage only flips when video actually arrives.
    assert resp.json()["state"] == "intermission"
    os.remove(art_path)


def test_playing_survives_a_projector_with_no_poster_for_the_title(client):
    setup_admin(client, username="owner")
    start(client)
    attach(StubProjector(replies={"play": PLAY_DETAIL}, errors=["art"]))
    body = client.post("/api/admin/theater/play", json={"jf_id": "abc123"}).json()
    assert body["now"]["title"] == "The Long Afternoon"
    assert body["now"]["art"] is None


def test_playing_needs_a_session(client):
    setup_admin(client, username="owner")
    working_projector()
    assert client.post(
        "/api/admin/theater/play", json={"jf_id": "abc123"}
    ).status_code == 409


def test_stopping_clears_the_title_without_ending_the_session(client):
    setup_admin(client, username="owner")
    start(client)
    working_projector()
    client.post("/api/admin/theater/play", json={"jf_id": "abc123"})
    body = client.post("/api/admin/theater/stop").json()
    assert body["active"] is True and body["now"] is None
    assert db.get_active_theater_session() is not None


def test_ending_a_session_stops_the_projector_first(client):
    setup_admin(client, username="owner")
    start(client)
    stub = working_projector()
    client.post("/api/admin/theater/play", json={"jf_id": "abc123"})
    client.post("/api/admin/theater/end")
    assert stub.methods()[-1] == "stop"


def test_a_session_still_ends_when_the_projector_cannot_be_stopped(client):
    # A projector that will not answer is a problem on the operator's machine,
    # not a reason to leave the room stuck in a session nobody can close.
    setup_admin(client, username="owner")
    start(client)
    attach(StubProjector(replies={"play": PLAY_DETAIL, "art": {}}, errors=["stop"]))
    client.post("/api/admin/theater/play", json={"jf_id": "abc123"})
    assert client.post("/api/admin/theater/end").status_code == 200
    assert db.get_active_theater_session() is None


@pytest.mark.parametrize("method,path,body", [
    ("POST", "/api/admin/theater/play", {"jf_id": "abc123"}),
    ("POST", "/api/admin/theater/stop", None),
    ("GET", "/api/admin/theater/search?q=afternoon", None),
])
def test_every_route_that_needs_the_projector_answers_502_without_one(
    client, method, path, body
):
    setup_admin(client, username="owner")
    start(client)
    resp = client.request(method, path, json=body)
    assert resp.status_code == 502
    assert resp.json() == {"error": "projector unavailable"}


def test_the_projector_panel_reports_connection_and_key_state(client):
    setup_admin(client, username="owner")
    assert client.get("/api/admin/theater/projector").json() == {
        "connected": False, "last_seen": 0, "has_key": False, "key": "",
    }
    key = db.regenerate_projector_key()
    working_projector()
    body = client.get("/api/admin/theater/projector").json()
    assert body["connected"] is True and body["has_key"] is True
    assert body["key"] == key


def test_regenerating_the_key_changes_it_and_drops_the_live_projector(client):
    setup_admin(client, username="owner")
    old = db.regenerate_projector_key()
    stub = working_projector()
    body = client.post("/api/admin/theater/projector/key").json()
    assert body["key"] != old
    assert db.get_projector_key() == body["key"]
    assert stub.closed == 4401
    assert link.connected() is False


def test_the_key_is_never_seeded_from_the_environment(client):
    # Unlike the publish key, which is seeded from PUBLISH_PASS on an upgrade.
    # No key means no projector can authenticate, which is the right default.
    assert db.get_projector_key() is None


# ---- 5. The projector socket ----------------------------------------------

def connect_projector(client, key):
    return client.websocket_connect(f"/ws/projector?key={key}")


def test_the_projector_socket_accepts_the_current_key(client):
    setup_admin(client, username="owner")
    key = db.regenerate_projector_key()
    with connect_projector(client, key) as ws:
        assert wait_until(link.connected)
        ws.send_json({"event": "status", "state": "idle"})
    assert wait_until(lambda: not link.connected())


@pytest.mark.parametrize("key", ["", "not-the-real-key"])
def test_the_projector_socket_refuses_a_wrong_or_missing_key(client, key):
    setup_admin(client, username="owner")
    db.regenerate_projector_key()
    with connect_projector(client, key) as ws:
        with pytest.raises(WebSocketDisconnect) as excinfo:
            ws.receive_json()
    assert excinfo.value.code == 4401


def test_the_projector_socket_refuses_everything_when_no_key_exists(client):
    setup_admin(client, username="owner")
    assert db.get_projector_key() is None
    with connect_projector(client, "anything") as ws:
        with pytest.raises(WebSocketDisconnect) as excinfo:
            ws.receive_json()
    assert excinfo.value.code == 4401


def test_the_projector_socket_is_rate_limited_per_address(client):
    setup_admin(client, username="owner")
    key = db.regenerate_projector_key()
    # The limiter is checked before the key, so a guessing loop costs a
    # dictionary probe rather than a query and a constant-time compare each.
    for _ in range(10):
        auth.too_many_projector_connects("testclient")
    with connect_projector(client, key) as ws:
        with pytest.raises(WebSocketDisconnect) as excinfo:
            ws.receive_json()
    assert excinfo.value.code == 4429


def test_reset_limiters_covers_the_projector_limiter():
    # Forgetting this is what makes a test pass alone and fail in the suite.
    for _ in range(10):
        auth.too_many_projector_connects("10.0.0.9")
    assert auth.too_many_projector_connects("10.0.0.9")
    auth.reset_limiters()
    assert not auth.too_many_projector_connects("10.0.0.9")


def test_a_new_projector_displaces_the_old_one(client):
    # An operator restarting the projector, or moving it to another machine,
    # must not have to wait for a half-dead socket to time out.
    setup_admin(client, username="owner")
    key = db.regenerate_projector_key()
    with connect_projector(client, key) as first:
        with connect_projector(client, key) as second:
            with pytest.raises(WebSocketDisconnect) as excinfo:
                first.receive_json()
            assert excinfo.value.code == 4409
            # The newer socket is the live one, and the displaced one's exit
            # must not clear it.
            assert wait_until(link.connected)
            second.send_json({"event": "status", "state": "idle"})


# ---- 6. The link itself ---------------------------------------------------

def test_an_rpc_without_a_projector_raises_rather_than_hanging(client):
    with pytest.raises(ProjectorError):
        asyncio.run(link.rpc("ping"))


def test_an_error_reply_is_raised_as_a_projector_error(client):
    attach(StubProjector(errors=["play"]))
    with pytest.raises(ProjectorError):
        asyncio.run(link.rpc("play", {"jf_id": "abc123"}))


def test_a_status_event_of_idle_takes_the_title_off_air(client):
    # ffmpeg dying on its own has to reach the room, or the page keeps showing a
    # title that stopped playing minutes ago.
    setup_admin(client, username="owner")
    start(client)
    session = db.get_active_theater_session()
    db.set_theater_now(session["id"], jf_id="abc123", title="The Long Afternoon")
    asyncio.run(link.handle({"event": "status", "state": "idle"}))
    assert db.get_active_theater_session()["now_title"] is None


def test_the_link_remembers_when_it_last_heard_from_the_projector(client):
    assert link.last_seen() == 0
    asyncio.run(link.handle({"event": "status", "state": "idle"}))
    assert link.last_seen() > 0


# ---- 7. Poster art and the media store ------------------------------------

def test_poster_art_is_re_encoded_and_anything_else_is_refused(client):
    name = theater.save_art("art-test", base64.b64encode(a_jpeg()).decode("ascii"))
    assert name == "art-test.jpg"
    stored = os.path.join(ART_DIR, name)
    assert Image.open(stored).format == "JPEG"
    os.remove(stored)
    assert theater.save_art("art-test", "not base64 at all!!") is None
    assert theater.save_art("art-test", base64.b64encode(b"nope").decode()) is None
    assert theater.save_art("art-test", None) is None


def test_an_id_that_is_not_a_safe_filename_never_reaches_the_disk(client):
    for bad in ("../escape", "a/b", "", "x" * 65, "a b"):
        assert theater.art_filename(bad) is None
        assert theater.save_art(bad, base64.b64encode(a_jpeg()).decode()) is None


def test_retention_and_the_orphan_sweep_leave_poster_art_alone(client):
    # Art lives beside the recordings but is not one: the sweeps walk vods/ and
    # clips/ only, and a poster is not a file "no row points at".
    os.makedirs(ART_DIR, exist_ok=True)
    poster = os.path.join(ART_DIR, "keep-me.jpg")
    with open(poster, "wb") as handle:
        handle.write(a_jpeg())
    orphan = os.path.join(VOD_DIR, "999999.mp4")
    with open(orphan, "wb") as handle:
        handle.write(b"x" * 100)
    try:
        assert media.sweep_orphan_media() >= 1
        assert not os.path.exists(orphan)
        assert os.path.exists(poster)
        # And it does not count against the size cap either, so a poster can
        # never pay for itself by deleting somebody's recording.
        usage = media.media_usage()
        assert usage["total_bytes"] == (
            media._dir_bytes(VOD_DIR) + media._dir_bytes(CLIP_DIR)
        )
    finally:
        os.remove(poster)
