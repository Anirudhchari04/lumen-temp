"""AutoGen wrappers for the v1 general-purpose Lumen agent and the Social agent.

- general: app.agents.interaction_manager._handle_lumen (progress, meta, casual chat)
- social:  app.agents.interaction_manager._handle_social (peers, study groups, DMs)
"""

from __future__ import annotations

from autogen_agentchat.agents import AssistantAgent

from app.agents.interaction_manager import _handle_lumen, _handle_social
from v2.agents.base import make_agent, reply_text

NAME = "general"
SOCIAL_NAME = "social"


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


def build_social(user_id: str, user_info: dict, model_client) -> AssistantAgent:
    async def manage_social(task: str) -> str:
        """Peer networking: find peers / study groups, compare progress, and send
        peer (Lumen-to-Lumen) messages. Pass the request as `task`."""
        return reply_text(await _handle_social(user_id, task))

    return make_agent(
        name=SOCIAL_NAME,
        description="The user's peer-network specialist: discover peers/study groups, compare progress, send peer messages.",
        instructions=(
            "You are Lumen's Social specialist. For peer discovery, study groups, "
            "progress comparison, or peer messaging, call manage_social once with "
            "the request as `task`, then report its result."
        ),
        tools=[manage_social],
        model_client=model_client,
    )
