"""Shiksha agent — teaching-assistant queries, progress, deep TA memory. Class-based: `ShikshaAgent` holds the logic as `self` methods (logic unchanged)."""
from __future__ import annotations

from app.agents.base import BaseAgent, registry
from app.agents.intents import Intent
from app.agents.a2a_client import a2a_tasks_send
from app.agents.handlers._common import _ensure_intent


class ShikshaAgent(BaseAgent):
    name = "shiksha"
    intents = (Intent.SHIKSHA, Intent.LEARNING)
    description = "Shiksha teaching-assistant queries"
    # Offline keyword fallbacks owned by this agent (the LLM router is primary).
    KEYWORDS = (
        "shiksha", "ekalaiva",
        "my ta", "my tas", "which ta", "which tas", "available ta", "available tas",
        "what ta", "what tas", "list ta", "list tas", "show ta", "show my ta",
        "teaching agent", "teaching agents",
        "go to shiksha", "open shiksha", "open ta", "launch shiksha",
        "continue learning", "continue my learning",
        "my progress in", "how am i doing in", "what did i learn",
        "what have i learned in", "my english ta", "english ta",
        "my course", "my courses", "shiksha progress",
        "using now", "am i using",
        "what did my", "what has my", "what topics did i", "what questions did i",
        "show me my", "show my session", "my session with", "my conversation with",
        "ta memory", "ta said", "ta told me", "ta covered", "ta session",
        "tell me about my", "summarize my", "what did i ask",
        "what did the ta", "what did ta", "blockchain ta", "chemistry ta",
        "accountancy ta", "what was covered", "what have i covered",
        "memory of", "history with", "session history",
    )
    LEARNING_QUERY_KEYWORDS = (
        "what have i covered", "what should i learn", "what did i learn",
        "my progress in", "how am i doing in",
    )
    LEARNING_KEYWORDS = (
        "teach", "learn", "explain", "help me", "study", "practice",
        "understand", "start", "continue", "begin",
        "lets", "let's", "do math", "do cs", "do coding",
    )

    async def handle(self, user_id: str, message: str) -> dict:
        """Handle Shiksha queries — list courses, progress, redirect, summarize learning, deep TA memory queries."""
        import re as _re
        from app.agents.shiksha_agent import (
            get_available_agents, get_user_progress, summarize_learning,
            find_agent_by_keyword, shiksha_course_url, SHIKSHA_FRONTEND,
            get_agent_full_memory, get_all_ta_memory, format_memory_for_llm,
            _agent_id_to_name,
        )

        msg = message.lower().strip()

        # ── Sub-intent: list courses ─────────────────────────────────
        is_list = any(kw in msg for kw in [
            "available ta", "available tas", "which ta", "which tas",
            "list ta", "list tas", "what tas", "what ta", "my tas", "my ta",
            "teaching agent", "teaching agents", "my courses", "my course",
            "show ta", "show my ta", "using now", "am i using",
            "do i have", "i have",
        ])
        if is_list or msg in ("shiksha", "ekalaiva"):
            agents = await get_available_agents(user_id)
            if not agents:
                return {
                    "reply": f"You don't have any active Shiksha courses yet. [Open Shiksha]({SHIKSHA_FRONTEND}) to get started.",
                    "action": "inline_answer", "intent": Intent.SHIKSHA, "agent_id": "shiksha",
                }
            lines = ["🎓 **Your Shiksha courses:**"]
            for i, a in enumerate(agents, 1):
                lines.append(f"{i}. **{a['name']}** — [Open]({a['url']})")
            return {
                "reply": "\n".join(lines),
                "action": "shiksha_list",
                "intent": Intent.SHIKSHA,
                "agent_id": "shiksha",
                "cards": [{"type": "shiksha_agents", "data": {"agents": agents}}],
            }

        # ── Sub-intent: redirect to specific course ──────────────────
        is_redirect = any(kw in msg for kw in [
            "go to", "open", "launch", "take me to", "redirect to", "continue learning with",
        ])
        if is_redirect:
            agents = await get_available_agents(user_id)
            agent = find_agent_by_keyword(msg, agents)
            if not agent:
                return {
                    "reply": f"Opening Shiksha for you → [Go to Shiksha]({SHIKSHA_FRONTEND})\n\nYour active courses: " + (", ".join(a["name"] for a in agents) if agents else "none yet"),
                    "action": "shiksha_redirect",
                    "intent": Intent.SHIKSHA,
                    "agent_id": "shiksha",
                    "redirect_url": SHIKSHA_FRONTEND,
                }
            return {
                "reply": f"Opening **{agent['name']}** on Shiksha → [Go to course]({agent['url']})",
                "action": "shiksha_redirect",
                "intent": Intent.SHIKSHA,
                "agent_id": "shiksha",
                "redirect_url": agent["url"],
            }

        # ── Sub-intent: progress ─────────────────────────────────────
        is_progress = any(kw in msg for kw in [
            "my progress", "how am i doing", "progress in", "shiksha progress",
        ])
        if is_progress:
            progress = await get_user_progress(user_id)
            if not progress:
                return {
                    "reply": f"You haven't started any Shiksha courses yet. [Open Shiksha]({SHIKSHA_FRONTEND}) to get started.",
                    "action": "inline_answer", "intent": Intent.SHIKSHA, "agent_id": "shiksha",
                }
            lines = ["📊 **Your Shiksha Progress:**"]
            for p in progress:
                last = p["last_active"][:10] if p["last_active"] else "—"
                lines.append(f"• **{p['name']}** — {p['thread_count']} session(s), last active {last}")
            lines.append(f"\n[Continue on Shiksha]({SHIKSHA_FRONTEND})")
            return {
                "reply": "\n".join(lines),
                "action": "shiksha_progress",
                "intent": Intent.SHIKSHA,
                "agent_id": "shiksha",
                "cards": [{"type": "shiksha_progress", "data": {"progress": progress}}],
            }

        # ── Sub-intent: deep TA memory / arbitrary natural-language query ────
        # Catches: "what did my blockchain ta say about hashing",
        #          "what topics have I covered in chemistry",
        #          "what did I ask the accountancy ta", "show my session history", etc.
        deep_query_kw = [
            "what did my", "what has my", "what topics did i", "what questions did i",
            "show me my", "show my session", "my session with", "my conversation with",
            "ta memory", "ta said", "ta told me", "ta covered", "ta session",
            "tell me about my", "what did i ask", "what did the ta", "what did ta",
            "what was covered", "what have i covered", "memory of", "history with",
            "session history",
        ]
        is_deep = any(kw in msg for kw in deep_query_kw)

        # Also catch "what did i learn" patterns (if not already handled above)
        is_learn_query = any(kw in msg for kw in [
            "what did i learn", "what have i learned", "summarize my learning",
            "what topics", "what did i study",
        ])

        if is_deep or is_learn_query:
            agents = await get_available_agents(user_id)
            if not agents:
                return {
                    "reply": "I couldn't find any Shiksha courses yet. Start a session on Shiksha and I'll be able to answer questions about it.",
                    "action": "inline_answer", "intent": Intent.SHIKSHA, "agent_id": "shiksha",
                }

            # Try to identify which TA the user is asking about
            agent = find_agent_by_keyword(msg, agents)

            if agent:
                # Fetch memory for the specific TA
                memory = await get_agent_full_memory(user_id, agent["agent_id"], max_threads=5, messages_per_thread=40)
                memory_text = format_memory_for_llm(memory, agent["name"])
                ta_context = agent["name"]
            else:
                # Fetch memory for all TAs (limited)
                all_memory = await get_all_ta_memory(user_id, max_threads_per_agent=2, messages_per_thread=15)
                parts = []
                for aid, msgs in all_memory.items():
                    name = _agent_id_to_name(aid)
                    parts.append(format_memory_for_llm(msgs, name, max_chars=2000))
                memory_text = "\n\n".join(parts) if parts else ""
                ta_context = "your Shiksha TAs"

            if not memory_text or "No conversation history" in memory_text and len(agents) == len([m for m in [memory_text] if "No conversation" in m]):
                return {
                    "reply": f"I couldn't find any conversation history with {ta_context} yet. Start a session on Shiksha first.",
                    "action": "inline_answer", "intent": Intent.SHIKSHA, "agent_id": "shiksha",
                }

            from app.lumen.agent import lumen_chat
            query_prompt = (
                f"The student asked: \"{message}\"\n\n"
                f"Here is their actual conversation history with {ta_context}:\n\n"
                f"{memory_text}\n\n"
                "Answer the student's question based ONLY on the conversation history above. "
                "Be specific and reference actual content from the conversations. "
                "Do NOT generate new teaching content or pretend to be the TA. "
                "You are Lumen, a learning companion summarizing what happened in their TA sessions. "
                "If the answer isn't in the history, say so honestly."
            )
            resp = await lumen_chat(user_id, query_prompt, thread_id=None)
            answer = resp.get("reply", "I couldn't find a specific answer in your TA history.")
            return {
                "reply": answer,
                "action": "inline_answer",
                "intent": Intent.SHIKSHA,
                "agent_id": agent["agent_id"] if agent else "shiksha",
            }

        # ── Default: show progress ────────────────────────────────────
        progress = await get_user_progress(user_id)
        agents   = await get_available_agents(user_id)
        if progress:
            lines = [f"🎓 You have **{len(progress)} active Shiksha course(s)**:"]
            for p in progress:
                last = p["last_active"][:10] if p["last_active"] else "—"
                lines.append(f"• **{p['name']}** — last active {last} — [Continue]({p['continue_url']})")
            return {
                "reply": "\n".join(lines),
                "action": "shiksha_progress",
                "intent": Intent.SHIKSHA,
                "agent_id": "shiksha",
                "cards": [{"type": "shiksha_progress", "data": {"progress": progress, "agents": agents}}],
            }
        return {
            "reply": f"You don't have any active Shiksha courses yet. [Open Shiksha]({SHIKSHA_FRONTEND}) to get started.",
            "action": "inline_answer", "intent": Intent.SHIKSHA, "agent_id": "shiksha",
        }

    async def broker(self, env: dict) -> dict:
        return _ensure_intent(
            await a2a_tasks_send("/a2a/shiksha", env["message"], env["user_id"],
                                 env.get("user_name", "")),
            Intent.SHIKSHA, "shiksha",
        )


agent = ShikshaAgent()
registry.register_agent(agent)

# Back-compat alias for `from app.agents.interaction_manager import _handle_shiksha`.
_handle_shiksha = agent.handle
