"""
SSE broadcaster for the meh-scanner dashboard.

Design
──────
* One asyncio.Queue[dict | None] per connected browser tab.
* Queues hold message DICTS (event + data) consumed by sse-starlette's
  EventSourceResponse generator — not pre-formatted strings.
* All methods are coroutines on the event loop; no threading.Lock needed
  because the event loop is single-threaded and list mutations here never
  span an await point.
* Slow / full queues are silently skipped; the client resyncs via
  status-update on the next reconnect.
* Shutdown sends a None sentinel to every queue so generator loops exit.

sse-starlette message dict format
──────────────────────────────────
  Named event : {"event": "scan-complete", "data": "<json string>"}
  Comment     : {"comment": "keepalive"}
  Shutdown    : None  (sentinel — not yielded to the client)
"""
from __future__ import annotations

import asyncio
import json

# ── wire-level constants ──────────────────────────────────────────────────────

# Keepalive comment sent every 15 s so proxies don't close idle connections
KEEPALIVE: dict = {"comment": "keepalive"}

# Named SSE event identifiers — imported by app.py for consistency
EVENT_SCAN_STARTED:  str = "scan-started"
EVENT_SCAN_COMPLETE: str = "scan-complete"
EVENT_STATUS_UPDATE: str = "status-update"


# ── helper ────────────────────────────────────────────────────────────────────

def make_msg(event: str, data: dict | None = None) -> dict:
    """
    Build a message dict for sse-starlette EventSourceResponse.
    The data value is JSON-serialised here so the generator just yields dicts.
    """
    return {
        "event": event,
        "data":  json.dumps(data or {}, default=str, ensure_ascii=False),
    }


# ── broadcaster ───────────────────────────────────────────────────────────────

class Broadcaster:
    """
    Fan-out broadcaster: publish() delivers one message to every subscriber.

    Each connected browser tab subscribes with subscribe() and receives its own
    asyncio.Queue.  The /api/events generator pulls from that queue.
    """

    _QUEUE_MAXSIZE: int = 64   # capacity per client; infrequent events so 64 is plenty

    def __init__(self) -> None:
        self._queues: list[asyncio.Queue[dict | None]] = []

    # ── subscription ──────────────────────────────────────────────────────────

    async def subscribe(self) -> asyncio.Queue[dict | None]:
        """Register a new client; returns a dedicated queue for that connection."""
        q: asyncio.Queue[dict | None] = asyncio.Queue(maxsize=self._QUEUE_MAXSIZE)
        self._queues.append(q)
        return q

    async def unsubscribe(self, q: asyncio.Queue) -> None:
        """Remove a client's queue; safe to call even if already removed."""
        try:
            self._queues.remove(q)
        except ValueError:
            pass

    # ── fan-out ───────────────────────────────────────────────────────────────

    async def publish(self, event: str, data: dict | None = None) -> None:
        """
        Broadcast a named event to ALL connected clients.
        Clients with a full queue (slow consumer) silently miss this message
        and will re-sync their state when they reconnect.
        """
        msg = make_msg(event, data)
        for q in list(self._queues):          # snapshot avoids mutation-during-iter
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                pass                          # slow client — skip this message

    # ── lifecycle ─────────────────────────────────────────────────────────────

    async def close_all(self) -> None:
        """
        Graceful shutdown: send None sentinel to every queue so that generator
        loops in /api/events exit their while-True cleanly.
        """
        for q in self._queues:
            try:
                q.put_nowait(None)
            except asyncio.QueueFull:
                pass
        self._queues.clear()

    # ── introspection ─────────────────────────────────────────────────────────

    @property
    def client_count(self) -> int:
        return len(self._queues)


# ── singleton ─────────────────────────────────────────────────────────────────

broadcaster = Broadcaster()
