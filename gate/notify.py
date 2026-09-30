"""
Go-live notifications for the upperroom gate.

When a broadcast starts, send a Web Push notice to every device that turned it
on (webpush.py does the sending). Best effort throughout: with the dashboard
switch off, or SITE_URL unset, nothing is sent, and a failure never touches the
stream.
"""

import logging
import time

import db
import webpush
from config import NOTIFY_COOLDOWN, THEATER_ENABLED

logger = logging.getLogger("upperroom.notify")


def live_payload():
    """What the go-live notice says. The service worker always opens /watch,
    so no URL rides along."""
    info = db.get_stream_info()
    site_name = info["site_name"] or "upperroom"
    body = info["stream_title"] or "Live now."
    # A theater session silences the game label, as the status and the link
    # preview do. Read through db: theater imports this module.
    in_theater = THEATER_ENABLED and db.get_active_theater_session() is not None
    game = db.get_now_playing()
    if game and not in_theater:
        body += f"\nPlaying {game}"
    return {"title": f"{site_name} is live", "body": body}


async def notify_live():
    """Announce that the channel went live. Sends at most once per cooldown
    window."""
    now = int(time.time())
    settings = db.get_notify_settings()
    if now - settings["last_notified_at"] < NOTIFY_COOLDOWN:
        return
    # Stamp the cooldown before sending so a slow push service cannot let a
    # second transition slip through and double-announce.
    db.mark_notified(now)
    if not settings["notify_on_live"] or not webpush.ready():
        return
    subscriptions = db.list_push_subscriptions()
    if not subscriptions:
        return
    try:
        result = await webpush.send(subscriptions, live_payload())
    except Exception:
        logger.warning("go-live push failed", exc_info=True)
        return
    logger.info(
        "announced go-live: %d sent, %d failed", result["sent"], result["failed"]
    )
