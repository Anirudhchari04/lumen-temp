"""Lumen v2 top-level orchestrator — Magentic-One planning over v1 specialists.

MagenticOneGroupChat (autogen-agentchat) is the Magentic-One orchestrator: it
instantiates autogen's MagenticOneOrchestrator internally, which maintains the
Task Ledger (facts + plan) and Progress Ledger (who-speaks-next) and drives the
registered specialist agents until the task is complete. `run_chat` runs one task,
mirrors the ledgers into Cosmos via v2.ledger, and returns a v1-shaped result dict.

Execution is sequential by design (MagenticOne picks one speaker per turn) —
matching the constraint that v2 need not parallelize agents.
"""

from __future__ import annotations

import logging

from autogen_agentchat.base import TaskResult

from v2 import config, ledger
from v2.model_client import build_model_client
from v2.runtime import build_team

logger = logging.getLogger("lumen.v2.orchestrator")


def _msg_source(msg) -> str:
    return getattr(msg, "source", "") or ""


def _msg_text(msg) -> str:
    """Best-effort text for an autogen message/event of any subtype."""
    to_text = getattr(msg, "to_text", None)
    if callable(to_text):
        try:
            return str(to_text())
        except Exception:
            pass
    content = getattr(msg, "content", "")
    return content if isinstance(content, str) else str(content)


def _final_reply(result: TaskResult) -> str:
    """The last message with usable text is MagenticOne's final answer."""
    for msg in reversed(result.messages or []):
        text = _msg_text(msg).strip()
        if text:
            return text
    return ""


async def run_chat(user_id: str, message: str, user_info: dict | None = None,
                   graph_token: str | None = None, thread_id: str | None = None) -> dict:
    """Run one v2 chat turn end to end. Returns a v1-shaped response dict."""
    user_info = user_info or {}
    await ledger.ensure_ready()

    model_client = build_model_client()
    team, agents = build_team(user_id, user_info, model_client, graph_token)

    session = await ledger.start_session(
        user_id=user_id, task=message,
        agents=[a.name for a in agents], thread_id=thread_id,
    )

    final_text = ""
    turns: list[dict] = []
    try:
        async for event in team.run_stream(task=message):
            if isinstance(event, TaskResult):
                final_text = _final_reply(event)
                continue
            entry = {
                "source": _msg_source(event),
                "kind": type(event).__name__,
                "text": _msg_text(event)[:2000],
            }
            turns.append(entry)
            await ledger.append_progress(session, entry)

        await ledger.complete_session(session, reply=final_text)
    except Exception as e:
        logger.exception("v2 orchestration failed")
        await ledger.fail_session(session, error=str(e))
        return {
            "reply": f"Lumen v2 hit an error while orchestrating: {str(e)[:300]}",
            "action": "error",
            "intent": "v2",
            "agent_id": "magentic-one",
            "thread_id": thread_id,
            "session_id": session.get("id"),
            "turns": turns,
        }
    finally:
        try:
            await model_client.close()
        except Exception:
            pass

    return {
        "reply": final_text or "(Lumen v2 completed the task but produced no final text.)",
        "action": "v2_response",
        "intent": "v2",
        "agent_id": "magentic-one",
        "thread_id": thread_id,
        "session_id": session.get("id"),
        "turns": turns,
    }
