"""Lumen Access Control — connections & two-tier access.

Implements the design's connection model (§13): conversation and private
profile data are gated behind a consented connection between two lumens. A
request is *pending* until the addressee's owner accepts — consent lives with
the addressee, like a friend request. Accepted connections are symmetric.

State is mirrored on both lumen docs under ``connections`` (a map keyed by the
peer's user id), reusing the existing persistence path.

Statuses (from a given lumen's point of view):
  - ``pending_out`` — this lumen sent a request, awaiting the peer's consent.
  - ``pending_in``  — the peer requested; this lumen must accept/reject.
  - ``accepted``    — mutual; conversation + private profile unlocked.
  - ``rejected``    — request declined.
  - ``blocked``     — this lumen blocked the peer.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from app.lumen.core import get_lumen, save_lumen

logger = logging.getLogger(__name__)

UTC = timezone.utc


class ConnectionError(Exception):
    """Raised for invalid connection-lifecycle transitions."""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _conns(lumen: dict) -> dict:
    c = lumen.get("connections")
    if not isinstance(c, dict):
        c = {}
        lumen["connections"] = c
    return c


def _peer_view(lumen: dict) -> dict:
    return {
        "peer_id": lumen["id"],
        "peer_username": lumen.get("username", ""),
        "peer_name": lumen.get("name", ""),
    }


def get_status(user_id: str, peer_id: str, lumen: dict | None = None) -> str | None:
    """Status of the relationship from ``user_id``'s perspective, or None."""
    src = lumen
    if src is None:
        return None
    entry = _conns(src).get(peer_id)
    return entry.get("status") if entry else None


async def is_connected(a_id: str, b_id: str) -> bool:
    """True iff an accepted connection exists between the two lumens."""
    if a_id == b_id:
        return True
    a = await get_lumen(a_id)
    if not a:
        return False
    entry = _conns(a).get(b_id)
    return bool(entry and entry.get("status") == "accepted")


async def list_connections(user_id: str) -> list[dict]:
    lumen = await get_lumen(user_id)
    if not lumen:
        return []
    return [
        {"peer_id": pid, **{k: v for k, v in entry.items()}}
        for pid, entry in _conns(lumen).items()
    ]


async def send_request(from_id: str, to_id: str) -> dict:
    """Send a connection request. Idempotent; auto-accepts a mutual pending."""
    if from_id == to_id:
        raise ConnectionError("cannot connect a lumen to itself")
    frm = await get_lumen(from_id)
    to = await get_lumen(to_id)
    if not frm or not to:
        raise ConnectionError("lumen not found")

    existing = _conns(frm).get(to_id, {}).get("status")
    if existing == "accepted":
        return {"status": "accepted"}
    if existing == "blocked":
        raise ConnectionError("you have blocked this lumen")
    if _conns(to).get(from_id, {}).get("status") == "blocked":
        raise ConnectionError("connection not permitted")

    # If the peer already requested us, accepting their pending closes the loop.
    if _conns(frm).get(to_id, {}).get("status") == "pending_in":
        return await accept(from_id, to_id)

    now = _now()
    _conns(frm)[to_id] = {**_peer_view(to), "status": "pending_out",
                          "created_at": now, "updated_at": now}
    _conns(to)[from_id] = {**_peer_view(frm), "status": "pending_in",
                           "created_at": now, "updated_at": now}
    await save_lumen(frm)
    await save_lumen(to)
    return {"status": "pending_out"}


async def accept(user_id: str, peer_id: str) -> dict:
    """Accept a pending incoming request. Consent lives with the addressee."""
    me = await get_lumen(user_id)
    peer = await get_lumen(peer_id)
    if not me or not peer:
        raise ConnectionError("lumen not found")
    entry = _conns(me).get(peer_id)
    if not entry or entry.get("status") not in {"pending_in", "pending_out"}:
        raise ConnectionError("no pending request to accept")
    now = _now()
    _conns(me)[peer_id] = {**_peer_view(peer), "status": "accepted",
                           "created_at": entry.get("created_at", now), "updated_at": now}
    pentry = _conns(peer).get(user_id, {})
    _conns(peer)[user_id] = {**_peer_view(me), "status": "accepted",
                             "created_at": pentry.get("created_at", now), "updated_at": now}
    await save_lumen(me)
    await save_lumen(peer)
    return {"status": "accepted"}


async def _set_one_sided(user_id: str, peer_id: str, status: str) -> dict:
    """Reject/block: update the actor's side; demote the peer's side to pending/removed."""
    me = await get_lumen(user_id)
    peer = await get_lumen(peer_id)
    if not me or not peer:
        raise ConnectionError("lumen not found")
    now = _now()
    _conns(me)[peer_id] = {**_peer_view(peer), "status": status,
                           "created_at": _conns(me).get(peer_id, {}).get("created_at", now),
                           "updated_at": now}
    # The peer no longer has an accepted/pending link to us.
    if user_id in _conns(peer):
        _conns(peer)[user_id]["status"] = "rejected"
        _conns(peer)[user_id]["updated_at"] = now
    await save_lumen(me)
    await save_lumen(peer)
    return {"status": status}


async def reject(user_id: str, peer_id: str) -> dict:
    return await _set_one_sided(user_id, peer_id, "rejected")


async def block(user_id: str, peer_id: str) -> dict:
    return await _set_one_sided(user_id, peer_id, "blocked")
