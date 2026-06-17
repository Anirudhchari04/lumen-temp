"""Lumen v2 FastAPI router — mounted at /v2 by app.main.

Endpoints:
  GET  /v2/health  — liveness + config echo (no auth)
  POST /v2/chat    — run one Magentic-One turn (same auth + request schema as v1)

The request body mirrors v1's POST /chat (app/routes/chat.py:ChatBody) so the
frontend can switch v1<->v2 with a single flag. v1 /chat is non-streaming (returns
a JSON dict), so /v2/chat is non-streaming too and returns the same-shaped dict
(reply / action / intent / agent_id / thread_id), plus v2 extras (session_id, turns).
"""

from __future__ import annotations

import logging
import traceback

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.middleware.auth import get_current_user

logger = logging.getLogger("lumen.v2.router")
router = APIRouter(tags=["lumen-v2"])

# The v2 UI is served by the React SPA at /v2 (see app/main.py SPA fallback +
# frontend route). This router only exposes the v2 API: /v2/chat and /v2/health.


class ChatBody(BaseModel):
    # Identical to v1 app/routes/chat.py:ChatBody
    message: str
    thread_id: str | None = None
    graph_token: str | None = None


@router.get("/health")
async def health():
    """200 OK with v2 config echo. Safe to call without credentials."""
    from v2 import config, ledger

    await ledger.ensure_ready()
    return {
        "status": "ok",
        "version": "2.0.0",
        "type": "lumen-v2-magentic-one",
        "orchestrator": "autogen MagenticOneGroupChat",
        "model_deployment": config.AZURE_OPENAI_DEPLOYMENT or "(unset)",
        "azure_openai_configured": bool(config.AZURE_OPENAI_ENDPOINT),
        "sessions_container": config.V2_SESSIONS_CONTAINER,
        "cosmos_ready": ledger.is_ready(),
        "specialists": [
            "general", "communication", "calendar", "portfolio", "shiksha",
            "graph", "gmail", "drive", "notion", "arxiv", "wolfram", "social",
        ],
    }


@router.post("/chat")
async def chat(body: ChatBody, current_user: dict = Depends(get_current_user)):
    """Run one Magentic-One orchestration turn for the authenticated user."""
    from v2.orchestrator import run_chat

    try:
        result = await run_chat(
            user_id=current_user["id"],
            message=body.message,
            user_info=current_user,
            graph_token=body.graph_token,
            thread_id=body.thread_id,
        )
        result["thread_id"] = body.thread_id
        return result
    except Exception as e:
        logger.error("v2 chat error: %s\n%s", e, traceback.format_exc())
        return {
            "reply": f"Error: {str(e)[:200]}",
            "action": "error",
            "intent": "v2",
            "agent_id": "magentic-one",
            "thread_id": body.thread_id or "",
        }
