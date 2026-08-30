"""
The one-time "what changed" notice.

The rule it has to keep: one notice per person per release, for the release
actually running, and never a queue of them for somebody who skipped a few.
"""

import pytest

import changelog
import db
from config import VERSION
from test_api import add_user, login, setup_admin
from conftest import make_client


@pytest.fixture
def notes(monkeypatch):
    """A known notice for whatever version the suite is running as, so these
    tests do not go red every time the real changelog is edited."""
    monkeypatch.setitem(changelog.NOTES, VERSION, ["First thing", "Second thing"])
    return ["First thing", "Second thing"]


def test_an_existing_account_is_offered_the_notice_once(client, notes):
    setup_admin(client, username="owner")
    add_user("nell")
    # An account made before this release has read nothing.
    db.mark_version_seen("nell", "")
    nell = make_client()
    login(nell, "nell")

    body = nell.get("/api/me").json()
    assert body["whats_new"] == {"version": VERSION, "notes": notes}

    assert nell.post("/api/whats-new/seen").status_code == 200
    assert nell.get("/api/me").json()["whats_new"] is None


def test_a_new_account_is_not_greeted_with_a_changelog(client, notes):
    # Nothing changed for somebody who has only just arrived.
    setup_admin(client, username="owner")
    add_user("nell")
    nell = make_client()
    login(nell, "nell")
    assert nell.get("/api/me").json()["whats_new"] is None


def test_the_first_admin_is_not_greeted_either(client, notes):
    setup_admin(client, username="owner")
    assert client.get("/api/me").json()["whats_new"] is None


def test_somebody_who_skipped_releases_sees_only_the_newest(client, monkeypatch):
    monkeypatch.setitem(changelog.NOTES, "0.0.1", ["Ancient"])
    monkeypatch.setitem(changelog.NOTES, VERSION, ["Newest"])
    setup_admin(client, username="owner")
    add_user("nell")
    db.mark_version_seen("nell", "0.0.1")
    nell = make_client()
    login(nell, "nell")
    body = nell.get("/api/me").json()["whats_new"]
    assert body["version"] == VERSION
    assert body["notes"] == ["Newest"]


def test_a_release_with_nothing_to_say_shows_no_notice(client, monkeypatch):
    monkeypatch.delitem(changelog.NOTES, VERSION, raising=False)
    setup_admin(client, username="owner")
    add_user("nell")
    db.mark_version_seen("nell", "")
    nell = make_client()
    login(nell, "nell")
    assert nell.get("/api/me").json()["whats_new"] is None


def test_the_notice_is_capped_at_five_lines(monkeypatch):
    monkeypatch.setitem(changelog.NOTES, VERSION, [f"line {i}" for i in range(9)])
    assert len(changelog.current()["notes"]) == changelog.MAX_NOTES == 5


def test_acknowledging_stamps_the_running_release_not_what_was_sent(client, notes):
    # A tab left open across an upgrade must not be able to mark a release it
    # never showed as read, which would swallow that release's notice.
    setup_admin(client, username="owner")
    add_user("nell")
    db.mark_version_seen("nell", "")
    nell = make_client()
    login(nell, "nell")
    nell.post("/api/whats-new/seen", json={"version": "99.0.0"})
    assert db.get_user("nell")["last_seen_version"] == VERSION


def test_a_signed_out_visitor_cannot_acknowledge_anything(client):
    setup_admin(client, username="owner")
    assert make_client().post("/api/whats-new/seen").status_code == 401


def test_the_shipped_changelog_keeps_to_its_own_budget():
    """The real notes, not a fixture: five lines is the whole window, and a
    line long enough to wrap three times is not a bullet point."""
    for version, lines in changelog.NOTES.items():
        assert len(lines) <= changelog.MAX_NOTES, version
        for line in lines:
            assert line and len(line) <= 90, (version, line)


def test_the_running_release_has_notes():
    """A version bump that forgets its notes ships a silent release, which is
    exactly the thing this feature exists to stop."""
    assert changelog.current() is not None
