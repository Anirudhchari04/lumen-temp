"""Graph Mail — Real Outlook email reading via Microsoft Graph API.

Uses the user's delegated OAuth token to read their actual Outlook inbox.
Requires Mail.Read permission on the Entra app registration.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone as _tz

import httpx

logger = logging.getLogger(__name__)
UTC = _tz.utc


async def list_inbox(user_token: str, filter_unread: bool = False, limit: int = 20) -> list[dict]:
    """Fetch emails from Outlook inbox using Microsoft Graph API.

    Args:
        user_token: User's delegated OAuth token
        filter_unread: Only return unread messages if True
        limit: Max number of emails to return (1-50)

    Returns: List of email dicts with id, subject, from, received_at, is_read, preview
    """
    if not user_token:
        logger.warning("list_inbox: No user token provided")
        return []

    limit = min(limit, 50)  # Graph API max

    # Build filter query
    filters = []
    if filter_unread:
        filters.append("isRead eq false")
    filter_query = " and ".join(filters) if filters else None

    url = "https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages"
    params = {
        "$select": "id,subject,from,receivedDateTime,isRead,bodyPreview,hasAttachments",
        "$orderby": "receivedDateTime desc",
        "$top": limit,
    }
    if filter_query:
        params["$filter"] = filter_query

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                url,
                params=params,
                headers={"Authorization": f"Bearer {user_token}"},
            )
            if resp.status_code == 401:
                logger.warning("list_inbox: Token expired or invalid (401)")
                return []
            if resp.status_code == 403:
                logger.warning("list_inbox: Permission denied (403) — ensure Mail.Read scope is granted")
                return []
            resp.raise_for_status()
            data = resp.json()
            emails = []
            for msg in data.get("value", []):
                sender = msg.get("from", {}).get("emailAddress", {})
                emails.append({
                    "id": msg.get("id", ""),
                    "subject": msg.get("subject", "(no subject)"),
                    "from": sender.get("name", ""),
                    "from_email": sender.get("address", ""),
                    "received_at": msg.get("receivedDateTime", ""),
                    "is_read": msg.get("isRead", False),
                    "preview": msg.get("bodyPreview", ""),
                    "has_attachments": msg.get("hasAttachments", False),
                    "source": "outlook",
                })
            logger.info(f"list_inbox: Fetched {len(emails)} emails")
            return emails
    except httpx.HTTPError as e:
        logger.warning(f"list_inbox: HTTP error: {e}")
        return []
    except Exception as e:
        logger.warning(f"list_inbox: Error: {e}")
        return []


async def get_email(user_token: str, message_id: str) -> dict | None:
    """Fetch a single email by ID with full body.

    Returns: Email dict with full content, or None on error
    """
    if not user_token or not message_id:
        return None

    url = f"https://graph.microsoft.com/v1.0/me/messages/{message_id}"
    params = {
        "$select": "id,subject,from,toRecipients,ccRecipients,receivedDateTime,isRead,body,hasAttachments",
    }

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                url,
                params=params,
                headers={"Authorization": f"Bearer {user_token}"},
            )
            if resp.status_code == 404:
                logger.warning(f"get_email: Message {message_id} not found")
                return None
            resp.raise_for_status()
            msg = resp.json()
            sender = msg.get("from", {}).get("emailAddress", {})
            to_recips = msg.get("toRecipients", [])
            to_list = [r.get("emailAddress", {}).get("address", "") for r in to_recips]
            body_obj = msg.get("body", {})
            return {
                "id": msg.get("id", ""),
                "subject": msg.get("subject", "(no subject)"),
                "from": sender.get("name", ""),
                "from_email": sender.get("address", ""),
                "to": to_list,
                "received_at": msg.get("receivedDateTime", ""),
                "is_read": msg.get("isRead", False),
                "body": body_obj.get("content", ""),
                "body_type": body_obj.get("contentType", "text"),
                "has_attachments": msg.get("hasAttachments", False),
                "source": "outlook",
            }
    except Exception as e:
        logger.warning(f"get_email: Error fetching {message_id}: {e}")
        return None


async def mark_as_read(user_token: str, message_ids: list[str]) -> int:
    """Mark messages as read.

    Returns: Number of messages successfully marked
    """
    if not user_token or not message_ids:
        return 0

    success_count = 0
    for msg_id in message_ids:
        url = f"https://graph.microsoft.com/v1.0/me/messages/{msg_id}"
        body = {"isRead": True}

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.patch(
                    url,
                    json=body,
                    headers={"Authorization": f"Bearer {user_token}"},
                )
                if resp.status_code in (200, 204):
                    success_count += 1
                else:
                    logger.warning(f"mark_as_read: Failed for {msg_id}: {resp.status_code}")
        except Exception as e:
            logger.warning(f"mark_as_read: Error for {msg_id}: {e}")

    logger.info(f"mark_as_read: Marked {success_count}/{len(message_ids)} as read")
    return success_count


async def delete_email(user_token: str, message_id: str) -> bool:
    """Delete a message permanently.

    Returns: True if deleted, False otherwise
    """
    if not user_token or not message_id:
        return False

    url = f"https://graph.microsoft.com/v1.0/me/messages/{message_id}"

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.delete(
                url,
                headers={"Authorization": f"Bearer {user_token}"},
            )
            if resp.status_code in (200, 204):
                logger.info(f"delete_email: Deleted {message_id}")
                return True
            else:
                logger.warning(f"delete_email: Failed to delete {message_id}: {resp.status_code}")
                return False
    except Exception as e:
        logger.warning(f"delete_email: Error: {e}")
        return False


async def search_emails(user_token: str, query: str, limit: int = 20) -> list[dict]:
    """Search emails by subject or body content.

    Args:
        user_token: User's delegated OAuth token
        query: Search term (e.g., "blockchain" or "from:professor@domain.com")
        limit: Max results

    Returns: List of matching emails
    """
    if not user_token or not query:
        return []

    limit = min(limit, 50)

    url = "https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages"
    # Graph supports KQL-style queries in $search parameter
    params = {
        "$search": f'"{query}"',
        "$select": "id,subject,from,receivedDateTime,isRead,bodyPreview",
        "$orderby": "receivedDateTime desc",
        "$top": limit,
    }

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                url,
                params=params,
                headers={"Authorization": f"Bearer {user_token}"},
            )
            if resp.status_code != 200:
                logger.warning(f"search_emails: Query failed: {resp.status_code}")
                return []
            data = resp.json()
            emails = []
            for msg in data.get("value", []):
                sender = msg.get("from", {}).get("emailAddress", {})
                emails.append({
                    "id": msg.get("id", ""),
                    "subject": msg.get("subject", "(no subject)"),
                    "from": sender.get("name", ""),
                    "from_email": sender.get("address", ""),
                    "received_at": msg.get("receivedDateTime", ""),
                    "is_read": msg.get("isRead", False),
                    "preview": msg.get("bodyPreview", ""),
                    "source": "outlook",
                })
            logger.info(f"search_emails: Found {len(emails)} matches for '{query}'")
            return emails
    except Exception as e:
        logger.warning(f"search_emails: Error: {e}")
        return []
