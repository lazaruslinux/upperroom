"""
Chat WebSocket route and slash-command handling.

Moderation is driven entirely by commands typed into chat, not buttons. The chat
socket intercepts any message starting with "/", authorizes it against a fresh
database read of the sender's role, and never echoes it to other people.
"""

import logging
import secrets
import time
from collections import deque

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

import db
from auth import country_allowed, read_session
from config import COOKIE_NAME, MAX_MESSAGE_LENGTH
from hub import hub

logger = logging.getLogger("upperroom.ws")

router = APIRouter()


# ---- Chat slash commands --------------------------------------------------

DEFAULT_TIMEOUT_SECONDS = 300
MAX_TIMEOUT_SECONDS = 86400


async def system_reply(websocket, text):
    """Send a private system line back to the person who ran a command."""
    try:
        await websocket.send_json(
            {"type": "system", "text": text, "ts": int(time.time())}
        )
    except Exception:
        logger.debug("system_reply send failed", exc_info=True)


def _target_name(arg):
    return arg.strip().lower().lstrip("@")


def _split_banned(raw):
    """The admin's banned-words text is split on newlines and commas into a set
    of lowercased phrases, empties dropped."""
    words = set()
    for chunk in str(raw or "").replace(",", "\n").split("\n"):
        word = chunk.strip().lower()
        if word:
            words.add(word)
    return words


def _contains_banned(text_lower, raw):
    return any(word in text_lower for word in _split_banned(raw))


async def handle_mod_delete(websocket, who, raw_id):
    """The hover-to-delete button on a chat line. Authorize against a fresh role
    read (so a demotion takes effect at once), then remove that one message for
    everyone. A non-admin cannot delete an admin's line."""
    actor = db.get_user(who["username"])
    is_admin = bool(actor and actor["is_admin"])
    is_mod = bool(actor and actor["is_moderator"])
    if not (is_admin or is_mod):
        await system_reply(websocket, "You do not have permission to delete messages.")
        return
    try:
        msg_id = int(raw_id)
    except (TypeError, ValueError):
        return
    result = await hub.delete_by_id(msg_id, who["username"], is_admin)
    if result == "forbidden":
        await system_reply(websocket, "You can't delete an admin's message.")


async def handle_command(websocket, who, text):
    parts = text[1:].split()
    if not parts:
        return
    cmd = parts[0].lower()
    args = parts[1:]

    # Role is read fresh from the database, never trusted from the cookie, so a
    # demotion takes effect immediately.
    actor = db.get_user(who["username"])
    is_admin = bool(actor and actor["is_admin"])
    is_mod = bool(actor and actor["is_moderator"])

    if cmd == "help":
        lines = ["Chat commands:"]
        if is_admin or is_mod:
            lines += [
                "/timeout <user> [seconds] - mute a viewer (default 300)",
                "/untimeout <user> - lift a timeout",
                "/del <user> - delete that viewer's last message",
                "/purge <user> - delete all of that viewer's messages",
                "/ban <user> [reason] - ban a viewer from chat",
                "/unban <user> - lift a ban (yours, or any if admin)",
            ]
        if is_admin:
            lines += [
                "/mod <user> - make someone a chat moderator",
                "/unmod <user> - remove a chat moderator",
            ]
        if len(lines) == 1:
            lines.append("Your account has no chat commands.")
        await system_reply(websocket, "\n".join(lines))
        return

    if not (is_admin or is_mod):
        await system_reply(websocket, "You do not have permission for chat commands.")
        return

    # Granting and removing moderators is admin only: moderators cannot mint more
    # moderators.
    if cmd in ("mod", "unmod"):
        if not is_admin:
            await system_reply(websocket, "Only an admin can change moderators.")
            return
        if not args:
            await system_reply(websocket, f"Usage: /{cmd} <username>")
            return
        target = db.get_user(_target_name(args[0]))
        if not target:
            await system_reply(websocket, f"No account named {_target_name(args[0])}.")
            return
        make = cmd == "mod"
        if bool(target["is_moderator"]) == make:
            state = "already" if make else "not"
            await system_reply(
                websocket, f"{target['display_name']} is {state} a moderator."
            )
            return
        db.update_user(target["username"], is_moderator=make)
        await hub.update_role(target["username"], mod=make)
        word = "now" if make else "no longer"
        await system_reply(
            websocket, f"{target['display_name']} is {word} a moderator."
        )
        await hub.notify_user(
            target["username"],
            "You are now a chat moderator." if make
            else "You are no longer a chat moderator.",
        )
        return

    # timeout / untimeout / del all act on a target, and a moderator may not act
    # on an admin (only another admin can).
    if not args:
        await system_reply(websocket, f"Usage: /{cmd} <username>")
        return
    target = db.get_user(_target_name(args[0]))
    if not target:
        await system_reply(websocket, f"No account named {_target_name(args[0])}.")
        return
    if target["is_admin"] and not is_admin:
        await system_reply(websocket, "You can't moderate an admin.")
        return

    if cmd == "timeout":
        seconds = DEFAULT_TIMEOUT_SECONDS
        if len(args) > 1:
            try:
                seconds = max(1, min(MAX_TIMEOUT_SECONDS, int(args[1])))
            except ValueError:
                await system_reply(websocket, "Seconds must be a whole number.")
                return
        hub.set_timeout(target["username"], seconds)
        await system_reply(
            websocket, f"{target['display_name']} is timed out for {seconds}s."
        )
        await hub.notify_user(
            target["username"],
            f"A moderator has timed you out for {seconds} seconds.",
        )
        return

    if cmd == "untimeout":
        hub.clear_timeout(target["username"])
        await system_reply(websocket, f"Timeout lifted for {target['display_name']}.")
        await hub.notify_user(target["username"], "Your timeout has been lifted.")
        return

    if cmd == "del":
        removed = await hub.delete_last_by(target["username"], who["username"])
        if removed:
            await system_reply(
                websocket, f"Deleted {target['display_name']}'s last message."
            )
        else:
            await system_reply(
                websocket, f"No recent message from {target['display_name']}."
            )
        return

    if cmd == "purge":
        count = await hub.delete_all_by(target["username"], who["username"])
        await system_reply(
            websocket,
            f"Purged {count} message{'' if count == 1 else 's'} from {target['display_name']}.",
        )
        return

    if cmd == "ban":
        reason = " ".join(args[1:])[:200]
        try:
            db.add_ban(target["username"], who["username"], reason, int(time.time()))
        except Exception:
            await system_reply(websocket, "Could not save the ban.")
            return
        hub.add_ban_local(target["username"])
        await system_reply(
            websocket, f"{target['display_name']} is banned from chat."
        )
        await hub.notify_user(
            target["username"], "You have been banned from chat."
        )
        return

    if cmd == "unban":
        existing = db.get_ban(target["username"])
        if not existing:
            await system_reply(
                websocket, f"{target['display_name']} is not banned."
            )
            return
        # A moderator may lift only a ban they issued; an admin may lift any.
        if not is_admin and existing["banned_by"] != who["username"]:
            await system_reply(
                websocket, "Only the moderator who set this ban, or an admin, can lift it."
            )
            return
        db.remove_ban(target["username"])
        hub.remove_ban_local(target["username"])
        await system_reply(websocket, f"{target['display_name']} is unbanned.")
        await hub.notify_user(
            target["username"], "Your chat ban has been lifted."
        )
        return

    await system_reply(websocket, f"Unknown command /{cmd}. Try /help.")


async def overlay_socket(websocket: WebSocket, key):
    """A read-only connection for the OBS chat overlay. It authenticates with a
    bearer key in the URL (OBS cannot sign in), receives every broadcast, and is
    kept out of presence and the watching count. Any frame it sends is ignored."""
    stored = db.get_overlay_key()
    # Constant-time compare, and refuse if no key has ever been generated so an
    # empty/absent key can never authenticate.
    if not stored or not secrets.compare_digest(str(key), stored):
        await websocket.close(code=4401)
        return
    await websocket.accept()
    hub.add_watcher(websocket)
    try:
        while True:
            # Drain and discard anything the source sends; the overlay is
            # display-only. Stop on the disconnect frame (or any receive error).
            try:
                message = await websocket.receive()
            except Exception:
                break
            if message.get("type") == "websocket.disconnect":
                break
    finally:
        hub.remove_watcher(websocket)


@router.websocket("/ws")
async def chat_socket(websocket: WebSocket):
    # The OBS overlay authenticates with a key in the query string instead of a
    # session cookie, and connects read-only. Handle it before the cookie gate.
    overlay_key = websocket.query_params.get("overlay")
    if overlay_key is not None:
        await overlay_socket(websocket, overlay_key)
        return

    session = read_session(websocket.cookies.get(COOKIE_NAME, ""))
    if not session:
        await websocket.close(code=4401)
        return

    forwarded = websocket.headers.get("x-forwarded-for", "")
    ws_ip = forwarded.split(",")[0].strip() if forwarded else (
        websocket.client.host if websocket.client else ""
    )
    if not country_allowed(ws_ip):
        await websocket.close(code=4403)
        return

    user = db.get_user(session["sub"])
    # If the account was deleted, the token may still be valid but there is no
    # one to be: refuse the socket rather than seating a ghost in chat.
    if not user:
        await websocket.close(code=4401)
        return
    who = {
        "username": session["sub"],
        "name": user["display_name"],
        "admin": bool(user["is_admin"]),
        "mod": bool(user["is_moderator"]),
        "avatar": user["avatar_version"],
        "font": user["chat_font"],
        "name_color": user["name_color"],
        "msg_color": user["msg_color"],
    }
    await websocket.accept()
    await hub.join(websocket, who)

    sent_times = deque(maxlen=5)
    try:
        while True:
            try:
                data = await websocket.receive_json()
            except WebSocketDisconnect:
                break
            except Exception:
                logger.debug("ignoring unparseable chat frame", exc_info=True)
                continue
            # The hover-to-delete button on a chat line sends this instead of a
            # chat message; it removes one message by id for everyone.
            if data.get("type") == "moddelete":
                await handle_mod_delete(websocket, who, data.get("id"))
                continue
            if data.get("type") != "chat":
                continue
            text = str(data.get("text", "")).strip()[:MAX_MESSAGE_LENGTH]
            if not text:
                continue
            # A leading slash is a moderation command, handled and answered
            # privately, never shown to other viewers.
            if text.startswith("/"):
                await handle_command(websocket, who, text)
                continue
            # A banned viewer cannot chat at all (a persistent timeout).
            if hub.is_banned(who["username"]):
                await system_reply(websocket, "You are banned from chat.")
                continue
            # A timed-out viewer's messages are dropped, with a private notice.
            remaining = hub.is_timed_out(who["username"])
            if remaining:
                await system_reply(
                    websocket, f"You are timed out for {remaining} more seconds."
                )
                continue
            # Chat moderation settings are read fresh so an admin's change to the
            # word filter or slow-mode interval takes effect immediately.
            settings = db.get_chat_moderation()
            if _contains_banned(text.lower(), settings.get("banned_words", "")):
                await system_reply(
                    websocket, "Your message was blocked by the word filter."
                )
                continue
            interval = settings.get("slow_mode_seconds", 0) or 0
            # Mods and admins are exempt from slow mode.
            if interval and not (who["admin"] or who.get("mod")):
                wait = hub.slow_wait(who["username"], interval)
                if wait:
                    await system_reply(
                        websocket,
                        f"Slow mode is on. Wait {wait}s before posting again.",
                    )
                    continue
            # Flood guard: drop anything past five messages in three seconds.
            now = time.time()
            sent_times.append(now)
            if len(sent_times) == sent_times.maxlen and now - sent_times[0] < 3:
                continue
            await hub.say(who, text)
            hub.record_post(who["username"])
    finally:
        await hub.leave(websocket)
