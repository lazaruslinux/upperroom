"""
Go-live notifications for the upperroom gate.

When a broadcast starts, and again an hour before an announced one, tell people
over any channel the operator has configured: a Discord webhook and/or email
through an SMTP relay (e.g. Brevo). Everything here is best effort and gated on
configuration: with nothing set, notifications are simply skipped.
"""

import asyncio
import logging
import smtplib
import time
from email.message import EmailMessage

import httpx

import db
from config import (
    NOTIFY_COOLDOWN, SCHEDULE_CHECK_INTERVAL, SCHEDULE_GRACE,
    SCHEDULE_REMIND_LEAD, SITE_URL, SMTP_FROM, SMTP_HOST, SMTP_PASS, SMTP_PORT,
    SMTP_USER,
)

logger = logging.getLogger("upperroom.notify")


async def send_discord(webhook, content):
    """Post a message to a Discord incoming webhook. Best effort."""
    if not webhook:
        return
    try:
        async with httpx.AsyncClient(timeout=10) as http:
            await http.post(webhook, json={"content": content})
    except httpx.HTTPError as exc:
        logger.warning("discord go-live notification failed: %r", exc)


def _send_emails_blocking(recipients, subject, body):
    """Send the go-live email to each recipient over the SMTP relay. Blocking, so
    it is called from a thread. One bad address never stops the rest."""
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as server:
            server.starttls()
            if SMTP_USER:
                server.login(SMTP_USER, SMTP_PASS)
            for _name, address in recipients:
                message = EmailMessage()
                message["Subject"] = subject
                message["From"] = SMTP_FROM
                message["To"] = address
                message.set_content(body)
                try:
                    server.send_message(message)
                except smtplib.SMTPException as exc:
                    logger.warning(
                        "go-live email to %s failed: %r", address, exc
                    )
                    continue
    except (smtplib.SMTPException, OSError) as exc:
        logger.warning("go-live email relay failed: %r", exc)


def email_enabled():
    """Whether the channel sends go-live email at all: a relay has to be
    configured on the server AND the operator has to have left the dashboard
    switch on. Discord is deliberately not covered by this; the two are
    independent."""
    if not (SMTP_HOST and SMTP_FROM):
        return False
    return bool(db.get_notify_settings()["email_on_live"])


async def send_live_emails(title, site_name="upperroom"):
    """Email everyone who opted in that the channel is live. No-op unless email
    is enabled for the channel and at least one account has an address."""
    if not email_enabled():
        return
    recipients = db.list_live_recipients()
    if not recipients:
        return
    lines = [title, "", "The stream just went live."]
    if SITE_URL:
        lines += ["", f"Watch: {SITE_URL}/home"]
    body = "\n".join(lines)
    await asyncio.to_thread(
        _send_emails_blocking, recipients, f"{site_name} is live", body
    )


async def notify_live(force=False):
    """Announce that the channel went live, over every configured channel. Sends
    at most once per cooldown window unless force=True (a manual test). Best
    effort throughout: a failure on any channel never touches the stream."""
    now = int(time.time())
    settings = db.get_notify_settings()
    if not force:
        if now - settings["last_notified_at"] < NOTIFY_COOLDOWN:
            return
        # Stamp the cooldown before sending so a slow relay cannot let a second
        # transition slip through and double-announce. A test send leaves the
        # real cooldown untouched.
        db.mark_notified(now)
    info = db.get_stream_info()
    title = info["stream_title"] or "Live Stream"
    site_name = info["site_name"] or "upperroom"
    discord_text = f"**{title}** is live now."
    if SITE_URL:
        discord_text += f"\n{SITE_URL}/home"
    logger.info("announcing go-live%s", " (test)" if force else "")
    await send_discord(settings["discord_webhook"], discord_text)
    await send_live_emails(title, site_name)


def schedule_due(now, when, lead):
    """Whether a scheduled stream is close enough to remind about. A plain
    predicate, so the worker's decision can be tested without a clock or a
    relay, the same way the recorder's restart decision is."""
    return bool(when) and when - lead <= now < when


def format_lead(seconds):
    """How far off the stream is, in words, for the reminder message."""
    minutes = max(1, round(seconds / 60))
    if minutes >= 90:
        return f"in about {round(minutes / 60)} hours"
    if minutes >= 50:
        return "in about an hour"
    if minutes == 1:
        return "in about a minute"
    return f"in about {minutes} minutes"


async def notify_scheduled(when, note, now=None):
    """Announce that a scheduled broadcast is coming up, over every configured
    channel. Reuses the go-live recipients and relay, so an operator who has set
    neither up simply gets nothing, exactly as with going live."""
    info = db.get_stream_info()
    site_name = info["site_name"] or "upperroom"
    lead = format_lead(when - (now if now is not None else int(time.time())))
    starts = time.strftime("%H:%M UTC on %a %d %b", time.gmtime(when))
    logger.info("announcing the scheduled stream at %s", starts)
    lines = [f"{site_name} is streaming {lead}.", "", f"Starting {starts}."]
    if note:
        lines += ["", note]
    if SITE_URL:
        lines += ["", f"Watch: {SITE_URL}/home"]
    body = "\n".join(lines)
    settings = db.get_notify_settings()
    discord_text = f"**{site_name}** is streaming {lead} ({starts})."
    if note:
        discord_text += f"\n{note}"
    if SITE_URL:
        discord_text += f"\n{SITE_URL}/home"
    await send_discord(settings["discord_webhook"], discord_text)
    if not email_enabled():
        return
    recipients = db.list_live_recipients()
    if not recipients:
        return
    await asyncio.to_thread(
        _send_emails_blocking, recipients, f"{site_name} is streaming soon", body
    )


async def schedule_worker():
    """Send the reminder when a scheduled stream is close, and clear a schedule
    once it is well past. Runs on a short timer because a reminder an hour
    before is only useful if it lands near the hour."""
    while True:
        try:
            now = int(time.time())
            schedule = db.get_schedule()
            when = schedule["next_stream_at"]
            if schedule_due(now, when, SCHEDULE_REMIND_LEAD):
                # Claim before sending, like the go-live cooldown: if the relay
                # then fails, the reminder is lost rather than mailed twice.
                if db.claim_schedule_reminder(when):
                    await notify_scheduled(when, schedule["next_stream_note"], now)
            db.clear_schedule_if_past(now - SCHEDULE_GRACE)
        except Exception:
            logger.warning("schedule check failed", exc_info=True)
        await asyncio.sleep(SCHEDULE_CHECK_INTERVAL)
