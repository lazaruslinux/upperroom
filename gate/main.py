"""
upperroom gate and chat service.

This service is the brains of upperroom. It:

  - logs viewers in with a named account and password
  - issues a signed session cookie that lasts a few hours
  - answers the check Caddy makes before it serves any video
  - runs the live chat and the watching list over a WebSocket
  - reports whether the stream is currently live

No accounts are hard coded here and no secrets are written in this file.
Accounts live in a SQLite database that the admin manages with manage.py, and
every sensitive value is read from the environment.

The service is split into a small package: config (env parsing), auth
(sessions, rate limit, geo), hub (chat and presence), media (recording, clips,
thumbnails, stream watcher), notify and webpush (go-live push), and routes/*
(APIRouters grouped by area). This module just assembles them into `app`.
"""

import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.datastructures import Headers
from starlette.responses import JSONResponse

import auth
import config
import db
import store
import webpush
from hub import chat_purge_worker, hub
from media import (
    backfill_media_facts, cleanup_record_scratch, retention_worker,
    retry_pending_archives, stream_watcher, sweep_orphan_media,
    sweep_orphan_shared, thumbnail_worker,
)
from routes import admin as admin_routes
from routes import auth as auth_routes
from routes import media as media_routes
from routes import mod as mod_routes
from routes import points as points_routes
from routes import push as push_routes
from routes import theater as theater_routes
from routes import ws as ws_routes

logger = logging.getLogger("upperroom.gate")


def _log_startup_summary():
    """A one-line-ish summary of how the gate is configured, at startup. Never
    logs secrets (no JWT secret, no push key)."""
    logger.info("upperroom gate starting; log level %s", config.LOG_LEVEL)
    logger.info(
        "stream path=%s, allowed countries=%s, geo gate=%s",
        config.STREAM_PATH,
        ",".join(sorted(config.ALLOWED_COUNTRIES)) or "(none)",
        "on" if auth._geo_reader else "off",
    )
    limits = db.get_retention()
    logger.info(
        "media store=%s (key %s), record scratch=%s, retention=%s",
        config.STORE_URL,
        "set" if config.STORE_KEY else "MISSING: nothing can be archived",
        config.RECORD_TMP,
        ", ".join(f"{k}={v}" for k, v in limits.items() if v) or "off",
    )
    logger.info(
        "notifications: push=%s, site url=%s, cooldown=%ss",
        "on" if webpush.ready() else "off (set SELFSTREAM_SITE_URL)",
        config.SITE_URL or "(unset)",
        config.NOTIFY_COOLDOWN,
    )


def _startup_notify_pass():
    """Empty the contact details the old go-live email and Discord post left
    in the database, and make the push key pair if there is none. Both are
    idempotent; only counts are logged, never values."""
    emails, hooks = db.clear_legacy_contacts()
    if emails or hooks:
        logger.info(
            "cleared %d stored email address(es) and %d webhook URL(s)", emails, hooks
        )
    if webpush.ensure_vapid_keys():
        logger.info("generated the push (VAPID) key pair")


# How long the startup pass gives a store that is still starting: ten tries, two
# seconds apart, which covers a container started alongside this one.
_STORE_STARTUP_TRIES = 10
_STORE_STARTUP_PAUSE = 2


async def _startup_store_pass():
    """The startup work that needs the media store, in the background: sweep
    the store for files no row points at, record what older items weigh, then
    try once to archive the recordings a previous run parked, so a restart
    after the store comes back is all it takes.

    A background task and not part of startup on purpose. Every step here is a
    call to a store that may be on another machine and may be away, and each
    parked recording is a whole broadcast to upload and remux. Awaited before
    the server starts serving, that is a site refusing connections at exactly
    the moment somebody restarted it to get it back. The sweeps are safe to run
    beside live traffic (they list before they read the rows; see
    sweep_orphan_media), and the hourly retry in retention_worker is the
    backstop if this finds the store still away."""
    # Started together, the store may still be coming up when the gate is, and
    # a sweep that found it "away" would be skipped until the next restart. So
    # it gets a short grace first. Bounded, because a store that really is away
    # is exactly the case this must not wait on for long.
    for attempt in range(_STORE_STARTUP_TRIES):
        try:
            await store.usage()
            break
        except store.StoreError:
            if attempt + 1 < _STORE_STARTUP_TRIES:
                await asyncio.sleep(_STORE_STARTUP_PAUSE)
    # Files whose rows were dropped at startup are bytes nothing points at,
    # which would otherwise count against the size cap.
    await sweep_orphan_media()
    # And the public area. This is the one place where a stale file means
    # strangers can still watch something that was deleted, so it is checked on
    # every start rather than trusted to the publish and delete paths alone.
    await sweep_orphan_shared()
    await backfill_media_facts()
    try:
        await retry_pending_archives()
    except Exception:
        logger.warning("pending archive retry failed at startup", exc_info=True)


@asynccontextmanager
async def lifespan(_app):
    _log_startup_summary()
    hub.load_bans()
    # Any VOD still marked unfinished is from a recording the previous run never
    # got to close out; drop those rows so they do not linger.
    try:
        db.clear_unfinished_vods()
    except Exception:
        logger.warning("clear_unfinished_vods failed at startup", exc_info=True)
    # Their scratch files can outlive the rows (a restart mid-recording), so sweep
    # the recording scratch dir of anything not tied to an active recording. A
    # recording parked by a failed archive is spared by the sweep itself.
    cleanup_record_scratch()
    try:
        _startup_notify_pass()
    except Exception:
        logger.warning("notification startup pass failed", exc_info=True)
    tasks = [
        asyncio.create_task(_startup_store_pass()),
        asyncio.create_task(stream_watcher()),
        asyncio.create_task(thumbnail_worker()),
        asyncio.create_task(chat_purge_worker()),
        asyncio.create_task(retention_worker()),
    ]
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()


class RefuseCrossSiteWrites:
    """Refuse any write that a browser says came from another page's origin.

    The session cookie's SameSite=Lax already keeps it off another site's forms,
    but not off a sibling subdomain's, and every write here is a JSON POST that
    a text/plain form could imitate. Reads are left alone: they change nothing,
    and the link preview fetchers that read /watch send no Origin anyway. Plain
    ASGI rather than BaseHTTPMiddleware, so nothing about streamed responses or
    the WebSocket routes changes; the chat socket checks its own Origin."""

    SAFE = frozenset({"GET", "HEAD", "OPTIONS"})

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if (scope["type"] == "http" and scope["method"] not in self.SAFE
                and not auth.origin_allowed(Headers(scope=scope))):
            refused = JSONResponse(
                {"error": "Refused: this request came from another site."},
                status_code=403,
            )
            await refused(scope, receive, send)
            return
        await self.app(scope, receive, send)


app = FastAPI(title="upperroom", docs_url=None, redoc_url=None, lifespan=lifespan)
app.add_middleware(RefuseCrossSiteWrites)
db.init_db()
for _dir in (config.AVATAR_DIR, config.RECORD_TMP, config.ART_DIR):
    os.makedirs(_dir, exist_ok=True)

app.include_router(auth_routes.router)
app.include_router(media_routes.router)
app.include_router(admin_routes.router)
app.include_router(mod_routes.router)
app.include_router(points_routes.router)
app.include_router(push_routes.router)
app.include_router(theater_routes.router)
app.include_router(ws_routes.router)
