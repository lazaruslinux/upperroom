# The chat overlay

The overlay is a transparent page that shows recent chat, join notices, and clip
alerts in the site's terminal look. You add it to OBS as a browser source and
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
5. Position the source. By default the overlay anchors its chat to the
   bottom-left; move the whole browser source in OBS if you want it elsewhere.

The overlay reconnects on its own if the connection drops, so it is safe to leave
running for a whole broadcast unattended.

## What it shows

- **Chat**, newest at the bottom, with the author's name and any op/mod tag. Lines
  clear themselves after about a minute, and only the last several stay on screen,
  so it stays ambient rather than piling up.
- **Join notices** ("someone joined") as a smaller muted line.
- **Clip alerts** ("someone clipped: <name>") with an accent border, whenever a
  viewer clips the stream.

Moderation still applies: if a moderator deletes a message it disappears from the
overlay too, and the overlay clears when a broadcast ends.

## Security note

The key in the URL is a bearer token: whoever holds the full URL can read chat
through the overlay, with no account needed. It is read-only (the overlay can
never send chat or run commands), but you should still not share or screen-record
the URL. If it leaks, open the **Overlay** panel and press **Regenerate**: that
mints a new key, instantly invalidates the old URL, and you then paste the new URL
into your OBS browser source.
