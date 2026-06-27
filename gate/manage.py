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

    p_pw = sub.add_parser("passwd", help="change a password")
    p_pw.add_argument("username")
    p_pw.add_argument("--password")

    p_del = sub.add_parser("deluser", help="delete an account")
    p_del.add_argument("username")

    sub.add_parser("listusers", help="list all accounts")

    args = parser.parse_args()

    if args.command == "adduser":
        username = args.username.strip().lower()
        if db.get_user(username):
            print(f"User {username} already exists.")
            sys.exit(1)
        name = args.name or args.username
        password = args.password or prompt_password()
        db.add_user(username, name, password, is_admin=args.admin)
        role = "admin" if args.admin else "viewer"
        print(f"Created {role} account {username} (shown as {name}).")

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

    elif args.command == "listusers":
        users = db.list_users()
        if not users:
            print("No accounts yet. Create one with adduser.")
            return
        for u in users:
            tag = " [admin]" if u["is_admin"] else ""
            print(f"{u['username']}  ({u['display_name']}){tag}")


if __name__ == "__main__":
    main()
