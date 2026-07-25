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

From then on you let other people in with invite codes: open the accounts page
at `/accounts`, generate a single-use code under **Invites**, and share it. They
redeem it from the login page ("have an invite?") to make their own viewer
account. No terminal, and no email is involved. See
`docs/06-accounts-and-chat.md` for the details.

There are two admin pages, and it is worth knowing which is which. `/accounts`
is the people: accounts, bans and invites. `/admin` is the channel: branding,
the schedule, chat rules, the stream key, the overlay, notifications, storage
and the recorded library.

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
Viewers can also clip the last 30 seconds while you are live, and those appear
under **Clips**, each with synced chat replay.

Recording recovers on its own. If the recorder ever dies or its file stops
growing mid-broadcast (for example, a rough reconnect on a long session), the
gate finalizes whatever it captured, starts a fresh recording while you stay
live, and backs off if failures repeat. You may see more than one VOD for a
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
