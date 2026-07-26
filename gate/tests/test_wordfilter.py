"""The chat word filter.

The test that matters most here is
`test_default_list_does_not_block_ordinary_english`. The default list contains
short words like "ass" and "cum", and the moment matching drifts back toward
substrings, or somebody widens the allowed suffixes, ordinary chat starts
getting refused with no visible cause. That test is the tripwire.
"""

import db
import wordfilter


# Ordinary messages, several of them chosen precisely because a substring
# matcher would refuse them against the shipped default list.
ORDINARY = [
    "what a class play",
    "please pass the ball",
    "the grass is really green",
    "I will assist you with that",
    "that is embarrassing",
    "read the documentation first",
    "a cucumber sandwich",
    "back to the title screen",
    "the constitution",
    "run the analysis again",
    "a canal boat",
    "I ate a grape",
    "put on your shoe",
    "look at that peacock",
    "the cockpit view is better",
    "Charles Dickens",
    "Sussex and Essex",
    "hello everyone",
    "just a shell script",
    "Massachusetts",
    "my therapist said so",
    "Scunthorpe",
    "the assassin build",
    "bass guitar",
    "a classic run",
    "passing it now",
    "massive damage",
    "that assumption is wrong",
    "under the circumstances",
    "they accumulate fast",
    "the competition is open",
    "a substitute teacher",
    "analyze the replay",
    "grape soda and glass bottles",
]


def test_default_list_does_not_block_ordinary_english():
    """The tripwire. Every one of these is a message a viewer could plausibly
    send, and a substring matcher refuses most of them."""
    blocked = [t for t in ORDINARY if wordfilter.contains_banned(t, wordfilter.DEFAULT_BANNED_WORDS)]
    assert blocked == [], f"the default list blocks ordinary messages: {blocked}"


def test_default_list_blocks_what_it_is_for():
    for text in ("fuck this", "you asshole", "stupid bitch", "kys", "retarded"):
        assert wordfilter.contains_banned(text, wordfilter.DEFAULT_BANNED_WORDS), text


def test_matching_is_whole_word_not_substring():
    assert wordfilter.contains_banned("ass", "ass")
    assert not wordfilter.contains_banned("a class act", "ass")
    assert not wordfilter.contains_banned("please pass", "ass")
    assert not wordfilter.contains_banned("I will assist", "ass")


def test_allowed_endings_are_caught():
    assert wordfilter.contains_banned("fucking awful", "fuck")
    assert wordfilter.contains_banned("he fucked up", "fuck")
    assert wordfilter.contains_banned("what a fucker", "fuck")
    # A doubled final consonant is how English inflects a lot of short words.
    assert wordfilter.contains_banned("shitting bricks", "shit")


def test_unrelated_endings_are_not_caught():
    """"ass" must not reach "assist", which is exactly what a wider suffix set
    would break."""
    assert not wordfilter.contains_banned("assist me", "ass")
    assert not wordfilter.contains_banned("the analysis", "anal")
    assert not wordfilter.contains_banned("the title", "tit")
    assert not wordfilter.contains_banned("hello there", "hell")


def test_case_and_separators():
    assert wordfilter.contains_banned("I love PINEAPPLE pizza", "pineapple")
    # Commas and newlines both separate entries.
    assert wordfilter.contains_banned("a badword here", "pineapple, badword")
    assert wordfilter.contains_banned("a badword here", "pineapple\nbadword")


def test_phrases_work():
    assert wordfilter.contains_banned("that is a bad phrase right there", "bad phrase")
    assert not wordfilter.contains_banned("that is bad, and a phrase", "bad phrase")


def test_empty_list_blocks_nothing():
    assert not wordfilter.contains_banned("anything at all", "")
    assert not wordfilter.contains_banned("anything at all", None)
    assert not wordfilter.contains_banned("anything at all", "   \n , \n  ")


def test_punctuation_around_a_word_still_matches():
    assert wordfilter.contains_banned("oh, fuck!", "fuck")
    assert wordfilter.contains_banned("(bitch)", "bitch")


def test_split_words_dedupes_and_lowercases():
    assert wordfilter.split_words("One, one\nTWO\n\n  three  ") == ["one", "two", "three"]


def test_fresh_install_seeds_the_default_list(tmp_path, monkeypatch):
    """A new channel starts with the defaults; the operator owns it from there."""
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "fresh.db"))
    db.init_db()
    assert db.get_chat_moderation()["banned_words"] == wordfilter.DEFAULT_BANNED_WORDS


def test_an_existing_channel_is_never_given_the_default_list(tmp_path, monkeypatch):
    """The operator may have emptied the list deliberately. An update that
    refilled it would be putting words back that somebody chose to remove."""
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "existing.db"))
    db.init_db()
    db.set_chat_moderation(banned_words="")
    db.init_db()   # a later start, e.g. after an update
    assert db.get_chat_moderation()["banned_words"] == ""
