"""Google Drive agent — read/search/create/summarize files. Class-based: `DriveAgent` holds the logic as `self` methods (logic unchanged)."""
from __future__ import annotations

from app.agents.base import BaseAgent, registry
from app.agents.intents import Intent
from app.agents.handlers._common import _google_consent_response


class DriveAgent(BaseAgent):
    name = "drive"
    intents = (Intent.DRIVE, Intent.ONEDRIVE)
    description = "Google Drive / OneDrive file operations"
    # Offline keyword fallbacks owned by this agent (the LLM router is primary).
    KEYWORDS = (
        "google drive", "my drive", "drive doc", "drive sheet", "drive file",
        "in my drive", "from my drive", "search drive", "search my drive",
        "my google doc", "my google docs", "google doc", "google docs",
        "google sheet", "google sheets", "my google sheet",
        "in drive", "from drive", "to drive",
        "my pdf", "my pdfs",
        "create a google doc", "make a google doc", "new google doc",
    )
    ONEDRIVE_KEYWORDS = (
        "onedrive", "one drive", "my drive", "my files", "my documents",
        "list files", "list my files", "recent files", "files shared with me",
        "shared with me", "search onedrive", "search my drive", "search my files",
        "create folder", "make folder", "new folder", "files in my drive",
        "what's in my drive", "what is in my drive", "drive files", "drive folder",
    )

    async def handle(self, user_id: str, message: str) -> dict:
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

    async def broker(self, env: dict) -> dict:
        return await self.handle(env["user_id"], env["message"])


agent = DriveAgent()
registry.register_agent(agent)

# Back-compat alias for `from app.agents.interaction_manager import _handle_drive`.
_handle_drive = agent.handle
