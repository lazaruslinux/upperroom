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


# ---- invites --------------------------------------------------------------

def test_invite_code_format_and_lookup(fresh_db):
    now = int(time.time())
    code = db.create_invite("for grandma", "owner", now)
    assert code.count("-") == 2            # three words joined by dashes
    assert code == code.lower()
    inv = db.get_invite(code)
    assert inv["label"] == "for grandma"
    assert inv["created_by"] == "owner"
    assert inv["revoked_at"] is None and inv["redeemed_at"] is None
    assert any(i["code"] == code for i in db.list_invites())


def test_invite_redeem_creates_viewer(fresh_db):
    now = int(time.time())
    code = db.create_invite("", "owner", now)
    assert db.register_via_invite(code, "alice", "Alice", "password1", now) == "ok"
    row = db.get_user("alice")
    assert row is not None
    assert row["is_admin"] == 0 and row["is_moderator"] == 0   # viewer only
    assert row["invite_code"] == code                          # provenance stamped
    inv = db.get_invite(code)
    assert inv["redeemed_by"] == "alice"
    assert inv["redeemed_at"] == now


def test_invite_is_single_use(fresh_db):
    now = int(time.time())
    code = db.create_invite("", "owner", now)
    assert db.register_via_invite(code, "alice", "Alice", "password1", now) == "ok"
    # A second redeemer of the same code is rejected and no account is made.
    assert db.register_via_invite(code, "bob", "Bob", "password1", now) == "used"
    assert db.get_user("bob") is None
    assert db.get_invite(code)["redeemed_by"] == "alice"       # first claim stands


def test_invite_taken_username_does_not_burn_code(fresh_db):
    now = int(time.time())
    db.add_user("alice", "Alice", "password1")
    code = db.create_invite("", "owner", now)
    assert db.register_via_invite(code, "alice", "Alice", "password1", now) == "user_exists"
    # The claim is rolled back, so the code stays usable by someone else.
    inv = db.get_invite(code)
    assert inv["redeemed_at"] is None and inv["revoked_at"] is None
    assert db.register_via_invite(code, "carol", "Carol", "password1", now) == "ok"


def test_invite_revoke_blocks_redeem(fresh_db):
    now = int(time.time())
    code = db.create_invite("", "owner", now)
    assert db.revoke_invite(code, now) is True
    assert db.get_invite(code)["revoked_at"] == now
    assert db.register_via_invite(code, "alice", "Alice", "password1", now) == "used"
    # Revoking is only for active codes: a second revoke, and revoking a redeemed
    # code, both report no change.
    assert db.revoke_invite(code, now) is False
    used = db.create_invite("", "owner", now)
    db.register_via_invite(used, "dave", "Dave", "password1", now)
    assert db.revoke_invite(used, now) is False


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


# ---- Retention ------------------------------------------------------------

def _aged_vods(count, now, spacing=100):
    """`count` finished VODs, oldest first, spaced apart in time."""
    ids = []
    for i in range(count):
        vid = db.create_vod(f"V{i}", "", now - (count - i) * spacing)
        db.finalize_vod(vid, now, 60, f"{vid}.mp4")
        ids.append(vid)
    return ids


def test_retention_is_off_on_a_fresh_install(fresh_db):
    # Every limit zero, and a sweep with those limits removes nothing. This is
    # the promise that a new install never deletes a recording on its own.
    limits = db.get_retention()
    assert limits == {field: 0 for field in db.RETENTION_FIELDS}
    now = int(time.time())
    _aged_vods(5, now)
    assert db.prune_media(limits, now) == []
    assert len(db.list_vods()) == 5


def test_prune_media_keeps_the_newest_by_count(fresh_db):
    now = int(time.time())
    ids = _aged_vods(5, now)
    doomed = db.prune_media({"vod_keep_count": 2}, now)
    remaining = {v["id"] for v in db.list_vods()}
    assert remaining == {ids[-1], ids[-2]}                 # two newest kept
    assert {d["id"] for d in doomed} == set(ids[:3])
    assert all(d["kind"] == "vod" for d in doomed)


def test_a_pinned_vod_survives_and_does_not_use_up_a_slot(fresh_db):
    # The pin is the whole safety story: an operator who pins a recording keeps
    # it no matter how low the limit goes, and pinning it does not push another
    # recording out to make room.
    now = int(time.time())
    ids = _aged_vods(4, now)
    db.set_media_keep("vod", ids[0], True)
    db.prune_media({"vod_keep_count": 2}, now)
    remaining = {v["id"] for v in db.list_vods()}
    assert remaining == {ids[0], ids[-1], ids[-2]}         # pinned plus two newest


def test_prune_media_age_limit_ignores_the_count(fresh_db):
    now = int(time.time())
    ids = _aged_vods(3, now, spacing=86400 * 3)            # 9, 6 and 3 days old
    db.prune_media({"vod_keep_days": 5}, now)
    assert {v["id"] for v in db.list_vods()} == {ids[-1]}


def test_prune_media_prunes_clips_too(fresh_db):
    # Clips were never pruned before retention v2; they are now a first-class
    # kind with their own limits.
    now = int(time.time())
    for i in range(4):
        db.create_clip(f"c{i}", f"{i}.mp4", "alice", None, now, now, 30, now - (4 - i) * 10)
    doomed = db.prune_media({"clip_keep_count": 1}, now)
    assert len(doomed) == 3 and all(d["kind"] == "clip" for d in doomed)
    assert len(db.list_clips()) == 1


def test_prune_media_leaves_the_other_kind_alone(fresh_db):
    now = int(time.time())
    _aged_vods(3, now)
    db.create_clip("c", "c.mp4", "alice", None, now, now, 30, now)
    db.prune_media({"vod_keep_count": 1}, now)
    assert len(db.list_clips()) == 1


def test_retention_settings_update_only_what_was_passed(fresh_db):
    db.set_retention(vod_keep_count=5, media_cap_gb=100)
    db.set_retention(vod_keep_days=30)
    limits = db.get_retention()
    assert limits["vod_keep_count"] == 5                   # untouched by the second call
    assert limits["media_cap_gb"] == 100
    assert limits["vod_keep_days"] == 30
    with pytest.raises(ValueError):
        db.set_retention(nonsense=1)


def test_set_media_keep_reports_a_missing_row(fresh_db):
    vod_id = _ready_vod()
    assert db.set_media_keep("vod", vod_id, True) is True
    assert db.set_media_keep("vod", vod_id + 999, True) is False
    with pytest.raises(ValueError):
        db.set_media_keep("nope", 1, True)


def test_retention_candidates_are_oldest_first_and_skip_pins(fresh_db):
    now = int(time.time())
    ids = _aged_vods(3, now)
    db.create_clip("c", "c.mp4", "alice", None, now, now, 30, now - 1000)
    db.set_media_keep("vod", ids[0], True)
    candidates = db.retention_candidates()
    assert [c["ts"] for c in candidates] == sorted(c["ts"] for c in candidates)
    assert ("vod", ids[0]) not in {(c["kind"], c["id"]) for c in candidates}
    assert any(c["kind"] == "clip" for c in candidates)


def test_count_media_counts_pins_across_both_kinds(fresh_db):
    now = int(time.time())
    ids = _aged_vods(2, now)
    db.create_clip("c", "c.mp4", "alice", None, now, now, 30, now)
    db.set_media_keep("vod", ids[0], True)
    assert db.count_media() == {"vods": 2, "clips": 1, "pinned": 1}


def test_retention_seeds_from_the_environment_only_on_an_upgrade(fresh_db, monkeypatch):
    # An install that predates dashboard retention has been pruning by the old
    # environment variables. The upgrade must carry those over exactly once, so
    # nobody's disk usage changes silently; a genuinely fresh database ignores
    # them and starts with retention off.
    monkeypatch.setenv("SELFSTREAM_VOD_KEEP", "7")
    monkeypatch.setenv("SELFSTREAM_VOD_KEEP_DAYS", "3")
    with db.connect() as conn:
        conn.execute("ALTER TABLE channel_settings DROP COLUMN vod_keep_count")
    db.init_db()
    assert db.get_retention()["vod_keep_count"] == 7
    assert db.get_retention()["vod_keep_days"] == 3
    # Re-running init on the now-current database must not re-seed or reset.
    db.set_retention(vod_keep_count=2)
    db.init_db()
    assert db.get_retention()["vod_keep_count"] == 2
    # A genuinely new database ignores the same environment and starts off.
    monkeypatch.setattr(db, "DB_PATH", db.DB_PATH + ".new")
    db.init_db()
    assert db.get_retention() == {field: 0 for field in db.RETENTION_FIELDS}


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


def test_accent_defaults_green_and_persists(fresh_db):
    # A fresh channel is the green flavor by default.
    assert db.get_stream_info()["accent"] == "green"
    db.set_stream_info(accent="amber")
    assert db.get_stream_info()["accent"] == "amber"
    # A partial update that omits accent leaves the stored flavor untouched.
    db.set_stream_info(title="Something")
    assert db.get_stream_info()["accent"] == "amber"
    # An unknown flavor never reaches the column.
    with pytest.raises(ValueError):
        db.set_stream_info(accent="rainbow")
    assert db.get_stream_info()["accent"] == "amber"


def test_overlay_key_generate_persist_and_regenerate(fresh_db):
    # A fresh channel has no overlay key until one is generated.
    assert db.get_overlay_key() is None
    key = db.regenerate_overlay_key()
    assert key
    # It persists and reads back as the same value.
    assert db.get_overlay_key() == key
    # Regenerating replaces it with a different value, revoking the old URL.
    key2 = db.regenerate_overlay_key()
    assert key2 != key
    assert db.get_overlay_key() == key2


def test_stream_key_generate_persist_and_regenerate(fresh_db):
    # A fresh channel has no publish key until one is generated.
    assert db.get_stream_key() is None
    key = db.regenerate_stream_key()
    assert key
    # It persists and reads back as the same value.
    assert db.get_stream_key() == key
    # Regenerating replaces it, rotating the key that OBS must use.
    key2 = db.regenerate_stream_key()
    assert key2 != key
    assert db.get_stream_key() == key2


def test_stream_key_seeds_from_env_only_while_unset(tmp_path, monkeypatch):
    # On first init the publish key is seeded from PUBLISH_PASS so an existing
    # install keeps publishing after the switch to gate-delegated auth.
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "seed.db"))
    monkeypatch.setenv("PUBLISH_PASS", "legacy-obs-pass")
    db.init_db()
    assert db.get_stream_key() == "legacy-obs-pass"
    # Once a key exists, a later env change never overwrites it: the seed is a
    # one-time bootstrap, not an ongoing source of truth.
    monkeypatch.setenv("PUBLISH_PASS", "some-other-pass")
    db.init_db()
    assert db.get_stream_key() == "legacy-obs-pass"


# ---- channel points -------------------------------------------------------

def test_points_credit_dedupes_and_accumulates(fresh_db):
    db.add_user("alice", "Alice", "password1")
    db.add_user("bob", "Bob", "password1")
    assert db.get_points("alice") == 0
    # One round: each distinct account is credited once, even if a username
    # appears several times (a viewer with several tabs open).
    updated = db.credit_points(["alice", "alice", "bob"], 1)
    assert updated == 2                        # two accounts touched, not three
    assert db.get_points("alice") == 1         # deduped, not doubled
    assert db.get_points("bob") == 1
    db.credit_points(["alice"], 5)
    assert db.get_points("alice") == 6         # accumulates across rounds


def test_spend_points_guards_the_balance(fresh_db):
    db.add_user("alice", "Alice", "password1")
    db.credit_points(["alice"], 50)
    assert db.spend_points("alice", 30) == 20     # affordable: decremented
    assert db.spend_points("alice", 30) is None   # too low: no change, no debt
    assert db.get_points("alice") == 20


def test_spend_points_is_atomic_under_a_race(fresh_db):
    # Two redemptions of 60 racing on a balance that covers only one. The guarded
    # UPDATE serializes them, so exactly one wins and the balance never goes
    # negative, no matter the interleaving.
    import threading

    db.add_user("alice", "Alice", "password1")
    db.credit_points(["alice"], 100)
    results = []

    def spend():
        results.append(db.spend_points("alice", 60))

    threads = [threading.Thread(target=spend) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert results.count(None) == 1               # exactly one was refused
    assert db.get_points("alice") == 40           # only one spend landed


def test_clip_count_since(fresh_db):
    now = int(time.time())
    db.create_clip("c1", "", "alice", None, now, now + 30, 30, now)
    db.create_clip("c2", "", "alice", None, now, now + 30, 30, now)
    db.create_clip("c3", "", "bob", None, now, now + 30, 30, now)
    assert db.count_user_clips_since("alice", now - 86400) == 2
    assert db.count_user_clips_since("bob", now - 86400) == 1
    assert db.count_user_clips_since("alice", now + 100) == 0   # window excludes
