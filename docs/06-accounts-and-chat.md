# 6. Accounts, chat, and presence

## First-run setup

On a brand new install no account exists yet, so the login page sends you to a
one-time setup wizard at `/setup`. Enter a username, a display name, a password,
and a site name, then create the account. This first account is the
admin, you are signed in immediately, and the wizard disappears for good the
instant the account exists (the server refuses it from then on, not just the
page). There is nothing to run in a terminal.

## Letting people in with invites

After setup you add everyone else with single-use invite codes, from **Access
codes** on the dashboard's **People** tab, with the **Invites** tab selected:

- **Generate a code** with the **Generate code** button. You can add an optional
  label ("who it's for") to help you keep track. Each code is a short, readable
  string of three words like `ember-quiet-harbor`.
- **Share the code** with one person, however you like. There is no email.
- They **redeem it** from the login page: under the sign-in form is a "have an
  invite?" link that reveals a short join form (code, username, display name,
  password). Redeeming makes them a **viewer** account and signs them in. A code
  can never create an admin or a moderator.
- Each code works **once**. After it is redeemed the page shows who used it and
  when. You can **Revoke** a code that has not been redeemed yet, which keeps the
  row for the record but stops it from ever being used.
- Once a code is spent, either used or revoked, a **Remove** button appears on
  it, and **Clear used codes** sweeps all of them at once. An active code has to
  be revoked before it can be removed, so removing can never quietly un-issue a
  code somebody is still holding. Removing a code does not affect the account it
  created; the account keeps its own record of where it came from.

## Letting someone watch without an account: guest passes

An invite makes somebody a member forever. A **guest pass** is for the other
case: one person, one broadcast, no account.

- **Generate a batch** on the **Guest passes** tab of **Access codes**, on the
  dashboard's **People** tab. Set how many you
  want; each one is single use. They look the same as invite codes.
- **Copy all unused** puts the whole batch on your clipboard, one per line. The
  intended shape is one group message with several codes in it: whoever gets
  there first takes one.
- **Send people to `/guest`**. They type the code, the name they want in chat,
  and answer a small question. Then they are watching.
- **The half hour starts when they redeem it**, not when you generate it, so you
  can prepare passes days in advance.
- A pass that has been used, or revoked, can be **removed**, and there is a
  **Clear used passes** button for the same reason as the invites one.

What a guest can and cannot do:

- **Can**: watch the live stream, and chat.
- **Cannot**: clip, like, comment, earn points, or see the recordings and clips
  library. They have no settings page, because there is nothing on it that would
  outlast them.
- **Can be moderated exactly like anyone else.** A guest can be timed out,
  banned, `/del`ed and `/purge`d. This is the reason a redeemed pass creates a
  real account behind the scenes rather than some separate kind of visitor: a
  stranger who can talk in your chat and cannot be moderated would be worse than
  no guests at all.

When the time runs out their video stops, they get a sign-in prompt, and a few
minutes later the account deletes itself along with anything attached to it.

The question on the guest form is there to keep casual automation out. It is
generated and checked by your own server, so there is no third party involved
and nothing is sent anywhere. It will not stop somebody determined; what stops
them is that redemption is rate limited per address and the codes are drawn from
a space of about thirty million.

## Managing people

If your account has the admin flag, sign in and open the dashboard at `/admin`
(there is a **dashboard** link in the site nav on every page). On the
**People** tab, the **Manage users** button opens the list, and **Bans** opens
the same window on the list of who is barred. With no terminal you can:

- **Create an account** with the small **+** above the list. Enter a username and a
  password, optionally a display name, tick admin if you want, and it is made.
- **Edit any account**: reset its password, set the email for go-live alerts, or
  grant and remove the admin or moderator role (the two are independent).
- **Delete an account**, which also clears its watch history and chat log.
- See each person's **watch activity** (when they watched and for how long) and
  their **chat history** from the last 7 days, under the **Activity** button.
- Review and lift **bans**.
- Generate, copy and revoke **invites**.

Two things you deliberately cannot do here.

You cannot change somebody's **display name**. You choose the starting one when
you create the account, and after that the name is theirs: they change it in
**Settings** on their own home page. The server refuses a rename from here, so
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

`/admin` opens with your own watch page in a frame, and the controls under it
are grouped in five tabs:

- **Broadcast**: the theater session and the **Next stream** announcement.
- **Content**: the library of recordings and clips you review, pin and delete,
  and the **Storage** limits that decide how long they last.
- **People**: accounts, bans, invite codes and guest passes.
- **Channel**: your site name, description and accent color, **Chat
  moderation** (slow mode and banned words), and **Go-live notifications**.
- **Connections**: the **Stream key**, the projector, and the **Overlay** URL.

The row directly under the frame is what is on tonight: the stream title and
the game. Both feed the card on the home page and the preview anyone gets when
they share the watch link.

## Moderators

A moderator is a separate, lower role from admin. Admins keep every moderator
power, but a moderator cannot manage accounts and never sees admin accounts.

The easiest way to grant the role is in chat: an admin types `/mod <username>`.
The change takes effect immediately, and that person's messages then carry a
`mod` tag. `/unmod <username>` removes it. The host's own messages carry a small
red camera instead, so it is always clear who is streaming.

A moderator gets a **Mod** link on the home page leading to `/mod`, a trimmed
dashboard where they can review watch and chat history and lift bans they set.
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
- `/help`: list the commands available to your account.

A moderator cannot act on an admin's messages, and cannot grant moderators.

Single messages can also be removed without typing anything: hover a line in
chat and a small delete control appears. A highlighted message deletes the same
way, by either route: a highlight is a chat message with a spotlight, so it sits
in the admin log with a message id and `/del`, `/purge` and the hover control
all reach it like any other line.

### Slow mode and banned words

Two settings under **Chat moderation**, on the dashboard's **Channel** tab,
apply to everyone at once. Slow mode sets a minimum number of seconds between
one viewer's messages; moderators and admins are exempt. A new install starts
at 2 seconds, which is short enough that a conversation never notices it and
long enough to take the edge off someone hammering the enter key. Set it to 0
to turn it off, or raise it when chat gets away from you. An install that was
already running before this default arrived keeps whatever it had, so nothing
changes under you on an update.

The banned words list sits behind **Open banned words list**, rather than on the
dashboard itself, because a new install ships with about a hundred entries and
most of them are not things you want on screen every time you open the page. It
is one entry per line, or separated by commas, and a message containing any of
them is refused, with only the sender told why. The list is admin-only and never
leaves the dashboard. It has its own Save inside the modal, so closing without
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

## Changing your own password

Anyone signed in can change their own password from **Settings** on the home
page. They enter their current password and a new one. This does not need an
admin. The gear on the watch page is for the things that only affect how chat
looks to them: the theme, their chat font, and their name and message colors.

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
  chat and in the watching list. If you do not pass `--name`, the username is
  used as the display name.
- If you do not pass `--password`, you are prompted for it without it showing on
  screen, which is the safer way.
- The admin flag marks that account as the host, whose messages carry a small
  red camera in chat, and unlocks the admin dashboard at `/admin` described
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
is not left assuming something broke.

Everyone can pick their own name and message colors from the gear on the watch
page. The server checks the choice rather than trusting it: a color too dark to
read against the panel is refused, and so is the red kept for the LIVE tag and
the host's camera mark.

Separately, the gate keeps an admin-only copy of chat in its database for the
last 7 days, so you can review history from the dashboard. It is purged
automatically after that window. Change the retention by setting
`SELFSTREAM_CHAT_RETENTION_DAYS` in `.env` (set it to `0` to keep nothing).

Messages are limited to 500 characters, and there is a small flood guard that
drops anything past five messages in three seconds.

## Presence, who is watching

The watching list is the safety and fun feature. Everyone signed in can see:

- a live count, for example "3 watching"
- the names of everyone currently watching, with "(you)" next to their own
- a short line in chat when someone joins or leaves

Those lines are one per person, not one per tab, and a brief disappearance never
produces any. Someone switching to another app on their phone drops the
connection and makes a new one when they come back, so the room used to get a
departure and an arrival every time. A departure is now held for a minute
(`SELFSTREAM_JOIN_GRACE`) and cancelled if they return inside it, which means a
glance at another app is silent and only a real leaving is announced.

Tap or click the count at the top of the chat panel to show or hide the full
list of names. The count updates the moment someone opens or closes the page.
Because every viewer has a named account, you always know exactly who is on the
other side of the stream.
