"""Google A2A protocol adapter — v1.0.0.

Implements A2A spec:
  - Agent card (GET /agents/{id}/agent-card.json and /.well-known/agent-card.json)
  - JSON-RPC 2.0 endpoint (POST /a2a/{agent_id}) with methods:
        tasks/send, tasks/get, tasks/cancel
  - Task store with state transitions.
  - Per-agent handlers: calendar, communication, portfolio, shiksha, graph.
  - External TA pass-through via HTTP A2A.

Each internal handler emits a dual-part artifact:
  1. text part — human-readable reply (A2A-spec compatible for external callers)
  2. application/json part — full structured result dict (cards, action, intent,
     proposal, a2ui, redirect_url, etc.) for internal callers to unpack.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone as _tz
UTC = _tz.utc
from typing import Any, Literal

from fastapi import APIRouter, Request

from app.orchestrator.registry import AGENT_ROUTES, get_external_by_slug, is_ta

logger = logging.getLogger(__name__)
router = APIRouter(tags=["a2a"])


TaskState = Literal["submitted", "working", "input-required", "completed",
                    "canceled", "failed"]

_tasks: dict[str, dict] = {}


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _new_task(agent_id: str, message: dict, session_id: str | None) -> dict:
    tid = str(uuid.uuid4())
    task = {
        "id": tid,
        "sessionId": session_id or str(uuid.uuid4()),
        "agentId": agent_id,
        "status": {"state": "submitted", "timestamp": _now()},
        "history": [message],
        "artifacts": [],
        "metadata": {},
        "createdAt": _now(),
    }
    _tasks[tid] = task
    return task


def _transition(task: dict, state: TaskState, message: dict | None = None,
                artifact: dict | None = None) -> dict:
    task["status"] = {"state": state, "timestamp": _now()}
    if message:
        task["history"].append(message)
    if artifact:
        task["artifacts"].append(artifact)
    return task


def _complete_with_result(task: dict, result: dict) -> dict:
    """Mark task completed with a dual-part artifact (text + application/json).

    The text part is the human-readable reply. The JSON part carries the full
    result dict so internal callers can unpack cards/action/etc.
    """
    text = (result or {}).get("reply", "")
    return _transition(task, "completed", artifact={
        "name": "reply",
        "parts": [
            {"type": "text", "text": text},
            {"type": "application/json", "data": result or {}},
        ],
    })


def _fail_with_text(task: dict, text: str) -> dict:
    return _transition(task, "failed", artifact={
        "parts": [
            {"type": "text", "text": text},
            {"type": "application/json", "data": {"reply": text, "action": "error"}},
        ],
    })


def build_a2a_card(agent_id: str, base_url: str = "") -> "AgentCard | None":
    """Get card by calling the agent module's own get_agent_card()."""
    from app.orchestrator.registry import get_agent_card
    return get_agent_card(agent_id, base_url)




class JSONRPCError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _rpc_error(req_id: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": req_id,
            "error": {"code": code, "message": message}}


def _rpc_ok(req_id: Any, result: Any) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


# -- Agent-specific handlers --------------------------------------------------

def _extract_text(params: dict) -> str:
    parts = params.get("message", {}).get("parts", [])
    return " ".join(p.get("text", "") for p in parts if p.get("type") == "text")


async def _handle_calendar(params: dict, task: dict) -> dict:
    """Calendar handler — produces full rich response (cards, a2ui, events).

    skill="calendar.get_events" → read-only query. Anything else (including
    no skill) → scheduling (which itself handles create/delete/postpone/plan).
    For external callers without a skill, fall back to a keyword heuristic.
    """
    from app.agents.interaction_manager import (
        _handle_scheduling, _handle_calendar_query,
    )
    text = _extract_text(params)
    skill = params.get("message", {}).get("skill", params.get("skill", ""))
    user_id = params.get("metadata", {}).get("user", {}).get("id", "a2a-guest")
    try:
        if skill == "calendar.get_events":
            is_query = True
        elif skill:
            is_query = False
        else:
            msg_lower = text.lower()
            is_query = any(kw in msg_lower for kw in [
                "what's on", "what is on", "my events", "my schedule",
                "upcoming", "what do i have", "show my calendar", "show calendar",
                "this week", "next week", "this month", "next month",
                "calendar events", "on my calendar",
            ])
        if is_query:
            result = await _handle_calendar_query(user_id, text)
        else:
            result = await _handle_scheduling(user_id, text)
        _complete_with_result(task, result)
    except Exception as e:
        logger.exception("calendar a2a handler failed")
        _fail_with_text(task, f"Calendar error: {e}")
    return task


async def _handle_communication_a2a(params: dict, task: dict) -> dict:
    """Communication handler — produces draft cards + inbox listings."""
    from app.agents.interaction_manager import _handle_communication
    text = _extract_text(params)
    metadata = params.get("metadata", {})
    user_info = metadata.get("user", {})
    user_id = user_info.get("id", "a2a-guest")
    try:
        result = await _handle_communication(user_id, text, user_info)
        _complete_with_result(task, result)
    except Exception as e:
        logger.exception("communication a2a handler failed")
        _fail_with_text(task, f"Communication error: {e}")
    return task


async def _handle_github_a2a(params: dict, task: dict) -> dict:
    """GitHub handler — repo exploration, file ops, portfolio artifacts, classroom."""
    from app.agents.github_agent import handle_github
    text = _extract_text(params)
    user_id = params.get("metadata", {}).get("user", {}).get("id", "a2a-guest")
    try:
        result = await handle_github(user_id, text)
        _complete_with_result(task, result)
    except Exception as e:
        logger.exception("github a2a handler failed")
        _fail_with_text(task, f"GitHub error: {e}")
    return task


async def _handle_shiksha_a2a(params: dict, task: dict) -> dict:
    """Shiksha handler — produces course lists, progress, deep memory queries."""
    from app.agents.interaction_manager import _handle_shiksha
    text = _extract_text(params)
    user_id = params.get("metadata", {}).get("user", {}).get("id", "a2a-guest")
    try:
        result = await _handle_shiksha(user_id, text)
        _complete_with_result(task, result)
    except Exception as e:
        logger.exception("shiksha a2a handler failed")
        _fail_with_text(task, f"Shiksha error: {e}")
    return task


async def _handle_graph_a2a(params: dict, task: dict) -> dict:
    """Graph handler — Outlook/OneDrive responses via Microsoft Graph.

    skill="graph.list_drive" or "graph.search_drive" → OneDrive.
    Other graph skills (or no skill) default to Outlook with a keyword fallback.
    """
    from app.agents.interaction_manager import _handle_outlook, _handle_onedrive
    text = _extract_text(params)
    skill = params.get("message", {}).get("skill", params.get("skill", ""))
    metadata = params.get("metadata", {})
    graph_token = metadata.get("graph_token")
    try:
        if skill in ("graph.list_drive", "graph.search_drive"):
            is_drive = True
        elif skill:
            is_drive = False
        else:
            msg_lower = text.lower()
            is_drive = any(kw in msg_lower for kw in [
                "onedrive", "one drive", "my drive", "my files", "my documents",
                "list files", "recent files", "shared with me", "search drive",
                "create folder", "new folder",
            ])
        if is_drive:
            result = await _handle_onedrive(text, graph_token)
        else:
            result = await _handle_outlook(text, graph_token)
        _complete_with_result(task, result)
    except Exception as e:
        logger.exception("graph a2a handler failed")
        _fail_with_text(task, f"Graph error: {e}")
    return task


async def _handle_external_ta(agent_id: str, params: dict, task: dict) -> dict:
    """Route to an externally registered agent via HTTP A2A."""
    import httpx
    agent = get_external_by_slug(agent_id)
    if not agent:
        _fail_with_text(task, f"External agent {agent_id} not found")
        return task
    endpoint = agent.get("endpoint", "")
    if not endpoint:
        _fail_with_text(task, f"No endpoint for {agent_id}")
        return task
    try:
        body = {"jsonrpc": "2.0", "id": task["id"], "method": "tasks/send", "params": params}
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(endpoint, json=body)
            result = r.json()
            reply_text = ""
            if "result" in result:
                artifacts = result["result"].get("artifacts", [])
                if artifacts:
                    for part in artifacts[0].get("parts", []):
                        if part.get("type") == "text":
                            reply_text = part["text"]
                            break
            if not reply_text:
                reply_text = str(result.get("result", "No response"))
        _complete_with_result(task, {"reply": reply_text, "action": "ta_response"})
    except Exception as e:
        _fail_with_text(task, f"External TA error: {e}")
    return task


async def _handle_tasks_send(agent_id: str, params: dict) -> dict:
    message = params.get("message")
    if not message or "parts" not in message:
        raise JSONRPCError(-32602, "message.parts required")

    session_id = params.get("sessionId")
    task = _new_task(agent_id, message, session_id)
    _transition(task, "working")

    # Route to agent-specific handler
    if agent_id == "calendar":
        return await _handle_calendar(params, task)
    elif agent_id == "communication":
        return await _handle_communication_a2a(params, task)
    elif agent_id in ("github", "portfolio"):
        return await _handle_github_a2a(params, task)
    elif agent_id == "shiksha":
        return await _handle_shiksha_a2a(params, task)
    elif agent_id == "graph":
        return await _handle_graph_a2a(params, task)
    elif get_external_by_slug(agent_id) is not None:
        return await _handle_external_ta(agent_id, params, task)
    else:
        _fail_with_text(task, f"Agent {agent_id} handler not found")
        return task


async def _handle_tasks_get(params: dict) -> dict:
    tid = params.get("id")
    if tid not in _tasks:
        raise JSONRPCError(-32001, f"Task {tid} not found")
    return _tasks[tid]


async def _handle_tasks_cancel(params: dict) -> dict:
    tid = params.get("id")
    if tid not in _tasks:
        raise JSONRPCError(-32001, f"Task {tid} not found")
    task = _tasks[tid]
    if task["status"]["state"] not in ("completed", "failed", "canceled"):
        _transition(task, "canceled")
    return task


@router.post("/a2a/{agent_id}")
async def a2a_jsonrpc(agent_id: str, body: dict):
    req_id = body.get("id")
    if body.get("jsonrpc") != "2.0":
        return _rpc_error(req_id, -32600, "jsonrpc must be '2.0'")
    method = body.get("method")
    params = body.get("params") or {}

    if agent_id not in AGENT_ROUTES and get_external_by_slug(agent_id) is None:
        return _rpc_error(req_id, -32601, f"Agent {agent_id} not found")

    try:
        if method == "tasks/send":
            result = await _handle_tasks_send(agent_id, params)
        elif method == "tasks/get":
            result = await _handle_tasks_get(params)
        elif method == "tasks/cancel":
            result = await _handle_tasks_cancel(params)
        else:
            return _rpc_error(req_id, -32601, f"Method not found: {method}")
        return _rpc_ok(req_id, result)
    except JSONRPCError as e:
        return _rpc_error(req_id, e.code, e.message)
    except Exception as e:
        logger.exception("a2a rpc failed")
        return _rpc_error(req_id, -32603, f"Internal error: {e}")
