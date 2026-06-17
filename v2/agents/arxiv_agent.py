"""AutoGen wrapper for the v1 arXiv research specialist.

Reuses app.agents.interaction_manager._handle_arxiv AS-IS.
"""

from __future__ import annotations

from autogen_agentchat.agents import AssistantAgent

from app.agents.interaction_manager import _handle_arxiv
from v2.agents.base import make_agent, reply_text

NAME = "arxiv"


def build(user_id: str, user_info: dict, model_client) -> AssistantAgent:
    async def manage_arxiv(task: str) -> str:
        """Search arXiv for research papers, fetch abstracts, and summarize papers.
        Pass the request as `task` — e.g. 'find recent papers on RAG'."""
        return reply_text(await _handle_arxiv(user_id, task))

    return make_agent(
        name=NAME,
        description="Research-paper specialist: search arXiv, fetch abstracts, summarize papers.",
        instructions=(
            "You are Lumen's research-paper specialist. For finding or summarizing "
            "research papers, call manage_arxiv once with the request as `task`, "
            "then report its result."
        ),
        tools=[manage_arxiv],
        model_client=model_client,
    )
