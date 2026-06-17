"""Lumen-to-Lumen A2A protocol.

Each Lumen is an A2A agent with skills: message, schedule_meeting, info_request, remind.
Endpoint: POST /a2a/lumen/{user_id}
Card: GET /a2a/lumen/{user_id}/agent.json
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone as _tz
UTC = _tz.utc

from fastapi import APIRouter, Request

from app.lumen.core import get_or_create_lumen, get_lumen
from app.agents.calendar_agent import schedule_event

logger = logging.getLogger(__name__)
router = APIRouter(tags=["lumen-a2a"])


# ── Agent Card ───────────────────────────────────────────────

def build_lumen_a2a_card(lumen: dict, base_url: str = "") -> dict:
    """Build an A2A v1.0.0 agent card for a Lumen instance."""
    user_id = lumen["id"]
    name = lumen.get("name", "Student")
    progress = lumen.get("curriculum_progress", {})
    tc_inv = lumen.get("tc_inventory", {})
    in_progress = tc_inv.get("in_progress", [])

    desc_parts = [f"Personal learning agent for {name}."]
    if progress:
        subject_strs = []
        for ta_id, prog in progress.items():
            level = prog.get("current_level", 1)
            module = prog.get("current_module", "")
            subject_strs.append(f"{ta_id} Level {level} ({module})" if module else f"{ta_id} Level {level}")
        desc_parts.append(f"Currently studying: {'; '.join(subject_strs)}.")
    if in_progress:
        tc_strs = [f"{tc['tc_id']} {tc.get('progress_pct', 0)}%" for tc in in_progress[:3]]
        desc_parts.append(f"TCs in progress: {', '.join(tc_strs)}.")

    return {
        "name": f"{name}'s Lumen",
        "description": " ".join(desc_parts),
        "version": "1.0.0",
        "provider": {"organization": "Lumen Network", "url": base_url},
        "supportedInterfaces": [
            {"url": f"{base_url}/a2a/lumen/{user_id}", "protocolBinding": "JSONRPC", "protocolVersion": "1.0"}
        ],
        "capabilities": {
            "streaming": False,
            "pushNotifications": False,
            "extendedAgentCard": True,
        },
        "defaultInputModes": ["text/plain"],
        "defaultOutputModes": ["text/plain"],
        "securitySchemes": {
            "lumenJwt": {"httpAuthSecurityScheme": {"scheme": "bearer", "bearerFormat": "JWT"}}
        },
        "skills": [
            {"id": "message", "name": "Send Message",
             "description": "Send a message to this Lumen's human",
             "tags": ["messaging", "peer", "a2a", "social"],
             "examples": [f"Hey {name}, want to study together?", f"Hi {name}, can I ask you about your progress?"]},
            {"id": "schedule_meeting", "name": "Propose Meeting",
             "description": "Propose a study session that gets added to this Lumen's calendar",
             "tags": ["scheduling", "meeting", "calendar", "peer"],
             "examples": ["Schedule a 30-min study session tomorrow at 3pm", "Propose a blockchain study group"]},
            {"id": "info_request", "name": "Request Profile Info",
             "description": "Request a public profile field (user approves or denies)",
             "tags": ["profile", "info", "privacy"],
             "examples": [f"What level is {name} at in their course?", "What subjects is this Lumen studying?"]},
            {"id": "remind", "name": "Send Reminder",
             "description": "Send a reminder/notification to this Lumen",
             "tags": ["reminder", "notification"],
             "examples": [f"Remind {name} about the study session tomorrow"]},
        ],
    }


# ── JSON-RPC Endpoint ───────────────────────────────────────

@router.get("/a2a/lumen/{user_id}/agent-card.json")
async def lumen_a2a_card_v2(user_id: str, request: Request):
    """A2A v1.0.0 compliant card for a Lumen instance."""
    lumen = await get_lumen(user_id)
    if not lumen:
        lumen = {"id": user_id, "name": "Student"}
    base_url = str(request.base_url).rstrip("/")
    return build_lumen_a2a_card(lumen, base_url)


@router.get("/a2a/lumen/{user_id}/agent.json")
async def lumen_a2a_card(user_id: str, request: Request):
    """Return the A2A agent card for a specific Lumen (backwards compat redirect)."""
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url=f"/a2a/lumen/{user_id}/agent-card.json", status_code=301)


@router.post("/a2a/lumen/{user_id}")
async def lumen_a2a_jsonrpc(user_id: str, body: dict):
    """Handle A2A JSON-RPC requests for a Lumen agent.

    Supports method 'tasks/send' with skill routing:
    - message: deliver a message from another Lumen
    - schedule_meeting: propose a meeting time
    - info_request: request a private profile field
    - remind: send a reminder/notification

    Sender identity comes from `params.metadata.user.{id,name}` (preferred,
    matches the Lumen self-call client) or legacy `params.{sender_id,sender_name}`.
    """
    method = body.get("method", "")
    rpc_id = body.get("id", str(uuid.uuid4())[:8])
    params = body.get("params", {})

    if method != "tasks/send":
        return _jsonrpc_error(rpc_id, -32601, f"Method not found: {method}")

    message = params.get("message", {})
    skill_id = message.get("skill", params.get("skill", "message"))
    text = ""
    for part in message.get("parts", []):
        if part.get("type") == "text":
            text = part.get("text", "")
            break
    if not text:
        text = params.get("text", "")

    metadata = params.get("metadata", {})
    sender_user = metadata.get("user", {})
    sender_id = sender_user.get("id") or params.get("sender_id", "unknown")
    sender_name = sender_user.get("name") or params.get("sender_name", "Someone")
    task_id = params.get("taskId", str(uuid.uuid4())[:8])

    lumen = await get_lumen(user_id)
    target_name = (lumen or {}).get("name", "Student")

    if skill_id == "message":
        result = await _handle_a2a_message(user_id, target_name, sender_id, sender_name, text)
    elif skill_id == "schedule_meeting":
        result = await _handle_a2a_schedule(user_id, target_name, sender_id, sender_name, text)
    elif skill_id == "info_request":
        result = await _handle_a2a_info_request(user_id, target_name, sender_id, sender_name, text)
    elif skill_id == "remind":
        result = await _handle_a2a_remind(user_id, target_name, sender_id, sender_name, text)
    else:
        return _jsonrpc_error(rpc_id, -32602, f"Unknown skill: {skill_id}")

    return {
        "jsonrpc": "2.0",
        "id": rpc_id,
        "result": {
            "id": task_id,
            "status": {"state": "completed"},
            "artifacts": [{
                "name": "reply",
                "parts": [
                    {"type": "text", "text": result.get("reply", "")},
                    {"type": "application/json", "data": result},
                ],
            }],
        },
    }


# ── Skill handlers ───────────────────────────────────────────

async def _handle_a2a_message(user_id: str, target_name: str,
                               sender_id: str, sender_name: str, text: str) -> dict:
    """Deliver a message, persist to Cosmos, and trigger an auto-reply."""
    from app.routes.lumen_social import (
        _persist_peer_message, _hydrate_peer_messages, _peer_lumen_autoreply,
        _peer_messages,
    )

    # Hydrate so the auto-reply has prior conversation context.
    await _hydrate_peer_messages(user_id)
    await _hydrate_peer_messages(sender_id)

    sender_lumen = await get_lumen(sender_id)
    sender_lumen_id = (sender_lumen or {}).get(
        "lumen_id", f"lumen://default/{sender_id}"
    )
    target_lumen = await get_lumen(user_id)
    target_lumen_id = (target_lumen or {}).get(
        "lumen_id", f"lumen://default/{user_id}"
    )

    msg = {
        "id": str(uuid.uuid4())[:8],
        "kind": "chat",
        "from_id": sender_id,
        "from_name": sender_name,
        "from_lumen_id": sender_lumen_id,
        "from_lumen": True,
        "sender_display": f"{sender_name.split(' ')[0]}'s Lumen",
        "to_id": user_id,
        "to_lumen_id": target_lumen_id,
        "to_name": target_name,
        "message": text,
        "read": False,
        "protocol": "litp/1.0",
        "created_at": datetime.now(UTC).isoformat(),
    }
    await _persist_peer_message(msg)

    # Auto-reply from the recipient's Lumen — fire-and-forget so the caller
    # gets an immediate ack. Pass conversation history for context.
    if target_lumen:
        try:
            import asyncio
            conversation = [
                m for m in _peer_messages
                if (m["from_id"] == sender_id and m["to_id"] == user_id)
                or (m["from_id"] == user_id and m["to_id"] == sender_id)
            ]
            asyncio.create_task(
                _peer_lumen_autoreply(
                    sender_id=sender_id, sender_name=sender_name,
                    peer=target_lumen, incoming_message=text,
                    conversation_history=conversation,
                )
            )
        except Exception as e:
            logger.warning(f"peer auto-reply scheduling failed: {e}")

    return {
        "reply": f"✉️ LITP → {target_name} ({target_lumen_id}): \"{text}\"",
        "action": "social",
        "intent": "social",
        "agent_id": None,
        "peer_id": user_id,
        "peer_lumen_id": target_lumen_id,
        "protocol": "litp/1.0",
        "message_id": msg["id"],
    }


async def _handle_a2a_schedule(user_id: str, target_name: str,
                                sender_id: str, sender_name: str, text: str) -> dict:
    """Create a meeting proposal as a calendar event."""
    try:
        event = await schedule_event(
            user_id=user_id,
            title=f"Meeting with {sender_name}",
            event_type="meeting",
            description=f"Proposed by {sender_name} via A2A: {text}",
        )
        return {"reply": f"Meeting proposal added to {target_name}'s calendar."}
    except Exception as e:
        logger.warning(f"A2A schedule_meeting failed: {e}")
        return {"reply": f"Could not schedule meeting: {e}"}


async def _handle_a2a_info_request(user_id: str, target_name: str,
                                    sender_id: str, sender_name: str, text: str) -> dict:
    """Create an info-request for the target user."""
    from app.routes.lumen_social import _info_requests

    req = {
        "id": str(uuid.uuid4())[:8],
        "from_id": sender_id,
        "from_name": sender_name,
        "to_id": user_id,
        "to_name": target_name,
        "field": text,
        "status": "pending",
        "protocol": "a2a/1.0",
        "created_at": datetime.now(UTC).isoformat(),
    }
    _info_requests.append(req)
    return {"reply": f"Info request sent to {target_name}. They'll be prompted to approve or deny."}


async def _handle_a2a_remind(user_id: str, target_name: str,
                              sender_id: str, sender_name: str, text: str) -> dict:
    """Add a notification/reminder for the target user."""
    from app.agents.calendar_agent import get_notifications

    # We use the calendar's notification system indirectly by scheduling a reminder event
    try:
        event = await schedule_event(
            user_id=user_id,
            title=f"Reminder from {sender_name}: {text[:80]}",
            event_type="reminder",
            description=f"Sent via A2A by {sender_name}: {text}",
            reminder_minutes_before=0,
        )
        return {"reply": f"Reminder added for {target_name}."}
    except Exception as e:
        logger.warning(f"A2A remind failed: {e}")
        return {"reply": f"Could not set reminder: {e}"}


# ── Helpers ──────────────────────────────────────────────────

def _jsonrpc_error(rpc_id: str, code: int, message: str) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": rpc_id,
        "error": {"code": code, "message": message},
    }
