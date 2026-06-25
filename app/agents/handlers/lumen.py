"""Lumen general chat + progress agent. Class-based: `GeneralAgent` holds the logic as `self` methods (logic unchanged)."""
from __future__ import annotations

from app.agents.base import BaseAgent, registry
from app.agents.intents import Intent
from app.lumen.agent import lumen_chat


class GeneralAgent(BaseAgent):
    name = "general"
    intents = (Intent.PROGRESS, Intent.META)
    description = "Lumen general chat + progress"
    # Offline keyword fallbacks owned by this agent (the LLM router is primary).
    PROGRESS_KEYWORDS = (
        "progress", "how am i", "my status", "what have i learned",
        "my level", "threshold", "my score", "how far",
        "across courses", "where am i", "doing",
    )
    META_KEYWORDS = (
        "what tas", "which tas", "available", "what agents", "list agents",
        "what can you", "help me with",
    )

    async def handle(self, user_id: str, message: str,
                     conversation_history: list[dict] | None = None) -> dict:
        result = await lumen_chat(user_id, message, conversation_history=conversation_history)
        msg_lower = message.lower()
        is_progress = (
            "progress" in msg_lower or "how am i" in msg_lower or "how am I" in message
            or "my status" in msg_lower or "what have i learned" in msg_lower
            or "how far" in msg_lower or "where am i" in msg_lower or "doing" in msg_lower
        )
        result["intent"] = Intent.PROGRESS if is_progress else Intent.GENERAL
        result["agent_id"] = "lumen"

        if result["intent"] == Intent.PROGRESS:
            # Fetch live progress directly from Shiksha backend
            from app.agents import shiksha_agent as _shiksha
            shiksha_courses = await _shiksha.get_user_progress(user_id)
            progress_cards = []
            if shiksha_courses:
                progress_cards.append({
                    "type": "shiksha_progress",
                    "data": {
                        "progress": shiksha_courses,
                        "agents": [],
                    },
                })
            result["cards"] = progress_cards

            # Generate A2UI document for rich rendering
            a2ui_components = []
            a2ui_children = []
            for i, item in enumerate(shiksha_courses):
                cid = f"prog-{i}"
                a2ui_children.append(cid)
                a2ui_components.extend([
                    {"id": cid, "type": "Card", "props": {"variant": "outlined"}, "children": [f"{cid}-h", f"{cid}-stats"]},
                    {"id": f"{cid}-h", "type": "Heading", "props": {"text": item.get("name", item.get("agent_id", "TA")), "level": 3}},
                    {"id": f"{cid}-stats", "type": "Row", "props": {"label": "Sessions", "value": f"{item.get('thread_count', 0)} sessions"}},
                ])

            if a2ui_components:
                root_id = "progress-root"
                a2ui_components.insert(0, {"id": root_id, "type": "List", "props": {"title": "Your Progress"}, "children": a2ui_children})
                result["a2ui"] = {"surface": "chat", "root": root_id, "components": a2ui_components}

        return result

    async def broker(self, env: dict) -> dict:
        return await self.handle(env["user_id"], env["message"],
                                 conversation_history=env.get("conversation_history"))


agent = GeneralAgent()
registry.register_agent(agent)

# Back-compat alias for `from app.agents.interaction_manager import _handle_lumen`.
_handle_lumen = agent.handle
