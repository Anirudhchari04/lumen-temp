"""Shared A2A JSON-RPC client for internal self-calls and peer-Lumen calls.

All agent-to-agent connections — internal handlers, peer Lumens, and external
TAs alike — go through this client. It POSTs a `tasks/send` JSON-RPC body to
any A2A endpoint and unpacks the response.

Internal handlers emit a dual-part artifact: a `text` part with the
human-readable reply plus an `application/json` part carrying the full
structured result dict. This client prefers the JSON part (preserving cards,
proposals, redirect_url, etc.) and falls back to text for spec-only callers.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


async def a2a_tasks_send(
    agent_path: str,
    message: str,
    user_id: str,
    user_name: str = "Student",
    user_email: str | None = None,
    skill: str | None = None,
    session_id: str | None = None,
    extra_metadata: dict | None = None,
    base_url: str | None = None,
    timeout: float = 30.0,
) -> dict:
    """POST a tasks/send JSON-RPC call to any A2A endpoint.

    Args:
      agent_path: e.g. "/a2a/calendar" or "/a2a/lumen/{peer_user_id}".
      message: free-text message body, placed in the text part.
      user_id: caller identity, written into metadata.user.id (A2A endpoints
               read user from metadata, not from a Bearer token).
      user_name: display name for the caller.
      user_email: optional caller email; placed in metadata.user.email.
      skill: optional skill id (e.g. "message", "calendar.generate_study_plan").
      session_id: optional session/conversation id.
      extra_metadata: extra fields to merge into metadata (e.g. graph_token).
      base_url: override settings.app_base_url for testing.
      timeout: HTTP timeout in seconds.

    Returns:
      The unpacked result dict — the application/json part if present, else
      {"reply": "<text>"}.
    """
    base = (base_url or settings.app_base_url or "http://localhost:8000").rstrip("/")
    endpoint = f"{base}{agent_path}"

    user_obj: dict[str, Any] = {"id": user_id, "name": user_name}
    if user_email:
        user_obj["email"] = user_email

    params: dict[str, Any] = {
        "message": {"parts": [{"type": "text", "text": message}]},
        "metadata": {
            "user": user_obj,
            **(extra_metadata or {}),
        },
    }
    if skill:
        params["skill"] = skill
        params["message"]["skill"] = skill
    if session_id:
        params["sessionId"] = session_id

    body = {
        "jsonrpc": "2.0",
        "id": str(uuid.uuid4())[:8],
        "method": "tasks/send",
        "params": params,
    }

    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(endpoint, json=body)
        r.raise_for_status()
        rpc = r.json()

    if "error" in rpc:
        raise RuntimeError(f"A2A error from {agent_path}: {rpc['error']}")

    artifacts = (rpc.get("result") or {}).get("artifacts", [])
    # Prefer the structured JSON part — it carries the full result dict.
    for art in artifacts:
        for part in art.get("parts", []):
            if part.get("type") == "application/json":
                data = part.get("data") or {}
                if isinstance(data, dict):
                    return data
    # Fall back to a text-only reply for spec-compliant external callers.
    for art in artifacts:
        for part in art.get("parts", []):
            if part.get("type") == "text":
                return {"reply": part.get("text", "")}
    return {"reply": ""}
