"""Orchestrator — Routes messages to the right TA agent.

The Orchestrator:
1. Receives LumenRequest from the personal Lumen agent
2. Classifies intent (learning / progress / meta)
3. Detects which TA to route to
4. Sends TARequest to the TA via A2A
5. Returns OrchestratorResponse to Lumen
"""

from __future__ import annotations

import logging

from app.agents.interface import (
    LumenRequest, TARequest, StudentContext,
    OrchestratorResponse, ProgressReport,
)
from app.agents.ta_agent import a2a_chat
from app.orchestrator.registry import detect_ta, get_all_agents, get_agent_card

logger = logging.getLogger(__name__)


def _classify_intent(message: str) -> str:
    """Classify user intent. Orchestrator decides routing."""
    msg_lower = message.lower().strip()

    progress_keywords = [
        "progress", "my progress", "show progress", "how am i doing",
        "what have i learned", "what did i learn", "my level",
        "threshold concept", "what's next", "whats next",
        "recommend", "what should i", "my curriculum", "my status",
        "show my", "my score", "my mastery",
    ]
    for kw in progress_keywords:
        if kw in msg_lower:
            return "progress"

    meta_keywords = [
        "what ta", "which ta", "available ta", "list ta",
        "what agents", "who can teach", "what can you",
    ]
    for kw in meta_keywords:
        if kw in msg_lower:
            return "meta"

    return "learning"


def _format_agents_list() -> str:
    """Format available agents list."""
    agents = get_all_agents()
    lines = ["# Available Agents\n"]
    for agent in agents:
        icon = agent.get("icon", "🤖")
        name = agent.get("name", agent["id"])
        desc = agent.get("description", "")
        lines.append(f"## {icon} {name}")
        if desc:
            lines.append(desc)
        lines.append("")
    lines.append("Just ask me a question and I'll route you to the right agent!")
    return "\n".join(lines)


async def route(request: LumenRequest) -> OrchestratorResponse:
    """Main orchestrator entry point. Classifies intent and routes."""

    intent = _classify_intent(request.message)

    # Progress query — Orchestrator tells Lumen to handle from DB
    if intent == "progress":
        return OrchestratorResponse(
            reply="__PROGRESS_FROM_DB__",  # Signal to Lumen to render from its own DB
            intent="progress",
        )

    # Meta query — Orchestrator answers directly
    if intent == "meta":
        return OrchestratorResponse(
            reply=_format_agents_list(),
            intent="meta",
        )

    # Learning request — detect TA and route
    ta_id = detect_ta(request.message)
    if not ta_id:
        ta_id = "shiksha"  # Default

    card = get_agent_card(ta_id)
    ta_name = card.name if card else ta_id

    # Build TARequest
    ta_request = TARequest(
        message=request.message,
        student_context=StudentContext(
            user_id=request.user_id,
            name=request.user_name,
            progress=request.student_progress.get(ta_id, {}),
            cross_ta_progress=[
                {"ta_id": tid, **data}
                for tid, data in request.student_progress.items()
                if tid != ta_id
            ],
            tc_inventory=request.student_progress.get("_tc_inventory", {}),
        ),
        thread_id=request.thread_id,
    )

    # A2A call to TA
    ta_response = await a2a_chat(ta_id, ta_request)

    return OrchestratorResponse(
        reply=ta_response.reply,
        ta_id=ta_id,
        ta_name=ta_name,
        progress_report=ta_response.progress_report,
        routed_to=ta_id,
        intent="learning",
    )
