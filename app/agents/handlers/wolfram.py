"""Wolfram Alpha agent — math / physics / unit-conversion computational queries.

Class-based: `WolframAgent` holds the logic as `self` methods and a single
instance is registered on the shared registry. `_handle_wolfram` is kept as a thin
alias to the instance method so existing callers keep working.
"""

from __future__ import annotations

from app.agents.base import BaseAgent, registry
from app.agents.intents import Intent


class WolframAgent(BaseAgent):
    name = "wolfram"
    intents = (Intent.WOLFRAM,)
    description = "Wolfram Alpha computational queries"
    # Offline keyword fallback owned by this agent (the LLM router is primary).
    KEYWORDS = (
        "wolfram", "wolfram alpha",
        "integrate ", "differentiate ", "derivative of", "integral of",
        "solve for", "solve x", "solve the equation",
        "convert ", "what is the value of",
        "boiling point", "melting point", "density of",
        "step by step",
    )

    async def handle(self, user_id: str, message: str) -> dict:
        """Ask Wolfram Alpha — Full Results API (with Short Answers fast-path)."""
        from app.agents.wolfram_agent import ask

        msg = (message or "").strip()
        if not msg:
            return {
                "reply": "📐 Ask me a math, physics, or unit-conversion question. E.g. *integrate sin x dx* or *5 light years in km*.",
                "action": "inline_answer",
                "intent": Intent.WOLFRAM,
                "agent_id": "wolfram",
            }

        # Detect "step by step" / "show working" preference
        want_steps = bool(__import__("re").search(
            r"\b(step\s+by\s+step|show\s+(?:working|steps)|with\s+steps)\b", msg.lower()
        ))

        # Strip routing keywords from the question we send to Wolfram
        cleaned = msg
        for kw in ("wolfram alpha", "wolfram", "ask wolfram"):
            if cleaned.lower().startswith(kw):
                cleaned = cleaned[len(kw):].strip(" :,")
                break

        out = await ask(cleaned, want_steps=want_steps)
        answer = out.get("answer", "(no answer)")
        interpreted = out.get("interpreted", "")
        image_url = out.get("image_url")

        parts = [f"📐 **{answer}**"]
        if interpreted and interpreted.lower() != cleaned.lower():
            parts.append(f"\n_Interpreted as: {interpreted}_")
        if image_url:
            parts.append(f"\n![]({image_url})")

        return {
            "reply": "\n".join(parts),
            "action": "inline_answer",
            "intent": Intent.WOLFRAM,
            "agent_id": "wolfram",
        }

    async def broker(self, env: dict) -> dict:
        return await self.handle(env["user_id"], env["message"])


agent = WolframAgent()
registry.register_agent(agent)

# Back-compat alias for `from app.agents.interaction_manager import _handle_wolfram`.
_handle_wolfram = agent.handle
