"""Notion integration routes — OAuth callback + REST proxy."""

from __future__ import annotations

import logging
import secrets
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.config import settings
from app.middleware.auth import get_current_user
from app.lumen.core import get_lumen
from app.agents.notion_agent import (
    is_notion_connected,
    get_notion_token,
    save_notion_config,
    disconnect_notion,
    search_notion,
    read_page,
    create_page,
    append_to_page,
    replace_page_content,
    summarize_page,
    _page_summary,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["notion"])

NOTION_OAUTH_AUTHORIZE = "https://api.notion.com/v1/oauth/authorize"
NOTION_OAUTH_TOKEN = "https://api.notion.com/v1/oauth/token"

# In-memory CSRF state store (5-min TTL). For demo scale this is fine.
_oauth_states: dict[str, dict] = {}


def _get_notion_config() -> tuple[str, str, str]:
    """Return (client_id, client_secret, redirect_uri) or raise if not configured."""
    cid = getattr(settings, "notion_client_id", "") or ""
    cs = getattr(settings, "notion_client_secret", "") or ""
    ru = getattr(settings, "notion_redirect_uri", "") or ""
    if not (cid and cs and ru):
        raise HTTPException(
            status_code=501,
            detail="Notion is not configured. Set NOTION_CLIENT_ID, NOTION_CLIENT_SECRET, NOTION_REDIRECT_URI on the server.",
        )
    return cid, cs, ru


# ── OAuth ────────────────────────────────────────────────────────────────────

@router.get("/auth/notion-authorize-url")
async def notion_authorize_url(current_user: dict = Depends(get_current_user)):
    """Return a one-time-use OAuth URL the frontend can open in a popup."""
    cid, _cs, ru = _get_notion_config()
    state = secrets.token_urlsafe(24)
    _oauth_states[state] = {"user_id": current_user["id"]}
    params = {
        "client_id": cid,
        "response_type": "code",
        "owner": "user",
        "redirect_uri": ru,
        "state": state,
    }
    return {"url": f"{NOTION_OAUTH_AUTHORIZE}?{urlencode(params)}"}


class NotionCallbackBody(BaseModel):
    code: str
    state: str


@router.post("/auth/notion-callback")
async def notion_callback(body: NotionCallbackBody, current_user: dict = Depends(get_current_user)):
    """Exchange the OAuth code for an access token + store it encrypted."""
    cid, cs, ru = _get_notion_config()

    # CSRF check (state must match an outstanding request for this user)
    saved = _oauth_states.pop(body.state, None)
    if not saved or saved.get("user_id") != current_user["id"]:
        raise HTTPException(status_code=400, detail="Invalid or expired state token")

    import base64
    basic = base64.b64encode(f"{cid}:{cs}".encode()).decode()

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            NOTION_OAUTH_TOKEN,
            headers={
                "Authorization": f"Basic {basic}",
                "Content-Type": "application/json",
                "Notion-Version": "2022-06-28",
            },
            json={
                "grant_type": "authorization_code",
                "code": body.code,
                "redirect_uri": ru,
            },
        )

    if resp.status_code >= 400:
        logger.error(f"Notion token exchange failed: {resp.status_code} {resp.text}")
        raise HTTPException(status_code=400, detail=f"Token exchange failed: {resp.text}")

    data = resp.json()
    access_token = data.get("access_token")
    if not access_token:
        raise HTTPException(status_code=400, detail="No access_token in Notion response")

    await save_notion_config(
        user_id=current_user["id"],
        access_token=access_token,
        workspace_name=data.get("workspace_name", "") or "",
        workspace_id=data.get("workspace_id", "") or "",
        bot_id=data.get("bot_id", "") or "",
    )

    return {
        "connected": True,
        "workspace_name": data.get("workspace_name", ""),
    }


@router.get("/lumen/notion/status")
async def notion_status(current_user: dict = Depends(get_current_user)):
    lumen = await get_lumen(current_user["id"])
    if not is_notion_connected(lumen):
        return {"connected": False}
    cfg = lumen.get("notion_config", {})
    return {
        "connected": True,
        "workspace_name": cfg.get("workspace_name", ""),
        "workspace_id": cfg.get("workspace_id", ""),
    }


@router.post("/lumen/notion/disconnect")
async def notion_disconnect(current_user: dict = Depends(get_current_user)):
    ok = await disconnect_notion(current_user["id"])
    return {"disconnected": ok}


# ── Notion REST proxies (called from chat handlers / frontend) ───────────────

class NotionSearchBody(BaseModel):
    query: str = ""
    limit: int = 10


@router.post("/lumen/notion/search")
async def notion_search(body: NotionSearchBody, current_user: dict = Depends(get_current_user)):
    token = await get_notion_token(current_user["id"])
    if not token:
        raise HTTPException(status_code=400, detail="Notion not connected")
    results = await search_notion(token, body.query, limit=body.limit)
    return {"results": [_page_summary(r) for r in results]}


class NotionReadBody(BaseModel):
    page_id: str


@router.post("/lumen/notion/read")
async def notion_read(body: NotionReadBody, current_user: dict = Depends(get_current_user)):
    token = await get_notion_token(current_user["id"])
    if not token:
        raise HTTPException(status_code=400, detail="Notion not connected")
    return await read_page(token, body.page_id)


class NotionCreateBody(BaseModel):
    title: str
    content_lines: list[str] = []
    parent_page_id: str | None = None
    use_todos: bool = False


@router.post("/lumen/notion/create")
async def notion_create(body: NotionCreateBody, current_user: dict = Depends(get_current_user)):
    token = await get_notion_token(current_user["id"])
    if not token:
        raise HTTPException(status_code=400, detail="Notion not connected")
    return await create_page(
        token, body.title, body.content_lines, body.parent_page_id, body.use_todos,
    )


class NotionAppendBody(BaseModel):
    page_id: str
    lines: list[str]
    use_todos: bool = False


@router.post("/lumen/notion/append")
async def notion_append(body: NotionAppendBody, current_user: dict = Depends(get_current_user)):
    token = await get_notion_token(current_user["id"])
    if not token:
        raise HTTPException(status_code=400, detail="Notion not connected")
    return await append_to_page(token, body.page_id, body.lines, body.use_todos)


class NotionReplaceBody(BaseModel):
    page_id: str
    lines: list[str]
    use_todos: bool = False


@router.post("/lumen/notion/replace")
async def notion_replace(body: NotionReplaceBody, current_user: dict = Depends(get_current_user)):
    """DESTRUCTIVE — deletes all child blocks of the page, then inserts new lines.

    Notion has no atomic replace; rich formatting (tables, embeds, sub-pages,
    colors) is lost. Callers should require explicit user confirmation.
    """
    token = await get_notion_token(current_user["id"])
    if not token:
        raise HTTPException(status_code=400, detail="Notion not connected")
    return await replace_page_content(token, body.page_id, body.lines, body.use_todos)


class NotionSummarizeBody(BaseModel):
    page_id: str
    instruction: str = ""


@router.post("/lumen/notion/summarize")
async def notion_summarize(body: NotionSummarizeBody, current_user: dict = Depends(get_current_user)):
    token = await get_notion_token(current_user["id"])
    if not token:
        raise HTTPException(status_code=400, detail="Notion not connected")
    summary = await summarize_page(token, body.page_id, body.instruction)
    return {"summary": summary}
