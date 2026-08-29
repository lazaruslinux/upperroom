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
    """One item, with the same fields the search asks for.

    Asked for through the /Items list filtered to one id, not /Items/{id}.
    The latter is gone in Jellyfin 10.10 and later, which answer it with a bare
    400, and this is the call the play command makes: on those servers every
    play failed while search worked, because search already used this endpoint."""
    # MediaSources on top of the search fields: the play path needs the source a
    # subtitle track belongs to, and a search of 25 titles does not.
    params = {"ids": str(item_id), "Fields": SEARCH_FIELDS + ",MediaSources"}
    return f"{base}/Items?{urlencode(params)}"


def text_subtitle_index(item):
    """The index of a text subtitle track to burn, or None.

    External tracks come first: a library with Bazarr keeps its subtitles in
    .srt files beside the video, and those are the ones somebody went to the
    trouble of fetching. Image tracks (PGS, VOBSUB) are skipped entirely rather
    than preferred and then failed on, which is what burning straight from the
    video file used to do: ffmpeg took whatever subtitle stream came first, and
    on a disc rip that is a picture it cannot render."""
    text = [
        stream for stream in (item.get("MediaStreams") or [])
        if isinstance(stream, dict) and stream.get("Type") == "Subtitle"
        and stream.get("IsTextSubtitleStream")
        and isinstance(stream.get("Index"), int)
    ]
    if not text:
        return None
    external = [s for s in text if s.get("IsExternal")]
    return (external or text)[0]["Index"]


def subtitle_url(base, item_id, media_source_id, index):
    """One subtitle track as SRT, which is what the burn filter can read."""
    return (
        f"{base}/Videos/{quote(str(item_id))}/{quote(str(media_source_id))}"
        f"/Subtitles/{index}/Stream.srt"
    )


def media_source_id(item):
    """The source a track belongs to. One file per item here, so the first
    source is the one, and it falls back to the item's own id, which is what
    Jellyfin uses for a single-file item anyway."""
    sources = item.get("MediaSources") or []
    if sources and isinstance(sources[0], dict) and sources[0].get("Id"):
        return sources[0]["Id"]
    return item.get("Id")


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
        # Which track to burn and where it lives. Only the play path reads these;
        # the gate's clean_results drops them before anything reaches a browser.
        "subtitle_index": text_subtitle_index(item),
        "media_source_id": media_source_id(item),
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
    """One title's details, or None when the library has no such id."""
    async with httpx.AsyncClient(timeout=TIMEOUT) as http:
        reply = await http.get(item_url(base, item_id), headers=headers(api_key))
        reply.raise_for_status()
        found = parse_items(reply.json())
        return found[0] if found else None


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
