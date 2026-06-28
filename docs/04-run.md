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
- Use a long, random `PUBLISH_PASS`. You only type it into OBS once.
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

## 4.3 Create accounts

There are no public sign ups. You create every account. Make yourself an admin
first:

```
docker compose exec gate python manage.py adduser yourname --admin
```

It asks for a password. Then make accounts for the people you want to let in:

```
docker compose exec gate python manage.py adduser alice --name "Alice"
```

See `docs/06-accounts-and-chat.md` for the full set of account commands.

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

To stop recordings from filling the disk, only the most recent
`SELFSTREAM_VOD_KEEP` are kept (20 by default); older ones are deleted. You can
also delete any VOD or clip by hand from the admin dashboard's **Content**
section. Per-role daily clip limits are set in the home **Settings** menu.

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
  the OBS stream key is exactly `live?user=publisher&pass=YOUR_PUBLISH_PASS`.
- OBS cannot connect. The firewall rule for port 1935 may not match your current
  home IP. See `docs/01-vps-setup.md`, section 1.4.
