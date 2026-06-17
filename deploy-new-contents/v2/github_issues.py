"""GitHub Issues capability — new in Lumen v2.

v1 has no issues tool (its portfolio agent only did repos / commits / files), so
this is genuinely new logic and therefore lives in v2. It reuses v1's credential
helper `app.agents.portfolio_agent.get_github_credentials` (no v1 change) and
PyGithub (already a v1 dependency) to read the user's open issues.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("lumen.v2.github_issues")


async def list_open_issues(user_id: str, repo_hint: str | None = None,
                           limit: int = 20) -> dict:
    """List the user's open GitHub issues.

    If `repo_hint` is given (e.g. "owner/repo" or just "repo"), list open issues
    in that repo; otherwise search open issues that involve the authenticated
    user across GitHub. Returns {ok, connected, issues, reply}.
    """
    from app.agents.portfolio_agent import get_github_credentials

    token, owner = await get_github_credentials(user_id)
    if not token:
        return {
            "ok": False,
            "connected": False,
            "issues": [],
            "reply": "📁 Connect your GitHub first (Profile → Connect GitHub) to see your issues.",
        }

    try:
        from github import Auth, Github

        gh = Github(auth=Auth.Token(token))
        issues: list[dict] = []

        if repo_hint:
            full = repo_hint if "/" in repo_hint else f"{owner}/{repo_hint}"
            repo = gh.get_repo(full)
            for iss in repo.get_issues(state="open")[:limit]:
                if getattr(iss, "pull_request", None) is not None:
                    continue  # PRs surface in the issues API; skip them
                issues.append(_issue_dict(iss))
            scope = f"`{full}`"
        else:
            login = gh.get_user().login
            # Open issues that involve the user (created / assigned / mentioned),
            # excluding PRs.
            for iss in gh.search_issues(query=f"is:open is:issue involves:{login}")[:limit]:
                issues.append(_issue_dict(iss))
            scope = f"@{login}"

        if not issues:
            return {"ok": True, "connected": True, "issues": [],
                    "reply": f"✅ No open GitHub issues found for {scope}."}

        lines = [f"🐛 **Open GitHub issues ({scope})** — {len(issues)} shown:"]
        for it in issues:
            lines.append(f"- [#{it['number']}]({it['url']}) {it['title']} "
                         f"— `{it['repo']}`" + (f" · {', '.join(it['labels'])}" if it['labels'] else ""))
        return {"ok": True, "connected": True, "issues": issues, "reply": "\n".join(lines)}

    except Exception as e:
        logger.warning("list_open_issues failed: %s", e)
        return {"ok": False, "connected": True, "issues": [],
                "reply": f"Couldn't fetch your GitHub issues: {e}"}


def _issue_dict(iss) -> dict:
    repo_name = ""
    try:
        # search_issues items expose repository via .repository; repo.get_issues
        # items expose it the same way.
        repo_name = iss.repository.full_name
    except Exception:
        try:
            repo_name = iss.repository_url.split("/repos/")[-1]
        except Exception:
            repo_name = ""
    return {
        "number": iss.number,
        "title": iss.title,
        "url": iss.html_url,
        "repo": repo_name,
        "state": iss.state,
        "labels": [l.name for l in (iss.labels or [])],
    }
