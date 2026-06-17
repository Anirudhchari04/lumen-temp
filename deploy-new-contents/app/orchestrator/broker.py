"""Message Broker — subscriber/broker routing for Lumen agents.

This is the broker in a subscriber–broker model. Specialist agents *subscribe*
to a topic (their agent id, e.g. "github", "calendar"). The orchestrator
(``interaction_manager``) and the A2A layer *publish* a request to a topic; the
broker delivers it to the subscribed handler and returns its reply (request/
reply). Fire-and-forget *notifications* are fanned out to all subscribers.

The broker sits at the agent-routing layer: the underlying transport (in-process
calls, A2A HTTP self-calls, or Magentic-One) is unchanged — the broker just
decouples *who asks* from *who answers*. In production the in-process delivery
can be swapped for Azure Service Bus / Event Grid without touching publishers.
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from app.events import bus as _bus

logger = logging.getLogger("lumen.broker")

# topic -> request/reply handler. Exactly one handler per topic (the agent that
# owns it). Envelope in, response dict out.
Handler = Callable[[dict], Awaitable[dict]]

_handlers: dict[str, Handler] = {}
# topic aliases (e.g. legacy "portfolio" -> "github")
_aliases: dict[str, str] = {}


def subscribe(topic: str, handler: Handler) -> None:
    """Register the owning handler for a topic. Last registration wins."""
    _handlers[topic] = handler
    logger.info("broker: '%s' subscribed -> %s", topic, getattr(handler, "__name__", handler))


def alias(topic: str, target: str) -> None:
    """Make ``topic`` resolve to ``target``'s handler (back-compat routing)."""
    _aliases[topic] = target


def _resolve(topic: str) -> str:
    seen = set()
    while topic in _aliases and topic not in seen:
        seen.add(topic)
        topic = _aliases[topic]
    return topic


def has_topic(topic: str) -> bool:
    return _resolve(topic) in _handlers


def topics() -> list[str]:
    return sorted(set(_handlers) | set(_aliases))


def make_envelope(user_id: str, message: str, **meta: Any) -> dict:
    """Build the standard request envelope passed to subscribers."""
    env = {"user_id": user_id, "message": message}
    env.update({k: v for k, v in meta.items() if v is not None})
    return env


async def request(topic: str, envelope: dict) -> dict:
    """Publish a request to ``topic`` and return the subscriber's reply dict.

    Raises KeyError if no subscriber owns the (resolved) topic, so callers can
    fall back to a default route.
    """
    resolved = _resolve(topic)
    handler = _handlers.get(resolved)
    if handler is None:
        raise KeyError(f"No subscriber for topic '{topic}'")
    # Mirror the request onto the event log for observability.
    await _bus.publish("broker_request", {"topic": resolved,
                                          "user": envelope.get("user_id"),
                                          "message": (envelope.get("message") or "")[:200]})
    return await handler(envelope)


async def notify(event_type: str, data: dict) -> None:
    """Fire-and-forget broadcast to all event-bus subscribers of ``event_type``."""
    await _bus.publish(event_type, data)
