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
   and revoking one takes effect at once. There is no way in without an account:
   someone with the watch link still cannot get in without one you allowed.

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

6a. **Writes only come from this site.** Every request that changes something,
   and the chat socket, is refused unless the browser says it came from this
   site's own pages (the `Origin` and `Sec-Fetch-Site` headers). The cookie's
   SameSite=Lax setting already keeps it off other sites' forms, but a page on a
   sibling subdomain of the same domain counts as the same site and would get the
   cookie anyway; this closes that gap.

7. **The video is gated, not just the page.** This is the important one. Caddy
   does not serve a single video segment until it asks the gate to check the
   cookie. Even if someone found the raw stream URL, it returns nothing without a
   valid cookie. The lock is on the video, not only on the page that shows it.
   The same check guards the saved recordings and clips, so their files cannot
   be fetched without a session even by guessing their names.

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

The media store's two keys rotate the same way: put new values in `.env` (and in
the store's own `.env` if it runs on another machine) and run
`docker compose up -d`. The gate, Caddy and the store pick them up together, and
the old keys stop working at once.

## What is deliberately public

Four things answer a visitor with no session at all: the sign-in page with
invite registration, a published clip, and the link previews for your watch
page and for an invite link. All are deliberate, and none is video you have not
chosen to publish. Here is exactly how big each one is.

### Sign-in and invite registration

The sign-in page, and the two endpoints behind it: `/api/auth` signs in, and
`/api/register` makes a viewer account from an invite code. Both draw on the
same per-address allowance, so guessing passwords and guessing codes cannot be
alternated for two budgets. A code is three words from a list sized so that
three of them clear ten million combinations. It is claimed in one guarded
database write, so it makes exactly one account, and that account is only ever
a viewer.

An invite can travel as a link, `/join#<code>`. The code rides after the `#`,
and that part of an address is never sent to a server by a browser, so it is
not in Caddy's access log, not in the gate's, not in a `Referer`, and not in
anything a link preview fetches. The page reads it into the sign-up form and
takes it off the address bar and out of the history at once. What a leaked
link costs is one account, once, and revoking the code closes it.

### A published clip

A clip you publish is readable by anyone with its link and no session at all.
That is the whole point of the feature, so it is worth knowing its shape:

- It is **per clip**. There is no way to publish the library.
- It is **admin only**, and **off** until you turn it on for a specific clip.
- **Unsharing takes effect immediately.** The file stops being reachable, not
  just the page. If the media store cannot be reached at that moment, the clip
  stays shared and you are told, rather than the link being half revoked: the
  database only lets go of the token once the store has removed the file.
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
- **The link previews as itself.** Pasted into a chat app the card reads
  `"Big whiff" - Stream Clip` over your site name and the game the clip was cut
  from, with one frame of the clip as the picture. That is the clip's own
  poster, already public beside its video at `/shared/<token>.jpg`. A clip made
  before this was added has no game stamped on it, so its second line is the
  site name alone. An unknown or revoked token gets the generic card written
  into the page: it never confirms that a clip existed.
- Deleting a clip, by hand or by the two day sweep, removes the public copy too.

### Link previews

Paste your watch link into a chat app and it shows a card: the channel and what
this broadcast is called, what is being played, and a picture. A shared clip
link shows one too, described above, and so does an invite link. Every card is
built by the app fetching the page, and a preview fetcher never carries a
cookie, so those pages and their pictures have to answer without one.

- **The page itself, `/watch`.** What comes back is only the shell: the markup,
  the stylesheet, and the preview tags. It contains no video, no chat and no
  account data. The stream, the chat socket and the library each check the
  session separately, exactly as before, and a visitor without one is sent to
  the sign-in page the moment the page runs.
- **The picture, `/api/og-image.jpg`.** While a broadcast is running this is the
  current frame of it, the same 640px still the home page shows, refreshed every
  fifteen seconds. **Be clear on what that means: anyone holding your watch link
  can fetch that URL and see a frame of your stream without an account.** They
  cannot watch it. It is one still, at the rate the app captures them, with no
  sound. Between broadcasts the app deletes the frame, so this falls back to the
  channel's static card and no frame of anything is reachable.
- The preview tags carry your site name, your stream title or description, and
  what is being played. Nothing else about the channel, no account names, and
  nothing at all about your viewers.
- **During a theater session that line is what is on the projector**: a film by
  name and year, or an episode as its show, that show's year, and its place in
  the run (`playing Silo (2023) S3E1`). The episode's own title is not in it.
  This is
  the one place a title leaves the account wall: `/api/theater`, where the watch
  page reads it, refuses anyone without a session. Anyone holding your watch
  link can therefore see what you are showing tonight without an account. That
  is deliberate, so a share says something worth reading, and it tells a
  stranger no more than the frame beside it already does. A session hides the
  game label entirely, between titles included: the room is at a film night,
  not back to whatever was set for some earlier broadcast.
- **The clip page, `/clip/<token>`.** Rendered by the app for the same reason
  and just as much a shell: the tags, and markup that then asks
  `/api/shared/<token>` what to show. That endpoint answers a dead token with a
  404, so rendering the page opens nothing the link did not already reach.
- **The invite page, `/join`.** The sign-in page, rendered with a card that
  reads "You're invited to" and your site name, over your channel description
  (or "Make an account to watch."), with the channel's static picture. No frame
  of the stream, never the code (it is in the fragment, which the fetcher does
  not send), and a `noindex` tag.
- The country gate, if you set one, applies to all of them. So does the rate
  limiting and the fail2ban jail below.

If a frame of your live stream, or the name of tonight's film, reaching whoever
holds the link is not a trade you want, keep the link to people you would tell
anyway: there is no switch for either, but nothing is fetched unless somebody
pastes the link somewhere that unfurls it.

Nothing else is reachable without signing in. Publishing no clips and sharing no
links leaves the site closed.

One smaller thing is also public, and it is harmless: `/api/status` reports the
running version of the app, so the dashboard footer can show it and an external
check can read it without a session. While a broadcast is running it also
reports what you are playing, which the room's slate reads; that is the same label
the link preview above already puts in front of anyone holding the link, so it
is public either way. This is accepted rather than hidden: the
source is public under the AGPL, so the version is not a secret, and knowing it
buys an attacker nothing they could not already read in the code.

## Content security policy

Every page except the versioned assets is served with a strict
`Content-Security-Policy` from the `Caddyfile`: scripts, styles, fonts, images,
video and connections may come from this site and nowhere else, and no page
carries an inline script, an inline style block or a `style` attribute. A
script somebody managed to get into a page would have nowhere to load from and
no way to run inline. The few sources past `'self'` each have a reason written
beside them in the `Caddyfile`: `blob:` for the video player (Media Source
Extensions and its worker) and the avatar crop, `data:` for images the browser's
own widgets use, and the site's own `wss:` address for the chat socket, which
some older Safari releases do not count as `'self'`.

## Framing

The same policy carries `frame-ancestors 'self'`, so only this site can put
these pages in a frame. The dashboard does exactly that with the watch page, to
show the streamer their own broadcast, and the watch page listens for messages
from whatever framed it (it uses them to hide or show the video). Both halves are
same-origin checked: the browser refuses the frame, and the page ignores any
message that did not come from this site.

## Browser headers

Every response also carries four headers from Caddy:

- `Strict-Transport-Security: max-age=31536000`. After one visit a browser uses
  HTTPS for this host for a year, even if someone types or links `http://`. It
  covers this host only (no `includeSubDomains`, no preload list), so other
  names under the same domain are unaffected.
- `X-Content-Type-Options: nosniff`. A browser treats each file as the type the
  server says it is, and never guesses a script out of something else.
- `Referrer-Policy: strict-origin-when-cross-origin`. A link out of the site
  tells the other site where the visitor came from as the origin only, never a
  path.
- `Permissions-Policy: camera=(), microphone=(), geolocation=(), payment=()`.
  The site uses none of those, so nothing on any page can be granted them.

## The overlay key

The OBS chat overlay cannot sign in, so it authenticates with a long random key in
its URL (see `docs/08-overlay.md`). It is worth being clear about what that key
does and does not unlock:

- It is **read-only**. The overlay receives chat, join notices, and clip and
  highlight alerts. It can never send chat or run a command.
- The overlay **test buttons** (which send a fake chat, join, clip or highlight so
  you can line up your browser source) are **admin only**, and the fake events go
  to the overlay alone. A test never reaches real chat or the chat history.
- None of this adds anything a stranger can reach. The key is a bearer secret:
  treat the URL like a password, and regenerate it from the dashboard if it leaks.

## The projector key

Theater (see `docs/11-theater.md`) adds one more service and one more key. The
projector runs on your own media machine and cannot sign in, so it authenticates
with a long random key the same way the overlay does.

- **It is off unless you turn it on.** Without `SELFSTREAM_THEATER=1` every
  theater route answers 404, the projector socket closes every connection with
  4404 before it reads a key, and the pages show no theater controls. A session
  left open when theater was switched off no longer stops recording or clips.

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
  and only to signed-in viewers. Item ids, paths and your media server's address never leave the
  gate. `/api/status`, the one payload every visitor's page polls, is unchanged.
- **A session suppresses two write paths.** While it is open nothing is
  recorded and clips are refused outright, so a film you put on for the room
  cannot be turned into a file or a shareable cut by anyone, including you.

The key is a bearer secret: treat it like a password, and regenerate it from the
dashboard if it leaks.

## Keeping those keys out of the logs

Both of those keys travel in a query string, because neither a browser source
nor the projector can send a header. That makes them the one credential this
system writes down every time it is used, and logs travel: they roll, they are
compressed, they go into backups, and they get pasted into bug reports. Two
redactions, on both sides of the proxy, keep them out:

- **The gate** strips the query off its own HTTP and WebSocket access lines
  (`RedactQueryStrings` in `gate/config.py`). The path is kept, so the line is
  still worth having.
- **Caddy** deletes the `key` and `overlay` parameters from the `uri` field of
  its JSON access log, with a `format filter` in the `Caddyfile`. Other
  parameters are left alone, so the log still shows what was asked for.

Both are needed: they write separate files, and one alone leaves the key in the
other. If you have logs from before this, treat the keys in them as burned:
regenerate both from the dashboard and delete the old files.

## The media store and its two keys

Recordings and clips live in the media store (`store/`), a small service that
answers over HTTP on one port and publishes none to the internet. It sits on the
same private docker network as the gate by default, and can live on another
machine entirely (`docs/04-run.md`). Either way, what it trusts is two keys:

- **The write key** (`SELFSTREAM_STORE_KEY`) is the gate's. It uploads, remuxes,
  hard-links a clip into the public area, deletes, and lists. Only the gate
  holds it.
- **The read key** (`SELFSTREAM_STORE_READ_KEY`) is Caddy's. It fetches one file
  by its exact name and does nothing else: it cannot write, delete, list a
  directory or read the usage. Caddy only uses it after its own checks, the
  session check for `/media/*` and nothing but the country gate for the public
  `/shared/*`, so holding it adds nothing a visitor could not already reach.

The store refuses to start if either key is missing, shorter than 32
characters, or the same as the other, and compares them in constant time. It
answers only names of the one shape the gate writes (a row id or a share token
and `.mp4` or `.jpg`) in its three areas, so no request can name a path outside
them, and it never lists a directory over a read, so the public area's tokens
cannot be discovered. Caddy strips the viewer's cookie before the request
reaches it: the store has no use for a session and is never handed one, so a
store on another machine learns nothing about who is watching and holds nothing
that could be replayed against the site. It runs as an unprivileged user on a
read-only root filesystem with every capability dropped, so the only thing it
can write is its own volume.

## Putting the media store on another machine

The store is often happier on the machine with the disks, and
`store/docker-compose.yml` is an example of running it there behind a Tailscale
sidecar. What that changes, security-wise, is worth being explicit about:

- **Nothing new listens on the streaming server.** The gate and Caddy open the
  connections; the storage machine never reaches into the streaming server, and
  its public surface is unchanged.
- **The storage machine exposes one port, to your tailnet only.** The store sits
  on an internal docker network with no route out, and the sidecar forwards
  tcp:8080 to it and nothing else. Nothing is forwarded at the router.
- **Say who may reach it in your tailnet policy.** Tag the store's node and the
  streaming server, and allow exactly that one direction on that one port, for
  example:

  ```json
  "grants": [
    {"src": ["tag:upperroom-gate"], "dst": ["tag:upperroom-store"], "ip": ["tcp:8080"]}
  ]
  ```

  Every other machine on your tailnet, and the store node itself, then has no
  way in or out through it.
- **A compromised streaming server reaches that one port and no further.** Its
  `.env` holds both keys, so an attacker who owns it can read, replace or delete
  the recordings and clips. That is the extent of it: no shell on the storage
  machine, no other directory, no other service, no mount to walk. The store
  cannot write outside its own volume even if the attacker controls every byte
  it is sent, and nothing it stores is ever executed. Give the volume a quota
  (a dedicated dataset or partition) so a runaway or hostile writer cannot fill
  the disk your other services share.
- **The traffic is encrypted end to end.** Tailscale is WireGuard underneath, so
  the plain HTTP between the containers never crosses a network in the clear.
- **A storage outage is not a data loss.** Recording is written to local scratch
  first and only uploaded when the broadcast ends, so an unreachable store never
  interrupts a live stream. If the upload fails the recording is kept and
  retried rather than discarded, and nothing is deleted while the store cannot
  confirm it (`docs/04-run.md`, "When the store cannot be reached").

## Go-live notifications

When you go live, the server sends a push notification to every device whose
owner turned it on in Options, admins included. No email is collected anywhere:
no account has an address, no page asks for one, and the server keeps none.
Updating from a release that had go-live email empties every stored address and
the old Discord webhook URL on the first start, and logs only how many.

- **A push travels through the browser maker's push service**: Apple for
  Safari, iPhone and iPad, Google for Chrome, Brave and most other Chromium
  browsers, Mozilla for Firefox, Microsoft for Edge on Windows. That is how Web Push works; a browser only
  takes pushes from its own service. Your server encrypts each message for the
  one device it is going to (RFC 8291), so the service carries it without being
  able to read it.
- **What the push service can see**: that a message went to a device that
  signed up through your site, when, and how big it was. Your server identifies
  itself to the service with a signed token (VAPID, RFC 8292) that names
  `SELFSTREAM_SITE_URL` as its contact, which is why push is off until that is
  set.
- **What a notice says**: your site name, the stream title, and the game when
  one is set. It carries no link: a tap opens `/watch` on your site, which asks
  for a session like always.
- **The server only ever sends to those services.** A subscription is a URL the
  browser hands over, and the server posts to it later. If any URL were kept, a
  signed-in account could make your server send requests wherever it liked, into
  your own network included. So every subscription is checked when it is saved
  and again before each send: `https://`, no other port, no credentials in it,
  and a host that is exactly `fcm.googleapis.com` or ends in
  `.push.services.mozilla.com`, `.push.apple.com` or `.notify.windows.com`.
  Anything else is refused. Redirects are never followed. The keys that come
  with it must be a real P-256 public key and a 16 byte secret.
- **A subscription URL is a credential on its own**, so it is never logged: a
  failed send logs the push service's host and its status code, nothing more.
  One the service reports gone (404 or 410) is deleted. Each account keeps at
  most ten, the newest, and deleting an account deletes its subscriptions.
- **Only a signed-in account can sign a device up**, the cross-site write check
  applies, and turning it on or off is rate limited per address. Turning a
  device off removes only your own subscription; a browser that changes hands
  moves its subscription to whoever turns it on next.
- **The server's push key pair lives in the database.** It is made on the first
  start. The private half is never logged and no endpoint returns it; browsers
  only ever get the public half. **Database backups therefore hold the private
  key** (`docs/10-backup.md`): with a backup, someone could send notifications
  that look like yours to the devices signed up in it.
- **The dashboard's test** goes only to the devices on the admin's own account,
  and is rate limited. With the dashboard switch off you go live quietly and
  nothing is sent.

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
- **Sign-in attempts are rate limited per address**, five a minute. Redeeming
  an invite code draws on the same allowance, so guessing codes and guessing
  passwords cannot be alternated for two budgets.
- **Posting a comment is rate limited per address**, ten a minute on its own
  budget. A comment lands in a thread everyone reads, so one caller, however
  many sessions they hold, cannot post them in a loop.
- **Highlighting a message is rate limited per address**, ten a minute on its
  own budget. A highlight spends points and posts to chat, so it is a write path
  worth capping, but it draws on its own allowance rather than the sign-in one.
- **Changing your password is rate limited per address**, five a minute on its
  own budget. A valid session is needed to reach that endpoint, but that is
  exactly the case worth guarding: the limit stops a borrowed session from brute
  forcing the current-password check on its way to setting a new one.
- **Turning notifications on or off is rate limited per address**, twenty a
  minute, and the dashboard's test push five a minute, each on its own budget.
- **A highlighted message can be moderated like any other.** A highlight is a
  chat message with a spotlight: it goes in the same admin chat log and carries
  a message id, so a moderator can delete it, and it obeys the same word filter,
  bans and timeouts. Spending points is never a way to post something a
  moderator cannot remove.
- **Only the address your own proxy observed is trusted.** `X-Forwarded-For` is
  something a caller can write, so the rate limiter and the country gate read
  the entry Caddy added, never one that arrived from outside. Caddy hands the
  gate its own resolved visitor address, trusting only proxies on private
  ranges, so a second proxy in front of it on the same box (a host-level Caddy
  shared by several sites) still gives the gate the real visitor rather than its
  own private address.
- **A socket cannot be used to stall the server.** A chat frame larger than
  8 KB is dropped unread, the server refuses any WebSocket frame over 4 MB, and
  a socket whose connection has failed is closed rather than read again.
- **Taking a role away takes effect at once.** Deleting an account closes its
  chat, and removing someone's admin role reaches their open sockets, so neither
  keeps powers until they happen to reconnect. A moderator cannot rename an
  admin's clip or delete an admin's comment, the same line chat draws.
- **The room can be capped.** The room limit on the dashboard's Manage Stream screen sets how
  many people may pull the live video at once; `0`, the default, is no limit.
  Caddy already asks the gate to authorize every video segment, so that is
  where the limit is applied. Each check names which door it is for in the
  Caddyfile itself (`/api/verify?scope=live`), so an oddly encoded path cannot
  slip past uncounted. The limit applies to the live stream only: saved
  broadcasts and clips are files on disk and are never refused by it. An admin
  is never counted and never turned away, so the limit cannot lock you out of
  your own broadcast, and someone already watching is never cut off mid-title.

  **Be clear about what this is.** It is a bandwidth guard, not an access
  control. It counts distinct accounts, so one signed-in member could pull
  several copies at once and still occupy a single place. Everyone who can reach
  the video already holds an account you issued; if you need to stop a specific
  person, remove or ban the account rather than tightening this number.

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
