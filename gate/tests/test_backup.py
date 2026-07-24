"""
Backup and restore.

A backup is only worth having if it restores, so these drive the real archive
format both ways, and lean hardest on the refusals: a restore must never be the
thing that loses the data that was already there, and it must not trust an
archive that came back from somewhere else.
"""

import json
import os
import sqlite3
import tarfile
import time

import pytest

import db
import manage


@pytest.fixture
def channel(tmp_path, monkeypatch):
    """A populated database and avatar dir, with the module paths pointed at
    them. Returns the tmp dir."""
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "upperroom.db"))
    avatars = tmp_path / "avatars"
    avatars.mkdir()
    (avatars / "alice.jpg").write_bytes(b"avatar-bytes")
    monkeypatch.setattr(manage, "AVATAR_DIR", str(avatars))
    monkeypatch.setattr(manage, "MEDIA_DIR", str(tmp_path / "media"))
    monkeypatch.setattr(manage, "BACKUP_DIR", str(tmp_path / "backups"))
    db.init_db()
    db.add_user("alice", "Alice", "password1", is_admin=True)
    db.add_user("bob", "Bob", "password1")
    db.set_stream_info(site_name="Northwind Live")
    return tmp_path


def test_backup_is_consistent_and_holds_what_it_claims(channel):
    archive = manage.make_backup()
    assert archive.startswith(str(channel / "backups"))
    manifest = manage.read_manifest(archive)
    assert manifest["app"] == "upperroom"
    assert manifest["users"] == 2 and manifest["admins"] == 1
    assert manifest["avatars"] == 1
    assert "channel_settings" in manifest["tables"]
    with tarfile.open(archive) as tar:
        names = set(tar.getnames())
    assert manage.DB_NAME in names
    assert manage.MANIFEST_NAME in names
    assert "avatars/alice.jpg" in names


def test_backup_copy_passes_its_own_integrity_check(channel, tmp_path):
    copy = tmp_path / "copy.db"
    db.backup_to(copy)
    assert manage.check_database(str(copy)) == []
    conn = sqlite3.connect(copy)
    try:
        assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 2
    finally:
        conn.close()


def test_backup_refuses_to_overwrite(channel, tmp_path):
    target = tmp_path / "taken.tar.gz"
    target.write_bytes(b"not a backup")
    with pytest.raises(FileExistsError):
        manage.make_backup(out=str(target))
    assert target.read_bytes() == b"not a backup"


def test_backup_leaves_no_partial_file_behind(channel):
    manage.make_backup()
    leftovers = [n for n in os.listdir(channel / "backups") if n.endswith(".partial")]
    assert leftovers == []


def test_restore_round_trips_accounts_and_settings(channel):
    archive = manage.make_backup()
    # Lose everything: a different, emptier channel in the same place.
    os.remove(db.DB_PATH)
    db.init_db()
    db.add_user("stranger", "Stranger", "password1")
    summary = manage.do_restore(archive, db.DB_PATH, manage.AVATAR_DIR, force=True)
    assert summary["manifest"]["users"] == 2
    assert db.get_user("alice")["is_admin"] == 1
    assert db.get_user("stranger") is None
    assert db.get_stream_info()["site_name"] == "Northwind Live"


def test_restore_refuses_without_force_when_a_database_exists(channel):
    archive = manage.make_backup()
    with pytest.raises(FileExistsError):
        manage.do_restore(archive, db.DB_PATH, manage.AVATAR_DIR)
    assert db.get_user("alice") is not None      # nothing was touched


def test_restore_moves_the_old_database_aside_rather_than_deleting_it(channel):
    archive = manage.make_backup()
    db.add_user("carol", "Carol", "password1")   # a change made after the backup
    summary = manage.do_restore(archive, db.DB_PATH, manage.AVATAR_DIR, force=True)
    kept = summary["kept"][db.DB_PATH]
    assert os.path.exists(kept)
    assert db.get_user("carol") is None          # the restore really replaced it
    conn = sqlite3.connect(kept)                 # and carol is still recoverable
    try:
        names = {r[0] for r in conn.execute("SELECT username FROM users")}
    finally:
        conn.close()
    assert "carol" in names


def test_restore_puts_the_avatars_back(channel):
    archive = manage.make_backup()
    os.remove(os.path.join(manage.AVATAR_DIR, "alice.jpg"))
    manage.do_restore(archive, db.DB_PATH, manage.AVATAR_DIR, force=True)
    restored = os.path.join(manage.AVATAR_DIR, "alice.jpg")
    assert open(restored, "rb").read() == b"avatar-bytes"


def test_restore_reports_media_files_that_are_not_here(channel):
    vod_id = db.create_vod("Show", "", int(time.time()))
    db.finalize_vod(vod_id, int(time.time()), 60, f"{vod_id}.mp4")
    archive = manage.make_backup()
    summary = manage.do_restore(archive, db.DB_PATH, manage.AVATAR_DIR, force=True)
    # Recordings are never in a backup, so the row survives without its file and
    # the operator is told rather than left to discover it.
    assert summary["missing_media"] == 1


def _tar_with(tmp_path, entries, name="evil.tar.gz"):
    """A hand-built archive with exactly the members given. The payloads are
    staged in their own directory: one of them is named like the real database,
    and staging beside it would overwrite the channel under test."""
    staging = tmp_path / "staging"
    staging.mkdir(exist_ok=True)
    archive = tmp_path / name
    with tarfile.open(archive, "w:gz") as tar:
        for member_name, payload in entries.items():
            blob = staging / os.path.basename(member_name)
            blob.write_bytes(payload)
            tar.add(blob, arcname=member_name)
    return str(archive)


def test_restore_rejects_an_archive_from_something_else(channel, tmp_path):
    archive = _tar_with(tmp_path, {
        manage.MANIFEST_NAME: json.dumps({"app": "something-else"}).encode(),
        manage.DB_NAME: b"",
    })
    with pytest.raises(ValueError):
        manage.do_restore(archive, db.DB_PATH, manage.AVATAR_DIR, force=True)
    assert db.get_user("alice") is not None


def test_restore_rejects_a_member_that_escapes_the_directory(channel, tmp_path):
    archive = _tar_with(tmp_path, {
        manage.MANIFEST_NAME: json.dumps({"app": "upperroom"}).encode(),
        "../escape.txt": b"pwned",
    })
    with pytest.raises(ValueError):
        manage.read_manifest(archive)
    assert not os.path.exists(tmp_path.parent / "escape.txt")


def test_restore_rejects_an_unexpected_member(channel, tmp_path):
    archive = _tar_with(tmp_path, {
        manage.MANIFEST_NAME: json.dumps({"app": "upperroom"}).encode(),
        "run-me.sh": b"#!/bin/sh\n",
    })
    with pytest.raises(ValueError):
        manage.read_manifest(archive)


def test_restore_rejects_a_corrupt_database(channel, tmp_path):
    archive = _tar_with(tmp_path, {
        manage.MANIFEST_NAME: json.dumps({"app": "upperroom"}).encode(),
        manage.DB_NAME: b"this is not a database",
    })
    with pytest.raises(ValueError):
        manage.do_restore(archive, db.DB_PATH, manage.AVATAR_DIR, force=True)
    assert db.get_user("alice") is not None      # the live database is untouched


def test_restore_rejects_a_database_missing_tables(channel, tmp_path):
    stub = tmp_path / "stub.db"
    conn = sqlite3.connect(stub)
    conn.execute("CREATE TABLE users (username TEXT)")
    conn.commit()
    conn.close()
    archive = _tar_with(tmp_path, {
        manage.MANIFEST_NAME: json.dumps({"app": "upperroom"}).encode(),
        manage.DB_NAME: stub.read_bytes(),
    })
    with pytest.raises(ValueError) as caught:
        manage.do_restore(archive, db.DB_PATH, manage.AVATAR_DIR, force=True)
    assert "missing tables" in str(caught.value)
