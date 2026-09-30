"""
The gate against a media store that is away.

The store is a separate service, possibly on another machine, and the point of
putting it behind HTTP is that its absence becomes a fast, plain answer instead
of a hang or a lie. These pin what each part of the gate does with that answer:
nothing that lists the library asks the store at all, a clip press fails at once
with a plain message and leaves nothing behind, a finished broadcast is parked
rather than lost, and no row is deleted until the store has answered for its
files.
"""

import asyncio
import os
import time

import pytest

import db
import main
import media
import store

from test_api import make_client, setup_admin
from test_clips import live  # noqa: F401  (the clip press tests use it)
from test_sharing import make_clip_row


def finished_vod(media_store, started_at=None, poster=True):
    started_at = started_at or int(time.time())
    vod_id = db.create_vod("Show", "", started_at)
    media_store.write("vods", f"{vod_id}.mp4", b"recording" * 100)
    if poster:
        media_store.write("vods", f"{vod_id}.jpg", b"poster")
    db.finalize_vod(vod_id, started_at + 60, 60, f"{vod_id}.mp4",
                    has_poster=poster, size_bytes=900 + (6 if poster else 0))
    return vod_id


def publish(client, clip_id):
    url = client.post(f"/api/clips/{clip_id}/share", json={"share": True}).json()["url"]
    return url.rsplit("/", 1)[-1]


# ---- listings never ask --------------------------------------------------

def test_the_library_is_listed_without_asking_the_store(client, media_store):
    setup_admin(client)
    vod_id = finished_vod(media_store)
    clip_id = make_clip_row()
    token = publish(client, clip_id)
    media_store.calls.clear()
    media_store.down()
    stranger = make_client()
    for resp in (
        client.get("/api/vods"),
        client.get("/api/clips"),
        client.get(f"/api/vods/{vod_id}"),
        client.get(f"/api/clips/{clip_id}"),
        stranger.get(f"/api/shared/{token}"),
        stranger.get(f"/clip/{token}"),
    ):
        assert resp.status_code == 200
    assert media_store.calls == [], "a listing must never wait on the store"


@pytest.mark.parametrize("recorded, shown", [(1, True), (0, False), (None, True)])
def test_the_poster_flag_comes_from_the_row(client, media_store, recorded, shown):
    # Unknown means a row from before the column; the browser is let to try.
    setup_admin(client)
    vod_id = finished_vod(media_store)
    db.set_media_facts("vod", vod_id, recorded, None)
    assert client.get("/api/vods").json()["vods"][0]["poster"] is shown


# ---- a clip press --------------------------------------------------------

def test_a_clip_press_with_the_store_away_says_so_and_leaves_nothing(
    client, live, media_store  # noqa: F811
):
    setup_admin(client, username="owner")
    media_store.down()
    began = time.monotonic()
    resp = client.post("/api/clip", json={"seconds": 30})
    assert time.monotonic() - began < 2
    assert resp.status_code == 400
    assert resp.json()["error"] == "Saving clips is not available right now."
    assert db.list_clips() == [], "no half-made row"
    assert not [n for n in os.listdir(media.RECORD_TMP) if n.startswith("clip-")]
    # A clip that could not be saved does not use up the cooldown.
    media_store.back()
    assert client.post("/api/clip", json={"seconds": 30}).status_code == 200


def test_a_clip_whose_video_will_not_go_up_is_not_kept(
    client, live, media_store, monkeypatch  # noqa: F811
):
    # The store takes the poster and then goes away before the video.
    real_put = store.put_file

    async def poster_only(area, name, local_path):
        if name.endswith(".mp4"):
            raise store.StoreError("PUT: connection reset")
        return await real_put(area, name, local_path)

    monkeypatch.setattr(store, "put_file", poster_only)
    setup_admin(client, username="owner")
    resp = client.post("/api/clip", json={"seconds": 30})
    assert resp.json()["error"] == "Saving clips is not available right now."
    assert db.list_clips() == []


def test_a_saved_clip_records_its_poster_and_size(client, live, media_store):  # noqa: F811
    setup_admin(client, username="owner")
    clip_id = client.post("/api/clip", json={"seconds": 30}).json()["id"]
    clip = db.get_clip(clip_id)
    assert clip["filename"] == f"{clip_id}.mp4"
    assert clip["has_poster"] == 1
    assert clip["size_bytes"] == (
        os.path.getsize(media_store.path("clips", f"{clip_id}.mp4"))
        + os.path.getsize(media_store.path("clips", f"{clip_id}.jpg"))
    )
    assert not [n for n in os.listdir(media.RECORD_TMP) if n.startswith("clip-")]


# ---- a finished broadcast ------------------------------------------------

@pytest.fixture
def scratch(tmp_path, monkeypatch, client):
    rec = tmp_path / "rec"
    rec.mkdir()
    monkeypatch.setattr(media, "RECORD_TMP", str(rec))

    async def poster(src, dst, seek=2):
        with open(dst, "wb") as handle:
            handle.write(b"poster")

    monkeypatch.setattr(media, "_make_poster", poster)
    monkeypatch.setitem(media._archiving, "busy", False)
    return rec


def recording(rec, vod_id):
    path = rec / f"{vod_id}.mp4"
    path.write_bytes(b"\0" * 200_000)
    return str(path)


def test_a_store_that_fails_the_remux_call_parks_the_recording(
    scratch, media_store, monkeypatch
):
    async def away(name):
        raise store.StoreError("POST finalize: timed out")

    monkeypatch.setattr(store, "finalize_vod", away)
    vod_id = db.create_vod("A broadcast", "", 1000)
    path = recording(scratch, vod_id)
    asyncio.run(media._finalize_recording(vod_id, path, 1000, 2000))
    assert [row["id"] for row in db.pending_vods()] == [vod_id]
    assert os.path.exists(path)
    assert db.list_vods() == []


def test_a_remux_that_failed_on_the_store_still_keeps_the_recording(
    scratch, media_store, monkeypatch
):
    real = store.finalize_vod

    async def raw(name):
        reply = await real(name)
        return {**reply, "remuxed": False}

    monkeypatch.setattr(store, "finalize_vod", raw)
    vod_id = db.create_vod("A broadcast", "", 1000)
    asyncio.run(media._finalize_recording(vod_id, recording(scratch, vod_id), 1000, 2000))
    vod = db.get_vod(vod_id)
    assert vod["ready"] == 1
    # The store could not say how long it is, so the wall clock does.
    assert vod["duration"] == 1000


# ---- deleting: the row waits for the store -------------------------------

def test_retention_keeps_every_row_while_the_store_is_away(client, media_store):
    now = int(time.time())
    ids = [finished_vod(media_store, now - (3 - i) * 100) for i in range(3)]
    db.set_retention(vod_keep_count=1)
    media_store.down()
    assert asyncio.run(media.enforce_retention()) == 0
    assert {v["id"] for v in db.list_vods()} == set(ids)
    # The first refusal ends the pass: the rest would only wait out the same
    # timeout to learn the same thing.
    assert media_store.calls.count("delete") == 1
    media_store.back()
    assert asyncio.run(media.enforce_retention()) == 2
    assert {v["id"] for v in db.list_vods()} == {ids[-1]}
    assert not media_store.has("vods", f"{ids[0]}.mp4")


def test_the_size_cap_keeps_every_row_while_the_store_is_away(client, media_store):
    now = int(time.time())
    ids = [finished_vod(media_store, now - (3 - i) * 100) for i in range(3)]
    db.set_retention(media_cap_gb=1)
    for vod_id in ids:
        db.set_media_facts("vod", vod_id, True, 1024 ** 3)
        media_store.truncate("vods", f"{vod_id}.mp4", 1024 ** 3)
    media_store.down()
    assert asyncio.run(media.enforce_retention()) == 0
    assert {v["id"] for v in db.list_vods()} == set(ids)


def test_a_half_answered_item_keeps_its_row(client, media_store, monkeypatch):
    # The video went and then the store did: the row stays until a later pass
    # can finish, and the missing video is an answer ("not there") next time.
    vod_id = finished_vod(media_store, int(time.time()) - 500)
    finished_vod(media_store)
    real_delete = store.delete

    async def dies_after_the_video(area, name):
        if name.endswith(".jpg"):
            raise store.StoreError("DELETE: connection refused")
        return await real_delete(area, name)

    monkeypatch.setattr(store, "delete", dies_after_the_video)
    db.set_retention(vod_keep_count=1)
    assert asyncio.run(media.enforce_retention()) == 0
    assert db.get_vod(vod_id) is not None
    monkeypatch.setattr(store, "delete", real_delete)
    assert asyncio.run(media.enforce_retention()) == 1
    assert db.get_vod(vod_id) is None


@pytest.mark.parametrize("kind", ["vods", "clips"])
def test_an_admin_delete_with_the_store_away_keeps_the_item(client, media_store, kind):
    setup_admin(client)
    ref = finished_vod(media_store) if kind == "vods" else make_clip_row()
    media_store.down()
    resp = client.delete(f"/api/{kind}/{ref}")
    assert resp.status_code == 503
    assert "not answering" in resp.json()["error"]
    assert client.get(f"/api/{kind}/{ref}").status_code == 200
    media_store.back()
    assert client.delete(f"/api/{kind}/{ref}").status_code == 200
    assert client.get(f"/api/{kind}/{ref}").status_code == 404
    assert not media_store.has(kind, f"{ref}.mp4")


def test_a_published_clip_stays_whole_if_its_delete_cannot_reach_the_store(
    client, media_store
):
    setup_admin(client)
    clip_id = make_clip_row()
    token = publish(client, clip_id)
    media_store.down()
    assert client.delete(f"/api/clips/{clip_id}").status_code == 503
    media_store.back()
    # Still listed, still shared, and its public file is still where it was:
    # nothing is left reachable without a row that says so.
    assert make_client().get(f"/api/shared/{token}").status_code == 200
    assert media_store.has("shared", f"{token}.mp4")


# ---- sharing -------------------------------------------------------------

def test_sharing_with_the_store_away_says_so_and_shares_nothing(client, media_store):
    setup_admin(client)
    clip_id = make_clip_row()
    media_store.down()
    resp = client.post(f"/api/clips/{clip_id}/share", json={"share": True})
    assert resp.status_code == 503
    assert resp.json()["error"] == "Sharing is not available right now."
    assert db.get_clip(clip_id)["share_token"] is None


def test_sharing_a_clip_whose_file_is_gone_says_that(client, media_store):
    setup_admin(client)
    clip_id = make_clip_row(with_file=False)
    resp = client.post(f"/api/clips/{clip_id}/share", json={"share": True})
    assert resp.status_code == 409
    assert resp.json()["error"] == "That clip's file is missing."
    assert db.get_clip(clip_id)["share_token"] is None


def test_unsharing_with_the_store_away_leaves_it_shared_and_says_so(client, media_store):
    # Dropping the token first would leave the public file reachable with
    # nothing in the database to say it is, and no way to find it again but the
    # startup sweep.
    setup_admin(client)
    clip_id = make_clip_row()
    token = publish(client, clip_id)
    media_store.down()
    resp = client.post(f"/api/clips/{clip_id}/share", json={"share": False})
    assert resp.status_code == 503
    assert db.get_clip(clip_id)["share_token"] == token
    media_store.back()
    assert client.post(
        f"/api/clips/{clip_id}/share", json={"share": False}
    ).status_code == 200
    assert not media_store.has("shared", f"{token}.mp4")


# ---- sweeps and the storage panel ----------------------------------------

def test_the_orphan_sweeps_skip_entirely_when_the_store_cannot_be_listed(
    client, media_store
):
    media_store.write("vods", "9999.mp4")
    media_store.write("shared", "nobody-issued-this.mp4")
    media_store.down()
    assert asyncio.run(media.sweep_orphan_media()) == 0
    assert asyncio.run(media.sweep_orphan_shared()) == 0
    # One listing asked for each, and nothing else: no deletes on a guess.
    assert media_store.calls == ["list", "list"]
    assert media_store.has("vods", "9999.mp4")
    assert media_store.has("shared", "nobody-issued-this.mp4")


def test_the_storage_panel_says_the_store_is_away_and_shows_the_rest(
    client, media_store
):
    setup_admin(client)
    finished_vod(media_store)
    media_store.down()
    resp = client.get("/api/admin/retention")
    assert resp.status_code == 200
    body = resp.json()
    assert body["usage"] == {"available": False}
    assert body["counts"]["vods"] == 1
    assert "vod_keep_count" in body


def test_the_startup_pass_waits_briefly_for_a_store_that_is_starting(
    client, media_store, monkeypatch
):
    # Started together, the store can still be coming up when the gate is. The
    # startup sweeps must not give up on it before it has had a moment.
    media_store.write("vods", "9999.mp4")
    answers = {"n": 0}
    real_usage = store.usage

    async def starting_up():
        answers["n"] += 1
        if answers["n"] < 3:
            raise store.StoreError("GET /usage: connection refused")
        return await real_usage()

    async def no_wait(seconds):
        return None

    monkeypatch.setattr(store, "usage", starting_up)
    monkeypatch.setattr(main.asyncio, "sleep", no_wait)
    asyncio.run(main._startup_store_pass())
    assert answers["n"] == 3
    assert not media_store.has("vods", "9999.mp4")


def test_the_startup_pass_gives_up_on_a_store_that_is_away(
    client, media_store, monkeypatch
):
    waits = []

    async def counted(seconds):
        waits.append(seconds)

    monkeypatch.setattr(main.asyncio, "sleep", counted)
    media_store.write("vods", "9999.mp4")
    media_store.down()
    asyncio.run(main._startup_store_pass())
    # Bounded: a fixed number of short waits, then on without it.
    assert len(waits) == main._STORE_STARTUP_TRIES - 1
    assert media_store.has("vods", "9999.mp4")
