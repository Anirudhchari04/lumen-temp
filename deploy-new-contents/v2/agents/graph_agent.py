"""AutoGen wrapper for the v1 Graph specialist (Outlook + OneDrive via MS Graph).

Reuses _handle_outlook and _handle_onedrive AS-IS. Both v1 handlers take a
graph_token (may be None — they degrade gracefully), so this agent closes over
the token passed in from the request.
"""

from __future__ import annotations

from autogen_agentchat.agents import AssistantAgent

from app.agents.interaction_manager import _handle_onedrive, _handle_outlook
from v2.agents.base import make_agent, reply_text

NAME = "graph"


def build(user_id: str, user_info: dict, model_client,
          graph_token: str | None = None) -> AssistantAgent:
    async def outlook_admin(task: str) -> str:
        """Outlook/Graph reads that have no IMAP equivalent: high-importance mail,
        inbox rules, categories, email headers, conference rooms, mail search.
        Pass the request as `task`."""
        return reply_text(await _handle_outlook(task, graph_token))

    async def onedrive_files(task: str) -> str:
        """OneDrive via Graph: list files, recent files, files shared with me,
        search OneDrive, create a folder. Pass the request as `task`."""
        return reply_text(await _handle_onedrive(task, graph_token))

    return make_agent(
        name=NAME,
        description="Microsoft Graph specialist: Outlook admin queries (rules/categories/rooms) and OneDrive files.",
        instructions=(
            "You are Lumen's Microsoft Graph specialist. Use onedrive_files for "
            "OneDrive file requests and outlook_admin for Outlook rules/categories/"
            "headers/rooms/high-importance mail. Call exactly one tool, then report "
            "its result."
        ),
        tools=[outlook_admin, onedrive_files],
        model_client=model_client,
    )
