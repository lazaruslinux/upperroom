"""
The go-live Web Push: the RFC 8291 encryption and RFC 8292 VAPID token, the
subscription routes and their allowlist, who a push reaches, and the startup
step that empties what the old email and Discord notices left behind.

No test reaches a real push service: the HTTP client is swapped for an httpx
MockTransport that records each request and answers with a chosen status.
"""

import ast
import json
import logging
import pathlib
import re
import struct

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

import db
import main
import notify
import webpush
from conftest import make_client
from test_api import add_user, login, setup_admin

SITE = "https://watch.example.com"
FCM = "https://fcm.googleapis.com/fcm/send/"


def unspace(text):
    return re.sub(r"\s+", "", text)


# ---- RFC 8291 Appendix A --------------------------------------------------
# Copied verbatim from https://www.rfc-editor.org/rfc/rfc8291.txt (section 5
# and Appendix A); whitespace inside a value is the RFC's line wrapping.

RFC_PLAINTEXT = "V2hlbiBJIGdyb3cgdXAsIEkgd2FudCB0byBiZSBhIHdhdGVybWVsb24"
RFC_AS_PUBLIC = """BP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIg
      Dll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A8"""
RFC_AS_PRIVATE = "yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw"
RFC_UA_PUBLIC = """BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-
      JvLexhqUzORcx aOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4"""
RFC_UA_PRIVATE = "q1dXpw3UpT5VOmu_cf_v6ih07Aems3njxI-JWgLcM94"
RFC_SALT = "DGv6ra1nlYgDCS1FRnbzlw"
RFC_AUTH = "BTBZMqHH6r4Tts7J_aSIgg"
RFC_HEADER = """DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z 9KsN6nGRTbVYI_c7VJSPQTBtkgcy27ml
   mlMoZIIgDll6e3vCYLocInmYWAmS6Tlz AC8wEqKK6PBru3jl7A8"""
RFC_CIPHERTEXT = """8pfeW0KbunFT06SuDKoJH9Ql87S1QUrd irN6GcG7sFz1y1sqLgVi1VhjVkHsUoEs
   bI_0LpXMuGvnzQ"""
RFC_BODY = """DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27ml
   mlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A_yl95bQpu6cVPT
   pK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWLVWGNWQexSgSxsj_Qulcy4a-fN"""


def d(text):
    return webpush.b64u_decode(unspace(text))


def test_rfc8291_example_is_reproduced_byte_for_byte():
    body = webpush.encrypt(
        d(RFC_PLAINTEXT), d(RFC_UA_PUBLIC), d(RFC_AUTH),
        salt=d(RFC_SALT), server_key=webpush.private_key_from_raw(d(RFC_AS_PRIVATE)),
    )
    header = d(RFC_HEADER)
    assert len(header) == 86
    assert body[:86] == header
    assert body[86:] == d(RFC_CIPHERTEXT)
    assert body == d(RFC_BODY)
    # The server key the header carries is the RFC's public key.
    assert body[21:86] == d(RFC_AS_PUBLIC)


def decrypt(body, ua_private, auth_secret):
    """The receiving side of RFC 8291, for checking what a request carried."""
    salt, (rs, idlen) = body[:16], struct.unpack("!IB", body[16:21])
    as_public = body[21:21 + idlen]
    ua_public = ua_private.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    shared = ua_private.exchange(
        ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), as_public)
    )
    ikm = webpush._hkdf(auth_secret, shared, b"WebPush: info\x00" + ua_public + as_public, 32)
    cek = webpush._hkdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = webpush._hkdf(salt, ikm, b"Content-Encoding: nonce\x00", 12)
    plain = AESGCM(cek).decrypt(nonce, body[21 + idlen:], None)
    assert rs == 4096 and plain.endswith(b"\x02")
    return plain[:-1]


def test_the_rfc_receiver_can_read_what_encrypt_makes():
    ua = webpush.private_key_from_raw(d(RFC_UA_PRIVATE))
    body = webpush.encrypt(b'{"title":"x"}', d(RFC_UA_PUBLIC), d(RFC_AUTH))
    assert decrypt(body, ua, d(RFC_AUTH)) == b'{"title":"x"}'


def test_encrypt_refuses_more_than_one_record():
    with pytest.raises(ValueError):
        webpush.encrypt(b"x" * 3994, d(RFC_UA_PUBLIC), d(RFC_AUTH))


# ---- VAPID ----------------------------------------------------------------

def test_vapid_token_verifies_with_the_right_claims(client, monkeypatch):
    monkeypatch.setattr(webpush, "SITE_URL", SITE)
    private, public = webpush.vapid_keys()
    value = webpush.vapid_authorization(FCM + "abc", private, public, now=1_000_000)
    match = re.fullmatch(r"vapid t=([^,]+), k=([A-Za-z0-9_-]+)", value)
    assert match
    token, k = match.groups()
    raw = webpush.b64u_decode(k)
    assert len(raw) == 65 and raw[0] == 4 and raw == public
    key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), raw)
    assert jwt.get_unverified_header(token) == {"typ": "JWT", "alg": "ES256"}
    claims = jwt.decode(
        token, key, algorithms=["ES256"], audience="https://fcm.googleapis.com",
        options={"verify_exp": False},
    )
    assert claims == {
        "aud": "https://fcm.googleapis.com", "exp": 1_000_000 + 12 * 3600, "sub": SITE,
    }
    # A different key does not verify it.
    other = ec.generate_private_key(ec.SECP256R1()).public_key()
    with pytest.raises(jwt.InvalidSignatureError):
        jwt.decode(token, other, algorithms=["ES256"], options={"verify_aud": False,
                                                                 "verify_exp": False})


def test_vapid_keys_are_made_once_and_kept(client):
    assert webpush.ensure_vapid_keys() is True
    first = db.get_vapid_keys()
    assert webpush.ensure_vapid_keys() is False
    assert db.get_vapid_keys() == first
    assert len(webpush.b64u_decode(first["private"])) == 32


# ---- subscribe, unsubscribe, key ------------------------------------------

def browser_keys():
    """A browser's side of a subscription: its key pair and auth secret."""
    key = ec.generate_private_key(ec.SECP256R1())
    raw = key.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    return key, webpush.b64u_encode(raw), webpush.b64u_encode(b"0123456789abcdef")


def sub_body(endpoint, p256dh=None, auth=None):
    _, pub, secret = browser_keys()
    return {"endpoint": endpoint,
            "keys": {"p256dh": pub if p256dh is None else p256dh,
                     "auth": secret if auth is None else auth}}


@pytest.fixture
def ready(monkeypatch):
    monkeypatch.setattr(webpush, "SITE_URL", SITE)


def viewer(name="nell"):
    add_user(name)
    browser = make_client()
    login(browser, name)
    return browser


def test_signed_out_cannot_touch_push(client, ready):
    assert client.get("/api/push/key").status_code == 401
    assert client.post("/api/push/subscribe", json=sub_body(FCM + "a")).status_code == 401
    assert client.post("/api/push/unsubscribe", json={"endpoint": FCM + "a"}).status_code == 401
    assert db.list_push_subscriptions() == []


def test_key_is_the_stored_public_key_and_nothing_private(client, ready):
    browser = viewer()
    reply = browser.get("/api/push/key").json()
    assert reply["ready"] is True
    stored = db.get_vapid_keys()
    assert reply["key"] == stored["public"]
    assert stored["private"] not in json.dumps(reply)
    assert browser.get("/api/push/key").json()["key"] == reply["key"]


def test_key_says_not_ready_without_a_site_url(client, monkeypatch):
    monkeypatch.setattr(webpush, "SITE_URL", "")
    browser = viewer()
    assert browser.get("/api/push/key").json() == {"ready": False, "key": ""}
    assert browser.post("/api/push/subscribe", json=sub_body(FCM + "a")).status_code == 503


@pytest.mark.parametrize("endpoint", [
    "http://fcm.googleapis.com/fcm/send/a",                 # not https
    "https://example.com/push/a",                           # unknown host
    "https://fcm.googleapis.com.evil.example/a",            # suffix trick
    "https://evilfcm.googleapis.com/a",                     # not the exact host
    "https://fcm.googleapis.com@evil.example/a",            # userinfo trick
    "https://user@fcm.googleapis.com/a",                    # any userinfo
    "https://fcm.googleapis.com:8443/a",                    # another port
    "https://127.0.0.1/a",
    "https://push.apple.com.evil.example/a",
    "https://fcm.googleapis.com/" + "a" * 1024,             # too long
    # urlsplit takes these; httpx would refuse them at send time.
    "https://fcm.googleapis.com/fcm/send/x\x7f",
    "https://evil\t.push.apple.com/x",
    "https://a\u200b.push.apple.com/x",
    "",
    None,
    42,
])
def test_subscribe_refuses_endpoints_off_the_allowlist(client, ready, endpoint):
    browser = viewer()
    body = sub_body("x")
    body["endpoint"] = endpoint
    assert browser.post("/api/push/subscribe", json=body).status_code == 400
    assert db.list_push_subscriptions() == []


@pytest.mark.parametrize("endpoint", [
    FCM + "abc",
    "https://updates.push.services.mozilla.com/wpush/v2/abc",
    "https://web.push.apple.com/abc",
    "https://wns2-par02p.notify.windows.com/w/?token=abc",
])
def test_subscribe_accepts_each_known_push_service(client, ready, endpoint):
    browser = viewer()
    assert browser.post("/api/push/subscribe", json=sub_body(endpoint)).status_code == 200
    assert [s["endpoint"] for s in db.list_push_subscriptions("nell")] == [endpoint]


def test_subscribe_refuses_bad_keys(client, ready):
    browser = viewer()
    _, good_pub, good_auth = browser_keys()
    raw = webpush.b64u_decode(good_pub)
    bad = [
        {"p256dh": webpush.b64u_encode(raw[:64])},                 # 64 bytes
        {"p256dh": webpush.b64u_encode(b"\x05" + raw[1:])},        # not 0x04
        {"p256dh": webpush.b64u_encode(b"\x04" + b"\x01" * 64)},   # off the curve
        {"p256dh": good_pub + "!"},                                # not base64url
        {"auth": webpush.b64u_encode(b"x" * 15)},                  # 15 bytes
        {"auth": webpush.b64u_encode(b"x" * 17)},                  # 17 bytes
        {"auth": ""},
    ]
    for change in bad:
        keys = {"p256dh": good_pub, "auth": good_auth, **change}
        body = {"endpoint": FCM + "a", "keys": keys}
        assert browser.post("/api/push/subscribe", json=body).status_code == 400, change
    assert browser.post("/api/push/subscribe", json={"endpoint": FCM + "a"}).status_code == 400
    assert browser.post("/api/push/subscribe", json=[1]).status_code == 400
    assert db.list_push_subscriptions() == []


def test_subscribe_from_another_site_is_refused(client, ready):
    browser = viewer()
    for headers in ({"Origin": "https://evil.example"}, {"Sec-Fetch-Site": "cross-site"}):
        reply = browser.post("/api/push/subscribe", json=sub_body(FCM + "a"), headers=headers)
        assert reply.status_code == 403
        reply = browser.post("/api/push/unsubscribe", json={"endpoint": FCM + "a"},
                             headers=headers)
        assert reply.status_code == 403
    assert db.list_push_subscriptions() == []


def test_subscribe_is_rate_limited(client, ready):
    browser = viewer()
    codes = [browser.post("/api/push/subscribe", json=sub_body(FCM + str(i))).status_code
             for i in range(21)]
    assert codes[:20] == [200] * 20
    assert codes[20] == 429


def test_each_account_keeps_its_newest_ten(client):
    for i in range(11):
        _, pub, secret = browser_keys()
        db.add_push_subscription("nell", f"{FCM}{i}", pub, secret, 1000 + i)
    kept = [s["endpoint"] for s in db.list_push_subscriptions("nell")]
    assert len(kept) == 10
    assert f"{FCM}0" not in kept and f"{FCM}10" in kept
    # Somebody else's devices are not counted against it.
    _, pub, secret = browser_keys()
    db.add_push_subscription("tom", FCM + "t", pub, secret, 5000)
    assert len(db.list_push_subscriptions("nell")) == 10


def test_an_endpoint_moves_to_whoever_subscribes_it(client, ready):
    nell, tom = viewer("nell"), viewer("tom")
    body = sub_body(FCM + "shared")
    assert nell.post("/api/push/subscribe", json=body).status_code == 200
    assert tom.post("/api/push/subscribe", json=body).status_code == 200
    rows = db.list_push_subscriptions()
    assert [(r["username"], r["endpoint"]) for r in rows] == [("tom", FCM + "shared")]


def test_unsubscribe_removes_only_your_own(client, ready):
    nell, tom = viewer("nell"), viewer("tom")
    assert nell.post("/api/push/subscribe", json=sub_body(FCM + "n")).status_code == 200
    reply = tom.post("/api/push/unsubscribe", json={"endpoint": FCM + "n"})
    assert reply.json() == {"ok": True, "removed": False}
    assert len(db.list_push_subscriptions("nell")) == 1
    reply = nell.post("/api/push/unsubscribe", json={"endpoint": FCM + "n"})
    assert reply.json() == {"ok": True, "removed": True}
    assert db.list_push_subscriptions() == []


def test_deleting_an_account_deletes_its_subscriptions(client):
    add_user("nell")
    _, pub, secret = browser_keys()
    db.add_push_subscription("nell", FCM + "n", pub, secret, 1)
    assert db.delete_user("nell")
    assert db.list_push_subscriptions() == []


# ---- sending --------------------------------------------------------------

class Service:
    """Stands in for every push service: records each request and answers with
    the status set per endpoint (201 by default)."""

    def __init__(self):
        self.requests = []
        self.status = {}

    def handler(self, request):
        self.requests.append(request)
        return httpx.Response(self.status.get(str(request.url), 201))


@pytest.fixture
def service(monkeypatch, ready):
    fake = Service()
    monkeypatch.setattr(webpush, "_client", lambda: httpx.AsyncClient(
        transport=httpx.MockTransport(fake.handler), follow_redirects=False))
    return fake


def subscribe(username, endpoint):
    """Save a subscription straight to the database; returns the browser's
    private key and auth secret so a test can read what was sent to it."""
    key, pub, secret = browser_keys()
    db.add_push_subscription(username, endpoint, pub, secret, 1)
    return key, webpush.b64u_decode(secret)


def run(coro):
    import asyncio
    return asyncio.run(coro)


def test_a_gone_subscription_is_pruned_and_a_failing_one_kept(client, service):
    for name in ("ok", "gone", "missing", "broken"):
        subscribe("nell", FCM + name)
    service.status[FCM + "gone"] = 410
    service.status[FCM + "missing"] = 404
    service.status[FCM + "broken"] = 500
    result = run(webpush.send(db.list_push_subscriptions(), {"title": "t", "body": "b"}))
    assert result == {"sent": 1, "failed": 3}
    left = {s["endpoint"] for s in db.list_push_subscriptions()}
    assert left == {FCM + "ok", FCM + "broken"}


BAD_ENDPOINTS = ("https://fcm.googleapis.com/fcm/send/x\x7f",
                 "https://evil\t.push.apple.com/x",
                 "https://a\u200b.push.apple.com/x")


def store_bad_rows():
    """Rows that never passed the subscribe check, put straight in the table."""
    _, pub, secret = browser_keys()
    with db.connect() as conn:
        for i, endpoint in enumerate(BAD_ENDPOINTS):
            conn.execute(
                "INSERT INTO push_subscriptions "
                "(username, endpoint, p256dh, auth, created_at) VALUES (?, ?, ?, ?, ?)",
                ("nell", endpoint, pub, secret, i),
            )


def test_a_bad_stored_row_never_stops_the_rest(client, service):
    subscribe("nell", FCM + "first")
    store_bad_rows()
    subscribe("tom", FCM + "last")
    result = run(webpush.send(db.list_push_subscriptions(), {"title": "t", "body": "b"}))
    assert result["sent"] == 2
    assert sorted(str(r.url) for r in service.requests) == [FCM + "first", FCM + "last"]


def test_a_row_httpx_refuses_counts_as_failed(client, service, monkeypatch):
    # Even past the allowlist, a URL httpx will not send to is one failure.
    monkeypatch.setattr(webpush, "endpoint_allowed", lambda endpoint: True)
    subscribe("nell", FCM + "first")
    store_bad_rows()
    subscribe("tom", FCM + "last")
    result = run(webpush.send(db.list_push_subscriptions(), {"title": "t", "body": "b"}))
    assert result == {"sent": 2, "failed": 3}


def test_an_unforeseen_error_on_one_row_counts_as_failed(client, service, monkeypatch):
    real = webpush.encrypt
    boom = subscribe("nell", FCM + "boom")[0].public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)

    def encrypt(data, ua_public, auth_secret):
        if ua_public == boom:
            raise RuntimeError("unexpected")
        return real(data, ua_public, auth_secret)

    monkeypatch.setattr(webpush, "encrypt", encrypt)
    subscribe("tom", FCM + "fine")
    result = run(webpush.send(db.list_push_subscriptions(), {"title": "t", "body": "b"}))
    assert result == {"sent": 1, "failed": 1}
    assert [str(r.url) for r in service.requests] == [FCM + "fine"]


@pytest.fixture
def logs(caplog):
    """The gate's log lines. Its logger does not propagate to the root, where
    caplog listens, so the handler is attached to it directly."""
    gate_logger = logging.getLogger("upperroom")
    gate_logger.addHandler(caplog.handler)
    yield caplog
    gate_logger.removeHandler(caplog.handler)


def test_a_failed_push_logs_the_host_and_never_the_endpoint(client, service, logs):
    subscribe("nell", FCM + "secret-token-123")
    service.status[FCM + "secret-token-123"] = 500
    run(webpush.send(db.list_push_subscriptions(), {"title": "t", "body": "b"}))
    assert "fcm.googleapis.com" in logs.text and "500" in logs.text
    assert "secret-token-123" not in logs.text


def test_go_live_pushes_once_to_every_subscription(client, service):
    setup_admin(client, channel="Northwind Live")
    db.set_stream_info(title="Friday night")
    db.set_now_playing("Tetris")
    readers = {FCM + n: subscribe(n, FCM + n) for n in ("owner", "nell", "tom")}
    run(notify.notify_live())
    assert sorted(str(r.url) for r in service.requests) == sorted(readers)
    for request in service.requests:
        assert request.headers["content-encoding"] == "aes128gcm"
        assert request.headers["content-type"] == "application/octet-stream"
        assert request.headers["ttl"] == "1800"
        assert request.headers["urgency"] == "high"
        assert request.headers["topic"] == "live"
        assert request.headers["authorization"].startswith("vapid t=")
        key, secret = readers[str(request.url)]
        payload = json.loads(decrypt(request.content, key, secret))
        assert payload == {"title": "Northwind Live is live",
                           "body": "Friday night\nPlaying Tetris"}


def test_go_live_respects_the_cooldown(client, service):
    subscribe("nell", FCM + "n")
    run(notify.notify_live())
    run(notify.notify_live())
    assert len(service.requests) == 1


def test_go_live_respects_the_switch(client, service):
    subscribe("nell", FCM + "n")
    db.set_notify_on_live(False)
    run(notify.notify_live())
    assert service.requests == []
    db.mark_notified(0)
    db.set_notify_on_live(True)
    run(notify.notify_live())
    assert len(service.requests) == 1


def test_go_live_sends_nothing_without_a_site_url(client, service, monkeypatch):
    monkeypatch.setattr(webpush, "SITE_URL", "")
    subscribe("nell", FCM + "n")
    run(notify.notify_live())
    assert service.requests == []


def test_payload_without_a_game_or_title(client):
    db.set_stream_info(site_name="Northwind Live")
    with db.connect() as conn:
        conn.execute("UPDATE channel_settings SET stream_title = ''")
    assert notify.live_payload() == {"title": "Northwind Live is live", "body": "Live now."}


def test_payload_drops_the_game_during_a_theater_session(client):
    db.set_stream_info(site_name="Northwind Live", title="Film night")
    db.set_now_playing("Tetris")
    assert notify.live_payload()["body"] == "Film night\nPlaying Tetris"
    assert db.create_theater_session(1000)
    assert notify.live_payload() == {"title": "Northwind Live is live", "body": "Film night"}


# ---- the dashboard --------------------------------------------------------

def test_admin_notify_reports_and_toggles(client, monkeypatch):
    setup_admin(client)
    monkeypatch.setattr(webpush, "SITE_URL", "")
    assert client.get("/api/admin/notify").json() == {
        "on": True, "devices": 0, "accounts": 0, "ready": False,
    }
    monkeypatch.setattr(webpush, "SITE_URL", SITE)
    subscribe("owner", FCM + "o")
    subscribe("nell", FCM + "n1")
    subscribe("nell", FCM + "n2")
    assert client.post("/api/admin/notify", json={"on": False}).json() == {
        "ok": True, "on": False,
    }
    reply = client.get("/api/admin/notify").json()
    assert reply == {"on": False, "devices": 3, "accounts": 2, "ready": True}


def test_test_send_reaches_only_the_admins_devices(client, service):
    setup_admin(client, channel="Northwind Live")
    mine = {FCM + "o1": subscribe("owner", FCM + "o1"),
            FCM + "o2": subscribe("owner", FCM + "o2")}
    subscribe("nell", FCM + "n1")
    subscribe("nell", FCM + "n2")
    db.set_notify_on_live(False)               # a quiet channel can still test
    reply = client.post("/api/admin/notify/test")
    assert reply.json() == {"sent": 2, "failed": 0, "devices": 2}
    assert sorted(str(r.url) for r in service.requests) == sorted(mine)
    key, secret = mine[str(service.requests[0].url)]
    payload = json.loads(decrypt(service.requests[0].content, key, secret))
    assert payload["title"] == "Test from Northwind Live"
    assert db.get_notify_settings()["last_notified_at"] == 0


def test_test_send_with_no_devices_says_so(client, service):
    setup_admin(client)
    assert client.post("/api/admin/notify/test").json() == {
        "sent": 0, "failed": 0, "devices": 0,
    }
    assert service.requests == []


def test_test_send_is_rate_limited_and_admin_only(client, service):
    setup_admin(client)
    browser = viewer()
    assert browser.post("/api/admin/notify/test").status_code == 403
    codes = [client.post("/api/admin/notify/test").status_code for _ in range(6)]
    assert codes == [200] * 5 + [429]


# ---- startup: what the old notices left behind ----------------------------

def make_legacy(conn):
    """Give a fresh database the columns an older install still carries."""
    conn.execute("ALTER TABLE users ADD COLUMN email TEXT NOT NULL DEFAULT ''")
    conn.execute("ALTER TABLE users ADD COLUMN notify_live INTEGER NOT NULL DEFAULT 1")
    conn.execute(
        "ALTER TABLE channel_settings ADD COLUMN discord_webhook TEXT NOT NULL DEFAULT ''")


def test_startup_clears_old_addresses_and_webhook_once(client, logs):
    add_user("nell")
    add_user("tom")
    add_user("ada")
    with db.connect() as conn:
        make_legacy(conn)
        conn.execute("UPDATE users SET email = 'nell@example.com' WHERE username = 'nell'")
        conn.execute("UPDATE users SET email = 'tom@example.com' WHERE username = 'tom'")
        conn.execute("UPDATE channel_settings SET discord_webhook = "
                     "'https://hooks.example.com/secret-hook'")
    main._startup_notify_pass()
    assert "cleared 2 stored email address(es) and 1 webhook URL(s)" in logs.text
    assert "generated the push (VAPID) key pair" in logs.text
    for value in ("nell@example.com", "tom@example.com", "secret-hook",
                  db.get_vapid_keys()["private"]):
        assert value not in logs.text
    with db.connect() as conn:
        assert {r[0] for r in conn.execute("SELECT email FROM users")} == {""}
        assert conn.execute(
            "SELECT discord_webhook FROM channel_settings").fetchone()[0] == ""
    # Again: nothing left to clear, the key pair stays, and nothing is logged.
    keys = db.get_vapid_keys()
    logs.clear()
    main._startup_notify_pass()
    assert logs.text == ""
    assert db.clear_legacy_contacts() == (0, 0)
    assert db.get_vapid_keys() == keys


def test_startup_clear_is_harmless_on_a_fresh_database(client):
    assert db.clear_legacy_contacts() == (0, 0)


def test_the_go_live_switch_carries_over_from_the_old_email_switch(client):
    # An older install that had turned the go-live email off stays quiet.
    with db.connect() as conn:
        conn.execute("ALTER TABLE channel_settings DROP COLUMN notify_on_live")
        conn.execute("ALTER TABLE channel_settings "
                     "ADD COLUMN email_on_live INTEGER NOT NULL DEFAULT 1")
        conn.execute("UPDATE channel_settings SET email_on_live = 0")
    db.init_db()
    assert db.get_notify_settings()["notify_on_live"] == 0
    # Only once: turning it on afterwards sticks across restarts.
    db.set_notify_on_live(True)
    db.init_db()
    assert db.get_notify_settings()["notify_on_live"] == 1


def test_me_carries_no_email(client):
    browser = viewer()
    me = browser.get("/api/me").json()
    assert "email" not in me and "notify_live" not in me


# ---- pin: the old channels stay gone ---------------------------------------

GATE = pathlib.Path(__file__).resolve().parents[1]
WEB = GATE.parent / "web"
OLD = re.compile(r"e-?mail|smtp|discord", re.I)
# The two places that still name the old columns on purpose: the startup step
# that empties them, and the one-time carry-over of the old switch.
ALLOWED_FUNCTIONS = {("db.py", "clear_legacy_contacts"), ("main.py", "_startup_notify_pass")}
ALLOWED_STRINGS = {("db.py", "email_on_live"),
                   ("db.py", "UPDATE channel_settings SET notify_on_live = email_on_live")}


def _python_hits(path):
    """Names and non-docstring strings in a module that mention the old
    channels, skipping the allowed functions. Comments are not in the tree."""
    tree = ast.parse(path.read_text())
    docstrings = set()
    skip = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                docstrings.add(id(body[0].value))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if (path.name, node.name) in ALLOWED_FUNCTIONS:
                skip.update(id(n) for n in ast.walk(node))
    hits = []
    for node in ast.walk(tree):
        if id(node) in skip or id(node) in docstrings:
            continue
        text = None
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            text = node.value
            if path.name == "db.py" and "CREATE TABLE" in text:
                # The schema's SQL comments may name the legacy columns.
                text = re.sub(r"--[^\n]*", "", text)
            if (path.name, text) in ALLOWED_STRINGS:
                continue
        elif isinstance(node, ast.Name):
            text = node.id
        elif isinstance(node, ast.Attribute):
            text = node.attr
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            text = node.name
        elif isinstance(node, ast.arg):
            text = node.arg
        elif isinstance(node, ast.keyword):
            text = node.arg
        if text and OLD.search(text):
            hits.append(f"{path.name}:{getattr(node, 'lineno', '?')}: {text[:60]!r}")
    return hits


def test_no_code_reads_email_smtp_or_discord_again():
    hits = []
    for path in sorted(list(GATE.glob("*.py")) + list(GATE.glob("routes/*.py"))):
        if path.name == "changelog.py":
            continue    # release notes for viewers may say that email is gone
        hits += _python_hits(path)
    for path in sorted(list(WEB.glob("*.html")) + list(WEB.glob("assets/*.js"))
                       + list(WEB.glob("assets/*.css")) + list(WEB.glob("*.js"))):
        text = path.read_text()
        text = re.sub(r"<!--.*?-->|/\*.*?\*/", "", text, flags=re.S)
        text = re.sub(r"(?m)^\s*//.*$", "", text)
        for number, line in enumerate(text.splitlines(), 1):
            if OLD.search(line):
                hits.append(f"{path.name}: {line.strip()[:80]}")
    assert hits == []
