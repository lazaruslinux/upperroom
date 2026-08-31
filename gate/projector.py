"""
The gate's half of the projector link.

The projector is a separate service that runs on whatever machine holds the
operator's media library. It connects out to this gate over one WebSocket and
does the playing; the gate never dials it, never learns where it is, and needs
no port open on the operator's side. This module owns that socket: one at a
time, newest wins, with a small request/reply protocol over it.

The wire format is JSON, three shapes:

    request   {"id": 7, "method": "search", "params": {"query": "..."}}
    reply     {"id": 7, "result": ...}   or   {"id": 7, "error": "..."}
    event     {"event": "status", "state": "playing", ...}

Requests only ever go gate -> projector. Events only ever come back the other
way, and are how the projector reports what it is doing without being asked.

The FIRST event of a connection is special: the projector sends its current
state the moment it connects, so that frame is where it already is rather than
something that just happened. It is flagged as such for the handler, because a
projector restart otherwise looks exactly like a title ending.
"""

import asyncio
import logging
import time

logger = logging.getLogger("upperroom.projector")

# Every call has a deadline, because the far end is a machine in somebody's
# house on a link we do not control, and a route that waits forever is a route
# that holds a worker forever.
RPC_TIMEOUT = 10          # search and art: reads, answered from memory or the library
PLAY_TIMEOUT = 20         # play and stop: the projector has to start or kill ffmpeg


class ProjectorError(Exception):
    """No projector, or the projector could not do it. Routes turn this into a
    502 rather than a 500: nothing here is the gate's fault."""


class ProjectorLink:
    """The single projector socket and the calls in flight over it."""

    def __init__(self):
        self._socket = None
        self._pending = {}        # request id -> Future waiting for its reply
        self._next_id = 0
        self._last_seen = 0
        # Whether the next event is this connection's opening state report.
        self._opening_due = True
        # Set by theater.py at import. Keeping it a hook rather than an import
        # avoids a cycle: theater already imports this module.
        self.on_event = None

    def connected(self):
        return self._socket is not None

    def last_seen(self):
        """Epoch of the last frame from the projector, or 0 if never."""
        return self._last_seen

    async def attach(self, socket):
        """Seat a newly authenticated projector, replacing any current one.

        Newest wins: an operator restarting the projector, or moving it to
        another machine, must not have to wait for a half-dead socket to time
        out. The old one is closed here rather than left to discover it."""
        previous = self._socket
        self._socket = socket
        self._last_seen = int(time.time())
        # A new connection opens with a state report, whatever the last one had
        # already told us.
        self._opening_due = True
        if previous is not None and previous is not socket:
            logger.info("a new projector connected; closing the previous one")
            try:
                await previous.close(code=4409)
            except Exception:
                logger.debug("closing the previous projector failed", exc_info=True)

    def detach(self, socket):
        """Forget a projector socket, if it is still the current one. The guard
        matters: a socket displaced by a newer one detaches on its way out, and
        without it that would clear the live connection."""
        if self._socket is socket:
            self._socket = None
            self._fail_pending("the projector disconnected")

    def _fail_pending(self, why):
        for future in self._pending.values():
            if not future.done():
                future.set_exception(ProjectorError(why))
        self._pending.clear()

    async def handle(self, message):
        """Route one inbound frame: a reply to something we asked, or an event."""
        self._last_seen = int(time.time())
        if not isinstance(message, dict):
            return
        if "id" in message:
            future = self._pending.pop(message["id"], None)
            if future is None or future.done():
                # A reply to a call that already timed out. Nothing to do with it.
                return
            if message.get("error"):
                future.set_exception(ProjectorError(str(message["error"])))
            else:
                future.set_result(message.get("result"))
            return
        if not message.get("event"):
            return
        # Consumed whether or not anything is listening, so the opening report
        # cannot be spent twice or carried into the next frame.
        opening = self._opening_due
        self._opening_due = False
        if self.on_event:
            try:
                await self.on_event(message, opening)
            except Exception:
                logger.warning("projector event handler failed", exc_info=True)

    async def rpc(self, method, params=None, timeout=RPC_TIMEOUT):
        """Ask the projector something and wait for its answer.

        Raises ProjectorError when there is no projector, the call times out, or
        the projector answers with an error."""
        socket = self._socket
        if socket is None:
            raise ProjectorError("no projector is connected")
        self._next_id += 1
        request_id = self._next_id
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        try:
            await socket.send_json(
                {"id": request_id, "method": method, "params": params or {}}
            )
            return await asyncio.wait_for(future, timeout=timeout)
        except ProjectorError:
            raise
        except asyncio.TimeoutError:
            raise ProjectorError(f"the projector did not answer {method} in time")
        except Exception as exc:
            raise ProjectorError(f"could not reach the projector: {exc!r}")
        finally:
            self._pending.pop(request_id, None)

    async def close(self):
        """Drop the current projector, e.g. after its key was regenerated."""
        socket = self._socket
        self._socket = None
        self._fail_pending("the projector key was regenerated")
        if socket is not None:
            try:
                await socket.close(code=4401)
            except Exception:
                logger.debug("closing the projector failed", exc_info=True)


link = ProjectorLink()
