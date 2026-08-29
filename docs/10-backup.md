# Backing up and restoring

Everything that makes your channel yours lives in one small SQLite database:
your accounts and their passwords, the chat history, the chat replay attached to
each recording, your channel settings, your invites, and your stream key. It is
a few megabytes. Losing it means rebuilding all of that by hand.

This is the command that saves it.

## Take a backup

```
docker compose exec gate python manage.py backup
```

It prints where it wrote the file, which by default is `/data/backups` inside
the gate's data volume. It is safe to run while you are live and streaming: the
database is copied with SQLite's own consistent-copy mechanism, not by grabbing
the file mid-write.

## What is in it, and what is not

In the archive:

- every account, including password hashes, roles, avatars, bios and points
- the chat log and the chat replay attached to each recording and clip
- channel settings: site name, stream title, accent, retention limits, chat
  moderation, your stream key and overlay key
- invites and bans
- the list of recordings and clips

Not in the archive, on purpose:

- **The recordings and clips themselves.** These run to hundreds of gigabytes.
  A backup that takes an hour and fills your disk is a backup nobody runs. If
  you restore onto a server without them, your recordings list will still be
  there but the files will not, and the restore tells you how many are missing.
  To keep the files too, snapshot the `media_data` volume separately, with
  whatever your host or filesystem already offers.
- **Your `.env`.** It holds your session secret and your SMTP password, and
  putting secrets in a file you copy to a laptop is how secrets get out. It also
  lives on the host, outside the container, so this command cannot see it. Keep
  your own copy somewhere safe. If you restore without the original session
  secret, the only consequence is that everyone signs in again; your stream key
  and overlay key are in the database and come back with it.

## Get it off the server

A backup that only exists on the machine that can fail is not a backup.

```
docker compose cp gate:/data/backups/upperroom-backup-20260101-040000.tar.gz ./
scp you@yourserver:/srv/upperroom/upperroom-backup-20260101-040000.tar.gz ~/backups/
```

To have them land somewhere you already back up, point `SELFSTREAM_BACKUP_DIR`
at a directory you mount from the host, in your uncommitted
`docker-compose.override.yml`.

## Do it nightly

From the host's crontab. `-T` matters: cron has no terminal.

```
0 4 * * * cd /srv/upperroom && docker compose exec -T gate python manage.py backup
```

Old backups are not cleaned up for you. Delete them on your own schedule, or
write them somewhere that rotates.

## Restore

The gate must not be running, so the restore runs in a throwaway container
against the same image and volumes.

```
docker compose stop gate
docker compose cp ./upperroom-backup-20260101-040000.tar.gz gate:/data/backups/
docker compose run --rm gate python manage.py restore \
    /data/backups/upperroom-backup-20260101-040000.tar.gz --force
docker compose start gate
```

Before it writes anything, the restore checks that the archive is an upperroom
backup, that no file in it tries to escape into somewhere it should not, that
the database inside passes its integrity check, and that it has the tables it
should. Any of those failing means nothing is touched.

Without `--force` it refuses to replace a database that is already there. With
`--force`, the database that was in place is **moved aside**, not deleted, to a
file named `upperroom.db.pre-restore-<number>` next to it. If you restored the
wrong archive, the old one is still sitting there.

## Check that it works

The only backup you can trust is one you have restored. Once, on a spare server
or a local copy, run the restore and sign in. It takes ten minutes and it is the
difference between having backups and thinking you do.
