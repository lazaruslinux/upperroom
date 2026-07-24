"""
The scheduled-stream reminder.

The decision to remind is a plain predicate, tested here the way the recorder's
restart decision is, and the send path is driven with the Discord and SMTP calls
replaced, so nothing here touches the network.
"""

import asyncio
import time

import pytest

import db
import notify
from config import SCHEDULE_REMIND_LEAD


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "test.db"))
    db.init_db()
    return db


@pytest.fixture
def sent(monkeypatch):
    """Capture what would have gone out, instead of sending it."""
    calls = {"discord": [], "email": []}

    async def fake_discord(webhook, content):
        calls["discord"].append(content)

    def fake_emails(recipients, subject, body):
        calls["email"].append((list(recipients), subject, body))

    monkeypatch.setattr(notify, "send_discord", fake_discord)
    monkeypatch.setattr(notify, "_send_emails_blocking", fake_emails)
    monkeypatch.setattr(notify, "SMTP_HOST", "smtp.example.com")
    monkeypatch.setattr(notify, "SMTP_FROM", "notify@example.com")
    return calls


@pytest.mark.parametrize(
    "offset,due,why",
    [
        (SCHEDULE_REMIND_LEAD + 60, False, "still more than the lead away"),
        (SCHEDULE_REMIND_LEAD, True, "exactly at the lead time"),
        (60, True, "inside the lead window"),
        (0, False, "already started"),
        (-600, False, "already over"),
    ],
)
def test_schedule_due_window(offset, due, why):
    now = 1_700_000_000
    assert notify.schedule_due(now, now + offset, SCHEDULE_REMIND_LEAD) is due, why


def test_nothing_scheduled_is_never_due():
    assert notify.schedule_due(1_700_000_000, 0, SCHEDULE_REMIND_LEAD) is False


@pytest.mark.parametrize(
    "seconds,expected",
    [
        (3600, "in about an hour"),
        (60, "in about a minute"),
        (600, "in about 10 minutes"),
        (7200, "in about 2 hours"),
    ],
)
def test_format_lead_reads_like_a_person_wrote_it(seconds, expected):
    assert notify.format_lead(seconds) == expected


def test_the_reminder_goes_out_once_and_carries_the_note(fresh_db, sent):
    db.add_user("viewer", "Viewer", "password1", email="viewer@example.com")
    when = int(time.time()) + 1800
    db.set_schedule(when, "Week four")

    # What the worker does on one pass, without its sleep loop.
    def one_pass():
        now = int(time.time())
        schedule = db.get_schedule()
        if notify.schedule_due(now, schedule["next_stream_at"], SCHEDULE_REMIND_LEAD):
            if db.claim_schedule_reminder(schedule["next_stream_at"]):
                asyncio.run(
                    notify.notify_scheduled(
                        schedule["next_stream_at"], schedule["next_stream_note"], now
                    )
                )

    one_pass()
    assert len(sent["email"]) == 1
    recipients, subject, body = sent["email"][0]
    assert recipients == [("Viewer", "viewer@example.com")]
    assert "streaming soon" in subject
    assert "Week four" in body
    assert len(sent["discord"]) == 1
    # The worker runs every minute for the whole hour before; it must not send
    # again on any of those passes.
    for _ in range(5):
        one_pass()
    assert len(sent["email"]) == 1


def test_the_reminder_is_skipped_when_nobody_opted_in(fresh_db, sent):
    db.add_user("owner", "Owner", "password1", is_admin=True, email="owner@example.com")
    when = int(time.time()) + 1800
    asyncio.run(notify.notify_scheduled(when, "", int(time.time())))
    # Admins run the broadcast, so they are excluded from the recipient list and
    # there is nobody left to mail.
    assert sent["email"] == []


def test_the_reminder_still_posts_to_discord_without_smtp(fresh_db, sent, monkeypatch):
    monkeypatch.setattr(notify, "SMTP_HOST", "")
    db.set_discord_webhook("https://discord.example/hook")
    when = int(time.time()) + 1800
    asyncio.run(notify.notify_scheduled(when, "Week four", int(time.time())))
    assert sent["email"] == []
    assert "Week four" in sent["discord"][0]


def test_going_live_retires_a_schedule_that_is_due_now(fresh_db):
    # Set for twenty minutes' time, then go live early: the announcement has
    # served its purpose and should not linger.
    now = int(time.time())
    db.set_schedule(now + 1200, "tonight")
    assert db.clear_schedule_if_past(now + 7200) is True
    assert db.get_schedule()["next_stream_at"] == 0


def test_going_live_leaves_next_week_alone(fresh_db):
    now = int(time.time())
    db.set_schedule(now + 7 * 86400, "next week")
    assert db.clear_schedule_if_past(now + 7200) is False
    assert db.get_schedule()["next_stream_at"] == now + 7 * 86400
