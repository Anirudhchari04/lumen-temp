"""Interaction Manager — Central dispatcher for multi-agent routing.

Routes user messages to the correct micro-agent (Lumen, TA, Calendar, etc.)
based on intent detection. Handles cross-agent context and conversation state.
Replaces direct agent calls with a unified dispatch layer.
"""

from __future__ import annotations

import logging
from typing import Any

from app.lumen.agent import lumen_chat
from app.agents.ta_agent import a2a_chat
from app.agents.calendar_agent import (
    generate_study_plan,
    parse_and_schedule,
    schedule_event,
    get_prefs,
)
from app.agents.a2a_client import a2a_tasks_send
from app.agents.interface import TARequest, StudentContext
from app.lumen.core import get_or_create_lumen, get_lumen_state, update_progress
from app.orchestrator.registry import get_all_agents, get_agent_card, detect_ta, is_ta
from app.events.bus import publish, PROGRESS_UPDATED, SESSION_STARTED, SESSION_ENDED
from app.orchestrator import broker

logger = logging.getLogger(__name__)


# ── Intent Classification ────────────────────────────────────

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


def classify_intent(message: str) -> tuple[str, str | None]:
    """Classify user intent and optionally detect target agent.
    Returns (intent, target_agent_id_or_None)."""
    # Normalize curly quotes to straight quotes
    msg = message.lower().strip().replace("\u2018", "'").replace("\u2019", "'").replace("\u201c", '"').replace("\u201d", '"')

    # Coding TA — demo subject agent with its own in-app page. Catch it before
    # the generic launch handling so "open coding ta" / "coding ta" route here.
    # Exclude path-like inputs (e.g. "coding-ta/", "coding-ta/ in my portfolio") —
    # those are portfolio folder expansions, not launch requests.
    if "coding-ta/" not in msg and any(k in msg for k in ("coding ta", "coding-ta", "code ta",
                              "coding assistant", "coding tutor", "coding helper")):
        return Intent.LAUNCH, "coding-ta"

    # Launch intent — explicit "open X" (but only when we can identify the target)
    open_kw = ["open ", "go to ", "switch to ", "connect me to ", "take me to ", "launch "]
    if any(msg.startswith(kw) for kw in open_kw):
        # Strip the prefix to get the target
        remainder = msg
        for kw in open_kw:
            if msg.startswith(kw):
                remainder = msg[len(kw):].strip()
                break
        # "open it" / "open that" — ambiguous, don't match as LAUNCH
        if remainder in ("it", "that", "this", ""):
            pass  # fall through to other intents
        elif "calendar" in remainder:
            return Intent.LAUNCH, "calendar"
        elif any(w in remainder for w in ("github", "portfolio", "git hub")):
            return Intent.LAUNCH, "github"
        else:
            ta_id = detect_ta(remainder)
            if ta_id:
                return Intent.LAUNCH, ta_id
            # Try common aliases
            if any(w in remainder for w in ("math", "mathematics", "computer", "cs", "coding", "programming")):
                return Intent.LAUNCH, "shiksha"

    # Communication intent — send/check EMAIL specifically (not peer messages)
    comm_kw = ["send email", "send gmail", "send an email", "email to", "mail to",
               "send a mail", "message on teams", "teams message",
               "check email", "check my email", "any replies",
               "check inbox", "check my inbox", "send outlook", "notify ",
               "write an email", "compose email", "draft email",
               # connect/disconnect
               "connect my email", "connect email", "connect outlook",
               "set up email", "setup email", "set up outlook", "setup outlook",
               "link my email", "link outlook", "configure email", "add my email",
               "disconnect my email", "disconnect email", "disconnect outlook", "remove my email",
               # search/read via IMAP
               "search my email", "search my mail", "find my email", "find my mail",
               "any email from", "any mail from", "any new email", "new emails",
               "unread emails", "unread mail", "my unread", "recent emails", "recent mail",
               "emails about", "mails about", "emails containing", "mails containing",
               "search mail", "search email", "find mail", "find email",
               "email from ", "mail from ", "emails from ", "mails from "]
    if any(kw in msg for kw in comm_kw):
        return Intent.COMMUNICATION, None

    # Import re ONCE at the top of the email-detection block so all patterns can use it.
    import re as _re

    # Sent-mail listing — "my sent emails", "what did i send today", "sent items"
    _sent_pat = _re.compile(
        r"\b(my\s+sent|sent\s+(?:mails?|emails?|messages?|items?)|"
        r"what\s+(?:did|have)\s+i\s+send|emails?\s+i\s+(?:have\s+)?sent|"
        r"mails?\s+i\s+(?:have\s+)?sent|outbox)\b",
        _re.IGNORECASE,
    )
    if _sent_pat.search(msg):
        return Intent.COMMUNICATION, None

    # Typo-tolerant view-email pattern: any "show/list/check/find/get/pull/read X mail/email/inbox" phrasing.
    # Catches "show my recemt emails", "give me my mails", "fetch new email" etc.
    _view_email_pat = _re.compile(
        r"\b(show|list|check|find|get|give|fetch|read|display|pull|see|view|grab|open)\b"
        r".*?\b(mails?|emails?|inbox|messages?|gmail|outlook)\b",
        _re.IGNORECASE,
    )
    if _view_email_pat.search(msg):
        return Intent.COMMUNICATION, None

    # Inbound-email questions that don't start with a verb (keyword safety net
    # for when LLM routing is unavailable):
    #   "did rajesh send me a mail", "has alice emailed me", "any mail from bob",
    #   "did vedanth get back to me", "reply from priya", "heard from sam?"
    _inbound_email_pat = _re.compile(
        r"\b(?:did|has|have)\b.*?\b(?:send|sent|sending|e-?mail(?:ed|s)?|mail(?:ed|s)?)\b"
        r"|\b(?:any\s+)?(?:mails?|emails?|repl(?:y|ies)|response|messages?)\s+from\b"
        r"|\b(?:get|got|getting)\s+back\s+to\s+me\b"
        r"|\b(?:hear|heard)\s+(?:back\s+)?from\b",
        _re.IGNORECASE,
    )
    if _inbound_email_pat.search(msg):
        return Intent.COMMUNICATION, None

    # Catch email-driven phrasings that comm_kw misses:
    #   "<email-addr> send to him/her ..."
    #   "send to him/her ..."
    #   "reply to X about Y"
    #   "write to <name>"
    #   any sentence containing both an email address AND a send-verb
    _has_email_addr = bool(_re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", msg))
    _send_verb = any(w in msg for w in [" send ", " sent ", "reply", "write to", "write him", "write her",
                                          "mail him", "mail her", "email him", "email her",
                                          "send him", "send her", "send to", "send a follow"])
    if _send_verb and (_has_email_addr or " to " in msg or " him" in msg or " her" in msg):
        return Intent.COMMUNICATION, None

    # Peer message: "send message to X", "send a message to X", "message X", "dm X"
    peer_msg_match = _re.match(r"(?:send\s+(?:a\s+)?message\s+to|message|msg|dm)\s+\w", msg)
    if peer_msg_match:
        return Intent.SOCIAL, None

    # Notion intent — read/write/summarize Notion pages
    notion_kw = [
        "notion", "in notion", "from notion", "to notion",
        "notion page", "notion pages", "notion workspace", "notion doc", "notion docs",
        "notion notes", "my notion", "search notion",
        "create a note", "make a note", "new note",
        "find my notes", "summarize my notes", "summarise my notes",
        "find my note", "summarize my note",
    ]
    if any(kw in msg for kw in notion_kw):
        return Intent.NOTION, None

    # arXiv intent — research paper search / fetch / summarize
    arxiv_kw = [
        "arxiv", "arxiv paper", "arxiv papers", "research paper", "research papers",
        "find papers", "find a paper", "search papers", "search for papers",
        "papers on", "papers about", "papers related to",
        "summarize the paper", "summarize this paper", "summarize the arxiv",
        "latest research", "latest paper",
    ]
    if any(kw in msg for kw in arxiv_kw):
        return Intent.ARXIV, None

    # Wolfram intent — math, units, computational queries
    wolfram_kw = [
        "wolfram", "wolfram alpha",
        "integrate ", "differentiate ", "derivative of", "integral of",
        "solve for", "solve x", "solve the equation",
        "convert ", "what is the value of",
        "boiling point", "melting point", "density of",
        "step by step",
    ]
    if any(kw in msg for kw in wolfram_kw):
        return Intent.WOLFRAM, None

    # Google Drive intent — read/write/summarize Drive files (Docs, Sheets, PDFs)
    drive_kw = [
        "google drive", "my drive", "drive doc", "drive sheet", "drive file",
        "in my drive", "from my drive", "search drive", "search my drive",
        "my google doc", "my google docs", "google doc", "google docs",
        "google sheet", "google sheets", "my google sheet",
        "in drive", "from drive", "to drive",
        "my pdf", "my pdfs",  # PDFs are stored in Drive
        "create a google doc", "make a google doc", "new google doc",
    ]
    if any(kw in msg for kw in drive_kw):
        return Intent.DRIVE, None

    # OneDrive intent — file queries via Graph
    onedrive_kw = [
        "onedrive", "one drive", "my drive", "my files", "my documents",
        "list files", "list my files", "recent files", "files shared with me",
        "shared with me", "search onedrive", "search my drive", "search my files",
        "create folder", "make folder", "new folder", "files in my drive",
        "what's in my drive", "what is in my drive", "drive files", "drive folder",
    ]
    if any(kw in msg for kw in onedrive_kw):
        return Intent.ONEDRIVE, None

    # Outlook read intent — Graph-powered email queries (admin/org specific, no IMAP equivalent)
    outlook_kw = [
        "high importance mail", "important mail", "high priority mail",
        "inbox rules", "my inbox rules", "outlook categories", "email categories",
        "email headers", "mail headers", "conference rooms", "list rooms",
        "email changes", "mail changes", "track email",
    ]
    if any(kw in msg for kw in outlook_kw):
        return Intent.OUTLOOK, None

    # Portfolio / GitHub intent — must be checked before calendar (both have "remove/delete")
    portfolio_kw = [
        "from github", "from my github", "from repo", "from the repo",
        "from portfolio", "from my portfolio",
        "to github", "to my github", "to repo", "upload to github",
        "portfolio files", "my portfolio", "show portfolio",
        "list portfolio", "what's in my repo", "what is in my repo",
        "github files", "show my repo", "my github files",
        "delete from github", "remove from github",
        # General GitHub queries
        "my repo", "my repos", "my repositories", "my commits",
        "in my github", "on github", "on my github",
        "github repo", "show commits", "recent commits", "list commits",
        "what did i commit", "what's in the repo", "show files in",
        "shiksha-portfolio", "portfolio repo",
        # Staged-commit flow + GitHub Actions
        "commit staged", "commit my staged", "commit the staged", "commit changes",
        "commit them", "commit now", "discard staged", "clear staged",
        "what's staged", "whats staged", "my staged", "staged file", "staged change",
        "github action", "github actions", "workflow run", "workflow runs", "ci status",
        # Full GitHub repo exploration + classroom (handled by the GitHub agent)
        "pull request", "pull requests", "merge commit", "merge commits", "rebase",
        "rebased", "branches", "branch list", "review code", "code review",
        "classroom", "classrooms", "assignment", "assignments", "open github",
        "open the github agent", "github agent", "create repo", "create a repo",
    ]
    if any(kw in msg for kw in portfolio_kw):
        return Intent.PORTFOLIO, None

    # Portfolio TA-folders — "files in math ta folder", "cs-ta folder",
    # "my portfolio files". These are GitHub portfolio folders, NOT Google Drive.
    # Bare "math ta" (without a folder/file cue) is left for shiksha/LLM routing.
    _portfolio_folder_pat = _re.compile(
        r"\b[\w-]*ta\s+folder\b"
        r"|\b(?:files?|contents?|artifacts?|docs?|notes?|stuff)\s+(?:in|from|inside)\s+"
        r"(?:the\s+|my\s+)?[\w-]+\s+(?:folder|ta)\b"
        r"|\bmy\s+portfolio\b|\bportfolio\s+(?:files?|folder|repo|artifacts?)\b",
        _re.IGNORECASE,
    )
    if _portfolio_folder_pat.search(msg):
        return Intent.PORTFOLIO, None

    # Calendar management via Lumen — remove/postpone/cancel/delete events
    # Must be checked BEFORE calendar query detection (e.g., "remove all events today")
    cal_manage_kw = ["cancel ", "remove ", "delete ", "postpone", "reschedule",
                     "move meeting", "move event", "push back"]
    if any(kw in msg for kw in cal_manage_kw):
        return Intent.SCHEDULING, "calendar"

    # Query intent — info-seeking calendar queries
    query_kw = ["what's on", "what\u2019s on", "what is on", "my events", "my schedule",
                "what's scheduled", "what\u2019s scheduled",
                "upcoming", "what do i have", "show my calendar", "show calendar",
                "events this", "events in", "events for", "monthly schedule",
                "this month", "next month", "this week", "next week",
                "my calendar", "on my calendar", "calendar events"]
    if any(kw in msg for kw in query_kw):
        return Intent.QUERY, "calendar"
    # "today" / "tomorrow" alone as calendar queries
    if msg.strip() in ("today", "tomorrow", "what's today", "what's tomorrow"):
        return Intent.QUERY, "calendar"

    # Scheduling/event intent
    schedule_kw = ["study plan", "schedule", "plan my", "what order", "when should",
                   "routine", "plan for me", "study schedule",
                   "remind me", "set a reminder", "deadline", "exam on",
                   "add holiday", "add event", "mark as holiday"]
    if any(kw in msg for kw in schedule_kw):
        return Intent.SCHEDULING, "calendar"

    # Calendar create patterns — typo-tolerant via regex.
    # Catches "set june 12 as my birthday on my calendar", "add a meeting tomorrow",
    # "mark holi on my calendar", "block out friday", "book a slot at 3pm", etc.
    _calendar_create_pat = _re.compile(
        r"\b(set|add|mark|block|book|create|put|insert|schedule|new)\b"
        r".*?\b(calendar|event|birthday|anniversary|reminder|meeting|"
        r"appointment|slot|holiday|deadline)\b",
        _re.IGNORECASE,
    )
    if _calendar_create_pat.search(msg):
        return Intent.SCHEDULING, "calendar"
    # Plain "on my calendar" / "to my calendar" / "in my google calendar" phrasings
    if _re.search(r"\b(on|to|in|from|into)\s+(my\s+)?(google\s+)?calendar\b", msg):
        return Intent.SCHEDULING, "calendar"

    # Shiksha TA queries — checked before generic progress
    shiksha_kw = [
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
        # deep TA memory queries
        "what did my", "what has my", "what topics did i", "what questions did i",
        "show me my", "show my session", "my session with", "my conversation with",
        "ta memory", "ta said", "ta told me", "ta covered", "ta session",
        "tell me about my", "summarize my", "what did i ask",
        "what did the ta", "what did ta", "blockchain ta", "chemistry ta",
        "accountancy ta", "what was covered", "what have i covered",
        "memory of", "history with", "session history",
    ]
    if any(kw in msg for kw in shiksha_kw):
        return Intent.SHIKSHA, None

    # Progress intent
    progress_kw = ["progress", "how am i", "my status", "what have i learned",
                   "my level", "threshold", "my score", "how far",
                   "across courses", "where am i", "doing"]
    if any(kw in msg for kw in progress_kw):
        return Intent.PROGRESS, None

    # Meta intent
    meta_kw = ["what tas", "which tas", "available", "what agents", "list agents",
               "what can you", "help me with"]
    if any(kw in msg for kw in meta_kw):
        return Intent.META, None

    # Social intent
    social_kw = ["peers", "peer", "study group", "who else", "other students",
                 "compare", "collaborate", "partner",
                 "message ", "msg ", "dm ", "send message"]
    if any(kw in msg for kw in social_kw):
        return Intent.SOCIAL, None

    # Learning intent — detect which TA
    # Query detection for learning info-seeking
    learning_query_kw = ["what have i covered", "what should i learn", "what did i learn",
                         "my progress in", "how am i doing in"]
    if any(kw in msg for kw in learning_query_kw):
        ta_id = detect_ta(message)
        return Intent.QUERY, ta_id or "shiksha"

    learning_kw = ["teach", "learn", "explain", "help me", "study", "practice",
                   "understand", "start", "continue", "begin",
                   "lets", "let's", "do math", "do cs", "do coding"]
    ta_id = detect_ta(message)
    if ta_id:
        return Intent.LEARNING, ta_id
    if any(kw in msg for kw in learning_kw):
        return Intent.LEARNING, "shiksha"

    return Intent.GENERAL, None


# ── LLM-primary routing fast-path ────────────────────────────
# Routing is LLM-first: every message is classified by the LLM router so natural
# phrasings ("show my google mails", "did vedanth reply") route correctly. To
# avoid paying for a model call on trivial chatter, a few obvious cases skip the
# LLM and go straight to Lumen's general chat.

import re as _re_fastpath

_GREETING_RE = _re_fastpath.compile(
    r"^\s*(hi+|hey+|hello+|yo|sup|hiya|thanks|thank\s*you|thx|ty|ok(ay)?|cool|nice|"
    r"great|awesome|got\s*it|gotcha|bye|good\s*(morning|afternoon|evening|night))"
    r"[\s!.,?]*$",
    _re_fastpath.IGNORECASE,
)


def _is_disconnect_request(message: str) -> bool:
    """Detect 'disconnect <integration>' intents (kept separate so we always
    confirm before revoking, and never confuse it with file deletes)."""
    msg = (message or "").lower()
    has_verb = (
        any(v in msg for v in ["disconnect", "unlink", "revoke", "log out", "sign out"])
        or ("remove" in msg and any(w in msg for w in ["access", "connection", "integration"]))
        or ("delete" in msg and any(w in msg for w in ["connection", "integration"]))
    )
    if not has_verb:
        return False
    return any(s in msg for s in [
        "github", "portfolio", "notion", "google", "gmail", "drive",
        "calendar", "email", "outlook",
    ])


def _handle_disconnect(message: str) -> dict:
    """Return a confirmation card for disconnecting an integration.

    Never disconnects directly — the frontend card has a Confirm button that
    calls the disconnect API only after the user taps it.
    """
    msg = (message or "").lower()
    targets: list[tuple[str, str]] = []
    if any(k in msg for k in ["github", "portfolio"]):
        targets.append(("github", "GitHub"))
    if "notion" in msg:
        targets.append(("notion", "Notion"))
    if any(k in msg for k in ["google", "gmail", "drive", "calendar", "email", "outlook", "mail"]):
        targets.append(("google", "Gmail · Drive · Calendar"))

    if not targets:
        return {
            "reply": "Which connection do you want to disconnect — **Gmail · Drive · Calendar**, **GitHub**, or **Notion**?",
            "action": "inline_answer",
            "agent_id": "lumen",
            "intent": Intent.GENERAL,
        }

    names = " and ".join(label for _, label in targets)
    return {
        "reply": f"Disconnect **{names}**? This revokes Lumen's access until you reconnect. Tap **Disconnect** to confirm.",
        "action": "confirm_disconnect",
        "agent_id": "lumen",
        "intent": Intent.GENERAL,
        "cards": [{"type": "confirm_disconnect", "data": {"service": s, "label": label}}
                  for s, label in targets],
    }


def _should_skip_llm_routing(message: str, rule_intent: str) -> bool:
    """True for obvious messages where the LLM router adds nothing.

    Keeps greetings / progress / meta cheap, and pins unambiguous Portfolio
    (GitHub) queries so the LLM can't mistake a "math-ta folder" for Drive.
    Everything else is LLM-routed.
    """
    if rule_intent in (Intent.PROGRESS, Intent.META, Intent.PORTFOLIO):
        return True
    if _GREETING_RE.match(message or ""):
        return True
    return False


# ── Dispatch ─────────────────────────────────────────────────

async def dispatch(user_id: str, message: str, thread_id: str | None = None,
                   user_info: dict | None = None,
                   conversation_history: list[dict] | None = None,
                   graph_token: str | None = None) -> dict:
    """Central dispatch — Lumen orchestrates all routing via LLM.

    Returns a unified response dict with:
      reply, action, intent, agent_id, thread_id, redirect_url (optional)
    """
    user_info = user_info or {}
    ctx = _get_ctx(user_id)

    await get_or_create_lumen(user_id, user_info.get("name", ""), user_info.get("email", ""))

    # Best-effort graph token for email/drive sub-agents
    if graph_token is None:
        try:
            from app.agents.graph_token_manager import get_graph_access_token, get_external_graph_access_token
            graph_token = await get_external_graph_access_token() or await get_graph_access_token()
        except Exception as _e:
            logger.debug(f"Backend graph token unavailable: {_e}")

    # ── Context-based routing: if we asked the user for more info, their reply is
    #    a continuation of that conversation, not a new intent.
    awaiting = ctx.get("awaiting")

    # Disambiguate recipient: user replied with a name/email pick from a candidate list
    if awaiting == "disambiguate_recipient" and _pending_drafts.get(user_id):
        candidates = ctx.get("disambiguate_candidates", []) or []
        reply_lower = (message or "").strip().lower()
        # Escape hatch: don't trap unrelated user asks inside disambiguation mode.
        # Examples: "what can you do", "help", "cancel".
        if any(kw in reply_lower for kw in [
            "what can you do", "what do you do", "help", "features", "capabilities",
            "cancel", "never mind", "nevermind", "stop",
        ]):
            _clear_ctx(user_id)
            if any(kw in reply_lower for kw in ["cancel", "never mind", "nevermind", "stop"]):
                return {
                    "reply": "👍 Recipient selection cancelled. Your draft is still saved — say 'send to <name>' or 'send' when ready.",
                    "action": "comm_draft",
                    "intent": Intent.COMMUNICATION,
                    "agent_id": "communication",
                }
            return await _handle_lumen(user_id, message, conversation_history=conversation_history)
        chosen = None
        # 1. Direct email paste — match exact email
        import re as _re_dis
        email_in_reply = _re_dis.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", reply_lower)
        if email_in_reply:
            wanted = email_in_reply.group(0)
            for c in candidates:
                if (c.get("email") or "").lower() == wanted:
                    chosen = c
                    break
            # Even if not a known candidate, accept the email the user typed
            if not chosen:
                chosen = {"name": wanted.split("@")[0].title(), "email": wanted}
        # 2. Name match — pick candidate whose name overlaps the user's reply most
        if not chosen and candidates:
            best_score = 0
            for c in candidates:
                cname = (c.get("name") or "").lower()
                # Token-overlap score
                reply_tokens = set(t for t in _re_dis.split(r"\s+", reply_lower) if t)
                cname_tokens = set(t for t in _re_dis.split(r"\s+", cname) if t)
                score = len(reply_tokens & cname_tokens)
                if score > best_score:
                    best_score = score
                    chosen = c
            # Require at least one token overlap
            if best_score == 0:
                chosen = None
        if chosen:
            draft = _pending_drafts[user_id]
            draft["to"] = chosen.get("name") or draft.get("to", "")
            draft["to_email"] = chosen.get("email") or ""
            _pending_drafts[user_id] = draft
            _clear_ctx(user_id)
            return {
                "reply": (
                    f"✓ Got it — sending to **{draft['to']}** <{draft['to_email']}>. "
                    f"Review the draft below and say *send* (or refine first)."
                ),
                "action": "comm_draft",
                "intent": Intent.COMMUNICATION,
                "agent_id": "communication",
                "cards": [{
                    "type": "email_draft",
                    "data": {
                        "id": draft.get("id"),
                        "to": draft["to"],
                        "to_email": draft["to_email"],
                        "subject": draft.get("subject", ""),
                        "body": draft.get("body", ""),
                    },
                }],
            }
        # Couldn't parse the user's pick — re-prompt
        options = "\n".join(f"- **{c['name']}** <{c['email']}>" for c in candidates[:5])
        return {
            "reply": (
                f"Hmm, I couldn't match that to any of these. Try again — type the **full name** "
                f"or **paste the email**:\n\n{options}"
            ),
            "action": "comm_disambiguate",
            "intent": Intent.COMMUNICATION,
            "agent_id": "communication",
        }

    if awaiting == "comm_info":
        # User replied with email details (e.g. "swapnik about the meeting" or "yes write him a mail")
        msg_lower = message.lower().strip()
        # Treat short continuations as COMMUNICATION follow-up
        continuation_kw = ["yes", "yeah", "ok", "sure", "write", "compose", "draft",
                           "send", "mail", "email", "him", "her", "them"]
        import re as _re
        looks_like_comm = (
            any(msg_lower.startswith(kw) for kw in continuation_kw) or
            _re.match(r"^\w[\w\s]+\s+about\s+.+", msg_lower) or
            len(message.split()) <= 6  # short reply likely a follow-up
        )
        if looks_like_comm:
            # If we have a partial (stored "to" or "about"), enrich the message
            partial = ctx.get("comm_partial", "")
            if partial and message.lower().strip() not in ("yes", "yeah", "ok", "sure"):
                message = f"{partial} {message}".strip()
            _clear_ctx(user_id)
            # Directly route to communication agent
            user_name = user_info.get("name") or "Student"
            return _ensure_intent(
                await a2a_tasks_send("/a2a/communication", message, user_id, user_name,
                                     user_email=user_info.get("email", "")),
                Intent.COMMUNICATION, "communication",
            )

    await get_or_create_lumen(user_id, user_info.get("name", ""), user_info.get("email", ""))

    # ── UX preset switching: "switch to vision mode" ──
    from app.lumen.ux_agent import detect_preset_switch, set_ux_preset, PRESETS
    preset_switch = detect_preset_switch(message)
    if preset_switch:
        try:
            preset = await set_ux_preset(user_id, preset_switch)
            return {
                "reply": f"{preset['icon']} Switched to **{preset['name']}** mode. {preset['description']}",
                "action": "ux_preset_changed",
                "intent": "ux",
                "agent_id": "ux-agent",
                "cards": [{"type": "ux_preset", "data": preset}],
            }
        except ValueError:
            pass  # Fall through to normal dispatch

    # ── Widget commands: "add a clock", "remove the calendar" ──
    from app.lumen.widget_manager import detect_widget_command, add_widget, remove_widget, get_widgets, WIDGET_TEMPLATES
    widget_cmd = detect_widget_command(message)
    if widget_cmd:
        action, template_key = widget_cmd
        if action == "add":
            w = add_widget(user_id, template_key)
            if w:
                # Customize map location if specified
                if template_key == "map":
                    import re as _re
                    map_match = _re.search(r"map\s+(?:of\s+)?(.+)", message.lower())
                    if map_match:
                        city = map_match.group(1).strip().rstrip(".")
                        # Common cities
                        cities = {
                            "bengaluru": (12.9716, 77.5946), "bangalore": (12.9716, 77.5946),
                            "mumbai": (19.0760, 72.8777), "delhi": (28.6139, 77.2090),
                            "chennai": (13.0827, 80.2707), "hyderabad": (17.3850, 78.4867),
                            "kolkata": (22.5726, 88.3639), "pune": (18.5204, 73.8567),
                            "new york": (40.7128, -74.0060), "london": (51.5074, -0.1278),
                            "tokyo": (35.6762, 139.6503), "paris": (48.8566, 2.3522),
                            "seattle": (47.6062, -122.3321), "san francisco": (37.7749, -122.4194),
                            "redmond": (47.6740, -122.1215),
                        }
                        coords = cities.get(city, (12.9716, 77.5946))
                        w["a2ui"]["components"][0]["props"]["lat"] = coords[0]
                        w["a2ui"]["components"][0]["props"]["lng"] = coords[1]
                        w["a2ui"]["components"][0]["props"]["title"] = city.title()
                        w["title"] = f"Map — {city.title()}"

                template = WIDGET_TEMPLATES.get(template_key, {})
                return {
                    "reply": f"✅ Added **{w.get('title', template.get('title', template_key))}** to your dashboard!",
                    "action": "widget_added",
                    "intent": "ux",
                    "agent_id": "ux-agent",
                    "widget": w,
                    "widgets": get_widgets(user_id),
                }
            else:
                return {
                    "reply": f"That widget is already on your dashboard, or I don't recognize it.\n\nAvailable: {', '.join(WIDGET_TEMPLATES.keys())}",
                    "action": "inline_answer",
                    "intent": "ux",
                    "agent_id": "ux-agent",
                }
        elif action == "remove":
            ok = remove_widget(user_id, template_key)
            return {
                "reply": f"{'Removed' if ok else 'Could not find'} the {template_key} widget.",
                "action": "widget_removed" if ok else "inline_answer",
                "intent": "ux",
                "agent_id": "ux-agent",
                "widgets": get_widgets(user_id),
            }

    # ── Font/theme commands: "make font bigger", "dark mode" ──
    msg_lower = (message or "").lower().strip()
    if any(kw in msg_lower for kw in ["make font bigger", "bigger font", "increase font", "larger text"]):
        return {"reply": "✅ Font size increased. (Applied via your UX preset settings.)", "action": "font_scale_up", "intent": "ux", "agent_id": "ux-agent"}
    if any(kw in msg_lower for kw in ["make font smaller", "smaller font", "decrease font", "smaller text"]):
        return {"reply": "✅ Font size decreased.", "action": "font_scale_down", "intent": "ux", "agent_id": "ux-agent"}
    if any(kw in msg_lower for kw in ["dark mode", "dark theme"]):
        return {"reply": "🌙 Dark mode activated. (Same as vision mode.)", "action": "ux_preset_changed", "intent": "ux", "agent_id": "ux-agent",
                "cards": [{"type": "ux_preset", "data": PRESETS.get("vision", {})}]}
    if any(kw in msg_lower for kw in ["light mode", "light theme", "normal mode"]):
        return {"reply": "☀️ Light mode activated.", "action": "ux_preset_changed", "intent": "ux", "agent_id": "ux-agent",
                "cards": [{"type": "ux_preset", "data": PRESETS.get("standard", {})}]}

    # If a study-plan proposal is pending and user confirms it with "yes"
    msg_norm = (message or "").strip().lower().rstrip(".!?")
    plan_yes_kw = {"yes", "yeah", "sure", "ok", "schedule them", "schedule it",
                   "yes please", "yes schedule", "add them", "add to calendar", "confirm"}
    plan_no_kw = {"no", "nope", "nah", "no thanks", "discard", "cancel", "don't", "dont"}
    if _pending_proposals.get(user_id):
        if msg_norm in plan_yes_kw:
            proposal = _pending_proposals.pop(user_id)
            return await confirm_study_plan(user_id, proposal)
        elif msg_norm in plan_no_kw:
            _pending_proposals.pop(user_id, None)
            return {
                "reply": "No problem — study plan discarded. Let me know when you want to try again!",
                "action": "inline_answer",
                "intent": Intent.SCHEDULING,
                "agent_id": "calendar",
            }

    # ── Conversational draft refinement ──
    # If a draft is pending and user wants to refine it (not send, not cancel) → re-LLM the body
    refine_kw = ["make it", "more detail", "more detailed", "shorter", "longer", "more formal",
                 "less formal", "more friendly", "be friendlier", "be polite", "more polite",
                 "rewrite", "redo", "redraft", "change the", "add a", "add the", "mention",
                 "say also", "include", "remove", "drop", "tone it down", "shorter please",
                 "be brief", "be concise", "make it pop", "more professional", "less stiff"]
    pending = _pending_drafts.get(user_id)
    msg_for_refine = (message or "").lower()
    if pending and any(kw in msg_for_refine for kw in refine_kw):
        # Use LLM to refine the existing draft body
        try:
            from app.agents.calendar_agent import _get_client
            from app.agents.prompt_kit import build_agent_prompt
            user_name = user_info.get("name", "")
            sys = build_agent_prompt(
                role="Email Editor",
                mission=f"Revise an existing email draft for {user_name or 'the sender'} according to their edit instruction.",
                capabilities=[
                    "Rewrite tone (more formal, friendlier, shorter, longer, etc.).",
                    "Add, remove, or reword specific content the user calls out.",
                    "Preserve the parts of the draft the user didn't ask to change.",
                ],
                rules=[
                    "Return ONLY the new email body — no subject line, no commentary, no surrounding quotes, no markdown.",
                    "Keep the greeting + sign-off structure intact.",
                    f"Sign off as {user_name or 'the sender'}.",
                    "Apply the user's instruction faithfully without inventing unrelated content.",
                ],
                output_format="Plain text — just the revised email body.",
            )
            user_prompt = (
                f"CURRENT EMAIL BODY:\n{pending.get('body', '')}\n\n"
                f"USER'S EDIT INSTRUCTION: {message}\n\n"
                f"Write the revised body now."
            )
            client = _get_client()
            agent = client.as_agent(name="EmailEditor", instructions=sys)
            result = await agent.run(user_prompt)
            new_body = str(result).strip()
            if new_body.startswith("```"):
                new_body = new_body.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            pending["body"] = new_body
            _pending_drafts[user_id] = pending
            return {
                "reply": "✓ Updated the draft. Say *send* when ready, or tell me to refine more.",
                "action": "comm_draft",
                "intent": Intent.COMMUNICATION,
                "agent_id": "communication",
                "cards": [{
                    "type": "email_draft",
                    "data": {
                        "id": pending.get("id"),
                        "to": pending.get("to", ""),
                        "to_email": pending.get("to_email", ""),
                        "subject": pending.get("subject", ""),
                        "body": new_body,
                    },
                }],
            }
        except Exception as e:
            logger.warning(f"Draft refinement failed: {e}")
            # Fall through to compose flow if refinement broke

    # If a draft is pending, only treat as confirmation when message is CLEARLY
    # a confirm/cancel — not when it's a new compose command like "send a mail to X".
    looks_like_confirm = False
    if _pending_drafts.get(user_id):
        import re as _re_conf
        # New-compose patterns — if any match, this is a NEW draft, not a confirmation.
        # Examples: "send a mail to vedanth", "email anirudh about lunch", "write to john"
        new_compose_patterns = [
            r"\bsend\s+(?:a\s+|an\s+)?(?:mail|email|message|note)\s+",  # "send a mail ..."
            r"\b(?:mail|email|message)\s+(?:to\s+)?\w",                  # "email anirudh..."
            r"\bwrite\s+(?:to\s+|a\s+(?:mail|email|note)\s+)?\w",       # "write to john" / "write a note to..."
            r"\bcompose\s+",
            r"\bdraft\s+(?:a\s+|an\s+)?(?:mail|email|message|reply)",
            r"\breply\s+to\s+\w",
            r"\bsend\s+\w+@",  # contains an email address right after "send"
        ]
        is_new_compose = any(_re_conf.search(p, msg_norm) for p in new_compose_patterns)

        # Cancel intent
        cancel_substrs = ["cancel", "discard", "scrap", "throw away", "nevermind", "never mind",
                          "abort", "no don't", "don't send", "do not send"]
        if any(s in msg_norm for s in cancel_substrs) and not is_new_compose:
            clear_pending_draft(user_id)
            return {
                "reply": "👍 Draft discarded.",
                "action": "inline_answer",
                "intent": Intent.COMMUNICATION,
                "agent_id": "communication",
            }

        if not is_new_compose:
            # Strict confirmation patterns (the message must be primarily about sending,
            # not a fresh recipient/topic combination).
            strict_confirms = {
                "send", "send it", "send the mail", "send the email", "send that",
                "send it now", "yes send", "yes send it", "yes send this", "ok send",
                "okay send", "confirm", "confirm send", "yes confirm", "yep send",
                "do it", "yes do it", "send please", "go ahead", "go ahead and send",
                "ship it", "fire away", "yes please", "yes",  "yeah", "yep",
                "ok", "okay", "sure", "alright",
            }
            if msg_norm in strict_confirms:
                looks_like_confirm = True
            elif _re_conf.match(r"^(yes|ok|okay|sure|yeah|yep)[\s,.\-]+(send|do it|confirm|ship it)\b", msg_norm):
                looks_like_confirm = True
            elif _re_conf.match(r"^send\s+(it|that|this|now|please|the\s+(mail|email))?$", msg_norm):
                looks_like_confirm = True
    if looks_like_confirm and _pending_drafts.get(user_id):
        draft = _pending_drafts[user_id]

        # IMAP removed — send routes through Gmail API (Google users) or the
        # Chrome extension (Outlook users) on the frontend's EmailDraftCard.
        # Return the draft card with auto_send so the frontend executes the send.
        # triggers the Chrome extension's composeAndSend flow. The frontend's
        # EmailDraftCard already routes to the extension when IMAP is missing.
        return {
            "reply": (
                f"📤 Sending via your Outlook session… If the Lumen extension is installed "
                f"and Outlook is open in another tab, the email will be sent there.\n\n"
                f"If nothing happens, install the **Lumen for Outlook** extension or say "
                f"**connect my email** for IMAP setup."
            ),
            "action": "comm_send_via_extension",
            "intent": Intent.COMMUNICATION,
            "agent_id": "communication",
            "cards": [{
                "type": "email_draft",
                "data": {
                    "id": draft.get("id"),
                    "to": draft.get("to", ""),
                    "to_email": draft.get("to_email", ""),
                    "subject": draft.get("subject", ""),
                    "body": draft.get("body", ""),
                    "auto_send": True,  # frontend reads this and auto-fires handleSend
                },
            }],
        }

    user_name = user_info.get("name") or "Student"
    graph_meta = {"graph_token": graph_token} if graph_token else None

    # ── Rule-based routing (fast, zero LLM cost) ─────────────────────────────
    # classify_intent() handles all well-known single-intent messages with
    # keyword rules. Only GENERAL intent (or multi-intent signals) goes to LLM.
    # Maps classify_intent() string values → agent names used by _execute_multi_intents.
    _INTENT_TO_AGENT: dict[str, str] = {
        Intent.QUERY:          "calendar",
        Intent.SCHEDULING:     "calendar",
        Intent.COMMUNICATION:  "communication",
        Intent.PORTFOLIO:      "github",
        Intent.SOCIAL:         "social",
        Intent.SHIKSHA:        "shiksha",
        Intent.LEARNING:       "shiksha",
        Intent.GMAIL:          "gmail",
        Intent.OUTLOOK:        "gmail",
        Intent.ONEDRIVE:       "drive",
        Intent.DRIVE:          "drive",
        Intent.NOTION:         "notion",
        Intent.ARXIV:          "arxiv",
        Intent.WOLFRAM:        "wolfram",
        Intent.PROGRESS:       "general",
        Intent.META:           "general",
    }

    rule_intent, rule_target = classify_intent(message)

    # Deep-link launches (e.g. "open Shiksha") stay a fast rule.
    if rule_intent == Intent.LAUNCH:
        return await _handle_launch(rule_target or "shiksha")

    # Disconnecting an integration always asks for confirmation first — never
    # auto-revokes, and routed deterministically (not via the LLM).
    if _is_disconnect_request(message):
        return _handle_disconnect(message)

    # "Show my profile" — answered directly from the user's own Lumen doc.
    if _is_profile_query(message):
        return await _handle_profile(user_id, user_info)

    from app.agents.llm_router import IntentMatch, llm_classify_multi

    def _run(plan):
        return _execute_multi_intents(
            plan, user_id, user_info, message, conversation_history, graph_token, user_name,
        )

    # ── Fast-path: skip the LLM for trivially obvious messages ───────────────
    if _should_skip_llm_routing(message, rule_intent):
        if rule_intent in (Intent.PROGRESS, Intent.META, Intent.GENERAL):
            return await _handle_lumen(user_id, message, conversation_history=conversation_history)
        return await _run([IntentMatch(agent=_INTENT_TO_AGENT.get(rule_intent, "general"), task=message)])

    # ── LLM-primary routing: classify every other message with the LLM ───────
    # Natural phrasings route correctly here regardless of keyword wording.
    # Token cost is attributed to source="lumen" since routing is Lumen's job.
    plan = await llm_classify_multi(user_id, message, conversation_history or [])
    if plan:
        return await _run(plan)

    # ── Degrade gracefully if the LLM router is unavailable ──────────────────
    # (circuit breaker / failure) — fall back to keyword classification.
    if rule_intent != Intent.GENERAL:
        return await _run([IntentMatch(agent=_INTENT_TO_AGENT.get(rule_intent, "general"), task=message)])
    return await _handle_lumen(user_id, message, conversation_history=conversation_history)


def _ensure_intent(result: dict, intent: str, agent_id: str | None = None) -> dict:
    """Backfill intent/agent_id on results coming back from an A2A self-call.

    The handler-side dicts already include intent and agent_id, but if a
    response only has the text part (older external caller) the JSON may be
    missing those fields. Fill them in defensively.
    """
    result = dict(result or {})
    result.setdefault("intent", intent)
    if agent_id is not None:
        result.setdefault("agent_id", agent_id)
    return result


async def dispatch_to_ta(user_id: str, ta_id: str, message: str,
                         thread_id: str | None = None,
                         user_info: dict | None = None) -> dict:
    """Dispatch directly to a TA (used from TA chat pages)."""
    user_info = user_info or {}
    lumen = await get_or_create_lumen(
        user_id, user_info.get("name", ""), user_info.get("email", ""))

    await publish(SESSION_STARTED, {"user_id": user_id, "ta_id": ta_id})

    state = await get_lumen_state(user_id, ta_id) or {}
    ta_state = state.get("current_ta_state", {})

    ta_request = TARequest(
        message=message,
        student_context=StudentContext(
            user_id=user_id,
            name=lumen.get("name", "Student"),
            progress=ta_state,
            cross_ta_progress=[
                {"ta_id": ct.get("ta_id", ""), "ta_name": ct.get("ta_name", ""), **ct}
                for ct in state.get("cross_ta_context", [])
            ],
            tc_inventory=state.get("tc_inventory", {}),
        ),
        thread_id=thread_id,
    )

    result = await a2a_chat(ta_id, ta_request)

    # Publish progress event (decoupled from inline update)
    progress_data = result.progress_report.model_dump() if result.progress_report else {}
    ta_name = result.ta_metadata.get("ta_name", ta_id) if result.ta_metadata else ta_id

    await update_progress(user_id, ta_id, ta_name, progress_data)
    await publish(PROGRESS_UPDATED, {
        "user_id": user_id, "ta_id": ta_id, "ta_name": ta_name,
        "progress": progress_data,
    })
    await publish(SESSION_ENDED, {"user_id": user_id, "ta_id": ta_id})

    return {
        "reply": result.reply,
        "action": "ta_response",
        "intent": Intent.LEARNING,
        "agent_id": ta_id,
        "progress": progress_data,
        "thread_id": thread_id,
    }


# ── Intent Handlers ──────────────────────────────────────────

TA_URLS = {"shiksha": "/ta", "calendar": "/calendar", "github": "/github"}


async def _handle_portfolio(user_id: str, message: str) -> dict:
    """Handle portfolio / GitHub queries and file operations from chat."""
    import re as _re
    from app.agents.portfolio_agent import (
        list_artifacts, delete_artifact, get_portfolio_status, ensure_portfolio_repo,
        get_github_credentials, PORTFOLIO_REPO_NAME,
    )

    msg = message.lower().strip()

    status = await get_portfolio_status(user_id)
    if not status.get("connected"):
        return {
            "reply": "📁 Connect your GitHub to use your portfolio. It opens GitHub, you click **Authorize**, and you're done — no token to paste.",
            "action": "portfolio_not_connected",
            "intent": Intent.PORTFOLIO,
            "agent_id": "portfolio",
            "cards": [{"type": "connect_github", "data": {"retry_message": message}}],
        }

    token, owner = await get_github_credentials(user_id)

    # ── Sub-intent: GitHub Actions / workflow runs ───────────────
    is_actions = any(kw in msg for kw in [
        "github action", "github actions", "workflow run", "workflow runs",
        "actions runs", "ci run", "ci status", "build status", "pipeline",
        "my actions", "show actions", "workflow status",
    ])
    if is_actions:
        from app.agents.portfolio_agent import list_workflow_runs
        result = await list_workflow_runs(user_id, limit=8)
        if not result.get("ok"):
            return {"reply": f"Couldn't load GitHub Actions: {result.get('error')}", "action": "error", "intent": Intent.PORTFOLIO, "agent_id": "portfolio"}
        runs = result.get("runs", [])
        if not runs:
            return {"reply": "⚙️ No GitHub Actions workflow runs in your portfolio repo yet.", "action": "inline_answer", "intent": Intent.PORTFOLIO, "agent_id": "portfolio"}
        lines = ["⚙️ **Recent GitHub Actions runs:**"]
        for r in runs[:8]:
            icon = "✅" if r.get("conclusion") == "success" else "❌" if r.get("conclusion") == "failure" else "🟡"
            lines.append(f"{icon} [{r.get('name', 'workflow')}]({r.get('url', '')}) — {r.get('status', '')} on `{r.get('branch', '')}`")
        return {"reply": "\n".join(lines), "action": "portfolio_actions", "intent": Intent.PORTFOLIO, "agent_id": "portfolio"}

    # ── Sub-intent: discard / list staged ───────────────────────
    if any(kw in msg for kw in ["discard staged", "clear staged"]):
        from app.agents.portfolio_agent import clear_staged, get_staged
        n = len(get_staged(user_id))
        clear_staged(user_id)
        return {"reply": f"🗑️ Discarded {n} staged change(s).", "action": "portfolio_staged_cleared", "intent": Intent.PORTFOLIO, "agent_id": "portfolio"}
    if any(kw in msg for kw in ["what's staged", "whats staged", "my staged", "staged file", "staged change", "list staged"]):
        from app.agents.portfolio_agent import get_staged
        staged = get_staged(user_id)
        if not staged:
            return {"reply": "Nothing is staged right now. Attach a file and say 'save to my portfolio' to stage one.", "action": "inline_answer", "intent": Intent.PORTFOLIO, "agent_id": "portfolio"}
        lines = [f"🟡 **{len(staged)} staged change(s)** (not committed):"]
        for s in staged:
            lines.append(f"- `{s['path']}` ({(s.get('size', 0) / 1024):.1f} KB)")
        lines.append("\nSay **commit staged** to push, or **discard staged** to drop.")
        return {"reply": "\n".join(lines), "action": "portfolio_staged_list", "intent": Intent.PORTFOLIO, "agent_id": "portfolio"}

    # ── Sub-intent: commit staged changes ────────────────────────
    is_commit_action = any(kw in msg for kw in [
        "commit staged", "commit my staged", "commit the staged", "commit changes",
        "commit my files", "commit now", "commit them", "push staged",
    ])
    if is_commit_action:
        from app.agents.portfolio_agent import commit_staged, get_staged
        if not get_staged(user_id):
            return {"reply": "Nothing is staged to commit. Stage a file from the Portfolio page first, then say 'commit staged'.", "action": "inline_answer", "intent": Intent.PORTFOLIO, "agent_id": "portfolio"}
        result = await commit_staged(user_id)
        if not result.get("ok"):
            return {"reply": f"Commit failed: {result.get('error')}", "action": "error", "intent": Intent.PORTFOLIO, "agent_id": "portfolio"}
        committed = result.get("committed", [])
        return {"reply": f"✅ Committed {len(committed)} file(s) to your portfolio.", "action": "portfolio_committed", "intent": Intent.PORTFOLIO, "agent_id": "portfolio"}

    # ── Sub-intent: read/retrieve file content ───────────────────
    content_match = _re.search(
        r"(?:show|open|read|view|get|display|cat|content[s]? of|what'?s in)\s+"
        r"(?:the\s+|my\s+|file\s+)*([\w\-./]+\.\w+)",
        msg,
    )
    if content_match:
        from app.agents.portfolio_agent import get_file_content
        fpath = content_match.group(1).strip()
        result = await get_file_content(user_id, fpath)
        if result.get("ok"):
            if not result.get("is_text"):
                return {"reply": f"📄 **{result.get('name')}** is a binary file ({result.get('size', 0)} bytes). [Download]({result.get('download_url', '')})", "action": "inline_answer", "intent": Intent.PORTFOLIO, "agent_id": "portfolio"}
            body = result.get("content", "")
            trunc = "\n\n…(truncated)" if result.get("truncated") else ""
            return {
                "reply": f"📄 **{result.get('path')}**:\n\n```\n{body}\n```{trunc}",
                "action": "portfolio_file_content",
                "intent": Intent.PORTFOLIO,
                "agent_id": "portfolio",
            }
        # Not found by exact path — fall through to normal listing/search below.

    # ── Sub-intent: commits ──────────────────────────────────────
    is_commits = any(kw in msg for kw in ["commit", "commits", "what did i commit", "recent commit"])
    if is_commits:
        try:
            from github import Auth, Github
            gh = Github(auth=Auth.Token(token))
            repo_name = f"{owner}/{PORTFOLIO_REPO_NAME}"
            repo = gh.get_repo(repo_name)
            commits = list(repo.get_commits()[:10])
            if not commits:
                return {"reply": "No commits found in your portfolio repo yet.", "action": "inline_answer", "intent": Intent.PORTFOLIO, "agent_id": "portfolio"}
            lines = [f"**Recent commits in `{repo_name}`:**"]
            for c in commits:
                date = c.commit.author.date.strftime("%b %d") if c.commit.author else "?"
                sha = c.sha[:7]
                msg_line = c.commit.message.split("\n")[0][:60]
                lines.append(f"- `{sha}` {date} — {msg_line}")
            return {
                "reply": "\n".join(lines),
                "action": "portfolio_commits",
                "intent": Intent.PORTFOLIO,
                "agent_id": "portfolio",
            }
        except Exception as e:
            return {"reply": f"Couldn't fetch commits: {e}", "action": "error", "intent": Intent.PORTFOLIO, "agent_id": "portfolio"}

    # ── Sub-intent: list all user repos ─────────────────────────
    is_repos = any(kw in msg for kw in [
        "my repos", "my repositories", "list repos", "all repos", "list my repos",
        "my github repos", "my github repositories", "github repos", "github repositories",
        "list github repos", "list my github", "show github repos", "show my repos",
        "show my github repos", "show my github",
    ])
    if is_repos:
        try:
            from github import Auth, Github
            gh = Github(auth=Auth.Token(token))
            user = gh.get_user()
            repos = list(user.get_repos(sort="updated"))[:15]
            if not repos:
                return {"reply": "No repositories found.", "action": "inline_answer", "intent": Intent.PORTFOLIO, "agent_id": "portfolio"}
            lines = [f"**Your GitHub repositories (@{user.login}):**"]
            for r in repos:
                vis = "🔒" if r.private else "🌐"
                lines.append(f"{vis} [{r.name}]({r.html_url}) — {r.description or 'no description'}")
            return {
                "reply": "\n".join(lines),
                "action": "portfolio_repos",
                "intent": Intent.PORTFOLIO,
                "agent_id": "portfolio",
            }
        except Exception as e:
            return {"reply": f"Couldn't fetch repos: {e}", "action": "error", "intent": Intent.PORTFOLIO, "agent_id": "portfolio"}

    # ── Sub-intent: list specific folder ────────────────────────
    folder_match = _re.search(
        r"(?:files? in|contents? of|inside|in the?)\s+([\w\-]+(?:/[\w\-]+)?)\s*(?:folder|directory|ta|path)?",
        msg
    )
    if folder_match:
        raw_hint = folder_match.group(1).strip()
        # Map natural names → canonical TA folders: "math"/"math ta" → "math-ta",
        # "cs"/"computer" → "cs-ta", etc. Fall back to the raw hint otherwise.
        from app.agents.portfolio_agent import detect_ta_folder
        canonical = detect_ta_folder(msg)
        path_hint = canonical if canonical != "general" else raw_hint
        result = await list_artifacts(user_id, path_hint)
        if result.get("ok"):
            files = result.get("files", [])
            if not files:
                return {"reply": f"The `{path_hint}/` folder is empty.", "action": "inline_answer", "intent": Intent.PORTFOLIO, "agent_id": "portfolio"}
            return {
                "reply": f"📂 `{path_hint}/` — {len(files)} item(s):",
                "action": "portfolio_list",
                "intent": Intent.PORTFOLIO,
                "agent_id": "portfolio",
                "cards": [{"type": "portfolio_files", "data": {"files": files, "mode": "browse"}}],
            }

    # ── Sub-intent: delete/remove ────────────────────────────────
    is_delete = any(kw in msg for kw in ["remove", "delete", "del "])
    if is_delete:
        stripped = _re.sub(
            r"(remove|delete|del|from github|from my github|from repo|from the repo|from portfolio|from my portfolio|please)",
            "", msg
        ).strip().strip(".")
        root = await list_artifacts(user_id, "")
        all_files = []
        if root.get("ok"):
            for item in root.get("files", []):
                if item["type"] == "dir":
                    sub = await list_artifacts(user_id, item["path"])
                    if sub.get("ok"):
                        all_files.extend(f for f in sub.get("files", []) if f["type"] == "file")
                else:
                    all_files.append(item)

        if not all_files:
            return {"reply": "📁 Your portfolio is empty — nothing to remove.", "action": "inline_answer", "intent": Intent.PORTFOLIO, "agent_id": "portfolio"}

        def score(f):
            name = f["name"].lower().replace("_", " ").replace("-", " ")
            return any(len(word) > 2 and word in name for word in stripped.split())

        matches = [f for f in all_files if score(f)]
        if len(matches) == 1:
            result = await delete_artifact(user_id, matches[0]["path"])
            if result.get("ok"):
                return {"reply": f"🗑️ Deleted **{matches[0]['name']}** from your portfolio.", "action": "portfolio_deleted", "intent": Intent.PORTFOLIO, "agent_id": "portfolio"}
            return {"reply": f"Couldn't delete: {result.get('error')}", "action": "error", "intent": Intent.PORTFOLIO, "agent_id": "portfolio"}
        elif len(matches) > 1:
            return {
                "reply": f"Found {len(matches)} matching files — which one?",
                "action": "portfolio_pick_delete",
                "intent": Intent.PORTFOLIO,
                "agent_id": "portfolio",
                "cards": [{"type": "portfolio_files", "data": {"files": matches, "mode": "delete"}}],
            }
        else:
            return {
                "reply": f"I couldn't find \"{stripped.strip()}\" — here are all your files:",
                "action": "portfolio_list",
                "intent": Intent.PORTFOLIO,
                "agent_id": "portfolio",
                "cards": [{"type": "portfolio_files", "data": {"files": all_files, "mode": "delete"}}],
            }

    # ── Default: list portfolio root ─────────────────────────────
    if not status.get("repo_exists"):
        setup = await ensure_portfolio_repo(user_id)
        if not setup.get("ok"):
            return {
                "reply": f"📁 Couldn't create your portfolio repo: {setup.get('error', 'unknown error')}. Check your GitHub connection in Profile.",
                "action": "portfolio_not_connected",
                "intent": Intent.PORTFOLIO,
                "agent_id": "portfolio",
            }
        if setup.get("created"):
            return {
                "reply": f"📁 Created your portfolio repo **{setup.get('full_name', PORTFOLIO_REPO_NAME)}**! It's empty — upload files via the 📎 button or say 'add to my github'.\n[View on GitHub]({setup.get('url', '')})",
                "action": "portfolio_created",
                "intent": Intent.PORTFOLIO,
                "agent_id": "portfolio",
            }

    result = await list_artifacts(user_id, "")
    if not result.get("ok"):
        return {"reply": f"Couldn't load portfolio: {result.get('error')}", "action": "error", "intent": Intent.PORTFOLIO, "agent_id": "portfolio"}

    files = result.get("files", [])
    if not files:
        return {"reply": "📁 Your portfolio repo is empty. Upload files via the 📎 button and say 'add to my github'.", "action": "inline_answer", "intent": Intent.PORTFOLIO, "agent_id": "portfolio"}

    folders = [f for f in files if f["type"] == "dir"]
    top_files = [f for f in files if f["type"] == "file"]
    parts = []
    if folders:
        parts.append(f"{len(folders)} folder(s): " + ", ".join(f'`{f["name"]}`' for f in folders))
    if top_files:
        parts.append(f"{len(top_files)} file(s) at root")

    return {
        "reply": f"📁 **{owner}/{PORTFOLIO_REPO_NAME}** — {', '.join(parts) if parts else 'empty'}.\n[View on GitHub]({status.get('repo_url', '')})",
        "action": "portfolio_list",
        "intent": Intent.PORTFOLIO,
        "agent_id": "portfolio",
        "cards": [{"type": "portfolio_files", "data": {"files": files, "mode": "browse"}}],
    }

async def _handle_shiksha(user_id: str, message: str) -> dict:
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


async def _handle_context_switch(user_id: str, message: str, ta_id: str) -> dict:
    """Handle learning intent — query TA state inline instead of redirecting."""
    card = get_agent_card(ta_id) if ta_id else None
    ta_name = card.name if card else ta_id or "Teaching Assistant"
    state = await get_lumen_state(user_id, ta_id) or {}
    ta_state = state.get("current_ta_state", {})

    level = ta_state.get("level", 1)
    label = ta_state.get("label", "beginner")
    sessions = ta_state.get("sessions_completed", 0)
    topics_covered = ta_state.get("topics_covered", [])
    topics_mastered = ta_state.get("topics_mastered", [])
    module = ta_state.get("current_module", "basics")
    pct = ta_state.get("completion_pct", 0)

    if sessions == 0:
        reply = f"You haven't started with {ta_name} yet. Say 'open {ta_id}' to begin a session!"
    else:
        reply = (f"📊 **{ta_name}** — Level {level} ({label})\n"
                 f"- Sessions: {sessions}\n"
                 f"- Module: {module} ({pct}% complete)\n"
                 f"- Topics covered: {len(topics_covered)}, mastered: {len(topics_mastered)}\n\n"
                 f"Say 'open {ta_id}' to start a new session.")

    progress_card = {
        "type": "progress",
        "data": {"ta_id": ta_id, "ta_name": ta_name, "level": level, "label": label,
                 "sessions": sessions, "topics_covered": topics_covered,
                 "topics_mastered": topics_mastered, "module": module, "pct": pct},
    }

    return {
        "reply": reply,
        "action": "inline_answer",
        "intent": Intent.LEARNING,
        "agent_id": ta_id,
        "cards": [progress_card],
    }


async def _handle_launch(target: str) -> dict:
    """Handle explicit 'open X' — all agents open in new browser tab."""
    # Coding TA is an in-app page (context switch), not an external tab.
    if target == "coding-ta":
        return {
            "reply": "Opening your Coding TA — anything you create there is auto-saved to your GitHub portfolio.",
            "action": "context_switch",
            "intent": Intent.LAUNCH,
            "agent_id": "coding-ta",
            "redirect_url": "/coding-ta",
        }
    # GitHub agent opens in-app — explore repos, commits, files, and your portfolio.
    if target in ("github", "portfolio"):
        return {
            "reply": "Opening the GitHub agent — explore your repos, commits, branches, pull requests, files, and learning portfolio.",
            "action": "context_switch",
            "intent": Intent.LAUNCH,
            "agent_id": "github",
            "redirect_url": "/github",
        }
    card = get_agent_card(target) if target and target != "calendar" else None
    name = card.name if card else target.replace("-", " ").title()
    url = TA_URLS.get(target, f"/ta/{target}")
    return {
        "reply": f"Opening {name} in a new window...",
        "action": "external_launch",
        "intent": Intent.LAUNCH,
        "agent_id": target,
        "redirect_url": url,
        "target": "_blank",
    }


async def _handle_google_calendar(user_id: str, message: str) -> dict | None:
    """If the user has Google Calendar connected, handle read/create/delete via the
    Google Calendar API. Returns a response dict, or None to fall through to the
    Lumen calendar agent (used for study plans, holidays, and other Lumen-specific
    features that don't map to Google Calendar).
    """
    from app.agents.gmail_agent import is_gcalendar_connected, get_valid_google_token
    from app.agents.gcalendar_agent import (
        list_events, search_events, create_event, delete_event, parse_when,
    )
    from app.lumen.core import get_lumen

    lumen = await get_lumen(user_id)
    if not is_gcalendar_connected(lumen):
        return None  # fall through to Lumen calendar

    # Honor the user's preferred calendar provider (set in Profile).
    # Default behavior when unset / "auto" / "google" → use Google Calendar (current path).
    pref = (lumen.get("preferences", {}) or {}).get("calendar_provider", "auto") or "auto"
    pref = pref.lower()
    if pref in ("lumen", "local"):
        return None  # fall through to Lumen's internal calendar
    if pref == "outlook":
        return None  # let the existing Outlook handler take it

    # User can also opt out per-message by saying "lumen calendar"
    msg = (message or "").lower().strip()
    if "lumen calendar" in msg or "local calendar" in msg or "internal calendar" in msg:
        return None
    if "outlook calendar" in msg:
        return None

    # Skip Google Calendar for Lumen-specific features (study plans, holidays).
    LUMEN_ONLY_KW = ["study plan", "plan my week", "plan for me", "study schedule",
                     "add holiday", "mark as holiday", "build a plan"]
    if any(kw in msg for kw in LUMEN_ONLY_KW):
        return None

    token = await get_valid_google_token(user_id)
    if not token:
        return None

    import re as _re_gc
    from datetime import datetime, timedelta, timezone

    # ── DELETE ──
    delete_kw = ["cancel ", "delete ", "remove ", "drop "]
    if any(kw in msg for kw in delete_kw):
        # Find what to delete — strip the verb + filler words
        hint = msg
        for kw in delete_kw:
            if kw in hint:
                hint = hint.split(kw, 1)[-1].strip()
                break
        for drop in ("event", "events", "meeting", "meetings", "reminder", "reminders",
                      "the", "my", "all", "from", "on", "in", "calendar", "google", "gcal"):
            hint = _re_gc.sub(rf"\b{drop}\b", "", hint).strip()
        hint = _re_gc.sub(r"\s+", " ", hint).strip()
        if not hint:
            return {
                "reply": "Which event? Say e.g. *delete my 3pm meeting* or *cancel chemistry class*.",
                "action": "inline_answer",
                "intent": Intent.SCHEDULING,
                "agent_id": "gcalendar",
            }
        matches = await search_events(token, hint, days_ahead=90)
        if not matches:
            return {
                "reply": f"📅 No events matching *{hint}* found in your Google Calendar.",
                "action": "inline_answer",
                "intent": Intent.SCHEDULING,
                "agent_id": "gcalendar",
            }
        # Delete the closest upcoming match (or all if "all")
        targets = matches if "all" in msg else matches[:1]
        deleted = []
        for ev in targets:
            r = await delete_event(token, ev["id"])
            if r.get("ok"):
                deleted.append(ev.get("title", "(no title)"))
        if not deleted:
            return {
                "reply": f"⚠ Could not delete events matching *{hint}*.",
                "action": "inline_answer",
                "intent": Intent.SCHEDULING,
                "agent_id": "gcalendar",
            }
        titles = ", ".join(deleted)
        return {
            "reply": f"🗑 Deleted from Google Calendar: **{titles}**.",
            "action": "inline_answer",
            "intent": Intent.SCHEDULING,
            "agent_id": "gcalendar",
        }

    # ── CREATE ──
    # Patterns: "add event X at Y", "schedule X for tomorrow", "remind me to X at 3pm",
    # "create event X", "set a reminder for X at Y", "book X tomorrow 5pm",
    # "set june 12 as my birthday on my calendar", "mark holi on my calendar".
    create_kw = ["add event", "add a meeting", "add meeting", "add to calendar",
                 "schedule", "remind me", "set a reminder", "create event",
                 "create a meeting", "book ", "new event", "new meeting",
                 "mark "]
    # Pattern fallback — verb + (calendar OR event noun)
    _create_pat = _re_gc.compile(
        r"\b(set|add|mark|block|book|create|put|insert|schedule|new)\b"
        r".*?\b(calendar|event|birthday|anniversary|reminder|meeting|"
        r"appointment|slot|holiday|deadline)\b",
        _re_gc.IGNORECASE,
    )
    is_create = any(kw in msg for kw in create_kw) or bool(_create_pat.search(msg))
    if is_create:
        # Extract title — strip the verb prefix
        title_text = message
        for kw in ["add an event", "add a meeting", "add event", "add meeting",
                   "schedule a meeting", "schedule a", "schedule",
                   "remind me to", "remind me",
                   "set a reminder for", "set a reminder", "set ",
                   "mark ",
                   "create an event", "create a meeting", "create event", "create",
                   "new event", "new meeting", "book ", "add to calendar"]:
            if title_text.lower().startswith(kw):
                title_text = title_text[len(kw):].strip()
                break
        # Drop leading articles
        title_text = _re_gc.sub(r"^(?:to|a|an|the)\s+", "", title_text, flags=_re_gc.IGNORECASE)
        # If the message follows the "X as my <birthday|...>" template, the
        # ACTUAL title is the noun after "as my", and the date is what came before.
        # e.g. "june 12 as my birthday on my google calendar" → title="Birthday", date="june 12"
        as_my_match = _re_gc.search(
            r"^(.+?)\s+as\s+(?:my|the)\s+(.+?)(?:\s+(?:on|in|to)\s+(?:my\s+)?(?:google\s+)?calendar)?$",
            title_text, _re_gc.IGNORECASE,
        )
        date_hint_from_as_my = None
        if as_my_match:
            date_hint_from_as_my = as_my_match.group(1).strip()
            title_text = as_my_match.group(2).strip()

        # Detect natural-language time. Prefer the "as my" date hint if present,
        # then the rest of the message.
        when = None
        if date_hint_from_as_my:
            when = parse_when(date_hint_from_as_my)
        if when is None:
            when = parse_when(message)

        # Strip the when-portion from the title for a cleaner summary
        title = title_text
        for cue in [" at ", " on ", " tomorrow", " today", " tonight",
                    " next ", " this ", " for ", " from "]:
            idx = title.lower().find(cue)
            if idx > 0:
                title = title[:idx].strip()
                break
        title = title.strip(".,;:!?\"' ") or "Untitled event"

        if when is None:
            # Default: tomorrow 10am, 1h block
            start = (datetime.now(timezone.utc) + timedelta(days=1)).replace(
                hour=10, minute=0, second=0, microsecond=0
            ).astimezone()
            end = start + timedelta(hours=1)
            all_day = False
        else:
            start, end, all_day = when

        result = await create_event(token, title=title, start=start, end=end, all_day=all_day)
        if result.get("error"):
            return {
                "reply": f"⚠ Couldn't create the event: {result['error']}",
                "action": "inline_answer",
                "intent": Intent.SCHEDULING,
                "agent_id": "gcalendar",
            }
        when_str = start.strftime("%a %b %d") + ("" if all_day else f" at {start.strftime('%I:%M %p').lstrip('0')}")
        return {
            "reply": (
                f"✅ Added **{title}** to your Google Calendar — {when_str}"
                + (f" — [open]({result.get('url', '')})" if result.get("url") else "")
            ),
            "action": "inline_answer",
            "intent": Intent.SCHEDULING,
            "agent_id": "gcalendar",
        }

    # ── SEARCH ──
    search_match = _re_gc.search(
        r"(?:search|find|look\s+for)\s+(?:my\s+)?(?:event|meeting|calendar)?s?"
        r"\s+(?:about|on|for|with|containing|regarding)?\s+(.+?)(?:\?|\.|$)",
        msg,
    )
    if search_match or any(kw in msg for kw in ["search calendar", "find event", "find meeting"]):
        query = (search_match.group(1) if search_match else "").strip().strip('"\'')
        events = await search_events(token, query or "", days_ahead=60) if query else \
                  await list_events(token, days_ahead=30, max_results=20)
        if not events:
            return {
                "reply": f"📅 No matching events found in Google Calendar.",
                "action": "inline_answer",
                "intent": Intent.QUERY,
                "agent_id": "gcalendar",
            }
        lines = [f"📅 **{len(events)} event(s){' matching *' + query + '*' if query else ''}:**\n"]
        for ev in events[:5]:
            when_str = ev.get("start", "")[:16].replace("T", " ")
            lines.append(f"- **{ev.get('title')}** — {when_str}")
        return {
            "reply": "\n".join(lines),
            "action": "calendar_query",
            "intent": Intent.QUERY,
            "agent_id": "gcalendar",
            "cards": [{"type": "gcal_events", "data": events[:10]}],
        }

    # ── DEFAULT QUERY: today / tomorrow / this week / upcoming ──
    days_ahead = 7
    if "today" in msg or "tonight" in msg:
        days_ahead = 1
    elif "tomorrow" in msg:
        days_ahead = 2
    elif "this week" in msg or "next week" in msg:
        days_ahead = 7
    elif "this month" in msg or "next month" in msg:
        days_ahead = 31
    events = await list_events(token, days_ahead=days_ahead, max_results=20)

    # Filter to specific day if asked
    if "today" in msg or "tonight" in msg:
        today_iso = datetime.now().astimezone().date().isoformat()
        events = [e for e in events if (e.get("start") or "").startswith(today_iso)]
    elif "tomorrow" in msg:
        tomorrow_iso = (datetime.now().astimezone() + timedelta(days=1)).date().isoformat()
        events = [e for e in events if (e.get("start") or "").startswith(tomorrow_iso)]

    if not events:
        return {
            "reply": "📅 No events in your Google Calendar for that range.",
            "action": "inline_answer",
            "intent": Intent.QUERY,
            "agent_id": "gcalendar",
        }
    label = "today" if "today" in msg else ("tomorrow" if "tomorrow" in msg
            else (f"the next {days_ahead} day(s)" if days_ahead > 1 else "today"))
    lines = [f"📅 **{len(events)} event(s)** in {label}:\n"]
    for ev in events[:5]:
        when_str = ev.get("start", "")[:16].replace("T", " ")
        lines.append(f"- **{ev.get('title')}** — {when_str}")
    return {
        "reply": "\n".join(lines),
        "action": "calendar_query",
        "intent": Intent.QUERY,
        "agent_id": "gcalendar",
        "cards": [{"type": "gcal_events", "data": events[:10]}],
    }


async def _handle_calendar_query(user_id: str, message: str = "") -> dict:
    """Query calendar events and return inline. Supports month/week/type filtering."""
    # Prefer Google Calendar if the user has it connected
    gcal_result = await _handle_google_calendar(user_id, message)
    if gcal_result is not None:
        return gcal_result

    from app.agents.calendar_agent import get_user_events, seed_holidays
    seed_holidays(user_id)  # Ensure holidays are present
    events = await get_user_events(user_id, include_past=True)
    msg = (message or "").lower()

    # Filter by month
    months = {"january": "01", "february": "02", "march": "03", "april": "04",
              "may": "05", "june": "06", "july": "07", "august": "08",
              "september": "09", "october": "10", "november": "11", "december": "12",
              "jan": "01", "feb": "02", "mar": "03", "apr": "04",
              "jun": "06", "jul": "07", "aug": "08", "sep": "09",
              "oct": "10", "nov": "11", "dec": "12"}
    month_filter = None
    for mname, mnum in months.items():
        if mname in msg:
            month_filter = mnum
            break

    # Filter by type
    type_filter = None
    if "holiday" in msg or "holidays" in msg:
        type_filter = "holiday"
    elif "study" in msg:
        type_filter = "study"
    elif "meeting" in msg:
        type_filter = "meeting"
    elif "reminder" in msg:
        type_filter = "reminder"

    filtered = events
    if month_filter:
        filtered = [e for e in filtered if e.get("date", "").split("-")[1:2] == [month_filter]]
    if type_filter:
        filtered = [e for e in filtered if e.get("type", "") == type_filter]

    # Today / tomorrow filter
    from datetime import datetime, timedelta
    today = datetime.now().strftime("%Y-%m-%d")
    tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    if "today" in msg:
        filtered = [e for e in events if e.get("date") == today]
    elif "tomorrow" in msg:
        filtered = [e for e in events if e.get("date") == tomorrow]
    elif "this week" in msg:
        from datetime import date as dt_date
        d = dt_date.today()
        start = d - timedelta(days=d.weekday())
        end = start + timedelta(days=6)
        filtered = [e for e in events if start.isoformat() <= e.get("date", "") <= end.isoformat()]
    elif not month_filter and not type_filter and "today" not in msg and "tomorrow" not in msg:
        # Default: upcoming only (future events)
        filtered = [e for e in events if e.get("date", "") >= today]

    if not filtered:
        label = ""
        if month_filter:
            _month_names = [k for k, v in months.items() if v == month_filter and len(k) > 3]
            label = f" in {_month_names[0].title()}" if _month_names else ""
        if type_filter:
            label += f" ({type_filter})"
        reply = f"📅 No events found{label}. Say 'remind me...' or 'schedule...' to add one."
    else:
        label = ""
        if month_filter:
            long_months = {v: k.title() for k, v in months.items() if len(k) > 3}
            label = f" in {long_months.get(month_filter, '')}"
        if type_filter:
            label += f" ({type_filter})"
        lines = [f"📅 **Your events{label}** ({len(filtered)}):\n"]
        for ev in filtered[:10]:
            date = ev.get("date", "TBD")
            time = ev.get("time", "")
            title = ev.get("title", "Event")
            status = ev.get("status", "scheduled")
            etype = ev.get("type", "")
            time_str = f" at {time}" if time and time != "TBD" else ""
            type_tag = f" [{etype}]" if etype and etype not in ("event", "study") else ""
            lines.append(f"- **{title}** — {date}{time_str}{type_tag} ({status})")
        if len(filtered) > 10:
            lines.append(f"\n...and {len(filtered) - 10} more.")
        reply = "\n".join(lines)

    event_cards = [{
        "type": "events",
        "data": [{"id": e.get("id", ""), "title": e.get("title", ""), "date": e.get("date", ""),
                  "time": e.get("time", ""), "status": e.get("status", "scheduled"),
                  "type": e.get("type", "event")} for e in filtered[:10]],
    }]

    # Generate A2UI calendar view
    from datetime import datetime as _dt
    now_dt = _dt.now()
    cal_events = [{"date": e.get("date", ""), "label": e.get("title", ""), "tone": "success" if e.get("type") == "holiday" else None} for e in filtered[:15]]
    a2ui_doc = {
        "surface": "chat",
        "root": "cal-root",
        "components": [
            {"id": "cal-root", "type": "Card", "props": {"variant": "outlined"}, "children": ["cal-heading", "cal-widget", "cal-table"]},
            {"id": "cal-heading", "type": "Heading", "props": {"text": f"Calendar{label}", "level": 3}},
            {"id": "cal-widget", "type": "Calendar", "props": {"year": now_dt.year, "month": now_dt.month, "events": cal_events}},
            {"id": "cal-table", "type": "Table", "props": {"columns": ["Event", "Date", "Type"], "rows": [[e.get("title", ""), e.get("date", ""), e.get("type", "")] for e in filtered[:10]]}},
        ],
    }

    return {
        "reply": reply,
        "action": "inline_answer",
        "intent": Intent.QUERY,
        "agent_id": "calendar",
        "cards": event_cards,
        "a2ui": a2ui_doc,
    }


async def _handle_learning_query(user_id: str, message: str, ta_id: str | None) -> dict:
    """Handle info-seeking learning queries inline."""
    ta_id = ta_id or "shiksha"
    card = get_agent_card(ta_id) if ta_id else None
    ta_name = card.name if card else ta_id or "Teaching Assistant"

    state = await get_lumen_state(user_id, ta_id) or {}
    ta_state = state.get("current_ta_state", {})

    level = ta_state.get("level", 1)
    label = ta_state.get("label", "beginner")
    sessions = ta_state.get("sessions_completed", 0)
    topics_covered = ta_state.get("topics_covered", [])
    topics_mastered = ta_state.get("topics_mastered", [])
    module = ta_state.get("current_module", "basics")
    pct = ta_state.get("completion_pct", 0)

    msg = message.lower()
    if "what should i learn" in msg or "next" in msg:
        if not topics_covered:
            reply = f"You haven't started with {ta_name} yet — say 'open {ta_id}' to start a session."
        else:
            reply = (f"📚 **{ta_name}** — you're on module '{module}' ({pct}% complete).\n"
                     f"You've mastered {len(topics_mastered)} topics so far.\n\n"
                     f"Say 'open {ta_id}' to start a session.")
    else:
        if sessions == 0:
            reply = f"No sessions with {ta_name} yet. Say 'open {ta_id}' to begin!"
        else:
            covered_str = ", ".join(topics_covered[:5]) if topics_covered else "none yet"
            mastered_str = ", ".join(topics_mastered[:5]) if topics_mastered else "none yet"
            reply = (f"📊 **{ta_name}** — Level {level} ({label})\n"
                     f"- Sessions: {sessions}\n"
                     f"- Module: {module} ({pct}%)\n"
                     f"- Covered: {covered_str}\n"
                     f"- Mastered: {mastered_str}")

    progress_card = {
        "type": "progress",
        "data": {"ta_id": ta_id, "ta_name": ta_name, "level": level, "label": label,
                 "sessions": sessions, "topics_covered": topics_covered,
                 "topics_mastered": topics_mastered, "module": module, "pct": pct},
    }

    return {
        "reply": reply,
        "action": "inline_answer",
        "intent": Intent.QUERY,
        "agent_id": ta_id,
        "cards": [progress_card],
    }


async def _handle_scheduling(user_id: str, message: str = "") -> dict:
    """Handle scheduling intents.

    - "remind me…"/"schedule a…" → create the event immediately.
    - "study plan" / "plan my week" → build a proposal; user confirms Yes/No before scheduling.
    - "cancel/delete/remove event X" → delete event by title match.
    - "postpone X" → delete + reschedule hint.
    - "add holiday" → schedule as holiday type.
    """
    # Prefer Google Calendar if connected (skips for study-plan / holiday flows
    # which are Lumen-specific — those fall through to the original logic below).
    gcal_result = await _handle_google_calendar(user_id, message)
    if gcal_result is not None:
        return gcal_result

    from app.agents.calendar_agent import get_user_events, delete_event as cal_delete
    msg = (message or "").lower()

    # Cancel / delete / remove events — works with any phrasing:
    # "remove Holi", "cancel all events today", "delete the 3pm study", "remove them all"
    cancel_kw = ["cancel ", "delete ", "remove "]
    for ckw in cancel_kw:
        if ckw not in msg:
            continue
        events = await get_user_events(user_id)
        hint = msg.split(ckw, 1)[-1].strip()

        # Strip filler words
        for drop in ("event", "events", "meeting", "meetings", "reminder", "reminders",
                      "the", "my", "all", "from", "on", "in", "calendar"):
            hint = hint.replace(drop, "").strip()

        # Date-based: "today", "tomorrow"
        from datetime import datetime, timedelta
        today_str = datetime.now().strftime("%Y-%m-%d")
        tomorrow_str = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")

        if "today" in msg:
            match = [e for e in events if e.get("date") == today_str and e.get("type") != "holiday"]
        elif "tomorrow" in msg:
            match = [e for e in events if e.get("date") == tomorrow_str and e.get("type") != "holiday"]
        elif hint in ("", "them", "them all", "everything"):
            # "remove them all" / "remove all" — remove all scheduled events
            match = [e for e in events if e.get("status") == "scheduled" and e.get("type") != "holiday"]
        elif hint:
            # Title match — holidays are protected and excluded
            candidates = [e for e in events if hint in e.get("title", "").lower()]
            holiday_hits = [e for e in candidates if e.get("type") == "holiday"]
            match = [e for e in candidates if e.get("type") != "holiday"]
            if holiday_hits and not match:
                titles = ", ".join(e.get("title", "?") for e in holiday_hits)
                return {
                    "reply": f"🎉 **{titles}** is a holiday and can't be removed. Holidays are fixed on the calendar.",
                    "action": "inline_answer",
                    "intent": Intent.SCHEDULING,
                    "agent_id": "calendar",
                }
        else:
            match = []

        if match:
            for m in match:
                await cal_delete(user_id, m["id"])
            titles = ", ".join(m.get("title", "?") for m in match[:5])
            extra = f" ...and {len(match) - 5} more" if len(match) > 5 else ""
            return {
                "reply": f"Removed **{len(match)}** event(s): {titles}{extra}",
                "action": "event_deleted",
                "intent": Intent.SCHEDULING,
                "agent_id": "calendar",
            }
        return {
            "reply": "I couldn't find matching events. Say 'what's on my calendar' to see your events.",
            "action": "inline_answer",
            "intent": Intent.SCHEDULING,
            "agent_id": "calendar",
        }

    # Postpone / reschedule
    if "postpone" in msg or "reschedule" in msg or "push back" in msg or "move " in msg:
        events = await get_user_events(user_id)
        hint = msg
        for drop in ("postpone", "reschedule", "push back", "move", "event", "meeting", "the", "my"):
            hint = hint.replace(drop, "").strip()
        match = [e for e in events if hint and hint in e.get("title", "").lower()] if hint else []
        if match:
            ev = match[0]
            return {
                "reply": (f"To reschedule **{ev['title']}** (currently {ev.get('date', '?')} "
                          f"at {ev.get('time', '?')}), tell me the new date/time.\n"
                          f"E.g. 'schedule {ev['title']} on 2026-05-01 at 3pm'"),
                "action": "inline_answer",
                "intent": Intent.SCHEDULING,
                "agent_id": "calendar",
            }
        return {
            "reply": "Which event do you want to reschedule? Say 'what's on my calendar' to see your events.",
            "action": "inline_answer",
            "intent": Intent.SCHEDULING,
            "agent_id": "calendar",
        }

    # Add holiday
    if "holiday" in msg or "mark as holiday" in msg:
        try:
            result = await parse_and_schedule(user_id, message)
            event = result.get("event", {})
            event["type"] = "holiday"
            return {
                "reply": f"🎉 **Holiday added:** {event.get('title', message)} on {event.get('date', 'TBD')}",
                "action": "event_scheduled",
                "intent": Intent.SCHEDULING,
                "agent_id": "calendar",
                "event": event,
            }
        except Exception:
            pass

    create_kw = ["remind me", "reminder", "set a reminder", "deadline",
                 "exam on", "schedule a", "schedule ", "book ", "add to calendar",
                 "add to my calendar", "put on my calendar", "on my calendar",
                 "add event", "add meeting", "schedule meeting"]
    if any(kw in msg for kw in create_kw):
        try:
            result = await parse_and_schedule(user_id, message)
            event = result.get("event", {})
            title = event.get("title", message)
            date = event.get("date", "TBD")
            time = event.get("time", "TBD")

            # If date/time are TBD, ask the user for details instead of creating a vague event
            if date == "TBD" or time == "TBD":
                return {
                    "reply": (f"I'd like to schedule **{title}** for you. "
                              f"When should it be?\n\n"
                              f"Try: 'schedule {title} on May 5 at 3pm'"),
                    "action": "inline_answer",
                    "intent": Intent.SCHEDULING,
                    "agent_id": "calendar",
                }

            reply_lines = [f"📅 **Added to your calendar:** {title}"]
            when = date + (f" at {time}" if time not in (None, "TBD") else "")
            reply_lines.append(f"- **When:** {when}")
            rmin = event.get("reminder_minutes_before")
            if rmin:
                reply_lines.append(f"- **Reminder:** {rmin} min before + at start")
            reply_lines.append("\nCheck your Calendar tab to view all events.")
            return {
                "reply": "\n".join(reply_lines),
                "action": "event_scheduled",
                "intent": Intent.SCHEDULING,
                "agent_id": "calendar",
                "event": event,
            }
        except Exception as e:
            return {
                "reply": f"I had trouble creating that event ({e}). Try the Calendar tab to add it manually.",
                "action": "scheduling_error",
                "intent": Intent.SCHEDULING,
                "agent_id": "calendar",
            }

    # Study-plan proposal flow
    plan_kw = ["study plan", "plan my", "make a plan", "plan for me", "study schedule",
               "what should i study", "what order", "when should"]
    if any(kw in msg for kw in plan_kw):
        try:
            plan = await generate_study_plan(user_id)
            sessions = plan.get("sessions", [])[:4]
            if not sessions:
                return {
                    "reply": "I can't build a plan yet — start a session with one of the TAs first so I know where you are.",
                    "action": "study_plan_empty",
                    "intent": Intent.SCHEDULING,
                    "agent_id": "calendar",
                }

            from datetime import datetime, timedelta, timezone as _tz
            UTC = _tz.utc
            prefs = get_prefs(user_id)
            remind = prefs.get("reminder_minutes_before", 15)
            base = datetime.now(UTC) + timedelta(days=1)
            base = base.replace(hour=17, minute=0, second=0, microsecond=0)
            proposal = []
            for i, s in enumerate(sessions):
                when = base + timedelta(days=i)
                proposal.append({
                    "title": f"Study: {s.get('topic', 'session')}",
                    "date": when.strftime("%Y-%m-%d"),
                    "time": "17:00",
                    "duration_mins": 60,
                    "type": "study",
                    "ta_id": s.get("ta_id"),
                    "description": f"Focus on {s.get('topic','')}. Priority: {s.get('priority','normal')}.",
                    "reminder_minutes_before": remind,
                })

            reply_lines = ["Here's a plan based on where you are — want me to add these to your calendar?\n"]
            for p in proposal:
                reply_lines.append(f"- **{p['title']}** — {p['date']} at {p['time']} (reminds {remind}m before)")
            reply_lines.append("\nReply **yes** to schedule them, or **no** to discard.")
            result = {
                "reply": "\n".join(reply_lines),
                "action": "study_plan_proposal",
                "intent": Intent.SCHEDULING,
                "agent_id": "calendar",
                "proposal": proposal,
            }
            _pending_proposals[user_id] = proposal
            return result
        except Exception as e:
            logger.warning(f"Study plan proposal failed: {e}")
            return {
                "reply": f"I couldn't draft a plan right now ({e}).",
                "action": "study_plan_error",
                "intent": Intent.SCHEDULING,
                "agent_id": "calendar",
            }

    # Generic scheduling query — suggest actions
    return {
        "reply": "I can help with your calendar! Try:\n- 'remind me...' to set a reminder\n- 'study plan' to generate a plan\n- 'what's on my calendar' to see events\n- 'open calendar' to launch the full calendar",
        "action": "inline_answer",
        "intent": Intent.SCHEDULING,
        "agent_id": "calendar",
    }


async def confirm_study_plan(user_id: str, proposal: list[dict]) -> dict:
    """Schedule all events from an approved study-plan proposal."""
    created = []
    for p in proposal or []:
        try:
            ev = await schedule_event(
                user_id=user_id,
                title=p.get("title", "Study session"),
                event_type=p.get("type", "study"),
                date=p.get("date"),
                time=p.get("time"),
                duration_mins=p.get("duration_mins", 60),
                description=p.get("description", ""),
                ta_id=p.get("ta_id"),
                reminder_minutes_before=p.get("reminder_minutes_before"),
            )
            created.append(ev)
        except Exception as e:
            logger.warning(f"schedule_event failed for proposal item: {e}")
    return {"scheduled": created, "count": len(created)}


def _find_peer_by_name(all_lumens: list[dict], user_id: str, name_hint: str) -> dict | None:
    """Resolve a free-text name reference (e.g. "Priya", "priya s") to a peer lumen.

    Matches are case-insensitive and prefer, in order:
      1. exact full-name match
      2. exact first-name match
      3. unique substring match on the full name
    Returns None if no unambiguous match is found.
    """
    hint = (name_hint or "").strip().lower().rstrip(".,!?:;")
    if not hint:
        return None

    candidates = [
        l for l in all_lumens
        if l.get("id") != user_id and l.get("social", {}).get("discoverable", True)
    ]

    exact_full = [l for l in candidates if l.get("name", "").strip().lower() == hint]
    if len(exact_full) == 1:
        return exact_full[0]

    exact_first = [
        l for l in candidates
        if l.get("name", "").strip().split(" ", 1)[0].lower() == hint
    ]
    if len(exact_first) == 1:
        return exact_first[0]

    substring = [l for l in candidates if hint in l.get("name", "").strip().lower()]
    if len(substring) == 1:
        return substring[0]

    return None


async def _handle_social(user_id: str, message: str) -> dict:
    """Handle social queries by fetching actual peer/group data."""
    import re
    from app.routes.lumen_social import (
        get_all_lumens_full, _anonymize_peer, _study_groups,
        _progress_summary, _find_common_topics, _collaboration_suggestions,
        build_lumen_card,
    )
    from app.lumen.core import get_lumen
    from datetime import datetime, timezone as _tz
    UTC = _tz.utc

    my_lumen = await get_lumen(user_id)
    msg = message.lower()

    # Study group creation
    if "create" in msg and "group" in msg:
        return {
            "reply": "To create a study group, use the Study Groups panel in the sidebar, or I can create one for you. What subject should the group focus on?",
            "action": "social",
            "intent": Intent.SOCIAL,
            "agent_id": None,
        }

    all_lumens = await get_all_lumens_full()

    # ── Message a peer by name: various patterns ──────
    # Pattern 1: "message <name>: <body>" or "send to <name>: <body>"
    msg_match = re.match(r"\s*(?:message|msg|dm|send)\s+(?:to\s+)?([^:]+?)\s*[:\-]\s*(.+)",
                         message, re.IGNORECASE | re.DOTALL)
    # Pattern 2: "send message to <name> saying <body>"
    if not msg_match:
        msg_match = re.match(r"\s*(?:send\s+(?:a\s+)?message\s+to|message|msg|dm)\s+(\w[\w\s]*?)\s+(?:saying|that|about)\s+(.+)",
                             message, re.IGNORECASE | re.DOTALL)
    # Pattern 3: "send message to <name>" (no body — we'll ask)
    no_body_match = None
    if not msg_match:
        no_body_match = re.match(r"\s*(?:send\s+(?:a\s+)?message\s+to|message|msg|dm)\s+(\w[\w\s]+?)\.?\s*$",
                                 message, re.IGNORECASE)

    if no_body_match:
        name_hint = no_body_match.group(1).strip()
        peer = _find_peer_by_name(all_lumens, user_id, name_hint)
        if peer:
            peer_name = peer.get('name', name_hint) or name_hint
            peer_first = peer_name.split()[0] if peer_name.strip() else peer_name
            return {
                "reply": f"What message would you like me to send to **{peer_name}**?\n\nSay: *message {peer_first}: your message here*",
                "action": "social", "intent": Intent.SOCIAL, "agent_id": None,
            }

    if msg_match:
        name_hint, body_text = msg_match.group(1).strip(), msg_match.group(2).strip()
        peer = _find_peer_by_name(all_lumens, user_id, name_hint)
        if not peer:
            return {
                "reply": f"I couldn't find a peer named '{name_hint}'. Say 'show my peers' to see who's on the network.",
                "action": "social", "intent": Intent.SOCIAL, "agent_id": None,
            }
        # Deliver the message AND synchronously fetch the peer Lumen's reply, so the
        # conversation is interactive inline (no static "delivered" dead-end). The peer's
        # Lumen answers on their behalf from their public profile. We persist both the
        # outgoing message and the reply so the Peers thread stays in sync.
        from app.routes.lumen_social import (
            _persist_peer_message, _hydrate_peer_messages,
            _peer_lumen_autoreply, _peer_messages, _lumen_msg,
        )
        sender_name = (my_lumen or {}).get("name", "Student")
        peer_name = peer.get("name", name_hint) or name_hint
        peer_first = peer_name.split()[0] if peer_name.strip() else peer_name
        try:
            await _hydrate_peer_messages(user_id)
            out_msg = _lumen_msg(user_id, sender_name, peer["id"], peer_name, body_text)
            await _persist_peer_message(out_msg)
            conversation = [
                m for m in _peer_messages
                if (m.get("from_id") == user_id and m.get("to_id") == peer["id"])
                or (m.get("from_id") == peer["id"] and m.get("to_id") == user_id)
            ]
            reply = await _peer_lumen_autoreply(
                sender_id=user_id, sender_name=sender_name, peer=peer,
                incoming_message=body_text, conversation_history=conversation,
            )
            reply_text = (reply or {}).get("message", "") if isinstance(reply, dict) else ""
        except Exception as e:
            logger.warning(f"Peer messaging failed: {e}")
            return {
                "reply": f"I couldn't deliver the message to {peer_name} ({e}).",
                "action": "social", "intent": Intent.SOCIAL, "agent_id": None,
            }
        if reply_text:
            chat_reply = (
                f"✉️ Sent to **{peer_name}**.\n\n"
                f"💬 **{peer_first}'s Lumen replies:**\n\n{reply_text}"
            )
        else:
            chat_reply = f"✉️ Delivered your message to **{peer_name}**'s Lumen."
        return _ensure_intent({
            "reply": chat_reply,
            "action": "social",
            "peer_id": peer["id"],
            "peer_lumen_id": peer.get("lumen_id"),
            "protocol": "litp/1.0",
        }, Intent.SOCIAL, None)

    # ── Compare with a peer by name: "compare with <name>" ────
    cmp_match = re.match(r"\s*compare\s+(?:with|to|against)\s+(.+)",
                         message, re.IGNORECASE)
    if cmp_match:
        name_hint = cmp_match.group(1).strip().rstrip(".!?")
        peer = _find_peer_by_name(all_lumens, user_id, name_hint)
        if not peer:
            return {
                "reply": f"I couldn't find a peer named '{name_hint}'. Say 'show my peers' to see who's on the network.",
                "action": "social", "intent": Intent.SOCIAL, "agent_id": None,
            }
        if not my_lumen:
            return {
                "reply": "I don't have your progress yet — try learning something first!",
                "action": "social", "intent": Intent.SOCIAL, "agent_id": None,
            }
        my_sum = _progress_summary(my_lumen)
        their_sum = _progress_summary(peer, anonymize=True)
        common = _find_common_topics(my_lumen, peer)
        suggestions = _collaboration_suggestions(my_lumen, peer)

        lines = [f"📊 Comparing you with {peer.get('name', 'peer')}:\n"]
        lines.append(f"  You:  {len(my_sum['tcs_mastered'])} mastered, "
                     f"{len(my_sum['tcs_in_progress'])} in progress")
        lines.append(f"  Them: {len(their_sum['tcs_mastered'])} mastered, "
                     f"{len(their_sum['tcs_in_progress'])} in progress")
        if common:
            lines.append(f"\nCommon topics: {', '.join(common[:5])}")
        if suggestions:
            lines.append("\n" + "\n".join(f"  • {s}" for s in suggestions))
        return {
            "reply": "\n".join(lines),
            "action": "social", "intent": Intent.SOCIAL, "agent_id": None,
            "peer_id": peer["id"],
            "comparison": {"you": my_sum, "peer": their_sum,
                           "common_topics": common, "suggestions": suggestions},
        }

    # Peer discovery — filter: same tenant + not demo
    my_tenant = ""
    if my_lumen:
        my_lid = my_lumen.get("lumen_id", "")
        # lumen_id format: lumen://<tenant>/<user_id>
        parts = my_lid.replace("lumen://", "").split("/")
        my_tenant = parts[0] if parts else ""

    def _is_demo(l):
        if l.get("org") == "demo": return True
        if (l.get("email") or "").endswith("@demo.local"): return True
        lid = l.get("id") or ""
        if lid.startswith("peer-") or lid == "demo-guest": return True
        return False

    def _same_network(l):
        """Only show peers from the same tenant/org."""
        if not my_tenant or my_tenant == "default":
            return True  # If no tenant info, show all non-demo
        peer_lid = l.get("lumen_id", "")
        peer_tenant = peer_lid.replace("lumen://", "").split("/")[0] if "lumen://" in peer_lid else ""
        return peer_tenant == my_tenant or peer_tenant == "default"

    peers = [
        _anonymize_peer(l) for l in all_lumens
        if l["id"] != user_id
        and not _is_demo(l)
        and _same_network(l)
        and l.get("social", {}).get("discoverable", True)
    ]

    # Groups
    my_groups = [g for g in _study_groups.values() if user_id in g["members"]]
    open_groups = [g for g in _study_groups.values() if user_id not in g["members"] and len(g["members"]) < g["max_members"]]

    lines = []
    if peers:
        lines.append(f"Found {len(peers)} peer(s) on your network:\n")
        for p in peers[:5]:
            subjects = ", ".join(s["ta_name"] + f" (L{s['level']})" for s in p.get("active_subjects", []))
            lines.append(f"  **{p['name']}**")
            lines.append(f"    {p['total_sessions']} sessions, {p['tcs_mastered']} concepts mastered")
            if subjects:
                lines.append(f"    Studying: {subjects}")
        lines.append(f"\nTo compare: 'compare with [name]'")
        lines.append(f"To message: 'message [name]: hi!'")
    else:
        lines.append("No other students on your network yet. As more people join, you'll see peers here.")

    if my_groups:
        lines.append(f"\nYour study groups: {', '.join(g['name'] for g in my_groups)}")
    if open_groups:
        lines.append(f"Open groups to join: {', '.join(g['name'] for g in open_groups)}")

    peer_cards = [{
        "type": "peers",
        "data": [{"id": p.get("id", ""), "name": p.get("name", ""),
                  "sessions": p.get("total_sessions", 0),
                  "tcs_mastered": p.get("tcs_mastered", 0),
                  "subjects": [s["ta_name"] for s in p.get("active_subjects", [])]}
                 for p in peers[:5]],
    }]

    return {
        "reply": "\n".join(lines),
        "action": "social",
        "intent": Intent.SOCIAL,
        "agent_id": None,
        "peers": peers[:5],
        "my_groups": my_groups,
        "open_groups": open_groups,
        "cards": peer_cards,
    }


def _is_profile_query(message: str) -> bool:
    """Detect 'show my profile' style queries handled by _handle_profile."""
    msg = (message or "").lower().strip()
    pats = [
        "my profile", "show my profile", "what's my profile", "whats my profile",
        "view my profile", "my bio", "about me", "who am i", "my account",
        "my details", "my info", "profile info", "my lumen profile",
        "what do you know about me",
    ]
    return any(p in msg for p in pats)


async def _handle_profile(user_id: str, user_info: dict) -> dict:
    """Answer 'show my profile' from chat — name, contact, bio, connected
    integrations, and a quick learning snapshot. Owner-only (the requester's
    own Lumen), so private fields are fine to surface."""
    from app.lumen.core import get_lumen
    from app.agents.gmail_agent import is_gmail_connected, is_drive_connected

    lumen = await get_lumen(user_id) or {}
    name = lumen.get("name") or user_info.get("name") or "You"
    email = user_info.get("email") or lumen.get("email") or ""

    lines = [f"👤 **{name}**"]
    if email:
        lines.append(f"📧 {email}")
    if lumen.get("occupation"):
        lines.append(f"💼 {lumen['occupation']}")
    if lumen.get("bio"):
        lines.append(f"\n_{lumen['bio']}_")

    detail_bits = []
    if lumen.get("expertise"):
        detail_bits.append(f"**Good at:** {lumen['expertise']}")
    if lumen.get("interests"):
        detail_bits.append(f"**Interests:** {lumen['interests']}")
    if detail_bits:
        lines.append("\n" + "  •  ".join(detail_bits))

    # Connected integrations
    connections = []
    if lumen.get("github"):
        connections.append("GitHub")
    if is_gmail_connected(lumen):
        connections.append("Gmail")
    if is_drive_connected(lumen):
        connections.append("Drive")
    if lumen.get("notion"):
        connections.append("Notion")
    lines.append(
        f"\n🔗 **Connected:** {', '.join(connections) if connections else 'nothing yet — connect GitHub, Google, or Notion from your profile'}"
    )

    # Learning snapshot
    try:
        from app.agents import shiksha_agent as _shiksha
        courses = await _shiksha.get_user_progress(user_id)
        if courses:
            names = ", ".join(c.get("name", c.get("agent_id", "")) for c in courses[:4])
            lines.append(f"\n🎓 **Courses:** {names}")
    except Exception:
        pass

    return {
        "reply": "\n".join(lines),
        "action": "profile",
        "intent": Intent.GENERAL,
        "agent_id": "lumen",
        "cards": [{
            "type": "profile",
            "data": {
                "name": name,
                "email": email,
                "occupation": lumen.get("occupation", ""),
                "bio": lumen.get("bio", ""),
                "expertise": lumen.get("expertise", ""),
                "interests": lumen.get("interests", ""),
                "connections": connections,
            },
        }],
    }


async def _handle_lumen(user_id: str, message: str,
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


# ── Communication Handler ────────────────────────────────────

_pending_drafts: dict[str, dict] = {}
_pending_proposals: dict[str, list] = {}  # user_id → pending study-plan proposal

# Short-term conversation context per user: tracks last intent + awaiting state
# so follow-up messages are understood correctly
_user_context: dict[str, dict] = {}


def _get_ctx(user_id: str) -> dict:
    return _user_context.get(user_id, {})


def _set_ctx(user_id: str, **kwargs) -> None:
    ctx = _user_context.get(user_id, {})
    ctx.update(kwargs)
    _user_context[user_id] = ctx


def _clear_ctx(user_id: str) -> None:
    _user_context.pop(user_id, None)


def get_pending_draft(user_id: str) -> dict | None:
    return _pending_drafts.get(user_id)

def clear_pending_draft(user_id: str) -> None:
    _pending_drafts.pop(user_id, None)

async def _handle_communication(user_id: str, message: str, user_info: dict) -> dict:
    """Handle communication intents — send/check email, Teams messages.

    Routing by account type:
    - email-* / google-* / ext-*  →  IMAP/SMTP with app password (preferred)
    - Entra ID org accounts       →  WorkIQ MCP / Microsoft Graph (tenant-permitting)
    """
    from app.agents.communication_agent import (
        compose_draft, send_simulated, check_inbox, check_outbox,
    )
    from app.lumen.core import get_lumen
    msg = message.lower()

    # Account type detection — drives which send/read path we use
    uid = user_info.get("id", user_id)
    is_entra = not (uid.startswith("email-") or uid.startswith("google-") or uid.startswith("ext-"))

    # Lookup user's lumen doc (used for Gmail/Drive connection checks below)
    lumen = await get_lumen(user_id)
    has_email = False  # IMAP removed — always False
    is_google = uid.startswith("google-")

    from app.agents.gmail_agent import is_gmail_connected as _is_gmail_connected

    # Google accounts use Gmail exclusively — they must never fall through to the
    # Outlook / Chrome-extension path. If Gmail access hasn't been granted yet,
    # prompt the in-chat consent card (Allow once / Always allow) right away.
    if is_google and not _is_gmail_connected(lumen):
        return _google_consent_response("Gmail", message, Intent.COMMUNICATION, "communication")

    # If user has Gmail OAuth connected, route READ-only inbox/search/summarize through Gmail API.
    # Compose / send keeps falling through to compose_draft (which then ships via /lumen/comm/send-real
    # → gmail_agent.send_gmail for Google users).
    if _is_gmail_connected(lumen):
        import re as _re_g_route
        # FIRST: detect SEND patterns and skip Gmail-read routing entirely.
        # "send a mail to X", "email alice about lunch", "write to bob", "reply to manager", etc.
        _gmail_send_pat = _re_g_route.compile(
            r"\b(send|sending|email|mail|write|writing|compose|composing|draft|drafting|"
            r"reply|replying|respond|responding|notify|notifying|forward|forwarding)\b"
            r"\s+(?:a\s+|an\s+|the\s+)?(?:mail|email|message|note|reply|response)?"
            r".*?\b(to|@)\b",
            _re_g_route.IGNORECASE,
        )
        # Also: any message containing an explicit email address AND a send-verb
        _has_email_in_msg = bool(_re_g_route.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", msg))
        _has_send_verb = bool(_re_g_route.search(
            r"\b(send|sending|reply|replying|respond|notify|write\s+to|email\s+\w|mail\s+\w|"
            r"draft|compose|forward)\b", msg))
        if _gmail_send_pat.search(msg) or (_has_email_in_msg and _has_send_verb):
            # SEND pattern — fall through to compose_draft, do NOT route to Gmail read.
            pass
        else:
            # READ-flavored email phrasing — typo-tolerant via regex.
            _gmail_view_pat = _re_g_route.compile(
                r"\b(show|list|check|find|get|give|fetch|read|display|pull|see|view|grab|open|"
                r"summari[sz]e|search|any|latest|recent|unread|new|what)\b"
                r".*?\b(mails?|emails?|inbox|messages?|gmail)\b",
                _re_g_route.IGNORECASE,
            )
            # "mail from X", "emails about Y" — read patterns (NOT "mail to X" which is send)
            _gmail_from_pat = _re_g_route.compile(
                r"\b(emails?|mails?|messages?)\b\s+(?:from|about|by|regarding)\s+",
                _re_g_route.IGNORECASE,
            )
            _gmail_short_kw = [
                "my inbox", "my mail", "my mails", "my emails", "my gmail",
                "any replies", "any new email", "what mail", "what email",
            ]
            if (_gmail_view_pat.search(msg) or _gmail_from_pat.search(msg) or
                    any(kw in msg for kw in _gmail_short_kw)):
                return await _handle_gmail(user_id, message)

    # ── Connect email flow ── (IMAP removed)
    # Lumen now uses Gmail API (Google users) or the Chrome extension (Outlook users).
    # No IMAP/app-password setup needed.
    connect_kw = ["connect my email", "connect outlook", "set up email", "setup email",
                  "set up outlook", "setup outlook", "link my email", "link outlook",
                  "configure email", "add my email"]
    if any(kw in msg for kw in connect_kw):
        if _is_gmail_connected(lumen):
            return {
                "reply": "✓ Your Google account is already connected — Lumen handles your Gmail directly.",
                "action": "inline_answer",
                "intent": Intent.COMMUNICATION,
                "agent_id": "communication",
            }
        if is_entra:
            return {
                "reply": (
                    "📧 For Outlook: install the **Lumen for Outlook** Chrome extension and "
                    "keep Outlook Web open in another tab. Lumen sends/reads through your "
                    "authenticated Outlook session — no admin consent needed.\n\n"
                    "For Gmail instead: open Profile → **Connect Google (Drive + Gmail)**."
                ),
                "action": "inline_answer",
                "intent": Intent.COMMUNICATION,
                "agent_id": "communication",
            }
        return _google_consent_response("Gmail", message, Intent.COMMUNICATION, "communication")

    # ── Disconnect email ──
    if any(kw in msg for kw in ["disconnect my email", "disconnect outlook", "remove my email"]):
        if _is_gmail_connected(lumen):
            return {
                "reply": "To disconnect Gmail, open **Profile** → **Disconnect Google**.",
                "action": "inline_answer",
                "intent": Intent.COMMUNICATION,
                "agent_id": "communication",
            }
        return {
            "reply": "You don't have an email connected yet.",
            "action": "inline_answer",
            "intent": Intent.COMMUNICATION,
            "agent_id": "communication",
        }

    # ── Sent mail listing ──
    # "what emails did i send today", "what did i send", "my sent emails", "recent sent mails"
    sent_kw = [
        "what did i send", "what emails did i send", "what mails did i send",
        "what have i sent", "emails i've sent", "mails i've sent",
        "emails i sent", "mails i sent", "my sent", "sent today",
        "sent emails", "sent mails", "recent sent", "sent items",
        "outbox", "what did i email",
    ]
    if any(kw in msg for kw in sent_kw):
        # Gmail-connected users: pull from Gmail's Sent folder (authoritative)
        if _is_gmail_connected(lumen):
            from app.agents.gmail_agent import get_valid_google_token as _gvt, search_gmail as _gmail_search
            token = await _gvt(user_id)
            if token:
                sent_msgs = await _gmail_search(token, "in:sent", limit=10)
                if not sent_msgs:
                    return {
                        "reply": "📤 No sent messages in your Gmail.",
                        "action": "inline_answer",
                        "intent": Intent.COMMUNICATION,
                        "agent_id": "communication",
                    }
                lines = [f"📤 **{len(sent_msgs)} recent sent message(s):**\n"]
                for m in sent_msgs[:5]:
                    lines.append(f"- **{m.get('subject', '(no subject)')}** — to {m.get('to', '?')}")
                return {
                    "reply": "\n".join(lines),
                    "action": "gmail_sent",
                    "intent": Intent.COMMUNICATION,
                    "agent_id": "communication",
                    "cards": [{"type": "gmail_inbox", "data": sent_msgs[:10]}],
                }
        # Otherwise fall back to the in-memory outbox (logs sends across all providers)
        outbox = check_outbox(user_id)
        if not outbox:
            return {
                "reply": "📤 No sent messages logged in Lumen yet.",
                "action": "inline_answer",
                "intent": Intent.COMMUNICATION,
                "agent_id": "communication",
            }
        lines = [f"📤 **{len(outbox)} recently sent message(s):**\n"]
        for m in outbox[-5:][::-1]:
            lines.append(f"- **{m.get('subject', '(no subject)')}** — to {m.get('to_email', m.get('to', '?'))}")
        return {
            "reply": "\n".join(lines),
            "action": "inline_answer",
            "intent": Intent.COMMUNICATION,
            "agent_id": "communication",
        }

    # ── Search emails ──
    # Match: "search my email for X", "find emails about X", "any email from X",
    # "find mails related to X", "find emails X" (bare query)
    import re as _re
    search_match = _re.search(
        r"(?:search|find|look for|any).*?(?:emails?|inbox|mails?).*?"
        r"(?:about|for|from|with|on|containing|regarding|related\s+to|related|to)\s+(.+?)(?:\?|\.|$)",
        msg,
    )
    # Fallback: "find mails X" / "search emails X" — anything trailing after the noun is the query
    if not search_match:
        search_match = _re.search(
            r"(?:search|find|look\s+for)\s+(?:my\s+)?(?:emails?|inbox|mails?)\s+(.+?)(?:\?|\.|$)",
            msg,
        )
    if search_match:
        query = search_match.group(1).strip().strip('"\'')
        # Detect "from X" pattern → prefix with "from:"
        from_match = _re.search(r"^(?:from|by)\s+(.+?)$", query)
        if from_match:
            query = "from:" + from_match.group(1).strip()

        # Google accounts → Gmail search (defensive; usually handled above).
        if is_google:
            return await _handle_gmail(user_id, message)
        # IMAP removed — Gmail-connected users were already handled above
        # (gmail-read branch). Otherwise → Chrome extension scrape of Outlook.
        return {
            "reply": f"🔍 Searching Outlook for *{query}*…",
            "action": "outlook_search",
            "intent": Intent.COMMUNICATION,
            "agent_id": "communication",
            "cards": [{
                "type": "outlook_search",
                "data": {"query": query},
            }],
        }

    # Check inbox / replies — broad coverage of natural phrasings
    check_kw = ["check email", "check my email", "check inbox", "check my inbox",
                "any replies", "any new email", "any messages", "new emails", "unread emails",
                "recent mail", "recent email", "recent emails", "recent mails",
                "my mail", "my mails", "my emails", "my inbox",
                "what mail", "what email", "what mails", "what emails",
                "show my mail", "show my email", "show emails", "show me my email",
                "list my email", "list emails", "latest email", "latest mail"]
    if any(kw in msg for kw in check_kw):
        # Google accounts → Gmail inbox (defensive; usually handled above).
        if is_google:
            return await _handle_gmail(user_id, message)
        # Gmail-connected users already handled by the gmail-read branch above.
        # Everyone else → Chrome extension scrape of Outlook (frontend prompts to
        # install if it isn't present).
        return {
            "reply": "📬 Pulling your recent Outlook emails…",
            "action": "outlook_search",
            "intent": Intent.COMMUNICATION,
            "agent_id": "communication",
            "cards": [{
                "type": "outlook_search",
                "data": {"query": "", "mode": "recent"},
            }],
        }

        # (legacy Entra→Graph path retained below as fallback for reference only;
        # it's now unreachable because the return above ends this branch.)
        if is_entra and not has_email:
            from app.agents.graph_token_manager import get_graph_access_token
            token = await get_graph_access_token()
            if not token:
                return {
                    "reply": (
                        "📬 To read your Microsoft mailbox I need a Graph token. "
                        "Your frontend should auto-seed one after sign-in — try refreshing the page.\n\n"
                        "If your tenant blocks Mail.Read, you'll see an \"admin access required\" "
                        "error. In that case, use a personal email account instead."
                    ),
                    "action": "inline_answer",
                    "intent": Intent.COMMUNICATION,
                    "agent_id": "communication",
                }
            from app.agents.graph_mail import list_inbox as graph_list_inbox
            unread_only = any(kw in msg for kw in ["unread", "new email"])
            emails = await graph_list_inbox(token, filter_unread=unread_only, limit=10)
            if not emails:
                return {
                    "reply": (
                        f"📬 No {'unread ' if unread_only else ''}messages found, "
                        f"or your tenant blocked the Mail.Read call. Check Lumen logs for details."
                    ),
                    "action": "inline_answer",
                    "intent": Intent.COMMUNICATION,
                    "agent_id": "communication",
                }
            lines = [f"📬 **{len(emails)} {'unread ' if unread_only else ''}message(s)** (via Microsoft Graph):\n"]
            for m in emails[:5]:
                lines.append(f"- **{m.get('subject', 'No subject')}** — from {m.get('from', '?')}")
            return {
                "reply": "\n".join(lines),
                "action": "inline_answer",
                "intent": Intent.COMMUNICATION,
                "agent_id": "communication",
                "cards": [{"type": "inbox", "data": emails[:5]}],
            }

        # Fall back to simulated inbox (or prompt to connect)
        inbox = check_inbox(user_id)
        if not inbox and not has_email:
            return {
                "reply": (
                    "📬 Your inbox is empty in Lumen, and your real email isn't connected yet.\n\n"
                    "Say **connect my email** to link your Outlook account so I can read your real inbox."
                ),
                "action": "inline_answer",
                "intent": Intent.COMMUNICATION,
                "agent_id": "communication",
            }
        if not inbox:
            return {
                "reply": "📬 No new messages in your inbox.",
                "action": "inline_answer",
                "intent": Intent.COMMUNICATION,
                "agent_id": "communication",
            }
        lines = [f"📬 **You have {len(inbox)} message(s):**\n"]
        for m in inbox[:5]:
            status = "🔵 unread" if m["status"] == "unread" else "✓ read"
            lines.append(f"- **{m.get('subject', 'No subject')}** from {m.get('from', '?')} ({status})")
        return {
            "reply": "\n".join(lines),
            "action": "inline_answer",
            "intent": Intent.COMMUNICATION,
            "agent_id": "communication",
            "cards": [{"type": "inbox", "data": inbox[:5]}],
        }

    # "Did X reply?"
    import re
    reply_match = re.match(r".*(?:did|has)\s+(\w+)\s+(?:reply|respond|answer|get back)", msg)
    if reply_match:
        name_hint = reply_match.group(1)
        inbox = check_inbox(user_id, from_filter=name_hint)
        if inbox:
            latest = inbox[0]
            return {
                "reply": f"Yes! **{latest.get('from', '?')}** replied:\n\n> {latest.get('body', '')}",
                "action": "inline_answer",
                "intent": Intent.COMMUNICATION,
                "agent_id": "communication",
            }
        return {
            "reply": f"No reply from {name_hint} yet. I'll keep checking.",
            "action": "inline_answer",
            "intent": Intent.COMMUNICATION,
            "agent_id": "communication",
        }

    # Check if message has enough info to compose (needs a recipient at minimum)
    vague_kw = ["send an email", "send email", "send a mail", "write an email",
                "compose email", "draft email", "send a message"]
    is_vague = any(msg.strip() == kw or msg.strip() == kw + "." for kw in vague_kw)
    # Also vague if no "to" indicator
    has_recipient = any(w in msg for w in [" to ", " for "])
    if is_vague or (not has_recipient and not any(kw in msg for kw in check_kw)):
        # Save context so next message is treated as email details
        _set_ctx(user_id, awaiting="comm_info", comm_partial="send an email")
        return {
            "reply": "I can help you send an email! Who should I send it to, and what should it be about?\n\nExample: *send an email to Manohar about the project update*",
            "action": "inline_answer",
            "intent": Intent.COMMUNICATION,
            "agent_id": "communication",
        }

    # Compose draft (do NOT auto-send — return for user confirmation)
    user_name = user_info.get("name", "Student")
    user_email = user_info.get("email", "")
    draft = await compose_draft(user_id, user_name, message, user_email=user_email)

    # Stash draft in memory so the user can say "send it" to confirm
    _pending_drafts[user_id] = draft

    to_display = draft.get("to", "")
    to_email = draft.get("to_email", "")
    if not to_email and to_display:
        # Try to resolve email from peers (with self-fallback). Use candidates for disambiguation.
        from app.agents.communication_agent import _resolve_email_candidates
        candidates = await _resolve_email_candidates(to_display, self_email=user_email, self_name=user_name)

        if len(candidates) == 1:
            best = candidates[0]
            draft["to_email"] = best["email"]
            to_email = best["email"]
            if best["email"] == user_email:
                draft["to"] = "Me"
                to_display = "Me"
        elif len(candidates) > 1:
            # Multiple matches — ask user to clarify, save draft + candidates as pending
            _pending_drafts[user_id] = draft
            _set_ctx(
                user_id,
                awaiting="disambiguate_recipient",
                disambiguate_candidates=[
                    {"name": c["name"], "email": c["email"]} for c in candidates[:5]
                ],
            )
            options = "\n".join(
                f"- **{c['name']}** <{c['email']}>" for c in candidates[:5]
            )
            return {
                "reply": (
                    f"I found multiple people matching **{to_display}**:\n\n{options}\n\n"
                    f"Reply with the full name (or paste the email) to pick one."
                ),
                "action": "comm_disambiguate",
                "intent": Intent.COMMUNICATION,
                "agent_id": "communication",
            }

    return {
        "reply": (
            f"📝 Drafted an email to **{to_display}** — review below, then say *send* "
            f"or edit / refine (e.g. *make it shorter*, *more formal*)."
        ),
        "action": "comm_draft",
        "intent": Intent.COMMUNICATION,
        "agent_id": "communication",
        "cards": [{
            "type": "email_draft",
            "data": {
                "id": draft.get("id"),
                "to": draft.get("to", ""),
                "to_email": draft.get("to_email", ""),
                "subject": draft.get("subject", ""),
                "body": draft.get("body", ""),
            },
        }],
    }


# ── Outlook (Graph) handler ───────────────────────────────────────────────────

async def _handle_outlook(message: str, graph_token: str | None) -> dict:
    """Handle Outlook read queries via Microsoft Graph."""
    NO_TOKEN_MSG = (
        "📧 To read your Outlook, connect your Microsoft account first — "
        "open your **Profile** (top-right) and click **Connect Microsoft Account**."
    )
    if not graph_token:
        return {"reply": NO_TOKEN_MSG, "action": "inline_answer", "intent": Intent.OUTLOOK, "agent_id": "outlook"}

    from app.agents.graph_agent import (
        get_high_importance_mail, get_mail_from_address, search_mail,
        get_mail_delta, get_inbox_rules, get_outlook_categories,
        get_email_headers, get_conference_rooms,
        extract_email_address, extract_search_query,
    )

    msg = message.lower().strip()

    # Conference rooms
    if any(kw in msg for kw in ["conference room", "meeting room", "list rooms", "available rooms"]):
        reply = await get_conference_rooms(graph_token)
        return {"reply": reply, "action": "inline_answer", "intent": Intent.OUTLOOK, "agent_id": "outlook"}

    # Inbox rules
    if any(kw in msg for kw in ["inbox rules", "my rules", "mail rules", "email rules"]):
        reply = await get_inbox_rules(graph_token)
        return {"reply": reply, "action": "inline_answer", "intent": Intent.OUTLOOK, "agent_id": "outlook"}

    # Outlook categories
    if any(kw in msg for kw in ["categories", "outlook categories", "email categories"]):
        reply = await get_outlook_categories(graph_token)
        return {"reply": reply, "action": "inline_answer", "intent": Intent.OUTLOOK, "agent_id": "outlook"}

    # Email headers
    if any(kw in msg for kw in ["email headers", "mail headers", "message headers"]):
        reply = "To show email headers, I need the message ID. Try: 'email headers for <message-id>'"
        return {"reply": reply, "action": "inline_answer", "intent": Intent.OUTLOOK, "agent_id": "outlook"}

    # High importance
    if any(kw in msg for kw in ["high importance", "important mail", "important email", "high priority"]):
        reply = await get_high_importance_mail(graph_token)
        return {"reply": reply, "action": "inline_answer", "intent": Intent.OUTLOOK, "agent_id": "outlook"}

    # Mail from address
    addr = extract_email_address(message)
    if addr or any(kw in msg for kw in ["mail from ", "email from ", "emails from ", "mails from "]):
        if not addr:
            # Try to extract name-based query — fall back to search
            query = extract_search_query(msg, ["mail from", "email from", "emails from", "mails from"])
            reply = await search_mail(graph_token, query) if query else await get_mail_delta(graph_token)
        else:
            reply = await get_mail_from_address(graph_token, addr)
        return {"reply": reply, "action": "inline_answer", "intent": Intent.OUTLOOK, "agent_id": "outlook"}

    # Search mail by keyword
    if any(kw in msg for kw in ["search mail", "search email", "find mail", "find email", "emails about", "mails about", "emails containing", "mails containing"]):
        query = extract_search_query(msg, ["search mail", "search email", "find mail", "find email",
                                            "emails about", "mails about", "emails containing", "mails containing"])
        if query:
            reply = await search_mail(graph_token, query)
        else:
            reply = "What would you like to search for? E.g. 'search my email for project update'"
        return {"reply": reply, "action": "inline_answer", "intent": Intent.OUTLOOK, "agent_id": "outlook"}

    # Default: unread / recent changes
    reply = await get_mail_delta(graph_token)
    return {"reply": reply, "action": "inline_answer", "intent": Intent.OUTLOOK, "agent_id": "outlook"}


# ── OneDrive (Graph) handler ──────────────────────────────────────────────────

async def _handle_onedrive(message: str, graph_token: str | None) -> dict:
    """Handle OneDrive queries via Microsoft Graph."""
    NO_TOKEN_MSG = (
        "📁 To access your OneDrive, connect your Microsoft account first — "
        "open your **Profile** (top-right) and click **Connect Microsoft Account**."
    )
    if not graph_token:
        return {"reply": NO_TOKEN_MSG, "action": "inline_answer", "intent": Intent.ONEDRIVE, "agent_id": "onedrive"}

    from app.agents.graph_agent import (
        list_drive_items, get_recent_files, get_shared_with_me,
        search_drive, create_drive_folder,
        extract_search_query, extract_folder_name,
    )

    msg = message.lower().strip()

    # Create folder
    if any(kw in msg for kw in ["create folder", "make folder", "new folder", "make a folder", "create a folder"]):
        folder_name = extract_folder_name(message)
        if not folder_name:
            return {"reply": "What should I name the folder? E.g. 'create folder Study Notes'",
                    "action": "inline_answer", "intent": Intent.ONEDRIVE, "agent_id": "onedrive"}
        reply = await create_drive_folder(graph_token, folder_name)
        return {"reply": reply, "action": "inline_answer", "intent": Intent.ONEDRIVE, "agent_id": "onedrive"}

    # Shared with me
    if any(kw in msg for kw in ["shared with me", "files shared", "shared files"]):
        reply = await get_shared_with_me(graph_token)
        return {"reply": reply, "action": "inline_answer", "intent": Intent.ONEDRIVE, "agent_id": "onedrive"}

    # Search
    if any(kw in msg for kw in ["search", "find", "look for"]):
        query = extract_search_query(msg, ["search onedrive", "search my drive", "search my files",
                                            "search drive", "find in drive", "look for"])
        if query:
            reply = await search_drive(graph_token, query)
        else:
            reply = "What would you like to search for in OneDrive? E.g. 'search my drive for lecture notes'"
        return {"reply": reply, "action": "inline_answer", "intent": Intent.ONEDRIVE, "agent_id": "onedrive"}

    # Recent files
    if any(kw in msg for kw in ["recent", "recently", "recent files", "last opened"]):
        reply = await get_recent_files(graph_token)
        return {"reply": reply, "action": "inline_answer", "intent": Intent.ONEDRIVE, "agent_id": "onedrive"}

    # Default: list root
    reply = await list_drive_items(graph_token)
    return {"reply": reply, "action": "inline_answer", "intent": Intent.ONEDRIVE, "agent_id": "onedrive"}


# ── Notion handler ────────────────────────────────────────────────────────────

async def _handle_notion(user_id: str, message: str) -> dict:
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


# ── Google on-demand consent ──────────────────────────────────────────────────

def _google_consent_response(service: str, message: str, intent, agent_id: str) -> dict:
    """Build an in-chat Google consent prompt.

    Rendered by the frontend as a card with "Allow once" / "Always allow"
    buttons. On approval the frontend connects Google and re-sends `message`.
    """
    return {
        "reply": (
            f"🔐 Lumen needs access to your **{service}** to do that. "
            "Choose **Allow once** for this request only, or **Always allow** to keep it connected."
        ),
        "action": "google_consent",
        "intent": intent,
        "agent_id": agent_id,
        "cards": [{
            "type": "connect_google",
            "data": {"service": service, "retry_message": message},
        }],
    }


# ── Gmail handler (Google users with Google connected) ────────────────────────

async def _handle_gmail(user_id: str, message: str) -> dict:
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


# ── Google Drive handler ──────────────────────────────────────────────────────

async def _handle_drive(user_id: str, message: str) -> dict:
    """Read / search / create / summarize Google Drive files."""
    import re as _re_d
    from app.agents.gmail_agent import is_drive_connected, get_valid_google_token
    from app.agents.gdrive_agent import (
        list_files, list_files_raw, read_file, search_drive, search_drive_raw,
        create_doc, summarize_file,
        append_to_doc, replace_doc_content, find_replace_doc,
    )

    def _format_api_error(err: str | dict) -> str:
        """Pull a clean one-line summary out of Google API error bodies."""
        import json as _json
        if isinstance(err, dict):
            inner = err.get("error", err)
            return inner.get("message", str(inner))[:300] if isinstance(inner, dict) else str(inner)[:300]
        s = str(err or "")
        # Often the body is a JSON string — extract the message
        try:
            parsed = _json.loads(s)
            inner = parsed.get("error", parsed)
            if isinstance(inner, dict):
                return inner.get("message", s)[:300]
        except Exception:
            pass
        return s[:300]
    from app.lumen.core import get_lumen

    msg = (message or "").lower().strip()
    lumen = await get_lumen(user_id)
    if not is_drive_connected(lumen):
        return _google_consent_response("Drive", message, Intent.DRIVE, "drive")

    # One-time access gate (Lumen-side): a spent "Allow once" grant re-prompts.
    from app.agents.gmail_agent import consume_once_if_needed as _consume_once
    if await _consume_once(user_id) == "blocked":
        return _google_consent_response("Drive", message, Intent.DRIVE, "drive")

    token = await get_valid_google_token(user_id)
    if not token:
        # Access expired and no refresh token (e.g. an "Allow once" grant) — re-prompt.
        return _google_consent_response("Drive", message, Intent.DRIVE, "drive")

    # CREATE: "create a google doc titled X with [a,b,c]"
    create_match = _re_d.search(
        r"(?:create|make|new)\s+(?:a\s+)?(?:new\s+)?(?:google\s+)?(?:doc|document|file)"
        r"(?:\s+(?:titled|called|named|about|on)\s+)?(.+?)(?:\s+with\s+(.+))?$",
        msg,
    )
    if create_match and ("create" in msg or "make" in msg or "new" in msg):
        title = (create_match.group(1) or "").strip().strip('"\'')
        body_part = (create_match.group(2) or "").strip()
        title = _re_d.sub(r"^(?:in|to|on)\s+drive\s+", "", title)
        if not title:
            return {
                "reply": "What should the doc be titled? Try: *create a google doc titled Today's plan with: study, lab, gym*",
                "action": "inline_answer",
                "intent": Intent.DRIVE,
                "agent_id": "drive",
            }
        lines: list[str] = []
        if body_part:
            for part in _re_d.split(r"\s*(?:,|;|\band\b)\s*", body_part):
                part = part.strip().strip('.')
                if part:
                    lines.append(part)
        result = await create_doc(token, title.title(), lines)
        if result.get("error"):
            return {
                "reply": f"⚠ Couldn't create the doc: {result['error']}",
                "action": "inline_answer",
                "intent": Intent.DRIVE,
                "agent_id": "drive",
            }
        return {
            "reply": (
                f"✓ Created **{title.title()}** in Google Drive"
                + (f" — [open]({result.get('url', '')})" if result.get('url') else "")
                + (f"\n\n{len(lines)} item(s) added." if lines else "")
            ),
            "action": "inline_answer",
            "intent": Intent.DRIVE,
            "agent_id": "drive",
        }

    # ── Google Doc EDIT (append / replace / find-replace) ──
    # Patterns require a doc-name to look up + the action.
    # Find/Replace: "in my doc X, replace 'old' with 'new'" / "replace 'old' with 'new' in my google doc X"
    fr_match = _re_d.search(
        r"(?:in\s+(?:my\s+)?(?:google\s+)?(?:doc|document)\s+)?(.+?)\s*,?\s*"
        r"(?:find|replace)\s+['\"]?(.+?)['\"]?\s+with\s+['\"]?(.+?)['\"]?\s*$",
        msg,
    ) if ("replace" in msg and " with " in msg and ("doc" in msg or "document" in msg)) else None
    if fr_match:
        # Try to extract doc name + find + replace
        # Pattern can capture title in either group 1 (when "in my doc X" comes first) or
        # we may have to search separately. Use a simpler 2-pass:
        m2 = _re_d.search(
            r"in\s+(?:my\s+)?(?:google\s+)?(?:doc|document)\s+(.+?)\s*[,:]?\s*"
            r"(?:find|replace)\s+['\"](.+?)['\"]?\s+with\s+['\"]?(.+?)['\"]?\s*$",
            msg, _re_d.IGNORECASE,
        )
        if not m2:
            m2 = _re_d.search(
                r"(?:find|replace)\s+['\"](.+?)['\"]\s+with\s+['\"](.+?)['\"]"
                r".*?(?:in\s+(?:my\s+)?(?:google\s+)?(?:doc|document)\s+(.+?))?\s*$",
                msg, _re_d.IGNORECASE,
            )
            if m2:
                find_t, replace_t, doc_name = m2.group(1), m2.group(2), (m2.group(3) or "")
            else:
                find_t = replace_t = doc_name = ""
        else:
            doc_name, find_t, replace_t = m2.group(1), m2.group(2), m2.group(3)
        if find_t and replace_t and doc_name:
            results = await search_drive(token, doc_name.strip(), limit=3,
                                          mime_types=["application/vnd.google-apps.document"])
            if not results:
                return {
                    "reply": f"📁 Couldn't find a Google Doc matching *{doc_name}* to edit.",
                    "action": "inline_answer",
                    "intent": Intent.DRIVE,
                    "agent_id": "drive",
                }
            top = results[0]
            r = await find_replace_doc(token, top["id"], find_t, replace_t)
            if r.get("error"):
                return {
                    "reply": f"⚠ Find/replace failed: {_format_api_error(r['error'])}",
                    "action": "inline_answer",
                    "intent": Intent.DRIVE,
                    "agent_id": "drive",
                }
            return {
                "reply": (
                    f"✓ Replaced **{r.get('occurrences', 0)}** occurrence(s) of "
                    f"*\"{find_t}\"* with *\"{replace_t}\"* in **{top.get('name', '')}**"
                    + (f" — [open]({top.get('url', '')})" if top.get("url") else "")
                ),
                "action": "inline_answer",
                "intent": Intent.DRIVE,
                "agent_id": "drive",
            }

    # APPEND / REPLACE: "add to my google doc X: content" / "replace my doc X with: content"
    edit_match = _re_d.search(
        r"(?:add\s+to|append\s+to|edit|update)\s+(?:my\s+)?(?:google\s+)?(?:doc|document)\s+"
        r"(.+?)\s*[:\-]\s*(.+)$",
        msg, _re_d.IGNORECASE,
    )
    replace_match = _re_d.search(
        r"replace\s+(?:my\s+)?(?:google\s+)?(?:doc|document)\s+"
        r"(.+?)\s+(?:with|:|-)\s*(.+)$",
        msg, _re_d.IGNORECASE,
    )
    if edit_match or replace_match:
        is_replace = bool(replace_match) and not edit_match
        m = replace_match if is_replace else edit_match
        doc_name = m.group(1).strip().strip('"\'')
        new_content = m.group(2).strip()
        if not doc_name or not new_content:
            return {
                "reply": "Tell me the doc name and the content. E.g. *add to my google doc Notes: new line here*",
                "action": "inline_answer",
                "intent": Intent.DRIVE,
                "agent_id": "drive",
            }
        results = await search_drive(token, doc_name, limit=3,
                                      mime_types=["application/vnd.google-apps.document"])
        if not results:
            return {
                "reply": f"📁 Couldn't find a Google Doc matching *{doc_name}* to edit.",
                "action": "inline_answer",
                "intent": Intent.DRIVE,
                "agent_id": "drive",
            }
        top = results[0]
        if is_replace:
            r = await replace_doc_content(token, top["id"], new_content)
            verb = "Replaced contents of"
        else:
            r = await append_to_doc(token, top["id"], new_content)
            verb = "Appended to"
        if r.get("error"):
            return {
                "reply": f"⚠ Doc edit failed: {_format_api_error(r['error'])}",
                "action": "inline_answer",
                "intent": Intent.DRIVE,
                "agent_id": "drive",
            }
        return {
            "reply": (
                f"✓ {verb} **{top.get('name', '')}**"
                + (f" — [open]({top.get('url', '')})" if top.get("url") else "")
            ),
            "action": "inline_answer",
            "intent": Intent.DRIVE,
            "agent_id": "drive",
        }

    # SEARCH: "find my drive files about X" / "search my drive for X"
    # Preposition (about/for/on/etc) is REQUIRED so "show my drive files" doesn't
    # treat "files" as a search query — it falls through to the default listing.
    search_match = _re_d.search(
        r"(?:search|find|look\s+for|show)\s+(?:my\s+)?(?:google\s+)?(?:drive|docs?|sheets?|files?)"
        r"\s+(?:on|about|for|with|containing|regarding)\s+(.+?)(?:\?|\.|$)",
        msg,
    )
    if search_match or "find my notes in drive" in msg:
        query = ""
        if search_match:
            query = (search_match.group(1) or "").strip().strip('"\'')
        query = _re_d.sub(r"^(?:in|from|on)\s+drive\s*", "", query).strip()
        raw = await search_drive_raw(token, query, limit=10)
        if raw.get("error"):
            return {
                "reply": f"⚠ Drive API error: {_format_api_error(raw['error'])}",
                "action": "inline_answer",
                "intent": Intent.DRIVE,
                "agent_id": "drive",
            }
        files = raw.get("files", [])
        if not files:
            return {
                "reply": f"📁 No Drive files matched *{query or 'recent'}*.",
                "action": "inline_answer",
                "intent": Intent.DRIVE,
                "agent_id": "drive",
            }
        lines = [f"📁 **{len(files)} Drive file(s){' matching *' + query + '*' if query else ''}:**\n"]
        for f in files[:5]:
            lines.append(f"- **{f['name']}**" + (f" — [open]({f['url']})" if f.get('url') else ""))
        return {
            "reply": "\n".join(lines),
            "action": "drive_search",
            "intent": Intent.DRIVE,
            "agent_id": "drive",
            "cards": [{"type": "drive_files", "data": files[:10]}],
        }

    # SUMMARIZE: "summarize my google doc on machine learning"
    sum_match = _re_d.search(
        r"summari[sz]e\s+(?:my\s+)?(?:google\s+)?(?:doc|document|sheet|file|pdf)?"
        r"(?:\s+(?:on|about|titled|called))?\s+(.+?)(?:\?|\.|$)",
        msg,
    )
    if sum_match and "summari" in msg:
        topic = (sum_match.group(1) or "").strip().strip('"\'')
        results = await search_drive(token, topic, limit=3)
        if not results:
            return {
                "reply": f"📁 No Drive files found matching *{topic}* to summarize.",
                "action": "inline_answer",
                "intent": Intent.DRIVE,
                "agent_id": "drive",
            }
        top = results[0]
        summary = await summarize_file(token, top["id"], f"Summarize relevant to: {topic}", user_id=user_id)
        return {
            "reply": (
                f"📁 **{top.get('name', '')}** — summary:\n\n{summary}\n\n"
                f"[Open in Drive]({top.get('url', '')})"
            ),
            "action": "inline_answer",
            "intent": Intent.DRIVE,
            "agent_id": "drive",
        }

    # DEFAULT: list recent files — use raw so we can show API errors
    raw = await list_files_raw(token, "", limit=10)
    if raw.get("error"):
        return {
            "reply": f"⚠ Drive API error: {_format_api_error(raw['error'])}",
            "action": "inline_answer",
            "intent": Intent.DRIVE,
            "agent_id": "drive",
        }
    files = raw.get("files", [])
    if not files:
        return {
            "reply": (
                "📁 No Docs/Sheets/PDFs visible in your Drive. "
                "(Other file types like videos and folders are filtered out.)"
            ),
            "action": "inline_answer",
            "intent": Intent.DRIVE,
            "agent_id": "drive",
        }
    lines = ["📁 **Your recent Drive files:**\n"]
    for f in files[:5]:
        lines.append(f"- **{f['name']}**" + (f" — [open]({f['url']})" if f.get('url') else ""))
    return {
        "reply": "\n".join(lines),
        "action": "drive_search",
        "intent": Intent.DRIVE,
        "agent_id": "drive",
        "cards": [{"type": "drive_files", "data": files[:10]}],
    }


# ── arXiv handler ─────────────────────────────────────────────────────────────

async def _handle_arxiv(user_id: str, message: str) -> dict:
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


# ── Wolfram handler ──────────────────────────────────────────────────────────

async def _handle_wolfram(user_id: str, message: str) -> dict:
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


# ── Multi-intent executor ─────────────────────────────────────────────────────

async def _execute_multi_intents(plan, user_id, user_info, original_message,
                                  conversation_history, graph_token, user_name):
    """Execute a list of IntentMatch entries from the LLM router.

    Each sub-task is dispatched to its agent using the rephrased `task` from
    the router. Results are merged: replies stitched together, cards concatenated,
    agent_id set to the LAST agent invoked. Errors in one task don't abort others.
    """
    replies: list[str] = []
    all_cards: list[dict] = []
    last_agent_id = "lumen"
    last_intent = Intent.GENERAL
    actions = []

    for i, im in enumerate(plan):
        agent = im.agent
        task = im.task
        try:
            envelope = broker.make_envelope(
                user_id, task,
                user_info=user_info,
                user_name=user_name,
                conversation_history=conversation_history,
                graph_token=graph_token,
            )
            if broker.has_topic(agent):
                r = await broker.request(agent, envelope)
            else:
                # Unknown agent → Lumen general chat.
                r = await _handle_lumen(user_id, task, conversation_history=conversation_history)

            # Collect outputs
            text = r.get("reply", "") if isinstance(r, dict) else ""
            if text:
                # Prefix multi-step replies with a step marker so the user can tell them apart
                if len(plan) > 1:
                    replies.append(f"**Step {i + 1}** — {text}")
                else:
                    replies.append(text)
            cards = (r or {}).get("cards") or []
            if cards:
                all_cards.extend(cards)
            last_agent_id = (r or {}).get("agent_id") or agent
            last_intent = (r or {}).get("intent") or last_intent
            if (r or {}).get("action"):
                actions.append(r["action"])
        except Exception as e:
            logger.exception(f"multi-intent sub-task failed [{agent}]: {e}")
            replies.append(f"**Step {i + 1}** — ⚠ {agent} task failed: {e}")

    combined_reply = "\n\n".join(replies) if replies else "I tried, but didn't get a clear result."
    return {
        "reply": combined_reply,
        "action": actions[-1] if actions else "multi_intent",
        "intent": last_intent,
        "agent_id": last_agent_id,
        "cards": all_cards,
        "multi_intent": [{"agent": im.agent, "task": im.task} for im in plan],
    }


# ── Subscriber–broker registration ───────────────────────────────────────────
# Each specialist agent SUBSCRIBES to its topic on the broker. The orchestrator
# (_execute_multi_intents) and the A2A layer PUBLISH requests to a topic; the
# broker delivers to the owning handler below. This decouples routing from the
# concrete handler while keeping the existing transport underneath.

async def _broker_github(env: dict) -> dict:
    from app.agents.github_agent import handle_github
    return await handle_github(env["user_id"], env["message"])


async def _broker_notion(env: dict) -> dict:
    return await _handle_notion(env["user_id"], env["message"])


async def _broker_drive(env: dict) -> dict:
    return await _handle_drive(env["user_id"], env["message"])


async def _broker_gmail(env: dict) -> dict:
    user_info = env.get("user_info") or {}
    return _ensure_intent(
        await a2a_tasks_send("/a2a/communication", env["message"], env["user_id"],
                             env.get("user_name", ""), user_email=user_info.get("email", "")),
        Intent.COMMUNICATION, "gmail",
    )


async def _broker_communication(env: dict) -> dict:
    user_info = env.get("user_info") or {}
    return _ensure_intent(
        await a2a_tasks_send("/a2a/communication", env["message"], env["user_id"],
                             env.get("user_name", ""), user_email=user_info.get("email", "")),
        Intent.COMMUNICATION, "communication",
    )


async def _broker_calendar(env: dict) -> dict:
    # Prefer Google Calendar if connected; otherwise Lumen's internal calendar.
    gcal_r = await _handle_google_calendar(env["user_id"], env["message"])
    if gcal_r is not None:
        return gcal_r
    return _ensure_intent(
        await a2a_tasks_send("/a2a/calendar", env["message"], env["user_id"],
                             env.get("user_name", "")),
        Intent.SCHEDULING, "calendar",
    )


async def _broker_shiksha(env: dict) -> dict:
    return _ensure_intent(
        await a2a_tasks_send("/a2a/shiksha", env["message"], env["user_id"],
                             env.get("user_name", "")),
        Intent.SHIKSHA, "shiksha",
    )


async def _broker_social(env: dict) -> dict:
    return await _handle_social(env["user_id"], env["message"])


async def _broker_arxiv(env: dict) -> dict:
    return await _handle_arxiv(env["user_id"], env["message"])


async def _broker_wolfram(env: dict) -> dict:
    return await _handle_wolfram(env["user_id"], env["message"])


async def _broker_general(env: dict) -> dict:
    return await _handle_lumen(env["user_id"], env["message"],
                               conversation_history=env.get("conversation_history"))


def _register_broker_agents() -> None:
    """Wire each specialist agent onto the broker as a topic subscriber."""
    broker.subscribe("github", _broker_github)
    broker.subscribe("notion", _broker_notion)
    broker.subscribe("drive", _broker_drive)
    broker.subscribe("gmail", _broker_gmail)
    broker.subscribe("communication", _broker_communication)
    broker.subscribe("calendar", _broker_calendar)
    broker.subscribe("shiksha", _broker_shiksha)
    broker.subscribe("social", _broker_social)
    broker.subscribe("arxiv", _broker_arxiv)
    broker.subscribe("wolfram", _broker_wolfram)
    broker.subscribe("general", _broker_general)
    # Back-compat: the former "portfolio" agent is now the GitHub agent.
    broker.alias("portfolio", "github")


_register_broker_agents()
