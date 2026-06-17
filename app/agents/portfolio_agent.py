"""Portfolio Agent — manages student artifact storage on GitHub.

Each user gets a `shiksha-portfolio` repo (created on first use).
Artifacts are organized into folders by TA or into `general/`.

Folder layout:
  shiksha-portfolio/
    general/          <- uploads not tied to a specific TA
    math-ta/          <- Math TA artifacts
    cs-ta/            <- CS TA artifacts
    science-ta/       <- Science TA artifacts
    <custom-ta>/      <- any other TA slugs
"""

from __future__ import annotations

import base64
import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

PORTFOLIO_REPO_NAME = "shiksha-portfolio"
PORTFOLIO_REPO_DESC = "Shiksha student portfolio — managed by Lumen"

# Keyword -> folder mapping for TA detection
_TA_KEYWORDS: list[tuple[list[str], str]] = [
    (["math", "maths", "mathematics", "algebra", "geometry", "calculus", "arithmetic"], "math-ta"),
    (["cs", "computer", "code", "coding", "programming", "python", "java", "javascript", "software"], "cs-ta"),
    (["science", "physics", "chemistry", "biology", "lab", "experiment"], "science-ta"),
    (["english", "literature", "grammar", "writing", "essay", "language"], "english-ta"),
    (["history", "social", "civics", "geography", "sst"], "social-ta"),
]


def detect_ta_folder(context: str) -> str:
    """Detect which TA folder a file belongs to from message context/filename.
    Returns folder name like 'math-ta' or 'general' if undetected."""
    lower = context.lower()
    for keywords, folder in _TA_KEYWORDS:
        if any(kw in lower for kw in keywords):
            return folder
    return "general"


def _get_client(token: str):
    """Return a PyGithub client."""
    from github import Auth, Github
    return Github(auth=Auth.Token(token))


async def get_github_credentials(user_id: str) -> tuple[str | None, str | None]:
    """Fetch GitHub token + owner from user's Lumen profile in CosmosDB."""
    from app.lumen.core import get_lumen
    lumen = await get_lumen(user_id)
    if not lumen:
        return None, None
    github = lumen.get("github") or {}
    return github.get("token"), github.get("owner")


async def save_github_credentials(user_id: str, token: str, owner: str) -> None:
    """Save GitHub token + owner into the user's Lumen profile."""
    from app.lumen.core import get_lumen, save_lumen
    lumen = await get_lumen(user_id)
    if not lumen:
        return
    lumen["github"] = {"token": token, "owner": owner}
    await save_lumen(lumen)


async def clear_github_credentials(user_id: str) -> None:
    """Remove GitHub credentials from the user's Lumen profile."""
    from app.lumen.core import get_lumen, save_lumen
    lumen = await get_lumen(user_id)
    if not lumen:
        return
    lumen.pop("github", None)
    lumen.pop("github_token", None)
    lumen.pop("github_owner", None)
    await save_lumen(lumen)


async def ensure_portfolio_repo(user_id: str) -> dict:
    """Create the portfolio repo if it does not exist. Returns repo info."""
    token, owner = await get_github_credentials(user_id)
    if not token:
        return {"ok": False, "error": "GitHub not connected. Connect GitHub from your profile."}

    try:
        gh = _get_client(token)
        try:
            repo = gh.get_user().get_repo(PORTFOLIO_REPO_NAME)
            return {"ok": True, "created": False, "full_name": repo.full_name, "url": repo.html_url}
        except Exception:
            pass
        user = gh.get_user()
        repo = user.create_repo(
            name=PORTFOLIO_REPO_NAME,
            description=PORTFOLIO_REPO_DESC,
            private=True,
            auto_init=True,
        )
        return {"ok": True, "created": True, "full_name": repo.full_name, "url": repo.html_url}
    except Exception as e:
        logger.error(f"ensure_portfolio_repo error: {e}")
        return {"ok": False, "error": str(e)}


async def upload_artifact(
    user_id: str,
    filename: str,
    content_bytes: bytes,
    ta_hint: Optional[str] = None,
    student_name: Optional[str] = None,
) -> dict:
    """Upload an artifact to the portfolio repo in the appropriate folder."""
    token, owner = await get_github_credentials(user_id)
    if not token:
        return {"ok": False, "error": "GitHub not connected. Connect GitHub from your profile."}

    folder = detect_ta_folder(ta_hint or filename)
    safe_name = re.sub(r"[^\w.\-]", "_", filename)
    if student_name:
        safe_student = re.sub(r"[^\w\-]", "_", student_name)
        path = f"{folder}/{safe_student}/{safe_name}"
    else:
        path = f"{folder}/{safe_name}"

    repo_full = f"{owner}/{PORTFOLIO_REPO_NAME}"

    try:
        gh = _get_client(token)
        repo = gh.get_repo(repo_full)
        sha = None
        try:
            existing = repo.get_contents(path)
            sha = existing.sha
        except Exception:
            pass

        # PyGithub base64-encodes content itself — pass raw bytes, not pre-encoded.
        commit_msg = f"Add {filename} to {folder}"
        if sha:
            result = repo.update_file(path, commit_msg, content_bytes, sha)
            action = "updated"
        else:
            result = repo.create_file(path, commit_msg, content_bytes)
            action = "created"

        commit = result.get("commit")
        content_obj = result.get("content")
        return {
            "ok": True, "action": action, "path": path, "folder": folder,
            "url": content_obj.html_url if content_obj else None,
            "commit_url": commit.html_url if commit else None,
            "repo": repo_full,
        }
    except Exception as e:
        logger.error(f"upload_artifact error: {e}")
        return {"ok": False, "error": str(e)}


# ── Typed artifact auto-save ──────────────────────────────────────────────────
# Used by subject TAs (e.g. the Coding TA) to silently commit a generated
# artifact straight to the portfolio — no staging. Files are grouped first by
# artifact TYPE, then named by date + title:
#     <ta_folder>/<type-subfolder>/<YYYY-MM-DD>_<slug>.<ext>

# Artifact type -> stable subfolder name (so files group by type in the repo).
_ARTIFACT_SUBFOLDERS = {
    "code": "code",
    "quiz": "quizzes",
    "notes": "notes",
    "exercise": "exercises",
    "file": "files",
}


def _slugify(text: str, maxlen: int = 60) -> str:
    """Turn a title into a filesystem-safe slug."""
    s = re.sub(r"[^\w\s-]", "", (text or "").strip().lower())
    s = re.sub(r"[\s_]+", "-", s).strip("-")
    return (s or "untitled")[:maxlen]


async def save_typed_artifact(
    user_id: str,
    title: str,
    content_bytes: bytes,
    artifact_type: str = "file",
    ext: str = "txt",
    ta_folder: str = "coding-ta",
) -> dict:
    """Directly commit a generated artifact to the portfolio, organized by type
    then dated+titled filename. Auto-creates the repo on first use. No staging —
    this is the silent auto-save path for TA-generated artifacts."""
    token, owner = await get_github_credentials(user_id)
    if not token:
        return {"ok": False, "error": "GitHub not connected."}

    subfolder = _ARTIFACT_SUBFOLDERS.get(artifact_type, "files")
    slug = _slugify(title)
    safe_ext = re.sub(r"[^\w]", "", ext or "txt") or "txt"
    repo_full = f"{owner}/{PORTFOLIO_REPO_NAME}"

    try:
        gh = _get_client(token)
        try:
            repo = gh.get_repo(repo_full)
        except Exception:
            # Repo doesn't exist yet — create it, then proceed.
            await ensure_portfolio_repo(user_id)
            repo = gh.get_repo(repo_full)

        # Name by title (no date prefix). Suffix only to avoid clobbering an
        # existing artifact with the same title.
        base = f"{ta_folder}/{subfolder}/{slug}"
        path = f"{base}.{safe_ext}"
        n = 2
        while True:
            try:
                repo.get_contents(path)
                path = f"{base}-{n}.{safe_ext}"
                n += 1
            except Exception:
                break

        msg = f"Add {artifact_type} '{title}' via Coding TA"
        result = repo.create_file(path, msg, content_bytes)
        content_obj = result.get("content")
        commit = result.get("commit")
        return {
            "ok": True, "path": path, "type": artifact_type,
            "url": content_obj.html_url if content_obj else None,
            "commit_url": commit.html_url if commit else None,
            "repo": repo_full,
        }
    except Exception as e:
        logger.error(f"save_typed_artifact error: {e}")
        return {"ok": False, "error": str(e)}


# ── Staged commits ──────────────────────────────────────────────────────────
# Uploads are STAGED first (in memory) and only written to GitHub when the user
# explicitly commits. This gives a "review then commit" flow instead of every
# upload silently creating a commit. Keyed by user_id.
import uuid as _uuid

_staged: dict[str, list[dict]] = {}


def stage_artifact(
    user_id: str,
    filename: str,
    content_bytes: bytes,
    ta_hint: Optional[str] = None,
    student_name: Optional[str] = None,
) -> dict:
    """Stage an artifact for a later commit. Nothing is written to GitHub yet."""
    folder = detect_ta_folder(ta_hint or filename)
    safe_name = re.sub(r"[^\w.\-]", "_", filename)
    if student_name:
        safe_student = re.sub(r"[^\w\-]", "_", student_name)
        path = f"{folder}/{safe_student}/{safe_name}"
    else:
        path = f"{folder}/{safe_name}"

    entry = {
        "id": str(_uuid.uuid4())[:8],
        "filename": filename,
        "folder": folder,
        "path": path,
        "content_b64": base64.b64encode(content_bytes).decode(),
        "size": len(content_bytes),
    }
    _staged.setdefault(user_id, [])
    # Replace any existing staged entry for the same path (latest wins).
    _staged[user_id] = [e for e in _staged[user_id] if e["path"] != path]
    _staged[user_id].append(entry)
    # Don't leak file content back to the caller.
    return {k: v for k, v in entry.items() if k != "content_b64"}


def get_staged(user_id: str) -> list[dict]:
    """Return staged (uncommitted) artifacts for a user, without file content."""
    return [{k: v for k, v in e.items() if k != "content_b64"} for e in _staged.get(user_id, [])]


def unstage(user_id: str, staged_id: str) -> bool:
    """Drop a single staged artifact before commit."""
    items = _staged.get(user_id, [])
    new_items = [e for e in items if e["id"] != staged_id]
    _staged[user_id] = new_items
    return len(new_items) != len(items)


def clear_staged(user_id: str) -> None:
    """Discard all staged artifacts for a user."""
    _staged.pop(user_id, None)


async def commit_staged(user_id: str, commit_message: str | None = None) -> dict:
    """Commit all staged artifacts to GitHub in one go. Clears staging on success."""
    token, owner = await get_github_credentials(user_id)
    if not token:
        return {"ok": False, "error": "GitHub not connected. Connect GitHub from your profile."}

    staged = _staged.get(user_id, [])
    if not staged:
        return {"ok": False, "error": "Nothing staged to commit."}

    repo_full = f"{owner}/{PORTFOLIO_REPO_NAME}"
    committed: list[dict] = []
    failed: list[dict] = []
    try:
        gh = _get_client(token)
        repo = gh.get_repo(repo_full)
        for entry in list(staged):
            path = entry["path"]
            msg = commit_message or f"Add {entry['filename']} to {entry['folder']}"
            try:
                sha = None
                try:
                    existing = repo.get_contents(path)
                    sha = existing.sha
                except Exception:
                    pass
                # Staging keeps content base64-encoded (JSON-safe); decode back
                # to raw bytes here — PyGithub base64-encodes it again itself.
                raw = base64.b64decode(entry["content_b64"])
                if sha:
                    result = repo.update_file(path, msg, raw, sha)
                    action = "updated"
                else:
                    result = repo.create_file(path, msg, raw)
                    action = "created"
                content_obj = result.get("content")
                commit = result.get("commit")
                committed.append({
                    "path": path, "action": action,
                    "url": content_obj.html_url if content_obj else None,
                    "commit_url": commit.html_url if commit else None,
                })
            except Exception as e:
                logger.error(f"commit_staged file error ({path}): {e}")
                failed.append({"path": path, "error": str(e)})
    except Exception as e:
        logger.error(f"commit_staged error: {e}")
        return {"ok": False, "error": str(e)}

    # Drop the ones that committed; keep failures staged so they can be retried.
    committed_paths = {c["path"] for c in committed}
    _staged[user_id] = [e for e in staged if e["path"] not in committed_paths]

    return {
        "ok": len(committed) > 0,
        "committed": committed,
        "failed": failed,
        "repo": repo_full,
    }


async def get_file_content(user_id: str, path: str, max_bytes: int = 100_000) -> dict:
    """Retrieve and decode a file's text content from the portfolio repo."""
    token, owner = await get_github_credentials(user_id)
    if not token:
        return {"ok": False, "error": "GitHub not connected."}

    repo_full = f"{owner}/{PORTFOLIO_REPO_NAME}"
    try:
        gh = _get_client(token)
        repo = gh.get_repo(repo_full)
        content = repo.get_contents(path)
        if isinstance(content, list):
            return {"ok": False, "error": f"'{path}' is a folder, not a file."}
        raw = base64.b64decode(content.content or "")
        truncated = len(raw) > max_bytes
        try:
            text = raw[:max_bytes].decode("utf-8")
            is_text = True
        except UnicodeDecodeError:
            text = ""
            is_text = False
        return {
            "ok": True, "path": content.path, "name": content.name,
            "size": content.size, "is_text": is_text, "truncated": truncated,
            "content": text, "download_url": content.download_url, "url": content.html_url,
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


async def list_workflow_runs(user_id: str, limit: int = 10) -> dict:
    """List recent GitHub Actions workflow runs for the portfolio repo."""
    token, owner = await get_github_credentials(user_id)
    if not token:
        return {"ok": False, "error": "GitHub not connected."}

    repo_full = f"{owner}/{PORTFOLIO_REPO_NAME}"
    try:
        gh = _get_client(token)
        repo = gh.get_repo(repo_full)
        runs = []
        for run in repo.get_workflow_runs():
            if len(runs) >= limit:
                break
            runs.append({
                "id": run.id,
                "name": run.name or run.display_title or "workflow",
                "status": run.status,           # queued / in_progress / completed
                "conclusion": run.conclusion,   # success / failure / cancelled / None
                "branch": run.head_branch,
                "event": run.event,
                "url": run.html_url,
                "created_at": run.created_at.isoformat() if run.created_at else "",
            })
        return {"ok": True, "runs": runs, "repo": repo_full}
    except Exception as e:
        err = str(e)
        if "404" in err or "Not Found" in err:
            return {"ok": True, "runs": [], "repo": repo_full}
        return {"ok": False, "error": err}


async def list_artifacts(user_id: str, path: str = "") -> dict:
    """List files in the portfolio repo at the given path."""
    token, owner = await get_github_credentials(user_id)
    if not token:
        return {"ok": False, "error": "GitHub not connected."}

    repo_full = f"{owner}/{PORTFOLIO_REPO_NAME}"
    try:
        gh = _get_client(token)
        repo = gh.get_repo(repo_full)
        contents = repo.get_contents(path or "")
        if not isinstance(contents, list):
            contents = [contents]
        files = [
            {"name": c.name, "path": c.path, "type": c.type, "size": c.size, "url": c.html_url}
            for c in contents
        ]
        return {"ok": True, "files": files, "path": path or "/", "repo": repo_full}
    except Exception as e:
        err_str = str(e)
        if "404" in err_str or "Not Found" in err_str:
            return {"ok": False, "error": f"Portfolio repo '{repo_full}' not found. Say 'set up my portfolio' to create it."}
        return {"ok": False, "error": err_str}


async def get_artifact(user_id: str, path: str) -> dict:
    """Get metadata/download URL for a specific file."""
    token, owner = await get_github_credentials(user_id)
    if not token:
        return {"ok": False, "error": "GitHub not connected."}

    repo_full = f"{owner}/{PORTFOLIO_REPO_NAME}"
    try:
        gh = _get_client(token)
        repo = gh.get_repo(repo_full)
        content = repo.get_contents(path)
        return {
            "ok": True, "path": content.path, "name": content.name,
            "size": content.size, "download_url": content.download_url,
            "url": content.html_url, "sha": content.sha,
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


async def delete_artifact(user_id: str, path: str) -> dict:
    """Delete a file from the portfolio repo."""
    token, owner = await get_github_credentials(user_id)
    if not token:
        return {"ok": False, "error": "GitHub not connected."}

    repo_full = f"{owner}/{PORTFOLIO_REPO_NAME}"
    try:
        gh = _get_client(token)
        repo = gh.get_repo(repo_full)
        content = repo.get_contents(path)
        result = repo.delete_file(path, f"Remove {path}", content.sha)
        commit = result.get("commit")
        return {"ok": True, "path": path, "commit_url": commit.html_url if commit else None}
    except Exception as e:
        return {"ok": False, "error": str(e)}


async def get_portfolio_status(user_id: str) -> dict:
    """Check if GitHub is connected and portfolio repo exists."""
    token, owner = await get_github_credentials(user_id)
    if not token:
        return {"connected": False, "owner": None, "repo_exists": False}

    repo_full = f"{owner}/{PORTFOLIO_REPO_NAME}"
    try:
        gh = _get_client(token)
        login = gh.get_user().login
        try:
            repo = gh.get_repo(repo_full)
            return {
                "connected": True, "owner": login,
                "repo_exists": True, "repo_url": repo.html_url,
                "repo_full_name": repo.full_name,
            }
        except Exception:
            return {"connected": True, "owner": login, "repo_exists": False}
    except Exception as e:
        return {"connected": False, "owner": None, "repo_exists": False, "error": str(e)}


def get_agent_card(base_url: str = "") -> "AgentCard":
    from app.protocols.models import AgentCard, AgentProvider, AgentInterface, AgentCapabilities, AgentSkill
    return AgentCard(
        name="Portfolio Agent",
        description="GitHub-backed learning artifact storage. Save, retrieve and manage learning notes, solutions, and projects.",
        version="1.0.0",
        documentationUrl=f"{base_url}/docs/portfolio",
        provider=AgentProvider(organization="Lumen Network", url=base_url),
        supportedInterfaces=[AgentInterface(url=f"{base_url}/a2a/portfolio")],
        capabilities=AgentCapabilities(streaming=False, pushNotifications=False),
        defaultInputModes=["text/plain", "application/octet-stream"],
        defaultOutputModes=["text/plain", "application/json"],
        securitySchemes={
            "lumenJwt": {"httpAuthSecurityScheme": {"scheme": "bearer", "bearerFormat": "JWT"}},
            "githubOAuth": {"httpAuthSecurityScheme": {"scheme": "bearer", "bearerFormat": "oauth", "description": "GitHub OAuth token stored in Lumen profile"}},
        },
        securityRequirements=[{"lumenJwt": [], "githubOAuth": []}],
        skills=[
            AgentSkill(
                id="portfolio.list_artifacts",
                name="List Artifacts",
                description="List all learning artifacts and files in the GitHub portfolio repository",
                tags=["portfolio", "list", "github", "artifacts", "files"],
                examples=["Show my portfolio", "List my saved files", "What did I save from the math TA?", "Show files in my cs-ta folder"],
            ),
            AgentSkill(
                id="portfolio.upload_artifact",
                name="Upload Artifact",
                description="Save a learning artifact (notes, solution, code) to the GitHub portfolio",
                tags=["portfolio", "save", "upload", "artifact", "github"],
                inputModes=["text/plain", "application/octet-stream"],
                examples=["Save this solution to my portfolio", "Upload my calculus notes", "Store this code in my GitHub portfolio"],
            ),
            AgentSkill(
                id="portfolio.get_artifact",
                name="Get Artifact",
                description="Retrieve a specific artifact from the portfolio by path",
                tags=["portfolio", "get", "retrieve", "github"],
                examples=["Get my recursion solution", "Show me the file math-ta/limits.md"],
            ),
            AgentSkill(
                id="portfolio.get_portfolio_status",
                name="Portfolio Status",
                description="Check if GitHub portfolio is configured and get repo info",
                tags=["portfolio", "status", "github", "setup"],
                examples=["Is my portfolio set up?", "What repo am I using?", "Show my portfolio stats"],
            ),
        ],
    )
