# 6. Accounts, chat, and presence

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
- Review and delete recordings and clips under the **Content** section.

The dashboard is gated server side, so only a signed in admin can reach any of
it. The last remaining admin cannot be deleted or demoted, so you can never lock
yourself out.

## Moderators

A moderator is a separate, lower role from admin. Admins keep every moderator
power, but a moderator cannot manage accounts and never sees admin accounts.

The easiest way to grant the role is in chat: an admin types `/mod <username>`.
The change takes effect immediately, and that person's messages then carry a
shield badge. `/unmod <username>` removes it. (Admins show a crown badge.)

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
- `/ban <user> [reason]` — ban a viewer from chat. A ban is persistent.
- `/unban <user>` — lift a ban. A moderator can only lift bans they set; an
  admin can lift any.
- `/mod <user>` and `/unmod <user>` — grant or remove the moderator role
  (admins only).
- `/help` — list the commands available to your account.

A moderator cannot act on an admin's messages, and cannot grant moderators.

## Changing your own password

Anyone signed in can change their own password from the **Settings** panel on
the watch page (the gear icon). They enter their current password and a new one.
This does not need an admin.

## Accounts (command line)

The dashboard is the easy path, but every account can still be managed with
`manage.py`, run inside the gate container. This is handy for bootstrapping the
very first admin before you can log in.

Create an admin (give yourself this):

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
- The admin flag adds a crown badge next to your name in chat and unlocks the
  admin dashboard at `/admin` described above. The moderator role adds a shield
  badge and the `/mod` dashboard instead.

## Chat

Chat is live for everyone signed in. Messages appear instantly through a
WebSocket, with no page reloads. The last fifty messages stay on screen, each
with a small local timestamp like `6.26.26 4:32pm`, and the whole chat is wiped
automatically when a broadcast ends, so the next stream starts clean.

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
