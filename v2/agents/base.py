"""Shared helpers for wrapping v1 specialists as autogen AssistantAgents.

Design: each v1 specialist already owns its complete brain as an async
`_handle_*` function in app.agents.interaction_manager — it resolves OAuth
tokens, runs the v1 LLM, manages multi-turn state, calls the underlying tool
functions, and returns a {"reply": ..., "action": ..., ...} dict. v2 reuses those
AS-IS: each AssistantAgent gets one (or two) tools that are thin closures over the
matching v1 handler, bound to the current user. No v1 tool logic is duplicated.

A handler may need a user_id, a user_info dict, and/or a graph_token; the per-agent
build() functions close over whichever the v1 handler requires.
"""

from __future__ import annotations

import logging

from autogen_agentchat.agents import AssistantAgent

logger = logging.getLogger("lumen.v2.agents")


def reply_text(result) -> str:
    """Collapse a v1 handler's dict result into the text autogen passes around."""
    if isinstance(result, dict):
        text = result.get("reply") or result.get("error")
        if text:
            return str(text)
        return "(the specialist completed but returned no text)"
    return str(result)


def make_agent(*, name: str, description: str, instructions: str, tools: list,
               model_client) -> AssistantAgent:
    """Build a thin AssistantAgent. reflect_on_tool_use=False so the v1 reply text
    (already user-ready) is surfaced verbatim instead of paying for an extra LLM
    summarization pass."""
    return AssistantAgent(
        name=name,
        model_client=model_client,
        tools=tools,
        description=description,
        system_message=instructions,
        reflect_on_tool_use=False,
    )
