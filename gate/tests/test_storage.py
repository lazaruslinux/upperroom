"""
Storage accounting and the retention sweep.

These exercise the parts of retention that touch the media store, which
test_db.py deliberately avoids: measuring the store, and the size cap, which
decides what goes by adding up what each item weighs. The store is the
in-process fake from fake_store.py, so nothing here can reach a real one.
"""

import asyncio
import time

import pytest

import db
import media


@pytest.fixture
def store(tmp_path, monkeypatch, media_store):
    """A fresh database over the (fake, empty) media store. Returns the store."""
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "test.db"))
    db.init_db()
    return media_store


def _vod_with_file(store, started_at, size, poster=True):
    """A finished recording, its files in the store, and its size recorded the
    way an upload records it. The files are sparse: the size cap reasons about
    gigabytes, and writing those would need gigabytes of disk on every CI run."""
    vod_id = db.create_vod("Show", "", started_at)
    store.truncate("vods", f"{vod_id}.mp4", size)
    if poster:
        store.truncate("vods", f"{vod_id}.jpg", 100)
    db.finalize_vod(vod_id, started_at + 60, 60, f"{vod_id}.mp4",
                    has_poster=poster, size_bytes=size + (100 if poster else 0))
    return vod_id


def test_media_usage_counts_both_areas_and_reports_free_space(store):
    store.truncate("vods", "1.mp4", 3000)
    store.truncate("clips", "1.mp4", 2000)
    usage = asyncio.run(media.media_usage())
    assert usage["available"] is True
    assert usage["vods_bytes"] == 3000
    assert usage["clips_bytes"] == 2000
    assert usage["total_bytes"] == 5000
    # The filesystem numbers come from the store; only their shape is ours.
    assert usage["fs_total_bytes"] > 0
    assert usage["free_bytes"] > 0


def test_published_clips_are_not_counted_twice(store):
    # The public area holds hard links to clips: the same bytes again.
    store.truncate("clips", "1.mp4", 2000)
    store.truncate("shared", "tok.mp4", 2000)
    assert asyncio.run(media.media_usage())["total_bytes"] == 2000


def test_media_usage_says_so_when_the_store_is_away(store):
    store.down()
    assert asyncio.run(media.media_usage()) == {"available": False}


def test_enforce_retention_does_nothing_when_every_limit_is_zero(store):
    now = int(time.time())
    for i in range(4):
        _vod_with_file(store, now - (4 - i) * 100, 1000)
    assert asyncio.run(media.enforce_retention()) == 0
    assert len(db.list_vods()) == 4


def test_enforce_retention_removes_the_files_and_the_poster(store):
    now = int(time.time())
    old = _vod_with_file(store, now - 1000, 1000)
    new = _vod_with_file(store, now, 1000)
    db.set_retention(vod_keep_count=1)
    assert asyncio.run(media.enforce_retention()) == 1
    assert {v["id"] for v in db.list_vods()} == {new}
    assert not store.has("vods", f"{old}.mp4")
    assert not store.has("vods", f"{old}.jpg")          # the poster goes too
    assert store.has("vods", f"{new}.mp4")


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
    now = int(time.time())
    only = _vod_with_file(store, now, 5 * 1024 * 1024 * 1024, poster=False)
    db.set_retention(media_cap_gb=1)
    asyncio.run(media.enforce_retention())
    assert {v["id"] for v in db.list_vods()} == {only}
    assert store.has("vods", f"{only}.mp4")


def test_size_cap_protects_the_newest_of_each_kind(store):
    now = int(time.time())
    gb = 1024 * 1024 * 1024
    vod_id = _vod_with_file(store, now - 500, 3 * gb, poster=False)
    clip_id = db.create_clip("c", "", "alice", None, now, now, 30, now)
    db.set_clip_filename(clip_id, "c.mp4", has_poster=False, size_bytes=3 * gb)
    store.truncate("clips", "c.mp4", 3 * gb)
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


def test_enforce_retention_swallows_a_broken_database(store, monkeypatch):
    # The sweep runs on a timer and from the recording finalize path; it must
    # never raise into either of them.
    monkeypatch.setattr(db, "get_retention", lambda: (_ for _ in ()).throw(RuntimeError))
    assert asyncio.run(media.enforce_retention()) == 0


def test_size_cap_removes_nothing_when_it_cannot_get_under(store):
    # The defect this exists to prevent: usage is measured from the store, but
    # only rows can be deleted, so bytes retention cannot reach (a recording
    # still being written, or a file no row points at) would otherwise be paid
    # for by deleting the entire archive and still be over the cap afterwards.
    now = int(time.time())
    gb = 1024 * 1024 * 1024
    ids = [_vod_with_file(store, now - (3 - i) * 100, gb, poster=False)
           for i in range(3)]
    store.truncate("vods", "orphan.mp4", 50 * gb)     # bytes with no row behind them
    db.set_retention(media_cap_gb=10)
    asyncio.run(media.enforce_retention())
    assert {v["id"] for v in db.list_vods()} == set(ids)


def test_size_cap_waits_for_sizes_it_does_not_know(store):
    # A row from before sizes were recorded cannot be weighed, and a cap that
    # guessed could delete more than it had to.
    now = int(time.time())
    gb = 1024 * 1024 * 1024
    ids = [_vod_with_file(store, now - (3 - i) * 100, gb, poster=False)
           for i in range(3)]
    db.set_media_facts("vod", ids[0], None, None)
    db.set_retention(media_cap_gb=1)
    assert asyncio.run(media.enforce_retention()) == 0
    assert {v["id"] for v in db.list_vods()} == set(ids)


def test_the_orphan_sweep_removes_only_files_no_row_points_at(store):
    now = int(time.time())
    kept = _vod_with_file(store, now, 1000)
    store.write("vods", "9999.mp4")           # left by a gate that stopped early
    store.write("clips", "stray.jpg")
    assert asyncio.run(media.sweep_orphan_media()) == 2
    assert store.has("vods", f"{kept}.mp4")
    assert store.has("vods", f"{kept}.jpg")   # the poster is a known file too
    assert not store.has("vods", "9999.mp4")
    assert not store.has("clips", "stray.jpg")


def test_the_orphan_sweep_spares_a_clip_whose_upload_is_in_flight(store):
    # make_clip writes the row before it uploads, and the row has no filename
    # until the upload is done. The sweep must read that row as the owner of the
    # file arriving under its id, not take the file for an orphan.
    now = int(time.time())
    clip_id = db.create_clip("cutting", "", "alice", None, now, now, 30, now)
    store.write("clips", f"{clip_id}.jpg")
    store.write("clips", f"{clip_id}.mp4")
    assert asyncio.run(media.sweep_orphan_media()) == 0
    assert store.has("clips", f"{clip_id}.mp4")


def test_a_clip_still_being_cut_is_never_a_retention_candidate(store):
    # make_clip writes the row before the clip is cut and uploaded, so for up to
    # forty seconds there is a row with no filename. Deleting it would strand
    # the file that is about to arrive.
    now = int(time.time())
    in_flight = db.create_clip("cutting", "", "alice", None, now, now, 30, now - 500)
    for i in range(3):
        db.create_clip(f"c{i}", f"{i}.mp4", "alice", None, now, now, 30, now - i)
    assert in_flight not in {c["id"] for c in db.retention_candidates()}
    doomed = db.prune_candidates({"clip_keep_count": 1}, now)
    assert in_flight not in {d["id"] for d in doomed}


def test_the_backfill_records_posters_and_sizes_from_the_store(store):
    now = int(time.time())
    with_poster = db.create_vod("Old", "", now - 100)
    db.finalize_vod(with_poster, now, 60, f"{with_poster}.mp4")
    store.truncate("vods", f"{with_poster}.mp4", 5000)
    store.truncate("vods", f"{with_poster}.jpg", 50)
    bare = db.create_clip("c", "", "alice", None, now, now, 30, now)
    db.set_clip_filename(bare, f"{bare}.mp4")
    store.truncate("clips", f"{bare}.mp4", 700)

    assert asyncio.run(media.backfill_media_facts()) == 2
    vod = db.get_vod(with_poster)
    clip = db.get_clip(bare)
    assert (vod["has_poster"], vod["size_bytes"]) == (1, 5050)
    assert (clip["has_poster"], clip["size_bytes"]) == (0, 700)
    # And once it has run there is nothing left to do, and nothing to ask.
    store.calls.clear()
    assert asyncio.run(media.backfill_media_facts()) == 0
    assert store.calls == []


def test_the_backfill_waits_for_a_store_that_is_away(store):
    now = int(time.time())
    vod_id = db.create_vod("Old", "", now - 100)
    db.finalize_vod(vod_id, now, 60, f"{vod_id}.mp4")
    store.down()
    assert asyncio.run(media.backfill_media_facts()) == 0
    assert db.get_vod(vod_id)["has_poster"] is None
