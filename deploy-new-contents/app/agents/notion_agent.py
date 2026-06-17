"""Notion agent — read/write/search/summarize Notion pages and databases.

Auth: OAuth-based. Each user stores their access_token encrypted in
lumen.notion_config = {access_token_encrypted, workspace_name, workspace_id, bot_id}.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from app.agents.email_crypto import encrypt_password, decrypt_password
from app.lumen.core import get_lumen, save_lumen

logger = logging.getLogger(__name__)

NOTION_API_BASE = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"


# ── Connection helpers ───────────────────────────────────────────────────────

def is_notion_connected(lumen: dict | None) -> bool:
    if not lumen:
        return False
    cfg = lumen.get("notion_config") or {}
    return bool(cfg.get("access_token_encrypted"))


async def get_notion_token(user_id: str) -> str | None:
    lumen = await get_lumen(user_id)
    if not is_notion_connected(lumen):
        return None
    enc = lumen["notion_config"]["access_token_encrypted"]
    return decrypt_password(enc)


async def save_notion_config(user_id: str, access_token: str, workspace_name: str,
                              workspace_id: str, bot_id: str) -> dict:
    lumen = await get_lumen(user_id)
    if not lumen:
        raise ValueError(f"No Lumen for user {user_id}")
    lumen["notion_config"] = {
        "access_token_encrypted": encrypt_password(access_token),
        "workspace_name": workspace_name,
        "workspace_id": workspace_id,
        "bot_id": bot_id,
    }
    await save_lumen(lumen)
    return lumen["notion_config"]


async def disconnect_notion(user_id: str) -> bool:
    lumen = await get_lumen(user_id)
    if not lumen:
        return False
    lumen.pop("notion_config", None)
    await save_lumen(lumen)
    return True


# ── HTTP helper ──────────────────────────────────────────────────────────────

async def _notion_request(token: str, method: str, path: str,
                          json: dict | None = None) -> dict:
    headers = {
        "Authorization": f"Bearer {token}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }
    url = f"{NOTION_API_BASE}{path}"
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.request(method, url, headers=headers, json=json)
        if resp.status_code >= 400:
            logger.warning(f"Notion API {method} {path} -> {resp.status_code}: {resp.text}")
            return {"error": resp.text, "status": resp.status_code}
        return resp.json()


# ── Search / list ────────────────────────────────────────────────────────────

async def search_notion(token: str, query: str = "", limit: int = 10) -> list[dict]:
    """Search the user's Notion workspace. Empty query lists recent pages."""
    body: dict = {"page_size": limit}
    if query:
        body["query"] = query
    body["sort"] = {"timestamp": "last_edited_time", "direction": "descending"}
    res = await _notion_request(token, "POST", "/search", json=body)
    if "error" in res:
        return []
    return res.get("results", [])


def _page_title(page: dict) -> str:
    """Extract title from a Notion page object, trying 4 fallback paths.

    Notion's /v1/search returns mixed pages + database objects with varying shapes.
    """
    import re as _re_t

    # Path 1: standard — any property with type == "title"
    props = page.get("properties") or {}
    for prop in props.values():
        if isinstance(prop, dict) and prop.get("type") == "title":
            spans = prop.get("title") or []
            t = "".join(s.get("plain_text", "") for s in spans).strip()
            if t:
                return t

    # Path 2: well-known property keys ("Name", "Title", "title")
    for key in ("Name", "Title", "title"):
        prop = props.get(key)
        if isinstance(prop, dict):
            spans = prop.get("title") or []
            t = "".join(s.get("plain_text", "") for s in spans).strip()
            if t:
                return t

    # Path 3: top-level "title" array (databases + some special pages)
    root_title = page.get("title")
    if isinstance(root_title, list):
        t = "".join(s.get("plain_text", "") for s in root_title if isinstance(s, dict)).strip()
        if t:
            return t
    if isinstance(root_title, str) and root_title.strip():
        return root_title.strip()

    # Path 4: URL slug — "https://notion.so/Getting-Started-abc123<32hex>" → "Getting Started"
    url = page.get("url", "")
    if url:
        slug = url.rstrip("/").split("/")[-1]
        # Strip trailing 32-hex ID (with or without a leading dash)
        slug = _re_t.sub(r"-?[a-fA-F0-9]{32}$", "", slug)
        slug = slug.replace("-", " ").strip()
        # If the slug ended up empty (page has only a hex ID and no human title),
        # return "Untitled" rather than showing the raw hex.
        if slug:
            return slug.title()

    return "Untitled"


def _page_summary(page: dict) -> dict:
    return {
        "id": page.get("id"),
        "title": _page_title(page),
        "url": page.get("url"),
        "last_edited": page.get("last_edited_time"),
        "type": page.get("object"),  # "page" or "database"
    }


# ── Read page (blocks → markdown) ────────────────────────────────────────────

async def get_page_blocks(token: str, block_id: str, depth: int = 0) -> list[dict]:
    """Fetch all child blocks for a page or block. Recurses children up to depth 2."""
    if depth > 2:
        return []
    blocks: list[dict] = []
    next_cursor = None
    while True:
        path = f"/blocks/{block_id}/children?page_size=100"
        if next_cursor:
            path += f"&start_cursor={next_cursor}"
        res = await _notion_request(token, "GET", path)
        if "error" in res:
            break
        blocks.extend(res.get("results", []))
        if not res.get("has_more"):
            break
        next_cursor = res.get("next_cursor")
    return blocks


def _blocks_to_markdown(blocks: list[dict]) -> str:
    """Render a list of Notion blocks as Markdown."""
    out: list[str] = []

    def text_of(spans: list[dict]) -> str:
        return "".join(s.get("plain_text", "") for s in spans)

    for b in blocks:
        btype = b.get("type", "")
        content = b.get(btype, {})
        spans = content.get("rich_text", [])
        txt = text_of(spans)
        if btype == "paragraph":
            out.append(txt)
        elif btype == "heading_1":
            out.append(f"# {txt}")
        elif btype == "heading_2":
            out.append(f"## {txt}")
        elif btype == "heading_3":
            out.append(f"### {txt}")
        elif btype == "bulleted_list_item":
            out.append(f"- {txt}")
        elif btype == "numbered_list_item":
            out.append(f"1. {txt}")
        elif btype == "to_do":
            checked = "x" if content.get("checked") else " "
            out.append(f"- [{checked}] {txt}")
        elif btype == "quote":
            out.append(f"> {txt}")
        elif btype == "code":
            lang = content.get("language", "")
            out.append(f"```{lang}\n{txt}\n```")
        elif btype == "divider":
            out.append("---")
        elif btype == "callout":
            icon = content.get("icon", {}).get("emoji", "💡")
            out.append(f"{icon} {txt}")
        elif btype == "child_page":
            out.append(f"📄 **{content.get('title', 'Untitled')}**")
        elif btype == "child_database":
            out.append(f"📊 *(database: {content.get('title', 'Untitled')})*")
        elif txt:
            out.append(txt)
    return "\n\n".join(out)


async def read_page(token: str, page_id: str) -> dict:
    """Return {title, url, content_md} for a Notion page."""
    page = await _notion_request(token, "GET", f"/pages/{page_id}")
    if "error" in page:
        return {"error": page.get("error", "Could not fetch page")}
    blocks = await get_page_blocks(token, page_id)
    return {
        "id": page_id,
        "title": _page_title(page),
        "url": page.get("url"),
        "content_md": _blocks_to_markdown(blocks),
        "last_edited": page.get("last_edited_time"),
    }


# ── Create / append ──────────────────────────────────────────────────────────

def _text_block(text: str, block_type: str = "paragraph") -> dict:
    return {
        "object": "block",
        "type": block_type,
        block_type: {
            "rich_text": [{"type": "text", "text": {"content": text}}]
        },
    }


def _bullet_block(text: str) -> dict:
    return _text_block(text, "bulleted_list_item")


def _todo_block(text: str, checked: bool = False) -> dict:
    return {
        "object": "block",
        "type": "to_do",
        "to_do": {
            "rich_text": [{"type": "text", "text": {"content": text}}],
            "checked": checked,
        },
    }


async def create_page(token: str, title: str, content_lines: list[str] | None = None,
                       parent_page_id: str | None = None,
                       use_todos: bool = False) -> dict:
    """Create a new page. If parent_page_id is None, picks the first accessible page
    in the workspace as parent (Notion requires a parent — workspace-root pages need
    a workspace integration which most users don't grant).
    """
    if not parent_page_id:
        # Find any accessible page as parent
        pages = await search_notion(token, "", limit=20)
        page_only = [p for p in pages if p.get("object") == "page"]
        if not page_only:
            return {"error": "No accessible pages in your Notion workspace. Share at least one page with the Lumen integration first."}
        parent_page_id = page_only[0].get("id")

    blocks: list[dict] = []
    if content_lines:
        for line in content_lines:
            line = line.strip()
            if not line:
                continue
            if use_todos:
                blocks.append(_todo_block(line))
            elif line.startswith("- "):
                blocks.append(_bullet_block(line[2:]))
            else:
                blocks.append(_text_block(line))

    body = {
        "parent": {"page_id": parent_page_id},
        "properties": {
            "title": {"title": [{"type": "text", "text": {"content": title}}]}
        },
        "children": blocks,
    }
    res = await _notion_request(token, "POST", "/pages", json=body)
    if "error" in res:
        return res
    return {"id": res.get("id"), "url": res.get("url"), "title": title}


async def replace_page_content(token: str, page_id: str, lines: list[str],
                                use_todos: bool = False) -> dict:
    """Replace the entire body of a Notion page.

    Notion has no atomic "replace" — we (1) list child blocks, (2) delete each,
    (3) append the new ones. Destructive: rich formatting (tables, embeds,
    sub-pages, colors) is lost. If the delete phase partially fails, what
    remains is whatever blocks we couldn't delete + the new content appended.
    """
    # 1. List existing top-level blocks
    existing: list[dict] = []
    next_cursor = None
    while True:
        path = f"/blocks/{page_id}/children?page_size=100"
        if next_cursor:
            path += f"&start_cursor={next_cursor}"
        res = await _notion_request(token, "GET", path)
        if "error" in res:
            return {"error": f"Could not read existing content: {res.get('error', 'unknown')}"}
        existing.extend(res.get("results", []))
        if not res.get("has_more"):
            break
        next_cursor = res.get("next_cursor")

    # 2. Delete each block (Notion treats DELETE as "archive")
    deleted = 0
    failed = 0
    for block in existing:
        bid = block.get("id")
        if not bid:
            continue
        r = await _notion_request(token, "DELETE", f"/blocks/{bid}")
        if "error" in r:
            failed += 1
        else:
            deleted += 1

    # 3. Append the new content (reuses the same logic as append_to_page)
    new_blocks: list[dict] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if use_todos:
            new_blocks.append(_todo_block(line))
        else:
            new_blocks.append(_text_block(line))

    if new_blocks:
        res = await _notion_request(
            token, "PATCH", f"/blocks/{page_id}/children",
            json={"children": new_blocks},
        )
        if "error" in res:
            return {
                "error": f"Deleted {deleted} blocks but could not insert new content: {res.get('error', 'unknown')}",
                "deleted": deleted,
                "failed_delete": failed,
            }

    return {
        "ok": True,
        "deleted": deleted,
        "failed_delete": failed,
        "inserted": len(new_blocks),
    }


async def append_to_page(token: str, page_id: str, lines: list[str],
                          use_todos: bool = False) -> dict:
    blocks: list[dict] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if use_todos:
            blocks.append(_todo_block(line))
        else:
            blocks.append(_text_block(line))
    if not blocks:
        return {"error": "No content to append"}
    res = await _notion_request(token, "PATCH", f"/blocks/{page_id}/children",
                                 json={"children": blocks})
    if "error" in res:
        return res
    return {"ok": True, "appended": len(blocks)}


# ── Summarize via LLM ────────────────────────────────────────────────────────

async def summarize_page(token: str, page_id: str, instruction: str = "", user_id: str = "") -> str:
    """Read a page then ask the LLM to summarize it."""
    page = await read_page(token, page_id)
    if page.get("error"):
        return f"Could not read page: {page['error']}"
    content = page.get("content_md", "")
    if not content.strip():
        return f"📄 **{page.get('title', 'Untitled')}** is empty."

    from app.agents.calendar_agent import _get_client
    from app.agents.prompt_kit import build_agent_prompt
    client = _get_client()
    sys = build_agent_prompt(
        role="Notion Reading Assistant",
        mission="Read a single Notion page and give the student a clear, accurate summary of what it contains.",
        capabilities=[
            "Summarize notes, docs, and structured Notion pages.",
            "Organize the key points, using bullet points where they help.",
            "Follow a specific instruction about the page when the user gives one.",
        ],
        rules=[
            "If the user gave a specific instruction, follow it exactly; otherwise default to a 2-paragraph summary.",
            "Use bullet points where they make the content clearer.",
            "Stay faithful to the page — never invent content.",
            "Be concise and well structured; skip preambles.",
        ],
        output_format="Plain text — a short summary with bullet points where appropriate.",
    )
    user = (
        f"NOTION PAGE TITLE: {page.get('title', '')}\n\n"
        f"PAGE CONTENT:\n{content[:8000]}\n\n"
        f"INSTRUCTION: {instruction or 'Summarize this page.'}"
    )
    agent = client.as_agent(name="NotionSummarizer", instructions=sys)
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
            await record_usage(user_id, p, c, model="agent_framework (estimated)", source="notion", latency_ms=_latency_ms)
        except Exception:
            pass

    return reply
