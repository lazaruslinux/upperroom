# 7. Demo mode

Demo mode brings the whole site up already alive, in one command, so you (or
someone you are showing it to) can see a working stream without OBS, without
making accounts, and without waiting for anything. It runs three extra
containers, all behind a Compose profile named `demo`, so a normal install never
touches them.

When it is running you get, straight away:

- demo accounts that already exist (no setup wizard to fill in),
- a synthetic broadcast already streaming, so the watch page shows live video,
- live chat and presence, and a populated admin dashboard,
- one unredeemed invite code, so the invite flow works from the login page,
- a paired projector, so a theater session can be run without a media library.

## 7.1 One command up

From the project folder, after copying `.env.example` to `.env` and filling it
in the usual way (see `docs/04-run.md`):

```
docker compose --profile demo up -d
```

This starts the normal three services (`mediamtx`, `gate`, `caddy`) plus:

- `demo-seed`, a one-shot container that creates the demo accounts, the stream
  title and description, and the invite code, then exits. It is idempotent, so
  re-running the command is harmless.
- `demo-stream`, a small `ffmpeg` container that loops forever, publishing an
  animated 720p30 test source with audio into MediaMTX. It publishes with
  `PUBLISH_PASS`, which the gate seeds into the stream key on first start, so the
  demo authenticates with no dashboard step. This is what makes the watch page
  show live video.
- `demo-projector`, the projector in demo mode: three built-in generated titles
  and no media library at all, so a theater session works end to end. It pairs
  itself with the gate through `DEMO_PROJECTOR_KEY` (default
  `demo-projector-key`), which `demo-seed` writes into the channel's projector
  key **only while the channel has none**, so this can never overwrite a real
  operator's key.

Give it under a minute for the certificate and the first video segments, then
open your site. Sign in with a demo account below and you are looking at a live
stream with chat.

### Run demo-stream or demo-projector, not both

Both publish to the same `live` path, and two publishers contending for one
channel is a mess to watch and a worse one to diagnose. Stop one before starting
the other:

```
docker compose --profile demo stop demo-stream      # then run a theater session
docker compose --profile demo start demo-stream     # back to the synthetic broadcast
```

`demo-projector` only publishes while a title is actually playing, so leaving it
running is harmless as long as `demo-stream` is stopped before you press play.

To try the theater: sign in as the demo admin, open the dashboard, and under
**Theater** on the **Broadcast** tab press **Start session**, then **Search**
(an empty-ish query like `the` finds the demo titles) and **play** a row. Each
demo title runs two minutes and then ends on its own, so you also see the
return to intermission. Among the films is one demo **show**, The Standing
Stones, with two short seasons: its row has an **episodes** button rather than
a play button, which is what the episode picker looks like on a real library.
The whole feature is in `docs/11-theater.md`.

## 7.2 Demo credentials

The accounts and their password come from the environment, with these defaults
(override them in `.env` if you like, see `.env.example`):

| Account       | Username     | Password       | Role   |
|---------------|--------------|----------------|--------|
| Demo admin    | `demo`       | `demodemo123`  | admin  |
| Viewer one    | `viewer_one` | `demodemo123`  | viewer |
| Viewer two    | `viewer_two` | `demodemo123`  | viewer |

The admin username and password are set with `DEMO_ADMIN_USER` and
`DEMO_ADMIN_PASSWORD`. The two viewer accounts use the same password.

Because the seeder creates the first account, the first-run setup wizard at
`/setup` seals itself exactly as it does on a normal install: once any account
exists, the wizard is spent and the server refuses it. There is nothing to fill
in.

## 7.3 The invite code

The seeder also creates one single-use invite code with the label **try me**, so
the invite flow is demonstrable end to end. The code is a short three-word string
like `ember-quiet-harbor`. Find it two ways:

- in the `demo-seed` container's log:
  ```
  docker compose --profile demo logs demo-seed
  ```
- or signed in as the demo admin, under **Access codes** on the `/admin`
  dashboard's **People** tab.

Redeem it from the login page: click **have an invite?**, then enter the code
with a new username, display name, and password to make a fresh viewer account.

## 7.4 Tearing it down

Stop the demo containers but keep the data (accounts, the recording made from the
demo broadcast, chat history):

```
docker compose --profile demo down
```

To remove everything including the volumes, so the next start is completely
fresh:

```
docker compose --profile demo down -v
```

Warning: `-v` erases the data volumes. That deletes every account, recording,
clip, and chat log in this instance, not just the demo ones. Only use it when you
want a clean slate.

## 7.5 Do not run demo mode against a real instance

The demo profile is for a throwaway or evaluation instance. Do not start it
against a production `.env` or a database that has your real accounts in it.

Two safeguards make an accidental run harmless, but do not rely on them:

- `demo-seed` refuses to act if accounts already exist and none of them is the
  demo admin. It logs why and changes nothing, so it will not add demo accounts
  or overwrite your stream title on a real database.
- `demo-stream` publishes with your `PUBLISH_PASS`, which is seeded into the
  stream key only on the first start (once the database has a key, changing
  `PUBLISH_PASS` does nothing; wipe the volumes to reset). If you start it while
  you are genuinely live from OBS, two publishers would contend for the same
  channel.

If you want to try demo mode, do it on a separate instance with its own `.env`
and its own volumes.
