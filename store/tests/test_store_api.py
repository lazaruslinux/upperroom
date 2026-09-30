"""
The media store's HTTP surface.

This service holds the whole archive and answers anyone who can reach its one
port, so most of what is pinned here is refusal: the read key cannot write, no
key gets nothing, and no name reaches outside the three areas. The rest is the
contract the gate depends on: uploads are atomic, deletes are idempotent, a
failed remux keeps the recording, and ranges work so a video can seek.
"""

import os

import pytest

import main

READ = {"Authorization": f"Bearer {main.READ_KEY}"}
WRITE = {"Authorization": f"Bearer {main.WRITE_KEY}"}
DATA = bytes(range(256)) * 40          # 10240 bytes, every offset distinct-ish


def put(client, area, name, data=DATA):
    return client.put(f"/{area}/{name}", content=data, headers=WRITE)


def leftovers(media):
    """Dot-prefixed temp files anywhere in the store."""
    return [
        name for area in main.AREAS
        for name in os.listdir(media / area) if name.startswith(".")
    ]


# ---- configuration ---------------------------------------------------------

def test_health_needs_no_key_and_says_nothing_else(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


@pytest.mark.parametrize("read, write", [
    ("", "w" * 40),
    ("r" * 40, ""),
    ("r" * 31, "w" * 40),
    ("r" * 40, "w" * 31),
])
def test_a_missing_or_short_key_is_refused(read, write):
    assert main.config_problem(read, write) is not None


def test_the_same_key_for_both_is_refused():
    # Caddy holds the read key; if it were also the write key, the proxy that
    # only ever serves could delete the archive.
    assert "different" in main.config_problem("k" * 40, "k" * 40)


def test_two_good_keys_pass():
    assert main.config_problem("r" * 32, "w" * 32) is None


def test_startup_clears_an_unfinished_upload(media):
    stale = media / "vods" / ".abcdef.part"
    stale.write_bytes(b"half a recording")
    kept = media / "vods" / "1.mp4"
    kept.write_bytes(b"a real one")
    main.prepare_dirs()
    assert not stale.exists()
    assert kept.exists()


# ---- who may do what -------------------------------------------------------

WRITES = [
    ("put", "/vods/1.mp4", {"content": b"x"}),
    ("delete", "/vods/1.mp4", {}),
    ("post", "/vods/1.mp4/finalize", {}),
    ("post", "/link", {"json": {"src": "1.mp4", "dst": "t.mp4"}}),
    ("get", "/usage", {}),
    ("get", "/list/vods", {}),
]


@pytest.mark.parametrize("method, path, kwargs", WRITES)
@pytest.mark.parametrize("headers", [
    {},
    READ,
    {"Authorization": "Bearer not-the-key"},
    {"Authorization": f"Basic {main.WRITE_KEY}"},
    {"Authorization": "Bearer"},
])
def test_writes_need_the_write_key(client, method, path, kwargs, headers):
    resp = getattr(client, method)(path, headers=headers, **kwargs)
    assert resp.status_code == 401


@pytest.mark.parametrize("method, path, kwargs", WRITES)
def test_the_write_key_is_not_refused(client, method, path, kwargs):
    resp = getattr(client, method)(path, headers=WRITE, **kwargs)
    assert resp.status_code != 401


@pytest.mark.parametrize("headers, status", [
    ({}, 401),
    ({"Authorization": "Bearer not-the-key"}, 401),
    (READ, 200),
    (WRITE, 200),
])
def test_reading_a_file_needs_either_key(client, headers, status):
    assert put(client, "clips", "5.mp4").status_code == 201
    assert client.get("/clips/5.mp4", headers=headers).status_code == status
    assert client.head("/clips/5.mp4", headers=headers).status_code == status


# ---- names -----------------------------------------------------------------

@pytest.mark.parametrize("path", [
    "/vods/..%2Fsecret.mp4",
    "/vods/%2e%2e%2fsecret.mp4",
    "/vods/..%2F..%2Fetc%2Fpasswd",
    "/vods/%2e%2e",
    "/vods/a%00.mp4",
    "/vods/a.mp4%0A",
    "/vods/.hidden.mp4",
    "/vods/a.txt",
    "/vods/a.MP4",
    "/vods/a.mp4.exe",
    "/vods/a%20b.mp4",
    "/vods/" + "x" * 65 + ".mp4",
    "/secret/a.mp4",
])
def test_anything_but_an_area_and_a_safe_name_is_not_found(client, media, path):
    (media.parent / "secret.mp4").write_bytes(b"outside the store")
    assert client.get(path, headers=READ).status_code == 404
    assert client.put(path, content=b"x", headers=WRITE).status_code == 404
    assert client.delete(path, headers=WRITE).status_code == 404
    assert (media.parent / "secret.mp4").read_bytes() == b"outside the store"
    assert leftovers(media) == []


@pytest.mark.parametrize("path", ["/vods", "/vods/", "/shared/", "/"])
def test_a_directory_is_never_listed_over_get(client, path):
    put(client, "shared", "tok.mp4")
    for headers in (READ, WRITE):
        resp = client.get(path, headers=headers)
        assert resp.status_code == 404
        assert b"tok" not in resp.content


def test_the_listing_is_write_key_only_because_it_names_every_public_link(client):
    put(client, "shared", "tok.mp4")
    assert client.get("/list/shared", headers=READ).status_code == 401


# ---- serving ---------------------------------------------------------------

def test_a_file_comes_back_with_its_type(client):
    put(client, "vods", "1.mp4")
    put(client, "vods", "1.jpg", b"\xff\xd8poster")
    video = client.get("/vods/1.mp4", headers=READ)
    assert video.status_code == 200
    assert video.content == DATA
    assert video.headers["content-type"] == "video/mp4"
    assert video.headers["accept-ranges"] == "bytes"
    assert client.get("/vods/1.jpg", headers=READ).headers["content-type"] == "image/jpeg"


def test_a_missing_file_is_not_found(client):
    assert client.get("/vods/404.mp4", headers=READ).status_code == 404


@pytest.mark.parametrize("header, start, end", [
    ("bytes=10-19", 10, 20),
    ("bytes=0-0", 0, 1),
    ("bytes=10000-", 10000, len(DATA)),
    ("bytes=-5", len(DATA) - 5, len(DATA)),
])
def test_a_range_gets_exactly_those_bytes(client, header, start, end):
    put(client, "vods", "1.mp4")
    resp = client.get("/vods/1.mp4", headers={**READ, "Range": header})
    assert resp.status_code == 206
    assert resp.content == DATA[start:end]
    assert resp.headers["content-range"] == f"bytes {start}-{end - 1}/{len(DATA)}"
    assert resp.headers["content-length"] == str(end - start)


def test_a_range_past_the_end_is_refused(client):
    put(client, "vods", "1.mp4")
    resp = client.get("/vods/1.mp4", headers={**READ, "Range": "bytes=999999-"})
    assert resp.status_code == 416


def test_head_answers_the_size_without_a_body(client):
    put(client, "vods", "1.mp4")
    resp = client.head("/vods/1.mp4", headers=READ)
    assert resp.status_code == 200
    assert resp.headers["content-length"] == str(len(DATA))
    assert resp.content == b""
    ranged = client.head("/vods/1.mp4", headers={**READ, "Range": "bytes=0-99"})
    assert ranged.status_code == 206
    assert ranged.headers["content-length"] == "100"


# ---- uploads ---------------------------------------------------------------

def test_an_upload_lands_whole_and_reports_its_size(client, media):
    resp = put(client, "clips", "7.mp4")
    assert resp.status_code == 201
    assert resp.json() == {"size": len(DATA)}
    assert (media / "clips" / "7.mp4").read_bytes() == DATA
    assert leftovers(media) == []


def test_an_upload_replaces_what_was_there(client, media):
    put(client, "clips", "7.mp4", b"old")
    put(client, "clips", "7.mp4", b"new")
    assert (media / "clips" / "7.mp4").read_bytes() == b"new"


def test_a_streamed_upload_without_a_length_works(client, media):
    def body():
        for _ in range(4):
            yield DATA
    resp = client.put("/vods/2.mp4", content=body(), headers=WRITE)
    assert resp.status_code == 201
    assert (media / "vods" / "2.mp4").read_bytes() == DATA * 4


def test_an_upload_over_the_cap_is_refused_by_its_declared_length(client, media, monkeypatch):
    monkeypatch.setattr(main, "MAX_UPLOAD_BYTES", 100)
    put(client, "clips", "7.mp4", b"keep me")
    assert put(client, "clips", "7.mp4", b"x" * 101).status_code == 413
    assert (media / "clips" / "7.mp4").read_bytes() == b"keep me"
    assert leftovers(media) == []


def test_an_upload_over_the_cap_is_refused_while_streaming(client, media, monkeypatch):
    # A chunked body declares no length, so the cap has to be counted as the
    # bytes arrive, and the partial file has to go.
    monkeypatch.setattr(main, "MAX_UPLOAD_BYTES", 1000)

    def body():
        for _ in range(10):
            yield b"x" * 500
    resp = client.put("/vods/3.mp4", content=body(), headers=WRITE)
    assert resp.status_code == 413
    assert not (media / "vods" / "3.mp4").exists()
    assert leftovers(media) == []


# ---- publishing by hard link ----------------------------------------------

def test_link_gives_a_clip_a_public_name_for_the_same_bytes(client, media):
    put(client, "clips", "9.mp4")
    resp = client.post("/link", json={"src": "9.mp4", "dst": "tok_en-1.mp4"}, headers=WRITE)
    assert resp.status_code == 200
    source = media / "clips" / "9.mp4"
    public = media / "shared" / "tok_en-1.mp4"
    assert os.stat(source).st_ino == os.stat(public).st_ino
    assert client.get("/shared/tok_en-1.mp4", headers=READ).content == DATA


def test_linking_again_is_harmless(client):
    put(client, "clips", "9.mp4")
    body = {"src": "9.mp4", "dst": "tok.mp4"}
    assert client.post("/link", json=body, headers=WRITE).status_code == 200
    assert client.post("/link", json=body, headers=WRITE).status_code == 200


def test_link_refuses_a_missing_clip(client):
    resp = client.post("/link", json={"src": "404.mp4", "dst": "tok.mp4"}, headers=WRITE)
    assert resp.status_code == 404


def test_link_never_replaces_a_different_file(client, media):
    put(client, "clips", "9.mp4")
    put(client, "shared", "tok.mp4", b"something else")
    resp = client.post("/link", json={"src": "9.mp4", "dst": "tok.mp4"}, headers=WRITE)
    assert resp.status_code == 409
    assert (media / "shared" / "tok.mp4").read_bytes() == b"something else"


@pytest.mark.parametrize("body, status", [
    ({"src": "9.mp4", "dst": "tok.jpg"}, 400),
    ({"src": "9.mp4"}, 400),
    (["9.mp4", "tok.mp4"], 400),
    ({"src": "../vods/1.mp4", "dst": "tok.mp4"}, 404),
    ({"src": "9.mp4", "dst": "../vods/1.mp4"}, 404),
])
def test_link_refuses_anything_but_two_safe_names_of_one_kind(client, body, status):
    put(client, "clips", "9.mp4")
    assert client.post("/link", json=body, headers=WRITE).status_code == status


def test_deleting_the_clip_leaves_the_public_name_playing_until_it_goes_too(client, media):
    # The bytes survive while any name for them does, which is why the gate
    # always removes the public name when it removes a clip.
    put(client, "clips", "9.mp4")
    client.post("/link", json={"src": "9.mp4", "dst": "tok.mp4"}, headers=WRITE)
    client.delete("/clips/9.mp4", headers=WRITE)
    assert client.get("/shared/tok.mp4", headers=READ).content == DATA
    client.delete("/shared/tok.mp4", headers=WRITE)
    assert client.get("/shared/tok.mp4", headers=READ).status_code == 404


# ---- deleting, listing, usage ---------------------------------------------

def test_delete_is_idempotent_and_says_whether_anything_was_there(client, media):
    put(client, "vods", "1.mp4")
    first = client.delete("/vods/1.mp4", headers=WRITE)
    second = client.delete("/vods/1.mp4", headers=WRITE)
    assert first.status_code == second.status_code == 200
    assert first.json() == {"existed": True}
    assert second.json() == {"existed": False}
    assert not (media / "vods" / "1.mp4").exists()


def test_list_names_each_file_with_its_size_and_skips_temp_files(client, media):
    put(client, "vods", "1.mp4")
    put(client, "vods", "1.jpg", b"poster")
    (media / "vods" / ".inflight.part").write_bytes(b"not yet")
    (media / "vods" / "notes.txt").write_bytes(b"not ours")
    resp = client.get("/list/vods", headers=WRITE)
    assert resp.status_code == 200
    listed = {entry["name"]: entry for entry in resp.json()}
    assert set(listed) == {"1.mp4", "1.jpg"}
    assert listed["1.mp4"]["size"] == len(DATA)
    assert listed["1.jpg"]["size"] == len(b"poster")
    assert isinstance(listed["1.mp4"]["mtime"], int)


def test_list_of_an_unknown_area_is_not_found(client):
    assert client.get("/list/art", headers=WRITE).status_code == 404


def test_usage_sums_each_area_and_reports_the_disk(client):
    put(client, "vods", "1.mp4")
    put(client, "clips", "2.mp4", b"x" * 100)
    body = client.get("/usage", headers=WRITE).json()
    assert body["vods_bytes"] == len(DATA)
    assert body["clips_bytes"] == 100
    assert body["shared_bytes"] == 0
    assert body["total_bytes"] > 0
    assert 0 < body["free_bytes"] <= body["total_bytes"]


# ---- finalize --------------------------------------------------------------

def stub_tools(monkeypatch, remux_rc=0, duration=b"42.7\n", calls=None):
    """Stand in for ffmpeg and ffprobe. The remux writes a recognisable file to
    the output path it was given, the way ffmpeg would."""
    async def fake_run(args, timeout):
        if calls is not None:
            calls.append((args[0], timeout))
        if args[0] == "ffmpeg":
            if remux_rc == 0:
                with open(args[-1], "wb") as handle:
                    handle.write(b"remuxed")
            else:
                with open(args[-1], "wb") as handle:
                    handle.write(b"half")
            return remux_rc, b""
        return 0, duration
    monkeypatch.setattr(main, "_run", fake_run)


def test_finalize_remuxes_in_place_and_reports_the_duration(client, media, monkeypatch):
    calls = []
    stub_tools(monkeypatch, calls=calls)
    put(client, "vods", "1.mp4")
    resp = client.post("/vods/1.mp4/finalize", headers=WRITE)
    assert resp.status_code == 200
    assert resp.json() == {"remuxed": True, "duration": 42, "size": len(b"remuxed")}
    assert (media / "vods" / "1.mp4").read_bytes() == b"remuxed"
    assert leftovers(media) == []
    # The remux runs under the long ceiling, never unbounded.
    assert calls[0] == ("ffmpeg", main.REMUX_TIMEOUT)


def test_a_failed_remux_keeps_the_recording_as_uploaded(client, media, monkeypatch):
    stub_tools(monkeypatch, remux_rc=1)
    put(client, "vods", "1.mp4")
    resp = client.post("/vods/1.mp4/finalize", headers=WRITE)
    assert resp.status_code == 200
    assert resp.json()["remuxed"] is False
    assert (media / "vods" / "1.mp4").read_bytes() == DATA
    assert leftovers(media) == []


def test_an_unreadable_duration_is_null_not_an_error(client, monkeypatch):
    stub_tools(monkeypatch, duration=b"N/A\n")
    put(client, "vods", "1.mp4")
    assert client.post("/vods/1.mp4/finalize", headers=WRITE).json()["duration"] is None


@pytest.mark.parametrize("path", ["/vods/404.mp4/finalize", "/vods/1.jpg/finalize"])
def test_finalize_needs_an_uploaded_recording(client, monkeypatch, path):
    stub_tools(monkeypatch)
    put(client, "vods", "1.jpg", b"poster")
    assert client.post(path, headers=WRITE).status_code == 404
