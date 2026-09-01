"""
Theater routes.

One endpoint viewers may read (what is on now), and the operator's controls for
running a session. The viewer side is deliberately thin: it says whether a
session is on, whether a title is playing, and the title's own details. It never
carries a library id, the projector's key, or anything about the machine the
library lives on.
"""

import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

import db
import theater
from auth import admin_user, session_user
from config import MAX_THEATER_EPISODES, MAX_THEATER_QUERY, MIN_THEATER_QUERY
from projector import ProjectorError, link

router = APIRouter()

UNAVAILABLE = "projector unavailable"


def _unavailable():
    return JSONResponse({"error": UNAVAILABLE}, status_code=502)


@router.get("/api/theater")
def theater_state(request: Request):
    # Guests included: watching is the whole of what a guest pass buys, and
    # between titles the intermission card is what there is to watch.
    if not session_user(request):
        return JSONResponse({"error": "Sign in first."}, status_code=401)
    return theater.public_state()


@router.post("/api/admin/theater/session")
async def start_session(request: Request):
    if not admin_user(request):
        return JSONResponse({"error": "Admins only."}, status_code=403)
    state, error = await theater.start_session()
    if error:
        return JSONResponse({"error": error}, status_code=409)
    return state


@router.post("/api/admin/theater/end")
async def end_session(request: Request):
    if not admin_user(request):
        return JSONResponse({"error": "Admins only."}, status_code=403)
    state, error = await theater.end_session()
    if error:
        return JSONResponse({"error": error}, status_code=409)
    return state


@router.post("/api/admin/theater/play")
async def play(request: Request):
    if not admin_user(request):
        return JSONResponse({"error": "Admins only."}, status_code=403)
    body = await request.json()
    subtitles = body.get("subtitles")
    # No box in the body is not "burn them": it is whatever the channel is set
    # to. The box is an override for one showing.
    if subtitles is None:
        subtitles = db.get_theater_subtitles()
    try:
        state, error = await theater.play(body.get("jf_id"), subtitles=bool(subtitles))
    except ProjectorError:
        return _unavailable()
    if error:
        return JSONResponse({"error": error}, status_code=409)
    return state


@router.post("/api/admin/theater/restart")
async def restart(request: Request):
    """Play what is on again from the start, without the subtitle burn. One
    click, because a room is watching subtitles run out of sync while it takes."""
    if not admin_user(request):
        return JSONResponse({"error": "Admins only."}, status_code=403)
    try:
        state, error = await theater.restart_without_subtitles()
    except ProjectorError:
        return _unavailable()
    if error:
        return JSONResponse({"error": error}, status_code=409)
    return state


@router.post("/api/admin/theater/stop")
async def stop(request: Request):
    if not admin_user(request):
        return JSONResponse({"error": "Admins only."}, status_code=403)
    try:
        state, error = await theater.stop()
    except ProjectorError:
        return _unavailable()
    if error:
        return JSONResponse({"error": error}, status_code=409)
    return state


@router.get("/api/admin/theater/search")
async def search(request: Request, q: str = ""):
    if not admin_user(request):
        return JSONResponse({"error": "Admins only."}, status_code=403)
    query = (q or "").strip()
    if not MIN_THEATER_QUERY <= len(query) <= MAX_THEATER_QUERY:
        return JSONResponse(
            {"error": f"Search for {MIN_THEATER_QUERY} to {MAX_THEATER_QUERY} "
                      "characters."},
            status_code=400,
        )
    try:
        results = await link.rpc("search", {"query": query})
    except ProjectorError:
        return _unavailable()
    return {"results": theater.clean_results(results)}


@router.get("/api/admin/theater/episodes")
async def episodes(request: Request, series: str = ""):
    """One show's episodes, asked for after its row is picked out of a search.

    The whole run in one reply rather than a request per season: the library
    returns it that way, and a picker that has every episode can group them
    without going back for each season the operator opens."""
    if not admin_user(request):
        return JSONResponse({"error": "Admins only."}, status_code=403)
    if not theater.SAFE_ID.match(str(series or "")):
        return JSONResponse({"error": "Unknown show."}, status_code=400)
    try:
        results = await link.rpc("episodes", {"series": series})
    except ProjectorError:
        return _unavailable()
    return {
        "episodes": theater.clean_results(results, limit=MAX_THEATER_EPISODES)
    }


@router.get("/api/admin/theater/art")
async def art(request: Request, jf_id: str = ""):
    """Where one title's poster is, fetching it from the projector the first
    time. Answers with a null rather than an error when the library has no
    picture: a missing poster is a row without a picture, not a failed search."""
    if not admin_user(request):
        return JSONResponse({"error": "Admins only."}, status_code=403)
    if not theater.SAFE_ID.match(str(jf_id or "")):
        return JSONResponse({"error": "Unknown title."}, status_code=400)
    try:
        return {"art": await theater.poster(jf_id)}
    except ProjectorError:
        return {"art": None}


@router.get("/api/admin/theater/projector")
def projector_status(request: Request):
    if not admin_user(request):
        return JSONResponse({"error": "Admins only."}, status_code=403)
    key = db.get_projector_key()
    return {
        "connected": link.connected(),
        "last_seen": link.last_seen(),
        # Whether a key exists at all, so the panel can say "generate one" rather
        # than show a projector that can never authenticate as merely offline.
        "has_key": bool(key),
        "key": key or "",
    }


@router.post("/api/admin/theater/projector/key")
async def regenerate_projector_key(request: Request):
    if not admin_user(request):
        return JSONResponse({"error": "Admins only."}, status_code=403)
    key = db.regenerate_projector_key()
    # The projector holding the old key is no longer authorized, so it is closed
    # here rather than left connected until it happens to reconnect.
    await link.close()
    return {"key": key, "connected": False, "last_seen": link.last_seen(),
            "has_key": True, "regenerated_at": int(time.time())}
