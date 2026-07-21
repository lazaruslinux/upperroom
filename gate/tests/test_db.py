"""
Tests for the selfstream data layer.

db.py is deliberately pure standard library (sqlite3 + hashlib), so these run
without installing FastAPI, geoip, or any of the service dependencies:

    cd gate && python -m pytest

Each test gets its own throwaway database via the fresh_db fixture.
"""

import os
import sys
import time

import pytest

# Import db with the module directory on the path, the same way it is imported
# when the service runs from inside the gate directory.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import db  # noqa: E402


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "test.db"))
    db.init_db()
    return db


# ---- passwords ------------------------------------------------------------

def test_password_roundtrip_and_format(fresh_db):
    stored = db.hash_password("correct horse battery")
    assert stored.startswith("scrypt$")
    assert db.verify_password("correct horse battery", stored)
    assert not db.verify_password("wrong", stored)


def test_verify_rejects_garbage_and_dummy(fresh_db):
    # A malformed stored value must never raise, just return False.
    assert not db.verify_password("anything", "not-a-real-hash")
    assert not db.verify_password("", "")
    # The dummy hash exists so unknown-user logins still take the hashing time;
    # no real password should ever match it.
    assert not db.verify_password("password", db.DUMMY_HASH)


# ---- accounts and roles ---------------------------------------------------

def test_roles_are_independent(fresh_db):
    db.add_user("alice", "Alice", "password1", is_admin=False)
    db.update_user("alice", is_moderator=True)
    row = db.get_user("alice")
    assert row["is_moderator"] == 1
    assert row["is_admin"] == 0          # promoting to mod must not grant admin
    db.update_user("alice", is_admin=True)
    assert db.get_user("alice")["is_moderator"] == 1   # and vice versa


def test_count_admins_and_channel_owner(fresh_db):
    assert db.count_admins() == 0
    db.add_user("owner", "Owner", "password1", is_admin=True)
    time.sleep(0.01)
    db.add_user("second", "Second", "password1", is_admin=True)
    assert db.count_admins() == 2
    # The owner shown on the home card is the longest-standing admin.
    assert db.channel_owner()["username"] == "owner"


def test_delete_user_cascades(fresh_db):
    db.add_user("gone", "Gone", "password1")
    db.start_watch_session("gone", int(time.time()))
    db.log_chat("gone", "Gone", "hi", int(time.time()))
    db.add_ban("gone", "owner", "spam", int(time.time()))
    assert db.delete_user("gone")
    assert db.get_user("gone") is None
    assert db.get_ban("gone") is None
    assert db.user_activity("gone") == {"watch_sessions": [], "chat": []}


# ---- first-run setup gate -------------------------------------------------

def test_setup_gate_creates_first_admin_only(fresh_db):
    now = int(time.time())
    assert db.count_users() == 0
    # The first call makes the account, as admin.
    assert db.create_first_user("owner", "Owner", "password1", now) is True
    row = db.get_user("owner")
    assert row["is_admin"] == 1
    assert db.count_users() == 1
    # Once anyone exists the wizard is spent: no second bootstrap account, and
    # the existing table is left untouched.
    assert db.create_first_user("intruder", "Intruder", "password1", now) is False
    assert db.get_user("intruder") is None
    assert db.count_users() == 1


# ---- go-live notification recipients --------------------------------------

def test_live_recipients_respects_email_and_optout(fresh_db):
    db.add_user("with_email", "A", "password1", email="a@example.com")
    db.add_user("no_email", "B", "password1")                 # no address
    db.add_user("opted_out", "C", "password1", email="c@example.com")
    db.set_notify_live("opted_out", False)
    recipients = dict((email, name) for name, email in db.list_live_recipients())
    assert "a@example.com" in recipients
    assert "c@example.com" not in recipients                  # opted out
    assert len(recipients) == 1                               # no_email excluded


def test_notify_settings_and_cooldown_stamp(fresh_db):
    settings = db.get_notify_settings()
    assert settings["discord_webhook"] == ""
    assert settings["last_notified_at"] == 0
    db.set_discord_webhook("https://discord.com/api/webhooks/xyz")
    db.mark_notified(12345)
    settings = db.get_notify_settings()
    assert settings["discord_webhook"].endswith("/xyz")
    assert settings["last_notified_at"] == 12345


# ---- bans -----------------------------------------------------------------

def test_ban_lifecycle(fresh_db):
    db.add_ban("troll", "mod1", "being a troll", int(time.time()))
    assert db.get_ban("troll")["banned_by"] == "mod1"
    assert "troll" in db.banned_usernames()
    # Re-banning updates rather than duplicating (username is the primary key).
    db.add_ban("troll", "mod2", "again", int(time.time()))
    assert db.get_ban("troll")["banned_by"] == "mod2"
    assert db.remove_ban("troll")
    assert db.get_ban("troll") is None


# ---- VODs, clips, and view de-duplication ---------------------------------

def _ready_vod(title="Show"):
    vod_id = db.create_vod(title, "", int(time.time()))
    db.finalize_vod(vod_id, int(time.time()), 60, f"{vod_id}.mp4")
    return vod_id


def test_view_count_dedupes_per_viewer(fresh_db):
    vod_id = _ready_vod()
    assert db.add_view("vod", vod_id, "alice") == 1
    assert db.add_view("vod", vod_id, "alice") == 1     # same viewer, no bump
    assert db.add_view("vod", vod_id, "bob") == 2       # new viewer counts


def test_media_kind_is_guarded(fresh_db):
    with pytest.raises(ValueError):
        db._media_table("robert'); DROP TABLE users;--")


def test_prune_vods_keeps_newest(fresh_db):
    now = int(time.time())
    ids = []
    for i in range(5):
        vid = db.create_vod(f"V{i}", "", now - (5 - i) * 100)
        db.finalize_vod(vid, now, 60, f"{vid}.mp4")
        ids.append(vid)
    doomed = db.prune_vods(keep_count=2, keep_days=0, now=now)
    remaining = {v["id"] for v in db.list_vods()}
    assert len(remaining) == 2
    assert ids[-1] in remaining and ids[-2] in remaining   # two newest kept
    assert {d["id"] for d in doomed} == set(ids[:3])


def test_last_clip_at(fresh_db):
    assert db.last_clip_at("alice") == 0          # never clipped
    now = int(time.time())
    db.create_clip("c1", "", "alice", None, now - 100, now - 70, 30, now - 100)
    db.create_clip("c2", "", "alice", None, now - 50, now - 20, 30, now - 30)
    assert db.last_clip_at("alice") == now - 30   # most recent wins
    assert db.last_clip_at("bob") == 0


def test_clip_cooldown_settings(fresh_db):
    info = db.get_stream_info()
    assert info["clip_cooldown_user"] == 15       # defaults
    assert info["clip_cooldown_mod"] == 5
    assert info["clip_cooldown_admin"] == 1
    db.set_stream_info(clip_cooldown_user=30, clip_cooldown_admin=0)
    info = db.get_stream_info()
    assert info["clip_cooldown_user"] == 30
    assert info["clip_cooldown_admin"] == 0
    assert info["clip_cooldown_mod"] == 5         # untouched by a partial update


def test_clip_count_since(fresh_db):
    now = int(time.time())
    db.create_clip("c1", "", "alice", None, now, now + 30, 30, now)
    db.create_clip("c2", "", "alice", None, now, now + 30, 30, now)
    db.create_clip("c3", "", "bob", None, now, now + 30, 30, now)
    assert db.count_user_clips_since("alice", now - 86400) == 2
    assert db.count_user_clips_since("bob", now - 86400) == 1
    assert db.count_user_clips_since("alice", now + 100) == 0   # window excludes
