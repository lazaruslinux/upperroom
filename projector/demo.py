"""
Demo mode: three built-in titles and no library at all.

Enough for the demo stack and for trying the theater locally: search finds them,
each has a poster and a synopsis, and playing one publishes a generated picture
and tone with its name on screen. Nothing here reads a disk or a network.
"""

import asyncio
import logging

logger = logging.getLogger("projector.demo")

CATALOG = [
    {
        "jf_id": "demo-one",
        "title": "The Long Afternoon",
        "year": 2019,
        "runtime_min": 2,
        "synopsis": "A generated demo title. Two minutes of test picture and a "
                    "steady tone, so you can watch a theater session start, play "
                    "and return to intermission without a media library.",
        "has_subtitles": False,
        "color": "0x243b32",
    },
    {
        "jf_id": "demo-two",
        "title": "Harbour Lights",
        "year": 2021,
        "runtime_min": 2,
        "synopsis": "A second generated demo title, so the search returns more "
                    "than one row and the dashboard's result list is worth "
                    "looking at.",
        "has_subtitles": False,
        "color": "0x2b3448",
    },
    {
        "jf_id": "demo-three",
        "title": "Field Recordings",
        "year": 2023,
        "runtime_min": 2,
        "synopsis": "A third generated demo title. Play it after another one to "
                    "see the Now Showing card change without the session ending.",
        "has_subtitles": False,
        "color": "0x3a2f28",
    },
]


def public(item):
    """One catalog entry as the gate's search reply shape (no internal fields)."""
    return {k: v for k, v in item.items() if k != "color"}


def find(jf_id):
    for item in CATALOG:
        if item["jf_id"] == jf_id:
            return item
    return None


def search(query):
    """Substring match on the title, case-insensitive. A blank query lists all
    three, which is what makes the demo dashboard show something immediately."""
    text = (query or "").strip().lower()
    if not text:
        return [public(i) for i in CATALOG]
    return [public(i) for i in CATALOG if text in i["title"].lower()]


async def art(jf_id):
    """A flat colored poster for a demo title, generated with ffmpeg so this
    costs no image library. Returns JPEG bytes, or None."""
    item = find(jf_id)
    if not item:
        return None
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin",
            "-f", "lavfi", "-i", f"color=c={item['color']}:s=400x600",
            "-frames:v", "1", "-f", "image2", "-c:v", "mjpeg", "-",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
        return out or None
    except Exception:
        logger.info("could not generate demo art for %s", jf_id, exc_info=True)
        return None
