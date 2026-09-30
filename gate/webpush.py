"""
Web Push for the go-live notice.

Message encryption is RFC 8291 (the "aes128gcm" coding of RFC 8188, one
record), and the server identifies itself with RFC 8292 VAPID. Messages go
straight to the browser maker's push service over httpx.

cryptography is the one extra dependency: P-256 ECDH, HKDF, AES-GCM and ES256
are not in the standard library, and pyjwt signs ES256 once it is installed.
pywebpush was passed over because it adds requests, py_vapid and http_ece on
top of it.
"""

import asyncio
import base64
import json
import logging
import os
import re
import struct
import time
from urllib.parse import urlsplit

import httpx
import jwt
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

import db
from config import SITE_URL

logger = logging.getLogger("upperroom.webpush")

RECORD_SIZE = 4096
# The most plaintext one 4096 byte record carries (RFC 8291 section 4).
MAX_PLAINTEXT = 3993
TTL_SECONDS = 1800
VAPID_LIFETIME = 12 * 3600
MAX_ENDPOINT = 1024
# How many sends run at once.
CONCURRENCY = 4

# The push services of the browsers people use: Chrome, Edge, Brave and
# friends (Google), Firefox (Mozilla), Safari and iOS (Apple), and Edge on
# Windows (Microsoft). The server only ever posts to these, so a stored
# endpoint can never point it at anything else.
_EXACT_HOSTS = ("fcm.googleapis.com",)
_HOST_SUFFIXES = (
    ".push.services.mozilla.com", ".push.apple.com", ".notify.windows.com",
)

_B64URL = re.compile(r"^[A-Za-z0-9_-]+={0,2}$")


def b64u_encode(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def b64u_decode(text):
    """Strict base64url, padded or not. ValueError on anything else."""
    text = str(text or "").strip()
    if not _B64URL.match(text):
        raise ValueError("not base64url")
    text = text.rstrip("=")
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _public_raw(key):
    return key.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )


def private_key_from_raw(raw):
    return ec.derive_private_key(int.from_bytes(raw, "big"), ec.SECP256R1())


def _hkdf(salt, ikm, info, length):
    return HKDF(
        algorithm=hashes.SHA256(), length=length, salt=salt, info=info
    ).derive(ikm)


def encrypt(plaintext, ua_public, auth_secret, salt=None, server_key=None):
    """One push message body: the aes128gcm header (salt, record size, the
    server's one-off public key) followed by the single encrypted record.
    salt and server_key are fresh for every message; they are parameters only
    so the RFC 8291 example can be reproduced."""
    if len(plaintext) > MAX_PLAINTEXT:
        raise ValueError("push payload too large")
    salt = os.urandom(16) if salt is None else salt
    server_key = server_key or ec.generate_private_key(ec.SECP256R1())
    as_public = _public_raw(server_key)
    ua_key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_public)
    ecdh_secret = server_key.exchange(ec.ECDH(), ua_key)
    ikm = _hkdf(auth_secret, ecdh_secret,
                b"WebPush: info\x00" + ua_public + as_public, 32)
    cek = _hkdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = _hkdf(salt, ikm, b"Content-Encoding: nonce\x00", 12)
    # 0x02 marks the last (and only) record; no padding after it.
    record = AESGCM(cek).encrypt(nonce, plaintext + b"\x02", None)
    header = salt + struct.pack("!IB", RECORD_SIZE, len(as_public)) + as_public
    return header + record


def endpoint_allowed(endpoint):
    """Whether an endpoint is an https URL on one of the known push services."""
    if not isinstance(endpoint, str) or len(endpoint) > MAX_ENDPOINT:
        return False
    # Plain printable ASCII only: urlsplit lets control and invisible
    # characters through that httpx would refuse at send time.
    if not endpoint.isascii() or any(ord(c) <= 0x20 or ord(c) == 0x7F for c in endpoint):
        return False
    try:
        parts = urlsplit(endpoint)
        port = parts.port
    except ValueError:
        return False
    if parts.scheme != "https" or parts.username or parts.password:
        return False
    if port not in (None, 443):
        return False
    host = (parts.hostname or "").lower()
    if not (host in _EXACT_HOSTS or any(host.endswith(s) for s in _HOST_SUFFIXES)):
        return False
    # And httpx, which does the sending, must read the same host.
    try:
        return httpx.URL(endpoint).host == host
    except httpx.InvalidURL:
        return False


def check_subscription(endpoint, p256dh, auth):
    """None when a subscription from a browser is usable, else why not."""
    if not endpoint_allowed(endpoint):
        return "That push service is not one this server sends to."
    try:
        ua_public = b64u_decode(p256dh)
        secret = b64u_decode(auth)
    except (ValueError, TypeError):
        return "The subscription keys are not valid."
    if len(ua_public) != 65 or ua_public[0] != 4 or len(secret) != 16:
        return "The subscription keys are not valid."
    try:
        ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_public)
    except ValueError:
        return "The subscription keys are not valid."
    return None


# ---- VAPID ------------------------------------------------------------------

def ensure_vapid_keys():
    """Make the server's VAPID key pair if the database has none. True when it
    was made just now. The private half stays in the database."""
    if db.get_vapid_keys():
        return False
    key = ec.generate_private_key(ec.SECP256R1())
    private = key.private_numbers().private_value.to_bytes(32, "big")
    return db.set_vapid_keys(b64u_encode(private), b64u_encode(_public_raw(key)))


def vapid_keys():
    """(private key, raw public key), made on first use."""
    ensure_vapid_keys()
    stored = db.get_vapid_keys()
    return (private_key_from_raw(b64u_decode(stored["private"])),
            b64u_decode(stored["public"]))


def public_key():
    """The public key browsers subscribe with, base64url."""
    return b64u_encode(vapid_keys()[1])


def vapid_authorization(endpoint, private_key, public_raw, now=None):
    """The Authorization header value for one push service."""
    parts = urlsplit(endpoint)
    claims = {
        "aud": f"{parts.scheme}://{parts.netloc}",
        "exp": int(time.time() if now is None else now) + VAPID_LIFETIME,
        "sub": SITE_URL,
    }
    token = jwt.encode(claims, private_key, algorithm="ES256",
                       headers={"typ": "JWT"})
    return f"vapid t={token}, k={b64u_encode(public_raw)}"


# ---- sending ----------------------------------------------------------------

def ready():
    """Push needs SITE_URL: it is the contact the VAPID token names."""
    return bool(SITE_URL)


def _client():
    return httpx.AsyncClient(timeout=10, follow_redirects=False)


async def _send_one(client, gate, sub, data, auth_header):
    """Post one message. True on success. An endpoint URL is itself a
    credential, so only its host is ever logged."""
    endpoint = sub["endpoint"]
    host = urlsplit(endpoint).hostname or "?"
    try:
        body = encrypt(data, b64u_decode(sub["p256dh"]), b64u_decode(sub["auth"]))
        async with gate:
            reply = await client.post(endpoint, content=body, headers={
                "Authorization": auth_header,
                "Content-Encoding": "aes128gcm",
                "Content-Type": "application/octet-stream",
                "TTL": str(TTL_SECONDS),
                "Urgency": "high",
                "Topic": "live",
            })
    except (httpx.HTTPError, httpx.InvalidURL, ValueError) as exc:
        logger.warning("push to %s failed: %s", host, type(exc).__name__)
        return False
    if 200 <= reply.status_code < 300:
        return True
    if reply.status_code in (404, 410):
        db.delete_push_subscription(endpoint)
        logger.info("push to %s: subscription gone, removed", host)
    else:
        logger.warning("push to %s answered %s", host, reply.status_code)
    return False


async def send(subscriptions, payload):
    """Send one payload (a dict, sent as JSON) to each subscription. Returns
    {"sent": n, "failed": n}. Endpoints off the allowlist are skipped."""
    subs = [s for s in subscriptions if endpoint_allowed(s["endpoint"])]
    if not subs or not ready():
        return {"sent": 0, "failed": len(subs)}
    data = json.dumps(payload, separators=(",", ":")).encode()
    private, public = vapid_keys()
    now = int(time.time())
    headers = {}
    for sub in subs:
        aud = urlsplit(sub["endpoint"]).netloc
        if aud not in headers:
            headers[aud] = vapid_authorization(sub["endpoint"], private, public, now)
    gate = asyncio.Semaphore(CONCURRENCY)
    # return_exceptions: one row that fails in a way nobody foresaw counts as
    # failed and never stops the rest of the batch.
    async with _client() as client:
        results = await asyncio.gather(*(
            _send_one(client, gate, sub, data,
                      headers[urlsplit(sub["endpoint"]).netloc])
            for sub in subs
        ), return_exceptions=True)
    for result in results:
        if isinstance(result, BaseException):
            logger.warning("push failed: %s", type(result).__name__)
    sent = sum(1 for ok in results if ok is True)
    return {"sent": sent, "failed": len(results) - sent}
