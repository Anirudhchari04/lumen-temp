"""Event Bus — Simple in-process pub/sub for decoupled agent communication.

Agents publish events (e.g., 'progress_updated'), other agents subscribe.
Designed to be swapped with Azure Service Bus / Event Grid in production.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from datetime import datetime, timezone as _tz
UTC = _tz.utc
from typing import Any, Callable, Coroutine

logger = logging.getLogger(__name__)

# Type for async event handlers
EventHandler = Callable[[dict], Coroutine[Any, Any, None]]

_subscribers: dict[str, list[EventHandler]] = defaultdict(list)
_event_log: list[dict] = []
MAX_LOG_SIZE = 500


def subscribe(event_type: str, handler: EventHandler) -> None:
    """Subscribe an async handler to an event type."""
    _subscribers[event_type].append(handler)
    logger.info(f"Event bus: subscribed to '{event_type}' -> {handler.__name__}")


async def publish(event_type: str, data: dict) -> None:
    """Publish an event to all subscribers. Fire-and-forget."""
    event = {
        "type": event_type,
        "data": data,
        "timestamp": datetime.now(UTC).isoformat(),
    }
    _event_log.append(event)
    if len(_event_log) > MAX_LOG_SIZE:
        _event_log.pop(0)

    handlers = _subscribers.get(event_type, [])
    if not handlers:
        logger.debug(f"Event '{event_type}' published, no subscribers")
        return

    for handler in handlers:
        try:
            await handler(data)
        except Exception as e:
            logger.error(f"Event handler {handler.__name__} failed for '{event_type}': {e}")


def get_recent_events(event_type: str | None = None, limit: int = 20) -> list[dict]:
    """Get recent events, optionally filtered by type."""
    events = _event_log if not event_type else [e for e in _event_log if e["type"] == event_type]
    return events[-limit:]


# ── Standard Event Types ─────────────────────────────────────
PROGRESS_UPDATED = "progress_updated"
LUMEN_CREATED = "lumen_created"
TA_REGISTERED = "ta_registered"
SESSION_STARTED = "session_started"
SESSION_ENDED = "session_ended"
PEER_CONNECTED = "peer_connected"
