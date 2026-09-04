"""
Recording, clips, and the live preview thumbnail for the upperroom gate.

A broadcast is recorded with a plain stream copy (no transcode) to local scratch
while live, then archived to the media store when it ends. Clips are cut from
that in-progress file on demand, and a background worker keeps a fresh preview
frame for the home card. The stream watcher ties it together: it polls MediaMTX
and drives the online/offline transitions (start/stop recording, announce
go-live, and narrate the night in chat).
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
import theater
from config import (
    CLIP_COOLDOWN_HOST_SECONDS, CLIP_COOLDOWN_SECONDS, CLIP_DIR,
    CLIP_KEYFRAME_SLACK, CLIP_LAG, DEFAULT_CLIP_LENGTH, MAX_CLIP_NAME,
    MEDIAMTX_API, MEDIA_DIR, SHARED_DIR,
    MEDIA_SOURCE, POINTS_PER_MINUTE, RECORD_BACKOFF, RECORD_STALL_POLLS,
    CHAT_IDLE_WIPE_SECONDS, NIGHT_GAP_SECONDS,
    RECORD_STARTUP_GRACE, RECORD_SURVIVAL_SECONDS, RECORD_TMP, RETENTION_INTERVAL,
    STREAM_PATH, THUMB_INTERVAL, THUMB_PATH, THUMB_TMP, VOD_DIR,
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


def credit_watch_points():
    """Credit one round of watch points: POINTS_PER_MINUTE to each distinct
    viewer connected to chat right now, but only while the stream is live. The
    stream watcher calls this once per minute of live time. A single UPDATE
    covers everyone, and each person is credited once no matter how many tabs
    they have open. Best effort: it never raises into the watcher loop. Returns
    the number of accounts credited."""
    if not hub.is_live():
        return 0
    usernames = hub.present_usernames()
    if not usernames:
        return 0
    try:
        return db.credit_points(usernames, POINTS_PER_MINUTE)
    except Exception:
        logger.debug("credit_watch_points failed", exc_info=True)
        return 0


async def stream_watcher():
    """Follow the stream up and down, and drive what each transition means."""
    was_online = False
    # Accrue watch points once per minute of live time. We track elapsed live
    # seconds across polls with a monotonic clock and credit a round each time it
    # crosses 60, so the rate stays one round per minute regardless of the poll
    # interval. The accumulator resets when the stream goes offline.
    live_seconds = 0.0
    last_tick = time.monotonic()
    while True:
        try:
            data = await fetch_path()
            online = bool(data and data.get("ready", False))
            # Open/close watch sessions on the live<->offline transition so
            # watch time only counts while the stream is live.
            await hub.set_live(online)
            now = time.monotonic()
            if online:
                live_seconds += now - last_tick
                while live_seconds >= 60:
                    live_seconds -= 60
                    credit_watch_points()
            else:
                live_seconds = 0.0
            last_tick = now
            if online and not was_online:
                logger.info("stream online")
                # Fresh broadcast: clear any leftover failure-streak backoff.
                _watch.update(last_size=-1, no_growth=0, attempts=0,
                              next_retry_at=0.0)
                plan = theater.stream_transition(True, theater.is_active())
                await wipe_if_new_night()
                if plan["record"]:
                    await start_recording()
                else:
                    logger.info(
                        "theater session running: not recording this stream"
                    )
                # Announce in the background so a slow webhook or mail relay never
                # delays the status poll. notify_live enforces its own cooldown.
                if plan["notify"]:
                    asyncio.create_task(notify_live())
                if plan["state"]:
                    await theater.set_stage(plan["state"])
            elif online:
                # Supervise the in-progress recording: restart it if the recorder
                # died or its scratch file stalled while the stream is still live.
                await _recorder_watchdog()
            if was_online and not online:
                logger.info("stream offline")
                plan = theater.stream_transition(
                    False, theater.is_active(), theater.recently_closed()
                )
                if plan["announce_end"]:
                    # Chat is not wiped here any more: the evening carries on,
                    # into theater or just into people talking.
                    db.set_last_air_ended_at(int(time.time()))
                    await hub.narrate("Stream ended.")
                if plan["state"]:
                    await theater.set_stage(plan["state"])
                await stop_recording()
            was_online = online
        except Exception:
            logger.warning("stream watcher poll failed", exc_info=True)
        await asyncio.sleep(5)


# Live input tuning. A long-running, messy session can defeat ffmpeg's default
# codec probing ("could not find codec parameters"); giving it a larger analyze
# window and probe budget makes joining one mid-flight far more reliable.
_PROBE_ARGS = ["-analyzeduration", "10000000", "-probesize", "10000000"]


def source_input_args(source, io_timeout=None):
    """ffmpeg input args for reading the live stream back from MediaMTX.

    Scheme aware, because MEDIA_SOURCE is an operator setting and can be pointed
    anywhere ffmpeg can open. For the RTSP default we force TCP: the UDP
    transport drops packets under load and ffmpeg surfaces the loss as a demux
    error, which is the failure this read path exists to avoid.

    io_timeout (microseconds) makes a read give up rather than hang. It is set
    for the thumbnail grab, which must stay responsive, and deliberately left off
    the recorder, whose stalls are the watchdog's job to detect and restart. The
    option spelling differs by demuxer: RTSP calls it -timeout, everything else
    takes the generic -rw_timeout. Passing the wrong one is not ignored, ffmpeg
    exits with "Option not found" and the read never happens at all.
    """
    args = [*_PROBE_ARGS]
    if source.startswith("rtsp://"):
        args += ["-rtsp_transport", "tcp"]
        if io_timeout:
            args += ["-timeout", str(io_timeout)]
    elif io_timeout:
        args += ["-rw_timeout", str(io_timeout)]
    return [*args, "-i", source]


# Which streams to copy out of the live source. ffmpeg's default selection picks
# one stream per type, which was fine over RTMP where a single muxed FLV carried
# both tracks, but over RTSP the tracks arrive as separate RTP streams and audio
# was being dropped silently: every recording made after the RTSP switch had a
# video track and nothing else. Map explicitly instead of trusting the default.
# The "?" on the audio map makes it optional, so a genuinely video-only source
# still records rather than failing outright.
COPY_MAPS = ["-map", "0:v:0", "-map", "0:a:0?"]


def recorder_args(source, tmp_path):
    """The full ffmpeg argv for the broadcast recorder.

    Kept as a pure function so the stream mapping can be asserted in a test. It
    has to be: dropping the audio track produces a file that is the right size,
    plays fine, and passes every check except listening to it."""
    return [
        "ffmpeg", "-y", "-loglevel", "error",
        # Give codec probing a wide window and budget so joining a messy,
        # long-running session does not fail with "could not find codec
        # parameters" and die instantly. No read timeout here on purpose: a
        # stalled recorder is the watchdog's to detect and restart.
        *source_input_args(source),
        *COPY_MAPS,
        "-c", "copy",
        "-f", "mp4",
        # A fragmented MP4 is web playable and survives an abrupt stop, which
        # matters because we cut clips from it while it is still being written.
        "-movflags", "+frag_keyframe+empty_moov+default_base_moof",
        tmp_path,
    ]


# Thumbnail capture is best effort and runs every THUMB_INTERVAL seconds, so a
# persistent failure (e.g. a dead scratch file) would otherwise spam the log. We
# warn once, stay quiet (debug) while it keeps failing, and log a single INFO when
# it recovers.
_thumb_fail = {"failing": False}


async def _grab_frame(source_args, timeout=12):
    """Run one ffmpeg single-frame grab into THUMB_TMP. Returns (rc, stderr).
    rc is None on timeout. Does not swap the file into place."""
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
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return None, b"capture timed out"
    return proc.returncode, stderr or b""


async def capture_thumbnail():
    """Save one frame from the live stream into THUMB_PATH. Best effort.

    While a broadcast is recording, the stream is already being written to local
    disk, so we grab the freshest frame from that file instead of opening a
    second full pull just for a thumbnail. If that scratch file is dead or
    stalled the grab fails, so we immediately retry once via a live read. When no
    recording is in progress (a brief window right at go-live), we go straight to
    the live read."""
    live_args = source_input_args(MEDIA_SOURCE, io_timeout=5000000)
    rec_path = _rec["tmp_path"] if _rec["active"] else None
    if rec_path and os.path.exists(rec_path):
        # -sseof -1 seeks to one second before the end of the file, reading the
        # most recent frame without decoding the whole recording.
        rc, stderr = await _grab_frame(["-sseof", "-1", "-i", rec_path])
        if rc != 0:
            # The scratch file may be dead or stalled; fall back to a live read
            # within the same call so one bad recording never stalls previews.
            rc, stderr = await _grab_frame(live_args)
    else:
        rc, stderr = await _grab_frame(live_args)

    # Swap in atomically so a half-written file is never served.
    if rc == 0 and os.path.exists(THUMB_TMP):
        os.replace(THUMB_TMP, THUMB_PATH)
        if _thumb_fail["failing"]:
            _thumb_fail["failing"] = False
            logger.info("thumbnail capture recovered")
        return
    last = _stderr_tail(stderr)
    if not _thumb_fail["failing"]:
        _thumb_fail["failing"] = True
        logger.warning("thumbnail capture failed (rc=%s): %s", rc, last)
    else:
        logger.debug("thumbnail capture still failing (rc=%s): %s", rc, last)


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
    "stderr": None,        # bounded (~2KB) tail of the recorder's stderr
    "drain": None,         # task that keeps that tail drained and current
}

# Recorder supervision state, tracked across stream_watcher polls. Kept separate
# from _rec because it outlives a single recording: attempts/next_retry_at carry
# a failure streak's backoff across restarts, reset only on a healthy survival.
_watch = {
    "last_size": -1,       # scratch size seen at the previous poll
    "no_growth": 0,        # consecutive polls with no scratch growth
    "attempts": 0,         # restarts in the current failure streak
    "next_retry_at": 0.0,  # monotonic time before which a restart must wait
}

# A single restart runs at a time; the startup grace check and the watchdog can
# both spot the same failure, and this lock keeps them from double-restarting.
_restart_lock = asyncio.Lock()

# True while an archive is in flight. The startup retry, the hourly retry and the
# archive a finishing broadcast starts can all want the same work at once, and a
# recording is now parked for the whole of its own archive, so a second pass
# would find it mid-flight and remux it again. A plain flag rather than a lock:
# the second caller must give up, not queue behind one vCPU's worth of ffmpeg.
_archiving = {"busy": False}

# Watchdog decision outcomes.
WATCHDOG_NONE = "none"        # recording looks healthy; do nothing
WATCHDOG_WAIT = "wait"        # failed, but still inside the backoff window
WATCHDOG_RESTART = "restart"  # failed and clear to restart now


def recording_status():
    """The recorder's health for the dashboard's stream strip, read straight from
    the state the stream watcher already tracks. 'off' when nothing is being
    recorded, 'restarting' while a failure streak is being cycled (the recorder
    died or the scratch file stalled and the watchdog is backing off and retrying),
    and 'ok' while the scratch file is growing normally. It keeps no state of its
    own, so it can never disagree with the watcher about what is happening."""
    if not _rec["active"]:
        return "off"
    if _watch["attempts"] > 0:
        return "restarting"
    return "ok"


def watchdog_action(proc_alive, no_growth_polls, stall_threshold,
                    now, next_retry_at):
    """Decide what the recorder watchdog should do this poll (pure).

    Failure is either a dead process or a scratch file that has not grown for at
    least stall_threshold consecutive polls. On failure we restart only once the
    backoff window has passed (now >= next_retry_at), otherwise we wait."""
    failed = (not proc_alive) or (no_growth_polls >= stall_threshold)
    if not failed:
        return WATCHDOG_NONE
    if now < next_retry_at:
        return WATCHDOG_WAIT
    return WATCHDOG_RESTART


def backoff_delay(attempts):
    """Seconds to wait before the next restart, given how many restarts this
    failure streak has already made (pure). Immediate first retry, then the
    RECORD_BACKOFF schedule, capped at its last value."""
    if attempts < 0:
        attempts = 0
    return RECORD_BACKOFF[min(attempts, len(RECORD_BACKOFF) - 1)]


def survived_long_enough(uptime_seconds):
    """Whether a recording has run long enough to be considered healthy, which
    clears the restart backoff so a later, unrelated failure retries promptly."""
    return uptime_seconds >= RECORD_SURVIVAL_SECONDS


def _stderr_tail(buf, limit=500):
    """The last line of an ffmpeg stderr capture, trimmed, for a diagnostic log.
    Accepts bytes/bytearray (a bounded tail buffer) or None; returns ''."""
    if not buf:
        return ""
    if isinstance(buf, (bytes, bytearray)):
        text = bytes(buf).decode("utf-8", "replace")
    else:
        text = str(buf)
    text = text.strip()
    if not text:
        return ""
    return text.splitlines()[-1][-limit:]


async def _run_ffmpeg(args, timeout):
    """Run an ffmpeg/ffprobe command to completion, returning (rc, stdout, stderr).
    Best effort: a timeout or failure returns (None, b'', b'')."""
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return proc.returncode, out, err
    except Exception:
        logger.debug("ffmpeg/ffprobe command failed: %s", args[0], exc_info=True)
        if proc and proc.returncode is None:
            try:
                proc.kill()
            except Exception:
                logger.debug("could not kill ffmpeg subprocess", exc_info=True)
        return None, b"", b""


async def _make_poster(src, dst, seek=2):
    """Save a single frame as the card poster for a VOD or clip."""
    await _run_ffmpeg(
        ["ffmpeg", "-y", "-loglevel", "error", "-ss", str(seek), "-i", src,
         "-frames:v", "1", "-vf", "scale=640:-2", "-q:v", "5", dst],
        timeout=20,
    )


async def _probe_duration(path):
    code, out, _ = await _run_ffmpeg(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", path],
        timeout=20,
    )
    try:
        return int(float(out.decode().strip()))
    except Exception:
        logger.debug("could not probe duration of %s", path, exc_info=True)
        return 0


def shared_paths(token):
    """The public file names for a share token: the video and its poster."""
    return (
        os.path.join(SHARED_DIR, f"{token}.mp4"),
        os.path.join(SHARED_DIR, f"{token}.jpg"),
    )


def link_shared(clip_id, filename, token):
    """Make a published clip reachable by hard-linking it into SHARED_DIR.

    A hard link is a second name for the same bytes, so this costs no disk and
    cannot drift from the original. Returns True if the video was linked; the
    poster is best effort, since a clip without one is still watchable.
    """
    os.makedirs(SHARED_DIR, exist_ok=True)
    source = os.path.join(CLIP_DIR, os.path.basename(filename or ""))
    video, poster = shared_paths(token)
    if not filename or not os.path.exists(source):
        logger.warning("cannot publish clip %s: %s is missing", clip_id, source)
        return False
    try:
        if not os.path.exists(video):
            os.link(source, video)
    except OSError:
        logger.warning("could not link %s for sharing", source, exc_info=True)
        return False
    source_poster = os.path.join(CLIP_DIR, f"{clip_id}.jpg")
    try:
        if os.path.exists(source_poster) and not os.path.exists(poster):
            os.link(source_poster, poster)
    except OSError:
        logger.debug("could not link the poster for clip %s", clip_id, exc_info=True)
    return True


def unlink_shared(token):
    """Remove the public names for a token. The clip itself is untouched: the
    bytes survive because CLIP_DIR still holds a name for them."""
    if not token:
        return
    for path in shared_paths(token):
        try:
            if os.path.exists(path):
                os.remove(path)
        except OSError:
            logger.warning("could not unshare %s", path, exc_info=True)


def sweep_orphan_shared():
    """Delete public files with no live token behind them.

    Belt and braces for the case that matters most here: a file left reachable
    after its clip is gone. Publishing and unpublishing keep these in step, but
    this is the one directory where being wrong means strangers can still watch
    something that was deleted, so it is also checked at startup."""
    if not os.path.isdir(SHARED_DIR):
        return 0
    live = db.published_clip_tokens()
    removed = 0
    for name in os.listdir(SHARED_DIR):
        token = os.path.splitext(name)[0]
        if token in live:
            continue
        try:
            os.remove(os.path.join(SHARED_DIR, name))
            removed += 1
            logger.info("removed an orphaned public clip file: %s", name)
        except OSError:
            logger.warning("could not remove %s", name, exc_info=True)
    return removed


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


def cleanup_record_scratch():
    """Remove leftover files in the recording scratch dir that do not belong to
    an in-progress recording. Called at startup, when nothing is recording, so it
    clears the litter a gate restart during a mid-recording leaves behind (e.g.
    /rec/1.mp4 whose VOD row was already dropped by clear_unfinished_vods).
    Files a pending VOD row points at are spared: those recordings finished and
    are only waiting on the media store, so deleting them here is exactly the
    data loss this sweep is otherwise meant to avoid.

    Best effort and logged; never raises."""
    active = _rec["tmp_path"] if _rec["active"] else None
    spared = {os.path.abspath(active)} if active else set()
    try:
        for row in db.pending_vods():
            if row["pending_path"]:
                spared.add(os.path.abspath(row["pending_path"]))
    except Exception:
        # A DB that cannot be read is no reason to start deleting recordings.
        logger.warning("could not read pending archives; skipping scratch sweep",
                       exc_info=True)
        return
    try:
        entries = os.listdir(RECORD_TMP)
    except OSError:
        logger.debug("recording scratch dir %s not listable", RECORD_TMP,
                     exc_info=True)
        return
    for name in entries:
        path = os.path.join(RECORD_TMP, name)
        if os.path.abspath(path) in spared:
            continue
        if not os.path.isfile(path):
            continue
        try:
            os.remove(path)
            logger.info("removed orphaned recording scratch file: %s", path)
        except OSError:
            logger.warning("could not remove scratch file %s", path, exc_info=True)


def _dir_bytes(folder):
    """Bytes used by the regular files in one directory. Stat'ed rather than
    tracked in the database, so it is the truth about the disk and it counts
    orphans whose rows are already gone."""
    total = 0
    try:
        with os.scandir(folder) as entries:
            for entry in entries:
                try:
                    if entry.is_file():
                        total += entry.stat().st_size
                except OSError:
                    continue
    except OSError:
        logger.debug("media dir %s not listable", folder, exc_info=True)
    return total


def media_usage():
    """Bytes used by the media store, split by kind, plus the free space on the
    filesystem holding it. Never touches the database, never raises."""
    vods_bytes = _dir_bytes(VOD_DIR)
    clips_bytes = _dir_bytes(CLIP_DIR)
    free_bytes = 0
    fs_total_bytes = 0
    try:
        usage = shutil.disk_usage(MEDIA_DIR)
        free_bytes = usage.free
        fs_total_bytes = usage.total
    except OSError:
        logger.debug("could not stat the media filesystem", exc_info=True)
    return {
        "vods_bytes": vods_bytes,
        "clips_bytes": clips_bytes,
        "total_bytes": vods_bytes + clips_bytes,
        "free_bytes": free_bytes,
        "fs_total_bytes": fs_total_bytes,
    }


def _item_bytes(item):
    folder = VOD_DIR if item["kind"] == "vod" else CLIP_DIR
    total = 0
    for name in (item.get("filename"), f"{item['id']}.jpg"):
        if not name:
            continue
        try:
            total += os.path.getsize(os.path.join(folder, os.path.basename(name)))
        except OSError:
            continue
    return total


def _remove_item_files(item):
    """Remove a VOD or clip's file and poster. True when nothing of it is left
    on disk, which is what lets the caller decide whether the row may go.

    A published clip has a second name in SHARED_DIR, and the bytes only go when
    the last name does. Missing that would leave a deleted clip still playing
    for anyone holding the link, including after the retention sweep removed it,
    so the public name goes first."""
    folder = VOD_DIR if item["kind"] == "vod" else CLIP_DIR
    if item.get("share_token"):
        unlink_shared(item["share_token"])
    _remove_media_files(folder, item.get("filename"), item["id"])
    for name in (item.get("filename"), f"{item['id']}.jpg"):
        if name and os.path.exists(os.path.join(folder, os.path.basename(name))):
            return False
    return True


def _over_cap(cap_gb, used_bytes):
    return cap_gb > 0 and used_bytes > cap_gb * 1024 * 1024 * 1024


def _remove_batch(items, why):
    """Remove the files for a batch, then delete the rows of the ones whose
    files really went. Files first, deliberately: a row whose file could not be
    removed keeps its recording visible and deletable, while deleting the row
    anyway would leave bytes on disk that nothing points at and that the size
    cap would then try to reclaim by deleting somebody else's recording."""
    gone = []
    for item in items:
        if _remove_item_files(item):
            gone.append(item)
            logger.info("%s removed by %s: id=%s", item["kind"], why, item["id"])
        else:
            logger.warning(
                "%s id=%s was kept: its files could not be removed",
                item["kind"], item["id"],
            )
    if gone:
        db.delete_media_rows(gone)
    return gone


def _apply_size_cap(cap_gb):
    """Bring the media store back under the size cap, oldest first.

    Two things it will not do. It never removes the newest recording or the
    newest clip, so a single file bigger than the cap cannot delete itself the
    moment it lands. And if removing everything it is allowed to remove would
    still leave the store over the cap, it removes nothing at all: the excess is
    then something retention cannot reach (a recording still being written, or a
    file no row points at), and deleting real recordings would not fix it."""
    used = media_usage()["total_bytes"]
    if not _over_cap(cap_gb, used):
        return []
    limit = cap_gb * 1024 * 1024 * 1024
    candidates = db.retention_candidates()
    # The newest of each kind is off limits, whatever the cap says.
    protected = set()
    for kind in ("vod", "clip"):
        of_kind = [c for c in candidates if c["kind"] == kind]
        if of_kind:
            protected.add((kind, of_kind[-1]["id"]))
    doomed = []
    freed = 0
    for item in candidates:
        if used - freed <= limit:
            break
        if (item["kind"], item["id"]) in protected:
            continue
        freed += _item_bytes(item)
        doomed.append(item)
    if used - freed > limit:
        logger.warning(
            "the media store is %s bytes, over the %s GB cap, but only %s bytes "
            "can be reclaimed; removing nothing, because deleting every "
            "recording it is allowed to would still leave it over",
            used, cap_gb, freed,
        )
        return []
    return _remove_batch(doomed, "the size cap")


# One sweep at a time. It runs from three places (the hourly worker, the end of
# a recording, and an admin saving the limits), and two overlapping sweeps would
# each measure the store before the other's deletions and delete far past the
# cap between them.
_retention_lock = asyncio.Lock()


async def enforce_retention():
    """Apply the channel's retention limits: the per-kind count and age limits
    first, then the total size cap. Pinned items are never touched. Best effort
    and logged; never raises. Returns how many items were removed."""
    try:
        limits = db.get_retention()
        if not any(limits.values()):
            return 0
        async with _retention_lock:
            doomed = db.prune_candidates(limits, int(time.time()))
            removed = await asyncio.to_thread(_remove_batch, doomed, "retention")
            capped = await asyncio.to_thread(
                _apply_size_cap, limits["media_cap_gb"]
            )
        return len(removed) + len(capped)
    except Exception:
        logger.warning("retention sweep failed", exc_info=True)
        return 0


def sweep_orphan_media():
    """Remove files in the media store that no row points at.

    They come from a gate that stopped between writing a recording and marking
    it finished, whose row is then dropped at the next start. Left alone they
    are invisible bytes that count against the size cap, which could only pay
    for them by deleting real recordings. Called at startup, when nothing is
    recording or being clipped, for the same reason the scratch sweep is."""
    try:
        known = db.media_filenames()
    except Exception:
        logger.warning("could not list media files to sweep", exc_info=True)
        return 0
    swept = 0
    for kind, folder in (("vod", VOD_DIR), ("clip", CLIP_DIR)):
        try:
            entries = os.listdir(folder)
        except OSError:
            continue
        for name in entries:
            if name in known[kind]:
                continue
            path = os.path.join(folder, name)
            if not os.path.isfile(path):
                continue
            try:
                os.remove(path)
                swept += 1
                logger.info("removed orphaned media file: %s", path)
            except OSError:
                logger.warning("could not remove %s", path, exc_info=True)
    return swept


async def retention_worker():
    """Apply the retention limits on a timer, so lowering a limit on the
    dashboard takes effect without waiting for the next broadcast to end."""
    while True:
        await enforce_retention()
        try:
            await sweep_idle_chat()
        except Exception:
            logger.warning("idle chat sweep failed", exc_info=True)
        # Rides this loop rather than a task of its own: an unreachable media
        # store is not worth its own timer, and hourly is soon enough.
        try:
            await retry_pending_archives()
        except Exception:
            logger.warning("pending archive retry failed", exc_info=True)
        await asyncio.sleep(RETENTION_INTERVAL)


async def wipe_if_new_night():
    """Clear chat when a broadcast opens a new night, and only then.

    Chat used to be wiped when a stream ended, which cut the room off exactly
    when an evening was moving from a broadcast to a film. It is wiped here
    instead, so the wipe lands on an empty room at the start rather than on a
    conversation at the end. The gap test is what makes a restart safe: OBS
    dying and coming back is the same night and keeps everything, while
    tomorrow evening is a new one and starts clean. A channel that has never
    been on air has nothing to clear."""
    last = db.get_last_air_ended_at()
    if not last or int(time.time()) - last < NIGHT_GAP_SECONDS:
        return
    await hub.wipe(reason="new_night")


async def sweep_idle_chat():
    """The backstop for a night with no sequel: once the channel has been off
    air longer than the idle window, the last evening's chat is cleared rather
    than left on screen for days. Runs from the retention worker, and wipes at
    most once because the wipe empties what it measures."""
    last = db.get_last_air_ended_at()
    if not last or int(time.time()) - last < CHAT_IDLE_WIPE_SECONDS:
        return
    if not hub.has_backlog():
        return
    await hub.wipe(reason="idle")


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
            *recorder_args(MEDIA_SOURCE, tmp_path),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.DEVNULL,
            # Keep stderr so a recorder that dies can be diagnosed. A drain task
            # (below) reads it into a bounded tail so a full pipe never stalls it.
            stderr=asyncio.subprocess.PIPE,
        )
    except Exception:
        logger.warning("could not start recording ffmpeg", exc_info=True)
        try:
            db.delete_media("vod", vod_id)
        except Exception:
            logger.debug("could not delete stub VOD row", exc_info=True)
        return
    stderr_tail = bytearray()
    drain = asyncio.create_task(_drain_stderr(proc, stderr_tail))
    _rec.update(
        active=True, vod_id=vod_id, tmp_path=tmp_path,
        started_at=started_at, proc=proc, stderr=stderr_tail, drain=drain,
    )
    # Fresh growth tracking for this recording; the failure-streak backoff
    # (attempts/next_retry_at) is left alone so a restart honours its schedule.
    _watch["last_size"] = -1
    _watch["no_growth"] = 0
    logger.info("recording started: %s", tmp_path)
    # Catch a recorder that dies within the first few seconds (never wrote a
    # usable file) fast, rather than waiting for the stall watchdog.
    asyncio.create_task(_confirm_recorder_started(vod_id))


async def _drain_stderr(proc, buf, tail_max=2048):
    """Continuously read the recorder's stderr into a bounded tail buffer, so a
    full pipe can never stall ffmpeg and the last output survives for diagnosis
    once the process exits. Best effort; ends at EOF."""
    if proc.stderr is None:
        return
    try:
        while True:
            chunk = await proc.stderr.read(4096)
            if not chunk:
                break
            buf.extend(chunk)
            if len(buf) > tail_max:
                del buf[:-tail_max]
    except Exception:
        logger.debug("recorder stderr drain ended", exc_info=True)


async def _stop_recorder_process(proc, drain, graceful):
    """Stop the recorder ffmpeg and its stderr drain. graceful=True sends SIGINT
    so ffmpeg writes the trailer (normal stream end); otherwise it is killed."""
    if proc and proc.returncode is None:
        try:
            if graceful:
                proc.send_signal(signal.SIGINT)   # let ffmpeg write the trailer
            else:
                proc.kill()
            await asyncio.wait_for(proc.wait(), timeout=15)
        except Exception:
            logger.warning(
                "recording ffmpeg did not stop cleanly; killing it", exc_info=True
            )
            try:
                proc.kill()
            except Exception:
                logger.debug("could not kill recording ffmpeg", exc_info=True)
    if drain:
        try:
            await asyncio.wait_for(asyncio.shield(drain), timeout=2)
        except Exception:
            drain.cancel()


async def _confirm_recorder_started(vod_id):
    """A recorder that dies within the first few seconds never wrote a usable
    file. Catch that fast, and hand the failure to the shared restart path (which
    cleans up the stub VOD and schedules a backed-off retry)."""
    await asyncio.sleep(RECORD_STARTUP_GRACE)
    # If we have already moved on (stopped, or restarted to a new vod), do nothing.
    if not _rec["active"] or _rec["vod_id"] != vod_id:
        return
    proc = _rec["proc"]
    if proc is None or proc.returncode is None:
        return                                    # still up: hand off to watchdog
    logger.warning(
        "recording failed at startup (rc=%s); stderr: %s",
        proc.returncode, _stderr_tail(_rec["stderr"]),
    )
    await _restart_recording(vod_id)


async def _restart_recording(expected_vod_id):
    """Tear down a failed recording, finalize its partial file if it holds usable
    content (else discard row and file), then start a fresh recording while the
    stream is still live. Serialized and idempotent: a second caller racing on the
    same failure sees the vod already replaced and returns."""
    async with _restart_lock:
        if not _rec["active"] or _rec["vod_id"] != expected_vod_id:
            return                                # already handled by someone else
        vod_id, tmp_path = _rec["vod_id"], _rec["tmp_path"]
        started_at, proc, drain = _rec["started_at"], _rec["proc"], _rec["drain"]
        ended_at = int(time.time())
        _rec.update(active=False, vod_id=None, tmp_path=None, started_at=None,
                    proc=None, stderr=None, drain=None)
        await _stop_recorder_process(proc, drain, graceful=False)
        try:
            size = (os.path.getsize(tmp_path)
                    if tmp_path and os.path.exists(tmp_path) else 0)
        except OSError:
            size = 0
        if size > 100_000:
            # Keep what we captured before the failure as its own VOD.
            asyncio.create_task(
                _finalize_recording(vod_id, tmp_path, started_at, ended_at))
        else:
            try:
                db.delete_media("vod", vod_id)
            except Exception:
                logger.debug("could not delete failed VOD row", exc_info=True)
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    logger.debug("could not remove failed scratch %s", tmp_path,
                                 exc_info=True)
        # Record the attempt and set the next backoff window before retrying.
        _watch["attempts"] += 1
        _watch["next_retry_at"] = time.monotonic() + backoff_delay(_watch["attempts"])
        await start_recording()


async def _recorder_watchdog():
    """One supervision pass over the active recording, run each poll while the
    stream is online. Detects a dead recorder or a stalled scratch file and, past
    the backoff window, restarts the recording. Never raises."""
    try:
        if not _rec["active"]:
            return
        proc = _rec["proc"]
        vod_id = _rec["vod_id"]
        tmp_path = _rec["tmp_path"]
        started_at = _rec["started_at"]
        now = time.monotonic()
        proc_alive = proc is not None and proc.returncode is None

        # A recording that has run long enough is healthy: clear the failure
        # streak so a later, unrelated failure retries promptly.
        if started_at and survived_long_enough(int(time.time()) - started_at):
            if _watch["attempts"]:
                _watch["attempts"] = 0
                _watch["next_retry_at"] = 0.0

        try:
            size = (os.path.getsize(tmp_path)
                    if tmp_path and os.path.exists(tmp_path) else 0)
        except OSError:
            size = 0
        if size > _watch["last_size"]:
            _watch["no_growth"] = 0
        else:
            _watch["no_growth"] += 1
        _watch["last_size"] = size

        action = watchdog_action(
            proc_alive, _watch["no_growth"], RECORD_STALL_POLLS,
            now, _watch["next_retry_at"],
        )
        if action != WATCHDOG_RESTART:
            return
        if not proc_alive:
            logger.warning(
                "recording process died mid-stream (rc=%s); restarting. stderr: %s",
                proc.returncode if proc else None, _stderr_tail(_rec["stderr"]),
            )
        else:
            logger.warning(
                "recording stalled (scratch stuck at %s bytes for %s polls); "
                "restarting", size, _watch["no_growth"],
            )
        await _restart_recording(vod_id)
    except Exception:
        logger.warning("recorder watchdog pass failed", exc_info=True)


async def stop_recording():
    if not _rec["active"]:
        return
    vod_id, tmp_path = _rec["vod_id"], _rec["tmp_path"]
    started_at, proc, drain = _rec["started_at"], _rec["proc"], _rec["drain"]
    ended_at = int(time.time())
    # Mark inactive at once so clips stop and a quick re-go-live starts clean.
    _rec.update(active=False, vod_id=None, tmp_path=None, started_at=None,
                proc=None, stderr=None, drain=None)
    await _stop_recorder_process(proc, drain, graceful=True)
    # Normal end of broadcast: an expected exit, so INFO rather than WARNING.
    logger.info("recording stopped (rc=%s)", proc.returncode if proc else None)
    # Archive and finalize in the background so a slow transfer to the media
    # store (which may be a network mount) never blocks the stream watcher.
    asyncio.create_task(_finalize_recording(vod_id, tmp_path, started_at, ended_at))


async def _finalize_recording(vod_id, tmp_path, started_at, ended_at):
    # Saved and restored rather than simply cleared, because the retry loop holds
    # the flag across the whole pass and calls this for each recording in it.
    was_busy = _archiving["busy"]
    _archiving["busy"] = True
    try:
        if not (tmp_path and os.path.exists(tmp_path)
                and os.path.getsize(tmp_path) > 100_000):
            db.delete_media("vod", vod_id)
            if tmp_path and os.path.exists(tmp_path):
                os.remove(tmp_path)
            return
        filename = f"{vod_id}.mp4"
        # Park the recording before anything touches it. The pending mark is what
        # makes clear_unfinished_vods spare the row and cleanup_record_scratch
        # spare the file, so from here until the row names an archived copy a
        # crash, a restart or a raised exception costs nothing.
        db.mark_vod_pending(vod_id, tmp_path, ended_at)
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
        code, _, _ = await _run_ffmpeg(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", tmp_path,
             "-c", "copy", "-movflags", "+faststart", dst],
            timeout=600,
        )
        remuxed = code == 0 and os.path.exists(dst) and os.path.getsize(dst) > 100_000
        if not remuxed:
            logger.warning(
                "recording remux failed (rc=%s); keeping raw file for %s",
                code, dst,
            )
        # Probe whichever copy exists; the raw scratch is the same recording, so
        # the fallback path gets the same answer it did when it probed the moved
        # file instead.
        duration = (await _probe_duration(dst if remuxed else tmp_path)
                    or max(0, ended_at - started_at))
        # The row learns the filename before the scratch copy goes. Releasing the
        # scratch first left a window where a finalize that raised (a busy
        # database, a full disk) lost the recording twice over: nothing was left
        # to park, the next start dropped the unfinished row, and the orphan
        # sweep then deleted the archived file it no longer pointed at.
        db.finalize_vod(vod_id, ended_at, duration, filename)
        if remuxed:
            try:
                os.remove(tmp_path)
            except OSError:
                logger.debug("could not remove scratch recording %s", tmp_path,
                             exc_info=True)
        else:
            # Same rule for the raw-file fallback: the move is what consumes the
            # scratch, so it happens after the row can name what it produced. A
            # move that fails here is caught below and re-parks the recording.
            await asyncio.to_thread(shutil.move, tmp_path, dst)
        db.snapshot_chat("vod", vod_id, started_at, ended_at)
        logger.info("recording finalized: %s (%ss)", dst, duration)
        # Finalizing is when the media store jumps in size, so reclaim here
        # rather than waiting up to an hour for the sweep.
        await enforce_retention()
    except Exception:
        # The recording itself is fine; only the archive failed, which on a media
        # store that lives across a network mount usually means the far side is
        # unreachable. Park it rather than lose it: the row stays, the scratch
        # file stays, and retry_pending_archives picks it up later.
        logger.warning("recording finalize failed for vod_id=%s", vod_id,
                       exc_info=True)
        try:
            if tmp_path and os.path.exists(tmp_path):
                db.mark_vod_pending(vod_id, tmp_path, ended_at)
                logger.warning(
                    "recording %s kept at %s, waiting to be archived",
                    vod_id, tmp_path,
                )
        except Exception:
            logger.warning("could not park unarchived recording %s", vod_id,
                           exc_info=True)
    finally:
        _archiving["busy"] = was_busy


async def retry_pending_archives():
    """Archive again any recording whose move to the media store failed.

    Skipped while a recording is running, and while another archive pass is
    already going: this box is small, and a retry racing a live broadcast or a
    second retry for the same disk and CPU is a worse trade than waiting for the
    next hourly pass. Returns how many were retried."""
    if _rec["active"] or _archiving["busy"]:
        return 0
    _archiving["busy"] = True
    try:
        try:
            rows = db.pending_vods()
        except Exception:
            logger.warning("could not list recordings waiting to be archived",
                           exc_info=True)
            return 0
        retried = 0
        for row in rows:
            if _rec["active"]:
                # A broadcast started while this pass was running. Checking only
                # at entry meant a long pass kept competing with it; the rest
                # waits for the next hour instead.
                logger.info("a broadcast started; leaving the remaining "
                            "archives for the next pass")
                break
            path = row["pending_path"]
            if not path or not os.path.exists(path):
                # The bytes are gone, so there is nothing left to archive and the
                # row would otherwise sit pending for ever.
                logger.warning(
                    "recording %s was waiting at %s but the file is gone; "
                    "dropping it", row["id"], path,
                )
                try:
                    db.delete_media("vod", row["id"])
                except Exception:
                    logger.warning("could not drop pending VOD row %s", row["id"],
                                   exc_info=True)
                continue
            logger.info("retrying the archive of recording %s", row["id"])
            await _finalize_recording(
                row["id"], path, row["started_at"],
                row["ended_at"] or int(time.time()),
            )
            retried += 1
        return retried
    finally:
        _archiving["busy"] = False


def cooldown_for(user):
    """A user's clip cooldown in seconds (0 disables it). The host gets a
    shorter one than everybody else; moderators wait as long as viewers."""
    if user and user["is_admin"]:
        return max(0, CLIP_COOLDOWN_HOST_SECONDS)
    return max(0, CLIP_COOLDOWN_SECONDS)


def format_remaining(seconds):
    """How much cooldown is left, in whole words: '4 minutes', '30 seconds'.
    Rounded up, so it never reads 0 while there is still time to wait."""
    seconds = max(0, int(seconds))
    if seconds >= 60:
        minutes = -(-seconds // 60)          # ceiling
        return f"{minutes} minute" + ("" if minutes == 1 else "s")
    return f"{seconds} second" + ("" if seconds == 1 else "s")


def clip_window(started_at, now, clip_seconds, at=None):
    """Work out which slice of the recording a clip should contain.

    `at` is the wall-clock instant of the frame the viewer was actually looking
    at when they pressed Clip, sent by the player. Everything about clip accuracy
    lives or dies on that number, so it is worth saying why.

    Without it the server can only use "now", which is wrong three times over:
    the request arrives after the viewer has finished typing a name, the stream
    the viewer sees is some seconds behind the server, and how far behind varies
    per viewer and per moment. The old code answered that with `now - 2`, a fixed
    guess at a delay the docs themselves put at 2 to 5 seconds.

    With it, none of that estimation is needed: the player knows exactly which
    instant is on screen, because MediaMTX stamps the playlist with
    EXT-X-PROGRAM-DATE-TIME and hls.js exposes it as playingDate.

    `at` is clamped into the recording, so a bad or hostile client cannot ask for
    footage from outside it. Returns (start, end, duration) as epoch seconds, or
    (None, None, 0) if there is not enough recorded yet.

    Kept pure so the arithmetic can be tested without a stream, a recorder or a
    subprocess; the reason this drifted in the first place is that none of it was
    reachable from a test.
    """
    if at is None:
        # No usable instant from the client. Fall back to the old behaviour
        # rather than refusing: a browser without playingDate still gets a clip,
        # just a less exact one.
        end = now - CLIP_LAG
    else:
        end = at
    # Never past the live edge (a clock that is ahead would ask for footage that
    # does not exist yet) and never before the recording began.
    end = max(started_at, min(int(end), now))
    start = max(started_at, end - clip_seconds)
    duration = end - start
    if duration < 3:
        return None, None, 0
    return start, end, duration


async def make_clip(user, name, at=None, seconds=None):
    """Cut the last stretch of the live stream into a named clip. Returns
    (clip_id, None) on success or (None, error_message).

    `at` is the wall-clock instant the viewer pressed Clip, in epoch seconds.
    See clip_window() for why that matters.

    `seconds` is how much the viewer asked for, and it is taken as given: the
    route has already checked it against CLIP_LENGTHS. Asking for nothing takes
    DEFAULT_CLIP_LENGTH, which is the overlay's clip button."""
    # Refused outright during a theater session, and said plainly rather than
    # left to fail as "the stream is not live": the stream may well be live, it
    # is simply somebody else's film and nothing about it is ours to cut.
    if theater.is_active():
        return None, "Clips are off during theater."
    if not _rec["active"]:
        return None, "The stream is not live."
    username = user["username"]
    cooldown = cooldown_for(user)
    if cooldown:
        elapsed = int(time.time()) - db.last_clip_at(username)
        if elapsed < cooldown:
            return None, f"Clip cooldown: {format_remaining(cooldown - elapsed)}"
    started_at, src, vod_id = _rec["started_at"], _rec["tmp_path"], _rec["vod_id"]
    clip_seconds = int(seconds) if seconds else DEFAULT_CLIP_LENGTH
    start, end, duration = clip_window(started_at, int(time.time()), clip_seconds, at)
    if start is None:
        return None, "The stream just started; nothing to clip yet."
    # The clip is cut from the in-progress scratch file. If the recorder died or
    # stalled that file may be gone; say so in the log, not just to the client.
    if not (src and os.path.exists(src)):
        logger.warning(
            "clip failed for %s: scratch recording missing (%s)", username, src)
        return None, "Could not make the clip. Try again in a moment."
    name = (name or "").strip()[:MAX_CLIP_NAME] or "Clip"
    # Stamp what is being played onto the clip. The channel's label moves on to
    # the next game; the clip's share card should still say what this was.
    game = db.get_now_playing()
    # Create the row first so the file can be named by its id.
    clip_id = db.create_clip(
        name, "", username, vod_id, start, end, duration, int(time.time()),
        game=game,
    )
    filename = f"{clip_id}.mp4"
    dst = os.path.join(CLIP_DIR, filename)
    # Seeking with -ss on a stream copy lands on the keyframe at or before the
    # requested point, because a copy cannot cut mid-GOP; only re-encoding could,
    # and this box has one core. So the clip starts up to one keyframe interval
    # early, and since the duration is measured from where it actually started,
    # the far end would fall short by the same amount and cut off the very moment
    # the viewer pressed Clip.
    #
    # Ask for that slack back. The clip then runs slightly long instead of
    # slightly short, which is the right direction to be wrong in: an extra
    # second of lead-out is a shrug, and losing the thing you clipped is the
    # whole failure.
    code, _, err = await _run_ffmpeg(
        ["ffmpeg", "-y", "-loglevel", "error",
         "-ss", str(start - started_at), "-i", src,
         "-t", str(duration + CLIP_KEYFRAME_SLACK),
         *COPY_MAPS,
         "-c", "copy", "-movflags", "+faststart", dst],
        timeout=40,
    )
    if code != 0 or not os.path.exists(dst) or os.path.getsize(dst) < 1000:
        if not os.path.exists(dst):
            why = "output file missing"
        elif os.path.getsize(dst) < 1000:
            why = f"output too small ({os.path.getsize(dst)} bytes)"
        else:
            why = f"ffmpeg rc={code}: {_stderr_tail(err)}"
        logger.warning("clip creation failed for id=%s: %s", clip_id, why)
        db.delete_media("clip", clip_id)
        _remove_media_files(CLIP_DIR, filename, clip_id)
        return None, "Could not make the clip. Try again in a moment."
    db.set_clip_filename(clip_id, filename)
    await _make_poster(dst, os.path.join(CLIP_DIR, f"{clip_id}.jpg"), seek=1)
    db.snapshot_chat("clip", clip_id, start, end)
    logger.info(
        "clip created: id=%s name=%r by=%s (%ss)", clip_id, name, username, duration
    )
    # Announce the clip so the overlay (and any future feature) can react. Best
    # effort: a broadcast failure must never fail the clip itself.
    try:
        await hub.broadcast(
            {"type": "clip", "name": name, "by": user["display_name"]}
        )
    except Exception:
        logger.debug("clip broadcast failed", exc_info=True)
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
