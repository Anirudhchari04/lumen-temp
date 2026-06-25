"""AutoGen wrapper for the v1 general-purpose Lumen agent.

- general: app.agents.interaction_manager._handle_lumen (progress, meta, casual chat)

The Social specialist lives in its own module (v2/agents/social_agent.py).
"""

from __future__ import annotations

from autogen_agentchat.agents import AssistantAgent

from app.agents.interaction_manager import _handle_lumen
from v2.agents.base import make_agent, reply_text

NAME = "general"


def build(user_id: str, user_info: dict, model_client) -> AssistantAgent:
    async def ask_lumen(task: str) -> str:
        """General Lumen chat: learning-progress questions ('how am I doing?'),
        meta questions ('what can you do?'), and casual conversation that no other
        specialist covers. Pass the request as `task`."""
        return reply_text(await _handle_lumen(user_id, task))

    return make_agent(
        name=NAME,
        description="Lumen's general assistant: progress questions, meta/help, and anything no other specialist covers.",
        instructions=(
            "You are Lumen's general assistant — the fallback for progress questions, "
            "'what can you do', and casual chat. Call ask_lumen once with the request "
            "as `task`, then report its result."
        ),
        tools=[ask_lumen],
        model_client=model_client,
    )
