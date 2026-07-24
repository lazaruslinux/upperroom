"""
Storage accounting and the retention sweep.

These exercise the parts of retention that touch the disk, which test_db.py
deliberately avoids: measuring the media store, and the size cap, which decides
what goes by looking at file sizes rather than at rows. The media directories
are pointed at a scratch path per test, so nothing here can reach a real store.
"""

import asyncio
import os
import time

import pytest

import db
import media


@pytest.fixture
def store(tmp_path, monkeypatch):
    """A throwaway media store with empty vods/ and clips/ dirs, and a fresh
    database. Returns the two directories."""
    vods = tmp_path / "vods"
    clips = tmp_path / "clips"
    vods.mkdir()
    clips.mkdir()
    monkeypatch.setattr(media, "VOD_DIR", str(vods))
    monkeypatch.setattr(media, "CLIP_DIR", str(clips))
    monkeypatch.setattr(media, "MEDIA_DIR", str(tmp_path))
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "test.db"))
    db.init_db()
    return vods, clips


def _write(path, size):
    """A file that reports `size` bytes without occupying them. The size cap
    reasons about gigabytes, and actually writing those would need gigabytes of
    disk on every CI run; a sparse file stats identically and costs nothing."""
    with open(path, "wb") as handle:
        handle.truncate(size)


def _vod_with_file(store, started_at, size, poster=True):
    vods, _ = store
    vod_id = db.create_vod("Show", "", started_at)
    db.finalize_vod(vod_id, started_at + 60, 60, f"{vod_id}.mp4")
    _write(vods / f"{vod_id}.mp4", size)
    if poster:
        _write(vods / f"{vod_id}.jpg", 100)
    return vod_id


def test_media_usage_counts_both_dirs_and_reports_free_space(store):
    vods, clips = store
    _write(vods / "1.mp4", 3000)
    _write(clips / "1.mp4", 2000)
    usage = media.media_usage()
    assert usage["vods_bytes"] == 3000
    assert usage["clips_bytes"] == 2000
    assert usage["total_bytes"] == 5000
    # The filesystem numbers come from the OS; only their shape is ours to check.
    assert usage["fs_total_bytes"] > 0
    assert usage["free_bytes"] > 0


def test_media_usage_survives_a_missing_directory(store, monkeypatch):
    monkeypatch.setattr(media, "VOD_DIR", "/nonexistent/vods")
    assert media.media_usage()["vods_bytes"] == 0


def test_enforce_retention_does_nothing_when_every_limit_is_zero(store):
    now = int(time.time())
    for i in range(4):
        _vod_with_file(store, now - (4 - i) * 100, 1000)
    assert asyncio.run(media.enforce_retention()) == 0
    assert len(db.list_vods()) == 4


def test_enforce_retention_removes_the_files_and_the_poster(store):
    vods, _ = store
    now = int(time.time())
    old = _vod_with_file(store, now - 1000, 1000)
    new = _vod_with_file(store, now, 1000)
    db.set_retention(vod_keep_count=1)
    assert asyncio.run(media.enforce_retention()) == 1
    assert {v["id"] for v in db.list_vods()} == {new}
    assert not (vods / f"{old}.mp4").exists()
    assert not (vods / f"{old}.jpg").exists()          # the poster goes too
    assert (vods / f"{new}.mp4").exists()


def test_size_cap_deletes_oldest_first_until_under_the_limit(store):
    now = int(time.time())
    gb = 1024 * 1024 * 1024
    ids = [_vod_with_file(store, now - (4 - i) * 100, gb, poster=False)
           for i in range(4)]
    db.set_retention(media_cap_gb=2)
    asyncio.run(media.enforce_retention())
    remaining = {v["id"] for v in db.list_vods()}
    assert remaining == {ids[-1], ids[-2]}             # oldest two went


def test_size_cap_never_deletes_the_newest_recording(store):
    # One recording bigger than the whole cap must not delete itself: doing so
    # would wipe every broadcast the moment it finished, forever.
    vods, _ = store
    now = int(time.time())
    only = _vod_with_file(store, now, 5 * 1024 * 1024 * 1024, poster=False)
    db.set_retention(media_cap_gb=1)
    asyncio.run(media.enforce_retention())
    assert {v["id"] for v in db.list_vods()} == {only}
    assert (vods / f"{only}.mp4").exists()


def test_size_cap_protects_the_newest_of_each_kind(store):
    vods, clips = store
    now = int(time.time())
    gb = 1024 * 1024 * 1024
    vod_id = _vod_with_file(store, now - 500, 3 * gb, poster=False)
    clip_id = db.create_clip("c", "c.mp4", "alice", None, now, now, 30, now)
    _write(clips / "c.mp4", 3 * gb)
    db.set_retention(media_cap_gb=1)
    asyncio.run(media.enforce_retention())
    assert {v["id"] for v in db.list_vods()} == {vod_id}
    assert {c["id"] for c in db.list_clips()} == {clip_id}


def test_size_cap_leaves_pinned_items_alone(store):
    now = int(time.time())
    gb = 1024 * 1024 * 1024
    ids = [_vod_with_file(store, now - (3 - i) * 100, gb, poster=False)
           for i in range(3)]
    db.set_media_keep("vod", ids[0], True)
    db.set_retention(media_cap_gb=1)
    asyncio.run(media.enforce_retention())
    assert ids[0] in {v["id"] for v in db.list_vods()}


def test_enforce_retention_swallows_a_broken_store(store, monkeypatch):
    # The sweep runs on a timer and from the recording finalize path; it must
    # never raise into either of them.
    monkeypatch.setattr(db, "get_retention", lambda: (_ for _ in ()).throw(RuntimeError))
    assert asyncio.run(media.enforce_retention()) == 0
