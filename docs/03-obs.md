# 3. Set up OBS

OBS sends your video to the server over RTMP. Your computer does the encoding,
so this is where you choose 1080p60 and a bitrate.

## 3.1 Stream settings

In OBS, open Settings, then Stream.

- Service: `Custom...`
- Server: `rtmp://watch.example.com:1935`
- Stream Key: copy it from the admin dashboard

Sign in as an admin, open the dashboard, and find the Stream key panel. It shows
the exact server and stream key to paste here; the key looks like
`live?pass=...`. Use the Copy key button to grab it, and Show to reveal it. The
stream key carries the publish credentials, which is how the server knows the
stream is allowed. Treat it like a password, and regenerate it on the dashboard
if it ever leaks (a live broadcast keeps running; the next connection needs the
new key). The word `live` at the start is the stream path and must stay as is,
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
from the offline card to your video. If it does not, see the troubleshooting
section in `docs/04-run.md`.

## A note on latency

This setup runs low latency HLS, which lands around two to five seconds behind
real time. That is far better than the ten to thirty seconds of normal HLS, and
it is the sweet spot of low delay while still playing smoothly on phones. If you
ever need true sub second latency, that requires WebRTC, which is a larger
change and a different tradeoff.
