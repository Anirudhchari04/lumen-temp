"""Gmail agent — full read/write/search/send/summarize via Gmail REST API.

Auth: OAuth code flow. Tokens stored encrypted in lumen.google_config = {
  access_token_encrypted, refresh_token_encrypted, scopes, expires_at, email
}.

This module also owns the token-refresh helper that gdrive_agent imports.
"""

from __future__ import annotations

import asyncio
import base64
import email.utils
import logging
import re
import time
from email.mime.text import MIMEText

import httpx

from app.agents.email_crypto import encrypt_password, decrypt_password
from app.config import settings
from app.lumen.core import get_lumen, save_lumen

logger = logging.getLogger(__name__)

GMAIL_API_BASE = "https://gmail.googleapis.com/gmail/v1/users/me"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"

GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.modify"
DRIVE_SCOPE = "https://www.googleapis.com/auth/drive"
CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar"

# Per-user lock to serialize OAuth token refreshes. Without this, two parallel
# requests both refresh + save the token concurrently, causing Cosmos 449
# "Conflicting request to resource" errors.
_refresh_locks: dict[str, asyncio.Lock] = {}


def _get_refresh_lock(user_id: str) -> asyncio.Lock:
    lock = _refresh_locks.get(user_id)
    if lock is None:
        lock = asyncio.Lock()
        _refresh_locks[user_id] = lock
    return lock


# ── Connection helpers ───────────────────────────────────────────────────────

def is_google_connected(lumen: dict | None) -> bool:
    if not lumen:
        return False
    cfg = lumen.get("google_config") or {}
    # An access token is enough to count as connected. "Always" grants also have
    # a refresh token (durable); "once" grants have only the short-lived access
    # token, so they naturally lapse back to disconnected once it expires.
    return bool(cfg.get("access_token_encrypted"))


def is_gmail_connected(lumen: dict | None) -> bool:
    if not is_google_connected(lumen):
        return False
    scopes = lumen.get("google_config", {}).get("scopes", "")
    return GMAIL_SCOPE in scopes or "gmail" in scopes


def is_drive_connected(lumen: dict | None) -> bool:
    if not is_google_connected(lumen):
        return False
    scopes = lumen.get("google_config", {}).get("scopes", "")
    return DRIVE_SCOPE in scopes or "drive" in scopes


def is_gcalendar_connected(lumen: dict | None) -> bool:
    if not is_google_connected(lumen):
        return False
    scopes = lumen.get("google_config", {}).get("scopes", "")
    return CALENDAR_SCOPE in scopes or "calendar" in scopes


async def save_google_config(user_id: str, access_token: str, refresh_token: str,
                              scopes: str, expires_in: int, email: str,
                              consent: str = "always") -> dict:
    lumen = await get_lumen(user_id)
    if not lumen:
        raise ValueError(f"No Lumen for user {user_id}")
    # For "once" grants we deliberately do NOT carry over any prior refresh token —
    # the access token must be allowed to lapse so Lumen re-prompts next time.
    if consent == "once":
        refresh_enc = encrypt_password(refresh_token) if refresh_token else ""
    else:
        refresh_enc = (encrypt_password(refresh_token) if refresh_token
                       else lumen.get("google_config", {}).get("refresh_token_encrypted", ""))
    lumen["google_config"] = {
        "access_token_encrypted": encrypt_password(access_token),
        "refresh_token_encrypted": refresh_enc,
        "scopes": scopes,
        "expires_at": int(time.time()) + max(expires_in - 60, 60),
        "email": email,
        "consent": consent,
        # Lumen-side one-time gate (see consume_once_if_needed). Only meaningful
        # for consent="once"; reset to False on every fresh grant.
        "once_used": False,
    }
    await save_lumen(lumen)
    return lumen["google_config"]


async def consume_once_if_needed(user_id: str) -> str:
    """Lumen-side one-time access gate. Independent of Google token expiry.

    For a consent="once" grant:
      - first call  → mark it consumed, return "ok" (this request is allowed)
      - later calls → revoke the grant, return "blocked" (caller re-prompts)
    For "always" grants (or no Google config): always returns "ok".
    """
    lumen = await get_lumen(user_id)
    if not lumen:
        return "ok"
    cfg = lumen.get("google_config") or {}
    if cfg.get("consent") != "once":
        return "ok"
    if cfg.get("once_used"):
        # Already spent — revoke so the next request prompts for consent again.
        lumen.pop("google_config", None)
        await save_lumen(lumen)
        return "blocked"
    cfg["once_used"] = True
    lumen["google_config"] = cfg
    await save_lumen(lumen)
    return "ok"


async def disconnect_google(user_id: str) -> bool:
    lumen = await get_lumen(user_id)
    if not lumen:
        return False
    lumen.pop("google_config", None)
    await save_lumen(lumen)
    return True


async def _refresh_token(refresh_token: str) -> dict:
    """Exchange a refresh token for a fresh access token."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            GOOGLE_TOKEN_URL,
            data={
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    if resp.status_code >= 400:
        logger.error(f"Google token refresh failed: {resp.status_code} {resp.text}")
        return {"error": resp.text}
    return resp.json()


async def get_valid_google_token(user_id: str) -> str | None:
    """Get an access token, auto-refreshing if expired. Returns None if no Google config.

    Serialized per-user via asyncio.Lock to prevent Cosmos 449 conflicts from concurrent
    token refreshes (e.g. two parallel API calls both trying to write the new token).
    """
    # Fast path — read current token without holding the lock if it's still valid.
    lumen = await get_lumen(user_id)
    if not is_google_connected(lumen):
        return None
    cfg = lumen["google_config"]
    expires_at = cfg.get("expires_at", 0)
    if expires_at > int(time.time()) + 30:
        return decrypt_password(cfg["access_token_encrypted"])

    # Slow path — only one refresh per user at a time.
    async with _get_refresh_lock(user_id):
        # Re-read lumen INSIDE the lock — another concurrent caller may have just refreshed.
        lumen = await get_lumen(user_id)
        if not is_google_connected(lumen):
            return None
        cfg = lumen["google_config"]
        expires_at = cfg.get("expires_at", 0)
        if expires_at > int(time.time()) + 30:
            return decrypt_password(cfg["access_token_encrypted"])

        refresh_token = decrypt_password(cfg["refresh_token_encrypted"])
        if not refresh_token:
            logger.warning(f"No refresh token for user {user_id}")
            return None
        fresh = await _refresh_token(refresh_token)
        if "error" in fresh:
            return None
        new_access = fresh.get("access_token")
        new_expires_in = fresh.get("expires_in", 3600)
        cfg["access_token_encrypted"] = encrypt_password(new_access)
        cfg["expires_at"] = int(time.time()) + max(new_expires_in - 60, 60)
        if fresh.get("refresh_token"):
            cfg["refresh_token_encrypted"] = encrypt_password(fresh["refresh_token"])
        try:
            await save_lumen(lumen)
        except Exception as e:
            # If save fails (e.g. transient Cosmos 449), still return the token —
            # caller can use it for this request; we'll re-refresh next time.
            logger.warning(f"Token persist failed (will retry on next refresh): {e}")
        return new_access


# ── HTTP helper ──────────────────────────────────────────────────────────────

async def _gmail_request(token: str, method: str, path: str,
                         params: dict | None = None, json: dict | None = None) -> dict:
    url = f"{GMAIL_API_BASE}{path}"
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.request(method, url, headers=headers, params=params, json=json)
        if resp.status_code >= 400:
            logger.warning(f"Gmail {method} {path} -> {resp.status_code}: {resp.text[:300]}")
            return {"error": resp.text, "status": resp.status_code}
        return resp.json() if resp.text else {}


# ── Message body parsing ─────────────────────────────────────────────────────

def _b64url_decode(data: str) -> bytes:
    # Gmail returns base64url with padding stripped.
    pad = (-len(data)) % 4
    return base64.urlsafe_b64decode(data + ("=" * pad))


def _walk_parts(payload: dict, prefer: str = "text/plain") -> str:
    """Find the best body text in a Gmail payload (recursive)."""
    mime_type = payload.get("mimeType", "")
    body = payload.get("body", {}) or {}
    data = body.get("data")

    if data and mime_type == prefer:
        try:
            return _b64url_decode(data).decode("utf-8", errors="replace")
        except Exception:
            pass

    # Recurse into parts
    parts = payload.get("parts") or []
    for p in parts:
        found = _walk_parts(p, prefer)
        if found:
            return found

    # Fallback: any plain text data we can find
    if data:
        try:
            return _b64url_decode(data).decode("utf-8", errors="replace")
        except Exception:
            pass
    return ""


def _strip_html(html: str) -> str:
    """Very light HTML → text. Avoids a BeautifulSoup dependency just for this."""
    # Remove script/style blocks first
    html = re.sub(r"<script[\s\S]*?</script>", "", html, flags=re.IGNORECASE)
    html = re.sub(r"<style[\s\S]*?</style>", "", html, flags=re.IGNORECASE)
    # Replace block tags with newlines
    html = re.sub(r"</?(p|div|br|tr|li|h[1-6])[^>]*>", "\n", html, flags=re.IGNORECASE)
    # Strip remaining tags
    text = re.sub(r"<[^>]+>", "", html)
    # Decode common entities
    text = (text.replace("&nbsp;", " ").replace("&amp;", "&")
                .replace("&lt;", "<").replace("&gt;", ">")
                .replace("&quot;", '"').replace("&#39;", "'"))
    # Collapse whitespace
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _extract_header(headers: list[dict], name: str) -> str:
    for h in headers or []:
        if (h.get("name") or "").lower() == name.lower():
            return h.get("value") or ""
    return ""


def _format_message(msg: dict) -> dict:
    """Turn a Gmail API message into a Lumen-friendly dict."""
    payload = msg.get("payload", {}) or {}
    headers = payload.get("headers", []) or []
    subject = _extract_header(headers, "Subject")
    from_hdr = _extract_header(headers, "From")
    date_hdr = _extract_header(headers, "Date")
    to_hdr = _extract_header(headers, "To")

    # Sender name + email
    sender_name = from_hdr
    sender_email = ""
    name_email = email.utils.parseaddr(from_hdr)
    if name_email[1]:
        sender_email = name_email[1]
        sender_name = name_email[0] or name_email[1]

    body_text = _walk_parts(payload, prefer="text/plain")
    if not body_text:
        body_html = _walk_parts(payload, prefer="text/html")
        if body_html:
            body_text = _strip_html(body_html)

    snippet = msg.get("snippet", "")
    return {
        "id": msg.get("id"),
        "thread_id": msg.get("threadId"),
        "subject": subject or "(no subject)",
        "sender": sender_name or "(unknown)",
        "senderEmail": sender_email,
        "to": to_hdr,
        "date": date_hdr,
        "snippet": snippet,
        "body": body_text,
        "label_ids": msg.get("labelIds", []),
    }


# ── List / read / search ─────────────────────────────────────────────────────

async def list_inbox(token: str, query: str = "", limit: int = 10) -> list[dict]:
    """List inbox messages (newest first). Empty query → recent inbox."""
    q = query.strip() if query else "in:inbox"
    res = await _gmail_request(token, "GET", "/messages",
                                params={"q": q, "maxResults": limit})
    if "error" in res:
        return []
    msg_refs = res.get("messages", []) or []
    out = []
    for ref in msg_refs:
        msg = await _gmail_request(token, "GET", f"/messages/{ref['id']}",
                                    params={"format": "full"})
        if "error" in msg:
            continue
        out.append(_format_message(msg))
    return out


async def get_message(token: str, msg_id: str) -> dict:
    msg = await _gmail_request(token, "GET", f"/messages/{msg_id}", params={"format": "full"})
    if "error" in msg:
        return {"error": msg.get("error", "Could not fetch message")}
    return _format_message(msg)


async def search_gmail(token: str, query: str, limit: int = 10) -> list[dict]:
    """Gmail-native search (supports from:, subject:, is:unread, etc.)."""
    return await list_inbox(token, query=query, limit=limit)


# ── Send / reply ─────────────────────────────────────────────────────────────

def _build_raw_message(to: str, subject: str, body: str, sender_email: str = "",
                       in_reply_to: str = "", references: str = "") -> str:
    msg = MIMEText(body or "", _charset="utf-8")
    msg["To"] = to
    msg["Subject"] = subject
    if sender_email:
        msg["From"] = sender_email
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
    if references:
        msg["References"] = references
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii").rstrip("=")
    return raw


async def send_gmail(token: str, to: str, subject: str, body: str,
                      reply_to_msg_id: str | None = None,
                      sender_email: str = "") -> dict:
    """Send a Gmail message. If reply_to_msg_id is set, threads under that message."""
    if not to or not body:
        return {"error": "Missing 'to' or 'body'"}

    in_reply_to = ""
    references = ""
    thread_id = None

    if reply_to_msg_id:
        original = await _gmail_request(token, "GET", f"/messages/{reply_to_msg_id}",
                                          params={"format": "metadata",
                                                  "metadataHeaders": "Message-ID,References"})
        if "error" not in original:
            headers = (original.get("payload", {}) or {}).get("headers", [])
            in_reply_to = _extract_header(headers, "Message-ID")
            existing_refs = _extract_header(headers, "References")
            references = (existing_refs + " " + in_reply_to).strip() if in_reply_to else existing_refs
            thread_id = original.get("threadId")

    raw = _build_raw_message(to, subject, body, sender_email, in_reply_to, references)
    payload: dict = {"raw": raw}
    if thread_id:
        payload["threadId"] = thread_id

    res = await _gmail_request(token, "POST", "/messages/send", json=payload)
    if "error" in res:
        return {"error": res.get("error", "Send failed"), "status": "failed"}
    return {
        "status": "sent",
        "id": res.get("id"),
        "thread_id": res.get("threadId"),
        "to": to,
        "subject": subject,
        "body": body,
    }


# ── Summarize ────────────────────────────────────────────────────────────────

async def summarize_message(token: str, msg_id: str, instruction: str = "", user_id: str = "") -> str:
    msg = await get_message(token, msg_id)
    if msg.get("error"):
        return f"Could not read message: {msg['error']}"
    body = msg.get("body", "") or msg.get("snippet", "")
    if not body.strip():
        return f"📧 **{msg.get('subject', 'Untitled')}** has no readable body."

    from app.agents.calendar_agent import _get_client
    from app.agents.prompt_kit import build_agent_prompt
    sys = build_agent_prompt(
        role="Gmail Reading Assistant",
        mission="Read a single Gmail message and give the student a fast, accurate summary so they never have to open their inbox.",
        capabilities=[
            "Condense an email down to its key points.",
            "Surface action items, requests, and deadlines.",
            "Follow a specific instruction about the email when the user gives one.",
        ],
        rules=[
            "If the user gave a specific instruction, follow it exactly; otherwise default to a 2-3 sentence summary.",
            "Call out any action items or deadlines explicitly.",
            "Stay faithful to the email — never invent facts, names, or dates.",
            "Be concise and well structured; skip preambles like 'Here is the summary'.",
        ],
        output_format="Plain text — a short summary, optionally followed by a brief 'Action items:' list when relevant.",
    )
    user = (
        f"FROM: {msg.get('sender', '')} <{msg.get('senderEmail', '')}>\n"
        f"SUBJECT: {msg.get('subject', '')}\n"
        f"DATE: {msg.get('date', '')}\n\n"
        f"BODY:\n{body[:8000]}\n\n"
        f"INSTRUCTION: {instruction or 'Summarize this email.'}"
    )
    client = _get_client()
    agent = client.as_agent(name="GmailSummarizer", instructions=sys)
    _t0 = time.perf_counter()
    result = await agent.run(user)
    _latency_ms = (time.perf_counter() - _t0) * 1000
    reply = str(result).strip()

    # Best-effort token accounting for sub-agent usage.
    if user_id:
        try:
            from app.lumen.token_tracker import record_usage, estimate_tokens
            p = estimate_tokens(sys + "\n" + user)
            c = estimate_tokens(reply)
            await record_usage(user_id, p, c, model="agent_framework (estimated)", source="gmail", latency_ms=_latency_ms)
        except Exception:
            pass

    return reply
