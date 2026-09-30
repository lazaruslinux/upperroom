"""
Public clip sharing.

This is the only part of the app a stranger can reach content through, so the
tests are mostly about what must NOT happen: no username leaving the building,
no clip public that was not chosen, and above all nothing still reachable after
it was deleted or unshared.
"""

import asyncio
import time

import db
import fake_store
import media

from test_api import add_user, login, make_client, setup_admin


def make_clip_row(name="A clip", creator="owner", with_file=True, game="",
                  poster=True):
    """A clip row plus its files in the (fake) media store, so the link and
    unlink paths are exercised against files rather than mocked. `game` is what
    was playing when the clip was cut, which the share card names."""
    now = int(time.time())
    clip_id = db.create_clip(
        name, "", creator, None, now - 60, now, 60, now, game=game
    )
    filename = f"{clip_id}.mp4"
    media_store = fake_store.current()
    if with_file:
        media_store.write("clips", filename, b"not really video, but bytes" * 40)
        if poster:
            media_store.write("clips", f"{clip_id}.jpg", b"poster")
    db.set_clip_filename(clip_id, filename, has_poster=with_file and poster)
    return clip_id


def shared(token, ext="mp4"):
    return fake_store.current().has("shared", f"{token}.{ext}")


# ---- publishing ------------------------------------------------------------

def test_a_clip_is_private_until_it_is_published(client):
    setup_admin(client)
    clip_id = make_clip_row()
    assert db.get_clip(clip_id)["share_token"] is None
    listed = client.get("/api/clips").json()["clips"][0]
    assert listed["shared"] is False
    assert listed["share_url"] is None


def test_publishing_makes_it_reachable_without_any_session(client):
    setup_admin(client)
    clip_id = make_clip_row()
    resp = client.post(f"/api/clips/{clip_id}/share", json={"share": True})
    assert resp.status_code == 200
    url = resp.json()["url"]
    token = url.rsplit("/", 1)[-1]

    # A brand new client with no cookies at all.
    stranger = make_client()
    public = stranger.get(f"/api/shared/{token}")
    assert public.status_code == 200
    assert public.json()["name"] == "A clip"

    # And the file itself was linked into the public area, poster too.
    assert shared(token)
    assert shared(token, "jpg")

    # The listing must say so too: list_clips once dropped share_token, which
    # left every clip looking unshared on the dashboard.
    listed = client.get("/api/clips").json()["clips"][0]
    assert listed["shared"] is True
    assert listed["share_url"] == url


def test_the_public_view_never_carries_a_username(client):
    """The clip row holds the creator's account name. This is the one place it
    must not travel, so it is asserted on the whole payload rather than by
    checking the fields we happen to remember."""
    setup_admin(client)
    add_user("bob")
    clip_id = make_clip_row(creator="bob")
    token = client.post(
        f"/api/clips/{clip_id}/share", json={"share": True}
    ).json()["url"].rsplit("/", 1)[-1]

    raw = make_client().get(f"/api/shared/{token}").text
    assert "bob" not in raw.lower()
    assert "creator" not in raw.lower()


def test_the_public_view_carries_no_chat_replay(client):
    """A replay names every person who spoke. None of them agreed to be
    published, so it is not on the public page at any price."""
    setup_admin(client)
    clip_id = make_clip_row()
    token = client.post(
        f"/api/clips/{clip_id}/share", json={"share": True}
    ).json()["url"].rsplit("/", 1)[-1]
    payload = make_client().get(f"/api/shared/{token}").json()
    assert "chat" not in payload and "messages" not in payload
    # And the account-only replay endpoint is still refused to a stranger.
    assert make_client().get(f"/api/clips/{clip_id}/chat").status_code == 401


def test_publishing_twice_keeps_the_same_link(client):
    """Somebody may already have the first link. Minting a second token would
    silently break it."""
    setup_admin(client)
    clip_id = make_clip_row()
    first = client.post(f"/api/clips/{clip_id}/share", json={"share": True}).json()
    second = client.post(f"/api/clips/{clip_id}/share", json={"share": True}).json()
    assert first["url"] == second["url"]


# ---- taking it back --------------------------------------------------------

def test_unpublishing_removes_the_file_and_the_link(client):
    setup_admin(client)
    clip_id = make_clip_row()
    token = client.post(
        f"/api/clips/{clip_id}/share", json={"share": True}
    ).json()["url"].rsplit("/", 1)[-1]
    assert shared(token)

    client.post(f"/api/clips/{clip_id}/share", json={"share": False})
    assert make_client().get(f"/api/shared/{token}").status_code == 404
    assert not shared(token)
    assert not shared(token, "jpg")
    # The clip itself is untouched: the bytes survive under their private name.
    assert fake_store.current().has("clips", f"{clip_id}.mp4")


def test_deleting_a_published_clip_takes_the_public_copy_with_it(client):
    """The public file is a second name for the same bytes. Leaving it behind
    would keep a deleted clip playing for anyone holding the link."""
    setup_admin(client)
    clip_id = make_clip_row()
    token = client.post(
        f"/api/clips/{clip_id}/share", json={"share": True}
    ).json()["url"].rsplit("/", 1)[-1]

    assert client.delete(f"/api/clips/{clip_id}").status_code == 200
    assert not shared(token)
    assert make_client().get(f"/api/shared/{token}").status_code == 404


def test_retention_sweeping_a_published_clip_takes_the_public_copy_too(client):
    """The 48 hour sweep deletes clips on its own, with nobody watching. If it
    left the public copy, a clip would keep playing for strangers forever and
    nothing would look wrong from the inside. This is the quiet one."""
    setup_admin(client)
    clip_id = make_clip_row()
    token = client.post(
        f"/api/clips/{clip_id}/share", json={"share": True}
    ).json()["url"].rsplit("/", 1)[-1]

    # Age it past a two day limit and run the real sweep.
    with db.connect() as conn:
        conn.execute(
            "UPDATE clips SET created_at = ? WHERE id = ?",
            (int(time.time()) - 3 * 86400, clip_id),
        )
    candidates = db.prune_candidates({"clip_keep_days": 2}, int(time.time()))
    assert [c["id"] for c in candidates] == [clip_id]
    # The candidate must carry the token, or the remover cannot unlink it.
    assert candidates[0]["share_token"] == token

    for item in candidates:
        assert asyncio.run(media._remove_item_files(item))
    assert not shared(token)


def test_the_startup_sweep_removes_public_files_with_no_clip_behind_them(client):
    """Last line of defence. If anything ever leaves a file here without a live
    token, it is gone at the next start rather than served forever."""
    setup_admin(client)
    fake_store.current().write("shared", "a-token-nobody-issued.mp4", b"orphan")
    # A genuinely published clip must survive the same sweep.
    clip_id = make_clip_row()
    token = client.post(
        f"/api/clips/{clip_id}/share", json={"share": True}
    ).json()["url"].rsplit("/", 1)[-1]

    assert asyncio.run(media.sweep_orphan_shared()) == 1
    assert not shared("a-token-nobody-issued")
    assert shared(token)


# ---- who may publish -------------------------------------------------------

def test_only_an_admin_may_publish(client):
    setup_admin(client)
    clip_id = make_clip_row()
    add_user("viewer")
    member = make_client()
    login(member, "viewer", ip="203.0.113.120")
    assert member.post(
        f"/api/clips/{clip_id}/share", json={"share": True}
    ).status_code == 403
    assert db.get_clip(clip_id)["share_token"] is None
    # And a stranger with no session at all.
    assert make_client().post(
        f"/api/clips/{clip_id}/share", json={"share": True}
    ).status_code == 403


def test_an_unknown_token_is_refused_the_same_as_a_revoked_one(client):
    setup_admin(client)
    clip_id = make_clip_row()
    token = client.post(
        f"/api/clips/{clip_id}/share", json={"share": True}
    ).json()["url"].rsplit("/", 1)[-1]
    client.post(f"/api/clips/{clip_id}/share", json={"share": False})

    stranger = make_client()
    a = stranger.get(f"/api/shared/{token}")
    b = stranger.get("/api/shared/never-was-a-token")
    assert a.status_code == b.status_code == 404
    assert a.json() == b.json()


def test_the_token_is_long_enough_to_be_the_whole_credential(client):
    """There is nothing to sign in to behind a share link, so the token is the
    entire secret."""
    setup_admin(client)
    clip_id = make_clip_row()
    token = client.post(
        f"/api/clips/{clip_id}/share", json={"share": True}
    ).json()["url"].rsplit("/", 1)[-1]
    assert len(token) >= 20
