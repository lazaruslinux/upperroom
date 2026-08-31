"""
Who is pulling the video right now.

The chat hub knows who is in the room; this knows who is actually fetching
segments, which is a different question and the one bandwidth is made of. Every
viewer takes a full copy of the broadcast from this server, so the viewer count
is the bill. Caddy already asks the gate to authorize each segment, so that call
is the only place this needs to be fed from.

Deliberately in memory and deliberately approximate: a restart forgets who was
watching, which costs one window of over-admission and nothing else.
"""

import threading
import time

from config import WATCHER_WINDOW_SECONDS

# username -> the last time they asked for a segment.
_seen = {}

# Every entry point here reads and then writes _seen, and they are called from
# FastAPI's threadpool: /api/verify is a sync def, so a room of viewers pulling
# segments runs these genuinely in parallel. Without the lock, _prune's delete
# loop and a concurrent note() raced on the same dict, and the loser got a
# RuntimeError or a KeyError out of an authorization check that Caddy turns into
# a 500 and a stalled video. One lock around the whole read-modify, because the
# work under it is a walk of a dict with one entry per viewer.
_lock = threading.Lock()


def _prune(now):
    """Drop everyone past the window. The caller holds _lock."""
    for name in [n for n, at in _seen.items() if now - at > WATCHER_WINDOW_SECONDS]:
        del _seen[name]


def note(username, now=None):
    """Record that this person just took a segment."""
    now = time.time() if now is None else now
    with _lock:
        _prune(now)
        _seen[username] = now


def active(username, now=None):
    """Whether this person is already counted, so admitting them again costs
    nothing. Someone mid-watch must never be turned away by a limit they were
    admitted under."""
    now = time.time() if now is None else now
    with _lock:
        _prune(now)
        return username in _seen


def count(now=None):
    """How many distinct people are watching."""
    now = time.time() if now is None else now
    with _lock:
        _prune(now)
        return len(_seen)


def reset():
    """Forget everyone. For tests, and for nothing else."""
    with _lock:
        _seen.clear()
