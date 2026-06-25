"""Keyword fallback routing — assembled from each agent's OWN declarations.

This module answers the "when do we use regex vs. semantically-closer keywords
vs. the LLM?" question (#6) and makes routing modular (#1):

ROUTING POLICY
==============
1. PRIMARY — semantic LLM router (`app.agents.llm_router.llm_classify_multi`).
   `dispatch()` sends every non-trivial message here; it understands natural
   phrasing and multi-intent requests.

2. DETERMINISTIC / STRUCTURAL regex — lives in `interaction_manager.classify_intent`.
   Regex is used ONLY where a pattern is exact and structural, never as a fuzzy
   synonym matcher: greetings, explicit "open X" launches, literal email
   addresses, arXiv-style IDs, yes/confirm replies, disconnect commands.

3. KEYWORD fallback — THIS module. A cheap substring match used as the OFFLINE
   fallback (when the LLM router is unavailable) and for the obvious rule
   fast-path. Crucially, the keyword data is NOT centralised here: each agent
   declares its own keywords as class attributes (e.g. `WolframAgent.KEYWORDS`),
   so routing knowledge lives WITH the agent. This module only references those
   attributes and preserves the precedence order the dispatcher relies on.

Adding/clarifying an agent's keywords is therefore a one-file change in that
agent's module — never an edit to the central router.
"""

from __future__ import annotations

from app.agents.handlers.arxiv import ArxivAgent
from app.agents.handlers.calendar import CalendarAgent
from app.agents.handlers.communication import CommunicationAgent
from app.agents.handlers.drive import DriveAgent
from app.agents.handlers.gmail import GmailAgent
from app.agents.handlers.lumen import GeneralAgent
from app.agents.handlers.notion import NotionAgent
from app.agents.handlers.portfolio import GitHubAgent
from app.agents.handlers.shiksha import ShikshaAgent
from app.agents.handlers.social import SocialAgent
from app.agents.handlers.wolfram import WolframAgent

# Each name aliases the owning agent's keyword tuple — the single source of truth
# is the agent class, not this module.
COMM_KW = CommunicationAgent.KEYWORDS
NOTION_KW = NotionAgent.KEYWORDS
ARXIV_KW = ArxivAgent.KEYWORDS
WOLFRAM_KW = WolframAgent.KEYWORDS
DRIVE_KW = DriveAgent.KEYWORDS
ONEDRIVE_KW = DriveAgent.ONEDRIVE_KEYWORDS
OUTLOOK_KW = GmailAgent.OUTLOOK_KEYWORDS
PORTFOLIO_KW = GitHubAgent.KEYWORDS
CAL_MANAGE_KW = CalendarAgent.MANAGE_KEYWORDS
CAL_QUERY_KW = CalendarAgent.QUERY_KEYWORDS
SCHEDULE_KW = CalendarAgent.SCHEDULE_KEYWORDS
SHIKSHA_KW = ShikshaAgent.KEYWORDS
LEARNING_QUERY_KW = ShikshaAgent.LEARNING_QUERY_KEYWORDS
LEARNING_KW = ShikshaAgent.LEARNING_KEYWORDS
PROGRESS_KW = GeneralAgent.PROGRESS_KEYWORDS
META_KW = GeneralAgent.META_KEYWORDS
SOCIAL_KW = SocialAgent.KEYWORDS


def matches(message: str, keywords) -> bool:
    """True if any keyword matches the (already lower-cased) message.

    Robustness: matching is WORD-BOUNDARY aware, not naive substring. Each
    keyword must start on a word boundary, but a trailing suffix is allowed —
    so "learn" still matches "learning"/"learned", while "start" no longer
    falsely matches inside "restart" and "mark" no longer matches "remark".
    Patterns are compiled once per keyword group and cached.
    """
    pat = _compiled_for(keywords)
    return bool(pat.search(message or ""))


# ── word-boundary compilation (cached per keyword group) ────────────────────
import re as _re

_PATTERN_CACHE: dict[int, "_re.Pattern"] = {}


def _compile(keywords) -> "_re.Pattern":
    # Longest phrases first so e.g. "create a google doc" wins over "google doc".
    parts = sorted({k.strip() for k in keywords if k and k.strip()},
                   key=len, reverse=True)
    if not parts:
        return _re.compile(r"(?!x)x")  # never matches
    # Start-anchored word boundary; suffixes allowed (no trailing \b).
    return _re.compile(r"\b(?:" + "|".join(_re.escape(p) for p in parts) + r")",
                       _re.IGNORECASE)


def _compiled_for(keywords) -> "_re.Pattern":
    key = id(keywords)  # module-level tuples are stable for the process lifetime
    pat = _PATTERN_CACHE.get(key)
    if pat is None:
        pat = _compile(keywords)
        _PATTERN_CACHE[key] = pat
    return pat
