"""
Theater sessions: playing titles from the operator's library to the room.

A session is a frame around ordinary broadcasts rather than a second video path.
The projector publishes to the same ingest OBS would, so everything downstream
(MediaMTX, HLS, Caddy, the watch page's player) is untouched. What a session
changes is what the gate does around that video:

  - it does not record, because a session is somebody else's film
  - clips are refused for the same reason
  - nothing about it wipes chat: a session is part of the same evening as the
    broadcast before it, and the room carries over
  - going live announces once, at the start, not once per title

Between titles viewers see an intermission card instead of the offline card, and
the state rides the chat socket so it changes without a poll.
"""

import base64
import io
import logging
import os
import re
import time

from PIL import Image

import db
from config import (
    ART_DIR, MAX_THEATER_ART_BYTES, MAX_THEATER_RESULTS, THEATER_ART_MAX,
)
from hub import hub
from notify import notify_live
from projector import PLAY_TIMEOUT, ProjectorError, link

logger = logging.getLogger("upperroom.theater")

# A library id reaches us from the projector and is used as a filename, so it is
# restricted to what a filename may safely be rather than trusted.
SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def is_active():
    """Whether a theater session is open right now."""
    return db.get_active_theater_session() is not None


def stream_transition(going_online, theater_active):
    """What the stream watcher should do on a live/offline transition (pure).

    Kept apart from the watcher for the same reason watchdog_action is: these
    are the decisions worth asserting, and asserting them through a poll loop
    that talks to MediaMTX and ffmpeg is not a test, it is a stage set.

    During a session the gate skips recording and the go-live announcement (the
    session start already made it). `state` is the session state the transition
    implies, or None when there is no session to move."""
    if going_online:
        return {
            "record": not theater_active,
            "notify": not theater_active,
            "state": "playing" if theater_active else None,
        }
    return {
        # Only a real broadcast ending is worth saying out loud. During a session
        # the path dropping is just the gap between titles.
        "announce_end": not theater_active,
        "state": "intermission" if theater_active else None,
    }


# ---- What viewers see -----------------------------------------------------

def public_now(session):
    """The on-air title as viewers may see it, or None. Deliberately without the
    library id: what the operator's library calls a film is their business, and
    this rides a socket every viewer holds."""
    if not session or not session["now_title"]:
        return None
    return {
        "title": session["now_title"],
        "year": session["now_year"],
        "runtime_min": session["now_runtime"],
        "synopsis": session["now_synopsis"] or "",
        "art": f"/media/art/{session['now_art']}" if session["now_art"] else None,
    }


def public_state(session=None):
    """The theater state for /api/theater and the socket frame."""
    if session is None:
        session = db.get_active_theater_session()
    if not session:
        return {"active": False, "state": "off", "now": None}
    return {
        "active": True,
        "state": session["state"],
        "now": public_now(session),
    }


async def broadcast_state(session=None):
    """Push the current state to every watch page and overlay. Best effort: a
    dead socket must never fail the transition that produced the frame."""
    state = public_state(session)
    try:
        await hub.broadcast({"type": "theater", **state})
    except Exception:
        logger.debug("theater broadcast failed", exc_info=True)


# ---- Session lifecycle ----------------------------------------------------

async def start_session():
    """Open a session. Returns (state, None) or (None, error).

    The go-live announcement fires here, once, rather than when the first title
    starts: from a viewer's side the session is the broadcast, and one message
    per film would be a mailing list nobody asked for."""
    session_id = db.create_theater_session(int(time.time()))
    if session_id is None:
        return None, "A theater session is already running."
    logger.info("theater session %s started", session_id)
    await notify_live()
    db.mark_theater_notified(session_id)
    await hub.narrate("Theater mode enabled.")
    session = db.get_active_theater_session()
    await broadcast_state(session)
    return public_state(session), None


async def end_session():
    """Close the open session. Stops anything playing first. Chat is left alone:
    it belongs to the evening, not to the session, and is wiped when a later
    broadcast starts a new night."""
    session = db.get_active_theater_session()
    if not session:
        return None, "No theater session is running."
    if session["state"] == "playing" or session["now_title"]:
        try:
            await link.rpc("stop", timeout=PLAY_TIMEOUT)
        except ProjectorError as exc:
            # The session ends either way. A projector that cannot be told to
            # stop is a problem for the operator's machine, not a reason to
            # leave the room stuck in a session nobody can close.
            logger.warning("could not stop the projector at session end: %s", exc)
    ended = int(time.time())
    db.end_theater_session(session["id"], ended)
    logger.info("theater session %s ended", session["id"])
    # The night is over as far as the channel is concerned, so this is what a
    # later broadcast measures its gap from.
    db.set_last_air_ended_at(ended)
    await hub.narrate("Theater mode disabled.")
    await broadcast_state(None)
    return public_state(None), None


async def set_stage(state):
    """Move the open session between 'intermission' and 'playing' and tell the
    room. Called by the stream watcher when the video path comes up or goes
    down, so the state follows the actual stream rather than an ack."""
    session = db.get_active_theater_session()
    if not session or session["state"] == state:
        return
    db.set_theater_state(session["id"], state)
    await broadcast_state(db.get_active_theater_session())


# ---- Playing a title ------------------------------------------------------

def art_filename(jf_id):
    """Where a title's poster is stored, as a bare filename, or None if the id
    is not something we are willing to write to disk."""
    if not SAFE_ID.match(str(jf_id or "")):
        return None
    return f"{jf_id}.jpg"


def save_art(jf_id, jpeg_b64):
    """Re-encode a poster from the projector and store it. Returns the filename,
    or None if there was nothing usable.

    Re-encoded through Pillow for the same reason an avatar upload is: it proves
    the bytes are really an image and drops everything that is not pixels. The
    projector is the operator's own machine, but it is still a machine on the
    other side of a socket handing this one a file."""
    name = art_filename(jf_id)
    if not name or not jpeg_b64:
        return None
    try:
        raw = base64.b64decode(str(jpeg_b64), validate=True)
    except Exception:
        logger.warning("theater art for %s was not valid base64", jf_id)
        return None
    if not raw or len(raw) > MAX_THEATER_ART_BYTES:
        logger.warning("theater art for %s is %d bytes; ignoring", jf_id, len(raw))
        return None
    try:
        picture = Image.open(io.BytesIO(raw))
        picture.load()
        picture = picture.convert("RGB")
        picture.thumbnail(THEATER_ART_MAX, Image.Resampling.LANCZOS)
        os.makedirs(ART_DIR, exist_ok=True)
        picture.save(os.path.join(ART_DIR, name), format="JPEG", quality=85)
    except Exception:
        logger.warning("could not store theater art for %s", jf_id, exc_info=True)
        return None
    return name


def clean_results(results):
    """Trim a projector's search reply to the fields the dashboard uses, capped
    in count and in length, so a misbehaving library cannot push a wall of text
    through the admin page."""
    cleaned = []
    for item in (results or [])[:MAX_THEATER_RESULTS]:
        if not isinstance(item, dict):
            continue
        jf_id = str(item.get("jf_id") or "")
        if not SAFE_ID.match(jf_id):
            continue
        year = item.get("year")
        runtime = item.get("runtime_min")
        cleaned.append({
            "jf_id": jf_id,
            "title": str(item.get("title") or "Untitled")[:200],
            "year": int(year) if isinstance(year, int) else None,
            "runtime_min": int(runtime) if isinstance(runtime, int) else None,
            "synopsis": str(item.get("synopsis") or "")[:1000],
            "has_subtitles": bool(item.get("has_subtitles")),
        })
    return cleaned


async def play(jf_id, subtitles=True):
    """Put a title on air. Returns (state, None) or (None, error).

    The state stays 'intermission' here on purpose. Telling the projector to
    play is not the same as video arriving, and the watch page must not show a
    Now Showing card over a black stage: the flip to 'playing' happens when the
    stream path actually comes up (see stream_transition)."""
    session = db.get_active_theater_session()
    if not session:
        return None, "No theater session is running."
    if not SAFE_ID.match(str(jf_id or "")):
        return None, "That is not a title this library can play."

    # Fetch the poster first: a failure here costs the card, not the film.
    art = None
    try:
        reply = await link.rpc("art", {"jf_id": jf_id})
        if isinstance(reply, dict):
            art = save_art(jf_id, reply.get("jpeg_b64"))
    except ProjectorError as exc:
        logger.info("no poster for %s: %s", jf_id, exc)

    detail = await link.rpc(
        "play", {"jf_id": jf_id, "subtitles": bool(subtitles)}, timeout=PLAY_TIMEOUT
    )
    detail = detail if isinstance(detail, dict) else {}
    db.set_theater_now(
        session["id"],
        jf_id=jf_id,
        title=str(detail.get("title") or "")[:200] or "Now playing",
        year=detail.get("year") if isinstance(detail.get("year"), int) else None,
        runtime=(detail.get("runtime_min")
                 if isinstance(detail.get("runtime_min"), int) else None),
        synopsis=str(detail.get("synopsis") or "")[:1000],
        art=art,
    )
    session = db.get_active_theater_session()
    await hub.narrate(_now_showing_line(session))
    await broadcast_state(session)
    return public_state(session), None


def _now_showing_line(session):
    """What the room is told when a title goes on: the title, its year when the
    library knows one, and an invitation."""
    title = (session or {})["now_title"] or "The next title"
    year = (session or {})["now_year"]
    named = f"{title} ({year})" if year else title
    return f"{named} selected. Enjoy the show!"


async def stop():
    """Take whatever is on air off it. The projector stops publishing, the path
    drops, and the watcher moves the session to intermission."""
    session = db.get_active_theater_session()
    if not session:
        return None, "No theater session is running."
    await link.rpc("stop", timeout=PLAY_TIMEOUT)
    db.set_theater_now(session["id"])
    session = db.get_active_theater_session()
    await hub.narrate("Film stopped.")
    await broadcast_state(session)
    return public_state(session), None


# ---- Events from the projector --------------------------------------------

async def on_projector_event(message):
    """Handle one event the projector sent unprompted. Only 'status' matters
    this round: it says what the projector thinks it is doing, which is how an
    ffmpeg that died on its own reaches the room instead of leaving the page
    showing a title that stopped playing minutes ago."""
    if message.get("event") != "status":
        return
    state = str(message.get("state") or "")
    if state in ("idle", "error"):
        session = db.get_active_theater_session()
        if not session:
            return
        if state == "error":
            logger.warning(
                "the projector reported an error: %s",
                str(message.get("detail") or "")[:200],
            )
        db.set_theater_now(session["id"])
        await broadcast_state(db.get_active_theater_session())


link.on_event = on_projector_event
