"""Communication agent — email/Teams composing, sending, inbox. Class-based: `CommunicationAgent` holds the logic as `self` methods (logic unchanged)."""
from __future__ import annotations

from app.agents.base import BaseAgent, registry
from app.agents.intents import Intent
from app.agents.a2a_client import a2a_tasks_send
from app.agents.handlers._common import _ensure_intent, _google_consent_response
from app.agents.handlers.gmail import _handle_gmail
from app.agents.state import _pending_drafts, _set_ctx


class CommunicationAgent(BaseAgent):
    name = "communication"
    intents = (Intent.COMMUNICATION,)
    description = "Email/Teams composing, sending, inbox"
    # Offline keyword fallback owned by this agent (the LLM router is primary).
    KEYWORDS = (
        "send email", "send gmail", "send an email", "email to", "mail to",
        "send a mail", "message on teams", "teams message",
        "check email", "check my email", "any replies",
        "check inbox", "check my inbox", "send outlook", "notify ",
        "write an email", "compose email", "draft email",
        "connect my email", "connect email", "connect outlook",
        "set up email", "setup email", "set up outlook", "setup outlook",
        "link my email", "link outlook", "configure email", "add my email",
        "disconnect my email", "disconnect email", "disconnect outlook", "remove my email",
        "search my email", "search my mail", "find my email", "find my mail",
        "any email from", "any mail from", "any new email", "new emails",
        "unread emails", "unread mail", "my unread", "recent emails", "recent mail",
        "emails about", "mails about", "emails containing", "mails containing",
        "search mail", "search email", "find mail", "find email",
        "email from ", "mail from ", "emails from ", "mails from ",
    )

    async def handle(self, user_id: str, message: str, user_info: dict) -> dict:
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

    async def broker(self, env: dict) -> dict:
        user_info = env.get("user_info") or {}
        return _ensure_intent(
            await a2a_tasks_send("/a2a/communication", env["message"], env["user_id"],
                                 env.get("user_name", ""), user_email=user_info.get("email", "")),
            Intent.COMMUNICATION, "communication",
        )


agent = CommunicationAgent()
registry.register_agent(agent)

# Back-compat alias for `from app.agents.interaction_manager import _handle_communication`.
_handle_communication = agent.handle
