"""
Seed a ready-to-explore demo instance for upperroom.

Run as a one-shot container under the "demo" compose profile:

    docker compose --profile demo up -d

This reuses the gate image and shares the gate's data volume, then idempotently
seeds a demo admin, two viewer accounts, a demo stream title and description, and
one unredeemed invite code (label "try me"), so a first-time evaluator lands on a
working, populated site with live video and chat.

It is safe to re-run: anything that already exists is left as is. It also refuses
to disturb a real install. If accounts already exist and none of them is the demo
admin, this is almost certainly someone's live instance with the demo profile
started against it by mistake, so it does nothing and logs why.

Credentials come from the environment, with documented defaults:

    DEMO_ADMIN_USER=demo
    DEMO_ADMIN_PASSWORD=demodemo123

The two viewer accounts are seeded with the same password. Seeding creates the
first account, so the first-run wizard at /setup seals itself just as it does on a
normal install.
"""

import logging
import os
import sqlite3
import time

import db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("upperroom.demo_seed")

ADMIN_USER = os.environ.get("DEMO_ADMIN_USER", "demo").strip().lower()
ADMIN_PASSWORD = os.environ.get("DEMO_ADMIN_PASSWORD", "demodemo123")
ADMIN_DISPLAY = "Demo Admin"

# Two generic viewer accounts so the watching list and dashboard look populated.
VIEWERS = (
    ("viewer_one", "Viewer One"),
    ("viewer_two", "Viewer Two"),
)

# The operator's brand, shown leading the visitor pages next to "powered by
# upperroom". A placeholder name so the demo shows off the two-layer brand: the
# site name up top, the per-broadcast stream title on the card below.
SITE_NAME = "Northwind Live"
STREAM_TITLE = "Demo Stream"
STREAM_DESCRIPTION = (
    "A synthetic broadcast so you can explore the site with live video, chat, "
    "and a populated dashboard. Sign in with a demo account to look around."
)
INVITE_LABEL = "try me"


def wait_for_db(attempts=30, delay=1.0):
    """Create the schema and wait until the shared database answers a query.

    The gate service may be initialising the same SQLite file at the same moment,
    so a lock is expected briefly; retry rather than failing the one-shot."""
    for attempt in range(1, attempts + 1):
        try:
            db.init_db()
            db.count_users()
            return
        except sqlite3.OperationalError as exc:
            logger.info(
                "database not ready yet (%s), retry %d/%d", exc, attempt, attempts
            )
            time.sleep(delay)
    raise SystemExit("demo seed: database did not become ready in time")


def seed():
    now = int(time.time())
    existing = db.count_users()
    admin = db.get_user(ADMIN_USER)

    # Refuse to touch a real install. If accounts already exist and none of them
    # is the demo admin, do nothing and say why, so running the demo profile
    # against a production database cannot damage it.
    if existing and not admin:
        logger.warning(
            "%d account(s) already exist and none is the demo admin %r; leaving "
            "this database untouched (demo profile run against a real install?). "
            "Nothing was changed.",
            existing, ADMIN_USER,
        )
        return

    # The demo admin. On an empty database this atomic first-user insert also
    # seals the /setup wizard, exactly as a real first run does. On a re-run the
    # admin is already present, so skip it.
    if admin:
        logger.info("demo admin %r already present; leaving it as is", ADMIN_USER)
    elif db.create_first_user(ADMIN_USER, ADMIN_DISPLAY, ADMIN_PASSWORD, now):
        logger.info(
            "created demo admin %r (password from DEMO_ADMIN_PASSWORD)", ADMIN_USER
        )
    else:
        # Lost a race with a concurrent first run; treat the admin as present.
        logger.info("demo admin was created concurrently; continuing")

    # Two viewer accounts, same password, skipped if already present.
    for username, display in VIEWERS:
        if db.get_user(username):
            logger.info("viewer %r already present; skipping", username)
            continue
        db.add_user(username, display, ADMIN_PASSWORD, is_admin=False)
        logger.info("created viewer %r", username)

    # The title and description shown on the home card and stamped onto the demo
    # recording. Setting the single settings row is safe to repeat.
    db.set_stream_info(
        site_name=SITE_NAME, title=STREAM_TITLE, description=STREAM_DESCRIPTION
    )
    logger.info("set site name %r and stream title %r", SITE_NAME, STREAM_TITLE)

    # One unredeemed invite so the login-page invite flow is demonstrable. Only
    # mint a fresh code if no active (unredeemed, unrevoked) "try me" code exists.
    active = [
        i for i in db.list_invites()
        if i["label"] == INVITE_LABEL and not i["redeemed_at"] and not i["revoked_at"]
    ]
    if active:
        logger.info("invite %r already active: %s", INVITE_LABEL, active[0]["code"])
    else:
        code = db.create_invite(INVITE_LABEL, ADMIN_USER, now)
        logger.info(
            "created invite %r: %s (redeem it from the login page)", INVITE_LABEL, code
        )

    # Give one viewer a starting balance so the highlight redemption can be tried
    # right away. Only when it is still zero, so a re-run never piles points onto
    # an account that has been spending them down.
    if db.get_user("viewer_one") and db.get_points("viewer_one") == 0:
        db.credit_points(["viewer_one"], 120)
        logger.info("gave viewer_one a starting balance of 120 points")
    else:
        logger.info("viewer_one balance left as is")

    logger.info("demo seed complete")


def main():
    wait_for_db()
    seed()


if __name__ == "__main__":
    main()
