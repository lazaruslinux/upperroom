# 11. Theater

Theater is watching something together. Instead of broadcasting yourself, you
put on a film or an episode from your own media library and everyone in the
room watches it at the same time, with the same chat they always have.

It is not a second video path. What plays reaches your viewers over exactly the
same ingest, the same MediaMTX, and the same watch page as an OBS broadcast, so
nothing about the delivery changes. What changes is the frame around it.

## 11.1 What a session does

While a theater session is open:

- **Nothing is recorded.** A session is somebody else's film, and building a
  library of it is not what this is for.
- **Clips are refused**, and say so, for the same reason.
- **The game label is hidden.** Whatever you set as what you are playing is
  about a broadcast, not a film night, so a session takes it off the home card
  and out of the link preview until the session ends.
- **Chat is never wiped by a session.** Not between titles, and not when you
  end it. Chat belongs to the night rather than to one broadcast, so an evening
  that runs from a stream into a film reads as one conversation. The room
  clears at the START of a later broadcast, once the channel has been off air
  long enough to count as a different night.
- **Going live announces once**, at the start of the session, rather than once
  per title. Nobody wants a Discord ping per film.

Your viewers see an **Intermission** card between titles instead of the offline
card, a **Now showing** panel over the first couple of seconds of a title, and a
slightly dimmed page while the session runs. Nothing else about the watch page
moves.

**A title reaching its end closes the session.** The live mark goes out, the
channel reads offline, and it takes you to start the next one. That is
deliberate: a film that finishes at midnight used to leave the room sitting on
the intermission card until somebody pressed end, so the channel looked on air
until morning and anyone who had left the page open was still pulling from it.

Only a title actually ending does that. A title that **fails** instead, because
the file will not open or the library blinked, drops the room back to
intermission and says so in chat; the session stays open and you pick again.
Nothing is retried for you, and the same title failing twice running is told
apart from the first time. A projector that **reconnects** mid-film, because its
process restarted or the link dropped, closes nothing either: the state it
reports on connecting is where it already is, not news that something happened.

## 11.2 The projector

The playing is done by a separate small service called the **projector**, in
`projector/`. It runs on whatever machine your library is on, not on your
server.

```
Your media machine                     Your server
------------------                     -----------
projector  --- WebSocket (outbound) --->  gate       "search", "play", "stop"
    |
    |     --- RTMP publish (outbound) --> mediamtx    the video itself
    v
your library (Jellyfin)
```

Both connections are made **by the projector, outward**. Your media machine
needs no open port, no certificate, no name in DNS, and nothing about it is
reachable from the internet. If you unplug it, the gate simply reports that the
projector is not connected.

### What actually crosses the wire

A 4K film does not leave your house as a 4K film. The projector reads the
original file from your library over your own network, then re-encodes it
locally before publishing: scaled down to `PROJECTOR_MAX_HEIGHT` (1080 by
default, and it never upscales) at `PROJECTOR_VIDEO_BITRATE` (6000k by default),
plus 160k of stereo audio. Roughly 6 Mbps leaves your machine, and that is all.

**That figure does not change with the number of viewers.** The projector
publishes one stream to your server; your server hands out the copies. Ten
people watching costs your home connection exactly what one person costs.

Your *server's* bandwidth is the other story. It does not re-encode either, so
each viewer downloads the full 6 Mbps: about 2.9 GB per person per hour. A
two-hour film for ten people is roughly 58 GB. Count the home page in that: its
card plays the real stream rather than a still, so a viewer parked there costs
the same as one watching and takes the same place in the room. It drops back to
the still frame when the room is full or the tab is hidden. If that matters on
your host's plan, lower `PROJECTOR_VIDEO_BITRATE`, or cap the audience under
**Broadcast -> Room limit** on the dashboard (see
`docs/06-accounts-and-chat.md`). The stream strip on the dashboard shows what
the running broadcast has sent so far.

### Setting it up

1. On your server's dashboard, open the **Connections** tab, find **Projector**
   and press **Regenerate** to mint a key. Copy it.
2. Copy your **stream key** and server address out of the **Stream key** panel
   above it, exactly as you would for OBS.
3. On your media machine:

   ```
   cd projector
   cp .env.example .env       # fill in the values below
   docker build -t upperroom-projector .
   docker run -d --restart unless-stopped --name upperroom-projector \
     --env-file .env upperroom-projector
   ```

   Without Docker: Python 3.12, `pip install -r requirements.txt`, an `ffmpeg`
   on the path, then `python main.py`.

4. Back on the dashboard, **Connections** > **Projector** should say
   *Connected*.

### The settings

| Variable | What it is |
|---|---|
| `PROJECTOR_GATE_URL` | Your gate's projector socket, `wss://your-domain/ws/projector`. Use `ws://` only on a link you already trust end to end. |
| `PROJECTOR_KEY` | The key from the dashboard's Projector panel (Connections). |
| `PROJECTOR_INGEST_URL` | Where to publish, `rtmp://your-domain:1935/live`. Same host and path OBS uses. |
| `PROJECTOR_STREAM_KEY` | The channel's stream key, from the dashboard. |
| `JELLYFIN_URL` | Your Jellyfin server, e.g. `http://media-box:8096`. Not needed in demo mode. |
| `JELLYFIN_API_KEY` | An API key from Jellyfin's own dashboard. Read only is enough. |
| `PROJECTOR_VAAPI_DEVICE` | A render node, e.g. `/dev/dri/renderD128`, to encode on the GPU. Unset means libx264 on the CPU. |
| `PROJECTOR_VIDEO_BITRATE` | Publish bitrate, default `6000k`. |
| `PROJECTOR_MAX_HEIGHT` | Cap the picture at this height, default `1080`. It never upscales. |
| `PROJECTOR_DEMO` | `1` plays three built-in generated titles and never touches a library. |

### Proxying the socket

Your reverse proxy has to route `/ws/projector` to the gate, and it has to do it
**before** any auth gate in front of the site. A `forward_auth` refuses a
WebSocket upgrade, so a projector behind one never connects; it gets whatever
the catch-all serves instead, usually a page. The bundled `Caddyfile` already
does this, next to the chat socket. If you run your own proxy, route both
sockets the same way. The projector still authenticates: the gate checks its key
and rate-limits the attempts.

### Hardware encoding

Transcoding a film to h264 in real time is the expensive part. Point
`PROJECTOR_VAAPI_DEVICE` at a render node and the encode moves to the GPU; the
scale and any subtitle burn stay on the CPU, which is cheap. With Docker you
have to pass the device in as well:

```
docker run -d --device /dev/dri/renderD128 --env-file .env upperroom-projector
```

The image carries the VAAPI runtime and drivers for the two common cases (iHD
for modern Intel, mesa for AMD and older Intel), because the pinned ffmpeg loads
libva at run time rather than linking it. If your card needs a different driver,
install it in the image; a device ffmpeg cannot open makes it abort on a libva
assertion the moment a title starts. That is survivable rather than fatal: the
projector notices, retries the same title on the CPU, and reports that hardware
encoding was unavailable, so a showing degrades instead of stopping. Leave the
variable unset and it encodes with libx264 at `veryfast` from the start, which
one modern core can hold at 1080p.

### Subtitles

Subtitles are **off by default**, on a channel that has been running for a year
as well as a fresh one. **Enable subtitles (Unstable, might be out of sync)** in
the dashboard's Theater panel turns them on: that is the default every play
starts from, and it is remembered. The **Burn in subtitles** box beside the
search overrides it for one showing, either way.

The label means what it says. Subtitles are burned into the picture rather than
sent alongside it, because the viewer's player has no separate track to switch
on: everyone is watching one video. That also means the timing is whatever the
library's subtitle file says it is, and a file that runs a few seconds out runs
a few seconds out for the whole room, with no way for anybody to turn it off.

That is what **Restart without subtitles** is for, on the dashboard and as **no
subs** on the watch page's host strip. One click, no confirmation: the same
title goes back on from the start without the burn, the session stays open, and
the room is told once. Nothing else about the showing changes.

Only titles that actually carry a subtitle track are burned; for the rest the
box does nothing. Text subtitles (SRT, ASS) work. If the burn fails, the
projector notices within a few seconds and restarts the title without it rather
than leaving you with nothing playing.

## 11.3 Running a session

Two places, the same controls:

- **The dashboard**, under **Theater** on the **Broadcast** tab. Start the
  session, search your library, press play on a row, restart it without
  subtitles, stop the title, end the session.
- **The watch page**, on a strip above chat, visible to admins only. Same
  buttons, so you can run the evening from the page you are watching on.

A normal evening:

1. **Start session.** Viewers see the intermission card, and the go-live
   announcement goes out once.
2. **Play** a title. Search, pick a row, press play. The Now showing card covers
   the few seconds before the picture arrives.

   Searching finds **films and shows**, each with its poster. A film has a
   **play** button. A show has an **episodes** button instead, because a show is
   not a thing you can put on: pressing it opens that show's run, with a chip
   per season and the episodes of the season you are looking at. Pick one and
   press play, or press **back** to return to what you searched for. A show with
   a single season shows no chips, since there would be nothing to choose
   between.

   An episode is named by its show wherever it appears: chat says
   `Silo (2023) S3E1, "Freedom Day" selected`, and a shared link reads
   `playing Silo (2023) S3E1`. The year is the show's, not the year that season
   happened to air, because that is the year anybody knows the show by.
3. **When the title ends, the session ends.** The room goes offline and chat is
   untouched. Nothing restarts on its own; the next one is yours to put on. A
   title that fails rather than finishes keeps the session: the room goes back
   to intermission and is told the title would not play.
4. **Stop** is different, and it is the one for changing your mind mid-film: the
   room drops back to intermission and waits for you to pick the next title,
   with the session still open.
5. **End session** closes the evening yourself. Whatever is playing stops and
   the room goes back to an ordinary broadcast. Chat is kept.

However a session ends, the room is told once: "Theater mode disabled." when
you end it, "That was the end of it. Theater mode is off." when a title runs
out. The "Stream ended." line that follows an ordinary broadcast is not said
after a theater close, because the close has already said it in its own words.

One session runs at a time, and starting a second is refused rather than
quietly ignored.

## 11.4 What your viewers can see

The theater state rides the chat socket every viewer already holds, so the
intermission and Now showing cards change without a poll. What it carries is the
title, year, runtime and synopsis of what is on, and the poster.

What it does not carry is anything about your library: no item ids, no paths, no
server address, and nothing at all when no session is running. `/api/status`,
which anyone signed in reads, is unchanged and says nothing about theater.

Posters are stored under the media directory in `art/`, served behind the same
session check as your recordings, and re-encoded on the way in so only pixels
are written. They are deliberately not in the recordings or clips folders, so
retention never treats a poster as something to prune.

## 11.5 Demo mode

`PROJECTOR_DEMO=1` gives the projector three built-in titles with generated
picture, sound and posters, and never contacts a library. That is what the demo
stack uses (`docs/07-demo.md`), and it is the quickest way to see a session work
before setting up a library.

Each demo title runs two minutes and then ends on its own, which is also the
quickest way to watch a session close itself when its title runs out.
