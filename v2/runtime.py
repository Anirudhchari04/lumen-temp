"""AutoGen runtime initialization + specialist agent registration for Lumen v2.

`build_team` assembles all v1-backed specialist agents and registers them in a
MagenticOneGroupChat. That group chat owns autogen's SingleThreadedAgentRuntime
and the MagenticOne planner — i.e. it IS the inter-agent runtime that replaces
v1's custom A2A HTTP protocol for v2 turns.
"""

from __future__ import annotations

import logging

from autogen_agentchat.agents import AssistantAgent
from autogen_agentchat.teams import MagenticOneGroupChat

from v2 import config
from v2.agents import registry

logger = logging.getLogger("lumen.v2.runtime")


def build_specialist_agents(user_id: str, user_info: dict, model_client,
                            graph_token: str | None = None) -> list[AssistantAgent]:
    """Instantiate every specialist agent bound to the current user.

    The roster is declared once in v2.agents.registry.SPECIALISTS; names match
    v1 agent ids: general, communication, calendar, github, shiksha, graph,
    gmail, drive, notion, arxiv, wolfram, social.
    """
    return registry.build_all(user_id, user_info, model_client, graph_token)


def build_team(user_id: str, user_info: dict, model_client,
               graph_token: str | None = None) -> tuple[MagenticOneGroupChat, list[AssistantAgent]]:
    """Build the MagenticOne group chat (orchestrator + registered specialists)."""
    agents = build_specialist_agents(user_id, user_info, model_client, graph_token)
    team = MagenticOneGroupChat(
        participants=agents,
        model_client=model_client,
        max_turns=config.V2_MAX_TURNS,
    )
    logger.info("v2 team built: %d specialists, max_turns=%d",
                len(agents), config.V2_MAX_TURNS)
    return team, agents
