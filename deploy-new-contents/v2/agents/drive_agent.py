"""AutoGen wrapper for the v1 Google Drive specialist.

Reuses app.agents.interaction_manager._handle_drive AS-IS.
"""

from __future__ import annotations

from autogen_agentchat.agents import AssistantAgent

from app.agents.interaction_manager import _handle_drive
from v2.agents.base import make_agent, reply_text

NAME = "drive"


def build(user_id: str, user_info: dict, model_client) -> AssistantAgent:
    async def manage_drive(task: str) -> str:
        """Work with the user's Google Drive: list/search files, read or summarize
        a Doc/Sheet/PDF, create a Google Doc. Pass the request as `task`."""
        return reply_text(await _handle_drive(user_id, task))

    return make_agent(
        name=NAME,
        description="The user's Google Drive specialist: list/search/read/summarize/create Drive files and Docs.",
        instructions=(
            "You are Lumen's Google Drive specialist. For Drive/Docs/Sheets tasks, "
            "call manage_drive once with the request as `task`, then report its "
            "result. (GitHub portfolio / TA folders are NOT Drive — leave those to "
            "the portfolio specialist.)"
        ),
        tools=[manage_drive],
        model_client=model_client,
    )
