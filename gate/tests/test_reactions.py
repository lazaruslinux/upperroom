"""
Likes and comments on recordings and clips.

Accounts only, and deliberately beside the chat replay rather than inside it.
The interesting cases are the boundaries: a guest must be refused, a deleted
recording must not leave its comments behind, and a comment must obey the same
chat moderation as a live message so it is not a way around a ban.
"""

import time

import db
from config import MAX_COMMENT_LENGTH
from hub import hub

from test_api import add_user, login, make_client, setup_admin
from test_guest import make_pass, redeem


def a_clip(name="A clip", creator="owner"):
    now = int(time.time())
    clip_id = db.create_clip(name, "", creator, None, now - 60, now, 60, now)
    db.set_clip_filename(clip_id, f"{clip_id}.mp4")
    return clip_id


# ---- likes -----------------------------------------------------------------

def test_a_like_counts_once_however_many_times_it_is_sent(client):
    """The primary key is what makes this true, so it is worth asserting rather
    than trusting: a double tap is not two likes."""
    setup_admin(client)
    clip_id = a_clip()
    for _ in range(3):
        body = client.post(f"/api/clips/{clip_id}/like", json={"liked": True}).json()
    assert body["likes"] == 1
    assert body["liked"] is True


def test_unliking_takes_it_back(client):
    setup_admin(client)
    clip_id = a_clip()
    client.post(f"/api/clips/{clip_id}/like", json={"liked": True})
    body = client.post(f"/api/clips/{clip_id}/like", json={"liked": False}).json()
    assert body["likes"] == 0
    reactions = client.get(f"/api/clips/{clip_id}/reactions").json()
    assert reactions["likes"] == 0 and reactions["liked"] is False


def test_two_people_are_two_likes(client):
    setup_admin(client)
    clip_id = a_clip()
    add_user("viewer")
    client.post(f"/api/clips/{clip_id}/like", json={"liked": True})
    other = make_client()
    login(other, "viewer", ip="203.0.113.130")
    body = other.post(f"/api/clips/{clip_id}/like", json={"liked": True}).json()
    assert body["likes"] == 2
    # And each sees their own state, not each other's.
    assert client.get(f"/api/clips/{clip_id}/reactions").json()["liked"] is True


def test_likes_work_on_recordings_too(client):
    setup_admin(client)
    now = int(time.time())
    vod_id = db.create_vod("A broadcast", "", now)
    db.finalize_vod(vod_id, now, 60, f"{vod_id}.mp4")
    body = client.post(f"/api/vods/{vod_id}/like", json={"liked": True}).json()
    assert body["likes"] == 1


# ---- comments --------------------------------------------------------------

def test_posting_and_reading_a_comment(client):
    setup_admin(client, username="owner")
    clip_id = a_clip()
    resp = client.post(f"/api/clips/{clip_id}/comments", json={"text": "nice one"})
    assert resp.status_code == 200
    comments = resp.json()["comments"]
    assert len(comments) == 1
    assert comments[0]["text"] == "nice one"
    assert comments[0]["username"] == "owner"


def test_an_empty_comment_is_refused(client):
    setup_admin(client)
    clip_id = a_clip()
    assert client.post(
        f"/api/clips/{clip_id}/comments", json={"text": "   "}
    ).status_code == 400


def test_a_long_comment_is_trimmed_not_rejected(client):
    setup_admin(client)
    clip_id = a_clip()
    body = client.post(
        f"/api/clips/{clip_id}/comments", json={"text": "x" * (MAX_COMMENT_LENGTH + 500)}
    ).json()
    assert len(body["comments"][0]["text"]) == MAX_COMMENT_LENGTH


def test_an_author_can_remove_their_own_comment(client):
    setup_admin(client, username="owner")
    add_user("viewer")
    clip_id = a_clip()
    member = make_client()
    login(member, "viewer", ip="203.0.113.131")
    cid = member.post(
        f"/api/clips/{clip_id}/comments", json={"text": "mine"}
    ).json()["comments"][0]["id"]
    assert member.delete(f"/api/comments/{cid}").status_code == 200
    # Soft deleted: the row stays, the text does not.
    after = member.get(f"/api/clips/{clip_id}/reactions").json()["comments"]
    assert len(after) == 1
    assert after[0]["text"] == ""
    assert after[0]["deleted_by"] == "viewer"


def test_a_moderator_can_remove_anyone_s_comment_but_a_viewer_cannot(client):
    setup_admin(client, username="owner")
    add_user("viewer")
    add_user("nosy")
    clip_id = a_clip()
    member = make_client()
    login(member, "viewer", ip="203.0.113.132")
    cid = member.post(
        f"/api/clips/{clip_id}/comments", json={"text": "mine"}
    ).json()["comments"][0]["id"]

    # Another plain viewer may not.
    nosy = make_client()
    login(nosy, "nosy", ip="203.0.113.133")
    assert nosy.delete(f"/api/comments/{cid}").status_code == 403
    # The admin may.
    assert client.delete(f"/api/comments/{cid}").status_code == 200


def test_a_comment_obeys_the_same_moderation_as_chat(client):
    """Otherwise commenting is a way around a chat ban."""
    setup_admin(client, username="owner")
    add_user("viewer")
    clip_id = a_clip()
    member = make_client()
    login(member, "viewer", ip="203.0.113.134")

    hub.add_ban_local("viewer")
    assert member.post(
        f"/api/clips/{clip_id}/comments", json={"text": "still here"}
    ).status_code == 403
    hub.remove_ban_local("viewer")

    hub.set_timeout("viewer", 300)
    assert member.post(
        f"/api/clips/{clip_id}/comments", json={"text": "still here"}
    ).status_code == 403
    hub.clear_timeout("viewer")
    assert member.post(
        f"/api/clips/{clip_id}/comments", json={"text": "now then"}
    ).status_code == 200


# ---- guests are refused ----------------------------------------------------

def test_a_guest_can_neither_like_nor_comment(client):
    """Both outlive a guest's half hour, so both are refused the same way
    clipping is."""
    setup_admin(client)
    clip_id = a_clip()
    guest = make_client()
    assert redeem(guest, make_pass()).status_code == 200

    like = guest.post(f"/api/clips/{clip_id}/like", json={"liked": True})
    comment = guest.post(f"/api/clips/{clip_id}/comments", json={"text": "hi"})
    assert like.status_code == 403 and comment.status_code == 403
    assert "Guests" in like.json()["error"]
    # And they cannot even read the thread, since it is part of the library.
    assert guest.get(f"/api/clips/{clip_id}/reactions").status_code == 401


def test_a_stranger_gets_nothing(client):
    setup_admin(client)
    clip_id = a_clip()
    stranger = make_client()
    assert stranger.get(f"/api/clips/{clip_id}/reactions").status_code == 401
    assert stranger.post(
        f"/api/clips/{clip_id}/like", json={"liked": True}
    ).status_code == 401


# ---- nothing is left behind ------------------------------------------------

def test_deleting_a_clip_takes_its_likes_and_comments(client):
    setup_admin(client)
    clip_id = a_clip()
    client.post(f"/api/clips/{clip_id}/like", json={"liked": True})
    client.post(f"/api/clips/{clip_id}/comments", json={"text": "bye"})
    client.delete(f"/api/clips/{clip_id}")
    assert db.list_comments("clip", clip_id) == []
    assert db.like_state("clip", clip_id, "owner") == (0, False)


def test_the_retention_sweep_also_takes_them(client):
    """The sweep uses its own delete path. Three paths delete media and each one
    has to clear the same children, which is why they share one helper."""
    setup_admin(client)
    clip_id = a_clip()
    client.post(f"/api/clips/{clip_id}/like", json={"liked": True})
    client.post(f"/api/clips/{clip_id}/comments", json={"text": "bye"})
    db.delete_media_rows([{"kind": "clip", "id": clip_id}])
    assert db.list_comments("clip", clip_id) == []
    assert db.like_state("clip", clip_id, "owner") == (0, False)


def test_deleting_an_account_takes_its_likes_and_comments(client):
    """The guest reaper runs delete_user every few minutes, so a comment left
    naming a deleted account would be a ghost in the thread."""
    setup_admin(client, username="owner")
    add_user("leaver")
    clip_id = a_clip()
    member = make_client()
    login(member, "leaver", ip="203.0.113.135")
    member.post(f"/api/clips/{clip_id}/like", json={"liked": True})
    member.post(f"/api/clips/{clip_id}/comments", json={"text": "here"})

    db.delete_user("leaver")
    assert db.list_comments("clip", clip_id) == []
    assert db.like_state("clip", clip_id, "leaver") == (0, False)


# ---- the public clip page has none of this ---------------------------------

def test_a_shared_clip_exposes_no_likes_or_comments(client):
    """The public page shows the video and nothing about the people around it."""
    setup_admin(client)
    clip_id = a_clip()
    client.post(f"/api/clips/{clip_id}/comments", json={"text": "a private thought"})
    client.post(f"/api/clips/{clip_id}/like", json={"liked": True})
    # Publishing needs a real file, so put one there.
    import os
    from config import CLIP_DIR
    os.makedirs(CLIP_DIR, exist_ok=True)
    with open(os.path.join(CLIP_DIR, f"{clip_id}.mp4"), "wb") as fh:
        fh.write(b"bytes" * 300)
    token = client.post(
        f"/api/clips/{clip_id}/share", json={"share": True}
    ).json()["url"].rsplit("/", 1)[-1]

    raw = make_client().get(f"/api/shared/{token}").text
    assert "a private thought" not in raw
    assert "likes" not in raw and "comments" not in raw
