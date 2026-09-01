"""
The request/reply envelope and the demo catalog. Both ends have to agree on the
wire format exactly, and it is small enough that the agreement can just be
asserted here.
"""

import asyncio
import json

import demo
import main


def test_a_reply_carries_the_request_id_and_a_result():
    assert json.loads(main.reply(7, {"ok": True})) == {"id": 7, "result": {"ok": True}}


def test_an_error_carries_the_request_id_and_a_string():
    assert json.loads(main.error(7, ValueError("nope"))) == {"id": 7, "error": "nope"}


def test_an_event_has_no_id_so_it_is_never_read_as_a_reply():
    frame = json.loads(main.event("status", state="playing", position_s=15))
    assert frame == {"event": "status", "state": "playing", "position_s": 15}
    assert "id" not in frame


def test_the_key_rides_the_query_string_of_the_gate_url():
    main.config.GATE_URL = "wss://example.test/ws/projector"
    main.config.KEY = "a-key"
    assert main.gate_url() == "wss://example.test/ws/projector?key=a-key"
    main.config.GATE_URL = "wss://example.test/ws/projector?x=1"
    assert main.gate_url() == "wss://example.test/ws/projector?x=1&key=a-key"


def test_the_demo_catalog_searches_by_title_and_lists_everything_when_blank():
    # Three films and the one show.
    assert len(demo.search("")) == 4
    assert [i["title"] for i in demo.search("harbour")] == ["Harbour Lights"]
    assert demo.search("nothing like this") == []


def test_the_demo_show_is_found_but_its_episodes_are_not_searched():
    # A real library works this way too: you find the show by name, then ask it
    # for its episodes. Searching an episode title finds nothing.
    found = demo.search("standing")
    assert [i["kind"] for i in found] == ["series"]
    assert demo.search("the ford") == []


def test_the_demo_show_lists_its_episodes_in_order():
    episodes = demo.episodes("demo-show")
    assert [(e["season"], e["episode"]) for e in episodes] == [
        (1, 1), (1, 2), (1, 3), (2, 1), (2, 2),
    ]
    assert {e["series"] for e in episodes} == {"The Standing Stones"}
    # The show's year, not the year the season aired: nobody calls it by that.
    assert {e["series_year"] for e in episodes} == {2020}
    assert demo.episodes("demo-one") == []


def test_demo_results_never_leak_internal_fields():
    for item in demo.search(""):
        assert set(item) <= {
            "jf_id", "kind", "title", "year", "runtime_min", "synopsis",
            "has_subtitles",
        }
    for item in demo.episodes("demo-show"):
        assert set(item) <= {
            "jf_id", "kind", "title", "year", "runtime_min", "synopsis",
            "has_subtitles", "series", "series_id", "series_year", "season",
            "episode",
        }
    # Whatever the shape, the internal one never rides along.
    assert not any("color" in i for i in demo.search("") + demo.episodes("demo-show"))


def test_demo_ids_are_findable_and_unknown_ones_are_not():
    assert demo.find("demo-two")["title"] == "Harbour Lights"
    assert demo.find("not-a-title") is None


def test_play_options_only_burn_subtitles_a_title_actually_has():
    main.config.DEMO = False
    assert not main.play_options({"has_subtitles": False}, True)["subtitles"]
    assert main.play_options({"has_subtitles": True}, True)["subtitles"]
    assert not main.play_options({"has_subtitles": True}, False)["subtitles"]


# ---- which showing a supervisor belongs to --------------------------------
# Nothing here runs ffmpeg either: the player is replaced with something that
# answers the four questions the supervisor asks it. What is being asserted is
# which showing a report belongs to, which is invisible from the projector's own
# logs and shows up as a room being closed on a film that just started.

class FakePlayer:
    """A publish that is already over. Enough of Player for the supervisor."""

    def __init__(self, running=False, code=0):
        self._running = running
        self.code = code
        self.starts = []

    def running(self):
        return self._running

    def stderr_text(self):
        return ""

    async def start(self, args):
        self.starts.append(args)
        self._running = True

    async def wait(self):
        return self.code

    async def stop(self):
        self._running = False

    def ended(self):
        """ffmpeg went away on its own."""
        self._running = False


class FakeSocket:
    def __init__(self):
        self.sent = []

    async def send(self, raw):
        self.sent.append(json.loads(raw))

    def states(self):
        return [f.get("state") for f in self.sent if f.get("event") == "status"]


def test_a_replay_of_the_same_title_silences_the_old_supervisor(monkeypatch):
    """Putting the SAME title back on used to end the night.

    Starting a title stops whatever was publishing, and the supervisor watching
    that one wakes up inside the new start to find its ffmpeg gone. The library
    id cannot tell the two showings apart, so it reported the room idle over a
    film that had just begun, and the gate closed the session. Each start
    carries a number instead."""
    monkeypatch.setattr(main.config, "DEMO", True)
    fake = FakePlayer()
    monkeypatch.setattr(main, "_player", fake)
    monkeypatch.setattr(main, "item_detail", _detail)
    socket = FakeSocket()

    async def scenario():
        await main.start_playing(socket, "demo-one", False)
        first = main._state["generation"]
        await main.start_playing(socket, "demo-one", False)   # the same title
        main._supervisor.cancel()
        # Where the first showing's supervisor actually resumes: the ffmpeg it
        # was watching is gone, and it is about to account for how that ended.
        fake.ended()
        await main._supervise(socket, "demo-one", {}, None, first)

    asyncio.run(scenario())
    # Two starts and not one word about anything being idle.
    assert socket.states() == ["starting", "starting"]
    assert main._state["jf_id"] == "demo-one"


def test_stop_bumps_the_generation(monkeypatch):
    """A stop ends the showing as surely as a replay does, so the supervisor
    watching it is stale from here on. Both stops count: the one the gate asks
    for, and the quiet one that runs when the link drops."""
    monkeypatch.setattr(main, "_player", FakePlayer())
    socket = FakeSocket()

    async def scenario():
        before = main._state["generation"]
        await main.stop_playing(socket)
        stopped = main._state["generation"]
        await main.stop_playing_quietly()
        return before, stopped, main._state["generation"]

    before, stopped, quiet = asyncio.run(scenario())
    assert stopped == before + 1
    assert quiet == stopped + 1


async def _detail(jf_id):
    return {"jf_id": jf_id, "title": "A Demo Title", "has_subtitles": False}
