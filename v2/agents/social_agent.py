"""AutoGen wrapper for the v1 Social specialist (peer network).

Reuses app.agents.interaction_manager._handle_social AS-IS.
"""

from __future__ import annotations

from autogen_agentchat.agents import AssistantAgent

from app.agents.interaction_manager import _handle_social
from v2.agents.base import make_agent, reply_text

NAME = "social"


def build(user_id: str, user_info: dict, model_client) -> AssistantAgent:
    async def manage_social(task: str) -> str:
        """Peer networking: find peers / study groups, compare progress, and send
        peer (Lumen-to-Lumen) messages. Pass the request as `task`."""
        return reply_text(await _handle_social(user_id, task))

    return make_agent(
        name=NAME,
        description="The user's peer-network specialist: discover peers/study groups, compare progress, send peer messages.",
        instructions=(
            "You are Lumen's Social specialist. For peer discovery, study groups, "
            "progress comparison, or peer messaging, call manage_social once with "
            "the request as `task`, then report its result."
        ),
        tools=[manage_social],
        model_client=model_client,
    )
