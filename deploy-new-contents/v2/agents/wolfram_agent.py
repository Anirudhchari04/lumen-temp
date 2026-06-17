"""AutoGen wrapper for the v1 Wolfram Alpha specialist.

Reuses app.agents.interaction_manager._handle_wolfram AS-IS.
"""

from __future__ import annotations

from autogen_agentchat.agents import AssistantAgent

from app.agents.interaction_manager import _handle_wolfram
from v2.agents.base import make_agent, reply_text

NAME = "wolfram"


def build(user_id: str, user_info: dict, model_client) -> AssistantAgent:
    async def manage_wolfram(task: str) -> str:
        """Answer math / physics / computational questions and unit, currency, or
        date conversions via Wolfram Alpha, with step-by-step where asked. Pass
        the request as `task` — e.g. 'integrate sin(x) dx'."""
        return reply_text(await _handle_wolfram(user_id, task))

    return make_agent(
        name=NAME,
        description="Computational specialist: math/physics, unit & currency conversions, step-by-step solutions (Wolfram Alpha).",
        instructions=(
            "You are Lumen's computational specialist. For math/physics/conversion "
            "questions, call manage_wolfram once with the request as `task`, then "
            "report its result."
        ),
        tools=[manage_wolfram],
        model_client=model_client,
    )
