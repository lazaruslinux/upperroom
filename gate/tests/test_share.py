"""
The link preview: the rendered watch page and the picture that goes with it.

Both are public on purpose. A preview fetcher (Signal, Discord) has no cookie,
and a page it cannot read previews as nothing, so these two are the only routes
that answer a stranger. Everything they lead to still needs an account, and the
tests below pin both halves of that: the tags are correct, and nothing but the
shell and the frame is reachable without signing in.
"""

import os

import pytest

import db
from config import THUMB_PATH
from hub import hub
from test_api import add_user, login, setup_admin


def go_live():
    """Mark the channel live. The flag is process-global and the client fixture
    resets it, so a test that wants a live stream says so."""
    hub._live = True


def write_thumb(data=b"\xff\xd8\xff\xdb-not-a-real-jpeg"):
    os.makedirs(os.path.dirname(THUMB_PATH), exist_ok=True)
    with open(THUMB_PATH, "wb") as fh:
        fh.write(data)


def clear_thumb():
    if os.path.exists(THUMB_PATH):
        os.remove(THUMB_PATH)


@pytest.fixture(autouse=True)
def no_stale_frame():
    """No test starts with a frame left over from the last one."""
    clear_thumb()
    yield
    clear_thumb()


# ---- the rendered page ----------------------------------------------------

def test_watch_page_is_served_without_a_session(client):
    # A preview fetcher never has a cookie. It still gets the page.
    resp = client.get("/watch")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert 'id="video"' in resp.text          # the real page, not a stub


def test_preview_names_the_game_while_live(client):
    setup_admin(client, username="owner", channel="Northwind Live")
    db.update_user("owner", display_name="Nell")
    db.set_now_playing("Ashfall Delta")
    go_live()
    body = client.get("/watch").text
    assert (
        '<meta property="og:title" '
        'content="Northwind Live: Nell is streaming Ashfall Delta!">'
    ) in body


def test_preview_falls_back_to_live_now_with_no_game(client):
    setup_admin(client, username="owner", channel="Northwind Live")
    db.update_user("owner", display_name="Nell")
    go_live()
    body = client.get("/watch").text
    assert 'content="Northwind Live: Nell is live now!"' in body


def test_preview_offline_does_not_claim_a_stream(client):
    setup_admin(client, username="owner", channel="Northwind Live")
    db.set_now_playing("Ashfall Delta")
    body = client.get("/watch").text
    assert '<meta property="og:title" content="Northwind Live">' in body
    assert "is streaming" not in body


def test_preview_escapes_the_channel_settings(client):
    # Every value here is operator-entered text going into an HTML attribute.
    setup_admin(client, username="owner", channel="ok")
    db.set_stream_info(site_name='"><script>alert(1)</script>')
    go_live()
    body = client.get("/watch").text
    assert "<script>alert(1)</script>" not in body
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in body


def test_preview_replaces_the_static_block_rather_than_adding_to_it(client):
    setup_admin(client, username="owner", channel="Northwind Live")
    body = client.get("/watch").text
    assert body.count('<meta property="og:title"') == 1
    # The markers themselves are consumed, so nothing advertises the seam.
    assert "og:start" not in body and "og:end" not in body


def test_the_page_answers_head_as_well_as_get(client):
    # Caddy's file server answered HEAD when this page was static, and some
    # preview fetchers ask that way before they ask for the body.
    setup_admin(client, username="owner", channel="Northwind Live")
    assert client.head("/watch").status_code == 200
    assert client.head("/api/og-image.jpg").status_code == 200


def test_preview_image_url_is_absolute(client):
    # A fetcher is not a browser and will not resolve a relative og:image.
    setup_admin(client, username="owner", channel="Northwind Live")
    body = client.get("/watch").text
    assert '<meta property="og:image" content="https://testserver/api/og-image.jpg' in body


# ---- the picture ----------------------------------------------------------

def test_og_image_serves_the_live_frame_without_a_session(client):
    write_thumb()
    resp = client.get("/api/og-image.jpg")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/jpeg"


def test_og_image_falls_back_to_the_static_card_when_offline(client):
    # The picture worker deletes the frame when the stream ends, so between
    # broadcasts no frame of anything is reachable.
    clear_thumb()
    resp = client.get("/api/og-image.jpg")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"


def test_the_authenticated_thumbnail_is_still_gated(client):
    # The public preview picture must not have relaxed the one the home card
    # uses, which is still members only.
    write_thumb()
    assert client.get("/api/thumbnail").status_code == 401


# ---- setting what you are playing -----------------------------------------

def test_game_round_trips_through_stream_info(client):
    setup_admin(client, username="owner")
    assert client.post("/api/stream-info", json={"game": "Ashfall Delta"}).status_code == 200
    assert db.get_now_playing() == "Ashfall Delta"
    assert client.get("/api/admin/stream").json()["game"] == "Ashfall Delta"


def test_game_can_be_cleared(client):
    setup_admin(client, username="owner")
    client.post("/api/stream-info", json={"game": "Ashfall Delta"})
    assert client.post("/api/stream-info", json={"game": ""}).status_code == 200
    assert db.get_now_playing() == ""
    # Clearing what is playing does not forget that it was played.
    assert "Ashfall Delta" in db.recent_games()


def test_game_is_length_capped(client):
    setup_admin(client, username="owner")
    client.post("/api/stream-info", json={"game": "x" * 500})
    assert len(db.get_now_playing()) == 80


def test_a_viewer_cannot_set_the_game(client):
    setup_admin(client, username="owner")
    add_user("viewer")
    viewer = make_viewer(client)
    assert viewer.post("/api/stream-info", json={"game": "nope"}).status_code == 403
    assert db.get_now_playing() == ""


def make_viewer(client):
    from conftest import make_client
    viewer = make_client()
    login(viewer, "viewer")
    return viewer


# ---- the remembered list --------------------------------------------------

def test_recent_games_are_newest_first_and_capped(fresh_games):
    for n in range(db.RECENT_GAMES_KEPT + 3):
        db.set_now_playing(f"game {n}")
    names = db.recent_games()
    assert len(names) == db.RECENT_GAMES_KEPT
    assert names[0] == f"game {db.RECENT_GAMES_KEPT + 2}"
    assert "game 0" not in names          # the oldest fell off the end


def test_playing_the_same_game_again_does_not_duplicate_it(fresh_games):
    db.set_now_playing("Ashfall Delta")
    db.set_now_playing("Something Else")
    db.set_now_playing("Ashfall Delta")
    names = db.recent_games()
    assert names.count("Ashfall Delta") == 1
    assert names[0] == "Ashfall Delta"   # and it moved back to the front


@pytest.fixture
def fresh_games(client):
    """The db helpers under test need a database; the client fixture is what
    points db at a fresh one."""
    return client
