"""
The request/reply envelope and the demo catalog. Both ends have to agree on the
wire format exactly, and it is small enough that the agreement can just be
asserted here.
"""

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
    assert len(demo.search("")) == 3
    assert [i["title"] for i in demo.search("harbour")] == ["Harbour Lights"]
    assert demo.search("nothing like this") == []


def test_demo_results_never_leak_internal_fields():
    for item in demo.search(""):
        assert set(item) == {
            "jf_id", "title", "year", "runtime_min", "synopsis", "has_subtitles"
        }


def test_demo_ids_are_findable_and_unknown_ones_are_not():
    assert demo.find("demo-two")["title"] == "Harbour Lights"
    assert demo.find("not-a-title") is None


def test_play_options_only_burn_subtitles_a_title_actually_has():
    main.config.DEMO = False
    assert not main.play_options({"has_subtitles": False}, True)["subtitles"]
    assert main.play_options({"has_subtitles": True}, True)["subtitles"]
    assert not main.play_options({"has_subtitles": True}, False)["subtitles"]
