# 6. Accounts, chat, and presence

## First-run setup

On a brand new install no account exists yet, so the login page sends you to a
one-time setup wizard at `/setup`. Enter a username, a display name, a password,
and a name for the channel, then create the account. This first account is the
admin, you are signed in immediately, and the wizard disappears for good the
instant the account exists (the server refuses it from then on, not just the
page). There is nothing to run in a terminal.

## Letting people in with invites

After setup you add everyone else with single-use invite codes, from the admin
dashboard's **Invites** section:

- **Generate a code** with the **Generate code** button. You can add an optional
  label ("who it's for") to help you keep track. Each code is a short, readable
  string of three words like `ember-quiet-harbor`.
- **Share the code** with one person, however you like. There is no email.
- They **redeem it** from the login page: under the sign-in form is a "have an
  invite?" link that reveals a short join form (code, username, display name,
  password). Redeeming makes them a **viewer** account and signs them in. A code
  can never create an admin or a moderator.
- Each code works **once**. After it is redeemed the dashboard shows who used it
  and when. You can **Revoke** a code that has not been redeemed yet, which keeps
  the row for the record but stops it from ever being used.

## The admin dashboard

If your account has the admin flag, sign in and open `/admin` (there is also an
**Admin** link on the home page). From the dashboard you can, with no terminal:

- **Create an account** with the **+ New user** button. Enter a username and a
  password, optionally a display name, tick admin if you want, and it is made.
- **Edit any account**: rename it, reset its password, or grant/remove the admin
  or moderator role (the two are independent).
- **Delete an account**, which also clears its watch history and chat log.
- See each person's **watch activity** (when they watched and for how long) and
  their **chat history** from the last 7 days, under the **Activity** button.
- Review and lift **bans** under the **Bans** section.
- Review, pin and delete recordings and clips under the **Content** section,
  and set how much of them to keep under **Storage**.
- Announce the **Next stream**, so viewers see a countdown to it.
- Set **Chat moderation**: slow mode, and a list of banned words.

The dashboard is gated server side, so only a signed in admin can reach any of
it. The last remaining admin cannot be deleted or demoted, so you can never lock
yourself out.

## Moderators

A moderator is a separate, lower role from admin. Admins keep every moderator
power, but a moderator cannot manage accounts and never sees admin accounts.

The easiest way to grant the role is in chat: an admin types `/mod <username>`.
The change takes effect immediately, and that person's messages then carry a
`mod` tag. `/unmod <username>` removes it. The host's own messages carry a small
red camera instead, so it is always clear who is streaming.

A moderator gets a **Mod** link on the home page leading to `/mod`, a trimmed
dashboard where they can review watch and chat history and lift bans they set.
They cannot add, edit, or delete accounts, and admin accounts are hidden from
this area entirely.

### Chat commands

Moderators and admins moderate by typing commands into chat. The command is
handled privately and never shown to other viewers:

- `/timeout <user> [seconds]` — mute a viewer for a while (default 300 seconds).
- `/untimeout <user>` — lift a timeout early.
- `/del <user>` — delete that viewer's most recent message for everyone. It is
  replaced with "deleted by a moderator" and kept in the admin log as deleted.
- `/purge <user>` — delete every message that viewer has sent, the same way.
- `/ban <user> [reason]` — ban a viewer from chat. A ban is persistent.
- `/unban <user>` — lift a ban. A moderator can only lift bans they set; an
  admin can lift any.
- `/mod <user>` and `/unmod <user>` — grant or remove the moderator role
  (admins only).
- `/help` — list the commands available to your account.

A moderator cannot act on an admin's messages, and cannot grant moderators.

Single messages can also be removed without typing anything: hover a line in
chat and a small delete control appears.

### Slow mode and banned words

Two settings under **Chat moderation** in the admin dashboard apply to everyone
at once. Slow mode sets a minimum number of seconds between one viewer's
messages; moderators and admins are exempt. The banned words list is one word or
phrase per line, and a message containing any of them is dropped, with only the
sender told why. The list is admin-only and never leaves the dashboard.

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
with a small local timestamp like `19:42`, and the whole chat is wiped
automatically when a broadcast ends, so the next stream starts clean.

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

Tap or click the count at the top of the chat panel to show or hide the full
list of names. The count updates the moment someone opens or closes the page.
Because every viewer has a named account, you always know exactly who is on the
other side of the stream.
