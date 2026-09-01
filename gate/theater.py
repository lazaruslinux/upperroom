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


def stream_transition(going_online, theater_active, theater_recently_closed=False):
    """What the stream watcher should do on a live/offline transition (pure).

    Kept apart from the watcher for the same reason watchdog_action is: these
    are the decisions worth asserting, and asserting them through a poll loop
    that talks to MediaMTX and ffmpeg is not a test, it is a stage set.

    During a session the gate skips recording and the go-live announcement (the
    session start already made it). `state` is the session state the transition
    implies, or None when there is no session to move.

    theater_recently_closed covers the seconds after a session ends: the video
    path outlives the session by a poll or two, so the watcher sees it drop when
    there is no session left to suppress the announcement, and the room was told
    the night was over in its own words moments earlier."""
    if going_online:
        return {
            "record": not theater_active,
            "notify": not theater_active,
            "state": "playing" if theater_active else None,
        }
    return {
        # Only a real broadcast ending is worth saying out loud. During a session
        # the path dropping is just the gap between titles.
        "announce_end": not theater_active and not theater_recently_closed,
        "state": "intermission" if theater_active else None,
    }


# ---- What viewers see -----------------------------------------------------

def episode_code(season, episode):
    """`S3E1`, or as much of it as the library knows. A special with no number
    has no code rather than a made-up one."""
    if not isinstance(season, int) and not isinstance(episode, int):
        return ""
    parts = []
    if isinstance(season, int):
        parts.append(f"S{season}")
    if isinstance(episode, int):
        parts.append(f"E{episode}")
    return "".join(parts)


def now_label(session):
    """What is on air as one line, the same wherever it is said.

    A film is its own name and year. An episode is the SHOW's name and year plus
    its place in the run: "Freedom Day" identifies nothing on its own, while
    "Silo (2023) S3E1" identifies it to anybody."""
    if not session or not session["now_title"]:
        return ""
    series = session["now_series"]
    name = series or session["now_title"]
    year = session["now_year"]
    label = f"{name} ({year})" if year else name
    code = episode_code(session["now_season"], session["now_episode"])
    return f"{label} {code}" if code else label


def public_now(session):
    """The on-air title as viewers may see it, or None. Deliberately without the
    library id: what the operator's library calls a film is their business, and
    this rides a socket every viewer holds."""
    if not session or not session["now_title"]:
        return None
    return {
        "title": session["now_title"],
        "year": session["now_year"],
        # Null on a film. A page showing an episode wants the show's name and
        # the numbering, not just the episode's own title.
        "series": session["now_series"],
        "season": session["now_season"],
        "episode": session["now_episode"],
        # Composed here so every surface says it the same way.
        "label": now_label(session),
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
    global _last_error_id
    session_id = db.create_theater_session(int(time.time()))
    if session_id is None:
        return None, "A theater session is already running."
    _last_error_id = None
    logger.info("theater session %s started", session_id)
    await notify_live()
    db.mark_theater_notified(session_id)
    await hub.narrate("Theater mode enabled.")
    session = db.get_active_theater_session()
    await broadcast_state(session)
    return public_state(session), None


async def end_session(narration="Theater mode disabled.", stop_projector=True):
    """Close the open session. Stops anything playing first. Chat is left alone:
    it belongs to the evening, not to the session, and is wiped when a later
    broadcast starts a new night.

    narration is what the room is told, so a session that closed because its
    title ran out can say so rather than reading like the host pressed end.

    stop_projector is False when the projector has just reported itself idle:
    it has nothing left to stop, does not answer a stop it cannot act on, and
    waiting out the timeout would hold the room open for another twenty seconds
    after the very thing that ended it.

    The room hears one line per ending, whichever way it ended. That is what
    _ending is for: stopping the projector makes it report idle, and the report
    arrives before the reply to the stop that caused it."""
    global _ending, _last_closed_at
    session = db.get_active_theater_session()
    if not session:
        return None, "No theater session is running."
    _ending = True
    try:
        if stop_projector and (session["state"] == "playing" or session["now_title"]):
            try:
                await link.rpc("stop", timeout=PLAY_TIMEOUT)
            except ProjectorError as exc:
                # The session ends either way. A projector that cannot be told to
                # stop is a problem for the operator's machine, not a reason to
                # leave the room stuck in a session nobody can close.
                logger.warning("could not stop the projector at session end: %s", exc)
            # Read again: the stop was awaited, and anything that closed the
            # session while we waited has already told the room so.
            session = db.get_active_theater_session()
            if not session:
                return public_state(None), None
        ended = int(time.time())
        # Whoever wins this race is the one that narrates. A session that is
        # already closed is not closed twice, and is not announced twice either.
        if not db.end_theater_session(session["id"], ended):
            return public_state(None), None
        logger.info("theater session %s ended", session["id"])
        # The night is over as far as the channel is concerned, so this is what a
        # later broadcast measures its gap from.
        db.set_last_air_ended_at(ended)
        _last_closed_at = time.monotonic()
        await hub.narrate(narration)
        await broadcast_state(None)
        return public_state(None), None
    finally:
        _ending = False


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


async def poster(jf_id):
    """The stored poster for one title as `/media/art/<file>`, fetching it from
    the projector the first time. None when the library has no picture for it.

    Cached on disk deliberately: a search of twenty-five rows would otherwise ask
    the projector for twenty-five posters every time it was run, and the poster
    for a title does not change."""
    name = art_filename(jf_id)
    if not name:
        return None
    if os.path.exists(os.path.join(ART_DIR, name)):
        return f"/media/art/{name}"
    reply = await link.rpc("art", {"jf_id": jf_id})
    saved = save_art(jf_id, (reply or {}).get("jpeg_b64")) if isinstance(reply, dict) else None
    return f"/media/art/{saved}" if saved else None


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


KINDS = ("movie", "series", "episode")


def clean_results(results, limit=MAX_THEATER_RESULTS):
    """Trim a projector's reply to the fields the dashboard uses, capped in
    count and in length, so a misbehaving library cannot push a wall of text
    through the admin page.

    `limit` is higher for an episode list than for a search: a search is a page
    of choices, while a show's run is the whole run and cutting it would hide a
    season with nothing to say it had happened."""
    cleaned = []
    for item in (results or [])[:limit]:
        if not isinstance(item, dict):
            continue
        jf_id = str(item.get("jf_id") or "")
        if not SAFE_ID.match(jf_id):
            continue
        year = item.get("year")
        runtime = item.get("runtime_min")
        kind = item.get("kind")
        kind = kind if kind in KINDS else "movie"
        row = {
            "jf_id": jf_id,
            "kind": kind,
            "title": str(item.get("title") or "Untitled")[:200],
            "year": int(year) if isinstance(year, int) else None,
            "runtime_min": int(runtime) if isinstance(runtime, int) else None,
            "synopsis": str(item.get("synopsis") or "")[:1000],
            "has_subtitles": bool(item.get("has_subtitles")),
        }
        if kind == "episode":
            season = item.get("season")
            number = item.get("episode")
            show_year = item.get("series_year")
            row["series"] = str(item.get("series") or "")[:200]
            row["season"] = int(season) if isinstance(season, int) else None
            row["episode"] = int(number) if isinstance(number, int) else None
            # The show's year wins over the episode's: it is what the show is
            # known by, and a later season would otherwise rename it.
            if isinstance(show_year, int):
                row["year"] = show_year
        cleaned.append(row)
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
    # One cleaning path for what the projector says, whether it came back from a
    # search or from playing, so a field can only be trusted in one shape.
    detail = (clean_results([{**detail, "jf_id": jf_id}]) or [{}])[0]
    db.set_theater_now(
        session["id"],
        jf_id=jf_id,
        title=detail.get("title") or "Now playing",
        year=detail.get("year"),
        runtime=detail.get("runtime_min"),
        synopsis=detail.get("synopsis") or "",
        art=art,
        series=detail.get("series"),
        season=detail.get("season"),
        episode=detail.get("episode"),
    )
    session = db.get_active_theater_session()
    await hub.narrate(_now_showing_line(session))
    await broadcast_state(session)
    return public_state(session), None


def _now_showing_line(session):
    """What the room is told when a title goes on. An episode is named by its
    show and its place in the run, with its own title after it: the code says
    which one, the name says what it is called."""
    named = now_label(session) or "The next title"
    if (session or {})["now_series"] and session["now_title"]:
        named = f'{named}, "{session["now_title"]}"'
    return f"{named} selected. Enjoy the show!"


# When the host last took a title off on purpose, on the monotonic clock. The
# projector reports "idle" whenever ffmpeg exits, and it cannot say why, so this
# is what tells a title that RAN OUT from one the host stopped: the first closes
# the session, the second leaves the room in intermission to pick the next.
_host_stopped_at = -1e9
# How long after the host's stop an idle report is still read as theirs. It has
# to outlast the stop itself: the marker is set before the rpc and the rpc waits
# up to PLAY_TIMEOUT for the projector, so a slow ffmpeg teardown can report
# idle after the call has already returned. Anything shorter than that plus a
# margin closes a session the host meant to keep, and even this is far short of
# any real title.
HOST_STOP_GRACE = PLAY_TIMEOUT + 10

# True while end_session is closing a session. Stopping the projector makes it
# report idle, and that report reaches on_projector_event before the reply to
# the stop that caused it, so without this the ending is narrated twice: once by
# the auto-close path reading the idle as a title running out, once by the close
# that asked for it.
_ending = False
# When a session last closed, on the monotonic clock. The stream watcher polls
# every few seconds, so the video path drops after the session is already gone
# and there is nothing left to tell the watcher this was theater rather than a
# broadcast ending.
_last_closed_at = -1e9
# How long after a close the dropping path is still that session's. Generous on
# purpose: it only has to outlast a poll or two, and nothing else is going live
# in the seconds after a movie night ends.
SESSION_CLOSE_GRACE = 30

# The title an error was last reported for, so a source that fails twice running
# says so plainly instead of repeating itself. Reset per session.
_last_error_id = None


def recently_closed():
    """Whether a theater session closed just now, so the stream watcher can tell
    a night that ended in the room's own words from a broadcast going off air."""
    return time.monotonic() - _last_closed_at < SESSION_CLOSE_GRACE


async def stop():
    """Take whatever is on air off it. The projector stops publishing, the path
    drops, and the watcher moves the session to intermission."""
    global _host_stopped_at
    session = db.get_active_theater_session()
    if not session:
        return None, "No theater session is running."
    # Marked before the call, not after: the projector may report itself idle
    # while we are still waiting on the reply.
    _host_stopped_at = time.monotonic()
    await link.rpc("stop", timeout=PLAY_TIMEOUT)
    db.set_theater_now(session["id"])
    session = db.get_active_theater_session()
    await hub.narrate("Film stopped.")
    await broadcast_state(session)
    return public_state(session), None


async def restart_without_subtitles():
    """Put the same title back on from the start, with the burn off. Returns
    (state, None) or (None, error).

    Subtitles that are out of sync are only visible once a film is running, and
    fixing it used to mean stop, search, find the title again, play it without
    the box: four steps, with the room watching every one.

    Deliberately not routed through play(): nothing about what is showing has
    changed, so the poster is not fetched again and the room is not told what is
    on for a second time. The host's stop marker is set first for the same reason
    stop() sets it, and as a belt against a projector that predates the
    generation counter: its idle for the old ffmpeg would otherwise read as the
    title ending and close the night on a film that is starting."""
    global _host_stopped_at
    session = db.get_active_theater_session()
    if not session or not session["now_jf_id"]:
        return None, "Nothing is playing."
    _host_stopped_at = time.monotonic()
    await link.rpc(
        "play", {"jf_id": session["now_jf_id"], "subtitles": False},
        timeout=PLAY_TIMEOUT,
    )
    # Read again: the call was awaited, and an old projector's idle may have
    # parked the room in intermission while we waited.
    session = db.get_active_theater_session()
    if not session:
        return public_state(None), None
    await hub.narrate("Restarting without subtitles.")
    await broadcast_state(session)
    return public_state(session), None


# ---- Events from the projector --------------------------------------------

async def _title_failed(session):
    """A title that died while it was on air. The room goes back to intermission
    and is told, rather than the night ending on one bad source: a file that
    will not open or a library that blinked is a reason to pick again, not a
    reason to close the room on everybody in it.

    Nothing is retried here, deliberately. A title that fails twice running says
    so plainly instead of repeating the same line at somebody watching it fail.
    """
    global _last_error_id
    session_id = session["id"]
    failed_id = session["now_jf_id"]
    again = bool(failed_id) and failed_id == _last_error_id
    _last_error_id = failed_id
    db.set_theater_now(session_id)
    # Set here rather than left to the stream watcher: the card the room is
    # looking at should change with the news, not a poll later.
    db.set_theater_state(session_id, "intermission")
    logger.info("theater session %s back to intermission: the title failed", session_id)
    await hub.narrate(
        "That title would not play again. Try a different one." if again else
        "That title would not play. The room is still open; pick another when "
        "you are ready."
    )
    await broadcast_state(db.get_active_theater_session())


async def on_projector_event(message, opening_report=False):
    """Handle one event the projector sent unprompted. Only 'status' matters:
    it says what the projector thinks it is doing, which is how an ffmpeg that
    died on its own reaches the room instead of leaving the page showing a
    title that stopped playing minutes ago.

    A title reaching its end closes the whole session. Before, the room fell
    back to the intermission card and stayed there until somebody pressed end,
    which meant a film finishing at midnight left the channel apparently on air
    until morning, with anyone who had left the page open still holding it.

    opening_report says this is the state a freshly connected projector reports
    about itself. Only a transition on a connection we were already holding can
    end anything.
    """
    if message.get("event") != "status":
        return
    state = str(message.get("state") or "")
    if state not in ("idle", "error"):
        return
    session = db.get_active_theater_session()
    if not session:
        return
    if state == "error":
        logger.warning(
            "the projector reported an error: %s",
            str(message.get("detail") or "")[:200],
        )
    # A projector that has just connected is telling us where it already is, not
    # that something happened. Its process restarting mid-film, or a blip on the
    # link, would otherwise arrive as a fresh "idle" and close the whole night.
    if opening_report:
        logger.info("the projector reconnected and reports %s", state)
        return
    # The session is already being closed, and the close says its own line. This
    # idle is the projector answering the stop that close asked for.
    if _ending:
        return
    # Nothing was on air to end, so there is nothing for this to be the end of.
    if session["state"] != "playing" and not session["now_title"]:
        return
    # The host took it off themselves, so leave them in intermission to pick
    # the next one rather than making them start a session again. Their own stop
    # explains the exit whatever the projector went on to call it.
    if time.monotonic() - _host_stopped_at < HOST_STOP_GRACE:
        db.set_theater_now(session["id"])
        await broadcast_state(db.get_active_theater_session())
        return
    if state == "error":
        await _title_failed(session)
        return
    logger.info("theater session %s closing: its title ended", session["id"])
    await end_session(
        narration="That was the end of it. Theater mode is off.",
        # It just told us it is idle, so there is nothing to stop.
        stop_projector=False,
    )


link.on_event = on_projector_event
