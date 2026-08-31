"""
A recording that finished but could not be archived must survive.

The media store can live on a network mount (a NAS, a pool on another machine),
and the one thing that makes genuinely worse is the moment a broadcast ends: the
recording is complete on local scratch, and the move into the store is the step
that can fail. Before this, that failure lost the recording twice over. The row
was left unfinished, so the next start dropped it, and the scratch file was then
an orphan, so the next start deleted that too.

These tests pin the three pieces that stop it: the failure parks the recording,
neither startup sweep touches a parked one, and the retry archives it.
"""

import asyncio
import os
import shutil
import sqlite3

import pytest

import db
import media


@pytest.fixture
def store(tmp_path, monkeypatch, client):
    """Isolated scratch and media dirs plus a fresh database.

    `client` is depended on for its database, not for requests: it is what points
    db.DB_PATH at a per-test file.
    """
    rec = tmp_path / "rec"
    vods = tmp_path / "vods"
    rec.mkdir()
    vods.mkdir()
    monkeypatch.setattr(media, "RECORD_TMP", str(rec))
    monkeypatch.setattr(media, "VOD_DIR", str(vods))
    # No recording in flight unless a test says so. _rec is process-global, so a
    # test that left it active would make the next one's retry a no-op.
    for field in ("active", "vod_id", "tmp_path", "started_at"):
        monkeypatch.setitem(media._rec, field, None)
    monkeypatch.setitem(media._rec, "active", False)
    return rec, vods


def _recording(rec, vod_id):
    """A scratch file big enough to count as a real recording."""
    path = rec / f"{vod_id}.mp4"
    path.write_bytes(b"\0" * 200_000)
    return str(path)


def _store_is_gone(monkeypatch):
    """Make every write to the media store fail, the way an unreachable mount
    does: the remux returns non-zero and the fallback move raises."""
    async def failing_ffmpeg(args, timeout):
        return 1, "", "no such file or directory"

    def failing_move(src, dst):
        raise OSError("media store unreachable")

    monkeypatch.setattr(media, "_run_ffmpeg", failing_ffmpeg)
    monkeypatch.setattr(shutil, "move", failing_move)


def _store_is_back(monkeypatch):
    """Make the archive succeed: ffmpeg writes its output file and reports ok."""
    async def working_ffmpeg(args, timeout):
        with open(args[-1], "wb") as handle:
            handle.write(b"\0" * 200_000)
        return 0, "", ""

    async def duration(path):
        return 42

    monkeypatch.setattr(media, "_run_ffmpeg", working_ffmpeg)
    monkeypatch.setattr(media, "_probe_duration", duration)


# --- 1. A failed archive parks the recording instead of losing it ------------

def test_a_failed_archive_keeps_the_row_and_the_file(store, monkeypatch):
    rec, _ = store
    vod_id = db.create_vod("A broadcast", "", 1000)
    path = _recording(rec, vod_id)
    _store_is_gone(monkeypatch)

    asyncio.run(media._finalize_recording(vod_id, path, 1000, 2000))

    pending = db.pending_vods()
    assert [row["id"] for row in pending] == [vod_id]
    assert pending[0]["pending_path"] == path
    assert pending[0]["ended_at"] == 2000
    assert os.path.exists(path), "the recording itself must still be on disk"


def test_a_parked_recording_is_not_listed_as_a_finished_one(store, monkeypatch):
    rec, _ = store
    vod_id = db.create_vod("A broadcast", "", 1000)
    _store_is_gone(monkeypatch)
    asyncio.run(media._finalize_recording(vod_id, _recording(rec, vod_id), 1000, 2000))

    # It is not playable, so it must not appear on the landing page.
    assert [row["id"] for row in db.list_vods()] == []


# --- 2. Neither startup sweep may touch a parked recording -------------------

def test_the_startup_row_sweep_spares_a_parked_recording(store, monkeypatch):
    rec, _ = store
    parked = db.create_vod("Parked", "", 1000)
    interrupted = db.create_vod("Interrupted", "", 3000)
    _store_is_gone(monkeypatch)
    asyncio.run(media._finalize_recording(parked, _recording(rec, parked), 1000, 2000))

    dropped = db.clear_unfinished_vods()

    # The one the gate never finished recording goes; the parked one stays.
    assert dropped == [interrupted]
    assert [row["id"] for row in db.pending_vods()] == [parked]


def test_the_startup_scratch_sweep_spares_a_parked_recording(store, monkeypatch):
    rec, _ = store
    vod_id = db.create_vod("Parked", "", 1000)
    path = _recording(rec, vod_id)
    litter = rec / "99.mp4"
    litter.write_bytes(b"\0" * 200_000)
    _store_is_gone(monkeypatch)
    asyncio.run(media._finalize_recording(vod_id, path, 1000, 2000))

    media.cleanup_record_scratch()

    assert os.path.exists(path), "a parked recording must survive the sweep"
    assert not litter.exists(), "real litter must still be cleared"


def test_the_scratch_sweep_deletes_nothing_when_the_database_cannot_be_read(
    store, monkeypatch
):
    # Not knowing what is parked is a reason to leave everything alone, never a
    # reason to start deleting recordings.
    rec, _ = store
    path = _recording(rec, 7)

    def unreadable():
        raise RuntimeError("database is locked")

    monkeypatch.setattr(db, "pending_vods", unreadable)
    media.cleanup_record_scratch()

    assert os.path.exists(path)


# --- 3. The retry archives it -----------------------------------------------

def test_the_retry_archives_a_parked_recording(store, monkeypatch):
    rec, vods = store
    vod_id = db.create_vod("Parked", "", 1000)
    path = _recording(rec, vod_id)
    _store_is_gone(monkeypatch)
    asyncio.run(media._finalize_recording(vod_id, path, 1000, 2000))

    _store_is_back(monkeypatch)
    assert asyncio.run(media.retry_pending_archives()) == 1

    assert db.pending_vods() == []
    assert [row["id"] for row in db.list_vods()] == [vod_id]
    assert (vods / f"{vod_id}.mp4").exists()
    assert not os.path.exists(path), "scratch is released once the archive lands"


def test_the_retry_drops_a_recording_whose_file_has_vanished(store, monkeypatch):
    rec, _ = store
    vod_id = db.create_vod("Parked", "", 1000)
    path = _recording(rec, vod_id)
    _store_is_gone(monkeypatch)
    asyncio.run(media._finalize_recording(vod_id, path, 1000, 2000))

    os.remove(path)
    assert asyncio.run(media.retry_pending_archives()) == 0

    # Nothing left to archive, so the row must not sit pending for ever.
    assert db.pending_vods() == []
    assert db.get_vod(vod_id) is None


def test_the_retry_waits_while_a_broadcast_is_recording(store, monkeypatch):
    rec, _ = store
    vod_id = db.create_vod("Parked", "", 1000)
    _store_is_gone(monkeypatch)
    asyncio.run(media._finalize_recording(vod_id, _recording(rec, vod_id), 1000, 2000))

    monkeypatch.setitem(media._rec, "active", True)
    _store_is_back(monkeypatch)
    assert asyncio.run(media.retry_pending_archives()) == 0
    assert [row["id"] for row in db.pending_vods()] == [vod_id]


# --- 4. The archive landed and only the database write failed ----------------

def test_a_finalize_that_fails_after_the_remux_loses_nothing(store, monkeypatch):
    """The narrow window that used to cost a whole recording.

    The scratch copy was released the moment the remux succeeded, before the row
    was told which file to play. A finalize that raised in between (a busy
    database, a full disk) left nothing to park: the row was still unfinished, so
    the next start dropped it, and the archived file was then pointed at by
    nothing, so the orphan sweep deleted that too."""
    rec, vods = store
    vod_id = db.create_vod("A broadcast", "", 1000)
    path = _recording(rec, vod_id)
    _store_is_back(monkeypatch)
    real_finalize = db.finalize_vod

    def locked(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(db, "finalize_vod", locked)
    asyncio.run(media._finalize_recording(vod_id, path, 1000, 2000))

    archived = vods / f"{vod_id}.mp4"
    assert archived.exists(), "the remux did land, and it is the archive"
    assert os.path.exists(path), "the scratch copy must not have been released"
    assert [row["id"] for row in db.pending_vods()] == [vod_id]

    # The three sweeps a restart runs, in the order main.py runs them.
    db.clear_unfinished_vods()
    media.cleanup_record_scratch()
    media.sweep_orphan_media()

    assert [row["id"] for row in db.pending_vods()] == [vod_id], "the row survives"
    assert os.path.exists(path), "the recording survives"
    assert archived.exists(), "and so does what was already archived"

    # And with the database answering again, the retry finishes the job.
    monkeypatch.setattr(db, "finalize_vod", real_finalize)
    assert asyncio.run(media.retry_pending_archives()) == 1
    assert [row["id"] for row in db.list_vods()] == [vod_id]
    assert not os.path.exists(path)


def test_one_archive_pass_runs_at_a_time(store, monkeypatch):
    # The startup retry, the hourly retry and a broadcast's own archive can all
    # ask at once, and a recording is parked for the whole of its own archive, so
    # a second pass would find it mid-flight and remux it again.
    rec, _ = store
    vod_id = db.create_vod("Parked", "", 1000)
    _store_is_gone(monkeypatch)
    asyncio.run(media._finalize_recording(vod_id, _recording(rec, vod_id), 1000, 2000))

    _store_is_back(monkeypatch)
    monkeypatch.setitem(media._archiving, "busy", True)
    assert asyncio.run(media.retry_pending_archives()) == 0
    assert [row["id"] for row in db.pending_vods()] == [vod_id]


def test_a_recording_is_parked_for_the_whole_of_its_own_archive(store, monkeypatch):
    # What makes the reordering safe: from the first destructive step until the
    # row names an archived file, both startup sweeps spare the row and the file.
    rec, _ = store
    vod_id = db.create_vod("A broadcast", "", 1000)
    path = _recording(rec, vod_id)
    seen = {}

    async def note_the_park(src, dst, seek=2):
        seen["parked"] = [row["id"] for row in db.pending_vods()]

    monkeypatch.setattr(media, "_make_poster", note_the_park)
    _store_is_back(monkeypatch)
    asyncio.run(media._finalize_recording(vod_id, path, 1000, 2000))

    assert seen["parked"] == [vod_id], "parked before the archive starts"
    assert db.pending_vods() == [], "and released once the row names the file"


def test_a_retry_does_not_duplicate_the_chat_replay(store, monkeypatch):
    # The replay is snapshotted on the way through, so an archive that runs twice
    # would otherwise leave every line in the recording's chat doubled.
    rec, _ = store
    vod_id = db.create_vod("Parked", "", 1000)
    db.log_chat("someone", "Someone", "hello", 1500)
    _store_is_back(monkeypatch)
    path = _recording(rec, vod_id)

    asyncio.run(media._finalize_recording(vod_id, path, 1000, 2000))
    _recording(rec, vod_id)
    asyncio.run(media._finalize_recording(vod_id, path, 1000, 2000))

    assert len(db.get_replay("vod", vod_id)) == 1
