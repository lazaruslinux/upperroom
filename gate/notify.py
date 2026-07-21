"""
Go-live notifications for the upperroom gate.

When a broadcast starts, announce it once over any channel the operator has
configured: a Discord webhook and/or email through an SMTP relay (e.g. Brevo).
Everything here is best effort and gated on configuration: with nothing set,
notifications are simply skipped.
"""

import asyncio
import logging
import smtplib
import time
from email.message import EmailMessage

import httpx

import db
from config import (
    NOTIFY_COOLDOWN, SITE_URL, SMTP_FROM, SMTP_HOST, SMTP_PASS, SMTP_PORT,
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


async def send_live_emails(title):
    """Email everyone who opted in that the channel is live. No-op unless an SMTP
    relay is configured and at least one account has an address."""
    if not (SMTP_HOST and SMTP_FROM):
        return
    recipients = db.list_live_recipients()
    if not recipients:
        return
    lines = [title, "", "The stream just went live."]
    if SITE_URL:
        lines += ["", f"Watch: {SITE_URL}/home"]
    body = "\n".join(lines)
    await asyncio.to_thread(
        _send_emails_blocking, recipients, "upperroom is live", body
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
    discord_text = f"**{title}** is live now."
    if SITE_URL:
        discord_text += f"\n{SITE_URL}/home"
    logger.info("announcing go-live%s", " (test)" if force else "")
    await send_discord(settings["discord_webhook"], discord_text)
    await send_live_emails(title)
