"""AutoGen wrapper for the v1 Notion specialist.

Reuses app.agents.interaction_manager._handle_notion AS-IS.
"""

from __future__ import annotations

from autogen_agentchat.agents import AssistantAgent

from app.agents.interaction_manager import _handle_notion
from v2.agents.base import make_agent, reply_text

NAME = "notion"


def build(user_id: str, user_info: dict, model_client) -> AssistantAgent:
    async def manage_notion(task: str) -> str:
        """Read, search, create, append to, or summarize the user's Notion pages
        and notes. Pass the request as `task`."""
        return reply_text(await _handle_notion(user_id, task))

    return make_agent(
        name=NAME,
        description="The user's Notion specialist: read/search/create/append/summarize Notion pages and notes.",
        instructions=(
            "You are Lumen's Notion specialist. For Notion page/note tasks, call "
            "manage_notion once with the request as `task`, then report its result."
        ),
        tools=[manage_notion],
        model_client=model_client,
    )
