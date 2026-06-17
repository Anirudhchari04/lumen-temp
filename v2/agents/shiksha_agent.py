"""AutoGen wrapper for the v1 Shiksha (learning / TA progress) specialist.

Reuses app.agents.interaction_manager._handle_shiksha AS-IS.
"""

from __future__ import annotations

from autogen_agentchat.agents import AssistantAgent

from app.agents.interaction_manager import _handle_shiksha
from v2.agents.base import make_agent, reply_text

NAME = "shiksha"


def build(user_id: str, user_info: dict, model_client) -> AssistantAgent:
    async def manage_shiksha(task: str) -> str:
        """Answer questions about the user's Shiksha/Ekalaiva courses and TAs:
        which TAs they have, course progress, session history, and deep TA memory
        ('what did my blockchain TA cover?'). Pass the request as `task`."""
        return reply_text(await _handle_shiksha(user_id, task))

    return make_agent(
        name=NAME,
        description="The user's learning specialist: Shiksha courses, TA progress, and TA session memory.",
        instructions=(
            "You are Lumen's Shiksha learning specialist. For questions about the "
            "user's courses, TAs, learning progress, or past TA sessions, call "
            "manage_shiksha once with the request as `task`, then report its result."
        ),
        tools=[manage_shiksha],
        model_client=model_client,
    )
