"""
Tiny in-process event bus used to decouple state-change code (Phases 2-10)
from the admin WebSocket broadcaster (Phase 11, /ws/admin in routers/admin.py).

Any code path that changes Application/Requirement/ReviewItem/Notification/
Consent state calls `emit(event_type, payload)` here. Until Phase 11 wires
a WebSocket manager in via `set_broadcaster()`, this is a safe no-op (also
useful for tests, which don't want a live websocket).
"""
import asyncio
import logging

logger = logging.getLogger("yono.events")

_broadcaster = None  # set by routers/admin.py at import/startup time


def set_broadcaster(fn):
    """fn(event_type: str, payload: dict) -> coroutine or None"""
    global _broadcaster
    _broadcaster = fn


def emit(event_type: str, payload: dict):
    """Fire-and-forget emit, safe to call from sync or async code, and safe
    to call before a broadcaster is registered (Phase 11 not wired yet, or
    running under pytest with no event loop)."""
    logger.info("event: %s %s", event_type, payload)
    if _broadcaster is None:
        return
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(_broadcaster(event_type, payload))
        else:
            loop.run_until_complete(_broadcaster(event_type, payload))
    except RuntimeError:
        # No event loop in this thread (e.g. plain script/test) -- skip.
        pass
