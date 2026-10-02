# 3. Set up OBS

OBS sends your video to the server over RTMP. Your computer does the encoding,
so this is where you choose 1080p60 and a bitrate.

## 3.1 Stream settings

In OBS, open Settings, then Stream.

- Service: `Custom...`
- Server: `rtmp://watch.example.com:1935`
- Stream Key: copy it from the admin dashboard

Sign in as an admin and open the dashboard. It opens on **Manage Stream**, and
its first step, **Settings in OBS**, has a **Server** and a **Stream Key** field
named exactly as OBS names them, each with a **Copy** button: copy each one
into the box of the same name here. The key looks like `live?pass=...` and
stays hidden until you press **Show**. The step ticks the first time OBS goes
live with that key, so you can see at a glance that OBS has it. The stream key
carries the publish credentials, which is how the server knows the stream is
allowed. Treat it like a password, and use **Regenerate the key** under it if
it ever leaks (a live broadcast keeps running; the next connection needs the
new key, and the step stays unticked until OBS has used it). The word `live` at the start is the stream path and must stay as is,
because the rest of the app expects a path called `live`. It is not the site
name you set in the dashboard: that one is your branding and you can change it
whenever you like, whereas this one is a fixed part of the publish URL.

## 3.2 Video and output settings

In Settings, then Video:

- Base (Canvas) Resolution: `1920x1080`
- Output (Scaled) Resolution: `1920x1080`
- Common FPS Values: `60`

In Settings, then Output, set Output Mode to `Advanced`, then the Streaming tab:

- Encoder: `NVIDIA NVENC H.264` if you have an NVIDIA card, otherwise `x264`
- Rate Control: `CBR`
- Bitrate: `8000 Kbps` is a good start for 1080p60. Lower it to `6000` if
  viewers on slower connections buffer.
- Keyframe Interval: `2` seconds. This matters for HLS. A value of 1 or 2 keeps
  latency low. Do not leave it on `0` (automatic).
- Profile: `high`

NVENC keeps the load off your processor while you game, which is why it is worth
using over x264 if you have it.

**What the bitrate costs.** Your server does not re-encode anything: it hands
every viewer the stream exactly as you send it, so whatever you set here is what
each of them downloads. At 8000 Kbps that is about 3.9 GB per person per hour,
at 6000 Kbps about 2.9 GB. Multiply by however many people watch and by how long
you are on, and that is your bandwidth for the night. If that number is
uncomfortable, the two levers are this setting and the room limit on the
dashboard's Manage Stream screen (see `docs/06-accounts-and-chat.md`).

Your own upload is one copy of the stream and nothing more, however many people
are watching: viewers pull from the server, never from you.

## 3.3 Audio

Your audio is sent automatically inside the same stream, so there is nothing
extra to turn on. In Settings, then Output, the Audio tab, an Audio Bitrate of
`160` is a good quality. Make sure the sources you want heard, such as desktop
audio and your microphone, are active in the OBS Audio Mixer and not muted.

One browser detail to expect: browsers will not start a video with sound on
their own, so the watch page begins muted and shows a "Tap for sound" button.
Viewers tap it once and they hear everything from then on. This is normal and
the same thing Twitch and YouTube do.

## 3.4 Go live

Click Start Streaming in OBS. Within a few seconds the watch page will switch
from the offline card to your video, and on the dashboard the second step,
**Start Streaming in OBS**, ticks and says since when and whether it is being
recorded. If it does not, see the troubleshooting section in `docs/04-run.md`.
The third step, **Send the link**, lights once you are on air: **Copy watch
link** copies your plain `/watch` address, and anyone with an account opens it
straight into the room. It works off air too, but send it once you are on air:
a chat app builds its preview the moment the link is pasted, so a link sent
early shows the offline card instead of tonight's title and a live frame. The
step ticks once the link is copied. Someone without an account needs an invite
link first (`docs/06-accounts-and-chat.md`).

Above the steps is the room: your own watch page, with the picture and the
chat (on a phone the chat sits under the picture), so you can keep an eye on it
without a second tab. **Chat only** drops
the picture (the choice is remembered on that browser), and **Sound** turns
its sound on. Under it, the call board shows who is here.

The slate under the picture is what is on tonight: the title this broadcast is
called and the game. Press **Edit**, type a game or pick one you have used
before, and **Save**; **No game** clears the game on its own. The two are what
a chat app shows when someone pastes your watch link:

```
Northwind Live: Thursday night run
playing Ashfall Delta
```

with a frame of the stream beside them while you are live. Your viewers see the
game on the home page card too, between the channel and the description:

```
Your stream title
Nell @nell
Playing: Ashfall Delta
Your description
```

That line shows only while you are live, and a theater session hides it: during
a film night the room is watching a title, not playing a game. The label itself
stays set until you change it, so it is worth clearing when you stop. What the
frame in the preview means for privacy is spelled out in `docs/05-security.md`.

## A note on latency

This setup runs low latency HLS, which lands around two to five seconds behind
real time. That is far better than the ten to thirty seconds of normal HLS, and
it is the sweet spot of low delay while still playing smoothly on phones. If you
ever need true sub second latency, that requires WebRTC, which is a larger
change and a different tradeoff.
