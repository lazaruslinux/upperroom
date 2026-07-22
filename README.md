# upperroom

Self hosted, single channel live streaming with accounts and chat. You
broadcast from OBS. Your viewers open one link, sign in with an account you made
for them, and watch in 1080p60 with live chat and a list of who else is
watching. No third party streaming service, no public sign ups. You run the
whole thing.

## What it does

- Ingests your stream from OBS over RTMP.
- Republishes it as low latency HLS, roughly two to five seconds behind live.
- Serves one gated watch page at your own domain.
- Logs viewers in with named accounts that only you can create.
- Runs live chat and shows a live list of who is watching, with a count.
- Records every broadcast as a VOD and lets viewers clip the last 30 seconds,
  both with synced chat replay and per-item view counts.
- Has an admin role and a separate moderator role, with chat commands
  (`/mod`, `/timeout`, `/ban`, `/del`) and dashboards for each.
- Provides a transparent chat overlay you add to OBS as a browser source, so the
  broadcast itself shows chat, joins, and clip alerts. See `docs/08-overlay.md`.
- Gives viewers channel points for watching live, which they spend on rewards you
  define; redemptions post in chat and on the overlay. See `docs/09-points.md`.

It does not transcode. Your computer does the encoding inside OBS, so the
server only repackages the video and stays light.

## How it fits together

```
OBS (your PC)
  |  RTMP, accepted only from your home address
  v
MediaMTX  ------------->  repackages to low latency HLS
  |
Caddy  --  checks the session cookie before serving any video
  |  HTTPS at your domain
  v
Viewer's browser  --  sign in, then video plus chat plus presence
```

Three containers:

- `mediamtx` receives OBS and produces HLS.
- `gate` is a small FastAPI service for login, sessions, chat, and presence.
- `caddy` terminates TLS, serves the pages, and guards the video.

## Quick start

You need a small Linux server with a public IP, a domain you control, and a
free Cloudflare account. The tutorials in `docs/` walk through each part.

1. Prepare the server: `docs/01-vps-setup.md`
2. Point your domain at the server with DNS: `docs/02-cloudflare.md`
3. Copy the config and fill it in:
   ```
   cp .env.example .env
   ```
   Open `.env` and set every value. The file explains each one.
4. Build and start it:
   ```
   docker compose up -d --build
   ```
5. Create your first account, an admin:
   ```
   docker compose exec gate python manage.py adduser yourname --admin
   ```
6. Point OBS at the server: `docs/03-obs.md`, then go live.

More detail on running and updating is in `docs/04-run.md`. Accounts and chat
are explained in `docs/06-accounts-and-chat.md`.

## Try it (demo mode)

To see the whole thing working without OBS or making accounts, bring it up in
demo mode:

```
docker compose --profile demo up -d
```

That seeds demo accounts and starts a synthetic broadcast, so within a minute you
have a live stream with chat and a populated admin dashboard. Sign in as `demo` /
`demodemo123`. The plain `docker compose up -d` never starts the demo containers,
so a real install is unaffected. Full details, credentials, and teardown are in
`docs/07-demo.md`.

## Configuration

Every setting lives in `.env`, which is never committed. See `.env.example` for
the full list and what each value does.

## Security model

In short: the video is never served without a valid session cookie, the cookie
is only issued after a correct username and password, and the ingest port only
accepts your home address. Passwords are stored as scrypt hashes. The full
explanation is in `docs/05-security.md`.

## License

MIT. See `LICENSE`.

## A note on how this was built

Parts of this project were written with the help of Claude Code. The design
choices, the review, and running it are my own.
