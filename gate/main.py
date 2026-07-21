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
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI

import config
import db
from hub import chat_purge_worker, hub
from media import stream_watcher, thumbnail_worker
from routes import admin as admin_routes
from routes import auth as auth_routes
from routes import media as media_routes
from routes import mod as mod_routes
from routes import ws as ws_routes


@asynccontextmanager
async def lifespan(_app):
    hub.load_bans()
    # Any VOD still marked unfinished is from a recording the previous run never
    # got to close out; drop those rows so they do not linger.
    try:
        db.clear_unfinished_vods()
    except Exception:
        pass
    tasks = [
        asyncio.create_task(stream_watcher()),
        asyncio.create_task(thumbnail_worker()),
        asyncio.create_task(chat_purge_worker()),
    ]
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()


app = FastAPI(title="selfstream", docs_url=None, redoc_url=None, lifespan=lifespan)
db.init_db()
for _dir in (config.AVATAR_DIR, config.RECORD_TMP, config.VOD_DIR, config.CLIP_DIR):
    os.makedirs(_dir, exist_ok=True)

app.include_router(auth_routes.router)
app.include_router(media_routes.router)
app.include_router(admin_routes.router)
app.include_router(mod_routes.router)
app.include_router(ws_routes.router)
