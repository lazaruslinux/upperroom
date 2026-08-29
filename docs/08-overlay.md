# The chat overlay

The overlay is a transparent page that shows recent chat, join notices, clip
alerts and highlighted messages, styled to match the site. You add it to OBS as a browser source and
composite it over your video, so the broadcast itself carries the chat. Nothing
about it touches your viewers' watch page; it is purely for the outgoing stream.

OBS cannot sign in, so the overlay authenticates with a long random key carried
in its URL. Anyone who has that URL can read chat through it, so treat it like a
password: keep it to yourself, and regenerate it if it ever leaks.

## Get the URL

1. Open the admin dashboard (`/admin`), go to the **Connections** tab, and find
   the **Overlay** panel at the bottom of it.
2. Copy the URL shown there with **Copy URL**. It looks like
   `https://your-domain/overlay?key=...`.

The same panel has a collapsed **Set up in OBS** block with ready-made URLs for
the common browser sources: everything in one source, chat only, the status chip
alone, the ticker alone, and the three scene screens. Each row copies a finished
URL with your key already in it, so you do not have to assemble the query options
described further down by hand.

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
- **Highlighted messages** ("<name> highlighted: <message>"), when a viewer
  spends channel points to put a message on the broadcast. They now carry the
  sender's identity, their name and role mark, the same way a chat line does.
  These carry an accent border like clips and **play a short chime**, so they
  are the one thing on the overlay that makes a sound. See `docs/09-points.md`
  for how points work.

Moderation still applies: if a moderator deletes a message it disappears from the
overlay too, and the overlay clears when a broadcast ends.

## The status chip

While you are live, a small chip in the top-right corner shows `LIVE`, how long
the broadcast has been running, and how many people are watching, for example
`LIVE 1:23:45 · 7 watching`. The clock ticks every second and the viewer count
updates the moment someone joins or leaves. The chip hides itself when you are
offline, so it only appears once you actually go live.

The chip reserves the strip it sits in rather than being drawn on top of chat. If
your chat column is anchored to a top corner it starts below the chip instead of
running underneath it, and if it is anchored to a bottom corner (the default) a
column tall enough to reach the chip drops its oldest lines early. Either way no
chat line is ever half hidden behind the chip.

## The ticker

The **Overlay** panel on the dashboard has a **Ticker message** box. Whatever you
put there scrolls along the bottom of the overlay as a single quiet line, which is
handy for a "now playing", a schedule, or a shout-out. Save it and it appears on
the overlay at once, with no reload. Clear the box and save to remove it. The
ticker only travels over the overlay's own connection, so it is never visible to a
logged-out visitor before it is on your broadcast.

If the message is short enough to fit it sits still; if it is longer than the
screen it scrolls. A viewer whose system asks for reduced motion never sees it
move.

The ticker reserves the band it occupies. While it is up, bottom-anchored chat
sits above the band and a scene card's text moves up to clear it, so the ticker
never covers a chat line or a countdown. Clear the ticker and everything settles
back where it was.

## Test buttons

Under the ticker box are four small buttons: **chat**, **join**, **clip** and
**highlight**. Each one sends a single fake event to any overlay you have open, so
you can line the browser source up in OBS and confirm it works without waiting for
a real viewer to do anything. The test events are clearly labelled as tests, are
sent only to the overlay, and never appear in real chat or the chat history.

## Tuning it with URL options

The overlay reads a few options from its own URL, so you can change how it looks
without any settings to save. Add them to the overlay URL after the key, each
separated by `&`, for example:

```
https://your-domain/overlay?key=...&pos=br&scale=1.1&max=6
```

| Option | What it does | Default |
| --- | --- | --- |
| `pos` | Which corner the chat anchors to: `bl`, `br`, `tl`, `tr`. With `tr`, the live status chip moves to the top-left so chat never covers it | `bl` (bottom-left) |
| `scale` | Text size multiplier, `0.75` to `1.5` | `1` |
| `show` | Which parts to draw, comma separated, from `chat,joins,clips,highlights,status,ticker` | all of them |
| `max` | How many chat lines stay on screen, `1` to `20` | `8` |
| `mute` | `mute=1` silences the highlight chime | off |
| `scene` | Show a full-screen scene card instead of chat: `starting`, `brb`, `ending` | off |
| `at` | For `scene=starting`, the local start time to count down to, as `HH:MM` | none |
| `title` | The title line on a scene card | a sensible default per scene |

Anything it does not recognise falls back to the default, so a typo never leaves
you with a blank overlay.

## Small browser sources

You do not have to give the overlay the whole canvas. A short, wide browser source
(a strip along the bottom of your 1080p scene, say) works, and the parts that need
room to themselves keep out of each other's way: the ticker and the status chip
reserve their bands, and a scene card tightens its text below 300px of height.

Give the source at least about 200px of height. Below that there is not enough room
for the default text sizes and you will start losing chat lines to the chip band,
so turn the text down with `scale` (for example `scale=0.8`), or trim the overlay
to just what you need with `show` and `max`.

## Scene screens (Starting Soon / BRB / Ending)

The same overlay page can also be a full-screen "holding" card for the moments
around a broadcast: a **Starting soon** screen with a countdown, a **Be right
back** card for a break, and a **Thanks for watching** card at the end. You get
them with the `scene` option:

```
https://your-domain/overlay?key=...&scene=starting&at=19:30&title=Season%20finale
https://your-domain/overlay?key=...&scene=brb&title=Back%20in%20five
https://your-domain/overlay?key=...&scene=ending
```

- `scene=starting` shows your site name, the title line, and a countdown to the
  `at=` time (a local `HH:MM`, today). If you leave `at=` off or it has already
  passed, it just shows the title. Once you actually go live, the countdown
  quietly changes to **Live now**, so you can see at a glance that the screen is
  still up and needs switching away from.
- `scene=brb` and `scene=ending` show the site name and a title line, with no
  countdown.
- While a scene card is up the normal chat column is hidden, but the ticker still
  shows, since it is your own line.

The tidiest way to use these in OBS is to add a **second Browser source** with the
scene URL and put it in its own scene (your "Starting Soon" scene, say). Switch to
that OBS scene when you want the card, and back to your live scene when you are on.
The overlay does not change your OBS scenes for you; switching scenes is OBS's job.

## Security note

The key in the URL is a bearer token: whoever holds the full URL can read chat
through the overlay, with no account needed. It is read-only (the overlay can
never send chat or run commands), but you should still not share or screen-record
the URL. If it leaks, open the **Overlay** panel and press **Regenerate**: that
mints a new key, instantly invalidates the old URL, and you then paste the new URL
into your OBS browser source.
