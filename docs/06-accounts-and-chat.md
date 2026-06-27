# 6. Accounts, chat, and presence

## Accounts

There are no public sign ups. You create and manage every account with
`manage.py`, run inside the gate container.

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

Notes:

- Usernames are stored in lower case. The display name is what others see in
  chat and in the watching list. If you do not pass `--name`, the username is
  used as the display name.
- If you do not pass `--password`, you are prompted for it without it showing on
  screen, which is the safer way.
- The admin flag only adds a badge next to your name in chat. It does not unlock
  any separate dashboard in this version.

## Chat

Chat is live for everyone signed in. Messages appear instantly through a
WebSocket, with no page reloads. The last fifty messages are kept in memory so
someone joining mid stream sees recent context. Chat history is not saved to
disk, so it clears if the gate service restarts. That is deliberate, to keep
things simple and private.

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
