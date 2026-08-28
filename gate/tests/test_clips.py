"""
Clip length and clip naming.

The watch page asks how much of the stream to take, cuts the clip the moment
Save is pressed, and only then offers a name. Two things have to hold for that
to be safe: a client can only ask for a length the operator allows, and a clip
that already exists can be renamed afterwards by the person who made it.

ffmpeg is stubbed here. These tests are about the window and the permissions,
not about the encoder; the media tests cover the cut itself.
"""

import asyncio
import os
import time

import pytest

import db
import media
from config import CLIP_DIR

from test_api import add_user, login, make_client, setup_admin


@pytest.fixture
def live(tmp_path, monkeypatch):
    """A stream that has been recording for ten minutes, with a scratch file on
    disk and ffmpeg stubbed out. Ten minutes so every offered length fits."""
    started = int(time.time()) - 600
    src = tmp_path / "scratch.mp4"
    src.write_bytes(b"scratch bytes" * 100)
    for key, value in (
        ("active", True), ("started_at", started),
        ("tmp_path", str(src)), ("vod_id", None),
    ):
        monkeypatch.setitem(media._rec, key, value)

    async def fake_ffmpeg(args, timeout):
        # The output path is the last argument, for the cut and the poster both.
        with open(args[-1], "wb") as fh:
            fh.write(b"pretend video" * 200)
        return 0, b"", b""

    monkeypatch.setattr(media, "_run_ffmpeg", fake_ffmpeg)
    os.makedirs(CLIP_DIR, exist_ok=True)
    return started


def make_clip_row(name="Clip", creator="owner"):
    now = int(time.time())
    return db.create_clip(name, "1.mp4", creator, None, now - 30, now, 30, now)


# ---- how much to take ------------------------------------------------------

@pytest.mark.parametrize("seconds", [30, 45, 60])
def test_each_offered_length_is_cut_as_asked(client, live, seconds):
    setup_admin(client, username="owner")
    resp = client.post("/api/clip", json={"seconds": seconds})
    assert resp.status_code == 200
    assert db.get_clip(resp.json()["id"])["duration"] == seconds


@pytest.mark.parametrize("seconds", [20, 90, "abc", 0.5, True])
def test_a_length_that_was_never_offered_is_refused(client, live, seconds):
    setup_admin(client, username="owner")
    resp = client.post("/api/clip", json={"seconds": seconds})
    assert resp.status_code == 400
    assert resp.json()["error"] == "Pick one of the offered clip lengths."
    # Refused before anything was written, not cleaned up afterwards.
    assert db.list_clips() == []


def test_the_channel_ceiling_wins_over_what_the_client_asked_for(client, live):
    # The chips are disabled client-side at this setting, but the server is what
    # actually holds the line: a hand-made request cannot take more than this.
    setup_admin(client, username="owner")
    db.set_stream_info(clip_seconds=30)
    resp = client.post("/api/clip", json={"seconds": 60})
    assert resp.status_code == 200
    assert db.get_clip(resp.json()["id"])["duration"] == 30


def test_no_length_at_all_falls_back_to_the_setting(client, live):
    # What the overlay and any older client send. Nothing about that path moved.
    setup_admin(client, username="owner")
    db.set_stream_info(clip_seconds=45)
    resp = client.post("/api/clip", json={})
    assert resp.status_code == 200
    assert db.get_clip(resp.json()["id"])["duration"] == 45


def test_a_clip_still_gets_the_default_name(client, live):
    # The watch page names it in a second step now, so creation sends none.
    setup_admin(client, username="owner")
    resp = client.post("/api/clip", json={"seconds": 30})
    assert db.get_clip(resp.json()["id"])["name"] == "Clip"


def test_make_clip_takes_the_smaller_of_the_two(client, live):
    setup_admin(client, username="owner")
    db.set_stream_info(clip_seconds=60)
    user = db.get_user("owner")
    clip_id, error = asyncio.run(media.make_clip(user, None, seconds=30))
    assert error is None
    assert db.get_clip(clip_id)["duration"] == 30


# ---- naming it afterwards --------------------------------------------------

def test_the_maker_can_name_their_own_clip(client):
    setup_admin(client, username="owner")
    add_user("maker")
    clip_id = make_clip_row(creator="maker")
    maker = make_client()
    login(maker, "maker")
    resp = maker.post(f"/api/clips/{clip_id}/name", json={"name": "that catch"})
    assert resp.status_code == 200
    assert db.get_clip(clip_id)["name"] == "that catch"


@pytest.mark.parametrize("role", ["mod", "admin"])
def test_a_moderator_or_an_admin_can_name_anyones_clip(client, role):
    setup_admin(client, username="owner")
    add_user("them", is_admin=(role == "admin"), is_moderator=(role == "mod"))
    clip_id = make_clip_row(creator="maker")
    other = make_client()
    login(other, "them")
    resp = other.post(f"/api/clips/{clip_id}/name", json={"name": "renamed"})
    assert resp.status_code == 200
    assert db.get_clip(clip_id)["name"] == "renamed"


def test_another_member_cannot_name_someone_elses_clip(client):
    setup_admin(client, username="owner")
    add_user("bystander")
    clip_id = make_clip_row(name="theirs", creator="maker")
    other = make_client()
    login(other, "bystander")
    resp = other.post(f"/api/clips/{clip_id}/name", json={"name": "mine now"})
    assert resp.status_code == 403
    assert db.get_clip(clip_id)["name"] == "theirs"


def test_naming_needs_a_session(client):
    setup_admin(client, username="owner")
    clip_id = make_clip_row()
    assert make_client().post(
        f"/api/clips/{clip_id}/name", json={"name": "hello"}
    ).status_code == 401


def test_naming_a_clip_that_is_not_there(client):
    setup_admin(client, username="owner")
    assert client.post("/api/clips/9999/name", json={"name": "hi"}).status_code == 404


def test_a_name_is_trimmed_and_capped(client):
    setup_admin(client, username="owner")
    clip_id = make_clip_row(creator="owner")
    resp = client.post(
        f"/api/clips/{clip_id}/name", json={"name": "   " + "x" * 200 + "   "}
    )
    assert resp.status_code == 200
    assert db.get_clip(clip_id)["name"] == "x" * 80


@pytest.mark.parametrize("name", ["", "   ", None])
def test_an_empty_name_keeps_the_one_it_has(client, name):
    # Skipping the name step, or clearing the box and saving, must not leave a
    # clip called nothing at all.
    setup_admin(client, username="owner")
    clip_id = make_clip_row(name="Clip", creator="owner")
    resp = client.post(f"/api/clips/{clip_id}/name", json={"name": name})
    assert resp.status_code == 200
    assert db.get_clip(clip_id)["name"] == "Clip"
    assert resp.json()["name"] == "Clip"
