"""
Admin tool for selfstream accounts.

There are no public sign ups. You create every account by hand with this tool.
Run it inside the running gate container. Examples:

    docker compose exec gate python manage.py adduser alice
    docker compose exec gate python manage.py adduser sam --admin
    docker compose exec gate python manage.py listusers
    docker compose exec gate python manage.py passwd alice
    docker compose exec gate python manage.py deluser alice

Usernames are stored in lower case. The display name is what other viewers see
in chat and in the watching list. If you do not pass a password on the command
line you are prompted for it without it showing on screen.
"""

import argparse
import getpass
import sys

import db


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


def main():
    db.init_db()
    parser = argparse.ArgumentParser(description="Manage selfstream accounts")
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

    args = parser.parse_args()

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
