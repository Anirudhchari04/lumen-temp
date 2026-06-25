"""Microsoft Graph handlers — Outlook mail + OneDrive. Class-based: `GraphAgent` holds the logic as `self` methods (logic unchanged).

This agent has no `@registry.agent` broker of its own; the gmail/drive
brokers cover the OUTLOOK/ONEDRIVE intents. interaction_manager re-exports
`_handle_outlook` and `_handle_onedrive` for backwards compatibility.
"""
from __future__ import annotations

from app.agents.base import BaseAgent, registry  # noqa: F401  (kept for parity with sibling handler modules)
from app.agents.intents import Intent


class GraphAgent(BaseAgent):
    name = "graph"

    async def handle_outlook(self, message: str, graph_token: str | None) -> dict:
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

    async def handle_onedrive(self, message: str, graph_token: str | None) -> dict:
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


agent = GraphAgent()

# Back-compat aliases (this agent has no broker, so it is NOT registered).
_handle_outlook = agent.handle_outlook
_handle_onedrive = agent.handle_onedrive
