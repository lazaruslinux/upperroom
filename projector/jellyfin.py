"""
Reading a Jellyfin library.

Only what the projector needs: find movies by name, read one item's details,
fetch its poster, and build the URL ffmpeg reads the file from. The API key is
passed in and never logged; the URL builders are pure so they can be asserted
without a server.
"""

import logging
from urllib.parse import quote, urlencode

import httpx

logger = logging.getLogger("projector.jellyfin")

TIMEOUT = 15
# The fields the search needs beyond the defaults: the synopsis, the runtime,
# and enough of the media streams to say whether a title has subtitles.
SEARCH_FIELDS = "Overview,MediaStreams"


def headers(api_key):
    """The auth header for the API. Jellyfin accepts the key on its own here."""
    return {"X-Emby-Token": api_key}


def search_url(base, query, limit=25):
    """The /Items search for movies matching `query`."""
    params = {
        "searchTerm": query,
        "IncludeItemTypes": "Movie",
        "Recursive": "true",
        "Limit": str(limit),
        "Fields": SEARCH_FIELDS,
    }
    return f"{base}/Items?{urlencode(params)}"


def item_url(base, item_id):
    """One item, with the same fields the search asks for."""
    return f"{base}/Items/{quote(str(item_id))}?{urlencode({'Fields': SEARCH_FIELDS})}"


def image_url(base, item_id, max_height=900):
    """The item's primary poster image."""
    params = {"maxHeight": str(max_height), "quality": "85"}
    return f"{base}/Items/{quote(str(item_id))}/Images/Primary?{urlencode(params)}"


def file_url(base, item_id, api_key):
    """The URL ffmpeg reads the title from.

    The key rides in the query string rather than a header because this string
    is handed to ffmpeg as an input, and ffmpeg's own header plumbing differs by
    protocol. It never reaches a log: the player logs the method and the item,
    never the argv."""
    return (
        f"{base}/Items/{quote(str(item_id))}/File?"
        f"{urlencode({'api_key': api_key})}"
    )


def ticks_to_minutes(ticks):
    """Jellyfin reports runtime in 100-nanosecond ticks. None stays None."""
    if not isinstance(ticks, (int, float)) or ticks <= 0:
        return None
    return int(ticks / 10_000_000 / 60)


def has_subtitles(item):
    """Whether the item carries at least one subtitle track."""
    for stream in item.get("MediaStreams") or []:
        if isinstance(stream, dict) and stream.get("Type") == "Subtitle":
            return True
    return False


def to_result(item):
    """One library item as the gate's search reply shape."""
    return {
        "jf_id": str(item.get("Id") or ""),
        "title": str(item.get("Name") or "Untitled"),
        "year": item.get("ProductionYear")
        if isinstance(item.get("ProductionYear"), int) else None,
        "runtime_min": ticks_to_minutes(item.get("RunTimeTicks")),
        "synopsis": str(item.get("Overview") or ""),
        "has_subtitles": has_subtitles(item),
    }


def parse_items(payload):
    """The results out of a /Items reply, ignoring anything without an id."""
    items = (payload or {}).get("Items") or []
    return [to_result(i) for i in items if isinstance(i, dict) and i.get("Id")]


async def search(base, api_key, query, limit=25):
    async with httpx.AsyncClient(timeout=TIMEOUT) as http:
        reply = await http.get(
            search_url(base, query, limit), headers=headers(api_key)
        )
        reply.raise_for_status()
        return parse_items(reply.json())


async def item(base, api_key, item_id):
    async with httpx.AsyncClient(timeout=TIMEOUT) as http:
        reply = await http.get(item_url(base, item_id), headers=headers(api_key))
        reply.raise_for_status()
        return to_result(reply.json())


async def art(base, api_key, item_id, max_height=900):
    """The poster's bytes, or None when the item has no image."""
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as http:
            reply = await http.get(
                image_url(base, item_id, max_height), headers=headers(api_key)
            )
            if reply.status_code != 200:
                return None
            return reply.content
    except httpx.HTTPError as exc:
        logger.info("no poster for %s: %r", item_id, exc)
        return None
