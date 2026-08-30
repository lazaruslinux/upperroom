"""
Demo mode: a few built-in titles and no library at all.

Enough for the demo stack and for trying the theater locally: search finds them,
each has a poster and a synopsis, and playing one publishes a generated picture
and tone with its name on screen. There is one show among the films, with two
short seasons, so the episode picker has something to pick. Nothing here reads a
disk or a network.
"""

import asyncio
import logging

logger = logging.getLogger("projector.demo")

# A show and its episodes. The series row is not playable, exactly as a real
# one is not: picking it asks for the episodes below.
DEMO_SERIES = {
    "jf_id": "demo-show",
    "kind": "series",
    "title": "The Standing Stones",
    "year": 2020,
    "runtime_min": None,
    "synopsis": "A generated demo show, so the episode picker has seasons to "
                "open. Every episode is two minutes of test picture and tone.",
    "has_subtitles": False,
    "color": "0x33283a",
}

DEMO_EPISODES = [
    {
        "jf_id": f"demo-s{season}e{number}",
        "kind": "episode",
        "title": name,
        "series": DEMO_SERIES["title"],
        "series_id": DEMO_SERIES["jf_id"],
        "series_year": DEMO_SERIES["year"],
        "season": season,
        "episode": number,
        "year": DEMO_SERIES["year"] + season - 1,
        "runtime_min": 2,
        "synopsis": f"Season {season}, episode {number} of the demo show.",
        "has_subtitles": False,
        "color": "0x33283a",
    }
    for season, names in (
        (1, ("The Field", "The Ford", "The Fold")),
        (2, ("The Rise", "The Return")),
    )
    for number, name in enumerate(names, start=1)
]

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
    """One catalog entry as the gate's search reply shape (no internal fields).

    A film with no kind of its own is a film, which keeps the three original
    entries as they were written."""
    out = {k: v for k, v in item.items() if k != "color"}
    out.setdefault("kind", "movie")
    return out


def find(jf_id):
    for item in CATALOG + [DEMO_SERIES] + DEMO_EPISODES:
        if item["jf_id"] == jf_id:
            return item
    return None


def search(query):
    """Substring match on the title, case-insensitive. A blank query lists
    everything, which is what makes the demo dashboard show something
    immediately. Episodes are not searched: a show is found by its own name and
    its episodes are asked for after that, the same as a real library."""
    rows = CATALOG + [DEMO_SERIES]
    text = (query or "").strip().lower()
    if not text:
        return [public(i) for i in rows]
    return [public(i) for i in rows if text in i["title"].lower()]


def episodes(series_id):
    """Every episode of the demo show, or nothing for anything else."""
    if series_id != DEMO_SERIES["jf_id"]:
        return []
    return [public(e) for e in DEMO_EPISODES]


async def art(jf_id):
    """A flat colored poster for a demo title, generated with ffmpeg so this
    costs no image library. Returns JPEG bytes, or None. An episode borrows its
    show's color, the way a real one borrows the series poster."""
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
