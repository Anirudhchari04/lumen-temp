"""AutoGen wrapper for the v1 Gmail specialist.

Reuses app.agents.interaction_manager._handle_gmail AS-IS.
"""

from __future__ import annotations

from autogen_agentchat.agents import AssistantAgent

from app.agents.interaction_manager import _handle_gmail
from v2.agents.base import make_agent, reply_text

NAME = "gmail"


def build(user_id: str, user_info: dict, model_client) -> AssistantAgent:
    async def manage_gmail(task: str) -> str:
        """Read the user's Gmail via the Gmail API: list inbox, search, list sent
        mail, summarize a specific message. Pass the request as `task` — e.g.
        'summarize my inbox' or 'any mail from Bob?'."""
        return reply_text(await _handle_gmail(user_id, task))

    return make_agent(
        name=NAME,
        description="The user's Gmail reader: inbox listing, search, sent mail, and message summaries via the Gmail API.",
        instructions=(
            "You are Lumen's Gmail specialist. For reading/searching/summarizing "
            "Gmail, call manage_gmail once with the request as `task`, then report "
            "its result."
        ),
        tools=[manage_gmail],
        model_client=model_client,
    )
