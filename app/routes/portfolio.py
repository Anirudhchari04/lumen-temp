"""Portfolio routes — GitHub-backed artifact storage for the Portfolio Agent."""

from __future__ import annotations

import logging
import secrets
import time
from typing import Dict

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from app.config import settings
from app.middleware.auth import get_current_user
from app.agents.portfolio_agent import (
    ensure_portfolio_repo,
    upload_artifact,
    list_artifacts,
    get_artifact,
    delete_artifact,
    get_portfolio_status,
    save_github_credentials,
    clear_github_credentials,
    stage_artifact,
    get_staged,
    unstage,
    clear_staged,
    commit_staged,
    get_file_content,
    list_workflow_runs,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["portfolio"])


class DeleteBody(BaseModel):
    path: str


class GitHubCallbackBody(BaseModel):
    code: str
    state: str


GITHUB_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_API_USER_URL = "https://api.github.com/user"

# In-memory CSRF state store for the web OAuth flow (10-min TTL via expires_at).
# {state: {"user_id": ..., "expires_at": ...}}
_gh_oauth_states: Dict[str, dict] = {}


async def _github_user_login(token: str) -> str | None:
    """Resolve the GitHub login (username) from an OAuth access token."""
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(
                GITHUB_API_USER_URL,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
        if r.status_code == 200:
            data = r.json() or {}
            return data.get("login")
    except Exception as e:
        logger.warning(f"GitHub login lookup failed: {e}")
    return None


def _require_github_oauth_config() -> tuple[str, str, str]:
    cid = settings.github_oauth_client_id or ""
    cs = settings.github_oauth_client_secret or ""
    ru = settings.github_oauth_redirect_uri or ""
    if not (cid and cs and ru):
        raise HTTPException(
            status_code=503,
            detail="GitHub OAuth not configured. Set GITHUB_OAUTH_CLIENT_ID, "
                   "GITHUB_OAUTH_CLIENT_SECRET, GITHUB_OAUTH_REDIRECT_URI.",
        )
    return cid, cs, ru


@router.get("/oauth/authorize-url")
async def github_authorize_url(current_user: dict = Depends(get_current_user)):
    """Return a one-time GitHub OAuth URL the frontend opens in a popup.

    Web Authorization Code flow — the user clicks "Authorize" on GitHub and is
    redirected back to github_oauth_redirect_uri, which completes automatically.
    """
    cid, _cs, ru = _require_github_oauth_config()

    # Prune expired states, then mint a fresh one bound to this user.
    now = time.time()
    for k in [k for k, v in _gh_oauth_states.items() if v.get("expires_at", 0) < now]:
        _gh_oauth_states.pop(k, None)

    state = secrets.token_urlsafe(24)
    _gh_oauth_states[state] = {"user_id": current_user["id"], "expires_at": now + 600}

    from urllib.parse import urlencode
    params = {
        "client_id": cid,
        "redirect_uri": ru,
        "scope": "repo read:user",
        "state": state,
        "allow_signup": "false",
    }
    return {"url": f"{GITHUB_AUTHORIZE_URL}?{urlencode(params)}"}


@router.post("/oauth/callback")
async def github_oauth_callback(body: GitHubCallbackBody, current_user: dict = Depends(get_current_user)):
    """Exchange the GitHub OAuth code for an access token and persist it."""
    cid, cs, ru = _require_github_oauth_config()

    saved = _gh_oauth_states.pop(body.state, None)
    if saved is None or saved.get("user_id") != current_user["id"]:
        raise HTTPException(status_code=400, detail="Invalid or expired state token")

    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.post(
            GITHUB_TOKEN_URL,
            headers={"Accept": "application/json"},
            data={
                "client_id": cid,
                "client_secret": cs,
                "code": body.code,
                "redirect_uri": ru,
            },
        )
    data = r.json() if r.text else {}
    if data.get("error"):
        raise HTTPException(status_code=400, detail=data.get("error_description") or data.get("error"))

    access_token = data.get("access_token")
    if not access_token:
        raise HTTPException(status_code=400, detail="GitHub did not return an access token")

    owner = await _github_user_login(access_token)
    if not owner:
        raise HTTPException(status_code=400, detail="Could not resolve GitHub user from token")

    await save_github_credentials(current_user["id"], access_token, owner)
    status = await get_portfolio_status(current_user["id"])
    if not status.get("connected"):
        raise HTTPException(status_code=400, detail=status.get("error", "GitHub connection failed"))

    # Best-effort: create the portfolio repo so it's ready immediately.
    try:
        await ensure_portfolio_repo(current_user["id"])
        status = await get_portfolio_status(current_user["id"])
    except Exception as e:
        logger.warning(f"Portfolio repo init after connect failed: {e}")

    return {"connected": True, "owner": owner, "portfolio": status}


@router.post("/disconnect")
async def portfolio_disconnect(current_user: dict = Depends(get_current_user)):
    """Remove GitHub credentials — disconnects the portfolio integration."""
    await clear_github_credentials(current_user["id"])
    return {"disconnected": True}


@router.get("/status")
async def portfolio_status(current_user: dict = Depends(get_current_user)):
    """Return GitHub connection status and whether the portfolio repo exists."""
    return await get_portfolio_status(current_user["id"])


@router.post("/init")
async def portfolio_init(current_user: dict = Depends(get_current_user)):
    """Initialize (create) the portfolio repo if it does not exist."""
    result = await ensure_portfolio_repo(current_user["id"])
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "Failed to create repo"))
    return result


@router.post("/upload")
async def portfolio_upload(
    file: UploadFile = File(...),
    ta_hint: str = Form(""),
    student_name: str = Form(""),
    current_user: dict = Depends(get_current_user),
):
    """Upload an artifact to the portfolio repo in the appropriate TA folder."""
    content_bytes = await file.read()
    result = await upload_artifact(
        user_id=current_user["id"],
        filename=file.filename or "upload",
        content_bytes=content_bytes,
        ta_hint=ta_hint or None,
        student_name=student_name or None,
    )
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "Upload failed"))
    return result


# ── Staged commit flow ──────────────────────────────────────────────────────
# Files are staged first and only written to GitHub when the user clicks Commit.

class CommitBody(BaseModel):
    message: str | None = None


@router.post("/stage")
async def portfolio_stage(
    file: UploadFile = File(...),
    ta_hint: str = Form(""),
    student_name: str = Form(""),
    current_user: dict = Depends(get_current_user),
):
    """Stage an artifact for commit. Nothing is written to GitHub until /commit."""
    status = await get_portfolio_status(current_user["id"])
    if not status.get("connected"):
        raise HTTPException(status_code=400, detail="GitHub not connected.")
    content_bytes = await file.read()
    entry = stage_artifact(
        user_id=current_user["id"],
        filename=file.filename or "upload",
        content_bytes=content_bytes,
        ta_hint=ta_hint or None,
        student_name=student_name or None,
    )
    return {"ok": True, "staged": entry, "all_staged": get_staged(current_user["id"])}


@router.get("/staged")
async def portfolio_staged(current_user: dict = Depends(get_current_user)):
    """List staged (uncommitted) artifacts."""
    return {"staged": get_staged(current_user["id"])}


@router.delete("/staged/{staged_id}")
async def portfolio_unstage(staged_id: str, current_user: dict = Depends(get_current_user)):
    """Drop a single staged artifact before committing."""
    ok = unstage(current_user["id"], staged_id)
    return {"ok": ok, "staged": get_staged(current_user["id"])}


@router.delete("/staged")
async def portfolio_clear_staged(current_user: dict = Depends(get_current_user)):
    """Discard all staged artifacts."""
    clear_staged(current_user["id"])
    return {"ok": True, "staged": []}


@router.post("/commit")
async def portfolio_commit(body: CommitBody, current_user: dict = Depends(get_current_user)):
    """Commit all staged artifacts to GitHub in one commit batch."""
    result = await commit_staged(current_user["id"], body.message)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "Commit failed"))
    return result


@router.get("/file-content")
async def portfolio_file_content(path: str, current_user: dict = Depends(get_current_user)):
    """Retrieve and decode a file's text content from the portfolio repo."""
    result = await get_file_content(current_user["id"], path)
    if not result.get("ok"):
        raise HTTPException(status_code=404, detail=result.get("error", "File not found"))
    return result


@router.get("/actions")
async def portfolio_actions(limit: int = 10, current_user: dict = Depends(get_current_user)):
    """List recent GitHub Actions workflow runs for the portfolio repo."""
    result = await list_workflow_runs(current_user["id"], limit)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "Failed to load workflow runs"))
    return result


@router.get("/files")
async def portfolio_list_files(path: str = "", current_user: dict = Depends(get_current_user)):
    """List files in the portfolio repo at the given path."""
    result = await list_artifacts(current_user["id"], path)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "Failed to list files"))
    return result


@router.get("/files/get")
async def portfolio_get_file(path: str, current_user: dict = Depends(get_current_user)):
    """Get metadata/download URL for a specific file."""
    result = await get_artifact(current_user["id"], path)
    if not result.get("ok"):
        raise HTTPException(status_code=404, detail=result.get("error", "File not found"))
    return result


@router.delete("/files")
async def portfolio_delete_file(body: DeleteBody, current_user: dict = Depends(get_current_user)):
    """Delete a file from the portfolio repo."""
    result = await delete_artifact(current_user["id"], body.path)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "Delete failed"))
    return result
