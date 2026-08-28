"""
The ffmpeg argv the projector builds.

None of this runs ffmpeg. The point is the opposite: these settings are the ones
whose mistakes do not crash anything, they just make the broadcast worse for
everybody watching, and are invisible from the projector's own logs.
"""

import player


BASE_OPTS = {
    "ingest_url": "rtmp://example.test:1935/live",
    "stream_key": "a-stream-key",
    "bitrate": "6000k",
    "max_height": 1080,
    "vaapi_device": "",
}


def args_for(url="http://library.test/Items/abc/File?api_key=k", **extra):
    return player.build_play_args(url, {**BASE_OPTS, **extra})


def pair_after(args, flag):
    """The value ffmpeg is given for `flag`."""
    return args[args.index(flag) + 1]


def test_output_is_flv_to_the_ingest_with_the_stream_key():
    args = args_for()
    assert pair_after(args, "-f") == "flv"
    assert args[-1] == "rtmp://example.test:1935/live?pass=a-stream-key"


def test_output_url_without_a_key_is_the_bare_ingest():
    assert player.output_url({"ingest_url": "rtmp://h/live", "stream_key": ""}) == (
        "rtmp://h/live"
    )


def test_cpu_encode_is_libx264_with_no_b_frames():
    # -bf 0 is not cosmetic: this stack's HLS muxer breaks on B-frame reordering.
    args = args_for()
    assert pair_after(args, "-c:v") == "libx264"
    assert pair_after(args, "-preset") == "veryfast"
    assert pair_after(args, "-bf") == "0"


def test_rate_control_is_cbr_ish_with_double_the_bitrate_as_buffer():
    args = args_for(bitrate="4500k")
    assert pair_after(args, "-b:v") == "4500k"
    assert pair_after(args, "-maxrate") == "4500k"
    assert pair_after(args, "-bufsize") == "9000k"


def test_bufsize_keeps_the_unit_and_survives_nonsense():
    assert player.bufsize_for("6000k") == "12000k"
    assert player.bufsize_for("3M") == "6M"
    assert player.bufsize_for("2500000") == "5000000"
    assert player.bufsize_for("not-a-bitrate") == "not-a-bitrate"


def test_keyframes_are_every_two_seconds_in_time_not_frames():
    args = args_for()
    assert pair_after(args, "-force_key_frames") == "expr:gte(t,n_forced*2)"


def test_audio_is_stereo_aac():
    args = args_for()
    assert pair_after(args, "-c:a") == "aac"
    assert pair_after(args, "-b:a") == "160k"
    assert pair_after(args, "-ac") == "2"


def test_the_input_is_read_at_native_rate_and_maps_both_tracks():
    # Without -re ffmpeg pushes the whole file at once; the audio map is explicit
    # because letting ffmpeg guess is how the gate's recorder once lost its sound.
    args = args_for()
    assert "-re" in args
    assert args[args.index("-i") + 1].startswith("http://library.test/")
    assert "0:v:0" in args and "0:a:0?" in args


def test_the_scale_caps_the_height_and_never_upscales():
    args = args_for(max_height=720)
    assert pair_after(args, "-filter:v") == "scale=-2:'min(720,ih)'"


def test_vaapi_sets_the_device_uploads_after_scaling_and_swaps_the_encoder():
    args = args_for(vaapi_device="/dev/dri/renderD128")
    assert pair_after(args, "-vaapi_device") == "/dev/dri/renderD128"
    assert pair_after(args, "-c:v") == "h264_vaapi"
    # Scale on the CPU, then upload: only the encode belongs on the GPU.
    assert pair_after(args, "-filter:v") == (
        "scale=-2:'min(1080,ih)',format=nv12,hwupload"
    )


def test_subtitles_burn_from_the_same_input_after_the_scale():
    url = "http://library.test/Items/abc/File?api_key=k"
    args = args_for(url, subtitles=True, subtitle_source=url)
    chain = pair_after(args, "-filter:v")
    assert chain.startswith("scale=-2:'min(1080,ih)',subtitles='")
    # The URL's colons would otherwise end the filter argument.
    assert "http\\://library.test" in chain


def test_subtitles_are_not_burned_unless_a_source_was_given():
    args = args_for(subtitles=True)
    assert "subtitles=" not in pair_after(args, "-filter:v")


def test_escaping_covers_what_ends_a_filter_argument():
    assert player.escape_filter_value("a:b") == "a\\:b"
    assert player.escape_filter_value("it's") == "it\\'s"
    assert player.escape_filter_value("a\\b") == "a\\\\b"
    assert player.escape_filter_value("a,b[c]") == "a\\,b\\[c\\]"


def test_demo_generates_its_own_picture_and_sound_with_the_title_on_screen():
    args = player.build_play_args(
        None, {**BASE_OPTS, "demo_title": "The Long Afternoon"}
    )
    assert "lavfi" in args
    assert any("testsrc2" in a for a in args)
    assert any("sine=" in a for a in args)
    chain = pair_after(args, "-filter:v")
    assert "drawtext=" in chain and "The Long Afternoon" in chain
    # It ends on its own, which is also how the demo shows the return to
    # intermission without anybody pressing stop.
    assert pair_after(args, "-t") == str(player.DEMO_SECONDS)


def test_a_demo_title_with_a_quote_cannot_break_the_filter():
    args = player.build_play_args(None, {**BASE_OPTS, "demo_title": "It's: Fine"})
    assert "It\\'s\\: Fine" in pair_after(args, "-filter:v")


def test_a_subtitle_failure_is_recognised_but_an_ordinary_error_is_not():
    assert player.is_subtitle_failure(
        "[Parsed_subtitles_1 @ 0x5] Unable to open subtitles"
    )
    assert not player.is_subtitle_failure("Connection refused")
    assert not player.is_subtitle_failure("")
