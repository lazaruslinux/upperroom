# The chat overlay

The overlay is a transparent page that shows recent chat, join notices, clip
alerts and highlighted messages, styled to match the site. You add it to OBS as a browser source and
composite it over your video, so the broadcast itself carries the chat. Nothing
about it touches your viewers' watch page; it is purely for the outgoing stream.

OBS cannot sign in, so the overlay authenticates with a long random key carried
in its URL. Anyone who has that URL can read chat through it, so treat it like a
password: keep it to yourself, and regenerate it if it ever leaks.

## Get the URL

1. Open the admin dashboard (`/admin`) and find the **Overlay** panel, just below
   **Channel**.
2. Copy the URL shown there with **Copy URL**. It looks like
   `https://your-domain/overlay?key=...`.

## Add it to OBS

1. In OBS, add a new source: **Browser**.
2. Paste the overlay URL.
3. Set the size to **1920 x 1080** (match your canvas). The text is sized to read
   at 1080p from across a room.
4. Leave the background alone: the page is transparent, so only the chat chips
   are drawn and your video shows through everywhere else. If your OBS build has a
   "custom CSS" box, you can leave it empty.
5. Decide what happens to the highlight chime. The overlay plays a short sound
   when a viewer highlights a message, and a browser source's audio does not
   reach your stream unless you tell OBS to take it: tick **Control audio via
   OBS** in the source properties if you want your viewers to hear it, and leave
   it unticked if the chime should stay off the broadcast. Either way you will
   not hear it in your own headphones unless you also monitor that source.
6. Position the source. By default the overlay anchors its chat to the
   bottom-left; move the whole browser source in OBS if you want it elsewhere.

The overlay reconnects on its own if the connection drops, so it is safe to leave
running for a whole broadcast unattended.

## What it shows

- **Chat**, newest at the bottom, with the author's name in whatever color they
  chose. The host shows a small red camera, a moderator a `mod` tag. A line
  clears itself after about 45 seconds, and only the last eight stay on screen,
  so it stays ambient rather than piling up.
- **Join notices** ("someone joined") as a smaller muted line, for about ten
  seconds. Leaving is deliberately not shown, to keep the overlay quiet.
- **Clip alerts** ("someone clipped: <name>") with an accent border, whenever a
  viewer clips the stream.
- **Highlighted messages** ("someone highlighted: <message>"), when a viewer
  spends channel points to put a message on the broadcast. These carry an accent
  border like clips and **play a short chime**, so they are the one thing on the
  overlay that makes a sound. See `docs/09-points.md` for how points work.

Moderation still applies: if a moderator deletes a message it disappears from the
overlay too, and the overlay clears when a broadcast ends.

## Security note

The key in the URL is a bearer token: whoever holds the full URL can read chat
through the overlay, with no account needed. It is read-only (the overlay can
never send chat or run commands), but you should still not share or screen-record
the URL. If it leaks, open the **Overlay** panel and press **Regenerate**: that
mints a new key, instantly invalidates the old URL, and you then paste the new URL
into your OBS browser source.
