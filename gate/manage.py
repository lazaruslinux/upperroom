"""
Admin tool for upperroom: accounts, and backing the channel up.

Accounts are normally managed from the dashboard; these commands are the
break-glass path. Backups are not: taking one is a command, by design, so it can
be put on a schedule. Run it all inside the gate container. Examples:

    docker compose exec gate python manage.py adduser alice
    docker compose exec gate python manage.py adduser sam --admin
    docker compose exec gate python manage.py listusers
    docker compose exec gate python manage.py passwd alice
    docker compose exec gate python manage.py deluser alice
    docker compose exec gate python manage.py backup
    docker compose run --rm gate python manage.py restore <archive> --force

Usernames are stored in lower case. The display name is what other viewers see
in chat and in the watching list. If you do not pass a password on the command
line you are prompted for it without it showing on screen.
"""

import argparse
import getpass
import json
import os
import shutil
import sqlite3
import sys
import tarfile
import tempfile
import time

import db

# Where backups are written when no path is given. Inside the gate's data
# volume, so they survive the container being replaced. Point
# SELFSTREAM_BACKUP_DIR at a mounted host directory to have them land straight
# on a disk you already back up.
BACKUP_DIR = os.environ.get("SELFSTREAM_BACKUP_DIR", "/data/backups")
AVATAR_DIR = os.environ.get("SELFSTREAM_AVATAR_DIR", "/data/avatars")
MEDIA_DIR = os.environ.get("SELFSTREAM_MEDIA_DIR", "/data/media")

# The archive layout. Anything else in a tarball means it is not one of ours.
MANIFEST_NAME = "manifest.json"
DB_NAME = "upperroom.db"
AVATARS_PREFIX = "avatars/"

# Tables a restored database must have before we will put it in place. Not a
# schema version: _ensure_column is the migration story here, and a version
# number nobody remembers to bump is worse than none.
REQUIRED_TABLES = (
    "users", "channel_settings", "vods", "clips", "chat_log", "watch_sessions",
    "replay_chat", "media_views", "bans", "invites",
)


def prompt_password():
    first = getpass.getpass("Password: ")
    second = getpass.getpass("Repeat password: ")
    if first != second:
        print("Passwords did not match.")
        sys.exit(1)
    if len(first) < 8:
        print("Use at least 8 characters.")
        sys.exit(1)
    return first


def _counts(db_file):
    """A few row counts for the manifest, so a backup can be identified at a
    glance without unpacking it."""
    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row
    try:
        def one(sql):
            try:
                return conn.execute(sql).fetchone()[0]
            except sqlite3.Error:
                return 0
        return {
            "users": one("SELECT COUNT(*) FROM users"),
            "admins": one("SELECT COUNT(*) FROM users WHERE is_admin = 1"),
            "vods": one("SELECT COUNT(*) FROM vods"),
            "clips": one("SELECT COUNT(*) FROM clips"),
        }
    finally:
        conn.close()


def make_backup(out=None, avatar_dir=None):
    """Write a .tar.gz holding a consistent copy of the database and the avatar
    images, and return its path. Refuses to overwrite an existing file."""
    avatar_dir = avatar_dir or AVATAR_DIR
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    default_name = f"upperroom-backup-{stamp}.tar.gz"
    if not out:
        target = os.path.join(BACKUP_DIR, default_name)
    elif os.path.isdir(out):
        target = os.path.join(out, default_name)
    else:
        target = out
    if os.path.exists(target):
        raise FileExistsError(f"{target} already exists")
    os.makedirs(os.path.dirname(os.path.abspath(target)) or ".", exist_ok=True)
    with tempfile.TemporaryDirectory() as work:
        db_copy = os.path.join(work, DB_NAME)
        db.backup_to(db_copy)
        manifest = {
            "app": "upperroom",
            "created_at": int(time.time()),
            "db": DB_NAME,
            "tables": db.table_names(),
            **_counts(db_copy),
        }
        avatars = []
        if os.path.isdir(avatar_dir):
            avatars = sorted(
                name for name in os.listdir(avatar_dir)
                if os.path.isfile(os.path.join(avatar_dir, name))
            )
        manifest["avatars"] = len(avatars)
        manifest_path = os.path.join(work, MANIFEST_NAME)
        with open(manifest_path, "w") as handle:
            json.dump(manifest, handle, indent=2)
        # Write to a temp name and move into place, so an interrupted backup
        # never leaves behind a half-written archive that looks complete.
        partial = target + ".partial"
        with tarfile.open(partial, "w:gz") as tar:
            tar.add(manifest_path, arcname=MANIFEST_NAME)
            tar.add(db_copy, arcname=DB_NAME)
            for name in avatars:
                tar.add(os.path.join(avatar_dir, name), arcname=AVATARS_PREFIX + name)
        os.replace(partial, target)
    return target


def _safe_members(tar):
    """Every member of the archive, refusing anything that is not part of the
    layout we write. A backup may have travelled through someone's laptop, so
    absolute paths, parent traversal, symlinks and devices are all rejected
    rather than trusted."""
    members = []
    for member in tar.getmembers():
        name = member.name
        if name.startswith("/") or ".." in name.split("/"):
            raise ValueError(f"unsafe path in archive: {name}")
        if not (member.isfile() or member.isdir()):
            raise ValueError(f"unexpected entry in archive: {name}")
        if name in (MANIFEST_NAME, DB_NAME) or name.rstrip("/") == "avatars":
            members.append(member)
        elif name.startswith(AVATARS_PREFIX):
            members.append(member)
        else:
            raise ValueError(f"unexpected entry in archive: {name}")
    return members


def read_manifest(archive):
    """The archive's manifest, after checking the archive is one of ours and
    that every member is safe to extract."""
    with tarfile.open(archive, "r:gz") as tar:
        _safe_members(tar)
        try:
            handle = tar.extractfile(MANIFEST_NAME)
            manifest = json.load(handle)
        except (KeyError, ValueError, TypeError) as exc:
            raise ValueError("this file has no readable upperroom manifest") from exc
    if manifest.get("app") != "upperroom":
        raise ValueError("this archive was not made by upperroom")
    return manifest


def check_database(db_file):
    """Everything wrong with a database file we are about to put in place. An
    empty list means it is healthy."""
    problems = []
    try:
        conn = sqlite3.connect(db_file)
    except sqlite3.Error as exc:
        return [f"could not open the database ({exc})"]
    try:
        result = conn.execute("PRAGMA integrity_check").fetchone()
        if not result or result[0] != "ok":
            problems.append("the database failed its integrity check")
        present = {
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        missing = [name for name in REQUIRED_TABLES if name not in present]
        if missing:
            problems.append("missing tables: " + ", ".join(missing))
    except sqlite3.Error as exc:
        problems.append(f"could not read the database ({exc})")
    finally:
        conn.close()
    return problems


def _missing_media(db_file):
    """How many VOD and clip rows point at a file that is not in the media
    store. Recordings are deliberately not in a backup, so this is expected on a
    restore to a new server; it is reported rather than hidden."""
    conn = sqlite3.connect(db_file)
    missing = 0
    try:
        for table, folder in (("vods", "vods"), ("clips", "clips")):
            try:
                rows = conn.execute(f"SELECT filename FROM {table}").fetchall()
            except sqlite3.Error:
                continue
            for (filename,) in rows:
                if not filename:
                    continue
                path = os.path.join(MEDIA_DIR, folder, os.path.basename(filename))
                if not os.path.exists(path):
                    missing += 1
    finally:
        conn.close()
    return missing


def _move_aside(path, stamp):
    """Move something out of the way instead of deleting it. A restore is
    already a bad day; it must not also be the thing that loses the current
    data."""
    if not os.path.exists(path):
        return None
    kept = f"{path}.pre-restore-{stamp}"
    shutil.move(path, kept)
    return kept


def do_restore(archive, db_path, avatar_dir, force=False):
    """Put a backup back in place, returning a summary for the caller to print.
    Everything that can refuse does so before anything is written, and the
    database currently in place is moved aside, never deleted."""
    manifest = read_manifest(archive)
    if os.path.exists(db_path) and not force:
        raise FileExistsError(
            f"{db_path} already exists; pass --force to replace it "
            "(the current database is moved aside, not deleted)"
        )
    # Unpack beside the target so the final move is on one filesystem, and so a
    # failed check costs nothing but a temp directory.
    work = tempfile.mkdtemp(
        prefix=".upperroom-restore-",
        dir=os.path.dirname(os.path.abspath(db_path)) or ".",
    )
    try:
        with tarfile.open(archive, "r:gz") as tar:
            tar.extractall(path=work, members=_safe_members(tar), filter="data")
        restored_db = os.path.join(work, DB_NAME)
        if not os.path.exists(restored_db):
            raise ValueError("the archive has no database in it")
        problems = check_database(restored_db)
        if problems:
            raise ValueError("; ".join(problems))
        stamp = int(time.time())
        kept = {}
        for sidecar in ("", "-wal", "-shm"):
            moved = _move_aside(db_path + sidecar, stamp)
            if moved:
                kept[db_path + sidecar] = moved
        restored_avatars = os.path.join(work, "avatars")
        if os.path.isdir(restored_avatars):
            moved = _move_aside(avatar_dir, stamp)
            if moved:
                kept[avatar_dir] = moved
            shutil.move(restored_avatars, avatar_dir)
        shutil.move(restored_db, db_path)
        return {
            "manifest": manifest,
            "kept": kept,
            "missing_media": _missing_media(db_path),
        }
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main():
    parser = argparse.ArgumentParser(description="Manage an upperroom channel")
    sub = parser.add_subparsers(dest="command", required=True)

    p_add = sub.add_parser("adduser", help="create an account")
    p_add.add_argument("username")
    p_add.add_argument("--name", help="display name shown in chat")
    p_add.add_argument("--admin", action="store_true", help="give the admin badge")
    p_add.add_argument("--password", help="set the password without a prompt")
    p_add.add_argument("--email", help="address for the go-live email (optional)")

    p_pw = sub.add_parser("passwd", help="change a password")
    p_pw.add_argument("username")
    p_pw.add_argument("--password")

    p_email = sub.add_parser("setemail", help="set or clear the go-live email")
    p_email.add_argument("username")
    p_email.add_argument("email", nargs="?", default="", help="leave blank to clear")

    p_del = sub.add_parser("deluser", help="delete an account")
    p_del.add_argument("username")

    # Granting moderators is normally done in chat with the /mod command. These
    # are a command line fallback (e.g. to set the very first moderator).
    p_mod = sub.add_parser("mod", help="make an account a moderator")
    p_mod.add_argument("username")
    p_unmod = sub.add_parser("unmod", help="remove a moderator")
    p_unmod.add_argument("username")

    sub.add_parser("listusers", help="list all accounts")

    p_backup = sub.add_parser("backup", help="save accounts, chat and settings")
    p_backup.add_argument(
        "--out", help="file or directory to write to (default: SELFSTREAM_BACKUP_DIR)"
    )

    p_restore = sub.add_parser("restore", help="put a backup back in place")
    p_restore.add_argument("archive", help="path to a backup .tar.gz")
    p_restore.add_argument(
        "--force", action="store_true",
        help="replace the current database (it is moved aside, not deleted)",
    )

    args = parser.parse_args()

    # Backup and restore work on the database file itself, so they must not have
    # one created or migrated underneath them first.
    if args.command not in ("backup", "restore"):
        db.init_db()

    if args.command == "backup":
        if not os.path.exists(db.DB_PATH):
            print(f"No database at {db.DB_PATH}; nothing to back up.")
            sys.exit(1)
        try:
            path = make_backup(args.out)
        except (FileExistsError, OSError, sqlite3.Error) as exc:
            print(f"Backup failed: {exc}")
            sys.exit(1)
        size = os.path.getsize(path)
        readable = (
            f"{size / 1024 / 1024:.1f} MB" if size >= 1024 * 1024
            else f"{max(1, size // 1024)} KB"
        )
        print(f"Wrote {path} ({readable}).")
        print("This holds accounts, chat, settings and avatars.")
        print("It deliberately does NOT hold:")
        print("  - recordings and clips: far too large. Back up the media volume")
        print("    separately, or accept that a restore keeps the list but not the")
        print("    files.")
        print("  - your .env: it holds secrets and lives outside this container.")
        print("    Restoring without the original session secret only means")
        print("    everyone signs in again; the stream key is in the database.")
        return

    if args.command == "restore":
        try:
            summary = do_restore(args.archive, db.DB_PATH, AVATAR_DIR, force=args.force)
        except (FileExistsError, FileNotFoundError, ValueError, OSError,
                tarfile.TarError) as exc:
            print(f"Restore failed: {exc}")
            sys.exit(1)
        manifest = summary["manifest"]
        when = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(manifest.get("created_at", 0)))
        print(f"Restored the backup taken {when}.")
        users = manifest.get("users", 0)
        print(
            f"  {users} {'account' if users == 1 else 'accounts'} "
            f"({manifest.get('admins', 0)} admin), "
            f"{manifest.get('vods', 0)} recordings, {manifest.get('clips', 0)} clips."
        )
        for original, kept in summary["kept"].items():
            print(f"  Kept the previous {os.path.basename(original)} at {kept}")
        if summary["missing_media"]:
            print(
                f"  {summary['missing_media']} recordings or clips are listed but "
                "their files are not on this server (backups never hold them)."
            )
        if not manifest.get("admins"):
            print("  Warning: this backup has no admin account in it.")
        print("Start the gate again when you are ready.")
        return

    if args.command == "adduser":
        username = args.username.strip().lower()
        if db.get_user(username):
            print(f"User {username} already exists.")
            sys.exit(1)
        name = args.name or args.username
        password = args.password or prompt_password()
        db.add_user(username, name, password, is_admin=args.admin,
                    email=(args.email or "").strip())
        role = "admin" if args.admin else "viewer"
        print(f"Created {role} account {username} (shown as {name}).")

    elif args.command == "setemail":
        username = args.username.strip().lower()
        if not db.get_user(username):
            print(f"No such user {username}.")
            sys.exit(1)
        db.set_email(username, args.email.strip())
        if args.email.strip():
            print(f"Set {username}'s go-live email to {args.email.strip()}.")
        else:
            print(f"Cleared {username}'s go-live email.")

    elif args.command == "passwd":
        username = args.username.strip().lower()
        if not db.get_user(username):
            print(f"No such user {username}.")
            sys.exit(1)
        password = args.password or prompt_password()
        db.set_password(username, password)
        print(f"Updated the password for {username}.")

    elif args.command == "deluser":
        username = args.username.strip().lower()
        if db.delete_user(username):
            print(f"Deleted {username}.")
        else:
            print(f"No such user {username}.")

    elif args.command in ("mod", "unmod"):
        username = args.username.strip().lower()
        if not db.get_user(username):
            print(f"No such user {username}.")
            sys.exit(1)
        make = args.command == "mod"
        db.update_user(username, is_moderator=make)
        print(f"{username} is {'now' if make else 'no longer'} a moderator.")

    elif args.command == "listusers":
        users = db.list_users()
        if not users:
            print("No accounts yet. Create one with adduser.")
            return
        for u in users:
            tags = ""
            if u["is_admin"]:
                tags += " [admin]"
            if u["is_moderator"]:
                tags += " [mod]"
            print(f"{u['username']}  ({u['display_name']}){tags}")


if __name__ == "__main__":
    main()
