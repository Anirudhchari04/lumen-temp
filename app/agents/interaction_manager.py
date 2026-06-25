"""Interaction Manager — the boundary between *humans and agents* and between
*agents and agents*.

Two entry points, one per interaction surface (see ``InteractionType``):

  • HUMAN → AGENT  (``dispatch``)        a person chats with their Lumen; this
                                         layer classifies intent and routes to the
                                         right specialist agent.
  • AGENT → AGENT  (``dispatch_to_ta``)  Lumen calls a TA on the user's behalf via
                                         the A2A protocol, passing cross-TA context.

Routing policy (regex vs. semantic vs. keyword) is documented on
``classify_intent`` below and in ``app.agents.routing``. The specialist agents
themselves live in ``app.agents.handlers`` and own their own routing keywords, so
this module is just the dispatcher — not a home for agent logic.
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
from app.agents.base import registry
from app.agents.intents import Intent, InteractionType
from app.agents.state import (
    _pending_drafts,
    _pending_proposals,
    _user_context,
    _get_ctx,
    _set_ctx,
    _clear_ctx,
    get_pending_draft,
    clear_pending_draft,
)
from app.agents.handlers._common import _ensure_intent

logger = logging.getLogger(__name__)


# ── Intent Classification ────────────────────────────────────
# `Intent` and `InteractionType` now live in app.agents.intents (imported above)
# and are re-exported from this module for backwards compatibility.


def classify_intent(message: str) -> tuple[str, str | None]:
    """Classify user intent and optionally detect target agent.
    Returns (intent, target_agent_id_or_None).

    ROUTING POLICY — regex/keywords vs. semantic (the #6 question):
      • The SEMANTIC LLM router (``llm_classify_multi``) is the PRIMARY router for
        natural-language intent. ``dispatch()`` calls it for everything that isn't
        trivially deterministic.
      • This function is the cheap rule layer, limited to TWO safe roles:
          1. DETERMINISTIC / STRUCTURAL signals where regex is exact and cheaper
             than an LLM call: greetings, explicit "open X" launches, email
             addresses, arXiv IDs, yes/confirm replies, disconnect commands. These
             stay inline below as regex.
          2. An OFFLINE FALLBACK (keyword substring match) so routing still works
             if the LLM router is down. That keyword data is NOT hard-coded here —
             each agent OWNS its keywords (e.g. ``WolframAgent.KEYWORDS``) and this
             function reads them via ``app.agents.routing`` (``_routing.*``). So
             tuning an agent's keywords is a one-file change in that agent.
      • Prefer adding a new intent to the LLM router over growing an agent's keyword
        list. Only add a keyword/regex when the phrasing is unambiguous and
        structural (not when it's a fuzzy synonym the LLM should infer).
      • ``_should_skip_llm_routing`` decides which rule results may bypass the LLM.
    """
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
    # Communication keywords are owned by CommunicationAgent (see app.agents.routing).
    if _routing.matches(msg, _routing.COMM_KW):
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
    # Notion keywords are owned by NotionAgent.
    if _routing.matches(msg, _routing.NOTION_KW):
        return Intent.NOTION, None

    # arXiv intent — research paper search / fetch / summarize
    # arXiv keywords are owned by ArxivAgent.
    if _routing.matches(msg, _routing.ARXIV_KW):
        return Intent.ARXIV, None

    # Wolfram intent — math, units, computational queries
    # Wolfram keywords are owned by WolframAgent.
    if _routing.matches(msg, _routing.WOLFRAM_KW):
        return Intent.WOLFRAM, None

    # Google Drive intent — read/write/summarize Drive files (Docs, Sheets, PDFs)
    # Google Drive keywords are owned by DriveAgent.
    if _routing.matches(msg, _routing.DRIVE_KW):
        return Intent.DRIVE, None

    # OneDrive intent — file queries via Graph
    # OneDrive keywords are owned by DriveAgent.
    if _routing.matches(msg, _routing.ONEDRIVE_KW):
        return Intent.ONEDRIVE, None

    # Outlook read intent — Graph-powered email queries (admin/org specific, no IMAP equivalent)
    # Outlook/Graph keywords are owned by GmailAgent.
    if _routing.matches(msg, _routing.OUTLOOK_KW):
        return Intent.OUTLOOK, None

    # Portfolio / GitHub intent — must be checked before calendar (both have "remove/delete")
    # Portfolio/GitHub keywords are owned by GitHubAgent. Checked before calendar.
    if _routing.matches(msg, _routing.PORTFOLIO_KW):
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

    # Calendar-management keywords owned by CalendarAgent. Checked BEFORE calendar
    # query detection (e.g. "remove all events today").
    if _routing.matches(msg, _routing.CAL_MANAGE_KW):
        return Intent.SCHEDULING, "calendar"

    # Query intent — info-seeking calendar keywords owned by CalendarAgent.
    if _routing.matches(msg, _routing.CAL_QUERY_KW):
        return Intent.QUERY, "calendar"
    # "today" / "tomorrow" alone as calendar queries
    if msg.strip() in ("today", "tomorrow", "what's today", "what's tomorrow"):
        return Intent.QUERY, "calendar"

    # Scheduling/event keywords owned by CalendarAgent.
    if _routing.matches(msg, _routing.SCHEDULE_KW):
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

    # Shiksha TA keywords owned by ShikshaAgent — checked before generic progress.
    if _routing.matches(msg, _routing.SHIKSHA_KW):
        return Intent.SHIKSHA, None

    # Progress keywords owned by GeneralAgent.
    if _routing.matches(msg, _routing.PROGRESS_KW):
        return Intent.PROGRESS, None

    # Meta keywords owned by GeneralAgent.
    if _routing.matches(msg, _routing.META_KW):
        return Intent.META, None

    # Social keywords owned by SocialAgent.
    if _routing.matches(msg, _routing.SOCIAL_KW):
        return Intent.SOCIAL, None

    # Learning info-seeking keywords owned by ShikshaAgent.
    if _routing.matches(msg, _routing.LEARNING_QUERY_KW):
        ta_id = detect_ta(message)
        return Intent.QUERY, ta_id or "shiksha"

    # Learning keywords owned by ShikshaAgent.
    ta_id = detect_ta(message)
    if ta_id:
        return Intent.LEARNING, ta_id
    if _routing.matches(msg, _routing.LEARNING_KW):
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
    """Whether dispatch may answer from the cheap rule layer and skip the
    semantic LLM router.

    Policy (the "regex vs semantic" answer): the semantic LLM (llm_classify_multi)
    is PRIMARY for natural language. We bypass it ONLY when the rule result is
    deterministic and cheap:
      • a greeting / thanks (matched structurally by _GREETING_RE), or
      • one of _LLM_FASTPATH_INTENTS below.
    For every other intent the message still goes to the LLM; the keyword rules
    are then merely the OFFLINE FALLBACK used when the LLM is unavailable.
    """
    return rule_intent in _LLM_FASTPATH_INTENTS or bool(_GREETING_RE.match(message or ""))


# Intents safe to answer from rules without a semantic LLM call:
#   PROGRESS / META → handled directly by Lumen's general chat (no specialist).
#   PORTFOLIO       → pinned: its keyword signals ("math-ta folder", "staged
#                     commit", "pull request") are precise, and skipping the LLM
#                     avoids it mistaking a GitHub folder op for Google Drive.
_LLM_FASTPATH_INTENTS = frozenset({Intent.PROGRESS, Intent.META, Intent.PORTFOLIO})


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
    # The intent→agent map (_INTENT_TO_AGENT) is derived once from the agent
    # registry at module load — see the agent declarations at the bottom of
    # this file. PROGRESS/META route to "general"; everything else to its agent.

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


# _ensure_intent now lives in app.agents.handlers._common (imported above).


async def dispatch_to_ta(user_id: str, ta_id: str, message: str,
                         thread_id: str | None = None,
                         user_info: dict | None = None) -> dict:
    """AGENT → AGENT entry point (InteractionType.AGENT_TO_AGENT).

    Lumen calls a TA directly on the user's behalf via the A2A protocol,
    passing full StudentContext (cross-TA progress, TC inventory). Used from
    the dedicated TA chat pages. For the human-facing surface, see ``dispatch``.
    """
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


# portfolio handler now lives in app.agents.handlers.portfolio (imported below).


# shiksha handler now lives in app.agents.handlers.shiksha (imported below).


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
    # GitHub agent opens as a separate standalone page (full page load, new tab).
    if target in ("github", "portfolio"):
        return {
            "reply": "Opening the GitHub Repo Explorer — explore your repos, commits, branches, pull requests, files, and learning portfolio.",
            "action": "external_launch",
            "intent": Intent.LAUNCH,
            "agent_id": "github",
            "redirect_url": "/github-explorer",
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


# _handle_google_calendar now lives in app.agents.handlers.calendar
# (imported below).


# _handle_calendar_query now lives in app.agents.handlers.calendar
# (imported below).


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


# _handle_scheduling and confirm_study_plan now live in
# app.agents.handlers.calendar (imported below).


# social handler (_find_peer_by_name + _handle_social) now lives in
# app.agents.handlers.social (imported below).


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


# lumen general/progress handler now lives in app.agents.handlers.lumen
# (imported below).


# ── Communication Handler ────────────────────────────────────
# Per-user conversational state (_pending_drafts, _pending_proposals,
# _user_context) and its accessors now live in app.agents.state (imported above)
# and are re-exported from this module for backwards compatibility.

# _handle_communication now lives in app.agents.handlers.communication
# (imported below).


# ── Outlook (Graph) handler ───────────────────────────────────────────────────

# outlook + onedrive (Graph) handlers now live in app.agents.handlers.graph
# (imported below).


# ── Notion handler ────────────────────────────────────────────────────────────

# notion handler now lives in app.agents.handlers.notion (imported below).


# ── Google on-demand consent ──────────────────────────────────────────────────

# _google_consent_response now lives in app.agents.handlers._common.


# ── Gmail handler (Google users with Google connected) ────────────────────────

# gmail handler now lives in app.agents.handlers.gmail (imported below).


# ── Google Drive handler ──────────────────────────────────────────────────────

# drive handler now lives in app.agents.handlers.drive (imported below).


# ── arXiv handler ─────────────────────────────────────────────────────────────

# ── arXiv + Wolfram handlers now live in app.agents.handlers.{arxiv,wolfram}
# (imported below); their _handle_* functions are re-exported from this module
# for backwards compatibility.


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
# Each specialist agent is declared with @registry.agent(...) — a single
# co-located source of truth for its broker topic, the intents that route to it,
# and any back-compat aliases. Adding an agent is therefore a one-line change
# here instead of editing the broker wiring, the intent→agent map, and the alias
# table separately. The orchestrator (_execute_multi_intents) and the A2A layer
# publish to a topic; the broker delivers to the owning handler below.

# github / notion / drive / gmail brokers now live in their handler modules
# (app.agents.handlers.{portfolio,notion,drive,gmail}).

# communication / calendar brokers now live in their handler modules
# (app.agents.handlers.{communication,calendar}).


# shiksha / social / general brokers now live in their handler modules
# (app.agents.handlers.{shiksha,social,lumen}).


def _register_broker_agents() -> None:
    """Wire every declared agent (and its aliases) onto the broker."""
    registry.wire(broker)


# Import the modular agent handler package LAST, so every handler module's
# @registry.agent decorator has run before we wire the broker. Re-export the
# handlers' public functions so existing `from app.agents.interaction_manager
# import _handle_*` call sites keep working.
from app.agents import handlers as _handlers  # noqa: E402,F401
from app.agents.handlers.arxiv import _handle_arxiv  # noqa: E402,F401
from app.agents.handlers.calendar import (  # noqa: E402,F401
    _handle_google_calendar, _handle_calendar_query, _handle_scheduling, confirm_study_plan,
)
from app.agents.handlers.communication import _handle_communication  # noqa: E402,F401
from app.agents.handlers.drive import _handle_drive  # noqa: E402,F401
from app.agents.handlers.gmail import _handle_gmail  # noqa: E402,F401
from app.agents.handlers.graph import _handle_outlook, _handle_onedrive  # noqa: E402,F401
from app.agents.handlers.lumen import _handle_lumen  # noqa: E402,F401
from app.agents.handlers.notion import _handle_notion  # noqa: E402,F401
from app.agents.handlers.portfolio import _handle_portfolio  # noqa: E402,F401
from app.agents.handlers.shiksha import _handle_shiksha  # noqa: E402,F401
from app.agents.handlers.social import _handle_social  # noqa: E402,F401
from app.agents.handlers.wolfram import _handle_wolfram  # noqa: E402,F401

# Keyword fallback data, assembled from each agent's own keyword declarations.
# Used by classify_intent's rule fast-path / offline fallback. Imported last so
# the agent classes it references are already loaded.
from app.agents import routing as _routing  # noqa: E402,F401

_register_broker_agents()

# Intent → agent-name routing table, derived from the registry above (never
# hand-maintained). Used by dispatch()'s rule-based fast path.
_INTENT_TO_AGENT: dict[str, str] = registry.intent_map()
