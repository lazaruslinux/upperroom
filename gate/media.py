"""
Recording, clips, and the live preview thumbnail for the upperroom gate.

A broadcast is recorded with a plain stream copy (no transcode) to local scratch
while live, then archived to the media store when it ends. Clips are cut from
that in-progress file on demand, and a background worker keeps a fresh preview
frame for the home card. The stream watcher ties it together: it polls MediaMTX
and drives the online/offline transitions (start/stop recording, wipe chat,
announce go-live).
"""

import asyncio
import logging
import os
import shutil
import signal
import time
from datetime import datetime, timezone

import httpx

import db
from config import (
    CLIP_DIR, CLIP_LAG, CLIP_SECONDS, MAX_CLIP_NAME, MEDIAMTX_API, RECORD_TMP,
    RTMP_SOURCE, STREAM_PATH, THUMB_INTERVAL, THUMB_PATH, THUMB_TMP, VOD_DIR,
    VOD_KEEP, VOD_KEEP_DAYS,
)
from hub import hub
from notify import notify_live

logger = logging.getLogger("upperroom.media")


async def fetch_path():
    """Return the MediaMTX path JSON for our stream, or None on any error."""
    url = f"{MEDIAMTX_API}/v3/paths/get/{STREAM_PATH}"
    try:
        async with httpx.AsyncClient(timeout=5) as http:
            reply = await http.get(url)
        if reply.status_code == 200:
            return reply.json()
    except httpx.HTTPError as exc:
        # Expected whenever MediaMTX is briefly unreachable or the stream is
        # offline; the caller treats None as "not live". Chatty, so debug.
        logger.debug("MediaMTX path poll failed: %r", exc)
    return None


async def stream_watcher():
    """Wipe the chat when a broadcast ends, so the next stream starts clean."""
    was_online = False
    while True:
        try:
            data = await fetch_path()
            online = bool(data and data.get("ready", False))
            # Open/close watch sessions on the live<->offline transition so
            # watch time only counts while the stream is live.
            await hub.set_live(online)
            if online and not was_online:
                logger.info("stream online")
                await start_recording()
                # Announce in the background so a slow webhook or mail relay never
                # delays the status poll. notify_live enforces its own cooldown.
                asyncio.create_task(notify_live())
            if was_online and not online:
                logger.info("stream offline")
                await hub.wipe()
                await stop_recording()
            was_online = online
        except Exception:
            logger.warning("stream watcher poll failed", exc_info=True)
        await asyncio.sleep(5)


async def capture_thumbnail():
    """Save one frame from the live stream into THUMB_PATH. Best effort.

    While a broadcast is recording, the stream is already being written to local
    disk, so we grab the freshest frame from that file instead of opening a
    second full RTMP pull just for a thumbnail. When no recording is in progress
    (a brief window right at go-live), fall back to a short RTMP read."""
    rec_path = _rec["tmp_path"] if _rec["active"] else None
    if rec_path and os.path.exists(rec_path):
        # -sseof -1 seeks to one second before the end of the file, reading the
        # most recent frame without decoding the whole recording.
        source_args = ["-sseof", "-1", "-i", rec_path]
    else:
        source_args = ["-rw_timeout", "5000000", "-i", RTMP_SOURCE]
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y", "-loglevel", "error",
        *source_args,
        "-frames:v", "1",
        "-vf", "scale=640:-2",             # 640px wide, height kept even
        "-q:v", "5",
        # The output has a ".tmp" extension ffmpeg cannot map to a muxer, so
        # force the image2 (JPEG) format explicitly or every capture fails.
        "-f", "image2",
        THUMB_TMP,
        stdout=asyncio.subprocess.DEVNULL,
        # Keep ffmpeg's stderr so a failed capture can be diagnosed. communicate()
        # below drains it so the small "-loglevel error" output cannot block.
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=12)
    except asyncio.TimeoutError:
        proc.kill()
        logger.warning("thumbnail capture timed out")
        return
    # Swap in atomically so a half-written file is never served.
    if proc.returncode == 0 and os.path.exists(THUMB_TMP):
        os.replace(THUMB_TMP, THUMB_PATH)
    else:
        last = ""
        if stderr:
            tail = stderr.decode("utf-8", "replace").strip().splitlines()
            last = tail[-1] if tail else ""
        logger.warning(
            "thumbnail capture failed (rc=%s): %s", proc.returncode, last
        )


async def thumbnail_worker():
    """While the stream is live, refresh the preview thumbnail on an interval.
    When it goes offline, drop the stale frame so the card shows offline."""
    while True:
        try:
            data = await fetch_path()
            if data and data.get("ready", False):
                await capture_thumbnail()
            elif os.path.exists(THUMB_PATH):
                os.remove(THUMB_PATH)
        except Exception:
            logger.debug("thumbnail worker iteration failed", exc_info=True)
        await asyncio.sleep(THUMB_INTERVAL)


# ---- Recording (VODs) and clips -------------------------------------------
# A broadcast is recorded with a plain stream copy (no transcode) to local
# scratch while live, then archived to the media store when it ends. Clips are
# cut from that in-progress file on demand.

_rec = {
    "active": False, "vod_id": None, "tmp_path": None,
    "started_at": None, "proc": None,
}


async def _run_ffmpeg(args, timeout):
    """Run an ffmpeg/ffprobe command to completion, returning (returncode, stdout).
    Best effort: a timeout or failure returns (None, b'')."""
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return proc.returncode, out
    except Exception:
        logger.debug("ffmpeg/ffprobe command failed: %s", args[0], exc_info=True)
        if proc and proc.returncode is None:
            try:
                proc.kill()
            except Exception:
                logger.debug("could not kill ffmpeg subprocess", exc_info=True)
        return None, b""


async def _make_poster(src, dst, seek=2):
    """Save a single frame as the card poster for a VOD or clip."""
    await _run_ffmpeg(
        ["ffmpeg", "-y", "-loglevel", "error", "-ss", str(seek), "-i", src,
         "-frames:v", "1", "-vf", "scale=640:-2", "-q:v", "5", dst],
        timeout=20,
    )


async def _probe_duration(path):
    code, out = await _run_ffmpeg(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", path],
        timeout=20,
    )
    try:
        return int(float(out.decode().strip()))
    except Exception:
        logger.debug("could not probe duration of %s", path, exc_info=True)
        return 0


def _remove_media_files(folder, filename, item_id):
    for name in (filename, f"{item_id}.jpg"):
        if not name:
            continue
        path = os.path.join(folder, os.path.basename(name))
        try:
            if os.path.exists(path):
                os.remove(path)
        except Exception:
            logger.debug("could not remove media file %s", path, exc_info=True)


async def start_recording():
    if _rec["active"]:
        return
    started_at = int(time.time())
    info = db.get_stream_info()
    try:
        vod_id = db.create_vod(
            info["stream_title"], info["stream_description"], started_at
        )
    except Exception:
        logger.warning("could not create VOD row; recording skipped", exc_info=True)
        return
    tmp_path = os.path.join(RECORD_TMP, f"{vod_id}.mp4")
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", "-loglevel", "error",
            "-i", RTMP_SOURCE,
            "-c", "copy",
            "-f", "mp4",
            # A fragmented MP4 is web playable and survives an abrupt stop, which
            # matters because we cut clips from it while it is still being written.
            "-movflags", "+frag_keyframe+empty_moov+default_base_moof",
            tmp_path,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except Exception:
        logger.warning("could not start recording ffmpeg", exc_info=True)
        try:
            db.delete_media("vod", vod_id)
        except Exception:
            logger.debug("could not delete stub VOD row", exc_info=True)
        return
    _rec.update(
        active=True, vod_id=vod_id, tmp_path=tmp_path,
        started_at=started_at, proc=proc,
    )
    logger.info("recording started: %s", tmp_path)


async def stop_recording():
    if not _rec["active"]:
        return
    vod_id, tmp_path = _rec["vod_id"], _rec["tmp_path"]
    started_at, proc = _rec["started_at"], _rec["proc"]
    ended_at = int(time.time())
    # Mark inactive at once so clips stop and a quick re-go-live starts clean.
    _rec.update(active=False, vod_id=None, tmp_path=None, started_at=None, proc=None)
    if proc and proc.returncode is None:
        try:
            proc.send_signal(signal.SIGINT)   # let ffmpeg write the trailer
            await asyncio.wait_for(proc.wait(), timeout=15)
        except Exception:
            logger.warning(
                "recording ffmpeg did not stop cleanly; killing it", exc_info=True
            )
            try:
                proc.kill()
            except Exception:
                logger.debug("could not kill recording ffmpeg", exc_info=True)
    # Archive and finalize in the background so a slow transfer to the media
    # store (which may be a network mount) never blocks the stream watcher.
    asyncio.create_task(_finalize_recording(vod_id, tmp_path, started_at, ended_at))


async def _finalize_recording(vod_id, tmp_path, started_at, ended_at):
    try:
        if not (tmp_path and os.path.exists(tmp_path)
                and os.path.getsize(tmp_path) > 100_000):
            db.delete_media("vod", vod_id)
            if tmp_path and os.path.exists(tmp_path):
                os.remove(tmp_path)
            return
        filename = f"{vod_id}.mp4"
        # Poster first, while the file is still on fast local scratch.
        await _make_poster(tmp_path, os.path.join(VOD_DIR, f"{vod_id}.jpg"))
        dst = os.path.join(VOD_DIR, filename)
        # Remux the recording into a regular, faststart MP4. The live recording is
        # a fragmented MP4 (empty_moov + keyframe fragments) so it survives an
        # abrupt stop, but fragmented files load slowly and break some mobile
        # players. A plain stream copy with +faststart rewrites it to a single
        # moov-at-front file that seeks and plays everywhere; no re-encode, so it
        # stays quick. If the remux fails, fall back to moving the raw recording
        # so the VOD is never lost, even if playback is degraded.
        code, _ = await _run_ffmpeg(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", tmp_path,
             "-c", "copy", "-movflags", "+faststart", dst],
            timeout=600,
        )
        if code == 0 and os.path.exists(dst) and os.path.getsize(dst) > 100_000:
            try:
                os.remove(tmp_path)
            except OSError:
                logger.debug("could not remove scratch recording %s", tmp_path,
                             exc_info=True)
        else:
            logger.warning(
                "recording remux failed (rc=%s); keeping raw file for %s",
                code, dst,
            )
            await asyncio.to_thread(shutil.move, tmp_path, dst)
        duration = await _probe_duration(dst) or max(0, ended_at - started_at)
        db.finalize_vod(vod_id, ended_at, duration, filename)
        db.snapshot_chat("vod", vod_id, started_at, ended_at)
        logger.info("recording finalized: %s (%ss)", dst, duration)
        # Enforce retention, removing the oldest VODs (rows and files) over the cap.
        try:
            for doomed in db.prune_vods(VOD_KEEP, VOD_KEEP_DAYS, int(time.time())):
                _remove_media_files(VOD_DIR, doomed.get("filename"), doomed["id"])
                logger.info("VOD pruned by retention: id=%s", doomed["id"])
        except Exception:
            logger.warning("VOD retention pruning failed", exc_info=True)
    except Exception:
        logger.warning("recording finalize failed for vod_id=%s", vod_id,
                       exc_info=True)


def cooldown_for(user):
    """A user's clip cooldown in seconds (0 disables it). Per-role and
    admin-configured: admins get the shortest, then moderators, then viewers."""
    info = db.get_stream_info()
    if user and user["is_admin"]:
        minutes = int(info["clip_cooldown_admin"])
    elif user and user["is_moderator"]:
        minutes = int(info["clip_cooldown_mod"])
    else:
        minutes = int(info["clip_cooldown_user"])
    return max(0, minutes) * 60


def format_remaining(seconds):
    """A short 'Xm Ys' / 'Ys' label for the cooldown message."""
    seconds = max(0, int(seconds))
    minutes, secs = divmod(seconds, 60)
    if minutes and secs:
        return f"{minutes}m {secs}s"
    if minutes:
        return f"{minutes}m"
    return f"{secs}s"


async def make_clip(user, name):
    """Cut the last CLIP_SECONDS of the live stream into a named clip. Returns
    (clip_id, None) on success or (None, error_message)."""
    if not _rec["active"]:
        return None, "The stream is not live."
    username = user["username"]
    cooldown = cooldown_for(user)
    if cooldown:
        elapsed = int(time.time()) - db.last_clip_at(username)
        if elapsed < cooldown:
            return None, (
                f"Clip cooldown — you can clip again in "
                f"{format_remaining(cooldown - elapsed)}."
            )
    started_at, src, vod_id = _rec["started_at"], _rec["tmp_path"], _rec["vod_id"]
    end = int(time.time()) - CLIP_LAG
    start = max(started_at, end - CLIP_SECONDS)
    duration = end - start
    if duration < 3:
        return None, "The stream just started; nothing to clip yet."
    name = (name or "").strip()[:MAX_CLIP_NAME] or "Clip"
    # Create the row first so the file can be named by its id.
    clip_id = db.create_clip(
        name, "", username, vod_id, start, end, duration, int(time.time())
    )
    filename = f"{clip_id}.mp4"
    dst = os.path.join(CLIP_DIR, filename)
    code, _ = await _run_ffmpeg(
        ["ffmpeg", "-y", "-loglevel", "error",
         "-ss", str(start - started_at), "-i", src, "-t", str(duration),
         "-c", "copy", "-movflags", "+faststart", dst],
        timeout=40,
    )
    if code != 0 or not os.path.exists(dst) or os.path.getsize(dst) < 1000:
        db.delete_media("clip", clip_id)
        _remove_media_files(CLIP_DIR, filename, clip_id)
        return None, "Could not make the clip. Try again in a moment."
    db.set_clip_filename(clip_id, filename)
    await _make_poster(dst, os.path.join(CLIP_DIR, f"{clip_id}.jpg"), seek=1)
    db.snapshot_chat("clip", clip_id, start, end)
    logger.info(
        "clip created: id=%s name=%r by=%s (%ss)", clip_id, name, username, duration
    )
    return clip_id, None


def ready_epoch(ready_time):
    # MediaMTX reports readyTime as an RFC3339 string, with nanosecond precision
    # and a trailing Z, e.g. "2026-06-27T12:34:56.789012345Z". Turn it into a
    # plain Unix timestamp the browser can use to show how long the stream has
    # been live. Anything unparseable returns None, and the page simply omits
    # the duration rather than breaking.
    if not ready_time:
        return None
    text = ready_time.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    # datetime.fromisoformat only accepts 3 or 6 fractional digits, so trim the
    # nanoseconds down to microseconds while leaving any timezone offset intact.
    if "." in text:
        head, _, tail = text.partition(".")
        frac = ""
        rest = ""
        for i, ch in enumerate(tail):
            if ch.isdigit():
                frac += ch
            else:
                rest = tail[i:]
                break
        text = head + "." + frac[:6] + rest
    try:
        when = datetime.fromisoformat(text)
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return int(when.timestamp())
