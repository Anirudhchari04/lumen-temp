"""Shared helpers used by multiple Lumen agent handler modules.

Extracted from interaction_manager so each handler module can live on its own.
``interaction_manager`` re-exports these for backwards compatibility.
"""

from __future__ import annotations


def _ensure_intent(result: dict, intent: str, agent_id: str | None = None) -> dict:
    """Backfill intent/agent_id on results coming back from an A2A self-call.

    The handler-side dicts already include intent and agent_id, but if a
    response only has the text part (older external caller) the JSON may be
    missing those fields. Fill them in defensively.
    """
    result = dict(result or {})
    result.setdefault("intent", intent)
    if agent_id is not None:
        result.setdefault("agent_id", agent_id)
    return result


def _google_consent_response(service: str, message: str, intent, agent_id: str) -> dict:
    """Build an in-chat Google consent prompt.

    Rendered by the frontend as a card with "Allow once" / "Always allow"
    buttons. On approval the frontend connects Google and re-sends `message`.
    """
    return {
        "reply": (
            f"🔐 Lumen needs access to your **{service}** to do that. "
            "Choose **Allow once** for this request only, or **Always allow** to keep it connected."
        ),
        "action": "google_consent",
        "intent": intent,
        "agent_id": agent_id,
        "cards": [{
            "type": "connect_google",
            "data": {"service": service, "retry_message": message},
        }],
    }
