"""
Unit tests for the recorder watchdog's pure decision logic.

The watchdog itself drives ffmpeg subprocesses, which the suite never spawns.
These tests exercise the small pure helpers it is factored around: the action
decision, the backoff schedule, and the survival check. conftest.py has already
pointed the gate's data paths at a scratch area and put the gate dir on the path,
so importing media here is side-effect free.
"""

import media
from config import RECORD_BACKOFF, RECORD_STALL_POLLS, RECORD_SURVIVAL_SECONDS


def test_growing_file_is_healthy():
    # Process alive and the scratch file grew this poll: nothing to do.
    assert media.watchdog_action(
        True, 0, RECORD_STALL_POLLS, 100.0, 0.0
    ) == media.WATCHDOG_NONE


def test_brief_no_growth_is_tolerated():
    # Fragments flush periodically, so a few no-growth polls must not trip it.
    assert media.watchdog_action(
        True, RECORD_STALL_POLLS - 1, RECORD_STALL_POLLS, 100.0, 0.0
    ) == media.WATCHDOG_NONE


def test_dead_process_restarts():
    assert media.watchdog_action(
        False, 0, RECORD_STALL_POLLS, 100.0, 0.0
    ) == media.WATCHDOG_RESTART


def test_stall_past_threshold_restarts():
    assert media.watchdog_action(
        True, RECORD_STALL_POLLS, RECORD_STALL_POLLS, 100.0, 0.0
    ) == media.WATCHDOG_RESTART


def test_failure_within_backoff_window_waits():
    # Dead, but the next retry time is still in the future: hold off.
    assert media.watchdog_action(
        False, 0, RECORD_STALL_POLLS, 100.0, 130.0
    ) == media.WATCHDOG_WAIT
    # Once the window has passed, the same failure restarts.
    assert media.watchdog_action(
        False, 0, RECORD_STALL_POLLS, 130.0, 130.0
    ) == media.WATCHDOG_RESTART


def test_backoff_schedule_is_immediate_then_growing():
    # Immediate first retry, then 10s, 30s, 60s, capped at 60s.
    assert RECORD_BACKOFF == (0, 10, 30, 60)
    assert [media.backoff_delay(n) for n in range(6)] == [0, 10, 30, 60, 60, 60]
    # A negative/odd attempt count never indexes out of range.
    assert media.backoff_delay(-1) == 0


def test_survival_resets_the_backoff():
    assert media.survived_long_enough(RECORD_SURVIVAL_SECONDS - 1) is False
    assert media.survived_long_enough(RECORD_SURVIVAL_SECONDS) is True
    # After a reset the attempt count is zero, so the next retry is immediate.
    assert media.backoff_delay(0) == 0


def test_recorder_maps_both_streams_explicitly():
    """Regression guard. The recorder used to rely on ffmpeg's default stream
    selection, which silently dropped audio once the read path moved from RTMP to
    RTSP. Every broadcast recorded that way lost its sound and nothing failed, so
    the mapping is asserted here rather than trusted."""
    args = media.recorder_args("rtsp://example/live", "/tmp/1.mp4")
    assert "-map" in args
    # Video first, then audio marked optional so a video-only source still records.
    v = args.index("0:v:0")
    a = args.index("0:a:0?")
    assert args[v - 1] == "-map" and args[a - 1] == "-map"
    # The maps must come after the input and before the codec choice, or ffmpeg
    # applies them to the wrong file.
    assert args.index("-i") < v < args.index("-c")


def test_recorder_still_forces_tcp_and_a_fragmented_mp4():
    # The mapping fix must not disturb what the RTSP read path already relies on.
    args = media.recorder_args("rtsp://example/live", "/tmp/1.mp4")
    assert args[args.index("-rtsp_transport") + 1] == "tcp"
    assert "+frag_keyframe+empty_moov+default_base_moof" in args
    assert args[-1] == "/tmp/1.mp4"
