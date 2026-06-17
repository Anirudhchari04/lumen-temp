"""
GitHub Agent — a conversational agent that uses Azure AI Foundry with
function-calling to let users explore any GitHub repository's commits,
merges, and rebases.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from typing import Optional

from app.config import settings

from . import github_client
from . import classroom_client

# Cached sync Azure OpenAI client (Entra ID auth, shared across sessions).
_openai_client = None


def _get_openai_client():
    """Return a cached sync AzureOpenAI client authed via Entra ID.

    Mirrors the auth used elsewhere in Lumen (see app/agents/github_agent.py)
    so the standalone explorer works inside the Lumen deployment without an
    OpenAI API key or a separate Foundry project endpoint.
    """
    global _openai_client
    if _openai_client is not None:
        return _openai_client
    from openai import AzureOpenAI
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
    _openai_client = AzureOpenAI(
        azure_endpoint=settings.azure_openai_endpoint,
        azure_ad_token_provider=token_provider,
        api_version=settings.azure_openai_api_version,
    )
    return _openai_client


def _sanitize_for_content_filter(text: str) -> str:
    """Remove patterns that Azure content filters may flag as PII.
    
    The filter sometimes misidentifies SHAs, numeric IDs, and URLs as
    phone numbers or other PII. We redact only the ambiguous patterns
    while keeping the data useful.
    """
    # Replace long digit sequences (10+ digits) that look like phone numbers
    text = re.sub(r'(?<!\w)\d{10,}(?!\w)', '[ID_REDACTED]', text)
    # Replace phone-like patterns (e.g. +1-234-567-8901, (234) 567-8901)
    text = re.sub(
        r'(\+?\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b',
        '[PHONE_REDACTED]',
        text,
    )
    return text

# ── Tool definitions (OpenAI function-calling schema) ──────────────────────

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "repo_summary",
            "description": "Get high-level metadata about a GitHub repository (stars, forks, language, etc.).",
            "parameters": {
                "type": "object",
                "properties": {
                    "repo_full_name": {
                        "type": "string",
                        "description": "Repository in 'owner/repo' format, e.g. 'microsoft/vscode'.",
                    }
                },
                "required": ["repo_full_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_commits",
            "description": "List recent commits in a repository. Can filter by author, branch, and date range.",
            "parameters": {
                "type": "object",
                "properties": {
                    "repo_full_name": {
                        "type": "string",
                        "description": "Repository in 'owner/repo' format.",
                    },
                    "author": {
                        "type": "string",
                        "description": "GitHub username to filter commits by author.",
                    },
                    "branch": {
                        "type": "string",
                        "description": "Branch name to list commits from.",
                    },
                    "since": {
                        "type": "string",
                        "description": "ISO 8601 date string. Only commits after this date.",
                    },
                    "until": {
                        "type": "string",
                        "description": "ISO 8601 date string. Only commits before this date.",
                    },
                    "max_count": {
                        "type": "integer",
                        "description": "Maximum number of commits to return (default 30).",
                    },
                },
                "required": ["repo_full_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_merges",
            "description": "List merge commits (commits with more than one parent) in a repository.",
            "parameters": {
                "type": "object",
                "properties": {
                    "repo_full_name": {
                        "type": "string",
                        "description": "Repository in 'owner/repo' format.",
                    },
                    "branch": {
                        "type": "string",
                        "description": "Branch name to scan.",
                    },
                    "author": {
                        "type": "string",
                        "description": "GitHub username to filter by.",
                    },
                    "max_count": {
                        "type": "integer",
                        "description": "Maximum merge commits to return (default 20).",
                    },
                },
                "required": ["repo_full_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "detect_rebases",
            "description": (
                "Detect commits that were likely rebased. Uses heuristics: "
                "author/committer date mismatch and commit messages mentioning 'rebase'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "repo_full_name": {
                        "type": "string",
                        "description": "Repository in 'owner/repo' format.",
                    },
                    "branch": {
                        "type": "string",
                        "description": "Branch name to scan.",
                    },
                    "max_count": {
                        "type": "integer",
                        "description": "Maximum results to return (default 30).",
                    },
                },
                "required": ["repo_full_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_commit_detail",
            "description": "Get full details for a single commit including changed files and line stats.",
            "parameters": {
                "type": "object",
                "properties": {
                    "repo_full_name": {
                        "type": "string",
                        "description": "Repository in 'owner/repo' format.",
                    },
                    "sha": {
                        "type": "string",
                        "description": "Full or short SHA of the commit.",
                    },
                },
                "required": ["repo_full_name", "sha"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_branches",
            "description": "List all branches in the repository.",
            "parameters": {
                "type": "object",
                "properties": {
                    "repo_full_name": {
                        "type": "string",
                        "description": "Repository in 'owner/repo' format.",
                    }
                },
                "required": ["repo_full_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_pull_requests",
            "description": "List pull requests for a repository. Useful for reviewing merge activity.",
            "parameters": {
                "type": "object",
                "properties": {
                    "repo_full_name": {
                        "type": "string",
                        "description": "Repository in 'owner/repo' format.",
                    },
                    "state": {
                        "type": "string",
                        "enum": ["open", "closed", "all"],
                        "description": "Filter by PR state (default 'all').",
                    },
                    "max_count": {
                        "type": "integer",
                        "description": "Maximum PRs to return (default 20).",
                    },
                },
                "required": ["repo_full_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_user_repos",
            "description": (
                "List repositories for a GitHub user. If no username is provided, "
                "lists repos for the authenticated user (the token owner)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "username": {
                        "type": "string",
                        "description": "GitHub username. Omit to list the authenticated user's repos.",
                    },
                    "max_count": {
                        "type": "integer",
                        "description": "Maximum repos to return (default 30).",
                    },
                    "sort": {
                        "type": "string",
                        "enum": ["updated", "created", "pushed", "full_name"],
                        "description": "Sort order (default 'updated').",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_repo",
            "description": (
                "Create a new GitHub repository under the authenticated user's account. "
                "Requires a repo name. Optionally set description, visibility (private/public), "
                "and whether to auto-initialize with a README."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Name for the new repository.",
                    },
                    "description": {
                        "type": "string",
                        "description": "Short description for the repo.",
                    },
                    "private": {
                        "type": "boolean",
                        "description": "True for private, false for public (default false).",
                    },
                    "auto_init": {
                        "type": "boolean",
                        "description": "Initialize with a README (default true).",
                    },
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_or_update_file",
            "description": (
                "Create a new file or update an existing file in a GitHub repository. "
                "Provide the file path, content, and a commit message."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "repo_full_name": {
                        "type": "string",
                        "description": "Repository in 'owner/repo' format.",
                    },
                    "path": {
                        "type": "string",
                        "description": "File path in the repo (e.g. 'src/main.py', 'README.md').",
                    },
                    "content": {
                        "type": "string",
                        "description": "The file content to write.",
                    },
                    "message": {
                        "type": "string",
                        "description": "Commit message for this change.",
                    },
                    "branch": {
                        "type": "string",
                        "description": "Branch to commit to. Defaults to the repo's default branch.",
                    },
                },
                "required": ["repo_full_name", "path", "content", "message"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_file_content",
            "description": "Read the content of a file from a GitHub repository.",
            "parameters": {
                "type": "object",
                "properties": {
                    "repo_full_name": {
                        "type": "string",
                        "description": "Repository in 'owner/repo' format.",
                    },
                    "path": {
                        "type": "string",
                        "description": "File path in the repo (e.g. 'src/main.py'). Use '' or '/' for root directory listing.",
                    },
                    "branch": {
                        "type": "string",
                        "description": "Branch to read from. Defaults to the repo's default branch.",
                    },
                },
                "required": ["repo_full_name", "path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_repo_contents",
            "description": "List files and directories at a given path in a repository. Use to browse the repo file tree.",
            "parameters": {
                "type": "object",
                "properties": {
                    "repo_full_name": {
                        "type": "string",
                        "description": "Repository in 'owner/repo' format.",
                    },
                    "path": {
                        "type": "string",
                        "description": "Directory path to list (empty string for repo root).",
                    },
                    "branch": {
                        "type": "string",
                        "description": "Branch to browse. Defaults to the repo's default branch.",
                    },
                },
                "required": ["repo_full_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_file",
            "description": "Delete a file from a GitHub repository.",
            "parameters": {
                "type": "object",
                "properties": {
                    "repo_full_name": {
                        "type": "string",
                        "description": "Repository in 'owner/repo' format.",
                    },
                    "path": {
                        "type": "string",
                        "description": "File path to delete (e.g. 'old_file.txt').",
                    },
                    "message": {
                        "type": "string",
                        "description": "Commit message for the deletion.",
                    },
                    "branch": {
                        "type": "string",
                        "description": "Branch to delete from. Defaults to the repo's default branch.",
                    },
                },
                "required": ["repo_full_name", "path", "message"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_drafts",
            "description": (
                "Get all draft (unsaved/uncommitted) file changes the user has made in the editor. "
                "Use this to see what the user is working on before it's committed to GitHub. "
                "Returns the draft content and the original content for comparison."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "repo_full_name": {
                        "type": "string",
                        "description": "Repository in 'owner/repo' format.",
                    },
                },
                "required": ["repo_full_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "review_code",
            "description": (
                "Review a file's code and provide suggestions for improvements, bugs, "
                "best practices, security issues, and potential optimizations. "
                "Use this when the user asks for a code review or wants recommendations. "
                "If there are drafts, review the draft version. Otherwise review the committed version."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "repo_full_name": {
                        "type": "string",
                        "description": "Repository in 'owner/repo' format.",
                    },
                    "path": {
                        "type": "string",
                        "description": "File path to review. If empty, reviews all files with drafts.",
                    },
                },
                "required": ["repo_full_name"],
            },
        },
    },
    # ── GitHub Classroom tools ─────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "list_classrooms",
            "description": "List all GitHub Classrooms the user administers.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_classroom",
            "description": "Get details for a specific GitHub Classroom including organization info.",
            "parameters": {
                "type": "object",
                "properties": {
                    "classroom_id": {
                        "type": "integer",
                        "description": "The unique ID of the classroom.",
                    },
                },
                "required": ["classroom_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_assignments",
            "description": "List all assignments in a GitHub Classroom (title, deadline, accepted/submitted/passing counts).",
            "parameters": {
                "type": "object",
                "properties": {
                    "classroom_id": {
                        "type": "integer",
                        "description": "The unique ID of the classroom.",
                    },
                },
                "required": ["classroom_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_assignment",
            "description": "Get details for a specific assignment including starter code repo and feedback settings.",
            "parameters": {
                "type": "object",
                "properties": {
                    "assignment_id": {
                        "type": "integer",
                        "description": "The unique ID of the assignment.",
                    },
                },
                "required": ["assignment_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_accepted_assignments",
            "description": "List student submissions for an assignment — shows each student's repo, commit count, grade, and pass/fail status.",
            "parameters": {
                "type": "object",
                "properties": {
                    "assignment_id": {
                        "type": "integer",
                        "description": "The unique ID of the assignment.",
                    },
                },
                "required": ["assignment_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_assignment_grades",
            "description": "Get grades for all students in an assignment — shows points awarded, points available, and submission timestamps.",
            "parameters": {
                "type": "object",
                "properties": {
                    "assignment_id": {
                        "type": "integer",
                        "description": "The unique ID of the assignment.",
                    },
                },
                "required": ["assignment_id"],
            },
        },
    },
]

# ── Map tool names to python functions ─────────────────────────────────────

# Shared draft store — the server sets this reference
_draft_store: dict = {}


def set_draft_store(store: dict):
    """Called by server.py to share the in-memory draft store."""
    global _draft_store
    _draft_store = store


def _get_drafts(repo_full_name: str) -> list[dict]:
    """Return all drafts for a repo."""
    prefix = f"{repo_full_name}::"
    return [v for k, v in _draft_store.items() if k.startswith(prefix)]


def _review_code(repo_full_name: str, path: str = "") -> dict:
    """Get file content for review, preferring drafts over committed versions."""
    drafts = _get_drafts(repo_full_name)

    if path:
        # Review specific file
        key = f"{repo_full_name}::{path}"
        draft = _draft_store.get(key)
        if draft:
            return {
                "path": path,
                "source": "draft",
                "content": draft["content"],
                "original_content": draft.get("original_content", ""),
                "has_uncommitted_changes": draft["content"] != draft.get("original_content", ""),
            }
        # Fall back to committed version
        try:
            file_data = github_client.get_file_content(repo_full_name, path)
            return {
                "path": path,
                "source": "committed",
                "content": file_data.get("content", ""),
                "has_uncommitted_changes": False,
            }
        except Exception as e:
            return {"error": str(e)}

    # Review all drafts if no path specified
    if drafts:
        return {
            "files": [
                {
                    "path": d["path"],
                    "source": "draft",
                    "content": d["content"],
                    "original_content": d.get("original_content", ""),
                    "has_uncommitted_changes": d["content"] != d.get("original_content", ""),
                }
                for d in drafts
            ]
        }
    return {"message": "No drafts found. Specify a file path to review a committed file."}


# Shared pending actions store — the server sets this reference
_pending_actions: dict = {}
_action_counter = 0


def set_pending_actions_store(store: dict):
    global _pending_actions
    _pending_actions = store


def _propose_create_or_update_file(repo_full_name: str, path: str, content: str, message: str, branch: str = None) -> dict:
    """Propose creating/updating a file. Returns a pending action ID for user approval."""
    global _action_counter
    _action_counter += 1
    action_id = f"action_{_action_counter}"
    _pending_actions[action_id] = {
        "id": action_id,
        "type": "create_or_update_file",
        "repo_full_name": repo_full_name,
        "path": path,
        "content": content,
        "message": message,
        "branch": branch,
        "status": "pending",
    }
    return {
        "status": "pending_approval",
        "action_id": action_id,
        "message": f"Proposed: {message}",
        "description": f"Create/update '{path}' in {repo_full_name}. Waiting for user approval.",
    }


def _propose_delete_file(repo_full_name: str, path: str, message: str, branch: str = None) -> dict:
    """Propose deleting a file. Returns a pending action ID for user approval."""
    global _action_counter
    _action_counter += 1
    action_id = f"action_{_action_counter}"
    _pending_actions[action_id] = {
        "id": action_id,
        "type": "delete_file",
        "repo_full_name": repo_full_name,
        "path": path,
        "message": message,
        "branch": branch,
        "status": "pending",
    }
    return {
        "status": "pending_approval",
        "action_id": action_id,
        "message": f"Proposed: {message}",
        "description": f"Delete '{path}' from {repo_full_name}. Waiting for user approval.",
    }


def _propose_create_repo(name: str, description: str = "", private: bool = False, auto_init: bool = True) -> dict:
    """Propose creating a new repository. Returns a pending action ID for user approval."""
    global _action_counter
    _action_counter += 1
    action_id = f"action_{_action_counter}"
    _pending_actions[action_id] = {
        "id": action_id,
        "type": "create_repo",
        "name": name,
        "description": description,
        "private": private,
        "auto_init": auto_init,
        "status": "pending",
    }
    return {
        "status": "pending_approval",
        "action_id": action_id,
        "message": f"Proposed: Create repository '{name}'",
        "description": f"Create {'private' if private else 'public'} repo '{name}'. Waiting for user approval.",
    }


TOOL_DISPATCH: dict = {
    "repo_summary": github_client.repo_summary,
    "list_user_repos": github_client.list_user_repos,
    "create_repo": _propose_create_repo,
    "create_or_update_file": _propose_create_or_update_file,
    "get_file_content": github_client.get_file_content,
    "list_repo_contents": github_client.list_repo_contents,
    "delete_file": _propose_delete_file,
    "get_drafts": _get_drafts,
    "review_code": _review_code,
    "list_commits": github_client.list_commits,
    "list_merges": github_client.list_merges,
    "detect_rebases": github_client.detect_rebases,
    "get_commit_detail": github_client.get_commit_detail,
    "list_branches": github_client.list_branches,
    "list_pull_requests": github_client.list_pull_requests,
    # Classroom
    "list_classrooms": classroom_client.list_classrooms,
    "get_classroom": classroom_client.get_classroom,
    "list_assignments": classroom_client.list_assignments,
    "get_assignment": classroom_client.get_assignment,
    "list_accepted_assignments": classroom_client.list_accepted_assignments,
    "get_assignment_grades": classroom_client.get_assignment_grades,
}


def _parse_dates(args: dict) -> dict:
    """Convert ISO date strings coming from the LLM into datetime objects."""
    for key in ("since", "until"):
        if key in args and isinstance(args[key], str):
            args[key] = datetime.fromisoformat(args[key])
    return args


def _call_tool(name: str, arguments: dict) -> str:
    """Execute a tool and return its sanitized JSON result."""
    fn = TOOL_DISPATCH.get(name)
    if fn is None:
        return json.dumps({"error": f"Unknown tool: {name}"})

    arguments = _parse_dates(arguments)
    try:
        result = fn(**arguments)
        raw = json.dumps(result, default=str)
        return _sanitize_for_content_filter(raw)
    except Exception as exc:
        return json.dumps({"error": str(exc)})


# ── Agent loop ─────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """\
You are a helpful GitHub Repository Agent. Users provide a GitHub repository
(in "owner/repo" format) and you help them explore it.

Your capabilities:
- Summarise repository metadata
- List and filter commits by author, branch, or date range
- Show merge commits
- Detect rebased commits (heuristic: author/committer date mismatch)
- Show detailed file-level changes for any commit
- List branches and pull requests
- Read file contents from the repository
- Create, update, and delete files
- List directory contents
- Create new repositories

IMPORTANT: Always use your tools to fetch data. Never make up or guess
file contents, commit lists, or repository structure. If the user asks to
see a file, use the get_file_content tool. If they ask about commits,
use list_commits. Always call the appropriate tool rather than generating
a text-only response.

The user can edit files in the UI and save them as "drafts" before committing.
Use the get_drafts tool to check for uncommitted changes. When reviewing code
or giving suggestions, always check for drafts first — the user may have made
changes they want you to look at.

When the user asks you to review code, go through the files, or suggest
changes, use the review_code tool to get the content, then analyze it for:
- Bugs and logical errors
- Security issues
- Performance improvements
- Code style and best practices
- Missing error handling
- Suggestions for refactoring

IMPORTANT: When you create, update, or delete files, the action is NOT
executed immediately. It creates a pending action that the user must approve
or reject. After calling create_or_update_file, delete_file, or create_repo,
inform the user that the action is pending their approval. They will see an
approval dialog in the UI.

You also have access to GitHub Classroom. You can:
- List classrooms the user administers
- List assignments in a classroom with deadlines and submission counts
- View student submissions (repos, commit counts, grades)
- Get detailed grades for all students
- Review student code by accessing their repos with get_file_content

When presenting results, be concise. Use tables or bullet lists for commit
lists. Always show the short SHA and link when referencing a commit.
If the user hasn't specified a repo yet, ask them for one.
"""


class GitHubAgent:
    def __init__(self, model: str | None = None, on_tool_call=None):
        self.client = _get_openai_client()
        # The UI sends model labels (e.g. "gpt-5.2-chat"); inside Lumen we route
        # every request to the configured Azure OpenAI deployment.
        self.model = settings.azure_openai_deployment
        self.messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
        self.on_tool_call = on_tool_call  # callback(name, args) for UI updates
        self.last_tool_results: list[dict] = []  # populated after each chat()

    def chat(self, user_message: str) -> str:
        """Send a user message and return the agent's final text response."""
        self.messages.append({"role": "user", "content": user_message})
        self.last_tool_results = []

        max_retries = 2
        while True:
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=self.messages,
                    tools=TOOLS,
                    tool_choice="auto",
                )
            except Exception as exc:
                err_str = str(exc)
                if "content_filter" in err_str or "content management policy" in err_str:
                    if max_retries > 0:
                        max_retries -= 1
                        # Sanitize all existing tool results more aggressively
                        for msg in self.messages:
                            if msg.get("role") == "tool" and isinstance(msg.get("content"), str):
                                msg["content"] = _sanitize_for_content_filter(msg["content"])
                        continue
                raise

            choice = response.choices[0]
            assistant_msg = choice.message
            self.messages.append(assistant_msg.model_dump())

            # If there are no tool calls, we have the final answer
            if not assistant_msg.tool_calls:
                return assistant_msg.content or ""

            # Process each tool call
            for tool_call in assistant_msg.tool_calls:
                fn_name = tool_call.function.name
                fn_args = json.loads(tool_call.function.arguments)
                if self.on_tool_call:
                    self.on_tool_call(fn_name, fn_args)
                else:
                    print(f"  ⚙  Calling tool: {fn_name}({json.dumps(fn_args, default=str)})")
                result = _call_tool(fn_name, fn_args)
                # Store for side panel
                try:
                    parsed = json.loads(result)
                except (json.JSONDecodeError, TypeError):
                    parsed = result
                self.last_tool_results.append({
                    "tool": fn_name,
                    "args": fn_args,
                    "result": parsed,
                })
                self.messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": result,
                    }
                )
            # Loop back so the model can process tool results
