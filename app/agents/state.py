"""Shared, per-user conversational state for the Lumen agent layer.

These dicts hold short-lived multi-turn state that is read AND written across the
dispatcher and several handlers (e.g. the email draft refine/confirm flow and the
study-plan proposal flow). They were previously module globals inside
interaction_manager; extracting them here lets each handler live in its own module
while still sharing the exact same dict objects (handlers only ever *mutate* these
dicts, never rebind the names, so the shared reference stays intact).

``interaction_manager`` re-exports the public accessors for backwards
compatibility.
"""

from __future__ import annotations

# user_id → pending email draft awaiting refine/confirm
_pending_drafts: dict[str, dict] = {}

# user_id → pending study-plan proposal awaiting yes/no
_pending_proposals: dict[str, list] = {}

# Short-term conversation context per user: tracks last intent + awaiting state
# so follow-up messages are understood correctly.
_user_context: dict[str, dict] = {}


def _get_ctx(user_id: str) -> dict:
    return _user_context.get(user_id, {})


def _set_ctx(user_id: str, **kwargs) -> None:
    ctx = _user_context.get(user_id, {})
    ctx.update(kwargs)
    _user_context[user_id] = ctx


def _clear_ctx(user_id: str) -> None:
    _user_context.pop(user_id, None)


def get_pending_draft(user_id: str) -> dict | None:
    return _pending_drafts.get(user_id)


def clear_pending_draft(user_id: str) -> None:
    _pending_drafts.pop(user_id, None)
