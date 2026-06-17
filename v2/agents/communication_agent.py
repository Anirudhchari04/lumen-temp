"""AutoGen wrapper for the v1 Communication specialist.

Reuses app.agents.interaction_manager._handle_communication AS-IS.
"""

from __future__ import annotations

from autogen_agentchat.agents import AssistantAgent

from app.agents.interaction_manager import _handle_communication
from v2.agents.base import make_agent, reply_text

NAME = "communication"


def build(user_id: str, user_info: dict, model_client) -> AssistantAgent:
    async def manage_communication(task: str) -> str:
        """Compose, refine, send, check, or search the user's email (Gmail or
        Outlook). Pass the user's full request as `task` — e.g. 'email Alice the
        meeting notes' or 'summarize my inbox'."""
        return reply_text(await _handle_communication(user_id, task, user_info))

    return make_agent(
        name=NAME,
        description="The user's email specialist: compose/send/refine drafts, check inbox, search mail (Gmail + Outlook).",
        instructions=(
            "You are Lumen's Communication specialist. For any email-related task, "
            "call manage_communication exactly once with the user's request as `task`, "
            "then report its result. Do not invent email content yourself."
        ),
        tools=[manage_communication],
        model_client=model_client,
    )
