"""arXiv agent — research-paper search / summarize (no auth required).

Class-based: `ArxivAgent` holds the logic as `self` methods and a single instance
is registered on the shared registry. `_handle_arxiv` is kept as a thin alias.
"""

from __future__ import annotations

from app.agents.base import BaseAgent, registry
from app.agents.intents import Intent


class ArxivAgent(BaseAgent):
    name = "arxiv"
    intents = (Intent.ARXIV,)
    description = "arXiv paper search/summarize"
    # Offline keyword fallback owned by this agent (the LLM router is primary).
    KEYWORDS = (
        "arxiv", "arxiv paper", "arxiv papers", "research paper", "research papers",
        "find papers", "find a paper", "search papers", "search for papers",
        "papers on", "papers about", "papers related to",
        "summarize the paper", "summarize this paper", "summarize the arxiv",
        "latest research", "latest paper",
    )

    async def handle(self, user_id: str, message: str) -> dict:
        """Search / summarize arXiv papers. No auth required."""
        import re as _re_a
        from app.agents.arxiv_agent import search_arxiv, summarize_paper, get_paper

        msg = (message or "").lower().strip()

        # MULTI-STEP: "find papers on X and summarize the top one" — handle inline
        # so we don't depend on the LLM router for the most common combo.
        multi_match = _re_a.search(
            r"(?:find|search|look\s+for|show\s+me)\s+(?:the\s+latest\s+|recent\s+)?(?:arxiv\s+)?(?:papers?|research)"
            r"\s+(?:on|about|for|related\s+to)\s+(.+?)\s+and\s+summari[sz]e",
            msg,
        )
        if multi_match:
            topic = multi_match.group(1).strip().strip('"\'')
            results = await search_arxiv(topic, max_results=3)
            if not results:
                return {
                    "reply": (
                        f"📄 No papers returned for *{topic}* — arXiv and Semantic Scholar may "
                        f"both be rate-limiting Lumen's shared IP. Wait ~30s and retry."
                    ),
                    "action": "inline_answer",
                    "intent": Intent.ARXIV,
                    "agent_id": "arxiv",
                }
            top = results[0]
            summary = await summarize_paper(top["id"], use_full_pdf=False, user_id=user_id)
            lines = [f"📄 Top paper on *{topic}*: **{top['title']}**"]
            authors = ", ".join(top.get("authors", [])[:3])
            if len(top.get("authors", [])) > 3:
                authors += " et al."
            if authors:
                lines.append(f"— {authors}")
            lines.append(f"\n**Summary:**\n{summary}")
            if top.get("url"):
                lines.append(f"\n[Open on arXiv]({top['url']}) · [PDF]({top.get('pdf_url', '')})")
            return {
                "reply": "\n".join(lines),
                "action": "inline_answer",
                "intent": Intent.ARXIV,
                "agent_id": "arxiv",
            }

        # SUMMARIZE a specific paper (by ID or by topic — pick top match)
        # Note: terminator is end-of-string or '?' only — NOT '.' since arXiv IDs
        # contain dots (e.g. 2406.04692) which would otherwise truncate the capture.
        sum_match = _re_a.search(
            r"summari[sz]e\s+(?:the\s+|this\s+)?(?:arxiv\s+)?paper(?:\s+(?:on|about|titled|with\s+id))?\s+(.+?)(?:\?|$)",
            msg,
        )
        if sum_match and "summari" in msg:
            target = (sum_match.group(1) or "").strip().strip('"\'')
            # If it looks like an arXiv ID (digits.digits), summarize directly
            if _re_a.match(r"^\d{4}\.\d{4,5}(v\d+)?$", target):
                summary = await summarize_paper(target, use_full_pdf=False, user_id=user_id)
                paper = await get_paper(target)
                title = paper.get("title", target) if paper else target
                url = paper.get("url", "") if paper else ""
                return {
                    "reply": f"📄 **{title}** — summary:\n\n{summary}" + (f"\n\n[Open on arXiv]({url})" if url else ""),
                    "action": "inline_answer",
                    "intent": Intent.ARXIV,
                    "agent_id": "arxiv",
                }
            # Otherwise search → summarize top hit
            results = await search_arxiv(target, max_results=3)
            if not results:
                return {
                    "reply": f"📄 No arXiv papers found matching *{target}* to summarize.",
                    "action": "inline_answer",
                    "intent": Intent.ARXIV,
                    "agent_id": "arxiv",
                }
            top = results[0]
            summary = await summarize_paper(top["id"], use_full_pdf=False, user_id=user_id)
            return {
                "reply": f"📄 **{top['title']}** — summary:\n\n{summary}\n\n[Open on arXiv]({top['url']})",
                "action": "inline_answer",
                "intent": Intent.ARXIV,
                "agent_id": "arxiv",
            }

        # SEARCH — "find papers on X" / "search arxiv for X" / "papers about X"
        # Note: '.' is NOT a terminator (arXiv IDs / decimal numbers contain dots).
        search_match = _re_a.search(
            r"(?:search|find|look\s+for|show\s+me)(?:\s+(?:the\s+latest|recent))?\s+(?:arxiv\s+)?(?:papers?|research)"
            r"(?:\s+(?:on|about|for|related\s+to|in))?\s+(.+?)(?:\?|$)",
            msg,
        )
        if not search_match:
            # Bare "papers on X"
            search_match = _re_a.search(
                r"(?:papers?|research)\s+(?:on|about|related\s+to|for)\s+(.+?)(?:\?|$)",
                msg,
            )
        if search_match:
            query = (search_match.group(1) or "").strip().strip('"\'')
            sort = "lastUpdatedDate" if ("latest" in msg or "recent" in msg) else "relevance"
            results = await search_arxiv(query, max_results=8, sort_by=sort)
            if not results:
                return {
                    "reply": (
                        f"📄 No papers returned for *{query}* — arXiv and Semantic Scholar "
                        f"may both be rate-limiting Lumen's shared IP. Wait ~30s and try again."
                    ),
                    "action": "inline_answer",
                    "intent": Intent.ARXIV,
                    "agent_id": "arxiv",
                }
            lines = [f"📄 **{len(results)} arXiv paper(s){' matching *' + query + '*' if query else ''}:**\n"]
            for p in results[:5]:
                authors = ", ".join(p.get("authors", [])[:3])
                if len(p.get("authors", [])) > 3:
                    authors += " et al."
                lines.append(f"- **{p['title']}**" + (f" — {authors}" if authors else ""))
                # Brief 1-2 sentence snippet from the abstract
                abstract = (p.get("abstract") or "").strip()
                if abstract:
                    # First ~280 chars or up to the second period, whichever comes first
                    snippet = abstract[:280]
                    # Try to cut at a sentence boundary
                    period_idx = snippet.rfind(". ")
                    if period_idx > 80:
                        snippet = snippet[:period_idx + 1]
                    if len(abstract) > len(snippet):
                        snippet = snippet.rstrip() + "…"
                    lines.append(f"  {snippet}")
                lines.append(f"  [Open]({p['url']}) · [PDF]({p['pdf_url']})")
            return {
                "reply": "\n".join(lines),
                "action": "arxiv_search",
                "intent": Intent.ARXIV,
                "agent_id": "arxiv",
                "cards": [{"type": "arxiv_papers", "data": results[:10]}],
            }

        # Default: prompt
        return {
            "reply": (
                "📄 Tell me what to search arXiv for — e.g. *find papers on RAG*, "
                "*search arxiv for diffusion models*, or *summarize the paper 2406.01234*."
            ),
            "action": "inline_answer",
            "intent": Intent.ARXIV,
            "agent_id": "arxiv",
        }

    async def broker(self, env: dict) -> dict:
        return await self.handle(env["user_id"], env["message"])


agent = ArxivAgent()
registry.register_agent(agent)

# Back-compat alias for `from app.agents.interaction_manager import _handle_arxiv`.
_handle_arxiv = agent.handle
