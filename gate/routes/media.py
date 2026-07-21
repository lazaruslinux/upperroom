"""
Media routes: the live preview thumbnail, VOD and clip listings and playback
metadata, view counting, clip creation, and the stream status the player polls.

The media files themselves are served straight from disk by Caddy at /media/*
(behind the same session check as the live video), so large files never pass
through this Python service; only metadata and view counting live here.
"""

import os

from fastapi import APIRouter, Request, Response
from fastapi.responses import FileResponse, JSONResponse

import db
from auth import admin_user, read_session
from config import CLIP_DIR, COOKIE_NAME, THUMB_PATH, VOD_DIR
from hub import hub
from media import fetch_path, make_clip, ready_epoch, _remove_media_files

router = APIRouter()


# ---- Thumbnail ------------------------------------------------------------

@router.get("/api/thumbnail")
def thumbnail(request: Request):
    # The home card preview. Signed in viewers only, and never cached so the
    # frame stays current. 404 means the stream is offline (no fresh frame).
    if not read_session(request.cookies.get(COOKIE_NAME, "")):
        return Response(status_code=401)
    if not os.path.exists(THUMB_PATH):
        return Response(status_code=404)
    return FileResponse(
        THUMB_PATH,
        media_type="image/jpeg",
        headers={"Cache-Control": "no-store"},
    )


# ---- VODs and clips -------------------------------------------------------
# Metadata and view counting live here; the media files themselves are served
# straight from disk by Caddy at /media/* (behind the same session check as the
# live video), so large files never pass through this Python service.

def _signed_in(request):
    return read_session(request.cookies.get(COOKIE_NAME, "")) is not None


def _media_summary(row, kind):
    """Shape a VOD or clip row for a listing, including whether a poster exists."""
    folder = VOD_DIR if kind == "vod" else CLIP_DIR
    has_poster = bool(row.get("id")) and os.path.exists(
        os.path.join(folder, f"{row['id']}.jpg")
    )
    out = {
        "id": row["id"],
        "filename": row.get("filename"),
        "duration": row.get("duration") or 0,
        "views": row.get("views") or 0,
        "poster": has_poster,
    }
    if kind == "vod":
        out.update(
            title=row["title"], description=row.get("description") or "",
            started_at=row["started_at"],
        )
    else:
        out.update(
            name=row["name"], creator=row.get("creator"),
            created_at=row["created_at"],
        )
    return out


@router.get("/api/vods")
def list_vods(request: Request):
    if not _signed_in(request):
        return Response(status_code=401)
    return {"vods": [_media_summary(v, "vod") for v in db.list_vods()]}


@router.get("/api/clips")
def list_clips(request: Request):
    if not _signed_in(request):
        return Response(status_code=401)
    return {"clips": [_media_summary(c, "clip") for c in db.list_clips()]}


@router.get("/api/vods/{vod_id}")
def get_vod(vod_id: int, request: Request):
    if not _signed_in(request):
        return Response(status_code=401)
    vod = db.get_vod(vod_id)
    if not vod or not vod["ready"]:
        return JSONResponse({"error": "No such VOD."}, status_code=404)
    return _media_summary(vod, "vod")


@router.get("/api/clips/{clip_id}")
def get_clip(clip_id: int, request: Request):
    if not _signed_in(request):
        return Response(status_code=401)
    clip = db.get_clip(clip_id)
    if not clip:
        return JSONResponse({"error": "No such clip."}, status_code=404)
    return _media_summary(clip, "clip")


@router.post("/api/vods/{vod_id}/view")
def view_vod(vod_id: int, request: Request):
    session = read_session(request.cookies.get(COOKIE_NAME, ""))
    if not session:
        return Response(status_code=401)
    if not db.get_vod(vod_id):
        return JSONResponse({"error": "No such VOD."}, status_code=404)
    return {"views": db.add_view("vod", vod_id, session["sub"])}


@router.post("/api/clips/{clip_id}/view")
def view_clip(clip_id: int, request: Request):
    session = read_session(request.cookies.get(COOKIE_NAME, ""))
    if not session:
        return Response(status_code=401)
    if not db.get_clip(clip_id):
        return JSONResponse({"error": "No such clip."}, status_code=404)
    return {"views": db.add_view("clip", clip_id, session["sub"])}


@router.get("/api/vods/{vod_id}/chat")
def vod_chat(vod_id: int, request: Request):
    if not _signed_in(request):
        return Response(status_code=401)
    return {"messages": db.get_replay("vod", vod_id)}


@router.get("/api/clips/{clip_id}/chat")
def clip_chat(clip_id: int, request: Request):
    if not _signed_in(request):
        return Response(status_code=401)
    return {"messages": db.get_replay("clip", clip_id)}


@router.post("/api/clip")
async def create_clip_endpoint(request: Request):
    session = read_session(request.cookies.get(COOKIE_NAME, ""))
    if not session:
        return JSONResponse({"error": "Sign in first."}, status_code=401)
    user = db.get_user(session["sub"])
    if not user:
        return JSONResponse({"error": "Sign in first."}, status_code=401)
    body = await request.json()
    clip_id, error = await make_clip(user, body.get("name"))
    if error:
        return JSONResponse({"error": error}, status_code=400)
    return {"ok": True, "id": clip_id}


@router.delete("/api/vods/{vod_id}")
def delete_vod(vod_id: int, request: Request):
    if not admin_user(request):
        return JSONResponse({"error": "Admins only."}, status_code=403)
    row = db.delete_media("vod", vod_id)
    if not row:
        return JSONResponse({"error": "No such VOD."}, status_code=404)
    _remove_media_files(VOD_DIR, row.get("filename"), vod_id)
    return {"ok": True}


@router.delete("/api/clips/{clip_id}")
def delete_clip(clip_id: int, request: Request):
    if not admin_user(request):
        return JSONResponse({"error": "Admins only."}, status_code=403)
    row = db.delete_media("clip", clip_id)
    if not row:
        return JSONResponse({"error": "No such clip."}, status_code=404)
    _remove_media_files(CLIP_DIR, row.get("filename"), clip_id)
    return {"ok": True}


@router.get("/api/status")
async def status():
    # Ask MediaMTX whether a publisher is connected to our stream path, so the
    # player can show an offline card instead of a broken video. When live we
    # also return when the stream started, so the landing page can show how
    # long it has been running.
    data = await fetch_path()
    watching = len(hub.viewers())
    # accent is public here so pre-login pages (the login screen) can paint the
    # channel's brand color before any session exists.
    accent = db.get_stream_info()["accent"]
    if data and data.get("ready", False):
        return {
            "online": True,
            "since": ready_epoch(data.get("readyTime")),
            "watching": watching,
            "accent": accent,
        }
    return {"online": False, "watching": watching, "accent": accent}
