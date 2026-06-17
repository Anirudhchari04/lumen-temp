"""GitHub + GitHub Classroom client for the Lumen GitHub Agent.

Wraps PyGithub (repo / commit / merge / rebase / file operations) and the
GitHub Classroom REST API (classrooms / assignments / grades). Every function
takes an explicit ``token`` — the per-user GitHub OAuth token stored in the
Lumen profile (see ``app.agents.portfolio_agent.get_github_credentials``).

This module is intentionally side-effect-light: it only talks to GitHub. The
conversational brain (function-calling loop, approval flow) lives in
``app.agents.github_agent``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"


# ── PyGithub helpers ───────────────────────────────────────────────

def _client(token: str):
    if not token:
        raise ValueError("A GitHub token is required.")
    from github import Auth, Github
    return Github(auth=Auth.Token(token))


def get_repo(repo_full_name: str, token: str):
    return _client(token).get_repo(repo_full_name)


@dataclass
class _CommitInfo:
    sha: str
    short_sha: str
    message: str
    author: str
    date: datetime
    is_merge: bool
    parent_count: int
    url: str


def _commit_to_info(commit) -> dict:
    author_name = "Unknown"
    if commit.author:
        author_name = commit.author.login
    elif commit.commit.author:
        author_name = commit.commit.author.name or "Unknown"
    return _CommitInfo(
        sha=commit.sha,
        short_sha=commit.sha[:7],
        message=commit.commit.message,
        author=author_name,
        date=commit.commit.author.date,
        is_merge=len(commit.parents) > 1,
        parent_count=len(commit.parents),
        url=commit.html_url,
    ).__dict__


# ── Read operations ─────────────────────────────────────────────────────────

def repo_summary(repo_full_name: str, token: str) -> dict:
    """High-level metadata about a repository."""
    repo = get_repo(repo_full_name, token)
    return {
        "full_name": repo.full_name,
        "description": repo.description,
        "default_branch": repo.default_branch,
        "stars": repo.stargazers_count,
        "forks": repo.forks_count,
        "open_issues": repo.open_issues_count,
        "language": repo.language,
        "url": repo.html_url,
    }


def list_user_repos(token: str, username: Optional[str] = None,
                    max_count: int = 30, sort: str = "updated") -> list[dict]:
    """List repositories for a user, or the authenticated user if none given."""
    gh = _client(token)
    user = gh.get_user(username) if username else gh.get_user()
    results = []
    for i, repo in enumerate(user.get_repos(sort=sort)):
        if i >= max_count:
            break
        results.append({
            "full_name": repo.full_name,
            "description": repo.description,
            "language": repo.language,
            "stars": repo.stargazers_count,
            "forks": repo.forks_count,
            "private": repo.private,
            "updated_at": repo.updated_at.isoformat() if repo.updated_at else None,
            "url": repo.html_url,
        })
    return results


def list_commits(repo_full_name: str, token: str, author: Optional[str] = None,
                 branch: Optional[str] = None, since: Optional[datetime] = None,
                 until: Optional[datetime] = None, max_count: int = 30) -> list[dict]:
    """Recent commits, optionally filtered by author / branch / date range."""
    repo = get_repo(repo_full_name, token)
    kwargs: dict = {}
    if branch:
        kwargs["sha"] = branch
    if author:
        kwargs["author"] = author
    if since:
        kwargs["since"] = since
    if until:
        kwargs["until"] = until
    results = []
    for i, c in enumerate(repo.get_commits(**kwargs)):
        if i >= max_count:
            break
        results.append(_commit_to_info(c))
    return results


def list_merges(repo_full_name: str, token: str, branch: Optional[str] = None,
                author: Optional[str] = None, max_count: int = 20) -> list[dict]:
    """Merge commits only (commits with more than one parent)."""
    repo = get_repo(repo_full_name, token)
    kwargs: dict = {}
    if branch:
        kwargs["sha"] = branch
    if author:
        kwargs["author"] = author
    merges = []
    for c in repo.get_commits(**kwargs):
        if len(merges) >= max_count:
            break
        if len(c.parents) > 1:
            merges.append(_commit_to_info(c))
    return merges


def detect_rebases(repo_full_name: str, token: str, branch: Optional[str] = None,
                   max_count: int = 30) -> list[dict]:
    """Heuristic rebase detection: author/committer date mismatch or 'rebase'
    in the commit message."""
    repo = get_repo(repo_full_name, token)
    kwargs: dict = {}
    if branch:
        kwargs["sha"] = branch
    rebased: list[dict] = []
    for i, c in enumerate(repo.get_commits(**kwargs)):
        if i >= max_count * 3:
            break
        author_date = c.commit.author.date
        committer_date = c.commit.committer.date
        diff_seconds = abs((committer_date - author_date).total_seconds())
        reason = ""
        if diff_seconds > 60:
            reason = (f"Author date ({author_date.isoformat()}) differs from "
                      f"committer date ({committer_date.isoformat()}) by "
                      f"{int(diff_seconds)}s — likely rebased")
        if "rebase" in c.commit.message.lower():
            reason = reason or "Commit message mentions 'rebase'"
        if reason:
            info = _commit_to_info(c)
            info["rebase_reason"] = reason
            rebased.append(info)
            if len(rebased) >= max_count:
                break
    return rebased


def get_commit_detail(repo_full_name: str, sha: str, token: str) -> dict:
    """Full details for a single commit, including file-level changes."""
    repo = get_repo(repo_full_name, token)
    c = repo.get_commit(sha)
    info = _commit_to_info(c)
    info["files"] = [
        {"filename": f.filename, "status": f.status, "additions": f.additions,
         "deletions": f.deletions, "changes": f.changes}
        for f in c.files
    ]
    info["stats"] = {"additions": c.stats.additions, "deletions": c.stats.deletions,
                     "total": c.stats.total}
    return info


def list_branches(repo_full_name: str, token: str) -> list[dict]:
    repo = get_repo(repo_full_name, token)
    return [{"name": b.name, "sha": b.commit.sha, "protected": b.protected}
            for b in repo.get_branches()]


def list_pull_requests(repo_full_name: str, token: str, state: str = "all",
                       max_count: int = 20) -> list[dict]:
    repo = get_repo(repo_full_name, token)
    prs = repo.get_pulls(state=state, sort="updated", direction="desc")
    results = []
    for i, pr in enumerate(prs):
        if i >= max_count:
            break
        results.append({
            "number": pr.number,
            "title": pr.title,
            "state": pr.state,
            "author": pr.user.login if pr.user else "Unknown",
            "created_at": pr.created_at.isoformat(),
            "merged": pr.merged,
            "merged_at": pr.merged_at.isoformat() if pr.merged_at else None,
            "merge_commit_sha": pr.merge_commit_sha,
            "url": pr.html_url,
        })
    return results


def get_file_content(repo_full_name: str, path: str, token: str,
                     branch: Optional[str] = None) -> dict:
    """Read a file's content, or return a directory listing for a folder path."""
    repo = get_repo(repo_full_name, token)
    ref = branch or repo.default_branch
    contents = repo.get_contents(path, ref=ref)
    if isinstance(contents, list):
        return {
            "type": "directory",
            "path": path,
            "files": [{"name": c.name, "path": c.path, "type": c.type} for c in contents],
        }
    return {
        "type": "file",
        "path": contents.path,
        "name": contents.name,
        "size": contents.size,
        "content": contents.decoded_content.decode("utf-8", errors="replace"),
        "url": contents.html_url,
    }


def list_repo_contents(repo_full_name: str, token: str, path: str = "",
                       branch: Optional[str] = None) -> dict:
    """List files and directories at a path in a repository."""
    repo = get_repo(repo_full_name, token)
    ref = branch or repo.default_branch
    contents = repo.get_contents(path, ref=ref)
    if not isinstance(contents, list):
        contents = [contents]
    return {
        "path": path or "/",
        "entries": [{"name": c.name, "path": c.path, "type": c.type,
                     "size": getattr(c, "size", 0)} for c in contents],
    }


# ── Write operations (executed only after user approval) ────────────────────

def create_repo(name: str, token: str, description: str = "",
                private: bool = False, auto_init: bool = True) -> dict:
    user = _client(token).get_user()
    repo = user.create_repo(name=name, description=description,
                            private=private, auto_init=auto_init)
    return {
        "full_name": repo.full_name,
        "description": repo.description,
        "private": repo.private,
        "default_branch": repo.default_branch,
        "url": repo.html_url,
        "created": True,
    }


def create_or_update_file(repo_full_name: str, path: str, content: str,
                          message: str, token: str,
                          branch: Optional[str] = None) -> dict:
    repo = get_repo(repo_full_name, token)
    ref = branch or repo.default_branch
    kwargs: dict = {"path": path, "message": message, "content": content}
    if branch:
        kwargs["branch"] = branch
    try:
        existing = repo.get_contents(path, ref=ref)
        kwargs["sha"] = existing.sha
        action = "updated"
    except Exception:
        action = "created"
    result = repo.create_file(**kwargs) if action == "created" else repo.update_file(**kwargs)
    commit = result["commit"]
    content_obj = result.get("content")
    return {
        "action": action,
        "path": path,
        "sha": commit.sha[:7] if commit else None,
        "url": content_obj.html_url if content_obj else None,
        "commit_url": commit.html_url if commit else None,
    }


def delete_file(repo_full_name: str, path: str, message: str, token: str,
                branch: Optional[str] = None) -> dict:
    repo = get_repo(repo_full_name, token)
    ref = branch or repo.default_branch
    existing = repo.get_contents(path, ref=ref)
    kwargs: dict = {"path": path, "message": message, "sha": existing.sha}
    if branch:
        kwargs["branch"] = branch
    result = repo.delete_file(**kwargs)
    commit = result.get("commit")
    return {"action": "deleted", "path": path,
            "commit_url": commit.html_url if commit else None}


# ── GitHub Classroom (REST) ─────────────────────────────────────────────────

def _classroom_headers(token: str) -> dict:
    h = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def list_classrooms(token: str) -> list[dict]:
    with httpx.Client(timeout=15) as client:
        resp = client.get(f"{GITHUB_API}/classrooms", headers=_classroom_headers(token))
        resp.raise_for_status()
        return [{"id": c["id"], "name": c["name"], "archived": c.get("archived", False),
                 "url": c.get("url", "")} for c in resp.json()]


def get_classroom(classroom_id: int, token: str) -> dict:
    with httpx.Client(timeout=15) as client:
        resp = client.get(f"{GITHUB_API}/classrooms/{classroom_id}",
                          headers=_classroom_headers(token))
        resp.raise_for_status()
        data = resp.json()
        org = data.get("organization", {})
        return {
            "id": data["id"], "name": data["name"],
            "archived": data.get("archived", False), "url": data.get("url", ""),
            "organization": {"login": org.get("login"), "name": org.get("name"),
                             "html_url": org.get("html_url")},
        }


def list_assignments(classroom_id: int, token: str) -> list[dict]:
    with httpx.Client(timeout=15) as client:
        resp = client.get(f"{GITHUB_API}/classrooms/{classroom_id}/assignments",
                          headers=_classroom_headers(token))
        resp.raise_for_status()
        items = resp.json()
        if not isinstance(items, list):
            items = [items]
        return [{
            "id": a["id"], "title": a.get("title", ""), "slug": a.get("slug", ""),
            "type": a.get("type", ""), "invite_link": a.get("invite_link", ""),
            "accepted": a.get("accepted", 0), "submitted": a.get("submitted", 0),
            "passing": a.get("passing", 0), "language": a.get("language", ""),
            "deadline": a.get("deadline"), "editor": a.get("editor", ""),
        } for a in items]


def get_assignment(assignment_id: int, token: str) -> dict:
    with httpx.Client(timeout=15) as client:
        resp = client.get(f"{GITHUB_API}/assignments/{assignment_id}",
                          headers=_classroom_headers(token))
        resp.raise_for_status()
        a = resp.json()
        classroom = a.get("classroom", {})
        starter = a.get("starter_code_repository") or a.get("stater_code_repository") or {}
        return {
            "id": a["id"], "title": a.get("title", ""), "slug": a.get("slug", ""),
            "type": a.get("type", ""), "invite_link": a.get("invite_link", ""),
            "accepted": a.get("accepted", 0), "submitted": a.get("submitted", 0),
            "passing": a.get("passing", 0), "language": a.get("language", ""),
            "deadline": a.get("deadline"), "editor": a.get("editor", ""),
            "starter_code_repo": starter.get("full_name") if starter else None,
            "classroom": {"id": classroom.get("id"), "name": classroom.get("name")},
        }


def list_accepted_assignments(assignment_id: int, token: str,
                              page: int = 1, per_page: int = 30) -> list[dict]:
    with httpx.Client(timeout=15) as client:
        resp = client.get(
            f"{GITHUB_API}/assignments/{assignment_id}/accepted_assignments",
            headers=_classroom_headers(token),
            params={"page": page, "per_page": per_page},
        )
        resp.raise_for_status()
        results = []
        for s in resp.json():
            repo = s.get("repository", {})
            results.append({
                "id": s.get("id"), "submitted": s.get("submitted", False),
                "passing": s.get("passing", False), "commit_count": s.get("commit_count", 0),
                "grade": s.get("grade", ""),
                "students": [{"login": st.get("login"), "html_url": st.get("html_url")}
                             for st in s.get("students", [])],
                "repository": {"full_name": repo.get("full_name"),
                               "html_url": repo.get("html_url"),
                               "private": repo.get("private", False)},
            })
        return results


def get_assignment_grades(assignment_id: int, token: str) -> list[dict]:
    with httpx.Client(timeout=15) as client:
        resp = client.get(f"{GITHUB_API}/assignments/{assignment_id}/grades",
                          headers=_classroom_headers(token))
        resp.raise_for_status()
        return [{
            "assignment_name": g.get("assignment_name", ""),
            "github_username": g.get("github_username", ""),
            "student_repository_name": g.get("student_repository_name", ""),
            "student_repository_url": g.get("student_repository_url", ""),
            "submission_timestamp": g.get("submission_timestamp"),
            "points_awarded": g.get("points_awarded"),
            "points_available": g.get("points_available"),
            "group_name": g.get("group_name", ""),
        } for g in resp.json()]
