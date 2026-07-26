"""
A small self-contained human check for the public guest redemption form.

Deliberately no third party: no captcha widget, no external script, nothing that
phones home. That rules out anything that leans on someone else's risk scoring,
so this is a plain question the server asks and then verifies.

Be honest about what that buys. A stateless challenge cannot stop a determined
script, because the answer space is small and the question is machine readable;
somebody who wants in badly enough will write ten lines and solve it. What it
does stop is undirected drive-by automation, which is the traffic a public
endpoint actually attracts. The load-bearing defences elsewhere are the per
address rate limit on redemption and the size of the code space; this sits in
front of both so neither gets exercised by accident.

The challenge carries its own state in a signed token rather than a server-side
store, so nothing needs cleaning up and a restart does not invalidate a form
somebody has open. The token holds an HMAC of the expected answer, never the
answer itself, so reading the token does not hand it over.
"""

import hmac
import secrets
import time
from hashlib import sha256

import jwt

from config import JWT_SECRET

# How long a question stays answerable. Long enough to type a code and a name
# without hurrying, short enough that a harvested token is not reusable later.
CHALLENGE_TTL = 600

# A ceiling on what we will even look at, so the verify path cannot be handed a
# megabyte to hash.
MAX_ANSWER_LENGTH = 40

_NUMBER_WORDS = (
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight",
    "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen",
    "sixteen", "seventeen", "eighteen",
)

# Ordinary, concrete words for the "which word" question. Kept separate from the
# invite wordlist so a guess at one is not a guess at the other.
_PICK_WORDS = (
    "river", "candle", "window", "orange", "pillow", "garden", "pocket",
    "silver", "meadow", "lantern", "basket", "harbor", "feather", "marble",
    "kettle", "ribbon", "anchor", "blanket", "compass", "walnut",
)

_ORDINALS = ("first", "second", "third")


def _normalize(answer):
    """Lowercase, trimmed, inner whitespace collapsed. A number typed as a word
    becomes its digits, so "seven" and "7" are the same answer: people type
    whichever the question put in their head."""
    text = " ".join((answer or "").strip().lower().split())[:MAX_ANSWER_LENGTH]
    if text in _NUMBER_WORDS:
        return str(_NUMBER_WORDS.index(text))
    return text


def _seal(answer):
    """An HMAC of the expected answer under the server secret. Stored in the
    token in place of the answer, so the token cannot simply be read for it."""
    return hmac.new(
        JWT_SECRET.encode(), _normalize(answer).encode(), sha256
    ).hexdigest()


def new_challenge():
    """Return (question, token). The question is shown to the person; the token
    comes back with their answer."""
    if secrets.choice((True, False)):
        # A small sum, worded rather than written in digits.
        a = secrets.randbelow(8) + 1
        b = secrets.randbelow(8) + 1
        question = (
            f"What is {_NUMBER_WORDS[a]} plus {_NUMBER_WORDS[b]}?"
        )
        answer = str(a + b)
    else:
        # Pick a word out of a short list by position.
        words = []
        while len(words) < 3:
            word = secrets.choice(_PICK_WORDS)
            if word not in words:
                words.append(word)
        index = secrets.randbelow(3)
        question = (
            f"Type the {_ORDINALS[index]} of these words: "
            + ", ".join(words)
        )
        answer = words[index]

    now = int(time.time())
    token = jwt.encode(
        {"a": _seal(answer), "iat": now, "exp": now + CHALLENGE_TTL},
        JWT_SECRET,
        algorithm="HS256",
    )
    return question, token


def check_challenge(token, answer):
    """Whether this answer matches this token. False for anything unusable: a
    missing, malformed, expired or re-signed token, or a wrong answer. A token
    is single-question, not single-use; the rate limiter is what stops one being
    replayed, because making it single-use would need the server-side store this
    design exists to avoid."""
    if not token or not answer:
        return False
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
    except jwt.PyJWTError:
        return False
    expected = payload.get("a")
    if not isinstance(expected, str):
        return False
    return hmac.compare_digest(expected, _seal(answer))
