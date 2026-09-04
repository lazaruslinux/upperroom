"""
The share card on a published clip link.

The page is rendered by the gate now, so pasting a clip link into a chat app
says what the clip is instead of showing a blank card. The rules it has to keep
are the ones /api/shared/{token} already keeps: no creator username anywhere,
and a token that is unknown or revoked tells a stranger nothing at all.
"""

import asyncio
import os

import db
import media
from config import SHARED_DIR

from test_api import add_user, make_client, setup_admin
from test_sharing import make_clip_row
from test_clips import live  # noqa: F401  (the make_clip stamping test uses it)


def publish(client, clip_id):
    """Share a clip and hand back its token."""
    url = client.post(
        f"/api/clips/{clip_id}/share", json={"share": True}
    ).json()["url"]
    return url.rsplit("/", 1)[-1]


def page(token):
    """The clip page as a stranger with no cookie at all sees it."""
    return make_client().get(f"/clip/{token}").text


# ---- what the card says ----------------------------------------------------

def test_the_page_answers_a_stranger_and_names_the_clip(client):
    setup_admin(client, username="owner", channel="Northwind Live")
    token = publish(client, make_clip_row())
    stranger = make_client()
    resp = stranger.get(f"/clip/{token}")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "<title>&quot;A clip&quot; - Stream Clip</title>" in resp.text
    assert (
        '<meta property="og:title" content="&quot;A clip&quot; - Stream Clip">'
    ) in resp.text
    assert 'id="clip-video"' in resp.text          # the real page, not a stub


def test_the_page_answers_head_as_well_as_get(client):
    # Some preview fetchers ask that way before they ask for the body, and
    # Caddy's file server answered HEAD when this page was static.
    setup_admin(client, username="owner", channel="Northwind Live")
    token = publish(client, make_clip_row())
    assert make_client().head(f"/clip/{token}").status_code == 200


def test_the_second_line_is_the_site_and_the_game(client):
    setup_admin(client, username="owner", channel="Northwind Live")
    token = publish(client, make_clip_row(game="Ashfall Delta"))
    body = page(token)
    assert (
        '<meta property="og:description" '
        'content="Northwind Live playing Ashfall Delta">'
    ) in body
    assert (
        '<meta name="twitter:description" '
        'content="Northwind Live playing Ashfall Delta">'
    ) in body


def test_a_clip_with_no_game_says_only_the_site(client):
    # Every clip cut before the game was stamped on clips is this case.
    setup_admin(client, username="owner", channel="Northwind Live")
    token = publish(client, make_clip_row())
    body = page(token)
    assert '<meta property="og:description" content="Northwind Live">' in body
    assert "playing" not in body


def test_the_picture_is_the_clips_own_frame(client):
    setup_admin(client, username="owner", channel="Northwind Live")
    token = publish(client, make_clip_row())
    body = page(token)
    assert (
        f'<meta property="og:image" content="https://testserver/shared/{token}.jpg">'
    ) in body
    assert (
        f'<meta name="twitter:image" content="https://testserver/shared/{token}.jpg">'
    ) in body
    # Width only: the height follows whatever the stream was shot at.
    assert '<meta property="og:image:width" content="640">' in body
    assert "og:image:height" not in body


def test_a_clip_with_no_poster_falls_back_to_the_channel_card(client):
    # The poster is linked best effort when a clip is published, so a clip
    # without one must not point a fetcher at a 404.
    setup_admin(client, username="owner", channel="Northwind Live")
    token = publish(client, make_clip_row())
    os.remove(os.path.join(SHARED_DIR, f"{token}.jpg"))
    body = page(token)
    assert (
        '<meta property="og:image" '
        'content="https://testserver/assets/icons/og-default.png?v=1">'
    ) in body
    assert "og:image:width" not in body


def test_the_page_url_is_absolute(client):
    # A fetcher is not a browser and will not resolve a relative og:url.
    setup_admin(client, username="owner", channel="Northwind Live")
    token = publish(client, make_clip_row())
    assert (
        f'<meta property="og:url" content="https://testserver/clip/{token}">'
    ) in page(token)


def test_a_clip_name_is_escaped_everywhere_it_appears(client):
    # A clip name is viewer-entered text going into an HTML attribute.
    setup_admin(client, username="owner", channel="Northwind Live")
    token = publish(client, make_clip_row(name='"Say <hi>" & run'))
    body = page(token)
    assert "<hi>" not in body and "&run" not in body
    assert body.count("&lt;hi&gt;") == 3          # title, og:title, twitter:title
    assert "&amp; run" in body


def test_the_static_block_is_replaced_rather_than_added_to(client):
    setup_admin(client, username="owner", channel="Northwind Live")
    token = publish(client, make_clip_row())
    body = page(token)
    assert body.count('<meta property="og:title"') == 1
    assert body.count("<title>") == 1
    assert "A clip&quot; - Stream Clip" in body and 'content="A clip"' not in body
    # The markers themselves are consumed, so nothing advertises the seam.
    assert "og:start" not in body and "og:end" not in body
    # And the card still refuses a search index.
    assert '<meta name="robots" content="noindex, nofollow">' in body


# ---- what it must not say --------------------------------------------------

def test_a_revoked_token_looks_exactly_like_one_that_never_existed(client):
    """The card must not confirm that a clip was ever there."""
    setup_admin(client, username="owner", channel="Northwind Live")
    clip_id = make_clip_row()
    token = publish(client, clip_id)
    client.post(f"/api/clips/{clip_id}/share", json={"share": False})

    stranger = make_client()
    revoked = stranger.get(f"/clip/{token}")
    unknown = stranger.get("/clip/never-was-a-token")
    assert revoked.status_code == unknown.status_code == 200
    assert revoked.text == unknown.text
    assert "A clip" in revoked.text          # the generic card, not the name
    assert "Stream Clip" not in revoked.text
    assert "Northwind Live" not in revoked.text


def test_the_rendered_page_never_carries_a_username(client):
    """The clip row holds the creator's account name, and this page is the other
    place it must not travel. Asserted on the whole body rather than by checking
    the fields we happen to remember."""
    setup_admin(client, username="owner", channel="Northwind Live")
    add_user("bob")
    token = publish(client, make_clip_row(creator="bob", game="Ashfall Delta"))
    assert "bob" not in page(token).lower()


# ---- stamping the game on the clip -----------------------------------------

def test_the_game_round_trips_through_the_clip_row(client):
    clip_id = make_clip_row(game="Ashfall Delta")
    assert db.get_clip(clip_id)["game"] == "Ashfall Delta"


def test_a_clip_is_stamped_with_what_was_playing(client, live):  # noqa: F811
    # The channel's label moves on to the next game; the clip's does not.
    setup_admin(client, username="owner")
    db.set_now_playing("Ashfall Delta")
    clip_id, error = asyncio.run(
        media.make_clip(db.get_user("owner"), None, seconds=30)
    )
    assert error is None
    assert db.get_clip(clip_id)["game"] == "Ashfall Delta"
    db.set_now_playing("Something Else")
    assert db.get_clip(clip_id)["game"] == "Ashfall Delta"
