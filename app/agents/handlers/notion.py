"""Notion agent — read/write/search/summarize. Class-based: `NotionAgent` holds the logic as `self` methods (logic unchanged)."""
from __future__ import annotations

from app.agents.base import BaseAgent, registry
from app.agents.intents import Intent


class NotionAgent(BaseAgent):
    name = "notion"
    intents = (Intent.NOTION,)
    description = "Notion read/write/search/summarize"
    # Offline keyword fallback owned by this agent (the LLM router is primary).
    KEYWORDS = (
        "notion", "in notion", "from notion", "to notion",
        "notion page", "notion pages", "notion workspace", "notion doc", "notion docs",
        "notion notes", "my notion", "search notion",
        "create a note", "make a note", "new note",
        "find my notes", "summarize my notes", "summarise my notes",
        "find my note", "summarize my note",
    )

    async def handle(self, user_id: str, message: str) -> dict:
        """Handle Notion read/write/search/summarize requests."""
        import re as _re_n
        from app.agents.notion_agent import (
            is_notion_connected, get_notion_token,
            search_notion, read_page, create_page, append_to_page, summarize_page,
            _page_summary, _page_title,
        )
        from app.lumen.core import get_lumen

        msg = (message or "").lower().strip()
        lumen = await get_lumen(user_id)

        if not is_notion_connected(lumen):
            return {
                "reply": (
                    "📓 Connect your Notion workspace first. Open your **Profile** "
                    "(top-right) and click **Connect Notion**, then try again."
                ),
                "action": "notion_not_connected",
                "intent": Intent.NOTION,
                "agent_id": "notion",
            }

        token = await get_notion_token(user_id)
        if not token:
            return {
                "reply": "⚠ Couldn't decrypt your Notion token. Disconnect and reconnect Notion in your profile.",
                "action": "inline_answer",
                "intent": Intent.NOTION,
                "agent_id": "notion",
            }

        # Disconnect
        if any(kw in msg for kw in ["disconnect notion", "remove notion", "unlink notion"]):
            from app.agents.notion_agent import disconnect_notion
            await disconnect_notion(user_id)
            return {
                "reply": "✓ Notion disconnected.",
                "action": "inline_answer",
                "intent": Intent.NOTION,
                "agent_id": "notion",
            }

        # ── EDIT / APPEND ─────────────────────────────────────────
        # Two flavors:
        #   - one-shot: "add to my notion page <name>: <content>" / "append to <name>: <content>"
        #   - two-step: "edit a notion page" → returns list with edit_mode=true → user clicks Edit
        is_edit_intent = bool(_re_n.search(
            r"\b(edit|append|update)\b.*\b(notion|note|page)\b", msg
        )) or bool(_re_n.search(r"\badd\s+(?:to|something\s+to)\b.*\b(notion|note|page)\b", msg))
        if is_edit_intent:
            # Try one-shot: "<verb> [to my] [notion] [page] <name> : <content>"
            oneshot = _re_n.search(
                r"(?:edit|append|add(?:\s+to)?|update)\s+"
                r"(?:to\s+)?(?:my\s+)?(?:notion\s+)?(?:page\s+)?"
                r"(.+?)\s*[:\-]\s*(.+)",
                msg,
            )
            if oneshot:
                page_name = oneshot.group(1).strip().strip('"\'')
                content = oneshot.group(2).strip()
                # Strip trailing "notion" / "page" cruft from the page_name capture
                page_name = _re_n.sub(r"\s*(?:in|on|to)\s+notion\s*$", "", page_name, flags=_re_n.IGNORECASE).strip()
                if page_name and content:
                    from app.agents.notion_agent import append_to_page
                    results = await search_notion(token, page_name, limit=3)
                    if not results:
                        return {
                            "reply": f"📓 Couldn't find a Notion page matching *{page_name}* to edit.",
                            "action": "inline_answer",
                            "intent": Intent.NOTION,
                            "agent_id": "notion",
                        }
                    top = results[0]
                    # Split content into lines: prefer newlines, fall back to commas / "and"
                    if "\n" in content:
                        lines = [ln.strip() for ln in content.split("\n") if ln.strip()]
                    else:
                        lines = [p.strip(" .") for p in _re_n.split(r"\s*(?:,|;|\band\b)\s*", content) if p.strip(" .")]
                    ar = await append_to_page(token, top["id"], lines)
                    if ar.get("error"):
                        return {
                            "reply": f"⚠ Couldn't append to **{_page_title(top)}**: {ar['error']}",
                            "action": "inline_answer",
                            "intent": Intent.NOTION,
                            "agent_id": "notion",
                        }
                    return {
                        "reply": (
                            f"✓ Appended {ar.get('appended', len(lines))} line(s) to "
                            f"**{_page_title(top)}**"
                            + (f" — [open]({top.get('url', '')})" if top.get("url") else "")
                        ),
                        "action": "inline_answer",
                        "intent": Intent.NOTION,
                        "agent_id": "notion",
                    }
            # Two-step path — return the pages list with edit_mode flag.
            results = await search_notion(token, "", limit=10)
            if not results:
                return {
                    "reply": "📓 No pages to edit yet — share at least one page with the Lumen integration in Notion first.",
                    "action": "inline_answer",
                    "intent": Intent.NOTION,
                    "agent_id": "notion",
                }
            summaries = [_page_summary(r) for r in results]
            return {
                "reply": "📝 Pick a page to edit — click ✏️ on any row to append content:",
                "action": "notion_edit",
                "intent": Intent.NOTION,
                "agent_id": "notion",
                "cards": [{"type": "notion_pages", "data": {"pages": summaries[:5], "edit_mode": True}}],
            }

        # CREATE: "create a note titled X with: a, b, c" / "make a new page with the topic X and content Y"
        create_match = _re_n.search(
            r"(?:create|make|add|new)\s+(?:a\s+)?(?:new\s+)?(?:note|page|notion\s+page|notion\s+doc|notion\s+note)"
            r"(?:\s+(?:titled|called|named|about|on|with\s+(?:the\s+)?(?:topic|subject|name|title))\s+)?"
            r"(.+?)"
            r"(?:\s+(?:with|containing|including)\s+(?:the\s+)?(?:content|body|text|items?)?\s*[:\-]?\s*(.+))?$",
            msg,
        )
        if create_match and ("create" in msg or "make" in msg or "new note" in msg or "new page" in msg):
            title_part = (create_match.group(1) or "").strip().strip('"\'')
            body_part = (create_match.group(2) or "").strip()
            # Strip leading "in notion" etc.
            title_part = _re_n.sub(r"^(in notion|to notion|on notion)\s+", "", title_part)
            # Post-process: if body wasn't captured but title contains " and content X" /
            # " and body X" / " and items X", split there. Catches phrasings like
            # "create a new page with the topic Lumen Test and content lumen test".
            if not body_part:
                split_match = _re_n.search(
                    r"^(.+?)\s+(?:and\s+(?:the\s+)?(?:content|body|text|items?))\s+(.+)$",
                    title_part,
                    _re_n.IGNORECASE,
                )
                if split_match:
                    title_part = split_match.group(1).strip()
                    body_part = split_match.group(2).strip()
            if not title_part:
                return {
                    "reply": "What should the page be titled? Try: *create a note titled Today's plan with: study calculus, finish lab, gym*",
                    "action": "inline_answer",
                    "intent": Intent.NOTION,
                    "agent_id": "notion",
                }
            lines = []
            if body_part:
                # Split by commas, "and", or semicolons
                for part in _re_n.split(r"\s*(?:,|;|\band\b)\s*", body_part):
                    part = part.strip().strip('.')
                    if part:
                        lines.append(part)
            result = await create_page(token, title_part.title(), lines)
            if result.get("error"):
                return {
                    "reply": f"⚠ Couldn't create the page: {result['error']}",
                    "action": "inline_answer",
                    "intent": Intent.NOTION,
                    "agent_id": "notion",
                }
            url = result.get("url", "")
            return {
                "reply": (
                    f"✓ Created **{title_part.title()}** in your Notion workspace"
                    + (f" — [open]({url})" if url else "")
                    + (f"\n\n{len(lines)} item(s) added." if lines else "")
                ),
                "action": "inline_answer",
                "intent": Intent.NOTION,
                "agent_id": "notion",
            }

        # SEARCH: "find my notion notes on X" / "search notion for X"
        search_match = _re_n.search(
            r"(?:search|find|look\s+for|show)\s+(?:my\s+)?(?:notion\s+)?(?:notes?|pages?|docs?)"
            r"(?:\s+(?:on|about|for|with|containing|regarding))?\s+(.+?)(?:\?|\.|$)",
            msg,
        )
        if search_match or "find my notes" in msg or "search notion" in msg:
            query = ""
            if search_match:
                query = (search_match.group(1) or "").strip().strip('"\'')
            # Also handle "from notion" / "in notion" prefix-strip
            query = _re_n.sub(r"^(?:in|from|on)\s+notion\s*", "", query).strip()
            results = await search_notion(token, query, limit=10)
            if not results:
                return {
                    "reply": f"🔍 No Notion pages matched *{query or 'recent'}*.",
                    "action": "inline_answer",
                    "intent": Intent.NOTION,
                    "agent_id": "notion",
                }
            summaries = [_page_summary(r) for r in results]
            lines = [f"📓 **{len(summaries)} Notion page(s){' matching *' + query + '*' if query else ''}:**\n"]
            for s in summaries[:5]:
                lines.append(f"- **{s['title']}**" + (f" — [open]({s['url']})" if s.get('url') else ""))
            return {
                "reply": "\n".join(lines),
                "action": "notion_search",
                "intent": Intent.NOTION,
                "agent_id": "notion",
                "cards": [{"type": "notion_pages", "data": summaries[:5]}],
            }

        # SUMMARIZE: "summarize my notion page on X"
        sum_match = _re_n.search(
            r"summari[sz]e\s+(?:my\s+)?(?:notion\s+)?(?:page|note|notes|doc)?"
            r"(?:\s+(?:on|about|titled|called))?\s+(.+?)(?:\?|\.|$)",
            msg,
        )
        if sum_match and ("summari" in msg):
            topic = (sum_match.group(1) or "").strip().strip('"\'')
            results = await search_notion(token, topic, limit=3)
            if not results:
                return {
                    "reply": f"📓 No Notion pages found matching *{topic}* to summarize.",
                    "action": "inline_answer",
                    "intent": Intent.NOTION,
                    "agent_id": "notion",
                }
            top = results[0]
            summary = await summarize_page(token, top["id"], f"Summarize key points relevant to: {topic}", user_id=user_id)
            return {
                "reply": (
                    f"📓 **{_page_title(top)}** — summary:\n\n{summary}\n\n"
                    f"[Open in Notion]({top.get('url', '')})"
                ),
                "action": "inline_answer",
                "intent": Intent.NOTION,
                "agent_id": "notion",
            }

        # Default: list recent pages
        results = await search_notion(token, "", limit=10)
        if not results:
            return {
                "reply": (
                    "📓 Your Notion workspace is connected but no pages are shared with the Lumen "
                    "integration yet.\n\nIn Notion: open a page → **•••** menu → **Connections** → "
                    "add **Lumen** (or whatever you named the integration). Then try again."
                ),
                "action": "inline_answer",
                "intent": Intent.NOTION,
                "agent_id": "notion",
            }
        summaries = [_page_summary(r) for r in results]
        lines = ["📓 **Your recent Notion pages:**\n"]
        for s in summaries[:5]:
            lines.append(f"- **{s['title']}**" + (f" — [open]({s['url']})" if s.get('url') else ""))
        return {
            "reply": "\n".join(lines),
            "action": "notion_search",
            "intent": Intent.NOTION,
            "agent_id": "notion",
            "cards": [{"type": "notion_pages", "data": summaries[:5]}],
        }

    async def broker(self, env: dict) -> dict:
        return await self.handle(env["user_id"], env["message"])


agent = NotionAgent()
registry.register_agent(agent)

# Back-compat alias for `from app.agents.interaction_manager import _handle_notion`.
_handle_notion = agent.handle
