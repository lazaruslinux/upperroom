"""
What changed in the running release, for the one-time notice on the home page.

Only the current version is ever shown. Somebody who skips three releases sees
the newest one and nothing else: this is a "here is what is new", not a version
history, and five lines is the whole budget. Anything longer belongs in the
GitHub release notes.

Keep the newest entry at the top and write the lines for a viewer, not for a
committer: what they can now do, not which file moved.
"""

from config import VERSION

# version -> up to five short lines. A version with no entry shows no notice at
# all, which is the right behaviour for a release with nothing a viewer would
# notice.
NOTES = {
    "0.21.1": [
        "The account button now shows a menu icon beside your avatar.",
        "The menu opens with your name at the top.",
    ],
    "0.21.0": [
        "A shared clip link now names the clip, the channel and the game.",
        "The card shows a frame of the clip instead of the site logo.",
        "Clips made before this update show the channel name on its own.",
    ],
    "0.20.0": [
        "New chat fonts: JetBrains Mono, Space Grotesk, IBM Plex Sans, Sora.",
        "The settings panel now previews your chat style as you pick.",
        "Old font picks fall back to the default.",
        "Highlighted messages now show in the sender's font.",
    ],
    "0.19.0": [
        "Subtitles are off unless the host turns them on.",
        "A film whose subtitles drift can be restarted without them in one press.",
    ],
    "0.18.0": [
        "Notices from the room now show the time, like any message.",
        "Chat shows a line while the stream is offline.",
        "A movie night ending is said once, without the extra lines.",
        "Moderators can wipe the chat with /wipe. It asks before it acts.",
    ],
    "0.17.0": [
        "The home page now plays the stream, muted, right in the card.",
        "Click the card to join with sound and chat, the way you always did.",
    ],
    "0.16.0": [
        "A new top bar: search, your points, and your account all in one place.",
        "Past broadcasts and clips moved to their own Browse page.",
        "Your account settings are now a page of their own, Options.",
    ],
    "0.15.0": [
        "The chat bar has a home button again, so the watch page is no longer a dead end.",
    ],
    "0.14.2": [
        "A movie night no longer ends by mistake when the projector hiccups.",
        "A title that will not play returns to the intermission card and says so.",
    ],
    "0.14.1": [
        "The site answers again straight away after a restart.",
        "A recording is no longer at risk if saving it fails part way through.",
        "Fixed an error that could refuse the video while a lot of people watched.",
    ],
    "0.14.0": [
        "More broadcasts stay saved, on a much bigger disk.",
        "A recording that cannot be saved is now kept and retried instead of lost.",
    ],
    "0.13.0": [
        "A film reaching its end now closes theater mode instead of running on.",
        "The scheduled next stream is gone: the room says what is happening now.",
    ],
    "0.12.0": [
        "The chat bar folds away, so the picture can have the whole width.",
        "The people icon opens the list of everyone watching.",
        "The channel can now limit how many people watch at once.",
        "This notice, once per release.",
    ],
}

# The most lines a notice may carry. Enforced rather than trusted: a longer list
# would push the acknowledge button off a phone screen.
MAX_NOTES = 5


def current():
    """The notice for the running version, or None if this release has none."""
    lines = NOTES.get(VERSION)
    if not lines:
        return None
    return {"version": VERSION, "notes": list(lines)[:MAX_NOTES]}
