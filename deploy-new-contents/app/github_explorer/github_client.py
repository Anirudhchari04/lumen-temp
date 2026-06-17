"""
GitHub Repository Client — wraps PyGithub to expose repo, commit, merge,
and rebase information in a structured way.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from github import Auth, Github
from github.Commit import Commit
from github.Repository import Repository


@dataclass
class CommitInfo:
    sha: str
    short_sha: str
    message: str
    author: str
    date: datetime
    is_merge: bool
    parent_count: int
    url: str


@dataclass
class RepoSummary:
    full_name: str
    description: Optional[str]
    default_branch: str
    stars: int
    forks: int
    open_issues: int
    language: Optional[str]
    url: str


def _get_github_client(token: Optional[str] = None) -> Github:
    token = token or os.getenv("GITHUB_TOKEN")
    if not token:
        raise ValueError(
            "A GitHub token is required. Set GITHUB_TOKEN in .env or pass it explicitly."
        )
    return Github(auth=Auth.Token(token))


def get_repo(repo_full_name: str, token: Optional[str] = None) -> Repository:
    """Return a PyGithub Repository object for *owner/repo*."""
    gh = _get_github_client(token)
    return gh.get_repo(repo_full_name)


def create_repo(
    name: str,
    description: str = "",
    private: bool = False,
    auto_init: bool = True,
    token: Optional[str] = None,
) -> dict:
    """Create a new repository under the authenticated user's account."""
    gh = _get_github_client(token)
    user = gh.get_user()
    repo = user.create_repo(
        name=name,
        description=description,
        private=private,
        auto_init=auto_init,
    )
    return {
        "full_name": repo.full_name,
        "description": repo.description,
        "private": repo.private,
        "default_branch": repo.default_branch,
        "url": repo.html_url,
        "clone_url": repo.clone_url,
        "created": True,
    }


def list_user_repos(
    username: Optional[str] = None,
    max_count: int = 30,
    sort: str = "updated",
    token: Optional[str] = None,
) -> list[dict]:
    """List repositories for a user, or the authenticated user if no username given."""
    gh = _get_github_client(token)
    if username:
        user = gh.get_user(username)
    else:
        user = gh.get_user()  # authenticated user

    repos = user.get_repos(sort=sort)
    results = []
    for i, repo in enumerate(repos):
        if i >= max_count:
            break
        results.append(
            {
                "full_name": repo.full_name,
                "description": repo.description,
                "language": repo.language,
                "stars": repo.stargazers_count,
                "forks": repo.forks_count,
                "private": repo.private,
                "updated_at": repo.updated_at.isoformat() if repo.updated_at else None,
                "url": repo.html_url,
            }
        )
    return results


def repo_summary(repo_full_name: str, token: Optional[str] = None) -> dict:
    """High-level metadata about a repository."""
    repo = get_repo(repo_full_name, token)
    summary = RepoSummary(
        full_name=repo.full_name,
        description=repo.description,
        default_branch=repo.default_branch,
        stars=repo.stargazers_count,
        forks=repo.forks_count,
        open_issues=repo.open_issues_count,
        language=repo.language,
        url=repo.html_url,
    )
    return summary.__dict__


def _commit_to_info(commit: Commit) -> CommitInfo:
    author_name = "Unknown"
    if commit.author:
        author_name = commit.author.login
    elif commit.commit.author:
        author_name = commit.commit.author.name or "Unknown"

    return CommitInfo(
        sha=commit.sha,
        short_sha=commit.sha[:7],
        message=commit.commit.message,
        author=author_name,
        date=commit.commit.author.date,
        is_merge=len(commit.parents) > 1,
        parent_count=len(commit.parents),
        url=commit.html_url,
    )


def list_commits(
    repo_full_name: str,
    author: Optional[str] = None,
    branch: Optional[str] = None,
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
    max_count: int = 30,
    token: Optional[str] = None,
) -> list[dict]:
    """Return recent commits, optionally filtered by author/branch/date range."""
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

    commits = repo.get_commits(**kwargs)
    results = []
    for i, c in enumerate(commits):
        if i >= max_count:
            break
        info = _commit_to_info(c)
        results.append(info.__dict__)
    return results


def list_merges(
    repo_full_name: str,
    branch: Optional[str] = None,
    author: Optional[str] = None,
    max_count: int = 20,
    token: Optional[str] = None,
) -> list[dict]:
    """Return only merge commits (commits with >1 parent)."""
    repo = get_repo(repo_full_name, token)
    kwargs: dict = {}
    if branch:
        kwargs["sha"] = branch
    if author:
        kwargs["author"] = author

    commits = repo.get_commits(**kwargs)
    merges = []
    for c in commits:
        if len(merges) >= max_count:
            break
        if len(c.parents) > 1:
            merges.append(_commit_to_info(c).__dict__)
    return merges


def detect_rebases(
    repo_full_name: str,
    branch: Optional[str] = None,
    max_count: int = 30,
    token: Optional[str] = None,
) -> list[dict]:
    """
    Heuristic rebase detection.

    A rebase rewrites commit history, so we look for:
    - Commits whose committer date differs significantly from author date
      (force-pushed rebased commits keep the original author date but get a
       new committer date).
    - Commit messages that mention 'rebase'.
    """
    repo = get_repo(repo_full_name, token)
    kwargs: dict = {}
    if branch:
        kwargs["sha"] = branch

    commits = repo.get_commits(**kwargs)
    rebased: list[dict] = []
    for i, c in enumerate(commits):
        if i >= max_count * 3:  # scan wider window
            break

        author_date = c.commit.author.date
        committer_date = c.commit.committer.date
        diff_seconds = abs((committer_date - author_date).total_seconds())

        is_rebase_candidate = False
        reason = ""

        # Large gap between author and committer timestamps
        if diff_seconds > 60:
            is_rebase_candidate = True
            reason = (
                f"Author date ({author_date.isoformat()}) differs from "
                f"committer date ({committer_date.isoformat()}) by "
                f"{int(diff_seconds)}s — likely rebased"
            )

        # Message mentions rebase
        msg_lower = c.commit.message.lower()
        if "rebase" in msg_lower:
            is_rebase_candidate = True
            reason = reason or "Commit message mentions 'rebase'"

        if is_rebase_candidate:
            info = _commit_to_info(c).__dict__
            info["rebase_reason"] = reason
            rebased.append(info)
            if len(rebased) >= max_count:
                break

    return rebased


def get_commit_detail(
    repo_full_name: str,
    sha: str,
    token: Optional[str] = None,
) -> dict:
    """Full details for a single commit, including file changes."""
    repo = get_repo(repo_full_name, token)
    c = repo.get_commit(sha)
    info = _commit_to_info(c).__dict__
    info["files"] = [
        {
            "filename": f.filename,
            "status": f.status,
            "additions": f.additions,
            "deletions": f.deletions,
            "changes": f.changes,
        }
        for f in c.files
    ]
    info["stats"] = {
        "additions": c.stats.additions,
        "deletions": c.stats.deletions,
        "total": c.stats.total,
    }
    return info


def list_branches(
    repo_full_name: str,
    token: Optional[str] = None,
) -> list[dict]:
    """List all branches in the repo."""
    repo = get_repo(repo_full_name, token)
    return [
        {"name": b.name, "sha": b.commit.sha, "protected": b.protected}
        for b in repo.get_branches()
    ]


def list_pull_requests(
    repo_full_name: str,
    state: str = "all",
    max_count: int = 20,
    token: Optional[str] = None,
) -> list[dict]:
    """List pull requests (useful for seeing merge activity)."""
    repo = get_repo(repo_full_name, token)
    prs = repo.get_pulls(state=state, sort="updated", direction="desc")
    results = []
    for i, pr in enumerate(prs):
        if i >= max_count:
            break
        results.append(
            {
                "number": pr.number,
                "title": pr.title,
                "state": pr.state,
                "author": pr.user.login if pr.user else "Unknown",
                "created_at": pr.created_at.isoformat(),
                "merged": pr.merged,
                "merged_at": pr.merged_at.isoformat() if pr.merged_at else None,
                "merge_commit_sha": pr.merge_commit_sha,
                "url": pr.html_url,
            }
        )
    return results


def create_or_update_file(
    repo_full_name: str,
    path: str,
    content: str,
    message: str,
    branch: Optional[str] = None,
    token: Optional[str] = None,
) -> dict:
    """Create or update a file in a repository."""
    repo = get_repo(repo_full_name, token)
    kwargs: dict = {
        "path": path,
        "message": message,
        "content": content,
    }
    if branch:
        kwargs["branch"] = branch

    # Check if file already exists to get its SHA (needed for updates)
    try:
        existing = repo.get_contents(path, ref=branch or repo.default_branch)
        kwargs["sha"] = existing.sha
        action = "updated"
    except Exception:
        action = "created"

    result = repo.create_file(**kwargs) if action == "created" else repo.update_file(**kwargs)
    commit = result["commit"]
    return {
        "action": action,
        "path": path,
        "sha": commit.sha[:7] if commit else None,
        "full_sha": commit.sha if commit else None,
        "message": message,
        "url": result["content"].html_url if result.get("content") else None,
        "commit_url": commit.html_url if commit else None,
    }


def get_file_content(
    repo_full_name: str,
    path: str,
    branch: Optional[str] = None,
    token: Optional[str] = None,
) -> dict:
    """Read a file's content from a repository."""
    repo = get_repo(repo_full_name, token)
    ref = branch or repo.default_branch
    contents = repo.get_contents(path, ref=ref)
    if isinstance(contents, list):
        # It's a directory — return listing
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


def list_repo_contents(
    repo_full_name: str,
    path: str = "",
    branch: Optional[str] = None,
    token: Optional[str] = None,
) -> list[dict]:
    """List files and directories at a given path in the repo."""
    repo = get_repo(repo_full_name, token)
    ref = branch or repo.default_branch
    contents = repo.get_contents(path, ref=ref)
    if not isinstance(contents, list):
        contents = [contents]
    return [
        {"name": c.name, "path": c.path, "type": c.type, "size": c.size}
        for c in contents
    ]


def delete_file(
    repo_full_name: str,
    path: str,
    message: str,
    branch: Optional[str] = None,
    token: Optional[str] = None,
) -> dict:
    """Delete a file from a repository."""
    repo = get_repo(repo_full_name, token)
    ref = branch or repo.default_branch
    contents = repo.get_contents(path, ref=ref)
    result = repo.delete_file(
        path=path,
        message=message,
        sha=contents.sha,
        branch=ref,
    )
    commit = result.get("commit")
    return {
        "action": "deleted",
        "path": path,
        "branch": ref,
        "message": message,
        "sha": commit.sha[:7] if commit else None,
        "commit_url": commit.html_url if commit else None,
    }
