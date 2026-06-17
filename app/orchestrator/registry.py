"""Agent Registry.

Internal agents: hard-coded routing table (agent_id -> module path).
External agents: keyed by endpoint URL — the canonical A2A identity.
  Identity = the URL the agent lives at (e.g. https://shiksha.example.com/a2a/blockchain).
  A slug derived from that URL is used for path-based routing (/a2a/{slug}).
  Registry is persisted to disk (data/agent_registry.json) and Cosmos DB so
  external agents survive process restarts.
"""

from __future__ import annotations

import importlib
import json
import logging
import os
import pathlib
from datetime import datetime, timezone as _tz
from urllib.parse import urlparse
UTC = _tz.utc

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter(tags=["agents"])

# -- Internal agent routing table (agent_id -> module path) ------------------
# Identity of each internal agent = {base_url}/a2a/{agent_id}
# Routing slug IS the agent_id (e.g. "calendar" in POST /a2a/calendar)

AGENT_ROUTES: dict[str, str] = {
    "calendar":      "app.agents.calendar_agent",
    "communication": "app.agents.communication_agent",
    "github":        "app.agents.github_agent",
    "shiksha":       "app.agents.shiksha_agent",
    "graph":         "app.agents.graph_agent",
}

# Back-compat slug aliases (old agent id -> current agent id). The former
# "portfolio" agent is now folded into the "github" agent, which preserves all
# portfolio behaviour and adds full GitHub repo/classroom support.
AGENT_ALIASES: dict[str, str] = {
    "portfolio": "github",
}


def resolve_agent_id(agent_id: str) -> str:
    """Resolve a routing slug through back-compat aliases."""
    seen: set[str] = set()
    while agent_id in AGENT_ALIASES and agent_id not in seen:
        seen.add(agent_id)
        agent_id = AGENT_ALIASES[agent_id]
    return agent_id

# -- External agent registry --------------------------------------------------
# Keyed by endpoint URL — the agent's canonical identity.
# e.g. {"https://shiksha.example.com/a2a/blockchain": {"slug": "blockchain", ...}}
# Loaded from disk/Cosmos on startup; flushed on every register/unregister.

def _resolve_registry_path() -> pathlib.Path:
    from app.config import settings
    if settings.lumen_store_path:
        return pathlib.Path(settings.lumen_store_path).parent / "agent_registry.json"
    if os.path.isdir("/home"):
        return pathlib.Path("/home/data/agent_registry.json")
    return pathlib.Path("data/agent_registry.json")

_REGISTRY_PATH = _resolve_registry_path()


def _load_registry() -> dict[str, dict]:
    """Load persisted external agents from disk."""
    try:
        if _REGISTRY_PATH.exists():
            with _REGISTRY_PATH.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                logger.info(f"Loaded {len(data)} external agents from {_REGISTRY_PATH}")
                return data
    except Exception as e:
        logger.warning(f"Failed to load agent registry at {_REGISTRY_PATH}: {e}")
    return {}


def _flush_registry() -> None:
    """Persist external agents to disk. Best-effort, never raises."""
    try:
        _REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = _REGISTRY_PATH.with_suffix(_REGISTRY_PATH.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(_external_agents, f, ensure_ascii=False, indent=2)
        tmp.replace(_REGISTRY_PATH)
    except Exception as e:
        logger.warning(f"Failed to persist agent registry at {_REGISTRY_PATH}: {e}")


def _slug_from_url(endpoint: str) -> str:
    """Derive a routing slug from an endpoint URL's last path segment.
    e.g. https://shiksha.example.com/a2a/blockchain -> 'blockchain'"""
    path = urlparse(endpoint).path.rstrip("/")
    return path.split("/")[-1] if "/" in path else path


# Keyed by endpoint URL. Loaded from disk on import.
_external_agents: dict[str, dict] = _load_registry()

# Slug → endpoint URL reverse index for O(1) routing lookup.
# Rebuilt lazily; call _slug_index() to get a fresh copy.
def _slug_index() -> dict[str, str]:
    return {a["slug"]: ep for ep, a in _external_agents.items() if a.get("slug")}


def get_external_by_slug(slug: str) -> dict | None:
    """Look up an external agent by its routing slug."""
    endpoint = _slug_index().get(slug)
    return _external_agents.get(endpoint) if endpoint else None


async def _cosmos_upsert_agent(agent: dict) -> None:
    """Persist an agent record to Cosmos DB (best-effort)."""
    try:
        from app.db.cosmos import is_cosmos_ready, _containers
        if is_cosmos_ready() and "agents" in _containers:
            await _containers["agents"].upsert_item(agent)
    except Exception as e:
        logger.warning(f"Cosmos upsert_agent failed: {e}")


async def _cosmos_delete_agent(agent_id: str) -> None:
    """Delete an agent record from Cosmos DB (best-effort)."""
    try:
        from app.db.cosmos import is_cosmos_ready, _containers
        if is_cosmos_ready() and "agents" in _containers:
            await _containers["agents"].delete_item(item=agent_id, partition_key=agent_id)
    except Exception as e:
        logger.warning(f"Cosmos delete_agent failed: {e}")


async def load_registry_from_cosmos() -> None:
    """On startup: hydrate _external_agents from Cosmos if available.
    Cosmos is source of truth; disk is the offline fallback."""
    try:
        from app.db.cosmos import is_cosmos_ready, _containers
        if not is_cosmos_ready() or "agents" not in _containers:
            return
        items = []
        async for item in _containers["agents"].query_items("SELECT * FROM c"):
            items.append(item)
        if items:
            for item in items:
                ep = item.get("endpoint", "")
                if ep:
                    _external_agents[ep] = item
            _flush_registry()  # sync disk with Cosmos state
            logger.info(f"Hydrated {len(items)} external agents from Cosmos")
    except Exception as e:
        logger.warning(f"load_registry_from_cosmos failed: {e}")


def get_all_agent_ids() -> list[str]:
    """Return routing slugs for all agents (internal + external)."""
    return list(AGENT_ROUTES.keys()) + [a["slug"] for a in _external_agents.values() if a.get("slug")]


def is_internal(agent_id: str) -> bool:
    return agent_id in AGENT_ROUTES


def get_agent_module(agent_id: str):
    """Import and return the module for an internal agent."""
    module_path = AGENT_ROUTES.get(resolve_agent_id(agent_id))
    if not module_path:
        return None
    return importlib.import_module(module_path)


def get_agent_card(slug: str, base_url: str = "") -> "AgentCard | None":
    """Get agent card by routing slug.
    Internal: calls the module's own get_agent_card().
    External: parses the cached card JSON into an AgentCard.
    """
    from app.protocols.models import AgentCard
    # Internal agent
    module = get_agent_module(slug)
    if module and hasattr(module, "get_agent_card"):
        return module.get_agent_card(base_url)
    # External agent — look up by slug, parse cached card
    agent = get_external_by_slug(slug)
    if agent and agent.get("cached_card"):
        try:
            return AgentCard.model_validate(agent["cached_card"])
        except Exception as e:
            logger.warning(f"Invalid cached card for {slug}: {e}")
    return None


def get_all_agents() -> list[dict]:
    """Returns agent list for legacy callers."""
    agents = []
    for agent_id in AGENT_ROUTES:
        module = get_agent_module(agent_id)
        card = module.get_agent_card("") if module and hasattr(module, "get_agent_card") else None
        agents.append({
            "id": agent_id,
            "name": card.name if card else agent_id,
            "description": card.description if card else "",
            "subject": card.name if card else "",
            "icon": "🤖",
            "type": "internal",
            "url": f"/a2a/{agent_id}",
            "capabilities": {"subjects": [agent_id], "levels": 0},
            "protocol": "a2a/1.0",
        })
    for endpoint, agent in _external_agents.items():
        agents.append({
            "id": agent["slug"],
            "name": agent.get("name", agent["slug"]),
            "description": agent.get("description", ""),
            "subject": "",
            "icon": agent.get("icon", "🤖"),
            "type": "external",
            "url": endpoint,           # canonical identity = endpoint URL
            "capabilities": agent.get("capabilities", {}),
            "protocol": "a2a/1.0",
        })
    return agents


def detect_ta(message: str) -> str | None:
    """Score agents by keyword match. Returns best matching slug."""
    msg_lower = message.lower()

    KEYWORDS: dict[str, list[str]] = {
        "shiksha": ["shiksha", "ekalaiva", "course", "blockchain", "ta session"],
        "calendar": [],
        "communication": [],
        "github": ["github", "portfolio", "repo", "repos", "repository", "commit",
                    "commits", "merge", "rebase", "branch", "pull request", "pr",
                    "classroom", "assignment"],
        "graph": ["onedrive"],
    }

    scores = {}
    for agent_id, keywords in KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in msg_lower)
        if score > 0:
            scores[agent_id] = score

    # External agents — scored by their registered keywords, returned as slug
    for endpoint, agent in _external_agents.items():
        keywords = agent.get("keywords", [])
        score = sum(1 for kw in keywords if kw in msg_lower)
        if score > 0:
            scores[agent["slug"]] = score

    return max(scores, key=scores.get) if scores else None


def is_ta(slug: str) -> bool:
    return (resolve_agent_id(slug) in AGENT_ROUTES
            or get_external_by_slug(slug) is not None)


# -- API Routes ---------------------------------------------------------------

@router.get("")
async def list_agents(request: Request):
    """List all agents — each card comes from the agent itself."""
    base = str(request.base_url).rstrip("/")
    cards = []
    for agent_id in AGENT_ROUTES:
        card = get_agent_card(agent_id, base)
        if card:
            cards.append({"id": agent_id, "url": f"{base}/a2a/{agent_id}", **card.model_dump()})
    for endpoint, agent in _external_agents.items():
        cards.append({
            "id": agent["slug"],
            "url": endpoint,            # canonical identity = endpoint URL
            "name": agent.get("name", agent["slug"]),
            "description": agent.get("description", ""),
            "type": "external",
        })
    return cards


@router.get("/{slug}/agent-card.json")
async def agent_card_endpoint(slug: str, request: Request):
    """Return A2A v1.0.0 compliant agent card. Each agent owns its card."""
    base = str(request.base_url).rstrip("/")
    card = get_agent_card(slug, base)
    if not card:
        raise HTTPException(status_code=404, detail=f"Agent '{slug}' not found")
    return card


@router.get("/{slug}/agent.json")
async def agent_card_old_redirect(slug: str):
    """Backwards compat redirect."""
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url=f"/agents/{slug}/agent-card.json", status_code=301)


class AgentRegistration(BaseModel):
    name: str
    endpoint: str                # canonical identity — the agent's A2A URL
    description: str = ""
    card_url: str = ""           # e.g. https://shiksha.example.com/.well-known/agent-card.json
    keywords: list[str] = []
    capabilities: dict = {}
    icon: str = "🤖"


@router.post("/register")
async def register_agent(reg: AgentRegistration, request: Request):
    """Register an external agent. Identity = endpoint URL; slug derived from URL path."""
    import httpx

    slug = _slug_from_url(reg.endpoint)
    if not slug:
        raise HTTPException(status_code=422, detail="Cannot derive slug from endpoint URL")

    cached_card = None
    if reg.card_url:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                r = await client.get(reg.card_url)
                if r.status_code == 200:
                    cached_card = r.json()
                    logger.info(f"Fetched card for {slug} from {reg.card_url}")
        except Exception as e:
            logger.warning(f"Could not fetch card from {reg.card_url}: {e}")

    agent_record = {
        "id": reg.endpoint,          # Cosmos partition key = endpoint URL
        "slug": slug,                 # routing key: last segment of endpoint URL path
        "name": reg.name,
        "description": reg.description,
        "endpoint": reg.endpoint,
        "card_url": reg.card_url,
        "keywords": reg.keywords,
        "capabilities": reg.capabilities,
        "icon": reg.icon,
        "cached_card": cached_card,
        "registered_at": datetime.now(UTC).isoformat(),
    }

    _external_agents[reg.endpoint] = agent_record
    _flush_registry()                 # persist to disk immediately
    await _cosmos_upsert_agent(agent_record)  # persist to Cosmos (best-effort)

    logger.info(f"External agent registered: {slug} ({reg.name}) at {reg.endpoint}")

    from app.events.bus import publish, TA_REGISTERED
    await publish(TA_REGISTERED, {"agent_id": slug, "name": reg.name, "endpoint": reg.endpoint})

    return {"ok": True, "slug": slug, "url": reg.endpoint, "card_fetched": cached_card is not None}


@router.delete("/{slug}")
async def unregister_agent(slug: str):
    if slug in AGENT_ROUTES:
        raise HTTPException(status_code=400, detail="Cannot unregister built-in agents")
    agent = get_external_by_slug(slug)
    if not agent:
        raise HTTPException(status_code=404, detail=f"Agent '{slug}' not found")
    endpoint = agent["endpoint"]
    del _external_agents[endpoint]
    _flush_registry()
    await _cosmos_delete_agent(endpoint)
    return {"ok": True, "message": f"Agent '{slug}' ({endpoint}) unregistered"}
