"""Communication Agent — Single-point communication mediator.

Handles sending/receiving messages across channels (Outlook/Teams/peer Lumen)
so the user never needs to open external apps. Uses Microsoft Graph API for
real email sending via the user's Outlook/M365 mailbox.

Channels:
  - email (Microsoft Graph — real Outlook send)
  - teams (simulated for demo)
  - peer  (existing LITP peer messaging)
"""

from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timezone as _tz
UTC = _tz.utc
from typing import Any

logger = logging.getLogger(__name__)


# ── In-memory stores (simulated inbox for demo) ─────────────

_comm_outbox: list[dict] = []   # sent messages
_comm_inbox: list[dict] = []    # simulated received messages (demo)


def _now() -> str:
    return datetime.now(UTC).isoformat()


# ── Draft composition via LLM ───────────────────────────────

async def compose_draft(user_id: str, user_name: str, message: str, user_email: str = "") -> dict:
    """Parse a natural language communication request into a structured draft.

    Returns: {id, channel, to, to_email, subject, body, status}
    """
    # Detect self-send intent so we can bias the LLM / post-fill
    msg_lower = (message or "").lower()
    self_kw = [" to myself", " to me ", " to me.", " to me,", " to me!", " to me?",
               "email myself", "send myself", "mail myself", "remind myself"]
    self_send = any(kw in f" {msg_lower} " for kw in self_kw) or msg_lower.strip().endswith(" to me")

    # Use LLM to parse intent
    try:
        # Import from calendar_agent (where _get_client actually lives)
        from app.agents.calendar_agent import _get_client

        self_hint = (
            f"\nSENDER_EMAIL: {user_email}\n"
            f"NOTE: If the request says 'send to myself / me / self', set to='Me' and to_email='{user_email}'."
            if user_email else ""
        )

        prompt = (
            f"You are an email drafting assistant. Parse this request and compose a professional, well-structured email.\n\n"
            f"REQUEST: \"{message}\"\n"
            f"SENDER NAME: {user_name}"
            f"{self_hint}\n\n"
            f"Return a JSON object with exactly these fields:\n"
            f"- to: recipient name (extract from the request, e.g. \"Anirudh\")\n"
            f"- to_email: only if explicitly mentioned in the request, else empty string\n"
            f"- subject: a clear, concise subject line (NOT the raw request text)\n"
            f"- body: a well-formed email body — greeting + meaningful content based on the request + sign-off as {user_name}\n"
            f"- channel: \"email\" or \"teams\" (default \"email\")\n\n"
            f"IMPORTANT: The body must be a real email, not an echo of the request. "
            f"For example, if the request is 'send a mail to anirudh scheduling a meeting at 3', "
            f"the body should say something like 'Hi Anirudh,\\n\\nWould you be available for a meeting "
            f"at 3pm today? Let me know if that works.\\n\\nBest,\\n{user_name}'\\n\\n"
            f"Return ONLY the JSON, no explanation, no markdown fences."
        )

        from app.agents.prompt_kit import build_agent_prompt
        draft_instructions = build_agent_prompt(
            role="Email Composer",
            mission="Turn a student's natural-language request into a polished, ready-to-send email draft.",
            capabilities=[
                "Infer the recipient, subject, and a complete email body from a short request.",
                "Write in a professional, warm, and concise tone.",
                "Detect self-addressed mail ('email myself') and set the sender as the recipient.",
            ],
            rules=[
                "Return ONLY a valid JSON object — no markdown, no code fences, no commentary.",
                "The body must be a real, well-formed email (greeting + content + sign-off), never an echo of the request.",
                "Only fill to_email if the request explicitly contains an address; otherwise leave it empty.",
                "Keep the subject line clear and specific — never the raw request text.",
            ],
            output_format='{"to": "...", "to_email": "...", "subject": "...", "body": "...", "channel": "email|teams"}',
        )
        client = _get_client()
        agent = client.as_agent(
            name="DraftComposer",
            instructions=draft_instructions,
        )
        _t0 = time.perf_counter()
        result = await agent.run(prompt)
        _latency_ms = (time.perf_counter() - _t0) * 1000
        text = str(result).strip()

        try:
            from app.lumen.token_tracker import record_usage, estimate_tokens
            p = estimate_tokens(prompt)
            c = estimate_tokens(text)
            await record_usage(user_id, p, c, model="agent_framework (estimated)", source="communication", latency_ms=_latency_ms)
        except Exception:
            pass

        # Parse JSON from response
        import json
        # Strip markdown code fences if present
        if text.startswith("```"):
            text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        draft_data = json.loads(text)
    except Exception as e:
        logger.warning(f"Draft LLM failed: {e}")
        # Fallback: regex-parse recipient and subject from message
        import re as _re
        # "send a mail to anirudh saying hi" → to_name="anirudh", subject/body="hi"
        to_match = _re.search(
            r'(?:to|for)\s+([A-Za-z][A-Za-z\s]{0,30}?)(?:\s+saying|\s+about|\s+re:|'
            r'\s+regarding|\s+that|\s+asking|\s+with subject|\s+and\s|[,.]|$)',
            message, _re.IGNORECASE
        )
        body_match = _re.search(
            r'(?:saying|about|regarding|that)\s+(.+?)(?:\.|$)',
            message, _re.IGNORECASE
        )
        to_name = to_match.group(1).strip().title() if to_match else ("Me" if self_send else "")
        body_text = body_match.group(1).strip() if body_match else message
        draft_data = {
            "to": to_name or ("Me" if self_send else ""),
            "to_email": user_email if self_send else "",
            "subject": body_text[:60] if body_text else "Message from Lumen",
            "body": body_text or message,
            "channel": "email",
        }

    # Post-process: force self-send if detected
    if self_send and user_email:
        draft_data["to"] = "Me"
        draft_data["to_email"] = user_email

    draft = {
        "id": str(uuid.uuid4())[:8],
        "user_id": user_id,
        "channel": draft_data.get("channel", "email"),
        "to": draft_data.get("to", ""),
        "to_email": draft_data.get("to_email", ""),
        "subject": draft_data.get("subject", ""),
        "body": draft_data.get("body", ""),
        "status": "draft",
        "created_at": _now(),
    }
    return draft


# ── Send via Microsoft Graph ─────────────────────────────────

async def send_via_graph(user_token: str, draft: dict) -> dict:
    """Send email using Microsoft Graph API with the user's delegated token.

    Requires Mail.Send permission on the Entra app registration.
    """
    import aiohttp

    to_email = draft.get("to_email", "")
    if not to_email:
        # Try to resolve from known peers
        to_email = await _resolve_email(draft.get("to", ""))

    if not to_email:
        draft["status"] = "failed"
        draft["error"] = f"Could not resolve email for '{draft.get('to', '')}'. Please provide an email address."
        return draft

    graph_url = "https://graph.microsoft.com/v1.0/me/sendMail"
    mail_body = {
        "message": {
            "subject": draft["subject"],
            "body": {
                "contentType": "Text",
                "content": draft["body"],
            },
            "toRecipients": [
                {"emailAddress": {"address": to_email}}
            ],
        },
        "saveToSentItems": True,
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                graph_url,
                json=mail_body,
                headers={
                    "Authorization": f"Bearer {user_token}",
                    "Content-Type": "application/json",
                },
            ) as resp:
                if resp.status == 202:
                    draft["status"] = "sent"
                    draft["sent_at"] = _now()
                    draft["to_email"] = to_email
                    _comm_outbox.append(draft)
                    logger.info(f"Email sent to {to_email}: {draft['subject']}")
                else:
                    body = await resp.text()
                    draft["status"] = "failed"
                    draft["error"] = f"Graph API {resp.status}: {body[:200]}"
                    logger.warning(f"Graph send failed: {resp.status} {body[:200]}")
    except Exception as e:
        draft["status"] = "failed"
        draft["error"] = str(e)
        logger.warning(f"Graph send exception: {e}")

    return draft


# ── Send via SMTP OAuth2 (auto-detects provider) ────────────

def _detect_smtp_config(user_email: str) -> dict:
    """Auto-detect SMTP server based on email domain."""
    domain = (user_email.split("@")[1] if "@" in user_email else "").lower()
    if domain in ("gmail.com", "googlemail.com") or domain.endswith(".edu.in"):
        return {"host": "smtp.gmail.com", "port": 587, "provider": "google"}
    if domain in ("outlook.com", "hotmail.com", "live.com") or domain.endswith("microsoft.com"):
        return {"host": "smtp.office365.com", "port": 587, "provider": "microsoft"}
    # Default to Gmail for Google-authed users, Office for others
    return {"host": "smtp.gmail.com", "port": 587, "provider": "google"}


async def send_via_smtp(user_email: str, oauth_token: str, draft: dict, provider: str = "") -> dict:
    """Send email via SMTP with OAuth2 XOAUTH2 authentication.

    Auto-detects provider (Gmail/Outlook) from email domain.
    Gmail: smtp.gmail.com:587 with XOAUTH2
    Outlook: smtp.office365.com:587 with XOAUTH2
    """
    import aiosmtplib
    import base64
    from email.message import EmailMessage

    to_email = draft.get("to_email", "")
    if not to_email:
        to_email = await _resolve_email(draft.get("to", ""))

    if not to_email:
        draft["status"] = "failed"
        draft["error"] = f"Could not resolve email for '{draft.get('to', '')}'. Please provide an email address."
        return draft

    smtp_config = _detect_smtp_config(user_email)
    if provider:
        smtp_config["provider"] = provider
        if provider == "google":
            smtp_config["host"] = "smtp.gmail.com"
        elif provider == "microsoft":
            smtp_config["host"] = "smtp.office365.com"

    # Build email message
    msg = EmailMessage()
    msg["From"] = user_email
    msg["To"] = to_email
    msg["Subject"] = draft.get("subject", "Message from Lumen")
    msg.set_content(draft.get("body", ""))

    # Build XOAUTH2 string
    xoauth2_str = f"user={user_email}\x01auth=Bearer {oauth_token}\x01\x01"
    xoauth2_b64 = base64.b64encode(xoauth2_str.encode()).decode()

    try:
        smtp = aiosmtplib.SMTP(hostname=smtp_config["host"], port=smtp_config["port"], use_tls=False)
        await smtp.connect()
        await smtp.starttls()
        code, resp_text = await smtp.execute_command(b"AUTH", b"XOAUTH2", xoauth2_b64.encode())
        if code != 235:
            draft["status"] = "failed"
            draft["error"] = f"SMTP auth failed ({code}): {resp_text}"
            logger.warning(f"SMTP XOAUTH2 auth failed on {smtp_config['host']}: {code} {resp_text}")
            await smtp.quit()
            return draft

        await smtp.send_message(msg)
        await smtp.quit()

        draft["status"] = "sent"
        draft["sent_at"] = _now()
        draft["to_email"] = to_email
        draft["method"] = f"smtp-{smtp_config['provider']}"
        _comm_outbox.append(draft)
        logger.info(f"Email sent via {smtp_config['provider']} SMTP to {to_email}: {draft.get('subject')}")
    except Exception as e:
        draft["status"] = "failed"
        draft["error"] = f"SMTP error: {str(e)}"
        logger.warning(f"SMTP send exception ({smtp_config['host']}): {e}")

    return draft


# ── Send via Gmail API (REST, no SMTP) ──────────────────────

async def send_via_gmail_api(oauth_token: str, user_email: str, draft: dict) -> dict:
    """Send email via Gmail REST API using gmail.send scope.

    Uses https://www.googleapis.com/auth/gmail.send — less restricted than
    https://mail.google.com/ and doesn't require Google app verification.
    """
    import aiohttp
    import base64
    from email.message import EmailMessage

    to_email = draft.get("to_email", "")
    if not to_email:
        to_email = await _resolve_email(draft.get("to", ""))

    if not to_email:
        draft["status"] = "failed"
        draft["error"] = f"Could not resolve email for '{draft.get('to', '')}'. Please provide an email address."
        return draft

    # Build RFC 2822 email
    msg = EmailMessage()
    msg["From"] = user_email
    msg["To"] = to_email
    msg["Subject"] = draft.get("subject", "Message from Lumen")
    msg.set_content(draft.get("body", ""))

    # Base64url encode the message
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
                json={"raw": raw},
                headers={
                    "Authorization": f"Bearer {oauth_token}",
                    "Content-Type": "application/json",
                },
            ) as resp:
                if resp.status == 200:
                    draft["status"] = "sent"
                    draft["sent_at"] = _now()
                    draft["to_email"] = to_email
                    draft["method"] = "gmail-api"
                    _comm_outbox.append(draft)
                    logger.info(f"Email sent via Gmail API to {to_email}: {draft.get('subject')}")
                else:
                    body = await resp.text()
                    draft["status"] = "failed"
                    draft["error"] = f"Gmail API {resp.status}: {body[:200]}"
                    logger.warning(f"Gmail API send failed: {resp.status} {body[:200]}")
    except Exception as e:
        draft["status"] = "failed"
        draft["error"] = f"Gmail API error: {str(e)}"
        logger.warning(f"Gmail API send exception: {e}")

    return draft


# ── IMAP / SMTP removed ─────────────────────────────────────────────
# Lumen now uses Gmail API (Google users) or the Chrome extension (Outlook users).
# is_email_connected() kept as a stub for any back-compat callers.

def is_email_connected(lumen: dict) -> bool:
    """Deprecated — IMAP removed. Always returns False."""
    return False


def log_extension_sent(user_id: str, to_email: str, subject: str, body: str) -> dict:
    """Record an email sent via the Chrome extension to the outbox.

    The extension itself does the sending (via the user's Outlook session) —
    this just lets Lumen answer "what did I send today?" queries.
    """
    entry = {
        "id": str(uuid.uuid4())[:8],
        "user_id": user_id,
        "channel": "email",
        "to": to_email,
        "to_email": to_email,
        "subject": subject or "",
        "body": body or "",
        "status": "sent",
        "sent_at": _now(),
        "method": "extension",
    }
    _comm_outbox.append(entry)
    logger.info(f"Extension-sent logged: {to_email} — {subject!r}")
    return entry


async def send_simulated(draft: dict) -> dict:
    """Simulated send for demo (when Graph token unavailable)."""
    to_email = draft.get("to_email", "") or await _resolve_email(draft.get("to", ""))
    draft["to_email"] = to_email or f"{draft.get('to', 'unknown').lower().replace(' ', '.')}@microsoft.com"
    draft["status"] = "sent"
    draft["sent_at"] = _now()
    draft["simulated"] = True
    _comm_outbox.append(draft)

    # Simulate a reply arriving after a delay (for demo purposes)
    import asyncio
    asyncio.create_task(_simulate_reply(draft))

    return draft


async def _simulate_reply(original: dict) -> None:
    """After a short delay, add a simulated reply to inbox."""
    import asyncio
    await asyncio.sleep(10)  # 10 seconds delay
    reply = {
        "id": str(uuid.uuid4())[:8],
        "from": original["to"],
        "from_email": original.get("to_email", ""),
        "subject": f"Re: {original['subject']}",
        "body": f"Thanks for the message! I'll get back to you soon. — {original['to']}",
        "channel": original["channel"],
        "status": "unread",
        "received_at": _now(),
        "in_reply_to": original["id"],
    }
    _comm_inbox.append(reply)
    logger.info(f"Simulated reply from {original['to']}")


async def _resolve_email(name: str, self_email: str = "", self_name: str = "") -> str:
    """Try to resolve a name to an email from known Lumen peers.

    Returns the BEST single match (or empty). For disambiguation, use _resolve_email_candidates.
    """
    candidates = await _resolve_email_candidates(name, self_email, self_name)
    return candidates[0].get("email", "") if candidates else ""


async def _resolve_email_candidates(name: str, self_email: str = "", self_name: str = "") -> list[dict]:
    """Return a list of possible matches for a name.

    Each candidate: {name, email, source: 'self'|'peer'}.
    Empty list if no matches. When multiple candidates, caller should disambiguate.
    """
    if not name:
        return []
    name_lower = name.lower().strip()
    if self_email and name_lower in ("me", "myself", "self", "i"):
        return [{"name": self_name or "Me", "email": self_email, "source": "self"}]
    if self_email and self_name and name_lower in self_name.lower():
        return [{"name": self_name, "email": self_email, "source": "self"}]

    from app.lumen.core import get_all_lumens
    lumens = await get_all_lumens()
    matches = []
    first_token = name_lower.split()[0] if name_lower else ""
    for l in lumens:
        lumen_name = (l.get("name") or "").lower()
        email = l.get("email", "")
        if not email:
            continue
        # Exact name match → strong
        if lumen_name == name_lower:
            matches.append({"name": l.get("name") or "", "email": email, "source": "peer", "score": 100})
        # Substring match → medium
        elif name_lower in lumen_name:
            matches.append({"name": l.get("name") or "", "email": email, "source": "peer", "score": 80})
        # First-token match → weaker
        elif first_token and lumen_name.startswith(first_token):
            matches.append({"name": l.get("name") or "", "email": email, "source": "peer", "score": 60})
    matches.sort(key=lambda m: -m["score"])
    return matches


# ── Inbox queries ────────────────────────────────────────────

def check_inbox(user_id: str, from_filter: str | None = None) -> list[dict]:
    """Check simulated inbox, optionally filtered by sender name."""
    results = _comm_inbox
    if from_filter:
        f = from_filter.lower()
        results = [m for m in results if f in m.get("from", "").lower()]
    return sorted(results, key=lambda m: m.get("received_at", ""), reverse=True)


def check_outbox(user_id: str) -> list[dict]:
    """Check sent messages."""
    return [m for m in _comm_outbox if m.get("user_id") == user_id]


def mark_inbox_read(msg_ids: list[str]) -> int:
    """Mark inbox messages as read."""
    count = 0
    for m in _comm_inbox:
        if m["id"] in msg_ids:
            m["status"] = "read"
            count += 1
    return count


def get_agent_card(base_url: str = "") -> "AgentCard":
    from app.protocols.models import AgentCard, AgentProvider, AgentInterface, AgentCapabilities, AgentSkill
    return AgentCard(
        name="Communication Agent",
        description="Email drafting and sending via SMTP or Microsoft Outlook. Compose, send, and check emails using natural language.",
        version="1.0.0",
        documentationUrl=f"{base_url}/docs/communication",
        provider=AgentProvider(organization="Lumen Network", url=base_url),
        supportedInterfaces=[AgentInterface(url=f"{base_url}/a2a/communication")],
        capabilities=AgentCapabilities(streaming=False, pushNotifications=False),
        defaultInputModes=["text/plain"],
        defaultOutputModes=["text/plain"],
        securitySchemes={
            "lumenJwt": {"httpAuthSecurityScheme": {"scheme": "bearer", "bearerFormat": "JWT"}},
            "smtpToken": {"httpAuthSecurityScheme": {"scheme": "bearer", "bearerFormat": "JWT", "description": "SMTP OAuth2 token (user-consentable, no admin needed)"}},
        },
        securityRequirements=[{"lumenJwt": []}],
        skills=[
            AgentSkill(
                id="communication.compose_draft",
                name="Compose Email Draft",
                description="Compose an email draft using natural language. Returns draft for review before sending.",
                tags=["email", "draft", "compose", "write"],
                examples=["Draft an email to my professor about the assignment", "Write a note to Swapnik about the project deadline", "Compose an email to the team about tomorrow's meeting"],
            ),
            AgentSkill(
                id="communication.send_email",
                name="Send Email",
                description="Send an email using SMTP (no admin consent needed) or Microsoft Graph (requires admin pre-consent on MS tenant)",
                tags=["email", "send", "outlook", "smtp"],
                examples=["Send an email to Manohar about the agent architecture", "Email the team the update", "Send my professor the assignment draft"],
            ),
            AgentSkill(
                id="communication.check_inbox",
                name="Check Inbox",
                description="Check messages in the Lumen inbox (peer messages and notifications)",
                tags=["inbox", "messages", "notifications"],
                examples=["Check my inbox", "Any new messages?", "What messages do I have from Priya?"],
            ),
        ],
    )
