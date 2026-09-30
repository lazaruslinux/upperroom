"""
gate/store.py against the real store service.

Everywhere else the store is faked at the level of these six calls, which is
right for testing what the gate does with an answer but says nothing about
whether the client and the service agree on what an answer is. These run the
client over the real store app (store/main.py) in process, so a key, a status
or a field that drifts on one side fails here instead of on a live broadcast.
"""

import asyncio
import importlib.util
import os
import tempfile
import time

import httpx
import pytest

import store
from fake_store import CALLS

# The service reads its keys and directory at import and refuses to start
# without two good keys, so they are set before it is loaded. Loaded under its
# own name: the gate already has a module called main.
READ_KEY = "client-test-read-" + "r" * 32
WRITE_KEY = "client-test-write-" + "w" * 32
os.environ.setdefault("STORE_DIR", tempfile.mkdtemp(prefix="upperroom-store-client-"))
os.environ["STORE_READ_KEY"] = READ_KEY
os.environ["STORE_WRITE_KEY"] = WRITE_KEY
_SERVICE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "store", "main.py",
)
_spec = importlib.util.spec_from_file_location("store_service", _SERVICE_PATH)
service = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(service)

# The real calls, taken before the autouse fixture swaps in the fake ones, and
# the real client class, before any test wraps it.
real = {name: getattr(store, name) for name in CALLS}
PLAIN_CLIENT = httpx.AsyncClient


def run(name, *args):
    return asyncio.run(real[name](*args))


@pytest.fixture
def served(tmp_path, monkeypatch):
    """The client talking to the real store app, over a fresh directory."""
    root = tmp_path / "served"
    monkeypatch.setattr(service, "STORE_DIR", str(root))
    service.prepare_dirs()
    monkeypatch.setattr(store, "STORE_KEY", WRITE_KEY)

    def over_asgi(**kwargs):
        return PLAIN_CLIENT(transport=httpx.ASGITransport(app=service.app), **kwargs)

    monkeypatch.setattr(store.httpx, "AsyncClient", over_asgi)

    async def fake_tools(args, timeout):
        if args[0] == "ffmpeg":
            with open(args[-1], "wb") as handle:
                handle.write(b"remuxed recording")
            return 0, b""
        return 0, b"61.5\n"

    monkeypatch.setattr(service, "_run", fake_tools)
    return root


def local_file(tmp_path, data=b"x" * 5000, name="local.mp4"):
    path = tmp_path / name
    path.write_bytes(data)
    return str(path)


def test_every_call_round_trips(served, tmp_path):
    assert run("put_file", "vods", "3.mp4", local_file(tmp_path)) == 5000
    assert run("put_file", "clips", "4.mp4", local_file(tmp_path, b"c" * 700)) == 700
    assert (served / "vods" / "3.mp4").read_bytes() == b"x" * 5000

    done = run("finalize_vod", "3.mp4")
    assert done == {"remuxed": True, "duration": 61, "size": len(b"remuxed recording")}

    run("link", "4.mp4", "tok.mp4")
    assert os.path.samefile(served / "clips" / "4.mp4", served / "shared" / "tok.mp4")

    listed = run("list_area", "clips")
    assert [(e["name"], e["size"]) for e in listed] == [("4.mp4", 700)]
    usage = run("usage")
    assert usage["clips_bytes"] == 700 and usage["shared_bytes"] == 700
    assert usage["total_bytes"] >= usage["free_bytes"] > 0

    assert run("delete", "shared", "tok.mp4") is True
    assert run("delete", "shared", "tok.mp4") is False


def test_a_large_file_goes_up_in_pieces(served, tmp_path):
    # Bigger than one read, so the stream really is several chunks.
    data = os.urandom(store.CHUNK * 2 + 123)
    assert run("put_file", "vods", "9.mp4", local_file(tmp_path, data)) == len(data)
    assert (served / "vods" / "9.mp4").read_bytes() == data


def test_a_missing_clip_is_a_404_when_linking(served):
    with pytest.raises(store.StoreError) as caught:
        run("link", "404.mp4", "tok.mp4")
    assert caught.value.status == 404


@pytest.mark.parametrize("key", ["wrong-key", READ_KEY])
def test_a_key_that_cannot_write_is_a_store_error(served, tmp_path, monkeypatch, key):
    monkeypatch.setattr(store, "STORE_KEY", key)
    with pytest.raises(store.StoreError) as caught:
        run("put_file", "vods", "3.mp4", local_file(tmp_path))
    assert caught.value.status == 401
    with pytest.raises(store.StoreError):
        run("delete", "vods", "3.mp4")


def test_a_name_the_store_cannot_hold_never_leaves_the_gate(served, tmp_path):
    with pytest.raises(store.StoreError):
        run("put_file", "vods", "../escape.mp4", local_file(tmp_path))
    # Deleting one is answered "not there" without asking, which is true.
    assert run("delete", "vods", "../escape.mp4") is False


def test_an_unreachable_store_fails_fast(monkeypatch):
    # A port nothing listens on: the refusal has to come back as a StoreError
    # at once, not as a hang and not as any other exception.
    monkeypatch.setattr(store, "STORE_URL", "http://127.0.0.1:9")
    began = time.monotonic()
    for name, args in (("usage", ()), ("list_area", ("vods",)),
                       ("delete", ("vods", "1.mp4"))):
        with pytest.raises(store.StoreError) as caught:
            run(name, *args)
        assert caught.value.status is None
    assert time.monotonic() - began < 3


def answering(monkeypatch, handler):
    """The client talking to something that is not the store."""
    def mocked(**kwargs):
        return PLAIN_CLIENT(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(store.httpx, "AsyncClient", mocked)


def test_a_url_pointing_at_something_else_is_never_read_as_gone(monkeypatch):
    # A wrong URL that answers 404, or a page, must not be taken for "the file
    # is not there": that answer is what lets a row be deleted.
    answering(monkeypatch, lambda request: httpx.Response(404, text="Not Found"))
    with pytest.raises(store.StoreError):
        run("delete", "vods", "1.mp4")
    answering(monkeypatch, lambda request: httpx.Response(200, html="<p>hi</p>"))
    with pytest.raises(store.StoreError):
        run("delete", "vods", "1.mp4")


def test_an_upload_the_store_did_not_fully_receive_is_an_error(monkeypatch, tmp_path):
    answering(monkeypatch, lambda request: httpx.Response(201, json={"size": 10}))
    with pytest.raises(store.StoreError):
        run("put_file", "vods", "1.mp4", local_file(tmp_path))


def test_the_timeouts_are_bounded_and_sized_to_the_call():
    assert store.QUICK.connect == store.UPLOAD.connect == store.REMUX.connect == 5
    assert store.QUICK.read <= 10
    # An upload makes progress or fails within a minute per write; the reply
    # after the store's fsync gets longer, and the remux longer than the
    # store's own ceiling, so the gate never gives up on a remux still running.
    assert store.UPLOAD.write == 60
    assert store.REMUX.read > service.REMUX_TIMEOUT
