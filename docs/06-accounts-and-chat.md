# 6. Accounts, chat, and presence

## First-run setup

On a brand new install no account exists yet, so the login page sends you to a
one-time setup wizard at `/setup`. Enter a username, a display name, a password,
and a site name, then create the account. This first account is the
admin, you are signed in immediately, and the wizard disappears for good the
instant the account exists (the server refuses it from then on, not just the
page). There is nothing to run in a terminal.

## Letting people in with invites

Everyone watches with an account; there is no way in without one. After setup
you add everyone else with single-use invite codes, from **Invite codes** in the
dashboard's **People** section:

- **Generate a code** with the **Generate code** button. You can add an optional
  label ("who it's for") to help you keep track. Each code is a short, readable
  string of three words like `ember-quiet-harbor`.
- **Send it as a link.** The new code shows with a **Copy invite link** button,
  and every code still good has a **Copy link** on its row. The link looks like
  `https://watch.example.com/join#ember-quiet-harbor`. Opening it shows the
  join form with the code already filled in; somebody already signed in is
  taken home instead. Pasted into a chat app it previews as an invite to your
  site, and never shows the code.
- **Or share just the code** with one person, however you like. There is no
  email. They **redeem it** from the login page: under the sign-in form is a
  "Have an invite code?" link that reveals a short join form (code, username,
  display name, password).
- Redeeming makes them a **viewer** account and signs them in. A code can never
  create an admin or a moderator.
- Each code works **once**. After it is redeemed the page shows who used it and
  when. You can **Revoke** a code that has not been redeemed yet, which keeps the
  row for the record but stops it from ever being used.
- Once a code is spent, either used or revoked, a **Remove** button appears on
  it, and **Clear used codes** sweeps all of them at once. An active code has to
  be revoked before it can be removed, so removing can never quietly un-issue a
  code somebody is still holding. Removing a code does not affect the account it
  created; the account keeps its own record of where it came from.

The code in a link sits after the `#`. Browsers never send that part of an
address to a server, so it does not end up in your server's logs or in a link
preview, and the page takes it off the address bar as soon as it has read it.
Treat an invite link like the code it carries: whoever opens it first gets the
account.

Once somebody has an account, the watch link is all they need: see **Send the
link** under the dashboard below.

## Managing people

If your account has the admin flag, sign in and open the dashboard at `/admin`
(**Dashboard** in the menu on every page). Its **People** section lists every
account, then who is banned, then the invite codes. With no terminal you can:

- **Create an account** with **New account**. Enter a username and a password,
  optionally a display name, tick admin if you want, and it is made.
- **Edit any account** with **Edit**: reset its password, or grant and remove
  the admin or moderator role (the two are independent).
- **Delete an account**, from its **Edit** panel, which also clears its watch
  history and chat log.
- See each person's **watch activity** (when they watched and for how long) and
  their **chat history** from the last 7 days, under the **Activity** button.
- Review bans and **Lift the ban** on any of them.
- Generate, copy, revoke and remove **invites**.

Two things you deliberately cannot do here.

You cannot change somebody's **display name**. You choose the starting one when
you create the account, and after that the name is theirs: they change it in
**Options** in the menu. The server refuses a rename from here, so
nobody's name moves by accident or by habit. It is not a guarantee against a
determined admin, who can always reset a password and sign in as the account; it
is a rule about how the software expects you to behave. If a name is a genuine
problem, that is what timeouts and bans are for.

**Deleting** asks you to type the username before it will go through, because it
takes the account, its watch history and its chat with it and there is no undo.
The server checks the typed name too, so nothing can delete an account with a
single stray request.

The page is gated server side, so only a signed in admin can reach any of it.
The last remaining admin cannot be deleted or demoted, so you can never lock
yourself out.

## The admin dashboard

`/admin` always opens on **Manage Stream**, the one screen a night is run from.
While you are on air, its name in the strip and in the menu carries a small red
dot, so the way back to it is lit from every other section.

At the top is **the room**: your own watch page in a frame (the picture and
the chat; **Chat only** drops the picture, **Sound** turns its sound on, and the
arrow opens the room in a tab of its own), then the slate with tonight's title
and game (**Edit** changes them; **No game** clears the game), the call board
of who is in the room, and three numbers the server itself counts: how many are
**watching** the video, the **room limit** (**Change** sets it), and how much
this broadcast has **sent**. Off air the same screen shows the room dark,
exactly as a viewer sees it. On a phone the chat sits under the picture inside
the frame, with its own scroll and the message box at the bottom.

Under the room, three steps, each ticking as it becomes true (side by side on a
wide screen, one under another on a phone):

1. **Settings in OBS**: the **Server** and **Stream Key** exactly as OBS names
   its fields (Settings, Stream, Service: Custom), each with **Copy**, the key
   hidden until **Show**. It ticks once OBS has gone live with the current key.
   **Regenerate the key** makes a new one (it asks first); OBS cannot go live
   again until you paste the new key into it, so the step unticks until it
   has.
2. **Start Streaming in OBS**: ticks while you are on air, with the time you
   went on and whether the broadcast is being recorded (or that the library is
   not answering, in which case it is saved once it is back).
3. **Send the link**: **Copy watch link** copies your plain `/watch` address,
   which opens straight into the room for anyone with an account. It lights
   once you are on air and ticks once the link is copied. It works off air too,
   but a chat app builds its preview when the link is pasted, so a link sent
   before you are on air shows the offline card rather than tonight's title and
   a live frame. If the browser will not copy, the link is shown under the
   button, selected, to copy by hand.

Everything else is a section, along the right of the strip on a wide screen
and at the top of the menu on anything narrower:

- **People**: accounts, bans and invite codes.
- **Library**: **Storage** (what the media store is using, and the limits that
  decide how long things last), then the recordings and clips you review, pin,
  share and delete.
- **Channel**: your site name, description and accent color, and **Go-live
  notifications**: whether going live notifies anyone, how many devices will
  get it, and a test that goes only to your own devices (`docs/04-run.md`).
- **Chat rules**: slow mode and the banned words list.
- **Connections**: the OBS **chat overlay** URL, and, when theater is switched
  on, the theater session and the projector (`docs/11-theater.md`).
- **Stats**: the numbers the app keeps, at `/analytics`.

The title and the game feed the card on the home page and the preview anyone
gets when they share the watch link. A link to `/admin#people` (or any
section's name) opens that section.

## Moderators

A moderator is a separate, lower role from admin. Admins keep every moderator
power, but a moderator cannot manage accounts and never sees admin accounts.

The easiest way to grant the role is in chat: an admin types `/mod <username>`.
The change takes effect immediately, and that person's messages then carry a
`mod` tag. `/unmod <username>` removes it. The host's own messages carry a small
red camera instead, so it is always clear who is streaming.

A moderator gets **Moderation** in the menu, leading to `/mod`, a trimmed
dashboard where they can review watch and chat history, read the room's recent
chat, and lift bans they set.
They can also rename any clip, on the clip's own page; everyone else can rename
only the clips they made themselves.
They cannot add, edit, or delete accounts, and admin accounts are hidden from
this area entirely.

### Chat commands

Moderators and admins moderate by typing commands into chat. The command is
handled privately and never shown to other viewers:

- `/timeout <user> [seconds]`: mute a viewer for a while (default 300 seconds).
- `/untimeout <user>`: lift a timeout early.
- `/del <user>`: delete that viewer's most recent message for everyone. It is
  replaced with "deleted by a moderator" and kept in the admin log as deleted.
- `/purge <user>`: delete every message that viewer has sent, the same way.
- `/ban <user> [reason]`: ban a viewer from chat. A ban is persistent.
- `/unban <user>`: lift a ban. A moderator can only lift bans they set; an
  admin can lift any.
- `/mod <user>` and `/unmod <user>`: grant or remove the moderator role
  (admins only).
- `/wipe`: clear the whole chat for everyone. It asks first, privately, with a
  **Wipe chat** button on the reply; nothing happens until you press it. The
  room then empties on every page and the overlay with no announcement, so
  viewers see the chat clear rather than a line saying it was cleared.
- `/help`: list the commands available to your account.

A moderator cannot act on an admin's messages, and cannot grant moderators.

Every system line in chat carries the time it was said, the same as a message.

Single messages can also be removed without typing anything: hover a line in
chat and a small delete control appears. A highlighted message deletes the same
way, by either route: a highlight is a chat message with a spotlight, so it sits
in the admin log with a message id and `/del`, `/purge` and the hover control
all reach it like any other line.

### Slow mode and banned words

Two settings in the dashboard's **Chat rules** section apply to everyone at
once. Slow mode sets a minimum number of seconds between
one viewer's messages; moderators and admins are exempt. A new install starts
at 2 seconds, which is short enough that a conversation never notices it and
long enough to take the edge off someone hammering the enter key. Set it to 0
to turn it off, or raise it when chat gets away from you. An install that was
already running before this default arrived keeps whatever it had, so nothing
changes under you on an update.

The banned words list stays folded behind **Show the list**, rather than open on
the page, because a new install ships with about a hundred entries and
most of them are not things you want on screen every time you open the page. It
is one entry per line, or separated by commas, and a message containing any of
them is refused, with only the sender told why. The list is admin-only and never
leaves the dashboard. It has its own **Save list**, so folding it away without
saving changes nothing, and unlike slow mode it applies to everybody, moderators
and you included. It also covers a paid highlight, so spending points is not a
way around it.

A new install starts with a default list covering ordinary profanity and slurs.
An install that was already running keeps its own list and is never given the
defaults, on the same reasoning as slow mode: you may have emptied it on
purpose, and an update should not put words back that you removed. Edit it down
to nothing if you would rather run without one.

Matching is whole word, not "appears anywhere in the message". This matters more
than it sounds. If a banned word matched anywhere inside a message, banning
"ass" would also block class, pass, grass and assist; "cum" would block document
and cucumber; "anal" would block analysis. Whole-word matching means the entry
has to stand on its own, while still covering the obvious endings, so "fuck"
catches fucking, fucked and fucker without "ass" ever reaching "assist".

What it deliberately does not do is chase evasion. Spacing a word out, stretching
it, or punching symbols through it will get past the filter. That is a trade
rather than an oversight: tightening it far enough to catch those reliably also
starts refusing ordinary messages, and a viewer who cannot say "class" has no
idea why and will not tell you, whereas a message that slips through is one your
moderators remove in seconds.

## The lamp strip and the menu

Every page carries the same strip across its top edge. On the left, the
**menu** key and the **site name**; on the right (in the middle, on a wide
screen), the **lamp**. The lamp reads **ON AIR**, lit red, while you are live,
with a clock counting how long you have been on, and dark **OFF AIR** glass
when you are not. That red is the lamp's alone: nothing else on the site uses
it.

The site name goes home. The menu holds:

- **The room**, `/watch`.
- **Past broadcasts**, `/browse`: the recordings and clips, with a search that
  matches their titles as you type.
- **Options**, your own account.
- **Dashboard** and **Stats** for an admin, **Moderation** for a moderator who
  is not one. On the dashboard itself the menu carries its sections instead.
- **Sign out**.

It names you at the top, with your points. It opens with a tap or a click,
works from the keyboard (arrow keys move, Escape closes), and closes when you
tap anywhere else.

Signing in takes you to the room if you are live and to **home** if you are
not. Home is the place between broadcasts: when you were last on air, a
**Notify me when it goes live** chip for a device that is not signed up yet,
the last broadcast, and the latest clips. While you are
live it shows a current frame and one button into the room.

## Options, your own account

`/options` is where anyone signed in changes their own things: their **chat
style**, whether **this device** is notified when the stream goes live, their
**display name**, **picture** and **bio**, and their **password**.

Notifications are per device, not per account: turn them on on each phone or
computer that should get them. The browser asks for permission only once the
switch is tapped, and the line under it says when it cannot work there (an
iPhone or iPad needs the site added to the Home Screen first; a browser that
was told no has to be allowed in its site settings). Nobody is asked for an
email address, anywhere.

Chat style is the font and the colors your lines carry for everyone. The font
choices are Default (Inter), JetBrains Mono, Space Grotesk, IBM Plex Sans and
Sora, each drawn in its own face, and under them a mock of your own chat line
shows the font and both colors together before you send anything.

## Changing your own password

Anyone signed in can change their own password under **Options**. They enter
their current password and a new one. This does not need an admin.

## Accounts (command line, recovery only)

The setup wizard and invite codes are the normal way to make accounts. Every
account can still be managed with `manage.py`, run inside the gate container, but
this is now the break-glass path: reach for it only if you are locked out, need
to reset a forgotten admin password, or want to script something. Day to day you
never need it.

Create an admin (for example to recover if you lost admin on every account):

```
docker compose exec gate python manage.py adduser yourname --admin
```

Create a normal viewer with a display name:

```
docker compose exec gate python manage.py adduser alice --name "Alice"
```

List everyone:

```
docker compose exec gate python manage.py listusers
```

Change a password:

```
docker compose exec gate python manage.py passwd alice
```

Delete an account:

```
docker compose exec gate python manage.py deluser alice
```

Make someone a moderator (normally done with `/mod` in chat; this is a fallback,
handy for the first moderator):

```
docker compose exec gate python manage.py mod alice
docker compose exec gate python manage.py unmod alice
```

Notes:

- Usernames are stored in lower case. The display name is what others see in
  chat and on the call board. If you do not pass `--name`, the username is
  used as the display name.
- If you do not pass `--password`, you are prompted for it without it showing on
  screen, which is the safer way.
- The admin flag marks that account as the host, whose messages carry a small
  camera in the channel's accent color and whose line on the call board is
  marked the same way, and unlocks the admin dashboard at `/admin` described
  above. The moderator role adds a `mod` tag and the `/mod` dashboard instead.

## Chat

Chat is live for everyone signed in. Messages appear instantly through a
WebSocket, with no page reloads. The last fifty messages stay on screen, each
with a small local timestamp like `19:42`.

**Chat belongs to the night, not to one broadcast.** Ending a stream does not
clear it, and neither does ending a theater session, so an evening that runs
from a broadcast into a film and back reads as one conversation. The room is
cleared at the *start* of a later broadcast instead, and only once the channel
has been off air long enough to be a different night
(`SELFSTREAM_NIGHT_GAP`, six hours by default). That gap is what makes a
restart safe: OBS crashing and coming back keeps the room, while tomorrow
evening starts clean. A night that never gets a sequel is swept after
`SELFSTREAM_CHAT_IDLE_WIPE`, a day by default. When a wipe does happen the room
does not just fall silent: a short line says why, so a viewer mid-conversation
is not left assuming something broke. A broadcast starting and ending is said
in chat too ("Stream started.", "Stream ended."), the start after any clear, so
the newest line in a live room never describes the last broadcast. The OBS
overlay shows neither.

Everyone can pick their own name and message colors on the options page. The
server checks the choice rather than trusting it: a color too dark to read
against the panel is refused, and so is the red kept for the ON AIR lamp.

Separately, the gate keeps an admin-only copy of chat in its database for the
last 7 days, so you can review history from the dashboard. It is purged
automatically after that window. Change the retention by setting
`SELFSTREAM_CHAT_RETENTION_DAYS` in `.env` (set it to `0` to keep nothing).

Messages are limited to 500 characters, and there is a small flood guard that
drops anything past five messages in three seconds.

## The room

Top to bottom on a phone: the lamp strip, the stream, the **slate** under it
(the stream title, what you are playing, and the **Sound** and full screen
keys), the **call board** of who is here, then chat, with the message box at
the bottom. On a wider screen chat moves into a column beside the stream, and a
key on the slate hides it when you want the picture to have the width; the
choice is remembered in that browser.

Nothing is ever drawn over the picture. Browsers start a stream muted, so
**Sound** is on the slate rather than floating on the video, and full screen
hands you the player's own controls.

Off air the stream's place says so, with when you were last on, and under it a
quiet **Notify me when it goes live** chip (only on a device that can take
notifications and has not turned them on) and a link to the last broadcast.
Tapping the chip is what lets the browser ask; nothing pops up on its own.

Beside the message box: the **scissors** clip the last stretch of the stream
(`docs/04-run.md`), and the **star** opens your points balance and the
highlight (`docs/09-points.md`).

Host and moderators can delete a single message: point at it with a mouse, or
press and hold it on a phone, and a small delete key appears.

## Presence, who is in the room

The call board is the safety and fun feature. Everyone signed in can see:

- a lit square for each person in the room, the host's marked in the channel
  accent. Tap one for that person's card. On a
  wide screen the squares carry names.
- somebody who has just left, dimmed, for a few minutes after they go
- the count, at the right of the board
- a short line in chat when someone joins or leaves

Those lines are one per person, not one per tab, and a brief disappearance never
produces any. Someone switching to another app on their phone drops the
connection and makes a new one when they come back, so the room used to get a
departure and an arrival every time. A departure is now held for a minute
(`SELFSTREAM_JOIN_GRACE`) and cancelled if they return inside it, which means a
glance at another app is silent and only a real leaving is announced.

**The channel owner is never announced, arriving or leaving.** They are in and
out of their own room all evening, often only to read it, and a running
commentary on the host coming and going is noise. They still appear on the
board and still count towards it: silent is not invisible. Everyone else,
moderators included, is announced as before.

The board updates the moment someone opens or closes the page. Because every
viewer has a named account, you always know exactly who is on the other side of
the stream.

## Limiting how many people watch at once

Everyone watching pulls their own copy of the broadcast from your server, so the
number of viewers is what your bandwidth bill is made of. At the 1080p and
6000 Kbps the theater projector publishes, that is roughly **2.9 GB per person
per hour**; at 8000 Kbps from OBS, roughly 3.9 GB.

The **room limit**, in the numbers under the room on the dashboard's **Manage
Stream** screen (press **Change**), sets how many people may watch at once. `0`, the default, means no limit. Past the limit, the video is refused and
the page says the room is full; the person is not signed out and **chat still
works for them**, so they can wait in the room and the video starts on its own
when a place opens up. A place opens up about thirty seconds after somebody
stops watching.

The home page shows a still frame of the stream rather than playing it, so
nobody sitting on home takes a place in the room or costs you a viewer's
bandwidth.

Two things the limit deliberately does not do. It never counts or refuses an
admin, so you cannot lock yourself out of your own broadcast. And it does not
touch saved broadcasts or clips, which are files served off the disk and cost
nothing per viewer.

The same numbers show what the current broadcast has **sent** while it runs,
so the limit can be set against a real number rather
than a guess. That figure is for this broadcast, not for the month; your host's
control panel is where the monthly total lives.
