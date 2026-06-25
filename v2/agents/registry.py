"""Single source of truth for the Lumen v2 specialist roster.

Every specialist is declared exactly once here as (name, factory). `runtime`
builds the team from this list, `router` reports the names from it, and the smoke
test counts it — so adding/removing a specialist is a one-line change in one place
(previously the roster was duplicated across runtime.py, router.py and the test).

Each factory has a uniform signature `(user_id, user_info, model_client,
graph_token) -> AssistantAgent`; agents that don't need the Graph token simply
ignore it (see `_bind`).
"""

from __future__ import annotations

from typing import Callable

from autogen_agentchat.agents import AssistantAgent

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
    social_agent,
    wolfram_agent,
)

# A factory binds a per-user context to a ready-to-run AssistantAgent.
Factory = Callable[[str, dict, object, "str | None"], AssistantAgent]


def _bind(build, *, needs_graph: bool = False) -> Factory:
    """Adapt an agent module's `build()` to the uniform factory signature."""

    def factory(user_id: str, user_info: dict, model_client, graph_token=None):
        if needs_graph:
            return build(user_id, user_info, model_client, graph_token)
        return build(user_id, user_info, model_client)

    return factory


# Declarative roster — order is the order agents are registered with MagenticOne.
SPECIALISTS: list[tuple[str, Factory]] = [
    ("general", _bind(general_agent.build)),
    ("communication", _bind(communication_agent.build)),
    ("calendar", _bind(calendar_agent.build)),
    ("github", _bind(github_agent.build)),
    ("shiksha", _bind(shiksha_agent.build)),
    ("graph", _bind(graph_agent.build, needs_graph=True)),
    ("gmail", _bind(gmail_agent.build)),
    ("drive", _bind(drive_agent.build)),
    ("notion", _bind(notion_agent.build)),
    ("arxiv", _bind(arxiv_agent.build)),
    ("wolfram", _bind(wolfram_agent.build)),
    ("social", _bind(social_agent.build)),
]

# Derived view — never hand-maintained.
SPECIALIST_NAMES: list[str] = [name for name, _ in SPECIALISTS]


def build_all(user_id: str, user_info: dict, model_client,
              graph_token: str | None = None) -> list[AssistantAgent]:
    """Instantiate every specialist bound to the current user."""
    return [factory(user_id, user_info, model_client, graph_token)
            for _, factory in SPECIALISTS]
