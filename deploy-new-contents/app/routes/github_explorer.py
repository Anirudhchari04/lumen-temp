"""FastAPI router for the standalone GitHub Repo Explorer.

Ported from the demo's ``server.py``. Mounted under ``/github-explorer`` in
:mod:`app.main`, so every path below is reached as ``/github-explorer/api/...``.
The page itself is served from ``public/github-explorer/index.html``.
"""

from __future__ import annotations

import os
from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.github_explorer import github_client
from app.github_explorer import classroom_client
from app.github_explorer.agent import GitHubAgent, set_draft_store, set_pending_actions_store
from app.github_explorer.github_auth import request_device_code, poll_for_token

router = APIRouter(tags=["github-explorer"])

# ── In-memory session store (single-user demo parity) ─────────────────────
_sessions: dict[str, GitHubAgent] = {}


def _ensure_token(token: str | None):
    if token:
        os.environ["GITHUB_TOKEN"] = token


# ── Pydantic models ───────────────────────────────────────────────────────


class ChatRequest(BaseModel):
    message: str
    repo: str | None = None
    model: str = "gpt-5.2-chat"
    session_id: str = "default"


class FileWriteRequest(BaseModel):
    path: str
    content: str
    message: str  # mandatory commit message
    branch: str | None = None


class FileDeleteRequest(BaseModel):
    path: str
    message: str  # mandatory commit message
    branch: str | None = None


class DeviceCodeRequest(BaseModel):
    scopes: str = "repo read:user"


# ── Auth endpoints ─────────────────────────────────────────────────────────


@router.post("/api/auth/device-code")
def auth_device_code(req: DeviceCodeRequest):
    try:
        dc = request_device_code(scopes=req.scopes)
        return {
            "device_code": dc.device_code,
            "user_code": dc.user_code,
            "verification_uri": dc.verification_uri,
            "expires_in": dc.expires_in,
            "interval": dc.interval,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/api/auth/poll-token")
def auth_poll_token(device_code: str, interval: int = 5):
    try:
        token = poll_for_token(device_code=device_code, interval=interval, timeout=300)
        return {"access_token": token}
    except TimeoutError:
        raise HTTPException(status_code=408, detail="Timed out waiting for authorization")
    except PermissionError:
        raise HTTPException(status_code=403, detail="User denied authorization")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ── Repo endpoints ─────────────────────────────────────────────────────────


@router.get("/api/repos")
def list_repos(token: str | None = None, max_count: int = 100):
    _ensure_token(token)
    try:
        return github_client.list_user_repos(max_count=max_count)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/api/repos/{owner}/{repo}/summary")
def repo_summary(owner: str, repo: str, token: str | None = None):
    _ensure_token(token)
    try:
        return github_client.repo_summary(f"{owner}/{repo}")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/api/repos/{owner}/{repo}/contents")
def repo_contents(owner: str, repo: str, path: str = "", branch: str | None = None, token: str | None = None):
    _ensure_token(token)
    try:
        return github_client.list_repo_contents(f"{owner}/{repo}", path=path, branch=branch)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/api/repos/{owner}/{repo}/file")
def get_file(owner: str, repo: str, path: str, branch: str | None = None, token: str | None = None):
    _ensure_token(token)
    try:
        return github_client.get_file_content(f"{owner}/{repo}", path=path, branch=branch)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/api/repos/{owner}/{repo}/file")
def create_or_update_file(owner: str, repo: str, req: FileWriteRequest, token: str | None = None):
    _ensure_token(token)
    if not req.message.strip():
        raise HTTPException(status_code=422, detail="Commit message is required.")
    try:
        return github_client.create_or_update_file(
            f"{owner}/{repo}", path=req.path, content=req.content,
            message=req.message.strip(), branch=req.branch,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.delete("/api/repos/{owner}/{repo}/file")
def delete_file(owner: str, repo: str, req: FileDeleteRequest, token: str | None = None):
    _ensure_token(token)
    if not req.message.strip():
        raise HTTPException(status_code=422, detail="Commit message is required.")
    try:
        return github_client.delete_file(
            f"{owner}/{repo}", path=req.path, message=req.message.strip(), branch=req.branch,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/api/repos/{owner}/{repo}/commits")
def list_commits(
    owner: str, repo: str,
    branch: str | None = None, author: str | None = None,
    max_count: int = 20, token: str | None = None,
):
    _ensure_token(token)
    try:
        return github_client.list_commits(
            f"{owner}/{repo}", branch=branch, author=author, max_count=max_count,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/api/repos/{owner}/{repo}/branches")
def list_branches(owner: str, repo: str, token: str | None = None):
    _ensure_token(token)
    try:
        return github_client.list_branches(f"{owner}/{repo}")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/api/repos/{owner}/{repo}/pulls")
def list_prs(owner: str, repo: str, state: str = "all", max_count: int = 20, token: str | None = None):
    _ensure_token(token)
    try:
        return github_client.list_pull_requests(f"{owner}/{repo}", state=state, max_count=max_count)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ── Draft storage (in-memory staging area) ─────────────────────────────────

# Key: "owner/repo::path" → { content, original_content, saved_at }
_drafts: dict[str, dict] = {}
set_draft_store(_drafts)  # Share with agent so it can read drafts

# Pending actions store (agent write actions waiting for user approval)
_pending_actions: dict[str, dict] = {}
set_pending_actions_store(_pending_actions)


class DraftSaveRequest(BaseModel):
    path: str
    content: str


@router.post("/api/repos/{owner}/{repo}/draft")
def save_draft(owner: str, repo: str, req: DraftSaveRequest, token: str | None = None):
    """Save a file as draft (local only, not committed to GitHub)."""
    _ensure_token(token)
    key = f"{owner}/{repo}::{req.path}"

    # Fetch original content for diffing
    original = ""
    try:
        file_data = github_client.get_file_content(f"{owner}/{repo}", req.path)
        if isinstance(file_data, dict) and file_data.get("type") == "file":
            original = file_data.get("content", "")
    except Exception:
        pass

    _drafts[key] = {
        "path": req.path,
        "content": req.content,
        "original_content": original,
        "saved_at": datetime.now().isoformat(),
        "repo": f"{owner}/{repo}",
    }
    return {"status": "saved", "path": req.path, "has_changes": req.content != original}


@router.get("/api/repos/{owner}/{repo}/draft")
def get_draft(owner: str, repo: str, path: str):
    """Get a saved draft for a file."""
    key = f"{owner}/{repo}::{path}"
    if key not in _drafts:
        raise HTTPException(status_code=404, detail="No draft found")
    return _drafts[key]


@router.get("/api/repos/{owner}/{repo}/drafts")
def list_drafts(owner: str, repo: str):
    """List all drafts for a repo."""
    prefix = f"{owner}/{repo}::"
    return [v for k, v in _drafts.items() if k.startswith(prefix)]


@router.delete("/api/repos/{owner}/{repo}/draft")
def discard_draft(owner: str, repo: str, path: str):
    """Discard a draft."""
    key = f"{owner}/{repo}::{path}"
    _drafts.pop(key, None)
    return {"status": "discarded"}


@router.post("/api/repos/{owner}/{repo}/draft/commit")
def commit_draft(owner: str, repo: str, req: FileWriteRequest, token: str | None = None):
    """Commit a draft to GitHub."""
    _ensure_token(token)
    if not req.message.strip():
        raise HTTPException(status_code=422, detail="Commit message is required.")
    key = f"{owner}/{repo}::{req.path}"
    draft = _drafts.get(key)
    content = draft["content"] if draft else req.content
    try:
        result = github_client.create_or_update_file(
            f"{owner}/{repo}", path=req.path, content=content,
            message=req.message.strip(), branch=req.branch,
        )
        # Remove draft after successful commit
        _drafts.pop(key, None)
        return result
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ── GitHub Classroom endpoints ─────────────────────────────────────────────


@router.get("/api/classrooms")
def api_list_classrooms(token: str | None = None):
    _ensure_token(token)
    try:
        return classroom_client.list_classrooms()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/api/classrooms/{classroom_id}")
def api_get_classroom(classroom_id: int, token: str | None = None):
    _ensure_token(token)
    try:
        return classroom_client.get_classroom(classroom_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/api/classrooms/{classroom_id}/assignments")
def api_list_assignments(classroom_id: int, token: str | None = None):
    _ensure_token(token)
    try:
        return classroom_client.list_assignments(classroom_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/api/assignments/{assignment_id}")
def api_get_assignment(assignment_id: int, token: str | None = None):
    _ensure_token(token)
    try:
        return classroom_client.get_assignment(assignment_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/api/assignments/{assignment_id}/submissions")
def api_list_submissions(assignment_id: int, token: str | None = None):
    _ensure_token(token)
    try:
        return classroom_client.list_accepted_assignments(assignment_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/api/assignments/{assignment_id}/grades")
def api_get_grades(assignment_id: int, token: str | None = None):
    _ensure_token(token)
    try:
        return classroom_client.get_assignment_grades(assignment_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ── Pending Actions (approve/reject agent commits) ─────────────────────────


@router.get("/api/actions/pending")
def get_pending_actions():
    """List all pending actions waiting for approval."""
    return [a for a in _pending_actions.values() if a.get("status") == "pending"]


class ApproveRequest(BaseModel):
    commit_message: str | None = None


@router.post("/api/actions/{action_id}/approve")
def approve_action(action_id: str, req: ApproveRequest | None = None, token: str | None = None):
    """Approve and execute a pending action. Optionally override the commit message."""
    _ensure_token(token)
    action = _pending_actions.get(action_id)
    if not action:
        raise HTTPException(status_code=404, detail="Action not found")
    if action["status"] != "pending":
        raise HTTPException(status_code=400, detail=f"Action already {action['status']}")

    # Allow user to override the commit message
    if req and req.commit_message and req.commit_message.strip():
        action["message"] = req.commit_message.strip()

    try:
        if action["type"] == "create_or_update_file":
            result = github_client.create_or_update_file(
                repo_full_name=action["repo_full_name"],
                path=action["path"],
                content=action["content"],
                message=action["message"],
                branch=action.get("branch"),
            )
        elif action["type"] == "delete_file":
            result = github_client.delete_file(
                repo_full_name=action["repo_full_name"],
                path=action["path"],
                message=action["message"],
                branch=action.get("branch"),
            )
        elif action["type"] == "create_repo":
            result = github_client.create_repo(
                name=action["name"],
                description=action.get("description", ""),
                private=action.get("private", False),
                auto_init=action.get("auto_init", True),
            )
        else:
            raise HTTPException(status_code=400, detail=f"Unknown action type: {action['type']}")

        action["status"] = "approved"
        return {"status": "approved", "result": result}
    except Exception as exc:
        action["status"] = "failed"
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/api/actions/{action_id}/reject")
def reject_action(action_id: str):
    """Reject a pending action."""
    action = _pending_actions.get(action_id)
    if not action:
        raise HTTPException(status_code=404, detail="Action not found")
    action["status"] = "rejected"
    return {"status": "rejected", "action_id": action_id}


# ── Chat endpoint ──────────────────────────────────────────────────────────


@router.post("/api/chat")
def chat(req: ChatRequest, token: str | None = None):
    _ensure_token(token)
    sid = req.session_id
    if sid not in _sessions:
        _sessions[sid] = GitHubAgent(model=req.model)
        if req.repo:
            _sessions[sid].messages.append({
                "role": "system",
                "content": (
                    f"The user has selected the repository '{req.repo}'. "
                    f"Unless they specify a different repo, always use '{req.repo}' "
                    f"as the repo_full_name for all tool calls."
                ),
            })

    try:
        response = _sessions[sid].chat(req.message)
        return {
            "response": response,
            "tool_results": _sessions[sid].last_tool_results,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/api/chat/reset")
def reset_chat(session_id: str = "default"):
    _sessions.pop(session_id, None)
    return {"status": "ok"}
