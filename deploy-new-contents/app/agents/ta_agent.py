"""TA Agent — A2A-compatible teaching agent.

Routes to external TAs only. Internal math/cs TAs have been removed — use Shiksha TA.
"""

from __future__ import annotations

import logging
import uuid

from app.agents.interface import TARequest, TAResponse, ProgressReport

logger = logging.getLogger(__name__)


async def a2a_chat(ta_id: str, request: TARequest) -> TAResponse:
    """Route to external TA via A2A. Internal math/cs TAs have been removed — use Shiksha TA."""
    from app.orchestrator.registry import _external_agents

    agent = _external_agents.get(ta_id)
    if not agent:
        return TAResponse(
            reply=(
                f"TA '{ta_id}' is not currently available. "
                "Connect a Shiksha TA via the Shiksha section, or ask about your Shiksha progress."
            ),
        )

    # Forward to external TA's A2A endpoint
    import httpx
    endpoint = agent.get("endpoint", "")
    if not endpoint:
        return TAResponse(reply=f"No endpoint configured for '{ta_id}'.")

    body = {
        "jsonrpc": "2.0", "id": str(uuid.uuid4())[:8], "method": "tasks/send",
        "params": {
            "message": {"parts": [{"type": "text", "text": request.message}]},
            "sessionId": request.thread_id,
            "metadata": {
                "user": {
                    "id": request.student_context.user_id if request.student_context else "guest",
                    "name": request.student_context.name if request.student_context else "Student",
                }
            }
        }
    }
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(endpoint, json=body)
            result = r.json()
        reply = ""
        if "result" in result:
            for art in result["result"].get("artifacts", []):
                for part in art.get("parts", []):
                    if part.get("type") == "text":
                        reply = part["text"]
                        break
        return TAResponse(reply=reply or "No response from TA.")
    except Exception as e:
        logger.error(f"External TA {ta_id} error: {e}")
        return TAResponse(reply=f"Could not reach TA '{ta_id}': {e}")
