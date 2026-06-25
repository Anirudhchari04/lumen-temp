"""Shiksha routes — read-only bridge to Ekalaiva (Shiksha) backend."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query

from app.auth import get_current_user
from app.agents.shiksha_agent import (
    get_available_agents,
    get_user_progress,
    get_user_threads,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["shiksha"])


@router.get("/agents")
async def shiksha_agents(user=Depends(get_current_user)):
    """Return the list of Shiksha course agents the user is enrolled in."""
    user_id = user.get("id") or user.get("oid") or user.get("sub") or user.get("user_id", "")
    agents = await get_available_agents(user_id)
    return {"agents": agents, "total": len(agents)}


@router.get("/progress")
async def shiksha_progress(user=Depends(get_current_user)):
    """Return user's aggregated progress across Shiksha TAs."""
    user_id = user.get("id") or user.get("oid") or user.get("sub") or user.get("user_id", "")
    if not user_id:
        return {"progress": [], "error": "no user id"}
    progress = await get_user_progress(user_id)
    return {"progress": progress, "total": len(progress)}


@router.get("/threads")
async def shiksha_threads(
    agent_id: str = Query(None),
    user=Depends(get_current_user),
):
    """Return user's threads, optionally filtered by agent_id."""
    user_id = user.get("id") or user.get("oid") or user.get("sub") or user.get("user_id", "")
    if not user_id:
        return {"threads": [], "error": "no user id"}
    threads = await get_user_threads(user_id)
    if agent_id:
        threads = [t for t in threads if t.get("agentId") == agent_id]
    return {"threads": threads, "total": len(threads)}


@router.get("/debug")
async def shiksha_debug(user=Depends(get_current_user)):
    """Debug endpoint — shows resolved user_id and raw thread count."""
    user_id = user.get("id") or user.get("oid") or user.get("sub") or user.get("user_id", "")
    threads = await get_user_threads(user_id)
    return {
        "resolved_user_id": user_id,
        "jwt_keys": list(user.keys()),
        "thread_count": len(threads),
        "agent_ids": list({t.get("agentId") for t in threads if t.get("agentId")}),
    }
