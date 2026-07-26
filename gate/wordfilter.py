"""The chat word filter: the admin's banned-words list, and the matching rule.

Matching is WHOLE WORD, not substring, and that is the whole point of this
module. The obvious implementation, `word in message`, is a trap with a name:
the Scunthorpe problem, after the English town whose residents could not sign up
to an early ISP because a rude word is buried in the middle of it. Banning "ass"
that way also blocks class, pass, grass, assist and embarrass; "cum" blocks
document and cucumber; "anal" blocks analysis; "tit" blocks title. On a channel
whose banned list contains the usual words, that is a guaranteed stream of
viewers who cannot say ordinary things and have no idea why.

So a listed word matches only when it stands alone, with a small set of endings
allowed so one entry still catches the obvious inflections: "fuck" catches
fucking, fucked and fucker without "ass" reaching assist.

The deliberate limit: this does NOT chase evasion. "f u c k" spaced out, "fuuuck"
stretched, and "sh!t" punched through with symbols all get past it. That is a
trade made on purpose. A false positive is a viewer silently unable to say
"class", which they will never understand and never report; a false negative is
one message a moderator removes in two seconds. Chasing spacing and leetspeak
costs far more false positives than it prevents misses, and the channel has
moderators.
"""

import re
from functools import lru_cache

# Endings allowed after a listed word. Deliberately short. Every addition here
# widens what an entry reaches, and the reason "ass" does not match "assist" is
# precisely that "ist" is not in this set.
_SUFFIXES = r"(?:s|es|ed|ing|er|ers|ies|y)?"

# Neither side of a match may be a letter or digit. This is `\b` in spirit, but
# spelled out so entries ending in punctuation behave predictably too.
_LEFT = r"(?<![a-z0-9])"
_RIGHT = r"(?![a-z0-9])"

# The starting list for a new channel. Ordinary profanity plus the slurs a
# public-facing chat should refuse by default. An operator owns this list
# completely from the dashboard: it can be edited down to nothing, and an
# existing channel is never given it retroactively.
#
# Two notes on what is deliberately ABSENT. Mild words that are far more often
# ordinary speech than profanity ("damn", "hell", "crap", "sex") are left out,
# because whole-word matching still cannot tell "what the hell was that play"
# from swearing, and a stream's chat is casual. Any operator who wants them adds
# one line each. Words whose inflection drops a letter are listed twice where it
# matters ("rape" does not reach "raping" through the suffix rule above).
DEFAULT_BANNED_WORDS = "\n".join(
    (
        # ---- general profanity ----
        "fuck", "fuk", "fuc", "motherfucker", "mofo", "bullshit", "horseshit",
        "shit", "shite", "bitch", "bastard", "cunt", "prick", "twat",
        "wank", "wanker", "bollocks", "arse", "arsehole", "asshole", "ass",
        "jackass", "dumbass", "dipshit", "dickhead", "douche", "douchebag",
        "piss", "pissed", "goddamn", "goddamnit", "godammit",
        # ---- sexual ----
        "dick", "cock", "pussy", "whore", "slut", "skank", "cum", "jizz",
        "blowjob", "handjob", "rimjob", "dildo", "boner", "tits", "titties",
        "porn", "pornhub", "hentai", "milf", "fap", "creampie", "gangbang",
        # ---- slurs: racial and ethnic ----
        "nigger", "nigga", "niggas", "niggers", "kike", "spic", "chink",
        "gook", "wetback", "beaner", "coon", "raghead", "towelhead", "paki",
        "jap", "gypsy", "cracker",
        # ---- slurs: sexuality, gender, disability ----
        "faggot", "fag", "fags", "dyke", "tranny", "shemale", "homo",
        "retard", "retarded", "spastic", "mongoloid", "midget",
        # ---- harassment, violence, exploitation ----
        "kys", "killyourself", "rape", "raping", "rapist", "molest",
        "molester", "pedo", "pedophile", "paedophile", "nonce", "groomer",
    )
)


def _word_pattern(word):
    """One listed entry as a regex fragment: the entry itself, optionally with
    its final letter doubled (so "shit" reaches "shitting"), optionally with one
    of the allowed endings, and with a letter or digit on neither side."""
    fragment = re.escape(word)
    # Doubling the final consonant is how English inflects a lot of short words.
    # Without this, "shit" misses "shitting" and "wank" misses "wanking"'s
    # doubled cousins entirely.
    if word and word[-1].isalpha():
        fragment += re.escape(word[-1]) + "?"
    return _LEFT + fragment + _SUFFIXES + _RIGHT


def split_words(raw):
    """The admin's text split on newlines and commas into lowercased entries,
    empties dropped. Kept public because the dashboard counts entries with it."""
    words = []
    seen = set()
    for chunk in str(raw or "").replace(",", "\n").split("\n"):
        word = chunk.strip().lower()
        if word and word not in seen:
            seen.add(word)
            words.append(word)
    return words


@lru_cache(maxsize=16)
def _compiled(raw):
    """Compile one banned-words list into a single alternation.

    Cached on the raw text because the socket reads the setting fresh for every
    message (so an admin's edit applies at once), and recompiling a hundred
    entries per chat line would be pure waste. The cache is small on purpose:
    the key is the whole list, so it turns over when the admin edits it, and a
    channel only ever has one list in play.
    """
    words = split_words(raw)
    if not words:
        return None
    return re.compile("|".join(_word_pattern(w) for w in words))


def contains_banned(text, raw):
    """True when the message trips the channel's word filter."""
    pattern = _compiled(str(raw or ""))
    if pattern is None:
        return False
    return pattern.search(str(text or "").lower()) is not None
