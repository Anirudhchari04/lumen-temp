"""Shiksha Agent — read-only bridge to the Ekalaiva (Shiksha) backend.

Lumen uses this to:
  - Discover courses the user is enrolled in (from their chat threads)
  - Fetch user progress (aggregated from thread counts)
  - Build deep links to Shiksha frontend
  - Summarize what a user has learned (from message history)

Zero writes to Shiksha. Zero changes to Shiksha codebase.

User ID mapping: Shiksha uses the Entra OID as userId (same GUID that Lumen's
JWT exposes as `oid` / `sub`).
"""

from __future__ import annotations

import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# ── Shiksha endpoints ─────────────────────────────────────────────────────────
SHIKSHA_BACKEND = "https://ekalaiva-backend-app-dqcga9abafhuh7cb.westus2-01.azurewebsites.net"
SHIKSHA_FRONTEND = "https://ekalaiva-frontend-app-cncbg2hwdueedpfn.westus2-01.azurewebsites.net"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _agent_id_to_name(agent_id: str) -> str:
    """'course-Blockchain-Technologies' → 'Blockchain Technologies'"""
    name = agent_id
    if name.startswith("course-"):
        name = name[len("course-"):]
    return name.replace("-", " ").strip()


def _agent_id_to_slug(agent_id: str) -> str:
    """'course-Blockchain-Technologies' → 'Blockchain-Technologies'"""
    if agent_id.startswith("course-"):
        return agent_id[len("course-"):]
    return agent_id


def shiksha_course_url(agent_id: str) -> str:
    slug = _agent_id_to_slug(agent_id)
    return f"{SHIKSHA_FRONTEND}/course/{slug}"


def shiksha_chat_url(agent_id: str, thread_id: str) -> str:
    slug = _agent_id_to_slug(agent_id)
    return f"{SHIKSHA_FRONTEND}/chat/{slug}/{thread_id}"


def _build_agent_card(agent_id: str) -> dict:
    """Build a display card for an agent from its ID alone."""
    return {
        "agent_id": agent_id,
        "name": _agent_id_to_name(agent_id),
        "slug": _agent_id_to_slug(agent_id),
        "url": shiksha_course_url(agent_id),
    }


# ── Shiksha API calls (async httpx) ──────────────────────────────────────────

async def get_user_threads(user_id: str) -> list[dict]:
    """Fetch all threads for a user from Shiksha backend."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(f"{SHIKSHA_BACKEND}/api/chat/threads/{user_id}")
            if r.status_code == 200:
                data = r.json()
                if isinstance(data, list):
                    return data
                if isinstance(data, dict):
                    return data.get("threads", [])
    except Exception as e:
        logger.warning(f"Shiksha get_user_threads failed: {e}")
    return []


async def get_available_agents(user_id: str | None = None) -> list[dict]:
    """
    Return the list of available Shiksha course agents for the user.
    Derived from the user's chat threads (the only reliable source).
    Returns agents the user has active sessions with.
    """
    if not user_id:
        return []
    threads = await get_user_threads(user_id)
    seen: dict[str, dict] = {}
    for t in threads:
        aid = t.get("agentId", "")
        if aid and aid not in seen:
            seen[aid] = _build_agent_card(aid)
    return list(seen.values())


async def get_thread_messages(thread_id: str, user_id: str, limit: int = 20) -> list[dict]:
    """Fetch messages from a specific thread."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(f"{SHIKSHA_BACKEND}/api/chat/thread/{thread_id}/messages")
            if r.status_code == 200:
                data = r.json()
                messages = data if isinstance(data, list) else data.get("messages", [])
                return messages[-limit:] if len(messages) > limit else messages
    except Exception as e:
        logger.warning(f"Shiksha get_thread_messages failed: {e}")
    return []


async def get_agent_full_memory(user_id: str, agent_id: str, max_threads: int = 5, messages_per_thread: int = 30) -> list[dict]:
    """
    Fetch the full conversation memory for a specific TA agent.
    Returns a flat list of {role, content, thread_id, thread_index} dicts
    from the most recent threads, newest-first.
    """
    threads = await get_user_threads(user_id)
    agent_threads = [t for t in threads if t.get("agentId") == agent_id]
    agent_threads.sort(key=lambda x: x.get("updatedAt", ""), reverse=True)
    agent_threads = agent_threads[:max_threads]

    all_messages: list[dict] = []
    for i, thread in enumerate(agent_threads):
        tid = thread.get("id", "")
        if not tid:
            continue
        msgs = await get_thread_messages(tid, user_id, limit=messages_per_thread)
        for m in msgs:
            all_messages.append({
                "role": m.get("role", "unknown"),
                "content": m.get("content", ""),
                "thread_id": tid,
                "thread_index": i,
            })
    return all_messages


async def get_all_ta_memory(user_id: str, max_threads_per_agent: int = 3, messages_per_thread: int = 20) -> dict[str, list[dict]]:
    """
    Fetch conversation memory for ALL of the user's TA agents.
    Returns {agent_id: [messages]} dict.
    """
    agents = await get_available_agents(user_id)
    result: dict[str, list[dict]] = {}
    for agent in agents:
        aid = agent["agent_id"]
        result[aid] = await get_agent_full_memory(
            user_id, aid,
            max_threads=max_threads_per_agent,
            messages_per_thread=messages_per_thread,
        )
    return result


def format_memory_for_llm(
    memory: list[dict],
    agent_name: str,
    max_chars: int = 6000,
) -> str:
    """
    Format TA conversation memory into a readable block for LLM context.
    Truncates to max_chars to fit within context windows.
    """
    if not memory:
        return f"No conversation history found for {agent_name}."

    lines = [f"=== Conversation history with {agent_name} TA ==="]
    current_thread = -1
    chars = 0
    for m in memory:
        if m["thread_index"] != current_thread:
            current_thread = m["thread_index"]
            label = f"\n--- Session {current_thread + 1} ---"
            lines.append(label)
            chars += len(label)
        role_label = "You" if m["role"] == "user" else f"{agent_name} TA"
        line = f"{role_label}: {m['content']}"
        chars += len(line)
        if chars > max_chars:
            lines.append("... [history truncated] ...")
            break
        lines.append(line)
    return "\n".join(lines)


async def get_user_progress(user_id: str) -> list[dict]:
    """
    Return aggregated progress per course agent.
    Each entry: {agent_id, name, slug, thread_count, last_active, url, continue_url}
    """
    threads = await get_user_threads(user_id)

    agent_threads: dict[str, list[dict]] = {}
    for t in threads:
        aid = t.get("agentId", "")
        if aid:
            agent_threads.setdefault(aid, []).append(t)

    progress: list[dict] = []
    for agent_id, t_list in agent_threads.items():
        t_list.sort(key=lambda x: x.get("updatedAt", ""), reverse=True)
        latest = t_list[0]
        card = _build_agent_card(agent_id)
        progress.append({
            **card,
            "thread_count": len(t_list),
            "last_active": latest.get("updatedAt", ""),
            "latest_thread_id": latest.get("id", ""),
            "continue_url": (
                shiksha_chat_url(agent_id, latest["id"])
                if latest.get("id") else card["url"]
            ),
        })

    progress.sort(key=lambda x: x.get("last_active", ""), reverse=True)
    return progress


def find_agent_by_keyword(keyword: str, agents: list[dict]) -> dict | None:
    """Find an agent by fuzzy keyword match on name/slug."""
    kw = keyword.lower()
    for a in agents:
        if kw in a["name"].lower() or kw in a["slug"].lower():
            return a
    return None


async def summarize_learning(user_id: str, agent_id: str | None = None) -> str:
    """
    Fetch recent messages from Shiksha and return raw assistant content
    for LLM summarization. Returns "" if no history found.
    """
    threads = await get_user_threads(user_id)
    if agent_id:
        threads = [t for t in threads if t.get("agentId") == agent_id]
    if not threads:
        return ""

    threads.sort(key=lambda x: x.get("updatedAt", ""), reverse=True)
    latest = threads[0]
    messages = await get_thread_messages(latest["id"], user_id, limit=15)
    if not messages:
        return ""

    assistant_msgs = [
        m.get("content", "") for m in messages
        if m.get("role") == "assistant" and m.get("content")
    ]
    if not assistant_msgs:
        return ""

    return "\n---\n".join(assistant_msgs[-5:])



def get_agent_card(base_url: str = "") -> "AgentCard":
    from app.protocols.models import AgentCard, AgentProvider, AgentInterface, AgentCapabilities, AgentSkill
    return AgentCard(
        name="Shiksha Bridge",
        description="Bridge to Shiksha/Ekalaiva platform. Discover available TAs, retrieve learning threads, track course progress, and summarize learning from Shiksha sessions.",
        version="1.0.0",
        documentationUrl=f"{base_url}/docs/shiksha",
        provider=AgentProvider(organization="Lumen Network", url=base_url),
        supportedInterfaces=[AgentInterface(url=f"{base_url}/a2a/shiksha")],
        capabilities=AgentCapabilities(streaming=False, pushNotifications=False),
        defaultInputModes=["text/plain"],
        defaultOutputModes=["text/plain", "application/json"],
        securitySchemes={
            "lumenJwt": {"httpAuthSecurityScheme": {"scheme": "bearer", "bearerFormat": "JWT"}}
        },
        securityRequirements=[{"lumenJwt": []}],
        skills=[
            AgentSkill(
                id="shiksha.get_available_tas",
                name="Get Available TAs",
                description="Fetch all available Teaching Assistants from the Shiksha/Ekalaiva platform",
                tags=["shiksha", "tas", "agents", "discover", "ekalaiva"],
                examples=["What TAs are available on Shiksha?", "Show me the Shiksha agents", "What courses can I take on Ekalaiva?"],
            ),
            AgentSkill(
                id="shiksha.get_user_progress",
                name="Get Shiksha Progress",
                description="Retrieve the student's learning progress across all Shiksha TAs",
                tags=["shiksha", "progress", "courses", "learning"],
                examples=["What's my Shiksha progress?", "How am I doing in blockchain?", "Show my Ekalaiva course status"],
            ),
            AgentSkill(
                id="shiksha.get_thread_messages",
                name="Get Thread Messages",
                description="Retrieve messages from a specific Shiksha TA conversation thread",
                tags=["shiksha", "thread", "messages", "history"],
                examples=["Show my blockchain TA conversation", "Get my last session with the Shiksha TA"],
            ),
            AgentSkill(
                id="shiksha.summarize_learning",
                name="Summarize Shiksha Learning",
                description="Generate an AI summary of what the student has learned across Shiksha sessions",
                tags=["shiksha", "summary", "learning", "recap"],
                examples=["Summarize what I learned on Shiksha", "Give me a recap of my TA sessions", "What have I covered in blockchain?"],
            ),
        ],
    )
