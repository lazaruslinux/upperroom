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
# The gate renders the watch page (for its link preview tags) out of the static
# site directory, so point that at the real one in the checkout: the preview
# tests assert against the page that actually ships.
os.environ["SELFSTREAM_WEB_DIR"] = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "web"
)
# A departure is normally held for a minute in case the viewer is only switching
# tabs. Off here, so the socket tests below see the line the moment they close a
# connection instead of waiting out a real minute; the grace has its own tests,
# which set their own value.
os.environ["SELFSTREAM_JOIN_GRACE"] = "0"
# The stream key is seeded from PUBLISH_PASS on first init_db, so drop any value
# a developer has in their shell before it can leak a real key into the tests.
os.environ.pop("PUBLISH_PASS", None)

import auth  # noqa: E402
import db  # noqa: E402
import main  # noqa: E402
import projector  # noqa: E402
import watchers  # noqa: E402
import theater  # noqa: E402
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
    # The rate limiters, chat backlog, bans and timeouts are process-global;
    # reset them so one test's state cannot leak into the next. reset_limiters()
    # covers all of them at once, so a limiter added later cannot be forgotten
    # here and turn the suite order-dependent.
    auth.reset_limiters()
    hub._sockets.clear()
    hub._watchers.clear()
    hub._history.clear()
    hub._timeouts.clear()
    hub._banned.clear()
    hub._last_post.clear()
    # A pending departure is a live asyncio task on whichever loop made it, so
    # one left behind would fire into the next test's room.
    for task in hub._leaving.values():
        task.cancel()
    hub._leaving.clear()
    # The live flag is process-global too, and now gates highlight redemption, so
    # reset it between tests. A case that wants a live stream sets it explicitly.
    hub._live = False
    # The projector link holds one socket for the whole process. A stub left
    # attached by one test would answer another test's calls.
    projector.link._socket = None
    projector.link._pending.clear()
    projector.link._last_seen = 0
    # Nothing is attached, so a test that hands the link an event directly is
    # speaking for a settled connection, not for one that has just opened. The
    # attach helper delivers the opening report the way a real projector does.
    projector.link._opening_due = False
    # Who is pulling video is process-global as well, so a test that filled the
    # room would leave the next one at its limit.
    watchers.reset()
    # When the host last stopped a title is process-global too, and it decides
    # whether an idle report closes the session. One test's stop would otherwise
    # keep the next test's finished title from closing anything.
    theater._host_stopped_at = -1e9
    # Which title last failed is process-global as well, and it decides whether
    # the room is told "again".
    theater._last_error_id = None
    return make_client()
