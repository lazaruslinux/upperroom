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

## 4.5 Updating

Pull the newest images and rebuild:

```
docker compose pull
docker compose up -d --build
```

Your accounts survive updates because they live in a docker volume, not in the
container.

## 4.6 Troubleshooting

- The page does not load at all. Check that the DNS record points at the server
  and that ports 80 and 443 are open in the firewall. Check `docker compose
  logs caddy` for certificate errors.
- The video never starts. Confirm OBS says it is streaming. Check
  `docker compose logs mediamtx` for a connection from your address. Make sure
  the OBS stream key is exactly `live?user=publisher&pass=YOUR_PUBLISH_PASS`.
- OBS cannot connect. The firewall rule for port 1935 may not match your current
  home IP. See `docs/01-vps-setup.md`, section 1.4.
