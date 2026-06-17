"""Graph Agent — Microsoft Graph API bridge for Outlook and OneDrive.

All calls are made using the user's own delegated token (acquired via MSAL in
the browser).  No application permissions / admin consent required.

Scopes needed (all user-delegated):
  Mail.Read            — read messages, rules, categories, delta
  MailboxSettings.Read — inbox rules
  Files.Read           — OneDrive list, recent, shared, search
  Files.ReadWrite      — OneDrive create folder
  Place.Read.All       — conference rooms (may need admin in some tenants)
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

GRAPH = "https://graph.microsoft.com/v1.0"


# ── Generic helper ────────────────────────────────────────────────────────────

async def _get(token: str, path: str, params: dict | None = None) -> dict | list | None:
    """GET from Graph API; returns parsed JSON or None on error."""
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(
                f"{GRAPH}{path}",
                params=params or {},
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            )
            if r.status_code == 200:
                return r.json()
            logger.warning(f"Graph GET {path} → {r.status_code}: {r.text[:200]}")
            return {"_error": r.status_code, "_message": r.text[:300]}
    except Exception as e:
        logger.warning(f"Graph GET {path} exception: {e}")
        return {"_error": "exception", "_message": str(e)}


async def _post(token: str, path: str, body: dict) -> dict | None:
    """POST to Graph API."""
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(
                f"{GRAPH}{path}",
                json=body,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            )
            if r.status_code in (200, 201):
                return r.json()
            logger.warning(f"Graph POST {path} → {r.status_code}: {r.text[:200]}")
            return {"_error": r.status_code, "_message": r.text[:300]}
    except Exception as e:
        logger.warning(f"Graph POST {path} exception: {e}")
        return {"_error": "exception", "_message": str(e)}


def _err(data: Any) -> str | None:
    """Return human-readable error string if data is an error dict, else None."""
    if isinstance(data, dict) and "_error" in data:
        code = data["_error"]
        msg = data.get("_message", "")
        if code == 403:
            return "I don't have permission for that. You may need to grant additional consent — open your **Profile** (top-right avatar) and click **Connect Microsoft Account**."
        if code == 401:
            return "Your Graph session token expired. Open your **Profile** and click **Connect Microsoft Account** to refresh it."
        if code == 404:
            # itemNotFound on mail/drive endpoints means Mail.Read / Files.Read scope is missing
            if "itemNotFound" in msg or "Item not found" in msg:
                return (
                    "I can't access your Microsoft 365 data yet — `Mail.Read` / `Files.Read` permission hasn't been granted.\n\n"
                    "**Quick fix:** open your **Profile** (avatar, top-right) → scroll to **Outlook & OneDrive** "
                    "→ click **\"Tenant blocks consent? Paste token instead\"** and follow the steps to paste "
                    "a token from [Graph Explorer](https://developer.microsoft.com/graph/graph-explorer)."
                )
        return f"Graph API error ({code}): {msg[:120]}"
    return None


# ── Mail helpers ──────────────────────────────────────────────────────────────

def _fmt_messages(messages: list[dict], limit: int = 10) -> str:
    """Format a list of Graph message objects into readable text."""
    if not messages:
        return "No messages found."
    lines = []
    for m in messages[:limit]:
        sender = m.get("from", {}).get("emailAddress", {})
        from_str = f"{sender.get('name', '')} <{sender.get('address', '')}>"
        date = (m.get("receivedDateTime") or "")[:10]
        subject = m.get("subject") or "(no subject)"
        preview = (m.get("bodyPreview") or "")[:120]
        read_flag = "" if m.get("isRead", True) else "🔵 "
        lines.append(f"{read_flag}**{subject}**  \n  From: {from_str} · {date}  \n  {preview}")
    return "\n\n".join(lines)


async def get_high_importance_mail(token: str, top: int = 15) -> str:
    """GET /me/messages?$filter=importance eq 'high'"""
    data = await _get(token, "/me/messages", {
        "$filter": "importance eq 'high'",
        "$select": "subject,from,receivedDateTime,importance,bodyPreview,isRead",
        "$top": top,
        "$orderby": "receivedDateTime desc",
    })
    if err := _err(data):
        return err
    msgs = data.get("value", []) if isinstance(data, dict) else []
    if not msgs:
        return "You have no high-importance emails right now. 🎉"
    return f"📌 **High-importance emails ({len(msgs)}):**\n\n" + _fmt_messages(msgs)


async def get_mail_from_address(token: str, address: str, top: int = 15) -> str:
    """GET /me/messages?$filter=from/emailAddress/address eq '{address}'"""
    data = await _get(token, "/me/messages", {
        "$filter": f"from/emailAddress/address eq '{address}'",
        "$select": "subject,from,receivedDateTime,bodyPreview,isRead",
        "$top": top,
        "$orderby": "receivedDateTime desc",
    })
    if err := _err(data):
        return err
    msgs = data.get("value", []) if isinstance(data, dict) else []
    if not msgs:
        return f"No emails from **{address}** found in your inbox."
    return f"📬 **Emails from {address} ({len(msgs)}):**\n\n" + _fmt_messages(msgs)


async def search_mail(token: str, query: str, top: int = 15) -> str:
    """GET /me/messages?$search='{query}'"""
    data = await _get(token, "/me/messages", {
        "$search": f'"{query}"',
        "$select": "subject,from,receivedDateTime,bodyPreview,isRead",
        "$top": top,
    })
    if err := _err(data):
        return err
    msgs = data.get("value", []) if isinstance(data, dict) else []
    if not msgs:
        return f"No emails found matching **\"{query}\"**."
    return f"🔍 **Emails matching \"{query}\" ({len(msgs)}):**\n\n" + _fmt_messages(msgs)


async def get_mail_delta(token: str, top: int = 20) -> str:
    """GET /me/mailFolders/Inbox/messages/delta — latest unread changes."""
    data = await _get(token, "/me/mailFolders/Inbox/messages/delta", {
        "$select": "subject,from,receivedDateTime,isRead,importance",
        "$top": top,
    })
    if err := _err(data):
        return err
    msgs = data.get("value", []) if isinstance(data, dict) else []
    unread = [m for m in msgs if not m.get("isRead", True)]
    if not unread:
        return "📭 No new unread messages in your inbox right now."
    lines = [f"📬 **{len(unread)} unread message(s) in inbox:**\n"]
    for m in unread[:15]:
        sender = m.get("from", {}).get("emailAddress", {})
        lines.append(f"• **{m.get('subject','(no subject)')}** — {sender.get('name','?')} · {(m.get('receivedDateTime') or '')[:10]}")
    return "\n".join(lines)


async def get_inbox_rules(token: str) -> str:
    """GET /me/mailFolders/Inbox/messageRules"""
    data = await _get(token, "/me/mailFolders/Inbox/messageRules")
    if err := _err(data):
        return err
    rules = data.get("value", []) if isinstance(data, dict) else []
    if not rules:
        return "You have no inbox rules set up."
    lines = [f"📋 **Your inbox rules ({len(rules)}):**\n"]
    for r in rules:
        enabled = "✅" if r.get("isEnabled", True) else "❌"
        lines.append(f"{enabled} **{r.get('displayName', 'Unnamed rule')}**")
        conds = r.get("conditions", {})
        acts = r.get("actions", {})
        if conds.get("senderContains"):
            lines.append(f"  · From contains: {', '.join(conds['senderContains'])}")
        if conds.get("subjectContains"):
            lines.append(f"  · Subject contains: {', '.join(conds['subjectContains'])}")
        if acts.get("moveToFolder"):
            lines.append(f"  · Move to folder: {acts['moveToFolder']}")
        if acts.get("delete"):
            lines.append(f"  · Delete message")
        if acts.get("markAsRead"):
            lines.append(f"  · Mark as read")
    return "\n".join(lines)


async def get_outlook_categories(token: str) -> str:
    """GET /me/outlook/masterCategories"""
    data = await _get(token, "/me/outlook/masterCategories")
    if err := _err(data):
        return err
    cats = data.get("value", []) if isinstance(data, dict) else []
    if not cats:
        return "You haven't set up any Outlook categories yet."
    lines = [f"🏷️ **Your Outlook categories ({len(cats)}):**\n"]
    for c in cats:
        color = c.get("color", "none")
        lines.append(f"• **{c.get('displayName', '?')}** — {color}")
    return "\n".join(lines)


async def get_email_headers(token: str, message_id: str) -> str:
    """GET /me/messages/{id}?$select=internetMessageHeaders"""
    data = await _get(token, f"/me/messages/{message_id}", {
        "$select": "subject,internetMessageHeaders",
    })
    if err := _err(data):
        return err
    headers = data.get("internetMessageHeaders", []) if isinstance(data, dict) else []
    subject = data.get("subject", "") if isinstance(data, dict) else ""
    if not headers:
        return f"No headers found for message: {subject}"
    # Show key headers only
    key_names = {"message-id", "x-ms-exchange-organization-scl", "received", "from",
                 "to", "subject", "date", "x-originating-ip", "x-mailer",
                 "authentication-results", "dkim-signature"}
    lines = [f"📨 **Headers for: {subject}**\n"]
    shown = 0
    for h in headers:
        name = (h.get("name") or "").lower()
        if name in key_names:
            lines.append(f"`{h.get('name')}`: {(h.get('value') or '')[:120]}")
            shown += 1
    if not shown:
        for h in headers[:10]:
            lines.append(f"`{h.get('name')}`: {(h.get('value') or '')[:120]}")
    return "\n".join(lines)


async def get_conference_rooms(token: str) -> str:
    """GET /places/microsoft.graph.room — requires Place.Read.All"""
    data = await _get(token, "/places/microsoft.graph.room", {"$top": 20})
    if err := _err(data):
        return err
    rooms = data.get("value", []) if isinstance(data, dict) else []
    if not rooms:
        return "No conference rooms found in your directory."
    lines = [f"🏢 **Conference rooms ({len(rooms)}):**\n"]
    for r in rooms:
        cap = r.get("capacity", "?")
        building = r.get("building", "")
        floor_label = r.get("floorLabel", "")
        location = f"{building} {floor_label}".strip()
        email = r.get("emailAddress", "")
        name = r.get("displayName", "?")
        lines.append(f"• **{name}** — capacity {cap}{f' · {location}' if location else ''}{f' · {email}' if email else ''}")
    return "\n".join(lines)


# ── OneDrive helpers ──────────────────────────────────────────────────────────

def _fmt_drive_items(items: list[dict], limit: int = 20) -> str:
    """Format Graph driveItem objects into readable text."""
    if not items:
        return "No files found."
    lines = []
    for item in items[:limit]:
        name = item.get("name", "?")
        is_folder = "folder" in item
        icon = "📁" if is_folder else "📄"
        size = item.get("size", 0)
        size_str = f"{size // 1024} KB" if size and not is_folder else ""
        modified = (item.get("lastModifiedDateTime") or "")[:10]
        url = (item.get("webUrl") or "")
        link = f"[Open]({url})" if url else ""
        lines.append(f"{icon} **{name}**{f' — {size_str}' if size_str else ''} · {modified} {link}")
    return "\n".join(lines)


async def list_drive_items(token: str, folder_path: str = "/") -> str:
    """GET /me/drive/root/children (or specific folder)."""
    if folder_path and folder_path != "/":
        path = f"/me/drive/root:/{folder_path.strip('/')}:/children"
    else:
        path = "/me/drive/root/children"
    data = await _get(token, path, {
        "$select": "name,size,lastModifiedDateTime,folder,webUrl,file",
        "$top": 50,
        "$orderby": "lastModifiedDateTime desc",
    })
    if err := _err(data):
        return err
    items = data.get("value", []) if isinstance(data, dict) else []
    folder_label = f"**{folder_path}**" if folder_path != "/" else "**root**"
    if not items:
        return f"The {folder_label} folder appears to be empty."
    return f"📂 **OneDrive — {folder_label} ({len(items)} items):**\n\n" + _fmt_drive_items(items)


async def get_recent_files(token: str, top: int = 20) -> str:
    """GET /me/drive/recent"""
    data = await _get(token, "/me/drive/recent", {"$top": top})
    if err := _err(data):
        return err
    items = data.get("value", []) if isinstance(data, dict) else []
    if not items:
        return "No recent files found in OneDrive."
    return f"🕐 **Your recent OneDrive files ({len(items)}):**\n\n" + _fmt_drive_items(items)


async def get_shared_with_me(token: str, top: int = 20) -> str:
    """GET /me/drive/sharedWithMe"""
    data = await _get(token, "/me/drive/sharedWithMe", {"$top": top})
    if err := _err(data):
        return err
    items = data.get("value", []) if isinstance(data, dict) else []
    if not items:
        return "No files have been shared with you in OneDrive."
    lines = [f"🔗 **Files shared with you ({len(items)}):**\n"]
    for item in items[:20]:
        name = item.get("name", "?")
        shared_by = item.get("remoteItem", {}).get("shared", {}).get("sharedBy", {}).get("user", {}).get("displayName", "?")
        url = item.get("webUrl") or item.get("remoteItem", {}).get("webUrl", "")
        link = f"[Open]({url})" if url else ""
        lines.append(f"• **{name}** — shared by {shared_by} {link}")
    return "\n".join(lines)


async def search_drive(token: str, query: str, top: int = 20) -> str:
    """GET /me/drive/root/search(q='{query}')"""
    data = await _get(token, f"/me/drive/root/search(q='{query}')", {
        "$select": "name,size,lastModifiedDateTime,folder,webUrl,file",
        "$top": top,
    })
    if err := _err(data):
        return err
    items = data.get("value", []) if isinstance(data, dict) else []
    if not items:
        return f"No files found in OneDrive matching **\"{query}\"**."
    return f"🔍 **OneDrive search: \"{query}\" ({len(items)} results):**\n\n" + _fmt_drive_items(items)


async def create_drive_folder(token: str, folder_name: str, parent_path: str = "/") -> str:
    """POST /me/drive/root/children — create a folder."""
    if parent_path and parent_path != "/":
        path = f"/me/drive/root:/{parent_path.strip('/')}:/children"
    else:
        path = "/me/drive/root/children"
    body = {
        "name": folder_name,
        "folder": {},
        "@microsoft.graph.conflictBehavior": "rename",
    }
    data = await _post(token, path, body)
    if err := _err(data):
        return err
    if isinstance(data, dict) and "id" in data:
        url = data.get("webUrl", "")
        link = f" [Open]({url})" if url else ""
        return f"✅ Folder **{data.get('name', folder_name)}** created in OneDrive.{link}"
    return f"✅ Folder **{folder_name}** created."


# ── Natural language query extraction ────────────────────────────────────────

def extract_email_address(msg: str) -> str:
    """Extract an email address from a natural language message."""
    import re
    m = re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", msg)
    return m.group(0) if m else ""


def extract_search_query(msg: str, prefix_kw: list[str]) -> str:
    """Extract the search term after any of the given prefix keywords."""
    import re
    msg_lower = msg.lower()
    for kw in sorted(prefix_kw, key=len, reverse=True):
        if kw in msg_lower:
            idx = msg_lower.index(kw) + len(kw)
            remainder = msg[idx:].strip().strip("'\"")
            # Remove common trailing noise
            remainder = re.sub(r"\s+(in my|on my|in outlook|in onedrive|please).*$", "", remainder, flags=re.IGNORECASE)
            return remainder.strip()
    return ""


def extract_folder_name(msg: str) -> str:
    """Extract folder name from 'create folder X' / 'make a folder called X'."""
    import re
    patterns = [
        r'(?:folder|directory)\s+(?:named?|called?|with name)?\s*["\']?([^"\']+?)["\']?(?:\s+in|\s+on|\s*$)',
        r'(?:create|make|add)\s+(?:a\s+)?(?:folder|directory)\s+["\']?([^"\']+?)["\']?(?:\s+in|\s+on|\s*$)',
    ]
    for pat in patterns:
        m = re.search(pat, msg, re.IGNORECASE)
        if m:
            return m.group(1).strip()
    return ""


def get_agent_card(base_url: str = "") -> "AgentCard":
    from app.protocols.models import AgentCard, AgentProvider, AgentInterface, AgentCapabilities, AgentSkill
    return AgentCard(
        name="Microsoft Graph Agent",
        description="Read Outlook email and OneDrive files via Microsoft Graph API. NOTE: Requires one-time admin consent on Microsoft tenant (72f988bf-86f1-41af-91ab-2d7cd011db47).",
        version="1.0.0",
        documentationUrl=f"{base_url}/docs/graph",
        provider=AgentProvider(organization="Lumen Network", url=base_url),
        supportedInterfaces=[AgentInterface(url=f"{base_url}/a2a/graph")],
        capabilities=AgentCapabilities(streaming=False, pushNotifications=False),
        defaultInputModes=["text/plain"],
        defaultOutputModes=["text/plain"],
        securitySchemes={
            "lumenJwt": {"httpAuthSecurityScheme": {"scheme": "bearer", "bearerFormat": "JWT"}},
            "graphDelegated": {
                "oauth2SecurityScheme": {
                    "description": "Microsoft Graph delegated token. Requires admin pre-consent on tenant 72f988bf. Scopes: Mail.Read, Files.Read.",
                    "flows": {
                        "authorizationCode": {
                            "authorizationUrl": "https://login.microsoftonline.com/72f988bf-86f1-41af-91ab-2d7cd011db47/oauth2/v2.0/authorize",
                            "tokenUrl": "https://login.microsoftonline.com/72f988bf-86f1-41af-91ab-2d7cd011db47/oauth2/v2.0/token",
                            "scopes": {"Mail.Read": "Read user mail", "Files.Read": "Read user files"}
                        }
                    }
                }
            },
        },
        securityRequirements=[{"lumenJwt": [], "graphDelegated": ["Mail.Read", "Files.Read"]}],
        skills=[
            AgentSkill(
                id="graph.read_email",
                name="Read Email",
                description="Read high-importance or recent emails from Outlook inbox",
                tags=["outlook", "email", "read", "inbox", "microsoft"],
                examples=["Show my important emails", "Check Outlook", "Read my inbox", "Show recent emails"],
            ),
            AgentSkill(
                id="graph.search_email",
                name="Search Email",
                description="Search Outlook emails by sender, keyword, or subject",
                tags=["outlook", "email", "search", "find", "microsoft"],
                examples=["Search emails from Manohar", "Find emails about the project", "Search for invoice emails"],
            ),
            AgentSkill(
                id="graph.list_drive",
                name="List OneDrive Files",
                description="List files and folders in OneDrive",
                tags=["onedrive", "files", "list", "drive", "microsoft"],
                examples=["Show my OneDrive files", "List files in my documents", "What's in my OneDrive?"],
            ),
            AgentSkill(
                id="graph.search_drive",
                name="Search OneDrive",
                description="Search for files in OneDrive by name or content",
                tags=["onedrive", "search", "files", "drive", "microsoft"],
                examples=["Find files about machine learning in OneDrive", "Search my drive for the report"],
            ),
        ],
    )
