"""AutoGen wrapper for the v1 Calendar specialist.

Reuses _handle_calendar_query (read) and _handle_scheduling (create/cancel/plan).
"""

from __future__ import annotations

from autogen_agentchat.agents import AssistantAgent

from app.agents.interaction_manager import _handle_calendar_query, _handle_scheduling
from v2.agents.base import make_agent, reply_text

NAME = "calendar"


def build(user_id: str, user_info: dict, model_client) -> AssistantAgent:
    async def view_calendar(task: str) -> str:
        """Read/query the user's calendar — list upcoming events, what's on a day,
        this week/month, etc. Pass the request as `task`."""
        return reply_text(await _handle_calendar_query(user_id, task))

    async def change_calendar(task: str) -> str:
        """Create, schedule, reschedule, cancel, or delete events, add holidays,
        set reminders, or generate a study plan. Pass the request as `task`."""
        return reply_text(await _handle_scheduling(user_id, task))

    return make_agent(
        name=NAME,
        description="The user's calendar specialist: view events and schedule/cancel/plan them.",
        instructions=(
            "You are Lumen's Calendar specialist. Call view_calendar for read-only "
            "questions about the schedule, and change_calendar to create, move, "
            "cancel, or plan events. Call exactly one tool, then report its result."
        ),
        tools=[view_calendar, change_calendar],
        model_client=model_client,
    )
