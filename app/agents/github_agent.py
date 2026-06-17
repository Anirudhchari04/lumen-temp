"""GitHub Agent — Lumen's conversational GitHub specialist.

Replaces the former Portfolio Agent as the registered ``github`` agent while
preserving every portfolio capability (artifact upload / stage / commit / list,
GitHub Actions, Coding-TA auto-save) by delegating those sub-intents to the
existing ``_handle_portfolio`` brain.

New capabilities (ported from the GitHub challenge demo) on top of portfolio:
  - Repo summary, list user repos
  - List commits (filter by author / branch / date), merges, rebase detection
  - Commit details, branches, pull requests
  - Read files / browse repo tree, code review
  - Create repo / create-or-update file / delete file (user-approved writes)
  - GitHub Classroom: classrooms, assignments, submissions, grades

LLM uses the project's existing Azure OpenAI deployment via Entra ID (no API
keys), matching every other Lumen agent.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import Optional

from app.config import settings
from app.agents import github_client as gh

logger = logging.getLogger(__name__)

AGENT_ID = "github"
PORTFOLIO_REPO_NAME = "shiksha-portfolio"


# ── Azure OpenAI (Entra ID, no API keys) ────────────────────────────────────

_raw_client = None


def _get_raw_client():
    """Return a cached AsyncAzureOpenAI client authed via Entra ID bearer token."""
    global _raw_client
    if _raw_client is not None:
        return _raw_client
    from openai import AsyncAzureOpenAI
    from azure.identity import (
        DefaultAzureCredential,
        ManagedIdentityCredential,
        get_bearer_token_provider,
    )
    if settings.azure_managed_identity_client_id:
        cred = ManagedIdentityCredential(client_id=settings.azure_managed_identity_client_id)
    else:
        cred = DefaultAzureCredential()
    token_provider = get_bearer_token_provider(
        cred, "https://cognitiveservices.azure.com/.default"
    )
    _raw_client = AsyncAzureOpenAI(
        azure_endpoint=settings.azure_openai_endpoint,
        azure_ad_token_provider=token_provider,
        api_version=settings.azure_openai_api_version,
    )
    return _raw_client


# ── Tool schema (OpenAI function-calling) ───────────────────────────────────

_REPO = {"type": "string", "description": "Repository in 'owner/repo' format."}

TOOLS = [
    {"type": "function", "function": {
        "name": "repo_summary",
        "description": "Get high-level metadata about a repository (stars, forks, language, default branch).",
        "parameters": {"type": "object", "properties": {"repo_full_name": _REPO}, "required": ["repo_full_name"]}}},
    {"type": "function", "function": {
        "name": "list_user_repos",
        "description": "List repositories for a GitHub user, or the authenticated user if username omitted.",
        "parameters": {"type": "object", "properties": {
            "username": {"type": "string", "description": "GitHub username; omit for the signed-in user."},
            "max_count": {"type": "integer"}, "sort": {"type": "string", "enum": ["updated", "created", "pushed", "full_name"]}},
            "required": []}}},
    {"type": "function", "function": {
        "name": "list_commits",
        "description": "List recent commits. Filter by author, branch, since/until ISO dates.",
        "parameters": {"type": "object", "properties": {
            "repo_full_name": _REPO, "author": {"type": "string"}, "branch": {"type": "string"},
            "since": {"type": "string"}, "until": {"type": "string"}, "max_count": {"type": "integer"}},
            "required": ["repo_full_name"]}}},
    {"type": "function", "function": {
        "name": "list_merges",
        "description": "List merge commits (more than one parent) in a repository.",
        "parameters": {"type": "object", "properties": {
            "repo_full_name": _REPO, "branch": {"type": "string"}, "author": {"type": "string"}, "max_count": {"type": "integer"}},
            "required": ["repo_full_name"]}}},
    {"type": "function", "function": {
        "name": "detect_rebases",
        "description": "Detect likely-rebased commits via author/committer date mismatch or 'rebase' in the message.",
        "parameters": {"type": "object", "properties": {
            "repo_full_name": _REPO, "branch": {"type": "string"}, "max_count": {"type": "integer"}},
            "required": ["repo_full_name"]}}},
    {"type": "function", "function": {
        "name": "get_commit_detail",
        "description": "Full details for a single commit including changed files and line stats.",
        "parameters": {"type": "object", "properties": {"repo_full_name": _REPO, "sha": {"type": "string"}},
                       "required": ["repo_full_name", "sha"]}}},
    {"type": "function", "function": {
        "name": "list_branches", "description": "List all branches in the repository.",
        "parameters": {"type": "object", "properties": {"repo_full_name": _REPO}, "required": ["repo_full_name"]}}},
    {"type": "function", "function": {
        "name": "list_pull_requests", "description": "List pull requests for a repository.",
        "parameters": {"type": "object", "properties": {
            "repo_full_name": _REPO, "state": {"type": "string", "enum": ["open", "closed", "all"]}, "max_count": {"type": "integer"}},
            "required": ["repo_full_name"]}}},
    {"type": "function", "function": {
        "name": "get_file_content", "description": "Read a file's content (or list a directory) from a repository.",
        "parameters": {"type": "object", "properties": {
            "repo_full_name": _REPO, "path": {"type": "string"}, "branch": {"type": "string"}},
            "required": ["repo_full_name", "path"]}}},
    {"type": "function", "function": {
        "name": "list_repo_contents", "description": "List files and directories at a path in a repository.",
        "parameters": {"type": "object", "properties": {
            "repo_full_name": _REPO, "path": {"type": "string"}, "branch": {"type": "string"}},
            "required": ["repo_full_name"]}}},
    {"type": "function", "function": {
        "name": "review_code", "description": "Fetch a file's committed content for code review and analysis.",
        "parameters": {"type": "object", "properties": {"repo_full_name": _REPO, "path": {"type": "string"}, "branch": {"type": "string"}},
                       "required": ["repo_full_name", "path"]}}},
    # ── Write operations — these PROPOSE an action requiring user approval ──
    {"type": "function", "function": {
        "name": "create_repo",
        "description": "Propose creating a new repository under the signed-in user (requires user approval).",
        "parameters": {"type": "object", "properties": {
            "name": {"type": "string"}, "description": {"type": "string"},
            "private": {"type": "boolean"}, "auto_init": {"type": "boolean"}}, "required": ["name"]}}},
    {"type": "function", "function": {
        "name": "create_or_update_file",
        "description": "Propose creating/updating a file in a repository (requires user approval).",
        "parameters": {"type": "object", "properties": {
            "repo_full_name": _REPO, "path": {"type": "string"}, "content": {"type": "string"},
            "message": {"type": "string"}, "branch": {"type": "string"}},
            "required": ["repo_full_name", "path", "content", "message"]}}},
    {"type": "function", "function": {
        "name": "delete_file",
        "description": "Propose deleting a file from a repository (requires user approval).",
        "parameters": {"type": "object", "properties": {
            "repo_full_name": _REPO, "path": {"type": "string"}, "message": {"type": "string"}, "branch": {"type": "string"}},
            "required": ["repo_full_name", "path", "message"]}}},
    # ── GitHub Classroom ──
    {"type": "function", "function": {
        "name": "list_classrooms", "description": "List GitHub Classrooms the user administers.",
        "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {
        "name": "get_classroom", "description": "Get details for a specific GitHub Classroom.",
        "parameters": {"type": "object", "properties": {"classroom_id": {"type": "integer"}}, "required": ["classroom_id"]}}},
    {"type": "function", "function": {
        "name": "list_assignments", "description": "List assignments in a GitHub Classroom.",
        "parameters": {"type": "object", "properties": {"classroom_id": {"type": "integer"}}, "required": ["classroom_id"]}}},
    {"type": "function", "function": {
        "name": "get_assignment", "description": "Get details for a specific assignment.",
        "parameters": {"type": "object", "properties": {"assignment_id": {"type": "integer"}}, "required": ["assignment_id"]}}},
    {"type": "function", "function": {
        "name": "list_accepted_assignments", "description": "List student submissions for an assignment.",
        "parameters": {"type": "object", "properties": {"assignment_id": {"type": "integer"}}, "required": ["assignment_id"]}}},
    {"type": "function", "function": {
        "name": "get_assignment_grades", "description": "Get grades for all students in an assignment.",
        "parameters": {"type": "object", "properties": {"assignment_id": {"type": "integer"}}, "required": ["assignment_id"]}}},
]

# Tools that mutate GitHub — routed through the approval store instead of run now.
_WRITE_TOOLS = {"create_repo", "create_or_update_file", "delete_file"}


# ── Pending-approval store (per user) ───────────────────────────────────────

_pending: dict[str, list[dict]] = {}
_action_seq = 0


def _new_action(user_id: str, action: dict) -> dict:
    global _action_seq
    _action_seq += 1
    action_id = f"gh_{_action_seq}"
    action["id"] = action_id
    action["status"] = "pending"
    _pending.setdefault(user_id, []).append(action)
    return action


def get_pending(user_id: str) -> list[dict]:
    return [a for a in _pending.get(user_id, []) if a.get("status") == "pending"]


def _clear_pending(user_id: str) -> None:
    _pending.pop(user_id, None)


def _describe_action(a: dict) -> str:
    t = a["type"]
    if t == "create_repo":
        vis = "private" if a.get("private") else "public"
        return f"Create {vis} repo `{a['name']}`"
    if t == "create_or_update_file":
        return f"Write `{a['path']}` in `{a['repo_full_name']}` — {a['message']}"
    if t == "delete_file":
        return f"Delete `{a['path']}` from `{a['repo_full_name']}`"
    return t


async def approve_action(user_id: str, action_id: Optional[str] = None) -> dict:
    """Execute one (or all) pending GitHub write action(s) for the user."""
    from app.agents.portfolio_agent import get_github_credentials
    token, _owner = await get_github_credentials(user_id)
    if not token:
        return {"reply": "GitHub not connected.", "action": "error", "agent_id": AGENT_ID}

    pending = get_pending(user_id)
    if not pending:
        return {"reply": "There's no pending GitHub action to approve.",
                "action": "inline_answer", "agent_id": AGENT_ID}

    targets = [a for a in pending if (action_id is None or a["id"] == action_id)]
    if not targets:
        return {"reply": f"No pending action `{action_id}`.", "action": "inline_answer", "agent_id": AGENT_ID}

    done, failed = [], []
    for a in targets:
        try:
            if a["type"] == "create_repo":
                res = gh.create_repo(a["name"], token, a.get("description", ""),
                                     a.get("private", False), a.get("auto_init", True))
            elif a["type"] == "create_or_update_file":
                res = gh.create_or_update_file(a["repo_full_name"], a["path"], a["content"],
                                               a["message"], token, a.get("branch"))
            elif a["type"] == "delete_file":
                res = gh.delete_file(a["repo_full_name"], a["path"], a["message"], token, a.get("branch"))
            else:
                res = {"error": f"unknown action {a['type']}"}
            a["status"] = "approved"
            done.append((a, res))
        except Exception as e:
            a["status"] = "failed"
            failed.append((a, str(e)))

    lines = []
    for a, res in done:
        url = res.get("url") or res.get("commit_url") or ""
        lines.append(f"✅ {_describe_action(a)}" + (f" — [view]({url})" if url else ""))
    for a, err in failed:
        lines.append(f"❌ {_describe_action(a)} — {err}")
    return {"reply": "\n".join(lines) or "Nothing to do.",
            "action": "github_action_done", "agent_id": AGENT_ID, "intent": "portfolio"}


def reject_action(user_id: str, action_id: Optional[str] = None) -> dict:
    pending = get_pending(user_id)
    if not pending:
        return {"reply": "There's no pending GitHub action to cancel.",
                "action": "inline_answer", "agent_id": AGENT_ID}
    n = 0
    for a in pending:
        if action_id is None or a["id"] == action_id:
            a["status"] = "rejected"
            n += 1
    return {"reply": f"🚫 Cancelled {n} pending GitHub action(s).",
            "action": "github_action_rejected", "agent_id": AGENT_ID}


# ── Tool dispatch ───────────────────────────────────────────────────────────

def _parse_dates(args: dict) -> dict:
    for key in ("since", "until"):
        if isinstance(args.get(key), str):
            try:
                args[key] = datetime.fromisoformat(args[key])
            except ValueError:
                args.pop(key, None)
    return args


def _call_tool(name: str, args: dict, user_id: str, token: str) -> str:
    """Run a read tool now, or register a write tool for approval. Returns JSON."""
    try:
        if name in _WRITE_TOOLS:
            action = {"type": name, **args}
            created = _new_action(user_id, action)
            return json.dumps({
                "status": "pending_approval",
                "action_id": created["id"],
                "summary": _describe_action(created),
                "note": "This change is NOT applied yet. Tell the user to approve it.",
            })

        args = _parse_dates(dict(args))
        read_dispatch = {
            "repo_summary": gh.repo_summary,
            "list_user_repos": gh.list_user_repos,
            "list_commits": gh.list_commits,
            "list_merges": gh.list_merges,
            "detect_rebases": gh.detect_rebases,
            "get_commit_detail": gh.get_commit_detail,
            "list_branches": gh.list_branches,
            "list_pull_requests": gh.list_pull_requests,
            "get_file_content": gh.get_file_content,
            "list_repo_contents": gh.list_repo_contents,
            "review_code": gh.get_file_content,
            "list_classrooms": gh.list_classrooms,
            "get_classroom": gh.get_classroom,
            "list_assignments": gh.list_assignments,
            "get_assignment": gh.get_assignment,
            "list_accepted_assignments": gh.list_accepted_assignments,
            "get_assignment_grades": gh.get_assignment_grades,
        }
        fn = read_dispatch.get(name)
        if fn is None:
            return json.dumps({"error": f"Unknown tool: {name}"})
        result = fn(token=token, **args)
        return json.dumps(result, default=str)
    except Exception as e:
        return json.dumps({"error": str(e)})


SYSTEM_PROMPT = """\
You are Lumen's GitHub specialist. The user has connected their GitHub account; \
their token is applied automatically — never ask for it.

Capabilities: summarise repos; list/filter commits, merges and rebases; show \
commit details, branches and pull requests; read files and browse repo trees; \
review code; create repos / create-update / delete files; and GitHub Classroom \
(classrooms, assignments, submissions, grades).

Rules:
- Always call a tool to fetch real data. Never invent commits, files or grades.
- The signed-in user's account owner is "{owner}". When the user says "my repo", \
"my portfolio" or "my github" without naming a repo, default to \
"{owner}/{portfolio_repo}". For other phrasing, ask which repo if ambiguous.
- Writes (create_repo, create_or_update_file, delete_file) do NOT apply \
immediately — they create a pending action the user must approve. After calling \
one, clearly tell the user it is pending approval and they can say "approve" or \
"cancel".
- Be concise. Use short tables or bullet lists for commits/PRs; show the 7-char \
short SHA and a link when referencing a commit.
"""


async def _run_agent_loop(user_id: str, token: str, owner: str, message: str) -> dict:
    client = _get_raw_client()
    system = SYSTEM_PROMPT.format(owner=owner or "the user",
                                  portfolio_repo=PORTFOLIO_REPO_NAME)
    messages = [{"role": "system", "content": system},
                {"role": "user", "content": message}]

    for _ in range(8):  # bounded tool-calling loop
        resp = await client.chat.completions.create(
            model=settings.azure_openai_deployment,
            messages=messages,
            tools=TOOLS,
            tool_choice="auto",
        )
        choice = resp.choices[0].message
        messages.append(choice.model_dump())
        if not choice.tool_calls:
            reply = choice.content or "(no response)"
            cards = []
            pend = get_pending(user_id)
            if pend:
                cards.append({"type": "github_actions", "data": {
                    "actions": [{"id": a["id"], "summary": _describe_action(a)} for a in pend]
                }})
            return {"reply": reply, "action": "github_query", "intent": "portfolio",
                    "agent_id": AGENT_ID, "cards": cards}

        for tc in choice.tool_calls:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            result = _call_tool(tc.function.name, args, user_id, token)
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})

    return {"reply": "I wasn't able to finish that GitHub request — try narrowing it down.",
            "action": "error", "intent": "portfolio", "agent_id": AGENT_ID}


# ── Portfolio sub-intent detection (delegate to the existing brain) ─────────

_PORTFOLIO_KW = (
    "stage", "staged", "commit staged", "discard staged", "clear staged",
    "upload", "save to my portfolio", "save this", "my portfolio files",
    "list portfolio", "show portfolio", "portfolio status",
    "github action", "github actions", "workflow run", "workflow runs", "ci status",
    "artifact", "artifacts",
)

_APPROVE_KW = ("approve", "yes do it", "go ahead", "confirm", "apply it", "do it")
_REJECT_KW = ("reject", "cancel", "discard action", "don't do it", "do not do it", "no don't")


def _looks_like_portfolio(msg: str) -> bool:
    return any(kw in msg for kw in _PORTFOLIO_KW)


# ── Public entry point ──────────────────────────────────────────────────────

async def handle_github(user_id: str, message: str) -> dict:
    """Main GitHub agent entry. Preserves portfolio behaviour and adds full
    GitHub repo/classroom exploration via an LLM tool loop."""
    from app.agents.portfolio_agent import get_portfolio_status, get_github_credentials
    from app.agents.interaction_manager import _handle_portfolio

    msg = (message or "").lower().strip()

    # Connection gate (mirror the portfolio connect card).
    status = await get_portfolio_status(user_id)
    if not status.get("connected"):
        return {
            "reply": "📁 Connect your GitHub to use the GitHub agent. It opens GitHub, you click **Authorize**, and you're done — no token to paste.",
            "action": "portfolio_not_connected",
            "intent": "portfolio",
            "agent_id": AGENT_ID,
            "cards": [{"type": "connect_github", "data": {"retry_message": message}}],
        }

    # Approve / reject a pending write action.
    if get_pending(user_id):
        action_id = None
        m = re.search(r"\b(gh_\d+)\b", msg)
        if m:
            action_id = m.group(1)
        if any(kw in msg for kw in _APPROVE_KW):
            return await approve_action(user_id, action_id)
        if any(kw in msg for kw in _REJECT_KW):
            return reject_action(user_id, action_id)

    # Preserve every existing Lumen-portfolio task.
    if _looks_like_portfolio(msg):
        return await _handle_portfolio(user_id, message)

    # Full GitHub exploration via the LLM tool loop.
    token, owner = await get_github_credentials(user_id)
    try:
        return await _run_agent_loop(user_id, token, owner or "", message)
    except Exception as e:
        logger.exception("github agent loop failed")
        # Fall back to the portfolio brain so the user still gets a useful answer.
        try:
            return await _handle_portfolio(user_id, message)
        except Exception:
            return {"reply": f"GitHub error: {e}", "action": "error",
                    "intent": "portfolio", "agent_id": AGENT_ID}


# ── Agent card ──────────────────────────────────────────────────────────────

def get_agent_card(base_url: str = "") -> "AgentCard":
    from app.protocols.models import (
        AgentCard, AgentProvider, AgentInterface, AgentCapabilities, AgentSkill,
    )
    return AgentCard(
        name="GitHub Agent",
        description=(
            "Lumen's GitHub specialist: explore any repository (commits, merges, "
            "rebases, branches, pull requests, files), review code, manage repos "
            "and files with approval, run the learning-portfolio artifact flow, "
            "and inspect GitHub Classroom assignments and grades."
        ),
        version="1.0.0",
        documentationUrl=f"{base_url}/docs/github",
        provider=AgentProvider(organization="Lumen Network", url=base_url),
        supportedInterfaces=[AgentInterface(url=f"{base_url}/a2a/github")],
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
                id="github.explore_repo", name="Explore Repository",
                description="Summaries, commits, merges, rebases, commit details, branches and pull requests for any repo.",
                tags=["github", "repo", "commits", "merges", "rebase", "branches", "pull-requests"],
                examples=["Tell me about microsoft/vscode", "Show the last 5 commits by alice",
                          "Any rebases on develop?", "List open PRs in my repo"],
            ),
            AgentSkill(
                id="github.files", name="Files & Code Review",
                description="Read files, browse the repo tree, review code, and create/update/delete files (with approval).",
                tags=["github", "files", "code-review", "create", "update", "delete"],
                examples=["Read src/main.py in my repo", "Review app.py", "Create a README in my-repo"],
            ),
            AgentSkill(
                id="github.portfolio", name="Learning Portfolio",
                description="List / upload / stage / commit learning artifacts in the GitHub portfolio, plus GitHub Actions status.",
                tags=["portfolio", "artifacts", "github", "actions", "commit"],
                examples=["Show my portfolio", "Save this solution", "Commit staged", "Show my GitHub Actions"],
            ),
            AgentSkill(
                id="github.classroom", name="GitHub Classroom",
                description="List classrooms and assignments, view student submissions and grades.",
                tags=["github", "classroom", "assignments", "grades", "education"],
                examples=["List my classrooms", "Show assignments in classroom 123", "Get grades for assignment 456"],
            ),
        ],
    )
