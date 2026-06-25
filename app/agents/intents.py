"""Intent + interaction-surface constants for the Lumen agent layer.

Extracted from interaction_manager so handler modules and the dispatcher can
share these without importing the (heavier) dispatcher module — which would
create an import cycle. ``interaction_manager`` re-exports both classes for
backwards compatibility.
"""

from __future__ import annotations


class Intent:
    PROGRESS = "progress"       # "How am I doing?"
    LEARNING = "learning"       # "Teach me calculus"
    SCHEDULING = "scheduling"   # "Make me a study plan"
    META = "meta"               # "What TAs are available?"
    SOCIAL = "social"           # "Who else is learning?"
    GENERAL = "general"         # Everything else
    LAUNCH = "launch"           # explicit "open X"
    QUERY = "query"             # info-seeking about progress/events
    COMMUNICATION = "communication"  # send/check email/teams messages
    PORTFOLIO = "portfolio"     # github/portfolio file operations
    SHIKSHA = "shiksha"         # Shiksha TA queries / redirects
    OUTLOOK = "outlook"         # Outlook / email read queries via Graph
    ONEDRIVE = "onedrive"       # OneDrive file queries via Graph
    NOTION = "notion"           # Notion read/write/summarize
    DRIVE = "drive"             # Google Drive read/write/summarize (Phase 2)
    GMAIL = "gmail"             # Gmail read/write via Gmail API (Phase 2)
    ARXIV = "arxiv"             # arXiv research paper search / summarize
    WOLFRAM = "wolfram"         # Wolfram Alpha — math / computational


class InteractionType:
    """The two interaction surfaces the Interaction Manager mediates.

    The Interaction Manager sits between *humans and agents* and between
    *agents and agents*. Each has a distinct entry point:

      HUMAN_TO_AGENT  → ``dispatch()``        (a person chats with their Lumen)
      AGENT_TO_AGENT  → ``dispatch_to_ta()``  (Lumen calls a TA on the user's
                                               behalf via the A2A protocol)
    """
    HUMAN_TO_AGENT = "human_to_agent"
    AGENT_TO_AGENT = "agent_to_agent"
