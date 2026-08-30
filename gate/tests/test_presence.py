"""
Joining and leaving, as the room hears about it.

A phone that switches to another app drops its WebSocket and opens a new one on
the way back, and one person can hold several sockets at once: a second tab, or
the dashboard, which frames the watch page. Both used to produce a line in chat
every single time. What is asserted here is that the room is told about PEOPLE
arriving and leaving, not about sockets opening and closing.
"""

import asyncio
import os
import re

import pytest

import hub as hub_module
from hub import hub


class Socket:
    """A chat socket that only remembers what was sent to it."""

    def __init__(self):
        self.sent = []

    async def send_json(self, message):
        self.sent.append(message)

    def lines(self):
        return [m["text"] for m in self.sent if m.get("type") == "system"]


def who(username, name=None, owner=False):
    return {"username": username, "name": name or username.title(),
            "admin": False, "mod": False, "owner": owner}


@pytest.fixture
def grace(monkeypatch):
    """A grace short enough to wait out in a test, long enough to return inside."""
    monkeypatch.setattr(hub_module, "JOIN_GRACE_SECONDS", 0.2)
    yield 0.2


def test_a_second_tab_does_not_announce_the_same_person_twice(client, grace):
    async def run():
        watcher, first, second = Socket(), Socket(), Socket()
        await hub.join(watcher, who("watcher"))
        await hub.join(first, who("nell", "Nell"))
        await hub.join(second, who("nell", "Nell"))
        return watcher.lines()

    lines = asyncio.run(run())
    assert lines.count("Nell joined") == 1


def test_closing_one_of_two_tabs_announces_nothing(client, grace):
    # The dashboard frames the watch page, so the host alone holds two sockets.
    async def run():
        watcher, first, second = Socket(), Socket(), Socket()
        await hub.join(watcher, who("watcher"))
        await hub.join(first, who("nell", "Nell"))
        await hub.join(second, who("nell", "Nell"))
        watcher.sent.clear()
        await hub.leave(second)
        await asyncio.sleep(0.35)
        return watcher.lines()

    assert asyncio.run(run()) == []


def test_a_tab_switch_is_silent_in_both_directions(client, grace):
    # The whole point: gone and back inside the grace says nothing at all, not
    # a departure and not a return.
    async def run():
        watcher, phone = Socket(), Socket()
        await hub.join(watcher, who("watcher"))
        await hub.join(phone, who("nell", "Nell"))
        watcher.sent.clear()
        await hub.leave(phone)
        await asyncio.sleep(0.05)
        await hub.join(Socket(), who("nell", "Nell"))
        await asyncio.sleep(0.35)
        return watcher.lines()

    assert asyncio.run(run()) == []


def test_a_real_departure_is_still_announced(client, grace):
    async def run():
        watcher, phone = Socket(), Socket()
        await hub.join(watcher, who("watcher"))
        await hub.join(phone, who("nell", "Nell"))
        watcher.sent.clear()
        await hub.leave(phone)
        await asyncio.sleep(0.35)
        return watcher.lines()

    assert asyncio.run(run()) == ["Nell left"]


def test_coming_back_after_the_grace_is_a_fresh_arrival(client, grace):
    async def run():
        watcher, phone = Socket(), Socket()
        await hub.join(watcher, who("watcher"))
        await hub.join(phone, who("nell", "Nell"))
        watcher.sent.clear()
        await hub.leave(phone)
        await asyncio.sleep(0.35)
        await hub.join(Socket(), who("nell", "Nell"))
        return watcher.lines()

    assert asyncio.run(run()) == ["Nell left", "Nell joined"]


def test_flapping_holds_one_pending_departure_per_person(client, grace):
    # Each drop cancels the last one's timer rather than stacking another, so a
    # bad connection cannot pile up tasks or announce a departure several times.
    async def run():
        watcher = Socket()
        await hub.join(watcher, who("watcher"))
        for _ in range(5):
            socket = Socket()
            await hub.join(socket, who("nell", "Nell"))
            await hub.leave(socket)
            await asyncio.sleep(0.02)
        pending = len(hub._leaving)
        watcher.sent.clear()
        await asyncio.sleep(0.35)
        return pending, watcher.lines()

    pending, lines = asyncio.run(run())
    assert pending == 1
    assert lines == ["Nell left"]


def test_a_grace_of_zero_announces_the_departure_at_once(client, monkeypatch):
    # What the test suite itself runs with, and an operator can set.
    monkeypatch.setattr(hub_module, "JOIN_GRACE_SECONDS", 0)

    async def run():
        watcher, phone = Socket(), Socket()
        await hub.join(watcher, who("watcher"))
        await hub.join(phone, who("nell", "Nell"))
        watcher.sent.clear()
        await hub.leave(phone)
        return watcher.lines()

    assert asyncio.run(run()) == ["Nell left"]


# ---- what the room is told when it is emptied ------------------------------

def test_the_page_explains_every_wipe_reason_the_gate_sends():
    """The reasons live in two files and drifted apart: the page still explained
    "stream_ended" and "moderator", neither of which is sent any more, and
    neither of the two that are. A night-start wipe therefore emptied the room
    in silence, which is the one thing the explanation exists to prevent."""
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    sent = set(re.findall(
        r'wipe\(reason="([a-z_]+)"\)',
        open(os.path.join(root, "gate", "media.py"), encoding="utf-8").read(),
    ))
    assert sent == {"new_night", "idle"}
    watch_js = open(
        os.path.join(root, "web", "assets", "watch.js"), encoding="utf-8"
    ).read()
    for reason in sent:
        assert f'msg.reason === "{reason}"' in watch_js, reason


# ---- the channel owner ----
# They are in and out of their own room all evening, often only to read it, so
# their arrivals are not news. Everyone else is announced exactly as before, and
# the owner still shows up in the watching list.


def test_the_owner_arrives_without_a_word(client, grace):
    async def run():
        watcher, host = Socket(), Socket()
        await hub.join(watcher, who("watcher"))
        await hub.join(host, who("rafe", "Rafe", owner=True))
        return watcher.lines()

    # The watcher hears its own arrival; what matters is that the owner's is
    # not in there with it.
    assert [line for line in asyncio.run(run()) if "Rafe" in line] == []


def test_the_owner_leaves_without_a_word(client, grace):
    async def run():
        watcher, host = Socket(), Socket()
        await hub.join(watcher, who("watcher"))
        await hub.join(host, who("rafe", "Rafe", owner=True))
        await hub.leave(host)
        # Well past the grace, so a pending departure would have spoken by now.
        await asyncio.sleep(0.4)
        return watcher.lines()

    assert [line for line in asyncio.run(run()) if "Rafe" in line] == []


def test_a_grace_of_zero_still_says_nothing_for_the_owner(client, monkeypatch):
    # The immediate path is a separate branch from the delayed one, so it needs
    # its own guard and its own test.
    monkeypatch.setattr(hub_module, "JOIN_GRACE_SECONDS", 0)

    async def run():
        watcher, host = Socket(), Socket()
        await hub.join(watcher, who("watcher"))
        await hub.join(host, who("rafe", "Rafe", owner=True))
        await hub.leave(host)
        return watcher.lines()

    assert [line for line in asyncio.run(run()) if "Rafe" in line] == []


def test_everybody_else_is_still_announced(client, grace):
    async def run():
        watcher, host, guest = Socket(), Socket(), Socket()
        await hub.join(watcher, who("watcher"))
        await hub.join(host, who("rafe", "Rafe", owner=True))
        await hub.join(guest, who("nell", "Nell"))
        return watcher.lines()

    lines = asyncio.run(run())
    assert "Nell joined" in lines
    assert [line for line in lines if "Rafe" in line] == []


def test_the_owner_is_still_in_the_watching_list(client, grace):
    # Silent is not invisible: the point of saying nothing is that they can read
    # the room, and the room can still see they are there.
    async def run():
        host = Socket()
        await hub.join(host, who("rafe", "Rafe", owner=True))
        return hub.presence_message()

    message = asyncio.run(run())
    assert message["count"] == 1
    assert [v["username"] for v in message["viewers"]] == ["rafe"]
