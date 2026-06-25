"""Portfolio / GitHub agent — repos, files, Actions, commits. Class-based: `GitHubAgent` holds the logic as `self` methods (logic unchanged)."""
from __future__ import annotations

from app.agents.base import BaseAgent, registry
from app.agents.intents import Intent


class GitHubAgent(BaseAgent):
    name = "github"
    intents = (Intent.PORTFOLIO,)
    aliases = ("portfolio",)
    description = "GitHub portfolio: repos, files, Actions, commits"
    # Offline keyword fallback owned by this agent (the LLM router is primary).
    KEYWORDS = (
        "from github", "from my github", "from repo", "from the repo",
        "from portfolio", "from my portfolio",
        "to github", "to my github", "to repo", "upload to github",
        "portfolio files", "my portfolio", "show portfolio",
        "list portfolio", "what's in my repo", "what is in my repo",
        "github files", "show my repo", "my github files",
        "delete from github", "remove from github",
        "my repo", "my repos", "my repositories", "my commits",
        "in my github", "on github", "on my github",
        "github repo", "show commits", "recent commits", "list commits",
        "what did i commit", "what's in the repo", "show files in",
        "shiksha-portfolio", "portfolio repo",
        "commit staged", "commit my staged", "commit the staged", "commit changes",
        "commit them", "commit now", "discard staged", "clear staged",
        "what's staged", "whats staged", "my staged", "staged file", "staged change",
        "github action", "github actions", "workflow run", "workflow runs", "ci status",
        "pull request", "pull requests", "merge commit", "merge commits", "rebase",
        "rebased", "branches", "branch list", "review code", "code review",
        "classroom", "classrooms", "assignment", "assignments", "open github",
        "open the github agent", "github agent", "create repo", "create a repo",
    )

    async def handle(self, user_id: str, message: str) -> dict:
        """Handle portfolio / GitHub queries and file operations from chat."""
        import re as _re
        from app.agents.portfolio_agent import (
            list_artifacts, delete_artifact, get_portfolio_status, ensure_portfolio_repo,
            get_github_credentials, PORTFOLIO_REPO_NAME,
        )

        msg = message.lower().strip()

        status = await get_portfolio_status(user_id)
        if not status.get("connected"):
            return {
                "reply": "📁 Connect your GitHub to use your portfolio. It opens GitHub, you click **Authorize**, and you're done — no token to paste.",
                "action": "portfolio_not_connected",
                "intent": Intent.PORTFOLIO,
                "agent_id": "portfolio",
                "cards": [{"type": "connect_github", "data": {"retry_message": message}}],
            }

        token, owner = await get_github_credentials(user_id)

        # ── Sub-intent: GitHub Actions / workflow runs ───────────────
        is_actions = any(kw in msg for kw in [
            "github action", "github actions", "workflow run", "workflow runs",
            "actions runs", "ci run", "ci status", "build status", "pipeline",
            "my actions", "show actions", "workflow status",
        ])
        if is_actions:
            from app.agents.portfolio_agent import list_workflow_runs
            result = await list_workflow_runs(user_id, limit=8)
            if not result.get("ok"):
                return {"reply": f"Couldn't load GitHub Actions: {result.get('error')}", "action": "error", "intent": Intent.PORTFOLIO, "agent_id": "portfolio"}
            runs = result.get("runs", [])
            if not runs:
                return {"reply": "⚙️ No GitHub Actions workflow runs in your portfolio repo yet.", "action": "inline_answer", "intent": Intent.PORTFOLIO, "agent_id": "portfolio"}
            lines = ["⚙️ **Recent GitHub Actions runs:**"]
            for r in runs[:8]:
                icon = "✅" if r.get("conclusion") == "success" else "❌" if r.get("conclusion") == "failure" else "🟡"
                lines.append(f"{icon} [{r.get('name', 'workflow')}]({r.get('url', '')}) — {r.get('status', '')} on `{r.get('branch', '')}`")
            return {"reply": "\n".join(lines), "action": "portfolio_actions", "intent": Intent.PORTFOLIO, "agent_id": "portfolio"}

        # ── Sub-intent: discard / list staged ───────────────────────
        if any(kw in msg for kw in ["discard staged", "clear staged"]):
            from app.agents.portfolio_agent import clear_staged, get_staged
            n = len(get_staged(user_id))
            clear_staged(user_id)
            return {"reply": f"🗑️ Discarded {n} staged change(s).", "action": "portfolio_staged_cleared", "intent": Intent.PORTFOLIO, "agent_id": "portfolio"}
        if any(kw in msg for kw in ["what's staged", "whats staged", "my staged", "staged file", "staged change", "list staged"]):
            from app.agents.portfolio_agent import get_staged
            staged = get_staged(user_id)
            if not staged:
                return {"reply": "Nothing is staged right now. Attach a file and say 'save to my portfolio' to stage one.", "action": "inline_answer", "intent": Intent.PORTFOLIO, "agent_id": "portfolio"}
            lines = [f"🟡 **{len(staged)} staged change(s)** (not committed):"]
            for s in staged:
                lines.append(f"- `{s['path']}` ({(s.get('size', 0) / 1024):.1f} KB)")
            lines.append("\nSay **commit staged** to push, or **discard staged** to drop.")
            return {"reply": "\n".join(lines), "action": "portfolio_staged_list", "intent": Intent.PORTFOLIO, "agent_id": "portfolio"}

        # ── Sub-intent: commit staged changes ────────────────────────
        is_commit_action = any(kw in msg for kw in [
            "commit staged", "commit my staged", "commit the staged", "commit changes",
            "commit my files", "commit now", "commit them", "push staged",
        ])
        if is_commit_action:
            from app.agents.portfolio_agent import commit_staged, get_staged
            if not get_staged(user_id):
                return {"reply": "Nothing is staged to commit. Stage a file from the Portfolio page first, then say 'commit staged'.", "action": "inline_answer", "intent": Intent.PORTFOLIO, "agent_id": "portfolio"}
            result = await commit_staged(user_id)
            if not result.get("ok"):
                return {"reply": f"Commit failed: {result.get('error')}", "action": "error", "intent": Intent.PORTFOLIO, "agent_id": "portfolio"}
            committed = result.get("committed", [])
            return {"reply": f"✅ Committed {len(committed)} file(s) to your portfolio.", "action": "portfolio_committed", "intent": Intent.PORTFOLIO, "agent_id": "portfolio"}

        # ── Sub-intent: read/retrieve file content ───────────────────
        content_match = _re.search(
            r"(?:show|open|read|view|get|display|cat|content[s]? of|what'?s in)\s+"
            r"(?:the\s+|my\s+|file\s+)*([\w\-./]+\.\w+)",
            msg,
        )
        if content_match:
            from app.agents.portfolio_agent import get_file_content
            fpath = content_match.group(1).strip()
            result = await get_file_content(user_id, fpath)
            if result.get("ok"):
                if not result.get("is_text"):
                    return {"reply": f"📄 **{result.get('name')}** is a binary file ({result.get('size', 0)} bytes). [Download]({result.get('download_url', '')})", "action": "inline_answer", "intent": Intent.PORTFOLIO, "agent_id": "portfolio"}
                body = result.get("content", "")
                trunc = "\n\n…(truncated)" if result.get("truncated") else ""
                return {
                    "reply": f"📄 **{result.get('path')}**:\n\n```\n{body}\n```{trunc}",
                    "action": "portfolio_file_content",
                    "intent": Intent.PORTFOLIO,
                    "agent_id": "portfolio",
                }
            # Not found by exact path — fall through to normal listing/search below.

        # ── Sub-intent: commits ──────────────────────────────────────
        is_commits = any(kw in msg for kw in ["commit", "commits", "what did i commit", "recent commit"])
        if is_commits:
            try:
                from github import Auth, Github
                gh = Github(auth=Auth.Token(token))
                repo_name = f"{owner}/{PORTFOLIO_REPO_NAME}"
                repo = gh.get_repo(repo_name)
                commits = list(repo.get_commits()[:10])
                if not commits:
                    return {"reply": "No commits found in your portfolio repo yet.", "action": "inline_answer", "intent": Intent.PORTFOLIO, "agent_id": "portfolio"}
                lines = [f"**Recent commits in `{repo_name}`:**"]
                for c in commits:
                    date = c.commit.author.date.strftime("%b %d") if c.commit.author else "?"
                    sha = c.sha[:7]
                    msg_line = c.commit.message.split("\n")[0][:60]
                    lines.append(f"- `{sha}` {date} — {msg_line}")
                return {
                    "reply": "\n".join(lines),
                    "action": "portfolio_commits",
                    "intent": Intent.PORTFOLIO,
                    "agent_id": "portfolio",
                }
            except Exception as e:
                return {"reply": f"Couldn't fetch commits: {e}", "action": "error", "intent": Intent.PORTFOLIO, "agent_id": "portfolio"}

        # ── Sub-intent: list all user repos ─────────────────────────
        is_repos = any(kw in msg for kw in [
            "my repos", "my repositories", "list repos", "all repos", "list my repos",
            "my github repos", "my github repositories", "github repos", "github repositories",
            "list github repos", "list my github", "show github repos", "show my repos",
            "show my github repos", "show my github",
        ])
        if is_repos:
            try:
                from github import Auth, Github
                gh = Github(auth=Auth.Token(token))
                user = gh.get_user()
                repos = list(user.get_repos(sort="updated"))[:15]
                if not repos:
                    return {"reply": "No repositories found.", "action": "inline_answer", "intent": Intent.PORTFOLIO, "agent_id": "portfolio"}
                lines = [f"**Your GitHub repositories (@{user.login}):**"]
                for r in repos:
                    vis = "🔒" if r.private else "🌐"
                    lines.append(f"{vis} [{r.name}]({r.html_url}) — {r.description or 'no description'}")
                return {
                    "reply": "\n".join(lines),
                    "action": "portfolio_repos",
                    "intent": Intent.PORTFOLIO,
                    "agent_id": "portfolio",
                }
            except Exception as e:
                return {"reply": f"Couldn't fetch repos: {e}", "action": "error", "intent": Intent.PORTFOLIO, "agent_id": "portfolio"}

        # ── Sub-intent: list specific folder ────────────────────────
        folder_match = _re.search(
            r"(?:files? in|contents? of|inside|in the?)\s+([\w\-]+(?:/[\w\-]+)?)\s*(?:folder|directory|ta|path)?",
            msg
        )
        if folder_match:
            raw_hint = folder_match.group(1).strip()
            # Map natural names → canonical TA folders: "math"/"math ta" → "math-ta",
            # "cs"/"computer" → "cs-ta", etc. Fall back to the raw hint otherwise.
            from app.agents.portfolio_agent import detect_ta_folder
            canonical = detect_ta_folder(msg)
            path_hint = canonical if canonical != "general" else raw_hint
            result = await list_artifacts(user_id, path_hint)
            if result.get("ok"):
                files = result.get("files", [])
                if not files:
                    return {"reply": f"The `{path_hint}/` folder is empty.", "action": "inline_answer", "intent": Intent.PORTFOLIO, "agent_id": "portfolio"}
                return {
                    "reply": f"📂 `{path_hint}/` — {len(files)} item(s):",
                    "action": "portfolio_list",
                    "intent": Intent.PORTFOLIO,
                    "agent_id": "portfolio",
                    "cards": [{"type": "portfolio_files", "data": {"files": files, "mode": "browse"}}],
                }

        # ── Sub-intent: delete/remove ────────────────────────────────
        is_delete = any(kw in msg for kw in ["remove", "delete", "del "])
        if is_delete:
            stripped = _re.sub(
                r"(remove|delete|del|from github|from my github|from repo|from the repo|from portfolio|from my portfolio|please)",
                "", msg
            ).strip().strip(".")
            root = await list_artifacts(user_id, "")
            all_files = []
            if root.get("ok"):
                for item in root.get("files", []):
                    if item["type"] == "dir":
                        sub = await list_artifacts(user_id, item["path"])
                        if sub.get("ok"):
                            all_files.extend(f for f in sub.get("files", []) if f["type"] == "file")
                    else:
                        all_files.append(item)

            if not all_files:
                return {"reply": "📁 Your portfolio is empty — nothing to remove.", "action": "inline_answer", "intent": Intent.PORTFOLIO, "agent_id": "portfolio"}

            def score(f):
                name = f["name"].lower().replace("_", " ").replace("-", " ")
                return any(len(word) > 2 and word in name for word in stripped.split())

            matches = [f for f in all_files if score(f)]
            if len(matches) == 1:
                result = await delete_artifact(user_id, matches[0]["path"])
                if result.get("ok"):
                    return {"reply": f"🗑️ Deleted **{matches[0]['name']}** from your portfolio.", "action": "portfolio_deleted", "intent": Intent.PORTFOLIO, "agent_id": "portfolio"}
                return {"reply": f"Couldn't delete: {result.get('error')}", "action": "error", "intent": Intent.PORTFOLIO, "agent_id": "portfolio"}
            elif len(matches) > 1:
                return {
                    "reply": f"Found {len(matches)} matching files — which one?",
                    "action": "portfolio_pick_delete",
                    "intent": Intent.PORTFOLIO,
                    "agent_id": "portfolio",
                    "cards": [{"type": "portfolio_files", "data": {"files": matches, "mode": "delete"}}],
                }
            else:
                return {
                    "reply": f"I couldn't find \"{stripped.strip()}\" — here are all your files:",
                    "action": "portfolio_list",
                    "intent": Intent.PORTFOLIO,
                    "agent_id": "portfolio",
                    "cards": [{"type": "portfolio_files", "data": {"files": all_files, "mode": "delete"}}],
                }

        # ── Default: list portfolio root ─────────────────────────────
        if not status.get("repo_exists"):
            setup = await ensure_portfolio_repo(user_id)
            if not setup.get("ok"):
                return {
                    "reply": f"📁 Couldn't create your portfolio repo: {setup.get('error', 'unknown error')}. Check your GitHub connection in Profile.",
                    "action": "portfolio_not_connected",
                    "intent": Intent.PORTFOLIO,
                    "agent_id": "portfolio",
                }
            if setup.get("created"):
                return {
                    "reply": f"📁 Created your portfolio repo **{setup.get('full_name', PORTFOLIO_REPO_NAME)}**! It's empty — upload files via the 📎 button or say 'add to my github'.\n[View on GitHub]({setup.get('url', '')})",
                    "action": "portfolio_created",
                    "intent": Intent.PORTFOLIO,
                    "agent_id": "portfolio",
                }

        result = await list_artifacts(user_id, "")
        if not result.get("ok"):
            return {"reply": f"Couldn't load portfolio: {result.get('error')}", "action": "error", "intent": Intent.PORTFOLIO, "agent_id": "portfolio"}

        files = result.get("files", [])
        if not files:
            return {"reply": "📁 Your portfolio repo is empty. Upload files via the 📎 button and say 'add to my github'.", "action": "inline_answer", "intent": Intent.PORTFOLIO, "agent_id": "portfolio"}

        folders = [f for f in files if f["type"] == "dir"]
        top_files = [f for f in files if f["type"] == "file"]
        parts = []
        if folders:
            parts.append(f"{len(folders)} folder(s): " + ", ".join(f'`{f["name"]}`' for f in folders))
        if top_files:
            parts.append(f"{len(top_files)} file(s) at root")

        return {
            "reply": f"📁 **{owner}/{PORTFOLIO_REPO_NAME}** — {', '.join(parts) if parts else 'empty'}.\n[View on GitHub]({status.get('repo_url', '')})",
            "action": "portfolio_list",
            "intent": Intent.PORTFOLIO,
            "agent_id": "portfolio",
            "cards": [{"type": "portfolio_files", "data": {"files": files, "mode": "browse"}}],
        }

    async def broker(self, env: dict) -> dict:
        from app.agents.github_agent import handle_github
        return await handle_github(env["user_id"], env["message"])


agent = GitHubAgent()
registry.register_agent(agent)

# Back-compat alias for `from app.agents.interaction_manager import _handle_portfolio`.
_handle_portfolio = agent.handle
