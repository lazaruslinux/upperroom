"""
The store reads its keys and its directory from the environment at import, and
refuses to start without two good keys, so both are set here BEFORE main is
imported. Each test then gets an empty directory of its own through the `media`
fixture. Nothing here needs ffmpeg: the finalize tests stub the subprocess.
"""

import os
import sys
import tempfile

import pytest

SERVICE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SERVICE_DIR)

READ_KEY = "read-" + "r" * 40
WRITE_KEY = "write-" + "w" * 40
os.environ["STORE_DIR"] = tempfile.mkdtemp(prefix="upperroom-store-tests-")
os.environ["STORE_READ_KEY"] = READ_KEY
os.environ["STORE_WRITE_KEY"] = WRITE_KEY

import main  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402


@pytest.fixture
def media(tmp_path, monkeypatch):
    """A fresh, empty store directory with its three areas."""
    monkeypatch.setattr(main, "STORE_DIR", str(tmp_path))
    main.prepare_dirs()
    return tmp_path


@pytest.fixture
def client(media):
    return TestClient(main.app)
