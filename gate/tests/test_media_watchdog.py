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


# ---- clip window arithmetic ----------------------------------------------
# The old code computed this inline at the moment the save request arrived, so
# none of it could be tested and all three of its errors were invisible. It is a
# pure function now, and these are the cases that were actually wrong.

def _window(**kw):
    args = dict(started_at=1000, now=1100, clip_seconds=60, at=None)
    args.update(kw)
    return media.clip_window(**args)


def test_the_window_ends_where_the_viewer_was_looking():
    # The whole point. The viewer pressed Clip at 1090, then spent 25 seconds
    # typing a name, so the request arrives at 1115. The window must end at
    # 1090, not at the moment the request landed.
    start, end, duration = media.clip_window(1000, 1115, 60, at=1090)
    assert end == 1090
    assert start == 1030
    assert duration == 60


def test_typing_for_a_long_time_does_not_move_the_window():
    # Same instant, three different save times: identical window every time.
    windows = {media.clip_window(1000, now, 60, at=1090) for now in (1091, 1100, 1200)}
    assert windows == {(1030, 1090, 60)}


def test_without_an_instant_it_falls_back_to_the_old_estimate():
    # A browser that cannot supply playingDate still gets a clip, just a less
    # exact one: now minus the fixed lag, which is what every clip used to be.
    from config import CLIP_LAG
    start, end, duration = _window(at=None)
    assert end == 1100 - CLIP_LAG
    assert duration == 60


def test_a_client_cannot_ask_for_footage_outside_the_recording():
    # Clamped at both ends, so a wrong or hostile clock cannot reach past the
    # live edge or back before the broadcast began.
    _, end, _ = _window(at=99999)          # far in the future
    assert end == 1100                     # pinned to now
    start, end, _ = _window(at=1005)       # before enough exists
    assert start == 1000                   # never before the recording started


def test_a_stream_that_just_started_has_nothing_to_clip_yet():
    # Two seconds in, there is no clip worth cutting, and the caller gets a
    # sentinel rather than a negative or absurdly short duration.
    assert media.clip_window(1000, 1002, 60, at=1002) == (None, None, 0)


def test_the_window_shortens_gracefully_early_in_a_broadcast():
    # Ten seconds into a broadcast with a 60 second clip length, the clip is the
    # ten seconds that exist, not a failure and not sixty seconds of nothing.
    start, end, duration = media.clip_window(1000, 1010, 60, at=1010)
    assert (start, end, duration) == (1000, 1010, 10)


def test_clip_length_comes_from_the_channel_not_a_constant():
    # The setting is what decides the window, so changing it on the dashboard
    # changes the clip. This is the behaviour that moving it out of config.py
    # was for.
    assert _window(at=1090, clip_seconds=30)[2] == 30
    assert _window(at=1090, clip_seconds=90)[0] == 1000   # clamped to the start
