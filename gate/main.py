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
thumbnails, stream watcher), notify (go-live announcements), and routes/*
(APIRouters grouped by area). This module just assembles them into `app`.
"""

import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI

import auth
import config
import db
from hub import chat_purge_worker, hub
from notify import schedule_worker
from media import (
    cleanup_record_scratch, retention_worker, stream_watcher, sweep_orphan_media,
    sweep_orphan_shared,
    thumbnail_worker,
)
from routes import admin as admin_routes
from routes import auth as auth_routes
from routes import guest as guest_routes
from routes import media as media_routes
from routes import mod as mod_routes
from routes import points as points_routes
from routes import theater as theater_routes
from routes import ws as ws_routes

logger = logging.getLogger("upperroom.gate")


def _log_startup_summary():
    """A one-line-ish summary of how the gate is configured, at startup. Never
    logs secrets (no JWT secret, no SMTP password)."""
    logger.info("upperroom gate starting; log level %s", config.LOG_LEVEL)
    logger.info(
        "stream path=%s, allowed countries=%s, geo gate=%s",
        config.STREAM_PATH,
        ",".join(sorted(config.ALLOWED_COUNTRIES)) or "(none)",
        "on" if auth._geo_reader else "off",
    )
    limits = db.get_retention()
    logger.info(
        "media dir=%s, record scratch=%s, retention=%s",
        config.MEDIA_DIR,
        config.RECORD_TMP,
        ", ".join(f"{k}={v}" for k, v in limits.items() if v) or "off",
    )
    logger.info(
        "notifications: smtp=%s, site url=%s, cooldown=%ss",
        "configured" if (config.SMTP_HOST and config.SMTP_FROM) else "off",
        config.SITE_URL or "(unset)",
        config.NOTIFY_COOLDOWN,
    )


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
    # the recording scratch dir of anything not tied to an active recording.
    cleanup_record_scratch()
    # And the archived side: files whose rows were dropped above are bytes
    # nothing points at, which would otherwise count against the size cap.
    sweep_orphan_media()
    # And the public directory. This is the one place where a stale file means
    # strangers can still watch something that was deleted, so it is checked on
    # every start rather than trusted to the publish and delete paths alone.
    sweep_orphan_shared()
    tasks = [
        asyncio.create_task(stream_watcher()),
        asyncio.create_task(thumbnail_worker()),
        asyncio.create_task(chat_purge_worker()),
        asyncio.create_task(retention_worker()),
        asyncio.create_task(schedule_worker()),
        asyncio.create_task(auth.guest_reaper()),
    ]
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()


app = FastAPI(title="upperroom", docs_url=None, redoc_url=None, lifespan=lifespan)
db.init_db()
for _dir in (config.AVATAR_DIR, config.RECORD_TMP, config.VOD_DIR, config.CLIP_DIR,
             config.SHARED_DIR, config.ART_DIR):
    os.makedirs(_dir, exist_ok=True)

app.include_router(auth_routes.router)
app.include_router(guest_routes.router)
app.include_router(media_routes.router)
app.include_router(admin_routes.router)
app.include_router(mod_routes.router)
app.include_router(points_routes.router)
app.include_router(theater_routes.router)
app.include_router(ws_routes.router)
