"""Gmail agent — inbox/search/read/summarize via Gmail REST API. Class-based: `GmailAgent` holds the logic as `self` methods (logic unchanged)."""
from __future__ import annotations

from app.agents.base import BaseAgent, registry
from app.agents.intents import Intent
from app.agents.a2a_client import a2a_tasks_send
from app.agents.handlers._common import _ensure_intent, _google_consent_response


class GmailAgent(BaseAgent):
    name = "gmail"
    intents = (Intent.GMAIL, Intent.OUTLOOK)
    description = "Gmail / Outlook read/send via the communication agent"
    # Outlook/Graph-specific keyword fallback owned by this agent (LLM router is primary).
    OUTLOOK_KEYWORDS = (
        "high importance mail", "important mail", "high priority mail",
        "inbox rules", "my inbox rules", "outlook categories", "email categories",
        "email headers", "mail headers", "conference rooms", "list rooms",
        "email changes", "mail changes", "track email",
    )

    async def handle(self, user_id: str, message: str) -> dict:
        """Handle inbox / search / read / summarize via Gmail REST API.
        Send is handled by the existing compose_draft → EmailDraftCard flow; that card
        calls /lumen/comm/send-real which now routes Google users to gmail_agent.send_gmail.
        """
        import re as _re_g
        from app.agents.gmail_agent import (
            is_gmail_connected, get_valid_google_token, consume_once_if_needed,
            list_inbox, get_message, search_gmail, summarize_message,
        )
        from app.lumen.core import get_lumen

        msg = (message or "").lower().strip()
        lumen = await get_lumen(user_id)
        if not is_gmail_connected(lumen):
            return _google_consent_response("Gmail", message, Intent.COMMUNICATION, "communication")

        # One-time access gate (Lumen-side): a spent "Allow once" grant re-prompts.
        if await consume_once_if_needed(user_id) == "blocked":
            return _google_consent_response("Gmail", message, Intent.COMMUNICATION, "communication")

        token = await get_valid_google_token(user_id)
        if not token:
            # Access expired and no refresh token (e.g. an "Allow once" grant) — re-prompt.
            return _google_consent_response("Gmail", message, Intent.COMMUNICATION, "communication")

        # SENT MAIL — "show my sent mails", "what did i send", "sent items", "outbox".
        # Must run BEFORE the default inbox listing, otherwise "sent" queries wrongly
        # return the inbox. The guard avoids matching inbound phrasings like
        # "who sent me a mail" (that's "sent me", not "sent mails/items").
        _sent_pat = _re_g.compile(
            r"\b(my\s+sent|sent\s+(?:mails?|emails?|messages?|items?)|"
            r"what\s+(?:did|have)\s+i\s+sen[dt]|(?:emails?|mails?)\s+i\s+(?:have\s+)?sent|"
            r"outbox)\b",
            _re_g.IGNORECASE,
        )
        if _sent_pat.search(msg):
            sent_msgs = await search_gmail(token, "in:sent", limit=10)
            if not sent_msgs:
                return {
                    "reply": "📤 No sent messages in your Gmail.",
                    "action": "inline_answer",
                    "intent": Intent.COMMUNICATION,
                    "agent_id": "gmail",
                }
            lines = [f"📤 **{len(sent_msgs)} recent sent message(s):**\n"]
            for m in sent_msgs[:5]:
                lines.append(f"- **{m.get('subject', '(no subject)')}** — to {m.get('to', m.get('sender', '?'))}")
            return {
                "reply": "\n".join(lines),
                "action": "gmail_sent",
                "intent": Intent.COMMUNICATION,
                "agent_id": "gmail",
                "cards": [{"type": "gmail_inbox", "data": sent_msgs[:10]}],
            }

        # SEARCH — "find emails from X", "search my gmail for X"
        search_match = _re_g.search(
            r"(?:search|find|look\s+for|any).*?(?:emails?|mail|inbox|gmail).*?"
            r"(?:about|for|from|with|on|containing|regarding|related\s+to|to)\s+(.+?)(?:\?|\.|$)",
            msg,
        )
        if search_match:
            raw = (search_match.group(1) or "").strip().strip('"\'')
            # If user phrased it as "from X" map to Gmail's from: syntax
            from_match = _re_g.search(r"^(?:from|by)\s+(.+?)$", raw)
            gmail_q = f"from:{from_match.group(1).strip()}" if from_match else raw
            results = await search_gmail(token, gmail_q, limit=10)
            if not results:
                return {
                    "reply": f"📧 No Gmail messages matched *{raw}*.",
                    "action": "inline_answer",
                    "intent": Intent.COMMUNICATION,
                    "agent_id": "communication",
                }
            # Return both a chat-text preview and a card the user can browse
            lines = [f"📧 **{len(results)} Gmail message(s)** matching *{raw}*:\n"]
            for r in results[:5]:
                lines.append(f"- **{r.get('subject', '(no subject)')}** — from {r.get('sender', '?')}")
            return {
                "reply": "\n".join(lines),
                "action": "gmail_search",
                "intent": Intent.COMMUNICATION,
                "agent_id": "communication",
                "cards": [{"type": "gmail_inbox", "data": results[:10]}],
            }

        # SUMMARIZE LAST EMAIL FROM X — quick path
        sum_match = _re_g.search(
            r"summari[sz]e.*?(?:email|mail|message).*?from\s+(.+?)(?:\?|\.|$)",
            msg,
        )
        if sum_match:
            sender = (sum_match.group(1) or "").strip().strip('"\'')
            results = await search_gmail(token, f"from:{sender}", limit=1)
            if not results:
                return {
                    "reply": f"📧 No recent emails from *{sender}* to summarize.",
                    "action": "inline_answer",
                    "intent": Intent.COMMUNICATION,
                    "agent_id": "communication",
                }
            top = results[0]
            summary = await summarize_message(token, top["id"], "", user_id=user_id)
            return {
                "reply": f"📧 **{top.get('subject', '')}** — from {top.get('sender', '')}:\n\n{summary}",
                "action": "inline_answer",
                "intent": Intent.COMMUNICATION,
                "agent_id": "communication",
            }

        # DEFAULT: list recent inbox
        results = await list_inbox(token, "", limit=10)
        if not results:
            return {
                "reply": "📭 Your Gmail inbox looks empty.",
                "action": "inline_answer",
                "intent": Intent.COMMUNICATION,
                "agent_id": "communication",
            }
        lines = [f"📧 **{len(results)} recent Gmail message(s):**\n"]
        for r in results[:5]:
            lines.append(f"- **{r.get('subject', '(no subject)')}** — from {r.get('sender', '?')}")
        return {
            "reply": "\n".join(lines),
            "action": "gmail_inbox",
            "intent": Intent.COMMUNICATION,
            "agent_id": "gmail",
            "cards": [{"type": "gmail_inbox", "data": results[:10]}],
        }

    async def broker(self, env: dict) -> dict:
        user_info = env.get("user_info") or {}
        return _ensure_intent(
            await a2a_tasks_send("/a2a/communication", env["message"], env["user_id"],
                                 env.get("user_name", ""), user_email=user_info.get("email", "")),
            Intent.COMMUNICATION, "gmail",
        )


agent = GmailAgent()
registry.register_agent(agent)

# Back-compat alias for `from app.agents.interaction_manager import _handle_gmail`.
_handle_gmail = agent.handle
