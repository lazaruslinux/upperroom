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
- Generate the media store's two keys the same way, one for
  `SELFSTREAM_STORE_KEY` and a different one for `SELFSTREAM_STORE_READ_KEY`.
  The store refuses to start without both, so recordings and clips have nowhere
  to go until they are set.
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
`/admin`, go to **People**, and generate a single-use code under **Access
codes** with the **Invites** tab selected, then share it. They
redeem it from the login page ("have an invite?") to make their own viewer
account, and it is theirs from then on. No terminal, and no email is involved.

**Guest passes** are for someone who just wants to watch this one. On the
**Guest passes** tab of the same panel, choose how many you want and generate
them: each is single
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

Everything you run the place with is on the dashboard at `/admin`, in five
tabs under your own watch page: **Broadcast** (theater and the room limit),
**Content** (past broadcasts, clips and storage limits), **People** (accounts,
bans, invites and guest passes), **Channel** (branding, chat rules and
notifications) and **Connections** (the stream key, the projector and the
overlay). `/analytics` sits beside it and holds the numbers: watch time,
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
file is uploaded to the media store and shown on the browse page under
**Broadcasts**.

The media store is the `store` container: a small service that holds every
recording and clip and answers over HTTP, with one key for the gate (which
writes) and another for Caddy (which only reads). Nothing else touches those
files, so the store can sit on this server or on another machine without the
rest of the site noticing the difference.
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
forever. Change it under **Content** > **Storage**, or pin a clip to keep it
regardless.

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

Pasted into a chat app the link previews as the clip: `"Big whiff" - Stream
Clip` on the first line, your site name and the game it was cut from on the
second, and a frame of the clip as the picture. What was being played is stamped
on the clip when it is made, so it stays right even after you move on to
something else; a clip made before this was added shows your site name on its
own. A link you have stopped sharing previews as nothing, the same as a link
that was never real.

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

Nothing is deleted automatically unless you ask for it. The **Storage** panel
on the dashboard's **Content** tab shows what your recordings and clips are
using and how much room is left on the disk, and lets you set limits: a number
of recordings, a number of days, the same two for clips, and a ceiling on the
total size. Every one of them is off until you set it, and lowering a limit
takes effect as soon as you save.

Anything you want to keep for good, pin. A pinned recording or clip is never
removed by any limit, and it does not use up a slot in the count, so "keep the
last 20, plus the ones I pinned" is exactly what you get. The size limit never
removes your newest recording or newest clip, so one large broadcast cannot
delete itself. Pin and Delete both sit next to each item in the same tab.

The one thing to know if you are updating an older install: it has been keeping
only the most recent 20 recordings, from a setting in `.env`. That value is
carried over into the dashboard once, so nothing changes under you, and from
then on the dashboard is where it lives.

### Storing recordings on a bigger disk

By default the store keeps recordings and clips in a docker volume named
`media_data`, so a fresh checkout just works. To keep them somewhere with more
room on the same server (a separate disk, a big partition), point that volume at
your own path with an **uncommitted** `docker-compose.override.yml` next to
`docker-compose.yml`:

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
here, never in a committed file. The store runs as an unprivileged user (uid
10001), so the directory has to belong to it: `chown 10001:10001
/your/own/path/to/recordings`.

Docker reads these options when it creates the volume, not on every start, so if
the volume already exists you have to remove it (`docker compose down`, then
`docker volume rm <project>_media_data`) before the new location takes effect.
Move the files across first if there are any you want to keep.

### Keeping them on another machine

Recordings add up fast, and the machine with the room for them is often not the
one serving the stream. Run the store there instead. It is the same container,
so the only change is how the gate and Caddy reach it: over a private network,
on one port, with the same two keys.

`store/docker-compose.yml` is an example that does exactly that with Tailscale:
the store sits on an internal network with no route out, and a Tailscale
sidecar joins your tailnet and forwards port 8080 to it and nothing else. Copy
the `store/` directory to the storage machine, fill in the `.env` its comments
describe (the two keys must be the same values as on the streaming server), and
start it. The streaming server has to be on the same tailnet, as a machine
rather than a container.

Then, on the streaming server, point both halves at the store's tailnet address
in `.env`:

```
SELFSTREAM_STORE_URL=http://STORE_TAILNET_IP:8080
SELFSTREAM_STORE_UPSTREAM=STORE_TAILNET_IP:8080
```

and switch the local store off in your uncommitted override, by giving it a
profile nobody enables. Nothing else depends on it, so nothing else changes:

```yaml
# docker-compose.override.yml  (git-ignored)
services:
  store:
    profiles: ["store-elsewhere"]
```

Run `docker compose up -d` and `docker compose ps` no longer lists a store.
The address is the tailnet IP rather than a name because the containers on the
streaming server resolve names through Docker, which may not know your tailnet's
names. `docs/05-security.md` covers what the keys can and cannot do and what to
put in your tailnet policy.

This replaces the older advice to mount a NAS export as the media volume. A
network mount that dies takes everything that touches it down with it: a file
check hangs, a "not there" can mean "unreachable", and a container that mounts
it may not even start. Over HTTP a store that is away is a refused connection or
a timeout of a few seconds, which the gate can tell apart from an answer.

### When the store cannot be reached

A broadcast is recorded to local scratch and only uploaded to the store when it
ends, so an unreachable store never interrupts a live stream. While it is away:

- **A finished broadcast is kept, not discarded.** It stays on scratch, and the
  server tries again when it next starts and once an hour after that. The
  Storage panel on the dashboard says when one is waiting, and the line goes
  away by itself once the archive lands. The remux happens on the store, so the
  streaming server never needs room for a second copy of the recording.
- **Pressing Clip says "Saving clips is not available right now."** within a few
  seconds. Nothing is half made, and the viewer's cooldown is not used up.
- **Nothing is deleted.** Retention, the size cap and the Delete button all have
  the store remove the files first and only then drop the row, so while it is
  away every recording stays listed and the next pass or the next press finishes
  the job. Stopping a share works the same way: the clip stays shared, and you
  are told, until the store confirms the public copy is gone.
- **The library still loads.** Listings never ask the store; only the videos and
  posters themselves are missing until it is back. The Storage panel says the
  store is not answering instead of showing its usage.

Nothing is expected of you beyond bringing the store back.

The retry at startup runs **beside** the server, not before it. Each waiting
recording is a whole broadcast to upload and remux, against a store that may
still be away, and the site would otherwise refuse connections for as long as
that took, at exactly the moment somebody restarted it to get the site back.
Only one retry runs at a time, and one waits while a broadcast is recording:
this is usually a small machine, and the live stream comes first.

## 4.6 Updating

Pull the newest images and rebuild:

```
docker compose pull
docker compose up -d --build
```

Your accounts survive updates because they live in a docker volume, not in the
container.

### Updating to the media store

Recordings and clips used to be a folder the gate and Caddy both mounted. They
now live behind the `store` service, and an install from before it needs three
things once:

1. **Two new keys in `.env`**, `SELFSTREAM_STORE_KEY` and
   `SELFSTREAM_STORE_READ_KEY`, each from `openssl rand -hex 32` and different
   from each other (see `.env.example`).
2. **Hand the existing volume to the store's user.** The files in `media_data`
   were written by the old gate as root, and the store runs as uid 10001. Build
   the new images, then, with the stack stopped, change the owner in one pass:

   ```
   docker compose build
   docker compose down
   docker compose run --rm --no-deps --user 0:0 --cap-add CHOWN \
       store chown -R 10001:10001 /media
   docker compose up -d
   ```

   Only the owner changes; no file is moved, rewritten or removed. If you
   pointed `media_data` at your own path, the same command covers it.
3. **Your override.** `SELFSTREAM_MEDIA_DIR` is no longer read, and neither the
   gate nor Caddy mounts the media volume any more, so drop any override of
   those mounts. An override that points `media_data` at a local path keeps
   working as it is. One that mounts a NAS export there should move to running
   the store on that machine instead (above).

Theater posters moved from the media volume to the gate's own data volume. Any
already stored are fetched from the projector again the next time they are
shown, and the old `art/` folder in the media volume can be deleted by hand.

If you run your own copy of the Caddyfile rather than the one in the repo, carry
two things across when updating past 0.21: every `reverse_proxy gate:8000` and
`forward_auth gate:8000` block imports the `to_gate` snippet, and each
`forward_auth` names its door (`/api/verify?scope=live` for `/live/*`,
`scope=art` for `/media/art/*`, `scope=media` for `/media/*`). Without the
scope, the gate treats a check as the members-only library and refuses guests
the live video.

With the media store, three blocks change, so compare yours with the repo's:
`/media/art/*` is a `handle` (not `handle_path`) that proxies to the gate;
`/media/*` and `/shared/*` proxy to `{$SELFSTREAM_STORE_UPSTREAM}` with
`header_up Authorization "Bearer {$SELFSTREAM_STORE_READ_KEY}"` and
`header_up -Cookie` in place of their `root` and `file_server`. Caddy then needs
`SELFSTREAM_STORE_UPSTREAM` and `SELFSTREAM_STORE_READ_KEY` in its environment,
and no longer mounts the media volume.

### Telling people what changed

After an update, everyone who signs in gets a small notice on the home page
naming the new version and listing what changed, with a button to dismiss it.
They see it once. Somebody who skipped three releases gets the newest one and
nothing else: it is a "here is what is new", not a version history.

The lines come from `NOTES` in `gate/changelog.py`, keyed by version. **A
version bump wants its entry in the same commit**, and the test suite fails if
the running version has none, so a silent release is caught before it ships.
Five lines is the whole budget and each one is a short sentence, because the
notice has to fit a phone screen without scrolling. Write them for a viewer:
what they can now do, not which file moved.

Nobody is greeted with a changelog on the way in. A new account is stamped with
the running version as it is created, so the notice only ever means "this
changed since you were last here".

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

Updating to 0.21.0 needs one more, for the same reason: the clip page is now
rendered by the gate so a shared link previews as the clip. In your Caddy config
replace the `handle /clip/*` block that rewrites to `/clip.html` with

```
handle /clip/* {
	reverse_proxy gate:8000
}
```

keeping it above the catch-all and below the `handle /shared/*` block, which
serves the video and its poster. Without this the clip page works
exactly as before and shared links keep the generic card.

## 4.7 Troubleshooting

- The page does not load at all. Check that the DNS record points at the server
  and that ports 80 and 443 are open in the firewall. Check `docker compose
  logs caddy` for certificate errors.
- The video never starts. Confirm OBS says it is streaming. Check
  `docker compose logs mediamtx` for a connection from your address. Make sure
  the OBS stream key matches the one shown in the dashboard's Stream key panel
  under **Connections**
  (it looks like `live?pass=...`).
- OBS cannot connect. The firewall rule for port 1935 may not match your current
  home IP. See `docs/01-vps-setup.md`, section 1.4.
