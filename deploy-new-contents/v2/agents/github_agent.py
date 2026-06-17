"""AutoGen wrapper for the v1 GitHub (formerly Portfolio) specialist.

Replaces the old portfolio_agent. Exposes the full GitHub agent to Magentic-One:

- explore_github     -> app.agents.github_agent.handle_github (repos, commits, merges,
                        rebases, branches, PRs, files, code review, classroom, writes
                        with approval)
- manage_portfolio   -> app.agents.interaction_manager._handle_portfolio (artifact
                        upload/stage/commit/list, GitHub Actions) — kept AS-IS
- list_github_issues -> v2/github_issues.py (v2-only capability)
"""

from __future__ import annotations

from autogen_agentchat.agents import AssistantAgent

from app.agents.github_agent import handle_github
from app.agents.interaction_manager import _handle_portfolio
from v2.agents.base import make_agent, reply_text
from v2.github_issues import list_open_issues

NAME = "github"


def build(user_id: str, user_info: dict, model_client) -> AssistantAgent:
    async def explore_github(task: str) -> str:
        """Explore and act on any GitHub repository: summary, commits (filter by
        author/branch/date), merges, rebase detection, commit details, branches,
        pull requests, read/browse files, code review, and create/update/delete
        files or repos (these require user approval). Also GitHub Classroom:
        classrooms, assignments, submissions, grades. Pass the request as `task`."""
        return reply_text(await handle_github(user_id, task))

    async def manage_portfolio(task: str) -> str:
        """Work with the user's GitHub learning portfolio: list/read/delete files
        in TA folders (math-ta, cs-ta, …), stage and commit artifacts, and check
        GitHub Actions. Pass the request as `task`."""
        return reply_text(await _handle_portfolio(user_id, task))

    async def list_github_issues(task: str) -> str:
        """List the user's OPEN GitHub issues. Optionally scope to a repo by
        passing 'owner/repo' or a repo name as `task`; otherwise lists open issues
        that involve the user across GitHub."""
        hint = (task or "").strip() or None
        if hint and not ("/" in hint or hint.replace("-", "").replace("_", "").isalnum()):
            hint = None
        res = await list_open_issues(user_id, repo_hint=hint)
        return res.get("reply", "(no issues information)")

    return make_agent(
        name=NAME,
        description=(
            "The user's GitHub specialist: explore any repo (commits, merges, "
            "rebases, branches, PRs, files), review code, create/update/delete "
            "files & repos with approval, run the learning-portfolio artifact flow, "
            "list open issues, and inspect GitHub Classroom assignments & grades."
        ),
        instructions=(
            "You are Lumen's GitHub specialist. For any repository/commit/merge/"
            "rebase/branch/pull-request/file/code-review/classroom task call "
            "explore_github; for portfolio artifacts/staging/commits/Actions call "
            "manage_portfolio; for listing the user's open GitHub issues call "
            "list_github_issues. Call exactly one tool with the request as `task`, "
            "then report its result."
        ),
        tools=[explore_github, manage_portfolio, list_github_issues],
        model_client=model_client,
    )
