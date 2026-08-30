# 5. Security model

This explains what protects the stream and why, so you can judge it for
yourself rather than take it on faith.

## The layers

1. **Rate limited login.** The login endpoint accepts only a handful of attempts
   per address each minute, and over its limit it returns a 429. This slows
   automated scanners and password guessing scripts that try to hammer the
   login.

2. **Named accounts, no open sign ups.** There is no public registration. The
   first account is made by the one-time setup wizard, which closes for good the
   moment it exists. After that, an account can only be created by an admin, or
   by someone redeeming a single-use invite code an admin generated. Codes are
   claimed with a single guarded write, so one code can never make two accounts,
   and revoking one takes effect at once. Someone with the link still cannot get
   in without an account you allowed.

3. **Passwords are hashed.** Passwords are never stored as written. Each one is
   run through scrypt with a random per account salt. Even if someone got the
   database file, they could not read the passwords out of it.

4. **Steady login timing.** When a username does not exist, the server still
   runs a throwaway hash check. That way the response takes about the same time
   whether or not the username is real, so an attacker cannot learn which
   usernames exist by timing the replies.

5. **One rate limit across every way in.** The same per-address limit covers
   signing in, redeeming an invite, and the setup wizard, so none of them can be
   used to get around the others.

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
   from there it requires the stream key managed on the admin dashboard. Nobody
   else can push video into your channel, and you can rotate the key at any time.

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

## What is deliberately public

Two things answer a visitor with no session at all. Both are deliberate, and
neither is video you have not chosen to publish. Here is exactly how big each
one is.

### A published clip

A clip you publish is readable by anyone with its link and no session at all.
That is the whole point of the feature, so it is worth knowing its shape:

- It is **per clip**. There is no way to publish the library.
- It is **admin only**, and **off** until you turn it on for a specific clip.
- **Unsharing takes effect immediately.** The file stops being reachable, not
  just the page.
- The link is the entire credential: it looks like
  `https://your-domain/clip/<token>`, where the token is a long random value,
  and the video file itself is served at `/shared/<token>.mp4`. The folder it
  lives in cannot be listed, so links cannot be discovered by guessing.
- Unsharing and re-sharing mints a **new** token, so a link that was ever
  revoked stays dead even if the clip is shared again.
- The country gate, if you set one, applies to public clips too: a shared link
  only works from the allowed countries.
- A published clip is **video only**. No chat replay, no comments, and it does
  not name who made it. Nobody who spoke in your chat is published by it.
- Deleting a clip, by hand or by the two day sweep, removes the public copy too.

### The watch page's link preview

Paste your watch link into a chat app and it shows a card: the channel and what
this broadcast is called, what is being played, and a picture. That card is
built by the app fetching the page, and a preview fetcher never carries a
cookie, so two things have to answer without one.

- **The page itself, `/watch`.** What comes back is only the shell: the markup,
  the stylesheet, and the preview tags. It contains no video, no chat and no
  account data. The stream, the chat socket and the library each check the
  session separately, exactly as before, and a visitor without one is sent to
  the sign-in page the moment the page runs.
- **The picture, `/api/og-image.jpg`.** While a broadcast is running this is the
  current frame of it, the same 640px still the home card shows, refreshed every
  fifteen seconds. **Be clear on what that means: anyone holding your watch link
  can fetch that URL and see a frame of your stream without an account.** They
  cannot watch it. It is one still, at the rate the app captures them, with no
  sound. Between broadcasts the app deletes the frame, so this falls back to the
  channel's static card and no frame of anything is reachable.
- The preview tags carry your site name, your stream title or description, and
  what is being played. Nothing else about the channel, no account names, and
  nothing at all about your viewers.
- **During a theater session that line is the film, by name and year.** This is
  the one place a title leaves the account wall: `/api/theater`, where the watch
  page reads it, refuses anyone without a session. Anyone holding your watch
  link can therefore see what you are showing tonight without an account. That
  is deliberate, so a share says something worth reading, and it tells a
  stranger no more than the frame beside it already does. A session hides the
  game label entirely, between titles included: the room is at a film night,
  not back to whatever was set for some earlier broadcast.
- The country gate, if you set one, applies to both. So does the rate limiting
  and the fail2ban jail below.

If a frame of your live stream, or the name of tonight's film, reaching whoever
holds the link is not a trade you want, keep the link to people you would tell
anyway: there is no switch for either, but nothing is fetched unless somebody
pastes the link somewhere that unfurls it.

Nothing else is reachable without signing in. Publishing no clips and sharing no
links leaves the site closed.

One smaller thing is also public, and it is harmless: `/api/status` reports the
running version of the app, so the dashboard footer can show it and an external
check can read it without a session. While a broadcast is running it also
reports what you are playing, which the home card reads; that is the same label
the link preview above already puts in front of anyone holding the link, so it
is public either way. This is accepted rather than hidden: the
source is public under the AGPL, so the version is not a secret, and knowing it
buys an attacker nothing they could not already read in the code.

## Framing

Every page except the versioned assets is served with
`Content-Security-Policy: frame-ancestors 'self'`, so only this site can put
these pages in a frame. The dashboard does exactly that with the watch page, to
show the streamer their own broadcast, and the watch page listens for messages
from whatever framed it (it uses them to hide or show the video). Both halves are
same-origin checked: the browser refuses the frame, and the page ignores any
message that did not come from this site.

## The overlay key

The OBS chat overlay cannot sign in, so it authenticates with a long random key in
its URL (see `docs/08-overlay.md`). It is worth being clear about what that key
does and does not unlock:

- It is **read-only**. The overlay receives chat, join notices, clip and highlight
  alerts, and your ticker line. It can never send chat or run a command.
- The **ticker** you set on the dashboard travels **only** over this key-authed
  connection. It is deliberately kept off `/api/status` and every other public
  endpoint, so a logged-out visitor cannot read your ticker before it is on the
  broadcast.
- The overlay **test buttons** (which send a fake chat, join, clip or highlight so
  you can line up your browser source) are **admin only**, and the fake events go
  to the overlay alone. A test never reaches real chat or the chat history.
- None of this adds anything a stranger can reach. The key is a bearer secret:
  treat the URL like a password, and regenerate it from the dashboard if it leaks.

## The projector key

Theater (see `docs/11-theater.md`) adds one more service and one more key. The
projector runs on your own media machine and cannot sign in, so it authenticates
with a long random key the same way the overlay does.

- **It only ever connects outward.** The projector opens the connection to your
  gate and the publish to your ingest. Your media machine listens on nothing,
  needs no open port, and is not reachable from the internet. There is no
  inbound path this feature adds to the machine your library is on.
- **It is not seeded from anything.** Unlike the publish key, which is seeded
  from `PUBLISH_PASS` on an upgrade, the projector key exists only once you press
  Regenerate. Until then the socket refuses every connection, including one that
  sends an empty key.
- **One projector at a time**, and the newest wins. Regenerating the key
  disconnects the connected projector immediately rather than waiting for it to
  reconnect and fail.
- **Connections to it are rate limited** per address, on their own budget, so
  guessing the key over that socket cannot spend the allowance that protects
  password guessing.
- **Nothing about your library is public.** What a session puts on a public
  endpoint is the title, year, runtime, synopsis and poster of what is playing,
  and only to signed-in viewers (guests included, since watching is what a guest
  pass is for). Item ids, paths and your media server's address never leave the
  gate. `/api/status`, the one payload every visitor's page polls, is unchanged.
- **A session suppresses two write paths.** While it is open nothing is
  recorded and clips are refused outright, so a film you put on for the room
  cannot be turned into a file or a shareable cut by anyone, including you.

The key is a bearer secret: treat it like a password, and regenerate it from the
dashboard if it leaks.

## What this does not do

- It does not hide your server's IP. The firewall and the login are the
  defense, not secrecy of the address.
- It does not encrypt chat end to end. Messages pass through your server, which
  is fine for a private stream you run yourself.
- It is built for one operator and a small audience. It is not trying to be a
  public platform with thousands of strangers.

## What the app does about abuse on its own

You do not have to configure any of this; it is on by default.

- **Request bodies are capped** at 64 KB for the API and 3 MB for the avatar
  upload. Nothing here needs more, and without a cap a stranger can make the
  server buffer and parse megabytes before it can say no.
- **Sign-in attempts are rate limited per address**, five a minute. Redeeming a
  guest pass draws on the same allowance, so guessing codes and guessing
  passwords cannot be alternated for two budgets.
- **Issuing a guest challenge question has its own, larger allowance**, since a
  visitor legitimately asks for several while filling the form in.
- **Highlighting a message is rate limited per address**, ten a minute on its
  own budget. A highlight spends points and posts to chat, so it is a write path
  worth capping, but it draws on its own allowance rather than the sign-in one.
- **Changing your password is rate limited per address**, five a minute on its
  own budget. A valid session is needed to reach that endpoint, but that is
  exactly the case worth guarding: the limit stops a borrowed session from brute
  forcing the current-password check on its way to setting a new one.
- **A highlighted message can be moderated like any other.** A highlight is a
  chat message with a spotlight: it goes in the same admin chat log and carries
  a message id, so a moderator can delete it, and it obeys the same word filter,
  bans and timeouts. Spending points is never a way to post something a
  moderator cannot remove.
- **Only the address your own proxy observed is trusted.** `X-Forwarded-For` is
  something a caller can write, so the rate limiter and the country gate read
  the entry Caddy added, never one that arrived from outside.

## Optional extra hardening

If you want another layer on the server itself, install fail2ban:

```
apt install -y fail2ban
```

The defaults already watch SSH, which is worth having on any box with a public
address.

### Banning web abuse at the firewall

The limits above are enforced by the application, which means an abusive caller
still costs it a worker and a database read every time. fail2ban can block a
repeat offender in the kernel instead, where they cost nothing. Caddy writes a
JSON access log to `logs/caddy/access.log` for exactly this.

Create `/etc/fail2ban/filter.d/upperroom-abuse.conf`:

```
[Definition]
failregex = ^\{.*"client_ip":"<HOST>".*"status":(?:429|413).*\}$
ignoreregex =
datepattern = "ts":{EPOCH}
```

And `/etc/fail2ban/jail.d/upperroom.conf`, with `logpath` pointing at wherever
you checked the project out:

```
[upperroom-abuse]
enabled  = true
logpath  = /path/to/upperroom/logs/caddy/access.log
filter   = upperroom-abuse
port     = http,https
maxretry = 10
findtime = 600
bantime  = 1800
```

Then `systemctl restart fail2ban` and check it with
`fail2ban-client status upperroom-abuse`.

This deliberately matches only 429 (a rate limit the app already enforced) and
413 (a body larger than anything here accepts). It never matches a plain failed
login, so somebody fumbling their password is not banned; they would have to
exhaust the rate limiter ten times over to qualify. Every rate limit emits the
same 429, so the highlight and password-change limits are caught by this filter
exactly like the sign-in one, with no change to the rule.
