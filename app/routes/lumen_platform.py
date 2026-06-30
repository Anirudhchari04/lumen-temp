"""Lumen Platform routes — economics, connections, and discovery resolver.

Surfaces the design's invariants that previously had no HTTP face:
  - Economics (§14): credit balance + append-only ledger.
  - Access control (§13): connection request / accept / reject / block.
  - Discovery (§10): name resolution returning *ranked* candidate handles
    (in-context, then verified, then deterministic) — not capability search.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.auth import get_current_user
from app.lumen.core import get_lumen, get_lumen_by_username, get_all_lumens, build_share_url
from app.lumen import credits as credits_mod
from app.lumen import connections as conn_mod

logger = logging.getLogger(__name__)
router = APIRouter(tags=["lumen-platform"])


# ── Economics / credits ──────────────────────────────────────

@router.get("/credits")
async def my_credits(current_user: dict = Depends(get_current_user)):
    """This account's credit balance plus the most recent ledger entries."""
    uid = current_user["id"]
    return {
        "balance": await credits_mod.get_balance(uid),
        "currency": "usd-credits",
        "enforced": credits_mod._enforce(),
        "ledger": await credits_mod.get_ledger(uid, limit=20),
    }


@router.get("/credits/ledger")
async def my_ledger(limit: int = Query(50, ge=1, le=200),
                    current_user: dict = Depends(get_current_user)):
    """Append-only credit ledger (most recent first)."""
    return {"ledger": await credits_mod.get_ledger(current_user["id"], limit=limit)}


# ── Access control / connections ─────────────────────────────

class ConnectionTarget(BaseModel):
    peer_id: str | None = None
    username: str | None = None


async def _resolve_target_id(body: ConnectionTarget) -> str:
    if body.peer_id:
        return body.peer_id
    if body.username:
        peer = await get_lumen_by_username(body.username)
        if not peer:
            raise HTTPException(status_code=404, detail="No Lumen with that username")
        return peer["id"]
    raise HTTPException(status_code=400, detail="peer_id or username required")


@router.get("/connections")
async def my_connections(current_user: dict = Depends(get_current_user)):
    return {"connections": await conn_mod.list_connections(current_user["id"])}


@router.post("/connections/request")
async def request_connection(body: ConnectionTarget,
                             current_user: dict = Depends(get_current_user)):
    target_id = await _resolve_target_id(body)
    try:
        result = await conn_mod.send_request(current_user["id"], target_id)
    except conn_mod.ConnectionError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, **result}


@router.post("/connections/accept")
async def accept_connection(body: ConnectionTarget,
                            current_user: dict = Depends(get_current_user)):
    target_id = await _resolve_target_id(body)
    try:
        result = await conn_mod.accept(current_user["id"], target_id)
    except conn_mod.ConnectionError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, **result}


@router.post("/connections/reject")
async def reject_connection(body: ConnectionTarget,
                            current_user: dict = Depends(get_current_user)):
    target_id = await _resolve_target_id(body)
    try:
        result = await conn_mod.reject(current_user["id"], target_id)
    except conn_mod.ConnectionError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, **result}


@router.post("/connections/block")
async def block_connection(body: ConnectionTarget,
                           current_user: dict = Depends(get_current_user)):
    target_id = await _resolve_target_id(body)
    try:
        result = await conn_mod.block(current_user["id"], target_id)
    except conn_mod.ConnectionError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, **result}


# ── Discovery resolver ───────────────────────────────────────

def _name_match_score(query: str, lumen: dict) -> int:
    """Higher is a better match. 0 means no match."""
    q = query.strip().lower()
    if not q:
        return 0
    name = (lumen.get("name") or "").lower()
    username = (lumen.get("username") or "").lower()
    if q == name or q == username:
        return 3
    if name.startswith(q) or username.startswith(q):
        return 2
    if q in name or q in username:
        return 1
    return 0


@router.get("/resolve")
async def resolve_name(
    name: str = Query(..., min_length=1, description="Display name or handle to resolve"),
    context_org: str = Query("", description="Caller's org, to float in-context matches"),
    limit: int = Query(10, ge=1, le=50),
):
    """Resolve a display name to *ranked* candidate handles (design §10).

    Order: in-context (same org) first, then verified, then deterministic
    (name-match strength, then username). Capability search is explicitly out
    of scope here — this is name resolution only. The resolver never silently
    guesses; it returns ranked candidates for the caller to choose from.
    """
    org = (context_org or "").strip().lower()
    candidates = []
    for lumen in await get_all_lumens():
        if not lumen.get("social", {}).get("discoverable", True):
            continue
        score = _name_match_score(name, lumen)
        if score == 0:
            continue
        candidates.append((
            1 if org and (lumen.get("org") or "").lower() == org else 0,  # in-context
            1 if lumen.get("verified") else 0,                            # verified
            score,                                                        # match strength
            lumen,
        ))

    # Sort by in-context desc, verified desc, score desc, then username asc.
    candidates.sort(key=lambda t: (-t[0], -t[1], -t[2], (t[3].get("username") or "")))

    results = []
    for in_ctx, verified, score, lumen in candidates[:limit]:
        results.append({
            "username": lumen.get("username", ""),
            "name": lumen.get("name", ""),
            "org": lumen.get("org", ""),
            "verified": bool(lumen.get("verified")),
            "in_context": bool(in_ctx),
            "share_url": build_share_url(lumen.get("username", "")),
        })
    return {"query": name, "count": len(results), "candidates": results}
