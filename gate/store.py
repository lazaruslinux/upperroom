"""
The gate's side of the media store.

Recordings and clips live behind a small HTTP service (store/ in this repo), on
this machine or on another one, and every file operation the gate makes on them
is one of the calls below. There is no other path: nothing in the gate opens a
recording or a clip by filename.

Every failure comes out as one StoreError, whatever its shape: the store is
down, a timeout, a refusal, a reply that is not what was asked for. A caller has
one thing to catch and decides what an outage means for its own work: park a
recording, refuse a clip, keep a row.

The timeouts are the point. The old media store was a network mount, and a
mount that died took every call with it: a stat on it blocked the event loop,
and the whole site with it. Here nothing waits longer than it has to. Connecting
gives up in seconds, a small call gives up in seconds, and only the two calls
that genuinely take a while (a whole broadcast going up, and its remux) are
allowed the time they need.
"""

import asyncio
import os
import re
from urllib.parse import quote

import httpx

from config import STORE_KEY, STORE_URL

# The names the store accepts, the same rule it applies itself.
SAFE_NAME = re.compile(r"[A-Za-z0-9_-]{1,64}\.(mp4|jpg)")
AREAS = ("vods", "clips", "shared")

_CONNECT = 5
# Deletes, links: a stat and an unlink on the far side.
QUICK = httpx.Timeout(10, connect=_CONNECT)
# A listing or the usage sums walk a directory, which on a big slow disk is not
# instant, but is still nothing like a transfer.
LISTING = httpx.Timeout(30, connect=_CONNECT)
# A multi-gigabyte recording at tens of megabytes a second takes minutes, so the
# upload has no ceiling on the whole: each write must make progress within a
# minute, and the reply (sent after the store has synced the file) gets five.
UPLOAD = httpx.Timeout(connect=_CONNECT, write=60, read=300, pool=_CONNECT)
# The store's own remux ceiling, plus room for the probe after it.
REMUX = httpx.Timeout(1260, connect=_CONNECT)
# How much of a local file is read at a time while it streams up.
CHUNK = 1024 * 1024


class StoreError(Exception):
    """The store could not do what was asked. `status` is the HTTP status when
    the store answered with a refusal, and None when it could not be reached."""

    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


def _path(area, name):
    if area not in AREAS or not SAFE_NAME.fullmatch(name or ""):
        raise StoreError(f"not a store name: {area}/{name!r}", status=404)
    return f"/{area}/{quote(name)}"


async def _request(method, path, timeout, **kwargs):
    try:
        async with httpx.AsyncClient(
            base_url=STORE_URL,
            headers={"Authorization": f"Bearer {STORE_KEY}"},
            timeout=timeout,
        ) as http:
            reply = await http.request(method, path, **kwargs)
    except httpx.HTTPError as exc:
        raise StoreError(f"{method} {path}: {exc!r}") from exc
    if reply.status_code >= 400:
        raise StoreError(f"{method} {path}: HTTP {reply.status_code}",
                         status=reply.status_code)
    try:
        return reply.json()
    except ValueError as exc:
        raise StoreError(f"{method} {path}: the reply was not JSON") from exc


async def _chunks(local_path):
    """A local file as an async stream, read off the event loop a piece at a
    time so a whole broadcast never sits in memory."""
    handle = await asyncio.to_thread(open, local_path, "rb")
    try:
        while True:
            chunk = await asyncio.to_thread(handle.read, CHUNK)
            if not chunk:
                return
            yield chunk
    finally:
        handle.close()


async def put_file(area, name, local_path):
    """Upload one local file as area/name. Returns its size in bytes."""
    path = _path(area, name)
    size = await asyncio.to_thread(os.path.getsize, local_path)
    reply = await _request(
        "PUT", path, UPLOAD, content=_chunks(local_path),
        headers={"Content-Length": str(size)},
    )
    # The store counts what actually arrived. Anything else means the file
    # changed under the upload or the body was cut, and neither is a copy.
    if not isinstance(reply, dict) or reply.get("size") != size:
        raise StoreError(f"PUT {path}: stored {reply!r}, sent {size} bytes")
    return size


async def finalize_vod(name):
    """Have the store remux an uploaded recording in place. Returns
    {"remuxed": bool, "duration": seconds or None, "size": bytes}."""
    path = _path("vods", name) + "/finalize"
    reply = await _request("POST", path, REMUX)
    try:
        return {
            "remuxed": bool(reply["remuxed"]),
            "duration": int(reply["duration"]) if reply["duration"] else None,
            "size": int(reply["size"]),
        }
    except (KeyError, TypeError, ValueError) as exc:
        raise StoreError(f"POST {path}: unexpected reply {reply!r}") from exc


async def link(src, dst):
    """Give clips/<src> a second name at shared/<dst>. A StoreError with status
    404 means the clip's file is not there."""
    _path("clips", src)
    _path("shared", dst)
    await _request("POST", "/link", QUICK, json={"src": src, "dst": dst})


async def delete(area, name):
    """Remove one name. True if it was there, False if it was not; both are an
    answer, and only a StoreError means the store could not be asked.

    A name the store cannot hold is answered False without asking. Anything the
    store itself says other than {"existed": ...} is an error, a 404 included:
    the real store never 404s a delete of a good name, so one means the URL is
    pointing at something else, and treating it as "gone" would drop rows
    whose files are still there."""
    if not SAFE_NAME.fullmatch(name or ""):
        return False
    path = _path(area, name)
    reply = await _request("DELETE", path, QUICK)
    if not isinstance(reply, dict) or "existed" not in reply:
        raise StoreError(f"DELETE {path}: unexpected reply {reply!r}")
    return bool(reply["existed"])


async def usage():
    """Bytes per area and the store's disk, as the store reports them."""
    reply = await _request("GET", "/usage", LISTING)
    keys = ("vods_bytes", "clips_bytes", "shared_bytes", "free_bytes", "total_bytes")
    try:
        return {key: int(reply[key]) for key in keys}
    except (KeyError, TypeError, ValueError) as exc:
        raise StoreError(f"GET /usage: unexpected reply {reply!r}") from exc


async def list_area(area):
    """Every file in one area, as [{"name", "size", "mtime"}]."""
    if area not in AREAS:
        raise StoreError(f"not a store area: {area!r}", status=404)
    reply = await _request("GET", f"/list/{area}", LISTING)
    try:
        return [
            {"name": str(e["name"]), "size": int(e["size"]), "mtime": int(e["mtime"])}
            for e in reply
        ]
    except (KeyError, TypeError, ValueError) as exc:
        raise StoreError(f"GET /list/{area}: unexpected reply") from exc
