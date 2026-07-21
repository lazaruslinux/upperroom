"""
Shared fixtures for the HTTP and WebSocket tests.

config.py reads its settings from the environment at import, and main.py creates
its media directories and opens the database the instant it is imported. So this
module points all of that at a throwaway scratch area BEFORE the gate package is
imported, which keeps the suite off the real /data paths. The MediaMTX poll and
the ffmpeg record/thumbnail workers run only inside the app's lifespan, and the
tests never enter it (the client below is not used as a context manager), so no
test touches docker or the network.
"""

import os
import sys
import tempfile

import pytest

# The gate package (config, db, main, routes/*) imports its modules by bare name,
# the same way it is imported when the service runs from inside the gate dir.
GATE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, GATE_DIR)

# A scratch area for the app's data dirs and its bootstrap database, set before
# the gate package is imported. Each test gets its own database again in the
# `client` fixture; these just make the one-time import at load harmless.
_SCRATCH = tempfile.mkdtemp(prefix="upperroom-tests-")
os.environ["SELFSTREAM_JWT_SECRET"] = "test-secret-not-a-real-key"
os.environ["SELFSTREAM_DB"] = os.path.join(_SCRATCH, "boot.db")
os.environ["SELFSTREAM_AVATAR_DIR"] = os.path.join(_SCRATCH, "avatars")
os.environ["SELFSTREAM_MEDIA_DIR"] = os.path.join(_SCRATCH, "media")
os.environ["SELFSTREAM_RECORD_TMP"] = os.path.join(_SCRATCH, "rec")
os.environ["SELFSTREAM_THUMB"] = os.path.join(_SCRATCH, "thumb.jpg")

import auth  # noqa: E402
import db  # noqa: E402
import main  # noqa: E402
from hub import hub  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402


def make_client():
    """A TestClient over the assembled app. base_url is https so the secure
    session cookie the app sets is returned on later requests in the same jar."""
    return TestClient(main.app, base_url="https://testserver")


@pytest.fixture
def client(tmp_path, monkeypatch):
    """A TestClient backed by a fresh, empty database per test.

    The client is not entered as a context manager, so the app's lifespan (the
    MediaMTX watcher and the ffmpeg workers) never starts; the tests exercise the
    request handlers only.
    """
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "test.db"))
    db.init_db()
    # The rate limiter, chat backlog, bans and timeouts are process-global; reset
    # them so one test's state cannot leak into the next.
    auth._ATTEMPTS.clear()
    hub._sockets.clear()
    hub._watchers.clear()
    hub._history.clear()
    hub._timeouts.clear()
    hub._banned.clear()
    return make_client()
