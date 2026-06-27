# 5. Security model

This explains what protects the stream and why, so you can judge it for
yourself rather than take it on faith.

## The layers

1. **Rate limited login.** The login endpoint accepts only a handful of attempts
   per address each minute, and over its limit it returns a 429. This slows
   automated scanners and password guessing scripts that try to hammer the
   login.

2. **Named accounts, no sign ups.** There is no public registration. Every
   account is created by the admin with `manage.py`. Someone with the link still
   cannot get in without an account you made.

3. **Passwords are hashed.** Passwords are never stored as written. Each one is
   run through scrypt with a random per account salt. Even if someone got the
   database file, they could not read the passwords out of it.

4. **Steady login timing.** When a username does not exist, the server still
   runs a throwaway hash check. That way the response takes about the same time
   whether or not the username is real, so an attacker cannot learn which
   usernames exist by timing the replies.

5. **Rate limited login.** No more than five attempts per address per minute.
   Past that, the server returns a wait and try again message.

6. **Signed session cookies.** After a correct login, the server sets a cookie
   that is a signed token. The signature uses a secret only the server knows, so
   the cookie cannot be forged or edited. It is marked HttpOnly, so page scripts
   cannot read it, Secure, so it only travels over HTTPS, and it expires after a
   few hours.

7. **The video is gated, not just the page.** This is the important one. Caddy
   does not serve a single video segment until it asks the gate to check the
   cookie. Even if someone found the raw stream URL, it returns nothing without a
   valid cookie. The lock is on the video, not only on the page that shows it.

8. **Chat cannot inject code.** Chat messages are placed into the page as plain
   text, never as HTML, so nobody can post a message that runs a script in
   someone else's browser.

9. **Only you can publish.** The ingest port accepts your home IP only, and even
   from there it requires the publish password. Nobody else can push video into
   your channel.

10. **Video never touches Cloudflare.** With the DNS record set to grey cloud,
    the stream goes straight from your server to the viewer. Fewer parties see
    the traffic, and you stay clear of Cloudflare's rules about video on the free
    plan.

11. **Country lock.** Every request is checked against a country allow list
    before it reaches anything. By default only United States addresses are
    allowed and everyone else gets a 403. Because the video deliberately does
    not pass through Cloudflare, this is enforced on the server itself using a
    free DB-IP country database baked into the gate image. Set the list with
    `SELFSTREAM_ALLOWED_COUNTRIES` in `.env`, or leave it blank to allow every
    country. The database is refreshed whenever you rebuild the gate image.

## Rotating access

To cut someone off, change their password or delete their account:

```
docker compose exec gate python manage.py passwd alice
docker compose exec gate python manage.py deluser alice
```

Existing sessions still work until the cookie expires, within a few hours. To
force everyone to sign in again immediately, change `SELFSTREAM_JWT_SECRET` in
`.env` and restart:

```
docker compose up -d
```

That invalidates every existing cookie at once.

## What this does not do

- It does not hide your server's IP. The firewall and the login are the
  defense, not secrecy of the address.
- It does not encrypt chat end to end. Messages pass through your server, which
  is fine for a private stream you run yourself.
- It is built for one operator and a small audience. It is not trying to be a
  public platform with thousands of strangers.

## Optional extra hardening

If you want another layer on the server itself, install fail2ban to ban
addresses that fail SSH repeatedly:

```
apt install -y fail2ban
```

The defaults already watch SSH. This is about protecting the server, separate
from the stream's own login.
