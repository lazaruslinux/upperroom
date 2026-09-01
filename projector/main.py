"""
upperroom projector.

Runs on whatever machine holds the operator's media library, and connects OUT to
their gate over one WebSocket. Nothing listens here: no port to open, no
certificate to get, nothing about this machine reachable from the internet. The
gate asks it to search, to fetch a poster, to play, and to stop; it answers, and
reports what it is doing as it goes.

It plays by publishing to the same RTMP ingest OBS would, with the channel's
stream key, so the whole video path downstream is untouched.
"""

import asyncio
import base64
import json
import logging
from urllib.parse import urlencode

import websockets

import config
import demo
import jellyfin
import player

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("projector")

# One ffmpeg, one thing playing, and one task watching it. "generation" counts
# the starts and stops: it is what tells one showing from the next when both are
# the same title, which the library id cannot do (see _supervise).
_player = player.Player()
_state = {
    "state": "idle", "jf_id": None, "started_at": 0.0, "detail": "",
    "generation": 0,
}
_supervisor = None


# ---- The protocol ---------------------------------------------------------
# Requests arrive as {"id", "method", "params"}, answered with {"id", "result"}
# or {"id", "error"}. Events go the other way unprompted, as {"event", ...}.

def reply(request_id, result):
    return json.dumps({"id": request_id, "result": result})


def error(request_id, message):
    return json.dumps({"id": request_id, "error": str(message)})


def event(name, **fields):
    return json.dumps({"event": name, **fields})


async def send_status(socket, state, detail="", position_s=None):
    _state["state"] = state
    _state["detail"] = detail
    try:
        await socket.send(event(
            "status", state=state, jf_id=_state["jf_id"],
            position_s=position_s, detail=detail,
        ))
    except Exception:
        logger.debug("could not send a status event", exc_info=True)


# ---- The library ----------------------------------------------------------

async def do_search(query):
    if config.DEMO:
        return demo.search(query)
    return await jellyfin.search(
        config.JELLYFIN_URL, config.JELLYFIN_API_KEY, query
    )


async def do_episodes(series_id):
    """Every episode of one series, in order. Asked for only after somebody
    picks a show out of a search, so this never runs on its own."""
    if config.DEMO:
        return demo.episodes(series_id)
    return await jellyfin.episodes(
        config.JELLYFIN_URL, config.JELLYFIN_API_KEY, series_id
    )


async def do_art(jf_id):
    raw = (await demo.art(jf_id)) if config.DEMO else (
        await jellyfin.art(config.JELLYFIN_URL, config.JELLYFIN_API_KEY, jf_id)
    )
    if not raw:
        return {"jpeg_b64": None}
    return {"jpeg_b64": base64.b64encode(raw).decode("ascii")}


async def item_detail(jf_id):
    """What is known about a title, for the gate's Now Showing card."""
    if config.DEMO:
        found = demo.find(jf_id)
        if not found:
            raise ValueError("no such demo title")
        return demo.public(found)
    found = await jellyfin.item(
        config.JELLYFIN_URL, config.JELLYFIN_API_KEY, jf_id
    )
    if not found:
        raise ValueError("no such title in the library")
    return found


# ---- Playing --------------------------------------------------------------

def play_options(detail, subtitles):
    opts = config.encoder_options()
    opts["subtitles"] = bool(subtitles) and bool(detail.get("has_subtitles"))
    if config.DEMO:
        opts["demo_title"] = detail.get("title") or "Demo"
    return opts


def subtitle_source(jf_id, detail, file_source):
    """Where the burn should read subtitles from, or None when there is nothing
    worth burning.

    A text track is asked for from the library as SRT rather than read out of
    the video file, because the two are often not the same thing: a library with
    Bazarr keeps its subtitles in files beside the video, and a disc rip's own
    track is usually a picture ffmpeg cannot render. In demo mode there is no
    library, so the generated card's own file is all there is."""
    if config.DEMO:
        return file_source
    index = detail.get("subtitle_index")
    if index is None:
        return None
    return jellyfin.subtitle_url(
        config.JELLYFIN_URL, jf_id,
        detail.get("media_source_id") or jf_id, index,
    ) + f"?{urlencode({'api_key': config.JELLYFIN_API_KEY})}"


async def start_playing(socket, jf_id, subtitles):
    """Start a title. Returns its detail for the gate's reply.

    A burn-in that will not work fails within seconds, so a subtitle filter
    failure is retried once without the burn rather than costing the showing.
    That is reported in the status detail, so the operator learns the subtitles
    were dropped instead of quietly not getting them."""
    detail = await item_detail(jf_id)
    opts = play_options(detail, subtitles)
    source = None if config.DEMO else jellyfin.file_url(
        config.JELLYFIN_URL, jf_id, config.JELLYFIN_API_KEY
    )
    if opts["subtitles"]:
        opts["subtitle_source"] = subtitle_source(jf_id, detail, source)
        # Nothing burnable: an image-only track cannot be rendered, and pointing
        # the burn at the video file just fails and costs a retry to find out.
        opts["subtitles"] = bool(opts["subtitle_source"])
    _state["jf_id"] = jf_id
    _state["started_at"] = asyncio.get_running_loop().time()
    # Bumped before the start, not after: starting stops the previous ffmpeg,
    # and the supervisor watching THAT one wakes up inside this call.
    _state["generation"] += 1
    generation = _state["generation"]
    await _player.start(player.build_play_args(source, opts))
    await send_status(socket, "starting")
    _start_supervisor(socket, jf_id, opts, source, generation)
    return detail


def _start_supervisor(socket, jf_id, opts, source, generation):
    global _supervisor
    if _supervisor:
        _supervisor.cancel()
    _supervisor = asyncio.create_task(
        _supervise(socket, jf_id, opts, source, generation)
    )


async def _supervise(socket, jf_id, opts, source, generation):
    """Report the showing while it runs, and account for how it ended."""
    started = asyncio.get_running_loop().time()
    position = 0
    try:
        while _player.running():
            await asyncio.sleep(config.STATUS_INTERVAL)
            if not _player.running():
                break
            position += config.STATUS_INTERVAL
            await send_status(socket, "playing", position_s=position)
        code = await _player.wait()
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.warning("the play supervisor failed", exc_info=True)
        return
    ran_for = asyncio.get_running_loop().time() - started
    # Something else has started or stopped since this showing began, so its
    # ending is not news. Counted rather than compared by id: putting the SAME
    # title back on (the gate's restart-without-subtitles) reads as unchanged
    # from here, and this supervisor would report the new showing idle.
    if _state["generation"] != generation:
        return
    tail = _player.stderr_text()
    died_early = code not in (0, None) and ran_for < player.EARLY_EXIT_SECONDS
    if (died_early and opts.get("subtitles")
            and player.is_subtitle_failure(tail)):
        logger.warning("subtitle burn failed; retrying without it")
        retry = dict(opts, subtitles=False)
        await _player.start(player.build_play_args(source, retry))
        await send_status(
            socket, "starting", detail="subtitles could not be burned in"
        )
        # The same generation: this is the same showing, retried.
        _start_supervisor(socket, jf_id, retry, source, generation)
        return
    # Same idea one step down: a GPU that will not encode should cost the
    # picture quality, not the showing. Without this the room just falls back to
    # the intermission card and nothing on screen says why.
    if (died_early and opts.get("vaapi_device")
            and player.is_hwaccel_failure(tail)):
        logger.warning("hardware encoding failed; retrying on the CPU")
        retry = dict(opts, vaapi_device="")
        await _player.start(player.build_play_args(source, retry))
        await send_status(
            socket, "starting", detail="hardware encoding unavailable, using the CPU"
        )
        _start_supervisor(socket, jf_id, retry, source, generation)
        return
    _state["jf_id"] = None
    if code in (0, None) or ran_for >= player.EARLY_EXIT_SECONDS:
        await send_status(socket, "idle")
    else:
        await send_status(socket, "error", detail=_last_line(tail))


def _last_line(text, limit=200):
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    return lines[-1][:limit] if lines else ""


async def stop_playing(socket):
    global _supervisor
    if _supervisor:
        _supervisor.cancel()
        _supervisor = None
    _state["jf_id"] = None
    # A stop ends this showing too, so anything still watching it is stale.
    _state["generation"] += 1
    await _player.stop()
    await send_status(socket, "idle")


# ---- Dispatch -------------------------------------------------------------

async def handle(socket, message):
    request_id = message.get("id")
    method = message.get("method")
    params = message.get("params") or {}
    if request_id is None or not method:
        return
    try:
        if method == "ping":
            await socket.send(reply(request_id, {"ok": True}))
        elif method == "search":
            await socket.send(
                reply(request_id, await do_search(str(params.get("query") or "")))
            )
        elif method == "episodes":
            await socket.send(
                reply(request_id, await do_episodes(str(params.get("series") or "")))
            )
        elif method == "art":
            await socket.send(
                reply(request_id, await do_art(str(params.get("jf_id") or "")))
            )
        elif method == "play":
            detail = await start_playing(
                socket, str(params.get("jf_id") or ""), params.get("subtitles", True)
            )
            await socket.send(reply(request_id, detail))
        elif method == "stop":
            await stop_playing(socket)
            await socket.send(reply(request_id, {"ok": True}))
        else:
            await socket.send(error(request_id, f"unknown method {method}"))
    except Exception as exc:
        # The gate turns this into a 502 with the operator's own wording; what
        # went wrong here is logged here, where the library actually is.
        logger.warning("%s failed: %r", method, exc)
        await socket.send(error(request_id, f"{method} failed"))


async def serve(socket):
    await send_status(socket, _state["state"])
    async for raw in socket:
        try:
            message = json.loads(raw)
        except ValueError:
            logger.debug("ignoring an unparseable frame")
            continue
        if isinstance(message, dict):
            await handle(socket, message)


def gate_url():
    """The socket URL with the key in the query string. Never logged."""
    joiner = "&" if "?" in config.GATE_URL else "?"
    return f"{config.GATE_URL}{joiner}key={config.KEY}"


async def run():
    if not (config.GATE_URL and config.KEY):
        raise SystemExit(
            "projector: set PROJECTOR_GATE_URL and PROJECTOR_KEY (see README)"
        )
    if not config.INGEST_URL:
        raise SystemExit("projector: set PROJECTOR_INGEST_URL (see README)")
    if not config.DEMO and not (config.JELLYFIN_URL and config.JELLYFIN_API_KEY):
        raise SystemExit(
            "projector: set JELLYFIN_URL and JELLYFIN_API_KEY, or PROJECTOR_DEMO=1"
        )
    attempt = 0
    while True:
        try:
            logger.info("connecting to the gate")
            async with websockets.connect(gate_url(), max_size=None) as socket:
                attempt = 0
                logger.info("connected")
                await serve(socket)
            logger.info("the gate closed the connection")
        except Exception as exc:
            # Nothing here is worth exiting over: the gate may be updating, the
            # link may be down, the key may not be set yet. Say so and retry.
            logger.warning("connection failed: %r", exc)
        await stop_playing_quietly()
        delay = config.BACKOFF[min(attempt, len(config.BACKOFF) - 1)]
        attempt += 1
        await asyncio.sleep(delay)


async def stop_playing_quietly():
    """Stop ffmpeg when the link drops. Publishing on with nobody to tell would
    leave the gate showing a title it can no longer stop."""
    global _supervisor
    if _supervisor:
        _supervisor.cancel()
        _supervisor = None
    _state["jf_id"] = None
    _state["state"] = "idle"
    _state["generation"] += 1
    await _player.stop()


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass
