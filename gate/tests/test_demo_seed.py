"""
Tests for the demo-mode seeder.

demo_seed.py imports only db (pure standard library), so these run without
FastAPI or any service dependency, the same as test_db.py:

    cd gate && python -m pytest

The seeder's decision logic is the crux: seed everything on an empty database,
do nothing (and leave a real install alone) when non-demo accounts already exist,
and be safe to re-run.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import db  # noqa: E402
import demo_seed  # noqa: E402


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "test.db"))
    # Keep the demo admin name stable regardless of the host environment.
    monkeypatch.setattr(demo_seed, "ADMIN_USER", "demo")
    db.init_db()
    return db


def _active_invites(label):
    return [
        i for i in db.list_invites()
        if i["label"] == label and not i["redeemed_at"] and not i["revoked_at"]
    ]


def test_seed_empty_creates_everything(fresh_db):
    demo_seed.seed()

    admin = db.get_user("demo")
    assert admin is not None and admin["is_admin"] == 1     # first user is admin

    # Both viewers exist and are plain viewers.
    for username in ("viewer_one", "viewer_two"):
        row = db.get_user(username)
        assert row is not None
        assert row["is_admin"] == 0 and row["is_moderator"] == 0
    assert db.count_users() == 3

    info = db.get_stream_info()
    assert info["stream_title"] == "Demo Stream"
    assert info["stream_description"]                       # non-empty blurb

    invites = _active_invites("try me")
    assert len(invites) == 1                                # one unredeemed code
    assert invites[0]["created_by"] == "demo"

    # One viewer gets a starting balance so the highlight redemption is usable.
    assert db.get_points("viewer_one") == 120

    # And the demo projector is paired, so the theater works with no key to copy.
    assert db.get_projector_key() == demo_seed.PROJECTOR_KEY


def test_seed_is_idempotent(fresh_db):
    demo_seed.seed()
    demo_seed.seed()                                        # second run changes nothing
    assert db.count_users() == 3                            # no duplicate accounts
    assert len(_active_invites("try me")) == 1              # no duplicate invite
    assert db.get_points("viewer_one") == 120               # balance not re-added


def test_seed_never_overwrites_an_existing_projector_key(fresh_db):
    # Same rule as the publish key: once a key exists, the seed is a no-op, so
    # running the demo profile can never revoke a real operator's projector.
    mine = db.regenerate_projector_key()
    demo_seed.seed()
    assert db.get_projector_key() == mine


def test_seed_refuses_real_install(fresh_db):
    # A pre-existing, non-demo account means this is someone's real database.
    db.add_user("realowner", "Real Owner", "password1", is_admin=True)

    demo_seed.seed()

    assert db.get_user("demo") is None                     # no demo admin created
    assert db.get_user("viewer_one") is None               # no viewers created
    assert db.count_users() == 1                            # untouched
    assert _active_invites("try me") == []                 # no invite minted
    assert db.get_stream_info()["stream_title"] == "Live Stream"   # default kept
