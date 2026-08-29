# 4. Run it

## 4.1 Fill in the config

On the server, in the project folder:

```
cp .env.example .env
nano .env
```

Set every value. A couple of tips:

- Generate the cookie secret with `openssl rand -hex 32` and paste the result
  into `SELFSTREAM_JWT_SECRET`.
- `PUBLISH_PASS` is optional now: the stream key lives on the admin dashboard and
  is generated for you the first time you open the Stream key panel. Set
  `PUBLISH_PASS` only if you want to seed a specific key on first start (an
  existing install carries its old value over), or if you run the demo profile.
- `SELFSTREAM_DOMAIN` is just the hostname, like `watch.example.com`, with no
  `https://` in front.

## 4.2 Start the stack

```
docker compose up -d --build
```

The first start builds the gate image and asks Let's Encrypt for a certificate,
so give it a minute. Watch the logs if you want:

```
docker compose logs -f
```

## 4.3 First-run setup

Open `https://watch.example.com` in a browser. On a brand new install the login
page sends you straight to a one-time setup wizard at `/setup`. Fill in a
username, a display name, a password, and a site name (your own brand, shown
above "powered by upperroom" on every page), then create the account. That first account is the admin, you are signed in at once, and the
wizard closes for good the moment it exists.

From then on there are two ways to let people in, and they answer different
questions.

**Invite codes** are for someone you want to keep. Open the dashboard at
`/admin`, generate a single-use code under **Invites**, and share it. They
redeem it from the login page ("have an invite?") to make their own viewer
account, and it is theirs from then on. No terminal, and no email is involved.

**Guest passes** are for someone who just wants to watch this one. Under
**Guest passes**, choose how many you want and generate them: each is single
use and lets one person watch and chat for half an hour without making an
account. "Copy all unused" puts the whole batch on your clipboard, one per
line, which is the point: one message, one code each, first come first served.
Send people to `/guest` to redeem one. The half hour starts when they redeem
it, not when you make it, so you can prepare a batch days ahead.

A guest can watch and chat, and nothing else. No clipping, no library, no
likes or comments, no points. They can be timed out, banned, `/del`ed and
`/purge`d exactly like anyone else, which is the whole reason they exist as
real accounts rather than as a separate kind of visitor. When the time is up
their video stops, they are shown a sign-in prompt, and the account removes
itself a few minutes later.

See `docs/06-accounts-and-chat.md` for the details.

Everything you run the place with is on the dashboard at `/admin`: people
(accounts, bans and invites), branding, the schedule, chat rules, the stream
key, the overlay, notifications, storage and the library of past broadcasts
(the recordings). `/analytics` sits beside it and holds the numbers: watch time,
broadcasts, library and invite use, plus line charts of watch time, unique
viewers and chat messages per day over the last thirty days.

If you ever need to bootstrap or recover an account from the command line (for
example if you are locked out), `manage.py` still works; it is described in
`docs/06-accounts-and-chat.md` as the break-glass path.

## 4.4 Test it

Open `https://watch.example.com` in a browser. You should see the login page.
Sign in with the account you made. Until OBS is streaming you will see the
offline card. Start OBS and the video appears.

## 4.5 Recordings and clips

Every broadcast is recorded automatically. While you are live the recording is
written to a local scratch volume (a plain copy of the stream, with no
re-encoding, pulled over the internal docker network), so it never competes with
the live stream for bandwidth or quality. When the stream ends, the finished
file is archived to the media store and shown on the home page under **VODs**.
Viewers can also clip the recent stream while you are live, and those appear
under **Clips**, each with synced chat replay. Pressing Clip asks how much to
take (one minute, 45 seconds or 30 seconds) and saves it straight away; naming
it comes after, and skipping that leaves it called "Clip". Either way the clip
is taken from the moment the button was pressed, so nothing that happens next
moves the window. A clip can be renamed later on its own page, by whoever made
it or by a moderator.

Those three lengths are the whole rule; there is no channel-wide cap to set.
One person waits five minutes between clips, and the host waits one. Both come
from `SELFSTREAM_CLIP_COOLDOWN` and `SELFSTREAM_CLIP_COOLDOWN_HOST` in seconds,
so retuning them is an environment change rather than a dashboard toggle.

Clips are deleted after two days unless you pin them. That is the one retention
limit a fresh install ships switched on, and it is deliberate: a clip is the
thing you hand to other people, so a short life keeps a mistake from standing
forever. Change it under **Storage**, or pin a clip to keep it regardless.

### Sharing a clip publicly

Any single clip can be given a link that works without an account. **Share**
sits on the clip's own page, under the video, and on its row in the dashboard's
library; pressing it copies the link for you. While the clip is shared, **Copy
link** hands you that link again in either place, so you never have to remember
it from the one time it was offered. **Stop sharing** on the clip page, or
**Unshare** in the library, ends it: the link stops working immediately and
permanently, and sharing the clip again later makes a new link rather than
reviving the old one.

This is the only part of the site a stranger can reach. A public clip is video
only: no chat replay, no comments, and it does not say who made it. Nothing
else opens up, and the clip stays private until you choose otherwise.

### Likes and comments

Signed-in viewers can like a recording or a clip, and leave comments under it.
Comments are separate from the chat replay on purpose: the replay is what was
said live, comments are what people say afterwards. An author can delete their
own; you and your moderators can delete any. A comment obeys the same chat
rules, so someone banned from chat cannot comment instead.

A strip at the top of the dashboard shows the broadcast at a glance so you never
have to read the container logs to know it is up: **Live** or **Offline**, how
long you have been live, how many people are watching, and whether the broadcast
is being recorded (**recording**, **recording (restarting)** while the recorder
is cycling, or **not recording** if it is live but nothing is being captured). It
refreshes on its own while the page is open.

Recording recovers on its own. If the recorder ever dies or its file stops
growing mid-broadcast (for example, a rough reconnect on a long session), the
gate finalizes whatever it captured, starts a fresh recording while you stay
live, and backs off if failures repeat. You may see more than one recording for a
single broadcast when this happens; nothing is lost.

Nothing is deleted automatically unless you ask for it. The admin dashboard's
**Storage** section shows what your recordings and clips are using and how much
room is left on the disk, and lets you set limits: a number of recordings, a
number of days, the same two for clips, and a ceiling on the total size. Every
one of them is off until you set it, and lowering a limit takes effect as soon
as you save.

Anything you want to keep for good, pin. A pinned recording or clip is never
removed by any limit, and it does not use up a slot in the count, so "keep the
last 20, plus the ones I pinned" is exactly what you get. The size limit never
removes your newest recording or newest clip, so one large broadcast cannot
delete itself. Pin and Delete both sit next to each item under **Content**.

The one thing to know if you are updating an older install: it has been keeping
only the most recent 20 recordings, from a setting in `.env`. That value is
carried over into the dashboard once, so nothing changes under you, and from
then on the dashboard is where it lives.

### Storing recordings on a bigger disk

By default, recordings and clips live in a docker volume named `media_data`, so
a fresh checkout just works. To keep them somewhere with more room (a separate
disk, a NAS, a network mount), point that volume at your own path with an
**uncommitted** `docker-compose.override.yml` next to `docker-compose.yml`:

```yaml
# docker-compose.override.yml  (git-ignored; your paths stay out of the repo)
volumes:
  media_data:
    driver: local
    driver_opts:
      type: none
      o: bind
      device: /your/own/path/to/recordings
```

Docker merges this automatically; nothing else changes. Keep your real path only
here, never in a committed file.

## 4.6 Updating

Pull the newest images and rebuild:

```
docker compose pull
docker compose up -d --build
```

Your accounts survive updates because they live in a docker volume, not in the
container.

If you keep your own copy of `docker-compose.yml` or your own Caddy config
rather than the ones in this repo, updating to 0.8.0 needs two small additions,
both for the watch page's link preview:

- the gate mounts the static site read only, so it can fill in that page's
  preview tags: `- ./web:/srv/web:ro` under the gate's `volumes`, and
  `- SELFSTREAM_WEB_DIR=/srv/web` under its `environment`
- Caddy sends that one path to the gate instead of serving it off disk:

```
handle /watch {
	reverse_proxy gate:8000
}
```

put with the other `handle` blocks, above the catch-all that serves the static
site. Without them the watch page still works; its link preview is just the
generic one baked into the file.

## 4.7 Troubleshooting

- The page does not load at all. Check that the DNS record points at the server
  and that ports 80 and 443 are open in the firewall. Check `docker compose
  logs caddy` for certificate errors.
- The video never starts. Confirm OBS says it is streaming. Check
  `docker compose logs mediamtx` for a connection from your address. Make sure
  the OBS stream key matches the one shown in the dashboard Stream key panel
  (it looks like `live?pass=...`).
- OBS cannot connect. The firewall rule for port 1935 may not match your current
  home IP. See `docs/01-vps-setup.md`, section 1.4.
