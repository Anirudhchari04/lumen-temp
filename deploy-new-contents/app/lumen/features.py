"""Lumen capabilities + changelog registry.

Each FEATURE is a tuple of (category, summary, date_added).
When you ship something new, add a row at the BOTTOM with today's date.
The chat handlers read this to answer "what can you do" / "what's new".
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import NamedTuple


class Feature(NamedTuple):
    category: str       # e.g. "Email", "Calendar", "Notion"
    summary: str        # one-line user-facing description
    since: str          # YYYY-MM-DD when it shipped


# Ordered chronologically. NEW FEATURES GO AT THE BOTTOM.
FEATURES: list[Feature] = [
    Feature("Learning",     "Track progress across Shiksha courses and Teaching Agents (TAs)", "2026-04-01"),
    Feature("Calendar",     "Lumen's internal calendar — study plans, holidays, reminders", "2026-04-05"),
    Feature("Portfolio",    "GitHub portfolio: list / upload / delete artifacts, list commits + repos", "2026-04-12"),
    Feature("Peers",        "See peers, message them, share progress, request info securely", "2026-04-20"),
    Feature("Comms agent",  "Send email via Outlook (Chrome extension) or IMAP/SMTP with app password", "2026-05-01"),
    Feature("Comms agent",  "Read / search Outlook inbox in chat via the Chrome extension", "2026-05-08"),
    Feature("Auth",         "Sign in with Microsoft (Entra), Google, or email + password", "2026-05-10"),
    Feature("Notion",       "Connect Notion via OAuth — search, read, summarize, create pages", "2026-05-18"),
    Feature("Notion",       "Edit Notion pages from chat — Append, Replace whole page, or pick a page in the UI", "2026-05-19"),
    Feature("Google",       "Connect Google — Gmail (read/send/summarize) + Drive (read/create/summarize) in one grant", "2026-05-18"),
    Feature("Google",       "Google Calendar full integration — list, search, create, delete events with natural-language dates ('june 12', 'tomorrow at 3pm')", "2026-05-19"),
    Feature("Profile",      "Pick your preferred calendar source — Auto / Google / Outlook / Lumen", "2026-05-20"),
    Feature("Google Docs",  "Edit Google Docs from chat or UI — Append, Replace whole doc, Find & Replace", "2026-05-20"),
    Feature("Lumen",        "Token usage tracker with source breakdown (Lumen + sub-agents) on sidebar/API", "2026-05-21"),
    Feature("Lumen",        "Contextual agent badge in chat shows which sub-agent answered (Gmail / Drive / Calendar / Notion / etc.)", "2026-05-21"),
    Feature("Peers",        "Peer messaging polish — avatars, time grouping, smarter thread layout", "2026-05-21"),
    Feature("Research",     "Search and summarize arXiv research papers from chat", "2026-05-21"),
    Feature("Research",     "Wolfram Alpha for math, physics, unit conversions, step-by-step solutions", "2026-05-21"),
    Feature("Lumen",        "Smarter orchestrator — handles multi-step requests in one turn (e.g. 'note this and remind me Monday')", "2026-05-21"),
]


def all_features() -> list[Feature]:
    return list(FEATURES)


def features_since(days: int = 14) -> list[Feature]:
    """Return features added in the last N days (uses today as the reference)."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).date()
    out = []
    for f in FEATURES:
        try:
            d = datetime.fromisoformat(f.since).date()
            if d >= cutoff:
                out.append(f)
        except Exception:
            continue
    return out


def features_by_category() -> dict[str, list[Feature]]:
    out: dict[str, list[Feature]] = {}
    for f in FEATURES:
        out.setdefault(f.category, []).append(f)
    return out


# ── Rendered chat responses ──────────────────────────────────────────────────

def render_capabilities() -> str:
    """A brief, friendly "what can you do" message. No LLM call.

    Skips internal/meta categories (Lumen, Auth, Profile) — those are plumbing,
    not user-facing capabilities. The full list still appears in `what's new`.
    """
    return (
        "Hey! I'm Lumen 👋 Here's what I can help with:\n"
        "\n"
        "📚  Track your learning progress across Shiksha courses & TAs\n"
        "📅  Calendar — add events, list, search (Google or internal)\n"
        "✉️  Email — read, send, search, and summarize (Gmail + Outlook)\n"
        "📓  Notion — search, read, create, summarize, edit pages\n"
        "📁  Google Drive — Docs, Sheets, PDFs (read / create / edit / find-replace)\n"
        "👥  Peers — message them, share progress, request info\n"
        "💼  GitHub portfolio — list, upload, delete artifacts; commits & repos\n"
        "\n"
        "Ask *what's new?* to see latest updates. So — what's up?"
    )


def render_whats_new(days: int = 5) -> str:
    """A 'what's new' changelog — abstract themes, last `days` days. No LLM.

    Groups features by user-facing category (Notion, Google, Research, etc.),
    skips internal plumbing categories, and shows ONE summary line per
    category instead of a full per-feature breakdown.
    """
    HIDDEN_CATS = {"Lumen", "Auth", "Profile"}
    recent = features_since(days=days)
    if not recent:
        return f"Nothing new in the last {days} days. Check back soon!"

    # Group by category; drop internal plumbing
    by_cat: dict[str, list[Feature]] = {}
    for f in recent:
        if f.category in HIDDEN_CATS:
            continue
        by_cat.setdefault(f.category, []).append(f)

    if not by_cat:
        return f"No user-facing updates in the last {days} days."

    # One abstract line per category — combine summaries into a brief theme
    lines = [f"✨ **Recent updates ({days} days):**\n"]
    for cat in by_cat:
        items = by_cat[cat]
        if len(items) == 1:
            lines.append(f"• **{cat}** — {items[0].summary}")
        else:
            # Abstract roll-up: take the first summary as the headline, mention "+N more"
            head = items[0].summary
            extras = len(items) - 1
            lines.append(f"• **{cat}** — {head}" + (f" *(+ {extras} more update{'s' if extras != 1 else ''})*" if extras else ""))
    return "\n".join(lines).strip()
