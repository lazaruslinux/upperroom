"""
Guest passes: redemption, expiry, and the line between a guest and a member.

The two things worth pinning here are the ones that would be quiet if they
broke. A pass that could be redeemed twice would hand one code to a group text
and let all of them in, and a guest who could reach an account-only endpoint
would make the whole "watch and chat only" decision a comment rather than a
rule. Both get their own test per endpoint rather than one loop, so a failure
names the endpoint that let them through.
"""

import time

import pytest

import auth
import db
from challenge import check_challenge, new_challenge
from config import COOKIE_NAME, GUEST_MINUTES

from test_api import add_user, login, make_client, setup_admin


def make_pass(label="group text"):
    """A guest pass created straight through the db layer, so a test does not
    need an admin client just to get a code."""
    return db.create_guest_pass(label, "owner", int(time.time()))


def solve(client):
    """Fetch a challenge and answer it correctly.

    The test has to solve the same question a person would, which means parsing
    it. That is the point: it proves the question the server asks is actually
    answerable from what it sends, not just that the verify function agrees with
    itself."""
    body = client.get("/api/guest/challenge").json()
    question, token = body["question"], body["token"]
    words = "zero one two three four five six seven eight nine ten eleven twelve " \
            "thirteen fourteen fifteen sixteen seventeen eighteen".split()
    if question.startswith("What is"):
        parts = question.rstrip("?").split()
        answer = str(words.index(parts[2]) + words.index(parts[4]))
    else:
        # "Type the second of these words: alpha, beta, gamma"
        ordinal = question.split("the ", 1)[1].split(" of", 1)[0]
        options = [w.strip() for w in question.split(":", 1)[1].split(",")]
        answer = options[("first", "second", "third").index(ordinal)]
    return {"challenge": answer, "challenge_token": token}


def redeem(client, code, name="Sam"):
    return client.post(
        "/api/guest",
        json={"code": code, "name": name, **solve(client)},
        headers={"X-Forwarded-For": "203.0.113.55"},
    )


# ---- The challenge --------------------------------------------------------

def test_challenge_accepts_its_own_answer_and_refuses_others():
    question, token = new_challenge()
    words = "zero one two three four five six seven eight nine ten eleven twelve " \
            "thirteen fourteen fifteen sixteen seventeen eighteen".split()
    if question.startswith("What is"):
        parts = question.rstrip("?").split()
        answer = str(words.index(parts[2]) + words.index(parts[4]))
        # A sum may be typed as digits or as the word, because the question is
        # worded and people answer in the form they read.
        assert check_challenge(token, words[int(answer)])
    else:
        ordinal = question.split("the ", 1)[1].split(" of", 1)[0]
        options = [w.strip() for w in question.split(":", 1)[1].split(",")]
        answer = options[("first", "second", "third").index(ordinal)]
    assert check_challenge(token, answer)
    assert check_challenge(token, f"  {answer.upper()} ")   # forgiving of typing
    assert not check_challenge(token, "definitely not it")
    assert not check_challenge(token, "")


def test_challenge_refuses_a_token_it_did_not_sign():
    _, token = new_challenge()
    assert not check_challenge(token + "x", "7")
    assert not check_challenge("", "7")
    assert not check_challenge(None, "7")


def test_challenge_token_does_not_contain_the_answer():
    """The token goes to the browser, so it holds an HMAC of the answer rather
    than the answer. Reading it must not give the game away."""
    import base64
    question, token = new_challenge()
    body = token.split(".")[1]
    body += "=" * (-len(body) % 4)
    decoded = base64.urlsafe_b64decode(body).decode()
    if question.startswith("What is"):
        words = "zero one two three four five six seven eight nine ten eleven " \
                "twelve thirteen fourteen fifteen sixteen seventeen eighteen".split()
        parts = question.rstrip("?").split()
        answer = str(words.index(parts[2]) + words.index(parts[4]))
    else:
        ordinal = question.split("the ", 1)[1].split(" of", 1)[0]
        options = [w.strip() for w in question.split(":", 1)[1].split(",")]
        answer = options[("first", "second", "third").index(ordinal)]
    assert f'"{answer}"' not in decoded


# ---- Redemption -----------------------------------------------------------

def test_redeeming_a_pass_signs_in_as_a_guest(client):
    setup_admin(client)
    code = make_pass()
    visitor = make_client()
    resp = redeem(visitor, code, name="Sam")
    assert resp.status_code == 200, resp.text
    assert COOKIE_NAME in resp.cookies

    me = visitor.get("/api/me").json()
    assert me["authed"] is True
    assert me["guest"] is True
    assert me["name"] == "Sam"
    assert me["admin"] is False and me["mod"] is False
    # The expiry is absolute and roughly the configured window away.
    assert abs(me["guest_expires_at"] - (time.time() + GUEST_MINUTES * 60)) < 60


def test_a_pass_can_only_be_redeemed_once(client):
    setup_admin(client)
    code = make_pass()
    assert redeem(make_client(), code).status_code == 200
    second = redeem(make_client(), code)
    assert second.status_code == 400
    assert "not valid" in second.json()["error"]


def test_two_racing_redemptions_cannot_both_win(client):
    """The single-use guarantee lives in one guarded UPDATE, so drive that
    directly: whichever statement matches the still-active row wins and the
    other matches nothing."""
    setup_admin(client)
    code = make_pass()
    now = int(time.time())
    first = db.redeem_guest_pass(code, "guest_aaaa1111", "A", now, now + 60)
    second = db.redeem_guest_pass(code, "guest_bbbb2222", "B", now, now + 60)
    assert first == "ok"
    assert second == "used"
    assert db.get_user("guest_aaaa1111") is not None
    assert db.get_user("guest_bbbb2222") is None


def test_a_revoked_pass_cannot_be_redeemed(client):
    setup_admin(client)
    code = make_pass()
    assert db.revoke_guest_pass(code, int(time.time())) is True
    assert redeem(make_client(), code).status_code == 400


def test_redemption_needs_the_challenge(client):
    setup_admin(client)
    code = make_pass()
    visitor = make_client()
    resp = visitor.post(
        "/api/guest",
        json={"code": code, "name": "Sam", "challenge": "7", "challenge_token": "junk"},
    )
    assert resp.status_code == 400
    # And the pass is untouched, so a failed answer cannot burn somebody's code.
    assert db.get_guest_pass(code)["redeemed_at"] is None


def test_redemption_needs_a_name(client):
    setup_admin(client)
    code = make_pass()
    visitor = make_client()
    resp = visitor.post(
        "/api/guest", json={"code": code, "name": "   ", **solve(visitor)}
    )
    assert resp.status_code == 400
    assert db.get_guest_pass(code)["redeemed_at"] is None


def test_an_unknown_code_and_a_revoked_code_read_the_same(client):
    """The error must not say which codes exist, or the endpoint becomes a way
    to test codes one at a time."""
    setup_admin(client)
    revoked = make_pass()
    db.revoke_guest_pass(revoked, int(time.time()))
    a = redeem(make_client(), "no-such-code").json()["error"]
    b = redeem(make_client(), revoked).json()["error"]
    assert a == b


def test_guest_cannot_sign_in_with_a_password(client):
    """A guest row stores a sentinel where the hash goes, so /api/auth can never
    authenticate one whatever is typed."""
    setup_admin(client)
    code = make_pass()
    visitor = make_client()
    assert redeem(visitor, code).status_code == 200
    username = visitor.get("/api/me").json()["username"]
    for attempt in ("", "password1", db.GUEST_PASSWORD_SENTINEL):
        resp = client.post(
            "/api/auth", json={"username": username, "password": attempt}
        )
        assert resp.status_code == 401


def test_a_member_cannot_register_a_guest_shaped_username(client):
    """The guest_ prefix is reserved, so nobody can sign up as something that
    reads as a guest in chat, or squat a name the redeemer might generate."""
    assert auth._clean_username("guest_abcd1234") is None
    assert auth._clean_username("guest_") is None
    # An ordinary name that merely starts with the word is still fine.
    assert auth._clean_username("guesthouse") == "guesthouse"


# ---- Expiry ---------------------------------------------------------------

def expire(username):
    """Move a guest's expiry into the past."""
    with db.connect() as conn:
        conn.execute(
            "UPDATE users SET guest_expires_at = ? WHERE username = ?",
            (int(time.time()) - 1, username),
        )


def test_expired_guest_is_refused_by_verify(client):
    """/api/verify is what Caddy calls per video segment, so this is the check
    that actually stops the picture."""
    setup_admin(client)
    code = make_pass()
    visitor = make_client()
    redeem(visitor, code)
    assert visitor.get("/api/verify").status_code == 200
    expire(visitor.get("/api/me").json()["username"])
    assert visitor.get("/api/verify").status_code == 401


def test_expired_guest_is_refused_by_the_chat_socket(client):
    setup_admin(client)
    code = make_pass()
    visitor = make_client()
    redeem(visitor, code)
    token = visitor.cookies.get(COOKIE_NAME)
    expire(visitor.get("/api/me").json()["username"])
    with pytest.raises(Exception):
        with visitor.websocket_connect("/ws", cookies={COOKIE_NAME: token}) as ws:
            ws.receive_json()


def test_guest_expired_reads_the_row_not_the_clock_alone():
    now = 1000
    member = {"is_guest": 0, "guest_expires_at": 0}
    live = {"is_guest": 1, "guest_expires_at": now + 10}
    done = {"is_guest": 1, "guest_expires_at": now - 10}
    assert auth.guest_expired(member, now) is False
    assert auth.guest_expired(live, now) is False
    assert auth.guest_expired(done, now) is True
    assert auth.guest_expired(None, now) is False
    # A guest row with no expiry set is not treated as already expired, which
    # would lock out anyone mid-session if the column were ever backfilled to 0.
    assert auth.guest_expired({"is_guest": 1, "guest_expires_at": 0}, now) is False


def test_the_reaper_finds_and_removes_expired_guests(client):
    setup_admin(client)
    now = int(time.time())
    db.redeem_guest_pass(make_pass(), "guest_live0001", "Live", now, now + 600)
    db.redeem_guest_pass(make_pass(), "guest_done0001", "Done", now, now - 1)
    assert db.expired_guests(now) == ["guest_done0001"]
    assert db.delete_user("guest_done0001") is True
    assert db.get_user("guest_done0001") is None
    assert db.get_user("guest_live0001") is not None


# ---- The line between a guest and a member --------------------------------

def signed_in_guest(client):
    setup_admin(client)
    visitor = make_client()
    assert redeem(visitor, make_pass()).status_code == 200
    return visitor


def test_guest_is_refused_by_the_library(client):
    guest = signed_in_guest(client)
    for path in ("/api/vods", "/api/clips", "/api/vods/1", "/api/clips/1",
                 "/api/vods/1/chat", "/api/clips/1/chat"):
        assert guest.get(path).status_code == 401, path


def test_guest_cannot_clip(client):
    guest = signed_in_guest(client)
    resp = guest.post("/api/clip", json={"name": "nope"})
    assert resp.status_code == 403
    assert "Guests" in resp.json()["error"]


def test_guest_cannot_touch_points(client):
    guest = signed_in_guest(client)
    assert guest.get("/api/points").status_code == 403
    assert guest.post("/api/redeem", json={"message": "hi"}).status_code == 403


def test_guest_cannot_change_their_account(client):
    guest = signed_in_guest(client)
    assert guest.post(
        "/api/password",
        json={"current_password": "x", "new_password": "password2"},
    ).status_code == 403
    assert guest.post("/api/profile", json={"bio": "hello"}).status_code == 403


def test_guest_cannot_count_a_view(client):
    guest = signed_in_guest(client)
    assert guest.post("/api/vods/1/view").status_code == 401
    assert guest.post("/api/clips/1/view").status_code == 401


def test_guest_can_still_watch(client):
    """The other half of the rule. A guest who could not do this would have a
    pass that buys nothing."""
    guest = signed_in_guest(client)
    assert guest.get("/api/verify").status_code == 200
    assert guest.get("/api/me").json()["guest"] is True


def test_a_member_is_not_caught_by_any_of_the_guest_refusals(client):
    """Guard against the refusals being written too broadly: everything a guest
    is refused, an ordinary account must still be allowed."""
    setup_admin(client)
    add_user("viewer")
    member = make_client()
    login(member, "viewer", ip="203.0.113.90")
    assert member.get("/api/vods").status_code == 200
    assert member.get("/api/clips").status_code == 200
    assert member.get("/api/points").status_code == 200
    assert member.post("/api/profile", json={"bio": "hello"}).status_code == 200


# ---- Guests stay out of the member-facing lists ---------------------------

def test_guests_are_not_in_the_account_list_but_are_counted(client):
    setup_admin(client)
    add_user("viewer")
    now = int(time.time())
    db.redeem_guest_pass(make_pass(), "guest_aaaa0001", "A Guest", now, now + 600)
    names = [u["username"] for u in db.admin_list_users()]
    assert "viewer" in names and "owner" in names
    assert "guest_aaaa0001" not in names
    # Invisible in the list, but not invisible: the analytics page counts them.
    assert db.count_guests(now) == 1


def test_guests_never_receive_the_go_live_email(client):
    setup_admin(client)
    now = int(time.time())
    db.redeem_guest_pass(make_pass(), "guest_aaaa0002", "A Guest", now, now + 600)
    # Give the guest an address by hand; the row should still be excluded on the
    # is_guest flag alone rather than by having no address.
    with db.connect() as conn:
        conn.execute(
            "UPDATE users SET email = ?, notify_live = 1 WHERE username = ?",
            ("guest@example.invalid", "guest_aaaa0002"),
        )
    assert all(
        email != "guest@example.invalid"
        for _, email in db.list_live_recipients()
    )


# ---- Pass management ------------------------------------------------------

def test_a_spent_pass_can_be_removed_but_an_active_one_cannot(client):
    setup_admin(client)
    active = make_pass()
    spent = make_pass()
    db.revoke_guest_pass(spent, int(time.time()))
    # An active code must be revoked first, so removal can never quietly
    # un-issue a code somebody is holding.
    assert db.delete_guest_pass(active) is False
    assert db.get_guest_pass(active) is not None
    assert db.delete_guest_pass(spent) is True
    assert db.get_guest_pass(spent) is None


def test_clear_used_sweeps_only_spent_passes(client):
    setup_admin(client)
    active = make_pass()
    revoked = make_pass()
    redeemed = make_pass()
    now = int(time.time())
    db.revoke_guest_pass(revoked, now)
    db.redeem_guest_pass(redeemed, "guest_cccc0001", "C", now, now + 600)
    assert db.clear_used_guest_passes() == 2
    remaining = [p["code"] for p in db.list_guest_passes()]
    assert remaining == [active]


def test_admin_endpoints_mint_and_list_passes(client):
    setup_admin(client)
    created = client.post(
        "/api/admin/guest-passes", json={"label": "friday", "count": 3}
    )
    assert created.status_code == 200
    codes = created.json()["codes"]
    assert len(codes) == 3 and len(set(codes)) == 3
    listed = client.get("/api/admin/guest-passes").json()
    assert {p["code"] for p in listed["passes"]} == set(codes)
    assert listed["minutes"] == GUEST_MINUTES


def test_pass_batch_size_is_capped(client):
    setup_admin(client)
    resp = client.post("/api/admin/guest-passes", json={"count": 10000})
    from config import MAX_GUEST_PASS_BATCH
    assert len(resp.json()["codes"]) == MAX_GUEST_PASS_BATCH


def test_guest_pass_admin_routes_are_admin_only(client):
    setup_admin(client)
    add_user("viewer")
    member = make_client()
    login(member, "viewer", ip="203.0.113.91")
    assert member.get("/api/admin/guest-passes").status_code == 403
    assert member.post("/api/admin/guest-passes", json={}).status_code == 403
    assert member.post("/api/admin/guest-passes/clear-used").status_code == 403


# ---- The code space -------------------------------------------------------

def test_the_code_space_is_large_enough_to_be_public():
    """A guest pass is guessable at a public endpoint, unlike an invite, so the
    wordlist size is a security property. Three words from the old 48 word list
    was 110,592 combinations, which is walkable."""
    assert db.CODE_SPACE > 10_000_000
    assert len(set(db._CODE_WORDS)) == len(db._CODE_WORDS)
    code = db._new_code()
    assert len(code.split("-")) == db.CODE_WORD_COUNT
    assert all(part in db._CODE_WORDS for part in code.split("-"))


# ---- moderation, the reason guests are rows at all ------------------------

def test_a_moderator_can_act_on_a_guest(client):
    """The justification for the whole row-based design. Every moderator command
    resolves its target through db.get_user() at a single choke point, so a
    guest with no row could chat and could not be touched. If this test ever
    fails, the rowless approach has crept back in."""
    setup_admin(client, username="owner")
    now = int(time.time())
    db.redeem_guest_pass(make_pass(), "guest_mod00001", "Rowdy", now, now + 600)

    from routes.ws import _target_name
    target = db.get_user(_target_name("@guest_mod00001"))
    assert target is not None, "a moderator command could not resolve the guest"

    # Ban, the strongest of them, and the one that must persist.
    db.add_ban(target["username"], "owner", "spam", now)
    assert any(b["username"] == "guest_mod00001" for b in db.list_bans())

    # And deleting the guest takes the ban and their chat with it, so a reaped
    # guest leaves nothing orphaned.
    assert db.delete_user("guest_mod00001") is True
    assert not any(b["username"] == "guest_mod00001" for b in db.list_bans())
