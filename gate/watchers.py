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

import time

from config import WATCHER_WINDOW_SECONDS

# username -> the last time they asked for a segment.
_seen = {}


def _prune(now):
    for name in [n for n, at in _seen.items() if now - at > WATCHER_WINDOW_SECONDS]:
        del _seen[name]


def note(username, now=None):
    """Record that this person just took a segment."""
    now = time.time() if now is None else now
    _prune(now)
    _seen[username] = now


def active(username, now=None):
    """Whether this person is already counted, so admitting them again costs
    nothing. Someone mid-watch must never be turned away by a limit they were
    admitted under."""
    now = time.time() if now is None else now
    _prune(now)
    return username in _seen


def count(now=None):
    """How many distinct people are watching."""
    now = time.time() if now is None else now
    _prune(now)
    return len(_seen)


def reset():
    """Forget everyone. For tests, and for nothing else."""
    _seen.clear()
