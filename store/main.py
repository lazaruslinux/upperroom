"""
upperroom media store.

Recordings and clips live here, behind a small HTTP API, and nowhere else. The
gate uploads a finished recording or clip, asks for it to be remuxed, links a
published clip into the public area, and deletes what retention lets go. Caddy
reads files back out for viewers, with range requests so a video can seek.

It replaces a shared folder that used to be a network mount. When that mount
died, everything that touched it hung or lied: a stat on a dead mount blocks,
and a missing-file check on one says "gone" when it means "unreachable". Over
HTTP a dead store is a refused connection or a timeout, and the gate can tell
that apart from an answer.

Two keys, two powers. The read key fetches files and nothing else; it is the
one Caddy holds, because Caddy only ever serves. The write key does everything,
and only the gate holds it. Neither is ever a viewer's: the store never sees a
session cookie, and it has no idea who is watching.

One port, no directory listings over GET, and names restricted to what the gate
itself writes, so nothing outside the three areas below can be named at all.
"""

import asyncio
import hmac
import logging
import os
import re
import secrets
import stat

from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from starlette.requests import ClientDisconnect

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("upperroom.store")

STORE_DIR = os.environ.get("STORE_DIR", "/media")
READ_KEY = os.environ.get("STORE_READ_KEY", "")
WRITE_KEY = os.environ.get("STORE_WRITE_KEY", "")
# A whole broadcast arrives as one upload, so the cap is sized for a long night
# rather than for a clip. It exists so a runaway or hostile writer cannot fill
# the disk in one request, not to police ordinary recordings.
MAX_UPLOAD_BYTES = int(os.environ.get("STORE_MAX_UPLOAD_BYTES", str(32 * 1024 ** 3)))

MIN_KEY_LENGTH = 32
AREAS = ("vods", "clips", "shared")
# The only names that exist. Every file the gate writes is named by a row id or
# a share token, so a name with a slash, a dot in front, or any other extension
# is not a file here, it is somebody trying one.
SAFE_NAME = re.compile(r"[A-Za-z0-9_-]{1,64}\.(mp4|jpg)")
TYPES = {"mp4": "video/mp4", "jpg": "image/jpeg"}

# Remuxing a long broadcast is a stream copy of every byte of it. Twenty
# minutes is far more than a night's recording needs on a slow disk, and it is
# a ceiling, not a guess: a remux that has not finished by then is stuck.
REMUX_TIMEOUT = 1200
PROBE_TIMEOUT = 60
# Uploads are written in pieces this size, each off the event loop.
WRITE_CHUNK = 1024 * 1024


def config_problem(read_key, write_key):
    """What is wrong with the two keys, or None. A short or missing key is
    refused outright rather than run with, because this service holds the whole
    archive and the key is the only thing in front of it."""
    for name, value in (("STORE_READ_KEY", read_key), ("STORE_WRITE_KEY", write_key)):
        if len(value) < MIN_KEY_LENGTH:
            return f"{name} must be set to at least {MIN_KEY_LENGTH} characters"
    # The same value in both would hand Caddy, which only ever serves, the power
    # to write and delete.
    if hmac.compare_digest(read_key.encode(), write_key.encode()):
        return "STORE_READ_KEY and STORE_WRITE_KEY must be different"
    return None


def prepare_dirs():
    """Create the three areas, and clear the temp files an interrupted upload
    or remux left behind. Run at startup, when nothing can be in flight."""
    for area in AREAS:
        folder = os.path.join(STORE_DIR, area)
        os.makedirs(folder, exist_ok=True)
        for entry in os.scandir(folder):
            if entry.name.startswith(".") and entry.is_file(follow_symlinks=False):
                try:
                    os.remove(entry.path)
                    logger.info("removed an unfinished upload: %s", entry.path)
                except OSError:
                    logger.warning("could not remove %s", entry.path, exc_info=True)


_problem = config_problem(READ_KEY, WRITE_KEY)
if _problem:
    raise SystemExit(f"store refuses to start: {_problem}")
prepare_dirs()

app = FastAPI(title="upperroom store", docs_url=None, redoc_url=None, openapi_url=None)


def _allowed(request, write):
    """Whether this request carries a key good enough for what it asks. Compared
    as bytes in constant time, so the answer takes as long for a key that is
    nearly right as for one that is nothing like it."""
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        return False
    token = token.strip().encode()
    if hmac.compare_digest(token, WRITE_KEY.encode()):
        return True
    return not write and hmac.compare_digest(token, READ_KEY.encode())


def _refused():
    return Response(status_code=401)


def _missing():
    return Response(status_code=404)


def _path(area, name):
    """The one way a request becomes a path: STORE_DIR/area/name, for a known
    area and a name of the one allowed shape. None for anything else."""
    if area not in AREAS or not SAFE_NAME.fullmatch(name or ""):
        return None
    return os.path.join(STORE_DIR, area, name)


def _temp_beside(path):
    """A temp name in the same directory, so the final rename is atomic, and
    dot-prefixed, so no request can ever name it."""
    return os.path.join(os.path.dirname(path), f".{secrets.token_hex(8)}.part")


def _discard(path):
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
    except OSError:
        logger.warning("could not remove %s", path, exc_info=True)


def _finish(handle):
    handle.flush()
    os.fsync(handle.fileno())
    handle.close()


async def _run(args, timeout):
    """Run ffmpeg or ffprobe to completion off the event loop. Returns
    (returncode, stdout); returncode is None on a timeout or a failed start."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError:
        logger.warning("could not start %s", args[0], exc_info=True)
        return None, b""
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        logger.warning("%s timed out after %ss", args[0], timeout)
        return None, b""
    if proc.returncode != 0:
        tail = (err or b"").decode("utf-8", "replace").strip().splitlines()
        logger.warning("%s failed (rc=%s): %s", args[0], proc.returncode,
                       tail[-1][-500:] if tail else "")
    return proc.returncode, out or b""


@app.get("/health")
def health():
    # Unauthenticated so a container healthcheck needs no key, and so it says
    # nothing: not the version, not the disk, not whether anything is stored.
    return {"ok": True}


@app.get("/usage")
def usage(request: Request):
    """Bytes in each area plus the filesystem's free and total space, for the
    dashboard's Storage panel and the size cap."""
    if not _allowed(request, write=True):
        return _refused()
    out = {}
    for area in AREAS:
        total = 0
        with os.scandir(os.path.join(STORE_DIR, area)) as entries:
            for entry in entries:
                try:
                    if entry.is_file(follow_symlinks=False):
                        total += entry.stat(follow_symlinks=False).st_size
                except OSError:
                    continue
        out[f"{area}_bytes"] = total
    fs = os.statvfs(STORE_DIR)
    out["free_bytes"] = fs.f_bavail * fs.f_frsize
    out["total_bytes"] = fs.f_blocks * fs.f_frsize
    return out


@app.get("/list/{area}")
def list_area(area: str, request: Request):
    """Every file in one area, for the gate's orphan sweeps. Write key only:
    the public area's names are its tokens, so a listing of it is a list of
    every public link."""
    if not _allowed(request, write=True):
        return _refused()
    if area not in AREAS:
        return _missing()
    files = []
    with os.scandir(os.path.join(STORE_DIR, area)) as entries:
        for entry in entries:
            if not SAFE_NAME.fullmatch(entry.name):
                continue
            try:
                info = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            if stat.S_ISREG(info.st_mode):
                files.append({"name": entry.name, "size": info.st_size,
                              "mtime": int(info.st_mtime)})
    return files


@app.post("/link")
async def link(request: Request):
    """Publish a clip by giving it a second name in the public area. A hard
    link is the same bytes under another name, so it costs no space and can
    never drift from the original; the bytes go when the last name does."""
    if not _allowed(request, write=True):
        return _refused()
    try:
        body = await request.json()
        src, dst = str(body["src"]), str(body["dst"])
    except (ValueError, KeyError, TypeError):
        return JSONResponse({"error": "Send src and dst."}, status_code=400)
    source, target = _path("clips", src), _path("shared", dst)
    if not source or not target:
        return _missing()
    # A video published under a picture's name would be served as a picture.
    if os.path.splitext(src)[1] != os.path.splitext(dst)[1]:
        return JSONResponse({"error": "src and dst must be the same kind."},
                            status_code=400)
    if not os.path.isfile(source):
        return _missing()
    try:
        os.link(source, target)
    except FileExistsError:
        # Linking again is a no-op, so a retry after a lost reply is safe. A
        # different file already under that name is refused, never replaced.
        if not os.path.samefile(source, target):
            return JSONResponse({"error": "That name is taken."}, status_code=409)
    return {"linked": True}


@app.post("/vods/{name}/finalize")
async def finalize(name: str, request: Request):
    """Remux an uploaded recording in place into a regular, faststart MP4.

    The live recording is a fragmented MP4 (empty moov plus keyframe fragments)
    so it survives an abrupt stop and can be clipped while it is still being
    written, but fragmented files load slowly and break some mobile players. A
    stream copy with +faststart rewrites it as one moov-at-the-front file that
    seeks and plays everywhere. No re-encode, so it stays quick. If the remux
    fails the original is kept: a recording that plays badly beats one that is
    gone. Doing it here rather than on the gate is what spares the gate from
    needing twice the recording's size in free space."""
    if not _allowed(request, write=True):
        return _refused()
    path = _path("vods", name)
    if not path or not name.endswith(".mp4") or not os.path.isfile(path):
        return _missing()
    temp = _temp_beside(path)
    code, _ = await _run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", path,
         "-c", "copy", "-movflags", "+faststart", "-f", "mp4", temp],
        REMUX_TIMEOUT,
    )
    remuxed = code == 0 and os.path.isfile(temp) and os.path.getsize(temp) > 0
    if remuxed:
        os.replace(temp, path)
    else:
        _discard(temp)
        logger.warning("remux of %s failed; keeping the recording as uploaded", name)
    code, out = await _run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", path],
        PROBE_TIMEOUT,
    )
    try:
        duration = int(float(out.decode().strip())) if code == 0 else None
    except ValueError:
        duration = None
    return {"remuxed": remuxed, "duration": duration, "size": os.path.getsize(path)}


@app.api_route("/{area}/{name}", methods=["GET", "HEAD"])
def get_file(area: str, name: str, request: Request):
    """One file, with range support so a browser can seek a video through the
    proxy. Starlette's FileResponse answers Range itself: a single range as a
    206 with Content-Range, several as multipart, and Accept-Ranges always."""
    if not _allowed(request, write=False):
        return _refused()
    path = _path(area, name)
    if not path:
        return _missing()
    try:
        info = os.stat(path)
    except OSError:
        return _missing()
    if not stat.S_ISREG(info.st_mode):
        return _missing()
    return FileResponse(path, media_type=TYPES[name.rsplit(".", 1)[1]],
                        stat_result=info)


class _TooLarge(Exception):
    pass


@app.put("/{area}/{name}")
async def put_file(area: str, name: str, request: Request):
    """Store one file. The body is streamed to a temp file beside the target,
    synced, and renamed into place, so a reader sees the old file or the whole
    new one and never half of either, and an upload cut off halfway leaves
    nothing behind but a temp file the next start clears."""
    if not _allowed(request, write=True):
        return _refused()
    path = _path(area, name)
    if not path:
        return _missing()
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_UPLOAD_BYTES:
        return Response(status_code=413)
    temp = _temp_beside(path)
    handle = await asyncio.to_thread(open, temp, "wb")
    size = 0
    pending = bytearray()
    try:
        async for chunk in request.stream():
            size += len(chunk)
            # Counted as it arrives, not trusted from the header: a chunked
            # body has no length to declare.
            if size > MAX_UPLOAD_BYTES:
                raise _TooLarge()
            pending += chunk
            if len(pending) >= WRITE_CHUNK:
                await asyncio.to_thread(handle.write, pending)
                pending = bytearray()
        if pending:
            await asyncio.to_thread(handle.write, pending)
        await asyncio.to_thread(_finish, handle)
        await asyncio.to_thread(os.replace, temp, path)
    except _TooLarge:
        handle.close()
        _discard(temp)
        return Response(status_code=413)
    except ClientDisconnect:
        handle.close()
        _discard(temp)
        logger.warning("upload of %s/%s was cut off after %s bytes", area, name, size)
        return Response(status_code=400)
    except BaseException:
        handle.close()
        _discard(temp)
        raise
    return JSONResponse({"size": size}, status_code=201)


@app.delete("/{area}/{name}")
def delete_file(area: str, name: str, request: Request):
    """Remove one name. Idempotent, and it says whether there was anything to
    remove, because to the gate both are an answer: only a failure to ask at
    all means a row has to wait."""
    if not _allowed(request, write=True):
        return _refused()
    path = _path(area, name)
    if not path:
        return _missing()
    try:
        os.remove(path)
    except FileNotFoundError:
        return {"existed": False}
    return {"existed": True}
