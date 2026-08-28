"""
The ffmpeg side of the projector.

One process at a time, publishing to the gate's ingest exactly as OBS would. The
argv is built by a pure function so the encoder settings can be asserted without
running anything: getting these wrong is not a crash, it is a broadcast that
plays badly for everyone and looks fine from here.
"""

import asyncio
import logging
import signal

logger = logging.getLogger("projector.player")

# A font for the demo cards. Named explicitly rather than left to fontconfig,
# which a slim base image may not have anything registered with.
DEMO_FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
# How long the built-in demo titles run before ending on their own, which is
# also a free demonstration of the return to intermission.
DEMO_SECONDS = 120

# ffmpeg exits this fast only when it never started: a bad filter, a missing
# input. Past it, an exit is the title ending or the stream being stopped.
EARLY_EXIT_SECONDS = 10


def escape_filter_value(text):
    """Escape a string for use inside a filter argument.

    Backslashes first (or the escapes below get escaped again), then the
    characters that end an argument or a filter."""
    out = str(text).replace("\\", "\\\\")
    for char in ("'", ":", ",", "[", "]", ";"):
        out = out.replace(char, "\\" + char)
    return out


def bufsize_for(bitrate):
    """Twice the bitrate, keeping its unit suffix. Anything unparseable falls
    back to the bitrate itself, which is tight but never invalid."""
    text = str(bitrate).strip()
    suffix = ""
    if text and text[-1] in "kKmM":
        suffix = text[-1]
        text = text[:-1]
    try:
        return f"{int(float(text)) * 2}{suffix}"
    except ValueError:
        return str(bitrate)


def output_url(opts):
    """The ingest URL with the channel's stream key appended, in the same
    `live?pass=` form OBS uses (see docs/03-obs.md)."""
    ingest = opts.get("ingest_url") or ""
    key = opts.get("stream_key") or ""
    return f"{ingest}?pass={key}" if key else ingest


def video_filters(opts):
    """The video filter chain, as a list of filters in order.

    Scale first, then burn subtitles, so the text is rendered at the size it
    will be seen at rather than scaled afterwards and smeared. The VAAPI upload
    goes last: the scale and the burn stay on the CPU (they are cheap), and only
    the encode is handed to the GPU (it is not)."""
    filters = []
    if opts.get("demo_title"):
        filters.append(
            f"drawtext=fontfile={DEMO_FONT}:"
            f"text='{escape_filter_value(opts['demo_title'])}':"
            "fontsize=64:fontcolor=white:box=1:boxcolor=black@0.6:boxborderw=16:"
            "x=(w-text_w)/2:y=(h-text_h)/2"
        )
    height = opts.get("max_height") or 1080
    filters.append(f"scale=-2:'min({int(height)},ih)'")
    if opts.get("subtitles") and opts.get("subtitle_source"):
        filters.append(
            f"subtitles='{escape_filter_value(opts['subtitle_source'])}'"
        )
    if opts.get("vaapi_device"):
        filters += ["format=nv12", "hwupload"]
    return filters


def build_play_args(item_url, opts):
    """The full ffmpeg argv to publish one title (pure).

    item_url is the library's file URL, or None in demo mode, where the picture
    and sound are generated locally instead (opts["demo_title"] names the card).

    -re is what makes this a broadcast rather than a transfer: without it ffmpeg
    reads the file as fast as it can and the ingest is handed an hour of video in
    a minute. -bf 0 is not optional either: this stack's HLS muxer breaks on
    B-frame reordering."""
    args = ["ffmpeg", "-hide_banner", "-loglevel", "warning", "-nostdin", "-y"]
    if opts.get("vaapi_device"):
        args += ["-vaapi_device", opts["vaapi_device"]]

    if item_url:
        args += [
            # A film is a long read over somebody's LAN; let ffmpeg re-open the
            # connection rather than end the showing on one dropped socket.
            "-reconnect", "1", "-reconnect_streamed", "1",
            "-reconnect_delay_max", "5",
            "-re", "-i", item_url,
            "-map", "0:v:0", "-map", "0:a:0?",
        ]
    else:
        args += [
            "-re", "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30",
            "-f", "lavfi", "-i", "sine=frequency=220:sample_rate=48000",
            "-filter:a", "volume=0.08",
            "-map", "0:v", "-map", "1:a",
            "-t", str(DEMO_SECONDS),
        ]

    bitrate = opts.get("bitrate") or "6000k"
    args += ["-filter:v", ",".join(video_filters(opts))]
    if opts.get("vaapi_device"):
        args += ["-c:v", "h264_vaapi"]
    else:
        args += ["-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p"]
    args += [
        "-b:v", bitrate, "-maxrate", bitrate, "-bufsize", bufsize_for(bitrate),
        # A keyframe every two seconds, expressed in time rather than frames so
        # it holds whatever the source's frame rate turns out to be.
        "-force_key_frames", "expr:gte(t,n_forced*2)",
        "-bf", "0",
        "-c:a", "aac", "-b:a", "160k", "-ac", "2", "-ar", "48000",
        "-f", "flv", output_url(opts),
    ]
    return args


def is_subtitle_failure(stderr_text):
    """Whether ffmpeg's output says the subtitle burn is what failed, so the
    same title can be retried without it instead of not playing at all."""
    text = (stderr_text or "").lower()
    return "subtitles" in text and (
        "error" in text or "failed" in text or "invalid" in text
        or "no such" in text or "unable" in text
    )


class Player:
    """The single ffmpeg publish, and what is known about it."""

    def __init__(self):
        self._proc = None
        self._stderr = bytearray()
        self._drain = None

    def running(self):
        return self._proc is not None and self._proc.returncode is None

    def stderr_text(self):
        return bytes(self._stderr).decode("utf-8", "replace")

    async def start(self, args):
        """Start one publish. Any previous one is stopped first."""
        await self.stop()
        self._stderr = bytearray()
        # The argv holds the library's API key; log the shape, never the string.
        logger.info("starting ffmpeg (%d args)", len(args))
        self._proc = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        self._drain = asyncio.create_task(self._drain_stderr(self._proc, self._stderr))
        return self._proc

    async def _drain_stderr(self, proc, buf, tail_max=4096):
        """Keep ffmpeg's stderr read into a bounded tail, so a full pipe can
        never stall it and the last output survives for the error detail."""
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
            logger.debug("stderr drain ended", exc_info=True)

    async def wait(self):
        """Wait for the current publish to end and return its exit code."""
        proc = self._proc
        if proc is None:
            return None
        return await proc.wait()

    async def stop(self):
        """Stop the publish. SIGINT first so ffmpeg closes the connection
        cleanly, then a kill if it will not go."""
        proc, drain = self._proc, self._drain
        self._proc, self._drain = None, None
        if proc is not None and proc.returncode is None:
            try:
                proc.send_signal(signal.SIGINT)
                await asyncio.wait_for(proc.wait(), timeout=10)
            except Exception:
                logger.warning("ffmpeg did not stop cleanly; killing it")
                try:
                    proc.kill()
                    await proc.wait()
                except Exception:
                    logger.debug("could not kill ffmpeg", exc_info=True)
        if drain:
            try:
                await asyncio.wait_for(asyncio.shield(drain), timeout=2)
            except Exception:
                drain.cancel()
