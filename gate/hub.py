"""
Chat and presence hub for the upperroom gate.

The Hub tracks who is connected, relays chat messages to everyone, and holds the
in-memory moderation state (timeouts and bans) alongside the persistent bits in
the database. A single module-level `hub` instance is shared across the service.
"""

import asyncio
import time
from collections import deque

import db
from config import CHAT_HISTORY, CHAT_RETENTION_SECONDS


# ---- Chat and presence ----------------------------------------------------

class Hub:
    """Tracks who is connected and relays chat messages to everyone."""

    def __init__(self):
        self._sockets = {}            # websocket -> {"username", "name", "admin", "mod"}
        self._history = deque(maxlen=CHAT_HISTORY)
        self._lock = asyncio.Lock()
        self._live = False            # whether the stream is currently live
        self._timeouts = {}           # username -> epoch until which they are muted
        self._banned = set()          # usernames with a persistent chat ban

    def viewers(self):
        # One entry per person, even if they have several tabs open.
        seen = {}
        for who in self._sockets.values():
            seen[who["username"]] = who
        return [
            {
                "username": w["username"],
                "name": w["name"],
                "avatar": w.get("avatar", 0),
                "admin": bool(w.get("admin")),
                "mod": bool(w.get("mod")),
            }
            for w in seen.values()
        ]

    def presence_message(self):
        viewers = self.viewers()
        return {"type": "presence", "viewers": viewers, "count": len(viewers)}

    async def broadcast(self, message):
        dead = []
        for socket in list(self._sockets):
            try:
                await socket.send_json(message)
            except Exception:
                dead.append(socket)
        for socket in dead:
            self._sockets.pop(socket, None)

    async def join(self, socket, who):
        # Replay the recent backlog so someone joining mid stream sees the last
        # messages. The backlog is kept until the stream ends, then wiped.
        async with self._lock:
            self._sockets[socket] = who
            history = list(self._history)
            # Only accrue watch time while the stream is actually live. Joining
            # while offline just puts you in chat; no watch session is opened.
            who["watch_id"] = None
            if self._live:
                try:
                    who["watch_id"] = db.start_watch_session(
                        who["username"], int(time.time())
                    )
                except Exception:
                    who["watch_id"] = None
        await socket.send_json({"type": "hello", "you": who, "history": history})
        await self.broadcast(self.presence_message())
        await self.broadcast(
            {"type": "system", "text": f"{who['name']} joined", "ts": int(time.time())}
        )

    async def leave(self, socket):
        who = self._sockets.pop(socket, None)
        if who and who.get("watch_id"):
            try:
                db.end_watch_session(who["watch_id"], int(time.time()))
            except Exception:
                pass
        await self.broadcast(self.presence_message())
        if who:
            await self.broadcast(
                {"type": "system", "text": f"{who['name']} left", "ts": int(time.time())}
            )

    async def say(self, who, text):
        ts = int(time.time())
        # Log first so the message carries its database row id. A moderator's
        # /del command uses that id to remove a specific line for everyone.
        msg_id = None
        try:
            msg_id = db.log_chat(who["username"], who["name"], text, ts)
        except Exception:
            pass
        message = {
            "type": "chat",
            "id": msg_id,
            "user": who["username"],
            "name": who["name"],
            "admin": who["admin"],
            "mod": who.get("mod", False),
            "avatar": who.get("avatar", 0),
            "font": who.get("font", "system"),
            "text": text,
            "ts": ts,
        }
        self._history.append(message)
        await self.broadcast(message)

    # ---- moderation -------------------------------------------------------

    def is_timed_out(self, username):
        until = self._timeouts.get(username)
        if not until:
            return 0
        remaining = until - int(time.time())
        if remaining <= 0:
            self._timeouts.pop(username, None)
            return 0
        return remaining

    def set_timeout(self, username, seconds):
        self._timeouts[username] = int(time.time()) + seconds

    def clear_timeout(self, username):
        self._timeouts.pop(username, None)

    def load_bans(self):
        try:
            self._banned = set(db.banned_usernames())
        except Exception:
            self._banned = set()

    def is_banned(self, username):
        return username in self._banned

    def add_ban_local(self, username):
        self._banned.add(username)

    def remove_ban_local(self, username):
        self._banned.discard(username)

    async def delete_last_by(self, username, by):
        """Mark the most recent visible message from a user as deleted, in the
        backlog and on every open page. Returns the message dict, or None."""
        target = None
        for message in reversed(self._history):
            if message.get("user") == username and not message.get("deleted"):
                target = message
                break
        if not target:
            return None
        target["deleted"] = True
        if target.get("id"):
            try:
                db.mark_chat_deleted(target["id"], by)
            except Exception:
                pass
        await self.broadcast({"type": "delete", "id": target.get("id")})
        return target

    async def update_role(self, username, mod=None):
        """Reflect a role change on a user's open sockets so their next message
        and the watching list show (or drop) the badge without a reconnect."""
        for who in self._sockets.values():
            if who["username"] == username and mod is not None:
                who["mod"] = bool(mod)
        await self.broadcast(self.presence_message())

    async def notify_user(self, username, text):
        """Send a private system line to one user's open sockets (e.g. to tell
        them they have been timed out). Others do not see it."""
        note = {"type": "system", "text": text, "ts": int(time.time())}
        for socket, who in list(self._sockets.items()):
            if who["username"] == username:
                try:
                    await socket.send_json(note)
                except Exception:
                    pass

    async def set_live(self, online):
        # Called by the stream watcher. Watch time only counts while live, so on
        # the transitions we open or close a watch session for everyone who is
        # already connected.
        if online == self._live:
            return
        now = int(time.time())
        async with self._lock:
            self._live = online
            for who in self._sockets.values():
                if online and not who.get("watch_id"):
                    try:
                        who["watch_id"] = db.start_watch_session(who["username"], now)
                    except Exception:
                        who["watch_id"] = None
                elif not online and who.get("watch_id"):
                    try:
                        db.end_watch_session(who["watch_id"], now)
                    except Exception:
                        pass
                    who["watch_id"] = None

    async def wipe(self):
        # Called when a broadcast ends. Clear the backlog and tell every open
        # page to empty its chat, so the next stream starts fresh.
        async with self._lock:
            self._history.clear()
        await self.broadcast({"type": "wipe"})

    async def update_member(self, username, avatar=None, font=None, name=None):
        # A viewer changed their avatar, chat font, or display name mid-session.
        # Point their open sockets at the new value so their next messages and
        # the watching list reflect it, without making them reconnect.
        for who in self._sockets.values():
            if who["username"] == username:
                if avatar is not None:
                    who["avatar"] = avatar
                if font is not None:
                    who["font"] = font
                if name is not None:
                    who["name"] = name
        await self.broadcast(self.presence_message())


hub = Hub()


async def chat_purge_worker():
    """Once a day, drop chat messages older than the retention window."""
    while True:
        try:
            db.purge_old_chat(int(time.time()) - CHAT_RETENTION_SECONDS)
        except Exception:
            pass
        await asyncio.sleep(86400)
