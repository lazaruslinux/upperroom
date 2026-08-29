"""
The library URLs and the shape the projector reports items in. No server is
contacted: these are the string builders and the parser.
"""

import jellyfin

BASE = "http://library.test:8096"


def test_search_asks_only_for_movies_and_recurses_the_library():
    url = jellyfin.search_url(BASE, "afternoon", limit=10)
    assert url.startswith(f"{BASE}/Items?")
    assert "IncludeItemTypes=Movie" in url
    assert "Recursive=true" in url
    assert "Limit=10" in url
    assert "searchTerm=afternoon" in url


def test_search_escapes_the_query():
    url = jellyfin.search_url(BASE, "a & b?")
    assert "a+%26+b%3F" in url


def test_the_file_url_carries_the_key_in_the_query_for_ffmpeg():
    # ffmpeg is handed this string as an input, so the key rides the query
    # rather than a header. It is never logged: the player logs argv length.
    url = jellyfin.file_url(BASE, "abc123", "secret-key")
    assert url == f"{BASE}/Items/abc123/File?api_key=secret-key"


def test_the_item_and_image_urls_name_the_item():
    # Through the list endpoint filtered to one id: /Items/{id} is gone in
    # Jellyfin 10.10 and later, which answer it with a bare 400.
    url = jellyfin.item_url(BASE, "abc123")
    assert url.startswith(f"{BASE}/Items?")
    assert "ids=abc123" in url
    assert "/Items/abc123" not in url
    assert jellyfin.image_url(BASE, "abc123").startswith(
        f"{BASE}/Items/abc123/Images/Primary?"
    )


def test_ids_with_odd_characters_are_escaped_into_the_path():
    assert "a%20b" in jellyfin.file_url(BASE, "a b", "k")


def test_the_auth_header_is_the_emby_token():
    assert jellyfin.headers("k") == {"X-Emby-Token": "k"}


def test_runtime_ticks_become_whole_minutes():
    assert jellyfin.ticks_to_minutes(90 * 60 * 10_000_000) == 90
    assert jellyfin.ticks_to_minutes(0) is None
    assert jellyfin.ticks_to_minutes(None) is None


def test_subtitles_are_detected_from_the_media_streams():
    assert jellyfin.has_subtitles(
        {"MediaStreams": [{"Type": "Video"}, {"Type": "Subtitle"}]}
    )
    assert not jellyfin.has_subtitles({"MediaStreams": [{"Type": "Audio"}]})
    assert not jellyfin.has_subtitles({})


def test_parse_items_maps_the_fields_the_gate_asked_for():
    payload = {"Items": [{
        "Id": "abc123",
        "Name": "The Long Afternoon",
        "ProductionYear": 2019,
        "RunTimeTicks": 95 * 60 * 10_000_000,
        "Overview": "A synopsis.",
        "MediaStreams": [{"Type": "Subtitle"}],
    }]}
    assert jellyfin.parse_items(payload) == [{
        "jf_id": "abc123",
        "title": "The Long Afternoon",
        "year": 2019,
        "runtime_min": 95,
        "synopsis": "A synopsis.",
        "has_subtitles": True,
    }]


def test_parse_items_drops_anything_without_an_id_and_survives_an_empty_reply():
    assert jellyfin.parse_items({"Items": [{"Name": "no id"}]}) == []
    assert jellyfin.parse_items({}) == []
    assert jellyfin.parse_items(None) == []
