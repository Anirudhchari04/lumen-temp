"""Lumen v2 specialist agents — thin autogen AssistantAgent wrappers over v1.

Each module mirrors one v1 specialist and exposes a `build(...)` factory. The
agent NAME constants match v1's agent ids for consistency.
"""

from v2.agents import (
    arxiv_agent,
    calendar_agent,
    communication_agent,
    drive_agent,
    general_agent,
    github_agent,
    gmail_agent,
    graph_agent,
    notion_agent,
    shiksha_agent,
    wolfram_agent,
)

__all__ = [
    "arxiv_agent",
    "calendar_agent",
    "communication_agent",
    "drive_agent",
    "general_agent",
    "github_agent",
    "gmail_agent",
    "graph_agent",
    "notion_agent",
    "shiksha_agent",
    "wolfram_agent",
]
